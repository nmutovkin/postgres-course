#!/usr/bin/env bash
# One-shot build of the Citus lab: load Bazaar, register the workers,
# distribute the tables and install the Citus-adapted views and checkout.
#   ./scripts/citus-build.sh small     # seconds; the dataset most sections use
#   ./scripts/citus-build.sh large     # ~6 minutes, ~5 GB of disk across the nodes
set -euo pipefail
SIZE="${1:-small}"
HERE="$(cd "$(dirname "$0")" && pwd)"
quiet() { grep -v -e '^HINT' -e 'local tables that are added' -e 'Copying data' \
                  -e 'copying the data' -e 'local data in the table' || true; }
"$HERE/load-bazaar.sh" "$SIZE"
"$HERE/citus-init-nodes.sh"
echo ">> distributing tables"
docker exec -i m21-coord psql -U postgres -d bazaar -v ON_ERROR_STOP=1 < "$HERE/distribute.sql" 2>&1 | quiet
echo ">> installing Citus-adapted views and place_order_sharded()"
docker exec -i m21-coord psql -U postgres -d bazaar -v ON_ERROR_STOP=1 -q < "$HERE/citus-adapt.sql"
echo ">> done: docker exec -it m21-coord psql -U postgres -d bazaar"
