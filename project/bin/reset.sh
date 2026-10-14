#!/usr/bin/env bash
# Rebuild the bazaar database as it should look at the START of module NN.
#   ./project/bin/reset.sh 07    -> applies all project/db files from modules < 07
#   ./project/bin/reset.sh all   -> applies every file
# Files are applied in name order; the first two digits of the name are the module number.
set -euo pipefail
TARGET="${1:?usage: reset.sh <module-number|all>}"
CONTAINER="${COURSE_PG_CONTAINER:-course-pg}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
DB_DIR="$HERE/db"

run_psql() { docker exec -i "$CONTAINER" psql -U postgres -v ON_ERROR_STOP=1 -q "$@"; }

if [[ "$TARGET" == "all" ]]; then LIMIT=99; else LIMIT=$((10#$TARGET - 1)); fi

echo ">> recreating database bazaar"
run_psql -d postgres <<SQL
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'bazaar' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS bazaar;
CREATE DATABASE bazaar;
SQL
run_psql -d bazaar <<SQL
CREATE SCHEMA IF NOT EXISTS bazaar;
ALTER DATABASE bazaar SET search_path = bazaar, public;
SQL

shopt -s nullglob
for f in "$DB_DIR"/[0-9][0-9][0-9][0-9]_*.sql; do
  name="$(basename "$f")"
  mod=$((10#${name:0:2}))
  if (( mod <= LIMIT )); then
    echo ">> applying $name"
    run_psql -d bazaar < "$f"
  fi
done
echo ">> done (state at start of module ${TARGET})"
