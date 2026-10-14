#!/usr/bin/env bash
# Call a node's Patroni REST API from inside the lab network and print the
# HTTP status code and the JSON body.
#   ./scripts/rest.sh m20-pg1 /patroni
#   ./scripts/rest.sh m20-pg2 "/replica?lag=1MB"
#   ./scripts/rest.sh all /primary          status code of every node
# (Uses Python in m20-client, so it also works with Dockerfile.prebuilt, which has no curl.)
source "$(dirname "$0")/lib.sh"
TARGET="${1:?usage: rest.sh <m20-pgN|all> <path>}"
PATH_="${2:-/patroni}"
docker exec -i m20-client python3 - "$TARGET" "$PATH_" <<'PY'
import json, sys, urllib.request, urllib.error
target, path = sys.argv[1], sys.argv[2]
nodes = ["m20-pg1", "m20-pg2", "m20-pg3"] if target == "all" else [target]
for n in nodes:
    try:
        with urllib.request.urlopen(f"http://{n}:8008{path}", timeout=3) as r:
            code, body = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, body = e.code, e.read()
    except OSError as e:
        print(f"{n} {path}: unreachable ({e})"); continue
    if target == "all":
        print(f"{n} GET {path} -> {code}")
    else:
        print(f"HTTP {code}")
        try:
            print(json.dumps(json.loads(body), indent=2))
        except ValueError:
            print(body.decode(errors="replace"))
PY
