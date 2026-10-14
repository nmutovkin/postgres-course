-- Exercise 11: merge the three monthly partitions of Q1 2025 into one
-- quarterly partition, events_y2025q1, keeping the lock on events short.
-- The switch ends with ROLLBACK (and the staging table is dropped) so that
-- events keeps its monthly layout; use COMMIT to keep the result.
SET search_path = bazaar, public;
\timing on

-- 1. Prepare outside any lock: Q1 is closed (no new rows arrive), so we can
--    copy it while it is still attached and in use.
--    LIKE copies columns, NOT NULL, defaults and CHECK constraints (same
--    names), but not the identity (a partition must not have its own).
--    The bounds CHECK lets ATTACH skip its validation scan.
CREATE TABLE events_y2025q1 (
    LIKE events INCLUDING DEFAULTS INCLUDING CONSTRAINTS,
    CONSTRAINT events_y2025q1_bounds
        CHECK (occurred_at >= '2025-01-01 00:00:00+00' AND occurred_at < '2025-04-01 00:00:00+00')
);

INSERT INTO events_y2025q1
SELECT * FROM events
WHERE occurred_at >= '2025-01-01 00:00:00+00' AND occurred_at < '2025-04-01 00:00:00+00'
ORDER BY occurred_at;

-- The same indexes as the parent's partitioned indexes: ATTACH will adopt
-- them instead of building new ones under its lock.
SET maintenance_work_mem = '512MB';
ALTER TABLE events_y2025q1 ADD PRIMARY KEY (id, occurred_at);
CREATE INDEX ON events_y2025q1 (occurred_at);
CREATE INDEX ON events_y2025q1 (product_id);
RESET maintenance_work_mem;
VACUUM (ANALYZE) events_y2025q1;

-- 2. The switch: catalog changes only.
BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE events DETACH PARTITION events_y2025m01;
ALTER TABLE events DETACH PARTITION events_y2025m02;
ALTER TABLE events DETACH PARTITION events_y2025m03;
ALTER TABLE events ATTACH PARTITION events_y2025q1
    FOR VALUES FROM ('2025-01-01 00:00:00+00') TO ('2025-04-01 00:00:00+00');
ALTER TABLE events_y2025q1 DROP CONSTRAINT events_y2025q1_bounds;
DROP TABLE events_y2025m01, events_y2025m02, events_y2025m03;

-- Checks
SELECT count(*) AS q1_rows FROM events_y2025q1;
SELECT count(*) AS all_rows FROM events;
EXPLAIN (COSTS OFF)
SELECT count(*) FROM events WHERE occurred_at >= '2025-02-01' AND occurred_at < '2025-03-01';

ROLLBACK;                      -- COMMIT to keep the quarterly partition
DROP TABLE events_y2025q1;     -- only after ROLLBACK
