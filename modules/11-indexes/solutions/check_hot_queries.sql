-- =============================================================================
-- check_hot_queries.sql  --  Module 11 milestone: acceptance check
-- -----------------------------------------------------------------------------
-- Runs the eleven hot queries of the Bazaar workload (project.md) under
-- EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) and prints one summary row per query:
--
--   query       the label from project.md
--   ms          execution time reported by EXPLAIN ANALYZE
--   buffers     shared buffers touched (hit + read) by the whole plan
--   seq_scans   tables read with a Seq Scan (should be empty or tiny tables)
--   indexes     indexes the plan used
--
-- Run it with
--   ./project/bin/psql.sh bazaar < modules/11-indexes/solutions/check_hot_queries.sql
-- (or \i /course/modules/11-indexes/solutions/check_hot_queries.sql in psql).
--
-- Everything runs inside BEGIN ... ROLLBACK, so the FOR UPDATE in the job
-- dequeue and the DELETE in query 11 change nothing. The script creates its
-- demo jobs inside the same transaction, so they disappear afterwards too
-- (the VACUUM at the end removes the dead rows they leave behind).
-- =============================================================================

\set QUIET on
SET search_path = bazaar, public;
SET max_parallel_workers_per_gather = 0;   -- stable, comparable plans
BEGIN;

-- The jobs table is empty in the generated dataset. Give the queue a realistic
-- shape: 200,000 finished jobs and 1,000 waiting ones.
INSERT INTO jobs (queue, kind, payload, run_at, attempts, done_at, created_at)
SELECT 'email', 'send_receipt', jsonb_build_object('order_id', g),
       timestamptz '2025-06-01' + g * interval '1 minute', 1,
       timestamptz '2025-06-01' + g * interval '1 minute' + interval '5 seconds',
       timestamptz '2025-06-01' + g * interval '1 minute'
FROM generate_series(1, 200000) AS g;
INSERT INTO jobs (queue, kind, payload, run_at, created_at)
SELECT 'email', 'send_receipt', jsonb_build_object('order_id', g),
       timestamptz '2025-12-31 12:00' + g * interval '1 second',
       timestamptz '2025-12-31 12:00' + g * interval '1 second'
FROM generate_series(1, 1000) AS g;
ANALYZE jobs;

CREATE FUNCTION pg_temp.plan_check(label text, query text)
RETURNS TABLE (query_label text, ms numeric, buffers bigint, seq_scans text, indexes text)
LANGUAGE plpgsql AS $$
DECLARE
    plan jsonb;
BEGIN
    EXECUTE 'EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' || query INTO plan;
    query_label := label;
    ms := round((plan -> 0 ->> 'Execution Time')::numeric, 2);
    buffers := (plan -> 0 -> 'Plan' ->> 'Shared Hit Blocks')::bigint
             + (plan -> 0 -> 'Plan' ->> 'Shared Read Blocks')::bigint;
    SELECT string_agg(DISTINCT r #>> '{}', ', ') INTO seq_scans
    FROM jsonb_path_query(plan, 'strict $.**?(@."Node Type" == "Seq Scan")."Relation Name"') AS r;
    SELECT string_agg(DISTINCT i #>> '{}', ', ') INTO indexes
    FROM jsonb_path_query(plan, 'strict $.**."Index Name"') AS i;
    RETURN NEXT;
END;
$$;

\set QUIET off
SELECT * FROM pg_temp.plan_check('01 my orders', $q$
    SELECT id, status, total, created_at
    FROM orders
    WHERE user_id = 4242
    ORDER BY created_at DESC
    LIMIT 20 $q$)
UNION ALL
SELECT * FROM pg_temp.plan_check('02a order items', $q$
    SELECT oi.product_id, p.title, oi.quantity, oi.unit_price
    FROM order_items AS oi
    JOIN products AS p ON p.id = oi.product_id
    WHERE oi.order_id = 250000 $q$)
UNION ALL
SELECT * FROM pg_temp.plan_check('02b order payments', $q$
    SELECT id, amount, provider, status, created_at
    FROM payments
    WHERE order_id = 250000
    ORDER BY created_at $q$)
UNION ALL
SELECT * FROM pg_temp.plan_check('03 seller dashboard', $q$
    SELECT id, user_id, status, total, created_at
    FROM orders
    WHERE seller_id = 42
      AND created_at >= '2025-12-01'
    ORDER BY created_at DESC
    LIMIT 50 $q$)
UNION ALL
SELECT * FROM pg_temp.plan_check('04 category browse', $q$
    SELECT id, title, price
    FROM products
    WHERE status = 'active'
      AND category_id = 6
    ORDER BY price
    LIMIT 24 $q$)
UNION ALL
SELECT * FROM pg_temp.plan_check('05 attribute filter', $q$
    SELECT id, title, price
    FROM products
    WHERE status = 'active'
      AND attributes @> '{"brand": "Voltix", "ram_gb": 16}' $q$)
UNION ALL
SELECT * FROM pg_temp.plan_check('06 tag filter', $q$
    SELECT id, title, price
    FROM products
    WHERE status = 'active'
      AND tags @> '{eco,limited-edition}'
    ORDER BY price
    LIMIT 24 $q$)
UNION ALL
SELECT * FROM pg_temp.plan_check('07 admin email lookup', $q$
    SELECT id, email, full_name, is_active
    FROM users
    WHERE lower(email) = lower('Mia.Hill4242@Example.org') $q$)
UNION ALL
SELECT * FROM pg_temp.plan_check('08 jobs dequeue', $q$
    SELECT id
    FROM jobs
    WHERE queue = 'email'
      AND done_at IS NULL
      AND run_at <= timestamptz '2025-12-31 23:59'
      AND attempts < max_attempts
      AND locked_at IS NULL
    ORDER BY run_at, id
    LIMIT 10
    FOR UPDATE SKIP LOCKED $q$)
UNION ALL
SELECT * FROM pg_temp.plan_check('09 buyers of a product', $q$
    SELECT o.id, o.user_id, o.created_at, oi.quantity
    FROM order_items AS oi
    JOIN orders AS o ON o.id = oi.order_id
    WHERE oi.product_id = 4242
    ORDER BY o.created_at DESC $q$)
UNION ALL
SELECT * FROM pg_temp.plan_check('10 address book', $q$
    SELECT id, label, city, country_code, is_default
    FROM addresses
    WHERE user_id = 4242 $q$)
UNION ALL
SELECT * FROM pg_temp.plan_check('11 delete a product', $q$
    DELETE FROM products
    WHERE id = 1 $q$);
-- Query 11 deletes product 1, a draft that was never ordered. Its "ms" includes
-- the foreign-key triggers that check order_items, cart_items, reviews, ...

ROLLBACK;

-- Rolled-back INSERTs still leave dead tuples (and dead index entries) behind:
-- 201,000 of them in jobs. Clean up so that the next run starts from the same state.
VACUUM jobs;
