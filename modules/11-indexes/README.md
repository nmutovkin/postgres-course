# Module 11: Indexes

<!-- nav:start -->
> **Theory** · [Exercises](exercises.md) · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 10 Transactions and Concurrency](../10-transactions-concurrency/README.md) · [Course home](../../README.md) · [12 Query Planning and Performance →](../12-query-performance/README.md)
<!-- nav:end -->

Until now Bazaar has lived on a few dozen hand-written rows, and every query was instant because there was nothing to search. That changes in this module. You load the **large dataset**: 100,000 users, 200,000 products, half a million orders and 1.27 million order lines. The storefront immediately feels it. "My orders" takes 40 ms instead of 0.1 ms, a category page reads 80 MB of product data to show 24 items, and deleting a single draft product takes 70 ms because PostgreSQL scans every order line to make sure nobody bought it. None of these queries is wrong. They are missing **indexes**: the auxiliary data structures that let PostgreSQL jump to the rows it needs instead of reading everything.

Indexes are the single most effective performance tool you have, and also the most misused one. Each index makes some reads faster and *every* write slower. It takes disk space and memory, and it only helps queries whose shape it matches. This module teaches you how indexes really work, so that you can design the right ones for a workload and prove that they help.

**What you'll learn**

- What an index is physically, how it points into the table, and what it costs in space, write time, WAL volume and lost HOT updates.
- The three basic ways PostgreSQL reads a table (sequential scan, index scan, bitmap scan) and just enough `EXPLAIN` to tell them apart.
- How a B-tree is built: pages, root, internal and leaf levels, sorted keys, heap pointers, logarithmic search cost and deduplication, inspected with `pageinspect`.
- Which operators a B-tree supports and which it cannot.
- Multicolumn indexes: the leftmost-prefix rule and the "equality first, then range" rule, measured on `orders (seller_id, created_at)`.
- Using an index to avoid sorting (`ORDER BY ... LIMIT`), `DESC` and `NULLS FIRST/LAST`.
- Index-only scans, the visibility map, and covering indexes with `INCLUDE`.
- Partial indexes, expression indexes, unique indexes versus unique constraints.
- Why foreign keys need indexes on the referencing side, with timings.
- The other index types: GIN (`jsonb`, arrays), GiST (ranges, exclusion constraints, nearest neighbour), SP-GiST, BRIN and hash, and when each one wins.
- Collations and `text_pattern_ops` for `LIKE 'prefix%'`.
- Building indexes on a live system with `CREATE INDEX CONCURRENTLY`, invalid indexes, and `REINDEX CONCURRENTLY`.
- Finding missing, unused and redundant indexes; index bloat.
- A checklist of reasons why the planner ignores an index.

**Before you start**

This module starts from the small dataset and **you** load the large one. That is the module's first milestone step, and it is how the reference database gets its data too:

```bash
./project/bin/reset.sh 11      # state at the start of module 11: small dataset, no secondary indexes
./project/bin/psql.sh bazaar -v ON_ERROR_STOP=1 < project/db/1101_seed_large.sql    # about 70-90 s
./project/bin/psql.sh
```

`1101_seed_large.sql` truncates the small dataset and generates the large one entirely in SQL, then runs `VACUUM ANALYZE`. Afterwards the database is about 480 MB. It has primary keys and the unique constraints from earlier modules, but **none of the indexes this module is about**. The theory runs in this state: every example creates the index it needs and drops it again at the end, so the sections can be read in any order.

The exercises and the milestone check run in the state *after* this module, which has the reference indexes from `project/db/1102_schema_indexes.sql`. You get there either by applying that file on top of the state above:

```bash
./project/bin/psql.sh bazaar -v ON_ERROR_STOP=1 < project/db/1102_schema_indexes.sql   # 3 s
```

or from scratch with `./project/bin/reset.sh 12`, which loads both files (about two minutes). If you already have state 12 and want to go back to "large data, no indexes" for the theory, run [`examples/drop_1102_indexes.sql`](examples/drop_1102_indexes.sql).

A few demos use contrib extensions that ship with PostgreSQL. Create them once:

```sql
CREATE EXTENSION IF NOT EXISTS pageinspect;     -- look inside index pages
CREATE EXTENSION IF NOT EXISTS pgstattuple;     -- index density and GIN pending lists
CREATE EXTENSION IF NOT EXISTS pg_visibility;   -- the visibility map
CREATE SCHEMA IF NOT EXISTS lab;                -- scratch tables for demos that modify data
```

**About the numbers.** Every plan and timing in this module is real output from PostgreSQL 17.11 in Docker on a 4-vCPU machine with 15 GB of RAM and the **default configuration** (`shared_buffers = 128MB`, `work_mem = 4MB`, `random_page_cost = 4`). Your timings will differ, and they differ between runs on the same machine too: the first run of a query reads from disk (`read=` in the plans), later runs find the pages in memory (`hit=`). Compare *orders of magnitude* and *buffer counts*, which are stable, rather than exact milliseconds. Many examples turn off parallel query with `SET max_parallel_workers_per_gather = 0;` so that plans are simpler to read. Module 12 explains parallel plans.

---

<!-- toc:start -->
**Contents**

- [1. What an index is, and what it costs](#1-what-an-index-is-and-what-it-costs)
- [2. Three ways to read a table](#2-three-ways-to-read-a-table)
- [3. Inside a B-tree](#3-inside-a-b-tree)
- [4. What a B-tree can answer](#4-what-a-b-tree-can-answer)
- [5. Multicolumn indexes and column order](#5-multicolumn-indexes-and-column-order)
- [6. Indexes that sort](#6-indexes-that-sort)
- [7. Index-only scans and covering indexes](#7-index-only-scans-and-covering-indexes)
- [8. Partial indexes](#8-partial-indexes)
- [9. Expression indexes](#9-expression-indexes)
- [10. Unique indexes and unique constraints](#10-unique-indexes-and-unique-constraints)
- [11. Indexing foreign keys](#11-indexing-foreign-keys)
- [12. GIN: indexes for values that contain values](#12-gin-indexes-for-values-that-contain-values)
- [13. GiST and SP-GiST](#13-gist-and-sp-gist)
- [14. BRIN: tiny indexes for naturally ordered data](#14-brin-tiny-indexes-for-naturally-ordered-data)
- [15. Hash indexes](#15-hash-indexes)
- [16. Text: collations and `LIKE 'prefix%'`](#16-text-collations-and-like-prefix)
- [17. Building indexes on a live system](#17-building-indexes-on-a-live-system)
- [18. Finding missing and unused indexes](#18-finding-missing-and-unused-indexes)
- [19. Index bloat](#19-index-bloat)
- [20. When the planner ignores your index](#20-when-the-planner-ignores-your-index)
- [21. The Bazaar index set](#21-the-bazaar-index-set)
- [Summary](#summary)
- [What's next](#whats-next)

<!-- toc:end -->

## 1. What an index is, and what it costs

### 1.1 The heap and the `ctid`

A PostgreSQL table is stored in a **heap**: a file of 8 KB pages, and rows (called *tuples*) are placed wherever there is free space. The order of rows in the heap has nothing to do with any column value. It's roughly insertion order, and it changes as rows are updated and space is reused. Every tuple has a physical address called the **`ctid`**: `(page number, item number on that page)`. You can select it like a column:

```sql
SELECT ctid, id, user_id, created_at
FROM orders
WHERE user_id = 1
ORDER BY ctid
LIMIT 3;
```

```text
  ctid   |  id  | user_id |       created_at
---------+------+---------+------------------------
 (22,18) | 1566 |       1 | 2023-01-11 22:59:10+00
 (32,23) | 2274 |       1 | 2023-01-17 10:46:03+00
 (37,55) | 2658 |       1 | 2023-01-20 00:22:08+00
```

Order 1566 is the 18th tuple on page 22 of the `orders` file. Module 16 dissects pages and tuples byte by byte. For now, two facts matter:

1. Without an index, the only way to find "the orders of user 1" is to read **every page** of the table and test every row. That is a **sequential scan**.
2. An **index** is a separate data structure, stored in its own file, that maps *values* of one or more columns to the `ctid`s of the rows holding them. Looking up a value in the index gives you a short list of addresses, and PostgreSQL fetches exactly those pages.

The index is *derived* data. It contains nothing that isn't already in the table, so you can drop and rebuild it at any time without losing information. That's also why PostgreSQL must keep it in sync on every write.

### 1.2 A first index

User 4242 is a typical shopper with 15 orders. Here is their order history page, newest first:

```sql
SET max_parallel_workers_per_gather = 0;

EXPLAIN (ANALYZE, BUFFERS)
SELECT id, status, total, created_at
FROM orders
WHERE user_id = 4242;
```

```text
                                               QUERY PLAN
--------------------------------------------------------------------------------------------------------
 Seq Scan on orders  (cost=0.00..13345.00 rows=10 width=27) (actual time=1.174..27.268 rows=15 loops=1)
   Filter: (user_id = 4242)
   Rows Removed by Filter: 499985
   Buffers: shared hit=7095
 Planning Time: 0.037 ms
 Execution Time: 27.288 ms
```

`EXPLAIN (ANALYZE, BUFFERS)` runs the query and shows *how* it ran. Module 12 reads plans in depth; for now look at three lines. `Seq Scan on orders` is the access method. `Rows Removed by Filter: 499985` says PostgreSQL looked at half a million rows to keep 15. `Buffers: shared hit=7095` says it touched 7,095 pages of 8 KB, which is 55 MB, all of `orders`. That is 27 ms of work to return 15 rows, and it grows linearly with the table.

Now create an index on the column we search by:

```sql
CREATE INDEX orders_user_id_idx ON orders (user_id);   -- 0.18 s on this machine

EXPLAIN (ANALYZE, BUFFERS)
SELECT id, status, total, created_at
FROM orders
WHERE user_id = 4242;
```

```text
                                                          QUERY PLAN
-------------------------------------------------------------------------------------------------------------------------------
 Index Scan using orders_user_id_idx on orders  (cost=0.42..37.82 rows=10 width=27) (actual time=0.017..0.044 rows=15 loops=1)
   Index Cond: (user_id = 4242)
   Buffers: shared hit=18
 Planning Time: 0.144 ms
 Execution Time: 0.061 ms
```

18 pages instead of 7,095, and 0.06 ms instead of 27 ms: about 450 times faster. `Index Cond` means the condition was answered *by the index*, not by testing rows. Of the 18 pages, 3 are index pages (a root, an internal page and a leaf, see section 3) and 15 are heap pages, one per order, because the 15 orders were placed years apart and live on 15 different pages.

`CREATE INDEX name ON table (columns)` creates a B-tree, the default index type. Naming convention in this course: `<table>_<columns>_idx` for plain indexes and `<table>_<columns>_key` for unique ones, which matches what PostgreSQL generates for constraints.

> **Under the hood:** Why `rows=10` estimated but 15 actual? The planner doesn't know user 4242; it knows (from `ANALYZE` statistics) that `orders` has 500,000 rows and estimates about 50,000 distinct `user_id`s, so it guesses 10 per user. The estimate comes from a 30,000-row sample and is low: the real number is 84,058 buyers, about 6 orders each. Module 12 is about these estimates and what happens when they're badly wrong.

### 1.3 What an index costs

An index is not free. You pay in four currencies.

**Disk and memory.** `orders_user_id_idx` takes 5.3 MB; the table is 55 MB. Indexes compete with tables for the same `shared_buffers` and OS cache. A database whose indexes are bigger than its tables (common!) caches less of its data.

**Write time.** Every `INSERT` must add an entry to *every* index on the table. Every `DELETE` leaves index entries that VACUUM must later remove. Every `UPDATE` that creates a new row version may need new entries in every index. To measure this, copy all 500,000 orders into two empty tables, one without indexes and one with five:

```sql
CREATE TABLE lab.orders_noidx (LIKE orders INCLUDING DEFAULTS);
CREATE TABLE lab.orders_5idx  (LIKE orders INCLUDING DEFAULTS);
CREATE INDEX ON lab.orders_5idx (id);
CREATE INDEX ON lab.orders_5idx (user_id, created_at);
CREATE INDEX ON lab.orders_5idx (seller_id, created_at);
CREATE INDEX ON lab.orders_5idx (status);
CREATE INDEX ON lab.orders_5idx (shipping_address_id);

SELECT pg_current_wal_lsn() AS l0 \gset
\timing on
INSERT INTO lab.orders_noidx SELECT * FROM orders;     -- Time: 1143.903 ms
\timing off
SELECT pg_current_wal_lsn() AS l1 \gset
\timing on
INSERT INTO lab.orders_5idx SELECT * FROM orders;      -- Time: 5859.570 ms
\timing off
SELECT pg_current_wal_lsn() AS l2 \gset

SELECT pg_size_pretty(pg_wal_lsn_diff(:'l1', :'l0')) AS wal_noidx,
       pg_size_pretty(pg_wal_lsn_diff(:'l2', :'l1')) AS wal_5idx;
```

```text
 wal_noidx | wal_5idx
-----------+----------
 68 MB     | 274 MB
```

Five indexes made the load **five times slower** and produced **four times as much WAL**. (`pg_current_wal_lsn()` is the current position in the write-ahead log, so the difference between two positions is the WAL written in between; Module 16 explains the WAL.) WAL volume matters beyond disk: every byte is shipped to replicas and to backups. And the five indexes, filled one row at a time, ended up at 66 MB, *bigger than the 55 MB table*. An index built in one go by `CREATE INDEX` is packed tighter (section 19).

```sql
DROP TABLE lab.orders_noidx, lab.orders_5idx;
```

**Lost HOT updates.** When an `UPDATE` changes no indexed column and the new row version fits on the same page, PostgreSQL performs a **heap-only tuple (HOT)** update: the new version is chained from the old one inside the page and *no index is touched at all*. As soon as you index a column that an `UPDATE` modifies, those updates can no longer be HOT, and each one writes a new entry into every index on the table. Indexing `orders.updated_at` would make every status change touch all of the table's indexes. [Module 16, section 7.2](../16-internals/README.md#72-heap-only-tuples-hot) shows HOT chains on real pages.

**Planning time and choice.** Each index is one more option the planner must cost for every query on the table. That's negligible for a few indexes. With thirty, it's measurable, and an index that is *almost* right sometimes wins over the one you intended.

The rule that follows: **index for the queries you actually run, not for the columns you have.** A table with heavy writes and a few well-chosen indexes beats one with an index on every column.

---

## 2. Three ways to read a table

The planner has three basic strategies for "find the rows where *condition*". Which one it picks depends mostly on **how many rows match** (the *selectivity*) and on how the matching rows are spread across the heap. `orders_user_id_idx` is still in place, so let's watch all three on the same index.

**Index scan**: walk the index to the first matching entry, then for each entry fetch its heap tuple, in index order. Good for few rows.

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, status, total FROM orders WHERE user_id = 1;
```

```text
                                                            QUERY PLAN
----------------------------------------------------------------------------------------------------------------------------------
 Index Scan using orders_user_id_idx on orders  (cost=0.42..491.89 rows=150 width=19) (actual time=0.071..1.347 rows=234 loops=1)
   Index Cond: (user_id = 1)
   Buffers: shared hit=235 read=1
```

User 1 is Bazaar's top buyer with 234 orders. Notice `Buffers: 236` for 234 rows: almost one heap page per row. Each index entry leads to a different page, and in the worst case each of those is a random disk read. That is why an index scan stops being attractive as the row count grows.

**Bitmap scan**: first scan the index and collect *all* matching `ctid`s into an in-memory bitmap, one bit per heap page (or per tuple). Then read the marked heap pages **in physical order**, each one only once, and recheck the condition on the rows found there. Good for "hundreds to tens of thousands of rows".

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, status, total FROM orders WHERE user_id BETWEEN 1000 AND 1100;
```

```text
                                                            QUERY PLAN
----------------------------------------------------------------------------------------------------------------------------------
 Bitmap Heap Scan on orders  (cost=40.81..4951.68 rows=2379 width=19) (actual time=0.524..6.317 rows=2217 loops=1)
   Recheck Cond: ((user_id >= 1000) AND (user_id <= 1100))
   Heap Blocks: exact=1836
   Buffers: shared hit=1838 read=3
   ->  Bitmap Index Scan on orders_user_id_idx  (cost=0.00..40.21 rows=2379 width=0) (actual time=0.306..0.306 rows=2217 loops=1)
         Index Cond: ((user_id >= 1000) AND (user_id <= 1100))
         Buffers: shared hit=2 read=3
```

The plan is a tree: the inner `Bitmap Index Scan` builds the bitmap (5 index pages), and the outer `Bitmap Heap Scan` visits 1,836 heap pages (`Heap Blocks: exact=1836`) in order. `Recheck Cond` is there because a bitmap can become *lossy* when it grows too large for `work_mem`: it then remembers only "something on page 4711 matches" and the rows must be re-tested. You will see `lossy=` in the BRIN section.

**Sequential scan**: read the whole table in physical order. Reading a page you don't need costs little when you read sequentially, and the operating system's read-ahead makes it fast. Once a large fraction of the pages has to be visited anyway, reading them all in order beats jumping around:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, status, total FROM orders WHERE user_id < 40000;
```

```text
                                                   QUERY PLAN
----------------------------------------------------------------------------------------------------------------
 Seq Scan on orders  (cost=0.00..13345.00 rows=385302 width=19) (actual time=0.011..60.303 rows=383884 loops=1)
   Filter: (user_id < 40000)
   Rows Removed by Filter: 116116
   Buffers: shared hit=7095
```

77% of the rows match, so the index is useless here. The planner knew that from its statistics and didn't even try. Somewhere between 2,000 and 380,000 matching rows lies a crossover. For `user_id < 4000` (86,000 rows, 17%), the planner still picks a bitmap scan, because those rows sit on all 7,092 pages but the bitmap reads each only once. The exact crossover depends on the cost settings that Module 12 explains (`random_page_cost`, `seq_page_cost`, `effective_cache_size`).

The rule of thumb: **an index is useful for selective conditions**, roughly up to a few percent of the table for a plain index scan, more for a bitmap scan, and more still when the rows are physically clustered (section 14).

A fourth strategy, the **index-only scan**, answers the query from the index alone without visiting the heap. Section 7 covers it.

```sql
DROP INDEX orders_user_id_idx;
```

---

## 3. Inside a B-tree

### 3.1 The structure

PostgreSQL's default index type is the **B-tree** (strictly a B+-tree, in the Lehman-Yao variant that allows concurrent access without locking the whole tree). It's a balanced tree of 8 KB pages:

```text
                         page 290 (root, level 2)
                   ┌──────────────────────────────────┐
                   │  -∞ → 3 │ 20763 → 289 │ 63573 → 575 │
                   └────┬──────────┬───────────┬─────┘
          ┌─────────────┘          │           └──────────────────┐
          ▼                        ▼                              ▼
  page 3 (level 1)         page 289 (level 1)            page 575 (level 1)
 ┌──────────────────────┐ ┌──────────────────────────┐ ┌──────────────────────────┐
 │ -∞→1 │ 11→2 │ 27→4 …│ │ -∞→286 │ 20869→287 │ … │ │ -∞→572 │ 63796→573 │ … │
 └───┬──────┬───────────┘ └──────────────────────────┘ └──────────────────────────┘
     │      └──────────────────┐   284 downlinks           88 downlinks
     ▼                         ▼
  page 1 (leaf, level 0)     page 2 (leaf)
 ┌───────────────────────┐ ┌───────────────────────┐
 │ 1 → (22,18) (32,23) … │⇄│ 11 → (16,3) …         │⇄ … 662 pages in all
 │ 2 → (56,12) (77,55) … │ │ 12 → (53,71) …        │
 │ …  up to user 10      │ │ …                     │
 └───────────────────────┘ └───────────────────────┘
   key → heap ctids, sorted by key; leaves linked left ⇄ right
```

- **Leaf pages** hold the actual index entries: a key value and the `ctid` of the heap tuple, **sorted by key**. Leaves are doubly linked to their neighbours, so a range scan ("user_id between 1000 and 1100") finds the first entry and then walks right along the leaf level without going back up.
- **Internal pages** hold *separator keys* and downlinks: "keys below 20763 are under page 3, keys from 20763 up to 63573 under page 289, the rest under page 575". Each internal page here holds about 285 downlinks.
- The **root** is the single page at the top. A metapage (page 0) records where the root is.

This is the real `orders (user_id)` index on the large dataset, as `pageinspect` shows it below. Finding a key means reading one page per level: the root, then one internal page, then one leaf. Each page holds hundreds of entries, so the tree is *very* flat. With a fan-out of ~300, three levels address 300³ = 27 million entries and four levels address 8 billion. The search cost is **O(log N)**, and in practice it's 3 or 4 page reads for any table you will meet. The root and internal pages are touched by every lookup, so they stay in cache. That's why the user 4242 lookup above needed only 3 index pages.

Let's verify the picture with the `pageinspect` extension, on a freshly built `orders (user_id)` index:

```sql
CREATE INDEX orders_user_id_idx ON orders (user_id);

SELECT root, level FROM bt_metap('orders_user_id_idx');
```

```text
 root | level
------+-------
  290 |     2
```

The root is page 290, at level 2: root, one level of internal pages, then leaves. The whole index has 662 pages:

```sql
SELECT pg_relation_size('orders_user_id_idx') / 8192 AS pages;    -- 662

SELECT itemoffset, ctid, data
FROM bt_page_items('orders_user_id_idx', 290);
```

```text
 itemoffset |  ctid   |          data
------------+---------+-------------------------
          1 | (3,0)   |
          2 | (289,1) | 1b 51 00 00 00 00 00 00
          3 | (575,1) | 55 f8 00 00 00 00 00 00
```

The root holds three downlinks. In internal pages, the `ctid` column holds a *child page number* (3, 289, 575), not a heap address. The `data` column is the separator key in little-endian hex: `1b 51` is 0x511b = 20763, and `55 f8` is 0xf855 = 63573. The first entry has no key; it means "minus infinity".

### 3.2 Leaf entries and deduplication

Now a leaf page. Page 1 is the leftmost leaf, holding the smallest user ids:

```sql
SELECT itemoffset, htid, itemlen, data, cardinality(tids) AS n_tids
FROM bt_page_items('orders_user_id_idx', 1)
LIMIT 5;
```

```text
 itemoffset |   htid    | itemlen |          data           | n_tids
------------+-----------+---------+-------------------------+--------
          1 |           |      16 | 0b 00 00 00 00 00 00 00 |
          2 | (22,18)   |     808 | 01 00 00 00 00 00 00 00 |    132
          3 | (2666,69) |     632 | 01 00 00 00 00 00 00 00 |    102
          4 | (56,12)   |     808 | 02 00 00 00 00 00 00 00 |    132
          5 | (6624,50) |      56 | 02 00 00 00 00 00 00 00 |      6
```

Item 1 is the page's **high key**: the upper bound of the keys on this page (user 11), used by concurrent searches to detect that a page was split under them. Items 2 and 3 are both for `user_id = 1`, and each holds a whole list of heap pointers (`n_tids`): 132 + 102 = 234, exactly user 1's 234 orders. This is **deduplication** (PostgreSQL 13+): instead of storing the key once per row, the B-tree stores it once followed by a sorted *posting list* of `ctid`s. A plain entry costs 16 bytes (an 8-byte header with the `ctid`, plus the 8-byte `bigint` key); each additional duplicate in a posting list costs only 6 bytes.

The effect on low-cardinality columns is dramatic. Here are three indexes on the 500,000 orders, with deduplication on (the default) and off:

```sql
CREATE INDEX lab_status_dedup   ON orders (status);
CREATE INDEX lab_status_nodedup ON orders (status)  WITH (deduplicate_items = off);
CREATE INDEX lab_user_nodedup   ON orders (user_id) WITH (deduplicate_items = off);

SELECT relname, pg_size_pretty(pg_relation_size(oid))
FROM pg_class
WHERE relname IN ('orders_pkey', 'lab_status_dedup', 'lab_status_nodedup',
                  'lab_user_nodedup', 'orders_user_id_idx');
```

```text
      relname       | pg_size_pretty
--------------------+----------------
 orders_pkey        | 11 MB
 lab_status_dedup   | 3416 kB
 lab_status_nodedup | 11 MB
 lab_user_nodedup   | 11 MB
 orders_user_id_idx | 5296 kB
```

Without deduplication, every one of these is 11 MB: 500,000 entries of 16 bytes plus page overhead, the same as the unique primary key. With it, the `user_id` index (about 6 orders per buyer on average) halves, and the `status` index (6 distinct values) shrinks to a third. Deduplication is automatic and needs no tuning. It's disabled for some types where "equal" doesn't mean "identical bytes" (`numeric` with different display scales, for example, or nondeterministic collations), and unique indexes only use it to absorb the temporary duplicates that MVCC creates.

```sql
DROP INDEX lab_status_dedup, lab_status_nodedup, lab_user_nodedup;
```

### 3.3 Page splits and fillfactor

When a new key must go into a full leaf page, the page **splits**: a new page is allocated, roughly half the entries move to it, and a new separator is inserted into the parent (which may split in turn; a root split adds a level). Two details make B-trees behave well in practice:

- If inserts always go to the rightmost leaf (ever-increasing keys such as identity columns and `created_at`), PostgreSQL splits *unevenly*, leaving the old page full and starting a nearly empty one. Sequential keys therefore produce densely packed indexes.
- `CREATE INDEX` fills leaf pages to the index's **fillfactor**, 90% by default, leaving a little room for future inserts so that the first wave of updates doesn't split every page. You can change it with `WITH (fillfactor = 70)` for indexes on heavily updated columns, or 100 for indexes that are never written to.

Random keys (a `uuid` v4 primary key, an index on `email`) insert all over the tree and leave pages around 70% full in steady state. That is normal, and one of the reasons random UUID keys make bigger, less cache-friendly indexes than sequential ones.

---

## 4. What a B-tree can answer

A B-tree stores keys **in sorted order**, so it can answer every question that "sorted order" helps with. For a column `x`, the planner can use a B-tree on `x` for:

| Condition | Uses the B-tree? | Notes |
|---|---|---|
| `x = 5`, `x IN (1, 2, 3)`, `x = ANY ($1)` | yes | `IN` becomes `= ANY(array)`, one descent per element |
| `x < 5`, `x <= 5`, `x > 5`, `x >= 5`, `x BETWEEN 1 AND 9` | yes | one descent, then walk the leaves |
| `x IS NULL`, `x IS NOT NULL` | yes | NULLs are stored in the index (last, by default) |
| `ORDER BY x [DESC]`, `min(x)`, `max(x)` | yes | read in order instead of sorting (section 6) |
| `x LIKE 'abc%'` | only with `C` collation or `text_pattern_ops` | section 16 |
| `x <> 5`, `NOT (x = 5)` | no | "everything but one value" is never selective |
| `x LIKE '%abc'`, `x ILIKE ...`, `x ~ 'regex'` | no | use a trigram GIN index (Module 13) |
| `f(x) = 5`, `x + 1 = 5`, `x::text = '5'` | no | unless you index the expression (section 9) |

For example, an `IN` list is a single index scan with an array condition:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id FROM orders WHERE user_id IN (4242, 4243, 4244);
```

```text
                                                          QUERY PLAN
-------------------------------------------------------------------------------------------------------------------------------
 Index Scan using orders_user_id_idx on orders  (cost=0.42..108.68 rows=29 width=8) (actual time=0.051..0.393 rows=44 loops=1)
   Index Cond: (user_id = ANY ('{4242,4243,4244}'::bigint[]))
   Buffers: shared hit=5 read=42
```

while `<>` gets a sequential scan, as it should:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id FROM orders WHERE user_id <> 4242;
```

```text
                                                  QUERY PLAN
---------------------------------------------------------------------------------------------------------------
 Seq Scan on orders  (cost=0.00..13345.00 rows=499990 width=8) (actual time=0.025..86.923 rows=499985 loops=1)
   Filter: (user_id <> 4242)
   Rows Removed by Filter: 15
```

> **Under the hood:** How does PostgreSQL know that `<` on `bigint` matches the order of the B-tree? Through **operator classes**. An operator class (`int8_ops`, `text_ops`, `text_pattern_ops`, `jsonb_path_ops`, ...) tells an index type which operators of a data type it supports and which support functions implement the comparison. Every index column has one; `\d+ index_name` doesn't show the default, but `pg_get_indexdef()` shows non-default ones. You choose a non-default operator class by writing its name after the column: `CREATE INDEX ... (email text_pattern_ops)`. Extensions add operator classes; that is how `btree_gist` (Module 08) teaches GiST to handle `=` on `bigint`.

```sql
DROP INDEX orders_user_id_idx;
```

---

## 5. Multicolumn indexes and column order

### 5.1 The phone-book model

An index can have several key columns (up to 32). A multicolumn B-tree is sorted by the first column, then by the second within equal first values, and so on, exactly like a phone book sorted by (last name, first name). That one fact explains everything about column order:

- The phone book helps with "all Smiths" and with "John Smith", but not with "everyone named John": the Johns are spread over the whole book. That is the **leftmost-prefix rule**: an index on `(a, b, c)` is efficient for conditions on `a`, on `a` and `b`, or on all three, but not for `b` or `c` alone.
- "Smiths whose first name starts with J" is efficient: one contiguous run. "Everyone with a last name from Sa to Sm and first name John" is not: the Johns are scattered in every last name in the range. So: **put columns compared with equality first, and the column compared with a range (or used for sorting) last.**

### 5.2 Measuring it on orders

The seller dashboard asks: "my orders in November 2025".

```sql
SELECT id, total, created_at
FROM orders
WHERE seller_id = 42
  AND created_at >= '2025-11-01' AND created_at < '2025-12-01';
```

Seller 42 is mid-sized: 1,402 orders in total, 79 in November 2025. Let's build both possible two-column indexes and compare. First the "wrong" order, range column first:

```sql
CREATE INDEX orders_created_at_seller_id_idx ON orders (created_at, seller_id);

EXPLAIN (ANALYZE, BUFFERS)
SELECT id, total, created_at
FROM orders
WHERE seller_id = 42
  AND created_at >= '2025-11-01' AND created_at < '2025-12-01';
```

```text
 Index Scan using orders_created_at_seller_id_idx on orders  (cost=0.42..947.35 rows=88 width=23) (actual time=0.053..1.966 rows=79 loops=1)
   Index Cond: ((created_at >= '2025-11-01 00:00:00+00'::timestamp with time zone) AND (created_at < '2025-12-01 00:00:00+00'::timestamp with time zone) AND (seller_id = 42))
   Buffers: shared hit=4 read=185
```

Both conditions appear in `Index Cond`, so the index is "used for both columns". But the B-tree can only use `created_at` to decide *where to start and stop*. Within November it must step over all of November's ~30,000 entries and check `seller_id = 42` on each. That's about 110 leaf pages read to find 79 entries, plus ~70 heap pages. Now the right order, equality column first:

```sql
CREATE INDEX orders_seller_id_created_at_idx ON orders (seller_id, created_at);

EXPLAIN (ANALYZE, BUFFERS)
SELECT id, total, created_at
FROM orders
WHERE seller_id = 42
  AND created_at >= '2025-11-01' AND created_at < '2025-12-01';
```

```text
 Bitmap Heap Scan on orders  (cost=5.54..329.68 rows=88 width=23) (actual time=0.049..0.149 rows=79 loops=1)
   Recheck Cond: ((seller_id = 42) AND (created_at >= '2025-11-01 00:00:00+00'::timestamp with time zone) AND (created_at < '2025-12-01 00:00:00+00'::timestamp with time zone))
   Heap Blocks: exact=70
   Buffers: shared hit=70 read=3
   ->  Bitmap Index Scan on orders_seller_id_created_at_idx  (cost=0.00..5.52 rows=88 width=0) (actual time=0.034..0.035 rows=79 loops=1)
         Index Cond: ((seller_id = 42) AND (created_at >= '2025-11-01 00:00:00+00'::timestamp with time zone) AND (created_at < '2025-12-01 00:00:00+00'::timestamp with time zone))
         Buffers: shared read=3
```

**3 index pages** instead of ~115. In `(seller_id, created_at)` order, seller 42's November orders are one contiguous run of entries: descend once to `(42, 2025-11-01)` and read until `(42, 2025-12-01)`. The planner sees the cost difference and now prefers the new index (cost 330 versus 947). The remaining 70 heap pages are unavoidable unless the index covers the query (section 7).

Both versions of `EXPLAIN` print the same `Index Cond`, so you can't tell a tight range scan from a long one by reading the condition. Look at the **buffers** of the index scan node, or think about the sort order.

### 5.3 The leftmost-prefix rule in practice

Drop the wrong-order index and ask a question about `created_at` alone, "how many orders on Black Friday 2025?":

```sql
DROP INDEX orders_created_at_seller_id_idx;

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM orders
WHERE created_at >= '2025-11-28' AND created_at < '2025-11-29';
```

```text
 Aggregate  (cost=12736.59..12736.60 rows=1 width=8) (actual time=19.381..19.382 rows=1 loops=1)
   Buffers: shared hit=3 read=1916
   ->  Index Only Scan using orders_seller_id_created_at_idx on orders  (cost=0.42..12731.75 rows=1933 width=0) (actual time=2.505..19.148 rows=3527 loops=1)
         Index Cond: ((created_at >= '2025-11-28 00:00:00+00'::timestamp with time zone) AND (created_at < '2025-11-29 00:00:00+00'::timestamp with time zone))
         Heap Fetches: 0
         Buffers: shared hit=3 read=1916
```

Surprise: the planner *did* use `(seller_id, created_at)` without a condition on `seller_id`. Look at the buffers: 1,919 pages, which is the **entire index**. With no leading column to descend by, the scan reads every leaf and checks `created_at` on each entry. The planner chose it only because reading the 15 MB index is cheaper than reading the 55 MB table, and the query only needs index columns (an index-only scan, section 7). So "doesn't use the index" is slightly wrong; "can't *search* the index" is right. A full index scan is still O(N).

Meanwhile, a condition on the leading column alone is perfectly efficient. Seller 42's lifetime order count reads 12 pages:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM orders WHERE seller_id = 42;
```

```text
 Aggregate  (cost=55.42..55.43 rows=1 width=8) (actual time=0.239..0.239 rows=1 loops=1)
   Buffers: shared hit=12
   ->  Index Only Scan using orders_seller_id_created_at_idx on orders  (cost=0.42..51.55 rows=1550 width=0) (actual time=0.032..0.162 rows=1402 loops=1)
         Index Cond: (seller_id = 42)
         Heap Fetches: 0
         Buffers: shared hit=12
```

This is why an index on `(seller_id, created_at)` makes a separate index on `(seller_id)` alone **redundant**: the composite one serves both. Exercise 12 builds a query that finds such redundant indexes.

> **PG 18:** PostgreSQL 18 adds **B-tree skip scan**. When the leading column has few distinct values, a query on the second column alone can "skip" through the index: one descent per distinct leading value instead of a full scan. It helps for a leading `status` or `country_code`, not for `seller_id` with 2,000 values. The column-order advice above still holds.

### 5.4 Choosing column order

A procedure for designing a composite index for one query:

1. Columns compared with `=` (or `IN`) go first. Among them, order doesn't matter for *this* query, so choose an order that lets *other* queries use a prefix too. `seller_id` first serves "all orders of a seller" as well.
2. Then at most **one** column compared with a range (`<`, `>`, `BETWEEN`), or the `ORDER BY` column (section 6). Columns after a range column can only be checked, not searched.
3. Optionally, columns that the query only *reads* go into `INCLUDE` (section 7).

The often-quoted advice "put the most selective column first" is mostly wrong for B-trees: once all equality columns are used, the search lands on the same contiguous run whatever their order. Selectivity matters when choosing *which* columns to index at all, not their order.

---

## 6. Indexes that sort

### 6.1 `ORDER BY ... LIMIT` without sorting

A B-tree returns entries in key order, forward or **backward**. If a query wants rows in that order, the planner can skip the sort entirely. With `LIMIT`, it can stop after the first few entries. That turns "sort 70,000 rows and keep 20" into "read 20 entries". Seller 1, the biggest shop with about 70,000 orders, opens the dashboard:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, user_id, status, total, created_at
FROM orders
WHERE seller_id = 1
ORDER BY created_at DESC
LIMIT 20;
```

```text
 Limit  (cost=0.42..9.11 rows=20 width=35) (actual time=0.094..0.116 rows=20 loops=1)
   Buffers: shared hit=8 read=1
   ->  Index Scan Backward using orders_seller_id_created_at_idx on orders  (cost=0.42..30232.18 rows=69583 width=35) (actual time=0.093..0.113 rows=20 loops=1)
         Index Cond: (seller_id = 1)
         Buffers: shared hit=8 read=1
```

9 pages, 0.1 ms. `Index Scan Backward` means the index (sorted ascending) was walked from the end of seller 1's run towards its start. The estimated cost of the index scan is 30,232 for all 69,583 rows, but the `Limit` node above it needs only 20 of them, so the planner charges only the fraction it expects to read: 9.11. Without the index, the same query reads the whole table and sorts:

```sql
BEGIN;
DROP INDEX orders_seller_id_created_at_idx;     -- DDL is transactional: we can roll it back

EXPLAIN (ANALYZE, BUFFERS)
SELECT id, user_id, status, total, created_at
FROM orders
WHERE seller_id = 1
ORDER BY created_at DESC
LIMIT 20;

ROLLBACK;
```

```text
 Limit  (cost=15196.58..15196.63 rows=20 width=35) (actual time=55.185..55.189 rows=20 loops=1)
   Buffers: shared hit=791 read=6307
   ->  Sort  (cost=15196.58..15370.54 rows=69583 width=35) (actual time=55.183..55.185 rows=20 loops=1)
         Sort Key: created_at DESC
         Sort Method: top-N heapsort  Memory: 27kB
         Buffers: shared hit=791 read=6307
         ->  Seq Scan on orders  (cost=0.00..13345.00 rows=69583 width=35) (actual time=0.028..41.767 rows=69805 loops=1)
               Filter: (seller_id = 1)
               Rows Removed by Filter: 430195
               Buffers: shared hit=791 read=6307
```

55 ms and the whole table. (`top-N heapsort` is the sort algorithm for `ORDER BY ... LIMIT`: it keeps only the best 20 rows in memory. Module 12 covers sort methods.) The `BEGIN; DROP INDEX ...; ROLLBACK;` trick is handy for "what if this index didn't exist?" experiments, with a caveat: `DROP INDEX` takes an exclusive lock on the table until the rollback, so never do it on a busy production table.

The same mechanism serves `min()` and `max()`: `SELECT max(created_at) FROM orders WHERE seller_id = 1` reads one index entry.

### 6.2 When the order doesn't match

The index can provide an order only if the `ORDER BY` matches the index's column order, possibly all reversed, **after** the equality columns. With `(seller_id, created_at)`:

| `ORDER BY` | with `WHERE seller_id = $1` | without a condition |
|---|---|---|
| `created_at` / `created_at DESC` | yes (forward / backward) | no |
| `seller_id, created_at` | yes | yes (full index scan) |
| `seller_id DESC, created_at DESC` | yes (backward) | yes (backward) |
| `seller_id, created_at DESC` | yes (`seller_id` is constant) | **no**: mixed directions |
| `created_at DESC`, with `seller_id IN (...)` | n/a | **no**: the sellers' rows must be merged and sorted |

With an `IN` list, PostgreSQL 17 walks the index once per listed value, in index order, so `WHERE seller_id IN (42, 43) ORDER BY seller_id, created_at` comes out already sorted. But the *direction* must still match. Ask for the newest orders first:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, seller_id, created_at
FROM orders
WHERE seller_id IN (42, 43)
ORDER BY seller_id, created_at DESC
LIMIT 20;
```

```text
 Limit  (cost=8.57..74.67 rows=20 width=24) (actual time=9.627..9.632 rows=20 loops=1)
   Buffers: shared hit=211 read=1076
   ->  Incremental Sort  (cost=8.57..9979.54 rows=3017 width=24) (actual time=9.625..9.627 rows=20 loops=1)
         Sort Key: seller_id, created_at DESC
         Presorted Key: seller_id
         Full-sort Groups: 1  Sort Method: top-N heapsort  Average Memory: 27kB  Peak Memory: 27kB
         Pre-sorted Groups: 1  Sort Method: top-N heapsort  Average Memory: 27kB  Peak Memory: 27kB
         Buffers: shared hit=211 read=1076
         ->  Index Scan using orders_seller_id_created_at_idx on orders  (cost=0.42..9897.73 rows=3017 width=24) (actual time=0.053..9.027 rows=1403 loops=1)
               Index Cond: (seller_id = ANY ('{42,43}'::bigint[]))
               Buffers: shared hit=208 read=1076
```

The index delivers rows sorted by `seller_id` but with `created_at` *ascending*, so PostgreSQL uses an **Incremental Sort**: it sorts each group of equal `seller_id` separately ("Presorted Key"). That's better than a full sort, but it still reads all 1,402 of seller 42's orders (plus seller 43's first, to see where the group ends: `rows=1403`) to produce 20. If such an ordering is a hot path, build the index in that order: with `(seller_id, created_at DESC)`, the same query is a plain `Index Scan` under the `Limit`, with no sort node at all. A single-column index never needs `DESC`, since it can be read backward. And `ORDER BY created_at DESC` *without* `seller_id` first, over several sellers, always needs a sort step (exercise 3).

### 6.3 NULLs first or last

A B-tree also stores NULLs, by default **after** all non-NULL values (`NULLS LAST` for ascending order). Read backward, that becomes `DESC NULLS FIRST`, which is also PostgreSQL's default for `ORDER BY x DESC`. So the defaults match each other, but not always what you mean. "The 5 most recently shipped orders":

```sql
CREATE INDEX lab_orders_shipped_at ON orders (shipped_at);

SELECT id, shipped_at FROM orders ORDER BY shipped_at DESC LIMIT 5;
```

returns five orders with `shipped_at` NULL (unshipped ones sort first in `DESC`). The fix in SQL is `ORDER BY shipped_at DESC NULLS LAST`, but that no longer matches the index in either direction:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, shipped_at FROM orders ORDER BY shipped_at DESC NULLS LAST LIMIT 5;
```

```text
 Limit  (cost=20399.82..20399.83 rows=5 width=16) (actual time=115.138..115.140 rows=5 loops=1)
   Buffers: shared hit=1960 read=5135
   ->  Sort  (cost=20399.82..21649.82 rows=500000 width=16) (actual time=115.136..115.138 rows=5 loops=1)
         Sort Key: shipped_at DESC NULLS LAST
         Sort Method: top-N heapsort  Memory: 25kB
         ->  Seq Scan on orders  (cost=0.00..12095.00 rows=500000 width=16) (actual time=0.018..60.751 rows=500000 loops=1)
```

Either declare the index with the order you query, or filter the NULLs out (`WHERE shipped_at IS NOT NULL ORDER BY shipped_at DESC`, which the plain index serves):

```sql
DROP INDEX lab_orders_shipped_at;
CREATE INDEX lab_orders_shipped_at ON orders (shipped_at DESC NULLS LAST);

EXPLAIN (ANALYZE, BUFFERS)
SELECT id, shipped_at FROM orders ORDER BY shipped_at DESC NULLS LAST LIMIT 5;
```

```text
 Limit  (cost=0.42..0.62 rows=5 width=16) (actual time=0.037..0.046 rows=5 loops=1)
   Buffers: shared hit=5 read=3
   ->  Index Scan using lab_orders_shipped_at on orders  (cost=0.42..19838.75 rows=500000 width=16) (actual time=0.036..0.044 rows=5 loops=1)
```

```sql
DROP INDEX lab_orders_shipped_at;
```

---

## 7. Index-only scans and covering indexes

### 7.1 Skipping the heap

An index scan reads index entries, then fetches each row from the heap to get the other columns. If **all** the columns the query needs are in the index, the heap visit looks pointless. That's an **index-only scan**. You saw two above (`count(*)` by seller and by day).

There is a catch, and it comes from MVCC (Module 10). An index entry doesn't say whether the row version it points to is *visible to your transaction*: it may have been inserted by a transaction that hasn't committed, or deleted by one that has. Visibility information lives only in the heap tuple header. So how can PostgreSQL skip the heap?

Through the **visibility map** (VM): two bits per heap page, maintained by VACUUM. One bit means **all-visible**: every tuple on this page is visible to every current and future transaction. For an index entry pointing into an all-visible page, the heap visit can be skipped. For any other page, PostgreSQL must fetch the tuple to check, and `EXPLAIN ANALYZE` counts those visits as **`Heap Fetches`**. Any modification of a page clears its bit until the next VACUUM sets it again. [Module 16, section 10.4](../16-internals/README.md#104-the-visibility-map-and-index-only-scans) shows the map in detail.

### 7.2 Covering indexes with `INCLUDE`

The seller dashboard shows a revenue tile: number of orders and revenue for 2025.

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*), sum(total)
FROM orders
WHERE seller_id = 10 AND created_at >= '2025-01-01' AND created_at < '2026-01-01';
```

```text
 Aggregate  (cost=6093.79..6093.80 rows=1 width=40) (actual time=12.154..12.156 rows=1 loops=1)
   Buffers: shared hit=789 read=1342
   ->  Bitmap Heap Scan on orders  (cost=100.74..6076.41 rows=3476 width=7) (actual time=0.760..11.661 rows=3323 loops=1)
         Recheck Cond: ((seller_id = 10) AND (created_at >= '2025-01-01 00:00:00+00'::timestamp with time zone) AND (created_at < '2026-01-01 00:00:00+00'::timestamp with time zone))
         Heap Blocks: exact=2112
         ->  Bitmap Index Scan on orders_seller_id_created_at_idx  (cost=0.00..99.87 rows=3476 width=0) (actual time=0.495..0.496 rows=3323 loops=1)
               Index Cond: ((seller_id = 10) AND (created_at >= '2025-01-01 00:00:00+00'::timestamp with time zone) AND (created_at < '2026-01-01 00:00:00+00'::timestamp with time zone))
               Buffers: shared hit=18 read=1
```

19 index pages but 2,112 heap pages, only to read `total`. Add `total` to the index as a **non-key column**:

```sql
CREATE INDEX lab_orders_seller_created_incl
    ON orders (seller_id, created_at) INCLUDE (total);
```

```text
 Aggregate  (cost=168.02..168.03 rows=1 width=40) (actual time=0.809..0.809 rows=1 loops=1)
   Buffers: shared hit=1 read=20
   ->  Index Only Scan using lab_orders_seller_created_incl on orders  (cost=0.42..150.63 rows=3476 width=7) (actual time=0.034..0.504 rows=3323 loops=1)
         Index Cond: ((seller_id = 10) AND (created_at >= '2025-01-01 00:00:00+00'::timestamp with time zone) AND (created_at < '2026-01-01 00:00:00+00'::timestamp with time zone))
         Heap Fetches: 0
         Buffers: shared hit=1 read=20
```

21 pages instead of 2,131, 15 times faster. `INCLUDE` columns are stored only in the leaf entries. They are not part of the sort order, so they can't be searched, but they can be returned. Compared with adding `total` as a third key column, `INCLUDE`:

- keeps internal pages smaller (separator keys don't carry the extra column);
- works with a `UNIQUE` index without changing what is unique: `UNIQUE (email) INCLUDE (full_name)`;
- allows types that have no B-tree operator class, since they are never compared.

The price is size, as always: 19 MB for the covering index versus 15 MB for the plain one. And every `UPDATE` of `total` can no longer be HOT.

### 7.3 Heap fetches and VACUUM

To see the visibility map at work without touching Bazaar's real data, use a copy:

```sql
DROP INDEX lab_orders_seller_created_incl;

CREATE TABLE lab.orders_copy AS SELECT * FROM orders;
CREATE INDEX orders_copy_seller_created_idx
    ON lab.orders_copy (seller_id, created_at) INCLUDE (total);
VACUUM ANALYZE lab.orders_copy;

-- the revenue query on the copy: Index Only Scan, Heap Fetches: 0, 24 buffers

UPDATE lab.orders_copy SET status = 'delivered' WHERE status IN ('pending', 'paid', 'shipped');
UPDATE lab.orders_copy SET delivered_at = delivered_at
WHERE created_at >= '2025-07-01' AND seller_id <= 50;      -- 92,592 rows, touches many pages

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*), sum(total)
FROM lab.orders_copy
WHERE seller_id = 10 AND created_at >= '2025-01-01';
```

```text
 Aggregate  (cost=2486.84..2486.85 rows=1 width=40) (actual time=9.635..9.636 rows=1 loops=1)
   Buffers: shared hit=4020
   ->  Index Only Scan using orders_copy_seller_created_idx on orders_copy  (cost=0.42..2464.54 rows=4460 width=7) (actual time=0.019..9.206 rows=3323 loops=1)
         Index Cond: ((seller_id = 10) AND (created_at >= '2025-01-01 00:00:00+00'::timestamp with time zone))
         Heap Fetches: 4057
         Buffers: shared hit=4020
```

Still called "Index Only Scan", but with 4,057 heap fetches it does as much work as a normal index scan. (More fetches than result rows: the index now also has entries for the dead row versions, and each must be checked.) The visibility map explains why:

```sql
SELECT all_visible, count(*) FROM pg_visibility_map('lab.orders_copy') GROUP BY 1;
```

```text
 all_visible | count
-------------+-------
 f           |  3519
 t           |  4959
```

After `VACUUM lab.orders_copy`, every page is all-visible again and the query is back to `Heap Fetches: 0` and 36 buffers.

```sql
DROP TABLE lab.orders_copy;
```

> **In production:** Index-only scans are only as good as VACUUM. On a table with a steady stream of updates, the pages being written are never all-visible, and autovacuum's default trigger (20% of the table changed) may leave large parts of the map stale for a long time. If an important query depends on an index-only scan, watch `Heap Fetches` in its plan and consider tuning autovacuum for that table (Module 17). One subtlety: when only a tiny fraction of pages (under 2%) has dead tuples, VACUUM may *skip* index cleanup to save time, and the pages holding those dead tuples stay not-all-visible. `VACUUM (INDEX_CLEANUP ON)` forces the full job.

---

## 8. Partial indexes

A **partial index** has a `WHERE` clause and contains entries only for the rows that satisfy it. Two uses: make an index smaller and more precise, and enforce uniqueness on a subset of rows.

### 8.1 Smaller and more precise

The storefront's category page only ever shows **active** products: "Headphones, cheapest first, 24 per page".

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, title, price
FROM products
WHERE status = 'active' AND category_id = 6
ORDER BY price
LIMIT 24;
```

```text
 Limit  (cost=13421.67..13421.73 rows=24 width=46) (actual time=63.506..63.512 rows=24 loops=1)
   Buffers: shared hit=3 read=10315
   ->  Sort  (cost=13421.67..13431.22 rows=3820 width=46) (actual time=63.504..63.507 rows=24 loops=1)
         Sort Key: price
         Sort Method: top-N heapsort  Memory: 28kB
         ->  Seq Scan on products  (cost=0.00..13315.00 rows=3820 width=46) (actual time=0.041..62.130 rows=3763 loops=1)
               Filter: ((status = 'active'::product_status) AND (category_id = 6))
               Rows Removed by Filter: 196237
```

The whole 81 MB table to show 24 products. Following section 5, the index is `(category_id, price)`: equality, then the sort column. And since the page never shows drafts or archived products, we can leave them out of the index:

```sql
CREATE INDEX lab_products_cat_price        ON products (category_id, price);
CREATE INDEX lab_products_active_cat_price ON products (category_id, price)
    WHERE status = 'active';

SELECT relname, pg_size_pretty(pg_relation_size(oid))
FROM pg_class WHERE relname LIKE 'lab_products%';
```

```text
            relname            | pg_size_pretty
-------------------------------+----------------
 lab_products_active_cat_price | 5272 kB
 lab_products_cat_price        | 6192 kB
```

```text
 Limit  (cost=0.42..82.24 rows=24 width=46) (actual time=0.044..0.191 rows=24 loops=1)
   Buffers: shared read=26
   ->  Index Scan using lab_products_active_cat_price on products  (cost=0.42..13023.02 rows=3820 width=46) (actual time=0.043..0.187 rows=24 loops=1)
         Index Cond: (category_id = 6)
```

26 pages instead of 10,318. Note that `status = 'active'` disappeared from the plan entirely: every entry in a partial index satisfies the predicate by construction, so there is nothing left to check.

Here the size saving is modest (85% of products are active). Partial indexes shine when the indexed subset is *small*: open orders among half a million (exercise 4), unprocessed jobs among millions of finished ones, unverified reviews, soft-deleted rows excluded with `WHERE deleted_at IS NULL`. The job queue from Module 10 already has one, `jobs_ready_idx ON jobs (queue, run_at) WHERE done_at IS NULL`: however many finished jobs accumulate, the index only holds the waiting ones.

### 8.2 When a partial index can be used

The planner uses a partial index only if it can **prove** from the query's `WHERE` clause that every row the query needs satisfies the index predicate. The proof is simple-minded logic on the expressions, not on the data:

- `WHERE status = 'active' AND ...` matches `WHERE status = 'active'`.
- `WHERE status = 'paid'` matches an index `WHERE status IN ('pending', 'paid', 'shipped')`, because PostgreSQL can prove that `= 'paid'` implies `IN (...)`.
- `WHERE price > 100` matches an index `WHERE price > 50`, which is simple implication on the same operator family.
- `WHERE status = 'draft'` can't use the active-only index. With only the partial index in place, the same category query for drafts falls back to a sequential scan.

**Parameters are the classic trap.** A prepared statement `WHERE status = $1` can't be proven to match `status = 'active'` when the plan is generic (made without knowing `$1`):

```sql
PREPARE browse(product_status) AS
    SELECT id FROM products WHERE status = $1 AND category_id = 6 ORDER BY price LIMIT 24;

SET plan_cache_mode = force_generic_plan;
EXPLAIN EXECUTE browse('active');
```

```text
 Limit  (cost=13356.78..13356.84 rows=24 width=15)
   ->  Sort  (cost=13356.78..13360.52 rows=1496 width=15)
         Sort Key: price
         ->  Seq Scan on products  (cost=0.00..13315.00 rows=1496 width=15)
               Filter: ((status = $1) AND (category_id = 6))
```

With the default `plan_cache_mode` the first executions get *custom* plans (made with the actual value) and use the index. But drivers and ORMs that prepare statements may end up on a generic plan (Module 12 explains when). If a partial index's condition is constant for your application, **write it as a literal** in the query (`WHERE status = 'active'`), not as a parameter.

```sql
DEALLOCATE browse;
RESET plan_cache_mode;
DROP INDEX lab_products_cat_price, lab_products_active_cat_price;
```

### 8.3 Partial unique indexes

A partial **unique** index enforces uniqueness only among the rows it contains. Module 07 used one for "at most one default address per user":

```sql
CREATE UNIQUE INDEX addresses_one_default_per_user
    ON addresses (user_id)
    WHERE is_default;
```

A second default address for user 4242 is rejected, and any number of non-default ones are allowed:

```sql
BEGIN;
INSERT INTO addresses (user_id, label, recipient, line1, city, postal_code, country_code, is_default)
VALUES (4242, 'Office', 'Mia Hill', '1 Main St', 'Berlin', '10115', 'DE', true);
ROLLBACK;
```

```text
ERROR:  duplicate key value violates unique constraint "addresses_one_default_per_user"
DETAIL:  Key (user_id)=(4242) already exists.
```

A `UNIQUE` *constraint* can't have a `WHERE` clause, so this rule can only be expressed as an index (or, in some cases, an exclusion constraint). Other typical uses: "a SKU is unique among non-archived products", "one active subscription per user", "e-mail unique among non-deleted accounts".

---

## 9. Expression indexes

An index can store the result of an **expression** over the row instead of a plain column. The query must then use the *same* expression for the index to apply.

### 9.1 `lower(email)`

E-mail addresses are case-insensitive in practice. Module 08 replaced the case-sensitive `UNIQUE (email)` with a unique expression index:

```sql
CREATE UNIQUE INDEX users_email_lower_key ON users (lower(email));
```

The admin tool searches for a user by e-mail. Written naively, it doesn't find the user *and* reads the whole table:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, full_name FROM users WHERE email = 'Mia.Hill4242@example.org';
```

```text
 Seq Scan on users  (cost=0.00..2410.00 rows=1 width=21) (actual time=14.735..14.736 rows=0 loops=1)
   Filter: ((email)::text = 'Mia.Hill4242@example.org'::text)
   Rows Removed by Filter: 100000
   Buffers: shared read=1160
```

Written with the indexed expression, it finds her in 4 pages:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, full_name FROM users WHERE lower(email) = lower('Mia.Hill4242@example.org');
```

```text
 Index Scan using users_email_lower_key on users  (cost=0.42..8.44 rows=1 width=21) (actual time=1.722..1.724 rows=1 loops=1)
   Index Cond: (lower((email)::text) = 'mia.hill4242@example.org'::text)
   Buffers: shared hit=1 read=3
```

The planner evaluated `lower('Mia.Hill4242@example.org')` once, at planning time, because it's a constant expression, and matched `lower((email)::text)` against the index definition. The `(email)::text` cast is there because `email` has the domain type `email_address` (Module 08), and `lower()` is defined on `text`.

### 9.2 Only immutable functions

An index stores the expression's value computed at insert time and never recomputes it. So the expression must always return the same result for the same input: its functions must be **`IMMUTABLE`**. Try to index "the day an order was placed":

```sql
CREATE INDEX lab_orders_day ON orders ((created_at::date));
```

```text
ERROR:  functions in index expression must be marked IMMUTABLE
```

Casting a `timestamptz` to `date` depends on the session's `TimeZone` setting: 23:30 UTC on November 28 is already November 29 in Berlin. The function is `STABLE` (constant within a statement), not `IMMUTABLE`. Make the time zone explicit, and the expression becomes immutable:

```sql
CREATE INDEX lab_orders_day ON orders (date(created_at AT TIME ZONE 'UTC'));

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM orders WHERE date(created_at AT TIME ZONE 'UTC') = '2025-11-28';
```

```text
 Aggregate  (cost=5095.95..5095.96 rows=1 width=8) (actual time=0.952..0.953 rows=1 loops=1)
   Buffers: shared hit=52 read=6
   ->  Bitmap Heap Scan on orders  (cost=31.80..5089.70 rows=2500 width=0) (actual time=0.136..0.681 rows=3527 loops=1)
         Recheck Cond: (date((created_at AT TIME ZONE 'UTC'::text)) = '2025-11-28'::date)
         Heap Blocks: exact=52
         ->  Bitmap Index Scan on lab_orders_day  (cost=0.00..31.17 rows=2500 width=0) (actual time=0.123..0.123 rows=3527 loops=1)
               Index Cond: (date((created_at AT TIME ZONE 'UTC'::text)) = '2025-11-28'::date)
```

A query that writes `created_at::date = '2025-11-28'` instead doesn't match the index expression and gets a 70 ms sequential scan, even though the session time zone happens to be UTC. The expressions must match textually (after normalisation), not semantically.

Notice the estimate: `rows=2500`, the planner's default guess of 0.5% for an expression it knows nothing about. After the next `ANALYZE orders`, PostgreSQL also gathers **statistics on the indexed expression** (they appear in `pg_stats` under the index's name, with 1,096 distinct days), and the estimate becomes `rows=3517`, almost exactly right. Create an expression index, then run `ANALYZE` on the table.

```sql
DROP INDEX lab_orders_day;
```

> **Pitfall:** You *can* mark your own function `IMMUTABLE` even if it isn't, and PostgreSQL will believe you. If such a function's result ever changes (it reads a table, depends on a setting, or you change its code), the index silently holds stale values and queries return wrong results. After changing an immutable function used in an index, `REINDEX` every index that uses it. Module 13's `tags_to_text()` is an honest example of a user-defined immutable function.

> **In production:** An index on a range expression is often unnecessary. For "orders on November 28" the plain `created_at` index (or `(seller_id, created_at)`) answers the half-open range `created_at >= '2025-11-28' AND created_at < '2025-11-29'` just as well, without needing a second index. Prefer rewriting the condition as a range on the raw column (section 20); reserve expression indexes for expressions you can't rewrite, such as `lower(email)` or a JSON field.

---

## 10. Unique indexes and unique constraints

PostgreSQL enforces `PRIMARY KEY` and `UNIQUE` constraints **with a unique B-tree index**. It creates the index automatically, named after the constraint, and there is no other enforcement mechanism: when a new row arrives, the index is searched for the key, and a duplicate raises error 23505.

```sql
SELECT conname, contype, conindid::regclass
FROM pg_constraint
WHERE conrelid = 'sellers'::regclass;
```

```text
          conname           | contype |       conindid
----------------------------+---------+-----------------------
 sellers_country_code_upper | c       | -
 sellers_pkey               | p       | sellers_pkey
 sellers_user_id_key        | u       | sellers_user_id_key
 sellers_shop_name_key      | u       | sellers_shop_name_key
 sellers_user_id_fkey       | f       | users_pkey
```

`conindid` is the index behind each constraint. For the foreign key, it is the *referenced* table's primary key index, which the FK uses to check that the parent exists.

So what's the difference between `ALTER TABLE ... ADD CONSTRAINT ... UNIQUE (code)` and `CREATE UNIQUE INDEX ... (code)`? Both reject duplicates in the same way. The differences:

| | `UNIQUE` constraint | `CREATE UNIQUE INDEX` |
|---|---|---|
| Expressions (`lower(email)`) | no | yes |
| Partial (`WHERE ...`) | no | yes |
| `INCLUDE` columns | yes | yes |
| `NULLS NOT DISTINCT` (PG 15+) | yes | yes |
| Can be `DEFERRABLE` (checked at commit) | yes | no |
| Can be the target of a foreign key | yes | yes, if not partial and not an expression |
| `ON CONFLICT (cols)` can use it | yes | yes; a partial one needs `ON CONFLICT (cols) WHERE predicate` |
| Shows up in `information_schema` (ORMs, tools) | yes | no |

The rule of thumb: use a **constraint** when it states a business rule on plain columns (tools and readers see it as a rule), and a **unique index** when you need an expression or a predicate.

**NULLs are distinct by default.** Two rows with `code = NULL` don't violate a unique index, because `NULL = NULL` is not true:

```sql
CREATE TABLE lab.coupons (code text, seller_id bigint);
CREATE UNIQUE INDEX coupons_code_key ON lab.coupons (code);
INSERT INTO lab.coupons VALUES (NULL, 1), (NULL, 2);       -- INSERT 0 2
```

Since PostgreSQL 15, `NULLS NOT DISTINCT` treats NULLs as equal, allowing at most one:

```sql
DELETE FROM lab.coupons;
DROP INDEX lab.coupons_code_key;
CREATE UNIQUE INDEX coupons_code_key ON lab.coupons (code) NULLS NOT DISTINCT;
INSERT INTO lab.coupons VALUES (NULL, 1);   -- INSERT 0 1
INSERT INTO lab.coupons VALUES (NULL, 2);
```

```text
ERROR:  duplicate key value violates unique constraint "coupons_code_key"
DETAIL:  Key (code)=(null) already exists.
```

A partial unique index is rejected as an FK target and as an `ON CONFLICT` target unless you repeat its predicate:

```sql
DROP INDEX lab.coupons_code_key;
CREATE UNIQUE INDEX coupons_code_key ON lab.coupons (code) WHERE seller_id IS NOT NULL;

CREATE TABLE lab.redemptions (code text REFERENCES lab.coupons (code));
-- ERROR:  there is no unique constraint matching given keys for referenced table "coupons"

INSERT INTO lab.coupons VALUES ('XMAS', 1) ON CONFLICT (code) DO NOTHING;
-- ERROR:  there is no unique or exclusion constraint matching the ON CONFLICT specification

INSERT INTO lab.coupons VALUES ('XMAS', 1)
ON CONFLICT (code) WHERE seller_id IS NOT NULL DO NOTHING;   -- INSERT 0 1

DROP TABLE lab.coupons;
```

> **In production:** Adding a `UNIQUE` constraint to a big, busy table with `ALTER TABLE ... ADD CONSTRAINT ... UNIQUE` builds the index while holding an `ACCESS EXCLUSIVE` lock that blocks reads as well as writes. The zero-downtime way is to build the index first with `CREATE UNIQUE INDEX CONCURRENTLY` (section 17), then attach it: `ALTER TABLE t ADD CONSTRAINT t_code_key UNIQUE USING INDEX t_code_key;`, which only takes a brief lock. Module 26 uses this pattern in migrations.

---

## 11. Indexing foreign keys

### 11.1 Why PostgreSQL doesn't do it for you

A foreign key `order_items.product_id REFERENCES products (id)` involves two lookups:

1. **Child to parent**, on every insert or update of `order_items`: "does product 4242 exist?" That searches `products (id)`, which is always indexed, because the referenced columns must be a primary key or unique.
2. **Parent to child**, on every delete of a product, and on every update of a product's `id`: "does any order line still reference product 4242?" (and, for `ON DELETE CASCADE`, "which ones?"). That searches `order_items (product_id)`, the **referencing** side, which PostgreSQL does **not** index automatically.

Why not? Because it can't know whether you need it. Some parents are never deleted. Some child tables are tiny. Sometimes a composite index that *starts* with the FK column already exists (`order_items_pkey (order_id, product_id)` serves the FK on `order_id`). Creating an index for every FK automatically would waste space and write time in all those cases. MySQL's InnoDB does create one; PostgreSQL leaves the decision to you. In practice the decision is almost always "yes": joins from parent to child need the same index (`SELECT ... FROM order_items WHERE product_id = $1` is the "who bought this product?" query).

### 11.2 The cost, measured

At the state of this module's start, `order_items.product_id` has no index. A seller cleans up 100 draft products that were never ordered:

```sql
CREATE TEMP TABLE doomed AS
SELECT id
FROM products AS p
WHERE status = 'draft'
  AND NOT EXISTS (SELECT 1 FROM order_items AS oi WHERE oi.product_id = p.id)
ORDER BY id
LIMIT 100;

BEGIN;
EXPLAIN (ANALYZE, COSTS OFF)
DELETE FROM products WHERE id IN (SELECT id FROM doomed);
ROLLBACK;
```

```text
                                             QUERY PLAN
----------------------------------------------------------------------------------------------------
 Delete on products (actual time=0.530..0.531 rows=0 loops=1)
   ->  Nested Loop (actual time=0.046..0.356 rows=100 loops=1)
         ->  HashAggregate (actual time=0.035..0.108 rows=100 loops=1)
               Group Key: doomed.id
               ->  Seq Scan on doomed (actual time=0.007..0.016 rows=100 loops=1)
         ->  Index Scan using products_pkey on products (actual time=0.002..0.002 rows=1 loops=100)
               Index Cond: (id = doomed.id)
 Planning Time: 0.151 ms
 Trigger for constraint inventory_product_id_fkey: time=2.913 calls=100
 Trigger for constraint order_items_product_id_fkey: time=6962.121 calls=100
 Trigger for constraint reviews_product_id_fkey: time=6.608 calls=100
 Trigger for constraint cart_items_product_id_fkey: time=102.799 calls=100
 Trigger for constraint product_price_history_product_id_fkey: time=4.365 calls=100
 Trigger for constraint promotions_product_id_fkey: time=3.792 calls=100
 Trigger products_audit: time=39.929 calls=100
 Execution Time: 7123.610 ms
```

The delete itself took half a millisecond. Then come the **triggers**: foreign keys are implemented as internal triggers, and `EXPLAIN ANALYZE` reports their time separately. The check on `order_items` took **7 seconds**: 100 calls, each a sequential scan of 1.27 million order lines (70 ms per product). `cart_items` added another 100 ms. The other child tables have an index whose leading column is `product_id` (inventory's primary key, `reviews (product_id, user_id)`, the history's primary key, and the GiST index behind promotions' exclusion constraint), so their checks were fast.

Worse than the time is the **locking**: while the delete runs, it holds locks on the 100 product rows, and the sequential scans compete for I/O with everyone else. Run this from an admin screen at peak time and you have an incident. Now index the two referencing columns:

```sql
CREATE INDEX order_items_product_id_idx ON order_items (product_id);   -- 0.7 s
CREATE INDEX cart_items_product_id_idx  ON cart_items (product_id);    -- 0.01 s

BEGIN;
EXPLAIN (ANALYZE, COSTS OFF)
DELETE FROM products WHERE id IN (SELECT id FROM doomed);
ROLLBACK;
```

```text
 Trigger for constraint inventory_product_id_fkey: time=1.084 calls=100
 Trigger for constraint order_items_product_id_fkey: time=2.718 calls=100
 Trigger for constraint reviews_product_id_fkey: time=0.931 calls=100
 Trigger for constraint cart_items_product_id_fkey: time=0.977 calls=100
 Trigger for constraint product_price_history_product_id_fkey: time=0.925 calls=100
 Trigger for constraint promotions_product_id_fkey: time=1.579 calls=100
 Trigger products_audit: time=4.941 calls=100
 Execution Time: 13.984 ms
```

**7,124 ms became 14 ms**, 500 times faster.

```sql
DROP INDEX order_items_product_id_idx, cart_items_product_id_idx;
```

The rule that Bazaar follows from this module on (see `project/README.md`): **every foreign key column is the leading column of some index**, unless the child table is tiny. Exercise 7 writes a catalog query that finds the foreign keys that break this rule; at this module's starting state it finds ten.

> **Pitfall:** The cost hides in *other* statements. The slow query is the `DELETE FROM products`, but the missing index is on `order_items`. `pg_stat_statements` (Module 12) shows the delete as slow, and its plan, without `ANALYZE`, looks perfect. Only `EXPLAIN ANALYZE` shows the trigger times. Also remember that `ON DELETE CASCADE` turns one parent delete into a search *and* a delete on every child table, recursively.

---

## 12. GIN: indexes for values that contain values

### 12.1 The inverted index

A B-tree maps *one key per row* to that row. Some columns hold *many* searchable things per row: a `text[]` of tags, a `jsonb` document with dozens of keys, the words of a text. The question "which rows contain tag `eco`?" can't be answered by a B-tree on `tags`, because it sorts whole arrays.

A **GIN** (Generalized Inverted Index) index inverts the relationship. It extracts every **element** (each tag, each key/value pair) from every row and stores, for each distinct element, the list of rows that contain it, exactly like the index at the back of a book:

```text
  products.tags (heap)                      GIN index on tags
  ───────────────────────────────           ──────────────────────────────────────────
  (12,3)  {electronics,headphones,gift}     bestseller   → (3,7) (9,1) (12,8) …   15,989 rows
  (12,4)  {book,mystery}                    book         → (12,4) (12,9) …        37,379 rows
  (12,5)  {home,eco,cookware}               eco          → (12,5) (40,2) …        19,961 rows
  …                                         …
                                            limited-edition → (31,6) (40,2) …      4,029 rows
```

Internally, the distinct elements live in a B-tree (the *entry tree*), and each element's row list is a compressed *posting list*, or for frequent elements a separate *posting tree*. A query like `tags @> '{eco,limited-edition}'` (contains both) looks up both elements and intersects their row lists. `tags && '{tents,sleeping-bags}'` (overlaps) takes the union.

```sql
CREATE INDEX products_tags_idx ON products USING gin (tags);     -- 1208 kB

EXPLAIN (ANALYZE, BUFFERS)
SELECT id, title FROM products WHERE tags @> '{eco,limited-edition}';
```

```text
 Bitmap Heap Scan on products  (cost=31.55..1384.69 rows=402 width=39) (actual time=0.527..1.040 rows=389 loops=1)
   Recheck Cond: (tags @> '{eco,limited-edition}'::text[])
   Heap Blocks: exact=384
   Buffers: shared hit=394 read=2
   ->  Bitmap Index Scan on products_tags_idx  (cost=0.00..31.45 rows=402 width=0) (actual time=0.483..0.483 rows=389 loops=1)
         Index Cond: (tags @> '{eco,limited-edition}'::text[])
         Buffers: shared hit=12
```

GIN indexes are always read through **bitmap** scans: the row lists come out in physical order, not in any useful sort order, so GIN can never provide `ORDER BY` or an index-only scan. And GIN supports *operators*, not arbitrary expressions. The common way to ask "has tag eco" in application code, `'eco' = ANY (tags)`, is a different operator and can't use the index:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM products WHERE 'eco' = ANY (tags);
```

```text
 Aggregate  (cost=12864.88..12864.89 rows=1 width=8) (actual time=65.922..65.923 rows=1 loops=1)
   ->  Seq Scan on products  (cost=0.00..12815.00 rows=19953 width=0) (actual time=0.027..64.531 rows=19961 loops=1)
         Filter: ('eco'::text = ANY (tags))
```

Write `tags @> ARRAY['eco']` instead. The array operators that GIN's default `array_ops` supports are `@>` (contains), `<@` (is contained by), `&&` (overlaps) and `=`.

### 12.2 JSONB: `jsonb_ops` versus `jsonb_path_ops`

Module 08 introduced the `jsonb` containment operator `@>`. Products carry category-specific attributes; a laptop looks like `{"cpu": "Core X7", "brand": "Zentro", "ram_gb": 8, "storage": {"gb": 1024, "type": "ssd"}, ...}`. The storefront's filter sidebar asks for "Voltix laptops with 16 GB":

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, title FROM products WHERE attributes @> '{"brand": "Voltix", "ram_gb": 16}';
```

```text
 Seq Scan on products  (cost=0.00..12815.00 rows=18 width=39) (actual time=1.089..70.288 rows=152 loops=1)
   Filter: (attributes @> '{"brand": "Voltix", "ram_gb": 16}'::jsonb)
   Rows Removed by Filter: 199848
   Buffers: shared hit=975 read=9340
```

GIN offers two operator classes for `jsonb`, which differ in *what* they extract as elements:

- **`jsonb_ops`** (the default) extracts every key and every value as separate items: `brand`, `Voltix`, `ram_gb`, `16`, ... It supports `@>`, the key-existence operators `?`, `?|` and `?&`, and the jsonpath operators `@?` and `@@`. Since it doesn't remember which value belonged to which key, a query `{"brand": "Voltix"}` finds rows containing both the key `brand` and the value `Voltix` *anywhere*, then rechecks them.
- **`jsonb_path_ops`** extracts one item per *leaf value*: a hash of the full path to it plus the value (`brand → "Voltix"`, `storage.type → "ssd"`). It supports only `@>`, `@?` and `@@`, not `?`. Its items are more selective and the index is smaller.

```sql
CREATE INDEX lab_attr_ops  ON products USING gin (attributes);                  -- 1.26 s
CREATE INDEX lab_attr_path ON products USING gin (attributes jsonb_path_ops);   -- 0.75 s
```

```text
    relname    | pg_size_pretty
---------------+----------------
 lab_attr_ops  | 7792 kB
 lab_attr_path | 5720 kB
```

With both present, the planner picks `jsonb_path_ops` for containment:

```text
 Bitmap Heap Scan on products  (cost=30.14..100.11 rows=18 width=39) (actual time=0.454..1.737 rows=152 loops=1)
   Recheck Cond: (attributes @> '{"brand": "Voltix", "ram_gb": 16}'::jsonb)
   Heap Blocks: exact=152
   Buffers: shared hit=24 read=140
   ->  Bitmap Index Scan on lab_attr_path  (cost=0.00..30.13 rows=18 width=0) (actual time=0.406..0.407 rows=152 loops=1)
         Index Cond: (attributes @> '{"brand": "Voltix", "ram_gb": 16}'::jsonb)
         Buffers: shared hit=12
```

From 70 ms to 1.7 ms. jsonpath predicates work too: `attributes @? '$.storage ? (@.type == "ssd")'` (laptops with an SSD, 4,244 rows) uses `lab_attr_path` as well. But a key-existence test can only use `jsonb_ops`:

```sql
DROP INDEX lab_attr_ops;

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM products WHERE attributes ? 'isbn';      -- "is it a book?"
```

```text
 Aggregate  (cost=12907.28..12907.29 rows=1 width=8) (actual time=71.343..71.346 rows=1 loops=1)
   ->  Seq Scan on products  (cost=0.00..12815.00 rows=36912 width=0) (actual time=0.021..67.544 rows=37379 loops=1)
         Filter: (attributes ? 'isbn'::text)
```

Even when `lab_attr_ops` existed, this query took 84 ms with a bitmap scan, *slower* than this 71 ms sequential scan: 37,379 books are 19% of the table, and the bitmap visited almost every page. An index doesn't help a condition that matches a fifth of the table, whatever its type.

**Which to choose?** If your queries use `@>` (or jsonpath), `jsonb_path_ops`. If they test key existence, `jsonb_ops`. If one or two specific keys are queried all the time with `=` or ranges (`(attributes ->> 'brand') = 'Voltix'`, `(attributes ->> 'ram_gb')::int >= 16`), a **B-tree expression index** on that key is smaller and supports ranges and sorting, which GIN can't. Bazaar's reference index is `products_attributes_idx ON products USING gin (attributes jsonb_path_ops)`.

```sql
DROP INDEX lab_attr_path;
```

### 12.3 The price of GIN: writes and the pending list

Inserting one product with 3 tags and 6 attributes means inserting about 9 entries into GIN posting lists scattered over the index. To soften that, GIN has a **fast update** mode, on by default: new entries are first appended to an unsorted **pending list**, and moved into the main structure in bulk later, by VACUUM (or autovacuum), by `gin_clean_pending_list()`, or when the list exceeds `gin_pending_list_limit` (4 MB by default). Queries must scan the whole pending list in addition to the index, so a large pending list slows reads.

```sql
CREATE TABLE lab.tagged AS SELECT id, tags FROM products;
CREATE INDEX tagged_tags_idx ON lab.tagged USING gin (tags);

INSERT INTO lab.tagged SELECT id + 1000000, tags FROM products WHERE id <= 20000;   -- 68 ms

SELECT * FROM pgstatginindex('lab.tagged_tags_idx');
```

```text
 version | pending_pages | pending_tuples
---------+---------------+----------------
       2 |           138 |          20000
```

All 20,000 new rows wait in 138 pending pages. `SELECT gin_clean_pending_list('lab.tagged_tags_idx')` merges them (it returns 138, the number of pages processed). With `ALTER INDEX lab.tagged_tags_idx SET (fastupdate = off)`, the same insert writes directly into the index and takes 171 ms instead of 68 ms, but leaves nothing pending. Fast update trades smoother inserts for occasional slow ones (the backend that overflows the list pays for the merge) and slightly slower reads.

```sql
DROP TABLE lab.tagged;
```

> **In production:** For write-heavy tables with GIN indexes, watch for latency spikes on inserts (a backend merging the pending list) and consider `fastupdate = off` or a smaller `gin_pending_list_limit` for that index. For bulk loads, the fastest path is still: load the data, *then* create the GIN index.

> **PG 18:** PostgreSQL 18 can build GIN indexes with parallel workers, like B-tree and BRIN indexes, which speeds up `CREATE INDEX` on large `jsonb` and text-search columns.

---

## 13. GiST and SP-GiST

### 13.1 GiST: a tree of bounding boxes

**GiST** (Generalized Search Tree) is a framework for balanced trees over data that has no total order but can be *grouped*: ranges, geometric shapes, IP networks, text-search vectors. Each internal entry stores a **bounding predicate** that covers everything beneath it: for ranges, a range spanning all child ranges; for points, a bounding box. A search descends into every child whose bounding predicate *might* contain matches, which can be more than one child per level, unlike in a B-tree. Its operators are about overlap, containment, adjacency and distance (`&&`, `@>`, `<@`, `-|-`, `<->`).

Bazaar already has a GiST index: the one behind the exclusion constraint on promotions (Module 08), `EXCLUDE USING gist (product_id WITH =, active_during WITH &&)`. An exclusion constraint is a generalisation of uniqueness: "no two rows where *all* of these operators return true". It needs an index that can find *overlapping* ranges, which a B-tree can't do. The same index answers "which promotions are running right now?":

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT product_id, discount_pct
FROM promotions
WHERE active_during @> timestamptz '2025-12-15 12:00+00';
```

```text
 Bitmap Heap Scan on promotions  (cost=82.27..258.85 rows=1806 width=13) (actual time=1.690..1.945 rows=1674 loops=1)
   Recheck Cond: (active_during @> '2025-12-15 12:00:00+00'::timestamp with time zone)
   Heap Blocks: exact=17
   Buffers: shared hit=137 read=17
   ->  Bitmap Index Scan on promotions_no_overlap  (cost=0.00..81.82 rows=1806 width=0) (actual time=1.662..1.662 rows=1674 loops=1)
         Index Cond: (active_during @> '2025-12-15 12:00:00+00'::timestamp with time zone)
         Buffers: shared hit=137
```

GiST can use the *second* column of a multicolumn index on its own, which a B-tree can't search efficiently. But it reads 137 index pages, because the tree is organised by `product_id` first and the matches are scattered. A dedicated `gist (active_during)` index answers the same query from 12 index pages (0.5 ms). Whether that's worth a second index depends on how often the storefront asks.

### 13.2 Nearest neighbour

GiST can also return rows **ordered by distance** to a point, reading only as much of the tree as needed: the K-nearest-neighbour (KNN) search. Bazaar has no geodata, so here is a small lab table of 100,000 parcel lockers around Berlin:

```sql
CREATE TABLE lab.pickup_points (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name      text   NOT NULL,
    location  point  NOT NULL                   -- (longitude, latitude)
);

INSERT INTO lab.pickup_points (name, location)
SELECT 'Locker ' || g,
       point(13.0 + (hashint8(g) % 100000 + 100000) % 100000 / 100000.0,
             52.3 + (hashint8(g * 7) % 100000 + 100000) % 100000 / 250000.0)
FROM generate_series(1, 100000) AS g;

CREATE INDEX pickup_points_location_idx ON lab.pickup_points USING gist (location);
ANALYZE lab.pickup_points;

EXPLAIN (ANALYZE, BUFFERS)
SELECT name, location <-> point '(13.4050, 52.5200)' AS dist
FROM lab.pickup_points
ORDER BY location <-> point '(13.4050, 52.5200)'
LIMIT 3;
```

```text
 Limit  (cost=0.28..0.54 rows=3 width=20) (actual time=0.063..0.088 rows=3 loops=1)
   Buffers: shared hit=3 read=4
   ->  Index Scan using pickup_points_location_idx on pickup_points  (cost=0.28..8608.28 rows=100000 width=20) (actual time=0.062..0.086 rows=3 loops=1)
         Order By: (location <-> '(13.405,52.52)'::point)
         Buffers: shared hit=3 read=4
```

`Order By` inside an index scan means the index itself returns rows by increasing distance, and the `Limit` stops after three: 7 pages for the 3 nearest lockers among 100,000. Real geographic work uses the PostGIS extension (geography types, distances in metres on the globe), whose indexes are GiST too.

### 13.3 SP-GiST

**SP-GiST** (Space-Partitioned GiST) is the framework for *unbalanced*, space-partitioning trees: quad-trees, k-d trees and radix (prefix) trees. Where GiST groups data into possibly overlapping bounding boxes, SP-GiST splits the *space* into disjoint parts. That suits data with natural non-overlapping partitions: points (quad-tree), IP addresses and text (radix tree on prefixes), and ranges. On the lockers, an SP-GiST index is smaller (4.5 MB versus 6.5 MB for GiST) and answers the same nearest-neighbour query from 7 buffers:

```sql
CREATE INDEX pickup_points_location_spgist ON lab.pickup_points USING spgist (location);
```

You'll rarely choose SP-GiST by hand. Reach for it when the docs of a type or extension recommend it (for `inet`/`cidr` lookups, for example), and measure against GiST.

```sql
DROP TABLE lab.pickup_points;
```

---

## 14. BRIN: tiny indexes for naturally ordered data

### 14.1 Block ranges

A **BRIN** (Block Range INdex) doesn't index rows at all. It divides the table into **block ranges**, 128 consecutive pages by default, and stores one tiny summary per range: for the default `minmax` operator class, the minimum and maximum value of the column in that range. A query for `created_at` on November 28 skips every range whose [min, max] doesn't include that day and reads all pages of the ranges that might.

That only works if the column's values are **correlated with the physical row order**: each range must cover a narrow slice of values. Bazaar's orders were inserted chronologically, so `created_at` follows the heap order almost perfectly. The planner's statistics measure exactly that, as `correlation` (1 = perfectly ordered, 0 = random, −1 = reversed; Module 16 explains it):

```sql
SELECT attname, correlation
FROM pg_stats
WHERE tablename = 'orders' AND attname IN ('created_at', 'total', 'user_id');
```

```text
  attname   | correlation
------------+-------------
 user_id    |   0.4340459
 total      |  0.08679636
 created_at |   0.9998031
```

### 14.2 When BRIN is fantastic

```sql
CREATE INDEX lab_orders_created_brin  ON orders USING brin (created_at);   -- 0.15 s
CREATE INDEX lab_orders_created_btree ON orders (created_at);              -- 0.46 s
```

```text
         relname          | pg_size_pretty
--------------------------+----------------
 lab_orders_created_brin  | 24 kB
 lab_orders_created_btree | 11 MB
```

**24 KB versus 11 MB**: three pages for 500,000 rows. With the B-tree dropped, Black Friday's revenue:

```sql
DROP INDEX lab_orders_created_btree;

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*), sum(total)
FROM orders
WHERE created_at >= '2025-11-28' AND created_at < '2025-11-29';
```

```text
 Aggregate  (cost=7627.04..7627.05 rows=1 width=40) (actual time=6.197..6.199 rows=1 loops=1)
   Buffers: shared hit=773
   ->  Bitmap Heap Scan on orders  (cost=12.47..7618.25 rows=1756 width=7) (actual time=0.719..5.859 rows=3527 loops=1)
         Recheck Cond: ((created_at >= '2025-11-28 00:00:00+00'::timestamp with time zone) AND (created_at < '2025-11-29 00:00:00+00'::timestamp with time zone))
         Rows Removed by Index Recheck: 50609
         Heap Blocks: lossy=768
         Buffers: shared hit=773
         ->  Bitmap Index Scan on lab_orders_created_brin  (cost=0.00..12.03 rows=8931 width=0) (actual time=0.028..0.029 rows=7680 loops=1)
               Index Cond: ((created_at >= '2025-11-28 00:00:00+00'::timestamp with time zone) AND (created_at < '2025-11-29 00:00:00+00'::timestamp with time zone))
               Buffers: shared hit=5
```

A BRIN scan always produces a **lossy** bitmap: it knows only "these 768 pages (6 ranges) might contain matches", so every row on them is rechecked (`Rows Removed by Index Recheck: 50609`). That's still 773 pages instead of the table's 7,095, and 6 ms instead of 70 ms. The B-tree needed 64 pages and 4 ms. BRIN gives up a little speed for a 500 times smaller index that costs almost nothing to maintain: an insert only updates a range's summary when its value falls outside the current [min, max].

### 14.3 Outliers, and when BRIN is useless

Why 6 ranges for one day, when the day's 3,527 orders fit in ~50 pages? Look at the summaries with `pageinspect`:

```sql
SELECT itemoffset, blknum, value
FROM brin_page_items(get_raw_page('lab_orders_created_brin', 2), 'lab_orders_created_brin')
LIMIT 4;
```

```text
 itemoffset | blknum |                       value
------------+--------+----------------------------------------------------
          1 |      0 | {2023-01-01 00:28:19+00 .. 2023-10-03 23:13:14+00}
          2 |    128 | {2023-03-03 14:20:16+00 .. 2024-01-27 22:37:47+00}
          3 |    256 | {2023-04-17 18:11:10+00 .. 2024-06-15 22:53:08+00}
          4 |    384 | {2023-05-28 22:44:05+00 .. 2024-08-31 17:52:03+00}
```

The first 128 pages hold January to early March 2023, but the summary says "up to October 2023", because 25 of its 9,048 rows are out of place: order 58243 from October 2023, for example, sits in the last slot of page 125 between orders 8879 and 8880. The bulk load left a few rows in leftover space on earlier pages; in a live system, updated rows that move and new rows that reuse space freed by VACUUM do the same. **One outlier widens a whole range.** With wide ranges, more of them overlap any given day. Two remedies:

- `WITH (pages_per_range = 16)`: smaller ranges, so each outlier spoils less. Here the index grows to 32 KB and the query reads 403 pages in 3.3 ms.
- The **`minmax_multi`** operator class (PostgreSQL 14+) stores several small intervals per range instead of one, so isolated outliers don't stretch it: `USING brin (created_at timestamptz_minmax_multi_ops)` reads 390 pages here.

When the column isn't correlated at all, BRIN is useless. `total` has a correlation of 0.09: every range contains cheap and expensive orders.

```sql
CREATE INDEX lab_orders_total_brin ON orders USING brin (total);

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM orders WHERE total BETWEEN 1000 AND 1001;
```

```text
 Aggregate  (cost=8656.72..8656.73 rows=1 width=8) (actual time=168.228..168.230 rows=1 loops=1)
   ->  Bitmap Heap Scan on orders  (cost=12.40..8656.42 rows=120 width=0) (actual time=7.007..168.173 rows=75 loops=1)
         Recheck Cond: ((total >= '1000'::numeric) AND (total <= '1001'::numeric))
         Rows Removed by Index Recheck: 499925
         Heap Blocks: lossy=7101
         ->  Bitmap Index Scan on lab_orders_total_brin  (cost=0.00..12.37 rows=102868 width=0) (actual time=0.112..0.113 rows=71010 loops=1)
```

`lossy=7101`: every range "might" match, so the whole table is read and rechecked, which is slower than a plain sequential scan.

```sql
DROP INDEX lab_orders_total_brin, lab_orders_created_brin;
```

**When to use BRIN:** very large, append-mostly tables whose rows arrive in the order of the column you filter on: events, logs, payments, sensor readings, audit trails. `events.occurred_at` (Module 14, 5 million rows) and `payments.created_at` (exercise 8) are textbook cases. Avoid it for columns without physical correlation, and for tables with heavy updates that scatter rows. New ranges at the end of the table are summarised by VACUUM (or with `autosummarize = on`); until then they are always scanned.

---

## 15. Hash indexes

A **hash** index stores a 32-bit hash of the key and supports exactly one operator: `=`. No ranges, no sorting, no uniqueness, no multicolumn indexes, no index-only scans. Before PostgreSQL 10 hash indexes weren't WAL-logged (so they were lost on a crash and not replicated), which gave them a bad reputation that is no longer deserved.

Their one advantage is size for long keys: each entry stores a 4-byte hash instead of the key itself. Payment provider references are 16-character strings that are only ever looked up by equality, when the provider calls Bazaar's webhook:

```sql
CREATE INDEX lab_pay_ref_btree ON payments (provider_ref);
CREATE INDEX lab_pay_ref_hash  ON payments USING hash (provider_ref);
```

```text
      relname      | pg_size_pretty
-------------------+----------------
 lab_pay_ref_btree | 21 MB
 lab_pay_ref_hash  | 16 MB
```

Both find a payment by reference in a handful of buffers. The longer the key (URLs, long tokens), the bigger the hash index's advantage. But a B-tree on the same column also supports `LIKE 'ch_%'`-style prefix searches (with the right operator class), `ORDER BY`, and `UNIQUE`; the hash index never will. In practice, choose B-tree unless the key is long, the access is pure equality, and the size difference matters.

```sql
DROP INDEX lab_pay_ref_btree, lab_pay_ref_hash;
```

---

## 16. Text: collations and `LIKE 'prefix%'`

Admins search users by the beginning of their e-mail address: `WHERE lower(email) LIKE 'mia.hill%'`. A prefix search is a range in sorted order ("from `mia.hill` up to, but not including, `mia.hilm`"), so a B-tree should be able to answer it. Yet the existing `users_email_lower_key` index is ignored:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, email FROM users WHERE lower(email) LIKE 'mia.hill%';
```

```text
 Seq Scan on users  (cost=0.00..2660.00 rows=10 width=40) (actual time=2.006..61.974 rows=14 loops=1)
   Filter: (lower((email)::text) ~~ 'mia.hill%'::text)
   Rows Removed by Filter: 99986
```

The reason is the **collation**. The `bazaar` database uses `en_US.utf8`, whose sort order is linguistic: case and punctuation are compared only as tie-breakers, so `'a' < 'B'` is true (in byte order, `'B'` comes first), and multi-character rules apply in some languages. Under such rules, the strings starting with `mia.hill` are not guaranteed to form one contiguous range of the index, so PostgreSQL refuses to turn `LIKE` into a range scan. It only does so for indexes in the **`C` collation** (plain byte order), or for indexes built with a **pattern operator class** that compares byte by byte regardless of the collation:

```sql
SELECT 'a' < 'B' AS en_us, 'a' < 'B' COLLATE "C" AS c;
```

```text
 en_us | c
-------+---
 t     | f
```

```sql
CREATE INDEX users_email_lower_pattern_idx ON users (lower(email) text_pattern_ops);

EXPLAIN (ANALYZE, BUFFERS)
SELECT id, email FROM users WHERE lower(email) LIKE 'mia.hill%';
```

```text
 Index Scan using users_email_lower_pattern_idx on users  (cost=0.42..8.44 rows=10 width=40) (actual time=0.034..0.068 rows=14 loops=1)
   Index Cond: ((lower((email)::text) ~>=~ 'mia.hill'::text) AND (lower((email)::text) ~<~ 'mia.hilm'::text))
   Filter: (lower((email)::text) ~~ 'mia.hill%'::text)
```

The planner rewrote the `LIKE` into a range with the byte-wise operators `~>=~` and `~<~`, and kept the original `LIKE` as a filter. `text_pattern_ops` (and `varchar_pattern_ops`, `bpchar_pattern_ops`) indexes support `=` and the pattern-range operators, but **not** ordinary `<`, `>` or `ORDER BY` in the database's collation, which is why `users_email_lower_key` remains necessary for sorting. Exercise 6 shows how that combination can trick the planner. The alternative is an index with an explicit collation, `(lower(email) COLLATE "C")`, which supports `LIKE 'prefix%'` *and* ordering, but only for queries that also say `COLLATE "C"`.

What no B-tree can do is a **leading wildcard**: `LIKE '%hill42%'` has no prefix, so there is no range to scan, and you get a sequential scan whatever you index. Infix search, case-insensitive `ILIKE` and fuzzy matching need a trigram GIN index (`pg_trgm`), which is Module 13's topic.

```sql
DROP INDEX users_email_lower_pattern_idx;
```

> **Pitfall:** Indexes on text depend on the collation library's sort order. If the operating system's glibc or ICU version changes the rules (major OS upgrades have done this, for example glibc 2.28 in 2018), existing indexes can become silently *corrupt*: entries are in the "wrong" order, and lookups miss rows. PostgreSQL records the collation version it saw and logs a warning when it changes (`WARNING: database "bazaar" has a collation version mismatch`). The fix is to `REINDEX` the affected text indexes, then `ALTER DATABASE bazaar REFRESH COLLATION VERSION`. Byte-order (`C`) indexes are immune. Plan for this whenever you upgrade the operating system under a database, or restore a physical backup onto a different OS release (Module 18).

---

## 17. Building indexes on a live system

### 17.1 What a plain `CREATE INDEX` locks

`CREATE INDEX` reads the whole table, sorts the keys and writes the index in one pass, which is fast. To guarantee that no row is missed, it takes a **`SHARE` lock** on the table for the entire build. A `SHARE` lock allows reads but conflicts with the `ROW EXCLUSIVE` lock that every `INSERT`, `UPDATE` and `DELETE` needs. Watch it from a second session while a build is running (here the build is wrapped in a transaction with a `pg_sleep` to stretch it out):

```sql
-- session 1
BEGIN;
CREATE INDEX lab_lock_demo ON orders (total);
SELECT pg_sleep(4);
ROLLBACK;

-- session 2, during the sleep
SET lock_timeout = '2s';
INSERT INTO orders (user_id, seller_id, total) VALUES (1, 1, 0);

-- session 3
SELECT l.pid, a.query, l.mode, l.granted
FROM pg_locks AS l
JOIN pg_stat_activity AS a USING (pid)
WHERE l.relation = 'orders'::regclass
ORDER BY l.granted DESC;
```

```text
 pid |                                          query                                          |       mode       | granted
-----+-----------------------------------------------------------------------------------------+------------------+---------
 429 | BEGIN; CREATE INDEX lab_lock_demo ON orders (total); SELECT pg_sleep(4); ROLLBACK;      | ShareLock        | t
 436 | SET lock_timeout='2s'; INSERT INTO orders (user_id, seller_id, total) VALUES (1, 1, 0); | RowExclusiveLock | f
```

and session 2 eventually gives up with `ERROR: canceling statement due to lock timeout`. On a 500,000-row table the build takes half a second, which nobody notices. On a 500-million-row table it takes many minutes, during which no order can be placed.

### 17.2 `CREATE INDEX CONCURRENTLY`

`CREATE INDEX CONCURRENTLY` builds the index **without blocking writes**. It takes only a `SHARE UPDATE EXCLUSIVE` lock, which conflicts with other schema changes and VACUUM but not with reads or writes. The price is that the work is done in several phases, each in its own transaction:

1. **Register the index** in the catalog as *not ready, not valid*, and commit. From now on, every transaction that *starts* writing to the table knows the index exists.
2. **Wait** for all transactions that might have started writing before the index was registered (they don't know about it) to finish.
3. **Build** the index from a snapshot of the table, like a normal build, then mark it *ready*: from now on, every write also updates the new index. Commit.
4. **Wait** again, then **validate**: scan the table a second time and insert any rows that were written during the build but are missing from the index.
5. **Wait** for transactions that might still use old snapshots, then mark the index **valid**: the planner may use it.

So the index is built from two table scans instead of one, and the command waits several times for *other* transactions to finish. It typically takes two to three times longer than a plain build, and **one long-running transaction anywhere in the database (even an idle-in-transaction session) can stall it indefinitely** in a waiting phase. It can't run inside a transaction block:

```sql
BEGIN;
CREATE INDEX CONCURRENTLY x ON users (full_name);
```

```text
ERROR:  CREATE INDEX CONCURRENTLY cannot run inside a transaction block
```

You can follow a build's progress from another session, conveniently with psql's `\watch` (Module 01):

```sql
SELECT phase, blocks_done, blocks_total, tuples_done, tuples_total
FROM pg_stat_progress_create_index \watch 0.5
```

For `CREATE INDEX CONCURRENTLY order_items_unit_price_idx ON order_items (unit_price)`, sampling the view every quarter of a second (with `psql -At`, so columns are separated by `|`) gave:

```text
initializing|0|0|0|0
building index: sorting live tuples|9316|9316|0|0
building index: sorting live tuples|9316|9316|0|0
building index: loading tuples in tree|0|0|946571|1266928
```

followed by the validation phases. The phase names include `waiting for writers before build` and `waiting for old snapshots`: if a build sits in one of those, look in `pg_stat_activity` for the old transaction holding it up (Module 17).

### 17.3 Invalid indexes

If a concurrent build fails part-way, because of a unique violation, a deadlock, a cancelled statement or a lost connection, it can't roll back the catalog entries it already committed. It leaves an **invalid index** behind: the planner never uses it, and whether it still costs you anything depends on how far the build got. If it failed during the build of step 3 (a duplicate already in the table, as below, or a cancel during that scan), it was never marked ready (`pg_index.indisready = f`), so the index is empty and writes ignore it. If it failed later, in step 4 or 5, it had already been marked ready (`indisvalid = f`, `indisready = t`): it is *maintained on every write*, so it costs you write time and space for nothing. Either way, drop it.

```sql
CREATE UNIQUE INDEX CONCURRENTLY lab_users_full_name_key ON users (full_name);
```

```text
ERROR:  could not create unique index "lab_users_full_name_key"
DETAIL:  Key (full_name)=(Hugo Nelson) is duplicated.
```

```sql
\d users
```

```text
Indexes:
    "users_pkey" PRIMARY KEY, btree (id)
    "lab_users_full_name_key" UNIQUE, btree (full_name) INVALID
    "users_email_lower_key" UNIQUE, btree (lower(email::text))
```

A statement timeout in a migration tool does the same thing:

```sql
SET statement_timeout = '300ms';
CREATE INDEX CONCURRENTLY order_items_unit_price_idx ON order_items (unit_price);
-- ERROR:  canceling statement due to statement timeout
RESET statement_timeout;

CREATE INDEX CONCURRENTLY IF NOT EXISTS order_items_unit_price_idx ON order_items (unit_price);
-- NOTICE:  relation "order_items_unit_price_idx" already exists, skipping
```

That last line is a classic production trap: a migration that retries with `IF NOT EXISTS` "succeeds" and leaves the invalid index in place forever. Find invalid indexes with:

```sql
SELECT i.indexrelid::regclass AS index_name, i.indrelid::regclass AS table_name
FROM pg_index AS i
WHERE NOT i.indisvalid;
```

and fix them by dropping and rebuilding:

```sql
DROP INDEX CONCURRENTLY order_items_unit_price_idx;
CREATE INDEX CONCURRENTLY order_items_unit_price_idx ON order_items (unit_price);
DROP INDEX CONCURRENTLY lab_users_full_name_key;
DROP INDEX order_items_unit_price_idx;
```

`DROP INDEX CONCURRENTLY` likewise avoids blocking queries on the table while dropping.

### 17.4 `REINDEX` and `REINDEX CONCURRENTLY`

`REINDEX INDEX name` rebuilds an index from scratch: to remove bloat (section 19), after a collation change, or to repair a corrupted index. Plain `REINDEX` locks out writes *and* reads that would use the index. **`REINDEX INDEX CONCURRENTLY`** (PostgreSQL 12+) builds a new copy next to the old one, with the same phases as `CREATE INDEX CONCURRENTLY`, then swaps them and drops the old one. It needs disk space for the second copy while it runs. If it fails, it leaves the new copy behind as an invalid index with the suffix `_ccnew`; drop it.

> **In production:** The checklist for an index on a busy table: build it with `CONCURRENTLY`; outside a transaction (most migration tools need a "no transaction" flag for that step; Module 26 covers Alembic); with a generous `statement_timeout` but a short `lock_timeout`; at a quiet time if possible; then verify that `indisvalid` is true. Raise `maintenance_work_mem` for the session (`SET maintenance_work_mem = '1GB'`) to speed up the sort. B-tree builds use up to `max_parallel_maintenance_workers` (default 2) extra processes.

---

## 18. Finding missing and unused indexes

### 18.1 Missing indexes: who is reading whole tables?

PostgreSQL's cumulative statistics count, per table, how many sequential and index scans were started and how many rows they returned. A large table with many sequential scans returning many rows is the classic sign of a missing index. Here is `pg_stat_user_tables` after the milestone's hot-query workload ran five times on the database **without** this module's indexes:

```sql
SELECT relname,
       seq_scan,
       seq_tup_read,
       idx_scan,
       seq_tup_read / greatest(seq_scan, 1) AS avg_rows_per_seq_scan
FROM pg_stat_user_tables
WHERE schemaname = 'bazaar'
ORDER BY seq_tup_read DESC
LIMIT 6;
```

```text
   relname   | seq_scan | seq_tup_read | idx_scan | avg_rows_per_seq_scan
-------------+----------+--------------+----------+-----------------------
 order_items |       10 |     12669280 |       15 |               1266928
 orders      |       10 |      5000000 |      160 |                500000
 products    |       15 |      3000000 |       30 |                200000
 payments    |        5 |      2684080 |        0 |                536816
 addresses   |        5 |       625085 |        0 |                125017
 cart_items  |        5 |       99215 |        0 |                 19843
```

Every sequential scan on `order_items` read all 1.27 million rows. These counters tell you *which tables* to look at, not which queries; for that, combine them with `pg_stat_statements` and `EXPLAIN`, which is Module 12's method. Remember that a sequential scan is sometimes the right plan (a nightly report reading the whole table), so judge by the *average rows per scan* and by how often it happens. Reset the counters with `SELECT pg_stat_reset();` before a measurement.

### 18.2 Unused indexes

The mirror image: indexes that are never scanned only cost write time and space. `pg_stat_user_indexes.idx_scan` counts how often each index was used. After the same workload ran three times **with** the reference indexes:

```sql
SELECT s.relname AS table_name,
       s.indexrelname AS index_name,
       s.idx_scan,
       pg_size_pretty(pg_relation_size(s.indexrelid)) AS size,
       i.indisunique AS is_unique
FROM pg_stat_user_indexes AS s
JOIN pg_index AS i USING (indexrelid)
WHERE s.schemaname = 'bazaar'
  AND s.idx_scan = 0
ORDER BY pg_relation_size(s.indexrelid) DESC
LIMIT 12;
```

```text
     table_name     |              index_name              | idx_scan |  size   | is_unique
--------------------+--------------------------------------+----------+---------+-----------
 products           | products_seller_id_sku_key           |        0 | 12 MB   | t
 payments           | payments_pkey                        |        0 | 12 MB   | t
 jobs               | jobs_pkey                            |        0 | 8832 kB | t
 reviews            | reviews_pkey                         |        0 | 6432 kB | t
 orders             | orders_shipping_address_id_idx       |        0 | 5528 kB | f
 seller_sales_daily | seller_sales_daily_seller_id_day_key |        0 | 4728 kB | t
 reviews            | reviews_user_id_idx                  |        0 | 3592 kB | f
 addresses          | addresses_pkey                       |        0 | 2752 kB | t
 addresses          | addresses_one_default_per_user       |        0 | 2208 kB | t
 users              | users_pkey                           |        0 | 2208 kB | t
 products           | products_category_id_idx             |        0 | 1384 kB | f
 cart_items         | cart_items_pkey                      |        0 | 624 kB  | t
```

Reading this list correctly matters more than producing it:

- **Unique indexes enforce constraints** even if no query ever scans them. `idx_scan = 0` on `payments_pkey` means no query looked up a payment by id; dropping it would drop the primary key. Never drop a unique index because it's unused.
- **FK indexes are used by deletes on the parent**, which this workload never did. `orders_shipping_address_id_idx` and `reviews_user_id_idx` protect `DELETE FROM addresses` and `DELETE FROM users` (GDPR erasure requests!) from the 7-second scans of section 11. Keep them unless the parent is truly never deleted.
- **The observation window matters.** Statistics count since the last reset. An index used only by the month-end report shows zero for 29 days. Look at a window that covers every business cycle, and remember that statistics are **per server**: an index unused on the primary may be what the reporting replica lives on (Module 19).

What's left after those filters are real candidates. Here, `products_category_id_idx` serves the FK from categories (rarely deleted) and "all products in a category, any status" for the back office. A judgement call. Module 17 builds a reusable unused-and-duplicate-index report on these views. Exercise 12 finds **redundant** indexes: those whose columns are a prefix of another index's columns.

### 18.3 Size overview

Two functions you'll use constantly: `pg_relation_size(index)` for one index, `pg_indexes_size(table)` for all of a table's indexes, and `pg_table_size(table)` for the table itself, without its indexes:

```sql
SELECT pg_size_pretty(pg_table_size('orders'))   AS table_size,
       pg_size_pretty(pg_indexes_size('orders')) AS index_size;
```

```text
 table_size | index_size
------------+------------
 55 MB      | 46 MB
```

---

## 19. Index bloat

When a row is updated (non-HOT) or deleted, its old index entries stay in the index until VACUUM removes them, and the space they occupied can only be reused by **new entries that belong on the same leaf page**. VACUUM can delete a leaf page only when it becomes completely empty. B-tree pages are never merged with half-empty neighbours. So an index whose keys are updated in a way that moves entries around the key space grows and stays sparse. That is **index bloat**.

`pgstatindex()` (from `pgstattuple`) reports the density of a B-tree's leaf pages. A lab copy of `inventory` with an index on `updated_at`, then three rounds of updates that move every row's key:

```sql
CREATE TABLE lab.stock AS SELECT product_id, quantity, reserved, updated_at FROM inventory;
CREATE INDEX stock_updated_at_idx ON lab.stock (updated_at);

SELECT pg_size_pretty(pg_relation_size('lab.stock_updated_at_idx')) AS size,
       avg_leaf_density, leaf_fragmentation
FROM pgstatindex('lab.stock_updated_at_idx');
```

```text
  size   | avg_leaf_density | leaf_fragmentation
---------+------------------+--------------------
 1416 kB |            92.22 |                  0
```

```sql
UPDATE lab.stock SET updated_at = updated_at + interval '1 day', quantity = quantity + 1;   -- 3 times
VACUUM lab.stock;
```

```text
  size   | avg_leaf_density | leaf_fragmentation
---------+------------------+--------------------
 6328 kB |            25.62 |              50.39
```

The index is now 4.5 times bigger and its leaves are a quarter full, although it holds exactly the same 200,000 entries. Every scan reads four times as many pages, and the index takes four times as much cache. A rebuild fixes it:

```sql
REINDEX INDEX CONCURRENTLY lab.stock_updated_at_idx;
```

```text
  size   | avg_leaf_density | leaf_fragmentation
---------+------------------+--------------------
 1416 kB |            92.22 |                  0
```

```sql
DROP TABLE lab.stock;
```

Since PostgreSQL 13 and 14, B-trees resist bloat much better than they used to: deduplication absorbs the duplicates that MVCC creates, and **bottom-up deletion** removes dead versions from a leaf page *before* splitting it. Bloat now comes mostly from workloads like this one, where keys move through the key space, and from mass deletes. Measuring bloat on production systems without reading whole indexes, and scheduling rebuilds, is Module 17's topic.

---

## 20. When the planner ignores your index

You created an index, and `EXPLAIN` still shows `Seq Scan`. Here are the usual reasons, each shown on the large dataset with indexes `orders (user_id)`, `orders (seller_id, created_at)` and `orders (status)` in place.

**1. The condition isn't selective.** 452,767 of 500,000 orders are delivered. With an index on `status`, summing their totals with the index *forced* (by disabling the alternatives) takes 120 ms; the sequential scan the planner chooses on its own takes 94 ms:

```sql
CREATE INDEX lab_orders_status ON orders (status);

SET enable_seqscan = off; SET enable_bitmapscan = off; SET enable_indexonlyscan = off;
EXPLAIN (ANALYZE, BUFFERS) SELECT sum(total) FROM orders WHERE status = 'delivered';
RESET enable_seqscan; RESET enable_bitmapscan; RESET enable_indexonlyscan;
EXPLAIN (ANALYZE, BUFFERS) SELECT sum(total) FROM orders WHERE status = 'delivered';
```

```text
 Aggregate  (cost=25878.44..25878.45 rows=1 width=32) (actual time=120.267..120.269 rows=1 loops=1)
   ->  Index Scan using lab_orders_status on orders  (cost=0.42..24745.40 rows=453217 width=7) (actual time=0.011..77.657 rows=452767 loops=1)
         Index Cond: (status = 'delivered'::order_status)
 ...
 Aggregate  (cost=14484.05..14484.06 rows=1 width=32) (actual time=93.748..93.749 rows=1 loops=1)
   ->  Seq Scan on orders  (cost=0.00..13351.00 rows=453217 width=7) (actual time=0.008..53.803 rows=452767 loops=1)
         Filter: (status = 'delivered'::order_status)
```

The `enable_*` settings don't forbid a plan type; they add a huge cost penalty to it, so the planner avoids it whenever there's an alternative. They are a diagnostic tool for experiments like this one, never a production setting. The same index *is* used for `status = 'pending'` (1,099 rows). An index on a low-cardinality column helps only for its rare values, which is a good argument for a partial index on just those values.

**2. A function or operator is applied to the column.** `WHERE created_at::date = '2025-11-28'`, `WHERE lower(email) = ...` without an expression index, `WHERE date_trunc('day', created_at) = ...`, `WHERE total * 1.19 > 100`, `WHERE created_at + interval '30 days' > now()`. The index stores column values; it can't search for the value of an expression it doesn't store. Fix: rewrite so that the bare column stands alone on one side (`created_at >= '2025-11-28' AND created_at < '2025-11-29'`, `total > 100 / 1.19`, `created_at > now() - interval '30 days'`), or create an expression index.

**3. The types don't match.** Comparing a `bigint` column with a `numeric` value makes PostgreSQL cast the *column*, because `numeric` can't be converted to `bigint` losslessly:

```sql
EXPLAIN (ANALYZE) SELECT id FROM orders WHERE user_id = 4242.0;
```

```text
 Seq Scan on orders  (cost=0.00..14601.00 rows=2500 width=8) (actual time=2.505..58.984 rows=15 loops=1)
   Filter: ((user_id)::numeric = 4242.0)
```

Look for a cast on the column side, `(user_id)::numeric` or `(user_id)::text`, in the `Filter`. It typically comes from application code that sends a decimal or a string type, or from comparing columns of different types in a join. An untyped literal is fine: `WHERE user_id = '4242'` is resolved as `bigint` and uses the index (`Index Cond: (user_id = '4242'::bigint)`). Fix the parameter type in the application, or cast the value, never the column.

**4. `OR` across different columns, or across tables.** Within one table, PostgreSQL can combine indexes with a `BitmapOr`:

```sql
EXPLAIN (ANALYZE, BUFFERS) SELECT id FROM orders WHERE user_id = 4242 OR seller_id = 1500;
```

```text
 Bitmap Heap Scan on orders  (cost=9.55..333.48 rows=88 width=8) (actual time=0.052..0.073 rows=26 loops=1)
   Recheck Cond: ((user_id = 4242) OR (seller_id = 1500))
   ->  BitmapOr  (cost=9.55..9.55 rows=88 width=0) (actual time=0.046..0.046 rows=0 loops=1)
         ->  Bitmap Index Scan on orders_user_id_idx  (cost=0.00..4.50 rows=10 width=0) (actual time=0.018..0.018 rows=15 loops=1)
               Index Cond: (user_id = 4242)
         ->  Bitmap Index Scan on orders_seller_id_created_at_idx  (cost=0.00..5.01 rows=78 width=0) (actual time=0.027..0.027 rows=11 loops=1)
               Index Cond: (seller_id = 1500)
```

But **every** branch of the `OR` needs a usable index. `WHERE user_id = 4242 OR total > 5000` has no index on `total`, so the whole condition becomes a sequential scan. And an `OR` that spans two tables of a join (`WHERE o.user_id = $1 OR oi.product_id = $2`) can't be pushed down to either table, so both are read completely and the `OR` is evaluated on the joined rows. Exercise 9(c) measures that at 723 ms against 0.7 ms for the rewrite as a `UNION` of two indexable queries. Module 12 discusses the `OR` to `UNION` rewrite.

**5. A leading wildcard.** `LIKE '%phones'` and `LIKE '%hill42%'` have no prefix to search for (section 16). Use a trigram index (Module 13).

**6. The table is small.** For `categories` (69 rows, one page), reading the page is cheaper than reading an index *and* the page. The planner correctly ignores `categories_parent_id_idx` for queries; the index exists for FK checks and for the day the table grows.

**7. The planner's estimate is wrong.** The planner chooses by *estimated* row counts. If it thinks a condition matches 100,000 rows when it matches 10, it picks a sequential scan. Stale statistics (after a bulk load, before `ANALYZE`), correlated columns, and parameters in generic plans all cause this. Module 12 is devoted to finding and fixing these misestimates.

**8. The query wants rows the index doesn't contain.** A partial index whose predicate the query doesn't imply (section 8), a `NULLS` ordering or sort direction the index can't provide (section 6), a `COLLATE` that doesn't match (section 16), or an `IS NOT DISTINCT FROM` comparison, which B-trees don't support.

**9. The index is invalid**, left over from a failed `CREATE INDEX CONCURRENTLY` (section 17). `\d table` says `INVALID`.

```sql
DROP INDEX lab_orders_status, orders_user_id_idx, orders_seller_id_created_at_idx;
```

---

## 21. The Bazaar index set

The reference milestone file [`project/db/1102_schema_indexes.sql`](../../project/db/1102_schema_indexes.sql) adds 13 indexes in two groups.

**Foreign keys.** Every FK column that isn't already the leading column of an index: `categories (parent_id)`, `products (category_id)`, `order_items (product_id)`, `orders (shipping_address_id)`, `addresses (user_id)`, `payments (order_id)`, `reviews (user_id)` and `cart_items (product_id)`. The FKs on `orders.user_id` and `orders.seller_id` are covered by the composite indexes below. Those on `sellers.user_id`, `products.seller_id`, `inventory.product_id`, `order_items.order_id`, `reviews.product_id`, `cart_items.user_id`, `product_price_history.product_id` and `promotions.product_id` were already covered by primary keys, unique constraints and the promotions GiST index.

**Hot queries.**

| Index | Serves |
|---|---|
| `orders (user_id, created_at DESC)` | "My orders, newest first", plus the FK on `user_id` |
| `orders (seller_id, created_at DESC)` | Seller dashboard: recent orders, orders this month, plus the FK on `seller_id` |
| `products (category_id, price) WHERE status = 'active'` | Category pages sorted by price, price-range filters |
| `products USING gin (attributes jsonb_path_ops)` | Attribute filters: `attributes @> '{"brand": "Voltix", "ram_gb": 16}'` |
| `products USING gin (tags)` | Tag filters: `tags @> '{eco}'`, `tags && '{tents,sleeping-bags}'` |

The `DESC` in the two `orders` indexes isn't needed for single-seller queries (a backward scan works equally well), but it documents the access pattern and makes the index match `WHERE seller_id IN (...) ORDER BY seller_id, created_at DESC` without the incremental sort of section 6.2. The existing `users_email_lower_key` already serves the admin's e-mail lookup, and Module 10's `jobs_ready_idx` serves the queue.

The milestone's check script runs the eleven hot queries of the Bazaar workload (`project.md`) and reports time and buffers for each. Before and after `1102_schema_indexes.sql`, on this machine:

| Query | Before: ms | Before: buffers | After: ms | After: buffers | Index used after |
|---|---:|---:|---:|---:|---|
| 01 my orders | 42.28 | 7,095 | 0.20 | 21 | `orders_user_id_created_at_idx` |
| 02a order items | 0.09 | 15 | 0.10 | 12 | `order_items_pkey`, `products_pkey` |
| 02b order payments | 40.33 | 6,101 | 0.10 | 4 | `payments_order_id_idx` |
| 03 seller dashboard | 38.69 | 7,095 | 0.36 | 46 | `orders_seller_id_created_at_idx` |
| 04 category browse | 40.32 | 10,318 | 0.28 | 26 | `products_active_category_id_price_idx` |
| 05 attribute filter | 73.22 | 10,315 | 1.80 | 164 | `products_attributes_idx` |
| 06 tag filter | 77.64 | 10,315 | 3.92 | 399 | `products_tags_idx` |
| 07 admin email lookup | 0.06 | 4 | 0.05 | 4 | `users_email_lower_key` (from Module 08) |
| 08 jobs dequeue | 0.15 | 14 | 0.07 | 14 | `jobs_ready_idx` (from Module 10) |
| 09 buyers of a product | 99.61 | 9,436 | 0.72 | 153 | `order_items_product_id_idx` |
| 10 address book | 21.40 | 1,786 | 0.04 | 5 | `addresses_user_id_idx` |
| 11 delete a product | 81.22 | 20 | 2.39 | 19 | FK checks via `order_items_product_id_idx` |

(Query 11's buffer count covers only the delete itself, not the FK triggers, which is why it looks harmless in the "before" column.) The thirteen indexes add 79 MB to a 480 MB database. In exchange, every hot query now reads at most a few hundred pages and runs in a few milliseconds at most: the whole workload went from 515 ms and 62,514 buffers to 10 ms and 867 buffers.

---

## Summary

- An index is a separate, derived structure that maps values to row addresses (`ctid`s). It speeds up selective reads and slows down every write: five indexes made a bulk insert 5× slower and quadrupled its WAL, and indexing an updated column disables HOT updates.
- The planner chooses between **sequential**, **index**, **bitmap** and **index-only** scans based on estimated selectivity. Indexes help selective conditions; for a large fraction of the table, a sequential scan wins.
- A **B-tree** is a flat, balanced tree of 8 KB pages with sorted keys; lookups cost 3–4 page reads. Deduplication (PG 13+) stores repeated keys once with a posting list.
- B-trees answer `=`, ranges, `IN`, `IS NULL`, `ORDER BY`, `min`/`max`, and prefix `LIKE` (with `C` collation or `text_pattern_ops`). They can't help with `<>`, leading wildcards, or functions applied to the column.
- **Multicolumn indexes**: equality columns first, then one range or sort column. An index on `(a, b)` makes one on `(a)` redundant. It can't *search* by `b` alone.
- An index whose order matches `ORDER BY` turns "sort everything, keep 20" into "read 20 entries". Mind `DESC`, `NULLS FIRST/LAST` and mixed directions.
- **Index-only scans** need all columns in the index (`INCLUDE` adds non-key columns) and an up-to-date visibility map; watch `Heap Fetches`.
- **Partial indexes** index a subset (`WHERE status = 'active'`) and enforce conditional uniqueness. The query must imply the predicate, so use literals, not parameters.
- **Expression indexes** need immutable functions and queries that use the same expression; run `ANALYZE` afterwards.
- Unique constraints are implemented by unique indexes. Use a constraint for plain-column rules and an index for expressions or predicates.
- PostgreSQL doesn't index FK columns on the referencing side. Without such an index, deleting a parent row scans the child table: 7 s versus 14 ms for 100 products.
- **GIN** is an inverted index for arrays, `jsonb` (`jsonb_path_ops` for `@>`, `jsonb_ops` for `?`) and text search; fast update trades write cost for a pending list. **GiST** handles ranges, exclusion constraints and nearest-neighbour search; **SP-GiST** partitions space. **BRIN** stores per-block-range summaries: tiny and excellent for physically ordered data, useless otherwise. **Hash** supports only `=`.
- Build indexes on live tables with `CREATE INDEX CONCURRENTLY`, check for **invalid** indexes afterwards (and beware `IF NOT EXISTS`), and rebuild bloated ones with `REINDEX CONCURRENTLY`.
- `pg_stat_user_tables` (sequential scans of big tables) points at missing indexes; `pg_stat_user_indexes` (`idx_scan = 0`) at unused ones, except unique and FK indexes.

## What's next

You can now make a query fast when you know which query is slow and why. [Module 12](../12-query-performance/) teaches the other half: reading full execution plans, understanding the planner's cost model and statistics, finding the slow queries with `pg_stat_statements`, and fixing the ones no index can save.
