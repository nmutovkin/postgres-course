-- =============================================================================
-- measure_fixed.sql  --  Module 12 milestone: measure the fixed queries
-- -----------------------------------------------------------------------------
-- Run after your fixes (or after fixed_queries.sql, which creates the
-- statistics object and the BRIN index):
--   ./project/bin/psql.sh bazaar < modules/12-query-performance/solutions/measure_fixed.sql
-- =============================================================================
SET search_path = bazaar, public;
SET max_parallel_workers_per_gather = 0;
SET random_page_cost = 1.1;                 -- the S3 fix, for this session
\i /course/modules/12-query-performance/solutions/plan_check.sql
SET statement_timeout = '30s';

SELECT * FROM pg_temp.plan_check('S1 keyset page', $q$
    SELECT id, user_id, status, total, created_at FROM orders
    WHERE seller_id = 1 AND (created_at, id) < ('2023-10-07 08:23:06+00', 59156)
    ORDER BY created_at DESC, id DESC LIMIT 20 $q$);
SELECT * FROM pg_temp.plan_check('S2 join seller avg', $q$
    SELECT o.id, o.seller_id, o.total FROM orders AS o
    JOIN (SELECT seller_id, avg(total) AS avg_total FROM orders GROUP BY seller_id) AS sa
      ON sa.seller_id = o.seller_id
    WHERE o.created_at >= '2025-12-24' AND o.total > 3 * sa.avg_total
    ORDER BY o.total DESC $q$);
SELECT * FROM pg_temp.plan_check('S3 berlin shops', $q$
    SELECT s.shop_name, count(*) AS orders, sum(o.total) AS revenue
    FROM addresses AS a
    JOIN orders AS o ON o.shipping_address_id = a.id
    JOIN sellers AS s ON s.id = o.seller_id
    WHERE a.country_code = 'DE' AND a.city = 'Berlin' AND o.created_at >= '2025-01-01'
    GROUP BY s.shop_name ORDER BY revenue DESC LIMIT 10 $q$);
SELECT * FROM pg_temp.plan_check('S4 never ordered', $q$
    SELECT count(*) FROM users AS u
    WHERE NOT EXISTS (SELECT 1 FROM orders AS o WHERE o.user_id = u.id) $q$);
SELECT * FROM pg_temp.plan_check('S5 daily revenue', $q$
    SELECT date_trunc('day', created_at, 'UTC') AS day, count(*) AS orders, sum(total) AS revenue
    FROM orders WHERE created_at >= timestamptz '2025-12-25 00:00+00'
    GROUP BY 1 ORDER BY 1 $q$);
BEGIN;
SET LOCAL work_mem = '64MB';
SELECT * FROM pg_temp.plan_check('S6 best sellers', $q$
    SELECT product_id, sum(quantity) AS units FROM order_items
    GROUP BY product_id ORDER BY units DESC LIMIT 10 $q$);
COMMIT;

RESET statement_timeout;
RESET random_page_cost;
