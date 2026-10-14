#!/usr/bin/env bash
# pgbench: direct to PostgreSQL vs through PgBouncer (transaction mode).
# Run from modules/17-operations/lab after `docker compose up -d --wait`.
# The first run initializes the pgbench tables (scale 10, ~150 MB).
set -euo pipefail
cd "$(dirname "$0")"
T="${BENCH_SECONDS:-15}"

pgb() { docker compose exec -T -e PGPASSWORD=app_pw pg pgbench -U app "$@" bench 2>&1; }
direct=(-h pg -p 5432)
pooled=(-h pgbouncer -p 6432)
summary() { grep -E '^(tps|latency average|number of failed)|FATAL' | head -3; }
banner() { printf '\n=== %s ===\n' "$*"; }
# SHOW POOLS through the admin console, trimmed to the interesting columns.
pools() { docker compose exec -T -e PGPASSWORD=postgres pg psql -X -A -F ' ' -h pgbouncer -p 6432 \
            -U postgres -d pgbouncer -c "SHOW POOLS" \
          | awk 'NR==1 || $1=="bench" {printf "    %-9s %-10s %-11s %-10s %-8s %-8s\n", $1, $3, $4, $7, $10, $14}'; }

if ! docker compose exec -T pg psql -U postgres -d bench -Atc \
       "SELECT 1 FROM pg_tables WHERE tablename = 'pgbench_accounts'" | grep -q 1; then
  banner "initializing pgbench tables (scale 10)"
  pgb -i -s 10 -q "${direct[@]}" | tail -1
fi

banner "A. Connection churn: a new connection for EVERY transaction (-C), 20 clients, read-only"
echo "--- direct";  pgb "${direct[@]}" -C -S -c 20 -j 4 -T "$T" | summary
echo "--- pooled";  pgb "${pooled[@]}" -C -S -c 20 -j 4 -T "$T" | summary

banner "B. 300 concurrent clients, max_connections = 100"
echo "--- direct";  pgb "${direct[@]}" -c 300 -j 4 -T "$T" | summary || true
echo "--- pooled";  pgb "${pooled[@]}" -c 300 -j 4 -T "$T" > /tmp/m17-pooled.log &
sleep $(( T / 2 ))
echo "    (mid-run) PgBouncer SHOW POOLS for database bench:"
pools
echo "    (mid-run) backends in PostgreSQL for role app, by state:"
docker compose exec -T pg psql -X -U postgres -c \
  "SELECT state, count(*) FROM pg_stat_activity WHERE usename = 'app' GROUP BY state ORDER BY state"
wait
summary < /tmp/m17-pooled.log; rm -f /tmp/m17-pooled.log

banner "C. 50 long-lived clients (no churn, within max_connections)"
echo "--- direct";  pgb "${direct[@]}" -c 50 -j 4 -T "$T" | summary
echo "--- pooled";  pgb "${pooled[@]}" -c 50 -j 4 -T "$T" | summary

