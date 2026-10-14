#!/usr/bin/env bash
# One complete, measured failure drill:
#   1. start the write loop (in the background),
#   2. after a few seconds, hurt the leader in the chosen way,
#   3. time how long until Patroni reports a new running leader,
#   4. print the client's view (errors, downtime, lost writes).
#
#   ./scripts/failover-drill.sh kill        docker kill the leader   (crash)
#   ./scripts/failover-drill.sh stop        docker stop the leader   (clean shutdown)
#   ./scripts/failover-drill.sh switchover  patronictl switchover     (planned)
#   ./scripts/failover-drill.sh partition   disconnect the leader from m20-net
# Optional 2nd argument: how long the write loop runs (default 75 s).
# Optional env: DSN=... to use another connection string in the client.
source "$(dirname "$0")/lib.sh"
MODE="${1:?usage: failover-drill.sh kill|stop|switchover|partition [seconds]}"
DURATION="${2:-75}"
LOG="$(mktemp -t m20-drill.XXXX)"

OLD="$(leader)"; [[ -n "$OLD" ]] || { echo "no leader right now" >&2; exit 1; }
wl_args=(--duration "$DURATION")
[[ -n "${DSN:-}" ]] && wl_args+=(--dsn "$DSN")
"$LAB_DIR/scripts/writeloop.sh" "${wl_args[@]}" >"$LOG" 2>&1 &
WL=$!
sleep 8

t0=$(date +%s.%N)
case "$MODE" in
  kill)       echo "$(ts)  docker kill $OLD";  docker kill "$OLD" >/dev/null ;;
  stop)       echo "$(ts)  docker stop $OLD";  docker stop "$OLD" >/dev/null ;;
  switchover) echo "$(ts)  patronictl switchover (leader ${OLD#m20-})"
              docker exec "$(any_node)" patronictl switchover --leader "${OLD#m20-}" --force >/dev/null ;;
  partition)  echo "$(ts)  docker network disconnect m20-net $OLD"
              docker network disconnect m20-net "$OLD" ;;
  *) echo "unknown mode $MODE" >&2; kill $WL; exit 1 ;;
esac

# Ask a node that is NOT the old leader (the old one may be dead or isolated).
observer() { local n; for n in "${NODES[@]}"; do [[ "$n" != "$OLD" ]] && \
  [[ "$(docker inspect -f '{{.State.Running}}' "$n")" == true ]] && { echo "$n"; return; }; done; }
NEW=""
for _ in $(seq 1 600); do
  NEW="$(docker exec "$(observer)" sh -c 'patronictl list -f json 2>/dev/null' | python3 -c "
import json, sys
m = json.load(sys.stdin)
print(next(('m20-' + x['Member'] for x in m if x['Role'] == 'Leader' and x['State'] == 'running' and 'm20-' + x['Member'] != '$OLD'), ''))" 2>/dev/null || true)"
  [[ -n "$NEW" ]] && break
  sleep 0.2
done
printf '%s  new leader %s is running, %.1fs after the event\n' "$(ts)" "${NEW:-<none>}" "$(elapsed "$t0")"

wait $WL || true
echo "------------------------------- client output -------------------------------"
cat "$LOG"; rm -f "$LOG"
echo "-----------------------------------------------------------------------------"
docker exec "$(observer)" patronictl list || true
case "$MODE" in
  kill|stop)  echo "Old leader is down. Bring it back with: ./scripts/rejoin.sh $OLD" ;;
  partition)  echo "Old leader is still isolated. Reconnect with: ./scripts/partition.sh heal $OLD" ;;
esac
