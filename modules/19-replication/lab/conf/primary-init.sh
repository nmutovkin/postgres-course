#!/usr/bin/env bash
# Runs once when m19-primary's data directory is first initialised
# (mounted into /docker-entrypoint-initdb.d). The image restarts the server
# afterwards, so restart-only settings such as wal_level take effect.
set -euo pipefail

psql -v ON_ERROR_STOP=1 -U postgres -d postgres <<'SQL'
-- A dedicated role for replication connections. REPLICATION lets it open
-- walsender connections and manage replication slots; it is not a superuser.
CREATE ROLE replicator WITH LOGIN REPLICATION PASSWORD 'replicator';

-- ALTER SYSTEM (postgresql.auto.conf) rather than `postgres -c` flags, so you
-- can change these during the module and simply reload.
ALTER SYSTEM SET wal_level = 'logical';            -- physical AND logical replication (restart-only)
ALTER SYSTEM SET max_wal_senders = 10;             -- walsender processes (restart-only)
ALTER SYSTEM SET max_replication_slots = 10;       -- restart-only
ALTER SYSTEM SET max_slot_wal_keep_size = '2GB';   -- a stuck slot may pin at most 2 GB of WAL
ALTER SYSTEM SET hot_standby = 'on';               -- copied to standbys by pg_basebackup
ALTER SYSTEM SET synchronous_standby_names = 'FIRST 1 (replica1)';
ALTER SYSTEM SET track_commit_timestamp = 'on';    -- commit timestamps (used in the conflict section)
ALTER SYSTEM SET wal_log_hints = 'on';             -- required by pg_rewind (no data checksums here)
SQL

# Allow replication connections from the lab network. The image's default
# pg_hba.conf ends with "host all all all scram-sha-256", but the keyword
# `all` in the database column does NOT match replication connections:
# they need an explicit `replication` line.
echo "host replication replicator all scram-sha-256" >> "$PGDATA/pg_hba.conf"
