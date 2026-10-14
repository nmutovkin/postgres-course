# Module 17: Solutions

<!-- nav:start -->
> [Theory](../README.md) · [Exercises](../exercises.md) · [Project](../project.md) · **Solutions**  
> [← 16 Internals Deep Dive](../../16-internals/README.md) · [Course home](../../../README.md) · [18 Backup and Recovery →](../../18-backup-recovery/README.md)
<!-- nav:end -->

All solutions were run on PostgreSQL 17 against `./project/bin/reset.sh 17` (exercises 1 to 10) and in the [PgBouncer lab](../lab/) (exercises 11 and 12). Timings, PIDs and LSNs differ on every machine.

```sql
CREATE EXTENSION IF NOT EXISTS pgstattuple;
```

---

<!-- toc:start -->
**Contents**

- [Exercise 1 ★: What did someone change?](#exercise-1--what-did-someone-change)
- [Exercise 2 ★: Who can change what](#exercise-2--who-can-change-what)
- [Exercise 3 ★: Role settings and SET ROLE](#exercise-3--role-settings-and-set-role)
- [Exercise 4 ★★: How much memory can this query take?](#exercise-4--how-much-memory-can-this-query-take)
- [Exercise 5 ★★: Due for ANALYZE?](#exercise-5--due-for-analyze)
- [Exercise 6 ★★: Estimated vs. exact bloat](#exercise-6--estimated-vs-exact-bloat)
- [Exercise 7 ★★: Redundant indexes](#exercise-7--redundant-indexes)
- [Exercise 8 ★★: Break the chain](#exercise-8--break-the-chain)
- [Exercise 9 ★★: Find the query that spills](#exercise-9--find-the-query-that-spills)
- [Exercise 10 ★★★: Full-page writes, measured](#exercise-10--full-page-writes-measured)
- [Exercise 11 ★★★: Pool size vs. throughput (lab)](#exercise-11--pool-size-vs-throughput-lab)
- [Exercise 12 ★★★: Make it transaction-pooling safe (lab)](#exercise-12--make-it-transaction-pooling-safe-lab)

<!-- toc:end -->

## Exercise 1 ★: What did someone change?

```sql
SELECT name,
       setting || coalesce(' ' || unit, '') AS value,
       source,
       sourcefile || ':' || sourceline AS location,
       context,
       pending_restart
FROM pg_settings
WHERE source NOT IN ('default', 'override')
ORDER BY context = 'postmaster' DESC, name;
```

```text
            name            |              value              |       source       |                   location                   |  context   | pending_restart 
----------------------------+---------------------------------+--------------------+----------------------------------------------+------------+-----------------
 dynamic_shared_memory_type | posix                           | configuration file | /var/lib/postgresql/data/postgresql.conf:152 | postmaster | f
 listen_addresses           | *                               | configuration file | /var/lib/postgresql/data/postgresql.conf:60  | postmaster | f
 max_connections            | 100                             | configuration file | /var/lib/postgresql/data/postgresql.conf:65  | postmaster | f
 shared_buffers             | 16384 8kB                       | configuration file | /var/lib/postgresql/data/postgresql.conf:129 | postmaster | f
 shared_preload_libraries   | pg_stat_statements,auto_explain | command line       |                                              | postmaster | f
 application_name           | psql                            | client             |                                              | user       | f
 DateStyle                  | ISO, MDY                        | configuration file | /var/lib/postgresql/data/postgresql.conf:741 | user       | f
 default_text_search_config | pg_catalog.english              | configuration file | /var/lib/postgresql/data/postgresql.conf:767 | user       | f
 lc_messages                | en_US.utf8                      | configuration file | /var/lib/postgresql/data/postgresql.conf:757 | superuser  | f
 lc_monetary                | en_US.utf8                      | configuration file | /var/lib/postgresql/data/postgresql.conf:759 | user       | f
 lc_numeric                 | en_US.utf8                      | configuration file | /var/lib/postgresql/data/postgresql.conf:760 | user       | f
 lc_time                    | en_US.utf8                      | configuration file | /var/lib/postgresql/data/postgresql.conf:761 | user       | f
 log_timezone               | Etc/UTC                         | configuration file | /var/lib/postgresql/data/postgresql.conf:627 | sighup     | f
 max_wal_size               | 1024 MB                         | configuration file | /var/lib/postgresql/data/postgresql.conf:262 | sighup     | f
 min_wal_size               | 80 MB                           | configuration file | /var/lib/postgresql/data/postgresql.conf:263 | sighup     | f
 pg_stat_statements.track   | all                             | command line       |                                              | superuser  | f
 search_path                | bazaar, public                  | database           |                                              | user       | f
 TimeZone                   | Etc/UTC                         | configuration file | /var/lib/postgresql/data/postgresql.conf:743 | user       | f
 track_io_timing            | on                              | command line       |                                              | superuser  | f
(19 rows)
```

What the sources mean here:

- `configuration file`: values that `initdb` wrote into the Docker image's `postgresql.conf` (locale settings, `max_connections`, `shared_buffers`, `max_wal_size`, ...). The `location` column points at the exact line.
- `command line`: the `-c` options from `docker-compose.yml` (`shared_preload_libraries`, `pg_stat_statements.track`, `track_io_timing`). These override both configuration files, so an `ALTER SYSTEM` of them would have no effect, a classic source of confusion.
- `database`: `search_path`, set with `ALTER DATABASE bazaar SET search_path = ...` by `reset.sh`.
- `client`: values the client sent at connection start (here psql's `application_name`).

`'override'` marks a few parameters the server sets itself (for example `wal_buffers` when it is `-1`, i.e. automatic); they are not something an administrator changed, so they are excluded. Putting `postmaster` parameters first makes the "needs a restart to change" list stand out.

---

## Exercise 2 ★: Who can change what

From the contexts:

```sql
SELECT name, context FROM pg_settings
WHERE name IN ('work_mem', 'log_min_duration_statement', 'autovacuum_naptime',
               'max_connections', 'log_connections')
ORDER BY name;
```

```text
            name            |      context      
----------------------------+-------------------
 autovacuum_naptime         | sighup
 log_connections            | superuser-backend
 log_min_duration_statement | superuser
 max_connections            | postmaster
 work_mem                   | user
(5 rows)
```

Prediction: only `work_mem` (`user`) can be changed with `SET` by an ordinary role. `log_min_duration_statement` (`superuser`) needs a superuser or `GRANT SET ON PARAMETER`. `autovacuum_naptime` (`sighup`) can only be changed in the configuration files (or `ALTER SYSTEM`) plus a reload. `max_connections` (`postmaster`) needs a restart. `log_connections` (`superuser-backend`) can only be set at connection start, and only by a superuser (for example `PGOPTIONS='-c log_connections=on'`).

As `bazaar_app` (password-less inside the container):

```bash
docker exec -i course-pg psql -X -U bazaar_app -d bazaar \
  -c "SET work_mem = '64MB'" \
  -c "SET log_min_duration_statement = 0" \
  -c "SET autovacuum_naptime = '10s'" \
  -c "SET max_connections = 500" \
  -c "SET log_connections = on" 2>&1
```

```text
SET
ERROR:  permission denied to set parameter "log_min_duration_statement"
ERROR:  parameter "autovacuum_naptime" cannot be changed now
ERROR:  parameter "max_connections" cannot be changed without restarting the server
ERROR:  parameter "log_connections" cannot be set after connection start
```

One success, four errors, each phrased according to the context. To delegate the `superuser` one without making the role a superuser:

```sql
GRANT SET ON PARAMETER log_min_duration_statement TO bazaar_app;
```

```bash
docker exec -i course-pg psql -X -U bazaar_app -d bazaar -c "SET log_min_duration_statement = 0" 2>&1
```

```text
SET
```

```sql
REVOKE SET ON PARAMETER log_min_duration_statement FROM bazaar_app;
```

---

## Exercise 3 ★: Role settings and SET ROLE

```sql
ALTER ROLE bazaar_analyst IN DATABASE bazaar SET work_mem = '64MB';
SET ROLE bazaar_analyst;
SELECT current_user, current_setting('work_mem') AS work_mem;
RESET ROLE;
```

```text
ALTER ROLE
SET
  current_user  | work_mem 
----------------+----------
 bazaar_analyst | 4MB
(1 row)

RESET
```

```bash
docker exec -i course-pg psql -X -U bazaar_analyst -d bazaar -c "SELECT current_user, current_setting('work_mem') AS work_mem"
```

```text
  current_user  | work_mem 
----------------+----------
 bazaar_analyst | 64MB
(1 row)
```

Per-role settings from `pg_db_role_setting` are applied once, when a session **logs in**, for the login role. `SET ROLE` only changes the role used for privilege checks; it doesn't re-run the login-time configuration. So an application that logs in as one role and switches with `SET ROLE` per request (a common pattern with row-level security, Module 15) gets the **login** role's settings, never the target role's. If it needs per-tenant settings, it has to `SET LOCAL` them itself in each transaction.

Undo:

```sql
ALTER ROLE bazaar_analyst IN DATABASE bazaar RESET work_mem;
```

---

## Exercise 4 ★★: How much memory can this query take?

```sql
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT s.shop_name, count(DISTINCT o.id) AS orders, sum(oi.quantity * oi.unit_price) AS revenue
FROM orders o
JOIN order_items oi ON oi.order_id = o.id
JOIN sellers s ON s.id = o.seller_id
WHERE o.created_at >= '2025-12-01' AND o.created_at < '2026-01-01'
GROUP BY s.shop_name
ORDER BY revenue DESC;
```

```text
                                                                                           QUERY PLAN                                                                                           
------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
 Sort (actual time=283.361..285.340 rows=1761 loops=1)
   Sort Key: (sum(((oi.quantity)::numeric * oi.unit_price))) DESC
   Sort Method: quicksort  Memory: 144kB
   Buffers: shared hit=235 read=16430
   I/O Timings: shared read=36.903
   ->  GroupAggregate (actual time=205.295..284.034 rows=1761 loops=1)
         Group Key: s.shop_name
         Buffers: shared hit=232 read=16430
         I/O Timings: shared read=36.903
         ->  Gather Merge (actual time=205.256..242.502 rows=96670 loops=1)
               Workers Planned: 2
               Workers Launched: 2
               Buffers: shared hit=232 read=16430
               I/O Timings: shared read=36.903
               ->  Sort (actual time=190.789..195.200 rows=32223 loops=3)
                     Sort Key: s.shop_name, o.id
                     Sort Method: quicksort  Memory: 3201kB
                     Buffers: shared hit=232 read=16430
                     I/O Timings: shared read=36.903
                     Worker 0:  Sort Method: quicksort  Memory: 2615kB
                     Worker 1:  Sort Method: quicksort  Memory: 2610kB
                     ->  Hash Join (actual time=124.703..142.817 rows=32223 loops=3)
                           Hash Cond: (o.seller_id = s.id)
                           Buffers: shared hit=138 read=16430
                           I/O Timings: shared read=36.903
                           ->  Parallel Hash Join (actual time=123.888..135.523 rows=32223 loops=3)
                                 Hash Cond: (oi.order_id = o.id)
                                 Buffers: shared read=16411
                                 I/O Timings: shared read=36.803
                                 ->  Parallel Seq Scan on order_items oi (actual time=0.027..39.778 rows=422309 loops=3)
                                       Buffers: shared read=9316
                                       I/O Timings: shared read=21.815
                                 ->  Parallel Hash (actual time=22.712..22.713 rows=11496 loops=3)
                                       Buckets: 65536  Batches: 1  Memory Usage: 2176kB
                                       Buffers: shared read=7095
                                       I/O Timings: shared read=14.988
                                       ->  Parallel Seq Scan on orders o (actual time=11.689..18.261 rows=11496 loops=3)
                                             Filter: ((created_at >= '2025-12-01 00:00:00+00'::timestamp with time zone) AND (created_at < '2026-01-01 00:00:00+00'::timestamp with time zone))
                                             Rows Removed by Filter: 155171
                                             Buffers: shared read=7095
                                             I/O Timings: shared read=14.988
                           ->  Hash (actual time=0.712..0.713 rows=2000 loops=3)
                                 Buckets: 2048  Batches: 1  Memory Usage: 139kB
                                 Buffers: shared hit=38 read=19
                                 I/O Timings: shared read=0.100
                                 ->  Seq Scan on sellers s (actual time=0.024..0.319 rows=2000 loops=3)
                                       Buffers: shared hit=38 read=19
                                       I/O Timings: shared read=0.100
 Planning:
   Buffers: shared hit=292 read=37
   I/O Timings: shared read=0.235
 Planning Time: 1.385 ms
 Execution Time: 285.943 ms
(53 rows)
```

The nodes that use `work_mem`, and what they actually used:

| Node | Runs in | Actual memory | Allowed (worst case) |
|---|---|---|---|
| top `Sort` (by revenue) | leader only | 144 kB | 1 × 64 MB |
| `GroupAggregate` with `count(DISTINCT o.id)` | leader only | (sorts per group, not shown) | 1 × 64 MB |
| `Sort` (by shop_name, o.id) below `Gather Merge` | leader + 2 workers | 3,201 + 2,615 + 2,610 kB | 3 × 64 MB |
| `Parallel Hash` (orders) | shared by 3 processes | 2,176 kB | 3 × 2 × 64 MB |
| `Hash` (sellers) | each of the 3 processes builds its own | 3 × 139 kB | 3 × 2 × 64 MB |

Nothing spilled: every sort says `quicksort` (in memory) and every hash `Batches: 1`. The `count(DISTINCT ...)` is easy to overlook: a `GroupAggregate` with `DISTINCT` sorts each group's values, and that sort uses `work_mem` too, although `EXPLAIN` doesn't print it.

Worst case per query: (1 + 1 + 3) × 64 MB for the sorts plus (3 + 3) × 2 × 64 MB for the hashes = 320 MB + 768 MB = **1,088 MB**. For 40 concurrent dashboard sessions: **about 42.5 GB**, on a server that may have 16 GB of RAM. The actual peak is about 11 MB per query, or 450 MB for 40 sessions. The gap between the two is why a large global `work_mem` usually works fine for months and then takes the server down on the day a plan changes (for example, when a hash join's estimate is far too low and its hash table grows to the limit) or the number of concurrent reports doubles. Keep the global value modest and give the reporting role a larger one.

---

## Exercise 5 ★★: Due for ANALYZE?

The query reads per-table overrides from `reloptions` and falls back to the global settings:

```sql
CREATE OR REPLACE VIEW pg_temp.analyze_due AS
WITH t AS (
    SELECT s.relname, s.n_mod_since_analyze, c.reltuples,
           coalesce((SELECT substr(o, length('autovacuum_analyze_threshold=') + 1)::float8
                     FROM unnest(c.reloptions) AS o WHERE o LIKE 'autovacuum_analyze_threshold=%'),
                    current_setting('autovacuum_analyze_threshold')::float8) AS thr,
           coalesce((SELECT substr(o, length('autovacuum_analyze_scale_factor=') + 1)::float8
                     FROM unnest(c.reloptions) AS o WHERE o LIKE 'autovacuum_analyze_scale_factor=%'),
                    current_setting('autovacuum_analyze_scale_factor')::float8) AS sf
    FROM pg_stat_user_tables s
    JOIN pg_class c ON c.oid = s.relid
    WHERE s.schemaname = 'bazaar' AND c.relkind IN ('r', 'm')
)
SELECT relname, n_mod_since_analyze,
       round(thr + sf * greatest(reltuples, 0)) AS analyze_threshold,
       round(100 * n_mod_since_analyze / (thr + sf * greatest(reltuples, 0))) AS pct
FROM t
WHERE n_mod_since_analyze > thr + sf * greatest(reltuples, 0)
ORDER BY pct DESC;

SELECT * FROM pg_temp.analyze_due;
```

```text
CREATE VIEW
 relname | n_mod_since_analyze | analyze_threshold | pct 
---------+---------------------+-------------------+-----
(0 rows)
```

(A temporary view keeps the query reusable for the rest of the session. Right after `reset.sh` nothing is due, since the seed scripts ended with `ANALYZE`.) Now the test:

```sql
ALTER TABLE payments SET (autovacuum_enabled = off, autovacuum_analyze_scale_factor = 0.02);
UPDATE payments SET provider_ref = provider_ref WHERE id <= 12000;
SELECT pg_stat_force_next_flush();
SELECT * FROM pg_temp.analyze_due;
```

```text
ALTER TABLE
UPDATE 12000
 pg_stat_force_next_flush 
--------------------------
 
(1 row)

 relname  | n_mod_since_analyze | analyze_threshold | pct 
----------+---------------------+-------------------+-----
 payments |               12000 |             10786 | 111
(1 row)
```

12,000 modifications against a threshold of 50 + 0.02 × 536,816 ≈ 10,786: due. With the default scale factor (0.1) the threshold would be 53,732, and these 12,000 changes would go unnoticed for a long time. Clean up:

```sql
ALTER TABLE payments RESET (autovacuum_enabled, autovacuum_analyze_scale_factor);
ANALYZE payments;
SELECT count(*) AS still_due FROM pg_temp.analyze_due WHERE relname = 'payments';
```

```text
ALTER TABLE
ANALYZE
 still_due 
-----------
         0
(1 row)
```

---

## Exercise 6 ★★: Estimated vs. exact bloat

```sql
ALTER TABLE payments SET (autovacuum_enabled = off);
UPDATE payments SET provider_ref = provider_ref WHERE id % 10 = 0;
UPDATE payments SET provider_ref = provider_ref WHERE id % 10 = 1;
UPDATE payments SET provider_ref = provider_ref WHERE id % 10 = 2;
UPDATE payments SET provider_ref = provider_ref WHERE id % 10 = 3;
ANALYZE payments;

-- 1. the estimate (section 6.3)
SELECT relpages AS actual_pages,
       ceil(reltuples * (24 + 4 + (SELECT sum(avg_width) FROM pg_stats
                                   WHERE schemaname = 'bazaar' AND tablename = 'payments')) / 8168)::int AS expected_pages,
       round(100 * (1 - ceil(reltuples * (24 + 4 + (SELECT sum(avg_width) FROM pg_stats
                                                  WHERE schemaname = 'bazaar' AND tablename = 'payments')) / 8168)
                        / relpages)) AS est_bloat_pct
FROM pg_class WHERE oid = 'payments'::regclass;

-- 2. the exact numbers
SELECT pg_size_pretty(table_len) AS size, tuple_count, dead_tuple_count,
       round(dead_tuple_percent) AS dead_pct, round(free_percent) AS free_pct,
       round(dead_tuple_percent + free_percent) AS dead_plus_free_pct
FROM pgstattuple('payments');
```

```text
ALTER TABLE
UPDATE 53681
UPDATE 53682
UPDATE 53682
UPDATE 53682
ANALYZE
 actual_pages | expected_pages | est_bloat_pct 
--------------+----------------+---------------
         6916 |           5653 |            18
(1 row)

 size  | tuple_count | dead_tuple_count | dead_pct | free_pct | dead_plus_free_pct 
-------+-------------+------------------+----------+----------+--------------------
 54 MB |      536816 |            53699 |        8 |        3 |                 11
(1 row)
```

You updated 214,727 rows, so you might expect 214,727 dead tuples. There are only 53,699, the last slice. **Page pruning** (Module 16, section 7) cleaned up the earlier ones: each later `UPDATE` visited pages that were full and contained dead versions from the previous `UPDATE`, so it pruned them and reused the space, without any VACUUM. Only the dead versions of the last update are still there, plus the space the table grew by. So the table is 54 MB instead of 48 MB (12% larger), and `pgstattuple` counts 8% dead + 3% free.

The estimate says 18%, higher than the exact 11%. The estimate ignores alignment padding, the null bitmap and per-page overhead, so its "expected pages" is too low and every table looks a few percent more bloated than it is (compare the 4 to 8% it reports for untouched tables in section 6.3). Treat the estimate as a screening tool that tells you where to run `pgstattuple`, not as a measurement. Note also that a table that is updated slice by slice stays fairly compact even without VACUUM, because page pruning reclaims the space inside the pages (though not the dead line pointers or the index entries: those still need VACUUM). It's large single-transaction updates (like the `UPDATE inventory` in section 6.1) and long transactions that cause severe bloat.

```sql
-- 3. fix, measure, and re-enable autovacuum
VACUUM FULL payments;
SELECT pg_size_pretty(table_len) AS size, tuple_count, dead_tuple_count,
       round(free_percent) AS free_pct
FROM pgstattuple('payments');
ALTER TABLE payments RESET (autovacuum_enabled);
```

```text
VACUUM
 size  | tuple_count | dead_tuple_count | free_pct 
-------+-------------+------------------+----------
 48 MB |      536816 |                0 |        1
(1 row)

ALTER TABLE
```

---

## Exercise 7 ★★: Redundant indexes

The naive version compares only the column lists:

```sql
SELECT a.indrelid::regclass AS table_name,
       a.indexrelid::regclass AS redundant,
       b.indexrelid::regclass AS covered_by
FROM pg_index a
JOIN pg_index b ON b.indrelid = a.indrelid AND b.indexrelid <> a.indexrelid
WHERE a.indrelid IN (SELECT oid FROM pg_class WHERE relnamespace = 'bazaar'::regnamespace)
  AND NOT a.indisunique
  AND a.indnatts <= b.indnatts
  AND (b.indkey::int2[])[0:a.indnatts - 1] = (a.indkey::int2[])[0:a.indnatts - 1];
```

```text
 table_name |        redundant         |              covered_by               
------------+--------------------------+---------------------------------------
 addresses  | addresses_user_id_idx    | addresses_one_default_per_user
 products   | products_category_id_idx | products_active_category_id_price_idx
(2 rows)
```

Both are false positives. `addresses_one_default_per_user` is a **partial** unique index (`WHERE is_default`), so it contains only default addresses and can't serve "all addresses of user X". `products_active_category_id_price_idx` is partial too (`WHERE status = 'active'`). The careful version requires the covering index to be a plain, non-partial, non-expression B-tree, and the redundant one to be a plain B-tree as well:

```sql
SELECT a.indrelid::regclass AS table_name,
       a.indexrelid::regclass AS redundant,
       b.indexrelid::regclass AS covered_by,
       pg_size_pretty(pg_relation_size(a.indexrelid)) AS wasted
FROM pg_index a
JOIN pg_index b ON b.indrelid = a.indrelid AND b.indexrelid <> a.indexrelid
JOIN pg_class ca ON ca.oid = a.indexrelid
JOIN pg_class cb ON cb.oid = b.indexrelid
JOIN pg_am am_a ON am_a.oid = ca.relam
JOIN pg_am am_b ON am_b.oid = cb.relam
WHERE a.indrelid IN (SELECT oid FROM pg_class WHERE relnamespace = 'bazaar'::regnamespace)
  AND am_a.amname = 'btree' AND am_b.amname = 'btree'
  AND NOT a.indisunique                         -- unique indexes enforce constraints: keep
  AND a.indpred IS NULL AND b.indpred IS NULL   -- no partial indexes
  AND a.indexprs IS NULL AND b.indexprs IS NULL -- no expression indexes
  AND a.indnatts <= b.indnatts
  AND (b.indkey::int2[])[0:a.indnatts - 1] = (a.indkey::int2[])[0:a.indnatts - 1]
  AND (b.indclass::oid[])[0:a.indnatts - 1] = (a.indclass::oid[])[0:a.indnatts - 1];  -- same operator classes
```

```text
 table_name | redundant | covered_by | wasted 
------------+-----------+------------+--------
(0 rows)
```

Nothing on the reset database. With a deliberately redundant index:

```sql
CREATE INDEX ex_orders_user_id_idx ON orders (user_id);
```

```text
 table_name |       redundant       |          covered_by           | wasted  
------------+-----------------------+-------------------------------+---------
 orders     | ex_orders_user_id_idx | orders_user_id_created_at_idx | 5296 kB
(1 row)
```

`orders_user_id_created_at_idx` on `(user_id, created_at DESC)` serves every query that `(user_id)` alone could, at a slightly larger size per lookup. Some judgement remains: the single-column index is smaller and a bit faster for `WHERE user_id = ?` lookups, and the sort direction of a later column doesn't matter for prefix lookups. In general, though, the extra 5 MB and the extra index write per insert aren't worth it. Clean up:

```sql
DROP INDEX ex_orders_user_id_idx;
```

---

## Exercise 8 ★★: Break the chain

The three sessions:

```sql
-- Session 1
BEGIN;
UPDATE inventory SET reserved = reserved WHERE product_id = 100;
-- Session 2
UPDATE inventory SET reserved = reserved WHERE product_id = 100;          -- waits
-- Session 3
SELECT quantity FROM inventory WHERE product_id = 100 FOR UPDATE;         -- waits
```

From a fourth session, the tree (the query from section 7.3), then a cancel of the root, then the tree again:

```sql
SELECT pg_cancel_backend(<root pid>);
```

```text
-- the chain
   pid_tree   |        state        |     waiting_on     |                          query                          
--------------+---------------------+--------------------+---------------------------------------------------------
 1470         | idle in transaction | Client:ClientRead  | UPDATE inventory SET reserved = reserved WHERE product_
     1476     | active              | Lock:transactionid | UPDATE inventory SET reserved = reserved WHERE product_
         1482 | active              | Lock:tuple         | SELECT quantity FROM inventory WHERE product_id = 100 F
(3 rows)

-- cancel the root
 pg_cancel_backend 
-------------------
 t
(1 row)

   pid_tree   |        state        |     waiting_on     |                          query                          
--------------+---------------------+--------------------+---------------------------------------------------------
 1470         | idle in transaction | Client:ClientRead  | UPDATE inventory SET reserved = reserved WHERE product_
     1476     | active              | Lock:transactionid | UPDATE inventory SET reserved = reserved WHERE product_
         1482 | active              | Lock:tuple         | SELECT quantity FROM inventory WHERE product_id = 100 F
(3 rows)

-- terminate the root
 pg_terminate_backend 
----------------------
 t
(1 row)

 pid_tree | state | waiting_on | query 
----------+-------+------------+-------
(0 rows)

-- what session 2 saw:
UPDATE 1
-- what session 3 saw:
 quantity 
----------
      146
(1 row)
```

- **The chain.** Session 2 waits for session 1's transaction (`Lock:transactionid`: it wants to update a row whose `xmax` is session 1's XID). Session 3 waits behind session 2 (`Lock:tuple`): the first waiter for a row lock holds the *tuple lock*, which queues later waiters in order. So the tree has depth 2, with session 1 at the root.
- **Cancel does nothing useful.** `pg_cancel_backend` sends the cancel signal and returns `true` ("signal sent"), but session 1 is `idle in transaction`: no statement is running, so there is nothing to cancel. The chain is unchanged. (Cancelling session 2 instead would only abort the victim.)
- **Terminate resolves it.** `pg_terminate_backend` ends session 1, its transaction rolls back, and its row lock is released. Session 2's `UPDATE` proceeds and commits; then session 3 gets the row and its `SELECT ... FOR UPDATE` returns. Session 1's client finds out on its next statement: `FATAL: terminating connection due to administrator command`.

The lasting fix is `idle_in_transaction_session_timeout` for the application role, so that a forgotten transaction ends itself.

---

## Exercise 9 ★★: Find the query that spills

```sql
SELECT pg_stat_statements_reset();

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

SELECT queryid, calls, temp_blks_written, round(mean_exec_time::numeric, 1) AS mean_ms,
       left(regexp_replace(query, '\s+', ' ', 'g'), 60) AS query
FROM pg_stat_statements
WHERE temp_blks_written > 0
ORDER BY temp_blks_written DESC;
```

```text
   pg_stat_statements_reset    
-------------------------------
 2026-10-14 08:23:55.505366+00
(1 row)

 seller_id |   id   |  total   | rn 
-----------+--------+----------+----
         1 | 320175 | 14819.89 |  1
         1 | 156087 | 14124.90 |  2
         1 | 303800 | 14108.92 |  3
         2 | 413236 | 12350.91 |  1
         2 | 126348 | 11825.93 |  2
         2 | 287862 | 10831.91 |  3
         3 | 298693 | 14208.92 |  1
         3 | 181479 | 12549.95 |  2
         3 |  94691 | 12497.93 |  3
         4 | 157581 | 21970.88 |  1
(10 rows)

 seller_id |   id   |  total   | rn 
-----------+--------+----------+----
         1 | 320175 | 14819.89 |  1
         1 | 156087 | 14124.90 |  2
         1 | 303800 | 14108.92 |  3
         2 | 413236 | 12350.91 |  1
         2 | 126348 | 11825.93 |  2
         2 | 287862 | 10831.91 |  3
         3 | 298693 | 14208.92 |  1
         3 | 181479 | 12549.95 |  2
         3 |  94691 | 12497.93 |  3
         4 | 157581 | 21970.88 |  1
(10 rows)

       queryid        | calls | temp_blks_written | mean_ms |                            query                             
----------------------+-------+-------------------+---------+--------------------------------------------------------------
 -1162122087694771736 |     2 |              3828 |   374.7 | SELECT seller_id, id, total, rn FROM ( SELECT seller_id, id,
(1 row)
```

Which node spills, and by how much?

```sql
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF)
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

```text
                                          QUERY PLAN                                          
----------------------------------------------------------------------------------------------
 Limit (actual rows=10 loops=1)
   ->  Incremental Sort (actual rows=10 loops=1)
         Sort Key: orders.seller_id, (row_number() OVER (?))
         Presorted Key: orders.seller_id
         Full-sort Groups: 1  Sort Method: quicksort  Average Memory: 25kB  Peak Memory: 25kB
         ->  WindowAgg (actual rows=13 loops=1)
               Run Condition: (row_number() OVER (?) <= 3)
               ->  Sort (actual rows=143224 loops=1)
                     Sort Key: orders.seller_id, orders.total DESC
                     Sort Method: external merge  Disk: 15256kB
                     ->  Seq Scan on orders (actual rows=457908 loops=1)
                           Filter: (status = ANY ('{delivered,shipped}'::order_status[]))
                           Rows Removed by Filter: 42092
 Planning Time: 0.097 ms
 Execution Time: 384.147 ms
(15 rows)
```

The window function needs its input sorted by `seller_id, total DESC`. That `Sort` node needed more than the default 4 MB and wrote 15 MB to disk (`external merge`). How much memory is enough? An in-memory sort needs considerably more than the size of the spill file, because each tuple in memory carries overhead; about twice the disk figure is a reasonable first guess, so 32 to 64 MB.

The fix for this report only: raise `work_mem` inside the report's transaction.

```sql
BEGIN;
SET LOCAL work_mem = '64MB';
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
COMMIT;
SHOW work_mem;

SELECT queryid, calls, temp_blks_written, round(mean_exec_time::numeric, 1) AS mean_ms
FROM pg_stat_statements
WHERE query LIKE 'SELECT seller_id, id, total, rn%';
```

```text
BEGIN
SET
 seller_id |   id   |  total   | rn 
-----------+--------+----------+----
         1 | 320175 | 14819.89 |  1
         1 | 156087 | 14124.90 |  2
         1 | 303800 | 14108.92 |  3
         2 | 413236 | 12350.91 |  1
         2 | 126348 | 11825.93 |  2
         2 | 287862 | 10831.91 |  3
         3 | 298693 | 14208.92 |  1
         3 | 181479 | 12549.95 |  2
         3 |  94691 | 12497.93 |  3
         4 | 157581 | 21970.88 |  1
(10 rows)

COMMIT
 work_mem 
----------
 4MB
(1 row)

       queryid        | calls | temp_blks_written | mean_ms 
----------------------+-------+-------------------+---------
 -1162122087694771736 |     3 |              3828 |   410.9
(1 row)
```

Three calls now, but `temp_blks_written` didn't grow: the third run fit in memory. (It isn't faster here: the 15 MB of temporary files never left the operating system's page cache, so spilling was cheap. On a busy server, where temp files compete for real disk I/O, the difference is much larger.) `SHOW work_mem` after the `COMMIT` proves that the session is back at the default. In an application, the same effect comes from running the report as a role with a larger `work_mem` (`ALTER ROLE ... SET`), or from a function with `SET work_mem = '64MB'` in its definition.

---

## Exercise 10 ★★★: Full-page writes, measured

```sql
CREATE TEMP TABLE wal_runs (run text, wal_records bigint, wal_fpi bigint, wal_bytes numeric);

CHECKPOINT;
SELECT pg_stat_reset_shared('wal');
UPDATE users SET is_active = is_active;
SELECT pg_stat_force_next_flush();
INSERT INTO wal_runs SELECT '1: after CHECKPOINT', wal_records, wal_fpi, wal_bytes FROM pg_stat_wal;

SELECT pg_stat_reset_shared('wal');
UPDATE users SET is_active = is_active;
SELECT pg_stat_force_next_flush();
INSERT INTO wal_runs SELECT '2: no checkpoint', wal_records, wal_fpi, wal_bytes FROM pg_stat_wal;

CHECKPOINT;
SELECT pg_stat_reset_shared('wal');
UPDATE users SET is_active = is_active;
SELECT pg_stat_force_next_flush();
INSERT INTO wal_runs SELECT '3: after CHECKPOINT', wal_records, wal_fpi, wal_bytes FROM pg_stat_wal;

SELECT run, wal_records, wal_fpi, pg_size_pretty(wal_bytes) AS wal, round(wal_bytes / 100000) AS bytes_per_row
FROM wal_runs ORDER BY run;
```

```text
CREATE TABLE
CHECKPOINT
 pg_stat_reset_shared 
----------------------
 
(1 row)

UPDATE 100000
 pg_stat_force_next_flush 
--------------------------
 
(1 row)

INSERT 0 1
 pg_stat_reset_shared 
----------------------
 
(1 row)

UPDATE 100000
 pg_stat_force_next_flush 
--------------------------
 
(1 row)

INSERT 0 1
CHECKPOINT
 pg_stat_reset_shared 
----------------------
 
(1 row)

UPDATE 100000
 pg_stat_force_next_flush 
--------------------------
 
(1 row)

INSERT 0 1
         run         | wal_records | wal_fpi |  wal  | bytes_per_row 
---------------------+-------------+---------+-------+---------------
 1: after CHECKPOINT |      404547 |    2314 | 52 MB |           540
 2: no checkpoint    |      412805 |       0 | 36 MB |           373
 3: after CHECKPOINT |      411232 |    4266 | 57 MB |           596
(3 rows)
```

Reading the three runs:

- **Run 1**, right after a checkpoint: 2,314 full-page images. Every heap page whose old row versions get their `xmax` set, and every index page that receives an entry, is modified for the first time since the checkpoint, so its first WAL record carries the whole page.
- **Run 2**, no checkpoint in between: **zero** full-page images. All the pages it touches were already logged in full during run 1; WAL only needs the small per-row records. It still writes about 36 MB, because 100,000 updates mean about 400,000 records (heap update, index inserts for the new versions, ...): about 370 bytes per row.
- **Run 3**, after another checkpoint: even more full-page images than run 1 (4,266), because the table and its indexes have grown with each round (the new versions went to new pages), so more distinct pages are touched.

The full-page images cost roughly 150 to 220 extra bytes per updated row here. For a job that runs every minute: with `checkpoint_timeout = 5min`, one run in five pays the full-page-image price (assuming nothing else forces checkpoints); with `30min`, one in thirty. Per hour that's 12 expensive runs × ~55 MB + 48 cheap runs × ~36 MB ≈ 2.3 GB, versus 2 × 55 + 58 × 36 ≈ 2.2 GB. The difference grows with the number of distinct pages a workload touches between checkpoints: for random updates across a large table, nearly every update after a checkpoint carries a full 8 kB page, and spacing checkpoints out (with a `max_wal_size` big enough to allow it) can cut WAL volume by half or more. The cost is longer crash recovery.

---

## Exercise 11 ★★★: Pool size vs. throughput (lab)

From `modules/17-operations/lab`, with the lab running (`docker compose up -d --wait`; `./bench.sh` once, or `pgbench -i -s 10` as below, to create the tables):

```bash
cd modules/17-operations/lab
pgb() { docker compose exec -T -e PGPASSWORD=app_pw pg pgbench -h pgbouncer -p 6432 -U app "$@" bench 2>&1; }
admin() { docker compose exec -T -e PGPASSWORD=postgres pg psql -X -q -h pgbouncer -p 6432 -U postgres -d pgbouncer "$@"; }
docker compose exec -T pg psql -U postgres -d bench -Atc "SELECT 1 FROM pg_tables WHERE tablename = 'pgbench_accounts'" | grep -q 1 \
  || pgb -i -s 10 -q -h pg -p 5432 > /dev/null
echo "nproc: $(nproc)"
for size in 5 20 60; do
  admin -c "SET default_pool_size = $size" > /dev/null
  pgb -c 200 -j 4 -T 20 > /tmp/m17_e11.txt &
  sleep 10
  POOL=$(admin -A -F ' ' -t -c "SHOW POOLS" | awk '$1 == "bench" {print "cl_active=" $3, "cl_waiting=" $4, "sv_active=" $7, "sv_idle=" $10}')
  wait
  echo "pool_size=$size  $POOL  $(grep -E '^(tps|latency average)' /tmp/m17_e11.txt | sed 's/ (without initial connection time)//' | tr '\n' ' ')"
done
admin -c "SET default_pool_size = 20" > /dev/null
```

```text
nproc: 4
pool_size=5  cl_active=5 cl_waiting=195 sv_active=5 sv_idle=0  latency average = 211.845 ms tps = 944.086313 
pool_size=20  cl_active=20 cl_waiting=180 sv_active=20 sv_idle=0  latency average = 134.139 ms tps = 1490.991309 
pool_size=60  cl_active=60 cl_waiting=140 sv_active=60 sv_idle=0  latency average = 184.614 ms tps = 1083.338857 
```

With 5 server connections the database is underused: 4 CPUs, but each backend spends part of every transaction waiting for the next statement from its client (pgbench sends the statements of a transaction one by one), so 5 backends can't keep 4 cores busy, and 195 clients queue in PgBouncer. 20 connections keep the CPUs busy: throughput up by about 60%, latency down. With 60 connections, throughput drops again: the 4 cores are now shared by 60 active backends, which contend for the same rows of the small `pgbench_branches` table (scale 10 = 10 branches, every transaction updates one), and lock waits and context switches eat the gain. More server connections than the machine can run in parallel just moves the queue from PgBouncer, where waiting is cheap, into PostgreSQL, where it costs locks, memory and CPU.

The usual rule of thumb (2 to 4 × CPU cores, so 8 to 16 here, adjusted for how much time transactions spend waiting on I/O or the client) is a starting point; measure with your own workload.

---

## Exercise 12 ★★★: Make it transaction-pooling safe (lab)

Set-up (once), directly on the server:

```bash
cd modules/17-operations/lab
docker compose exec -T pg psql -X -q -U postgres -d bench -c "CREATE SCHEMA IF NOT EXISTS bazaar_batch AUTHORIZATION app"
```

The original job through `bench_tiny`, then a *different* client on the same pool:

```bash
cd modules/17-operations/lab
pq() { docker compose exec -T -e PGPASSWORD=app_pw pg psql -X -q -h pgbouncer -p 6432 -U app -d bench_tiny "$@"; }
pq <<'SQL'
SET search_path = bazaar_batch, public;
SET statement_timeout = '30s';
SELECT pg_advisory_lock(17);
CREATE TEMP TABLE batch_ids AS SELECT aid FROM pgbench_accounts WHERE abalance < 0;
UPDATE pgbench_accounts SET abalance = 0 WHERE aid IN (SELECT aid FROM batch_ids);
SELECT pg_advisory_unlock(17);
SQL
echo "--- next client:"
pq -c "SHOW search_path" -c "SHOW statement_timeout" \
   -c "SELECT to_regclass('pg_temp.batch_ids') AS leftover_temp_table"
```

```text
 pg_advisory_lock 
------------------
 
(1 row)

 pg_advisory_unlock 
--------------------
 t
(1 row)

--- next client:
     search_path      
----------------------
 bazaar_batch, public
(1 row)

 statement_timeout 
-------------------
 30s
(1 row)

 leftover_temp_table 
---------------------
 batch_ids
(1 row)
```

Three leaks: the next client runs with the batch job's `search_path` (it would silently resolve unqualified table names in `bazaar_batch` first) and its 30-second `statement_timeout`, and it can see, and trip over, the batch's temporary table (a second run of the job would even fail with `relation "batch_ids" already exists`). The advisory lock happened to be released here because the job unlocked it explicitly, but if the job had failed between lock and unlock, the lock would stay held by the server connection for whoever comes next, and with more than one server connection in the pool, `pg_advisory_unlock` may run on a different backend than `pg_advisory_lock` did (it then returns `false` with a warning, and the real lock stays held).

The transaction-safe version keeps every piece of state inside one transaction:

```bash
cd modules/17-operations/lab
docker compose restart pgbouncer > /dev/null 2>&1; sleep 2      # start from a clean server connection
pq() { docker compose exec -T -e PGPASSWORD=app_pw pg psql -X -q -h pgbouncer -p 6432 -U app -d bench_tiny "$@"; }
pq <<'SQL'
BEGIN;
SET LOCAL search_path = bazaar_batch, public;
SET LOCAL statement_timeout = '30s';
SELECT pg_advisory_xact_lock(17);          -- released automatically at COMMIT/ROLLBACK
CREATE TEMP TABLE batch_ids ON COMMIT DROP AS
    SELECT aid FROM pgbench_accounts WHERE abalance < 0;
UPDATE pgbench_accounts SET abalance = 0 WHERE aid IN (SELECT aid FROM batch_ids);
COMMIT;
SQL
echo "--- next client:"
pq -c "SHOW search_path" -c "SHOW statement_timeout" \
   -c "SELECT to_regclass('pg_temp.batch_ids') AS leftover_temp_table"
docker compose exec -T pg psql -X -U postgres -d bench -c "SELECT count(*) AS advisory_locks FROM pg_locks WHERE locktype = 'advisory'"
```

```text
 pg_advisory_xact_lock 
-----------------------
 
(1 row)

--- next client:
   search_path   
-----------------
 "$user", public
(1 row)

 statement_timeout 
-------------------
 0
(1 row)

 leftover_temp_table 
---------------------
 
(1 row)

 advisory_locks 
----------------
              0
(1 row)
```

- `SET LOCAL` reverts at the end of the transaction.
- `pg_advisory_xact_lock` is held until the transaction ends and can't be leaked or unlocked from the wrong backend.
- `ON COMMIT DROP` removes the temporary table at commit. (Creating temp tables still has a cost: each one writes to the system catalogs. If the ID list is small, a CTE, `UPDATE ... WHERE aid IN (SELECT ...)` directly, or an array variable avoids the temp table entirely. That's what `place_order()` in Module 10 does.)

The price: the whole job is one transaction. For a batch that runs for minutes, that holds a server connection, its locks, and the xmin horizon for the whole time. Long batch jobs are better off on a **session-mode** pool or a direct connection, and the transaction pool stays for short web transactions.
