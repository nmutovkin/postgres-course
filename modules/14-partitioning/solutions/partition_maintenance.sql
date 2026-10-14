-- =============================================================================
-- partition_maintenance.sql  --  Module 14 milestone, part 2 (reference)
-- -----------------------------------------------------------------------------
-- Generic monthly partition maintenance for any table that is
-- RANGE-partitioned by a timestamptz column with one partition per month,
-- named <parent>_yYYYYmMM (events, and audit_log after part 3):
--
--   partition_bounds(partition)              its FROM / TO bounds as timestamptz
--   create_monthly_partition(parent, month)  create one month (idempotent)
--   CALL maintain_monthly_partitions(parent, months_ahead, retention, today, archive_schema)
--       1. creates the partitions from the current month to months_ahead ahead;
--       2. detaches every partition whose upper bound is not after
--          (start of the current month - retention), then drops it, or moves
--          it to archive_schema when one is given;
--       3. warns when the DEFAULT partition holds rows (section 6 of the theory).
--
-- Bounds are always UTC month starts, independent of the session's TimeZone.
--
--   CALL maintain_monthly_partitions('events', 3, interval '12 months');
--   CALL maintain_monthly_partitions('events', 3, interval '10 months', date '2025-12-31');
--
-- The procedure COMMITs after each detached partition so that it never holds
-- more than one partition's ACCESS EXCLUSIVE lock at a time; therefore it
-- must be CALLed outside an explicit transaction block.
-- =============================================================================

SET search_path = bazaar, public;

-- The bounds of a RANGE partition on one timestamptz column, parsed from its
-- catalog definition. MINVALUE / MAXVALUE become -infinity / infinity.
-- No row for the DEFAULT partition.
CREATE OR REPLACE FUNCTION partition_bounds(
    p_partition regclass,
    OUT lower_bound timestamptz,
    OUT upper_bound timestamptz)
RETURNS SETOF record
LANGUAGE sql
STABLE
AS $$
    SELECT CASE WHEN m[1] = 'MINVALUE' THEN '-infinity' ELSE btrim(m[1], '''') END::timestamptz,
           CASE WHEN m[2] = 'MAXVALUE' THEN 'infinity'  ELSE btrim(m[2], '''') END::timestamptz
    FROM pg_class c,
         regexp_match(pg_get_expr(c.relpartbound, c.oid), 'FROM \((.+)\) TO \((.+)\)') AS m
    WHERE c.oid = p_partition;
$$;

-- Creates <parent>_yYYYYmMM for the month containing p_month, unless that
-- month is already (even partly) covered by another partition, for example a
-- "legacy" partition attached during a migration. Returns the name of the
-- partition created or already present, or NULL when another partition
-- covers the month.
CREATE OR REPLACE FUNCTION create_monthly_partition(p_parent regclass, p_month date)
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    v_schema  text;
    v_table   text;
    v_from    date := date_trunc('month', p_month)::date;
    v_to      date := (date_trunc('month', p_month) + interval '1 month')::date;
    v_from_ts text := v_from::text || ' 00:00:00+00';
    v_to_ts   text := v_to::text   || ' 00:00:00+00';
    v_name    text;
BEGIN
    SELECT n.nspname, c.relname INTO v_schema, v_table
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.oid = p_parent AND c.relkind = 'p';
    IF NOT FOUND THEN
        RAISE EXCEPTION '% is not a partitioned table', p_parent;
    END IF;

    v_name := format('%s_y%sm%s', v_table, to_char(v_from, 'YYYY'), to_char(v_from, 'MM'));

    IF to_regclass(format('%I.%I', v_schema, v_name)) IS NOT NULL THEN
        RETURN v_name;                                  -- already there
    END IF;

    IF EXISTS (SELECT 1
               FROM pg_inherits i
               CROSS JOIN LATERAL partition_bounds(i.inhrelid::regclass) b
               WHERE i.inhparent = p_parent
                 AND b.lower_bound < v_to_ts::timestamptz
                 AND b.upper_bound > v_from_ts::timestamptz) THEN
        RETURN NULL;                                    -- covered by another partition
    END IF;

    EXECUTE format(
        'CREATE TABLE %I.%I PARTITION OF %I.%I FOR VALUES FROM (%L) TO (%L)',
        v_schema, v_name, v_schema, v_table, v_from_ts, v_to_ts);
    RETURN v_name;
END;
$$;

CREATE OR REPLACE PROCEDURE maintain_monthly_partitions(
    p_parent          regclass,
    p_months_ahead    int      DEFAULT 3,
    p_retention       interval DEFAULT interval '12 months',
    p_today           date     DEFAULT current_date,
    p_archive_schema  text     DEFAULT NULL       -- NULL = drop old partitions
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_month   date := date_trunc('month', p_today)::date;
    v_cutoff  timestamptz := (date_trunc('month', p_today) - p_retention)::date::text || ' 00:00:00+00';
    v_part    record;
    v_default regclass;
    v_rows    bigint;
    v_saved_lock_timeout text := current_setting('lock_timeout');
BEGIN
    -- Never queue behind a long-running query (and block everyone else behind
    -- us) for long: fail fast instead and let the next run retry.
    -- (A procedure that COMMITs can't have a SET clause, hence set_config.)
    PERFORM set_config('lock_timeout', '5s', false);

    -- 1. future partitions (the current month included)
    FOR i IN 0 .. p_months_ahead LOOP
        PERFORM create_monthly_partition(p_parent, (v_month + make_interval(months => i))::date);
    END LOOP;
    COMMIT;

    -- 2. retention: every non-default partition whose upper bound <= cutoff
    FOR v_part IN
        SELECT n.nspname AS schema_name,
               c.relname AS table_name,
               b.upper_bound
        FROM pg_inherits i
        JOIN pg_class c     ON c.oid = i.inhrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL partition_bounds(c.oid::regclass) b
        WHERE i.inhparent = p_parent
          AND b.upper_bound <= v_cutoff
        ORDER BY b.upper_bound
    LOOP
        EXECUTE format('ALTER TABLE %s DETACH PARTITION %I.%I',
                       p_parent, v_part.schema_name, v_part.table_name);
        IF p_archive_schema IS NULL THEN
            EXECUTE format('DROP TABLE %I.%I', v_part.schema_name, v_part.table_name);
            RAISE NOTICE 'dropped %.%', v_part.schema_name, v_part.table_name;
        ELSE
            EXECUTE format('ALTER TABLE %I.%I SET SCHEMA %I',
                           v_part.schema_name, v_part.table_name, p_archive_schema);
            RAISE NOTICE 'archived %.% to schema %', v_part.schema_name, v_part.table_name, p_archive_schema;
        END IF;
        COMMIT;                                   -- release the locks partition by partition
    END LOOP;

    -- 3. the DEFAULT partition should be empty
    SELECT c.oid::regclass INTO v_default
    FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
    WHERE i.inhparent = p_parent AND pg_get_expr(c.relpartbound, c.oid) = 'DEFAULT';
    IF v_default IS NOT NULL THEN
        EXECUTE format('SELECT count(*) FROM (SELECT 1 FROM %s LIMIT 1000) s', v_default) INTO v_rows;
        IF v_rows > 0 THEN
            RAISE WARNING '% contains rows (% or more): create the missing partitions and move them',
                v_default, v_rows;
        END IF;
    END IF;

    PERFORM set_config('lock_timeout', v_saved_lock_timeout, false);
END;
$$;

COMMENT ON PROCEDURE maintain_monthly_partitions(regclass, int, interval, date, text) IS
    'Creates monthly partitions ahead and detaches/drops (or archives) those older than the retention.';
