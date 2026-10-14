-- Runs once, when the lab's data volume is first initialized.
-- An application role and a database for pgbench.
CREATE ROLE app LOGIN PASSWORD 'app_pw';
CREATE DATABASE bench OWNER app;
\connect bench
CREATE EXTENSION pg_stat_statements;
