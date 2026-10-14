#!/usr/bin/env bash
# Look inside the DCS.
#   ./scripts/etcd.sh            all Patroni keys and values for the cluster
#   ./scripts/etcd.sh health     etcd member list and health
source "$(dirname "$0")/lib.sh"
E=""
for n in m20-etcd1 m20-etcd2 m20-etcd3; do
  [[ "$(docker inspect -f '{{.State.Running}}' "$n" 2>/dev/null)" == true ]] && { E="$n"; break; }
done
[[ -n "$E" ]] || { echo "no etcd container running" >&2; exit 1; }
EPS=http://m20-etcd1:2379,http://m20-etcd2:2379,http://m20-etcd3:2379
if [[ "${1:-}" == "health" ]]; then
  docker exec "$E" etcdctl --endpoints="$EPS" member list -w table
  docker exec "$E" etcdctl --endpoints="$EPS" endpoint status -w table 2>&1 || true
else
  docker exec "$E" etcdctl --endpoints="$EPS" get --prefix /service/bazaar-ha/
fi
