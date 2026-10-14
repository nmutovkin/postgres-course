-- =============================================================================
-- db_report.sql  --  Module 01 milestone, part 3 (reference solution)
-- -----------------------------------------------------------------------------
-- Prints a short "database report" for the database you are connected to.
--   psql -X -d bazaar -f db_report.sql
--   docker exec -i course-pg psql -X -U postgres -d bazaar < db_report.sql
-- Optional: -v min_version=170000 to change the required server version.
-- =============================================================================

\set ON_ERROR_STOP on
\set QUIET on
\pset footer off
\pset null '(none)'

-- Default for an optional variable: only set it if the caller did not pass -v.
\if :{?min_version}
\else
    \set min_version 170000
\endif

-- 1. Gather the facts into psql variables (one row, several columns).
SELECT current_database()                                   AS db,
       current_user                                         AS who,
       current_setting('server_version')                    AS version,
       current_setting('server_version_num')::int >= :min_version AS version_ok,
       pg_size_pretty(pg_database_size(current_database())) AS db_size,
       current_setting('search_path')                       AS search_path,
       (SELECT count(*)
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind IN ('r', 'p')
           AND n.nspname NOT IN ('pg_catalog', 'information_schema')
           AND n.nspname NOT LIKE 'pg\_toast%')              AS user_tables
\gset

-- 2. Header.
\echo '=============================================='
\echo ' Database report:' :db
\echo '=============================================='
\echo 'Connected as     :' :who
\echo 'Server version   :' :version
\echo 'Database size    :' :db_size
\echo 'search_path      :' :search_path

\if :version_ok
    \echo 'Version check    : OK (server_version_num >=' :min_version')'
\else
    \warn 'Version check    : FAILED (server_version_num <' :min_version')'
\endif

-- 3. Tables per schema. Schemas with zero tables are listed too, which is why
--    we start from pg_namespace and LEFT JOIN the tables.
\echo
\echo 'Tables per schema:'
-- \if understands only boolean spellings (true/false, on/off, yes/no, 1/0),
-- not "7", so turn the count into a boolean in SQL first.
SELECT (:user_tables > 0) AS has_tables \gset

SELECT n.nspname                           AS schema,
       count(c.oid)                        AS tables,
       pg_size_pretty(coalesce(sum(pg_total_relation_size(c.oid)), 0)) AS size
FROM pg_namespace AS n
LEFT JOIN pg_class AS c
       ON c.relnamespace = n.oid
      AND c.relkind IN ('r', 'p')
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg\_toast%'
  AND n.nspname NOT LIKE 'pg\_temp%'
GROUP BY n.nspname
ORDER BY n.nspname;

\if :has_tables
    \echo 'Total user tables:' :user_tables
\else
    \echo 'No user tables yet. (The Bazaar core tables arrive in module 02.)'
\endif
