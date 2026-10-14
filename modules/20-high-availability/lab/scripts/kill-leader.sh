#!/usr/bin/env bash
# Kill the current leader container the hard way (SIGKILL: no clean shutdown,
# Patroni gets no chance to release the leader key).
#   ./scripts/kill-leader.sh          docker kill   (crash / power loss)
#   ./scripts/kill-leader.sh stop     docker stop   (clean shutdown: SIGTERM)
source "$(dirname "$0")/lib.sh"
L="$(leader)"; [[ -n "$L" ]] || { echo "no leader right now" >&2; exit 1; }
if [[ "${1:-kill}" == "stop" ]]; then
  echo "$(ts)  docker stop $L"; docker stop "$L" >/dev/null
else
  echo "$(ts)  docker kill $L"; docker kill "$L" >/dev/null
fi
echo "$(ts)  $L is down. Start it again later with: ./scripts/rejoin.sh $L"
