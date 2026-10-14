-- =============================================================================
-- setup_bazaar.sql  --  Module 01 milestone, part 1 (reference solution)
-- -----------------------------------------------------------------------------
-- Creates the Bazaar database and schema and sets the database-level
-- search_path. Safe to run more than once (idempotent).
--
-- Run it connected to the maintenance database `postgres`:
--   psql -X -v ON_ERROR_STOP=1 -d postgres -f setup_bazaar.sql
--   docker exec -i course-pg psql -X -U postgres -v ON_ERROR_STOP=1 -d postgres < setup_bazaar.sql
-- =============================================================================

\set ON_ERROR_STOP on

-- CREATE DATABASE has no IF NOT EXISTS clause and cannot run inside a
-- transaction block or a DO block. So we ask the catalog first: need_db is
-- true only when no row in pg_database is named bazaar (README section 10.3).
SELECT count(*) = 0 AS need_db
FROM pg_database
WHERE datname = 'bazaar' \gset

\if :need_db
    CREATE DATABASE bazaar;
\endif

-- The same check as a single query (needs EXISTS from module 04):
--   SELECT 'CREATE DATABASE bazaar'
--   WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'bazaar')
--   \gexec

-- Schemas live inside a database, so switch to bazaar first.
\connect bazaar

CREATE SCHEMA IF NOT EXISTS bazaar;

-- Stored in pg_db_role_setting; applied to every NEW session in this database.
ALTER DATABASE bazaar SET search_path = bazaar, public;

-- The ALTER DATABASE above does not affect the current session, so set it here too
-- in case the learner keeps using this session.
SET search_path = bazaar, public;

\echo 'bazaar database, schema and search_path are ready.'
