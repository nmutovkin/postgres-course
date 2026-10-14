#!/usr/bin/env bash
# Start PgBouncer (transaction pooling) in front of the course database, on localhost:6432.
#   ./run_pgbouncer.sh 0      # max_prepared_statements = 0   (the pre-1.21 behaviour)
#   ./run_pgbouncer.sh 200    # max_prepared_statements = 200 (PgBouncer tracks prepared statements)
#   docker rm -f m23-pgbouncer   # when you are done
set -euo pipefail
MAX_PREPARED="${1:-200}"
PG_CONTAINER="${COURSE_PG_CONTAINER:-course-pg}"
PORT="${PGBOUNCER_PORT:-6432}"
HERE="$(cd "$(dirname "$0")" && pwd)"
CONF_DIR="$(mktemp -d)"

# The server's address as seen from another container on the same Docker network.
PG_HOST="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$PG_CONTAINER" | awk '{print $1}')"
NETWORK="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$PG_CONTAINER" | awk '{print $1}')"

sed -e "s/@PG_HOST@/$PG_HOST/" -e "s/@MAX_PREPARED@/$MAX_PREPARED/" "$HERE/pgbouncer.ini.in" > "$CONF_DIR/pgbouncer.ini"
cp "$HERE/userlist.txt" "$CONF_DIR/userlist.txt"
chmod 755 "$CONF_DIR"; chmod 644 "$CONF_DIR"/*   # the container runs as uid 70

docker rm -f m23-pgbouncer >/dev/null 2>&1 || true
docker run -d --rm --name m23-pgbouncer --network "$NETWORK" -p "127.0.0.1:$PORT:6432" \
  -v "$CONF_DIR:/etc/pgbouncer:ro" --entrypoint /usr/bin/pgbouncer \
  edoburu/pgbouncer:v1.26.0-p0 /etc/pgbouncer/pgbouncer.ini >/dev/null
sleep 1
echo "PgBouncer on localhost:$PORT -> $PG_CONTAINER ($PG_HOST:5432), transaction mode, max_prepared_statements=$MAX_PREPARED"
