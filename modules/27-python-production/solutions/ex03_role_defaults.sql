-- Exercise 3: per-role, per-database defaults instead of startup options.
-- Run as a superuser (or a role with CREATEROLE that has ADMIN on these roles):
--   ./project/bin/psql.sh < modules/27-python-production/solutions/ex03_role_defaults.sql
-- IN DATABASE bazaar: only for connections to this database (the same role may
-- connect to others with different needs).

ALTER ROLE bazaar_app IN DATABASE bazaar SET statement_timeout = '5s';
ALTER ROLE bazaar_app IN DATABASE bazaar SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE bazaar_app IN DATABASE bazaar SET application_name = 'bazaar';

-- The seller portal: interactive, small queries.
ALTER ROLE bazaar_seller IN DATABASE bazaar SET statement_timeout = '3s';
ALTER ROLE bazaar_seller IN DATABASE bazaar SET idle_in_transaction_session_timeout = '30s';

-- Analysts run long reports, but must not sit in open transactions.
ALTER ROLE bazaar_analyst IN DATABASE bazaar SET statement_timeout = '15min';
ALTER ROLE bazaar_analyst IN DATABASE bazaar SET idle_in_transaction_session_timeout = '5min';

SELECT r.rolname, d.datname, s.setconfig
FROM pg_db_role_setting s
JOIN pg_roles r ON r.oid = s.setrole
LEFT JOIN pg_database d ON d.oid = s.setdatabase
WHERE r.rolname LIKE 'bazaar%'
ORDER BY 1;
