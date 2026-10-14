#!/usr/bin/env bash
# Scripted point-in-time-recovery drill for Bazaar. Run it from the HOST
# (it drives docker), from modules/18-backup-recovery/lab:
#
#   ./scripts/pitr-drill.sh            # full drill, removes the drill container afterwards
#   KEEP_DRILL=1 ./scripts/pitr-drill.sh   # leave m18-drill running to poke at it
#
# The drill proves, end to end, that "latest base backup + archived WAL" can
# bring Bazaar back to an exact moment:
#   1. snapshot the row count of every table in schema bazaar (one statement,
#      so one consistent MVCC snapshot) and mark that moment with a named
#      restore point;
#   2. let the database move on: in the lab we generate writes with
#      scripts/bazaar-activity.sql (set GENERATE_ACTIVITY=0 on a server that
#      has real traffic; the drill itself never modifies data);
#   3. force a WAL switch so everything is archived;
#   4. restore the newest base backup into a throw-away volume, configure
#      restore_command + recovery_target_name, start it as container m18-drill;
#   5. wait for recovery to reach the target and promote;
#   6. compare the restored row counts with the snapshot from step 1;
#   7. report PASS/FAIL and how long the restore took (your measured RTO).
set -euo pipefail

PRIMARY="${PRIMARY:-m18-primary}"
IMAGE="${IMAGE:-m18-postgres-pgbackrest:17}"
DRILL="${DRILL:-m18-drill}"
DRILL_VOLUME="${DRILL_VOLUME:-m18-drill-pgdata}"
BACKUPS_VOLUME="${BACKUPS_VOLUME:-m18-lab_backups}"
ARCHIVE_VOLUME="${ARCHIVE_VOLUME:-m18-lab_archive}"
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

log() { echo "$(date -u +%T) pitr-drill: $*"; }
psql_primary() { docker exec -i "$PRIMARY" psql -X -U postgres -d bazaar -v ON_ERROR_STOP=1 -qAt "$@"; }
psql_drill()   { docker exec -i "$DRILL"   psql -X -U postgres -d bazaar -v ON_ERROR_STOP=1 -qAt "$@"; }

# One SELECT that counts every table in schema bazaar (sorted by name).
count_query() {
    "$1" -c "
        SELECT string_agg(
                   format('SELECT %L AS t, count(*) AS n FROM bazaar.%I', relname, relname),
                   ' UNION ALL ' ORDER BY relname)
        FROM pg_class
        WHERE relnamespace = 'bazaar'::regnamespace
          AND relkind IN ('r', 'p')       -- tables and partitioned parents
          AND NOT relispartition;         -- partitions are counted through their parent"
}

# ---- 0. preconditions ---------------------------------------------------------
latest=$(docker exec "$PRIMARY" sh -c "ls -1d /backups/base/base-* 2>/dev/null | grep -v partial | sort | tail -n1" || true)
[[ -n "$latest" ]] || { echo "no base backup found: run nightly-backup.sh first" >&2; exit 1; }
log "using base backup $latest"

# ---- 1. snapshot + restore point -------------------------------------------------
q=$(count_query psql_primary)
psql_primary -F' ' -c "$q ORDER BY 1" > "$WORK/expected.txt"
point="drill-$(date -u +%Y%m%dT%H%M%SZ)"
psql_primary -c "SELECT pg_create_restore_point('$point')" >/dev/null
log "restore point '$point' created; $(wc -l < "$WORK/expected.txt") tables snapshotted"

# ---- 2. let the database move on -------------------------------------------------
if [[ "${GENERATE_ACTIVITY:-1}" == "1" ]]; then
    psql_primary < "$HERE/bazaar-activity.sql" >/dev/null
    log "post-snapshot activity generated"
fi
psql_primary -F' ' -c "$q ORDER BY 1" > "$WORK/current.txt"

# ---- 3. make sure the WAL containing all of this is archived -------------------
seg=$(psql_primary -c "SELECT pg_walfile_name(pg_switch_wal())")
for _ in $(seq 60); do
    docker exec "$PRIMARY" test -f "/archive/wal/$seg" && break
    sleep 1
done
docker exec "$PRIMARY" test -f "/archive/wal/$seg" || { echo "segment $seg was not archived in 60s" >&2; exit 1; }
log "WAL archived up to $seg"

# ---- 4. restore into a throw-away container ------------------------------------
t0=$(date +%s)
docker rm -f "$DRILL" >/dev/null 2>&1 || true
docker volume rm "$DRILL_VOLUME" >/dev/null 2>&1 || true
docker volume create "$DRILL_VOLUME" >/dev/null
docker run --rm \
    -v "$BACKUPS_VOLUME":/backups:ro \
    -v "$DRILL_VOLUME":/var/lib/postgresql/data \
    "$IMAGE" bash -euc "
        cp -a '$latest'/. /var/lib/postgresql/data/
        cd /var/lib/postgresql/data
        rm -f backup_manifest
        # Settings appended last win over earlier lines in the same file.
        cat >> postgresql.auto.conf <<CONF
# ---- added by pitr-drill.sh ----
archive_mode = 'off'                      # a drill must NEVER write into the real archive
restore_command = 'cp /archive/wal/%f %p'
recovery_target_name = '$point'
recovery_target_action = 'promote'
CONF
        touch recovery.signal
        chown -R postgres:postgres /var/lib/postgresql/data
        chmod 700 /var/lib/postgresql/data"
docker run -d --name "$DRILL" \
    -v "$DRILL_VOLUME":/var/lib/postgresql/data \
    -v "$ARCHIVE_VOLUME":/archive:ro \
    "$IMAGE" postgres >/dev/null
log "drill server $DRILL started, replaying WAL"

# ---- 5. wait until recovery finished and the server was promoted ---------------
for _ in $(seq 300); do
    if [[ "$(docker inspect -f '{{.State.Running}}' "$DRILL")" != "true" ]]; then
        docker logs "$DRILL" 2>&1 | tail -n 20
        echo "drill server died during recovery" >&2; exit 1
    fi
    state=$(docker exec "$DRILL" psql -X -U postgres -qAt -c "SELECT pg_is_in_recovery()" 2>/dev/null || true)
    [[ "$state" == "f" ]] && break
    sleep 1
done
[[ "$state" == "f" ]] || { echo "recovery did not finish in 300s" >&2; exit 1; }
rto=$(( $(date +%s) - t0 ))
docker logs "$DRILL" 2>&1 | grep -E "recovery stopping|selected new timeline|archive recovery complete" | sed 's/^/    /'

# ---- 6. verify --------------------------------------------------------------------
psql_drill -F' ' -c "$q ORDER BY 1" > "$WORK/restored.txt"
printf '%-24s %10s %10s %10s\n' table expected current restored
paste -d' ' "$WORK/expected.txt" "$WORK/current.txt" "$WORK/restored.txt" |
    awk '{ printf "%-24s %10s %10s %10s%s\n", $1, $2, $4, $6, ($2 == $6 ? "" : "   <-- MISMATCH") }'

# ---- 7. verdict -----------------------------------------------------------------
if cmp -s "$WORK/expected.txt" "$WORK/restored.txt"; then
    log "PASS: restored counts match the snapshot at '$point' (restore took ${rto}s)"
    rc=0
else
    log "FAIL: restored counts differ from the snapshot at '$point'"
    rc=1
fi
if [[ -z "${KEEP_DRILL:-}" ]]; then
    docker rm -f "$DRILL" >/dev/null
    docker volume rm "$DRILL_VOLUME" >/dev/null
    log "drill container and volume removed"
fi
exit "$rc"
