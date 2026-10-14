-- Exercise 11: a refresh that never runs twice at the same time
SET search_path = bazaar, public;

CREATE OR REPLACE FUNCTION refresh_sales_report()
RETURNS text
LANGUAGE plpgsql
AS $$
BEGIN
    -- Transaction-level and non-blocking: released automatically at COMMIT/ROLLBACK,
    -- and a second caller gets an answer immediately instead of queueing.
    -- Key: namespace 10 (module 10's "jobs") + a fixed number for this job.
    IF NOT pg_try_advisory_xact_lock(10, 1) THEN
        RETURN 'skipped';
    END IF;

    REFRESH MATERIALIZED VIEW CONCURRENTLY seller_sales_daily;
    RETURN 'refreshed';
END;
$$;
