# Module 17: Exercises

**Starting state.** Unless an exercise says otherwise:

```bash
./project/bin/reset.sh 17
./project/bin/psql.sh
```

plus `CREATE EXTENSION IF NOT EXISTS pgstattuple;`. Exercises 11 and 12 use the PgBouncer lab in [`lab/`](lab/) instead (`docker compose up -d --wait` in that directory).

Several exercises change server-wide settings or table storage parameters. Each one says how to undo its changes; do that before moving on, so the next exercise starts clean. Exercises that need several sessions say so: open more terminals with `./project/bin/psql.sh`. To connect as one of Bazaar's roles instead of `postgres`, use `docker exec -it course-pg psql -U bazaar_app -d bazaar` (inside the container, local connections don't ask for a password).

Solutions: [solutions/README.md](solutions/README.md).

---

## Exercise 1 ★: What did someone change?

Write a query against `pg_settings` that lists every parameter whose value does **not** come from the built-in default, showing its name, current value with unit, where the value comes from, the file and line if any, the context, and whether a change is waiting for a restart. Sort the parameters that need a restart to change first.

**Self-check:** no row has `source = 'default'`. The list includes `shared_preload_libraries` (source `command line`, context `postmaster`) and `search_path` (source `database`).

<details><summary>Hint</summary>

`source` is `'default'` for untouched parameters, and `'override'` for a few that the server computes itself (you may keep or drop those). `ORDER BY context = 'postmaster' DESC` puts `true` first.

</details>

---

## Exercise 2 ★: Who can change what

For each of `work_mem`, `log_min_duration_statement`, `autovacuum_naptime`, `max_connections` and `log_connections`, predict from `pg_settings.context` whether the application role `bazaar_app` can change it for its own session with `SET`, and if not, what it takes instead. Then verify by connecting as `bazaar_app` and trying each `SET`.

**Self-check:** exactly one of the five `SET`s succeeds for `bazaar_app`. Each failure's error message matches the context you predicted.

<details><summary>Hint</summary>

Contexts: `user` (anyone), `superuser` (superuser or a role granted `SET` on that parameter), `sighup` (config file + reload), `postmaster` (restart), `backend` (only at connection start). `GRANT SET ON PARAMETER ... TO ...` (PG 15+) is how a superuser delegates a `superuser`-context parameter.

</details>

---

## Exercise 3 ★: Role settings and SET ROLE

Give `bazaar_analyst` `work_mem = 64MB` in the `bazaar` database. Then, in your superuser session, run `SET ROLE bazaar_analyst` and check `work_mem`. Then connect as `bazaar_analyst` directly and check again. Explain the difference, and say which of the two an application that uses one login role and `SET ROLE` per request would get. Undo the setting at the end.

**Self-check:** after `SET ROLE` the value is `4MB`; in a fresh `bazaar_analyst` connection it is `64MB`.

<details><summary>Hint</summary>

Per-role settings are applied when a session **logs in**, from `pg_db_role_setting`.

</details>

---

## Exercise 4 ★★: How much memory can this query take?

The seller dashboard runs this query (revenue per seller for December 2025):

```sql
SELECT s.shop_name, count(DISTINCT o.id) AS orders, sum(oi.quantity * oi.unit_price) AS revenue
FROM orders o
JOIN order_items oi ON oi.order_id = o.id
JOIN sellers s ON s.id = o.seller_id
WHERE o.created_at >= '2025-12-01' AND o.created_at < '2026-01-01'
GROUP BY s.shop_name
ORDER BY revenue DESC;
```

Run it with `EXPLAIN (ANALYZE, BUFFERS)` and list every plan node that uses `work_mem` (sorts, hashes, hash aggregates), with the memory it actually used and whether it spilled to disk. Then compute the **worst case** the configuration allows for this plan if 40 dashboard sessions ran it at once with `work_mem = 64MB` and the default `hash_mem_multiplier`, taking parallel workers into account. Compare with the actual peak.

**Self-check:** your list contains every `Sort`, `Hash` and `HashAggregate` node of the plan, and your worst case is the number of such nodes, weighted (hash nodes × 2), times 64 MB, times the processes per query (leader + launched workers for nodes under a `Gather`), times 40.

<details><summary>Hint</summary>

`Sort Method: ... Memory: NkB` or `Disk: NkB`; `Hash ... Memory Usage: NkB`, `Batches: N` (more than 1 means it spilled); `HashAggregate ... Memory Usage: NkB, Batches: N`. Nodes below a `Gather`/`Gather Merge` run in every worker plus (usually) the leader.

</details>

---

## Exercise 5 ★★: Due for ANALYZE?

Write a query that lists the tables in schema `bazaar` that autovacuum would **analyze** at its next visit: `n_mod_since_analyze` above the analyze threshold, honouring per-table `autovacuum_analyze_threshold` and `autovacuum_analyze_scale_factor` storage parameters. Show the threshold and the percentage reached.

Test it: set `autovacuum_enabled = off` on `payments` (so autovacuum doesn't analyze it behind your back), lower its `autovacuum_analyze_scale_factor` to `0.02`, and update 12,000 rows (`UPDATE payments SET provider_ref = provider_ref WHERE id <= 12000`). Then reset both storage parameters and run `ANALYZE payments`.

**Self-check:** before the update `payments` is not listed; after it, `payments` is listed with a threshold of about 10,790 and roughly 111%.

<details><summary>Hint</summary>

Storage parameters are in `pg_class.reloptions` as `'name=value'` strings. A small helper (or a scalar subquery over `unnest(reloptions)`) extracts one, and `coalesce(..., current_setting('autovacuum_analyze_scale_factor')::float8)` falls back to the global value. Remember `pg_stat_force_next_flush()` (or wait a second) before reading the statistics.

</details>

---

## Exercise 6 ★★: Estimated vs. exact bloat

Disable autovacuum on `payments`, then update 40% of its rows (`SET provider_ref = provider_ref WHERE id % 10 < 4`) in four separate transactions (one per value of `id % 10`). Then:

1. Run `ANALYZE payments` and compute the estimated bloat with the estimate query from section 6.3 (or `ops.table_bloat` if you have done the project).
2. Measure the exact numbers with `pgstattuple`.
3. Fix the table with `VACUUM FULL` and measure again.

Compare the estimate with the exact `dead_tuple_percent + free_percent`. You updated about 215,000 rows: how many dead tuples did you expect, how many are there, and why? Finally re-enable autovacuum.

**Self-check:** before the fix, `pgstattuple` reports only about 54,000 dead tuples (one slice, not four), the table is about 54 MB, and the estimate is higher than the exact dead + free share. After `VACUUM FULL` there are 0 dead tuples and the table is back to about 48 MB.

<details><summary>Hint</summary>

Four `UPDATE ... WHERE id % 10 = k` statements, each autocommitted. With autovacuum off, what else can clean up dead tuples inside a page when the next `UPDATE` comes along (Module 16, section 7)?

</details>

---

## Exercise 7 ★★: Redundant indexes

An index on `(a)` is redundant if another index on the same table starts with `a`: the longer index can serve the same queries. Write a query that finds such pairs in schema `bazaar`. Be careful: a **partial** index (`WHERE ...`) or an **expression** index can't replace a plain one, a non-B-tree index (GIN, GiST, ...) isn't comparable, and a **unique** index enforces a constraint and must stay even if it looks redundant.

Test it by creating a deliberately redundant index, `CREATE INDEX ex_orders_user_id_idx ON orders (user_id)`, then drop it again.

**Self-check:** a naive "prefix of indkey" comparison finds 2 pairs on the reset database, both false positives. Your query finds none, and exactly one (`ex_orders_user_id_idx`, covered by `orders_user_id_created_at_idx`) after you create the test index.

<details><summary>Hint</summary>

`pg_index.indkey` is an `int2vector`; cast it with `indkey::int2[]` and compare slices (`[0:n-1]`, the vector is 0-based). `indpred` (partial) and `indexprs` (expressions) are NULL for plain indexes. The access method is `pg_class.relam` of the index, joined to `pg_am`.

</details>

---

## Exercise 8 ★★: Break the chain

With three sessions, build a blocking chain on **row** locks (no DDL): session 1 updates `inventory` row 100 in a transaction and stays idle; session 2 updates the same row; session 3 runs `SELECT ... FROM inventory WHERE product_id = 100 FOR UPDATE`. From a fourth session:

1. Show the chain with the query from section 7.3 (or `ops.blocking_chains`).
2. Try `pg_cancel_backend()` on the root. What happens, and why?
3. Resolve the chain correctly, and show what sessions 2 and 3 experience.

**Self-check:** the chain has depth 2 (session 3 under session 2 under session 1); `pg_cancel_backend` returns `true` but the chain is unchanged; after the fix, sessions 2 and 3 complete one after the other.

<details><summary>Hint</summary>

What does "cancel the current statement" do to a session that has no current statement? Is session 3 really waiting for session 2, or for session 1? `pg_blocking_pids()` and the tuple lock in `pg_locks` tell the story.

</details>

---

## Exercise 9 ★★: Find the query that spills

Reset `pg_stat_statements`, then run this report (the first page of "each seller's three biggest delivered or shipped orders") twice, as two separate statements:

```sql
SELECT seller_id, id, total, rn
FROM (
    SELECT seller_id, id, total,
           row_number() OVER (PARTITION BY seller_id ORDER BY total DESC) AS rn
    FROM orders
    WHERE status IN ('delivered', 'shipped')
) ranked
WHERE rn <= 3
ORDER BY seller_id, rn
LIMIT 10;
```

Find it in `pg_stat_statements` by its temp-file usage, and fix it **for this report only**, without changing `work_mem` for anything else. Prove with `pg_stat_statements` that the fixed run wrote no temporary blocks.

**Self-check:** before the fix, the statement's `temp_blks_written` is greater than 0 and the same for both calls; the fixed run adds a call without adding temp blocks.

<details><summary>Hint</summary>

`SET LOCAL` inside a transaction lasts until `COMMIT`. `pg_stat_statements` groups statements by normalized text, so the fixed run is counted under the same `queryid`. How much is enough? `EXPLAIN (ANALYZE)` tells you how much the spilling nodes wrote to disk.

</details>

---

## Exercise 10 ★★★: Full-page writes, measured

Measure what checkpoints cost in WAL. Using `pg_stat_wal` (`wal_records`, `wal_fpi`, `wal_bytes`), run `UPDATE users SET is_active = is_active` three times:

1. right after a `CHECKPOINT`;
2. again immediately (no checkpoint in between);
3. again right after another `CHECKPOINT`.

For each run, record the WAL bytes, the number of full-page images, and the bytes per updated row. Explain the numbers, then estimate how much WAL the same update would generate per hour if it ran every minute with `checkpoint_timeout = 5min` vs. `30min`.

**Self-check:** runs 1 and 3 have thousands of full-page images and generate clearly more WAL (around 1.5 times) than run 2, which has none.

<details><summary>Hint</summary>

`pg_stat_wal` is cumulative: read it before and after each run, or `SELECT pg_stat_reset_shared('wal')` before each. `pg_stat_force_next_flush()` makes sure your own counts are in. Run 2's new versions go where? (`users` pages are full, so where does an update put the new version?)

</details>

---

## Exercise 11 ★★★: Pool size vs. throughput (lab)

In the PgBouncer lab, run `pgbench` with 200 clients against the transaction-mode pool (`bench`) for 20 seconds, three times, with `default_pool_size` set to 5, 20 and 60 (change it through the admin console with `SET default_pool_size = N;`). Record tps, average latency and, during each run, `cl_waiting` and `sv_active` from `SHOW POOLS`. Explain the results in terms of what the server can actually run in parallel.

**Self-check:** pool size 5 gives clearly the lowest tps and the highest latency; going from 20 to 60 changes tps much less than going from 5 to 20 (it may even lower it). During every run, `sv_active` never exceeds the pool size.

<details><summary>Hint</summary>

`docker compose exec -T -e PGPASSWORD=app_pw pg pgbench -h pgbouncer -p 6432 -U app -c 200 -j 4 -T 20 bench`. Run `SHOW POOLS` from another terminal in the middle of the run. The lab machine's CPU count (`nproc`) matters.

</details>

---

## Exercise 12 ★★★: Make it transaction-pooling safe (lab)

A Bazaar batch job was written for a direct connection. In one session, it does:

```sql
SET search_path = bazaar_batch, public;
SET statement_timeout = '30s';
SELECT pg_advisory_lock(17);                       -- only one batch at a time
CREATE TEMP TABLE batch_ids AS SELECT aid FROM pgbench_accounts WHERE abalance < 0;
UPDATE pgbench_accounts SET abalance = 0 WHERE aid IN (SELECT aid FROM batch_ids);
SELECT pg_advisory_unlock(17);
```

(In the lab, `pgbench_accounts` stands in for a Bazaar table; create the `bazaar_batch` schema first.) Run it through the `bench_tiny` pool, and show three ways in which it leaves state behind for the next client of that backend. Then rewrite it so that it is safe behind a transaction-mode pooler, run it through `bench_tiny`, and show from a second client that nothing leaks.

**Self-check:** after the original version, a second client on `bench_tiny` sees `search_path = bazaar_batch, public`, `statement_timeout = 30s`, and the temp table `batch_ids`. After your version, it sees the defaults, no temp table, and `pg_locks` shows no advisory lock.

<details><summary>Hint</summary>

Put everything in one transaction: `SET LOCAL`, `pg_advisory_xact_lock()` (released at commit), and a CTE or `CREATE TEMP TABLE ... ON COMMIT DROP` instead of a session temp table. A clean way to check for leftovers: `SHOW search_path; SHOW statement_timeout; SELECT to_regclass('pg_temp.batch_ids');`.

</details>
