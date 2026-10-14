#!/usr/bin/env bash
# Build the image (first time only) and start the whole lab, then wait for a leader.
source "$(dirname "$0")/lib.sh"
cd "$LAB_DIR"
docker compose up -d
echo ">> waiting for the cluster to elect a leader and start both replicas..."
for _ in $(seq 1 90); do
  if docker exec m20-pg1 sh -c 'patronictl list -f json 2>/dev/null | python3 -c "
import json, sys
m = json.load(sys.stdin)
leaders = [x for x in m if x[\"Role\"] == \"Leader\" and x[\"State\"] == \"running\"]
streaming = [x for x in m if x[\"State\"] == \"streaming\"]
sys.exit(0 if len(leaders) == 1 and len(streaming) == 2 else 1)"' 2>/dev/null
  then
    docker exec m20-pg1 patronictl list
    exit 0
  fi
  sleep 2
done
echo "cluster did not become healthy in 180s; check: docker logs m20-pg1" >&2
exit 1
