-- Exercise 10: fast lookups of an event by id, without the partition key.
SET search_path = bazaar, public;

-- One row per partition: which ids it holds. Ids come from one sequence and
-- are assigned at insert time, so each month holds (almost) a contiguous range.
CREATE TABLE IF NOT EXISTS events_id_ranges (
    partition_name  text        PRIMARY KEY,
    from_at         timestamptz NOT NULL,     -- the partition's bounds
    to_at           timestamptz NOT NULL,
    min_id          bigint      NOT NULL,
    max_id          bigint      NOT NULL
);

-- Recompute the ranges of non-empty partitions. Cheap: min/max of the
-- primary key's leading column is one index probe per partition... almost.
-- (Run it from the monthly maintenance, and for the current month whenever
-- you need the newest ids to be findable; see event_by_id's fallback.)
CREATE OR REPLACE PROCEDURE refresh_events_id_ranges()
LANGUAGE plpgsql
AS $$
DECLARE
    p record;
    v_min bigint;
    v_max bigint;
BEGIN
    FOR p IN
        SELECT c.relname, b.lower_bound, b.upper_bound
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        CROSS JOIN LATERAL partition_bounds(c.oid::regclass) b
        WHERE i.inhparent = 'events'::regclass
    LOOP
        EXECUTE format('SELECT min(id), max(id) FROM %I', p.relname) INTO v_min, v_max;
        IF v_min IS NULL THEN
            DELETE FROM events_id_ranges WHERE partition_name = p.relname;
        ELSE
            INSERT INTO events_id_ranges VALUES (p.relname, p.lower_bound, p.upper_bound, v_min, v_max)
            ON CONFLICT (partition_name) DO UPDATE
                SET from_at = EXCLUDED.from_at, to_at = EXCLUDED.to_at,
                    min_id = EXCLUDED.min_id, max_id = EXCLUDED.max_id;
        END IF;
    END LOOP;
    DELETE FROM events_id_ranges r
    WHERE NOT EXISTS (SELECT 1 FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
                      WHERE i.inhparent = 'events'::regclass AND c.relname = r.partition_name);
END;
$$;

CREATE OR REPLACE FUNCTION event_by_id(p_id bigint)
RETURNS SETOF events
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_from timestamptz;
    v_to   timestamptz;
BEGIN
    SELECT min(from_at), max(to_at) INTO v_from, v_to
    FROM events_id_ranges
    WHERE p_id BETWEEN min_id AND max_id;

    IF v_from IS NULL THEN
        -- Unknown id (newer than the last refresh, or nonexistent):
        -- fall back to the slow path that probes every partition.
        RETURN QUERY SELECT * FROM events WHERE id = p_id;
        RETURN;
    END IF;

    -- Dynamic SQL with literal bounds: planned for these values, so the
    -- planner prunes at plan time (a cached generic plan could not).
    RETURN QUERY EXECUTE format(
        'SELECT * FROM events WHERE id = %s AND occurred_at >= %L AND occurred_at < %L',
        p_id, v_from, v_to);
END;
$$;
