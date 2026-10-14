# Module 17: Configuration, Maintenance and Monitoring

It is the Monday after Black Friday. Bazaar's checkout is slow, the on-call engineer sees 400 connections on the database, a migration that "only adds a default" has been waiting for ten minutes, and the `jobs` table that holds the e-mail queue is ten times its usual size. Each of these is an everyday operational problem with a known diagnosis and a known fix, and each fix depends on the internals from Module 16. This module is about running PostgreSQL rather than querying it: where configuration lives and how to change it safely, how to size memory and connections, how to tune checkpoints and autovacuum, how to measure and remove bloat, how to see what the server is doing right now (and what it was doing at 3 a.m.), how to stop a runaway query, and how to put a connection pooler in front of it all.

**What you'll learn**

- How PostgreSQL reads its configuration: `postgresql.conf`, `postgresql.auto.conf` and `ALTER SYSTEM`, `pg_settings` and setting contexts, reload versus restart, and per-role, per-database, per-session and per-transaction settings.
- How to size `shared_buffers`, `work_mem` (and why it multiplies), `maintenance_work_mem` and `effective_cache_size`, and why many connections hurt.
- How to tune checkpoints and WAL (`max_wal_size`, `checkpoint_completion_target`) using `pg_stat_checkpointer`.
- How autovacuum decides what to do, how to tune it globally and per table, and how to watch it with `pg_stat_progress_vacuum`.
- How to measure bloat (`pgstattuple`, estimates) and fix it (`VACUUM`, `VACUUM FULL`, `pg_repack`, `REINDEX CONCURRENTLY`).
- The monitoring views: `pg_stat_activity` and wait events, `pg_locks` and blocking chains, `pg_stat_user_tables`/`_indexes`, `pg_stat_database`, `pg_stat_checkpointer`, `pg_stat_io`, and `pg_stat_statements`.
- Logging that pays for itself: `log_line_prefix`, `log_min_duration_statement`, `log_lock_waits`, `log_autovacuum_min_duration`, and `auto_explain`.
- How to stop work: `pg_cancel_backend` vs `pg_terminate_backend`, and the timeouts that do it automatically.
- Connection pooling with PgBouncer: session, transaction and statement modes, what breaks in transaction mode, and a working lab with `pgbench`.
- What a metrics stack (postgres_exporter, Prometheus, Grafana) looks like and which alerts matter.

**Before you start**

```bash
./project/bin/reset.sh 17     # state at the start of module 17, with the large dataset (takes a few minutes)
./project/bin/psql.sh
```

You need the contrib extensions from Module 16 (they are not part of the reset state, so create them again if you reset):

```sql
CREATE EXTENSION IF NOT EXISTS pgstattuple;
CREATE EXTENSION IF NOT EXISTS pg_buffercache;
```

`pg_stat_statements` is already installed (Module 12), and the course container preloads both `pg_stat_statements` and `auto_explain` (see `shared_preload_libraries` in `docker-compose.yml`). Section 11 uses a separate Docker Compose lab in [`lab/`](lab/).

Many commands here change server-wide settings. On the course container that's fine: `./project/bin/reset.sh` doesn't reset them, but section 1 shows how to undo anything with `ALTER SYSTEM RESET`. Never experiment like this on a shared server.

---

## 1. Where settings come from

### 1.1 The files

PostgreSQL reads its configuration at startup, and again whenever it is told to **reload**, from these sources, in this order (later sources override earlier ones):

1. Built-in defaults compiled into the server.
2. **`postgresql.conf`** in the data directory (or wherever `config_file` points; Debian and Ubuntu packages put it in `/etc/postgresql/17/main/`). A plain text file of `name = value` lines; it can pull in more files with `include`, `include_if_exists` and `include_dir`.
3. **`postgresql.auto.conf`**, written only by `ALTER SYSTEM`. Never edit it by hand.
4. **Command-line options** to the server, such as the `-c shared_preload_libraries=...` options in the course's `docker-compose.yml`. These win over both files.

Then, per connection, more layers apply on top (section 1.5).

In psql you can ask where everything is:

```sql
SHOW config_file;
SHOW data_directory;
SELECT name, setting, source, sourcefile, sourceline
FROM pg_settings
WHERE source NOT IN ('default', 'override')
ORDER BY name;
```

```text
               config_file                
------------------------------------------
 /var/lib/postgresql/data/postgresql.conf
(1 row)

      data_directory      
--------------------------
 /var/lib/postgresql/data
(1 row)

            name            |             setting             |       source       |                sourcefile                | sourceline 
----------------------------+---------------------------------+--------------------+------------------------------------------+------------
 application_name           | psql                            | client             |                                          |           
 DateStyle                  | ISO, MDY                        | configuration file | /var/lib/postgresql/data/postgresql.conf |        741
 default_text_search_config | pg_catalog.english              | configuration file | /var/lib/postgresql/data/postgresql.conf |        767
 dynamic_shared_memory_type | posix                           | configuration file | /var/lib/postgresql/data/postgresql.conf |        152
 lc_messages                | en_US.utf8                      | configuration file | /var/lib/postgresql/data/postgresql.conf |        757
 lc_monetary                | en_US.utf8                      | configuration file | /var/lib/postgresql/data/postgresql.conf |        759
 lc_numeric                 | en_US.utf8                      | configuration file | /var/lib/postgresql/data/postgresql.conf |        760
 lc_time                    | en_US.utf8                      | configuration file | /var/lib/postgresql/data/postgresql.conf |        761
 listen_addresses           | *                               | configuration file | /var/lib/postgresql/data/postgresql.conf |         60
 log_timezone               | Etc/UTC                         | configuration file | /var/lib/postgresql/data/postgresql.conf |        627
 max_connections            | 100                             | configuration file | /var/lib/postgresql/data/postgresql.conf |         65
 max_wal_size               | 1024                            | configuration file | /var/lib/postgresql/data/postgresql.conf |        262
 min_wal_size               | 80                              | configuration file | /var/lib/postgresql/data/postgresql.conf |        263
 pg_stat_statements.track   | all                             | command line       |                                          |           
 search_path                | bazaar, public                  | database           |                                          |           
 shared_buffers             | 16384                           | configuration file | /var/lib/postgresql/data/postgresql.conf |        129
 shared_preload_libraries   | pg_stat_statements,auto_explain | command line       |                                          |           
 TimeZone                   | Etc/UTC                         | configuration file | /var/lib/postgresql/data/postgresql.conf |        743
 track_io_timing            | on                              | command line       |                                          |           
(19 rows)
```

`source` says why each value is what it is: `configuration file`, `command line`, `environment variable`, `database`, `user`, `session`, ... The Docker image's `postgresql.conf` sets a handful of values itself (it is generated by `initdb`, which tunes a few things such as `max_connections` and `shared_buffers` to what the machine allows).

### 1.2 pg_settings and contexts

Every parameter is a row in `pg_settings`. The interesting columns:

```sql
SELECT name, setting, unit, context, vartype, min_val, max_val, pending_restart
FROM pg_settings
WHERE name IN ('shared_buffers', 'max_connections', 'work_mem', 'log_min_duration_statement',
               'autovacuum_naptime', 'checkpoint_timeout', 'statement_timeout', 'wal_level');
```

```text
            name            | setting | unit |  context   | vartype | min_val |  max_val   | pending_restart 
----------------------------+---------+------+------------+---------+---------+------------+-----------------
 autovacuum_naptime         | 60      | s    | sighup     | integer | 1       | 2147483    | f
 checkpoint_timeout         | 300     | s    | sighup     | integer | 30      | 86400      | f
 log_min_duration_statement | -1      | ms   | superuser  | integer | -1      | 2147483647 | f
 max_connections            | 100     |      | postmaster | integer | 1       | 262143     | f
 shared_buffers             | 16384   | 8kB  | postmaster | integer | 16      | 1073741823 | f
 statement_timeout          | 0       | ms   | user       | integer | 0       | 2147483647 | f
 wal_level                  | replica |      | postmaster | enum    |         |            | f
 work_mem                   | 4096    | kB   | user       | integer | 64      | 2147483647 | f
(8 rows)
```

Note the **unit**: `shared_buffers` is stored in 8 kB pages, `work_mem` in kB, `checkpoint_timeout` in seconds. `SHOW` converts to a friendly form, and you can always write values with units (`'64MB'`, `'5min'`).

The **context** says when and by whom a parameter can be changed:

| Context | Can be changed | Examples |
|---|---|---|
| `internal` | Never (compile-time or `initdb`-time) | `block_size`, `data_checksums` (until PG 18's online tooling) |
| `postmaster` | Only at server **start** (restart required) | `shared_buffers`, `max_connections`, `wal_level`, `shared_preload_libraries` |
| `sighup` | In the config files, then **reload** | `autovacuum_naptime`, `checkpoint_timeout`, `max_wal_size`, `log_line_prefix` |
| `superuser-backend`, `backend` | At connection start (in the connection string or `ALTER ROLE`), not later in the session | `log_connections` |
| `superuser` | In the files, or by a superuser (or a role granted `SET` on it) with `SET` | `log_min_duration_statement`, `log_lock_waits`, `session_replication_role` |
| `user` | By anyone with `SET`, any time | `work_mem`, `statement_timeout`, `search_path` |

### 1.3 ALTER SYSTEM

`ALTER SYSTEM` writes a setting to `postgresql.auto.conf`, so you can change server configuration from SQL without shell access:

```sql
ALTER SYSTEM SET log_min_duration_statement = '250ms';
SELECT pg_reload_conf();
SELECT pg_sleep(0.5);     -- the reload is asynchronous: give the processes a moment
SHOW log_min_duration_statement;
```

```text
ALTER SYSTEM
 pg_reload_conf 
----------------
 t
(1 row)

 pg_sleep 
----------
 
(1 row)

 log_min_duration_statement 
----------------------------
 250ms
(1 row)
```

`ALTER SYSTEM` only edits the file. Nothing changes until the configuration is **reloaded** (`SELECT pg_reload_conf()`, `pg_ctl reload`, or `SIGHUP` to the postmaster). A reload re-reads the files and applies every changed `sighup`-or-lower parameter in all processes, without disconnecting anybody. A `postmaster`-context parameter is read but not applied: `pg_settings.pending_restart` turns true until the next restart.

```sql
ALTER SYSTEM SET shared_buffers = '256MB';
SELECT pg_reload_conf();
SELECT pg_sleep(0.5);
SELECT name, setting, unit, pending_restart FROM pg_settings WHERE name = 'shared_buffers';
```

```text
ALTER SYSTEM
 pg_reload_conf 
----------------
 t
(1 row)

 pg_sleep 
----------
 
(1 row)

      name      | setting | unit | pending_restart 
----------------+---------+------+-----------------
 shared_buffers | 16384   | 8kB  | t
(1 row)
```

The file holds both changes:

```bash
docker exec course-pg cat /var/lib/postgresql/data/postgresql.auto.conf
```

```text
# Do not edit this file manually!
# It will be overwritten by the ALTER SYSTEM command.
log_min_duration_statement = '250ms'
shared_buffers = '256MB'
```

`pg_file_settings` shows what the files *say*, and flags errors, before you reload. It is the safest way to check a hand-edited `postgresql.conf`: a typo in a value makes the reload ignore that line (and log an error), while the server keeps the old value.

```sql
SELECT sourcefile, sourceline, name, setting, applied, error
FROM pg_file_settings
WHERE name IN ('shared_buffers', 'log_min_duration_statement');
```

```text
                  sourcefile                   | sourceline |            name            | setting | applied |            error             
-----------------------------------------------+------------+----------------------------+---------+---------+------------------------------
 /var/lib/postgresql/data/postgresql.conf      |        129 | shared_buffers             | 128MB   | f       | 
 /var/lib/postgresql/data/postgresql.auto.conf |          3 | log_min_duration_statement | 250ms   | t       | 
 /var/lib/postgresql/data/postgresql.auto.conf |          4 | shared_buffers             | 256MB   | f       | setting could not be applied
(3 rows)
```

Undo both changes (we restart later, in section 2, when it matters):

```sql
ALTER SYSTEM RESET shared_buffers;
ALTER SYSTEM RESET log_min_duration_statement;
SELECT pg_reload_conf();
```

> **In production:** if your configuration is managed by a tool (Ansible, Patroni in Module 20, a Kubernetes operator, a managed service's parameter groups), make changes there, not with `ALTER SYSTEM`, or the tool and `postgresql.auto.conf` will fight. PG 17 adds `allow_alter_system = off` to forbid `ALTER SYSTEM` entirely on such servers.

### 1.4 Restart vs reload

| You change | You need |
|---|---|
| A `sighup` parameter | `SELECT pg_reload_conf();` (instant, nobody disconnected) |
| A `postmaster` parameter | A **restart**: all connections are dropped, shared memory is rebuilt (the buffer cache starts cold). With Docker: `docker restart course-pg`. |
| `pg_hba.conf` | A reload (Module 15) |

A restart performs a shutdown checkpoint first. On a server with lots of dirty buffers that can take a while; running a manual `CHECKPOINT` just before the restart shortens the window during which the server refuses connections.

### 1.5 Per-database, per-role, per-session, per-transaction

Most parameters with `user` or `superuser` context can also be set at finer levels. From least to most specific:

```sql
ALTER DATABASE bazaar SET work_mem = '16MB';                            -- everyone in this database
ALTER ROLE bazaar_analyst SET work_mem = '64MB';                         -- this role, any database
ALTER ROLE bazaar_app IN DATABASE bazaar SET statement_timeout = '5s';  -- this role in this database
SET work_mem = '128MB';                                                  -- this session
BEGIN; SET LOCAL work_mem = '1GB'; ... COMMIT;                           -- this transaction only
CREATE FUNCTION ... SET work_mem = '256MB' AS ...;                       -- while this function runs
```

The more specific level wins. The role and database settings are applied at **connection start**, so existing sessions keep their old values. They are stored in `pg_db_role_setting`, and psql's `\drds` lists them:

```sql
ALTER ROLE bazaar_analyst IN DATABASE bazaar SET work_mem = '64MB';
ALTER ROLE bazaar_app IN DATABASE bazaar SET statement_timeout = '5s';
\drds
```

```text
ALTER ROLE
ALTER ROLE
                    List of settings
      Role      | Database |          Settings          
----------------+----------+----------------------------
 bazaar_analyst | bazaar   | work_mem=64MB
 bazaar_app     | bazaar   | statement_timeout=5s
                | bazaar   | search_path=bazaar, public
(3 rows)
```

This is the right tool for "analysts may sort big things, the web app may not run anything longer than 5 seconds" (section 10). Let's keep those two settings; the project milestone builds on them.

> **Pitfall:** `SET` without `LOCAL` inside a transaction persists after `COMMIT` for the rest of the session. Behind a transaction-mode connection pooler (section 11), "the rest of the session" is some other client's session. Use `SET LOCAL` or function-level `SET` for anything temporary.

---

## 2. Memory

PostgreSQL's memory falls into two categories: **shared memory**, allocated once at start and used by all processes (`shared_buffers` and friends, Module 16), and **private memory**, allocated by each backend as its queries need it (`work_mem`, `maintenance_work_mem`, caches). Sizing is about leaving enough for the operating system's page cache too.

### 2.1 shared_buffers

`shared_buffers` (postmaster context, default 128 MB) is the buffer pool. Rules of thumb for a dedicated database server:

- Start at **25% of RAM**. Going much beyond 40% rarely helps, because of double buffering: what isn't in `shared_buffers` is usually in the OS page cache, which needs room too.
- If the working set (the pages your queries touch repeatedly) fits in `shared_buffers`, you get the most out of it. `pg_buffercache` shows what's cached (Module 16).
- Very large values (dozens of GB) make the OS's job of managing page tables expensive: enable **huge pages** in the kernel (`huge_pages = try` is the default and uses them when available).

The default 128 MB is deliberately tiny so that PostgreSQL starts on any machine. On a real server it's the first thing to change.

### 2.2 work_mem and the multiplication hazard

`work_mem` (default 4 MB) is the memory **one sort or hash operation** may use before spilling to temporary files on disk. It is not per query, and not per connection:

- A query plan can contain several sort and hash nodes, and **each** may use up to `work_mem`.
- Hash nodes (hash joins, hash aggregates) may use `work_mem × hash_mem_multiplier` (default 2.0).
- A parallel query runs the plan in several processes, **each** with its own allowance.
- Every connection running such a query does all of this at the same time.

So the worst case is roughly `connections × nodes per query × work_mem × (hash multiplier) × (parallel workers + 1)`. With 200 connections, plans with 4 such nodes, and `work_mem = 64MB`, that's 200 × 4 × 64 MB = 50 GB, before counting hash multipliers. When memory runs out, Linux's out-of-memory killer kills a backend, and the postmaster then restarts the whole server (Module 16, section 1).

Let's watch a sort spill. With the default 4 MB, sorting all orders by total spills to disk:

```sql
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY OFF)
SELECT id, total FROM orders ORDER BY total DESC;
```

```text
                      QUERY PLAN                       
-------------------------------------------------------
 Sort (actual rows=500000 loops=1)
   Sort Key: total DESC
   Sort Method: external merge  Disk: 12272kB
   ->  Seq Scan on orders (actual rows=500000 loops=1)
(4 rows)
```

`Sort Method: external merge  Disk: ...kB` means the sort spilled. Raise `work_mem` for this session only and run it again:

```sql
SET work_mem = '64MB';
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY OFF)
SELECT id, total FROM orders ORDER BY total DESC;
RESET work_mem;
```

```text
SET
                      QUERY PLAN                       
-------------------------------------------------------
 Sort (actual rows=500000 loops=1)
   Sort Key: total DESC
   Sort Method: quicksort  Memory: 27916kB
   ->  Seq Scan on orders (actual rows=500000 loops=1)
(4 rows)

RESET
```

Now it's an in-memory `quicksort`. Note that the in-memory sort needs much more memory than the spilled sort wrote to disk, because tuples in memory carry overhead.

The practical approach:

- Keep the **global** `work_mem` modest (8 to 32 MB on most OLTP systems).
- Raise it **where it's needed**: per role for reporting users, or `SET LOCAL` inside a known-heavy transaction.
- Find spills with `log_temp_files = 0` (section 9) or `temp_blks_written` in `pg_stat_statements`, and `temp_files`/`temp_bytes` in `pg_stat_database` (section 8).
- `temp_file_limit` caps the temporary-file space one process can use, so one runaway query can't fill the disk.

### 2.3 maintenance_work_mem and autovacuum_work_mem

`maintenance_work_mem` (default 64 MB) is used by `CREATE INDEX`, `REINDEX`, `ALTER TABLE ADD FOREIGN KEY`, and `VACUUM` (to store dead tuple IDs, Module 16, section 10). These operations are rare and run one at a time per session, so you can afford much more than `work_mem`: 256 MB to 1 GB on a server with tens of GB of RAM is common. Building a large index is much faster when the sort fits.

Autovacuum workers use `autovacuum_work_mem` (default `-1`, meaning "use `maintenance_work_mem`"). Since up to `autovacuum_max_workers` of them run at once, you may want to set it separately. In PG 17 VACUUM's dead-tuple storage is so compact that 64 MB to 256 MB is plenty for almost any table, and the old 1 GB limit is gone.

### 2.4 effective_cache_size

`effective_cache_size` (default 4 GB) allocates nothing. It tells the planner how much memory is likely available to cache data, **PostgreSQL's buffers plus the OS page cache**, which makes index scans that revisit pages look cheaper. Set it to about 50 to 75% of RAM on a dedicated server. A value that's too low pushes the planner toward sequential scans; too high, toward index scans that then hit the disk.

### 2.5 A worked sizing example

A dedicated server with 16 GB RAM and 8 cores, serving Bazaar's web traffic through a pooler (section 11):

```text
shared_buffers        = 4GB      # 25% of RAM
effective_cache_size  = 12GB     # 75% of RAM: buffers + page cache
work_mem              = 16MB     # 100 connections x ~2 nodes x 16MB = ~3GB worst case, fine
maintenance_work_mem  = 512MB    # fast index builds, VACUUM
autovacuum_work_mem   = 256MB    # x 3 workers
max_connections       = 100      # the pooler multiplexes thousands of clients onto these
huge_pages            = try
```

That leaves several GB for the OS, its page cache, and the backends' private caches. Tools like PGTune generate starting points like this. Treat them as starting points and validate with the monitoring in sections 7 and 8.

---

## 3. Connections

`max_connections` (postmaster context, default 100) is the maximum number of concurrent client connections. Three of them (`superuser_reserved_connections`) are kept for superusers so an administrator can always get in, and PG 16 added `reserved_connections` for roles that are members of `pg_use_reserved_connections`, for monitoring agents.

Why not just set it to 5000?

- **Memory.** Each backend is a process with its own private memory: catalog caches, plan caches, and `work_mem` allocations while it works. A few MB idle, much more busy.
- **Snapshots and locks scale with connection slots.** Taking a snapshot scans the array of all backends (Module 16, section 6), and several shared structures are sized by `max_connections`.
- **CPU contention.** A server with 8 cores runs 8 things at a time. With 500 *active* queries, the extra 492 just take turns, each holding locks and memory while it waits. Throughput goes down, latency goes up.
- **Connection setup.** Each new connection forks a process, authenticates (SCRAM is deliberately slow), and warms its caches. Applications that connect per request spend a large part of their time just connecting.

The usual guidance: the number of connections that are **actively running queries** should stay around 2 to 4 times the number of CPU cores. Applications should use a connection pool (psycopg's pool in Module 22, SQLAlchemy's in Module 24), and when many application processes each have a pool, a central pooler like PgBouncer (section 11) multiplexes them onto a small number of server connections.

```sql
SELECT count(*) AS total,
       count(*) FILTER (WHERE state = 'active') AS active,
       count(*) FILTER (WHERE state = 'idle') AS idle,
       count(*) FILTER (WHERE state LIKE 'idle in transaction%') AS idle_in_tx,
       current_setting('max_connections') AS max_connections
FROM pg_stat_activity
WHERE backend_type = 'client backend';
```

```text
 total | active | idle | idle_in_tx | max_connections 
-------+--------+------+------------+-----------------
     1 |      1 |    0 |          0 | 100
(1 row)
```

---

## 4. Checkpoints and WAL

Module 16 (section 9) explained checkpoints: they flush all dirty buffers so that crash recovery can start from a recent point, and every page modified for the first time after a checkpoint is logged in full in the WAL. Tuning is a trade-off between three things:

- **Crash recovery time**: proportional to the WAL written since the last checkpoint. Rare checkpoints mean long recovery.
- **WAL volume**: frequent checkpoints mean more full-page images, so more WAL to write, archive (Module 18) and replicate (Module 19).
- **I/O smoothness**: a checkpoint writes every dirty page; done in a hurry, it competes with queries for I/O.

### 4.1 The settings

| Setting | Default | Meaning |
|---|---|---|
| `checkpoint_timeout` | 5min | Start a checkpoint at least this often. |
| `max_wal_size` | 1GB | Start a checkpoint early if this much WAL accumulated since the last one. A soft limit, not a disk quota. |
| `min_wal_size` | 80MB | Keep at least this much old WAL for recycling instead of deleting it. |
| `checkpoint_completion_target` | 0.9 | Spread the checkpoint's writes over this fraction of the interval. |
| `checkpoint_warning` | 30s | Log a warning if WAL volume forces checkpoints closer together than this. |
| `log_checkpoints` | on | Log every checkpoint with its statistics (on by default since PG 15). |

The goal is that nearly all checkpoints are **timed** (started by `checkpoint_timeout`), not **requested** (forced by `max_wal_size`). A forced checkpoint means WAL arrived faster than planned, so the checkpoint is squeezed into a shorter interval, with more full-page writes and spiky I/O. Typical production settings: `checkpoint_timeout` 15 to 30 minutes, and `max_wal_size` large enough to cover that interval at peak write rate (often 8 to 64 GB).

### 4.2 Measuring

PostgreSQL 17 moved the checkpoint counters into their own view, `pg_stat_checkpointer`:

```sql
SELECT num_timed, num_requested, restartpoints_timed, write_time, sync_time, buffers_written,
       stats_reset
FROM pg_stat_checkpointer;
```

```text
 num_timed | num_requested | restartpoints_timed | write_time | sync_time | buffers_written |          stats_reset          
-----------+---------------+---------------------+------------+-----------+-----------------+-------------------------------
         8 |            24 |                   0 |     462335 |      6094 |           77865 | 2026-10-14 07:15:50.022034+00
(1 row)
```

Let's see a requested checkpoint happen. We shrink `max_wal_size` to its minimum and write some WAL by updating every row of `users` (100,000 rows, no triggers):

```sql
ALTER SYSTEM SET max_wal_size = '32MB';
SELECT pg_reload_conf();
SELECT pg_current_wal_lsn() AS before_lsn \gset
UPDATE users SET is_active = is_active;
SELECT pg_size_pretty(pg_current_wal_lsn() - :'before_lsn') AS wal_written;
SELECT pg_sleep(3);
SELECT num_timed, num_requested FROM pg_stat_checkpointer;
```

```text
ALTER SYSTEM
 pg_reload_conf 
----------------
 t
(1 row)

UPDATE 100000
 wal_written 
-------------
 66 MB
(1 row)

 pg_sleep 
----------
 
(1 row)

 num_timed | num_requested 
-----------+---------------
         8 |            26
(1 row)
```

```bash
docker logs --since 1m course-pg 2>&1 | grep -E 'checkpoint (starting|complete)|checkpoints are occurring' | tail -n 5
```

```text
2026-10-14 08:17:57.888 UTC [60] LOG:  checkpoint starting: wal
2026-10-14 08:17:58.282 UTC [60] LOG:  checkpoint complete: wrote 6168 buffers (37.6%); 0 WAL file(s) added, 6 removed, 0 recycled; write=0.302 s, sync=0.025 s, total=0.395 s; sync files=104, longest=0.005 s, average=0.001 s; distance=94893 kB, estimate=497367 kB; lsn=2/3314AD38, redo lsn=2/320AC190
2026-10-14 08:17:58.283 UTC [60] LOG:  checkpoints are occurring too frequently (1 second apart)
2026-10-14 08:17:58.283 UTC [60] LOG:  checkpoint starting: wal
2026-10-14 08:17:58.564 UTC [60] LOG:  checkpoint complete: wrote 1835 buffers (11.2%); 0 WAL file(s) added, 1 removed, 0 recycled; write=0.261 s, sync=0.009 s, total=0.282 s; sync files=5, longest=0.003 s, average=0.002 s; distance=19448 kB, estimate=449575 kB; lsn=2/34300870, redo lsn=2/333AA250
```

The log lines show why each checkpoint started (`wal` means forced by WAL volume, `time` means timed), how many buffers it wrote, how long the write and sync phases took, and the `distance`: how much WAL was written since the previous checkpoint. If WAL-triggered checkpoints come closer together than `checkpoint_warning` (30 s), the log adds a hint: "checkpoints are occurring too frequently ... Consider increasing the configuration parameter max_wal_size". Restore the default:

```sql
ALTER SYSTEM RESET max_wal_size;
SELECT pg_reload_conf();
```

### 4.3 Other WAL settings worth knowing

- **`wal_compression`** (off): compress full-page images (`lz4` or `zstd` are good choices). Often cuts WAL volume by a third or more for a little CPU.
- **`wal_buffers`** (auto: 1/32 of `shared_buffers`, capped at 16 MB): rarely needs touching.
- **`synchronous_commit`** (on): `off` makes commits return before their WAL is flushed. A crash can then lose the last few hundred milliseconds of *committed* transactions (but never corrupts data). It can be set per transaction, so you can use it for low-value writes (analytics events, `SET LOCAL synchronous_commit = off`) and keep it on for orders and payments.
- **`fsync`** and **`full_page_writes`**: never turn these off on data you care about. With `fsync = off`, a crash can corrupt the cluster beyond repair.

> **In production:** WAL volume drives replication bandwidth, archive storage, and backup restore time (Modules 18 and 19). `pg_stat_wal` reports `wal_records`, `wal_fpi` and `wal_bytes` since the last reset. A high ratio of `wal_fpi` to `wal_records` usually means checkpoints are too frequent.

---

## 5. Autovacuum

Module 16 explained what VACUUM does. **Autovacuum** decides *when*, for every table, so that dead tuples are cleaned up, statistics stay fresh, and XIDs get frozen, without anyone scheduling anything. Its defaults are tuned for small databases on small machines. On a busy production database they are almost always too timid.

### 5.1 How autovacuum decides

The **autovacuum launcher** wakes up every `autovacuum_naptime` (1 minute) for each database, reads the cumulative statistics, and starts a **worker** for the tables that need work (at most `autovacuum_max_workers`, default 3, at once across the server). A table is vacuumed when **any** of these is true:

```text
dead tuples          n_dead_tup         > autovacuum_vacuum_threshold        (50)
                                          + autovacuum_vacuum_scale_factor   (0.2)  x reltuples

inserts since vacuum n_ins_since_vacuum > autovacuum_vacuum_insert_threshold (1000)
                                          + autovacuum_vacuum_insert_scale_factor (0.2) x reltuples

wraparound           age(relfrozenxid)  > autovacuum_freeze_max_age          (200 million)
```

and analyzed when:

```text
changes since analyze  n_mod_since_analyze > autovacuum_analyze_threshold    (50)
                                              + autovacuum_analyze_scale_factor (0.1) x reltuples
```

The insert-based trigger (PG 13+) exists for append-only tables: they never get dead tuples, but they still need VACUUM to set visibility-map bits (for index-only scans) and to freeze.

**The scale factor is the problem on large tables.** With the default 0.2, a 500,000-row `orders` table is vacuumed after 100,050 dead tuples, which is fine. A 100-million-row table waits for 20 million dead tuples, and then one VACUUM has to process all of them at once. Meanwhile a tiny, extremely hot table like a job queue may be vacuumed constantly, but it can still fill up with dead tuples between runs if each run is slow.

Let's compute where Bazaar's tables stand. The view `pg_stat_user_tables` has the counters; the thresholds come from the settings (we'll handle per-table overrides in the project milestone):

```sql
SELECT s.relname,
       s.n_live_tup, s.n_dead_tup,
       round(current_setting('autovacuum_vacuum_threshold')::int
             + current_setting('autovacuum_vacuum_scale_factor')::float8 * c.reltuples) AS vacuum_at,
       s.n_mod_since_analyze,
       round(current_setting('autovacuum_analyze_threshold')::int
             + current_setting('autovacuum_analyze_scale_factor')::float8 * c.reltuples) AS analyze_at,
       s.last_autovacuum, s.last_autoanalyze
FROM pg_stat_user_tables s
JOIN pg_class c ON c.oid = s.relid
WHERE s.schemaname = 'bazaar'
ORDER BY s.n_dead_tup DESC
LIMIT 6;
```

```text
     relname     | n_live_tup | n_dead_tup | vacuum_at | n_mod_since_analyze | analyze_at | last_autovacuum | last_autoanalyze 
-----------------+------------+------------+-----------+---------------------+------------+-----------------+------------------
 users           |     100000 |     100000 |     20050 |              100000 |      10050 |                 | 
 events_y2025m01 |     326131 |          0 |     65276 |                   0 |      32663 |                 | 
 events_y2025m02 |     307750 |          0 |     61600 |                   0 |      30825 |                 | 
 events_y2025m05 |     380180 |          0 |     76086 |                   0 |      38068 |                 | 
 events_y2025m10 |     441764 |          0 |     88403 |                   0 |      44226 |                 | 
 events_y2025m09 |     419575 |          0 |     83965 |                   0 |      42008 |                 | 
(6 rows)
```

On a freshly reset database most tables have no dead tuples (the seed scripts ran `VACUUM ANALYZE` themselves, which is also why `last_autovacuum` is empty). The exception is `users`, which we updated completely in section 4: 100,000 dead tuples against a threshold of 20,050. Autovacuum will notice at its next visit (within a minute) and vacuum it; run the query again a little later and `last_autovacuum` will be filled in. Note how the thresholds scale with the table: an `events` partition with 500,000 rows needs over 100,000 dead tuples before autovacuum touches it.

### 5.2 Throttling: the cost-based delay

VACUUM can generate a lot of I/O, so autovacuum is **throttled**. Every page it processes costs points: `vacuum_cost_page_hit` = 1 (found in `shared_buffers`), `vacuum_cost_page_miss` = 2 (read from the OS), `vacuum_cost_page_dirty` = 20 (it modified the page, which must be written later). When a worker has accumulated `autovacuum_vacuum_cost_limit` points (default `-1`, meaning "use `vacuum_cost_limit`" = 200), it sleeps for `autovacuum_vacuum_cost_delay` (2 ms). The limit is **shared** between all running workers.

That means autovacuum can dirty at most 200 / 20 = 10 pages per 2 ms, which is 5,000 pages or **about 40 MB per second, total, for all workers together**. On modern SSDs that's very conservative. The typical production change is to raise `autovacuum_vacuum_cost_limit` to 1000 to 2000 (and, with many large tables, `autovacuum_max_workers`, which needs a restart; remember that the cost limit is divided among the workers).

Manual `VACUUM` is **not** throttled by default (`vacuum_cost_delay = 0`), which is why a manual VACUUM is often much faster than the autovacuum of the same table.

### 5.3 Per-table settings

Every autovacuum parameter can be overridden per table as a **storage parameter**. This is the right tool for the outliers: a huge table that needs a smaller scale factor, a hot queue that needs vacuuming after a fixed number of changes, an append-only log:

```sql
-- Example: orders, a large table that keeps growing.
ALTER TABLE orders SET (
    autovacuum_vacuum_scale_factor = 0.02,     -- 2% instead of 20%
    autovacuum_analyze_scale_factor = 0.01
);
SELECT relname, reloptions FROM pg_class WHERE oid = 'orders'::regclass;
```

```text
ALTER TABLE
 relname |                                 reloptions                                 
---------+----------------------------------------------------------------------------
 orders  | {autovacuum_vacuum_scale_factor=0.02,autovacuum_analyze_scale_factor=0.01}
(1 row)
```

`ALTER TABLE ... RESET (autovacuum_vacuum_scale_factor)` removes an override. The project milestone applies a tuned set to `jobs` and `inventory`. (`ALTER TABLE ... SET (...)` takes only a `SHARE UPDATE EXCLUSIVE` lock, so it is safe on a busy table.)

### 5.4 Watching autovacuum work

Running vacuums show up in `pg_stat_activity` (as `autovacuum: VACUUM bazaar.orders`) and, in detail, in **`pg_stat_progress_vacuum`**. To have something to watch, we give `payments` about 270,000 dead tuples (the table has no triggers, so this changes nothing else):

```sql
UPDATE payments SET provider_ref = provider_ref WHERE id % 2 = 0;
```

and run a deliberately slow manual VACUUM in a second session (do it right away, before autovacuum gets there first). Setting `vacuum_cost_delay` makes a manual VACUUM throttle itself the way autovacuum does:

```sql
-- Session B
SET vacuum_cost_delay = '10ms';   -- slower than autovacuum's 2 ms, so there is time to watch
VACUUM payments;
```

While it runs, look at its progress from session A:

```sql
-- Session A
SELECT p.pid, p.relid::regclass AS table_name, p.phase,
       p.heap_blks_total, p.heap_blks_scanned, p.heap_blks_vacuumed,
       p.index_vacuum_count, p.num_dead_item_ids, p.indexes_total, p.indexes_processed,
       round(100.0 * p.heap_blks_scanned / nullif(p.heap_blks_total, 0), 1) AS pct_scanned
FROM pg_stat_progress_vacuum p \gx
```


```text
-[ RECORD 1 ]------+--------------
pid                | 1128
table_name         | payments
phase              | scanning heap
heap_blks_total    | 9151
heap_blks_scanned  | 1050
heap_blks_vacuumed | 0
index_vacuum_count | 0
num_dead_item_ids  | 46200
indexes_total      | 0
indexes_processed  | 0
pct_scanned        | 11.5
```

The phases are `initializing`, `scanning heap`, `vacuuming indexes`, `vacuuming heap`, `cleaning up indexes`, `truncating heap` and `performing final cleanup`, as described in Module 16. A VACUUM stuck in `vacuuming indexes` for a long time on a table with many indexes is normal; one with `index_vacuum_count` > 1 ran out of `maintenance_work_mem` for its dead-tuple list.

After the fact, `pg_stat_user_tables` records `last_vacuum`/`last_autovacuum`, `vacuum_count`/`autovacuum_count`, and the same for analyze. Setting **`log_autovacuum_min_duration`** (default 10 min since PG 15) to something like `1s` logs a full report (like `VACUUM VERBOSE`) for every slower autovacuum run, which is the best way to see what autovacuum is doing over time.

### 5.5 When autovacuum can't keep up

If `n_dead_tup` keeps growing although autovacuum runs, check in this order:

1. **Is something holding back the xmin horizon?** A long transaction, an idle-in-transaction session, an abandoned replication slot, or a prepared transaction (Module 16, section 10.5). VACUUM runs but can't remove anything (`dead but not yet removable` in its log). Section 7 shows how to find the culprit.
2. **Is it cancelled?** Autovacuum gives up when another session requests a conflicting lock (DDL, `LOCK TABLE`). A table that is altered every few minutes may never finish being vacuumed. The log says `canceling autovacuum task`.
3. **Is it too slow?** Raise the cost limit (globally or per table), or set `autovacuum_vacuum_cost_delay = 0` for the table.
4. **Does it start too late?** Lower the scale factor or use a fixed threshold for that table.
5. **Are all workers busy?** If all `autovacuum_max_workers` are constantly busy on big tables, small hot tables wait. More workers (with a higher total cost limit) help.

> **Pitfall:** never disable autovacuum "because it uses I/O". The I/O is the cost of your workload's updates; skipping it means bloat, bad statistics, and eventually an anti-wraparound vacuum that runs anyway, at the worst moment. Tune it instead.

---

## 6. Bloat

**Bloat** is space in tables and indexes that holds dead tuples or free space that is not being reused fast enough. Some free space is healthy: it is where updates and inserts go. Bloat becomes a problem when a table is several times larger than its live data, so every sequential scan reads several times more pages, the cache holds fewer useful rows, and backups take longer.

### 6.1 Making some

Updating every row of `inventory` once creates a new version of each row. (The `set_updated_at` trigger from Module 09 also fires, but that doesn't matter here.)

```sql
CREATE EXTENSION IF NOT EXISTS pgstattuple;

SELECT pg_size_pretty(pg_table_size('inventory')) AS before;
UPDATE inventory SET quantity = quantity;
UPDATE inventory SET quantity = quantity;
SELECT pg_size_pretty(pg_table_size('inventory')) AS after;
```

```text
NOTICE:  extension "pgstattuple" already exists, skipping
CREATE EXTENSION
  before  
----------
 10224 kB
(1 row)

UPDATE 200000
UPDATE 200000
 after 
-------
 30 MB
(1 row)
```

### 6.2 Measuring exactly: pgstattuple

`pgstattuple` reads every page and reports exact numbers:

```sql
SELECT pg_size_pretty(table_len) AS size, tuple_count, round(tuple_percent) AS live_pct,
       dead_tuple_count, round(dead_tuple_percent) AS dead_pct, round(free_percent) AS free_pct
FROM pgstattuple('inventory');
```

```text
 size  | tuple_count | live_pct | dead_tuple_count | dead_pct | free_pct 
-------+-------------+----------+------------------+----------+----------
 30 MB |      200000 |       31 |           200000 |       31 |       31
(1 row)
```

A third live, a third dead, a third free space. The first round of updates wrote 200,000 new versions into new pages (the existing pages were full), doubling the table. Before the second round, page pruning and possibly autovacuum had already cleaned up the first round's dead versions, which is the free third. The second round's new versions couldn't all fit into that space within one transaction, so the table grew again, and the second round's old versions are the dead third. Only a third of the 30 MB is actual data.

Reading every page of a 500 GB table is itself an expensive operation. `pgstattuple_approx` uses the visibility map to skip all-visible pages (counting them as fully live) and is much cheaper:

```sql
SELECT pg_size_pretty(table_len) AS size, scanned_percent, approx_tuple_count,
       round(approx_tuple_percent) AS live_pct, dead_tuple_count, round(approx_free_percent) AS free_pct
FROM pgstattuple_approx('inventory');
```

```text
 size  | scanned_percent | approx_tuple_count | live_pct | dead_tuple_count | free_pct 
-------+-----------------+--------------------+----------+------------------+----------
 30 MB |             100 |             200000 |       31 |           200000 |       31
(1 row)
```

For **indexes**, `pgstatindex` reports B-tree leaf density. A freshly built B-tree has about 90% density (its default fillfactor); an index full of deleted entries or half-empty pages after random inserts and deletes has much less:

```sql
SELECT indexrelid::regclass AS index_name,
       pg_size_pretty(pg_relation_size(indexrelid)) AS size,
       (pgstatindex(indexrelid::regclass)).avg_leaf_density,
       (pgstatindex(indexrelid::regclass)).leaf_fragmentation
FROM pg_index
WHERE indrelid = 'inventory'::regclass;
```

```text
   index_name   |  size   | avg_leaf_density | leaf_fragmentation 
----------------+---------+------------------+--------------------
 inventory_pkey | 9904 kB |            82.83 |              44.35
(1 row)
```

### 6.3 Estimating cheaply

Monitoring can't run `pgstattuple` on every table every minute. The standard trick is to **estimate** the size a table *should* have from the planner statistics: average row width from `pg_stats` plus the 24-byte tuple header and 4-byte line pointer, times `reltuples`, divided by the usable bytes per page (respecting fillfactor), and compare with the actual number of pages:

```sql
ANALYZE inventory;    -- refresh relpages/reltuples: the estimate is only as fresh as the statistics

WITH t AS (
    SELECT c.oid, c.relname, c.relpages, c.reltuples,
           coalesce((SELECT (regexp_match(opt, 'fillfactor=(\d+)'))[1]::int
                     FROM unnest(c.reloptions) AS opt WHERE opt LIKE 'fillfactor=%'), 100) AS fillfactor,
           (SELECT sum(s.avg_width) FROM pg_stats s
            WHERE s.schemaname = n.nspname AND s.tablename = c.relname) AS row_width
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'bazaar' AND c.relkind = 'r'
)
SELECT relname, relpages AS actual_pages,
       ceil(reltuples * (24 + 4 + row_width) / (8168 * fillfactor / 100.0))::bigint AS expected_pages,
       greatest(round(100 * (1 - ceil(reltuples * (24 + 4 + row_width) / (8168 * fillfactor / 100.0))
                                 / nullif(relpages, 0)))::int, 0) AS est_bloat_pct
FROM t
WHERE relpages > 100
ORDER BY relpages - ceil(reltuples * (24 + 4 + row_width) / (8168 * fillfactor / 100.0)) DESC
LIMIT 6;
```

```text
ANALYZE
     relname     | actual_pages | expected_pages | est_bloat_pct 
-----------------+--------------+----------------+---------------
 payments        |         9151 |           5653 |            38
 inventory       |         3822 |           1274 |            67
 products        |        17728 |          16822 |             5
 order_items     |         9316 |           8531 |             8
 orders          |         7095 |           6795 |             4
 events_y2025m12 |         9259 |           9121 |             1
(6 rows)
```

The estimate ignores alignment padding and the null bitmap, so it underestimates the expected size somewhat, and tables with a few percent "bloat" are normal. It is good at finding the tables with a lot of it, and it is precise enough to alert on. (There are more elaborate community versions of this query that account for alignment; the ops toolkit in the project uses this simple one.) Note that the estimate depends on fresh statistics: `reltuples` and `relpages` come from the last VACUUM or ANALYZE.

### 6.4 Fixing it

From least to most disruptive:

1. **Let VACUUM and time do it.** Plain `VACUUM` makes the dead space reusable. If the bloat came from a one-off event (a mass update, a backfill), the table will refill its free space over time and stop growing. Often this is the right answer: do nothing more.
2. **`REINDEX INDEX CONCURRENTLY`** rebuilds a bloated index without blocking reads or writes (Module 11). It builds a new index next to the old one, then swaps them. Needs disk space for the new index.
3. **`pg_repack`** (an extension plus a command-line tool, not part of core PostgreSQL) rewrites a table online: it creates a shadow copy of the table, installs a trigger that records concurrent changes into a log table, copies the data, replays the log, builds the indexes, and finally swaps the files in a short transaction that holds an `ACCESS EXCLUSIVE` lock for a moment. It needs twice the space of the table and primary key (or unique not-null index), and it competes with your workload for I/O. `pg_squeeze` does the same using logical decoding.
4. **`VACUUM FULL`** (or `CLUSTER`) rewrites the table and its indexes while holding an **`ACCESS EXCLUSIVE`** lock: nothing can read or write the table until it finishes. Fine for small tables or maintenance windows.

```sql
VACUUM inventory;
SELECT pg_size_pretty(pg_table_size('inventory')) AS after_vacuum;
REINDEX TABLE CONCURRENTLY inventory;
VACUUM FULL inventory;
SELECT pg_size_pretty(pg_table_size('inventory')) AS after_vacuum_full,
       pg_size_pretty(pg_indexes_size('inventory')) AS indexes;
```

```text
VACUUM
 after_vacuum 
--------------
 30 MB
(1 row)

REINDEX
VACUUM
 after_vacuum_full | indexes 
-------------------+---------
 10192 kB          | 4408 kB
(1 row)
```

> **In production:** **prevention beats cure.** Most bloat comes from (a) long transactions holding the xmin horizon while a hot table churns, (b) autovacuum starting too late on a large table, and (c) mass `UPDATE`s or `DELETE`s done in one statement. Batch big data changes (update 10,000 rows per transaction, let autovacuum keep up), monitor the xmin horizon, and tune autovacuum per table.

---

## 7. Monitoring what is happening now

### 7.1 pg_stat_activity

`pg_stat_activity` has one row per server process (Module 16) and is the first place to look when "the database is slow". The most useful columns:

| Column | Meaning |
|---|---|
| `pid` | Process ID: what you pass to `pg_cancel_backend` and `pg_terminate_backend`. |
| `usename`, `datname`, `application_name`, `client_addr` | Who and from where. Have your applications set `application_name` (psycopg: `application_name=checkout` in the connection string), and you can tell services apart. |
| `backend_start`, `xact_start`, `query_start`, `state_change` | When the connection, the current transaction, the current (or last) statement, and the current state started. |
| `state` | `active` (running a statement), `idle` (waiting for the client), `idle in transaction` (inside a transaction, waiting for the client), `idle in transaction (aborted)`, `fastpath function call`. |
| `wait_event_type`, `wait_event` | What an active process is waiting for, if anything (section 7.2). |
| `backend_xid`, `backend_xmin` | The process's XID (if it has written) and the xmin of its snapshot: the column that tells you who holds back VACUUM. |
| `query` | The current statement if `active`, otherwise the **last** one. Truncated to `track_activity_query_size` (1 kB). |
| `query_id` | Matches `pg_stat_statements.queryid` (with `compute_query_id`, on automatically when `pg_stat_statements` is loaded). |

A good default "what's going on" query lists non-idle client sessions, oldest transaction first:

```sql
SELECT pid, usename, application_name, state,
       now() - xact_start  AS xact_age,
       now() - query_start AS query_age,
       wait_event_type, wait_event,
       left(query, 60) AS query
FROM pg_stat_activity
WHERE backend_type = 'client backend'
  AND state <> 'idle'
  AND pid <> pg_backend_pid()
ORDER BY xact_start NULLS LAST;
```

The rows to worry about are old `xact_age` values, especially in state `idle in transaction`: a session that began a transaction and then went quiet (an application that forgot to commit, a developer's psql left open over lunch). It holds its locks and its snapshot for as long as it stays like that, which blocks DDL and holds back VACUUM for the entire cluster (Module 16, section 10.5).

### 7.2 Wait events

When an active backend is not using CPU, `wait_event_type` and `wait_event` say what it is waiting for. The types you'll meet most:

| `wait_event_type` | Means | Typical events |
|---|---|---|
| `Lock` | Waiting for a heavyweight lock held by another transaction (row, table, transaction ID). | `transactionid` (a row lock: waiting for another transaction to finish), `relation` (a table lock), `tuple`, `advisory` |
| `LWLock` | Waiting for a lightweight lock on a shared-memory structure. | `BufferMapping`, `WALWrite`, `LockManager`, `XactSLRU` |
| `IO` | Waiting for a read or write to complete. | `DataFileRead`, `WALSync`, `WALWrite` |
| `Client` | Waiting for the client: idle, or the client is slow reading results. | `ClientRead`, `ClientWrite` |
| `IPC` | Waiting for another process. | `ParallelFinish`, `SyncRep` (waiting for a synchronous replica, Module 19) |
| `Timeout` | Sleeping on purpose. | `PgSleep`, `VacuumDelay` |
| `Activity` | Auxiliary processes idling in their main loop. | `CheckpointerMain`, `WalWriterMain` |

`wait_event` NULL with `state = 'active'` means the process is running on CPU. A quick histogram, sampled a few times, gives you the shape of the load:

```sql
SELECT coalesce(wait_event_type, 'CPU') AS wait_type, coalesce(wait_event, '-') AS wait_event, count(*)
FROM pg_stat_activity
WHERE state = 'active' AND pid <> pg_backend_pid()
GROUP BY 1, 2 ORDER BY 3 DESC;
```

Lots of `Lock` waits point to contention in the application's logic. `IO: DataFileRead` dominating means the working set doesn't fit in memory. `LWLock: WALWrite` or `IO: WALSync` point to commit-heavy load on slow storage. Monitoring tools sample this view every second or so to build "active session" charts.

### 7.3 Locks and blocking chains

Here is the scenario from the introduction, in miniature. Three sessions:

1. Session 1 updates a row of `inventory` in a transaction and then goes idle without committing (the forgotten transaction).
2. Session 2 runs a migration: `ALTER TABLE inventory ALTER COLUMN reserved SET DEFAULT 0`. `ALTER TABLE` needs an `ACCESS EXCLUSIVE` lock, which conflicts with session 1's `ROW EXCLUSIVE` lock, so it waits.
3. Session 3 is the web application running `SELECT ... FROM inventory`. Its `ACCESS SHARE` lock does *not* conflict with session 1, but it **does** conflict with session 2's pending `ACCESS EXCLUSIVE` request, and lock requests are granted in order. So session 3 waits behind session 2, and so does every other query on `inventory`.

One idle transaction plus one innocent-looking migration has taken the table offline. Try it with three psql windows:

```sql
-- Session 1
BEGIN;
UPDATE inventory SET reserved = reserved WHERE product_id = 1;
-- (now do nothing)
```

```sql
-- Session 2
ALTER TABLE inventory ALTER COLUMN reserved SET DEFAULT 0;     -- hangs
```

```sql
-- Session 3
SELECT quantity FROM inventory WHERE product_id = 42;           -- hangs too
```

`pg_blocking_pids(pid)` returns the PIDs that a process is waiting for. It understands lock queues, so it reports that session 3 is blocked by session 2 (queue order) *and* session 1. A recursive query turns that into a readable tree, starting from the sessions that block others but aren't blocked themselves (the root causes):

```sql
WITH RECURSIVE blocked AS (
    SELECT pid, pg_blocking_pids(pid) AS blockers
    FROM pg_stat_activity
    WHERE cardinality(pg_blocking_pids(pid)) > 0
),
tree AS (
    -- roots: blocking someone, not blocked themselves
    SELECT a.pid, NULL::int AS blocked_by, 0 AS depth, ARRAY[a.pid] AS path
    FROM pg_stat_activity a
    WHERE a.pid IN (SELECT unnest(blockers) FROM blocked)
      AND a.pid NOT IN (SELECT pid FROM blocked)
    UNION ALL
    SELECT b.pid, t.pid, t.depth + 1, t.path || b.pid
    FROM tree t
    JOIN blocked b ON t.pid = ANY (b.blockers)
    WHERE b.pid <> ALL (t.path)
)
SELECT repeat('    ', t.depth) || t.pid AS pid_tree,
       a.state,
       a.wait_event_type || ':' || a.wait_event AS waiting_on,
       now() - a.xact_start AS xact_age,
       left(a.query, 60) AS query
FROM tree t
JOIN pg_stat_activity a ON a.pid = t.pid
ORDER BY t.path;
```


```text
   pid_tree   |        state        |    waiting_on     |    xact_age     |                            query                             
--------------+---------------------+-------------------+-----------------+--------------------------------------------------------------
 1152         | idle in transaction | Client:ClientRead | 00:00:02.995873 | UPDATE inventory SET reserved = reserved WHERE product_id = 
     1159     | active              | Lock:relation     | 00:00:01.993332 | ALTER TABLE inventory ALTER COLUMN reserved SET DEFAULT 0
         1166 | active              | Lock:relation     | 00:00:00.995821 | SELECT quantity FROM inventory WHERE product_id = 42
(3 rows)

----
 pid  |   locktype    | relation  |        mode         | granted 
------+---------------+-----------+---------------------+---------
 1152 | relation      | inventory | RowExclusiveLock    | t
 1152 | transactionid |           | ExclusiveLock       | t
 1159 | transactionid |           | ExclusiveLock       | t
 1159 | relation      | inventory | AccessExclusiveLock | f
 1166 | relation      | inventory | AccessShareLock     | f
(5 rows)
```

The tree reads top-down: the idle-in-transaction session at the root, the `ALTER TABLE` waiting for it, and the `SELECT` waiting behind the `ALTER TABLE`. The raw locks behind it come from **`pg_locks`**, one row per lock held or awaited:

```sql
SELECT l.pid, l.locktype, l.relation::regclass AS relation, l.mode, l.granted
FROM pg_locks l
JOIN pg_stat_activity a ON a.pid = l.pid
WHERE a.backend_type = 'client backend' AND l.pid <> pg_backend_pid()
  AND (l.relation = 'inventory'::regclass OR l.locktype = 'transactionid')
ORDER BY l.pid, l.granted DESC;
```

(Its output is the part after `----` above.) `granted = false` marks the waiting requests. Every transaction also holds an `ExclusiveLock` on its own `transactionid`; a session waiting for a *row* lock waits for a `ShareLock` on the row owner's `transactionid`, which is how row locks show up in `pg_locks` without a row per locked row.

The fix here is to end session 1 (section 10). The prevention is to run migrations with `SET lock_timeout = '3s'`, so that the `ALTER TABLE` gives up quickly instead of queueing everyone behind it (Module 26 builds this into the migration workflow), and to set `idle_in_transaction_session_timeout` so forgotten transactions end themselves.

> **Under the hood:** `pg_blocking_pids()` takes a short lock on the whole lock manager, so don't call it for every backend every second on a server with thousands of connections. Filtering on `wait_event_type = 'Lock'` first keeps it cheap.

### 7.4 Long transactions and the xmin horizon

For VACUUM, what matters is the oldest snapshot anywhere. These three queries find whatever holds the horizon back:

```sql
-- sessions, by snapshot age
SELECT pid, usename, state, age(backend_xmin) AS xmin_age, now() - xact_start AS xact_age,
       left(query, 50) AS query
FROM pg_stat_activity
WHERE backend_xmin IS NOT NULL
ORDER BY age(backend_xmin) DESC
LIMIT 5;

-- replication slots (Module 19)
SELECT slot_name, active, age(xmin) AS xmin_age, age(catalog_xmin) AS catalog_xmin_age
FROM pg_replication_slots;

-- forgotten two-phase transactions
SELECT gid, prepared, owner, age(transaction) AS xid_age FROM pg_prepared_xacts;
```

```text
 pid  | usename  | state  | xmin_age | xact_age |                       query                        
------+----------+--------+----------+----------+----------------------------------------------------
 1187 | postgres | active |        0 | 00:00:00 | SELECT pid, usename, state, age(backend_xmin) AS x
(1 row)

 slot_name | active | xmin_age | catalog_xmin_age 
-----------+--------+----------+------------------
(0 rows)

 gid | prepared | owner | xid_age 
-----+----------+-------+---------
(0 rows)
```

On an idle system the first query shows only your own session. The project's ops toolkit combines these into one view.

---

## 8. Monitoring over time: the statistics views

The cumulative statistics (Module 16, section 12) are counters since the last reset. To make them meaningful, take a reading, wait, take another, and look at the differences. That's what monitoring systems do every 15 to 60 seconds. For investigations, the totals since the last reset are often enough.

### 8.1 Tables: pg_stat_user_tables

```sql
SELECT relname, seq_scan, seq_tup_read, idx_scan,
       n_tup_ins, n_tup_upd, n_tup_hot_upd, n_tup_del, n_live_tup, n_dead_tup
FROM pg_stat_user_tables
WHERE schemaname = 'bazaar'
ORDER BY seq_tup_read DESC
LIMIT 5;
```

```text
   relname   | seq_scan | seq_tup_read | idx_scan | n_tup_ins | n_tup_upd | n_tup_hot_upd | n_tup_del | n_live_tup | n_dead_tup 
-------------+----------+--------------+----------+-----------+-----------+---------------+-----------+------------+------------
 order_items |       12 |      6334875 |        0 |   1267163 |         0 |             0 |         0 |    1266928 |          0
 orders      |       22 |      4500600 |   500376 |    500120 |       239 |             0 |         0 |     500000 |          0
 products    |      108 |      3404310 |      460 |    200062 |        62 |             0 |         0 |     200000 |          0
 inventory   |       11 |      1600000 |        2 |    200062 |    400001 |            18 |         0 |     200000 |          1
 payments    |        8 |      1073912 |        0 |    536956 |    268408 |             0 |         0 |     536816 |          0
(5 rows)
```

Questions this view answers:

- **Which large tables are read by sequential scans?** High `seq_tup_read` on a big table with low `idx_scan` usually means a missing index (Module 11), or a query that really needs the whole table.
- **Are updates HOT?** `n_tup_hot_upd / n_tup_upd` near 1 is ideal for update-heavy tables (Module 16, section 7).
- **Is autovacuum keeping up?** `n_dead_tup` relative to `n_live_tup`, plus `last_autovacuum`.

### 8.2 Indexes: unused and duplicate indexes

Every index slows down writes and occupies memory and disk. `pg_stat_user_indexes.idx_scan` counts how often each one was used:

```sql
SELECT s.relname AS table_name, s.indexrelname AS index_name, s.idx_scan,
       pg_size_pretty(pg_relation_size(s.indexrelid)) AS size,
       i.indisunique, i.indisprimary
FROM pg_stat_user_indexes s
JOIN pg_index i ON i.indexrelid = s.indexrelid
WHERE s.schemaname = 'bazaar'
  AND s.idx_scan = 0
  AND NOT i.indisunique              -- unique indexes enforce constraints even if never scanned
ORDER BY pg_relation_size(s.indexrelid) DESC
LIMIT 8;
```

```text
   table_name    |           index_name            | idx_scan | size  | indisunique | indisprimary 
-----------------+---------------------------------+----------+-------+-------------+--------------
 payments        | payments_order_id_idx           |        0 | 23 MB | f           | f
 products        | products_title_trgm_idx         |        0 | 20 MB | f           | f
 orders          | orders_seller_id_created_at_idx |        0 | 15 MB | f           | f
 orders          | orders_user_id_created_at_idx   |        0 | 15 MB | f           | f
 events_y2025m12 | events_y2025m12_occurred_at_idx |        0 | 14 MB | f           | f
 order_items     | order_items_product_id_idx      |        0 | 12 MB | f           | f
 products        | products_search_vector_idx      |        0 | 12 MB | f           | f
 events_y2025m11 | events_y2025m11_occurred_at_idx |        0 | 11 MB | f           | f
(8 rows)
```

On the freshly reset database this list is long and meaningless: the counters started when `reset.sh` created the database a few minutes ago, so every index that no query has needed *yet* shows up, including `payments_order_id_idx`, the index that makes `payments` lookups by order fast. That's the most important lesson about this view: zero scans only means something after the counters have seen a representative stretch of production traffic.

Before dropping anything:

- Check **how long** the counters have been collecting (`stats_reset` in `pg_stat_database`; also, crashes reset them). A month that includes the end-of-month reports is a reasonable minimum.
- Check **replicas**. Statistics are per server. An index that is never used on the primary may serve all the read queries on a replica (Module 19).
- Unique and primary-key indexes enforce constraints and are needed even with zero scans. Indexes on foreign-key columns may show few scans but save you when a parent row is deleted.
- Use `DROP INDEX CONCURRENTLY`, and keep the `CREATE INDEX` statement at hand.

### 8.3 Databases: pg_stat_database

One row per database with transaction, I/O, and trouble counters:

```sql
SELECT datname, numbackends, xact_commit, xact_rollback,
       blks_read, blks_hit,
       round(100.0 * blks_hit / nullif(blks_hit + blks_read, 0), 2) AS cache_hit_pct,
       temp_files, pg_size_pretty(temp_bytes) AS temp_bytes, deadlocks, conflicts,
       checksum_failures, stats_reset
FROM pg_stat_database
WHERE datname = 'bazaar';
```

```text
 datname | numbackends | xact_commit | xact_rollback | blks_read | blks_hit | cache_hit_pct | temp_files | temp_bytes | deadlocks | conflicts | checksum_failures | stats_reset 
---------+-------------+-------------+---------------+-----------+----------+---------------+------------+------------+-----------+-----------+-------------------+-------------
 bazaar  |           1 |         537 |             0 |    861524 | 62590407 |         98.64 |         58 | 1063 MB    |         0 |         0 |                   | 
(1 row)
```

- **Cache hit ratio** (`blks_hit / (blks_hit + blks_read)`): the share of page requests served from `shared_buffers`. OLTP systems usually sit above 99%. A sudden drop means the working set no longer fits. Remember that a "read" may still come from the OS page cache (Module 16).
- **`xact_rollback`** relative to commits: a rising rollback rate usually means application errors (serialization failures, constraint violations, timeouts).
- **`temp_files` / `temp_bytes`**: sorts and hashes that spilled to disk (section 2.2).
- **`deadlocks`**: should be (close to) zero; Module 10 explains how to avoid them.
- **`checksum_failures`**: data corruption detected (only with data checksums enabled). Any non-zero value is an emergency.

### 8.4 Background writer, checkpointer, and pg_stat_io

`pg_stat_bgwriter` (now only the background writer's counters, since PG 17) and `pg_stat_checkpointer` (section 4) tell you who writes dirty buffers. Since PG 16, **`pg_stat_io`** breaks all I/O down by process type, object, and context, which answers questions that used to be guesswork:

```sql
SELECT backend_type, object, context, reads, writes, extends, hits, evictions, reuses, fsyncs
FROM pg_stat_io
WHERE (reads > 0 OR writes > 0 OR extends > 0)
ORDER BY backend_type, object, context;
```

```text
    backend_type     |    object     |  context  | reads  | writes | extends |   hits    | evictions | reuses | fsyncs 
---------------------+---------------+-----------+--------+--------+---------+-----------+-----------+--------+--------
 autovacuum launcher | relation      | normal    |      1 |      0 |         |         0 |         0 |        |      0
 autovacuum worker   | relation      | normal    |   1675 |    519 |      30 |     72167 |      1120 |        |      0
 autovacuum worker   | relation      | vacuum    |  27103 |   9729 |       0 |     15632 |       825 |  25430 |       
 background worker   | relation      | bulkread  | 190022 |  16362 |         |     47201 |      1728 | 188166 |       
 background worker   | relation      | normal    |   8720 |   6633 |       0 |     68330 |      7818 |        |      0
 background writer   | relation      | normal    |        | 144376 |         |           |           |        |      0
 checkpointer        | relation      | normal    |        |  92370 |         |           |           |        |   3453
 client backend      | relation      | bulkread  | 725397 |  60713 |         |    161241 |     23702 | 699509 |       
 client backend      | relation      | bulkwrite |      0 |  51795 |   57048 |     56016 |      9984 |  47040 |       
 client backend      | relation      | normal    |  78049 | 366036 |  557110 | 171559933 |    583002 |        |      0
 client backend      | relation      | vacuum    | 689732 | 293014 |       0 |    230609 |      3840 | 685892 |       
 client backend      | temp relation | normal    | 935979 | 381909 |  359091 |  16475730 |   1288926 |        |       
 standalone backend  | relation      | bulkwrite |      0 |      0 |       8 |         7 |         0 |      0 |       
 standalone backend  | relation      | normal    |    537 |   1014 |     665 |     90406 |         0 |        |      0
 standalone backend  | relation      | vacuum    |      9 |      0 |       0 |       910 |         0 |      0 |       
(15 rows)
```

How to read it:

- `context = normal` is I/O through the shared buffer pool; `bulkread`, `bulkwrite` and `vacuum` are the ring buffers from Module 16 (section 2.4). `reuses` counts ring buffers reused, `evictions` buffers evicted from the shared pool.
- **`writes` by `client backend` in context `normal`** means backends had to write dirty buffers themselves to free space, which slows queries down. Ideally the checkpointer and background writer do almost all writes. If client backends write a lot, the buffer pool is too small or the background writer too timid (`bgwriter_lru_maxpages`, `bgwriter_lru_multiplier`).
- `extends` are writes that grow files (inserts into new pages).
- With `track_io_timing = on`, `read_time` and `write_time` say how long it took.

### 8.5 pg_stat_statements, operationally

Module 12 used `pg_stat_statements` to find slow queries. Operationally, the useful questions are about **total load**: which statements consume the most time overall (a 2 ms query run 5 million times a day beats a 10-second report run twice), which do the most I/O, and which spill to disk:

```sql
SELECT left(regexp_replace(query, '\s+', ' ', 'g'), 50) AS query,
       calls,
       round(total_exec_time::numeric)                                  AS total_ms,
       round(mean_exec_time::numeric, 2)                                AS mean_ms,
       round((100 * total_exec_time / sum(total_exec_time) OVER ())::numeric, 1) AS pct_time,
       shared_blks_read, temp_blks_written
FROM pg_stat_statements
WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
ORDER BY total_exec_time DESC
LIMIT 5;
```

```text
                       query                        | calls | total_ms | mean_ms  | pct_time | shared_blks_read | temp_blks_written 
----------------------------------------------------+-------+----------+----------+----------+------------------+-------------------
 INSERT INTO events (occurred_at, user_id, session_ |     1 |    31582 | 31581.61 |     28.9 |              138 |             49607
 CREATE TEMP TABLE ev AS WITH steps AS ( -- page vi |     1 |    22630 | 22630.20 |     20.7 |                0 |             69856
 ALTER TABLE inventory ALTER COLUMN reserved SET DE |     1 |    10856 | 10855.95 |      9.9 |                7 |                 0
 ALTER TABLE products ADD COLUMN search_vector tsve |     1 |     9575 |  9575.42 |      8.8 |           102780 |              2454
 VACUUM payments                                    |     1 |     8027 |  8027.40 |      7.3 |             4580 |                 0
(5 rows)
```

On the freshly reset database the top entries are the seed scripts' bulk statements, and the `ALTER TABLE` from section 7.3, which ran for only a moment but spent about 10 seconds in the lock queue: execution time includes time spent waiting for locks. Note the `temp_blks_written` of the seed statements too: big `INSERT ... SELECT`s with sorts and hashes spill to disk at the default `work_mem`.

Practical notes:

- `pg_stat_statements.max` (default 5000) distinct statements are kept. When it overflows, the least-used entries are evicted; `pg_stat_statements_info.dealloc` counts evictions. Many evictions mean your application generates too many distinct query texts (often `IN (...)` lists of varying length, normalized better since PG 18).
- `pg_stat_statements.track = all` (the course setting) also tracks statements inside functions; `top` (the default) tracks only what clients send.
- Take periodic snapshots (or let your metrics stack do it) and compare: the totals since the last reset hide whether a statement became slow *today*.
- `pg_stat_statements_reset()` takes optional `userid`, `dbid`, `queryid` arguments to reset just one entry.

---

## 9. Logging

Statistics tell you *how much*. Logs tell you *what exactly, and when*. A well-configured log is the best tool for "what happened at 3:12 a.m."

### 9.1 Where logs go

`log_destination` (`stderr` by default; also `csvlog`, `jsonlog` since PG 15, `syslog`, `eventlog`) chooses the format and destination. With `logging_collector = on`, a `logger` process captures stderr into files in `log_directory`, rotated by `log_rotation_age`/`log_rotation_size`. In Docker the convention is the opposite: leave `logging_collector = off`, write to stderr, and let Docker collect it (`docker logs course-pg`). Structured `jsonlog` is the easiest to ship to a log platform.

### 9.2 What to log

A production-grade starting point (all `sighup` or `superuser` context, so a reload is enough):

```text
log_line_prefix = '%m [%p] %q%u@%d app=%a client=%h xid=%x '  # time, pid, user@db, app, client
log_min_duration_statement = 250ms     # every statement slower than this, with its duration
log_lock_waits = on                    # waits longer than deadlock_timeout (1s)
log_temp_files = 0                     # every temp file, with its size
log_checkpoints = on                   # default since PG 15
log_autovacuum_min_duration = 1s       # autovacuum runs slower than this, with details
log_connections = on                   # optional: noisy without a pooler
log_disconnections = on                # logs session duration
log_statement = ddl                    # every CREATE/ALTER/DROP, for the audit trail
```

- **`log_line_prefix`** decides whether a log line is useful at all. `%m` (timestamp with milliseconds), `%p` (PID, to correlate with `pg_stat_activity`), `%u@%d`, `%a` (application name) and `%h` (client) are the minimum. `%q` makes the rest of the prefix appear only for session processes.
- **`log_min_duration_statement`** is the classic slow-query log. Logging *every* statement (`0`) on a busy server costs real I/O. In PG 13+ `log_min_duration_sample` and `log_statement_sample_rate` let you log a sample of faster statements.
- **`log_lock_waits`** logs a message when a lock wait exceeds `deadlock_timeout` (1 s), including who holds the lock. It costs almost nothing and turns "it was slow for a while" into a precise story.

Let's turn on lock-wait logging and see what it produces with the blocking scenario from section 7.3:

```sql
ALTER SYSTEM SET log_lock_waits = on;
ALTER SYSTEM SET log_line_prefix = '%m [%p] %q%u@%d app=%a ';
SELECT pg_reload_conf();
```


```text
2026-10-14 08:18:29.725 UTC [1] LOG:  received SIGHUP, reloading configuration files
2026-10-14 08:18:29.727 UTC [1] LOG:  parameter "log_lock_waits" changed to "on"
2026-10-14 08:18:29.727 UTC [1] LOG:  parameter "log_line_prefix" changed to "%m [%p] %q%u@%d app=%a "
2026-10-14 08:18:31.380 UTC [1201] postgres@bazaar app=checkout LOG:  process 1201 still waiting for ShareLock on transaction 1311 after 1001.455 ms
2026-10-14 08:18:31.380 UTC [1201] postgres@bazaar app=checkout DETAIL:  Process holding the lock: 1194. Wait queue: 1201.
2026-10-14 08:18:31.380 UTC [1201] postgres@bazaar app=checkout CONTEXT:  while locking tuple (0,7) in relation "inventory"
2026-10-14 08:18:31.380 UTC [1201] postgres@bazaar app=checkout STATEMENT:  UPDATE inventory SET reserved = reserved WHERE product_id = 7
2026-10-14 08:18:32.745 UTC [1201] postgres@bazaar app=checkout LOG:  process 1201 acquired ShareLock on transaction 1311 after 2366.510 ms
2026-10-14 08:18:32.745 UTC [1201] postgres@bazaar app=checkout CONTEXT:  while locking tuple (0,7) in relation "inventory"
2026-10-14 08:18:32.745 UTC [1201] postgres@bazaar app=checkout STATEMENT:  UPDATE inventory SET reserved = reserved WHERE product_id = 7
```

The checkout session waited more than a second for a row lock; the log names the process holding it (`Process holding the lock`), the queue, the tuple, and the statement, and later reports when the lock was acquired and after how long.

### 9.3 auto_explain

The slow-query log tells you *which* statement was slow but not *why*: by the time you run `EXPLAIN` by hand, the data, the statistics, or the parameters may be different. **`auto_explain`** logs the actual execution plan of slow statements, at the moment they ran. It is loaded in the course container via `shared_preload_libraries`; you can also `LOAD 'auto_explain'` in a session as a superuser. Its main settings:

```text
auto_explain.log_min_duration = 500ms   # plans for statements slower than this (-1 = off, the default)
auto_explain.log_analyze = on           # include actual rows and timing (adds overhead to ALL statements)
auto_explain.log_buffers = on           # include buffer counts (needs log_analyze)
auto_explain.log_timing = off           # skip per-node timing, which is the expensive part of ANALYZE
auto_explain.log_nested_statements = on # also statements inside functions
auto_explain.sample_rate = 1            # fraction of statements to consider
auto_explain.log_format = text          # or json, for tools
```

Try it for one session:

```sql
SET auto_explain.log_min_duration = '100ms';
SET auto_explain.log_analyze = on;
SET auto_explain.log_timing = off;
SELECT c.name AS category, sum(oi.quantity * oi.unit_price) AS revenue
FROM order_items oi
JOIN products p   ON p.id = oi.product_id
JOIN categories c ON c.id = p.category_id
GROUP BY c.name ORDER BY revenue DESC LIMIT 3;
RESET auto_explain.log_min_duration;
```

```bash
docker logs --since 1m course-pg 2>&1 | grep -A 40 'duration: .* plan:'
```

```text
2026-10-14 08:18:33.950 UTC [1209] postgres@bazaar app=psql LOG:  duration: 508.675 ms  plan:
	Query Text: SELECT c.name AS category, sum(oi.quantity * oi.unit_price) AS revenue
	FROM order_items oi
	JOIN products p   ON p.id = oi.product_id
	JOIN categories c ON c.id = p.category_id
	GROUP BY c.name ORDER BY revenue DESC LIMIT 3;
	Limit  (cost=43357.73..43357.73 rows=3 width=42) (actual rows=3 loops=1)
	  ->  Sort  (cost=43357.73..43357.90 rows=69 width=42) (actual rows=3 loops=1)
	        Sort Key: (sum(((oi.quantity)::numeric * oi.unit_price))) DESC
	        Sort Method: top-N heapsort  Memory: 25kB
	        ->  Finalize GroupAggregate  (cost=43338.84..43356.83 rows=69 width=42) (actual rows=50 loops=1)
	              Group Key: c.name
	              ->  Gather Merge  (cost=43338.84..43354.94 rows=138 width=42) (actual rows=150 loops=1)
	                    Workers Planned: 2
	                    Workers Launched: 2
	                    ->  Sort  (cost=42338.81..42338.98 rows=69 width=42) (actual rows=50 loops=3)
	                          Sort Key: c.name
	                          Sort Method: quicksort  Memory: 29kB
	                          Worker 0:  Sort Method: quicksort  Memory: 29kB
	                          Worker 1:  Sort Method: quicksort  Memory: 29kB
	                          ->  Partial HashAggregate  (cost=42335.84..42336.70 rows=69 width=42) (actual rows=50 loops=3)
	                                Group Key: c.name
	                                Batches: 1  Memory Usage: 48kB
	                                Worker 0:  Batches: 1  Memory Usage: 48kB
	                                Worker 1:  Batches: 1  Memory Usage: 48kB
	                                ->  Hash Join  (cost=19605.55..37056.97 rows=527887 width=21) (actual rows=422309 loops=3)
	                                      Hash Cond: (p.category_id = c.id)
	                                      ->  Parallel Hash Join  (cost=19603.00..35583.60 rows=527887 width=15) (actual rows=422309 loops=3)
	                                            Hash Cond: (oi.product_id = p.id)
	                                            ->  Parallel Seq Scan on order_items oi  (cost=0.00..14594.87 rows=527887 width=19) (actual rows=422309 loops=3)
	                                            ->  Parallel Hash  (cost=18561.33..18561.33 rows=83333 width=12) (actual rows=66667 loops=3)
	                                                  Buckets: 262144  Batches: 1  Memory Usage: 11488kB
	                                                  ->  Parallel Seq Scan on products p  (cost=0.00..18561.33 rows=83333 width=12) (actual rows=66667 loops=3)
	                                      ->  Hash  (cost=1.69..1.69 rows=69 width=14) (actual rows=69 loops=3)
	                                            Buckets: 1024  Batches: 1  Memory Usage: 12kB
	                                            ->  Seq Scan on categories c  (cost=0.00..1.69 rows=69 width=14) (actual rows=69 loops=3)
```

> **Pitfall:** `auto_explain.log_analyze = on` instruments **every** statement (it must decide before execution whether the statement will turn out slow), and per-node timing calls the clock twice per row per node. On a busy server set `log_timing = off`, keep `sample_rate` below 1 if necessary, and measure the overhead.

Reset the logging settings we changed (keep them if you like the output):

```sql
ALTER SYSTEM RESET log_lock_waits;
ALTER SYSTEM RESET log_line_prefix;
SELECT pg_reload_conf();
```

---

## 10. Stopping runaway work

### 10.1 Cancel or terminate

Two functions stop another session's work. Both require being the same role as the target, a superuser, or a member of `pg_signal_backend`:

| Function | Effect |
|---|---|
| `pg_cancel_backend(pid)` | Cancels the **current statement** (like pressing Ctrl+C in psql). The transaction is aborted, but the connection stays open; the client gets `ERROR: canceling statement due to user request`. Does nothing to an idle session. |
| `pg_terminate_backend(pid [, timeout])` | Ends the **whole session**: the transaction rolls back, locks are released, the connection is closed. The client gets `FATAL: terminating connection due to administrator command`. |

Always try cancel first: it is gentler, and well-written applications retry. Use terminate for sessions that are `idle in transaction` (there is no statement to cancel) or that don't respond to a cancel. Never `kill -9` a backend from the shell: as Module 16 showed, the postmaster then restarts the entire server.

```sql
-- End every session that has been idle in a transaction for more than 10 minutes
SELECT pid, usename, application_name, now() - state_change AS idle_for,
       pg_terminate_backend(pid) AS terminated
FROM pg_stat_activity
WHERE state = 'idle in transaction'
  AND now() - state_change > interval '10 minutes';
```

### 10.2 Timeouts that do it for you

Better than watching for runaway sessions is to let PostgreSQL end them. All of these can be set per role, per database, per session or per transaction (section 1.5):

| Setting | Ends | Typical use |
|---|---|---|
| `statement_timeout` | Any statement running longer than this (cancels it). | Web application role: a few seconds. Never set it globally in `postgresql.conf`: it also cancels your maintenance jobs, `pg_dump`, and migrations. |
| `lock_timeout` | A statement that waits longer than this for a **lock**. | Migrations: `SET lock_timeout = '3s'` before `ALTER TABLE`, and retry (section 7.3). |
| `idle_in_transaction_session_timeout` | Sessions idle inside a transaction longer than this (terminates them). | 1 to 10 minutes for application roles. The best protection for VACUUM's xmin horizon. |
| `transaction_timeout` (new in PG 17) | Transactions (or single statements outside a transaction) whose total duration exceeds this (terminates the session). | A hard cap per role for batch jobs. |
| `idle_session_timeout` | Idle sessions outside a transaction (terminates them). | Rarely: it surprises connection pools, which keep idle connections on purpose. |

Let's watch two of them trigger:

```sql
SET statement_timeout = '200ms';
SELECT count(*) FROM order_items a JOIN order_items b USING (product_id);
RESET statement_timeout;
```

```text
SET
ERROR:  canceling statement due to statement timeout
RESET
```

For the idle-in-transaction timeout, type the statements by hand so that the session really sits idle between them:

```sql
SET idle_in_transaction_session_timeout = '1s';
BEGIN;
SELECT 1 AS inside_tx;
-- wait two seconds, then:
SELECT 2 AS after_idle;
```


```text
SET
BEGIN
 inside_tx 
-----------
         1
(1 row)

FATAL:  terminating connection due to idle-in-transaction timeout
server closed the connection unexpectedly
	This probably means the server terminated abnormally
	before or while processing the request.
connection to server was lost
```

The session that stayed idle for 2 seconds inside its transaction was terminated after 1 second; the client only found out when it tried to send its next statement. That's the idea: the application gets an error and its transaction is gone, but the locks and the snapshot were released after one second rather than one hour.

For Bazaar, sensible per-role defaults (the project milestone sets them):

```sql
ALTER ROLE bazaar_app      IN DATABASE bazaar SET statement_timeout = '5s';
ALTER ROLE bazaar_app      IN DATABASE bazaar SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE bazaar_analyst  IN DATABASE bazaar SET statement_timeout = '5min';
```

---

## 11. Connection pooling with PgBouncer

### 11.1 Why a pooler

Section 3 explained why PostgreSQL wants a small number of busy connections. Applications want the opposite: every web worker, every background job process, every serverless function wants its own connection, and most of the time each of them is idle. A modern deployment easily has 50 application instances with a pool of 10 connections each, which is 500 PostgreSQL backends, mostly idle, plus a connect/disconnect storm every time the instances are redeployed.

A **connection pooler** sits between them. Clients connect to the pooler (cheap: PgBouncer is a single-threaded event loop that handles thousands of client sockets in a few MB of memory), and the pooler forwards their work over a small set of real server connections that it keeps open:

```text
   app instance 1  ─┐  (thousands of cheap client connections)
   app instance 2  ─┤
   ...             ─┼──>  PgBouncer  ═══ 20 server connections ═══>  PostgreSQL
   app instance 50 ─┤                    (pool per database+user)    (20 backends)
   cron jobs       ─┘
```

### 11.2 Pool modes

The key question is **when a server connection is given back to the pool** so that another client can use it:

| Mode | Server connection is assigned... | Multiplexing | Breaks |
|---|---|---|---|
| **session** | for the client's whole connection; returned when the client disconnects | None while connected. Only saves connection setup cost for short-lived clients. | Nothing. |
| **transaction** | for the duration of one transaction; returned at `COMMIT`/`ROLLBACK` (or after each statement in autocommit mode) | High: 1000 mostly-idle clients can share 20 server connections. | Anything that relies on **session state** surviving between transactions. |
| **statement** | for one statement; multi-statement transactions are refused | Highest | All of the above, plus transactions. Rarely used. |

**Transaction mode** is what makes pooling worthwhile, and it changes the rules for application code. Consecutive transactions of one client may run on **different** server backends, and a backend may serve **other clients** in between. So anything that lives in the *session* rather than in the *transaction* is unreliable:

| Feature | What goes wrong in transaction mode | Use instead |
|---|---|---|
| `SET` (session-level) | Applies to whichever backend ran it: lost for your next transaction, *leaked* to the next client of that backend. | `SET LOCAL` inside the transaction; per-role/per-database settings (section 1.5); connection-string parameters PgBouncer tracks. |
| Session advisory locks (`pg_advisory_lock`) | The lock belongs to the backend; it may stay held after your client disconnects, and your unlock may run on another backend. | Transaction-level `pg_advisory_xact_lock`. |
| `LISTEN` | Notifications are delivered to the backend, which may be serving someone else. | A dedicated direct (or session-mode) connection for listeners. |
| Temporary tables | Created on one backend, missing on the next. | Real tables, CTEs, arrays; or session mode for that workload. |
| SQL-level `PREPARE` / `DEALLOCATE` | The prepared statement exists only on one backend. | Protocol-level prepared statements with PgBouncer 1.21+ (below). |
| `WITH HOLD` cursors | Survive the transaction on one backend only. | Fetch everything in the transaction, or keyset pagination. |

**Prepared statements** deserve a closer look, because drivers use them automatically. Drivers don't send `PREPARE` as SQL: they use the extended query protocol's `Parse`/`Bind`/`Execute` messages. asyncpg prepares and caches every statement by default, psycopg 3 prepares a statement after it has been executed 5 times (`prepare_threshold`), and JDBC after 5 executions. Before **PgBouncer 1.21**, a statement prepared on backend A and executed on backend B failed with `prepared statement "..." does not exist`, so the advice was to disable prepared statements in the driver. Since 1.21, PgBouncer can **track protocol-level prepared statements**: it remembers each client's statements, and when the client's transaction lands on a backend that doesn't know the statement yet, PgBouncer prepares it there first, transparently. It is enabled by setting `max_prepared_statements` to a non-zero value (the default is 200 since PgBouncer 1.24; it was 0 before). SQL-level `PREPARE` is still not supported.

### 11.3 The lab

The lab in [`lab/`](lab/) runs PostgreSQL 17 and PgBouncer 1.26 side by side with Docker Compose. You need nothing else installed: all clients (`psql`, `pgbench`) run inside the PostgreSQL container and reach PgBouncer over the Compose network.

```text
lab/
├── compose.yaml            # services: pg (postgres:17), pgbouncer (edoburu/pgbouncer:v1.26.0-p0)
├── initdb/01-lab.sql       # creates role app and database bench on first start
├── pgbouncer/
│   ├── pgbouncer.ini       # three pooled databases, transaction mode by default
│   └── userlist.txt        # lab credentials
├── bench.sh                # pgbench: direct vs pooled, three scenarios
└── pitfalls.sh             # what breaks in transaction mode, demonstrated
```

The PgBouncer configuration defines three **pooled databases**, all pointing to the same real database `bench`:

```ini
[databases]
bench         = host=pg port=5432 dbname=bench                       ; transaction pooling
bench_session = host=pg port=5432 dbname=bench pool_mode=session     ; session pooling
bench_tiny    = host=pg port=5432 dbname=bench pool_size=1           ; one server connection

[pgbouncer]
listen_port = 6432
auth_type = scram-sha-256
auth_file = /etc/pgbouncer/userlist.txt
pool_mode = transaction
max_client_conn = 2000
default_pool_size = 20
max_prepared_statements = 200
server_reset_query = DISCARD ALL
admin_users = postgres
```

(The real file has the comments on separate lines, because PgBouncer doesn't accept comments after a value.) `default_pool_size = 20` means at most 20 server connections per (database, user) pair; `max_client_conn = 2000` client connections are accepted. `server_reset_query` runs when a client releases a server connection in session mode, so the next client starts clean.

Start it:

```bash
cd modules/17-operations/lab
docker compose up -d --wait
docker compose ps
```

```text
NAME                      IMAGE                          COMMAND                  SERVICE     CREATED         STATUS                   PORTS
m17-pooling-pg-1          postgres:17                    "docker-entrypoint.s…"   pg          4 minutes ago   Up 4 minutes (healthy)   0.0.0.0:55432->5432/tcp
m17-pooling-pgbouncer-1   edoburu/pgbouncer:v1.26.0-p0   "/entrypoint.sh /usr…"   pgbouncer   4 minutes ago   Up 4 minutes (healthy)   5432/tcp, 0.0.0.0:56432->6432/tcp
```

Connect through PgBouncer (port 6432 inside the network; `56432` on your machine if you have a local `psql`):

```bash
docker compose exec -e PGPASSWORD=app_pw pg psql -h pgbouncer -p 6432 -U app bench
```

PgBouncer also has an **admin console**: connect to the virtual database `pgbouncer` as an `admin_users` member and run `SHOW` commands:

```bash
docker compose exec -e PGPASSWORD=postgres pg psql -h pgbouncer -p 6432 -U postgres pgbouncer -c 'SHOW POOLS'
```

The most useful are `SHOW POOLS` (per pool: `cl_active` clients linked to a server connection, `cl_waiting` clients waiting for one, `sv_active`/`sv_idle` server connections, `maxwait` seconds the oldest client has waited), `SHOW STATS` (queries and transactions per second, average wait), `SHOW CLIENTS`, `SHOW SERVERS`, and `SHOW CONFIG`. The console also accepts `RELOAD` (re-read the config), `PAUSE`/`RESUME` (hold all traffic, for example during a failover, Module 20), and `SET` for some settings.

### 11.4 What pooling buys: pgbench

`bench.sh` initializes the standard pgbench tables (scale 10) and runs three experiments, each once directly against PostgreSQL and once through PgBouncer. The default pgbench workload is a TPC-B-like transaction: three `UPDATE`s, a `SELECT` and an `INSERT`. The server has `max_connections = 100`. These numbers come from a 4-vCPU machine; yours will differ, the ratios are what matter.

```bash
./bench.sh
```

**A. Connection churn.** `pgbench -C` opens a new connection for every transaction, like a PHP page or a serverless function without a pool. Read-only queries, 20 clients:

```text
--- direct
number of failed transactions: 0 (0.000%)
latency average = 112.535 ms
tps = 177.722788 (including reconnection times)
--- pooled
number of failed transactions: 0 (0.000%)
latency average = 70.973 ms
tps = 281.799197 (including reconnection times)
```

Through PgBouncer the throughput is about 1.6 times as high. Opening a client connection to PgBouncer still costs a SCRAM exchange, but PgBouncer reuses its server connections, so there's no new PostgreSQL process to fork and no cold catalog caches to warm up.

**B. More clients than `max_connections`.** 300 concurrent clients:

```text
--- direct
pgbench: error: connection to server at "pg" (172.20.0.2), port 5432 failed: FATAL:  remaining connection slots are reserved for roles with the SUPERUSER attribute
--- pooled
    (mid-run) PgBouncer SHOW POOLS for database bench:
    database  cl_active  cl_waiting  sv_active  sv_idle  maxwait 
    bench     25         275         20         0        0       
    (mid-run) backends in PostgreSQL for role app, by state:
        state        | count 
---------------------+-------
 active              |    11
 idle                |     1
 idle in transaction |     8
(3 rows)

number of failed transactions: 0 (0.000%)
latency average = 250.248 ms
tps = 1198.811547 (without initial connection time)
```

Directly, pgbench can't even open its connections: `max_connections` is 100. Through PgBouncer all 300 clients run, sharing 20 server connections. Mid-run, `SHOW POOLS` shows all 20 server connections busy (`sv_active`) and some 275 clients waiting in PgBouncer's queue (`cl_waiting`), while PostgreSQL sees only the pool's 20 backends. Notice how many of them are `idle in transaction`: pgbench sends each statement of its transaction separately, and between statements the backend waits for the client's next round trip. A server connection is only as busy as its client is fast, which is another reason to keep transactions short and chatty clients close to the database.

**C. 50 long-lived clients.** No churn, and fewer clients than `max_connections`:

```text
--- direct
number of failed transactions: 0 (0.000%)
latency average = 26.130 ms
tps = 1913.477986 (without initial connection time)
--- pooled
number of failed transactions: 0 (0.000%)
latency average = 34.961 ms
tps = 1430.183481 (without initial connection time)
```

Here direct connections are *faster*. PgBouncer adds a network hop to every statement and, with 50 clients and 20 server connections, makes some clients wait. On this small machine, 50 backends are not yet too many. A pooler is not a performance booster in itself: it bounds the number of server connections and absorbs connection churn. Its benefit shows when clients outnumber what the server can run efficiently, or connect and disconnect frequently, which is the normal situation for a fleet of application servers.

### 11.5 What breaks: pitfalls.sh

`pitfalls.sh` demonstrates each item from the table in section 11.2. The `bench_tiny` pool has a single server connection, which makes the effects deterministic: every client is served by the same backend.

```bash
./pitfalls.sh
```

**1. `SET` leaks to the next client.** Client A sets `work_mem` and disconnects; client B, a different connection, inherits it:

```text
   who    | backend | work_mem 
----------+---------+----------
 client A |    3832 | 123MB
(1 row)

   who    | backend | work_mem 
----------+---------+----------
 client B |    3832 | 123MB
(1 row)
```

In session mode the same experiment is clean, because PgBouncer runs `DISCARD ALL` (`server_reset_query`) when A disconnects:

```text
   who    | backend | work_mem 
----------+---------+----------
 client A |    3844 | 123MB
(1 row)

   who    | backend | work_mem 
----------+---------+----------
 client B |    3844 | 4MB
(1 row)
```

**2. Your own session state disappears.** In the regular 20-connection pool, client A sets `work_mem` and prepares a statement. While A is between transactions, another client takes A's backend for a few seconds, so A's next transaction runs on a different backend:

```text
  step  | backend | work_mem 
--------+---------+----------
 before |    3545 | 123MB
(1 row)

 step  | backend | work_mem 
-------+---------+----------
 after |    3539 | 4MB
(1 row)

ERROR:  prepared statement "top_branch" does not exist
```

**3. Session advisory locks outlive their client.** Client A takes `pg_advisory_lock(42)` and disconnects without unlocking. The lock stays held by the server backend (as `pg_locks` shows), and client B, served by the same backend, "acquires" the lock that A should have held exclusively:

```text
       step        | pg_advisory_lock 
-------------------+------------------
 client A locks 42 | 
(1 row)

 locktype | objid | pid  | granted 
----------+-------+------+---------
 advisory |    42 | 3832 | t
(1 row)

       step        | got_it 
-------------------+--------
 client B tries 42 | t
(1 row)
```

**4. `LISTEN` goes to the wrong client.** Client A runs `LISTEN order_paid` and disconnects; the backend keeps listening. When client B later sends a `NOTIFY`, B receives the notification:

```text
Asynchronous notification "order_paid" with payload "order 42" received from server process with PID 3832.
                step                
------------------------------------
 client B: just ran NOTIFY + SELECT
(1 row)
```

**5. Protocol-level prepared statements.** `pgbench -M prepared` uses the extended protocol to prepare its statements once per connection, like asyncpg or psycopg after a few executions. With `max_prepared_statements = 200`, PgBouncer handles them; with `0` (the behavior before 1.21), clients fail as soon as they land on a backend that doesn't know their statement:

```text
--- with max_prepared_statements = 200 (PgBouncer tracks them):
number of failed transactions: 0 (0.000%)
tps = 16260.150738 (without initial connection time)
--- with max_prepared_statements = 0 (PgBouncer does not track them):
pgbench exit code: 2
clients aborted: 38 of 40
     28 prepared statement "P_0" already exists
     38 prepared statement "P_0" does not exist
```

Clean up when you are done:

```bash
docker compose down -v     # removes the containers and the lab's data volume
```

### 11.6 Running PgBouncer in production

- **Pool size.** Start with `default_pool_size` around 2 to 4 times the number of CPU cores of the database server, summed over all pools (database/user pairs), and keep the total under `max_connections` with room for admin, replication and monitoring connections. `max_db_connections` caps the total per database.
- **Watch `cl_waiting` and `maxwait`.** Clients waiting in PgBouncer is where pooling turns into queueing. Short waits under peak load are the point of pooling; long waits mean the pool is too small or transactions are too long. `query_wait_timeout` (default 120 s) fails clients that wait too long.
- **Keep transactions short.** In transaction mode a client holds a server connection for the whole transaction, including any time it spends in application code between statements. An application that opens a transaction and then calls an external payment API holds one of your 20 server connections hostage.
- **Authentication.** Instead of a plain-text `userlist.txt`, use `auth_query` (PgBouncer looks up the password hash in PostgreSQL via a dedicated low-privilege `auth_user`) or put SCRAM secrets in the auth file.
- **Placement.** Either one PgBouncer next to the database (the simplest), or one per application host (removes the network hop, but the pools add up). PgBouncer is single-threaded: at very high rates run several instances on the same port with `so_reuseport = 1`.
- **High availability.** During a failover (Module 20) PgBouncer can `PAUSE` traffic and `RECONNECT` to the new primary, so clients see a pause instead of errors.
- **Alternatives** exist, such as Odyssey and PgCat (multi-threaded, with sharding and load-balancing features) and Supavisor. The transaction-mode rules above apply to all of them, and to the application-side pools of Modules 22 to 27.

---

## 12. Metrics stacks

Everything in sections 7 and 8 is a query. In production you don't want to run those queries by hand: you want them run every 15 to 60 seconds, stored as time series, graphed, and alerted on. The most common open-source stack:

```text
  PostgreSQL ──SQL──> postgres_exporter ──HTTP /metrics──> Prometheus ──> Grafana (dashboards)
     │                                                         │
     └─ logs (jsonlog) ──> Loki / Elasticsearch / ...          └──> Alertmanager ──> on-call
```

- **postgres_exporter** (from the Prometheus community) connects to PostgreSQL with a monitoring role (grant it `pg_monitor`, a built-in role that can read all statistics views without being superuser), runs queries against `pg_stat_*` views, and exposes the results as Prometheus metrics: `pg_stat_database_xact_commit`, `pg_stat_user_tables_n_dead_tup`, `pg_locks_count`, replication lag, and more. You can add your own queries, such as the views of this module's ops toolkit.
- **Prometheus** scrapes the exporter every N seconds and stores the samples. Counters become rates with `rate(pg_stat_database_xact_commit[5m])`.
- **Grafana** draws dashboards on top (there are popular community dashboards for postgres_exporter), and **Alertmanager** routes alerts.
- **Node exporter** on the same host supplies CPU, memory, disk space, disk latency, and network: half of all database incidents are visible there first.

Managed services (Amazon RDS Performance Insights, Google Cloud SQL Insights, Azure) and commercial tools (Datadog, pganalyze, ...) do the same with less setup. Some tools sample `pg_stat_activity` every second to build "active sessions by wait event" charts, which are the single most useful picture of database load.

Whatever the tool, these alerts catch most real incidents early:

| Alert | Condition (starting point) |
|---|---|
| Server down / not accepting connections | exporter can't connect |
| Connections near the limit | `numbackends` > 80% of `max_connections` |
| Long-running transaction | oldest `xact_start` of a non-idle session > 15 min |
| Idle in transaction | any session `idle in transaction` > 5 min |
| Transaction ID wraparound risk | `age(datfrozenxid)` > 500 million |
| Replication lag / inactive slot (Module 19) | replay lag > N seconds; inactive slot retaining > N GB of WAL |
| Disk space | data or WAL volume > 80% full |
| Deadlocks or rollbacks spike | rate of `deadlocks`, `xact_rollback` |
| Checkpoints too frequent | `num_requested` increasing |
| Cache hit ratio drop | below the system's normal baseline |

---

## Summary

- Settings come from defaults, `postgresql.conf`, `postgresql.auto.conf` (`ALTER SYSTEM`) and the command line, then per database, role, session, transaction and function. `pg_settings.context` says whether a change needs a **restart** (`postmaster`), a **reload** (`sighup`), or just `SET`. `pending_restart` and `pg_file_settings` show what's waiting.
- **`shared_buffers`** ≈ 25% of RAM; **`effective_cache_size`** ≈ 50 to 75% (a hint). **`work_mem`** is per sort/hash node per process, so it multiplies: keep it modest globally and raise it per role or transaction. **`maintenance_work_mem`** can be generous.
- Many connections cost memory, snapshot overhead and CPU contention. Keep active connections near 2 to 4 × cores and pool the rest.
- Tune checkpoints so they are **timed**, not requested: `checkpoint_timeout` 15 to 30 min, `max_wal_size` big enough, `checkpoint_completion_target = 0.9`. Watch `pg_stat_checkpointer` and the checkpoint log lines.
- Autovacuum triggers at `threshold + scale_factor × reltuples` dead tuples (or inserts, or modifications for analyze). Large tables need smaller scale factors; hot small tables need fixed thresholds and no throttling; everyone benefits from a higher cost limit. Watch `pg_stat_progress_vacuum` and log slow autovacuums.
- Measure bloat exactly with `pgstattuple`/`pgstatindex`, estimate it cheaply from `pg_stats`, and fix it with `REINDEX CONCURRENTLY`, `pg_repack`, or `VACUUM FULL` (exclusive lock). Prevent it with short transactions, batched changes and tuned autovacuum.
- `pg_stat_activity` + wait events show what's happening now; `pg_blocking_pids()` turns `pg_locks` into blocking chains. `pg_stat_user_tables`/`_indexes`, `pg_stat_database`, `pg_stat_io`, `pg_stat_checkpointer` and `pg_stat_statements` show trends.
- Log slow statements, lock waits, temp files, checkpoints and slow autovacuums with a rich `log_line_prefix`; use `auto_explain` to capture plans of slow statements.
- Prefer `pg_cancel_backend` to `pg_terminate_backend`, and let `statement_timeout`, `lock_timeout`, `idle_in_transaction_session_timeout` and `transaction_timeout` (PG 17) end runaway work automatically, set per role.
- **PgBouncer** in **transaction mode** multiplexes many clients onto few server connections, but session state (`SET`, session advisory locks, `LISTEN`, temp tables, SQL `PREPARE`) doesn't survive between transactions. PgBouncer 1.21+ supports protocol-level prepared statements via `max_prepared_statements`.
- In production, a metrics stack (postgres_exporter + Prometheus + Grafana, or a managed equivalent) runs these queries continuously and alerts on the conditions that precede incidents.

## What's next

You can now keep a single PostgreSQL server healthy. [Module 18](../18-backup-recovery/) makes sure that when something goes wrong anyway (a dropped table, a dead disk) you can get the data back: logical and physical backups, WAL archiving, and point-in-time recovery.
