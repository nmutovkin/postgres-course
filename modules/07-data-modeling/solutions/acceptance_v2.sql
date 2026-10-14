-- =============================================================================
-- Module 07 milestone: acceptance tests for Bazaar schema v2
-- -----------------------------------------------------------------------------
-- Run against YOUR v2 migration applied on top of the module 07 start state:
--     ./project/bin/reset.sh 07
--     docker exec -i course-pg psql -X -U postgres -d bazaar -v ON_ERROR_STOP=1 < workspace/07/0701_schema_v2.sql
--     docker exec -i course-pg psql -X -U postgres -d bazaar < modules/07-data-modeling/solutions/acceptance_v2.sql
-- (It also works on the reference state: ./project/bin/reset.sh 08.)
--
-- Every test prints PASS or FAIL. Tests only check *behaviour* (which
-- statements must succeed and which must fail, and with which class of error),
-- not your constraint names, so any correct design passes.
-- Everything runs in one transaction that is rolled back: your data is untouched.
-- =============================================================================

\set ON_ERROR_STOP 1
SET search_path = bazaar, public;
BEGIN;

-- expect(label, sql, expected SQLSTATEs): runs sql in a subtransaction.
--   expected = '{}'      -> the statement must succeed
--   expected = '{23505}' -> it must fail with one of the listed SQLSTATEs
CREATE FUNCTION pg_temp.expect(label text, stmt text, expected text[])
RETURNS text
LANGUAGE plpgsql AS $$
BEGIN
    BEGIN
        EXECUTE stmt;
    EXCEPTION WHEN OTHERS THEN
        IF SQLSTATE = ANY (expected) THEN
            RETURN 'PASS  ' || label || '  (rejected: ' || SQLSTATE || ')';
        END IF;
        RETURN 'FAIL  ' || label || '  (unexpected error ' || SQLSTATE || ': ' || SQLERRM || ')';
    END;
    IF cardinality(expected) = 0 THEN
        RETURN 'PASS  ' || label;
    END IF;
    RETURN 'FAIL  ' || label || '  (statement succeeded but should have been rejected)';
END;
$$;

-- Fixtures: a fresh user, a fresh product (seller 5, category 13) and a fresh order.
INSERT INTO users (email, full_name) VALUES ('acceptance.v2@example.com', 'Acceptance Test')
RETURNING id AS uid \gset
INSERT INTO products (seller_id, category_id, sku, title, price, status)
VALUES (5, 13, 'ACC-TEST-1', 'Acceptance Planter', 10.00, 'active')
RETURNING id AS pid \gset
INSERT INTO orders (user_id, seller_id, status, total) VALUES (:uid, 5, 'pending', 10.00)
RETURNING id AS oid \gset
INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (:oid, :pid, 1, 10.00);

\pset tuples_only on
\pset format unaligned

-- --- addresses ----------------------------------------------------------------
SELECT pg_temp.expect('addresses: first default address',
    format($q$INSERT INTO addresses (user_id, label, recipient, line1, city, postal_code, country_code, is_default)
              VALUES (%s, 'Home', 'Acceptance Test', '1 Test St', 'Testville', '12345', 'US', true)$q$, :uid), '{}');
SELECT pg_temp.expect('addresses: two non-default addresses are fine',
    format($q$INSERT INTO addresses (user_id, label, recipient, line1, city, postal_code, country_code, is_default)
              VALUES (%1$s, 'Work', 'Acceptance Test', '2 Test St', 'Testville', '12345', 'US', false),
                     (%1$s, 'Mum',  'Acceptance Test', '3 Test St', 'Testville', '12345', 'US', false)$q$, :uid), '{}');
SELECT pg_temp.expect('addresses: a second default for the same user is rejected',
    format($q$INSERT INTO addresses (user_id, label, recipient, line1, city, postal_code, country_code, is_default)
              VALUES (%s, 'Other', 'Acceptance Test', '4 Test St', 'Testville', '12345', 'US', true)$q$, :uid), '{23505,23P01}');
SELECT pg_temp.expect('addresses: switching the default, step 1: unset the old default',
    format($q$UPDATE addresses SET is_default = false WHERE user_id = %s AND is_default$q$, :uid), '{}');
SELECT pg_temp.expect('addresses: switching the default, step 2: set the new default',
    format($q$UPDATE addresses SET is_default = true WHERE user_id = %s AND label = 'Work'$q$, :uid), '{}');
SELECT pg_temp.expect('addresses: recipient is required',
    format($q$INSERT INTO addresses (user_id, recipient, line1, city, postal_code, country_code)
              VALUES (%s, NULL, '5 Test St', 'Testville', '12345', 'US')$q$, :uid), '{23502}');
SELECT pg_temp.expect('addresses: unknown user is rejected',
    $q$INSERT INTO addresses (user_id, recipient, line1, city, postal_code, country_code)
       VALUES (999999, 'Nobody', '6 Test St', 'Testville', '12345', 'US')$q$, '{23503}');

-- --- orders.shipping_address_id -----------------------------------------------
SELECT pg_temp.expect('orders: shipping address may be NULL',
    format($q$UPDATE orders SET shipping_address_id = NULL WHERE id = %s$q$, :oid), '{}');
SELECT pg_temp.expect('orders: shipping address must exist',
    format($q$UPDATE orders SET shipping_address_id = 999999 WHERE id = %s$q$, :oid), '{23503}');
SELECT pg_temp.expect('orders: shipping address can be set',
    format($q$UPDATE orders SET shipping_address_id = (SELECT id FROM addresses WHERE user_id = %1$s AND label = 'Work')
              WHERE id = %2$s$q$, :uid, :oid), '{}');
SELECT pg_temp.expect('addresses: an address used by an order cannot be deleted',
    format($q$DELETE FROM addresses WHERE user_id = %s AND label = 'Work'$q$, :uid), '{23503}');

-- --- payments -----------------------------------------------------------------
SELECT pg_temp.expect('payments: a payment',
    format($q$INSERT INTO payments (order_id, amount, provider, provider_ref, status)
              VALUES (%s, 10.00, 'stripe', 'ch_acc_1', 'succeeded')$q$, :oid), '{}');
SELECT pg_temp.expect('payments: a refund is a negative amount',
    format($q$INSERT INTO payments (order_id, amount, provider, provider_ref, status)
              VALUES (%s, -10.00, 'stripe', 're_acc_1', 'succeeded')$q$, :oid), '{}');
SELECT pg_temp.expect('payments: amount 0 is rejected',
    format($q$INSERT INTO payments (order_id, amount, provider, status) VALUES (%s, 0, 'stripe', 'failed')$q$, :oid), '{23514}');
SELECT pg_temp.expect('payments: unknown status is rejected',
    format($q$INSERT INTO payments (order_id, amount, provider, status) VALUES (%s, 5, 'stripe', 'maybe')$q$, :oid), '{23514,22P02}');
SELECT pg_temp.expect('payments: unknown order is rejected',
    $q$INSERT INTO payments (order_id, amount, provider, status) VALUES (999999, 5, 'stripe', 'pending')$q$, '{23503}');
SELECT pg_temp.expect('payments: an order with payments cannot be deleted',
    format($q$DELETE FROM orders WHERE id = %s$q$, :oid), '{23503}');

-- --- reviews ------------------------------------------------------------------
SELECT pg_temp.expect('reviews: a review',
    format($q$INSERT INTO reviews (product_id, user_id, rating, title, body) VALUES (%s, %s, 5, 'Great', 'Lovely pot')$q$, :pid, :uid), '{}');
SELECT pg_temp.expect('reviews: a second review of the same product by the same user is rejected',
    format($q$INSERT INTO reviews (product_id, user_id, rating) VALUES (%s, %s, 1)$q$, :pid, :uid), '{23505}');
SELECT pg_temp.expect('reviews: the same user may review another product',
    format($q$INSERT INTO reviews (product_id, user_id, rating) VALUES (57, %s, 4)$q$, :uid), '{}');
SELECT pg_temp.expect('reviews: rating 6 is rejected',
    format($q$INSERT INTO reviews (product_id, user_id, rating) VALUES (58, %s, 6)$q$, :uid), '{23514}');
SELECT pg_temp.expect('reviews: rating 0 is rejected',
    format($q$INSERT INTO reviews (product_id, user_id, rating) VALUES (58, %s, 0)$q$, :uid), '{23514}');
SELECT pg_temp.expect('reviews: title and body are optional',
    format($q$INSERT INTO reviews (product_id, user_id, rating) VALUES (58, %s, 3)$q$, :uid), '{}');

-- --- cart_items ---------------------------------------------------------------
SELECT pg_temp.expect('cart: add a product',
    format($q$INSERT INTO cart_items (user_id, product_id, quantity) VALUES (%s, %s, 1)$q$, :uid, :pid), '{}');
SELECT pg_temp.expect('cart: the same product twice is rejected',
    format($q$INSERT INTO cart_items (user_id, product_id, quantity) VALUES (%s, %s, 1)$q$, :uid, :pid), '{23505}');
SELECT pg_temp.expect('cart: "add again" works as an upsert on (user_id, product_id)',
    format($q$INSERT INTO cart_items (user_id, product_id, quantity) VALUES (%s, %s, 2)
              ON CONFLICT (user_id, product_id) DO UPDATE SET quantity = cart_items.quantity + excluded.quantity$q$, :uid, :pid), '{}');
SELECT 'PASS  cart: quantity after the upsert is 3'
WHERE (SELECT quantity FROM cart_items WHERE user_id = :uid AND product_id = :pid) = 3
UNION ALL
SELECT 'FAIL  cart: quantity after the upsert is not 3'
WHERE (SELECT quantity FROM cart_items WHERE user_id = :uid AND product_id = :pid) IS DISTINCT FROM 3;
SELECT pg_temp.expect('cart: quantity 0 is rejected',
    format($q$INSERT INTO cart_items (user_id, product_id, quantity) VALUES (%s, 57, 0)$q$, :uid), '{23514}');

-- --- product_price_history ----------------------------------------------------
SELECT pg_temp.expect('price history: two prices',
    format($q$INSERT INTO product_price_history (product_id, price, valid_from)
              VALUES (%1$s, 12.00, '2025-01-01'), (%1$s, 10.00, '2025-06-01')$q$, :pid), '{}');
SELECT pg_temp.expect('price history: two prices valid from the same moment are rejected',
    format($q$INSERT INTO product_price_history (product_id, price, valid_from) VALUES (%s, 11.00, '2025-06-01')$q$, :pid), '{23505,23P01}');
SELECT pg_temp.expect('price history: negative price is rejected',
    format($q$INSERT INTO product_price_history (product_id, price, valid_from) VALUES (%s, -1, '2025-07-01')$q$, :pid), '{23514}');
SELECT pg_temp.expect('price history: valid_from is required',
    format($q$INSERT INTO product_price_history (product_id, price) VALUES (%s, 9.00)$q$, :pid), '{23502}');

-- --- lifecycle: what happens when parents are deleted ---------------------------
INSERT INTO users (email, full_name) VALUES ('acceptance.leaver@example.com', 'Leaving User')
RETURNING id AS leaver \gset
INSERT INTO addresses (user_id, recipient, line1, city, postal_code, country_code, is_default)
VALUES (:leaver, 'Leaving User', '9 Exit Rd', 'Testville', '12345', 'US', true);
INSERT INTO cart_items (user_id, product_id, quantity) VALUES (:leaver, 57, 1);
SELECT pg_temp.expect('users: deleting a user without orders or reviews removes their addresses and cart',
    format($q$DELETE FROM users WHERE id = %s$q$, :leaver), '{}');
SELECT CASE WHEN count(*) = 0 THEN 'PASS  users: no addresses or cart items left behind'
            ELSE 'FAIL  users: ' || count(*) || ' addresses/cart items left behind' END
FROM (SELECT user_id FROM addresses WHERE user_id = :leaver
      UNION ALL SELECT user_id FROM cart_items WHERE user_id = :leaver) AS leftovers;

INSERT INTO products (seller_id, category_id, sku, title, price, status)
VALUES (5, 13, 'ACC-TEST-2', 'Short-lived Planter', 5.00, 'draft')
RETURNING id AS pid2 \gset
INSERT INTO product_price_history (product_id, price, valid_from) VALUES (:pid2, 5.00, '2025-06-01');
INSERT INTO reviews (product_id, user_id, rating) VALUES (:pid2, 8, 4);
SELECT pg_temp.expect('products: deleting a never-ordered product removes its reviews and price history',
    format($q$DELETE FROM products WHERE id = %s$q$, :pid2), '{}');

\pset tuples_only off
\pset format aligned
ROLLBACK;
