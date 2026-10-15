#!/usr/bin/env bash
# Load Bazaar into a (not yet distributed) PostgreSQL server and apply the
# shard-prep migration 2101.
#
#   ./scripts/load-bazaar.sh small [container]   # default: small data, m21-coord
#   ./scripts/load-bazaar.sh large [container]
#
# small: reset.sh 11 (the hand-readable dataset: 30 users, 6 sellers, 120 orders)
#        plus the schema-only migrations of modules 11-15 (indexes, stats,
#        search, the events table WITHOUT its 5M generated rows, security).
#        2101 needs those (it drops module-11 indexes and module-15 policies).
#        Takes a few seconds.
# large: reset.sh 21 (100k users, 2,000 sellers, 500k orders, 5M events),
#        about 3-4 minutes and 2 GB of disk.
#
# The database is left as an ORDINARY single-node database with the 2101
# schema. scripts/citus-init-nodes.sh and scripts/distribute.sql (all run by
# scripts/citus-build.sh) then turn it into a Citus cluster.
set -euo pipefail
SIZE="${1:-small}"
CONTAINER="${2:-m21-coord}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
DB="$ROOT/project/db"
export COURSE_PG_CONTAINER="$CONTAINER"

apply() {
  echo ">> applying $(basename "$1")"
  docker exec -i "$CONTAINER" psql -U postgres -d bazaar -v ON_ERROR_STOP=1 -q < "$1"
}

case "$SIZE" in
  small)
    "$ROOT/project/bin/reset.sh" 11
    for f in 1102_schema_indexes 1201_schema_stats 1301_schema_search \
             1401_schema_events 1501_schema_security; do
      apply "$DB/$f.sql"
    done
    ;;
  large)
    "$ROOT/project/bin/reset.sh" 21
    ;;
  *) echo "usage: $0 small|large [container]" >&2; exit 1 ;;
esac

apply "$DB/sharding/2101_schema_shard_prep.sql"
echo ">> Bazaar ($SIZE) with the 2101 shard-prep schema is ready in $CONTAINER"
