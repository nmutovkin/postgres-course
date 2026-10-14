-- check_ready.sql -- Exercise 10 (reference solution).
-- Exit status 0 when the bazaar environment is set up, 3 otherwise.
--   docker exec -i course-pg psql -X -U postgres -d bazaar < check_ready.sql; echo $?
\set ON_ERROR_STOP on
\set QUIET on

SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'bazaar') AS has_schema,
       EXISTS (SELECT 1
                 FROM pg_db_role_setting s
                 JOIN pg_database d ON d.oid = s.setdatabase
                WHERE d.datname = current_database()
                  AND s.setrole = 0
                  AND EXISTS (SELECT 1
                                FROM unnest(s.setconfig) AS cfg
                               WHERE cfg LIKE 'search\_path=bazaar%'))
           AS has_search_path
\gset

\set ok true
\if :has_schema
\else
    \warn 'missing: schema bazaar'
    \set ok false
\endif
\if :has_search_path
\else
    \warn 'missing: ALTER DATABASE ... SET search_path = bazaar, public'
    \set ok false
\endif

\if :ok
    \echo READY
\else
    -- psql has no "\exit 3", but with ON_ERROR_STOP any SQL error ends the
    -- script with exit status 3. RAISE gives us an error with a clear message.
    DO $$ BEGIN RAISE EXCEPTION 'bazaar environment is not ready'; END $$;
\endif
