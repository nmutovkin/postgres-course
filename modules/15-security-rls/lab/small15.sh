#!/usr/bin/env bash
# Module 15: a light-weight "start of module 15" state on the SMALL dataset.
#
#   ./modules/15-security-rls/lab/small15.sh            # schema of state 15, small data
#   ./modules/15-security-rls/lab/small15.sh --with-1501 # ... plus the reference 1501 (= state 16, small data)
#
# reset.sh 15 builds the same schema with the large dataset (several minutes,
# about 2 GB). Security work rarely needs volume, and the small dataset is the
# one documented row by row in project/db/NOTES.md, so most of module 15 uses this.
# It runs reset.sh 11 (small data) and then applies only the SCHEMA files of
# modules 11-14 (indexes, statistics, search, the partitioned events table),
# skipping the large seeds 1101 and 1402.
#
# Uses the same container as reset.sh: COURSE_PG_CONTAINER (default course-pg).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/../../.." && pwd)"
CONTAINER="${COURSE_PG_CONTAINER:-course-pg}"
DB_DIR="$HERE/project/db"

"$HERE/project/bin/reset.sh" 11

for f in 1102_schema_indexes.sql 1201_schema_stats.sql 1301_schema_search.sql 1401_schema_events.sql; do
  echo ">> applying $f"
  docker exec -i "$CONTAINER" psql -U postgres -d bazaar -v ON_ERROR_STOP=1 -q < "$DB_DIR/$f"
done

if [[ "${1:-}" == "--with-1501" ]]; then
  echo ">> applying 1501_schema_security.sql"
  docker exec -i "$CONTAINER" psql -U postgres -d bazaar -v ON_ERROR_STOP=1 -q < "$DB_DIR/1501_schema_security.sql"
fi
echo ">> done (module 15 state on the small dataset${1:+ $1})"
