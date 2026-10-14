#!/usr/bin/env bash
# Remove every lab container, the m20-net network and all m20-* volumes.
# Add --image to also delete the m20-patroni:17 image.
source "$(dirname "$0")/lib.sh"
cd "$LAB_DIR"
docker compose down -v --remove-orphans
if [[ "${1:-}" == "--image" ]]; then docker image rm -f m20-patroni:17; fi
