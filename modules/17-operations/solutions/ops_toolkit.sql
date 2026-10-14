-- =============================================================================
-- ops_toolkit.sql  --  Module 17 project milestone (reference solution)
-- -----------------------------------------------------------------------------
-- A schema `ops` of monitoring views for the Bazaar database. Every view is a
-- plain SELECT over the statistics views and catalogs, so it is cheap to query
-- and safe to run on a busy server (no pgstattuple, no table scans).
--
--   ops.long_transactions   sessions with an open transaction, oldest first
--   ops.blocking_chains     who blocks whom, as a tree rooted at the culprits
--   ops.xmin_horizon        everything that holds back VACUUM (sessions,
--                           replication slots, prepared transactions)
--   ops.top_statements      pg_stat_statements: top 20 by total execution time
--   ops.unused_indexes      non-unique indexes never scanned since stats reset
--   ops.table_bloat         estimated bloat per table, from planner statistics
--   ops.vacuum_lag          dead tuples / inserts / changes vs. the autovacuum
--                           thresholds (honouring per-table settings), plus
--                           XID age vs. autovacuum_freeze_max_age
--   ops.database_health     one row per database: cache hit ratio, rollbacks,
--                           deadlocks, temp files, XID and multixact age
--
-- Apply:   docker exec -i course-pg psql -U postgres -d bazaar -v ON_ERROR_STOP=1 \
--              < modules/17-operations/solutions/ops_toolkit.sql
-- Re-runnable: it drops and recreates the ops schema.
-- Grants: members of pg_monitor (the built-in monitoring role) may read the
-- views. They see other users' query texts because pg_monitor includes
-- pg_read_all_stats.
-- =============================================================================

DROP SCHEMA IF EXISTS ops CASCADE;
CREATE SCHEMA ops;
COMMENT ON SCHEMA ops IS 'Monitoring views for operators (module 17).';

-- -----------------------------------------------------------------------------
-- Helper: read one storage parameter from pg_class.reloptions
-- (an array of 'name=value' strings), e.g. ops.reloption(c.reloptions, 'fillfactor').
-- -----------------------------------------------------------------------------
CREATE FUNCTION ops.reloption(p_options text[], p_name text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT substr(opt, length(p_name) + 2)
    FROM unnest(p_options) AS opt
    WHERE opt LIKE p_name || '=%'
$$;

-- -----------------------------------------------------------------------------
-- 1. Long transactions
-- Every client session inside a transaction, oldest first. The ones to worry
-- about: large xact_age, especially in state 'idle in transaction'.
-- -----------------------------------------------------------------------------
CREATE VIEW ops.long_transactions AS
SELECT a.pid,
       a.usename,
       a.datname,
       a.application_name,
       a.client_addr,
       a.state,
       now() - a.xact_start   AS xact_age,
       now() - a.state_change AS in_state_for,
       greatest(age(a.backend_xid), age(a.backend_xmin)) AS horizon_age,  -- how far it holds back VACUUM
       a.wait_event_type,
       a.wait_event,
       left(regexp_replace(a.query, '\s+', ' ', 'g'), 120) AS last_query
FROM pg_stat_activity a
WHERE a.backend_type = 'client backend'
  AND a.xact_start IS NOT NULL
  AND a.pid <> pg_backend_pid()
ORDER BY a.xact_start;

COMMENT ON VIEW ops.long_transactions IS 'Client sessions with an open transaction, oldest first.';

-- -----------------------------------------------------------------------------
-- 2. Blocking chains
-- Roots are sessions that block others but are not blocked themselves: the
-- ones to fix (commit, cancel, or terminate). pid_tree is indented by depth.
-- pg_blocking_pids() understands lock queues, so a session waiting behind a
-- queued ALTER TABLE shows up under it.
-- -----------------------------------------------------------------------------
CREATE VIEW ops.blocking_chains AS
WITH RECURSIVE blocked AS (
    SELECT a.pid, pg_blocking_pids(a.pid) AS blockers
    FROM pg_stat_activity a
    WHERE a.wait_event_type = 'Lock'            -- cheap pre-filter
      AND cardinality(pg_blocking_pids(a.pid)) > 0
),
tree AS (
    SELECT a.pid, 0 AS depth, ARRAY[a.pid] AS path, a.pid AS root_pid
    FROM pg_stat_activity a
    WHERE a.pid IN (SELECT unnest(b.blockers) FROM blocked b)
      AND a.pid NOT IN (SELECT b.pid FROM blocked b)
    UNION ALL
    SELECT b.pid, t.depth + 1, t.path || b.pid, t.root_pid
    FROM tree t
    JOIN blocked b ON t.pid = ANY (b.blockers)
    WHERE b.pid <> ALL (t.path)                 -- guard against cycles
)
SELECT t.root_pid,
       repeat('    ', t.depth) || t.pid AS pid_tree,
       t.pid,
       t.depth,
       a.usename,
       a.application_name,
       a.state,
       a.wait_event_type || ':' || a.wait_event AS waiting_on,
       now() - a.xact_start  AS xact_age,
       now() - a.query_start AS query_age,
       left(regexp_replace(a.query, '\s+', ' ', 'g'), 100) AS query,
       t.path
FROM tree t
JOIN pg_stat_activity a ON a.pid = t.pid
ORDER BY t.path;

COMMENT ON VIEW ops.blocking_chains IS 'Lock wait trees; depth 0 rows are the root blockers.';

-- -----------------------------------------------------------------------------
-- 3. The xmin horizon: what prevents VACUUM from removing dead tuples
-- -----------------------------------------------------------------------------
CREATE VIEW ops.xmin_horizon AS
-- A session holds the horizon back with its snapshot (backend_xmin) and, if it
-- has written anything, with its own transaction ID (backend_xid): an idle-in-
-- transaction session in READ COMMITTED has no snapshot, but its XID still counts.
SELECT 'session' AS kind,
       a.pid::text AS holder,
       a.usename || ' / ' || coalesce(nullif(a.application_name, ''), '-') || ' / ' || a.state AS detail,
       greatest(age(a.backend_xid), age(a.backend_xmin)) AS xmin_age,
       now() - a.xact_start AS since
FROM pg_stat_activity a
WHERE (a.backend_xmin IS NOT NULL OR a.backend_xid IS NOT NULL)
  AND a.pid <> pg_backend_pid()
UNION ALL
SELECT 'replication slot',
       s.slot_name,
       s.slot_type || CASE WHEN s.active THEN ' (active)' ELSE ' (INACTIVE)' END,
       greatest(age(s.xmin), age(s.catalog_xmin)),
       NULL
FROM pg_replication_slots s
WHERE s.xmin IS NOT NULL OR s.catalog_xmin IS NOT NULL
UNION ALL
SELECT 'prepared transaction',
       p.gid,
       p.owner || ' in ' || p.database,
       age(p.transaction),
       now() - p.prepared
FROM pg_prepared_xacts p
ORDER BY xmin_age DESC NULLS LAST;

COMMENT ON VIEW ops.xmin_horizon IS 'Sessions, slots and prepared transactions holding back the xmin horizon.';

-- -----------------------------------------------------------------------------
-- 4. Top statements (needs the pg_stat_statements extension, module 12)
-- -----------------------------------------------------------------------------
CREATE VIEW ops.top_statements AS
SELECT s.queryid,
       r.rolname AS username,
       s.calls,
       round(s.total_exec_time::numeric, 1)                         AS total_ms,
       round(s.mean_exec_time::numeric, 2)                          AS mean_ms,
       round(s.max_exec_time::numeric, 2)                           AS max_ms,
       round((100 * s.total_exec_time / nullif(sum(s.total_exec_time) OVER (), 0))::numeric, 1) AS pct_total_time,
       s.rows,
       round(100.0 * s.shared_blks_hit / nullif(s.shared_blks_hit + s.shared_blks_read, 0), 1) AS cache_hit_pct,
       s.shared_blks_read,
       s.temp_blks_written,
       left(regexp_replace(s.query, '\s+', ' ', 'g'), 120) AS query
FROM pg_stat_statements s
JOIN pg_roles r ON r.oid = s.userid
WHERE s.dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
ORDER BY s.total_exec_time DESC
LIMIT 20;

COMMENT ON VIEW ops.top_statements IS 'Top 20 statements of this database by total execution time.';

-- -----------------------------------------------------------------------------
-- 5. Unused indexes
-- Non-unique indexes with zero scans since the statistics were reset. Check
-- stats_age (and your replicas!) before dropping anything.
-- -----------------------------------------------------------------------------
CREATE VIEW ops.unused_indexes AS
SELECT s.schemaname,
       s.relname      AS table_name,
       s.indexrelname AS index_name,
       s.idx_scan,
       pg_relation_size(s.indexrelid)                 AS index_bytes,
       pg_size_pretty(pg_relation_size(s.indexrelid)) AS index_size,
       now() - d.stats_reset                          AS stats_age,   -- NULL: never reset, counting since the database was created
       pg_get_indexdef(s.indexrelid)                  AS definition
FROM pg_stat_user_indexes s
JOIN pg_index i     ON i.indexrelid = s.indexrelid
JOIN pg_stat_database d ON d.datname = current_database()
WHERE s.idx_scan = 0
  AND NOT i.indisunique
  AND NOT i.indisprimary
  AND NOT EXISTS (                                -- indexes backing constraints (e.g. EXCLUDE)
      SELECT 1 FROM pg_constraint c WHERE c.conindid = s.indexrelid)
ORDER BY pg_relation_size(s.indexrelid) DESC;

COMMENT ON VIEW ops.unused_indexes IS 'Non-unique indexes never scanned since the last statistics reset.';

-- -----------------------------------------------------------------------------
-- 6. Table bloat estimate
-- expected_pages = reltuples * (tuple header 24 + line pointer 4 + avg row width)
--                  / (usable page bytes 8168 * fillfactor)
-- Ignores alignment padding and null bitmaps, so a few percent of "bloat" is
-- normal. Needs fresh statistics (ANALYZE). Partitioned parents are skipped
-- (their partitions are listed individually).
-- -----------------------------------------------------------------------------
CREATE VIEW ops.table_bloat AS
WITH t AS (
    SELECT c.oid,
           n.nspname,
           c.relname,
           c.relpages,
           c.reltuples,
           coalesce(ops.reloption(c.reloptions, 'fillfactor')::int, 100) AS fillfactor,
           (SELECT sum(s.avg_width)
            FROM pg_stats s
            WHERE s.schemaname = n.nspname AND s.tablename = c.relname) AS row_width
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'm')
      AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'ops')
      AND n.nspname NOT LIKE 'pg_toast%'
      AND c.reltuples >= 0                        -- -1 = never vacuumed/analyzed
), e AS (
    SELECT t.*,
           ceil(t.reltuples * (24 + 4 + t.row_width) / (8168 * t.fillfactor / 100.0))::bigint AS expected_pages
    FROM t
    WHERE t.row_width IS NOT NULL
)
SELECT e.nspname AS schemaname,
       e.relname AS table_name,
       e.relpages AS actual_pages,
       e.expected_pages,
       greatest(e.relpages - e.expected_pages, 0) * 8192::bigint                  AS est_bloat_bytes,
       pg_size_pretty(greatest(e.relpages - e.expected_pages, 0) * 8192::bigint)  AS est_bloat,
       CASE WHEN e.relpages > 0
            THEN greatest(round(100 * (1 - e.expected_pages::numeric / e.relpages)), 0)
       END AS est_bloat_pct,
       e.fillfactor
FROM e
ORDER BY est_bloat_bytes DESC;

COMMENT ON VIEW ops.table_bloat IS 'Estimated table bloat from planner statistics; verify with pgstattuple.';

-- -----------------------------------------------------------------------------
-- 7. Vacuum lag
-- For each table: the counters autovacuum looks at, the thresholds it compares
-- them with (per-table storage parameters override the global settings), and
-- how close each one is (pct >= 100 means "due now"). Also the XID age compared
-- with autovacuum_freeze_max_age.
-- -----------------------------------------------------------------------------
CREATE VIEW ops.vacuum_lag AS
WITH t AS (
    SELECT s.relid,
           s.schemaname,
           s.relname,
           c.reltuples,
           s.n_live_tup,
           s.n_dead_tup,
           s.n_ins_since_vacuum,
           s.n_mod_since_analyze,
           s.last_autovacuum,
           s.last_vacuum,
           s.last_autoanalyze,
           s.last_analyze,
           s.autovacuum_count,
           age(c.relfrozenxid) AS xid_age,
           coalesce(ops.reloption(c.reloptions, 'autovacuum_enabled')::boolean, true) AS av_enabled,
           coalesce(ops.reloption(c.reloptions, 'autovacuum_vacuum_threshold')::float8,
                    current_setting('autovacuum_vacuum_threshold')::float8)            AS v_thr,
           coalesce(ops.reloption(c.reloptions, 'autovacuum_vacuum_scale_factor')::float8,
                    current_setting('autovacuum_vacuum_scale_factor')::float8)         AS v_sf,
           coalesce(ops.reloption(c.reloptions, 'autovacuum_vacuum_insert_threshold')::float8,
                    current_setting('autovacuum_vacuum_insert_threshold')::float8)     AS i_thr,
           coalesce(ops.reloption(c.reloptions, 'autovacuum_vacuum_insert_scale_factor')::float8,
                    current_setting('autovacuum_vacuum_insert_scale_factor')::float8)  AS i_sf,
           coalesce(ops.reloption(c.reloptions, 'autovacuum_analyze_threshold')::float8,
                    current_setting('autovacuum_analyze_threshold')::float8)           AS a_thr,
           coalesce(ops.reloption(c.reloptions, 'autovacuum_analyze_scale_factor')::float8,
                    current_setting('autovacuum_analyze_scale_factor')::float8)        AS a_sf,
           coalesce(ops.reloption(c.reloptions, 'autovacuum_freeze_max_age')::float8,
                    current_setting('autovacuum_freeze_max_age')::float8)              AS freeze_max_age
    FROM pg_stat_user_tables s
    JOIN pg_class c ON c.oid = s.relid
    WHERE c.relkind IN ('r', 'm')          -- partitioned parents have no storage of their own
)
SELECT t.schemaname,
       t.relname AS table_name,
       t.av_enabled,
       t.n_live_tup,
       t.n_dead_tup,
       round(t.v_thr + t.v_sf * greatest(t.reltuples, 0))::bigint AS vacuum_threshold,
       round(100 * t.n_dead_tup / (t.v_thr + t.v_sf * greatest(t.reltuples, 0)))::int AS vacuum_pct,
       t.n_ins_since_vacuum,
       round(t.i_thr + t.i_sf * greatest(t.reltuples, 0))::bigint AS insert_threshold,
       round(100 * t.n_ins_since_vacuum / (t.i_thr + t.i_sf * greatest(t.reltuples, 0)))::int AS insert_pct,
       t.n_mod_since_analyze,
       round(t.a_thr + t.a_sf * greatest(t.reltuples, 0))::bigint AS analyze_threshold,
       round(100 * t.n_mod_since_analyze / (t.a_thr + t.a_sf * greatest(t.reltuples, 0)))::int AS analyze_pct,
       t.xid_age,
       round(100 * t.xid_age / t.freeze_max_age)::int AS freeze_pct,
       greatest(t.last_autovacuum, t.last_vacuum)   AS last_vacuumed,
       greatest(t.last_autoanalyze, t.last_analyze) AS last_analyzed,
       t.autovacuum_count
FROM t
ORDER BY greatest(t.n_dead_tup / (t.v_thr + t.v_sf * greatest(t.reltuples, 0)),
                  t.n_ins_since_vacuum / (t.i_thr + t.i_sf * greatest(t.reltuples, 0)),
                  t.n_mod_since_analyze / (t.a_thr + t.a_sf * greatest(t.reltuples, 0)),
                  t.xid_age / t.freeze_max_age) DESC;

COMMENT ON VIEW ops.vacuum_lag IS 'Autovacuum counters vs. thresholds per table; pct >= 100 means due.';

-- -----------------------------------------------------------------------------
-- 8. Database health: one row per database
-- -----------------------------------------------------------------------------
CREATE VIEW ops.database_health AS
SELECT d.datname,
       d.numbackends,
       current_setting('max_connections')::int AS max_connections,
       round(100.0 * d.blks_hit / nullif(d.blks_hit + d.blks_read, 0), 2)          AS cache_hit_pct,
       d.xact_commit,
       d.xact_rollback,
       round(100.0 * d.xact_rollback / nullif(d.xact_commit + d.xact_rollback, 0), 2) AS rollback_pct,
       d.deadlocks,
       d.temp_files,
       pg_size_pretty(d.temp_bytes) AS temp_bytes,
       d.checksum_failures,
       age(db.datfrozenxid)         AS xid_age,
       mxid_age(db.datminmxid)      AS multixact_age,
       pg_size_pretty(pg_database_size(d.datid)) AS size,
       d.stats_reset
FROM pg_stat_database d
JOIN pg_database db ON db.oid = d.datid
WHERE d.datname IS NOT NULL
  AND db.datallowconn
ORDER BY d.datname;

COMMENT ON VIEW ops.database_health IS 'Per-database health counters since the last statistics reset.';

-- -----------------------------------------------------------------------------
-- Access for monitoring roles
-- -----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA ops TO pg_monitor;
GRANT SELECT ON ALL TABLES IN SCHEMA ops TO pg_monitor;
GRANT EXECUTE ON FUNCTION ops.reloption(text[], text) TO pg_monitor;
