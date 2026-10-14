#!/usr/bin/env bash
# Simulate a network partition by unplugging a container from the lab network.
#   ./scripts/partition.sh isolate m20-pg1     cut m20-pg1 off from everything
#   ./scripts/partition.sh heal    m20-pg1     plug it back in
# Works for etcd members too (m20-etcd2, ...).
source "$(dirname "$0")/lib.sh"
ACTION="${1:?usage: partition.sh isolate|heal <container>}"
N="${2:?container name}"
case "$ACTION" in
  isolate) echo "$(ts)  disconnecting $N from m20-net"; docker network disconnect m20-net "$N" ;;
  heal)    echo "$(ts)  reconnecting $N to m20-net";    docker network connect m20-net "$N" ;;
  *) echo "unknown action $ACTION" >&2; exit 1 ;;
esac
