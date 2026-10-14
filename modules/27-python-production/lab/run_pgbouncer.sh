#!/usr/bin/env bash
# PgBouncer 1.26 in transaction mode, 2 server connections, on localhost:6432 (module 27).
#   ./run_pgbouncer.sh            # rejects unknown startup parameters (PgBouncer's default)
#   ./run_pgbouncer.sh ignore     # ignore_startup_parameters = extra_float_digits,options
#   docker rm -f m27-pgbouncer    # when you are done
set -euo pipefail
MODE="${1:-strict}"
PG_CONTAINER="${COURSE_PG_CONTAINER:-course-pg}"
PORT="${PGBOUNCER_PORT:-6432}"
HERE="$(cd "$(dirname "$0")" && pwd)"
CONF_DIR="$(mktemp -d)"

PG_HOST="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$PG_CONTAINER" | awk '{print $1}')"
NETWORK="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$PG_CONTAINER" | awk '{print $1}')"
if [[ "$MODE" == "ignore" ]]; then
  IGNORE="ignore_startup_parameters = extra_float_digits,options"
else
  IGNORE="ignore_startup_parameters = extra_float_digits"
fi
sed -e "s/@PG_HOST@/$PG_HOST/" -e "s/@IGNORE@/$IGNORE/" "$HERE/pgbouncer.ini.in" > "$CONF_DIR/pgbouncer.ini"
cp "$HERE/userlist.txt" "$CONF_DIR/userlist.txt"
chmod 755 "$CONF_DIR"; chmod 644 "$CONF_DIR"/*   # the container runs as uid 70

docker rm -f m27-pgbouncer >/dev/null 2>&1 || true
docker run -d --rm --name m27-pgbouncer --network "$NETWORK" -p "127.0.0.1:$PORT:6432" \
  -v "$CONF_DIR:/etc/pgbouncer:ro" --entrypoint /usr/bin/pgbouncer \
  edoburu/pgbouncer:v1.26.0-p0 /etc/pgbouncer/pgbouncer.ini >/dev/null
sleep 1
echo "PgBouncer on localhost:$PORT -> $PG_CONTAINER ($PG_HOST:5432), transaction mode, 2 server connections, $IGNORE"
