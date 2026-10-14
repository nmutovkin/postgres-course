#!/usr/bin/env bash
# Show the cluster as Patroni sees it (patronictl list), plus the topology.
#   ./scripts/cluster.sh         one snapshot
#   ./scripts/cluster.sh -W      refresh every 2 s (Ctrl-C to stop)
source "$(dirname "$0")/lib.sh"
if [ -t 0 ] && [ -t 1 ]; then T=-it; else T=-i; fi
docker exec $T "$(any_node)" patronictl list "$@"
