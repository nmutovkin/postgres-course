#!/usr/bin/env bash
# Prepare the bazaar database on the Citus workers and register them with the
# coordinator. Run AFTER load-bazaar.sh (which recreates bazaar on m21-coord).
#   ./scripts/citus-init-nodes.sh [worker ...]     (default: m21-worker1 m21-worker2)
set -euo pipefail
WORKERS=("${@:-m21-worker1 m21-worker2}")
WORKERS=(${WORKERS[@]})
for w in "${WORKERS[@]}"; do
  echo ">> $w: fresh bazaar database with the citus extension"
  # Citus does not propagate CREATE DATABASE by default: every node needs it.
  docker exec "$w" psql -U postgres -q -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE IF EXISTS bazaar WITH (FORCE)" -c "CREATE DATABASE bazaar" 2>&1 \
    | grep -v -e 'Citus partially supports' -e 'Citus does not propagate' -e 'manually create a database' -e 'does not exist, skipping' || true
  docker exec "$w" psql -U postgres -d bazaar -q -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS citus"
done
docker exec -i m21-coord psql -U postgres -d bazaar -q -v ON_ERROR_STOP=1 <<SQL
CREATE EXTENSION IF NOT EXISTS citus;
-- The coordinator must know its own address so workers can reach it
-- (needed for reference tables with foreign keys from local tables, and for
-- running queries from any node).
SELECT citus_set_coordinator_host('m21-coord', 5432);
$(for w in "${WORKERS[@]}"; do echo "SELECT citus_add_node('$w', 5432);"; done)
SQL
docker exec m21-coord psql -U postgres -d bazaar -c "SELECT nodeid, nodename, groupid, noderole, shouldhaveshards FROM pg_dist_node ORDER BY nodeid"
