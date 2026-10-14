-- =============================================================================
-- plan_check.sql  --  Module 12: a tiny measuring helper (session-local)
-- -----------------------------------------------------------------------------
-- pg_temp.plan_check(label, query) runs EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
-- on the query and returns one summary row:
--   ms        execution time
--   buffers   shared buffers hit + read by the whole plan
--   temp      temp blocks written (spills to disk)
--   top_node  the plan's root node type
--   est_rows / act_rows  the root node's estimated and actual row counts
-- It lives in pg_temp, so it disappears when the session ends.
-- =============================================================================
CREATE OR REPLACE FUNCTION pg_temp.plan_check(label text, query text)
RETURNS TABLE (query_label text, ms numeric, buffers bigint, temp bigint,
               top_node text, est_rows bigint, act_rows bigint)
LANGUAGE plpgsql AS $$
DECLARE
    plan jsonb;
BEGIN
    EXECUTE 'EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' || query INTO plan;
    query_label := label;
    ms       := round((plan -> 0 ->> 'Execution Time')::numeric, 1);
    buffers  := (plan -> 0 -> 'Plan' ->> 'Shared Hit Blocks')::bigint
              + (plan -> 0 -> 'Plan' ->> 'Shared Read Blocks')::bigint;
    temp     := (plan -> 0 -> 'Plan' ->> 'Temp Written Blocks')::bigint;
    top_node := plan -> 0 -> 'Plan' ->> 'Node Type';
    est_rows := (plan -> 0 -> 'Plan' ->> 'Plan Rows')::bigint;
    act_rows := (plan -> 0 -> 'Plan' ->> 'Actual Rows')::bigint;
    RETURN NEXT;
END;
$$;
