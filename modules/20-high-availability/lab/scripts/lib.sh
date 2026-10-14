# Shared helpers for the module 20 lab scripts. Source it, don't run it.
set -euo pipefail
LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODES=(m20-pg1 m20-pg2 m20-pg3)

ts() { date +%H:%M:%S.%3N; }

# First Patroni container that is running (any of them can answer patronictl).
any_node() {
  local n
  for n in "${NODES[@]}"; do
    if [[ "$(docker inspect -f '{{.State.Running}}' "$n" 2>/dev/null)" == "true" ]]; then
      echo "$n"; return 0
    fi
  done
  echo "no Patroni container is running" >&2; return 1
}

# Container name of the current leader, as recorded in etcd (empty if none).
leader() {
  docker exec "$(any_node)" sh -c 'patronictl list -f json 2>/dev/null | python3 -c "
import json, sys
print(next((\"m20-\" + m[\"Member\"] for m in json.load(sys.stdin) if m[\"Role\"] == \"Leader\"), \"\"))"' 2>/dev/null || true
}

# patronictl from any live node
pctl() { docker exec -i "$(any_node)" patronictl "$@"; }

# psql on a node, as postgres, over the local socket
node_psql() { local n="$1"; shift; docker exec -i "$n" psql -U postgres -X "$@"; }

# Seconds (one decimal) since a start time taken with: t0=$(date +%s.%N)
elapsed() { awk -v a="$1" -v b="$(date +%s.%N)" 'BEGIN { printf "%.1f", b - a }'; }
