#!/usr/bin/env bash
# Demonstrates what breaks behind a transaction-mode pooler.
# Run from modules/17-operations/lab after `docker compose up -d --wait`.
# All clients run inside the pg container and connect to PgBouncer over the
# compose network, so you need nothing installed on your machine.
set -euo pipefail
cd "$(dirname "$0")"

# psql as the app user through PgBouncer. $1 = pooled database alias.
pq() { local db="$1"; shift
       docker compose exec -T -e PGPASSWORD=app_pw pg \
         psql -X -q -h pgbouncer -p 6432 -U app -d "$db" "$@"; }
admin() { docker compose exec -T -e PGPASSWORD=postgres pg \
            psql -X -q -h pgbouncer -p 6432 -U postgres -d pgbouncer "$@"; }
banner() { printf '\n=== %s ===\n' "$*"; }

banner "1. SET leaks to the next client (transaction mode, pool_size=1)"
pq bench_tiny -c "SET work_mem = '123MB'" \
              -c "SELECT 'client A' AS who, pg_backend_pid() AS backend, current_setting('work_mem') AS work_mem"
pq bench_tiny -c "SELECT 'client B' AS who, pg_backend_pid() AS backend, current_setting('work_mem') AS work_mem"

banner "1b. Same thing in session mode: DISCARD ALL cleans the backend"
pq bench_session -c "SET work_mem = '123MB'" \
                 -c "SELECT 'client A' AS who, pg_backend_pid() AS backend, current_setting('work_mem') AS work_mem"
pq bench_session -c "SELECT 'client B' AS who, pg_backend_pid() AS backend, current_setting('work_mem') AS work_mem"

banner "2. A client's own SET and PREPARE vanish when it is moved to another backend"
# Client A sets state, then a second client grabs A's (now idle) backend for 3 s,
# so A's next transaction is served by a different backend.
pq bench <<'SQL'
SET work_mem = '123MB';
PREPARE top_branch AS SELECT bid FROM pgbench_branches ORDER BY bbalance DESC LIMIT 1;
SELECT 'before' AS step, pg_backend_pid() AS backend, current_setting('work_mem') AS work_mem;
\! psql -X -q -h pgbouncer -p 6432 -U app -d bench -c 'SELECT pg_sleep(3)' >/dev/null &
\! sleep 1
SELECT 'after' AS step, pg_backend_pid() AS backend, current_setting('work_mem') AS work_mem;
EXECUTE top_branch;
SQL
pq bench_tiny -c "RESET ALL" -c "DEALLOCATE ALL" >/dev/null

banner "3. Session advisory locks outlive the client that took them"
pq bench_tiny -c "SELECT 'client A locks 42' AS step, pg_advisory_lock(42)"
docker compose exec -T pg psql -X -U postgres -d bench -c \
  "SELECT locktype, objid, pid, granted FROM pg_locks WHERE locktype = 'advisory'"
pq bench_tiny -c "SELECT 'client B tries 42' AS step, pg_try_advisory_lock(42) AS got_it"
pq bench_tiny -c "SELECT pg_advisory_unlock_all()" >/dev/null

banner "4. LISTEN: the notification goes to whoever holds the backend next"
pq bench_tiny -c "LISTEN order_paid"
pq bench_tiny -c "NOTIFY order_paid, 'order 42'" -c "SELECT 'client B: just ran NOTIFY + SELECT' AS step"
pq bench_tiny -c "UNLISTEN *" >/dev/null

banner "5. Protocol-level prepared statements (pgbench -M prepared)"
echo "--- with max_prepared_statements = 200 (PgBouncer tracks them):"
docker compose exec -T -e PGPASSWORD=app_pw pg \
  pgbench -h pgbouncer -p 6432 -U app -M prepared -S -c 40 -j 4 -T 5 bench 2>&1 \
  | grep -E '^(tps|number of failed)' || true
admin -c "SET max_prepared_statements = 0" >/dev/null
echo "--- with max_prepared_statements = 0 (PgBouncer does not track them):"
docker compose exec -T -e PGPASSWORD=app_pw pg sh -c '
  pgbench -h pgbouncer -p 6432 -U app -M prepared -S -c 40 -j 4 -T 5 bench > /tmp/noprep.log 2>&1
  echo "pgbench exit code: $?"
  echo "clients aborted: $(grep -c "aborted in command" /tmp/noprep.log) of 40"
  grep -oE "prepared statement \"P_0\" (does not exist|already exists)" /tmp/noprep.log | sort | uniq -c'
admin -c "SET max_prepared_statements = 200" >/dev/null
