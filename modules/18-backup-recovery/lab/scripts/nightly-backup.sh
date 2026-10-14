#!/usr/bin/env bash
# Nightly physical backup for Bazaar: pg_basebackup + verification + retention.
#
# Runs INSIDE the database container as the postgres OS user:
#   docker exec -u postgres m18-primary /lab/scripts/nightly-backup.sh
# In production you would run it from cron/systemd timer on a backup host:
#   15 2 * * *  postgres  /opt/bazaar/nightly-backup.sh >> /var/log/bazaar-backup.log 2>&1
#
# What it does:
#   1. pg_basebackup a full, plain-format copy into $BACKUP_ROOT/<label>.partial
#      (WAL needed for consistency is streamed into the backup with -X stream);
#   2. pg_verifybackup checks every file against the backup manifest
#      (checksums) and that the WAL needed to make it consistent is present;
#   3. renames .partial -> final only if 1 and 2 succeeded;
#   4. appends a line to the backup catalog (used for freshness monitoring);
#   5. keeps the newest $KEEP backups and deletes older ones;
#   6. deletes archived WAL that no kept backup needs (pg_archivecleanup).
#
# WAL archiving itself is continuous and independent of this script: it is done
# by the server's archive_command (archive-wal.sh) every time a segment fills.
set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-/backups/base}"
ARCHIVE_DIR="${ARCHIVE_DIR:-/archive/wal}"
KEEP="${KEEP:-3}"
CATALOG="$BACKUP_ROOT/catalog.tsv"
export PGHOST="${PGHOST:-/var/run/postgresql}" PGUSER="${PGUSER:-postgres}"

log() { echo "$(date -u +%FT%TZ) nightly-backup: $*"; }

label="base-$(date -u +%Y%m%dT%H%M%SZ)"
target="$BACKUP_ROOT/$label"
started=$(date -u +%FT%TZ)
t0=$(date +%s)

log "starting $label"
# --checkpoint=fast: do not wait for the next scheduled checkpoint.
# --manifest-checksums=SHA256: stronger than the default CRC32C (slower).
pg_basebackup \
    --pgdata="$target.partial" \
    --format=plain \
    --wal-method=stream \
    --checkpoint=fast \
    --label="$label" \
    --manifest-checksums=SHA256 \
    --no-password

log "verifying"
pg_verifybackup --quiet "$target.partial"
mv "$target.partial" "$target"

start_wal=$(sed -n 's/^START WAL LOCATION: .* (file \([0-9A-F]*\))$/\1/p' "$target/backup_label")
size=$(du -sb "$target" | cut -f1)
secs=$(( $(date +%s) - t0 ))
[[ -f "$CATALOG" ]] || printf 'label\tstarted_at\tfinished_at\tseconds\tbytes\tstart_wal\n' > "$CATALOG"
printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$label" "$started" "$(date -u +%FT%TZ)" "$secs" "$size" "$start_wal" >> "$CATALOG"
log "done: $label, $((size / 1024 / 1024)) MB in ${secs}s, starts at WAL $start_wal"

# ---- retention -------------------------------------------------------------
mapfile -t backups < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'base-*' ! -name '*.partial' | sort)
if (( ${#backups[@]} > KEEP )); then
    for old in "${backups[@]:0:${#backups[@]}-KEEP}"; do
        log "retention: removing $(basename "$old")"
        rm -rf "$old"
    done
fi

# The oldest backup we keep needs every WAL segment from its START WAL onwards
# (to reach consistency and to roll forward for PITR). Anything older can go.
oldest=$(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'base-*' ! -name '*.partial' | sort | head -n1)
oldest_wal=$(sed -n 's/^START WAL LOCATION: .* (file \([0-9A-F]*\))$/\1/p' "$oldest/backup_label")
log "WAL cleanup: removing archived segments older than $oldest_wal"
pg_archivecleanup "$ARCHIVE_DIR" "$oldest_wal"
