-- =============================================================================
-- bench_setup.sql  --  Module 10 acceptance tests: setup for the pgbench runs
-- -----------------------------------------------------------------------------
-- Run on a database that has YOUR place_order() and job queue (or the
-- reference: ./project/bin/reset.sh 11). Re-running it resets the experiment.
--
--   ./project/bin/psql.sh < modules/10-transactions-concurrency/solutions/bench_setup.sql
--
-- What it prepares:
--   * schema bench (dropped and recreated), outside the Bazaar schema
--   * bench.try_place_order(): calls place_order() and records the outcome
--     (ok / SQLSTATE) instead of letting the error abort the pgbench client
--   * bench.work_one(): one queue worker iteration (dequeue, "process", complete)
--   * stock for the contested products: exactly 40 units AVAILABLE each
--   * 2,000 ready jobs in queue 'bench'
-- =============================================================================

SET search_path = bazaar, public;

DROP SCHEMA IF EXISTS bench CASCADE;
CREATE SCHEMA bench;

-- One row per place_order() attempt.
CREATE TABLE bench.outcomes (
    id         bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    bigint      NOT NULL,
    items      jsonb       NOT NULL,
    sqlstate   text        NOT NULL,          -- '00000' = success
    order_ids  bigint[],
    at         timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- Stock baseline of the contested products, so the check can compute deltas.
CREATE TABLE bench.baseline AS
SELECT product_id, quantity, reserved
FROM inventory
WHERE product_id IN (1, 24, 47);

-- The highest order id before the run: orders above it were created by the bench.
CREATE TABLE bench.order_watermark AS
SELECT coalesce(max(id), 0) AS max_order_id FROM orders;

-- Make exactly 40 units available for each contested product.
-- (quantity = reserved + 40 keeps the existing reservations of pending orders intact.)
UPDATE inventory
SET quantity = reserved + 40
WHERE product_id IN (1, 24, 47);

UPDATE bench.baseline b
SET quantity = i.quantity
FROM inventory i
WHERE i.product_id = b.product_id;

-- Wrapper: pgbench aborts a client on any error other than a serialization
-- failure or a deadlock, so business errors (BZ001...) are caught here and
-- recorded. Deadlocks are caught and recorded too: the test asserts there are none.
CREATE FUNCTION bench.try_place_order(p_user_id bigint, p_items jsonb)
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    v_ids bigint[];
BEGIN
    BEGIN
        SELECT array_agg(o ORDER BY o) INTO v_ids
        FROM bazaar.place_order(p_user_id, p_items) AS o;
    EXCEPTION WHEN OTHERS THEN
        INSERT INTO bench.outcomes (user_id, items, sqlstate)
        VALUES (p_user_id, p_items, SQLSTATE);
        RETURN SQLSTATE;
    END;
    INSERT INTO bench.outcomes (user_id, items, sqlstate, order_ids)
    VALUES (p_user_id, p_items, '00000', v_ids);
    RETURN '00000';
END;
$$;

-- ---------------------------------------------------------------------------
-- Queue: 2,000 jobs, and a log of which worker processed which job.
-- ---------------------------------------------------------------------------
DELETE FROM jobs WHERE queue = 'bench';

INSERT INTO jobs (queue, kind, payload, run_at)
SELECT 'bench', 'bench_noop', jsonb_build_object('n', g), now() - interval '1 minute'
FROM generate_series(1, 2000) AS g;

CREATE TABLE bench.processed (
    job_id     bigint      NOT NULL,       -- deliberately NOT unique: duplicates must be visible
    worker     text        NOT NULL,
    at         timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- One worker iteration. Returns the job id, or NULL when the queue is empty.
-- Dequeue and completion happen in the same transaction here (the pgbench
-- statement), which is the simplest correct worker for short jobs.
CREATE FUNCTION bench.work_one(p_worker text)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_job bazaar.jobs;
BEGIN
    SELECT * INTO v_job FROM bazaar.dequeue_jobs('bench', p_worker, 1);
    IF v_job.id IS NULL THEN
        RETURN NULL;
    END IF;
    INSERT INTO bench.processed (job_id, worker) VALUES (v_job.id, p_worker);
    PERFORM bazaar.complete_job(v_job.id);
    RETURN v_job.id;
END;
$$;

SELECT b.product_id, b.quantity, b.reserved, b.quantity - b.reserved AS available
FROM bench.baseline b
ORDER BY b.product_id;

-- ---------------------------------------------------------------------------
-- Milestone extension: concurrent cancellations (needs cancel_order()).
-- plpgsql resolves names at run time, so this is created even if
-- cancel_order() does not exist yet.
-- ---------------------------------------------------------------------------
CREATE TABLE bench.cancel_outcomes (
    order_id  bigint NOT NULL,
    result    text   NOT NULL,      -- 'true' / 'false' / SQLSTATE
    at        timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE FUNCTION bench.try_cancel(p_order_id bigint)
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    v_result text;
BEGIN
    BEGIN
        v_result := bazaar.cancel_order(p_order_id)::text;
    EXCEPTION WHEN OTHERS THEN
        v_result := SQLSTATE;
    END;
    INSERT INTO bench.cancel_outcomes (order_id, result) VALUES (p_order_id, v_result);
    RETURN v_result;
END;
$$;
