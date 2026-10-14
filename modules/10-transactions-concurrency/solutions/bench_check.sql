-- =============================================================================
-- bench_check.sql  --  Module 10 acceptance checks after the pgbench runs
--   ./project/bin/psql.sh < modules/10-transactions-concurrency/solutions/bench_check.sql
-- Every row of the final "verdict" query must say PASS.
-- =============================================================================
SET search_path = bazaar, public;

\echo '--- place_order outcomes by SQLSTATE (00000 = success, BZ001 = out of stock)'
SELECT sqlstate, count(*) AS attempts
FROM bench.outcomes
GROUP BY sqlstate
ORDER BY sqlstate;

\echo '--- per product: units reserved during the run vs units in the new orders'
SELECT b.product_id,
       b.quantity - b.reserved                       AS available_before,
       i.reserved - b.reserved                       AS reserved_during_run,
       coalesce(s.units_ordered, 0)                  AS units_in_new_orders,
       i.quantity - i.reserved                       AS available_after
FROM bench.baseline b
JOIN inventory i ON i.product_id = b.product_id
LEFT JOIN (
    SELECT oi.product_id, sum(oi.quantity) AS units_ordered
    FROM order_items oi
    WHERE oi.order_id > (SELECT max_order_id FROM bench.order_watermark)
    GROUP BY oi.product_id
) s ON s.product_id = b.product_id
ORDER BY b.product_id;

\echo '--- queue: processed jobs'
SELECT count(*)                         AS processed_rows,
       count(DISTINCT job_id)           AS distinct_jobs,
       count(DISTINCT worker)           AS workers_used
FROM bench.processed;

\echo '--- verdict'
WITH checks AS (
    SELECT 'no deadlocks (40P01) in place_order' AS check_name,
           NOT EXISTS (SELECT 1 FROM bench.outcomes WHERE sqlstate = '40P01') AS ok
    UNION ALL
    SELECT 'only success or BZ001 outcomes',
           NOT EXISTS (SELECT 1 FROM bench.outcomes WHERE sqlstate NOT IN ('00000', 'BZ001'))
    UNION ALL
    SELECT 'no overselling: reserved <= quantity for every product',
           NOT EXISTS (SELECT 1 FROM inventory WHERE reserved > quantity)
    UNION ALL
    SELECT 'reservations match the units in the new orders',
           NOT EXISTS (
               SELECT 1
               FROM bench.baseline b
               JOIN inventory i ON i.product_id = b.product_id
               LEFT JOIN (
                   SELECT oi.product_id, sum(oi.quantity) AS units
                   FROM order_items oi
                   WHERE oi.order_id > (SELECT max_order_id FROM bench.order_watermark)
                   GROUP BY oi.product_id
               ) s ON s.product_id = b.product_id
               WHERE i.reserved - b.reserved <> coalesce(s.units, 0))
    UNION ALL
    SELECT 'one order per seller per successful checkout',
           (SELECT count(*) FROM orders WHERE id > (SELECT max_order_id FROM bench.order_watermark))
           = (SELECT coalesce(sum(cardinality(order_ids)), 0) FROM bench.outcomes WHERE sqlstate = '00000')
    UNION ALL
    SELECT 'order totals match their items',
           NOT EXISTS (
               SELECT 1
               FROM orders o
               WHERE o.id > (SELECT max_order_id FROM bench.order_watermark)
                 AND o.total <> (SELECT sum(quantity * unit_price) FROM order_items WHERE order_id = o.id))
    UNION ALL
    SELECT 'some checkouts failed with BZ001 (the stock really ran out)',
           EXISTS (SELECT 1 FROM bench.outcomes WHERE sqlstate = 'BZ001')
    UNION ALL
    SELECT 'queue: every job processed exactly once',
           (SELECT count(*) FROM bench.processed) = 2000
           AND (SELECT count(DISTINCT job_id) FROM bench.processed) = 2000
    UNION ALL
    SELECT 'queue: all bench jobs done, each after one attempt',
           NOT EXISTS (SELECT 1 FROM jobs WHERE queue = 'bench' AND (done_at IS NULL OR attempts <> 1))
)
SELECT check_name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result
FROM checks;
