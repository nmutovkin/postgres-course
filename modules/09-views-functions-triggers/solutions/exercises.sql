-- Module 09 exercise solutions, in order.
-- Run on a fresh state:  ./project/bin/reset.sh 10
--   docker exec -i course-pg psql -X -U postgres -d bazaar < modules/09-views-functions-triggers/solutions/exercises.sql
-- Exercise 10 deletes data (it archives orders); reset afterwards.

\echo '== Exercise 1: effective_price()'
CREATE FUNCTION effective_price(p_product_id bigint, p_at timestamptz DEFAULT now())
RETURNS numeric
LANGUAGE sql
STABLE
AS $$
    SELECT round(h.price * (1 - coalesce(pr.discount_pct, 0) / 100), 2)
    FROM (
        SELECT price
        FROM product_price_history
        WHERE product_id = p_product_id
          AND valid_from <= p_at
        ORDER BY valid_from DESC
        LIMIT 1
    ) h
    LEFT JOIN promotions pr
           ON pr.product_id = p_product_id
          AND pr.active_during @> p_at;
$$;

SELECT effective_price(1, '2024-11-26 12:00+00')  AS p1,
       effective_price(10, '2025-06-10 12:00+00') AS p10,
       effective_price(47, '2025-06-30 12:00+00') AS p47,
       effective_price(999)                       AS missing,
       effective_price(1, '2020-01-01')           AS before_listing;

SELECT count(*) AS discounted_now
FROM products
WHERE status = 'active'
  AND effective_price(id, '2025-06-30 12:00+00') < price;

\echo '== Exercise 2: low_stock on top of active_products'
CREATE VIEW low_stock AS
SELECT id, seller_id, shop_name, title, available
FROM active_products
WHERE available <= 3;

SELECT * FROM low_stock ORDER BY available, id;
EXPLAIN (COSTS OFF) SELECT * FROM low_stock;

BEGIN;
ALTER TABLE inventory ALTER COLUMN reserved TYPE bigint;
ROLLBACK;
BEGIN;
DROP VIEW active_products;
ROLLBACK;

\echo '== Exercise 3: no repricing of archived products'
CREATE FUNCTION forbid_archived_price_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'cannot change the price of archived product %', OLD.id
        USING ERRCODE = 'BZ020',
              HINT    = 'Reactivate the product first.';
END;
$$;

CREATE TRIGGER products_archived_price_guard
    BEFORE UPDATE OF price ON products
    FOR EACH ROW
    WHEN (OLD.status = 'archived' AND NEW.price IS DISTINCT FROM OLD.price)
    EXECUTE FUNCTION forbid_archived_price_change();

\set VERBOSITY verbose
UPDATE products SET price = 699.00 WHERE id = 5;                 -- fails: BZ020
\set VERBOSITY default
UPDATE products SET price = price WHERE id = 5;                  -- fine
UPDATE products SET title = 'AeroBook 13 (2023)' WHERE id = 5;   -- fine
BEGIN;
UPDATE products SET status = 'archived', price = 899.00 WHERE id = 1;   -- fine: OLD.status is active
ROLLBACK;
UPDATE products SET price = price * 0.9 WHERE seller_id = 2;     -- fails: product 23 is archived
BEGIN;
UPDATE products SET price = price * 0.9 WHERE seller_id = 2 AND status <> 'archived';
ROLLBACK;

\echo '== Exercise 4: automatic price history'
CREATE FUNCTION record_price_history()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO product_price_history (product_id, price, valid_from)
    VALUES (NEW.id, NEW.price, now())
    ON CONFLICT (product_id, valid_from) DO UPDATE SET price = EXCLUDED.price;
    RETURN NULL;
END;
$$;

CREATE TRIGGER products_price_history_ins
    AFTER INSERT ON products
    FOR EACH ROW
    EXECUTE FUNCTION record_price_history();

CREATE TRIGGER products_price_history_upd
    AFTER UPDATE OF price ON products
    FOR EACH ROW
    WHEN (OLD.price IS DISTINCT FROM NEW.price)
    EXECUTE FUNCTION record_price_history();

SELECT count(*) AS history_before FROM product_price_history;
UPDATE products SET price = 219.00 WHERE id = 10;
UPDATE products SET price = 219.00 WHERE id = 10;      -- no change: WHEN is false
UPDATE products SET title = title WHERE id = 10;       -- price not in SET: trigger not even considered
BEGIN;
UPDATE products SET price = 209.00 WHERE id = 11;
UPDATE products SET price = 199.00 WHERE id = 11;      -- same now(): ON CONFLICT keeps the last price
COMMIT;
INSERT INTO products (seller_id, category_id, sku, title, price)
VALUES (6, 6, 'SG-AUD-001', 'Sakura Earbuds', 59.00)
RETURNING id;

SELECT count(*) AS history_after FROM product_price_history;
SELECT product_id, price FROM product_price_history
WHERE product_id IN (10, 11) ORDER BY product_id, valid_from;
SELECT count(*) AS mismatches
FROM products p
WHERE p.price IS DISTINCT FROM (SELECT h.price FROM product_price_history h
                                WHERE h.product_id = p.id ORDER BY h.valid_from DESC LIMIT 1);

\echo '== Exercise 5: breadcrumbs'
CREATE FUNCTION category_breadcrumb(p_category_id int)
RETURNS TABLE (depth int, category_id int, name text, slug text)
LANGUAGE sql
STABLE
AS $$
    WITH RECURSIVE up AS (
        SELECT c.id, c.parent_id, c.name, c.slug, 0 AS steps_up
        FROM categories c
        WHERE c.id = p_category_id
        UNION ALL
        SELECT parent.id, parent.parent_id, parent.name, parent.slug, up.steps_up + 1
        FROM categories parent
        JOIN up ON parent.id = up.parent_id
    )
    SELECT (max(steps_up) OVER () - steps_up + 1)::int, id, name, slug
    FROM up
    ORDER BY 1;
$$;

CREATE FUNCTION category_path(p_category_id int, p_separator text DEFAULT ' > ')
RETURNS text
LANGUAGE sql
STABLE
AS $$
    SELECT string_agg(name, p_separator ORDER BY depth)
    FROM category_breadcrumb(p_category_id);
$$;

SELECT * FROM category_breadcrumb(16);
SELECT * FROM category_breadcrumb(21);
SELECT count(*) AS unknown FROM category_breadcrumb(999);
SELECT p.id, p.title, category_path(p.category_id) AS path
FROM products p
WHERE p.id IN (1, 17, 33, 47, 62)
ORDER BY p.id;
SELECT count(*) AS at_depth_3
FROM products p
WHERE (SELECT max(depth) FROM category_breadcrumb(p.category_id)) = 3;

\echo '== Exercise 6: exact and estimated row counts'
CREATE FUNCTION table_row_counts(p_schema text DEFAULT 'bazaar')
RETURNS TABLE (table_name text, row_count bigint, estimate bigint)
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT c.relname, c.reltuples
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = p_schema
          AND c.relkind IN ('r', 'p', 'm')
          AND NOT c.relispartition
        ORDER BY c.relname
    LOOP
        table_name := r.relname;
        estimate   := r.reltuples::bigint;
        EXECUTE format('SELECT count(*) FROM %I.%I', p_schema, r.relname) INTO row_count;
        RETURN NEXT;
    END LOOP;
END;
$$;

SELECT table_name, row_count FROM table_row_counts();
SELECT count(*) AS nope FROM table_row_counts('nope');
SELECT count(*) AS injected FROM table_row_counts('bazaar; DROP TABLE users');

\echo '== Exercise 7: product_rating_summary'
CREATE MATERIALIZED VIEW product_rating_summary AS
SELECT product_id,
       count(*)                            AS reviews,
       round(avg(rating), 2)               AS avg_rating,
       count(*) FILTER (WHERE rating >= 4) AS positive,
       max(created_at)                     AS last_review_at
FROM reviews
GROUP BY product_id;

CREATE UNIQUE INDEX product_rating_summary_product_id_key
    ON product_rating_summary (product_id);

SELECT count(*) AS products, sum(reviews) AS reviews FROM product_rating_summary;
SELECT * FROM product_rating_summary ORDER BY reviews DESC, avg_rating DESC, product_id LIMIT 3;

SELECT ap.id, ap.title, s.reviews, s.avg_rating
FROM active_products ap
LEFT JOIN product_rating_summary s ON s.product_id = ap.id
WHERE ap.category_id = 3
ORDER BY ap.id;

BEGIN;
INSERT INTO reviews (product_id, user_id, rating, title) VALUES (15, 8, 5, 'Great gaming laptop');
SELECT count(*) AS stale_rows_for_15 FROM product_rating_summary WHERE product_id = 15;
REFRESH MATERIALIZED VIEW CONCURRENTLY product_rating_summary;
SELECT product_id, reviews, avg_rating FROM product_rating_summary WHERE product_id = 15;
ROLLBACK;

\echo '== Exercise 8: forgiving price import'
CREATE FUNCTION import_prices(p_rows jsonb)
RETURNS TABLE (seller_id bigint, sku text, outcome text, error_code text, message text)
LANGUAGE plpgsql
AS $$
DECLARE
    r record;
BEGIN
    FOR r IN
        -- price as text: the cast happens inside the protected block below
        SELECT * FROM jsonb_to_recordset(p_rows) AS x(seller_id bigint, sku text, price text)
    LOOP
        seller_id  := r.seller_id;
        sku        := r.sku;
        outcome    := 'updated';
        error_code := NULL;
        message    := NULL;
        BEGIN
            UPDATE products p
            SET price = r.price::numeric
            WHERE p.seller_id = r.seller_id
              AND p.sku = r.sku;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'no product % for seller %', r.sku, r.seller_id
                    USING ERRCODE = 'no_data_found';
            END IF;
        EXCEPTION WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS error_code = RETURNED_SQLSTATE,
                                    message    = MESSAGE_TEXT;
            outcome := 'failed';
        END;
        RETURN NEXT;
    END LOOP;
END;
$$;

BEGIN;
SELECT * FROM import_prices('[
  {"seller_id": 1, "sku": "TT-LAP-001", "price": 949.00},
  {"seller_id": 1, "sku": "TT-LAP-005", "price": 699.00},
  {"seller_id": 2, "sku": "BW-SF-001",  "price": -1},
  {"seller_id": 2, "sku": "BW-XX-999",  "price": 10.00},
  {"seller_id": 3, "sku": "CC-CU-001",  "price": "cheap"},
  {"seller_id": 4, "sku": "TB-TS-001",  "price": 21.00}
]');
SELECT id, sku, price FROM products WHERE id IN (1, 47) ORDER BY id;
ROLLBACK;

\echo '== Exercise 9: audit_history()'
CREATE FUNCTION audit_history(p_table text, p_id bigint, p_column text)
RETURNS TABLE (audit_id bigint, changed_at timestamptz, operation text, old_value jsonb, new_value jsonb)
LANGUAGE sql
STABLE
AS $$
    SELECT a.id, a.changed_at, a.operation, a.old_row -> p_column, a.new_row -> p_column
    FROM audit_log a
    WHERE a.table_name = p_table
      AND a.row_pk @> jsonb_build_object('id', p_id)
      AND (a.old_row -> p_column) IS DISTINCT FROM (a.new_row -> p_column)
    ORDER BY a.id;
$$;

SELECT operation, old_value, new_value FROM audit_history('products', 11, 'price');
SELECT operation, old_value, new_value FROM audit_history('products', 5, 'title');
SELECT count(*) AS price_changes_of_5 FROM audit_history('products', 5, 'price');
SELECT operation, old_value, new_value
FROM audit_history('products', (SELECT id FROM products WHERE sku = 'SG-AUD-001'), 'price');

\echo '== Exercise 10: archive cancelled orders in batches'
CREATE TABLE orders_archive (
    LIKE orders INCLUDING DEFAULTS INCLUDING CONSTRAINTS,
    archived_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id)
);
CREATE TABLE order_items_archive (
    LIKE order_items INCLUDING DEFAULTS INCLUDING CONSTRAINTS,
    PRIMARY KEY (order_id, product_id)
);
CREATE TABLE payments_archive (
    LIKE payments INCLUDING DEFAULTS INCLUDING CONSTRAINTS,
    PRIMARY KEY (id)
);

CREATE PROCEDURE archive_cancelled_orders(p_before timestamptz, p_batch_size int DEFAULT 100)
LANGUAGE plpgsql
AS $$
DECLARE
    v_ids   bigint[];
    v_batch int := 0;
    v_total int := 0;
BEGIN
    LOOP
        SELECT array_agg(id ORDER BY id) INTO v_ids
        FROM (
            SELECT id
            FROM orders
            WHERE status = 'cancelled'
              AND created_at < p_before
            ORDER BY id
            LIMIT p_batch_size
            FOR UPDATE
        ) batch;
        EXIT WHEN v_ids IS NULL;

        INSERT INTO orders_archive      SELECT o.*, now() FROM orders o      WHERE o.id = ANY (v_ids);
        INSERT INTO order_items_archive SELECT oi.*       FROM order_items oi WHERE oi.order_id = ANY (v_ids);
        INSERT INTO payments_archive    SELECT pm.*       FROM payments pm    WHERE pm.order_id = ANY (v_ids);

        DELETE FROM payments WHERE order_id = ANY (v_ids);
        DELETE FROM orders   WHERE id = ANY (v_ids);        -- order_items follow via ON DELETE CASCADE

        v_batch := v_batch + 1;
        v_total := v_total + cardinality(v_ids);
        COMMIT;
        RAISE NOTICE 'batch %: archived orders % (total %)', v_batch, v_ids, v_total;
    END LOOP;
END;
$$;

CALL archive_cancelled_orders('2025-03-01', 2);

SELECT (SELECT count(*) FROM orders_archive)                       AS orders_archived,
       (SELECT count(*) FROM order_items_archive)                  AS items_archived,
       (SELECT count(*) FROM payments_archive)                     AS payments_archived,
       (SELECT count(*) FROM orders)                               AS orders_left,
       (SELECT array_agg(id ORDER BY id) FROM orders WHERE status = 'cancelled') AS cancelled_left,
       (SELECT total FROM orders_archive WHERE id = 73)            AS total_73,
       (SELECT count(*) FROM audit_log WHERE table_name = 'orders' AND operation = 'DELETE') AS audited_deletes;

CALL archive_cancelled_orders('2025-03-01', 2);   -- nothing left to do

\echo '== Exercise 11: order status drives inventory'
CREATE FUNCTION sync_inventory_on_status()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_was_reserved boolean := OLD.status IN ('pending', 'paid');
BEGIN
    IF v_was_reserved AND NEW.status = 'shipped' THEN
        UPDATE inventory i
        SET quantity = i.quantity - l.qty,
            reserved = i.reserved - l.qty
        FROM (SELECT product_id, sum(quantity) AS qty
              FROM order_items WHERE order_id = NEW.id GROUP BY product_id) l
        WHERE i.product_id = l.product_id;
    ELSIF v_was_reserved AND NEW.status = 'cancelled' THEN
        UPDATE inventory i
        SET reserved = i.reserved - l.qty
        FROM (SELECT product_id, sum(quantity) AS qty
              FROM order_items WHERE order_id = NEW.id GROUP BY product_id) l
        WHERE i.product_id = l.product_id;
    ELSIF v_was_reserved AND NEW.status NOT IN ('pending', 'paid') THEN
        RAISE EXCEPTION 'order % cannot go from % to %', NEW.id, OLD.status, NEW.status
            USING ERRCODE = 'BZ030';
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER orders_sync_inventory
    AFTER UPDATE OF status ON orders
    FOR EACH ROW
    WHEN (OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION sync_inventory_on_status();

SELECT product_id, quantity, reserved FROM inventory WHERE product_id IN (49, 62) ORDER BY product_id;

BEGIN;
UPDATE orders SET status = 'shipped', shipped_at = '2025-06-30 12:00+00' WHERE id = 110;
UPDATE orders SET status = 'cancelled' WHERE id = 115;
UPDATE orders SET status = 'paid', paid_at = '2025-06-30 12:00+00' WHERE id = 116;
SELECT product_id, quantity, reserved FROM inventory WHERE product_id IN (49, 62) ORDER BY product_id;
SELECT count(*) AS mismatches
FROM inventory i
LEFT JOIN (
    SELECT oi.product_id, sum(oi.quantity) AS open_units
    FROM order_items oi
    JOIN orders o ON o.id = oi.order_id
    WHERE o.status IN ('pending', 'paid')
    GROUP BY oi.product_id
) r ON r.product_id = i.product_id
WHERE i.reserved <> coalesce(r.open_units, 0);
\set VERBOSITY verbose
UPDATE orders SET status = 'delivered' WHERE id = 117;
\set VERBOSITY default
ROLLBACK;

\echo '== Exercise 12: row-level versus statement-level totals'
BEGIN;
-- (a) the reference statement-level triggers
SELECT count(*) AS order_updates_before FROM audit_log WHERE table_name = 'orders' AND operation = 'UPDATE' \gset
INSERT INTO orders (user_id, seller_id) SELECT 8, 1 FROM generate_series(1, 200);
\timing on
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT o.id, p.id, 1, p.price
FROM (SELECT id FROM orders ORDER BY id DESC LIMIT 200) o
CROSS JOIN (SELECT id, price FROM products WHERE id BETWEEN 1 AND 10) p;
\timing off
SELECT count(*) - :order_updates_before AS statement_level_order_updates
FROM audit_log WHERE table_name = 'orders' AND operation = 'UPDATE';
SELECT count(*) AS wrong_totals
FROM orders o
WHERE o.total <> (SELECT coalesce(sum(quantity * unit_price), 0) FROM order_items WHERE order_id = o.id);

-- (b) one row-level trigger instead
DROP TRIGGER order_items_total_ins ON order_items;
DROP TRIGGER order_items_total_upd ON order_items;
DROP TRIGGER order_items_total_del ON order_items;

CREATE FUNCTION order_items_maintain_total_row()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        UPDATE orders SET total = total - OLD.quantity * OLD.unit_price WHERE id = OLD.order_id;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        UPDATE orders SET total = total + NEW.quantity * NEW.unit_price WHERE id = NEW.order_id;
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER order_items_total_row
    AFTER INSERT OR UPDATE OR DELETE ON order_items
    FOR EACH ROW EXECUTE FUNCTION order_items_maintain_total_row();

SELECT count(*) AS order_updates_before FROM audit_log WHERE table_name = 'orders' AND operation = 'UPDATE' \gset
INSERT INTO orders (user_id, seller_id) SELECT 8, 1 FROM generate_series(1, 200);
\timing on
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT o.id, p.id, 1, p.price
FROM (SELECT id FROM orders ORDER BY id DESC LIMIT 200) o
CROSS JOIN (SELECT id, price FROM products WHERE id BETWEEN 1 AND 10) p;
\timing off
SELECT count(*) - :order_updates_before AS row_level_order_updates
FROM audit_log WHERE table_name = 'orders' AND operation = 'UPDATE';
SELECT count(*) AS wrong_totals
FROM orders o
WHERE o.total <> (SELECT coalesce(sum(quantity * unit_price), 0) FROM order_items WHERE order_id = o.id);
ROLLBACK;
