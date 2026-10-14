-- Acceptance checks for the module 09 milestone.
-- Run after applying your migration on top of ./project/bin/reset.sh 09:
--   docker exec -i course-pg psql -X -U postgres -d bazaar < modules/09-views-functions-triggers/solutions/project_checks.sql
-- Everything that writes runs in a transaction that is rolled back.

\echo '1. triggers (expect 8 rows: 5 row-level, 3 statement-level)'
SELECT tgrelid::regclass AS table_name, tgname, pg_get_triggerdef(oid) LIKE '%FOR EACH ROW%' AS row_level
FROM pg_trigger
WHERE NOT tgisinternal
  AND tgrelid IN ('products'::regclass, 'inventory'::regclass, 'orders'::regclass, 'order_items'::regclass)
ORDER BY 1, 2;

\echo '2. orders.updated_at was backfilled from the lifecycle timestamps (expect 0)'
SELECT count(*) AS wrong_updated_at
FROM orders
WHERE updated_at IS DISTINCT FROM greatest(created_at, paid_at, shipped_at, delivered_at);

\echo '3. set_updated_at (expect t, t)'
BEGIN;
UPDATE products SET price = price + 1 WHERE id = 10;
UPDATE inventory SET quantity = quantity + 1 WHERE product_id = 10;
SELECT (SELECT updated_at FROM products WHERE id = 10) = now()          AS products_touched,
       (SELECT updated_at FROM inventory WHERE product_id = 10) = now() AS inventory_touched;
ROLLBACK;

\echo '4. audit log: one row for a real change, none for a no-op (expect 1 | {"id": 10} | 229.00 | 230.00 | t)'
BEGIN;
UPDATE products SET price = price WHERE id = 11;
UPDATE products SET price = price + 1 WHERE id = 10;
SELECT count(*)                       AS audit_rows,
       min(row_pk::text)              AS row_pk,
       min(old_row ->> 'price')       AS old_price,
       min(new_row ->> 'price')       AS new_price,
       bool_and(changed_by = session_user) AS by_session_user
FROM audit_log;
ROLLBACK;

\echo '5. orders.total follows its items (expect 59.97, 62.97, 0.00, and 0 inconsistent orders)'
BEGIN;
INSERT INTO orders (user_id, seller_id) VALUES (8, 2);
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
VALUES (currval('orders_id_seq'), 19, 2, 14.99),
       (currval('orders_id_seq'), 21, 1, 18.00),
       (currval('orders_id_seq'), 24, 1, 11.99);
SELECT total AS after_insert FROM orders WHERE id = currval('orders_id_seq');
UPDATE order_items SET quantity = 3 WHERE order_id = currval('orders_id_seq') AND product_id = 19;
DELETE FROM order_items WHERE order_id = currval('orders_id_seq') AND product_id = 24;
SELECT total AS after_update_delete FROM orders WHERE id = currval('orders_id_seq');
DELETE FROM order_items WHERE order_id = currval('orders_id_seq');
SELECT total AS after_emptying FROM orders WHERE id = currval('orders_id_seq');
SELECT count(*) AS inconsistent_orders
FROM orders o
WHERE o.total <> (SELECT coalesce(sum(quantity * unit_price), 0) FROM order_items WHERE order_id = o.id);
ROLLBACK;

\echo '6. active_products (expect 54 rows; product 3 available 0; no draft or archived product)'
SELECT count(*) AS active_products,
       (SELECT available FROM active_products WHERE id = 3) AS available_3,
       count(*) FILTER (WHERE id IN (5, 14, 23, 28)) AS should_be_0
FROM active_products;

\echo '7. seller_sales_daily (expect 96 rows and the revenues from NOTES.md)'
SELECT seller_id, count(*) AS days, sum(orders_count) AS orders, sum(revenue) AS revenue
FROM seller_sales_daily
GROUP BY seller_id
ORDER BY seller_id;
REFRESH MATERIALIZED VIEW CONCURRENTLY seller_sales_daily;
SELECT count(*) AS rows_after_refresh FROM seller_sales_daily;
