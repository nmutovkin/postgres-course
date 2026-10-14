-- =============================================================================
-- queries.sql  --  the queries the module measures, single-node and on Citus.
--   docker exec -i m21-coord psql -U postgres -d bazaar -v seller=42 < scripts/queries.sql
-- Every query is written "shard-aware": joins between seller tables include
-- seller_id, so on Citus they are co-located joins. On a single node the extra
-- predicate costs nothing.
-- =============================================================================
\set ON_ERROR_STOP on
\pset footer off
\timing on

\echo '--- Q1 seller dashboard: monthly revenue in 2025 (one seller) ---'
SELECT date_trunc('month', o.created_at AT TIME ZONE 'UTC')::date AS month,
       count(*)                                                AS orders,
       sum(o.total)                                            AS revenue
FROM orders o
WHERE o.seller_id = :seller
  AND o.status IN ('paid', 'shipped', 'delivered')
  AND o.created_at >= '2025-01-01'
GROUP BY 1
ORDER BY 1 DESC
LIMIT 3;

\echo '--- Q2 seller dashboard: top products, Q4 2025 (one seller, 3-table join) ---'
SELECT p.id, p.title,
       sum(i.quantity)                AS units,
       sum(i.quantity * i.unit_price) AS revenue
FROM order_items i
JOIN orders o   ON o.seller_id = i.seller_id AND o.id = i.order_id
JOIN products p ON p.seller_id = i.seller_id AND p.id = i.product_id
WHERE i.seller_id = :seller
  AND o.created_at >= '2025-10-01'
  AND o.status IN ('paid', 'shipped', 'delivered')
GROUP BY p.id, p.title
ORDER BY revenue DESC
LIMIT 5;

\echo '--- Q3 global report: 2025 revenue per root category (all sellers) ---'
WITH RECURSIVE tree AS (            -- categories is a reference table: local everywhere
    SELECT id, id AS root_id, name AS root_name FROM categories WHERE parent_id IS NULL
    UNION ALL
    SELECT c.id, t.root_id, t.root_name FROM categories c JOIN tree t ON c.parent_id = t.id
)
SELECT t.root_name,
       count(DISTINCT o.id)           AS orders,
       sum(i.quantity * i.unit_price) AS revenue
FROM order_items i
JOIN orders o   ON o.seller_id = i.seller_id AND o.id = i.order_id
JOIN products p ON p.seller_id = i.seller_id AND p.id = i.product_id
JOIN tree t     ON t.id = p.category_id
WHERE o.created_at >= '2025-01-01' AND o.created_at < '2026-01-01'
  AND o.status IN ('paid', 'shipped', 'delivered')
GROUP BY t.root_name
ORDER BY revenue DESC;

\echo '--- Q4 global report: top 5 sellers of 2025 with shop name (reference join) ---'
SELECT s.id, s.shop_name, count(*) AS orders, sum(o.total) AS revenue
FROM orders o
JOIN sellers s ON s.id = o.seller_id
WHERE o.created_at >= '2025-01-01' AND o.created_at < '2026-01-01'
  AND o.status IN ('paid', 'shipped', 'delivered')
GROUP BY s.id, s.shop_name
ORDER BY revenue DESC
LIMIT 5;
