-- =============================================================================
-- bench_cancel_check.sql  --  milestone extension checks (after pgbench_cancel_orders.sql)
-- It cancels every remaining bench order, then verifies that all reserved
-- stock went back to where it was before the bench.
-- =============================================================================
SET search_path = bazaar, public;

\echo '--- concurrent cancel outcomes'
SELECT result, count(*) FROM bench.cancel_outcomes GROUP BY result ORDER BY result;

\echo '--- sweep: cancel every bench order that is still open'
SELECT count(*) FILTER (WHERE cancel_order(id)) AS cancelled_now
FROM (
    SELECT id
    FROM orders
    WHERE id > (SELECT max_order_id FROM bench.order_watermark)
      AND status IN ('pending', 'paid')
    ORDER BY id
) o;

\echo '--- verdict'
WITH checks AS (
    SELECT 'each order cancelled successfully at most once' AS check_name,
           NOT EXISTS (
               SELECT 1 FROM bench.cancel_outcomes
               WHERE result = 'true'
               GROUP BY order_id HAVING count(*) > 1) AS ok
    UNION ALL
    SELECT 'no deadlocks or unexpected errors while cancelling',
           NOT EXISTS (SELECT 1 FROM bench.cancel_outcomes WHERE result NOT IN ('true', 'false'))
    UNION ALL
    SELECT 'all bench orders are cancelled',
           NOT EXISTS (SELECT 1 FROM orders
                       WHERE id > (SELECT max_order_id FROM bench.order_watermark)
                         AND status <> 'cancelled')
    UNION ALL
    SELECT 'reserved stock is back to its pre-bench value',
           NOT EXISTS (SELECT 1 FROM bench.baseline b JOIN inventory i USING (product_id)
                       WHERE i.reserved <> b.reserved)
    UNION ALL
    SELECT 'no deadlocks in the checkouts that ran alongside',
           NOT EXISTS (SELECT 1 FROM bench.outcomes WHERE sqlstate = '40P01')
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM checks;
