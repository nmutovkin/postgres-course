-- Exercise 7: move rows out of a DEFAULT partition into proper monthly partitions.
-- Needs partition_maintenance.sql (create_monthly_partition).
SET search_path = bazaar, public;

CREATE OR REPLACE FUNCTION rescue_default_rows(p_parent regclass)
RETURNS TABLE (partition_name text, rows_moved bigint)
LANGUAGE plpgsql
AS $$
DECLARE
    v_default  regclass;
    v_key      text;
    v_month    date;
    v_name     text;
    v_moved    bigint;
BEGIN
    SELECT c.oid::regclass INTO v_default
    FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
    WHERE i.inhparent = p_parent AND pg_get_expr(c.relpartbound, c.oid) = 'DEFAULT';
    IF v_default IS NULL THEN
        RAISE EXCEPTION '% has no default partition', p_parent;
    END IF;

    -- The partition key column, e.g. occurred_at (single-column RANGE keys only).
    SELECT a.attname INTO v_key
    FROM pg_partitioned_table pt
    JOIN pg_attribute a ON a.attrelid = pt.partrelid AND a.attnum = pt.partattrs[0]
    WHERE pt.partrelid = p_parent AND pt.partstrat = 'r' AND pt.partnatts = 1;
    IF v_key IS NULL THEN
        RAISE EXCEPTION '% is not RANGE-partitioned on a single column', p_parent;
    END IF;

    -- Lock the default partition: nobody may add rows to it while we empty it.
    EXECUTE format('LOCK TABLE %s IN EXCLUSIVE MODE', v_default);

    FOR v_month IN
        EXECUTE format('SELECT DISTINCT date_trunc(''month'', %I, ''UTC'')::date FROM %s ORDER BY 1',
                       v_key, v_default)
    LOOP
        EXECUTE format('CREATE TEMP TABLE rescued ON COMMIT DROP AS
                            WITH d AS (DELETE FROM %s
                                       WHERE %I >= %L AND %I < %L
                                       RETURNING *)
                            SELECT * FROM d',
                       v_default,
                       v_key, v_month::text || ' 00:00:00+00',
                       v_key, (v_month + interval '1 month')::date::text || ' 00:00:00+00');
        GET DIAGNOSTICS v_moved = ROW_COUNT;

        v_name := create_monthly_partition(p_parent, v_month);
        IF v_name IS NULL THEN
            RAISE EXCEPTION 'month % is covered by another partition', v_month;
        END IF;

        -- OVERRIDING SYSTEM VALUE keeps the ids of an identity column.
        EXECUTE format('INSERT INTO %s OVERRIDING SYSTEM VALUE SELECT * FROM rescued', p_parent);
        DROP TABLE rescued;

        partition_name := v_name;
        rows_moved := v_moved;
        RETURN NEXT;
    END LOOP;
END;
$$;
