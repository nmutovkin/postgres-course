# Module 17 project: the ops toolkit

Bazaar is in production. When something goes wrong at 3 a.m., the on-call engineer should not be writing recursive CTEs over `pg_locks` from memory. In this milestone you build an **ops toolkit**: a schema of monitoring views that answer the standard questions in one `SELECT`, plus tuned autovacuum settings for Bazaar's two hottest tables and per-role guardrails, applied and verified.

**Starting state:** `./project/bin/reset.sh 17`. The schema of Bazaar's tables doesn't change (modules 16 to 20 work *on* the database); you add a separate `ops` schema and storage parameters.

## Part 1: monitoring views

Write `ops_toolkit.sql`, a re-runnable script (it may drop and recreate its own schema) that creates a schema `ops` with these views. Each must be a single query over catalogs and statistics views, cheap enough to run every few seconds:

| View | Answers | Must include |
|---|---|---|
| `ops.long_transactions` | Who has a transaction open, and for how long? | pid, user, application, state, transaction age, time in current state, how far it holds back the xmin horizon, wait event, last query; oldest first; your own session excluded |
| `ops.blocking_chains` | Who blocks whom? | one row per session in a lock-wait tree, indented by depth, with the **root blocker** of each tree identified; handles lock queues (a `SELECT` waiting behind a waiting `ALTER TABLE`) |
| `ops.xmin_horizon` | What prevents VACUUM from removing dead tuples? | sessions (by snapshot **and** by their own XID), replication slots, and prepared transactions, with their age in XIDs |
| `ops.top_statements` | What consumes the database's time? | top 20 statements of the current database from `pg_stat_statements` by total time, with calls, mean and max time, share of total time, cache hit ratio, temp blocks written |
| `ops.unused_indexes` | Which indexes cost writes but are never read? | non-unique, non-constraint indexes with zero scans, their size, their definition, and how long the statistics have been collecting |
| `ops.table_bloat` | Which tables are much bigger than their data? | estimated bloat in bytes and percent from planner statistics (no `pgstattuple`), respecting each table's fillfactor |
| `ops.vacuum_lag` | Is autovacuum keeping up? | per table: dead tuples, inserts and modifications since the last (auto)vacuum/analyze, the thresholds autovacuum compares them with (**honouring per-table storage parameters**), the percentage of each threshold reached, XID age relative to `autovacuum_freeze_max_age`, last vacuum and analyze times |
| `ops.database_health` | One-line health summary | per database: connections vs. `max_connections`, cache hit ratio, rollback ratio, deadlocks, temp files, checksum failures, XID and multixact age, size |

Grant read access on the schema to the built-in `pg_monitor` role, so a monitoring agent doesn't need superuser.

## Part 2: autovacuum for the hot tables

Write `autovacuum_settings.sql` that sets per-table storage parameters (no global changes):

- **`jobs`** (the job queue from Module 10): every job is inserted and then updated two or three times, so dead tuples accumulate at the rate of job throughput, while the queue that workers scan stays small. Vacuum and analyze it after a **fixed number** of changes (around 1,000), independent of table size; let its vacuums run **unthrottled**; make sure insert-only bursts also trigger a vacuum; leave room on each page for HOT updates of the lease columns.
- **`inventory`**: 200,000 small rows updated by every checkout. Vacuum at about **1% plus 1,000** dead tuples instead of 20%, analyze at 2%, give its vacuums a larger cost budget, and keep `fillfactor = 90` (Module 16's milestone).

Explain each value in a comment.

## Part 3: guardrails per role

In the same script, set (for the `bazaar` database only, so that `reset.sh` cleans them up):

- `bazaar_app`: `statement_timeout = 5s`, `idle_in_transaction_session_timeout = 60s`, `lock_timeout = 3s`;
- `bazaar_analyst`: `statement_timeout = 5min`, `work_mem = 64MB`.

## Acceptance checks

Apply both scripts:

```bash
docker exec -i course-pg psql -U postgres -d bazaar -v ON_ERROR_STOP=1 < ops_toolkit.sql
docker exec -i course-pg psql -U postgres -d bazaar -v ON_ERROR_STOP=1 < autovacuum_settings.sql
```

1. **Views exist and run.** `\dv ops.*` lists 8 views, and `SELECT * FROM ops.<view>` works for every one of them. `ops.database_health` has one row per database that accepts connections (`bazaar`, `postgres`, `template1`).

2. **Thresholds honour the per-table settings.**

   ```sql
   SELECT table_name, vacuum_threshold, insert_threshold, analyze_threshold
   FROM ops.vacuum_lag WHERE table_name IN ('jobs', 'inventory') ORDER BY table_name;
   ```

   ```text
    table_name | vacuum_threshold | insert_threshold | analyze_threshold
   ------------+------------------+------------------+-------------------
    inventory  |             3000 |            41000 |              5000
    jobs       |             1000 |             5000 |              1000
   ```

3. **Autovacuum reacts to the queue.** Run the provided workload, which enqueues and completes 20,000 jobs and then waits (up to 3 minutes) for autovacuum:

   ```bash
   docker exec -i course-pg psql -U postgres -d bazaar < modules/17-operations/solutions/jobs_workload.sql
   ```

   The final row shows `autovacuum_count` of at least 1 and a recent `last_autovacuum` for `jobs`. (With the default settings, 40,000 dead tuples in a 20,000-row table would also trigger a vacuum, but only because the table is still small: compute what the defaults would require once `jobs` holds a million finished jobs, and compare with your fixed threshold.)

4. **The blocking chain is found.** Reproduce section 7.3 of the theory (session 1: `BEGIN; UPDATE inventory SET reserved = reserved WHERE product_id = 1;` and stay idle; session 2: `ALTER TABLE inventory ALTER COLUMN reserved SET DEFAULT 0;`; session 3: `SELECT quantity FROM inventory WHERE product_id = 42;`). Then:

   ```sql
   SELECT root_pid, pid_tree, depth, state, waiting_on FROM ops.blocking_chains;
   SELECT pid, state, horizon_age FROM ops.long_transactions;
   SELECT kind, holder, xmin_age FROM ops.xmin_horizon;
   ```

   `ops.blocking_chains` shows 3 rows with the same `root_pid`: the idle-in-transaction session at depth 0, the `ALTER TABLE` at depth 1, and the `SELECT` at depth 2. `ops.long_transactions` lists all three, and `ops.xmin_horizon` includes session 1 (it holds the horizon with its XID even though it has no snapshot). Session 2 gives up by itself if you set `lock_timeout` for it; otherwise end session 1 with `COMMIT`.

5. **Guardrails apply at login.**

   ```bash
   docker exec -i course-pg psql -U bazaar_app -d bazaar -c "SHOW statement_timeout" -c "SHOW lock_timeout"
   ```

   shows `5s` and `3s`; `\drds` in psql lists the settings for `bazaar_app` and `bazaar_analyst` in database `bazaar`.

## Reference solution

- [`solutions/ops_toolkit.sql`](solutions/ops_toolkit.sql): the eight views and the `ops.reloption()` helper.
- [`solutions/autovacuum_settings.sql`](solutions/autovacuum_settings.sql): storage parameters for `jobs` and `inventory`, and the role guardrails.
- [`solutions/jobs_workload.sql`](solutions/jobs_workload.sql): the acceptance workload for check 3.

These views are also what you'd export to a metrics stack (section 12): postgres_exporter can run custom queries such as `SELECT * FROM ops.vacuum_lag` and turn each numeric column into a time series.
