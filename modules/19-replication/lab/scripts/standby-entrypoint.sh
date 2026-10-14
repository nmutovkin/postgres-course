#!/usr/bin/env bash
# Entrypoint for the standby containers (m19-replica1, m19-replica2, ...).
# On first start (empty data directory) it clones the upstream server with
# pg_basebackup, then hands over to the normal postgres image entrypoint,
# which sees an existing data directory and simply starts the server.
#
# Environment:
#   UPSTREAM_HOST   server to clone and stream from (primary, or a standby for cascading)
#   SLOT_NAME       physical replication slot to create/use on the upstream
#   PGPASSWORD      password of the replicator role
set -euo pipefail
: "${UPSTREAM_HOST:?}" "${SLOT_NAME:?}"

if [[ ! -s "$PGDATA/PG_VERSION" ]]; then
    echo "standby-entrypoint: waiting for $UPSTREAM_HOST"
    until pg_isready -q -h "$UPSTREAM_HOST" -U replicator -d postgres; do sleep 1; done

    # Create the slot first (idempotent), so WAL is retained for us from now on.
    psql -h "$UPSTREAM_HOST" -U replicator -d postgres -v ON_ERROR_STOP=1 -qAt -c "
        SELECT pg_create_physical_replication_slot('$SLOT_NAME')
        WHERE NOT EXISTS (SELECT FROM pg_replication_slots WHERE slot_name = '$SLOT_NAME');" >/dev/null

    mkdir -p "$PGDATA"
    chown postgres:postgres "$PGDATA"
    chmod 700 "$PGDATA"
    echo "standby-entrypoint: cloning $UPSTREAM_HOST into $PGDATA"
    # -R writes standby.signal + primary_conninfo; -S streams through our slot;
    # -X stream copies the WAL needed to make the copy consistent; -c fast asks
    # for an immediate checkpoint instead of waiting for the next one.
    gosu postgres pg_basebackup -h "$UPSTREAM_HOST" -U replicator -D "$PGDATA" \
        -X stream -R -S "$SLOT_NAME" -c fast
    if [[ -n "${RECOVERY_MIN_APPLY_DELAY:-}" ]]; then
        echo "recovery_min_apply_delay = '$RECOVERY_MIN_APPLY_DELAY'" >> "$PGDATA/postgresql.auto.conf"
    fi
fi

exec docker-entrypoint.sh "$@"
