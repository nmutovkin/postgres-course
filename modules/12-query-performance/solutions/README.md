# Module 12: Solutions

All solutions were run on PostgreSQL 17.11 at state 13 (`reset.sh 13`), with `SET max_parallel_workers_per_gather = 0;` and otherwise default settings, on a shared 4-vCPU machine. Timings vary between runs by up to 2×; row counts, plan shapes and buffer counts are what to compare. Estimates depend on `ANALYZE`'s random sample.

Files in this directory:

| File | Purpose |
|---|---|
| [`plan_check.sql`](plan_check.sql) | `pg_temp.plan_check(label, query)`: one-line plan summaries (used by the milestone) |
| [`slow_queries.sql`](slow_queries.sql) | the milestone's six slow queries, as reported |
| [`fixed_queries.sql`](fixed_queries.sql) | the reference fixes |
| [`measure_slow.sql`](measure_slow.sql), [`measure_fixed.sql`](measure_fixed.sql) | before-and-after measurements for the milestone |
| [`count_estimate.sql`](count_estimate.sql) | exercise 12 |

The milestone's reference write-up is at the end of this file.

---

## Exercise 1: Read a plan

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
 Planning Time: 0.545 ms
 Execution Time: 0.549 ms
```

1. **Order.** The top `Nested Loop` asks its outer child (the inner `Nested Loop`) for a row, which asks *its* outer child: the index scan on `orders` starts first. For the first order, the `order_items` scan runs; for the first line, the `products` scan runs. From then on the three interleave: each order triggers one `order_items` scan, each line one `products` lookup. Rows flow up one at a time.
2. **`products_pkey`**: `loops=35`, so 35 searches. Buffers are totals over all loops: **140**, four per lookup (three index levels plus the heap page).
3. **Order lines**: `rows=2` is the per-loop average, rounded; × 15 loops ≈ 35, and the parent `Nested Loop` confirms `rows=35`. Some orders have one line, some four.
4. **Exclusive time of the top node**: 0.480 ms total, minus its outer child (0.340 ms) and its inner child (35 × 0.003 = 0.105 ms) ≈ **0.035 ms**. Almost all the time is in the scans.
5. **No `Sort`**: the outer scan uses `orders_user_id_created_at_idx (user_id, created_at DESC)`, which returns user 4242's orders newest first. A nested loop emits its output in the order of its outer input (each outer row is followed by its matches), so the final result is already in `created_at DESC` order. The planner knows this ("pathkeys") and drops the sort. A hash join would not preserve the order.

---

## Exercise 2: Cost by hand

```sql
SELECT relpages, reltuples FROM pg_class WHERE relname = 'payments';
```

```text
 relpages | reltuples
----------+-----------
     6101 |    536816
```

With one condition (`amount > 1000`, one operator per row):

```text
6,101 × 1.0  +  536,816 × 0.01  +  536,816 × 0.0025
= 6,101      +  5,368.16        +  1,342.04          = 12,811.20
```

Without a condition: 6,101 + 5,368.16 = **11,469.16**.

```text
 Seq Scan on payments  (cost=0.00..12811.20 rows=95109 width=58)
   Filter: (amount > '1000'::numeric)

 Seq Scan on payments  (cost=0.00..11469.16 rows=536816 width=58)
```

**Reasoning.** A sequential scan reads every page once (`seq_page_cost` each), processes every row (`cpu_tuple_cost`), and evaluates its filter on every row (`cpu_operator_cost` per operator). The startup cost is 0 because the first row can be returned immediately. Notice what is *not* in the cost: the selectivity of the condition. A sequential scan costs the same whether it returns one row or all of them; only the operators above it care. (The planner does scale `reltuples` by the table's current size in pages, which here is unchanged since the last `VACUUM`.)

---

## Exercise 3: Statistics to estimates

```sql
SELECT most_common_freqs[array_position(most_common_vals::text::text[], 'paid')] AS paid_freq
FROM pg_stats WHERE tablename = 'orders' AND attname = 'status';
-- 0.0027666667

SELECT n_distinct,
       array_length(most_common_freqs, 1) AS n_mcv,
       (SELECT sum(f) FROM unnest(most_common_freqs) AS f) AS mcv_sum
FROM pg_stats WHERE tablename = 'orders' AND attname = 'seller_id';
-- n_distinct 1349 | n_mcv 100 | mcv_sum 0.80076677
```

(`most_common_vals` has the pseudo-type `anyarray`; casting through `text` turns it into a normal array.)

1. `status = 'paid'` is an MCV: 0.0027666667 × 500,000 = **1,383**. `EXPLAIN` says `rows=1383`; the true count is 1,397.
2. Seller 2000 is not an MCV: (1 − 0.80076677) / (1349 − 100) × 500,000 = 0.19923 / 1,249 × 500,000 = **80**. `EXPLAIN` says `rows=80`; the true count is 3.

The second estimate is 27 times too high. MCVs give an accurate frequency for each frequent value; everything else gets the *average* of the non-MCV values. Bazaar's sellers are Zipf-distributed: the 1,900 sellers outside the MCV list range from about 120 orders down to 3, and the average fits none of them well. The `n_distinct` of 1,349 (the truth is 1,992) also comes from a 30,000-row sample, which can't see the many tiny sellers. Raising the column's statistics target to 1,000 (section 8.4) brings seller 1500's estimate from 80 to 9 (true: 11). For the planner the error rarely matters here: 80 or 3 rows, the best plan is the same index scan.

---

## Exercise 4: Hash batches

```text
work_mem = 4MB (default):   Buckets: 262144  Batches: 2  Memory Usage: 7636kB   (+ temp buffers)
work_mem = 6MB:             Buckets: 262144  Batches: 2  Memory Usage: 7636kB
work_mem = 7MB:             Buckets: 262144  Batches: 1  Memory Usage: 13209kB
```

**Answer: 7 MB.** The hash table for 238,093 orders needs about 13.2 MB (`Memory Usage: 13209kB` once it fits). A hash node may use `work_mem × hash_mem_multiplier`, and `hash_mem_multiplier` defaults to 2.0 (since PostgreSQL 15), so the limit is 2 × `work_mem`: 6 MB gives 12 MB (too small), 7 MB gives 14 MB (enough). With `SET hash_mem_multiplier = 1`, `work_mem` must be 14 MB: at 13 MB the plan still shows `Batches: 2`, at 14 MB `Batches: 1`.

**Reasoning.** With two batches, the reported `Memory Usage` (7.6 MB) is the memory of *one* batch: PostgreSQL partitioned both inputs by hash value into two halves, joined the first half in memory, and wrote the second halves of both inputs to temporary files (the `temp read/written` buffers), joining them afterwards. The number of batches is decided at planning time from the *estimated* build size, so a badly underestimated build side can produce batches far bigger than memory. `hash_mem_multiplier` exists because hash-based nodes suffer more from spilling than sorts do, so they're allowed more memory than `work_mem` alone. For this query the spill cost little (about 10% of the run time, the files stayed in the OS cache); raising `work_mem` server-wide for it would be overkill.

---

## Exercise 5: Three ways to join

| Plan | Settings | Est. cost | Time | Buffers | `order_items` rows read |
|---|---|---:|---:|---:|---:|
| Nested loop (planner's choice) | defaults | 1,606 | 2.5 ms | ≈ 500 | 278 (100 index lookups) |
| Hash join | `enable_nestloop = off` | 25,694 | 242–267 ms | 9,411 | 1,266,928 |
| Merge join | `+ enable_hashjoin = off` | 63,616 | 326–362 ms | 14,257 | 1,265,851 |

All three return 278 rows.

```text
 Hash Join  (cost=383.08..25694.08 rows=261 width=35) (actual time=223.890..241.819 rows=278 loops=1)
   Hash Cond: (oi.order_id = o.id)
   Buffers: shared hit=6651 read=2760
   ->  Seq Scan on order_items oi  (cost=0.00..21985.28 rows=1266928 width=27) (actual time=0.031..115.380 rows=1266928 loops=1)
   ->  Hash  (cost=381.79..381.79 rows=103 width=16) (actual time=0.941..0.943 rows=100 loops=1)
         Buckets: 1024  Batches: 1  Memory Usage: 13kB
         ->  Bitmap Heap Scan on orders o  (...) (actual time=0.096..0.880 rows=100 loops=1)

 Merge Join  (cost=385.66..63615.82 rows=261 width=35) (actual time=307.828..326.041 rows=278 loops=1)
   Merge Cond: (oi.order_id = o.id)
   Buffers: shared hit=9073 read=5184
   ->  Index Scan using order_items_pkey on order_items oi  (...) (actual time=0.016..221.277 rows=1265851 loops=1)
   ->  Sort  (cost=385.24..385.49 rows=103 width=16) (actual time=0.181..0.195 rows=100 loops=1)
         Sort Key: o.id
         ->  Bitmap Heap Scan on orders o  (...) (actual time=0.034..0.165 rows=100 loops=1)
```

**Reasoning.** The outer input is tiny: 100 orders. A nested loop does 100 index lookups into `order_items_pkey`, about 5 buffers each. Neither alternative can exploit that:

- The **hash join** builds a hash of the 100 orders (13 kB, instant), but then must *probe* it with every row of `order_items`: a sequential scan of 1.27 million rows to find 278. It's the right algorithm when both sides are large.
- The **merge join** needs both inputs sorted by `order_id`. It sorts the 100 orders (cheap) and reads `order_items` in `order_id` order through its primary key index, and it can only stop when the outer side is exhausted. Seller 42's last December order is near the end of the id range, so it read 1,265,851 index entries and their heap rows: the most expensive plan, as its cost predicted.

A merge join *can* stop early, and a nested loop is not always best: if the condition were `created_at >= '2023-01-01'` (all 1,402 orders of seller 42), the nested loop would still win, but for 70,000 orders of seller 1 the hash join would. The crossover is what the cost model computes.

---

## Exercise 6: Keyset pagination for category pages

**1. `OFFSET`:**

```text
 Limit (actual time=10.448..10.569 rows=24 loops=1)
   Buffers: shared hit=71 read=1144
   ->  Incremental Sort (actual time=0.368..10.494 rows=1200 loops=1)
         Sort Key: price, id
         Presorted Key: price
         ->  Index Scan using products_active_category_id_price_idx on products (actual time=0.041..9.923 rows=1204 loops=1)
               Index Cond: (category_id = 6)
```

1,200 rows produced to show 24, 1,215 buffers (one heap page per product).

**2. Keyset**, starting after the last row of page 49:

```sql
SELECT id, title, price
FROM products
WHERE status = 'active'
  AND category_id = 6
  AND (price, id) > (48.99, 139596)
ORDER BY price, id
LIMIT 24;
```

```text
 Limit (actual time=0.245..0.250 rows=24 loops=1)
   Buffers: shared hit=62
   ->  Incremental Sort (actual time=0.243..0.245 rows=24 loops=1)
         Sort Key: price, id
         Presorted Key: price
         ->  Index Scan using products_active_category_id_price_idx on products (actual time=0.097..0.198 rows=28 loops=1)
               Index Cond: ((category_id = 6) AND (price >= 48.99))
               Filter: (ROW(price, id) > ROW(48.99, 139596))
               Rows Removed by Filter: 22
```

```text
   id   |            title             | price
--------+------------------------------+-------
 147022 | Pixelon Max Headset 302      | 48.99
 152947 | Arcadia Wireless Headset 641 | 48.99
 160517 | Nordvik Pro Headphones 795   | 48.99
 ...
```

The same 24 rows (`EXCEPT` between the two versions returns nothing), 62 buffers instead of 1,215. PostgreSQL derived `price >= 48.99` from the row comparison for the index; the 22 rows at exactly 48.99 with `id <= 139596` are filtered out.

**3. A single index scan.** The index `(category_id, price) WHERE status = 'active'` orders by price but not by `id` within equal prices, hence the incremental sort and the filter. An index matching the full `ORDER BY` turns the whole row comparison into an index condition:

```sql
CREATE INDEX products_active_category_id_price_id_idx
    ON products (category_id, price, id)
    WHERE status = 'active';
```

```text
 Limit (actual time=0.064..0.115 rows=24 loops=1)
   Buffers: shared hit=24 read=3
   ->  Index Scan using products_active_category_id_price_id_idx on products (actual time=0.063..0.110 rows=24 loops=1)
         Index Cond: ((category_id = 6) AND (ROW(price, id) > ROW(48.99, 139596)))
```

27 buffers. The cost: one more index (it would *replace* `products_active_category_id_price_idx`, since `(category_id, price)` is a prefix of it; Module 11's exercise 12). With prices rounded to .99, ties are common, so the tie-breaker matters for correctness, not just speed: without `id` in the `ORDER BY`, products at the same price could appear on two pages or none.

---

## Exercise 7: The `NOT IN` trap

**1. Leaf categories.**

```sql
SELECT count(*) FROM categories WHERE id NOT IN (SELECT parent_id FROM categories);   -- 0
```

The root categories have `parent_id` NULL, so the sub-query's list contains NULLs. For any id, `id NOT IN (1, 2, ..., NULL)` is `id <> 1 AND ... AND id <> NULL`, and `id <> NULL` is unknown, so the condition is never true. The correct versions:

```sql
SELECT count(*)
FROM categories AS c
WHERE NOT EXISTS (SELECT 1 FROM categories AS ch WHERE ch.parent_id = c.id);   -- 50

-- or, if you must use NOT IN, exclude the NULLs yourself:
SELECT count(*) FROM categories
WHERE id NOT IN (SELECT parent_id FROM categories WHERE parent_id IS NOT NULL);  -- 50
```

**2. Users without reviews.**

```sql
SET statement_timeout = '10s';
SELECT count(*) FROM users WHERE id NOT IN (SELECT user_id FROM reviews);
-- ERROR:  canceling statement due to statement timeout
RESET statement_timeout;

EXPLAIN SELECT count(*) FROM users WHERE id NOT IN (SELECT user_id FROM reviews);
```

```text
 Aggregate  (cost=475851910.42..475851910.43 rows=1 width=8)
   ->  Seq Scan on users  (cost=0.42..475851785.42 rows=50000 width=0)
         Filter: (NOT (ANY (id = (SubPlan 1).col1)))
         SubPlan 1
           ->  Materialize  (cost=0.42..8786.52 rows=292355 width=8)
                 ->  Index Only Scan using reviews_user_id_idx on reviews  (cost=0.42..6181.75 rows=292355 width=8)
```

```sql
SELECT count(*)
FROM users AS u
WHERE NOT EXISTS (SELECT 1 FROM reviews AS r WHERE r.user_id = u.id);
```

```text
 Aggregate (actual time=129.756..129.760 rows=1 loops=1)
   ->  Hash Right Anti Join (actual time=121.178..127.763 rows=29080 loops=1)
         Hash Cond: (r.user_id = u.id)
         ->  Index Only Scan using reviews_user_id_idx on reviews r (actual time=0.033..37.756 rows=292355 loops=1)
         ->  Hash (actual time=32.660..32.662 rows=100000 loops=1)
               ->  Seq Scan on users u (actual time=0.015..15.153 rows=100000 loops=1)
 Execution Time: 129.999 ms
```

**29,080 users**, in 0.13 s.

**Reasoning.** The `NOT IN` plan is a `SubPlan` (not `hashed`): 292,355 user ids don't fit in a hash table within 4 MB of `work_mem`, so for each of the 100,000 users PostgreSQL scans the materialised list until it finds a match, all 292,355 entries for the 29,080 users without reviews: billions of comparisons, an estimated cost of 476 million. With `SET work_mem = '32MB'`, the plan becomes `hashed SubPlan 1` with a cost of 9,448 and runs fast. But the NULL semantics prevent PostgreSQL from turning `NOT IN` into an anti-join, and every `NOT IN (subquery)` is one data-growth away from falling off the `work_mem` cliff. `NOT EXISTS` (or `LEFT JOIN ... WHERE r.user_id IS NULL`) is both correct with NULLs and plannable as an anti-join with any join algorithm.

---

## Exercise 8: Statistics on expressions

**1. Before:**

```text
 HashAggregate  (cost=41470.00..51572.81 rows=495725 width=16)
   Group Key: date_trunc('month'::text, created_at, 'UTC'::text)
   Planned Partitions: 8
   ->  Seq Scan on orders  (cost=0.00..13345.00 rows=500000 width=8)
-- actual: 36 groups

 Seq Scan on products  (cost=0.00..13315.00 rows=1000 width=8)
   Filter: ((attributes ->> 'brand'::text) = 'Voltix'::text)
-- actual: 4,388 rows
```

Neither expression has statistics. For `GROUP BY` on an unknown expression, the planner assumes almost every row is its own group (it has nothing better than the row count); for equality on an unknown expression, it uses the hard-coded default selectivity of 0.5% (200,000 × 0.005 = 1,000).

**2. Fix:**

```sql
CREATE STATISTICS orders_month_stats ON (date_trunc('month', created_at, 'UTC')) FROM orders;
CREATE STATISTICS products_brand_stats ON (attributes ->> 'brand') FROM products;
ANALYZE orders;
ANALYZE products;
```

```text
 HashAggregate  (cost=15845.00..15845.45 rows=36 width=16)
   Group Key: date_trunc('month'::text, created_at, 'UTC'::text)
   ->  Seq Scan on orders  (cost=0.00..13345.00 rows=500000 width=8)

 Seq Scan on products  (cost=0.00..13315.00 rows=4653 width=8)
   Filter: ((attributes ->> 'brand'::text) = 'Voltix'::text)
```

36 groups (exact) and 4,653 (6% above the truth; the MCV frequency comes from a sample). Single-expression statistics (PostgreSQL 14+) collect the same per-column statistics as for a real column: `n_distinct`, MCVs, histogram. They appear in `pg_stats_ext_exprs`.

**3. What changed in the plan.** Before, the planner expected ~500,000 groups, far more than a hash table in `work_mem` could hold, so it planned a **spilling** hash aggregate: `Planned Partitions: 8`, and its cost (41,470 to start) includes writing and re-reading partitions. With 36 groups, the hash table is tiny and the cost drops to 15,845. Here the executor didn't actually spill either way (the real group count was always 36), but the misestimate inflates the cost of every plan above it: in a larger query, a join or sort over the "500,000 groups" would be planned for the wrong size. For the brand filter, the estimate matters as soon as the result is joined or sorted.

An expression index on `(attributes ->> 'brand')` would also produce these statistics, *and* speed up the filter, at the price of index maintenance. Statistics objects are the choice when you need better estimates but not faster lookups, as for the `GROUP BY` here.

```sql
DROP STATISTICS orders_month_stats, products_brand_stats;
ANALYZE orders;
ANALYZE products;
```

---

## Exercise 9: What does pg_stat_statements say?

After a 60-second run (429,142 transactions, about 7,150 per second on this run):

```sql
WITH s AS (
    SELECT * FROM pg_stat_statements
    WHERE dbid = (SELECT oid FROM pg_database WHERE datname = 'bazaar')
)
SELECT left(regexp_replace(query, '\s+', ' ', 'g'), 50) AS query,
       calls,
       round(total_exec_time::numeric) AS total_ms,
       round(mean_exec_time::numeric, 3) AS mean_ms,
       round((100 * total_exec_time / sum(total_exec_time) OVER ())::numeric, 1) AS pct,
       round((shared_blks_hit + shared_blks_read)::numeric / nullif(rows, 0), 1) AS blocks_per_row
FROM s
ORDER BY total_exec_time DESC
LIMIT 10;
```

```text
                       query                        | calls  | total_ms | mean_ms | pct  | blocks_per_row
----------------------------------------------------+--------+----------+---------+------+----------------
 SELECT id, title, price FROM bazaar.products WHERE |  86510 |    61653 |   0.713 | 44.1 |           27.5
 SELECT s.id, s.shop_name, (SELECT count(*) FROM ba |   4375 |    32870 |   7.513 | 23.5 |          563.8
 SELECT id, user_id, status, total, created_at FROM |  38362 |    28762 |   0.750 | 20.6 |           27.5
 SELECT id, status, total, created_at FROM bazaar.o | 171626 |     4886 |   0.028 |  3.5 |            1.6
 SELECT id, title, price FROM bazaar.products WHERE | 384807 |     3949 |   0.010 |  2.8 |            4.0
 SELECT id, amount, provider, status FROM bazaar.pa | 128269 |     2798 |   0.022 |  2.0 |            3.7
 SELECT product_id, quantity, unit_price FROM bazaa | 128269 |     2527 |   0.020 |  1.8 |            1.6
 SELECT id, user_id, seller_id, status, total, crea | 128269 |     2214 |   0.017 |  1.6 |            4.0
```

1. **Most total time**: the category page (`... WHERE status = $1 AND category_id = $2 ORDER BY price, id LIMIT $3 OFFSET $4 * $5`), 44% of all execution time. **Highest mean**: the back-office report with correlated sub-queries per seller, 7.5 ms per call.
2. **Most calls**: the product lookup by id, 384,807 calls: three per order detail page (128,269 pages). That's the N+1 pattern of section 12.1; a single `WHERE id = ANY($1)` or a join with `order_items` would replace three round trips with one.
3. **Most buffers per row**: the back-office report, 564 buffers for each row it returns (it computes aggregates over thousands of orders to return 10 rows). Among the user-facing queries, the two `OFFSET`-paginated ones need 27.5 buffers per returned row, against 1.6–4 for the queries that fetch exactly what they show.
4. **44.1%** for the top statement. Fixing that single query (keyset pagination, exercise 6) would remove a large part of the server's load.

Note how differently the views rank: the report is second by total time with only 1% of the transactions, and the product lookup is the most frequent statement but only 2.8% of the time. Total time finds load, mean time finds latency, calls find chattiness.

---

## Exercise 10: Cheapest product per category

```text
 Sort (actual time=1558.259..1558.416 rows=2814 loops=1)
   Buffers: shared hit=510929 read=10161
   ->  Bitmap Heap Scan on products p (actual time=48.380..1555.942 rows=2814 loops=1)
         Recheck Cond: (status = 'active'::product_status)
         Filter: (price = (SubPlan 2))
         Rows Removed by Filter: 167224
         ->  Bitmap Index Scan on products_active_category_id_price_idx (actual time=40.987..40.987 rows=170038 loops=1)
         SubPlan 2
           ->  Result (actual time=0.008..0.008 rows=1 loops=170038)
                 InitPlan 1
                   ->  Limit (actual time=0.007..0.007 rows=1 loops=170038)
                         ->  Index Only Scan using products_active_category_id_price_idx on products p2 (actual time=0.007..0.007 rows=1 loops=170038)
                               Index Cond: (category_id = p.category_id)
 Execution Time: 1558.758 ms
```

**Reasoning.** PostgreSQL already did something clever inside the sub-plan: `min(price)` became an `InitPlan` with `LIMIT 1` over the `(category_id, price)` index, a single descent. But it runs that sub-plan once per active product, **170,038 times**, to compute only 50 distinct minimums (one per category with active products). 510,000 buffer accesses and 1.5 s.

**Rewrite A: compute the minimums once, then join** (57 ms, 3,627 buffers):

```sql
SELECT p.category_id, p.id, p.title, p.price
FROM (
    SELECT category_id, min(price) AS min_price
    FROM products
    WHERE status = 'active'
    GROUP BY category_id
) AS m
JOIN products AS p
  ON p.category_id = m.category_id
 AND p.price = m.min_price
 AND p.status = 'active'
ORDER BY p.category_id, p.id;
```

The grouped sub-query reads the whole partial index once (an index-only scan of 170,038 entries) and the join looks up the cheapest products of each of the 50 categories.

**Rewrite B: one descent per category with `LATERAL`** (8 ms, 3,176 buffers):

```sql
SELECT p.category_id, p.id, p.title, p.price
FROM categories AS c
CROSS JOIN LATERAL (
    SELECT min(price) AS min_price
    FROM products
    WHERE status = 'active' AND category_id = c.id
) AS m
JOIN products AS p
  ON p.category_id = c.id
 AND p.status = 'active'
 AND p.price = m.min_price
ORDER BY p.category_id, p.id;
```

```text
 Incremental Sort (actual time=0.169..8.055 rows=2814 loops=1)
   Buffers: shared hit=3176
   ->  Nested Loop (actual time=0.066..6.527 rows=2814 loops=1)
         ->  Nested Loop (actual time=0.023..0.818 rows=69 loops=1)
               ->  Index Only Scan using categories_pkey on categories c (actual time=0.003..0.017 rows=69 loops=1)
               ->  Result (actual time=0.011..0.011 rows=1 loops=69)
                     InitPlan 1
                       ->  Limit (actual time=0.010..0.010 rows=1 loops=69)
                             ->  Index Only Scan using products_active_category_id_price_idx on products (actual time=0.010..0.010 rows=1 loops=69)
                                   Index Cond: (category_id = c.id)
         ->  Bitmap Heap Scan on products p (actual time=0.012..0.075 rows=41 loops=69)
               Recheck Cond: ((c.id = category_id) AND (((InitPlan 1).col1) = price) AND (status = 'active'::product_status))
```

This is the correlated sub-query idea done right: the per-row work happens per *category* (69 loops, each one index descent), not per product. It's the standard way to emulate a "loose index scan" (jumping from one distinct value to the next) in PostgreSQL.

**Rewrite C: a window function** (`rank() OVER (PARTITION BY category_id ORDER BY price)` filtered on `rank = 1`) takes 221 ms here: it must sort all 170,038 active products (spilling to disk at 4 MB), though PostgreSQL 15+ stops each partition early thanks to the `Run Condition`. Window functions are the most general tool for top-N per group; when an index provides the per-group order, `LATERAL` beats them.

All three return the same 2,814 rows (ties at the minimum price included; `rank()` keeps ties, `row_number()` would keep one per category).

---

## Exercise 11: Watch a prepared statement go generic

```sql
PREPARE seller_lines(bigint) AS
SELECT oi.product_id, sum(oi.quantity) AS units
FROM orders AS o JOIN order_items AS oi ON oi.order_id = o.id
WHERE o.seller_id = $1
GROUP BY oi.product_id
ORDER BY units DESC
LIMIT 5;
```

**1. Small sellers.** After `EXECUTE seller_lines(2000)` ... `(1995)`:

```text
     name     | generic_plans | custom_plans
--------------+---------------+--------------
 seller_lines |             0 |            6
```

and `EXPLAIN EXECUTE seller_lines(1995)` shows `Recheck Cond: (seller_id = '1995'::bigint)`, a literal: a custom plan.

**2. Big sellers.** `DEALLOCATE seller_lines;`, prepare again, execute for sellers 1 to 5, then:

```text
-- EXPLAIN (ANALYZE, COSTS OFF) EXECUTE seller_lines(6)
                     ->  Nested Loop (actual time=2.091..127.371 rows=30535 loops=1)
                           ->  Bitmap Heap Scan on orders o (actual time=2.073..13.188 rows=11841 loops=1)
                                 Recheck Cond: (seller_id = $1)

     name     | generic_plans | custom_plans
--------------+---------------+--------------
 seller_lines |             1 |            5
```

`$1` in the plan: the generic plan.

**3. Why.** From the sixth execution on, PostgreSQL compares the generic plan's estimated cost with the **average** cost of the custom plans so far (plus an allowance for the planning time a generic plan saves):

```sql
EXPLAIN (GENERIC_PLAN)
SELECT oi.product_id, sum(oi.quantity) AS units
FROM orders AS o JOIN order_items AS oi ON oi.order_id = o.id
WHERE o.seller_id = $1
GROUP BY oi.product_id ORDER BY units DESC LIMIT 5;
-- Limit  (cost=5590.11..5590.12 rows=5 width=16)   ... Bitmap Heap Scan on orders o (... rows=372 ...)
```

The generic plan is costed for an *average* seller (372 orders) at about 5,590. For small sellers, the custom plans cost a few dozen units each (3 orders, a handful of index lookups), so the generic plan looks 100 times worse and is never chosen. For the five biggest sellers, the custom plans are hash joins over the whole `order_items` table costing tens of thousands; next to that average, 5,590 looks like a bargain, so PostgreSQL switches, permanently for this prepared statement. The switch is driven by which values happened to come first. From then on, seller 1 (69,805 orders) runs a plan made for 372 orders: on this machine it happened to be no slower than its custom plan (352 ms versus 399 ms, everything cached), but the planner has stopped choosing.

**4. Always custom:**

```sql
SET plan_cache_mode = force_custom_plan;
EXECUTE seller_lines(1);  EXECUTE seller_lines(2000);
SELECT name, generic_plans, custom_plans FROM pg_prepared_statements;
```

`generic_plans` stays at its value and `custom_plans` increases by 2. Planning this query costs well under a millisecond, which is negligible next to its execution, so for skewed parameters `force_custom_plan` is the safe choice. Set it narrowly: `ALTER ROLE bazaar_app SET plan_cache_mode = force_custom_plan`, or on the function that runs the query. For very frequent, cheap, uniform queries (primary key lookups), generic plans are the right default.

---

## Exercise 12: Counting without counting

[`count_estimate.sql`](count_estimate.sql):

```sql
CREATE OR REPLACE FUNCTION count_estimate(query text)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    plan jsonb;
BEGIN
    EXECUTE 'EXPLAIN (FORMAT JSON) ' || query INTO plan;
    RETURN (plan -> 0 -> 'Plan' ->> 'Plan Rows')::bigint;
END;
$$;

CREATE OR REPLACE FUNCTION table_count_estimate(tbl regclass)
RETURNS bigint
LANGUAGE sql
STABLE
AS $$
    SELECT CASE
               WHEN c.reltuples < 0 THEN NULL          -- never vacuumed or analyzed
               WHEN c.relpages = 0 THEN 0
               ELSE round(c.reltuples / c.relpages
                          * (pg_relation_size(c.oid) / current_setting('block_size')::int))::bigint
           END
    FROM pg_class AS c
    WHERE c.oid = tbl;
$$;
```

| Query | Estimate | Exact | Exact count time |
|---|---:|---:|---:|
| `orders WHERE status = 'delivered'` | 454,383 | 452,767 | 66 ms |
| `products WHERE status = 'active' AND category_id = 6` | 3,884 | 3,763 | 2 ms |
| `orders WHERE seller_id = 1500` | 80 | 11 | < 1 ms |
| `orders JOIN order_items` for December 2025 orders | 87,823 | 96,670 | 71 ms |
| `table_count_estimate('order_items')` | 1,266,928 | 1,266,928 | 75 ms |

Each estimate returns in about a millisecond whatever the table size.

**Which to show?** Estimates for large counts are good to within a few percent (MCV-based or histogram-based): "about 450,000 delivered orders" is fine for a dashboard. Estimates for small counts are unreliable in relative terms (80 versus 11) because they come from averages; for those, the exact `count(*)` is cheap anyway. A practical rule: run `count_estimate` first, and if it's below some threshold (say 10,000), run the exact count. For joins, estimates multiply errors and are only indicative.

**Never with user input**: `count_estimate` runs its argument as SQL. Plain `EXPLAIN` doesn't execute the query, and PL/pgSQL's `EXECUTE ... INTO` rejects a second statement (`count_estimate('SELECT 1; DELETE FROM victim')` fails with `INTO used with a command that cannot return data`), so the damage is limited. But planning still evaluates immutable functions with constant arguments, error messages reveal table and column names, and a crafted query can take a very long time just to plan. Treat the argument as code: call the function only with queries your application builds from fixed fragments, and pass values as parameters (Module 22). `reltuples` is `-1` for a table that has never been vacuumed or analysed (PostgreSQL 14+), hence the `CASE` in `table_count_estimate`.

---

## Exercise 13: The slow function

**Find the statement.** `pg_stat_statements` records statements inside functions with `track = all`:

```sql
SELECT pg_stat_statements_reset();
SELECT * FROM customer_summary(1);

SELECT left(regexp_replace(query, '\s+', ' ', 'g'), 70) AS query, calls,
       round(total_exec_time::numeric, 1) AS total_ms
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 4;
```

```text
                                 query                                  | calls | total_ms
------------------------------------------------------------------------+-------+----------
 SELECT * FROM customer_summary($1)                                     |     1 |     87.2
 SELECT c.name FROM order_items AS oi JOIN orders AS o ON o.id = oi.ord |     1 |     80.9
 SELECT pg_stat_statements_reset()                                      |     1 |      2.1
 SELECT count(*), coalesce(sum(o.total), $2) FROM orders AS o WHERE o.u |     1 |      1.0
```

The "favourite category" statement takes 81 of the function's 87 ms. Its plan, from `auto_explain` with `log_nested_statements`:

```sql
SET auto_explain.log_min_duration = '20ms';
SET auto_explain.log_analyze = on;
SET auto_explain.log_nested_statements = on;
SELECT * FROM customer_summary(1);
```

```text
LOG:  duration: 80.871 ms  plan:
	Query Text: SELECT c.name
	    FROM order_items AS oi
	    JOIN orders AS o ON o.id = oi.order_id
	    JOIN products AS p ON p.id = oi.product_id
	    JOIN categories AS c ON c.id = p.category_id
	    WHERE o.user_id::text = p_user::text
	    ...
	Limit  (cost=17035.33..17035.33 rows=1 width=18) (actual time=80.861..80.866 rows=1 loops=1)
	  ...
	                    Workers Planned: 2
	                    Workers Launched: 0
	                    ...
	                                            ->  Nested Loop  (cost=0.43..14591.65 rows=2640 width=8) (actual time=0.079..76.139 rows=515 loops=1)
	                                                  ->  Parallel Seq Scan on orders o  (cost=0.00..10740.83 rows=1042 width=8) (actual time=0.055..73.877 rows=234 loops=1)
	                                                        Filter: ((user_id)::text = '1'::text)
	                                                        Rows Removed by Filter: 499766
```

**The cause**: `o.user_id::text = p_user::text`. Casting the column to text hides it from `orders_user_id_created_at_idx` (Module 11, section 20), so all 500,000 orders are read (74 of the 81 ms) and the estimate falls back to the 0.5% default (1,042 rows instead of 234). Notice also `Workers Planned: 2, Workers Launched: 0`: the plan was made for parallel workers, but none were available inside the function call, so the "parallel" scan ran in the leader alone.

**Fix**: compare the values in their own type:

```sql
    WHERE o.user_id = p_user
```

After `CREATE OR REPLACE FUNCTION` with that change, `customer_summary(1)` returns the same row (214 orders, 111,280.69, Cycling, 2025-12-25 18:26:27+00) in about 5 ms warm, and `customer_summary(4242)` (14 orders, 4,554.62, T-Shirts) in 1.5 ms.

**Why `EXPLAIN` didn't help**: a function call is a `Function Scan` node; the statements inside are planned and executed when the function runs, as separate statements, invisible to the outer plan. The two tools that see them are `pg_stat_statements` with `track = all` (which statement) and `auto_explain` with `log_nested_statements` (which plan). A third option for PL/pgSQL is to copy the statement out and `EXPLAIN` it with the parameter replaced, or with `EXPLAIN (GENERIC_PLAN)`, which is how PL/pgSQL plans statements after a few calls.

To restore the original exercise function, re-run `examples/customer_summary.sql`.

---

## Milestone: the performance review

This is the reference write-up for [`../project.md`](../project.md). Measurements come from [`measure_slow.sql`](measure_slow.sql) at the milestone's starting state (state 12: no extended statistics) and [`measure_fixed.sql`](measure_fixed.sql) after [`fixed_queries.sql`](fixed_queries.sql), second runs of each.

| # | Query | Before | After | Root cause | Fix |
|---|---|---:|---:|---|---|
| S1 | Seller 1's orders, page 3001 | 97 ms, 7,373 buffers, temp spill | 0.2 ms, 15 buffers | `OFFSET` produces and discards 60,000 rows | keyset pagination on `(created_at, id)` |
| S2 | Orders above 3× seller average | > 30 s (56 s in a full run) | 172–189 ms, 7,670 buffers | correlated `SubPlan` re-averages a seller's orders 5,581 times (107 M rows) | pre-aggregate per seller, join |
| S3 | Berlin customers' shops | 41–61 ms, 24,595 buffers | 34–49 ms, 14,405 buffers | 10× underestimate (179 vs 1,827), masked by `random_page_cost = 4` | extended statistics **and** `random_page_cost = 1.1` |
| S4 | Users who never ordered | > 30 s (cost 890 M) | 150 ms, 2,769 buffers | `NOT IN` can't become an anti-join; un-hashed `SubPlan` | `NOT EXISTS` |
| S5 | Daily revenue, last 7 days | 65–103 ms, 7,098 buffers | 9 ms, 569 buffers | cast on `created_at` (no index use, 33% default estimate) | range on the bare column + BRIN index |
| S6 | Best sellers of all time | 1,074–1,145 ms, 1.27 M buffers | 576–603 ms, 9,316 buffers | `work_mem` too small for a hash aggregate, so `GroupAggregate` via a full index scan | `SET LOCAL work_mem = '64MB'` for this report |

**S3 in detail: two errors that cancelled out.** At the starting state, the Berlin filter is estimated at about 180 rows. The planner chooses nested loops with index lookups into `orders_shipping_address_id_idx`, which are cheap in reality because everything is cached (41–61 ms). Fixing the estimate with `CREATE STATISTICS addresses_country_city_stats` gives 1,888 rows, and the planner, still pricing each random page at 4, switches to hash joins over all 238,093 orders of 2025: correct estimates, *slower* query (95–156 ms, 5,452 buffers). Only with `random_page_cost = 1.1` does the plan go back to nested loops, now chosen for the right reason, at 34–49 ms. The lesson: when a plan is fast "by accident" (a wrong estimate compensating a wrong cost), fixing one error exposes the other. Fix both, and verify the plan after each step.

**S6**: the planner's `n_distinct` for `order_items.product_id` is underestimated (about 91,000 versus 169,483), but with either number the hash table doesn't fit in 8 MB of hash memory, so the plan choice is really about memory. `SET LOCAL` confines the larger `work_mem` to this report's transaction.

**What was not changed**: no `enable_*` settings, no query hints, and only one new index (the 24 kB BRIN). The BRIN index and `random_page_cost` are not in the reference files in `project/db/` (so that later modules' plans stay the same as printed); the extended statistics are, in `1201_schema_stats.sql`.
