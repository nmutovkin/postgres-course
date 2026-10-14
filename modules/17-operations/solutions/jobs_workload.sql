-- =============================================================================
-- jobs_workload.sql  --  Module 17 project milestone: acceptance workload
-- -----------------------------------------------------------------------------
-- Enqueues 20,000 jobs and works through them with dequeue_jobs() /
-- complete_job() in batches of 200, committing after every batch, like a real
-- worker would. Each job leaves two dead row versions behind (the lease update
-- and the completion), so this produces about 40,000 dead tuples.
-- Then it waits (up to 3 minutes) until autovacuum has processed `jobs`.
--
-- Run:  docker exec -i course-pg psql -U postgres -d bazaar < modules/17-operations/solutions/jobs_workload.sql
-- Must run outside an explicit transaction (the DO blocks COMMIT).
-- =============================================================================

SET search_path = bazaar, public;

-- Remember the current autovacuum count in a session variable (a custom setting).
SELECT set_config('workload.av_before', autovacuum_count::text, false) AS autovacuum_count_before
FROM pg_stat_user_tables WHERE relname = 'jobs';

INSERT INTO jobs (queue, kind, payload)
SELECT 'email', 'send_receipt', jsonb_build_object('order_id', g)
FROM generate_series(1, 20000) AS g;

DO $$
DECLARE
    j record;
    n int;
BEGIN
    LOOP
        n := 0;
        FOR j IN SELECT * FROM dequeue_jobs('email', 'worker-1', 200) LOOP
            PERFORM complete_job(j.id);
            n := n + 1;
        END LOOP;
        COMMIT;                 -- one transaction per batch
        EXIT WHEN n = 0;
    END LOOP;
END
$$;

SELECT count(*) FILTER (WHERE done_at IS NOT NULL) AS done,
       count(*) FILTER (WHERE done_at IS NULL)     AS not_done
FROM jobs;

-- Wait for autovacuum (it wakes up every autovacuum_naptime = 1 min).
DO $$
DECLARE
    before bigint := current_setting('workload.av_before')::bigint;
BEGIN
    FOR i IN 1..36 LOOP
        PERFORM pg_stat_clear_snapshot();
        EXIT WHEN (SELECT autovacuum_count FROM pg_stat_user_tables WHERE relname = 'jobs') > before;
        PERFORM pg_sleep(5);
    END LOOP;
END
$$;

SELECT relname, n_live_tup, n_dead_tup, autovacuum_count, last_autovacuum, last_autoanalyze
FROM pg_stat_user_tables WHERE relname = 'jobs';
