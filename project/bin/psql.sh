#!/usr/bin/env bash
# Open psql against the course database (default: bazaar) inside the course-pg container.
# Usage: ./project/bin/psql.sh [dbname] [extra psql args...]
#   e.g. ./project/bin/psql.sh                 (bazaar)
#        ./project/bin/psql.sh -c 'SELECT 1'   (bazaar)
#        ./project/bin/psql.sh postgres -c '\l'
set -euo pipefail
if [[ $# -gt 0 && "$1" != -* ]]; then DB="$1"; shift; else DB=bazaar; fi   # first arg = db name unless it is a flag
CONTAINER="${COURSE_PG_CONTAINER:-course-pg}"
TTY_FLAG="-i"; [ -t 0 ] && TTY_FLAG="-it"   # allow piping files: ./psql.sh bazaar < file.sql
exec docker exec $TTY_FLAG -e PGOPTIONS="${PGOPTIONS:-}" "$CONTAINER" psql -U postgres -d "$DB" "$@"
