# Module 12: Query Planning and Performance

Module 11 gave Bazaar its indexes, and the hot queries of the storefront now run in milliseconds. Then the back office starts using the system. The fraud team's report takes a minute. A seller with 70,000 orders clicks to page 3,000 of her order list and waits; a "how many customers never ordered?" query never comes back at all; and a marketing report about Berlin customers gets *slower* after someone fixes its row estimates. Indexes don't fix any of this. What fixes it is understanding how PostgreSQL decides to run a query, reading the plan it chose, and knowing which of its assumptions failed.

This module teaches you to read execution plans fluently, to understand the planner's cost model and statistics well enough to predict its choices, to find the queries worth fixing with `pg_stat_statements`, and to recognise the anti-patterns behind most slow queries.

**What you'll learn**

- How a query becomes a plan, and what the planner optimises.
- `EXPLAIN` and its options: `ANALYZE`, `BUFFERS`, `SETTINGS`, `WAL`, `SERIALIZE` and `MEMORY` (PostgreSQL 17), `GENERIC_PLAN`, `VERBOSE`, `FORMAT JSON`.
- Reading a plan as a tree: execution order, estimated versus actual rows, timing per loop, and the "rows × loops" trap.
- Scan nodes, the three join algorithms (nested loop, hash join, merge join) and when each wins, hash batches and spills to disk.
- Aggregation (`HashAggregate`, `GroupAggregate`), sort methods (quicksort, top-N heapsort, external merge, incremental sort), `Memoize`, `Materialize`, parallel query (`Gather`), CTE scans, sub-plans and `Append`.
- The cost model: `seq_page_cost`, `random_page_cost`, the `cpu_*` costs, `effective_cache_size`, and how they steer plan choice.
- Planner statistics: `ANALYZE`, `pg_stats`, MCV lists, histograms, statistics targets.
- Estimation errors: correlated columns and extended statistics, expressions, stale statistics, skew and generic plans for prepared statements.
- `pg_stat_statements` for finding the queries that matter, and `auto_explain` for catching plans in the act.
- The classic anti-patterns and their fixes: N+1, `OFFSET` pagination, `SELECT *`, functions on indexed columns, implicit casts, `OR` across tables, `NOT IN`, `count(*)` on big tables, correlated subqueries, CTE fences.
- A systematic method for performance investigations, and `pg_hint_plan` as a last resort.

**Before you start**

This module needs the large dataset with Module 11's indexes:

```bash
./project/bin/reset.sh 12      # large dataset + 1102_schema_indexes.sql (about 2 minutes)
./project/bin/psql.sh
```

If you finished Module 11's milestone, your database is already in this state. During the module you create the two objects of this module's reference migration, `project/db/1201_schema_stats.sql`: the extended statistics in section 9.1 and the `pg_stat_statements` extension in section 10. After that, your database matches `reset.sh 13`.

**About the numbers.** Every plan and timing below is real output from PostgreSQL 17.11 in Docker on a 4-vCPU machine with 15 GB of RAM, using the default configuration (`shared_buffers = 128MB`, `work_mem = 4MB`, `random_page_cost = 4`). The machine was shared with other workloads, so timings of the same query vary by up to a factor of two between runs. Compare orders of magnitude and **buffer counts** (stable), not exact milliseconds. Two more things vary on your machine: `ANALYZE` samples rows at random, so estimates (`rows=` in plans) can differ slightly from the ones shown; and the first run of a query reads from disk (`read=`) while later runs hit the cache (`hit=`).

Most examples disable parallel query to keep plans readable. Section 6.5 explains parallel plans:

```sql
SET max_parallel_workers_per_gather = 0;
```

---

## 1. From SQL to a plan

When PostgreSQL receives a query, it goes through four stages:

1. **Parse**: check the syntax and build a parse tree.
2. **Analyze and rewrite**: resolve names to tables, columns, types and functions; expand views (Module 09) and apply rules.
3. **Plan**: choose *how* to execute the query. SQL says *what* you want; there are usually many ways to get it. Which index, if any, for each table? Which order to join five tables in, and with which algorithm? Sort, or read an index in order? The planner (also called the optimizer) enumerates the alternatives, estimates the **cost** of each, and picks the cheapest.
4. **Execute**: run the chosen plan and stream rows back.

The planner never runs the alternatives; it *estimates* them, from statistics about your data (section 8) and a cost model of your hardware (section 7). Almost every bad plan comes from one of three causes:

- a **wrong estimate** of how many rows some step produces (the most common by far);
- a **cost model** that doesn't match the hardware (`random_page_cost` tuned for spinning disks on an SSD server);
- a query written so that the good plan is **impossible**: no index can serve it, or a construct (a correlated sub-query, `NOT IN`, a function on a column) rules out the efficient algorithm.

This module teaches you to tell these apart from the plan.

---

## 2. The EXPLAIN family

### 2.1 `EXPLAIN`: the plan and its estimates

`EXPLAIN` shows the plan the planner chose, with its estimates, *without running the query*. Here is user 4242's order history with product titles:

```sql
EXPLAIN
SELECT o.id, o.created_at, p.title, oi.quantity, oi.unit_price
FROM orders AS o
JOIN order_items AS oi ON oi.order_id = o.id
JOIN products AS p ON p.id = oi.product_id
WHERE o.user_id = 4242
ORDER BY o.created_at DESC;
```

```text
                                                 QUERY PLAN
-------------------------------------------------------------------------------------------------------------
 Nested Loop  (cost=1.27..172.63 rows=25 width=58)
   ->  Nested Loop  (cost=0.85..160.83 rows=25 width=35)
         ->  Index Scan using orders_user_id_created_at_idx on orders o  (cost=0.42..40.73 rows=10 width=16)
               Index Cond: (user_id = 4242)
         ->  Index Scan using order_items_pkey on order_items oi  (cost=0.43..11.98 rows=3 width=27)
               Index Cond: (order_id = o.id)
   ->  Index Scan using products_pkey on products p  (cost=0.42..0.47 rows=1 width=39)
         Index Cond: (id = oi.product_id)
```

Each line is a **plan node**. The numbers in parentheses are estimates:

- **`cost=1.27..172.63`**: the *startup cost* (work before the first row can be returned) and the *total cost* (work to return all rows), in the planner's abstract cost units (section 7). A sort has a high startup cost, since it must read everything before returning anything; an index scan's startup cost is the descent to the first leaf.
- **`rows=25`**: the estimated number of rows this node returns (*per execution*, see section 3.3).
- **`width=58`**: the estimated average row size in bytes.

`EXPLAIN` alone is safe to run on anything, including a `DELETE`, and it's instant. But it only shows what the planner *believes*.

### 2.2 `EXPLAIN ANALYZE`: what really happened

`EXPLAIN ANALYZE` **runs** the query, measures every node, and prints the actual numbers next to the estimates:

```sql
EXPLAIN ANALYZE
SELECT o.id, o.created_at, p.title, oi.quantity, oi.unit_price
FROM orders AS o
JOIN order_items AS oi ON oi.order_id = o.id
JOIN products AS p ON p.id = oi.product_id
WHERE o.user_id = 4242
ORDER BY o.created_at DESC;
```

```text
                                                                       QUERY PLAN
--------------------------------------------------------------------------------------------------------------------------------------------------------
 Nested Loop  (cost=1.27..172.63 rows=25 width=58) (actual time=0.119..1.177 rows=35 loops=1)
   ->  Nested Loop  (cost=0.85..160.83 rows=25 width=35) (actual time=0.095..0.536 rows=35 loops=1)
         ->  Index Scan using orders_user_id_created_at_idx on orders o  (cost=0.42..40.73 rows=10 width=16) (actual time=0.063..0.186 rows=15 loops=1)
               Index Cond: (user_id = 4242)
         ->  Index Scan using order_items_pkey on order_items oi  (cost=0.43..11.98 rows=3 width=27) (actual time=0.022..0.022 rows=2 loops=15)
               Index Cond: (order_id = o.id)
   ->  Index Scan using products_pkey on products p  (cost=0.42..0.47 rows=1 width=39) (actual time=0.018..0.018 rows=1 loops=35)
         Index Cond: (id = oi.product_id)
 Planning Time: 0.381 ms
 Execution Time: 1.205 ms
```

`actual time=0.119..1.177` is the real time in milliseconds to the first row and to the last row; `rows=35` is the real row count; `loops=1` is how many times the node was executed. Section 3 reads these in detail.

> **Pitfall:** `EXPLAIN ANALYZE` really executes the statement. `EXPLAIN ANALYZE DELETE FROM orders` deletes your orders. For data-changing statements, wrap it in a transaction and roll back:
>
> ```sql
> BEGIN;
> EXPLAIN ANALYZE UPDATE ...;
> ROLLBACK;
> ```
>
> Triggers fire and sequences advance, as with any statement; only the rollback undoes the data changes.

Two other caveats. Measuring every node costs time itself, especially on plans with millions of rows passing through cheap nodes, so `EXPLAIN ANALYZE` can report a longer time than the query takes on its own (`TIMING OFF` removes most of that overhead). And the result rows are computed but **not sent** to the client, so the cost of converting and transmitting them is missing (`SERIALIZE` adds it back, section 2.4).

### 2.3 `BUFFERS`: how much data was touched

Time is noisy; the amount of data touched is not. **Always add `BUFFERS`**:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT o.id, o.created_at, p.title, oi.quantity, oi.unit_price
FROM orders AS o
JOIN order_items AS oi ON oi.order_id = o.id
JOIN products AS p ON p.id = oi.product_id
WHERE o.user_id = 4242
ORDER BY o.created_at DESC;
```

```text
 Nested Loop  (cost=1.27..172.63 rows=25 width=58) (actual time=0.062..0.480 rows=35 loops=1)
   Buffers: shared hit=218
   ->  Nested Loop  (cost=0.85..160.83 rows=25 width=35) (actual time=0.055..0.340 rows=35 loops=1)
         Buffers: shared hit=78
         ->  Index Scan using orders_user_id_created_at_idx on orders o  (cost=0.42..40.73 rows=10 width=16) (actual time=0.010..0.072 rows=15 loops=1)
               Index Cond: (user_id = 4242)
               Buffers: shared hit=18
         ->  Index Scan using order_items_pkey on order_items oi  (cost=0.43..11.98 rows=3 width=27) (actual time=0.007..0.008 rows=2 loops=15)
               Index Cond: (order_id = o.id)
               Buffers: shared hit=60
   ->  Index Scan using products_pkey on products p  (cost=0.42..0.47 rows=1 width=39) (actual time=0.003..0.003 rows=1 loops=35)
         Index Cond: (id = oi.product_id)
         Buffers: shared hit=140
 Planning:
   Buffers: shared hit=32
 Planning Time: 0.545 ms
 Execution Time: 0.549 ms
```

The second run took 0.55 ms instead of 1.2 ms (everything was now in memory), but the buffer counts are what you compare between two versions of a query. The counters, all in 8 KB pages, are cumulative: a node's count includes its children's.

| Counter | Meaning |
|---|---|
| `shared hit` | page found in PostgreSQL's shared buffer cache |
| `shared read` | page not in the cache, requested from the operating system (which may still have it in *its* cache) |
| `shared dirtied` | page modified by this query (hint bits count, Module 16) |
| `shared written` | page this query had to write out itself, to make room in the cache |
| `temp read` / `temp written` | temporary files for sorts, hashes and materialisations that didn't fit in `work_mem` |
| `local ...` | pages of temporary tables |

With `track_io_timing = on` (set by the course's `docker-compose.yml`), `BUFFERS` also prints `I/O Timings: shared read=13.027`, the milliseconds spent waiting for reads: the direct measure of whether a query is I/O-bound.

> **PG 18:** In PostgreSQL 18, `EXPLAIN ANALYZE` includes `BUFFERS` by default. Explicitly writing `BUFFERS` works in both versions.

### 2.4 More options

`EXPLAIN` takes a parenthesised list of options; the most useful beyond `ANALYZE` and `BUFFERS`:

**`SETTINGS`** lists every planner-relevant setting that differs from its default. When someone sends you a plan, this tells you which knobs they had turned:

```sql
SET random_page_cost = 1.1;
SET work_mem = '16MB';
EXPLAIN (SETTINGS) SELECT id FROM orders WHERE user_id = 4242;
RESET random_page_cost; RESET work_mem;
```

```text
 Index Scan using orders_user_id_created_at_idx on orders  (cost=0.42..11.63 rows=10 width=8)
   Index Cond: (user_id = 4242)
 Settings: search_path = 'bazaar, public', max_parallel_workers_per_gather = '0', random_page_cost = '1.1', work_mem = '16MB'
```

**`WAL`** (with `ANALYZE`) shows the write-ahead log generated by a data-changing statement: records, full-page images (`fpi`) and bytes. Marking seller 42's paid orders as shipped:

```sql
BEGIN;
EXPLAIN (ANALYZE, WAL, BUFFERS, COSTS OFF)
UPDATE orders SET status = 'shipped', shipped_at = '2025-12-31 12:00+00'
WHERE seller_id = 42 AND status = 'paid';
ROLLBACK;
```

```text
 Update on orders (actual time=22.205..22.206 rows=0 loops=1)
   Buffers: shared hit=1442 read=46 dirtied=22
   WAL: records=56 fpi=14 bytes=110880
   ->  Bitmap Heap Scan on orders (actual time=19.414..19.490 rows=8 loops=1)
         Recheck Cond: (seller_id = 42)
         Filter: (status = 'paid'::order_status)
         Rows Removed by Filter: 1394
         Heap Blocks: exact=1274
 ...
 Trigger orders_audit: time=8.372 calls=8
 Trigger orders_set_updated_at: time=0.392 calls=8
 Execution Time: 30.686 ms
```

Eight rows updated, 56 WAL records and 108 KB of WAL, mostly the 14 full-page images (the first change to each page after a checkpoint logs the whole 8 KB page; Module 16). Note the trigger lines: Bazaar's audit trigger took 8 of the 31 ms.

**`SERIALIZE`** (PostgreSQL 17, with `ANALYZE`) also converts the result rows to their output format, as if sending them, and reports the time and the volume. That's the part plain `EXPLAIN ANALYZE` hides:

```sql
EXPLAIN (ANALYZE, SERIALIZE, COSTS OFF) SELECT * FROM products WHERE category_id = 3;
```

```text
 Bitmap Heap Scan on products (actual time=0.847..5.095 rows=4244 loops=1)
   Recheck Cond: (category_id = 3)
   Heap Blocks: exact=3520
   ->  Bitmap Index Scan on products_category_id_idx (actual time=0.373..0.373 rows=4244 loops=1)
         Index Cond: (category_id = 3)
 Planning Time: 0.057 ms
 Serialization: time=8.572 ms  output=1865kB  format=text
 Execution Time: 14.100 ms
```

Producing the 4,244 rows took 5 ms; turning them into 1.8 MB of text took 8.6 ms more. Section 12.3 comes back to this.

**`MEMORY`** (PostgreSQL 17) reports how much memory the *planner* used, useful for queries over hundreds of partitions (Module 14): `Planning: Memory: used=87kB allocated=128kB`.

**`GENERIC_PLAN`** (PostgreSQL 16+) plans a query with `$1`-style parameters without values, the way a prepared statement's generic plan is made (section 9.4):

```sql
EXPLAIN (GENERIC_PLAN) SELECT id FROM orders WHERE seller_id = $1 AND created_at >= $2;
```

```text
 Bitmap Heap Scan on orders  (cost=5.69..450.97 rows=124 width=8)
   Recheck Cond: ((seller_id = $1) AND (created_at >= $2))
   ->  Bitmap Index Scan on orders_seller_id_created_at_idx  (cost=0.00..5.66 rows=124 width=0)
         Index Cond: ((seller_id = $1) AND (created_at >= $2))
```

That is handy for queries copied from application logs, where the parameters are often missing.

**`VERBOSE`** adds each node's output columns, schema-qualified names, and the query identifier that `pg_stat_statements` uses (section 10):

```text
 Nested Loop
   Output: o.id, ((oi.quantity)::numeric * oi.unit_price)
   ->  Index Only Scan using orders_pkey on bazaar.orders o
         Output: o.id
         Index Cond: (o.id = 250000)
   ...
 Query Identifier: 1889723946337496455
```

**`COSTS OFF`**, **`TIMING OFF`** and **`SUMMARY OFF`** remove parts of the output, to make plans shorter or (with `TIMING OFF`) to reduce measuring overhead. This module uses `COSTS OFF` where only the plan shape matters.

**`FORMAT JSON`** (or `YAML`, `XML`) gives machine-readable output, with every number in a named field: `"Actual Rows": 15`, `"Shared Hit Blocks": 18`, and so on. Tools like the plan visualisers explain.depesz.com and explain.dalibo.com accept text or JSON. Module 11's milestone check (`check_hot_queries.sql`) reads JSON plans with jsonpath to summarise them.

---

## 3. Reading a plan

### 3.1 A tree, executed from the top

A plan is a tree. Each node takes rows from its children, does one job (scan, join, sort, aggregate, ...) and passes rows to its parent. The text format draws it with indentation and `->` arrows. Here is the order-history plan as a tree:

```text
                        Nested Loop  (35 rows)
                       /                      \
                      /                        \
           Nested Loop  (35 rows)        Index Scan products_pkey
          /                    \            (1 row, 35 loops)
         /                      \
 Index Scan                 Index Scan order_items_pkey
 orders_user_id_created_at   (2 rows, 15 loops)
 (15 rows, 1 loop)
```

Execution is **demand-driven** (a "pull" or iterator model): the top node asks its child for a row, which asks its own children, and so on down to the scans. Rows flow upward one at a time; a `Limit` at the top can stop the whole tree early. Some nodes must consume their entire input before producing anything (`Sort`, `HashAggregate`, the build side of a `Hash Join`); they are called *blocking*, and they're why a plan has a startup cost.

For a join node, the first child is the **outer** input and the second the **inner** input. A nested loop reads one outer row, then runs the inner side for it; that's why the inner index scan on `order_items` has `loops=15`, once per order.

Two consequences of this tree are worth noticing in the plan above. There is no `Sort` node despite the `ORDER BY o.created_at DESC`: the outer index scan returns orders newest first (the index is `(user_id, created_at DESC)`), and nested loops preserve the order of their outer input. And the planner put the most selective table, `orders` filtered by user, at the bottom-left, the start of the pipeline.

### 3.2 Estimated versus actual

On each node, compare `rows` (estimated) with `actual ... rows`. This is the single most important check when reading a plan:

```text
 Index Scan using orders_user_id_created_at_idx on orders o  (cost=0.42..40.73 rows=10 width=16) (actual time=0.063..0.186 rows=15 loops=1)
```

10 estimated, 15 actual: close enough. Estimates within a factor of 2–3 are normal and harmless. An estimate off by 10× or more is a red flag, *especially at the bottom of the tree*, because every node above builds its decisions on it. If the planner thinks a scan returns 10 rows and it returns 100,000, it may choose a nested loop that runs its inner side 100,000 times. Find the **lowest** node where estimate and reality diverge: that's where the plan went wrong. Section 9 is about why estimates fail.

### 3.3 Loops: the "rows × loops" trap

For a node that runs several times, `actual time` and `rows` are **averages per loop**:

```text
 ->  Index Scan using order_items_pkey on order_items oi  (cost=0.43..11.98 rows=3 width=27) (actual time=0.022..0.022 rows=2 loops=15)
```

This node ran 15 times, returning on average 2 rows (rounded; really 35 / 15 = 2.33) and taking 0.022 ms each time. Its total contribution is `rows × loops` ≈ 35 rows and `time × loops` ≈ 0.33 ms. The estimate `rows=3` is per loop too. Forgetting to multiply is the classic plan-reading mistake. Here is the extreme case, from a query in section 12.9:

```text
 ->  Bitmap Heap Scan on orders o2  (cost=11.31..1221.63 rows=372 width=7) (actual time=1.548..8.062 rows=19142 loops=5581)
```

"8 ms, 19,142 rows" looks harmless. Multiplied out: 5,581 executions, **107 million rows**, 45 seconds. Always multiply by `loops`.

`Buffers` are **not** averaged: they are totals over all loops.

### 3.4 Where did the time go?

`actual time` of a node **includes** its children. To find the node that did the work, compute each node's *exclusive* time: its own total time (× loops) minus its children's. In the order-history plan, the top `Nested Loop` finished at 0.480 ms; its children took 0.340 ms (the inner nested loop) and 35 × 0.003 = 0.105 ms (the product lookups), so the join logic itself took about 0.035 ms.

For big plans, use a visualiser: explain.depesz.com colours each node by exclusive time; explain.dalibo.com draws the tree. Both accept the text output.

Below the tree:

- **`Planning Time`**: time to choose the plan. Usually under a millisecond; it grows with many joins or many partitions. A query run 10,000 times a second with 1 ms of planning is a reason to use prepared statements (section 9.4).
- **`Execution Time`**: from start to the last row, including triggers but not the network.
- **`Trigger ...: time=... calls=...`**: time spent in triggers, including the internal triggers that check foreign keys (Module 11 showed how a missing FK index hides there).
- **`JIT:`**: just-in-time compilation of expressions, used when a plan's total cost exceeds `jit_above_cost` (100,000). It helps long analytical queries and *hurts* short ones whose cost was overestimated: one plan in section 12.9 spent 154 ms compiling. If you see JIT taking a large share of a fast query, `SET jit = off` for that workload.

### 3.5 Conditions: Index Cond, Filter, Recheck

Where a condition appears tells you how it was applied:

- **`Index Cond`**: used to *search* the index. Only these conditions reduce how much is read.
- **`Filter`**: checked on each row after it was fetched; `Rows Removed by Filter` counts the rejects. A filter that removes most rows means wasted reads, and that a better index (or a better query) could help.
- **`Recheck Cond`** (bitmap heap scans): the condition re-tested on rows from lossy bitmap pages. `Rows Removed by Index Recheck` counts how many failed it.
- **`Join Filter`**: a condition evaluated on joined row pairs that the join algorithm couldn't use, such as an `OR` across tables.
- **`Hash Cond`**, **`Merge Cond`**: the join condition of a hash or merge join.

---

## 4. Scan nodes

Module 11 explained the main ways to read a table. Here is the complete list you'll meet in plans:

| Node | What it does | Typical cue |
|---|---|---|
| `Seq Scan` | reads every page of the table in order | no usable index, or a large fraction of the rows needed |
| `Parallel Seq Scan` | the same, split among workers | big tables, under a `Gather` (section 6.5) |
| `Index Scan` | walks an index, fetches each heap row | few rows, or rows needed in index order |
| `Index Scan Backward` | the same, from the end | `ORDER BY ... DESC` on an ascending index |
| `Index Only Scan` | answers from the index, heap only for non-all-visible pages | all needed columns in the index; watch `Heap Fetches` |
| `Bitmap Index Scan` + `Bitmap Heap Scan` | collects row addresses, then reads heap pages in physical order | hundreds to tens of thousands of rows |
| `BitmapAnd` / `BitmapOr` | combines several bitmaps | conditions on several indexed columns |
| `Tid Scan`, `Tid Range Scan` | fetches rows by `ctid` | `WHERE ctid = '(22,18)'` (rare, maintenance scripts) |
| `Function Scan` | rows from a set-returning function | `generate_series()`, `unnest()`, your own functions |
| `Values Scan` | rows from a `VALUES` list | `FROM (VALUES (1), (2)) AS v(x)` |
| `Subquery Scan` | reads the output of a sub-query that couldn't be flattened | `WHERE` on a window function's result |
| `CTE Scan` | reads a materialised CTE | `WITH ... AS MATERIALIZED`, recursive CTEs (section 6.6) |

A `Function Scan` is opaque: `EXPLAIN` shows the function's estimated rows (1,000 by default for set-returning functions, unless declared with `ROWS`) and nothing about the queries inside it. Section 11 shows how `auto_explain` can see inside.

---

## 5. Join algorithms

PostgreSQL joins two inputs at a time, with one of three algorithms. A five-table join is a tree of four two-input joins. Each algorithm suits a different situation, and recognising which one the planner chose, and why, is half of plan reading.

### 5.1 Nested loop

```text
for each row r in OUTER:
    for each row s in INNER where s matches r:     -- usually an index lookup
        emit (r, s)
```

The simplest algorithm. With an index on the inner side's join column, each outer row costs one index descent, so the total work is proportional to *outer rows × (lookup cost)*. That's unbeatable when the outer side is small: user 4242's 15 orders needed 15 lookups into `order_items` and 35 into `products`, 218 buffers in all. It's also the only algorithm that can join on arbitrary conditions (`ON a.x < b.y`, `ON a.range && b.range`), and it returns its first row immediately, which suits `LIMIT`.

It degrades linearly: 238,000 outer rows mean 238,000 index descents. Without an inner index, each outer row rescans the whole inner input, which is quadratic and catastrophic; PostgreSQL then puts a `Materialize` node on the inner side so the inner input is at least computed only once (section 6.4).

### 5.2 Hash join

```text
build:  read INNER completely; put each row into a hash table keyed by the join column
probe:  for each row r in OUTER: look up r's key in the hash table; emit matches
```

Each input is read **once**, so the cost is proportional to *outer + inner*. That makes hash joins the workhorse for joining large row sets. The planner makes the **smaller** input the inner (build) side. Hash joins only work for equality conditions, and they have a startup cost: nothing comes out until the hash table is built.

Revenue by status for all of 2025 joins 238,093 orders with their 650,301 order lines:

```sql
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT o.status, sum(oi.quantity * oi.unit_price)
FROM orders AS o
JOIN order_items AS oi ON oi.order_id = o.id
WHERE o.created_at >= '2025-01-01'
GROUP BY o.status;
```

```text
 HashAggregate (actual time=834.148..834.154 rows=6 loops=1)
   Group Key: o.status
   Batches: 1  Memory Usage: 24kB
   Buffers: shared hit=7133 read=9278, temp read=3926 written=3926
   ->  Hash Join (actual time=280.198..636.452 rows=650301 loops=1)
         Hash Cond: (oi.order_id = o.id)
         Buffers: shared hit=7133 read=9278, temp read=3926 written=3926
         ->  Seq Scan on order_items oi (actual time=0.070..147.427 rows=1266928 loops=1)
         ->  Hash (actual time=102.609..102.611 rows=238093 loops=1)
               Buckets: 262144  Batches: 2  Memory Usage: 7636kB
               Buffers: shared hit=6979 read=116, temp written=522
               ->  Seq Scan on orders o (actual time=4.602..57.764 rows=238093 loops=1)
                     Filter: (created_at >= '2025-01-01 00:00:00+00'::timestamp with time zone)
                     Rows Removed by Filter: 261907
```

The `Hash` node shows how the build went: `Buckets: 262144` (hash table slots), **`Batches: 2`** and `Memory Usage: 7636kB`. The hash table may use up to `work_mem × hash_mem_multiplier` = 4 MB × 2 = 8 MB. The 238,093 orders need about 13 MB, so PostgreSQL split the join into **2 batches**: it partitions both inputs by hash value, keeps one batch in memory, and writes the rest of both inputs to temporary files to join them afterwards. That's the `temp written=3926` blocks (31 MB). With `SET work_mem = '7MB'` (hash memory 14 MB), the plan shows `Batches: 1  Memory Usage: 13209kB` and no temp I/O.

Spilling a hash join isn't a disaster: each batch is still read sequentially, once more. Here, with the temporary files in the OS cache, it cost under 10% of the run time. It hurts when there are many batches on slow disks, or when the planner *underestimated* the build side, which means the batch count is wrong from the start and some batches end up far larger than planned.

### 5.3 Merge join

```text
sort OUTER and INNER by the join key (or read them in that order from indexes)
walk both sorted lists side by side, like merging two sorted files
```

A merge join needs both inputs sorted on the join key. If an index provides that order, it's cheap; otherwise the sorts dominate. Like a hash join it reads each input once, and it needs no hash table, so it handles huge inputs gracefully and returns rows in key order, which a later `ORDER BY` or `GROUP BY` can reuse. Only equality (and some range) conditions qualify.

### 5.4 When each wins

| Algorithm | Best when | Weakness |
|---|---|---|
| Nested loop | small outer input + indexed inner; `LIMIT`; non-equality joins | cost grows with outer rows × inner lookups |
| Hash join | large unsorted inputs, equality join | needs memory; startup cost; spills to disk |
| Merge join | inputs already sorted (indexes), or the result must be sorted anyway | sorting large unsorted inputs is expensive |

To see the alternatives the planner rejected, disable the chosen algorithm with the `enable_*` settings and look at the costs. For the 2025 revenue query (with `work_mem = '64MB'` to keep spills out of the comparison):

| Plan | Estimated total cost | Actual time |
|---|---:|---:|
| Hash join (chosen) | 47,712 | 885 ms |
| Merge join (`enable_hashjoin = off`): two full index scans in `id` order | 97,270 | 950 ms |
| Nested loop (`+ enable_mergejoin = off`): 238,093 index lookups | 197,795 | 1,018 ms |

The planner ranked them correctly, but the real differences are far smaller than the cost ratios (about 1 : 2 : 4) suggest. Everything was cached in memory, and most of the time went into the `HashAggregate` summing 650,301 `numeric` products, which is the same in all three plans. The cost model assumes some of those index lookups go to disk; on this machine none did. Section 7 shows a case where that difference flips the choice.

### 5.5 Join order

For a query joining *n* tables, the number of possible join orders grows factorially. The planner searches them exhaustively up to `join_collapse_limit` (default 8) tables, using dynamic programming; beyond `geqo_threshold` (12) tables in one `FROM`, it switches to a genetic algorithm that finds a good-enough order. You rarely need to touch these. Know that with 10+ joins, planning time grows, and that explicit `JOIN` syntax beyond the collapse limits fixes the order as written.

---

## 6. Aggregation, sorting and other nodes

### 6.1 HashAggregate versus GroupAggregate

`GROUP BY` and `DISTINCT` are computed in one of two ways:

- **`HashAggregate`**: one hash table entry per group, filled while reading the input in any order. Needs memory proportional to the number of *groups*.
- **`GroupAggregate`**: reads input **sorted** by the group key and emits each group when the key changes. Needs almost no memory, but needs sorted input (a `Sort` node or an index).

"The ten best-selling products of all time" groups 1.27 million order lines into 169,483 products:

```sql
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT product_id, sum(quantity) AS units
FROM order_items
GROUP BY product_id
ORDER BY units DESC
LIMIT 10;
```

```text
 Limit (actual time=1108.359..1108.364 rows=10 loops=1)
   Buffers: shared hit=1261231 read=5971 written=78
   ->  Sort (actual time=1108.357..1108.360 rows=10 loops=1)
         Sort Key: (sum(quantity)) DESC
         Sort Method: top-N heapsort  Memory: 25kB
         ->  GroupAggregate (actual time=2.512..1081.999 rows=169483 loops=1)
               Group Key: product_id
               ->  Index Scan using order_items_product_id_idx on order_items (actual time=0.021..896.161 rows=1266928 loops=1)
```

The planner expected about 91,000 groups (`n_distinct` of `product_id` is underestimated; there are 169,483) and judged that hashing them in 8 MB of hash memory would spill, so it chose the memory-free strategy: read all 1.27 million order lines **in `product_id` order through the index**, which means 1.26 million buffer accesses, one heap page per row. Give it memory, and it switches:

```sql
SET work_mem = '64MB';
```

```text
 Limit (actual time=545.046..545.050 rows=10 loops=1)
   Buffers: shared hit=7364 read=1952
   ->  Sort (actual time=545.043..545.045 rows=10 loops=1)
         Sort Key: (sum(quantity)) DESC
         Sort Method: top-N heapsort  Memory: 25kB
         ->  HashAggregate (actual time=496.123..526.624 rows=169483 loops=1)
               Group Key: product_id
               Batches: 1  Memory Usage: 18449kB
               ->  Seq Scan on order_items (actual time=0.016..110.517 rows=1266928 loops=1)
```

Twice as fast, with 9,316 buffers instead of 1.27 million. A `HashAggregate` that runs out of memory spills too (PostgreSQL 13+): forcing the hash plan with the default 4 MB shows `Batches: 5  Memory Usage: 8241kB  Disk Usage: 11568kB` and takes 625 ms, still faster than the index-ordered `GroupAggregate`. In `EXPLAIN` without `ANALYZE`, a planned spill appears as `Planned Partitions: 8`.

### 6.2 Sort methods

A `Sort` node reports its method:

- **`quicksort  Memory: 77kB`**: everything fit in `work_mem`.
- **`top-N heapsort  Memory: 25kB`**: `ORDER BY ... LIMIT n`; only the best *n* rows are kept, in a heap. Very cheap in memory, whatever the input size.
- **`external merge  Disk: 41000kB`**: the input didn't fit; PostgreSQL sorted chunks of `work_mem` size, wrote them to temporary files, and merged them.
- **`Incremental Sort`** (PostgreSQL 13+): the input is already sorted by a prefix of the key (`Presorted Key`), so only groups of equal prefix need sorting. You met it in Module 11.

Sorting all 1.27 million order lines by price shows the external merge, and a surprise:

```sql
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT order_id, product_id, unit_price FROM order_items ORDER BY unit_price, order_id;
```

```text
 Sort (actual time=1383.266..1554.052 rows=1266928 loops=1)
   Sort Key: unit_price, order_id
   Sort Method: external merge  Disk: 41000kB
   Buffers: shared hit=7417 read=1902, temp read=10240 written=10260
 ...
 Execution Time: 1622.440 ms
```

With `SET work_mem = '128MB'` the sort runs in memory: `Sort Method: quicksort  Memory: 98642kB` and **2,508 ms**, slower than the disk-based sort. An external merge sorts small runs that fit in the CPU caches and merges them sequentially, while one huge in-memory quicksort jumps around 98 MB of memory. The temporary files never really touched the disk here; they stayed in the OS page cache. "Spills are bad, more `work_mem` is good" is a rule of thumb, not a law: measure.

> **In production:** `work_mem` is allocated **per sort or hash node, per query, per parallel worker**. A query with five hash joins running in 100 connections can use 500 × `work_mem`. Raise it moderately server-wide and generously for specific reports, with `SET LOCAL work_mem = '256MB'` inside the report's transaction, or `ALTER ROLE reporting SET work_mem = ...`. Module 17 covers memory sizing; `log_temp_files` logs every spill.

### 6.3 Memoize

A `Memoize` node (PostgreSQL 14+) sits on the inner side of a nested loop and **caches** inner results by join key. If the outer side contains repeated keys, repeated lookups are served from the cache instead of re-running the index scan. With `random_page_cost = 1.1` (section 7), the planner chose one to look up the shipping addresses of seller 1's December orders:

```text
 Nested Loop (actual time=0.646..57.497 rows=4388 loops=1)
   ->  Bitmap Heap Scan on orders o (actual time=0.340..5.244 rows=4388 loops=1)
         Recheck Cond: ((seller_id = 1) AND (created_at >= '2025-12-01 00:00:00+00'::timestamp with time zone))
   ->  Memoize (actual time=0.011..0.011 rows=1 loops=4388)
         Cache Key: o.shipping_address_id
         Cache Mode: logical
         Hits: 91  Misses: 4297  Evictions: 0  Overflows: 0  Memory Usage: 517kB
         ->  Index Scan using addresses_pkey on addresses a (actual time=0.011..0.011 rows=1 loops=4297)
               Index Cond: (id = o.shipping_address_id)
```

Read `Hits` and `Misses`: only 91 of 4,388 lookups were repeats (customers who ordered twice from seller 1 in December). The planner expected more. Here the cache cost little; when hits dominate (looking up a few hundred categories or sellers for many thousands of rows), `Memoize` saves most of the inner work. `enable_memoize = off` disables it for experiments.

### 6.4 Materialize

A `Materialize` node stores its child's output (in memory, spilling to disk past `work_mem`) so that it can be read again cheaply, typically on the inner side of a nested loop or merge join without an index. You'll see one in section 12.7, under a `NOT IN` sub-plan.

### 6.5 Parallel query

For large scans, PostgreSQL can split the work among **parallel workers**: separate processes that each scan part of the table (Module 16 shows them in `pg_stat_activity`). With parallelism back on:

```sql
RESET max_parallel_workers_per_gather;

EXPLAIN (ANALYZE, COSTS OFF) SELECT count(*) FROM order_items;
```

```text
 Finalize Aggregate (actual time=94.880..94.943 rows=1 loops=1)
   ->  Gather (actual time=94.871..94.936 rows=3 loops=1)
         Workers Planned: 2
         Workers Launched: 2
         ->  Partial Aggregate (actual time=88.439..88.441 rows=1 loops=3)
               ->  Parallel Seq Scan on order_items (actual time=0.023..53.550 rows=422309 loops=3)
 Planning Time: 0.101 ms
 Execution Time: 95.007 ms
```

Read it bottom-up. The `Parallel Seq Scan` ran in **3 processes** (`loops=3`: two workers plus the leader, which also works), each scanning about a third of the table, 422,309 rows on average. Each process computed a `Partial Aggregate` (its own count); `Gather` collected the three partial results, and `Finalize Aggregate` added them up. `Gather Merge` is the order-preserving variant, which merges sorted streams from workers. Without parallelism the same count took 179 ms.

The knobs:

| Setting | Default | Meaning |
|---|---|---|
| `max_parallel_workers_per_gather` | 2 | workers per `Gather` node |
| `max_parallel_workers` | 8 | workers for the whole server |
| `max_worker_processes` | 8 | all background workers (restart to change) |
| `min_parallel_table_scan_size` | 8MB | smaller tables are never scanned in parallel |
| `parallel_setup_cost`, `parallel_tuple_cost` | 1000, 0.1 | the planner's price of starting workers and passing rows |

`Workers Launched` lower than `Workers Planned` means the server ran out of workers at that moment, and the query ran slower than planned. Parallelism helps big scans and aggregations in reports; for short OLTP queries the start-up cost outweighs it. Functions marked `PARALLEL UNSAFE` (the default for your own functions!) and data-modifying statements prevent parallel plans.

### 6.6 CTEs: inlined or materialised

Since PostgreSQL 12, a non-recursive CTE (`WITH x AS (...)`) that is referenced once and has no side effects is **inlined**: the planner treats it as a sub-query and pushes conditions into it. `MATERIALIZED` forces the old behaviour: the CTE is computed once, completely, as an optimisation fence:

```sql
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
WITH recent AS (
    SELECT id, user_id, total, created_at FROM orders WHERE seller_id = 42
)
SELECT * FROM recent WHERE created_at >= '2025-12-01';
```

```text
 Bitmap Heap Scan on orders (actual time=0.109..0.719 rows=100 loops=1)
   Recheck Cond: ((seller_id = 42) AND (created_at >= '2025-12-01 00:00:00+00'::timestamp with time zone))
   Buffers: shared hit=93 read=2
```

The outer `created_at` condition was pushed into the scan; no trace of the CTE remains. With `AS MATERIALIZED`:

```text
 CTE Scan on recent (actual time=11.671..11.865 rows=100 loops=1)
   Filter: (created_at >= '2025-12-01 00:00:00+00'::timestamp with time zone)
   Rows Removed by Filter: 1302
   Buffers: shared hit=93 read=1189
   CTE recent
     ->  Bitmap Heap Scan on orders (actual time=0.500..11.316 rows=1402 loops=1)
           Recheck Cond: (seller_id = 42)
```

All 1,402 of seller 42's orders were fetched (1,282 buffers), then filtered: 15 times slower. `MATERIALIZED` is still useful when a CTE is expensive and referenced several times, or to stop the planner from pushing a condition into a sub-query where it makes the plan worse. A CTE referenced twice, a recursive CTE, and a CTE containing `INSERT`/`UPDATE`/`DELETE` are always materialised. Before PostgreSQL 12, *every* CTE was a fence, so old advice like "CTEs are slow" refers to that.

### 6.7 SubPlan and InitPlan

A sub-query in an expression becomes one of two node types:

- **`InitPlan`**: an *uncorrelated* sub-query, run **once**, its result reused. "Seller 42's orders above the seller's average":

  ```text
   Bitmap Heap Scan on orders (actual time=2.231..2.929 rows=529 loops=1)
     Recheck Cond: (seller_id = 42)
     Filter: (total > (InitPlan 1).col1)
     Rows Removed by Filter: 873
     InitPlan 1
       ->  Aggregate (actual time=1.884..1.885 rows=1 loops=1)
             ->  Bitmap Heap Scan on orders orders_1 (actual time=0.347..1.760 rows=1402 loops=1)
  ```

- **`SubPlan`**: a *correlated* sub-query, which refers to the outer row and runs **once per outer row**: look at its `loops`. Cheap when each execution is an index lookup, disastrous when each execution scans thousands of rows (section 12.9). A `hashed SubPlan` is the special case of `x IN (SELECT ...)` / `NOT IN`, where PostgreSQL runs the sub-query once and builds a hash table, if it fits in `work_mem` (section 12.7).

The planner turns many sub-queries into joins on its own: `IN (SELECT ...)` and `EXISTS (...)` usually become semi-joins, `NOT EXISTS` an anti-join (`Hash Anti Join`, `Nested Loop Anti Join`). Correlated scalar sub-queries in the `SELECT` list and `NOT IN` are not transformed.

### 6.8 Append, and the rest

**`Append`** concatenates the outputs of its children: `UNION ALL`, and, central to Module 14, the scan of a **partitioned** table, which is an `Append` of one scan per partition (with pruning of the partitions that can't match). **`Merge Append`** merges sorted children into one sorted stream; Module 11's exercise 3 used one for "newest five orders of two sellers".

Other nodes, briefly: `Limit` (stops pulling rows), `Unique` (removes adjacent duplicates from sorted input, used for `DISTINCT` and `UNION`), `WindowAgg` (window functions, Module 06; PostgreSQL 15+ shows a `Run Condition` when a filter like `rank <= 3` lets it stop early), `LockRows` (`FOR UPDATE`), `ModifyTable` (shown as `Insert on`, `Update on`, `Delete on`), `Result` (computes expressions without a table, or a one-time filter), `ProjectSet` (set-returning functions in the `SELECT` list).

---

## 7. The cost model

### 7.1 Cost units

The planner measures cost in abstract units anchored on one sequential page read. These settings define the prices:

| Setting | Default | Price of |
|---|---:|---|
| `seq_page_cost` | 1.0 | reading one page as part of a sequential scan |
| `random_page_cost` | 4.0 | reading one page at a random position (index scans) |
| `cpu_tuple_cost` | 0.01 | processing one row |
| `cpu_index_tuple_cost` | 0.005 | processing one index entry |
| `cpu_operator_cost` | 0.0025 | evaluating one operator or function call |
| `effective_cache_size` | 4GB | not a price: the planner's assumption about how much of the data the OS and PostgreSQL cache together |

The ratio between them is what matters, not the absolute values. Planning is entirely deterministic: same statistics, same settings, same plan.

### 7.2 A sequential scan by hand

`orders` has 7,095 pages and 500,000 rows (`pg_class.relpages` and `reltuples`). A sequential scan with one filter condition costs:

```text
pages × seq_page_cost        7,095 × 1.0    =  7,095
rows  × cpu_tuple_cost     500,000 × 0.01   =  5,000
rows  × cpu_operator_cost  500,000 × 0.0025 =  1,250   (one comparison per row)
                                            ---------
                                               13,345
```

which is exactly the `cost=0.00..13345.00` you saw on every `Seq Scan on orders` with one condition in Module 11. Without a condition, it's 12,095. Index scan costs are more involved (they model the descent, the fraction of leaf pages and heap pages visited, and how the `correlation` statistic makes heap access more or less random), but they are built from the same prices.

### 7.3 `random_page_cost`: when the defaults lie

The default `random_page_cost = 4` says a random page read is four times as expensive as a sequential one. That was true for spinning disks with data mostly on disk. On SSDs, and for databases that fit in RAM, random and sequential reads cost almost the same, and the default makes the planner shun index lookups it should use. A real example: "units sold per category for seller 1 in December 2025":

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT p.category_id, sum(oi.quantity) AS units
FROM orders AS o
JOIN order_items AS oi ON oi.order_id = o.id
JOIN products AS p ON p.id = oi.product_id
WHERE o.seller_id = 1
  AND o.created_at >= '2025-12-01'
GROUP BY p.category_id;
```

```text
 HashAggregate  (cost=37878.76..37879.26 rows=50 width=12) (actual time=305.109..305.119 rows=50 loops=1)
   Buffers: shared hit=50217 read=9357
   ->  Nested Loop  (cost=6900.85..37819.33 rows=11886 width=8) (actual time=236.729..302.281 rows=12436 loops=1)
         ->  Hash Join  (cost=6900.43..32211.43 rows=11886 width=12) (actual time=236.668..256.507 rows=12436 loops=1)
               Hash Cond: (oi.order_id = o.id)
               ->  Seq Scan on order_items oi  (cost=0.00..21985.28 rows=1266928 width=20) (actual time=0.024..112.802 rows=1266928 loops=1)
               ->  Hash  (cost=6841.80..6841.80 rows=4691 width=8) (actual time=3.584..3.586 rows=4388 loops=1)
                     ->  Bitmap Heap Scan on orders o  (cost=124.51..6841.80 rows=4691 width=8) (actual time=0.343..3.004 rows=4388 loops=1)
         ->  Index Scan using products_pkey on products p  (cost=0.42..0.47 rows=1 width=12) (actual time=0.003..0.003 rows=1 loops=12436)
 Execution Time: 305.293 ms
```

The estimates are excellent (4,691 versus 4,388 orders; 11,886 versus 12,436 lines). Yet the plan reads all 1.27 million order lines to find 12,436, because the planner priced 4,388 index lookups into `order_items_pkey` at `random_page_cost = 4` each, 7.97 per lookup, and the hash join came out cheaper (32,211 versus about 44,000). Tell it that random reads are cheap:

```sql
SET random_page_cost = 1.1;
```

```text
 HashAggregate  (cost=21196.44..21196.94 rows=50 width=12) (actual time=50.380..50.387 rows=50 loops=1)
   Buffers: shared hit=67870
   ->  Nested Loop  (cost=70.25..21137.01 rows=11886 width=8) (actual time=0.389..47.828 rows=12436 loops=1)
         ->  Nested Loop  (cost=69.83..15824.75 rows=11886 width=12) (actual time=0.367..14.879 rows=12436 loops=1)
               ->  Bitmap Heap Scan on orders o  (cost=69.41..3769.80 rows=4691 width=8) (actual time=0.344..2.495 rows=4388 loops=1)
               ->  Index Scan using order_items_pkey on order_items oi  (cost=0.43..2.54 rows=3 width=20) (actual time=0.002..0.002 rows=3 loops=4388)
                     Index Cond: (order_id = o.id)
         ->  Index Scan using products_pkey on products p  (cost=0.42..0.45 rows=1 width=12) (actual time=0.002..0.002 rows=1 loops=12436)
 Execution Time: 50.514 ms
```

Six times faster. Note the buffer count went *up* (67,870 hits versus 59,574 accesses): the nested loop touches more pages, but all of them were cached, while the hash join read 9,357 pages it didn't need.

> **In production:** On SSD or NVMe storage, or whenever the working set fits in memory, set `random_page_cost` between 1.1 and 2 (`ALTER SYSTEM SET random_page_cost = 1.1; SELECT pg_reload_conf();`), and set `effective_cache_size` to roughly the RAM available for caching (often 50–75% of the machine's memory). Change one setting at a time and compare the plans of your top queries before and after. The course container keeps the defaults so that the plans in every module match what you see; this module sets `random_page_cost` per session when it needs to.

`effective_cache_size` doesn't allocate anything. It tells the planner how likely a page fetched repeatedly by an index scan is to be cached already, which lowers the estimated cost of large index scans.

---

## 8. Statistics

### 8.1 Where the planner's knowledge comes from

Every row estimate starts from the statistics that **`ANALYZE`** collects, per column, and stores in `pg_statistic` (readable through the view `pg_stats`). `ANALYZE` reads a random sample of 300 × `default_statistics_target` rows, 30,000 with the default target of 100, whatever the table size, so it takes seconds even on huge tables. Autovacuum runs it automatically when about 10% of a table has changed. Two consequences:

- Because the sample is random, two `ANALYZE` runs give slightly different numbers, and so slightly different estimates. That's why your `rows=` may differ from this module's.
- Right after a bulk load (or a big `UPDATE`), statistics are stale until autovacuum catches up. **Run `ANALYZE` yourself after loading data.** `1101_seed_large.sql` ends with `VACUUM ANALYZE` for this reason. Temporary tables are never analysed by autovacuum at all.

Table-level numbers live in `pg_class`: `reltuples` (estimated rows) and `relpages` (pages), updated by `VACUUM` and `ANALYZE`. The planner scales them to the table's current physical size at planning time.

[Module 16, section 13](../16-internals/README.md#13-the-planners-statistics) takes `pg_statistic` apart. Here is what you need for reading plans.

### 8.2 Reading `pg_stats`

```sql
SELECT null_frac, n_distinct, most_common_vals, most_common_freqs, correlation
FROM pg_stats
WHERE tablename = 'orders' AND attname = 'status';
```

```text
 null_frac | n_distinct |                  most_common_vals                   |                   most_common_freqs                    | correlation
-----------+------------+-----------------------------------------------------+--------------------------------------------------------+-------------
         0 |          6 | {delivered,cancelled,refunded,shipped,paid,pending} | {0.9068,0.048866667,0.0293,0.0104,0.0027333333,0.0019} |  0.76893556
```

- **`null_frac`**: fraction of NULLs.
- **`n_distinct`**: number of distinct values. A **negative** value is a fraction of the row count (−1 means unique; `orders.user_id` has −0.10, i.e. about 50,000 distinct values for 500,000 rows), used when the count grows with the table.
- **`most_common_vals` (MCV)** and **`most_common_freqs`**: up to `statistics_target` frequent values with their frequency in the sample.
- **`histogram_bounds`**: for the values *not* in the MCV list, boundaries that divide them into 100 buckets of equal population. `orders.created_at` has a 101-entry histogram starting `{"2023-01-01 00:28:19+00","2023-02-04 09:13:32+00",...}`.
- **`correlation`**: how well the physical row order follows the column's order (Module 11 used it for BRIN).

### 8.3 From statistics to estimates

For **`status = 'pending'`**, the value is in the MCV list with frequency 0.0019: 0.0019 × 500,000 = **950** rows. (The truth is 1,099; the sample saw a few fewer pending orders.)

For **`seller_id = 1`**, the MCV list (100 entries, covering 80% of rows) says 0.1353 × 500,000 = 67,667 rows. For **`seller_id = 1500`**, which is not among the 100 most common sellers, the planner spreads the remaining 20% evenly over the remaining distinct values:

```text
(1 − sum of MCV frequencies) / (n_distinct − number of MCVs) × rows
= (1 − 0.8005) / (1344 − 100) × 500,000  ≈  80
```

```sql
EXPLAIN SELECT * FROM orders WHERE seller_id = 1500;
```

```text
 Bitmap Heap Scan on orders  (cost=5.04..300.56 rows=80 width=83)
```

The real count is 11. Bazaar's sellers are Zipf-skewed, so "the average non-common seller" is a poor model for both seller 300 (105 orders) and seller 1500 (11), and both get the estimate 80.

For a **range** like `created_at >= '2025-12-01'`, the planner finds where the constant falls in the histogram and counts the buckets above it (with linear interpolation inside a bucket): `rows=34665` against 34,488 actual. For conditions **combined with `AND`**, it multiplies the selectivities, assuming the columns are independent. Section 9.1 shows where that breaks.

### 8.4 More detail for one column

The statistics target can be raised per column, up to 10,000:

```sql
ALTER TABLE orders ALTER COLUMN seller_id SET STATISTICS 1000;
ANALYZE orders;
```

Now `ANALYZE` samples 300,000 rows and keeps up to 1,000 MCVs: `n_distinct` becomes 1,972 (the truth is 1,992), seller 1500 is estimated at **9** rows and seller 300 at 113. A bigger target costs a longer `ANALYZE`, more planning time for queries on the column, and more space in `pg_statistic`, so raise it only for columns whose estimates matter and are wrong: skewed foreign keys, status columns with rare values. Undo it with:

```sql
ALTER TABLE orders ALTER COLUMN seller_id SET STATISTICS -1;    -- back to default_statistics_target
ANALYZE orders;
```

---

## 9. When estimates go wrong

### 9.1 Correlated columns and extended statistics

Marketing wants Bazaar's customers in Berlin. At this module's starting state there are no extended statistics yet:

```sql
EXPLAIN ANALYZE
SELECT * FROM addresses WHERE country_code = 'DE' AND city = 'Berlin';
```

```text
 Seq Scan on addresses  (cost=0.00..3661.26 rows=179 width=81) (actual time=0.014..16.752 rows=1827 loops=1)
   Filter: ((country_code = 'DE'::bpchar) AND (city = 'Berlin'::text))
   Rows Removed by Filter: 123190
```

Estimated 179, actual 1,827: a **10× underestimate** on a simple two-condition filter. Each condition alone is estimated well. From `pg_stats`, 9.96% of addresses are in `DE` and 1.44% have `city = 'Berlin'`. The planner assumes the two conditions are **independent** and multiplies:

```text
125,017 × 0.0996 × 0.0144 ≈ 179
```

But they are not independent: every Berlin is in Germany, so `country_code = 'DE'` adds no information once you know the city. The right answer is just P(Berlin) × rows ≈ 1,800. The same mistake hits `GROUP BY country_code, city`, where the planner multiplies the distinct counts (21 countries × 91 cities) and estimates **1,911 groups**; there are **92**.

Real data is full of such dependencies: city and postal code, product category and seller, order status and order date (all open orders are recent), `country_code` and currency. PostgreSQL can't detect them on its own, but you can tell `ANALYZE` to measure specific column combinations with **extended statistics**:

```sql
CREATE STATISTICS addresses_country_city_stats (dependencies, ndistinct, mcv)
    ON country_code, city
    FROM addresses;

ANALYZE addresses;       -- extended statistics are only filled in by ANALYZE
```

This is the first half of the reference migration `project/db/1201_schema_stats.sql`. Now:

```sql
EXPLAIN SELECT * FROM addresses WHERE country_code = 'DE' AND city = 'Berlin';
EXPLAIN SELECT * FROM addresses WHERE country_code = 'CA' AND city = 'London';
EXPLAIN SELECT country_code, city, count(*) FROM addresses GROUP BY country_code, city;
```

```text
 Seq Scan on addresses  (cost=0.00..3661.26 rows=1888 width=81)
   Filter: ((country_code = 'DE'::bpchar) AND (city = 'Berlin'::text))

 Seq Scan on addresses  (cost=0.00..3661.26 rows=1054 width=81)
   Filter: ((country_code = 'CA'::bpchar) AND (city = 'London'::text))

 HashAggregate  (cost=3973.80..3974.72 rows=92 width=19)
   Group Key: country_code, city
```

Berlin: 1,888 (actual 1,827). London, Ontario: 1,054 (actual 1,025; it was estimated at 135 before). Groups: 92, exactly. The three kinds of statistics each contributed:

- **`dependencies`** measures functional dependencies between columns: "city determines country_code in 97.8% of rows" (not 100%, because London exists in both GB and CA). With it, the planner stops multiplying independent selectivities for equality conditions.
- **`ndistinct`** stores the number of distinct *combinations*, for `GROUP BY` and `DISTINCT` estimates.
- **`mcv`** stores the most common *combinations* with their frequencies, which handles cases like the two Londons exactly, where a dependency is only "mostly" true.

Inspect them in `pg_stats_ext`:

```sql
SELECT statistics_name, attnames, n_distinct, dependencies
FROM pg_stats_ext
WHERE statistics_name = 'addresses_country_city_stats';
```

```text
       statistics_name        |      attnames       |  n_distinct  |     dependencies
------------------------------+---------------------+--------------+----------------------
 addresses_country_city_stats | {city,country_code} | {"7, 9": 92} | {"7 => 9": 0.977700}
```

(Columns are identified by attribute number: 7 is `city`, 9 is `country_code`.) The `most_common_vals` column of the same view lists the combinations, including `{Berlin,DE}` with frequency 0.0151, `{London,GB}` with 0.0139 and `{London,CA}` with 0.0084.

**What does a better estimate change?** For a single-table filter, nothing: a sequential scan is a sequential scan. It matters as soon as the result feeds a join. The milestone's Berlin report (`project.md`, query S3) joins Berlin addresses to their orders and sellers. With the estimate of 179, the planner chooses nested loops; with 1,888, it switches to hash joins. Whether the new plan is faster is a separate question, and the milestone shows a case where it isn't, at first.

> **In production:** Extended statistics are cheap (a few kilobytes in `pg_statistic_ext_data`) and only consulted for queries that use those columns together. Create them for column pairs that appear together in `WHERE` or `GROUP BY` and that you know to be correlated, and check that the estimates actually improve. Bazaar's `NOTES.md` records a counter-example: statistics on `products (seller_id, category_id)` made a `GROUP BY` estimate *worse*, so they were left out.

### 9.2 Expressions hide the statistics

Statistics describe columns, not expressions. For a condition on an expression the planner knows nothing about, it falls back to hard-coded default selectivities: 0.5% for equality, 33% for a range, and for `GROUP BY expr`, a guess based on the row count. You saw both in passing:

- `WHERE created_at::date >= date '2025-12-25'` was estimated at **166,667** rows (a third of 500,000); there are 4,947.
- `GROUP BY product_id % 10` was estimated at **91,409** groups; there are 10.
- `GROUP BY date_trunc('month', created_at, 'UTC')` was estimated at 495,725 groups (with `Planned Partitions: 8`, the planner preparing to spill a hash aggregate); there are 36.
- `WHERE attributes ->> 'brand' = 'Voltix'` was estimated at 1,000 rows (0.5%); there are 4,388.

Three remedies, in order of preference:

1. **Rewrite the condition on the bare column**: `created_at >= '2025-12-25'` is estimated from the histogram at 5,227. This also lets indexes work (section 12.4).
2. **An expression index**: `ANALYZE` collects statistics on every indexed expression (Module 11, section 9).
3. **Extended statistics on an expression** (PostgreSQL 14+), when you want the statistics without the index:

   ```sql
   CREATE STATISTICS orders_month_stats ON (date_trunc('month', created_at, 'UTC')) FROM orders;
   CREATE STATISTICS products_brand_stats ON (attributes ->> 'brand') FROM products;
   ANALYZE orders;
   ANALYZE products;
   ```

   After which the monthly `GROUP BY` is estimated at 36 groups and the brand filter at 4,653 rows. Exercise 8 explores this.

```sql
DROP STATISTICS orders_month_stats, products_brand_stats;
```

### 9.3 Stale statistics

Statistics describe the table as it was at the last `ANALYZE`. After a bulk load, a mass delete, or a migration that rewrote a column, the planner works with a picture of a different table, and estimates can be off by orders of magnitude. A common trap is a job that loads a staging table and immediately queries it: autovacuum hasn't had time to analyse it, and the planner assumes a tiny table. The cure is simple: `ANALYZE table` as part of the job, right after the load. `pg_stat_user_tables.last_analyze` and `last_autoanalyze` show when statistics were last refreshed; `n_mod_since_analyze` shows how many rows changed since.

### 9.4 Skew and prepared statements

Applications rarely send literal SQL; drivers send **prepared statements** with parameters (`WHERE seller_id = $1`), and many reuse them. PostgreSQL then has a choice for each execution:

- a **custom plan**, made for the actual parameter values: always appropriate for those values, but it costs planning time on every execution;
- a **generic plan**, made once without knowing the values, using average statistics, and reused: no planning time, but one plan for every value.

With the default `plan_cache_mode = auto`, the first **five** executions of a prepared statement get custom plans. From the sixth on, PostgreSQL also builds the generic plan and compares its estimated cost with the average cost of the custom plans so far; if the generic plan isn't more expensive, it switches to it for good. On skewed data, that average can be misleading. A seller dashboard query, prepared and run first for Bazaar's five biggest sellers:

```sql
PREPARE seller_lines(bigint) AS
SELECT oi.product_id, sum(oi.quantity) AS units
FROM orders AS o
JOIN order_items AS oi ON oi.order_id = o.id
WHERE o.seller_id = $1
GROUP BY oi.product_id
ORDER BY units DESC
LIMIT 5;

EXECUTE seller_lines(1);  EXECUTE seller_lines(2);  EXECUTE seller_lines(3);
EXECUTE seller_lines(4);  EXECUTE seller_lines(5);

EXPLAIN (ANALYZE, COSTS OFF) EXECUTE seller_lines(6);

SELECT name, generic_plans, custom_plans FROM pg_prepared_statements;
```

```text
 Limit (actual time=142.003..142.007 rows=5 loops=1)
   ->  Sort (actual time=142.002..142.005 rows=5 loops=1)
         ...
                     ->  Nested Loop (actual time=2.091..127.371 rows=30535 loops=1)
                           ->  Bitmap Heap Scan on orders o (actual time=2.073..13.188 rows=11841 loops=1)
                                 Recheck Cond: (seller_id = $1)
                           ->  Index Scan using order_items_pkey on order_items oi (actual time=0.009..0.009 rows=3 loops=11841)
                                 Index Cond: (order_id = o.id)
```

```text
     name     | generic_plans | custom_plans
--------------+---------------+--------------
 seller_lines |             1 |            5
```

`seller_id = $1` in the plan (instead of a literal) shows that this is the **generic** plan. The five custom plans for huge sellers were expensive (hash joins over the whole `order_items` table), and the generic plan, costed for an *average* seller with 372 orders, looked cheaper, so PostgreSQL switched. From now on, every seller gets this nested-loop plan, including seller 1 with 69,805 orders. On this machine, with all data cached, the generic plan for seller 1 took 352 ms against 399 ms for its custom hash-join plan, so no harm done. On a server where the data doesn't fit in memory, 210,000 index lookups into `order_items` would be 210,000 potential random reads. More importantly, the planner is no longer making a decision for seller 1 at all.

Module 11 showed a case where the generic plan is clearly worse: a partial index (`WHERE status = 'active'`) can never be used by a generic plan with `status = $1`, so the plan falls back to a sequential scan (40 ms instead of 0.3 ms).

The controls:

- **`plan_cache_mode`**: `auto` (default), `force_custom_plan` (always re-plan: right for skewed data and cheap-to-plan queries), `force_generic_plan` (always reuse). Set it per session, per role, per database, or per function (`ALTER FUNCTION ... SET plan_cache_mode = force_custom_plan`).
- `pg_prepared_statements` shows, per prepared statement in the session, how many generic and custom plans were used.
- `EXPLAIN (GENERIC_PLAN)` shows the generic plan of a parameterised query without preparing it.

Drivers differ in when they prepare. psycopg 3 prepares a statement server-side after it has been executed 5 times on a connection (`prepare_threshold`); asyncpg prepares every statement; JDBC after 5 executions. PL/pgSQL functions prepare every query they run. So a function or a pooled connection can be the place where a generic plan appears. Modules 22 and 23 return to this from the Python side.

```sql
DEALLOCATE seller_lines;
```

---

## 10. pg_stat_statements: finding the queries that matter

### 10.1 Setup

You can only fix the queries you know about. `pg_stat_statements` records statistics for every statement the server executes: how often, how long, how many rows, how many buffers. It needs to be loaded at server start (the course's `docker-compose.yml` sets `shared_preload_libraries = 'pg_stat_statements,auto_explain'` and `pg_stat_statements.track = all`), and then installed once per database:

```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements WITH SCHEMA public;
```

That's the second half of `1201_schema_stats.sql`. Your database is now in the state `reset.sh 13` produces.

### 10.2 Normalisation

Statements are grouped by their **shape**: constants are replaced by placeholders, so `WHERE user_id = 4242` and `WHERE user_id = 17` count as one entry, `WHERE user_id = $1`. The grouping key is the `queryid`, a hash of the parsed query tree (`EXPLAIN (VERBOSE)` prints it as `Query Identifier`). Two consequences: the `query` column shows a representative text with `$1`, `$2`, ... (the first text seen); and queries that differ only in constants, such as an `IN` list with a varying number of elements, may still produce many entries, one per list length. With `track = all`, statements inside functions are recorded too, each as its own entry.

### 10.3 A workload

To make the view show something realistic, the module ships a small Bazaar workload: five `pgbench` scripts in [`examples/workload/`](examples/workload/) that simulate customers viewing their orders, order detail pages (written in the N+1 style, section 12.1), category pages with `OFFSET` pagination, seller dashboards for Zipf-distributed sellers, and an occasional back-office report. Reset the statistics and run it for a minute:

```sql
SELECT pg_stat_statements_reset();
```

```bash
./modules/12-query-performance/examples/workload/run_workload.sh 60
```

`pgbench` reports about 5,000 transactions per second on this machine. Now ask: **where does the time go?**

```sql
SELECT left(regexp_replace(query, '\s+', ' ', 'g'), 60) AS query,
       calls,
       round(total_exec_time::numeric, 0) AS total_ms,
       round(mean_exec_time::numeric, 3) AS mean_ms,
       rows,
       shared_blks_hit + shared_blks_read AS blocks
FROM pg_stat_statements
WHERE dbid = (SELECT oid FROM pg_database WHERE datname = 'bazaar')
ORDER BY total_exec_time DESC
LIMIT 8;
```

```text
                            query                             | calls  | total_ms | mean_ms |  rows   |  blocks
--------------------------------------------------------------+--------+----------+---------+---------+----------
 SELECT id, title, price FROM bazaar.products WHERE status =  |  59848 |    52679 |   0.880 | 1042224 | 28648496
 SELECT s.id, s.shop_name, (SELECT count(*) FROM bazaar.order |   3076 |    33909 |  11.024 |   30760 | 17327818
 SELECT id, user_id, status, total, created_at FROM bazaar.or |  26665 |    25250 |   0.947 |  368282 |  9993054
 SELECT id, status, total, created_at FROM bazaar.orders WHER | 119189 |     3971 |   0.033 |  577040 |   936100
 SELECT id, title, price FROM bazaar.products WHERE id = $1   | 269037 |     3111 |   0.012 |  269037 |  1076148
 SELECT id, amount, provider, status FROM bazaar.payments WHE |  89679 |     2265 |   0.025 |   96084 |   358695
 SELECT product_id, quantity, unit_price FROM bazaar.order_it |  89679 |     2089 |   0.023 |  226983 |   359701
 SELECT id, user_id, seller_id, status, total, created_at FRO |  89679 |     1816 |   0.020 |   89679 |   358726
```

How to read this:

- **Sort by `total_exec_time`** to find the load: the statements that, summed over all executions, use the most server time. Fixing the top entry helps the whole system. Here it's the **category page** at 0.88 ms per call: fast-looking, but it ran 60,000 times and touched 479 buffers per call to return 17 rows. Its full text (`SELECT query FROM pg_stat_statements WHERE ...`) reveals why: `LIMIT $3 OFFSET $4 * $5`, deep pages of `OFFSET` pagination (section 12.2).
- The **back-office report** is second with only 3,076 calls: 11 ms each, 5,600 blocks per call. Correlated sub-queries per seller (section 12.9).
- The **seller dashboard**, third, is also `OFFSET` pagination.
- **Sort by `mean_exec_time`** for the slowest individual statements, the ones users notice. `stddev_exec_time` and `max_exec_time` reveal statements whose speed varies wildly, often because of skewed parameters.
- **Sort by `calls`** to spot chatty patterns: the product lookup by id ran 269,037 times, three times per order detail page. Each call is cheap (0.012 ms), but each is also a network round trip for the application (section 12.1).
- **`rows / calls`** shows how many rows a statement returns on average; **blocks per row** shows how much work it does per row of result.
- **`temp_blks_written`** finds spilling sorts and hashes; **`wal_bytes`** finds write-heavy statements.

Other useful columns: `total_plan_time` and `mean_plan_time` (only with `pg_stat_statements.track_planning = on`, off by default because it has a measurable overhead), `shared_blk_read_time` (I/O wait, with `track_io_timing`), `jit_*` (JIT compilation time). `pg_stat_statements_info` shows when the statistics were last reset and how many entries were discarded (`dealloc`) because more than `pg_stat_statements.max` (default 5,000) distinct statements were seen.

> **In production:** Take snapshots of `pg_stat_statements` periodically (or let a monitoring tool do it) and compare them, rather than reading lifetime totals: what matters is "what changed since yesterday's deploy". Reset with `pg_stat_statements_reset()`, which can also reset a single `queryid`. Module 17, section 8.5, uses the view for capacity questions (I/O, temp files, WAL).

---

## 11. auto_explain: catching plans in the act

`pg_stat_statements` tells you *which* statement is slow; it doesn't keep plans. A query may be slow only for certain parameters, at certain times, or inside a function that `EXPLAIN` can't see into. **`auto_explain`** logs the plan of every statement that runs longer than a threshold, as it actually ran. It is preloaded in the course container, so you can enable it for your own session:

```sql
SET auto_explain.log_min_duration = '50ms';   -- log plans of statements slower than this
SET auto_explain.log_analyze = on;            -- include actual rows/times (costs overhead!)
SET auto_explain.log_buffers = on;
SET auto_explain.log_nested_statements = on;  -- also statements inside functions
```

A PL/pgSQL function hides its queries from `EXPLAIN`:

```sql
CREATE FUNCTION lab_seller_top_products(p_seller bigint)
RETURNS TABLE (product_id bigint, units bigint)
LANGUAGE plpgsql STABLE AS $$
BEGIN
    RETURN QUERY
    SELECT oi.product_id, sum(oi.quantity)::bigint
    FROM orders AS o JOIN order_items AS oi ON oi.order_id = o.id
    WHERE o.seller_id = p_seller
    GROUP BY oi.product_id
    ORDER BY 2 DESC
    LIMIT 5;
END;
$$;

EXPLAIN (ANALYZE, COSTS OFF) SELECT * FROM lab_seller_top_products(1);
```

```text
 Function Scan on lab_seller_top_products (actual time=285.728..285.730 rows=5 loops=1)
 Planning Time: 0.066 ms
 Execution Time: 285.820 ms
```

With the settings above, running `SELECT * FROM lab_seller_top_products(1);` writes the inner plan to the server log (`docker logs course-pg`):

```text
LOG:  duration: 400.673 ms  plan:
	Query Text: SELECT oi.product_id, sum(oi.quantity)::bigint
	    FROM orders AS o JOIN order_items AS oi ON oi.order_id = o.id
	    WHERE o.seller_id = p_seller
	    ...
	Limit  (cost=51427.82..51427.84 rows=5 width=16) (actual time=400.654..400.662 rows=5 loops=1)
	  ->  Sort  (cost=51427.82..51656.35 rows=91409 width=16) (actual time=400.652..400.658 rows=5 loops=1)
	        ->  HashAggregate  (cost=47250.54..49909.55 rows=91409 width=16) (actual time=394.668..398.277 rows=16647 loops=1)
	              ->  Hash Join  (cost=10492.85..35803.85 rows=178680 width=12) (actual time=40.731..338.064 rows=179157 loops=1)
	                    Hash Cond: (oi.order_id = o.id)
	                    ->  Seq Scan on order_items oi  (...) (actual time=0.021..106.167 rows=1266928 loops=1)
	                    ...
CONTEXT:  SQL statement "SELECT oi.product_id, ..."
	PL/pgSQL function lab_seller_top_products(bigint) line 3 at RETURN QUERY
LOG:  duration: 402.464 ms  plan:
	Query Text: SELECT * FROM lab_seller_top_products(1);
	Function Scan on lab_seller_top_products  (...)
```

```sql
DROP FUNCTION lab_seller_top_products(bigint);
```

`log_analyze = on` makes *every* statement pay the measuring overhead, not only the slow ones, because PostgreSQL can't know in advance which will be slow. In production, use it with `auto_explain.sample_rate` (log a fraction of sessions) and `log_timing = off` (keep row counts, drop per-node timing, which is the expensive part). Module 17, section 9.3, sets `auto_explain` up for a production server.

---

## 12. Anti-patterns and their fixes

Most slow queries in real applications come from a short list of patterns. Each subsection shows one on Bazaar's data, with measurements.

### 12.1 N+1 queries

The order detail page in the workload loads an order, then its lines, then **one query per line** for the product. That's the "N+1" pattern, the signature of ORM code that navigates relationships in a loop (`for item in order.items: item.product.title`). Each query is fast; the problem is the number of round trips. For Bazaar's top buyer, user 1, loading all 234 orders with their lines as 1 + 234 queries versus one join, timed from psql inside the container:

| Approach | Statements | Elapsed (local socket) | Elapsed (TCP to localhost) |
|---|---:|---:|---:|
| `SELECT id FROM orders WHERE user_id = 1`, then one `SELECT ... FROM order_items WHERE order_id = ...` per order | 235 | 21–26 ms | 26–30 ms |
| One join: `orders JOIN order_items ... WHERE o.user_id = 1` | 1 | 5–8 ms | 5–7 ms |

Four times slower even with no network to speak of. Between an application server and a database in different racks, a round trip costs 0.2–1 ms, so 235 statements add 50–250 ms of pure waiting. `pg_stat_statements` shows the pattern as one cheap statement with a huge `calls` count, like the 269,037 product lookups in section 10.3. Fixes: a join, `WHERE id = ANY($1)` with the list of ids (one round trip for all products), or the ORM's eager-loading options, which is Module 25's topic.

### 12.2 OFFSET pagination

"Page *n* of the list" is usually written as `ORDER BY ... LIMIT 20 OFFSET 20 * (n − 1)`. `OFFSET` doesn't skip rows cheaply: PostgreSQL must produce all the skipped rows and throw them away. Seller 1's order list, 20 per page, ordered by `created_at DESC, id DESC` (the `id` makes the order unique, so pages are stable):

```sql
SELECT id, user_id, status, total, created_at
FROM orders
WHERE seller_id = 1
ORDER BY created_at DESC, id DESC
LIMIT 20 OFFSET 60000;          -- page 3001
```

| Page | `OFFSET` | Time | Buffers | Plan |
|---:|---:|---:|---:|---|
| 1 | 0 | 0.18 ms | 18 | index scan + incremental sort, stops after 21 rows |
| 101 | 2,000 | 2.5 ms | 237 | the same, 2,021 rows read |
| 1,001 | 20,000 | 17.5 ms | 2,323 | 20,021 rows read |
| 3,001 | 60,000 | 88 ms | 7,364 + temp | bitmap scan of all 69,805 orders + external sort |

The cost grows linearly with the page number, and at some point the planner gives up on the index and sorts everything. (Real users rarely click to page 3,001, but crawlers, exports that page through everything, and "jump to last page" buttons do.) **Keyset pagination** (also called the seek method) remembers where the previous page ended and asks for the rows *after* it:

```sql
-- the last row of page 3000 was (created_at, id) = ('2023-10-07 08:23:06+00', 59156)
SELECT id, user_id, status, total, created_at
FROM orders
WHERE seller_id = 1
  AND (created_at, id) < ('2023-10-07 08:23:06+00', 59156)
ORDER BY created_at DESC, id DESC
LIMIT 20;
```

```text
 Limit (actual time=0.089..0.093 rows=20 loops=1)
   Buffers: shared hit=15
   ->  Incremental Sort (actual time=0.088..0.089 rows=20 loops=1)
         Sort Key: created_at DESC, id DESC
         Presorted Key: created_at
         ->  Index Scan using orders_seller_id_created_at_idx on orders (actual time=0.045..0.058 rows=21 loops=1)
               Index Cond: ((seller_id = 1) AND (created_at <= '2023-10-07 08:23:06+00'::timestamp with time zone))
               Filter: (ROW(created_at, id) < ROW('2023-10-07 08:23:06+00'::timestamp with time zone, 59156))
               Rows Removed by Filter: 1
 Execution Time: 0.168 ms
```

0.17 ms and 15 buffers, on page 3,001 as on page 1, returning the same 20 rows as the `OFFSET` version. The row comparison `(created_at, id) < (x, y)` means "`created_at < x`, or `created_at = x` and `id < y`": exactly "after this row in `ORDER BY created_at DESC, id DESC` order". PostgreSQL derived `created_at <= ...` from it as an index condition. An index on `(seller_id, created_at DESC, id DESC)` would make the whole row comparison an index condition and drop the incremental sort; with only a few orders per second for any seller, the existing index is enough.

The trade-offs: keyset pagination can go to the *next* or *previous* page, not jump to page 1,734; the client must send back the last row's key (an opaque cursor in an API); and the `ORDER BY` must be unique (add the primary key as a tie-breaker), otherwise rows with equal keys at a page boundary are skipped or repeated. Infinite scrolling and API cursors are natural fits. If users really need page numbers, cap them (few people look beyond page 50) and use keyset for exports.

### 12.3 `SELECT *`

`SELECT *` fetches every column, whether the code uses it or not. Three costs:

- **Transfer and conversion.** `EXPLAIN (ANALYZE, SERIALIZE)` measures them. The 4,244 laptops of category 3 with `SELECT *` produce 1,865 kB of output and take 8.6 ms to serialise; `SELECT id, title, price` produces 223 kB in 0.8 ms. Large `description` and `attributes` values must also be decompressed and fetched from TOAST (Module 16) only to be thrown away.
- **No index-only scans.** A covering index (Module 11) can't cover "all columns".
- **Fragile code.** Adding a column to the table changes every query's result.

Name the columns you need.

### 12.4 Functions on indexed columns

The finance team's daily revenue report:

```sql
SELECT created_at::date AS day, count(*) AS orders, sum(total) AS revenue
FROM orders
WHERE created_at::date >= date '2025-12-25'
GROUP BY 1
ORDER BY 1;
```

```text
 GroupAggregate  (cost=32316.20..36478.81 rows=166396 width=44) (actual time=79.919..80.730 rows=7 loops=1)
   ->  Sort  (cost=32316.20..32732.87 rows=166667 width=11) (actual time=79.788..80.064 rows=4947 loops=1)
         ->  Seq Scan on orders  (cost=0.00..15011.67 rows=166667 width=11) (actual time=50.259..78.689 rows=4947 loops=1)
               Filter: ((created_at)::date >= '2025-12-25'::date)
               Rows Removed by Filter: 495053
```

Two problems from one cast: no index on `created_at` could serve `created_at::date` (Module 11, section 9), and the estimate is the 33% default (166,667 rows instead of 4,947). Rewriting the condition as a range on the bare column fixes the estimate, `rows=5227`, and takes 51 ms; adding a 24 kB BRIN index on `orders (created_at)` (Module 11, section 14) makes it a 9.5 ms bitmap scan over 572 buffers. The milestone does both. Note also that `created_at::date` depends on the session's `TimeZone`, so the "same" report gives different numbers in Berlin and New York. `date_trunc('day', created_at, 'UTC')` and explicit `+00` offsets make it deterministic.

### 12.5 Implicit casts

Covered in Module 11, section 20: comparing a `bigint` column with a `numeric` value (`WHERE order_id = 250000.0`, or a parameter bound as a decimal) casts the *column* and disables its index. In plans, look for `(order_id)::numeric` in a `Filter`. Fix the parameter's type in the application, not the column.

### 12.6 `OR` across tables

Customer service searches "by order number or by customer e-mail":

```sql
SELECT o.id, o.status, o.total, u.email
FROM orders AS o
JOIN users AS u ON u.id = o.user_id
WHERE o.id = 250000 OR lower(u.email) = 'mia.hill4242@example.org';
```

```text
 Hash Join (actual time=66.794..600.349 rows=16 loops=1)
   Hash Cond: (o.user_id = u.id)
   Join Filter: ((o.id = 250000) OR (lower((u.email)::text) = 'mia.hill4242@example.org'::text))
   Rows Removed by Join Filter: 499984
   ->  Seq Scan on orders o (actual time=0.036..96.684 rows=500000 loops=1)
   ->  Hash (actual time=56.424..56.426 rows=100000 loops=1)
         ->  Seq Scan on users u (actual time=0.022..22.091 rows=100000 loops=1)
 Execution Time: 600.491 ms
```

Each branch of the `OR` refers to a different table, so neither can be applied before the join: both tables are read completely and joined, and the condition is checked on 500,000 joined rows. Split it into one query per branch:

```sql
SELECT o.id, o.status, o.total, u.email
FROM orders AS o JOIN users AS u ON u.id = o.user_id
WHERE o.id = 250000
UNION
SELECT o.id, o.status, o.total, u.email
FROM orders AS o JOIN users AS u ON u.id = o.user_id
WHERE lower(u.email) = 'mia.hill4242@example.org';
```

```text
 Unique (actual time=0.527..0.536 rows=16 loops=1)
   ->  Sort (actual time=0.526..0.529 rows=16 loops=1)
         ->  Append (actual time=0.245..0.501 rows=16 loops=1)
               ->  Nested Loop (actual time=0.244..0.246 rows=1 loops=1)
                     ->  Index Scan using orders_pkey on orders o (actual time=0.021..0.022 rows=1 loops=1)
                     ->  Index Scan using users_pkey on users u (actual time=0.220..0.220 rows=1 loops=1)
               ->  Nested Loop (actual time=0.226..0.253 rows=15 loops=1)
                     ->  Index Scan using users_email_lower_key on users u_1 (actual time=0.210..0.211 rows=1 loops=1)
                     ->  Index Scan using orders_user_id_created_at_idx on orders o_1 (actual time=0.014..0.039 rows=15 loops=1)
 Execution Time: 0.590 ms
```

600 ms became 0.6 ms. `UNION` removes the duplicates an order would produce if it matched both branches; use `UNION ALL` when the branches can't overlap. In application code, the cleaner fix is often two separate searches: the UI knows whether the user typed a number or an e-mail address.

### 12.7 `NOT IN` versus `NOT EXISTS`

"How many registered users have never ordered?" written with `NOT IN`:

```sql
SELECT count(*) FROM users WHERE id NOT IN (SELECT user_id FROM orders);
```

Two independent problems.

**Semantics.** `x NOT IN (list)` means `x <> a AND x <> b AND ...`. If the list contains a single NULL, `x <> NULL` is unknown, so the whole condition can never be true (Module 03's three-valued logic):

```sql
SELECT 3 NOT IN (1, 2, NULL) AS not_in;      -- NULL, not true
```

With a nullable sub-query column, `NOT IN` silently returns **no rows**. Exercise 7 finds a real case in Bazaar.

**Performance.** Because of those semantics, PostgreSQL can't turn `NOT IN` into an anti-join. It runs it as a sub-plan, which is fast only if the sub-query's result fits in a hash table in `work_mem` (a `hashed SubPlan`). 500,000 user ids don't fit in 4 MB, so:

```sql
EXPLAIN SELECT count(*) FROM users WHERE id NOT IN (SELECT user_id FROM orders);
```

```text
 Aggregate  (cost=889952535.00..889952535.01 rows=1 width=8)
   ->  Seq Scan on users u  (cost=0.00..889952410.00 rows=50000 width=0)
         Filter: (NOT (ANY (id = (SubPlan 1).col1)))
         SubPlan 1
           ->  Materialize  (cost=0.00..16549.00 rows=500000 width=8)
                 ->  Seq Scan on orders o  (cost=0.00..12095.00 rows=500000 width=8)
```

An estimated cost of **890 million**: for each of the 100,000 users, scan the materialised list of 500,000 order user ids until a match is found, all the way to the end for the 15,942 users who never ordered. We cancelled it after two minutes. (Limited to the first 200 users, it takes 200 ms.) With `SET work_mem = '64MB'` the sub-plan becomes `hashed SubPlan 1` and the query takes 243 ms, but the NULL problem remains, and next year's data won't fit either. `NOT EXISTS` has neither problem:

```sql
SELECT count(*)
FROM users AS u
WHERE NOT EXISTS (SELECT 1 FROM orders AS o WHERE o.user_id = u.id);
```

```text
 Aggregate (actual time=309.573..309.578 rows=1 loops=1)
   ->  Hash Right Anti Join (actual time=304.978..308.648 rows=15942 loops=1)
         Hash Cond: (o.user_id = u.id)
         ->  Seq Scan on orders o (actual time=0.015..94.054 rows=500000 loops=1)
         ->  Hash (actual time=43.915..43.916 rows=100000 loops=1)
               ->  Seq Scan on users u (actual time=0.017..18.645 rows=100000 loops=1)
 Execution Time: 309.937 ms
```

15,942 users, in 0.3 s. An **anti-join** returns outer rows without a match; it can use any join algorithm and spills to disk gracefully. Rule: **write `NOT EXISTS`, never `NOT IN (subquery)`**. (`NOT IN` with a short literal list, `status NOT IN ('cancelled', 'refunded')`, is fine.) A `LEFT JOIN ... WHERE o.user_id IS NULL` is equivalent to `NOT EXISTS` and gets the same anti-join plan.

### 12.8 `count(*)` on big tables

An exact `count(*)` must visit every row (or every index entry, with an index-only scan) because of MVCC: each transaction may see a different number of rows, so there is no stored counter. On `order_items`, that's 179 ms without parallelism, 95 ms with it, and it grows with the table. For "about 1.2 million order lines" on an admin dashboard, use the planner's estimate:

```sql
SELECT reltuples::bigint FROM pg_class WHERE relname = 'order_items';   -- 1266928, instantly
```

For an estimate of a *filtered* count, ask the planner: `EXPLAIN SELECT * FROM orders WHERE status = 'delivered'` says `rows=454383` (exact: 452,767). Exercise 12 wraps this in a function. Other techniques: count only up to what you need (`SELECT count(*) FROM (SELECT 1 FROM ... LIMIT 1001) AS s`, then display "1,000+"); maintain counters in a summary table updated by triggers or a job; or cache the count. Pagination UIs that show "page 3 of 21,844" are the usual culprit; ask whether anyone needs the 21,844.

### 12.9 Correlated sub-queries

The fraud team wants orders of the last week of 2025 worth more than three times their seller's average:

```sql
SELECT o.id, o.seller_id, o.total
FROM orders AS o
WHERE o.created_at >= '2025-12-24'
  AND o.total > 3 * (SELECT avg(o2.total)
                     FROM orders AS o2
                     WHERE o2.seller_id = o.seller_id)
ORDER BY o.total DESC;
```

```text
 Sort  (cost=7798041.93..7798047.23 rows=2119 width=23) (actual time=56118.228..56118.281 rows=418 loops=1)
   Sort Key: o.total DESC
   Buffers: shared hit=20966515 read=2893
   ->  Index Scan using orders_user_id_created_at_idx on orders o  (cost=0.42..7797924.86 rows=2119 width=23) (actual time=217.839..56116.820 rows=418 loops=1)
         Index Cond: (created_at >= '2025-12-24 00:00:00+00'::timestamp with time zone)
         Filter: (total > ('3'::numeric * (SubPlan 1)))
         Rows Removed by Filter: 5163
         SubPlan 1
           ->  Aggregate  (cost=1222.56..1222.57 rows=1 width=32) (actual time=10.015..10.015 rows=1 loops=5581)
                 ->  Bitmap Heap Scan on orders o2  (cost=11.31..1221.63 rows=372 width=7) (actual time=1.548..8.062 rows=19142 loops=5581)
                       Recheck Cond: (seller_id = o.seller_id)
                       Heap Blocks: exact=20536266
 ...
 JIT:
   Timing: Generation 0.640 ms (Deform 0.249 ms), Inlining 87.461 ms, Optimization 38.992 ms, Emission 27.022 ms, Total 154.115 ms
 Execution Time: 56138.157 ms
```

**56 seconds** for 418 rows. The sub-plan runs once per candidate order (`loops=5581`), and each run averages all of that seller's orders: 19,142 rows on average, because the big sellers place most of the late-December orders. 5,581 × 19,142 = 107 million rows read, 21 million buffer accesses. The averages are recomputed for the same few sellers thousands of times. (Note also the curious outer scan: the planner walks the whole `orders_user_id_created_at_idx` index to apply `created_at >= ...`, because its cost model thinks that's cheaper than a sequential scan when every row will be followed by a sub-plan.)

Compute each seller's average **once**, then join:

```sql
SELECT o.id, o.seller_id, o.total
FROM orders AS o
JOIN (
    SELECT seller_id, avg(total) AS avg_total
    FROM orders
    GROUP BY seller_id
) AS sa ON sa.seller_id = o.seller_id
WHERE o.created_at >= '2025-12-24'
  AND o.total > 3 * sa.avg_total
ORDER BY o.total DESC;
```

```text
 Sort  (cost=28107.38..28112.68 rows=2119 width=23) (actual time=283.015..283.073 rows=418 loops=1)
   Buffers: shared hit=14193
   ->  Hash Join  (cost=14628.60..27990.32 rows=2119 width=23) (actual time=265.995..282.862 rows=418 loops=1)
         Hash Cond: (o.seller_id = orders.seller_id)
         Join Filter: (o.total > ('3'::numeric * (avg(orders.total))))
         ->  Seq Scan on orders o  (actual time=31.675..46.824 rows=5581 loops=1)
         ->  Hash  (actual time=233.922..233.924 rows=1992 loops=1)
               ->  HashAggregate  (actual time=232.890..233.655 rows=1992 loops=1)
                     Group Key: orders.seller_id
                     ->  Seq Scan on orders  (actual time=0.010..85.816 rows=500000 loops=1)
 Execution Time: 283.341 ms
```

Same 418 rows, **200 times faster**: one pass to compute 1,992 averages, one pass to filter. A window function (`avg(total) OVER (PARTITION BY seller_id)`) would work too, but it must compute the average for every order, then filter. Correlated sub-queries aren't always bad: when each execution is a cheap index lookup, like the "latest order per seller" sub-plan in section 6.7, a few thousand executions cost little. The warning sign is a `SubPlan` with a high `loops` count *and* a lot of work per loop.

### 12.10 CTE fences

Section 6.6 showed it: `WITH ... AS MATERIALIZED` (and every CTE before PostgreSQL 12) is computed in full before the outer query's conditions are applied. If an old code base is full of CTEs written as fences "for readability", check whether they're still materialised (they are if referenced more than once), and remove `MATERIALIZED` where the conditions should be pushed down.

---

## 13. A method for performance investigations

When something is slow, resist the urge to add an index or change a setting immediately. Work through the steps:

1. **Find the right query.** Start from `pg_stat_statements` (total time for load, mean time for latency), slow-query logs (`log_min_duration_statement`) or `auto_explain`, not from guesses. Get the full text and real parameter values.
2. **Reproduce it.** Run it with `EXPLAIN (ANALYZE, BUFFERS)` (in a transaction with `ROLLBACK` for writes) on production-like data. Run it twice to see cold and warm cache behaviour. Note the settings (`EXPLAIN (... SETTINGS)`).
3. **Find where the time goes.** Which nodes have the largest exclusive time? Multiply by `loops`. Where are the buffers read? Is there `temp` I/O?
4. **Compare estimated and actual rows, bottom-up.** Find the lowest node where they diverge by 10× or more. That's usually the root cause; nodes above it only inherit the error.
5. **Classify the problem:**
   - *Estimate wrong* → stale statistics (`ANALYZE`), correlated columns (`CREATE STATISTICS`), expressions (rewrite, expression index or expression statistics), skew (statistics target, `plan_cache_mode`).
   - *Estimate right, plan still bad* → cost model (`random_page_cost`, `effective_cache_size`), memory (`work_mem` for this query), or no better plan exists.
   - *No good plan possible* → missing index (Module 11), or a query shape that rules it out: functions on columns, casts, `OR` across tables, `NOT IN`, `OFFSET`, correlated sub-queries, N+1.
   - *Waiting, not working* → if `EXPLAIN ANALYZE` is fast but the query is slow in production, look at locks and waits (`pg_stat_activity`, Module 10 and 17), the network, or the client.
6. **Fix one thing, measure again.** Compare buffers and time with the baseline. Verify that results are unchanged.
7. **Check for side effects.** A new index slows writes; a settings change affects every query; a rewrite must stay correct with NULLs and duplicates. Re-check the top of `pg_stat_statements` after deploying.
8. **Write it down.** The before-and-after plans are the documentation of why the code looks the way it does.

Two things *not* to do: don't leave `enable_*` settings off in production (they are diagnostic tools), and don't tune the whole server for one query when `SET LOCAL` in that query's transaction will do.

---

## 14. pg_hint_plan, the last resort

Some databases let you embed hints in queries to dictate the plan. PostgreSQL deliberately doesn't: its developers prefer to fix the planner and the statistics, because hints freeze one plan into the code, and the plan that's right for today's data is often wrong for next year's. The third-party extension **`pg_hint_plan`** adds hints as special comments:

```sql
/*+ IndexScan(o orders_seller_id_created_at_idx) NestLoop(o oi) */
SELECT ...
```

It's not in the core distribution or the course image. Consider it only after you've exhausted statistics, rewrites, indexes and per-query settings, for a critical query whose plan flips unpredictably, and document every hint as technical debt. Several managed services offer it as an installable extension. It can also read hints from a table (`hint_plan.hints`) keyed by the normalised query text, which attaches a hint to a query you can't edit, such as one generated by an ORM.

---

## Summary

- The planner chooses the cheapest plan **by estimate**. Bad plans come from wrong row estimates, a mis-calibrated cost model, or queries that rule out the good plan.
- Use `EXPLAIN (ANALYZE, BUFFERS)`, inside `BEGIN ... ROLLBACK` for writes. Add `SETTINGS` when sharing plans, `WAL` for writes, and `SERIALIZE` (PG 17) to include result transfer.
- Read plans as trees: estimated versus actual rows bottom-up; times and rows are **per loop**, so multiply by `loops`; buffers are totals.
- Nested loops win for small outer inputs with indexed lookups, hash joins for large unsorted inputs, merge joins for sorted inputs. Hashes and sorts spill to disk past `work_mem` (× `hash_mem_multiplier` for hashes): watch `Batches`, `Disk:` and `temp` buffers.
- `HashAggregate` needs memory per group; `GroupAggregate` needs sorted input. `Memoize` caches repeated inner lookups; `Gather` runs parallel workers; CTEs are inlined unless `MATERIALIZED` or used twice; `SubPlan` runs per outer row.
- Costs are built from `seq_page_cost`, `random_page_cost` and the `cpu_*` costs. On SSDs or cached data, lower `random_page_cost` (1.1–2): it turned a 305 ms hash join into a 50 ms nested loop.
- Estimates come from `ANALYZE` samples in `pg_stats`: MCVs, histograms, `n_distinct`. Conditions are assumed independent; **extended statistics** fix correlated columns (Berlin: 179 → 1,888 for 1,827 actual), and expression statistics fix expressions.
- Prepared statements switch to a **generic plan** after five executions if it looks no more expensive; with skewed data, use `plan_cache_mode = force_custom_plan`.
- `pg_stat_statements` finds the queries that matter (total time for load, mean for latency, calls for chattiness); `auto_explain` logs the plans of slow statements, including inside functions.
- Anti-patterns: N+1 (join or `= ANY`), `OFFSET` (keyset pagination), `SELECT *`, functions and casts on indexed columns, `OR` across tables (`UNION`), `NOT IN` (`NOT EXISTS`), exact counts (estimates), heavy correlated sub-queries (pre-aggregate and join).
- Investigate methodically: find, reproduce, locate, compare estimates, classify, fix one thing, measure, document.

## What's next

So far, Bazaar finds rows by exact values and ranges. Shoppers type words, with typos. [Module 13](../13-full-text-search/) adds full-text search with `tsvector` and GIN, fuzzy matching and autocomplete with trigrams, and applies this module's plan-reading skills to search queries.
