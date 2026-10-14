#!/usr/bin/env bash
# Build the postgres_fdw sharding lab (section 11):
#   m21-fdw-coord: Bazaar small dataset in schema bazaar + schema dist (sharded)
#   m21-shard1/2:  database bazaar, schema shard, the shard tables
# Requires: docker compose --profile fdw up -d
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
COURSE_PG_CONTAINER=m21-fdw-coord "$ROOT/project/bin/reset.sh" 11
n=0
for s in m21-shard1 m21-shard2; do
  docker exec "$s" psql -U postgres -q -c "DROP DATABASE IF EXISTS bazaar WITH (FORCE)" -c "CREATE DATABASE bazaar"
  docker exec -i "$s" psql -U postgres -d bazaar -q -v ON_ERROR_STOP=1 -v n=$n < "$HERE/fdw/shard.sql"
  n=$((n+1))
done
docker exec -i m21-fdw-coord psql -U postgres -d bazaar -q -v ON_ERROR_STOP=1 < "$HERE/fdw/coord.sql"
for s in m21-shard1 m21-shard2; do
  echo -n "$s: "
  docker exec "$s" psql -U postgres -d bazaar -Atc \
    "SELECT string_agg(format('%s=%s', relname, n_live_tup), ' ' ORDER BY relname) FROM pg_stat_user_tables"
done
