#!/usr/bin/env bash
# Start a stopped/killed node again and watch it rejoin as a replica.
#   ./scripts/rejoin.sh m20-pg1
source "$(dirname "$0")/lib.sh"
N="${1:?usage: rejoin.sh <container, e.g. m20-pg1>}"
MEMBER="${N#m20-}"
since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
t0=$(date +%s.%N)
echo "$(ts)  docker start $N"
docker start "$N" >/dev/null
for _ in $(seq 1 120); do
  state="$(docker exec "$(any_node)" sh -c "patronictl list -f json 2>/dev/null" \
           | python3 -c "import json,sys; print(next((m['Role']+'/'+m['State'] for m in json.load(sys.stdin) if m['Member']=='$MEMBER'), 'absent'))" 2>/dev/null || echo '?')"
  if [[ "$state" == "Replica/streaming" || "$state" == "Sync Standby/streaming" ]]; then
    printf '%s  %s is %s again after %.1fs\n' "$(ts)" "$N" "$state" "$(elapsed "$t0")"
    break
  fi
  sleep 1
done
echo "--- what Patroni did on $N (rewind / basebackup / role lines) ---"
docker logs --since "$since" "$N" 2>&1 | grep -E "rewind|Rewind|basebackup|bootstrap|demot|Lock owner|starting as|following|Local timeline|primary_timeline|reinitializ|no action" | head -25 || true
echo
docker exec "$(any_node)" patronictl list
