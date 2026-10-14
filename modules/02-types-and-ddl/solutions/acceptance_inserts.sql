-- =============================================================================
-- acceptance_inserts.sql  --  Module 02 acceptance check, part 2: behaviour
-- -----------------------------------------------------------------------------
-- Inserts a handful of valid rows, then tries 20 statements that a correct
-- core schema must REJECT, and reports which constraint stopped each one.
-- Everything runs in one transaction that is rolled back at the end, so the
-- script leaves your database exactly as it found it (identity sequences aside).
--
-- The helper below is a tiny PL/pgSQL function; module 09 explains how such
-- functions work. You only need to read its output: every row should say PASS.
--
--   docker exec -i course-pg psql -X -U postgres -d bazaar \
--       < modules/02-types-and-ddl/solutions/acceptance_inserts.sql
-- =============================================================================
\set QUIET on
\set ON_ERROR_STOP on
\pset footer off
SET search_path = bazaar, public;

BEGIN;

-- Runs one statement and returns the SQLSTATE and the constraint (or a short
-- message) it failed with, or 'no error' if it succeeded.
CREATE FUNCTION pg_temp.try(stmt text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
    v_state text;
    v_constraint text;
    v_message text;
BEGIN
    EXECUTE stmt;
    RETURN 'no error';
EXCEPTION WHEN OTHERS THEN
    GET STACKED DIAGNOSTICS v_state = RETURNED_SQLSTATE,
                            v_constraint = CONSTRAINT_NAME,
                            v_message = MESSAGE_TEXT;
    RETURN v_state || ' ' || CASE WHEN v_constraint <> '' THEN v_constraint ELSE v_message END;
END $$;

-- Valid baseline data. Fixed ids keep the tests readable.
INSERT INTO users (id, email, full_name) OVERRIDING SYSTEM VALUE VALUES
    (9001, 'ada@example.com', 'Ada Shopper'),
    (9002, 'sam@example.com', 'Sam Seller');
INSERT INTO sellers (id, user_id, shop_name, country_code) OVERRIDING SYSTEM VALUE VALUES
    (9001, 9002, 'Test Shop', 'DE');
INSERT INTO categories (id, parent_id, name, slug) OVERRIDING SYSTEM VALUE VALUES
    (9001, NULL, 'Test Root', 'test-root'),
    (9002, 9001, 'Test Leaf', 'test-leaf');
INSERT INTO products (id, seller_id, category_id, sku, title, price) OVERRIDING SYSTEM VALUE VALUES
    (9001, 9001, 9002, 'T-1', 'Test Product', 10.00);
INSERT INTO inventory (product_id, quantity, reserved) VALUES (9001, 5, 1);
INSERT INTO orders (id, user_id, seller_id) OVERRIDING SYSTEM VALUE VALUES (9001, 9001, 9001);
INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (9001, 9001, 1, 10.00);

CREATE TEMP TABLE expectations (n int, description text, stmt text, expected text);
INSERT INTO expectations VALUES
 ( 1, 'user without a name',              $$INSERT INTO users (email, full_name) VALUES ('x@example.com', NULL)$$, '23502'),
 ( 2, 'duplicate email',                  $$INSERT INTO users (email, full_name) VALUES ('ada@example.com', 'Ada 2')$$, '23505 users_email_key'),
 ( 3, 'explicit user id',                 $$INSERT INTO users (id, email, full_name) VALUES (1, 'y@example.com', 'Y')$$, '428C9'),
 ( 4, 'second shop for the same user',    $$INSERT INTO sellers (user_id, shop_name, country_code) VALUES (9002, 'Other Shop', 'DE')$$, '23505 sellers_user_id_key'),
 ( 5, 'duplicate shop name',              $$INSERT INTO sellers (user_id, shop_name, country_code) VALUES (9001, 'Test Shop', 'DE')$$, '23505 sellers_shop_name_key'),
 ( 6, 'lower-case country code',          $$INSERT INTO sellers (user_id, shop_name, country_code) VALUES (9001, 'Shop B', 'de')$$, '23514 sellers_country_code_upper'),
 ( 7, 'shop for a missing user',          $$INSERT INTO sellers (user_id, shop_name, country_code) VALUES (424242, 'Shop C', 'DE')$$, '23503 sellers_user_id_fkey'),
 ( 8, 'category that is its own parent',  $$UPDATE categories SET parent_id = 9002 WHERE id = 9002$$, '23514 categories_not_own_parent'),
 ( 9, 'duplicate category slug',          $$INSERT INTO categories (name, slug) VALUES ('Again', 'test-leaf')$$, '23505 categories_slug_key'),
 (10, 'negative price',                   $$INSERT INTO products (seller_id, category_id, sku, title, price) VALUES (9001, 9002, 'T-2', 'P', -0.01)$$, '23514 products_price_nonnegative'),
 (11, 'unknown product status',           $$INSERT INTO products (seller_id, category_id, sku, title, price, status) VALUES (9001, 9002, 'T-3', 'P', 1, 'live')$$, '23514 products_status_check'),
 (12, 'duplicate SKU within a seller',    $$INSERT INTO products (seller_id, category_id, sku, title, price) VALUES (9001, 9002, 'T-1', 'P', 1)$$, '23505 products_seller_id_sku_key'),
 (13, 'product in a missing category',    $$INSERT INTO products (seller_id, category_id, sku, title, price) VALUES (9001, 424242, 'T-4', 'P', 1)$$, '23503 products_category_id_fkey'),
 (14, 'negative stock',                   $$UPDATE inventory SET quantity = -1, reserved = 0 WHERE product_id = 9001$$, '23514 inventory_quantity_nonnegative'),
 (15, 'reserving more than in stock',     $$UPDATE inventory SET reserved = 6 WHERE product_id = 9001$$, '23514 inventory_reserved_within_quantity'),
 (16, 'second inventory row',             $$INSERT INTO inventory (product_id, quantity) VALUES (9001, 1)$$, '23505 inventory_pkey'),
 (17, 'unknown order status',             $$INSERT INTO orders (user_id, seller_id, status) VALUES (9001, 9001, 'lost')$$, '23514 orders_status_check'),
 (18, 'zero quantity line',               $$INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (9001, 9001, 0, 10)$$, '23514 order_items_quantity_positive'),
 (19, 'same product twice in an order',   $$INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (9001, 9001, 2, 10)$$, '23505 order_items_pkey'),
 (20, 'deleting a product that was sold', $$DELETE FROM products WHERE id = 9001$$, '23503 order_items_product_id_fkey');

SELECT e.n,
       e.description,
       t.got,
       CASE WHEN t.got LIKE e.expected || '%' THEN 'PASS'
            ELSE 'FAIL (expected ' || e.expected || ')'
       END AS result
FROM expectations AS e
CROSS JOIN LATERAL (SELECT pg_temp.try(e.stmt) AS got) AS t
ORDER BY e.n;

-- Two things that must SUCCEED: cascades.
DELETE FROM orders WHERE id = 9001;            -- takes its order_items with it
DELETE FROM products WHERE id = 9001;          -- now allowed; takes its inventory row with it
SELECT 'cascades' AS check,
       CASE WHEN NOT EXISTS (SELECT 1 FROM order_items WHERE order_id = 9001)
             AND NOT EXISTS (SELECT 1 FROM inventory WHERE product_id = 9001)
            THEN 'PASS' ELSE 'FAIL' END AS result;

ROLLBACK;
