# Module 14: Partitioning

<!-- nav:start -->
> **Theory** · [Exercises](exercises.md) · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 13 Full-Text and Fuzzy Search](../13-full-text-search/README.md) · [Course home](../../README.md) · [15 Security and Row-Level Security →](../15-security-rls/README.md)
<!-- nav:end -->

Every click on Bazaar becomes a row in `events`: 5 million in 2025, and growing every month. The analysts query it by month ("what was November's conversion funnel?"), the privacy policy says clickstream data is kept for 12 months, and every month somebody has to delete the oldest month. On a single 5-million-row table, that monthly `DELETE` writes tens of megabytes of WAL, leaves dead rows for VACUUM to clean, and returns no disk space to the operating system. With **partitioning**, `events` becomes a set of monthly tables behind one name: a query for November reads only November, and deleting January is a `DROP TABLE` that takes a millisecond. This module explains how PostgreSQL's declarative partitioning works, what it really buys you (and what it costs: it is *not* a general speed-up), how to maintain partitions month after month, and how to convert a big table that is already in production.

**What you'll learn**

- The problems partitioning solves (retention, partition pruning, maintenance in smaller units, hot and cold data) and the ones it doesn't.
- Declarative partitioning: `RANGE`, `LIST` and `HASH`, bound semantics, the `DEFAULT` partition and its traps, sub-partitioning, and the catalog functions that describe a partition tree.
- Why every primary key and unique constraint must include the partition key, and how identity columns, `CHECK`, `NOT NULL` and foreign keys behave on partitioned tables.
- Partitioned indexes, and building them without long locks (`ON ONLY` + `CREATE INDEX CONCURRENTLY` + `ATTACH PARTITION`).
- Partition pruning at plan time and at execution time, how to see it in `EXPLAIN`, and which `WHERE` clauses prevent it.
- The costs: planning time, point lookups that don't use the key, and how many partitions is too many.
- Partitionwise joins and aggregates.
- Inserting, `COPY`, and row movement when an `UPDATE` changes the partition key.
- The maintenance routine: creating partitions ahead, `ATTACH PARTITION` without a validation scan, `DETACH PARTITION [CONCURRENTLY]`, retention by `DROP` instead of `DELETE` (with measured time and WAL), archiving; `pg_partman` and `pg_cron`.
- Migrating an existing big table to partitioning with near-zero downtime.
- Partitioning versus sharding, and the legacy inheritance-based partitioning.

**Before you start**

```bash
./project/bin/reset.sh 15     # large dataset + this module's reference events table (a few minutes)
./project/bin/psql.sh
```

`reset.sh 15` is the state at the *end* of this module: it contains [`project/db/1401_schema_events.sql`](../../project/db/1401_schema_events.sql) (the partitioned `events` table) and [`1402_seed_events.sql`](../../project/db/1402_seed_events.sql) (4,983,725 events of 2025). The theory needs that data to show real plans and timings. In the milestone you build the same thing yourself, starting from `reset.sh 14`.

Some demos compare `events` with an unpartitioned copy, and some create small throw-away tables. They all live in a schema called `lab`:

```sql
CREATE SCHEMA IF NOT EXISTS lab;

SET maintenance_work_mem = '512MB';
CREATE TABLE lab.events_flat AS SELECT * FROM events;                           -- 5 s
ALTER TABLE lab.events_flat ADD PRIMARY KEY (id);
CREATE INDEX events_flat_occurred_at_idx ON lab.events_flat (occurred_at);
CREATE INDEX events_flat_product_id_idx ON lab.events_flat (product_id);
VACUUM ANALYZE lab.events_flat;
```

The copy takes about 790 MB; drop it (`DROP SCHEMA lab CASCADE`) when you are done. Timings were measured on a 4-vCPU container with the default configuration and a warm cache. Yours will differ, but the ratios won't change much. "Today" in the large dataset is 2025-12-31, but `now()` is your real clock, which matters in section 5.

You need [Module 11](../11-indexes/) (indexes, `CREATE INDEX CONCURRENTLY`), [Module 12](../12-query-performance/) (`EXPLAIN ANALYZE`), and the lock levels from [Module 10](../10-transactions-concurrency/).

---

<!-- toc:start -->
**Contents**

- [1. What partitioning is for, and what it is not for](#1-what-partitioning-is-for-and-what-it-is-not-for)
- [2. Declarative partitioning](#2-declarative-partitioning)
  - [2.1 RANGE partitioning and bound semantics](#21-range-partitioning-and-bound-semantics)
  - [2.2 LIST and HASH partitioning](#22-list-and-hash-partitioning)
  - [2.3 The DEFAULT partition](#23-the-default-partition)
  - [2.4 Sub-partitioning](#24-sub-partitioning)
  - [2.5 Looking at a partition tree](#25-looking-at-a-partition-tree)
- [3. Keys and constraints](#3-keys-and-constraints)
  - [3.1 Unique constraints must include the partition key](#31-unique-constraints-must-include-the-partition-key)
  - [3.2 Identity columns, `NOT NULL`, `CHECK`, defaults](#32-identity-columns-not-null-check-defaults)
  - [3.3 Foreign keys](#33-foreign-keys)
- [4. Indexes on partitioned tables](#4-indexes-on-partitioned-tables)
  - [4.1 Building an index without blocking writes](#41-building-an-index-without-blocking-writes)
  - [4.2 No global index: lookups without the key](#42-no-global-index-lookups-without-the-key)
- [5. Partition pruning](#5-partition-pruning)
  - [5.1 Plan-time pruning](#51-plan-time-pruning)
  - [5.2 Execution-time pruning](#52-execution-time-pruning)
  - [5.3 What prevents pruning](#53-what-prevents-pruning)
  - [5.4 Settings](#54-settings)
- [6. The costs: planning, locking, and how many partitions](#6-the-costs-planning-locking-and-how-many-partitions)
- [7. Partitionwise joins and aggregates](#7-partitionwise-joins-and-aggregates)
- [8. Writing to a partitioned table](#8-writing-to-a-partitioned-table)
  - [8.1 Inserts and `COPY`](#81-inserts-and-copy)
  - [8.2 Row movement on `UPDATE`](#82-row-movement-on-update)
- [9. Maintenance](#9-maintenance)
  - [9.1 Creating partitions ahead of time](#91-creating-partitions-ahead-of-time)
  - [9.2 The default-partition trap](#92-the-default-partition-trap)
  - [9.3 `ATTACH` and `DETACH`](#93-attach-and-detach)
  - [9.4 Retention: `DROP` versus `DELETE`](#94-retention-drop-versus-delete)
  - [9.5 Archiving a detached partition](#95-archiving-a-detached-partition)
  - [9.6 VACUUM, ANALYZE and statistics](#96-vacuum-analyze-and-statistics)
  - [9.7 Automating it: `pg_partman` and `pg_cron`](#97-automating-it-pg_partman-and-pg_cron)
- [10. Migrating an existing table to partitioning](#10-migrating-an-existing-table-to-partitioning)
- [11. Beyond one server, and before PostgreSQL 10](#11-beyond-one-server-and-before-postgresql-10)
- [Summary](#summary)
- [What's next](#whats-next)

<!-- toc:end -->

## 1. What partitioning is for, and what it is not for

A **partitioned table** is a table that stores no rows itself. It is a router in front of a set of ordinary tables, its **partitions**, each holding the rows whose **partition key** falls in its range or list. Queries and writes go to the parent. PostgreSQL sends each new row to the right partition, and each query reads only the partitions that can contain matching rows.

```text
                       events  (partitioned table: no storage)
                       PARTITION BY RANGE (occurred_at)
                                     │
     ┌───────────────┬───────────────┼───────────────┬──────────────────┐
     ▼               ▼               ▼               ▼                  ▼
 events_y2025m01 events_y2025m02    ...       events_y2026m12     events_default
 [2025-01-01,    [2025-02-01,                 [2026-12-01,        everything else
  2025-02-01)     2025-03-01)                  2027-01-01)
 326,131 rows    307,750 rows                 0 rows              0 rows
 its own heap, its own indexes, its own VACUUM, its own statistics
```

Partitioning solves four problems well.

**1. Retention and bulk deletion.** Removing a month of data from a single table is a `DELETE` of hundreds of thousands of rows: every row version is marked dead (WAL for each), every index keeps pointing to them until VACUUM removes the entries (more WAL), and the file doesn't shrink. With partitions, the month is a table: `DROP TABLE` unlinks its files. Section 9.4 measures it: 0.6 s and 89 MB of WAL for `DELETE` + `VACUUM` on the unpartitioned copy, against 2 ms and 0.2 MB to detach and drop the partition.

**2. Partition pruning.** A query with `WHERE occurred_at >= '2025-11-01' AND occurred_at < '2025-12-01'` reads one partition (509,415 rows, 88 MB) instead of the whole table. A sequential scan of one month beats a sequential scan of twelve.

**3. Maintenance in smaller units.** VACUUM, ANALYZE, `REINDEX` and `CLUSTER` work per partition. Old partitions stop changing, so after one `VACUUM (FREEZE)` they never need vacuuming again, and anti-wraparound VACUUM ([Module 16](../16-internals/)) has much less to do. You can rebuild one month's index without touching the others.

**4. Hot and cold data.** Recent partitions are small and in cache. Old ones can move to a cheaper tablespace, be detached and archived, or get different storage settings.

Partitioning does **not** make a database faster in general, and it can make it slower:

- **It doesn't beat a good index for selective queries.** On the unpartitioned copy, the same November query uses the `occurred_at` index on a table whose rows are physically in time order. Counting November's orders took 98 ms on the copy and 82 ms on the partitioned table (averages of 15 runs). A B-tree descent is already logarithmic; cutting the table into 12 pieces saves one or two levels.
- **Queries that don't filter on the partition key get slower**, because they must visit every partition. Fetching one event by `id` (the key is `occurred_at`) is a single index probe on the unpartitioned table, and 25 index probes on `events`. With `pgbench`: 0.07 ms per lookup on the copy, 1.16 ms on the partitioned table (0.03 ms against 0.13 ms with prepared statements). Section 6 explains where the time goes.
- **Planning gets more expensive** with the number of partitions, and stays expensive for queries that can't prune.
- **Uniqueness is limited**: no unique constraint that doesn't include the partition key (section 3).

The rule of thumb: partition when you have a **lifecycle problem** (data that arrives in time order and leaves in time order) or a table so large that per-table maintenance becomes a problem (hundreds of gigabytes or more), and when **almost every query filters on the partition key**. `events` and `audit_log` are textbook cases. `orders` is not: queries look orders up by `id`, `user_id` and `seller_id`, and nobody deletes old orders.

---

## 2. Declarative partitioning

Since PostgreSQL 10, partitioning is declared with `PARTITION BY` on the parent and `PARTITION OF ... FOR VALUES` on each partition. This is how [`1401_schema_events.sql`](../../project/db/1401_schema_events.sql) creates `events`:

```sql
CREATE TABLE events (
    id           bigint      GENERATED ALWAYS AS IDENTITY,
    occurred_at  timestamptz NOT NULL,
    user_id      bigint,                                      -- NULL = anonymous visitor
    session_id   uuid        NOT NULL,
    event_type   text        NOT NULL,
    product_id   bigint,
    payload      jsonb       NOT NULL DEFAULT '{}',
    CONSTRAINT events_event_type_check CHECK (event_type IN (
        'page_view', 'product_view', 'add_to_cart', 'remove_from_cart',
        'checkout_started', 'order_placed', 'search')),
    PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);

CREATE TABLE events_y2025m01 PARTITION OF events
    FOR VALUES FROM ('2025-01-01 00:00:00+00') TO ('2025-02-01 00:00:00+00');
-- ... one per month, created by create_events_partition() (section 9.1) ...
CREATE TABLE events_default PARTITION OF events DEFAULT;

CREATE INDEX events_occurred_at_idx ON events (occurred_at);
CREATE INDEX events_product_id_idx ON events (product_id);
```

The parent has the columns, constraints and indexes; the partitions inherit all of them. Each partition is a real table: you can query it directly (`SELECT count(*) FROM events_y2025m11`), and it has its own files, statistics and VACUUM schedule.

### 2.1 RANGE partitioning and bound semantics

A range partition holds the rows whose key satisfies **`FROM <= key < TO`**: the lower bound is inclusive, the upper bound exclusive. Adjacent partitions therefore share a boundary value without overlapping, and there can be no gap between `TO` of one month and `FROM` of the next. PostgreSQL rejects overlapping ranges when you create a partition.

Details worth knowing:

- **`MINVALUE` and `MAXVALUE`** stand for "unbounded": `FOR VALUES FROM (MINVALUE) TO ('2025-01-01')` catches everything older. Section 10 uses this to attach a whole legacy table as one partition.
- **The bounds of a `timestamptz` key are instants.** `'2025-02-01'` without a time zone is interpreted in the session's `TimeZone` when the partition is created. 1401 writes `'2025-02-01 00:00:00+00'` explicitly, so the partitions are UTC months whatever the session's setting. Mixing both styles creates gaps or overlap errors.
- **Multi-column range keys** (`PARTITION BY RANGE (a, b)`) compare bounds lexicographically, like a composite B-tree key: `FROM (1, 10) TO (1, 20)` and `FROM (1, 20) TO (2, 0)`. They are rarely what you want; prefer sub-partitioning (section 2.4).
- **The key can be an expression**: `PARTITION BY RANGE ((created_at::date))`. Queries then only prune if they use the same expression.

A row that fits no partition is an error:

```sql
CREATE TABLE lab.r (id int, at timestamptz NOT NULL) PARTITION BY RANGE (at);
CREATE TABLE lab.r_2025 PARTITION OF lab.r FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
INSERT INTO lab.r VALUES (1, '2026-01-01');
```

```text
ERROR:  no partition of relation "r" found for row
DETAIL:  Partition key of the failing row contains (at) = (2026-01-01 00:00:00+00).
```

### 2.2 LIST and HASH partitioning

**LIST** partitions by explicit values, for categorical keys:

```sql
CREATE TABLE lab.events_by_type (LIKE events) PARTITION BY LIST (event_type);
CREATE TABLE lab.events_views  PARTITION OF lab.events_by_type FOR VALUES IN ('page_view', 'product_view');
CREATE TABLE lab.events_funnel PARTITION OF lab.events_by_type
    FOR VALUES IN ('add_to_cart', 'remove_from_cart', 'checkout_started', 'order_placed');
CREATE TABLE lab.events_search PARTITION OF lab.events_by_type FOR VALUES IN ('search');
```

A value may appear in only one partition. `NULL` can be listed too (`FOR VALUES IN (NULL)`); otherwise NULL keys go to the default partition, or fail. LIST fits when the categories are few and stable and the queries filter on them: per-country tables, per-tenant tables for a handful of large tenants, or separating rarely-read event types.

**HASH** spreads rows evenly by a hash of the key: `FOR VALUES WITH (MODULUS 4, REMAINDER 0)` holds the rows whose `hash(key) mod 4 = 0`. Every remainder from 0 to modulus − 1 must have a partition, or inserts with those hashes fail. Hash partitioning gives you no lifecycle benefits (old data is spread over all partitions) and prunes only on equality (`WHERE user_id = 42`). It is useful for splitting one very large table into smaller units for VACUUM and index maintenance, and for partitionwise joins between tables partitioned the same way (section 7). Changing the number of hash partitions later means rewriting the data, though a modulus can be split in place: replace the partition for remainder 0 of modulus 4 by two partitions for remainders 0 and 4 of modulus 8.

### 2.3 The DEFAULT partition

A `DEFAULT` partition catches every row that matches no other partition. `events_default` exists so that an event with a wrong or far-future timestamp lands somewhere instead of failing the insert. It costs you three things:

1. **It blocks new partitions whose range already has rows in it.** If an event dated 2027-03-15 arrives before the March 2027 partition exists, it goes to `events_default`, and creating the partition later fails (section 9.2 shows the error and the fix).
2. **Creating or attaching any partition must check the default partition.** PostgreSQL scans `events_default` to prove that none of its rows belongs to the new partition, under a lock that blocks writes to it. Harmless while it is empty; slow if it has filled up.
3. **It prevents `DETACH PARTITION ... CONCURRENTLY`** (section 9.3).

Treat a default partition as an alarm, not as storage: keep it empty, monitor it (`SELECT count(*) FROM events_default` should always be 0), and create partitions ahead of time so that nothing ever lands in it. Many teams prefer no default partition at all: an insert for a missing month then fails loudly, which is exactly what makes the gap noticed.

### 2.4 Sub-partitioning

A partition can itself be partitioned, by a different method or key:

```sql
CREATE TABLE lab.ev2 (LIKE events) PARTITION BY RANGE (occurred_at);
CREATE TABLE lab.ev2_y2025m11 PARTITION OF lab.ev2
    FOR VALUES FROM ('2025-11-01 00:00:00+00') TO ('2025-12-01 00:00:00+00')
    PARTITION BY LIST (event_type);
CREATE TABLE lab.ev2_y2025m11_views PARTITION OF lab.ev2_y2025m11 FOR VALUES IN ('page_view', 'product_view');
CREATE TABLE lab.ev2_y2025m11_rest  PARTITION OF lab.ev2_y2025m11 DEFAULT;
```

Pruning works at each level. Sub-partitioning multiplies the number of tables (12 months × 3 types = 36), and with it the planning cost of section 6. Use it only when both levels earn their keep, for example time (for retention) and then tenant (for isolation or per-tenant export).

### 2.5 Looking at a partition tree

```sql
\d+ events            -- partition key, and every partition with its bounds

SELECT relid, parentrelid, isleaf, level FROM pg_partition_tree('events') LIMIT 4;
```

```text
      relid      | parentrelid | isleaf | level 
-----------------+-------------+--------+-------
 events          |             | f      |     0
 events_y2025m01 | events      | t      |     1
 events_y2025m02 | events      | t      |     1
 events_y2025m03 | events      | t      |     1
(4 rows)
```

`pg_partition_root(rel)` and `pg_partition_ancestors(rel)` go the other way. `pg_get_expr(relpartbound, oid)` from `pg_class` gives a partition's bounds as text. The size functions don't aggregate over partitions: `pg_total_relation_size('events')` returns 0 bytes, because the parent has no storage. Sum over the tree instead:

```sql
SELECT pg_size_pretty(sum(pg_total_relation_size(relid))) FROM pg_partition_tree('events');
```

```text
 pg_size_pretty 
----------------
 861 MB
(1 row)
```

Every row knows where it lives: the system column `tableoid` is the OID of the partition that stores it, so `SELECT tableoid::regclass, count(*) FROM events GROUP BY 1` counts rows per partition.

---

## 3. Keys and constraints

### 3.1 Unique constraints must include the partition key

`events` has `PRIMARY KEY (id, occurred_at)`, not `PRIMARY KEY (id)`, even though `id` alone comes from an identity sequence and is unique in practice. Try to declare it:

```sql
ALTER TABLE events ADD CONSTRAINT events_id_key UNIQUE (id);
```

```text
ERROR:  unique constraint on partitioned table must include all partitioning columns
DETAIL:  UNIQUE constraint on table "events" lacks column "occurred_at" which is part of the partition key.
```

The reason is that PostgreSQL has **no global indexes**. An index on a partitioned table is really one independent B-tree per partition. A B-tree can enforce uniqueness only within itself, so `UNIQUE (id)` could only promise "unique within each month". If the key includes the partition key, though, two rows with the same key value are guaranteed to be in the same partition, and the per-partition B-trees together enforce global uniqueness.

Consequences:

- **"Unique `id`" is enforced by the sequence, not by a constraint.** That is fine for an identity column that nobody writes explicitly (`GENERATED ALWAYS` rejects explicit values unless you say `OVERRIDING SYSTEM VALUE`).
- **Natural keys that must be unique across all partitions** (an email address, an idempotency key) can't be enforced on a partitioned table. Keep them in an unpartitioned table, or partition by that key.
- **`ON CONFLICT`** needs a unique index to infer the conflict target, so upserts work only on keys that include the partition key.

### 3.2 Identity columns, `NOT NULL`, `CHECK`, defaults

Columns, `NOT NULL`, `CHECK` constraints and defaults are defined on the parent and apply to all partitions. A partition can add its own `CHECK` constraints and defaults, but it can't drop the parent's. Identity columns on partitioned tables are fully supported from PostgreSQL 17: the parent owns the sequence, and rows inserted directly into a partition draw from it too. A table that *has its own* identity column can't be attached as a partition; section 10 meets that rule.

### 3.3 Foreign keys

Both directions are supported, with caveats.

**From a partitioned table to another table** (for example `events.user_id → users.id`): supported since PostgreSQL 11. 1401 deliberately doesn't declare them. Events are an append-only log that must keep flowing even if a user is deleted, an FK check on every insert would add an index lookup to the hottest write path in the system, and analytics tolerates the occasional dangling id.

**From another table to a partitioned table**: supported since PostgreSQL 12, but the referenced columns must be a unique constraint of the partitioned table, which means they must include the partition key:

```sql
CREATE TABLE lab.event_notes (id bigint PRIMARY KEY, event_id bigint NOT NULL REFERENCES events (id));
```

```text
ERROR:  there is no unique constraint matching given keys for referenced table "events"
```

You need to carry the partition key along:

```sql
CREATE TABLE lab.event_notes (
    id                 bigint PRIMARY KEY,
    event_id           bigint NOT NULL,
    event_occurred_at  timestamptz NOT NULL,
    FOREIGN KEY (event_id, event_occurred_at) REFERENCES events (id, occurred_at)
);
INSERT INTO lab.event_notes SELECT 1, id, occurred_at FROM events WHERE id = 1234567;
```

And the FK now constrains maintenance. Detaching (or dropping) the partition that holds a referenced row fails:

```sql
ALTER TABLE events DETACH PARTITION events_y2025m04;
```

```text
ERROR:  removing partition "events_y2025m04" violates foreign key constraint "event_notes_event_id_event_occurred_at_fkey4"
DETAIL:  Key (event_id, event_occurred_at)=(1234567, 2025-04-21 14:55:00+00) is still referenced from table "event_notes".
```

So your retention job must delete the referencing rows first, and checking this constraint scans the referencing table on every detach. For a table you partition *for retention*, inbound foreign keys work against the purpose. Avoid them, or partition the referencing table the same way and drop matching partitions together.

---

## 4. Indexes on partitioned tables

`CREATE INDEX ON events (...)` creates a **partitioned index**: an index definition on the parent, plus a real index on every partition, attached to it. Partitions created later automatically get their own index. There is no global index structure.

### 4.1 Building an index without blocking writes

`CREATE INDEX CONCURRENTLY` doesn't work on a partitioned table:

```sql
CREATE INDEX CONCURRENTLY events_user_id_idx ON events (user_id);
```

```text
ERROR:  cannot create index on partitioned table "events" concurrently
```

A plain `CREATE INDEX ON events (user_id)` works (1.9 s here), but it holds a `SHARE` lock on each partition while building, which blocks every insert into `events` until it is done. On a live table that takes hours to index, that is an outage. The standard workaround uses three steps:

```sql
-- 1. The index on the parent only. Instant, and marked invalid.
CREATE INDEX events_user_id_idx ON ONLY events (user_id);

SELECT indisvalid FROM pg_index WHERE indexrelid = 'events_user_id_idx'::regclass;
--  f

-- 2. Build each partition's index concurrently (no write lock), one by one.
--    \gexec runs every row of the result as a statement.
SELECT format('CREATE INDEX CONCURRENTLY %I ON %s (user_id)', c.relname || '_user_id_idx', c.oid::regclass)
FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
WHERE i.inhparent = 'events'::regclass
ORDER BY c.relname
\gexec

-- 3. Attach each one to the parent index. Instant (catalog only).
SELECT format('ALTER INDEX events_user_id_idx ATTACH PARTITION %I', c.relname || '_user_id_idx')
FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
WHERE i.inhparent = 'events'::regclass
ORDER BY c.relname
\gexec

SELECT indisvalid FROM pg_index WHERE indexrelid = 'events_user_id_idx'::regclass;
--  t
```

The concurrent builds took 0.16–0.42 s per month (about 3 s in total, longer than the blocking build, as usual for `CONCURRENTLY`), and inserts continued throughout. The parent index becomes valid automatically when the last partition's index is attached. Until then, the planner doesn't use it for the parent, and new partitions created in the meantime don't get the index automatically. The same technique works for `REINDEX`: rebuild one partition at a time with `REINDEX INDEX CONCURRENTLY`.

> **Pitfall:** `ON ONLY` + attach works for unique indexes too, but each partition's index must be a unique index on exactly the same columns. To add a primary key to a partitioned table this way, build unique indexes on the partitions concurrently, then add the constraint on the parent: PostgreSQL attaches the matching indexes instead of building new ones.

### 4.2 No global index: lookups without the key

Because every partition has its own index, a lookup that doesn't use the partition key probes all of them:

```sql
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF) SELECT * FROM events WHERE id = 1234567;
```

```text
                                                     QUERY PLAN                                                     
--------------------------------------------------------------------------------------------------------------------
 Append (actual time=0.032..0.099 rows=1 loops=1)
   Buffers: shared hit=37 read=1
   ->  Index Scan using events_y2025m01_pkey on events_y2025m01 events_1 (actual time=0.005..0.005 rows=0 loops=1)
         Index Cond: (id = 1234567)
         Buffers: shared hit=3
   ->  Index Scan using events_y2025m02_pkey on events_y2025m02 events_2 (actual time=0.003..0.003 rows=0 loops=1)
         Index Cond: (id = 1234567)
         Buffers: shared hit=3
   ->  Index Scan using events_y2025m03_pkey on events_y2025m03 events_3 (actual time=0.006..0.006 rows=0 loops=1)
         Index Cond: (id = 1234567)
         Buffers: shared hit=3
   ->  Index Scan using events_y2025m04_pkey on events_y2025m04 events_4 (actual time=0.017..0.018 rows=1 loops=1)
         Index Cond: (id = 1234567)
         Buffers: shared hit=3 read=1
...
   ->  Seq Scan on events_default events_25 (actual time=0.000..0.001 rows=0 loops=1)
         Filter: (id = 1234567)
 Planning:
   Buffers: shared hit=354
 Planning Time: 1.019 ms
 Execution Time: 0.149 ms
(72 rows)
```

38 buffers instead of 4, and a planning time of 1 ms (25 partitions to consider) for a query that executes in 0.15 ms. Under `pgbench` this was the 16× difference quoted in section 1. If your application needs fast lookups by `id`, include the key in the lookup (`WHERE id = $1 AND occurred_at = $2`, which an API can do by embedding the timestamp in the public identifier), or keep a small unpartitioned table mapping ids to timestamps.

---

## 5. Partition pruning

**Partition pruning** is the planner's ability to skip partitions that can't contain matching rows, by comparing the query's conditions on the partition key with each partition's bounds. It happens at two moments, and `EXPLAIN` shows both.

### 5.1 Plan-time pruning

When the conditions compare the key with **constants** using **immutable** operators, the planner prunes while planning, and the skipped partitions don't appear in the plan at all:

```sql
EXPLAIN (ANALYZE, COSTS OFF)
SELECT count(*) FROM events
WHERE occurred_at >= '2025-11-01' AND occurred_at < '2025-12-01';
```

```text
                                                                                QUERY PLAN                                                                                
--------------------------------------------------------------------------------------------------------------------------------------------------------------------------
 Finalize Aggregate (actual time=60.075..62.282 rows=1 loops=1)
   ->  Gather (actual time=59.957..62.273 rows=3 loops=1)
         Workers Planned: 2
         Workers Launched: 2
         ->  Partial Aggregate (actual time=54.788..54.789 rows=1 loops=3)
               ->  Parallel Seq Scan on events_y2025m11 events (actual time=0.033..31.853 rows=169805 loops=3)
                     Filter: ((occurred_at >= '2025-11-01 00:00:00+00'::timestamp with time zone) AND (occurred_at < '2025-12-01 00:00:00+00'::timestamp with time zone))
 Planning Time: 1.933 ms
 Execution Time: 62.382 ms
```

Only November is in the plan. Note the sequential scan: the whole partition matches, so its index is useless, and PostgreSQL reads exactly the 88 MB it needs.

### 5.2 Execution-time pruning

When a comparison value isn't known at plan time, PostgreSQL can still prune when the executor starts ("initial pruning", once the values of parameters and stable functions are known), or even during execution, each time a parameter changes (for example on the inner side of a nested loop). `EXPLAIN` shows the first as **`Subplans Removed: N`** and the second as partitions marked **`(never executed)`**.

`now()` is *stable*, not immutable (its value changes between transactions), so it is evaluated at executor startup:

```sql
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF)
SELECT count(*) FROM events WHERE occurred_at >= now() - interval '7 days';
```

```text
                                QUERY PLAN                                
--------------------------------------------------------------------------
 Aggregate (actual rows=1 loops=1)
   ->  Append (actual rows=0 loops=1)
         Subplans Removed: 21
         ->  Seq Scan on events_y2026m10 events_1 (actual rows=0 loops=1)
               Filter: (occurred_at >= (now() - '7 days'::interval))
         ->  Seq Scan on events_y2026m11 events_2 (actual rows=0 loops=1)
               Filter: (occurred_at >= (now() - '7 days'::interval))
         ->  Seq Scan on events_y2026m12 events_3 (actual rows=0 loops=1)
               Filter: (occurred_at >= (now() - '7 days'::interval))
         ->  Seq Scan on events_default events_4 (actual rows=0 loops=1)
               Filter: (occurred_at >= (now() - '7 days'::interval))
 Planning Time: 5.313 ms
 Execution Time: 0.104 ms
(13 rows)
```

(This ran in October 2026; your clock gives other partitions.) All 25 partitions were planned, and 21 removed before execution. The same happens with prepared statements that use a **generic plan**, where `$1` is unknown at plan time:

```sql
PREPARE day_count(timestamptz) AS
    SELECT count(*) FROM events WHERE occurred_at >= $1 AND occurred_at < $1 + interval '1 day';
SET plan_cache_mode = force_generic_plan;
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF) EXECUTE day_count('2025-11-28');
```

```text
                                                        QUERY PLAN                                                         
---------------------------------------------------------------------------------------------------------------------------
 Aggregate (actual rows=1 loops=1)
   ->  Append (actual rows=22585 loops=1)
         Subplans Removed: 24
         ->  Index Only Scan using events_y2025m11_occurred_at_idx on events_y2025m11 events_1 (actual rows=22585 loops=1)
               Index Cond: ((occurred_at >= $1) AND (occurred_at < ($1 + '1 day'::interval)))
               Heap Fetches: 0
 Planning Time: 5.165 ms
 Execution Time: 2.872 ms
(8 rows)
```

And with a nested loop, pruning happens again for every outer row. Here three days are looked up, and only their three months are ever scanned:

```sql
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF)
SELECT d.day, count(*)
FROM (VALUES (timestamptz '2025-03-08'), ('2025-07-19'), ('2025-11-28')) AS d (day)
CROSS JOIN LATERAL (
    SELECT 1 FROM events e WHERE e.occurred_at >= d.day AND e.occurred_at < d.day + interval '1 day'
) e
GROUP BY d.day;
```

```text
 HashAggregate (actual rows=3 loops=1)
   Group Key: "*VALUES*".column1
   ->  Nested Loop (actual rows=49529 loops=1)
         ->  Values Scan on "*VALUES*" (actual rows=3 loops=1)
         ->  Append (actual rows=16510 loops=3)
               ->  Index Only Scan using events_y2025m01_occurred_at_idx on events_y2025m01 e_1 (never executed)
               ->  Index Only Scan using events_y2025m02_occurred_at_idx on events_y2025m02 e_2 (never executed)
               ->  Index Only Scan using events_y2025m03_occurred_at_idx on events_y2025m03 e_3 (actual rows=12462 loops=1)
               ...
               ->  Index Only Scan using events_y2025m07_occurred_at_idx on events_y2025m07 e_7 (actual rows=14482 loops=1)
               ...
               ->  Index Only Scan using events_y2025m11_occurred_at_idx on events_y2025m11 e_11 (actual rows=22585 loops=1)
               ...
               ->  Seq Scan on events_default e_25 (never executed)
 Execution Time: 9.659 ms
```

> **Under the hood:** Execution-time pruning saves the *scanning*, not the *planning*: the planner still built a plan with all 25 partitions, and the executor still opened and locked them. With thousands of partitions, that overhead dominates short queries (section 6). Plan-time pruning avoids it entirely.

### 5.3 What prevents pruning

Pruning works by comparing the **bare partition key column** with values. Anything that wraps the column in a function or cast hides it from the planner, exactly like a function hides a column from a B-tree index ([Module 12](../12-query-performance/)). Each of these was run on `events`:

| `WHERE` clause | Pruning | Execution time |
|---|---|---|
| `occurred_at >= '2025-11-01' AND occurred_at < '2025-12-01'` | plan time, 1 partition | 30 ms |
| `occurred_at >= DATE '2025-11-01' AND occurred_at < DATE '2025-12-01'` | execution time (`Subplans Removed: 24`) | 53 ms |
| `occurred_at >= now() - interval '7 days'` | execution time | < 1 ms |
| `occurred_at >= (SELECT max(created_at) FROM orders) - interval '3 days'` | during execution (`never executed`) | 153 ms (mostly the subquery) |
| `occurred_at < '2025-01-15' OR occurred_at >= '2025-12-20'` | plan time, January + December + everything after | 108 ms |
| `occurred_at::date = '2025-11-15'` | **none**: all 25 partitions | 296 ms |
| `date_trunc('month', occurred_at) = '2025-11-01'` | **none** | 367 ms |
| `extract(month FROM occurred_at) = 11` | **none** | 611 ms |

Three of these deserve a comment:

- **The `DATE` literal** prunes only at execution time, because comparing a `timestamptz` with a `date` converts the date using the session's `TimeZone`: that operator is stable, not immutable. Pruning still happens, but the plan contains all partitions. Write timestamps with an explicit offset (`'2025-11-01 00:00:00+00'`), or plain untyped literals, which become `timestamptz` constants.
- **`occurred_at::date = ...` and `date_trunc(...)`** are the most common mistakes in reporting queries, and they read the whole table. Rewrite them as half-open ranges: `occurred_at >= '2025-11-15' AND occurred_at < '2025-11-16'`.
- **`BETWEEN '2025-11-01' AND '2025-11-30'`** prunes fine, but it is a correctness bug: it stops at 2025-11-30 00:00:00 and misses the last day. Half-open ranges (`>=` and `<`) are right for timestamps and for partition bounds alike.

A month-level funnel shows what this means in practice. Counting the sessions that viewed, carted, checked out and ordered in November:

```sql
WITH s AS (
    SELECT session_id,
           bool_or(event_type = 'product_view')     AS viewed,
           bool_or(event_type = 'add_to_cart')      AS carted,
           bool_or(event_type = 'checkout_started') AS checked_out,
           bool_or(event_type = 'order_placed')     AS ordered
    FROM events
    WHERE occurred_at >= '2025-11-01' AND occurred_at < '2025-12-01'
    GROUP BY session_id
)
SELECT count(*) FILTER (WHERE viewed)      AS viewed,
       count(*) FILTER (WHERE carted)      AS carted,
       count(*) FILTER (WHERE checked_out) AS checked_out,
       count(*) FILTER (WHERE ordered)     AS ordered,
       round(100.0 * count(*) FILTER (WHERE ordered) / count(*) FILTER (WHERE viewed), 2) AS conversion_pct
FROM s;
```

```text
 viewed | carted | checked_out | ordered | conversion_pct 
--------+--------+-------------+---------+----------------
  78378 |  21848 |       12039 |    9069 |          11.57
(1 row)
```

464 ms as written; 812 ms with `WHERE date_trunc('month', occurred_at) = '2025-11-01'`, which reads all twelve months to find the same rows. The grouping by session costs the same in both, so the difference is "only" 1.75×; for the plain counts in the table above it was 30 ms against 367 ms.

### 5.4 Settings

- **`enable_partition_pruning`** (on): turns both kinds of pruning off when set to `off`. Only useful to demonstrate what pruning saves.
- **`constraint_exclusion`** (`partition`): the older mechanism, which proves from `CHECK` constraints that a table can't match. Declarative partitioning doesn't need it; it still matters for inheritance-based partitioning (section 11) and for `UNION ALL` views over tables with `CHECK` constraints.

---

## 6. The costs: planning, locking, and how many partitions

Every partition that survives plan-time pruning costs planning work: the planner builds paths for it, reads its statistics, and the executor opens and locks it. To see the effect of the partition count, here is a table with 1,000 daily partitions and 100,000 rows:

```sql
CREATE TABLE lab.many (id bigint, d date NOT NULL, v int) PARTITION BY RANGE (d);
SELECT format('CREATE TABLE lab.many_%s PARTITION OF lab.many FOR VALUES FROM (%L) TO (%L)',
              i, date '2024-01-01' + i, date '2024-01-01' + i + 1)
FROM generate_series(0, 999) AS i
\gexec
CREATE INDEX ON lab.many (id);
INSERT INTO lab.many SELECT i, date '2024-01-01' + (i % 1000), i FROM generate_series(1, 100000) AS i;
ANALYZE lab.many;
```

Measured with `pgbench` (one client, each statement a lookup of a random `id`):

| Query | Simple protocol | Prepared |
|---|---|---|
| `WHERE id = :id` (no pruning: 1,000 index probes) | 23.8 ms | 10.9 ms |
| `WHERE id = :id AND d = <its date>` (one partition) | 0.11 ms | 0.11 ms |

`EXPLAIN ANALYZE` of the unpruned query: planning 20–50 ms, execution 15–20 ms. An `UPDATE ... WHERE id = 5` without the key: 16 ms of planning and 23 ms of execution. With pruning, none of that matters.

Guidelines that follow:

- **Hundreds of partitions are fine** if nearly all queries prune at plan time. Thousands work for queries that always prune, but every unpruned query, including those of tools and ad-hoc analysts, becomes slow, and memory use per backend grows (each backend caches metadata of every partition it touches).
- **Choose the granularity from the retention and the query patterns**, not from "smaller is faster". Monthly partitions for 12 months of retention give 13 partitions; daily partitions for the same data give 366 for no benefit, unless queries are mostly about single days *and* daily drops matter.
- **Partition sizes from a few GB to a few tens of GB** are a common target: big enough that the count stays low, small enough that per-partition maintenance is quick.

> **Under the hood:** With the simple query protocol, a query is planned every time, so a non-pruning query pays the full planning cost on every call. Prepared statements reuse a generic plan after five executions, which pays the planning once, but then pruning can only happen at executor startup, which still opens and locks every partition in the plan. That is why the prepared column improves but doesn't fix the unpruned lookup.

---

## 7. Partitionwise joins and aggregates

When two tables are partitioned **identically** (same method, same bounds, same key types) and joined on their partition keys, the join can be done partition by partition: partition 0 of one with partition 0 of the other, and so on. Each piece is smaller, so hash tables fit in `work_mem`, and the pieces can run in parallel. The planner only considers this when **`enable_partitionwise_join`** is on. It is off by default because it multiplies planning work by the number of partitions, even for queries that don't benefit.

To try it, hash-partition copies of `orders` and `order_items` by order id, 4 partitions each:

```sql
CREATE TABLE lab.orders_h (LIKE orders) PARTITION BY HASH (id);
CREATE TABLE lab.order_items_h (LIKE order_items) PARTITION BY HASH (order_id);
SELECT format('CREATE TABLE lab.orders_h_%s PARTITION OF lab.orders_h FOR VALUES WITH (MODULUS 4, REMAINDER %s)', r, r)
FROM generate_series(0, 3) AS r \gexec
SELECT format('CREATE TABLE lab.order_items_h_%s PARTITION OF lab.order_items_h FOR VALUES WITH (MODULUS 4, REMAINDER %s)', r, r)
FROM generate_series(0, 3) AS r \gexec
INSERT INTO lab.orders_h SELECT * FROM orders;
INSERT INTO lab.order_items_h SELECT * FROM order_items;
ANALYZE lab.orders_h, lab.order_items_h;

SET max_parallel_workers_per_gather = 0;     -- compare single-process plans
SET enable_partitionwise_join = on;
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF)
SELECT o.seller_id, sum(oi.quantity * oi.unit_price)
FROM lab.orders_h o JOIN lab.order_items_h oi ON oi.order_id = o.id
GROUP BY o.seller_id ORDER BY 2 DESC LIMIT 1;
```

```text
 Limit (actual rows=1 loops=1)
   ->  Sort (actual rows=1 loops=1)
         ->  HashAggregate (actual rows=1992 loops=1)
               Group Key: o.seller_id
               ->  Append (actual rows=1266928 loops=1)
                     ->  Hash Join (actual rows=315758 loops=1)
                           Hash Cond: (oi_1.order_id = o_1.id)
                           ->  Seq Scan on order_items_h_0 oi_1 (actual rows=315758 loops=1)
                           ->  Hash (actual rows=124818 loops=1)
                                 Buckets: 131072  Batches: 1  Memory Usage: 6875kB
                                 ->  Seq Scan on orders_h_0 o_1 (actual rows=124818 loops=1)
                     ->  Hash Join (actual rows=317917 loops=1)
                     ...
 Execution Time: 798.056 ms
```

With the setting off, the plan is one big hash join whose hash table of 500,000 orders needs 4 batches (spilling to disk with the default 4 MB `work_mem`), and it took 1,467 ms. Partitionwise, four hash tables of 125,000 orders each fit in memory: 798 ms.

**`enable_partitionwise_aggregate`** (also off by default) does the same for `GROUP BY`. If the grouping columns include the partition key, each partition is aggregated completely on its own. Otherwise PostgreSQL aggregates each partition partially and combines the results (`Partial HashAggregate` per partition under a `Finalize HashAggregate`), which helps mainly when the partial aggregates are much smaller than the input. For a monthly report that groups `events` by `session_id`, it reduced the time from 867 ms to 725 ms, mostly because the per-partition hash tables spilled less to disk.

Both settings are good candidates for `SET LOCAL` in a reporting session or `ALTER ROLE bazaar_analyst SET ...` (that role arrives with Module 15's `1501`), rather than for the whole server.

---

## 8. Writing to a partitioned table

### 8.1 Inserts and `COPY`

`INSERT` and `COPY` into the parent route each row to its partition ("tuple routing"). The routing is fast, a binary search over the bounds per row, and a multi-row insert or `COPY` can span many partitions. 1402 inserted 4,983,725 rows into 13 partitions with one `INSERT ... SELECT`. You can also insert directly into a partition (`INSERT INTO events_y2025m11 ...`), which skips routing and checks the row against the partition's bounds.

Triggers: row-level `BEFORE` and `AFTER` triggers defined on the parent are cloned to every partition. A `BEFORE` trigger fires on the partition chosen by routing, so it can't change the key to send the row elsewhere (that raises an error).

### 8.2 Row movement on `UPDATE`

An `UPDATE` that changes the partition key so that the row belongs to another partition **moves** it, by deleting it from the old partition and inserting it into the new one:

```sql
BEGIN;
SELECT tableoid::regclass, id, occurred_at FROM events WHERE id = 1234567;
UPDATE events SET occurred_at = occurred_at + interval '1 month'
WHERE id = 1234567
RETURNING tableoid::regclass, id, occurred_at;
ROLLBACK;
```

```text
    tableoid     |   id    |      occurred_at       
-----------------+---------+------------------------
 events_y2025m04 | 1234567 | 2025-04-21 14:55:00+00
(1 row)

    tableoid     |   id    |      occurred_at       
-----------------+---------+------------------------
 events_y2025m05 | 1234567 | 2025-05-21 14:55:00+00
(1 row)
```

Things to know:

- It costs a delete plus an insert, and fires the `DELETE` and `INSERT` triggers of the partitions involved (plus the `UPDATE` triggers of the parent).
- **Concurrency**: in a single table, a transaction waiting to update a row follows the update chain to the new version. A moved row has no such chain. A concurrent `UPDATE`, `DELETE` or `SELECT ... FOR UPDATE` of the row fails with `ERROR: tuple to be locked was already moved to another partition due to concurrent update` (a serialization failure, SQLSTATE `40001`), and the application must retry, as with the serialization errors of [Module 10](../10-transactions-concurrency/).
- For time-based keys, a moving row usually indicates a bug (who rewrites history?). For keys such as `status` (`LIST` partitioning of active and archived orders), moving rows is the normal lifecycle, and the cost and the retry rule apply to every status change.

---

## 9. Maintenance

### 9.1 Creating partitions ahead of time

Partitions must exist before the rows arrive. 1401 includes a function that creates the partition for any month, idempotently:

```sql
SELECT create_events_partition('2027-01-15');   -- any day of the month
```

```text
 create_events_partition 
-------------------------
 events_y2027m01
(1 row)
```

(Drop it again with `DROP TABLE events_y2027m01;`, so that the exercises find the reference layout of 25 partitions.)

It computes the month's bounds in UTC, builds the name `events_yYYYYmMM`, and runs `CREATE TABLE ... PARTITION OF` through `EXECUTE format(...)` only if `to_regclass` says the table doesn't exist yet. Because it is idempotent, a scheduler can call it every day for "next month" and "the month after"; the milestone builds a full maintenance procedure around the same idea. Always keep a margin of several months: if the job breaks, you want weeks of warning, not hours.

> **Pitfall:** `CREATE TABLE ... PARTITION OF` takes an `ACCESS EXCLUSIVE` lock on the parent, briefly, but it has to wait for every running query on `events` to finish, and every new query queues behind it. One long analytics query can turn "create next month's partition" into a stall of all event inserts. Run maintenance with `SET lock_timeout = '5s'` and retry later on failure. The alternative is to create the partition as a standalone table and then `ATTACH` it, which needs only a `SHARE UPDATE EXCLUSIVE` lock on the parent (next section).

### 9.2 The default-partition trap

If a row arrives for a month that has no partition, it goes to `events_default`. Then creating that month fails:

```sql
BEGIN;
INSERT INTO events (occurred_at, session_id, event_type)
VALUES ('2027-03-15 10:00+00', gen_random_uuid(), 'page_view')
RETURNING tableoid::regclass;
SELECT create_events_partition('2027-03-01');
ROLLBACK;
```

```text
    tableoid    
----------------
 events_default
(1 row)

ERROR:  updated partition constraint for default partition "events_default" would be violated by some row
CONTEXT:  SQL statement "CREATE TABLE bazaar.events_y2027m03 PARTITION OF bazaar.events FOR VALUES FROM ('2027-03-01 00:00:00+00') TO ('2027-04-01 00:00:00+00')"
PL/pgSQL function create_events_partition(date) line 9 at EXECUTE
```

The fix is to move the rows out of the default partition first, in one transaction (the identity column needs `OVERRIDING SYSTEM VALUE` to keep its values). Again with the stray row, and rolled back at the end:

```sql
BEGIN;
INSERT INTO events (occurred_at, session_id, event_type)
VALUES ('2027-03-15 10:00+00', gen_random_uuid(), 'page_view');

CREATE TEMP TABLE moved AS
    WITH d AS (
        DELETE FROM events_default
        WHERE occurred_at >= '2027-03-01' AND occurred_at < '2027-04-01'
        RETURNING *
    )
    SELECT * FROM d;
SELECT create_events_partition('2027-03-01');
INSERT INTO events OVERRIDING SYSTEM VALUE SELECT * FROM moved;

SELECT tableoid::regclass, count(*) FROM events WHERE occurred_at >= '2027-01-01' GROUP BY 1;
ROLLBACK;
```

```text
    tableoid     | count 
-----------------+-------
 events_y2027m03 |     1
(1 row)
```

With a large default partition, this is slow (a scan of the default partition while holding locks). Hence the advice of section 2.3: keep it empty, monitor it, create partitions early.

### 9.3 `ATTACH` and `DETACH`

**`ALTER TABLE parent ATTACH PARTITION t FOR VALUES ...`** turns an existing table into a partition. The table must have the same columns (names and types), all the parent's `CHECK` constraints (same names), `NOT NULL` where the parent has it, and no identity column of its own. Attaching needs only a `SHARE UPDATE EXCLUSIVE` lock on the parent (reads and writes on other partitions continue) and an `ACCESS EXCLUSIVE` lock on the table being attached (and on the default partition, if there is one, which must be checked too).

But PostgreSQL must verify that **every row of the table fits the bounds**, which means a full scan of it while holding that lock, unless an existing, validated `CHECK` constraint already proves it. On the unpartitioned copy, `lab.events_flat` (4.66 million rows after section 9.4's `DELETE`), attached to a new partitioned table `lab.ev` as the partition for 2025:

```sql
ALTER TABLE lab.ev ATTACH PARTITION lab.events_flat FOR VALUES FROM ('2025-01-01') TO ('2026-02-01');
-- DEBUG:  verifying table "events_flat"           (with SET client_min_messages = debug1)
-- Time: 490.835 ms
```

The scan took half a second here; on a 500 GB table it takes hours, all of them under an `ACCESS EXCLUSIVE` lock on the table. The trick is to add a matching constraint first, in two steps that don't block:

```sql
-- instant: only new rows are checked from now on
ALTER TABLE lab.events_flat ADD CONSTRAINT events_flat_bounds
    CHECK (occurred_at >= '2025-01-01' AND occurred_at < '2026-02-01') NOT VALID;
-- scans the table, but under SHARE UPDATE EXCLUSIVE: reads and writes continue
ALTER TABLE lab.events_flat VALIDATE CONSTRAINT events_flat_bounds;    -- 415 ms

ALTER TABLE lab.ev ATTACH PARTITION lab.events_flat FOR VALUES FROM ('2025-01-01') TO ('2026-02-01');
-- DEBUG:  partition constraint for table "events_flat" is implied by existing constraints
-- Time: 3.887 ms
```

After the attach, the `CHECK` constraint is redundant (the partition bound enforces the same thing) and can be dropped. Section 10 uses exactly this to convert a live table.

**`ALTER TABLE parent DETACH PARTITION t`** turns a partition back into a standalone table, with all its data and indexes. The plain form takes an `ACCESS EXCLUSIVE` lock on the parent, briefly, but it must wait for all running queries on `events` and blocks new ones while it waits: use a `lock_timeout`. Since PostgreSQL 14, **`DETACH PARTITION ... CONCURRENTLY`** avoids that: it takes only a `SHARE UPDATE EXCLUSIVE` lock and works in two transactions internally, waiting for queries that might still see the partition. Its restrictions:

```sql
ALTER TABLE events DETACH PARTITION events_y2026m12 CONCURRENTLY;
```

```text
ERROR:  cannot detach partitions concurrently when a default partition exists
```

It also can't run inside a transaction block (so not inside a function or a procedure), and if it is interrupted, the partition stays half-detached until you run `ALTER TABLE events DETACH PARTITION events_y2026m12 FINALIZE`. So for `events`, which has a default partition, retention uses the plain `DETACH` with a lock timeout. That is one more reason why some teams don't create a default partition.

### 9.4 Retention: `DROP` versus `DELETE`

Here is the monthly retention job both ways, removing January 2025 (326,131 rows). WAL is measured as the difference of `pg_current_wal_insert_lsn()` before and after, after a `CHECKPOINT`:

```sql
-- the unpartitioned copy: DELETE, then the VACUUM that it makes necessary
CHECKPOINT;
SELECT pg_current_wal_insert_lsn() AS start_lsn \gset
DELETE FROM lab.events_flat WHERE occurred_at < '2025-02-01';        -- 300 ms
SELECT pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_insert_lsn(), :'start_lsn')) AS wal_delete;   -- 53 MB
SELECT pg_current_wal_insert_lsn() AS start_lsn \gset
VACUUM lab.events_flat;                                              -- 333 ms
SELECT pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_insert_lsn(), :'start_lsn')) AS wal_vacuum;   -- 36 MB

-- the partitioned table
CHECKPOINT;
SELECT pg_current_wal_insert_lsn() AS start_lsn \gset
ALTER TABLE events DETACH PARTITION events_y2025m01;                 -- 0.9 ms
DROP TABLE events_y2025m01;                                          -- 0.7 ms
SELECT pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_insert_lsn(), :'start_lsn')) AS wal_drop;     -- 206 kB
```

| | `DELETE` + `VACUUM` | `DETACH` + `DROP` |
|---|---|---|
| Time | 300 ms + 333 ms | 1.6 ms |
| WAL generated | 53 MB + 36 MB | 0.2 MB |
| Disk space returned to the OS | none (537 MB before and after; the space is reused by future rows) | 57 MB, immediately |
| Dead tuples, index cleanup | 326,131 row versions and their index entries | none |

(If you run the second half, wrap it in `BEGIN` ... `ROLLBACK` to keep January for the rest of the module, as was done for these measurements.)

The `DELETE` was the friendly case: the rows of January were physically together at the start of the file. In a table where old and new rows are mixed, the `DELETE` touches pages all over the table and the indexes. And every byte of WAL also has to travel to every replica and into every backup ([Module 18](../18-backup-recovery/)): a large `DELETE` is a replication-lag event, `DROP TABLE` isn't.

### 9.5 Archiving a detached partition

A detached partition is an ordinary table, which gives you options between "keep it forever in the hot database" and "drop it":

- **Move it to another schema** (`ALTER TABLE events_y2025m01 SET SCHEMA archive`): instant, keeps it queryable, removes it from queries on `events`.
- **Move it to cheaper storage** (`ALTER TABLE events_y2025m01 SET TABLESPACE slow_disks`): rewrites the table under an `ACCESS EXCLUSIVE` lock, which is fine for a detached table nobody uses.
- **Export it and drop it**: `pg_dump -t bazaar.events_y2025m01 -Fc -f events_2025_01.dump bazaar`, store the file in object storage, `DROP TABLE`. To query it again: restore it and attach it back (with a `CHECK` constraint, to skip the validation scan).
- **Keep it attached, but frozen**: `VACUUM (FREEZE) events_y2025m01` once, so that anti-wraparound VACUUM never needs to touch it again.

### 9.6 VACUUM, ANALYZE and statistics

Autovacuum treats each partition as a separate table, which is good: small, busy partitions are vacuumed often, old ones rarely. But **autovacuum never analyzes the partitioned parent itself**, because the parent has no rows of its own. The planner uses the partitions' statistics for queries that prune, but it needs the parent's statistics for estimates across partitions (for example a join between `events` and `products` that doesn't filter on time). Run `ANALYZE events` yourself after large loads or once in a while (1402 ends with `VACUUM (ANALYZE) events`, which processes the parent and every partition).

> **PG 18:** `VACUUM ONLY` and `ANALYZE ONLY` on a partitioned table process just the parent, which makes refreshing the parent's statistics cheap: `ANALYZE ONLY events` samples rows from the partitions without re-analyzing each one.

### 9.7 Automating it: `pg_partman` and `pg_cron`

Two extensions do this work in many production systems:

- **`pg_partman`** manages time- and id-based partition sets: you register a table with its interval ("monthly") and premake count ("4 months ahead"), and its maintenance function creates future partitions, detaches or drops old ones according to a retention setting, and can move data out of the default partition. It is essentially a production-grade version of the procedure you write in the milestone.
- **`pg_cron`** runs SQL on a cron schedule inside the database (`SELECT cron.schedule('0 3 * * *', $$CALL maintain_monthly_partitions('events')$$)`), so the maintenance doesn't depend on an external scheduler.

Both must be installed on the server (and allowed by your hosting provider). Without them, a cron job or a Kubernetes CronJob calling `psql -c "CALL ..."` is just as good, as long as somebody is alerted when it fails.

---

## 10. Migrating an existing table to partitioning

`ALTER TABLE ... PARTITION BY` doesn't exist: you can't turn a regular table into a partitioned one in place. You create a new partitioned table and get the data into it. There are three strategies.

**1. Copy everything, then switch.** Create the partitioned table, `INSERT INTO new SELECT * FROM old` (in batches), stop writes, copy the rows that arrived in the meantime, rename. Simple, but it rewrites all the data (time, WAL, double disk space), and the final catch-up needs a write stop.

**2. Attach the old table as a partition** (the milestone's strategy). The old table becomes the first partition of the new one, covering everything up to a cut-over date, and new monthly partitions take the rows after it. No data is copied. Everything slow (building a unique index that includes the key, validating a `CHECK` constraint for the bounds) runs beforehand with non-blocking locks, and the switch itself is a short transaction of catalog changes:

```text
 before                        prepare (online)                         switch (one short transaction)
 ──────                        ────────────────                         ──────────────────────────────
 audit_log (1.5M rows)         CREATE UNIQUE INDEX CONCURRENTLY         LOCK audit_log
   PK (id)                       (id, changed_at)          ~1 s         swap the PK to (id, changed_at)
                               ADD CHECK (changed_at < cut-over)        DROP IDENTITY on the old table
                                 NOT VALID; VALIDATE       0.2 s        rename audit_log -> audit_log_legacy
                               CREATE TABLE audit_log_p                 rename audit_log_p -> audit_log
                                 PARTITION BY RANGE (changed_at)        ATTACH audit_log_legacy
                                 + monthly partitions                     FOR VALUES FROM (MINVALUE) TO (cut-over)
                                                                        COMMIT                     ~35 ms
```

The pitfalls you meet on the way are the rules of the previous sections: the attached table needs the parent's `CHECK` constraints *with the same names* ("child table is missing constraint"), can't keep its own primary key if the parent's differs ("multiple primary keys for table ... are not allowed"), must have `NOT NULL` on the key, and can't have an identity column. The milestone walks through all of them; the reference script is [`solutions/audit_log_migration.sql`](solutions/audit_log_migration.sql).

The cost of this strategy is one big legacy partition. It doesn't prune well (any query on old data reads all of it), and retention can only drop it whole, once its newest row has expired. Often that is exactly right for logs: after the retention period, the legacy partition disappears in one `DROP`, and from then on everything is monthly. If you need monthly granularity sooner, split it later: create the monthly tables for its range, copy the rows month by month, and swap them in (detach the legacy partition, attach the new months, attach the rest of the legacy partition with a narrower range).

**3. Dual-write and backfill.** The application (or a trigger on the old table) writes every new row to both tables; a background job copies the old rows over in batches; once the copy has caught up, reads switch to the new table, then writes, and the old table is dropped. It copies all the data, like strategy 1, but never needs a write stop, and it lets you change the schema at the same time. It is the most work, and the trigger doubles the write cost during the migration. **Logical replication** ([Module 19](../19-replication/)) is a variant of the same idea: publish the old table, subscribe a partitioned table with the same name in another database, and switch the application over. Since PostgreSQL 13, a partitioned table can be a subscription target, and the publisher option `publish_via_partition_root` lets a partitioned table be published as if it were one table.

---

## 11. Beyond one server, and before PostgreSQL 10

**Partitioning is not sharding.** All partitions live in the same database, on the same server, sharing its CPU, memory, disks and WAL. Partitioning manages *data volume over time* on one machine; it doesn't add write capacity or storage beyond that machine. Sharding ([Module 21](../21-sharding/)) spreads data over several servers. The two meet: a partition can be a `postgres_fdw` **foreign table** on another server, so a partitioned table can route rows to remote shards, and Citus builds distributed tables on similar ideas. Module 21 explores both.

**Inheritance-based partitioning** is how this was done before PostgreSQL 10, and you will still find it in old systems: child tables `INHERITS (parent)`, each with a `CHECK` constraint describing its range, a `BEFORE INSERT` trigger on the parent that routes rows with a long `IF ... ELSIF` chain, and `constraint_exclusion` to skip children at plan time. It has none of the conveniences of declarative partitioning (no automatic routing, no partitioned indexes, no foreign keys, no execution-time pruning, slow trigger-based inserts). Don't build new systems this way; to migrate an old one, detach the children (`ALTER TABLE child NO INHERIT parent`) and attach them to a new declaratively partitioned table, with their existing `CHECK` constraints making the attach instant.

---

## Summary

- A partitioned table is a router in front of ordinary tables. Partition when data arrives and leaves in time order (retention), when nearly every query filters on the partition key, or when a table is too big for per-table maintenance. It doesn't speed up queries that a good index already serves, and it slows down queries that don't use the key.
- `RANGE` bounds are `FROM` inclusive, `TO` exclusive; spell `timestamptz` bounds with an explicit offset. `LIST` is for a few stable categories, `HASH` for spreading one key evenly. Sub-partitioning multiplies partitions.
- The `DEFAULT` partition blocks creating partitions for ranges it holds rows for, forces a scan on every create or attach, and prevents `DETACH CONCURRENTLY`. Keep it empty, or don't have one.
- Primary keys and unique constraints must include the partition key, because there are no global indexes; hence `events`' `PRIMARY KEY (id, occurred_at)`. Foreign keys *to* a partitioned table must reference that composite key and block detaching referenced partitions.
- Build indexes on live partitioned tables with `CREATE INDEX ... ON ONLY parent`, `CREATE INDEX CONCURRENTLY` per partition, and `ALTER INDEX ... ATTACH PARTITION`.
- Pruning happens at plan time for immutable comparisons with constants, and at execution time for parameters, stable functions such as `now()`, cross-type comparisons with `date`, and nested-loop parameters. Functions and casts on the key (`::date`, `date_trunc`, `extract`) prevent pruning; write half-open ranges.
- Every unpruned partition costs planning and locking; with 1,000 partitions an unpruned lookup took 24 ms against 0.1 ms.
- Partitionwise joins and aggregates split work per partition for identically partitioned tables; they are off by default because of planning cost.
- Updating the key moves the row (delete + insert), and concurrent updaters get a serialization error.
- Maintenance: create partitions months ahead with a lock timeout; attach with a pre-validated `CHECK` to avoid the validation scan; retention by `DETACH` + `DROP` took 2 ms and 0.2 MB of WAL where `DELETE` + `VACUUM` took 0.6 s and 89 MB; analyze the parent yourself.
- An existing table is migrated by attaching it as the first partition (no copy, a millisecond-scale switch), by copying, or by dual-writing and backfilling.
- Partitioning is not sharding; inheritance-based partitioning is legacy.

## What's next

Bazaar's schema is now complete. [Module 15](../15-security-rls/) locks it down: roles, privileges, and row-level security so that a seller sees only their own products and orders, and the application connects with the least privilege it needs.
