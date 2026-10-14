-- =============================================================================
-- measure_slow.sql  --  Module 12 milestone: measure the six slow queries
-- -----------------------------------------------------------------------------
--   ./project/bin/psql.sh bazaar < modules/12-query-performance/solutions/measure_slow.sql
-- S4 is stopped by a 30-second statement_timeout and reports an ERROR instead
-- of a row: that means "more than 30 seconds".
-- =============================================================================
SET search_path = bazaar, public;
SET max_parallel_workers_per_gather = 0;    -- comparable, single-process plans
\i /course/modules/12-query-performance/solutions/plan_check.sql
SET statement_timeout = '30s';

SELECT * FROM pg_temp.plan_check('S1 offset page', $q$
    SELECT id, user_id, status, total, created_at FROM orders
    WHERE seller_id = 1 ORDER BY created_at DESC, id DESC LIMIT 20 OFFSET 60000 $q$);
SELECT * FROM pg_temp.plan_check('S2 above seller avg', $q$
    SELECT o.id, o.seller_id, o.total FROM orders AS o
    WHERE o.created_at >= '2025-12-24'
      AND o.total > 3 * (SELECT avg(o2.total) FROM orders AS o2 WHERE o2.seller_id = o.seller_id)
    ORDER BY o.total DESC $q$);
SELECT * FROM pg_temp.plan_check('S3 berlin shops', $q$
    SELECT s.shop_name, count(*) AS orders, sum(o.total) AS revenue
    FROM addresses AS a
    JOIN orders AS o ON o.shipping_address_id = a.id
    JOIN sellers AS s ON s.id = o.seller_id
    WHERE a.country_code = 'DE' AND a.city = 'Berlin' AND o.created_at >= '2025-01-01'
    GROUP BY s.shop_name ORDER BY revenue DESC LIMIT 10 $q$);
SELECT * FROM pg_temp.plan_check('S4 never ordered', $q$
    SELECT count(*) FROM users WHERE id NOT IN (SELECT user_id FROM orders) $q$);
SELECT * FROM pg_temp.plan_check('S5 daily revenue', $q$
    SELECT created_at::date AS day, count(*) AS orders, sum(total) AS revenue
    FROM orders WHERE created_at::date >= date '2025-12-25'
    GROUP BY 1 ORDER BY 1 $q$);
SELECT * FROM pg_temp.plan_check('S6 best sellers', $q$
    SELECT product_id, sum(quantity) AS units FROM order_items
    GROUP BY product_id ORDER BY units DESC LIMIT 10 $q$);

RESET statement_timeout;
