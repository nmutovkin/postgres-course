#!/usr/bin/env bash
# Acceptance check for the module 20 milestone:
#   zero lost (and zero duplicated) committed checkouts across a switchover,
#   with Patroni's synchronous mode on.
#
#   ./solutions/milestone_check.sh                 uses the reference client
#   CLIENT=/lab/solutions/my_client.py ./solutions/milestone_check.sh
#
# Requirements: the lab is up (lab/scripts/up.sh) and Bazaar is loaded
# (lab/scripts/load-bazaar.sh). The client must accept --duration and exit 0
# only if its own verification passed.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/../lab/scripts/lib.sh"
CLIENT="${CLIENT:-/lab/solutions/bazaar_ha_client.py}"
LOG="$(mktemp -t m20-milestone.XXXX)"

echo ">> enabling synchronous mode"
pctl edit-config -s synchronous_mode=true --force >/dev/null
for _ in $(seq 1 30); do
  pctl list 2>/dev/null | grep -q "Sync Standby" && break
  sleep 1
done
pctl list

echo ">> starting the client for 45 s; switchover after 15 s"
docker exec m20-client python3 -u "$CLIENT" --duration 45 >"$LOG" 2>&1 &
CL=$!
sleep 15
"$HERE/../lab/scripts/switchover.sh" | grep -E "patronictl|Success|failed" || true
set +e
wait $CL
RC=$?
set -e
echo "------------------------------ client output ------------------------------"
cat "$LOG"; rm -f "$LOG"
echo "---------------------------------------------------------------------------"
if [[ $RC -eq 0 ]]; then echo "ACCEPTED: no lost or duplicated checkouts"; else echo "FAILED (client exit code $RC)"; fi
exit $RC
