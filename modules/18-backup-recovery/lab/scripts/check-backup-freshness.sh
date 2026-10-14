#!/usr/bin/env bash
# Monitoring check for Bazaar backups, in the style of a Nagios/Icinga plugin:
# prints one line and exits 0 (OK), 1 (WARNING) or 2 (CRITICAL).
#
#   docker exec -u postgres m18-primary /lab/scripts/check-backup-freshness.sh
#
# Two independent things must be fresh:
#   * the newest successful base backup (catalog written by nightly-backup.sh);
#   * WAL archiving (pg_stat_archiver): the last archived segment must be
#     recent and the archiver must not be failing.
# Thresholds are in seconds and can be overridden from the environment.
set -uo pipefail

CATALOG="${CATALOG:-/backups/base/catalog.tsv}"
BACKUP_WARN="${BACKUP_WARN:-$((26 * 3600))}"     # nightly + 2h slack
BACKUP_CRIT="${BACKUP_CRIT:-$((50 * 3600))}"     # two missed nights
ARCHIVE_WARN="${ARCHIVE_WARN:-300}"              # archive_timeout is 60s in the lab
ARCHIVE_CRIT="${ARCHIVE_CRIT:-900}"

status=0; msgs=()
raise() { (( $1 > status )) && status=$1; msgs+=("$2"); }

# ---- base backups -----------------------------------------------------------
if [[ ! -s "$CATALOG" ]] || (( $(wc -l < "$CATALOG") < 2 )); then
    raise 2 "no base backup recorded in $CATALOG"
else
    last=$(tail -n1 "$CATALOG")
    label=$(cut -f1 <<<"$last"); finished=$(cut -f3 <<<"$last")
    age=$(( $(date +%s) - $(date -d "$finished" +%s) ))
    if   (( age > BACKUP_CRIT )); then raise 2 "last backup $label is ${age}s old"
    elif (( age > BACKUP_WARN )); then raise 1 "last backup $label is ${age}s old"
    else msgs+=("last backup $label ${age}s ago"); fi
fi

# ---- WAL archiving ----------------------------------------------------------
read -r arch_age failing last_wal < <(psql -X -qAt -F' ' -d postgres -c "
    SELECT coalesce(extract(epoch FROM now() - last_archived_time)::bigint, -1),
           (last_failed_time IS NOT NULL
            AND last_failed_time > coalesce(last_archived_time, '-infinity'))::int,
           coalesce(last_archived_wal, 'none')
    FROM pg_stat_archiver")
if [[ -z "${arch_age:-}" ]]; then
    raise 2 "cannot query pg_stat_archiver"
elif (( failing == 1 )); then
    raise 2 "archiver is FAILING (last success: $last_wal)"
elif (( arch_age < 0 )); then
    raise 2 "nothing archived yet"
elif (( arch_age > ARCHIVE_CRIT )); then raise 2 "last WAL archived ${arch_age}s ago ($last_wal)"
elif (( arch_age > ARCHIVE_WARN )); then raise 1 "last WAL archived ${arch_age}s ago ($last_wal)"
else msgs+=("last WAL $last_wal archived ${arch_age}s ago"); fi

names=(OK WARNING CRITICAL)
out="${msgs[0]}"
for m in "${msgs[@]:1}"; do out+="; $m"; done
echo "BACKUP ${names[$status]}: $out"
exit "$status"
