#!/usr/bin/env bash
# Open psql against the course database (default: bazaar) inside the course-pg container.
# Usage: ./project/bin/psql.sh [dbname] [extra psql args...]
set -euo pipefail
DB="${1:-bazaar}"; shift || true
CONTAINER="${COURSE_PG_CONTAINER:-course-pg}"
exec docker exec -it -e PGOPTIONS="${PGOPTIONS:-}" "$CONTAINER" psql -U postgres -d "$DB" "$@"
