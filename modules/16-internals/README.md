# Module 16: Internals Deep Dive

<!-- nav:start -->
> **Theory** · [Exercises](exercises.md) · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 15 Security and Row-Level Security](../15-security-rls/README.md) · [Course home](../../README.md) · [17 Configuration, Maintenance and Monitoring →](../17-operations/README.md)
<!-- nav:end -->

Bazaar's `inventory` table has about 200,000 rows, and each row is a handful of integers. Yet after a busy sale weekend the table takes several times more disk than it did on Friday, an `UPDATE` that touched one column wrote whole 8 KB pages to the WAL, and a monitoring graph shows a number called "transaction ID age" creeping towards two billion. None of this makes sense if you think of a table as a spreadsheet that PostgreSQL edits in place. It makes perfect sense once you know what PostgreSQL actually does with your rows. This module opens the hood. You will look at real pages with `pageinspect`, watch tuple headers change as transactions commit, follow an `UPDATE` through the heap and the WAL, crash the server on purpose and watch it recover, and read the statistics the planner uses to guess row counts.

Everything here is practical knowledge. Module 17 (configuration, maintenance and monitoring) tunes the machinery you meet in this module, and every operational decision there (how big `shared_buffers` should be, why autovacuum falls behind, what bloat is) is explained by the mechanisms here.

**What you'll learn**

- How a PostgreSQL server is organised as processes: the postmaster, one backend per connection, and the auxiliary processes (checkpointer, background writer, WAL writer, autovacuum launcher and workers), plus background and parallel workers.
- How shared memory works: the buffer pool, buffer descriptors, the clock-sweep replacement algorithm, ring buffers, and why data is cached twice (once by PostgreSQL, once by the operating system).
- Where your data lives on disk: the `PGDATA` directory, `base/<database oid>/<relfilenode>`, forks (`main`, `fsm`, `vm`, `init`), and 1 GB segment files.
- The anatomy of an 8 KB page and of a tuple header (`xmin`, `xmax`, `cmin`/`cmax`, `ctid`, infomask bits, the null bitmap), and how alignment padding makes column order matter.
- MVCC precisely: snapshots, the visibility rules, the commit log (`pg_xact`), and hint bits.
- Why an `UPDATE` writes a new row version, and how HOT updates and `fillfactor` avoid index maintenance.
- TOAST: how values larger than about 2 KB are compressed and moved out of line.
- The write-ahead log: LSNs, WAL records, full-page writes, checkpoints, and crash recovery, inspected with `pg_walinspect` and `pg_waldump`.
- VACUUM in detail, the visibility map, freezing, and transaction ID wraparound.
- The cumulative statistics system, and the planner statistics in `pg_statistic`: MCVs, histograms, `n_distinct`, and correlation.

**Before you start**

```bash
./project/bin/reset.sh 16     # state at the start of module 16, includes the large dataset (takes a few minutes)
./project/bin/psql.sh
```

This module uses several extensions that ship with PostgreSQL (the "contrib" modules). They only add inspection functions; none of them change how the server stores data. Create them once:

```sql
CREATE EXTENSION IF NOT EXISTS pageinspect;      -- raw pages and tuple headers
CREATE EXTENSION IF NOT EXISTS pg_buffercache;   -- what is in shared_buffers
CREATE EXTENSION IF NOT EXISTS pg_visibility;    -- the visibility map
CREATE EXTENSION IF NOT EXISTS pg_walinspect;    -- WAL records from SQL
CREATE EXTENSION IF NOT EXISTS pgstattuple;      -- exact live/dead tuple counts
```

The page-level demos use small purpose-built tables in a schema called `lab`, so that they don't disturb Bazaar's data and so that the pages are small enough to read in full:

```sql
CREATE SCHEMA IF NOT EXISTS lab;
```

Most examples need superuser rights (the inspection functions read raw files and shared memory). The course container's `postgres` user is a superuser. Some outputs below, such as transaction IDs, LSNs, OIDs and process IDs, will be different on your machine. The *shapes* and the reasoning are what matter.

---

<!-- toc:start -->
**Contents**

- [1. The process model](#1-the-process-model)
- [2. Shared memory and the buffer pool](#2-shared-memory-and-the-buffer-pool)
- [3. Where the data lives on disk](#3-where-the-data-lives-on-disk)
- [4. Inside an 8 KB page](#4-inside-an-8-kb-page)
- [5. The tuple header and alignment](#5-the-tuple-header-and-alignment)
- [6. MVCC, precisely](#6-mvcc-precisely)
- [7. UPDATE on disk, and HOT updates](#7-update-on-disk-and-hot-updates)
- [8. TOAST: storing large values](#8-toast-storing-large-values)
- [9. The write-ahead log](#9-the-write-ahead-log)
- [10. VACUUM in detail](#10-vacuum-in-detail)
- [11. Freezing and transaction ID wraparound](#11-freezing-and-transaction-id-wraparound)
- [12. The cumulative statistics system](#12-the-cumulative-statistics-system)
- [13. The planner's statistics](#13-the-planners-statistics)
- [Summary](#summary)
- [What's next](#whats-next)

<!-- toc:end -->

## 1. The process model

PostgreSQL is a **multi-process** server, not a multi-threaded one. Each connection gets its own operating-system process, and a family of helper processes runs alongside them. All of them share one big region of shared memory. That design dates from the 1990s, when threads were not portable. It has real consequences that you will feel in Module 17: connections are expensive, and a crash in one process is treated as a possible corruption of shared memory.

### 1.1 The family tree

Let's look at the processes inside the course container. `ps` shows the parent process ID (`PPID`) of each one:

```bash
docker exec course-pg ps -o pid,ppid,cmd -C postgres
```

```text
    PID    PPID CMD
      1       0 postgres -c shared_preload_libraries=pg_stat_statements,auto_explain -c pg_stat_statements.track=all -c track_io_timing=on
    760       1 postgres: checkpointer 
    761       1 postgres: background writer 
    762       1 postgres: walwriter 
    763       1 postgres: autovacuum launcher 
    764       1 postgres: logical replication launcher 
```

PID 1 is the **postmaster**, the process you start (in Docker it is the container's main process). Everything else is its child. The postmaster itself does very little:

- It listens on the TCP port and the Unix socket. When a client connects, it **forks** a new process, called a **backend**, and hands it the socket. From then on the client talks only to its backend. The postmaster never touches the query.
- It starts the auxiliary processes and restarts them if they die.
- It watches for crashes. If any child dies abnormally (a segfault, `kill -9`, the out-of-memory killer), the postmaster cannot know whether that process left shared memory half-modified. So it tells **every** other process to quit, discards shared memory, and runs crash recovery from the WAL (section 9). One crashing backend therefore disconnects everybody for a few seconds. That is a deliberate trade of availability for safety.

The children:

| Process | What it does |
|---|---|
| **backend** (`postgres: user db host(port) state`) | Runs the queries of one client connection: parsing, planning, executing. Lives until the client disconnects. |
| **checkpointer** | Periodically writes all dirty pages from shared memory to disk and records a *checkpoint*, so recovery never has to replay WAL from before it (section 9). |
| **background writer** | Trickles dirty pages to disk ahead of need, so that backends looking for a free buffer rarely have to write one themselves (section 2). |
| **walwriter** | Flushes WAL from memory to disk every `wal_writer_delay` (200 ms), so asynchronous commits (Module 17) reach disk soon, and backends at commit time find most of their WAL already written. |
| **autovacuum launcher** | Wakes up every `autovacuum_naptime` (1 min), decides which databases need attention, and starts **autovacuum workers** (at most `autovacuum_max_workers`, default 3) that run VACUUM and ANALYZE on individual tables. |
| **logical replication launcher** | Starts workers for logical replication subscriptions (Module 19). Idle when you have none. |

Depending on configuration you may also see: `archiver` (copies finished WAL files when `archive_mode = on`, Module 18), `walsender` / `walreceiver` (streaming replication, Module 19), `startup` (replays WAL during recovery and on standbys), `logger` (the syslogger when `logging_collector = on`, Module 17), and `walsummarizer` (new in PG 17, for incremental backups, only when `summarize_wal = on`).

> **Under the hood:** Before PostgreSQL 15 there was also a **stats collector** process that received statistics messages from backends over UDP. It is gone. Statistics now live in shared memory (section 12).

The same information is available from SQL. `pg_stat_activity` has one row per server process, and its `backend_type` column says what each one is:

```sql
SELECT pid, backend_type, state, left(query, 40) AS query
FROM pg_stat_activity
ORDER BY backend_type, pid;
```

```text
 pid  |         backend_type         | state  |                  query                   
------+------------------------------+--------+------------------------------------------
  763 | autovacuum launcher          |        | 
  761 | background writer            |        | 
  760 | checkpointer                 |        | 
 1315 | client backend               | active | SELECT pid, backend_type, state, left(qu
  764 | logical replication launcher |        | 
  762 | walwriter                    |        | 
(6 rows)
```

The `client backend` is your own `psql` session (`pg_backend_pid()` returns its PID). The auxiliary processes have no `state` and no query.

### 1.2 Background workers and parallel query

Besides the fixed cast above, PostgreSQL has a generic **background worker** framework. A background worker is a process that the postmaster starts on request, either at server start (registered by an extension in `shared_preload_libraries`, for example `pg_cron`) or dynamically while the server runs. Two built-in features use dynamic background workers:

- **Parallel query.** When the planner chooses a `Gather` node (Module 12), the backend running the query (the *leader*) asks the postmaster for up to `max_parallel_workers_per_gather` (default 2) **parallel workers**. Each worker attaches to a dynamic shared-memory segment, runs its part of the plan, and streams tuples back. They appear in `pg_stat_activity` with `backend_type = 'parallel worker'` and `leader_pid` set.
- **Logical replication** apply and table-sync workers.

All of them come out of one pool of `max_worker_processes` (default 8) slots, with `max_parallel_workers` (default 8) as the cap for parallel workers. If the pool is exhausted, a parallel query simply runs with fewer workers than planned (`Workers Launched: 0` in `EXPLAIN ANALYZE`), which is a common surprise on busy servers.

### 1.3 What a backend costs

Forking is cheap on Linux, but a backend is not just a fork. When a new backend starts it authenticates the client (SCRAM costs thousands of hash iterations on purpose, Module 15), loads catalog information into private caches as queries touch tables, and allocates private memory for sorting and hashing (`work_mem`, Module 17). A long-lived backend that has touched many tables can hold tens of megabytes of private catalog and plan caches. A few hundred idle connections are tolerable. Thousands are not: they cost memory, and they make every snapshot more expensive to take (section 6 explains why). This is why production systems put a connection pooler such as PgBouncer in front of PostgreSQL, which Module 17 builds in a lab.

> **PG 18:** PostgreSQL 18 adds asynchronous I/O. With the default `io_method = worker` you will see a few extra `io worker` processes that perform reads on behalf of backends.

---

## 2. Shared memory and the buffer pool

### 2.1 Why a buffer pool

Tables and indexes are files of 8 KB **pages** (also called **blocks**). PostgreSQL never reads or writes a single row from disk. It always moves whole pages. Reading the same page from disk again and again would be slow, so every page that any process needs is first brought into a shared cache called the **buffer pool**, whose size is `shared_buffers` (default 128 MB, that is 16,384 buffers of 8 KB). Every backend reads and modifies pages *in the buffer pool*. Changes are written back to the data files later, by the background writer, the checkpointer, or (as a last resort) by a backend.

Shared memory also holds other structures you will meet in this module: the **WAL buffers** (`wal_buffers`, section 9), the cache of the **commit log** (section 6), the **lock table** (Module 10), and the **proc array**, the list of running transactions that every snapshot reads.

### 2.2 Buffer descriptors and the mapping table

For each 8 KB buffer slot there is a small **buffer descriptor** that records:

- the **buffer tag**: which page is in the slot, as (tablespace, database, relfilenode, fork, block number);
- a **pin count** (`refcount`): how many processes are currently using the page. A pinned buffer can't be evicted;
- a **usage count** from 0 to 5, bumped every time someone pins the page (this is what makes popular pages stay);
- flags: **dirty** (modified in memory since it was read), valid, I/O in progress;
- a lightweight **content lock** that serialises readers and writers of the page's bytes.

To find a page, a backend computes the buffer tag, hashes it, and looks it up in the **buffer mapping table**, a shared hash table split into 128 partitions (each with its own lock, so lookups by different backends rarely collide). A hit returns the buffer number. A miss means the page has to be read from the file into some buffer, which first requires choosing a **victim**.

### 2.3 Clock sweep

PostgreSQL chooses victims with the **clock-sweep** algorithm, an approximation of "least recently used" that needs no global list to maintain on every access:

```text
            nextVictimBuffer
                   |
                   v
   ...  [buf 7: usage 2] [buf 8: usage 0, pinned] [buf 9: usage 0] [buf 10: usage 4] ...

   loop:
     look at the buffer under the hand
       pinned?              -> skip it, advance
       usage_count > 0 ?    -> usage_count -= 1, advance   ("second chance")
       usage_count = 0 ?    -> VICTIM: if dirty, write it out first, then reuse the slot
```

A page that is used constantly keeps getting its usage count bumped back up and survives many sweeps. A page read once and never again decays to zero after at most five passes of the hand and is replaced.

If the victim is **dirty**, its contents must reach the data file before the slot can be reused. If the backend running your query has to do that write itself, your query waits for it. The **background writer** exists to prevent this. Every `bgwriter_delay` (200 ms) it looks ahead of the clock hand and writes dirty buffers that are likely to be evicted soon, so that the backends find clean victims. Module 17 shows how to see whether this works (`pg_stat_io` counts writes by backend type).

### 2.4 Ring buffers: protecting the cache from big scans

A sequential scan of a table larger than the whole cache would, with plain clock sweep, push every useful page out of the cache while reading pages that are needed only once. PostgreSQL protects the cache with **buffer access strategies**, called **rings**. Certain operations reuse a small private set of buffers in a loop instead of claiming new ones:

| Operation | Ring size |
|---|---|
| Sequential scan of a table larger than ¼ of `shared_buffers` (`BULKREAD`) | 256 KB (32 buffers) |
| `VACUUM` and autovacuum (`VACUUM` strategy) | `vacuum_buffer_usage_limit`, default 2 MB |
| `COPY FROM`, `CREATE TABLE AS`, `CREATE INDEX` writes, other bulk writes (`BULKWRITE`) | 16 MB |

The consequence surprises people: scanning a big table twice does *not* make the second run fast because of `shared_buffers`. Only the operating system's page cache can help (next subsection). You can watch the ring at work with `pg_buffercache`, which shows one row per buffer. `orders` (about 55 MB) is larger than ¼ of the default 128 MB pool; `inventory` (about 10 MB) is smaller. We first evict any of their pages that happen to be cached (`pg_buffercache_evict` is new in PG 17 and meant for exactly this kind of experiment), switch off parallel query so that only one process scans, and read both tables in full. (We use `sum()` of a column rather than `count(*)`, because the planner would answer `count(*)` from a small index without reading the table.)

```sql
SELECT count(pg_buffercache_evict(b.bufferid)) AS evicted
FROM pg_buffercache b
WHERE b.relfilenode IN (pg_relation_filenode('orders'), pg_relation_filenode('inventory'));

SET max_parallel_workers_per_gather = 0;
SELECT sum(total) FROM orders;
SELECT sum(quantity) FROM inventory;

SELECT c.relname,
       pg_size_pretty(pg_relation_size(c.oid)) AS table_size,
       count(b.bufferid) AS cached_buffers,
       pg_size_pretty(count(b.bufferid) * 8192) AS cached
FROM pg_class c
LEFT JOIN pg_buffercache b
       ON b.relfilenode = pg_relation_filenode(c.oid)
      AND b.reldatabase = (SELECT oid FROM pg_database WHERE datname = current_database())
WHERE c.oid IN ('orders'::regclass, 'inventory'::regclass)
GROUP BY c.oid, c.relname;
```

```text
 evicted 
---------
    1274
(1 row)

SET
     sum      
--------------
 307345457.39
(1 row)

   sum    
----------
 17351439
(1 row)

  relname  | table_size | cached_buffers |  cached  
-----------+------------+----------------+----------
 inventory | 10192 kB   |           1274 | 10192 kB
 orders    | 55 MB      |             32 | 256 kB
(2 rows)
```

After reading all of `orders`, only about 256 kB of it is cached: the scan cycled through its 32-buffer ring. `inventory` was read through the normal buffer pool and is now cached in full. A second scan of `orders` again reads (almost) every page "from disk":

```sql
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF, TIMING OFF, SUMMARY OFF)
SELECT sum(total) FROM orders;
RESET max_parallel_workers_per_gather;
```

```text
                      QUERY PLAN                       
-------------------------------------------------------
 Aggregate (actual rows=1 loops=1)
   Buffers: shared hit=32 read=7063
   I/O Timings: shared read=9.452
   ->  Seq Scan on orders (actual rows=500000 loops=1)
         Buffers: shared hit=32 read=7063
         I/O Timings: shared read=9.452
(6 rows)

RESET
```

`shared read` is the number of pages that were not in the buffer pool. Whether those reads were slow is a different question, answered next.

### 2.5 Double buffering and the OS page cache

PostgreSQL reads and writes its files with ordinary buffered system calls (`pread`, `pwrite`). The Linux kernel keeps its own cache of file pages, the **page cache**, in all memory not used by processes. So a page can exist twice: once in `shared_buffers` and once in the kernel's page cache. A "miss" in shared buffers (`shared read` in `EXPLAIN (BUFFERS)`) is very often served from the kernel's cache in microseconds, without touching the disk.

This **double buffering** is why the usual advice is to give `shared_buffers` about 25% of RAM rather than 80%: a huge `shared_buffers` duplicates what the kernel caches anyway, and leaves the kernel less room. The planner is told about the total cache available (PostgreSQL's plus the kernel's) through `effective_cache_size`, a pure planning hint that allocates nothing. Module 17 turns this into sizing rules.

> **Under the hood:** `EXPLAIN (ANALYZE, BUFFERS)` reports `shared hit` (found in the buffer pool), `shared read` (not in the pool: read through the kernel, which may or may not have touched the disk), `dirtied` (this query modified a clean page) and `written` (this query had to write a dirty page to free a buffer). With `track_io_timing = on`, which the course container enables, `I/O Timings` tells you how long those reads really took, and that is the only way to tell a kernel-cache hit from a disk read.

---

## 3. Where the data lives on disk

### 3.1 A tour of PGDATA

The **data directory** (`PGDATA`; in the official Docker image it is `/var/lib/postgresql/data`) holds everything a cluster has: every database, the WAL, and in the Docker image also the configuration files.

```bash
docker exec course-pg ls /var/lib/postgresql/data
```

```text
base
global
pg_commit_ts
pg_dynshmem
pg_hba.conf
pg_ident.conf
pg_logical
pg_multixact
pg_notify
pg_replslot
pg_serial
pg_snapshots
pg_stat
pg_stat_tmp
pg_subtrans
pg_tblspc
pg_twophase
PG_VERSION
pg_wal
pg_xact
postgresql.auto.conf
postgresql.conf
postmaster.opts
postmaster.pid
```

The important entries:

| Entry | Contents |
|---|---|
| `base/` | One subdirectory per database, named by the database's OID. Table and index files live here. |
| `global/` | Cluster-wide catalogs shared by all databases (`pg_database`, `pg_authid` for roles, ...) and `pg_control`, the file that records the cluster's state and the location of the last checkpoint. |
| `pg_wal/` | The write-ahead log, as 16 MB segment files (section 9). Never delete files here by hand. |
| `pg_xact/` | The commit log: two bits of status for every transaction ID (section 6). |
| `pg_multixact/` | Multixact state, used when several transactions lock the same row (section 11). |
| `pg_subtrans/` | Parent links for subtransactions (savepoints). |
| `pg_stat/` | The saved cumulative statistics, written at clean shutdown (section 12). |
| `pg_tblspc/` | Symbolic links to tablespaces that live in other directories. |
| `pg_replslot/`, `pg_logical/` | Replication slots and logical decoding state (Module 19). |
| `PG_VERSION` | The major version, so a PostgreSQL 18 binary refuses to start on a PostgreSQL 17 directory. |
| `postmaster.pid` | The lock file of the running server: PID, data directory, port, shared-memory key. |
| `postgresql.conf`, `postgresql.auto.conf`, `pg_hba.conf`, `pg_ident.conf` | Configuration (Modules 15 and 17). |

### 3.2 From a table name to a file

Every table, index, sequence, materialized view and TOAST table is a **relation**, and its data lives in files named after its **relfilenode** inside the directory of its database. `pg_relation_filepath` assembles the path for you:

```sql
SELECT oid AS db_oid, datname FROM pg_database WHERE datname = current_database();

SELECT c.oid, c.relfilenode, pg_relation_filepath(c.oid) AS path
FROM pg_class c
WHERE c.oid IN ('orders'::regclass, 'orders_pkey'::regclass);
```

```text
 db_oid | datname 
--------+---------
  32848 | bazaar
(1 row)

  oid  | relfilenode |       path       
-------+-------------+------------------
 32939 |       33891 | base/32848/33891
 32948 |       33892 | base/32848/33892
(2 rows)
```

The **OID** identifies the relation in the catalogs and never changes. The **relfilenode** names the file and starts out equal to the OID, but any operation that rewrites the relation into a fresh file gives it a new relfilenode: `TRUNCATE`, `VACUUM FULL`, `CLUSTER`, `REINDEX`, and `ALTER TABLE` variants that rewrite the table (such as changing a column's type). The rewrite is written into a new file, the catalog is switched to it at commit, and the old file is deleted. That is how these commands stay transactional: if you roll back, the catalog still points to the old file.

```sql
CREATE TABLE lab.rewrite_demo (id int);
SELECT relfilenode FROM pg_class WHERE oid = 'lab.rewrite_demo'::regclass;
TRUNCATE lab.rewrite_demo;
SELECT relfilenode FROM pg_class WHERE oid = 'lab.rewrite_demo'::regclass;
```

```text
CREATE TABLE
 relfilenode 
-------------
       34631
(1 row)

TRUNCATE TABLE
 relfilenode 
-------------
       34634
(1 row)
```

> **Under the hood:** the reverse lookup, from a file name you found on disk to a relation, is `pg_filenode_relation(tablespace_oid, relfilenode)`, with `0` meaning the database's default tablespace. Handy when a disk-usage tool tells you that `base/16384/24601` is huge.

### 3.3 Forks and segments

A relation is not one file but up to four **forks**, each a separate file:

| Fork | File suffix | Contents |
|---|---|---|
| main | (none) | The actual pages of rows or index entries. |
| free space map (FSM) | `_fsm` | For each heap page, roughly how much free space it has, so `INSERT` and non-HOT `UPDATE` can find a page with room without scanning the table. Maintained by VACUUM. |
| visibility map (VM) | `_vm` | Two bits per heap page: *all-visible* (every tuple on the page is visible to every transaction) and *all-frozen* (every tuple is frozen). VACUUM uses it to skip pages, and index-only scans use it to skip heap visits (section 10). |
| initialization | `_init` | Only for `UNLOGGED` relations: an empty copy that replaces the main fork after a crash. |

Large forks are split into **segments** of 1 GB: the first is `16450`, the next `16450.1`, then `16450.2`, and so on. This dates from file systems that could not hold files larger than 2 GB and is fixed at compile time.

```bash
docker exec course-pg sh -c 'cd /var/lib/postgresql/data && ls -l $(psql -U postgres -d bazaar -Atc "SELECT pg_relation_filepath('"'"'orders'"'"')")*'
```

```text
-rw------- 1 postgres postgres 58122240 Oct 14 08:04 base/32848/33891
-rw------- 1 postgres postgres    32768 Oct 14 08:04 base/32848/33891_fsm
-rw------- 1 postgres postgres     8192 Oct 14 08:04 base/32848/33891_vm
```

You don't need to look at files to get sizes. These functions do the arithmetic and include all segments:

```sql
SELECT pg_size_pretty(pg_relation_size('orders'))           AS main,
       pg_size_pretty(pg_relation_size('orders', 'fsm'))    AS fsm,
       pg_size_pretty(pg_relation_size('orders', 'vm'))     AS vm,
       pg_size_pretty(pg_table_size('orders'))              AS table_incl_toast,
       pg_size_pretty(pg_indexes_size('orders'))            AS indexes,
       pg_size_pretty(pg_total_relation_size('orders'))     AS total;
```

```text
 main  |  fsm  |     vm     | table_incl_toast | indexes | total  
-------+-------+------------+------------------+---------+--------
 55 MB | 32 kB | 8192 bytes | 55 MB            | 46 MB   | 102 MB
(1 row)
```

`pg_table_size` covers all forks plus the TOAST table (section 8), `pg_indexes_size` all indexes, and `pg_total_relation_size` both. Unlogged tables show their `_init` fork too:

```sql
CREATE UNLOGGED TABLE lab.scratch_cache (k text PRIMARY KEY, v jsonb);
SELECT pg_relation_filepath('lab.scratch_cache'),
       pg_relation_size('lab.scratch_cache', 'init') AS init_fork_bytes;
```

```text
CREATE TABLE
 pg_relation_filepath | init_fork_bytes 
----------------------+-----------------
 base/32848/34635     |               0
(1 row)
```

> **Pitfall:** an `UNLOGGED` table skips the WAL, which makes writes much faster, but after a crash PostgreSQL cannot know whether its pages are consistent, so it replaces the main fork with the empty `_init` fork. **Every row is gone after a crash.** Unlogged tables are also not replicated. Use them only for data you can rebuild, like caches or staging tables.

---

## 4. Inside an 8 KB page

### 4.1 The layout

Every page of every table (the **heap**, PostgreSQL's name for table storage) has the same layout:

```text
 offset 0                                                                 8192
 +--------------+------+------+------+---------------------+--------+--------+--------+---------+
 | page header  | lp 1 | lp 2 | lp 3 |  ... free space ... | tuple3 | tuple2 | tuple1 | special |
 |  (24 bytes)  |  4 B |  4 B |  4 B |                     |        |        |        | (index) |
 +--------------+------+------+------+---------------------+--------+--------+--------+---------+
                                     ^                     ^                          ^
                                 pd_lower              pd_upper                  pd_special
```

- The **page header** (24 bytes) records, among other things, the **LSN** of the last WAL record that changed this page (section 9), a checksum (if data checksums are enabled), and three offsets: `pd_lower` (end of the line-pointer array), `pd_upper` (start of the tuple data), and `pd_special` (start of the special space).
- The **line pointers** (also called *item identifiers*), 4 bytes each, grow forwards from the header. Each stores the offset and length of one tuple and a 2-bit state: `1` normal, `2` redirect (HOT, section 7), `3` dead, `0` unused.
- The **tuples** grow backwards from the end of the page. The free space is the gap between `pd_lower` and `pd_upper`.
- The **special space** at the end is empty for tables. Index pages use it (a B-tree page stores its left and right sibling links there, Module 11).

The indirection through line pointers is what makes a **tuple identifier** (TID, also called `ctid`) stable: `(page 0, item 3)` means "line pointer 3 of page 0". PostgreSQL can move a tuple's bytes inside the page (to defragment free space) without changing its TID, so indexes that point to `(0,3)` stay correct.

### 4.2 Reading a page with pageinspect

Let's create a tiny table and read its first page byte by byte:

```sql
CREATE TABLE lab.page_demo (id int PRIMARY KEY, label text);
INSERT INTO lab.page_demo VALUES (1, 'apple'), (2, 'banana'), (3, 'cherry');

SELECT * FROM page_header(get_raw_page('lab.page_demo', 0));
```

```text
CREATE TABLE
INSERT 0 3
    lsn     | checksum | flags | lower | upper | special | pagesize | version | prune_xid 
------------+----------+-------+-------+-------+---------+----------+---------+-----------
 1/BB97ECD8 |        0 |     0 |    36 |  8072 |    8192 |     8192 |       4 |         0
(1 row)
```

`get_raw_page(relation, block)` copies one 8 KB page into a `bytea`, and `page_header` decodes its header. Do the arithmetic:

- `lower = 36` = 24 bytes of header + 3 line pointers × 4 bytes.
- `upper = 8072`: the three tuples occupy bytes 8072 to 8191.
- `special = 8192` = the page size: tables have no special space.
- `upper - lower` = 8036 bytes are free.
- `lsn` is the WAL position of the last change to this page, and `checksum` is 0 because the Docker image initializes clusters without data checksums.

Now the line pointers and tuples:

```sql
SELECT lp, lp_off, lp_flags, lp_len, t_xmin, t_xmax, t_ctid, t_hoff, t_data
FROM heap_page_items(get_raw_page('lab.page_demo', 0));
```

```text
 lp | lp_off | lp_flags | lp_len | t_xmin | t_xmax | t_ctid | t_hoff |          t_data          
----+--------+----------+--------+--------+--------+--------+--------+--------------------------
  1 |   8152 |        1 |     34 |  49237 |      0 | (0,1)  |     24 | \x010000000d6170706c65
  2 |   8112 |        1 |     35 |  49237 |      0 | (0,2)  |     24 | \x020000000f62616e616e61
  3 |   8072 |        1 |     35 |  49237 |      0 | (0,3)  |     24 | \x030000000f636865727279
(3 rows)
```

Tuple 1 starts at offset 8152 and is 34 bytes long. Those 34 bytes are:

```text
 24 bytes  tuple header (23 bytes, padded to 24 = t_hoff)
  4 bytes  id     = 01 00 00 00         (the integer 1, little-endian)
  6 bytes  label  = 0d 61 70 70 6c 65   (1-byte length header 0x0d, then "apple")
 --
 34 bytes
```

The text value uses a 1-byte **short varlena header** because it is shorter than 127 bytes. Longer values get a 4-byte header. Tuple 1 is placed at 8152, not at 8158 (= 8192 − 34), because tuples start on an 8-byte boundary (**MAXALIGN**), so each tuple really consumes `34 → 40` bytes plus its 4-byte line pointer.

How many rows like this fit on a page? (8192 − 24) / (40 + 4) ≈ 185. Inserting a 186th row would start page 1. You will see exactly this number again in section 7.

---

## 5. The tuple header and alignment

### 5.1 The 23-byte header

Every heap tuple starts with a fixed 23-byte header:

| Field | Size | Meaning |
|---|---|---|
| `t_xmin` | 4 | ID of the transaction that inserted this version. |
| `t_xmax` | 4 | ID of the transaction that deleted or updated it (or locked it); 0 if none. |
| `t_cid` | 4 | Command ID within the inserting/deleting transaction: `cmin`/`cmax`. When a transaction both inserts and deletes the same tuple, both are folded into one "combo command ID". |
| `t_ctid` | 6 | TID of this tuple, or of its **newer version** after an `UPDATE`. |
| `t_infomask2` | 2 | Number of attributes, plus flags: `HEAP_HOT_UPDATED`, `HEAP_ONLY_TUPLE`, `HEAP_KEYS_UPDATED`. |
| `t_infomask` | 2 | Flags: has nulls, has variable-width columns, and the **hint bits** (section 6.4), lock flags, `XMAX_IS_MULTI`, ... |
| `t_hoff` | 1 | Offset to the user data = header + null bitmap, rounded up to 8. |

Several of these are visible as **system columns** on any table. You just have to name them, because `SELECT *` does not show them:

```sql
SELECT ctid, xmin, xmax, cmin, cmax, * FROM lab.page_demo;
```

```text
 ctid  | xmin  | xmax | cmin | cmax | id | label  
-------+-------+------+------+------+----+--------
 (0,1) | 49237 |    0 |    0 |    0 |  1 | apple
 (0,2) | 49237 |    0 |    0 |    0 |  2 | banana
 (0,3) | 49237 |    0 |    0 |    0 |  3 | cherry
(3 rows)
```

`pageinspect` can translate the infomask bits into names:

```sql
SELECT lp, t_infomask, t_infomask2, raw_flags
FROM heap_page_items(get_raw_page('lab.page_demo', 0)),
     LATERAL heap_tuple_infomask_flags(t_infomask, t_infomask2);
```

```text
 lp | t_infomask | t_infomask2 |                        raw_flags                         
----+------------+-------------+----------------------------------------------------------
  1 |       2306 |           2 | {HEAP_HASVARWIDTH,HEAP_XMIN_COMMITTED,HEAP_XMAX_INVALID}
  2 |       2306 |           2 | {HEAP_HASVARWIDTH,HEAP_XMIN_COMMITTED,HEAP_XMAX_INVALID}
  3 |       2306 |           2 | {HEAP_HASVARWIDTH,HEAP_XMIN_COMMITTED,HEAP_XMAX_INVALID}
(3 rows)
```

`HEAP_HASVARWIDTH` says the row contains a variable-width column (`label`). `HEAP_XMAX_INVALID` says "there is no deleter". Notice the new flag `HEAP_XMIN_COMMITTED` that was not there before the `SELECT` in the previous step. That's a **hint bit**, and section 6.4 explains who set it and why.

### 5.2 The null bitmap

NULLs take no space in the data area. Instead, if any column of a row is NULL, the tuple gets a **null bitmap** right after the 23-byte header, with one bit per column (1 = not null):

```sql
INSERT INTO lab.page_demo VALUES (4, NULL);
SELECT lp, lp_len, t_hoff, t_bits, t_data
FROM heap_page_items(get_raw_page('lab.page_demo', 0))
WHERE lp = 4;
```

```text
INSERT 0 1
 lp | lp_len | t_hoff |  t_bits  |   t_data   
----+--------+--------+----------+------------
  4 |     28 |     24 | 10000000 | \x04000000
(1 row)
```

The bitmap `10000000` says column 1 is present and column 2 is NULL. The tuple is 28 bytes: header plus bitmap (still fits in 24 bytes because 23 + 1 = 24) and a 4-byte integer. A table with 9 to 64 columns needs a bitmap of 2 to 8 bytes, which pushes `t_hoff` to 32. A row without any NULL has no bitmap at all.

### 5.3 Alignment and column-order Tetris

Each data type has an **alignment** requirement (`pg_type.typalign`): `c` = any byte, `s` = 2 bytes, `i` = 4 bytes, `d` = 8 bytes. A value must start at an offset that is a multiple of its alignment (measured from the start of the tuple's data), so PostgreSQL inserts **padding** bytes before it if needed. Fixed-width types like `bigint` and `timestamptz` are `d`-aligned; `int`, `date`, and `text`/`jsonb`/`numeric` with a 4-byte header are `i`-aligned; `smallint` is `s`; `boolean`, `char` and `uuid` are `c`. (Short varlena values with a 1-byte header are not aligned at all, which is a nice saving for short strings.)

So column order matters. `pg_column_size` of a row value shows the effect directly:

```sql
SELECT pg_column_size(ROW(true, 1::bigint, true, 1::bigint))  AS bool_big_bool_big,
       pg_column_size(ROW(1::bigint, 1::bigint, true, true))  AS big_big_bool_bool;
```

```text
 bool_big_bool_big | big_big_bool_bool 
-------------------+-------------------
                56 |                42
(1 row)
```

In the first order each `boolean` is followed by 7 bytes of padding so the next `bigint` can start at a multiple of 8. In the second order the two booleans sit together at the end. (The numbers include a 24-byte row header.) On a table with millions of rows the difference is real disk, real cache, and real I/O:

```sql
CREATE TABLE lab.layout_bad (
    is_paid    boolean,
    id         bigint,
    is_gift    boolean,
    created_at timestamptz,
    qty        smallint,
    amount_ct  bigint,
    seller_id  int
);
CREATE TABLE lab.layout_good (
    id         bigint,
    created_at timestamptz,
    amount_ct  bigint,
    seller_id  int,
    qty        smallint,
    is_paid    boolean,
    is_gift    boolean
);
INSERT INTO lab.layout_bad
SELECT true, g, false, now(), 1, g * 100, g % 1000 FROM generate_series(1, 1000000) g;
INSERT INTO lab.layout_good
SELECT g, now(), g * 100, g % 1000, 1, true, false FROM generate_series(1, 1000000) g;

SELECT relname,
       pg_size_pretty(pg_relation_size(oid)) AS size,
       (SELECT pg_column_size(t.*) FROM lab.layout_bad t LIMIT 1)  AS bad_row,
       (SELECT pg_column_size(t.*) FROM lab.layout_good t LIMIT 1) AS good_row
FROM pg_class
WHERE oid IN ('lab.layout_bad'::regclass, 'lab.layout_good'::regclass);
```

```text
CREATE TABLE
CREATE TABLE
INSERT 0 1000000
INSERT 0 1000000
   relname   | size  | bad_row | good_row 
-------------+-------+---------+----------
 layout_bad  | 81 MB |      76 |       56
 layout_good | 57 MB |      76 |       56
(2 rows)
```

Same data, same columns, 30% less space, just by ordering columns from the widest alignment to the narrowest: 8-byte types first, then 4, then 2, then 1-byte and variable-width columns last.

How do Bazaar's tables fare? This query lists the columns of `orders` with their storage length and alignment:

```sql
SELECT a.attnum, a.attname, t.typname, t.typlen, t.typalign
FROM pg_attribute a
JOIN pg_type t ON t.oid = a.atttypid
WHERE a.attrelid = 'orders'::regclass AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum;
```

```text
 attnum |       attname       |   typname    | typlen | typalign 
--------+---------------------+--------------+--------+----------
      1 | id                  | int8         |      8 | d
      2 | user_id             | int8         |      8 | d
      3 | seller_id           | int8         |      8 | d
      4 | status              | order_status |      4 | i
      5 | total               | numeric      |     -1 | i
      6 | created_at          | timestamptz  |      8 | d
      7 | paid_at             | timestamptz  |      8 | d
      8 | shipped_at          | timestamptz  |      8 | d
      9 | delivered_at        | timestamptz  |      8 | d
     10 | shipping_address_id | int8         |      8 | d
     11 | updated_at          | timestamptz  |      8 | d
(11 rows)
```

After the three `bigint`s, `status` (an enum, 4 bytes) and `total` (a `numeric`, usually 5 to 8 bytes with a short header) are followed by `created_at`, which must start on an 8-byte boundary, so a few bytes of padding go in between. Moving `status` and `total` to the end would avoid it. Is it worth it? Measure: compare each row's actual size with the size of the same values in the better order, both rounded up to the tuple's 8-byte alignment, which is what really takes space on the page:

```sql
SELECT avg(pg_column_size(o.*))::numeric(6, 2) AS avg_as_defined,
       avg(pg_column_size(ROW(o.id, o.user_id, o.seller_id, o.created_at, o.paid_at, o.shipped_at,
                              o.delivered_at, o.shipping_address_id, o.updated_at,
                              o.status, o.total)))::numeric(6, 2) AS avg_reordered,
       sum(ceil(pg_column_size(o.*) / 8.0) * 8) AS bytes_as_defined,
       sum(ceil(pg_column_size(ROW(o.id, o.user_id, o.seller_id, o.created_at, o.paid_at, o.shipped_at,
                                   o.delivered_at, o.shipping_address_id, o.updated_at,
                                   o.status, o.total)) / 8.0) * 8) AS bytes_reordered
FROM orders o;
```

```text
 avg_as_defined | avg_reordered | bytes_as_defined | bytes_reordered 
----------------+---------------+------------------+-----------------
         111.05 |        106.05 |         55523648 |        55523648
(1 row)
```

Reordering would save about 5 bytes per row, but those bytes vanish into the final rounding of each tuple to a multiple of 8, so on disk it would save nothing at all. `orders` is fine as it is. Padding matters when it pushes a row over an 8-byte boundary, and most of all on narrow tables, where a few bytes are a large fraction of the row: `lab.layout_bad` above.

> **In production:** don't rewrite a big table only to reorder columns. PostgreSQL has no `ALTER TABLE ... MOVE COLUMN`, so it means building a new table and copying the data. Get the order right when you *design* a table, especially for narrow, very tall tables (event streams, line items, join tables), where the 24-byte header plus padding can be half the row. Also, columns added later with `ALTER TABLE ... ADD COLUMN` always go at the end.

---

## 6. MVCC, precisely

Module 10 described MVCC as "readers see a snapshot, writers create new versions". Now we can say exactly what that means in terms of the bytes you just saw.

### 6.1 Transaction IDs

Every transaction that **writes** gets a 32-bit **transaction ID** (XID), assigned lazily at its first write. Read-only transactions never consume one; they get a "virtual" transaction ID that lives only in memory. `pg_current_xact_id()` returns the XID (and assigns one if needed), `pg_current_xact_id_if_assigned()` returns NULL for a transaction that has not written yet:

```sql
BEGIN;
SELECT pg_current_xact_id_if_assigned() AS before_write;
INSERT INTO lab.page_demo VALUES (5, 'damson');
SELECT pg_current_xact_id_if_assigned() AS after_write;
COMMIT;
```

```text
BEGIN
 before_write 
--------------
             
(1 row)

INSERT 0 1
 after_write 
-------------
       49243
(1 row)

COMMIT
```

The SQL-level functions return `xid8`, a 64-bit value that includes an "epoch" counter, so it never wraps. On disk, however, tuple headers store only the low 32 bits, and section 11 is about the consequences.

### 6.2 Snapshots

A **snapshot** answers one question: *which transactions' effects may I see?* It is taken from the shared proc array and has three parts:

- `xmin`: the oldest XID that was still running. Everything older has finished (committed or aborted).
- `xmax`: one past the newest XID that had *finished*. Everything at or after it is treated as still running, so it is invisible.
- `xip`: the XIDs between `xmin` and `xmax` that were still running at snapshot time.

`pg_current_snapshot()` shows it as `xmin:xmax:xip1,xip2,...`. With two sessions you can watch a running transaction appear in the `xip` list:

```sql
-- Session A
BEGIN;
UPDATE lab.page_demo SET label = 'apricot' WHERE id = 1;
SELECT pg_current_xact_id();
```

Meanwhile some other transaction starts and commits. We simulate one in session B: in autocommit mode, `pg_current_xact_id()` assigns an XID and the statement commits immediately. Then B takes a snapshot and reads the row:

```sql
-- Session B
SELECT pg_current_xact_id() AS other_xid;
SELECT pg_current_snapshot();
SELECT ctid, xmin, xmax, * FROM lab.page_demo WHERE id = 1;
```

```text
 session_a_xid 
---------------
         49244
(1 row)
 other_xid 
-----------
     49245
(1 row)

 pg_current_snapshot 
---------------------
 49244:49246:49244
(1 row)

 ctid  | xmin  | xmax  | id | label 
-------+-------+-------+----+-------
 (0,1) | 49237 | 49244 |  1 | apple
(1 row)
```

Read B's snapshot: `xmin` is A's XID (the oldest one still running), `xmax` is one past the other transaction's XID (the newest finished one), and `xip` lists A's XID as in progress. So B ignores A's new version and still sees the old one. Notice that the old version, the one B sees, already carries A's XID in `xmax`: A has "deleted" it, but A has not committed, so for B the deletion doesn't count yet. Finish session A with a rollback (we'll look at what a rollback leaves behind in section 6.5):

```sql
-- Session A
ROLLBACK;
```

Under `READ COMMITTED` a new snapshot is taken for every statement. Under `REPEATABLE READ` and `SERIALIZABLE` one snapshot is taken at the first statement and kept for the whole transaction (Module 10). That is the entire difference in visibility between the levels.

> **Under the hood:** taking a snapshot means scanning the proc array, whose size is `max_connections`. PostgreSQL 14 made this much cheaper, but every connection slot, even an idle one, still costs a little on every snapshot. This is one of the reasons thousands of connections hurt even when they do nothing.

### 6.3 The visibility rules

Given a tuple with `xmin` and `xmax` and a snapshot, the tuple is **visible** if both of these hold:

1. **The inserter is visible:** `xmin` is my own transaction (and the row was inserted by an earlier command than the current one, which is what `cmin` is for), **or** `xmin` committed **and** it is not "in progress" from my snapshot's point of view (`xmin < snapshot.xmax` and `xmin` not in `xip`).
2. **The deleter is not visible:** `xmax` is 0 (invalid), **or** `xmax` aborted, **or** `xmax` is still in progress for my snapshot (in `xip`, or `≥ snapshot.xmax`), **or** `xmax` is my own transaction but the delete happened in a later command (`cmax`), **or** `xmax` only *locked* the row (`SELECT ... FOR UPDATE` stores the locker in `xmax` with a lock-only flag).

Rule 1 needs to know whether `xmin` **committed or aborted**. That information isn't in the tuple. It lives in the commit log.

### 6.4 The commit log and hint bits

The **commit log** (directory `pg_xact`, historically called CLOG) stores 2 bits per transaction ID: *in progress*, *committed*, *aborted*, or *sub-committed* (for subtransactions). 8 KB of commit log covers 32,768 transactions. Committing a transaction is, at its core, two things: writing a commit record to the WAL and flipping those 2 bits. Nothing in the table is touched at commit time. That is why commit is cheap no matter how many rows the transaction wrote, and why `ROLLBACK` is cheap too: the aborted rows simply stay where they are, invisible, until VACUUM removes them.

`pg_xact_status()` reads the commit log for you:

```sql
SELECT xmin, pg_xact_status(xmin::text::xid8) FROM lab.page_demo WHERE id = 5;
```

```text
 xmin  | pg_xact_status 
-------+----------------
 49243 | committed
(1 row)
```

Looking up the commit log for every tuple on every read would be expensive (it has its own small cache in shared memory, sized by `transaction_buffers` in PG 17). So the first process that looks up a tuple's `xmin` or `xmax` and finds it finished **writes the answer into the tuple header** as a **hint bit**: `HEAP_XMIN_COMMITTED`, `HEAP_XMIN_INVALID` (aborted), `HEAP_XMAX_COMMITTED`, `HEAP_XMAX_INVALID`. Every later reader trusts the hint and skips the lookup. That is the `HEAP_XMIN_COMMITTED` flag that appeared in section 5.1 after our first `SELECT`.

Hint bits have a surprising side effect: **reading can write.** Setting a hint bit dirties the page, so the first read of freshly loaded data marks every page dirty, and those pages must eventually be written back to disk. Watch the `dirtied` counter:

```sql
CREATE TABLE lab.hint_demo AS SELECT g AS id FROM generate_series(1, 100000) g;
CHECKPOINT;   -- write the new pages to disk, so that they are clean in the buffer pool
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF, TIMING OFF) SELECT count(*) FROM lab.hint_demo;
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF, TIMING OFF) SELECT count(*) FROM lab.hint_demo;
```

```text
SELECT 100000
CHECKPOINT
                        QUERY PLAN                        
----------------------------------------------------------
 Aggregate (actual rows=1 loops=1)
   Buffers: shared hit=448 dirtied=443
   ->  Seq Scan on hint_demo (actual rows=100000 loops=1)
         Buffers: shared hit=448 dirtied=443
 Planning:
   Buffers: shared hit=5 read=1
   I/O Timings: shared read=0.006
 Planning Time: 0.088 ms
 Execution Time: 6.974 ms
(9 rows)

                        QUERY PLAN                        
----------------------------------------------------------
 Aggregate (actual rows=1 loops=1)
   Buffers: shared hit=448
   ->  Seq Scan on hint_demo (actual rows=100000 loops=1)
         Buffers: shared hit=448
 Planning Time: 0.042 ms
 Execution Time: 10.051 ms
(6 rows)
```

(The `CHECKPOINT` matters for the demo: right after the `CREATE TABLE AS`, the new pages are still dirty in the buffer pool, and `dirtied` only counts pages that go from clean to dirty.) The first `count(*)` dirtied every page of the table just by reading it. The second found the hints already there. This is why a big `COPY` is often followed by a surprisingly write-heavy first query, and why running `VACUUM` (which sets hint bits and more) after a bulk load is good practice.

> **Under the hood:** hint-bit changes are normally not WAL-logged: losing one in a crash is harmless, because the next reader just consults the commit log again. With data checksums enabled (or `wal_log_hints = on`), a hint-bit change must be protected against torn pages, so the first hint-bit change to a page after a checkpoint writes a full-page image to the WAL (section 9).

### 6.5 Rollback leaves traces

Let's delete a row and roll back:

```sql
BEGIN;
DELETE FROM lab.page_demo WHERE id = 2;
ROLLBACK;

SELECT lp, t_xmax, raw_flags
FROM heap_page_items(get_raw_page('lab.page_demo', 0)),
     LATERAL heap_tuple_infomask_flags(t_infomask, t_infomask2)
WHERE lp = 2;
SELECT * FROM lab.page_demo WHERE id = 2;
SELECT lp, t_xmax, raw_flags
FROM heap_page_items(get_raw_page('lab.page_demo', 0)),
     LATERAL heap_tuple_infomask_flags(t_infomask, t_infomask2)
WHERE lp = 2;
```

```text
BEGIN
DELETE 1
ROLLBACK
 lp | t_xmax |                        raw_flags                         
----+--------+----------------------------------------------------------
  2 |  49247 | {HEAP_HASVARWIDTH,HEAP_XMIN_COMMITTED,HEAP_KEYS_UPDATED}
(1 row)

 id | label  
----+--------
  2 | banana
(1 row)

 lp | t_xmax |                                 raw_flags                                  
----+--------+----------------------------------------------------------------------------
  2 |  49247 | {HEAP_HASVARWIDTH,HEAP_XMIN_COMMITTED,HEAP_XMAX_INVALID,HEAP_KEYS_UPDATED}
(1 row)
```

After the rollback, the tuple still has `xmax` set to the aborted transaction. (`HEAP_KEYS_UPDATED` is how a `DELETE` marks that the row's key is gone, which matters for foreign-key locks.) The row is visible because that transaction aborted (rule 2). The next reader checks the commit log, finds "aborted", and sets `HEAP_XMAX_INVALID`, so nobody needs to check again.

---

## 7. UPDATE on disk, and HOT updates

### 7.1 An UPDATE is an INSERT plus a DELETE

PostgreSQL never overwrites a row in place. An `UPDATE`:

1. writes a **new tuple version** with `xmin` = the updating transaction;
2. sets `xmax` of the old version to the same transaction;
3. sets the old version's `t_ctid` to the TID of the new version, forming an **update chain**;
4. inserts an entry pointing to the new TID into **every index** of the table (unless the update is HOT, see below).

The old version stays on disk until no snapshot can need it and VACUUM (or page pruning) removes it. That old version is a **dead tuple**, and accumulated dead tuples are what people call **bloat** (Module 17).

```sql
UPDATE lab.page_demo SET label = 'blueberry' WHERE id = 2;
SELECT lp, t_xmin, t_xmax, t_ctid, t_data
FROM heap_page_items(get_raw_page('lab.page_demo', 0));
```

```text
UPDATE 1
 lp | t_xmin | t_xmax | t_ctid |             t_data             
----+--------+--------+--------+--------------------------------
  1 |  49237 |  49244 | (0,6)  | \x010000000d6170706c65
  2 |  49237 |  49248 | (0,7)  | \x020000000f62616e616e61
  3 |  49237 |      0 | (0,3)  | \x030000000f636865727279
  4 |  49238 |      0 | (0,4)  | \x04000000
  5 |  49243 |      0 | (0,5)  | \x050000000f64616d736f6e
  6 |  49244 |      0 | (0,6)  | \x010000001161707269636f74
  7 |  49248 |      0 | (0,7)  | \x0200000015626c75656265727279
(7 rows)
```

Line pointer 2 (the old "banana") now has `xmax` set and `t_ctid = (0,6)`, pointing to the new "blueberry" version in line pointer 6.

### 7.2 Heap-only tuples (HOT)

Step 4 is the expensive part. A table with five indexes turns a one-column update into six writes, plus five index entries that will later need cleanup, even when the changed column is not indexed at all. **HOT (heap-only tuple) updates** remove that cost. An update is HOT if:

- **no indexed column changed** (in PG 16 and later, columns used only by "summarizing" indexes such as BRIN don't count), and
- **the new version fits on the same page** as the old one.

Then PostgreSQL writes the new version on the same page, marks the old one `HEAP_HOT_UPDATED` and the new one `HEAP_ONLY_TUPLE`, and **adds no index entries**. The indexes keep pointing at the old line pointer, the root of the chain. An index lookup lands on the root and follows `t_ctid` within the page to the version its snapshot can see.

Later, when a backend visits the page and finds both that the page is getting full (its free space has dropped below the larger of the fillfactor reserve and 10% of the page) and that old versions on it are dead to everyone, it **prunes** the page: it removes the dead tuples, turns the root line pointer into a **redirect** to the live version, and compacts the free space. Pruning happens during ordinary reads and writes, page by page, without VACUUM and without touching the indexes.

The second condition, "fits on the same page", is where **fillfactor** comes in. `fillfactor` (10 to 100, default 100 for tables) is the percentage of each page that `INSERT` fills. The rest is reserved for updates of rows on that page. With the default of 100, pages fill up completely, and an update of a row on a full page must go to another page, so it can't be HOT.

Let's compare. Two tables with identical rows, one with fillfactor 100 and one with 90:

```sql
CREATE TABLE lab.stock_ff100 (product_id int PRIMARY KEY, qty int NOT NULL, note text)
    WITH (fillfactor = 100);
CREATE TABLE lab.stock_ff90  (product_id int PRIMARY KEY, qty int NOT NULL, note text)
    WITH (fillfactor = 90);
INSERT INTO lab.stock_ff100 SELECT g, 10, 'x' FROM generate_series(1, 1000) g;
INSERT INTO lab.stock_ff90  SELECT g, 10, 'x' FROM generate_series(1, 1000) g;

UPDATE lab.stock_ff100 SET qty = qty - 1 WHERE product_id = 1;
UPDATE lab.stock_ff90  SET qty = qty - 1 WHERE product_id = 1;

SELECT 'ff100' AS t, lp, lp_flags, t_xmax, t_ctid, t_infomask2 & 16384 > 0 AS hot_updated,
       t_infomask2 & 32768 > 0 AS heap_only
FROM heap_page_items(get_raw_page('lab.stock_ff100', 0)) WHERE lp = 1
UNION ALL
SELECT 'ff90', lp, lp_flags, t_xmax, t_ctid, t_infomask2 & 16384 > 0, t_infomask2 & 32768 > 0
FROM heap_page_items(get_raw_page('lab.stock_ff90', 0)) WHERE lp IN (1, 168);
```

```text
CREATE TABLE
CREATE TABLE
INSERT 0 1000
INSERT 0 1000
UPDATE 1
UPDATE 1
   t   | lp  | lp_flags | t_xmax | t_ctid  | hot_updated | heap_only 
-------+-----+----------+--------+---------+-------------+-----------
 ff100 |   1 |        1 |  49253 | (5,76)  | f           | f
 ff90  |   1 |        1 |  49254 | (0,168) | t           | f
 ff90  | 168 |        1 |      0 | (0,168) | f           | t
(3 rows)
```

In `stock_ff100`, page 0 was full (185 rows, as computed in section 4), so the new version of product 1 went to another page: a normal update, with a new index entry. In `stock_ff90`, page 0 held only 167 rows and had room, so the new version landed in line pointer 168 of the same page: the old version is flagged `hot_updated`, the new one `heap_only`, and the primary-key index was not touched. (`16384` and `32768` are the bit values of `HEAP_HOT_UPDATED` and `HEAP_ONLY_TUPLE` in `t_infomask2`.)

Now read the row through the index, and look at the page again:

```sql
SELECT ctid, * FROM lab.stock_ff90 WHERE product_id = 1;
SELECT lp, lp_off, lp_flags, t_ctid
FROM heap_page_items(get_raw_page('lab.stock_ff90', 0)) WHERE lp IN (1, 168);
```

```text
  ctid   | product_id | qty | note 
---------+------------+-----+------
 (0,168) |          1 |   9 | x
(1 row)

 lp  | lp_off | lp_flags | t_ctid  
-----+--------+----------+---------
   1 |    168 |        2 | 
 168 |   1512 |        1 | (0,168)
(2 rows)
```

The index lookup triggered **pruning**: line pointer 1 is now a redirect (`lp_flags = 2`, and `lp_off` holds the target line pointer, 168), and the dead tuple's bytes are gone. The index still says "product 1 is at (0,1)", and the redirect forwards the lookup.

The cumulative statistics count HOT updates per table. Statistics are flushed from a backend's memory to shared memory at most once a second (section 12), so we force a flush first:

```sql
SELECT pg_stat_force_next_flush();
SELECT relname, n_tup_upd, n_tup_hot_upd
FROM pg_stat_user_tables
WHERE relname IN ('stock_ff100', 'stock_ff90');
```

```text
 pg_stat_force_next_flush 
--------------------------
 
(1 row)

   relname   | n_tup_upd | n_tup_hot_upd 
-------------+-----------+---------------
 stock_ff100 |         1 |             0
 stock_ff90  |         1 |             1
(2 rows)
```

What about Bazaar? Every checkout updates `inventory.reserved` (Module 10's `place_order()`), a column that has no index, so those updates *could* be HOT. Let's simulate a burst of reservations and roll it back, so the data doesn't change (the statistics count rolled-back updates as well):

```sql
SELECT pg_stat_reset_single_table_counters('inventory'::regclass);  -- count only this burst
BEGIN;
UPDATE inventory SET reserved = reserved + 1
WHERE product_id <= 2000 AND reserved < quantity;
ROLLBACK;
SELECT pg_stat_force_next_flush();
SELECT n_tup_upd, n_tup_hot_upd FROM pg_stat_user_tables WHERE relname = 'inventory';
```

```text
 pg_stat_reset_single_table_counters 
-------------------------------------
 
(1 row)

BEGIN
UPDATE 1723
ROLLBACK
 pg_stat_force_next_flush 
--------------------------
 
(1 row)

 n_tup_upd | n_tup_hot_upd 
-----------+---------------
      1723 |             0
(1 row)
```

Not a single HOT update. The bulk load filled every page of `inventory` to 100%, so no page had room for a new version. This module's project milestone fixes that with a lower fillfactor and measures the result.

> **Pitfall:** HOT is defeated by *any* index on a changed column, including indexes you forgot about. A classic: a trigger sets `updated_at = now()` on every update, and someone adds an index on `updated_at` "for the sync job". From that day on, no update on the table is HOT. Check `n_tup_hot_upd / n_tup_upd` for update-heavy tables after adding indexes.

> **In production:** `fillfactor` only applies to pages written *after* you set it. `ALTER TABLE inventory SET (fillfactor = 90)` takes effect for new pages immediately; to repack existing pages you need a rewrite (`VACUUM FULL`, or `pg_repack` online, Module 17). Values between 70 and 90 are typical for update-heavy tables. Lower values waste space on tables that are mostly read.

---

## 8. TOAST: storing large values

### 8.1 The problem

A tuple must fit in one 8 KB page, because PostgreSQL never splits a row across heap pages. Yet a `text` or `jsonb` value can be up to 1 GB. The solution is **TOAST**, "The Oversized-Attribute Storage Technique". When a row would be too big, PostgreSQL compresses its large values and, if that is not enough, moves them **out of line** into a separate **TOAST table**, leaving an 18-byte pointer in the row.

Every table with at least one potentially large column (any variable-length type: `text`, `bytea`, `jsonb`, `numeric`, arrays, ...) gets a TOAST table at creation time, in the `pg_toast` schema, named after the owning table's OID:

```sql
SELECT c.relname, c.reltoastrelid::regclass AS toast_table,
       pg_size_pretty(pg_relation_size(c.reltoastrelid)) AS toast_size
FROM pg_class c
WHERE c.relname IN ('products', 'orders', 'inventory') AND c.relnamespace = 'bazaar'::regnamespace;
```

```text
  relname  |       toast_table       | toast_size 
-----------+-------------------------+------------
 inventory | -                       | 
 orders    | -                       | 
 products  | pg_toast.pg_toast_32899 | 0 bytes
(3 rows)
```

`inventory` and `orders` have no TOAST table (`-`): their columns are fixed-width or, like `numeric(12,2)`, have a small maximum size, so their rows can never reach the TOAST threshold. `products` has one because of `description`, `attributes` and `tags`, but it is empty: Bazaar's descriptions are short.

### 8.2 The rules

The TOAST machinery kicks in when a tuple about to be stored is larger than **`TOAST_TUPLE_THRESHOLD`, about 2 KB** (2032 bytes with 8 KB pages, chosen so that at least four rows fit on a page). Then PostgreSQL works through the row's variable-length columns, largest first, applying each column's **storage strategy**, until the row is below **`toast_tuple_target`** (also 2032 bytes by default, adjustable per table):

| Strategy | Compress? | Move out of line? | Default for |
|---|---|---|---|
| `PLAIN` | no | no | fixed-width types (`int`, `timestamptz`, ...) |
| `EXTENDED` | yes, first | yes, if still too big | most variable-length types: `text`, `bytea`, `jsonb`, arrays |
| `EXTERNAL` | no | yes | (choose it yourself) |
| `MAIN` | yes | only as a last resort | `numeric` |

Out-of-line values are cut into **chunks** of up to 1996 bytes, stored as rows `(chunk_id, chunk_seq, chunk_data)` in the TOAST table, which has its own index on `(chunk_id, chunk_seq)`. Reading a toasted value means an index lookup plus fetching all its chunks, and then decompressing.

Compression uses **pglz** (PostgreSQL's built-in LZ variant, the default) or **lz4** (faster, usually similar ratio, available when PostgreSQL was built with it, as in the official packages). The method is chosen per column with `ALTER TABLE ... ALTER COLUMN ... SET COMPRESSION`, or globally with `default_toast_compression`. pglz gives up on values that don't compress by at least 25%.

### 8.3 Watching it happen

Four values of increasing size and compressibility:

```sql
CREATE TABLE lab.toast_demo (id int PRIMARY KEY, body text);
INSERT INTO lab.toast_demo VALUES
    (1, repeat('a', 100)),                                                  -- small
    (2, repeat('Bazaar ', 1000)),                                           -- 7 kB, very compressible
    (3, (SELECT string_agg(md5(g::text), '') FROM generate_series(1, 200) AS g)), -- 6.4 kB of hex, barely compressible
    (4, repeat('Bazaar ', 300000));                                         -- 2.1 MB, very compressible

SELECT id,
       octet_length(body)            AS raw_bytes,
       pg_column_size(body)          AS stored_bytes,
       pg_column_compression(body)   AS compression
FROM lab.toast_demo ORDER BY id;
```

```text
CREATE TABLE
INSERT 0 4
 id | raw_bytes | stored_bytes | compression 
----+-----------+--------------+-------------
  1 |       100 |          101 | 
  2 |      7000 |           98 | pglz
  3 |      6400 |         6400 | 
  4 |   2100000 |        24053 | pglz
(4 rows)
```

- Row 1 is small: stored as is (plus a 4-byte header).
- Row 2 crossed the threshold, compressed to under 100 bytes, and stayed **inline**, compressed.
- Row 3 did not compress well enough, so it was moved **out of line** uncompressed.
- Row 4 compressed by a factor of almost 90, but even compressed it is larger than 2 KB, so it is compressed **and** out of line.

Which values went to the TOAST table, and in how many chunks?

```sql
SELECT chunk_id, count(*) AS chunks, sum(octet_length(chunk_data)) AS bytes
FROM pg_toast.pg_toast_NNNNN
GROUP BY chunk_id ORDER BY chunk_id;
```

That name depends on the table's OID, so look it up with `\gset` first (a psql meta-command from Module 01):

```sql
SELECT reltoastrelid::regclass AS toast FROM pg_class WHERE oid = 'lab.toast_demo'::regclass \gset
SELECT chunk_id, count(*) AS chunks, sum(octet_length(chunk_data)) AS bytes
FROM :toast
GROUP BY chunk_id ORDER BY chunk_id;
```

```text
 chunk_id | chunks | bytes 
----------+--------+-------
    34679 |      4 |  6400
    34680 |     13 | 24053
(2 rows)
```

Two out-of-line values: row 3 (6400 bytes = 3 full chunks of 1996 + a remainder) and row 4 (its 26 KB of compressed data).

Changing the strategy or the compression method affects values stored **from then on**:

```sql
ALTER TABLE lab.toast_demo ALTER COLUMN body SET COMPRESSION lz4;
INSERT INTO lab.toast_demo VALUES (5, repeat('Bazaar ', 300000));
ALTER TABLE lab.toast_demo ALTER COLUMN body SET STORAGE EXTERNAL;
INSERT INTO lab.toast_demo VALUES (6, repeat('Bazaar ', 1000));

SELECT id, octet_length(body) AS raw_bytes, pg_column_size(body) AS stored_bytes,
       pg_column_compression(body) AS compression
FROM lab.toast_demo WHERE id >= 4 ORDER BY id;
```

```text
ALTER TABLE
INSERT 0 1
ALTER TABLE
INSERT 0 1
 id | raw_bytes | stored_bytes | compression 
----+-----------+--------------+-------------
  4 |   2100000 |        24053 | pglz
  5 |   2100000 |         8256 | lz4
  6 |      7000 |         7000 | 
(3 rows)
```

lz4 compressed the same 2.1 MB value into fewer bytes than pglz here. With `EXTERNAL`, row 6 (identical to row 2) was moved out of line *uncompressed*. `EXTERNAL` is useful for large values that you read in pieces with `substr()`: PostgreSQL can then fetch only the chunks it needs instead of decompressing the whole value.

### 8.4 What TOAST means for your queries

- **`SELECT *` is expensive on tables with big values.** Toasted columns are fetched and decompressed only if the query actually needs them ("detoasting" is lazy), so listing only the columns you need can avoid most of the I/O.
- **Updates that don't touch a toasted column don't copy it.** The new row version reuses the same TOAST pointer. Updating `products.price` doesn't rewrite a 50 KB description.
- **Updates that do touch it rewrite the whole value.** Changing one key in a 1 MB `jsonb` document writes a new 1 MB (compressed) value and leaves the old chunks as dead tuples in the TOAST table. Big, frequently modified JSON documents are a bloat factory; split such data into rows.
- **The TOAST table is a table.** It is vacuumed (autovacuum treats it separately), it can bloat, and it counts in `pg_table_size` but not in `pg_relation_size`.
- **A single field is limited to 1 GB**, and a value must fit in memory to be processed. TOAST is not a substitute for object storage for large files.

---

## 9. The write-ahead log

### 9.1 Why a log

Changes are made to pages in shared memory, and dirty pages are written back to the data files *later*, in no particular order. If the server crashes in between, the data files are a mixture of old and new pages. The **write-ahead log** (WAL) fixes this with one rule:

> **Before a changed data page is written to disk, the WAL record describing the change must already be on disk. And before a commit is reported to the client, the commit's WAL record must be on disk.**

The WAL is an append-only, sequential log of every change to every page. Sequential writes to one file are fast, much faster than random writes of many data pages. At commit, PostgreSQL only has to `fsync` the WAL up to the commit record; the data pages can be written lazily. After a crash, replaying the WAL from a known-good point (the last checkpoint) recreates every committed change. The same log is what replicas replay (Module 19) and what point-in-time recovery restores (Module 18).

### 9.2 LSNs and segment files

A position in the WAL is a **log sequence number** (LSN): a 64-bit byte offset into the conceptually infinite WAL stream, printed as two hexadecimal halves, like `0/1A2B3C8`. Every page header stores the LSN of the last record that touched the page (the `lsn` column of `page_header` in section 4), which is how recovery knows whether a page already contains a change.

```sql
SELECT pg_current_wal_lsn(),
       pg_walfile_name(pg_current_wal_lsn()),
       pg_size_pretty(pg_current_wal_lsn() - '0/0'::pg_lsn) AS wal_written_since_initdb;
```

```text
 pg_current_wal_lsn |     pg_walfile_name      | wal_written_since_initdb 
--------------------+--------------------------+--------------------------
 1/C8019DE8         | 0000000100000001000000C8 | 7296 MB
(1 row)
```

The stream is stored in **segment files** of 16 MB in `pg_wal/`. The 24-character name encodes the timeline (8 hex digits, it changes after a point-in-time recovery or a failover, Modules 18 to 20) and the segment's position. Subtracting two LSNs gives the number of bytes of WAL between them, which is how you measure how much WAL an operation generates and, in Module 19, how far behind a replica is.

### 9.3 WAL records and full-page writes

A WAL record says what changed at a low level: "in block 0 of relation 16450, at item 168, insert this heap-only tuple", "in this B-tree page, insert this index entry", "transaction 812 committed at time T". Each record belongs to a **resource manager** (`Heap`, `Heap2`, `Btree`, `Transaction`, `XLOG`, ...).

There is one complication: **torn pages**. The operating system writes in 4 KB units (or smaller), PostgreSQL in 8 KB pages. If power fails in the middle of writing a page, the file can contain half old and half new page. Replaying a small record onto a torn page cannot repair it. So the **first time a page is modified after each checkpoint**, PostgreSQL writes a **full-page image** (FPI) of the page into the WAL. Recovery then starts from that clean image and replays later records on top. This is controlled by `full_page_writes` (on by default; never turn it off unless your storage guarantees atomic 8 KB writes).

`pg_walinspect` lets us see this. We capture the LSN, force a checkpoint, update one row twice, and look at the records in between:

```sql
CHECKPOINT;
SELECT pg_current_wal_lsn() AS start_lsn \gset
UPDATE lab.stock_ff90 SET qty = qty - 1 WHERE product_id = 2;
UPDATE lab.stock_ff90 SET qty = qty - 1 WHERE product_id = 2;
SELECT pg_current_wal_lsn() AS end_lsn \gset

SELECT start_lsn, resource_manager AS rmgr, record_type, record_length, fpi_length,
       left(description, 40) AS description
FROM pg_get_wal_records_info(:'start_lsn', :'end_lsn');
```

```text
CHECKPOINT
UPDATE 1
UPDATE 1
 start_lsn  |    rmgr     |   record_type   | record_length | fpi_length |               description                
------------+-------------+-----------------+---------------+------------+------------------------------------------
 1/C8019EB8 | Heap        | HOT_UPDATE      |          7485 |       7420 | old_xmax: 49262, old_off: 2, old_infobit
 1/C801BC10 | Transaction | COMMIT          |            34 |          0 | 2026-10-14 08:05:57.406326+00
 1/C801BC38 | Heap2       | PRUNE_ON_ACCESS |            58 |          0 | snapshotConflictHorizon: 49262, isCatalo
 1/C801BC78 | Heap        | HOT_UPDATE      |            71 |          0 | old_xmax: 49263, old_off: 169, old_infob
 1/C801BCC0 | Transaction | COMMIT          |            34 |          0 | 2026-10-14 08:05:57.407892+00
(5 rows)
```

The first `HOT_UPDATE` record is over 7 KB, almost all of it a full-page image (`fpi_length`), because it was the first change to that page since the checkpoint. (The image is smaller than 8 KB because the free space in the middle of the page is left out.) The second update of the same page is a 71-byte record. In between, the second `UPDATE` found the version that the first one had left dead and pruned it (section 7): `Heap2 PRUNE_ON_ACCESS`, since pruning changes the page too. Each autocommitted statement also produced a `COMMIT` record. Two consequences:

- WAL volume spikes right after every checkpoint, when every page touched for the first time is logged in full. More frequent checkpoints mean **more** WAL, not less. Module 17 tunes `checkpoint_timeout` and `max_wal_size` with this in mind.
- `wal_compression` (off by default, accepts `pglz`, `lz4` or `zstd`) compresses full-page images and often cuts WAL volume substantially at a small CPU cost.

`pg_get_wal_stats` summarises a range by record type. Here is a 10,000-row `INSERT` into a table with a primary key:

```sql
SELECT pg_current_wal_lsn() AS start_lsn \gset
INSERT INTO lab.stock_ff100 SELECT g, 10, 'x' FROM generate_series(1001, 11000) g;
SELECT pg_current_wal_lsn() AS end_lsn \gset

SELECT pg_size_pretty(:'end_lsn'::pg_lsn - :'start_lsn'::pg_lsn) AS wal_generated;
SELECT "resource_manager/record_type" AS type, count, round(record_size_percentage::numeric, 1) AS rec_pct,
       fpi_size, round(combined_size_percentage::numeric, 1) AS total_pct
FROM pg_get_wal_stats(:'start_lsn', :'end_lsn', per_record => true)
WHERE count > 0
ORDER BY combined_size DESC;
```

```text
INSERT 0 10000
 wal_generated 
---------------
 1366 kB
(1 row)

         type          | count | rec_pct | fpi_size | total_pct 
-----------------------+-------+---------+----------+-----------
 Heap/INSERT           |  9946 |    49.3 |     3412 |      49.1
 Btree/INSERT_LEAF     |  9973 |    48.7 |     5420 |      48.6
 Btree/SPLIT_R         |    27 |     1.5 |        0 |       1.5
 Heap2/PRUNE_ON_ACCESS |     1 |     0.0 |     5176 |       0.4
 Heap/INSERT+INIT      |    54 |     0.3 |        0 |       0.3
 Btree/INSERT_UPPER    |    27 |     0.1 |      112 |       0.2
 Transaction/COMMIT    |     1 |     0.0 |        0 |       0.0
(7 rows)
```

Every inserted row produced a heap record and a B-tree record. That is what "an index costs write amplification" means at the WAL level.

The command-line tool `pg_waldump` reads WAL files directly, without a running server (useful when the server will not start). It ships with the server:

```bash
docker exec -u postgres course-pg bash -c '
  D=/var/lib/postgresql/data
  REDO=$(pg_controldata $D | sed -n "s/^Latest checkpoint.s REDO location: *//p")
  pg_waldump -p $D/pg_wal -s $REDO -n 6'
```

```text
rmgr: XLOG        len (rec/tot):     30/    30, tx:          0, lsn: 1/C8019DE8, prev 1/C8019DC0, desc: CHECKPOINT_REDO wal_level replica
rmgr: Standby     len (rec/tot):     50/    50, tx:          0, lsn: 1/C8019E08, prev 1/C8019DE8, desc: RUNNING_XACTS nextXid 49262 latestCompletedXid 49261 oldestRunningXid 49262
rmgr: XLOG        len (rec/tot):    114/   114, tx:          0, lsn: 1/C8019E40, prev 1/C8019E08, desc: CHECKPOINT_ONLINE redo 1/C8019DE8; tli 1; prev tli 1; fpw true; wal_level replica; xid 0:49262; oid 40960; multi 1; offset 0; oldest xid 731 in DB 1; oldest multi 1 in DB 1; oldest/newest commit timestamp xid: 0/0; oldest running xid 49262; online
rmgr: Heap        len (rec/tot):     65/  7485, tx:      49262, lsn: 1/C8019EB8, prev 1/C8019E40, desc: HOT_UPDATE old_xmax: 49262, old_off: 2, old_infobits: [], flags: 0x60, new_xmax: 0, new_off: 169, blkref #0: rel 1663/32848/34665 blk 0 FPW
rmgr: Transaction len (rec/tot):     34/    34, tx:      49262, lsn: 1/C801BC10, prev 1/C8019EB8, desc: COMMIT 2026-10-14 08:05:57.406326 UTC
rmgr: Heap2       len (rec/tot):     58/    58, tx:          0, lsn: 1/C801BC38, prev 1/C801BC10, desc: PRUNE_ON_ACCESS snapshotConflictHorizon: 49262, isCatalogRel: F, nplans: 0, nredirected: 1, ndead: 0, nunused: 0, redirected: [2->169], blkref #0: rel 1663/32848/34665 blk 0
```

`pg_controldata` reads `global/pg_control` and reports, among other things, the redo point of the last checkpoint; `pg_waldump` prints the first six records from there. You can recognize the records from the `pg_walinspect` output above. Before them come the records of our `CHECKPOINT`: `CHECKPOINT_REDO` (new in PG 17, it marks the redo point itself), `RUNNING_XACTS` (a list of running transactions that standbys need, Module 19), and `CHECKPOINT_ONLINE`, the checkpoint record that `pg_control` points to. The `PRUNE_ON_ACCESS` record even says what pruning did: `redirected: [2->169]`. `blkref` shows which block of which relation (`tablespace/database/relfilenode`) a record changes, and `FPW` marks a full-page write.

### 9.4 wal_level

`wal_level` decides how much information goes into the WAL:

| Level | Contents | Use |
|---|---|---|
| `minimal` | Just enough for crash recovery. Some bulk operations (`CREATE TABLE AS`, `COPY` into a table created in the same transaction) skip WAL entirely and `fsync` the file instead. | Standalone servers with no backups from WAL. Requires `max_wal_senders = 0`. |
| `replica` (default) | Plus what physical replicas and WAL archiving need. | Almost everyone. |
| `logical` | Plus the information needed to decode changes into rows (logical replication, CDC, Module 19). | When you use logical decoding. |

Changing it requires a restart.

### 9.5 Checkpoints

A **checkpoint** guarantees that every change made before a certain WAL position, the **redo point**, is in the data files. To do this the checkpointer:

1. notes the current WAL position as the redo point;
2. writes **all** dirty buffers to the data files, spreading the writes over time (`checkpoint_completion_target`, default 0.9 of the interval) to avoid an I/O storm;
3. `fsync`s the data files;
4. writes a checkpoint record to the WAL and updates `global/pg_control` to point at it;
5. deletes or recycles WAL segments that are no longer needed for crash recovery.

Checkpoints start when `checkpoint_timeout` (default 5 minutes) has passed since the last one, when the WAL since the last one approaches `max_wal_size` (default 1 GB), or on demand with `CHECKPOINT`. The control file tells you where the last one is:

```sql
SELECT checkpoint_lsn, redo_lsn, timeline_id, checkpoint_time
FROM pg_control_checkpoint();
```

```text
 checkpoint_lsn |  redo_lsn  | timeline_id |    checkpoint_time     
----------------+------------+-------------+------------------------
 1/C8019E40     | 1/C8019DE8 |           1 | 2026-10-14 08:05:57+00
(1 row)
```

In PG 17 the checkpointer's own statistics moved to a new view, `pg_stat_checkpointer` (they used to be in `pg_stat_bgwriter`). `num_timed` counts checkpoints started by the timer and `num_requested` those forced by WAL volume or by commands. Module 17 explains why a high `num_requested` means `max_wal_size` is too small.

### 9.6 Crash recovery, live

Let's crash the server. Not the whole container, just one backend, killed with `SIGKILL` so it can't clean up. As explained in section 1, the postmaster responds by resetting the entire server. First we commit some rows. Unless a checkpoint happens to run in the next few seconds, their pages exist only in shared memory and in the WAL, not yet in the data files:

```sql
CREATE TABLE lab.crash_demo (id int, note text);
INSERT INTO lab.crash_demo SELECT g, 'committed before the crash' FROM generate_series(1, 1000) g;
SELECT pg_backend_pid();
```

Now, from a second terminal, kill that backend with `SIGKILL` (use the PID you got) and look at the server log:

```bash
docker exec course-pg kill -9 12345          # your PID here
docker logs --since 1m course-pg
```

Back in psql, your session reports that the server closed the connection (psql reconnects automatically on the next command). The log shows the whole sequence:

```text
2026-10-14 08:05:59.394 UTC [1] LOG:  server process (PID 1390) was terminated by signal 9: Killed
2026-10-14 08:05:59.394 UTC [1] DETAIL:  Failed process was running: SELECT pg_sleep(60)
2026-10-14 08:05:59.394 UTC [1] LOG:  terminating any other active server processes
2026-10-14 08:05:59.407 UTC [1] LOG:  all server processes terminated; reinitializing
2026-10-14 08:05:59.432 UTC [1403] LOG:  database system was interrupted; last known up at 2026-10-14 08:05:57 UTC
2026-10-14 08:05:59.538 UTC [1403] LOG:  database system was not properly shut down; automatic recovery in progress
2026-10-14 08:05:59.541 UTC [1403] LOG:  redo starts at 1/C8019DE8
2026-10-14 08:05:59.558 UTC [1403] LOG:  invalid record length at 1/C81A66F0: expected at least 24, got 0
2026-10-14 08:05:59.558 UTC [1403] LOG:  redo done at 1/C81A66C8 system usage: CPU: user: 0.01 s, system: 0.00 s, elapsed: 0.01 s
2026-10-14 08:05:59.562 UTC [1404] LOG:  checkpoint starting: end-of-recovery immediate wait
2026-10-14 08:05:59.572 UTC [1404] LOG:  checkpoint complete: wrote 126 buffers (0.8%); 0 WAL file(s) added, 0 removed, 0 recycled; write=0.002 s, sync=0.006 s, total=0.011 s; sync files=33, longest=0.002 s, average=0.001 s; distance=1586 kB, estimate=1586 kB; lsn=1/C81A66F0, redo lsn=1/C81A66F0
2026-10-14 08:05:59.577 UTC [1] LOG:  database system is ready to accept connections
```

Read it from the top. The postmaster noticed that a child died by signal 9, terminated every other server process (so all clients were disconnected), and reinitialized shared memory. The startup process found that the database had not been shut down cleanly and replayed the WAL from the redo point of the last checkpoint (`redo starts at`). It stopped where the WAL ends: the `invalid record length ... got 0` line is not an error, it is how recovery recognizes the end of the log, an empty slot where the next record would be. Then an end-of-recovery checkpoint ran and the server accepted connections again, all within about 200 milliseconds.

Reconnect, and the committed rows are there:

```sql
SELECT count(*) FROM lab.crash_demo;
```

```text
 count 
-------
  1000
(1 row)
```

The pages of `lab.crash_demo` were only in shared memory when the server reset and shared memory was discarded. The rows came back from the WAL, replayed starting at the redo point of the last checkpoint. Recovery time is proportional to the amount of WAL since that redo point, which is the other side of the checkpoint trade-off: rare checkpoints mean less WAL (fewer full-page images) but longer crash recovery.

> **Pitfall:** crash recovery throws away the **cumulative statistics** (section 12), because they live in shared memory and are only saved at a clean shutdown. After a crash, `pg_stat_user_tables` starts from zero, including the dead-tuple counts that autovacuum uses to decide what to vacuum. Autovacuum is effectively blind until the counters build up again. An `ANALYZE` of the database after a crash is a good habit.

---

## 10. VACUUM in detail

### 10.1 What VACUUM does

Dead tuples (old versions from `UPDATE`, deleted rows, rows from aborted transactions) occupy space until something removes them. Page pruning (section 7) removes dead tuples within a page during normal access, but it can't free the line pointers that indexes point to, and it doesn't touch the indexes. **VACUUM** does the full job. For a table, plain `VACUUM` (no `FULL`):

1. **Scans the heap**, skipping pages that the visibility map says are all-visible (they can't contain dead tuples). On each page it prunes dead tuples (like page pruning) and remembers the TIDs of the dead line pointers. It also freezes old tuples (section 11) and sets hint bits.
2. **Vacuums the indexes:** for each index, it scans the whole index and deletes entries that point to the remembered dead TIDs. In PG 17 the dead TIDs are kept in a compact radix tree (`TidStore`) limited by `maintenance_work_mem` (or `autovacuum_work_mem`); if it fills up, VACUUM vacuums the indexes, empties the store, and continues the heap scan, so each extra round means another full scan of every index.
3. **Vacuums the heap a second time**, visiting only the pages with dead line pointers and marking them `unused`, now that no index points to them.
4. **Updates the free space map** so that the freed space can be reused, and the **visibility map**: a page whose tuples are all visible to everyone gets its all-visible bit (and all-frozen bit, if all are frozen).
5. **Truncates** empty pages at the **end** of the file and returns them to the operating system. That needs a brief `ACCESS EXCLUSIVE` lock and is skipped if the lock is not immediately available.
6. Updates `pg_class.relpages`/`reltuples`, the table's `relfrozenxid`, and the statistics.

Plain VACUUM takes only a `SHARE UPDATE EXCLUSIVE` lock: reads and writes continue while it runs. It just can't run concurrently with DDL or another VACUUM on the same table.

### 10.2 A VACUUM, step by step

A purpose-built table with autovacuum disabled, so that we control when vacuuming happens:

```sql
CREATE TABLE lab.vac_demo (id int PRIMARY KEY, payload text)
    WITH (autovacuum_enabled = off);
INSERT INTO lab.vac_demo SELECT g, repeat('x', 100) FROM generate_series(1, 100000) g;
VACUUM (ANALYZE) lab.vac_demo;      -- sets the visibility map, like a settled table

DELETE FROM lab.vac_demo WHERE id % 10 < 3;   -- 30% of rows, spread over every page

SELECT pg_size_pretty(pg_relation_size('lab.vac_demo')) AS size,
       tuple_count, dead_tuple_count, round(dead_tuple_percent) AS dead_pct,
       round(free_percent) AS free_pct
FROM pgstattuple('lab.vac_demo');
SELECT * FROM pg_visibility_map_summary('lab.vac_demo');
```

```text
CREATE TABLE
INSERT 0 100000
VACUUM
DELETE 30000
 size  | tuple_count | dead_tuple_count | dead_pct | free_pct 
-------+-------------+------------------+----------+----------
 13 MB |       70000 |            30000 |       27 |        1
(1 row)

 all_visible | all_frozen 
-------------+------------
           0 |          0
(1 row)
```

`pgstattuple` reads the whole table and counts exactly. The `DELETE` touched every page, so every page lost its all-visible bit. Now vacuum it, with `VERBOSE` to see what it did:

```sql
VACUUM (VERBOSE) lab.vac_demo;
```

```text
INFO:  finished vacuuming "bazaar.lab.vac_demo": index scans: 1
pages: 0 removed, 1725 remain, 1725 scanned (100.00% of total)
tuples: 30000 removed, 70000 remain, 0 are dead but not yet removable
removable cutoff: 49271, which was 0 XIDs old when operation ended
frozen: 0 pages from table (0.00% of total) had 0 tuples frozen
index scan needed: 1725 pages from table (100.00% of total) had 30000 dead item identifiers removed
index "vac_demo_pkey": pages: 276 in total, 0 newly deleted, 0 currently deleted, 0 reusable
I/O timings: read: 0.000 ms, write: 0.000 ms
avg read rate: 0.000 MB/s, avg write rate: 0.000 MB/s
buffer usage: 5473 hits, 0 misses, 0 dirtied
WAL usage: 5451 records, 0 full page images, 475300 bytes
system usage: CPU: user: 0.01 s, system: 0.00 s, elapsed: 0.01 s
INFO:  vacuuming "bazaar.pg_toast.pg_toast_40960"
INFO:  finished vacuuming "bazaar.pg_toast.pg_toast_40960": index scans: 0
pages: 0 removed, 0 remain, 0 scanned (100.00% of total)
tuples: 0 removed, 0 remain, 0 are dead but not yet removable
removable cutoff: 49271, which was 0 XIDs old when operation ended
new relfrozenxid: 49271, which is 2 XIDs ahead of previous value
frozen: 0 pages from table (100.00% of total) had 0 tuples frozen
index scan not needed: 0 pages from table (100.00% of total) had 0 dead item identifiers removed
I/O timings: read: 0.000 ms, write: 0.000 ms
avg read rate: 0.000 MB/s, avg write rate: 0.000 MB/s
buffer usage: 12 hits, 0 misses, 0 dirtied
WAL usage: 1 records, 0 full page images, 188 bytes
system usage: CPU: user: 0.00 s, system: 0.00 s, elapsed: 0.00 s
VACUUM
```

Read the report from the top:

- `pages: 0 removed, ... remain, ... scanned (100.00% of total)`: every page had to be scanned because none was all-visible, and none could be removed because the remaining rows are spread over all of them.
- `tuples: 30000 removed`: the dead tuples are gone.
- `index scan needed: ... pages from table ... had 30000 dead item identifiers removed`, and the `index "vac_demo_pkey"` line: one pass over the primary key index deleted the 30,000 matching index entries.
- `removable cutoff`: the xmin horizon used to decide what is dead to everyone (section 10.5).
- The buffer and WAL usage lines show how much I/O the work cost.
- The second block is the table's TOAST table, which VACUUM processes along with the table (here it is empty).

The report doesn't mention the visibility map, but the summary below shows that VACUUM marked every page all-visible, so the next VACUUM can skip them.

And the effect:

```sql
SELECT pg_size_pretty(pg_relation_size('lab.vac_demo')) AS size,
       tuple_count, dead_tuple_count, round(free_percent) AS free_pct
FROM pgstattuple('lab.vac_demo');
SELECT * FROM pg_visibility_map_summary('lab.vac_demo');
```

```text
 size  | tuple_count | dead_tuple_count | free_pct 
-------+-------------+------------------+----------
 13 MB |       70000 |                0 |       30
(1 row)

 all_visible | all_frozen 
-------------+------------
        1725 |          0
(1 row)
```

**The file did not shrink.** The space is now free *inside* the pages and recorded in the free space map, ready for future inserts and updates. That is the design: in a table with steady churn, the freed space is reused, and the file stays at a stable size. Returning space to the OS would only mean asking for it back moments later.

VACUUM *can* return space when the free pages are at the **end** of the file:

```sql
DELETE FROM lab.vac_demo WHERE id > 50000;
VACUUM lab.vac_demo;
SELECT pg_size_pretty(pg_relation_size('lab.vac_demo')) AS size;
```

```text
DELETE 35000
VACUUM
  size   
---------
 6904 kB
(1 row)
```

### 10.3 VACUUM FULL

`VACUUM FULL` is a different command that happens to share a name. It **rewrites** the table into a new file (new relfilenode), copying only live tuples, and rebuilds every index. The result is compact, but:

- it takes an **`ACCESS EXCLUSIVE`** lock for the whole duration: no reads, no writes;
- it needs free disk space for the complete new copy (table plus indexes) until it finishes;
- it generates WAL for the whole new copy.

```sql
SELECT pg_relation_filepath('lab.vac_demo') AS before_path;
VACUUM FULL lab.vac_demo;
SELECT pg_relation_filepath('lab.vac_demo') AS after_path,
       pg_size_pretty(pg_relation_size('lab.vac_demo')) AS size;
```

```text
   before_path    
------------------
 base/32848/40960
(1 row)

VACUUM
    after_path    |  size   
------------------+---------
 base/32848/40967 | 4832 kB
(1 row)
```

On a busy production table, an exclusive lock for minutes is an outage. Module 17 covers the online alternatives (`pg_repack`, `REINDEX CONCURRENTLY`) and when bloat is worth fixing at all.

### 10.4 The visibility map and index-only scans

The visibility map has a second customer besides VACUUM: **index-only scans** (Module 11). An index entry doesn't say whether its tuple is visible to your snapshot. For an index-only scan, PostgreSQL checks the visibility map: if the heap page is all-visible, the index entry can be trusted without visiting the heap; otherwise it fetches the heap tuple to check. `EXPLAIN ANALYZE` reports those visits as `Heap Fetches`:

```sql
UPDATE lab.vac_demo SET payload = 'y' WHERE id <= 5000;   -- clears all-visible on those pages
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY OFF)
SELECT count(*) FROM lab.vac_demo WHERE id <= 10000;
VACUUM lab.vac_demo;
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY OFF)
SELECT count(*) FROM lab.vac_demo WHERE id <= 10000;
```

```text
UPDATE 3500
                                    QUERY PLAN                                    
----------------------------------------------------------------------------------
 Aggregate (actual rows=1 loops=1)
   ->  Index Only Scan using vac_demo_pkey on vac_demo (actual rows=7000 loops=1)
         Index Cond: (id <= 10000)
         Heap Fetches: 10439
(4 rows)

VACUUM
                                    QUERY PLAN                                    
----------------------------------------------------------------------------------
 Aggregate (actual rows=1 loops=1)
   ->  Index Only Scan using vac_demo_pkey on vac_demo (actual rows=7000 loops=1)
         Index Cond: (id <= 10000)
         Heap Fetches: 0
(4 rows)
```

Before the VACUUM, thousands of heap fetches made the "index-only" scan little better than an ordinary index scan. After it, zero. On tables that are mostly inserted (like an event log), VACUUM is what makes index-only scans work, which is why PostgreSQL 13 added insert-triggered autovacuum (Module 17).

### 10.5 Things that stop VACUUM from removing dead tuples

VACUUM can only remove a dead tuple if **no snapshot anywhere can still see it**. The cut-off is the oldest `xmin` of any snapshot in the cluster (the **xmin horizon**). These hold the horizon back:

- a **long-running transaction** or one that sits **idle in transaction** (Module 17 shows how to find and kill them);
- a long-running query on a **hot standby** with `hot_standby_feedback = on` (Module 19);
- an abandoned **replication slot** (Module 19);
- a forgotten **prepared transaction** (`PREPARE TRANSACTION`, two-phase commit).

While the horizon is stuck, VACUUM runs, finds the dead tuples, and leaves them, reporting them as `dead but not yet removable`:

```sql
-- Session A: an open REPEATABLE READ transaction keeps its snapshot
BEGIN ISOLATION LEVEL REPEATABLE READ;
SELECT count(*) FROM lab.vac_demo;
```

```sql
-- Session B
DELETE FROM lab.vac_demo WHERE id <= 1000;
VACUUM (VERBOSE) lab.vac_demo;     -- look at the "tuples:" line
```

Then `COMMIT` in session A and run the `VACUUM (VERBOSE)` in session B again:

```text
-- while session A is open:
tuples: 0 removed, 36838 remain, 700 are dead but not yet removable
removable cutoff: 49275, which was 1 XIDs old when operation ended
-- after session A committed:
tuples: 700 removed, 37223 remain, 0 are dead but not yet removable
removable cutoff: 49276, which was 0 XIDs old when operation ended
```

(Only 700 rows had `id <= 1000`, since the earlier `DELETE` had removed 30% of them.) The first VACUUM found the 700 deleted rows but had to keep them: session A's snapshot is older than the deleting transaction, so for A those rows still exist. The `removable cutoff` line shows the horizon VACUUM used, and how many XIDs behind the newest transaction it was. On a busy server, a horizon that is millions of XIDs old means some session has been sitting on a snapshot for a long time. Once A finished, the next VACUUM removed them.

---

## 11. Freezing and transaction ID wraparound

### 11.1 The problem with 32-bit XIDs

Tuple headers store 32-bit XIDs, so there are only about 4 billion of them, and a busy system can burn through 4 billion write transactions in months. PostgreSQL therefore compares XIDs with **modular arithmetic** on a circle: for any XID, the 2 billion XIDs "behind" it are in the past and the 2 billion "ahead" of it are in the future.

```text
                    current XID
                         |
            past         v        future
   ... <------------------*------------------> ...
       2^31 XIDs behind       2^31 XIDs ahead
   (visible if committed)   (invisible: "not yet happened")
```

The trouble: a tuple inserted by XID 100 is in the past today. After 2^31 more transactions, XID 100 would suddenly appear to be in the *future*, and the row would vanish from every query. That is **transaction ID wraparound**, and it would be silent data loss.

### 11.2 Freezing

The solution is **freezing**. Once a tuple is old enough that every current and future transaction must see it, VACUUM marks it **frozen** by setting both `HEAP_XMIN_COMMITTED` and `HEAP_XMIN_INVALID` (a combination that means `HEAP_XMIN_FROZEN`). A frozen tuple is treated as infinitely old, so its `xmin` no longer matters and can't wrap around.

```sql
VACUUM (FREEZE) lab.page_demo;
SELECT lp, t_xmin, raw_flags
FROM heap_page_items(get_raw_page('lab.page_demo', 0)),
     LATERAL heap_tuple_infomask_flags(t_infomask, t_infomask2)
WHERE lp_flags = 1;
SELECT * FROM pg_visibility_map('lab.page_demo');
```

```text
VACUUM
 lp | t_xmin |                                                raw_flags                                                
----+--------+---------------------------------------------------------------------------------------------------------
  1 |  49237 | {HEAP_HASVARWIDTH,HEAP_XMIN_COMMITTED,HEAP_XMIN_INVALID,HEAP_XMAX_INVALID}
  3 |  49237 | {HEAP_HASVARWIDTH,HEAP_XMIN_COMMITTED,HEAP_XMIN_INVALID,HEAP_XMAX_INVALID}
  4 |  49238 | {HEAP_HASNULL,HEAP_XMIN_COMMITTED,HEAP_XMIN_INVALID,HEAP_XMAX_INVALID}
  5 |  49243 | {HEAP_HASVARWIDTH,HEAP_XMIN_COMMITTED,HEAP_XMIN_INVALID,HEAP_XMAX_INVALID}
  7 |  49248 | {HEAP_HASVARWIDTH,HEAP_XMIN_COMMITTED,HEAP_XMIN_INVALID,HEAP_XMAX_INVALID,HEAP_UPDATED,HEAP_ONLY_TUPLE}
(5 rows)

 blkno | all_visible | all_frozen 
-------+-------------+------------
     0 | t           | t
(1 row)
```

`t_xmin` is still there, for debugging, but `HEAP_XMIN_FROZEN` tells every reader to ignore it. The page is now marked all-frozen in the visibility map, so even aggressive vacuums can skip it until it is modified again.

Each table records in `pg_class.relfrozenxid` an XID such that every tuple older than it is known to be frozen. Each database records the minimum over its tables in `pg_database.datfrozenxid`. `age(xid)` is the number of transactions between that XID and now: the number to watch.

```sql
SELECT datname, age(datfrozenxid) AS xid_age, mxid_age(datminmxid) AS multixact_age
FROM pg_database ORDER BY xid_age DESC;

SELECT c.oid::regclass AS table_name, age(c.relfrozenxid) AS xid_age,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS size
FROM pg_class c
WHERE c.relkind IN ('r', 'm', 't') AND c.relnamespace = 'bazaar'::regnamespace
ORDER BY age(c.relfrozenxid) DESC
LIMIT 5;
```

```text
  datname  | xid_age | multixact_age 
-----------+---------+---------------
 postgres  |   48545 |             0
 template1 |   48545 |             0
 template0 |   48545 |             0
 bazaar    |   48545 |             0
(4 rows)

      table_name       | xid_age |  size   
-----------------------+---------+---------
 addresses             |    8138 | 21 MB
 product_price_history |    8138 | 18 MB
 users                 |    8138 | 18 MB
 promotions            |    8138 | 2712 kB
 cart_items            |    8138 | 2264 kB
(5 rows)
```

### 11.3 How freezing is scheduled

Four settings control it (defaults in parentheses):

| Setting | Meaning |
|---|---|
| `vacuum_freeze_min_age` (50 million) | A tuple is frozen by a VACUUM that sees it if its `xmin` is at least this old. Younger tuples are left alone because they may be updated soon anyway. |
| `vacuum_freeze_table_age` (150 million) | When a table's `relfrozenxid` age passes this, VACUUM becomes **aggressive**: it scans every page that is not all-frozen (not just the ones that are not all-visible), so that it can advance `relfrozenxid`. |
| `autovacuum_freeze_max_age` (200 million) | When a table's age passes this, autovacuum starts an **anti-wraparound** vacuum on it, **even if autovacuum is disabled** for the table or the whole server. It shows up in `pg_stat_activity` as `autovacuum: VACUUM ... (to prevent wraparound)` and, unlike a normal autovacuum, it does not cancel itself when someone needs a conflicting lock. |
| `vacuum_failsafe_age` (1.6 billion) | Emergency mode: VACUUM skips index cleanup and ignores its cost-based throttling, to advance `relfrozenxid` as fast as possible. |

If all of that fails, PostgreSQL protects your data by **refusing to assign new XIDs**: about 40 million XIDs before the limit, it starts logging `WARNING: database "bazaar" must be vacuumed within N transactions`, and 3 million before the limit, every write fails with `ERROR: database is not accepting commands that assign new transaction IDs to avoid wraparound data loss`. Reads still work. The fix is a (possibly very long) `VACUUM` of the oldest tables.

> **In production:** wraparound outages are almost always caused by something that blocks freezing for weeks: a forgotten idle-in-transaction session, an abandoned replication slot, or autovacuum throttled so hard on a huge table that it never finishes. Alert when `age(datfrozenxid)` exceeds, say, 500 million (well past the 200 million at which autovacuum should have handled it), and investigate the oldest tables. Module 17 adds this check to the ops toolkit.

> **Under the hood:** since PostgreSQL 16, VACUUM also freezes **opportunistically**: if it is about to write a full-page image of a page anyway, it freezes all eligible tuples on it, since the extra cost is close to zero.

### 11.4 Multixacts

When several transactions lock the same row at the same time, for example two `SELECT ... FOR SHARE`, or the `FOR KEY SHARE` lock that a foreign-key check takes on the parent row (`orders` inserts lock their `users` row this way), one `xmax` field can't hold them all. PostgreSQL then allocates a **multixact ID**, stores the member list in `pg_multixact/`, puts the multixact ID in `xmax` and sets `HEAP_XMAX_IS_MULTI`.

Multixact IDs are also 32 bits and wrap around just like XIDs, so they are frozen too, tracked in `relminmxid`/`datminmxid`, watched with `mxid_age()`, and governed by `autovacuum_multixact_freeze_max_age` (400 million). Workloads with heavy foreign-key traffic on a few parent rows (all orders referencing the same handful of sellers) can consume multixacts much faster than you'd expect.

```sql
BEGIN;
SELECT * FROM lab.page_demo WHERE id = 3 FOR SHARE;
SELECT lp, t_xmax, raw_flags
FROM heap_page_items(get_raw_page('lab.page_demo', 0)),
     LATERAL heap_tuple_infomask_flags(t_infomask, t_infomask2)
WHERE lp = 3;
COMMIT;
```

```text
BEGIN
 id | label  
----+--------
  3 | cherry
(1 row)

 lp | t_xmax |                                                       raw_flags                                                        
----+--------+------------------------------------------------------------------------------------------------------------------------
  3 |  49276 | {HEAP_HASVARWIDTH,HEAP_XMAX_KEYSHR_LOCK,HEAP_XMAX_EXCL_LOCK,HEAP_XMAX_LOCK_ONLY,HEAP_XMIN_COMMITTED,HEAP_XMIN_INVALID}
(1 row)

COMMIT
```

A single locker stores its own XID in `xmax` with the lock-only flags. Exercise 9 asks you to produce a multixact with a second session.

---

## 12. The cumulative statistics system

### 12.1 How it works

All the `pg_stat_*` views you used in Module 12 and will use heavily in Module 17 (`pg_stat_user_tables`, `pg_stat_user_indexes`, `pg_stat_database`, `pg_stat_io`, `pg_stat_statements`, ...) read the **cumulative statistics system**. Since PostgreSQL 15 it works like this:

1. Each backend counts its own activity (rows inserted, blocks read, index scans, ...) in **local memory**, without any locking.
2. At transaction end, if at least one second has passed since its last flush (or when the backend is idle for a while), it **flushes** the pending counts into a hash table in **shared memory**.
3. Views read the shared-memory counters.
4. At a **clean shutdown**, the checkpointer writes everything to `pg_stat/pgstat.stat`, and it is loaded at the next start. After a **crash** or an immediate shutdown, the file is discarded and all counters start at zero.

That explains two things you may have noticed: why a freshly run statement doesn't show up in the counters immediately (the flush interval; `pg_stat_force_next_flush()` exists for tests and demos), and why counters reset after the crash in section 9.

```sql
SELECT relname, n_tup_ins, n_live_tup, n_dead_tup, last_autovacuum
FROM pg_stat_user_tables WHERE relname = 'crash_demo';
```

```text
  relname   | n_tup_ins | n_live_tup | n_dead_tup | last_autovacuum 
------------+-----------+------------+------------+-----------------
 crash_demo |         0 |          0 |          0 | 
(1 row)
```

The table has 1000 rows, but its counters say nothing happened, because the crash discarded them.

### 12.2 Consistency within a transaction

Statistics views behave differently from tables inside a transaction. By default (`stats_fetch_consistency = cache`), the first access to an object's statistics in a transaction caches them, and repeated reads in the same transaction return the cached values. Set it to `snapshot` to freeze *all* statistics at first access (consistent cross-view reports, more memory), or to `none` to always read live values (good for monitoring loops). `pg_stat_clear_snapshot()` discards the cache.

### 12.3 Resetting

Counters only ever grow. To analyse a period, either take two readings and subtract (what monitoring systems do), or reset: `pg_stat_reset()` for the current database's table/index/function counters, `pg_stat_reset_shared('io')` (or `'bgwriter'`, `'checkpointer'`, `'wal'`, ...) for cluster-wide views, and `pg_stat_statements_reset()` for statement statistics. Resetting is global and destroys data other people may be looking at, so in production prefer subtracting snapshots.

> **Pitfall:** `pg_stat_reset()` also zeroes `n_dead_tup` and `n_mod_since_analyze`, which autovacuum uses to decide what to vacuum and analyze. Resetting table statistics on a busy database makes autovacuum lazy until the counters build up again.

---

## 13. The planner's statistics

The cumulative statistics count *activity*. The planner needs something else: a description of the *data*, to estimate how many rows each step of a plan returns (Module 12). That description is collected by `ANALYZE` (run by autovacuum or by hand) and stored in the catalog `pg_statistic`.

### 13.1 What ANALYZE collects

`ANALYZE` reads a **random sample** of `300 × default_statistics_target` rows (default target 100, so 30,000 rows), regardless of table size. It updates `pg_class.reltuples` and `relpages`, and for each column it computes:

| Statistic | Meaning |
|---|---|
| `null_frac` | Fraction of NULLs. |
| `avg_width` | Average width in bytes (for memory estimates of sorts and hashes). |
| `n_distinct` | Number of distinct values. **Positive**: an absolute count. **Negative**: a fraction of the row count, used when the number of distinct values grows with the table. `-1` means every value is distinct (a unique column). |
| `most_common_vals`, `most_common_freqs` | The **MCV list**: up to *target* values that are more common than average, with their frequencies. |
| `histogram_bounds` | *target*+1 values that split the remaining (non-MCV, non-null) values into *target* buckets holding equal numbers of rows (an equi-depth histogram). |
| `correlation` | From −1 to 1: how well the physical order of rows matches the column's sort order. Near ±1, an index range scan reads pages nearly sequentially; near 0, it jumps randomly around the table. |
| `most_common_elems`, `elem_count_histogram` | For arrays and `tsvector`: element-level frequencies (used for `@>` and `&&`). |

`pg_statistic` stores these in generic "slots" with numeric kind codes and is readable only by superusers, because the values are samples of real data. `pg_stats` is the readable view, and it shows only columns of tables you have `SELECT` on.

### 13.2 Reading pg_stats for orders

```sql
SELECT attname, null_frac, avg_width, n_distinct, correlation
FROM pg_stats
WHERE schemaname = 'bazaar' AND tablename = 'orders'
ORDER BY attname;
```

```text
       attname       | null_frac | avg_width | n_distinct | correlation 
---------------------+-----------+-----------+------------+-------------
 created_at          |         0 |         8 |  -0.993574 |    0.999661
 delivered_at        |     0.077 |         8 |   -0.91024 |  0.99996305
 id                  |         0 |         8 |         -1 |    0.999661
 paid_at             |    0.0525 |         8 |   -0.94535 |  0.99999994
 seller_id           |         0 |         8 |       1362 |  0.16891216
 shipped_at          |    0.0667 |         8 |  -0.925814 |   0.9999924
 shipping_address_id |         0 |         8 |  -0.112916 |  0.39626727
 status              |         0 |         4 |          6 |   0.7697211
 total               |         0 |         7 |      11989 |  0.08550142
 updated_at          |         0 |         8 |  -0.987226 |  0.99961156
 user_id             |         0 |         8 |  -0.104418 |   0.4415908
(11 rows)
```

What each line tells the planner:

- **`id`**: `n_distinct = -1`, every value is distinct (it's the primary key). `correlation` ≈ 1: ids were assigned in insertion order, so the table's physical order matches the sort order, and an index range scan on `id` reads consecutive pages.
- **`created_at`, `updated_at`**: almost unique (`n_distinct` close to −1) and almost perfectly correlated, because orders were inserted chronologically. A query for "orders of the last week" via an index touches a compact run of pages. This is also why BRIN indexes (Module 11) work on such columns.
- **`status`**: `n_distinct = 6`, a positive number, because the number of statuses doesn't grow with the table.
- **`seller_id`**, **`user_id`**: estimated from the sample. Compare with the truth:

```sql
SELECT count(DISTINCT seller_id) AS sellers, count(DISTINCT user_id) AS buyers FROM orders;
```

```text
 sellers | buyers 
---------+--------
    1992 |  84058
(1 row)
```

  Both are underestimated. `n_distinct` is the hardest statistic to get from a sample: many sellers and buyers appear only a few times among 500,000 orders and are simply missing from a 30,000-row sample. Skewed data makes it worse. When such an error leads to a bad plan, you can override the estimate with `ALTER TABLE orders ALTER COLUMN user_id SET (n_distinct = -0.17)` (followed by `ANALYZE`).
- **`paid_at`, `shipped_at`, `delivered_at`**: their `null_frac` is the share of orders that haven't reached that stage (or never will, like cancelled ones).
- **`total`**: `correlation` near 0: the amount has nothing to do with where a row sits in the table.

### 13.3 From statistics to row estimates

**Equality on an MCV.** The estimate is simply `reltuples × frequency`:

```sql
SELECT most_common_vals, most_common_freqs
FROM pg_stats WHERE tablename = 'orders' AND attname = 'status';

SELECT reltuples FROM pg_class WHERE oid = 'orders'::regclass;

EXPLAIN SELECT * FROM orders WHERE status = 'shipped';
```

```text
                  most_common_vals                   |                        most_common_freqs                         
-----------------------------------------------------+------------------------------------------------------------------
 {delivered,cancelled,refunded,shipped,paid,pending} | {0.90646666,0.050133333,0.0282,0.0103,0.0025333334,0.0023666667}
(1 row)

 reltuples 
-----------
    500000
(1 row)

                                 QUERY PLAN                                 
----------------------------------------------------------------------------
 Gather  (cost=1000.00..11214.17 rows=5150 width=83)
   Workers Planned: 2
   ->  Parallel Seq Scan on orders  (cost=0.00..9699.17 rows=2146 width=83)
         Filter: (status = 'shipped'::order_status)
(4 rows)
```

Multiply: `shipped` has frequency 0.0103 in the sample, and 0.0103 × 500,000 = 5,150, exactly the `rows=5150` at the top of the plan. (The two parallel workers and the leader each take a share: the parallel scan node shows 5,150 / 2.4 ≈ 2,146 rows per process.) Your numbers will differ a little: `ANALYZE` draws a new random sample every time, so the frequencies, and with them the estimates, move slightly after every `ANALYZE`.

**Equality on a value not in the MCV list.** PostgreSQL assumes the remaining rows are spread evenly over the remaining distinct values: `selectivity = (1 − sum(MCV freqs) − null_frac) / (n_distinct − number of MCVs)`.

**Ranges use the histogram.** For `total`, both parts matter. Its MCV list holds popular price points (10.99, 18.99, 12.99, ...: single-item orders of cheap products), together about 18% of all orders. The histogram describes the remaining values: its 101 bounds split them into 100 buckets that each hold 1% of them. The first few bounds:

```sql
SELECT array_length(histogram_bounds::text::numeric[], 1) AS bounds,
       (histogram_bounds::text::numeric[])[1:6] AS first_bounds
FROM pg_stats WHERE tablename = 'orders' AND attname = 'total';
```

```text
 bounds |             first_bounds             
--------+--------------------------------------
    101 | {4.99,30.97,41.97,51.99,56.98,60.98}
(1 row)
```

For `total < 50`, the planner finds the bucket that contains 50, assumes values are spread linearly inside it, and adds the fraction of that bucket to the full buckets below it. The MCVs below 50 are added separately, by their exact frequencies. Here is the same calculation in SQL:

```sql
WITH s AS (
    SELECT histogram_bounds::text::numeric[] AS b,
           most_common_vals::text::numeric[] AS mcv,
           most_common_freqs AS mcf,
           null_frac
    FROM pg_stats WHERE tablename = 'orders' AND attname = 'total'
), k AS (
    SELECT s.*, (SELECT max(i) FROM generate_subscripts(b, 1) AS i WHERE b[i] <= 50) AS k
    FROM s
)
SELECT k - 1 AS full_buckets,
       round((50 - b[k]) / (b[k + 1] - b[k]), 3) AS part_of_bucket,
       round(((k - 1) + (50 - b[k]) / (b[k + 1] - b[k])) / (array_length(b, 1) - 1), 5) AS hist_selectivity,
       (SELECT coalesce(sum(f), 0) FROM unnest(mcv, mcf) AS m (v, f) WHERE v < 50) AS mcv_selectivity_below_50,
       (SELECT sum(f) FROM unnest(mcf) AS f) AS mcv_total_freq,
       round(500000 * ((SELECT coalesce(sum(f), 0) FROM unnest(mcv, mcf) AS m (v, f) WHERE v < 50)
                       + ((k - 1) + (50 - b[k]) / (b[k + 1] - b[k])) / (array_length(b, 1) - 1)
                         * (1 - (SELECT sum(f) FROM unnest(mcf) AS f) - null_frac))) AS estimated_rows
FROM k;

EXPLAIN SELECT * FROM orders WHERE total < 50;
```

```text
 full_buckets | part_of_bucket | hist_selectivity | mcv_selectivity_below_50 | mcv_total_freq | estimated_rows 
--------------+----------------+------------------+--------------------------+----------------+----------------
            2 |          0.801 |          0.02801 |               0.13283335 |      0.1750335 |          77972
(1 row)

                          QUERY PLAN                           
---------------------------------------------------------------
 Seq Scan on orders  (cost=0.00..13345.00 rows=77937 width=83)
   Filter: (total < '50'::numeric)
(2 rows)
```

The planner combines the two parts: the MCVs below 50 count with their exact frequencies, and the histogram's share applies only to the rows *not* covered by the MCVs or NULLs, so `selectivity = mcv_selectivity_below_50 + hist_selectivity × (1 − mcv_total_freq − null_frac)`. Multiplied by `reltuples`, that is the `estimated_rows` column, and it matches the plan's `rows=` (up to rounding).

### 13.4 Keeping statistics useful

- **Statistics go stale.** Autovacuum runs `ANALYZE` after about 10% of a table has changed (Module 17), so a table that just received a big batch load may have old statistics. Run `ANALYZE` after bulk loads, and after `pg_upgrade` (statistics are not carried over before PG 18).
- **Raise the target for skewed columns.** `ALTER TABLE orders ALTER COLUMN seller_id SET STATISTICS 1000` gives that column a 1,000-entry MCV list and histogram, and a 300,000-row sample. Planning gets a little slower, so do it per column, where an estimate is demonstrably wrong.
- **The planner scales `reltuples`.** It doesn't trust `reltuples` blindly: it takes the current number of pages from the file size and scales the row count by `reltuples / relpages`, so a table that doubled since the last `ANALYZE` is still estimated sensibly.
- **Per-column statistics can't see correlations between columns.** That is what extended statistics (`CREATE STATISTICS`, Module 12) are for. They are stored in `pg_statistic_ext_data` and readable through `pg_stats_ext`.

---

## Summary

- PostgreSQL runs one **process per connection**, forked by the **postmaster**, plus auxiliary processes: **checkpointer**, **background writer**, **walwriter**, **autovacuum launcher and workers**. A crash of any process resets the whole server and triggers crash recovery.
- All processes share the **buffer pool** (`shared_buffers`). Pages are found through the buffer mapping table, replaced by **clock sweep** using usage counts, and protected from big scans by **ring buffers**. The OS page cache sits underneath, so data is often cached twice.
- A relation is a set of files `base/<db oid>/<relfilenode>[_fsm|_vm|_init][.N]`, split into **1 GB segments**. Rewrites (`TRUNCATE`, `VACUUM FULL`, `CLUSTER`, many `ALTER TABLE`s) create a new relfilenode.
- An **8 KB page** has a header, line pointers growing forward, and tuples growing backward. A **tuple** has a 23-byte header (`xmin`, `xmax`, `cmin/cmax`, `ctid`, infomask bits), an optional null bitmap, and **aligned** data: order columns from 8-byte to 1-byte alignment to avoid padding.
- **MVCC visibility** compares a tuple's `xmin`/`xmax` with a **snapshot** (`xmin:xmax:xip`) and with commit status from **`pg_xact`**. **Hint bits** cache that status in the tuple, so the first read after a write can dirty pages.
- An **UPDATE writes a new version**. **HOT** updates keep it on the same page and skip index maintenance when no indexed column changes; **fillfactor** leaves room for them; **pruning** cleans HOT chains during normal access.
- **TOAST** compresses (pglz or lz4) and moves out of line values that make a row exceed about 2 KB, in 1996-byte chunks, with per-column strategies.
- The **WAL** records every change before the data page is written. **LSNs** are positions in it. **Full-page writes** after each checkpoint protect against torn pages. **Checkpoints** bound recovery time; crash recovery replays WAL from the last redo point.
- **VACUUM** removes dead tuples, cleans indexes, updates the free space and visibility maps, and only shrinks the file from the end. **VACUUM FULL** rewrites the table under an exclusive lock. Long transactions and slots hold back the **xmin horizon**.
- **Freezing** protects against 32-bit **XID wraparound**. Watch `age(datfrozenxid)` and `mxid_age(datminmxid)`; anti-wraparound autovacuum starts at 200 million.
- The **cumulative statistics** live in shared memory, flushed by backends about once a second, and are lost after a crash. The **planner statistics** (`pg_stats`: null fraction, `n_distinct`, MCVs, histograms, correlation) come from `ANALYZE`'s sample and drive every row estimate.

## What's next

You now know what every knob in `postgresql.conf` is actually turning. [Module 17](../17-operations/) puts this to work: sizing memory, tuning checkpoints and autovacuum, measuring and fixing bloat, monitoring with the `pg_stat_*` views, and putting PgBouncer in front of the server.
