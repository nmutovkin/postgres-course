-- =============================================================================
-- audit_log_migration.sql  --  Module 14 milestone, part 3 (reference)
-- -----------------------------------------------------------------------------
-- Converts the existing, unpartitioned audit_log (1.5M rows after
-- audit_log_seed.sql) into a table RANGE-partitioned by month on changed_at,
-- while the application keeps writing to it. Strategy: "attach the old table
-- as the first partition".
--
--   * Everything that reads the whole table (index build, constraint
--     validation) runs BEFORE the switch, under locks that don't block
--     reads or writes.
--   * The switch itself is one short transaction: catalog changes only, no
--     scan, no rewrite. It holds ACCESS EXCLUSIVE on audit_log for a few
--     milliseconds, and gives up after lock_timeout instead of queueing.
--   * The old rows stay where they are, in one big "legacy" partition
--     covering everything before the cut-over month. New rows go to monthly
--     partitions. The legacy partition is dropped as a whole once all of its
--     rows are past the retention period (or split later, see the solutions).
--
-- Requires partition_maintenance.sql (create_monthly_partition).
-- Run with psql (it uses \gset). Each numbered step is its own transaction;
-- in production you would run them one by one and check the result of each.
-- =============================================================================

SET search_path = bazaar, public;
\set ON_ERROR_STOP on
\timing on

-- -----------------------------------------------------------------------------
-- The cut-over boundary: the start of the month after next. Every existing
-- row, and every row written until the switch, is older than this, so the old
-- table can promise "changed_at < cutover" with a CHECK constraint.
-- Run the switch (step 4) well before this date: from then on, the CHECK would
-- reject the audit rows (and with them the UPDATEs that write them).
-- -----------------------------------------------------------------------------
SELECT d AS cutover, d::text || ' 00:00:00+00' AS cutover_ts       -- explicit UTC, like the monthly partitions
FROM (SELECT (date_trunc('month', now() AT TIME ZONE 'UTC') + interval '2 months')::date AS d) s
\gset
\echo cut-over boundary: :cutover_ts

-- -----------------------------------------------------------------------------
-- 1. The new partitioned table (nobody uses it yet: no locking concerns).
--    Same columns, defaults and CHECK as audit_log; the primary key must
--    include the partition key.
-- -----------------------------------------------------------------------------
CREATE TABLE audit_log_p (
    id          bigint      GENERATED ALWAYS AS IDENTITY,
    table_name  text        NOT NULL,
    operation   text        NOT NULL,
    row_pk      jsonb,
    old_row     jsonb,
    new_row     jsonb,
    changed_by  text        NOT NULL DEFAULT current_user,
    changed_at  timestamptz NOT NULL DEFAULT now(),
    -- A partition must have every CHECK constraint of the parent, under the
    -- same name: reuse the old table's name, or ATTACH fails with
    -- "child table is missing constraint".
    CONSTRAINT audit_log_operation_check CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE')),
    CONSTRAINT audit_log_p_pkey PRIMARY KEY (id, changed_at)
) PARTITION BY RANGE (changed_at);

-- Monthly partitions from the cut-over month, three months ahead of it.
SELECT create_monthly_partition('audit_log_p', (date :'cutover' + make_interval(months => i))::date)
FROM generate_series(0, 3) AS i;

-- A safety net: an audit row must never make the audited UPDATE fail just
-- because nobody created next month's partition. The maintenance procedure
-- warns when it is not empty.
CREATE TABLE audit_log_p_default PARTITION OF audit_log_p DEFAULT;

-- -----------------------------------------------------------------------------
-- 2. Give the old table the unique index the parent's primary key needs.
--    CONCURRENTLY: reads and writes continue during the build.
-- -----------------------------------------------------------------------------
SET maintenance_work_mem = '512MB';
CREATE UNIQUE INDEX CONCURRENTLY audit_log_id_changed_at_key ON audit_log (id, changed_at);
RESET maintenance_work_mem;

-- -----------------------------------------------------------------------------
-- 3. Prove the partition bound in advance, so that ATTACH doesn't scan:
--    NOT VALID: instant (checks only new rows from now on);
--    VALIDATE:  scans, but only takes SHARE UPDATE EXCLUSIVE (no blocking).
--    changed_at is already NOT NULL, which the MINVALUE lower bound needs.
-- -----------------------------------------------------------------------------
ALTER TABLE audit_log
    ADD CONSTRAINT audit_log_changed_at_before_cutover CHECK (changed_at < :'cutover_ts') NOT VALID;
ALTER TABLE audit_log VALIDATE CONSTRAINT audit_log_changed_at_before_cutover;

-- -----------------------------------------------------------------------------
-- 4. The switch: one short transaction, catalog changes only.
-- -----------------------------------------------------------------------------
BEGIN;
SET LOCAL lock_timeout = '3s';          -- don't queue behind a long query: retry later instead
LOCK TABLE audit_log IN ACCESS EXCLUSIVE MODE;

-- The parent's primary key is (id, changed_at); the old one is (id).
ALTER TABLE audit_log DROP CONSTRAINT audit_log_pkey;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_legacy_pkey PRIMARY KEY USING INDEX audit_log_id_changed_at_key;

-- A partition can't have its own identity column; ids now come from the parent's.
ALTER TABLE audit_log ALTER COLUMN id DROP IDENTITY;
SELECT setval(pg_get_serial_sequence('audit_log_p', 'id'), (SELECT max(id) FROM audit_log));

ALTER TABLE audit_log   RENAME TO audit_log_legacy;
ALTER TABLE audit_log_p RENAME TO audit_log;

ALTER TABLE audit_log
    ATTACH PARTITION audit_log_legacy FOR VALUES FROM (MINVALUE) TO (:'cutover_ts');
COMMIT;

-- -----------------------------------------------------------------------------
-- 5. Tidy up (no hurry): names that match the new table, and the CHECK
--    constraint, which the partition bound now guarantees anyway.
-- -----------------------------------------------------------------------------
ALTER TABLE audit_log_legacy DROP CONSTRAINT audit_log_changed_at_before_cutover;
ALTER TABLE audit_log RENAME CONSTRAINT audit_log_p_pkey TO audit_log_pkey;
ALTER TABLE audit_log_p_default RENAME TO audit_log_default;
ALTER SEQUENCE audit_log_p_id_seq RENAME TO audit_log_id_seq;
SELECT format('ALTER TABLE %I RENAME TO %I', c.relname, replace(c.relname, 'audit_log_p_y', 'audit_log_y'))
FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
WHERE i.inhparent = 'audit_log'::regclass AND c.relname LIKE 'audit_log_p_y%'
\gexec
COMMENT ON TABLE audit_log IS 'Row-level change history written by audit_trigger(); partitioned by month (audit_log_yYYYYmMM).';

ANALYZE audit_log;
