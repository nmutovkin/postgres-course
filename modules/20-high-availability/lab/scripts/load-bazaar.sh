#!/usr/bin/env bash
# Load the Bazaar database into the CURRENT LEADER with the course's reset.sh.
# Patroni streams it to the replicas like any other write.
#   ./scripts/load-bazaar.sh          state at the start of module 20 (large dataset)
#   ./scripts/load-bazaar.sh 11       a smaller state (before the large seed): fast
source "$(dirname "$0")/lib.sh"
STATE="${1:-20}"
L="$(leader)"
[[ -n "$L" ]] || { echo "no leader right now" >&2; exit 1; }
echo ">> leader is $L; loading reset state $STATE"
COURSE_PG_CONTAINER="$L" "$LAB_DIR/../../../project/bin/reset.sh" "$STATE"
