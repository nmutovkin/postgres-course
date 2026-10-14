-- =============================================================================
-- storage_report.sql  --  Module 16 project milestone (reference solution)
-- -----------------------------------------------------------------------------
-- A read-only report on how Bazaar's tables are stored. Five sections, each a
-- single query over the catalogs and statistics views (no table scans, so it is
-- safe to run on a busy server):
--
--   1. Where the bytes are: heap, TOAST, FSM+VM, indexes per table
--   2. Row anatomy: average row size and rows per page, expected vs actual
--   3. Update health: HOT ratio and dead tuples
--   4. Freeze health: XID and multixact age vs. the anti-wraparound limit
--   5. TOAST usage: how many tables actually store values out of line
--
-- Run:  docker exec -i course-pg psql -U postgres -d bazaar < modules/16-internals/solutions/storage_report.sql
-- Partitions of `events` are listed individually (the partitioned parent has
-- no storage of its own).
-- =============================================================================

SET search_path = bazaar, public;

\echo '== 1. Where the bytes are (top 12 by total size) =='
SELECT c.relname                                                        AS table_name,
       pg_size_pretty(pg_relation_size(c.oid))                          AS heap,
       pg_size_pretty(coalesce(pg_total_relation_size(nullif(c.reltoastrelid, 0)), 0)) AS toast,
       pg_size_pretty(pg_relation_size(c.oid, 'fsm') + pg_relation_size(c.oid, 'vm')) AS fsm_vm,
       pg_size_pretty(pg_indexes_size(c.oid))                           AS indexes,
       pg_size_pretty(pg_total_relation_size(c.oid))                    AS total,
       round(100.0 * pg_total_relation_size(c.oid) / pg_database_size(current_database()), 1) AS pct_of_db
FROM pg_class c
WHERE c.relnamespace = 'bazaar'::regnamespace
  AND c.relkind IN ('r', 'm')
ORDER BY pg_total_relation_size(c.oid) DESC
LIMIT 12;

\echo '== 2. Row anatomy: bytes per row and rows per 8 kB page =='
-- avg_row_bytes: data (sum of pg_stats.avg_width) + 24-byte tuple header + 4-byte line pointer.
-- expected_rows_per_page ignores alignment padding and null bitmaps, so it is an upper bound.
SELECT c.relname                                                AS table_name,
       c.reltuples::bigint                                      AS rows,
       c.relpages                                               AS pages,
       w.data_bytes + 28                                        AS avg_row_bytes,
       floor(8168 * coalesce((SELECT substr(o, 12)::int FROM unnest(c.reloptions) AS o
                              WHERE o LIKE 'fillfactor=%'), 100) / 100.0
             / (w.data_bytes + 28))::int                        AS expected_rows_per_page,
       round(c.reltuples / nullif(c.relpages, 0))::int          AS actual_rows_per_page
FROM pg_class c
JOIN LATERAL (SELECT sum(s.avg_width) AS data_bytes
              FROM pg_stats s
              WHERE s.schemaname = 'bazaar' AND s.tablename = c.relname) w ON true
WHERE c.relnamespace = 'bazaar'::regnamespace
  AND c.relkind = 'r'
  AND c.relpages > 0
  AND w.data_bytes IS NOT NULL
ORDER BY c.relpages DESC
LIMIT 12;

\echo '== 3. Update health: HOT ratio and dead tuples (tables with updates or dead tuples) =='
SELECT s.relname                                                     AS table_name,
       s.n_tup_upd,
       s.n_tup_hot_upd,
       round(100.0 * s.n_tup_hot_upd / nullif(s.n_tup_upd, 0), 1)    AS hot_pct,
       s.n_live_tup,
       s.n_dead_tup,
       round(100.0 * s.n_dead_tup / nullif(s.n_live_tup + s.n_dead_tup, 0), 1) AS dead_pct,
       coalesce((SELECT o FROM unnest(c.reloptions) AS o WHERE o LIKE 'fillfactor=%'), '-') AS fillfactor,
       greatest(s.last_vacuum, s.last_autovacuum)                    AS last_vacuumed
FROM pg_stat_user_tables s
JOIN pg_class c ON c.oid = s.relid
WHERE s.schemaname = 'bazaar'
  AND (s.n_tup_upd > 0 OR s.n_dead_tup > 0)
ORDER BY s.n_tup_upd DESC;

\echo '== 4. Freeze health (oldest first) =='
SELECT c.relname                                                     AS table_name,
       age(c.relfrozenxid)                                           AS xid_age,
       round(100.0 * age(c.relfrozenxid)
             / current_setting('autovacuum_freeze_max_age')::bigint, 2) AS pct_of_freeze_max_age,
       mxid_age(c.relminmxid)                                        AS multixact_age,
       (SELECT round(100.0 * v.all_frozen / nullif(c.relpages, 0), 1)
        FROM pg_visibility_map_summary(c.oid) v)                     AS pct_pages_all_frozen
FROM pg_class c
WHERE c.relnamespace = 'bazaar'::regnamespace
  AND c.relkind IN ('r', 'm')
ORDER BY age(c.relfrozenxid) DESC
LIMIT 8;

\echo '== 5. TOAST usage =='
-- Every table with a potentially large column has a TOAST table; few use it.
SELECT count(*)                                                       AS tables_with_toast_table,
       count(*) FILTER (WHERE pg_relation_size(t.oid) > 0)            AS tables_with_toasted_values,
       coalesce(string_agg(c.relname || ' (' || pg_size_pretty(pg_relation_size(t.oid)) || ')', ', ')
                FILTER (WHERE pg_relation_size(t.oid) > 0), '-')      AS which
FROM pg_class c
JOIN pg_class t ON t.oid = c.reltoastrelid
WHERE c.relnamespace = 'bazaar'::regnamespace
  AND c.relkind IN ('r', 'm');
