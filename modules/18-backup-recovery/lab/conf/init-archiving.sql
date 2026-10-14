-- Runs once, when the m18-primary data directory is first initialised
-- (mounted into /docker-entrypoint-initdb.d). The image restarts the server
-- after init scripts, so even restart-only settings take effect.
--
-- We use ALTER SYSTEM (which writes postgresql.auto.conf) rather than
-- `postgres -c ...` flags on purpose: command-line flags have higher priority
-- than ALTER SYSTEM, so a setting passed with -c can never be changed later
-- with ALTER SYSTEM + reload. Section 7 changes archive_command at runtime.
ALTER SYSTEM SET wal_level = 'replica';            -- enough WAL for archiving/PITR (the default)
ALTER SYSTEM SET archive_mode = 'on';              -- start the archiver (restart-only)
ALTER SYSTEM SET archive_command = '/lab/scripts/archive-wal.sh %p %f';
ALTER SYSTEM SET archive_timeout = '60s';          -- switch segments at least once a minute
ALTER SYSTEM SET summarize_wal = 'on';             -- PG17: needed for incremental pg_basebackup
ALTER SYSTEM SET max_wal_senders = 10;
ALTER SYSTEM SET log_checkpoints = 'on';
