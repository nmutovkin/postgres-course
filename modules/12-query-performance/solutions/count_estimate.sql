-- =============================================================================
-- count_estimate.sql  --  Module 12, exercise 12
-- -----------------------------------------------------------------------------
-- count_estimate(query) returns the planner's row estimate for a query, from
-- EXPLAIN (FORMAT JSON), without running it. Instant, approximate, and exactly
-- as good as the statistics behind it.
-- table_count_estimate(table) uses pg_class.reltuples, scaled to the table's
-- current size, the same way the planner does.
-- =============================================================================
SET search_path = bazaar, public;

CREATE OR REPLACE FUNCTION count_estimate(query text)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    plan jsonb;
BEGIN
    EXECUTE 'EXPLAIN (FORMAT JSON) ' || query INTO plan;
    RETURN (plan -> 0 -> 'Plan' ->> 'Plan Rows')::bigint;
END;
$$;

COMMENT ON FUNCTION count_estimate(text) IS
    'Planner row estimate for a query (EXPLAIN, not executed). Never pass untrusted input: the text is executed as SQL.';

CREATE OR REPLACE FUNCTION table_count_estimate(tbl regclass)
RETURNS bigint
LANGUAGE sql
STABLE
AS $$
    SELECT CASE
               WHEN c.reltuples < 0 THEN NULL          -- never vacuumed or analyzed
               WHEN c.relpages = 0 THEN 0
               ELSE round(c.reltuples / c.relpages
                          * (pg_relation_size(c.oid) / current_setting('block_size')::int))::bigint
           END
    FROM pg_class AS c
    WHERE c.oid = tbl;
$$;
