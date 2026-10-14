-- =============================================================================
-- Module 21 milestone: acceptance checks for the sharded Bazaar.
-- Run on the Citus coordinator after your own distribution scripts (or the
-- reference ones: lab/scripts/citus-build.sh small):
--   docker exec -i m21-coord psql -U postgres -d bazaar < solutions/milestone-checks.sql
-- =============================================================================
SET search_path = bazaar, public;
\pset footer off

\echo '--- A1 every table has a deliberate role ---'
SELECT table_name, citus_table_type, distribution_column, colocation_id
FROM citus_tables
ORDER BY colocation_id, table_name::text;

\echo '--- A2 tables left local on the coordinator (expect audit_log, events + partitions, jobs) ---'
SELECT count(*) FILTER (WHERE c.relname LIKE 'events%') AS events_relations,
       string_agg(c.relname, ', ' ORDER BY c.relname) FILTER (WHERE c.relname NOT LIKE 'events%') AS others
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'bazaar' AND c.relkind IN ('r', 'p')
  AND c.oid NOT IN (SELECT logicalrelid FROM pg_dist_partition);

\echo '--- A3 no data lost (small dataset: 30 / 62 / 120 / 235 / 55341.98) ---'
SELECT (SELECT count(*) FROM users)       AS users,
       (SELECT count(*) FROM products)    AS products,
       (SELECT count(*) FROM orders)      AS orders,
       (SELECT count(*) FROM order_items) AS order_items,
       (SELECT sum(total) FROM orders)    AS sum_total;

\echo '--- A4 orders.total still equals the sum of its items (expect 0) ---'
SELECT count(*) AS mismatches
FROM orders o
LEFT JOIN (SELECT seller_id, order_id, sum(quantity * unit_price) AS items
           FROM order_items GROUP BY seller_id, order_id) i
       ON i.seller_id = o.seller_id AND i.order_id = o.id
WHERE o.total <> coalesce(i.items, 0);

\echo '--- A5 the seller dashboard is a router query (expect Task Count: 1) ---'
EXPLAIN (COSTS OFF)
SELECT p.id, p.title, sum(i.quantity) AS units
FROM order_items i
JOIN orders o   ON o.seller_id = i.seller_id AND o.id = i.order_id
JOIN products p ON p.seller_id = i.seller_id AND p.id = i.product_id
WHERE i.seller_id = 3 AND o.status IN ('paid', 'shipped', 'delivered')
GROUP BY p.id, p.title;

\echo '--- A6 global report: revenue per seller (expect 41598.82 / 1656.43 / 3562.94 / 2277.00) ---'
SELECT s.id, s.shop_name, sum(o.total) AS revenue
FROM orders o
JOIN sellers s ON s.id = o.seller_id
WHERE o.status IN ('paid', 'shipped', 'delivered')
GROUP BY s.id, s.shop_name
ORDER BY s.id;

\echo '--- A7 the views work on the distributed schema ---'
SELECT (SELECT count(*) FROM active_products) AS active_products,
       (SELECT count(*) FROM seller_sales_daily) AS sales_days;

\echo '--- A8 RLS: seller 2 sees only its own 36 orders ---'
BEGIN;
SET LOCAL ROLE bazaar_seller;
SET LOCAL app.seller_id = '2';
SELECT count(*) AS visible_orders, count(DISTINCT seller_id) AS sellers FROM orders;
ROLLBACK;

\echo '--- A9 a two-seller checkout as the application role (rolled back) ---'
BEGIN;
SET LOCAL ROLE bazaar_app;
SELECT * FROM place_order_sharded(13,
    '[{"seller_id": 1, "product_id": 1, "quantity": 2}, {"seller_id": 3, "product_id": 40, "quantity": 1}]');
SELECT o.seller_id, o.total, count(i.*) AS lines
FROM orders o
JOIN order_items i ON i.seller_id = o.seller_id AND i.order_id = o.id
WHERE o.user_id = 13 AND o.status = 'pending' AND o.created_at > now() - interval '1 minute'
GROUP BY o.seller_id, o.total
ORDER BY o.seller_id;
ROLLBACK;

\echo '--- A10 checkout errors keep their SQLSTATEs (expect BZ001) ---'
\set ON_ERROR_STOP off
\set VERBOSITY verbose
SELECT * FROM place_order_sharded(13, '[{"seller_id": 1, "product_id": 3, "quantity": 1}]');
\set VERBOSITY default
