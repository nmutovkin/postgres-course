-- Exercise 8: postgres_fdw pushdown puzzles (on m21-fdw-coord after fdw-setup.sh).
SET search_path = dist, public;
CREATE FUNCTION public.with_vat(numeric) RETURNS numeric
    LANGUAGE sql IMMUTABLE AS $$ SELECT round($1 * 1.20, 2) $$;
CREATE FUNCTION public.with_vat_plpgsql(p numeric) RETURNS numeric
    LANGUAGE plpgsql IMMUTABLE AS $$ BEGIN RETURN round(p * 1.20, 2); END $$;

\echo a
EXPLAIN (VERBOSE, COSTS OFF) SELECT count(*) FROM orders WHERE created_at > '2025-06-30'::date - 30;
\echo b
EXPLAIN (VERBOSE, COSTS OFF) SELECT count(*) FROM orders WHERE created_at > timestamptz '2025-05-31 00:00+00';
\echo c
EXPLAIN (VERBOSE, COSTS OFF) SELECT id FROM orders WHERE with_vat(total) > 1000;
\echo d
EXPLAIN (VERBOSE, COSTS OFF) SELECT id FROM orders WHERE with_vat_plpgsql(total) > 1000;
\echo e
EXPLAIN (VERBOSE, COSTS OFF) SELECT seller_id, max(total) FROM orders GROUP BY seller_id;
SET enable_partitionwise_aggregate = on;
EXPLAIN (VERBOSE, COSTS OFF) SELECT seller_id, max(total) FROM orders GROUP BY seller_id;
RESET enable_partitionwise_aggregate;
\echo f
EXPLAIN (VERBOSE, COSTS OFF) SELECT status, count(*), sum(total) FROM orders_p1 WHERE seller_id = 3 GROUP BY status;
\echo g
EXPLAIN (VERBOSE, COSTS OFF)
SELECT o.id, p.title
FROM order_items i
JOIN products p ON p.id = i.product_id
JOIN orders o   ON o.id = i.order_id
WHERE o.seller_id = 2 AND o.id = 1;
\echo g-rewritten
EXPLAIN (VERBOSE, COSTS OFF)
SELECT o.id, p.title
FROM order_items i
JOIN products p ON p.seller_id = i.seller_id AND p.id = i.product_id
JOIN orders o   ON o.seller_id = i.seller_id AND o.id = i.order_id
WHERE o.seller_id = 2 AND o.id = 1;

SELECT count(*) AS a_result FROM orders WHERE created_at > '2025-06-30'::date - 30;
SELECT count(*) AS c_result FROM orders WHERE with_vat(total) > 1000;
DROP FUNCTION public.with_vat(numeric), public.with_vat_plpgsql(numeric);
