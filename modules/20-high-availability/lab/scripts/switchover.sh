#!/usr/bin/env bash
# Planned, graceful change of leader.
#   ./scripts/switchover.sh            Patroni picks the best candidate
#   ./scripts/switchover.sh pg3        make pg3 the new leader
source "$(dirname "$0")/lib.sh"
L="$(leader)"; [[ -n "$L" ]] || { echo "no leader right now" >&2; exit 1; }
args=(switchover --leader "${L#m20-}" --force)
[[ -n "${1:-}" ]] && args+=(--candidate "${1#m20-}")
t0=$(date +%s.%N)
echo "$(ts)  patronictl ${args[*]}"
docker exec "$(any_node)" patronictl "${args[@]}"
printf '%s  patronictl returned after %.1fs\n' "$(ts)" "$(elapsed "$t0")"
docker exec "$(any_node)" patronictl list
