-- =============================================================================
-- schema_check.sql  --  Module 02 acceptance check, part 1: structure
-- -----------------------------------------------------------------------------
-- Prints a canonical description of the seven core tables in schema bazaar:
-- columns (type, nullability, default, identity, generated) and constraints.
-- Run it against your database and against the reference (reset.sh 03), then
-- diff the two outputs. No differences = your schema matches the reference.
--
--   docker exec -i course-pg psql -X -U postgres -d bazaar \
--       < modules/02-types-and-ddl/solutions/schema_check.sql > mine.txt
-- =============================================================================
\set QUIET on
\pset footer off
\pset null '(null)'

\echo '== tables =='
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'bazaar'
  AND table_type = 'BASE TABLE'
ORDER BY table_name;

\echo '== columns =='
SELECT c.table_name,
       c.column_name,
       format_type(a.atttypid, a.atttypmod)      AS type,
       c.is_nullable                              AS nullable,
       coalesce(c.column_default,
                CASE WHEN c.is_identity = 'YES'
                     THEN 'identity ' || c.identity_generation
                END)                              AS default_or_identity
FROM information_schema.columns AS c
JOIN pg_attribute AS a
  ON a.attrelid = format('%I.%I', c.table_schema, c.table_name)::regclass
 AND a.attname  = c.column_name
WHERE c.table_schema = 'bazaar'
ORDER BY c.table_name, c.ordinal_position;

\echo '== constraints (NOT NULL is covered by the column list) =='
SELECT conrelid::regclass::text   AS table_name,
       conname                    AS constraint_name,
       contype                    AS type,
       pg_get_constraintdef(oid)  AS definition
FROM pg_constraint
WHERE connamespace = 'bazaar'::regnamespace
  AND contype IN ('p', 'u', 'f', 'c')
ORDER BY conrelid::regclass::text, contype, conname;

\echo '== table comments =='
SELECT c.relname AS table_name,
       obj_description(c.oid, 'pg_class') IS NOT NULL AS has_comment
FROM pg_class AS c
WHERE c.relnamespace = 'bazaar'::regnamespace
  AND c.relkind = 'r'
ORDER BY c.relname;
