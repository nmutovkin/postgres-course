# Module 12: Exercises

**Starting state.** The exercises run on the database **after** this module's milestone reference migration:

```bash
./project/bin/reset.sh 13          # large dataset + 1102 indexes + 1201 (pg_stat_statements, extended statistics)
./project/bin/psql.sh
```

If you worked through the theory, your database is already in this state (drop any `lab_` objects you left behind). Unless an exercise says otherwise, run `SET max_parallel_workers_per_gather = 0;` first so your plans match the solutions, and keep the default `random_page_cost` and `work_mem`.

Timings vary between machines and between runs; row counts, plan shapes and buffer counts are what the self-checks rely on. Estimates (`rows=` in plans) depend on `ANALYZE`'s random sample and can differ slightly from the solutions; where an exercise checks an estimate, it says how close yours should be.

Solutions: [solutions/README.md](solutions/README.md).

---

## Exercise 1 ★: Read a plan

Run:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT o.id, o.created_at, p.title, oi.quantity, oi.unit_price
FROM orders AS o
JOIN order_items AS oi ON oi.order_id = o.id
JOIN products AS p ON p.id = oi.product_id
WHERE o.user_id = 4242
ORDER BY o.created_at DESC;
```

From the output alone, answer:

1. In which order do the three scans start producing rows?
2. How many times in total was `products_pkey` searched, and how many buffers did those searches touch together?
3. The `order_items` scan shows `rows=2`. How many order lines did it return in total, and why isn't that 2?
4. Roughly how much time did the top `Nested Loop` spend on its own work, excluding its children?
5. The query has `ORDER BY o.created_at DESC`, but there is no `Sort` node. Why not?

**Self-check:** 35 order lines; 35 searches of `products_pkey` touching 140 buffers (yours may show a few `read=` instead of `hit=`, but the sum is the same); the top node's own time is a few hundredths of a millisecond.

<details><summary>Hint</summary>

Multiply per-loop numbers by `loops`; buffers are already totals. For question 5, look at the definition of the index used on `orders`, and think about what a nested loop does to the order of its outer input.

</details>

---

## Exercise 2 ★: Cost by hand

Look up `relpages` and `reltuples` for `payments` in `pg_class`, and compute **before running `EXPLAIN`** the total cost the planner will give to:

```sql
EXPLAIN SELECT * FROM payments WHERE amount > 1000;
```

Then do the same for `EXPLAIN SELECT * FROM payments;` (no condition). Check both against `EXPLAIN`.

**Self-check:** your two numbers match the `Seq Scan` node's total cost to the second decimal: 12811.20 and 11469.16.

<details><summary>Hint</summary>

Section 7.2: pages × `seq_page_cost` + rows × `cpu_tuple_cost` + rows × `cpu_operator_cost` per condition.

</details>

---

## Exercise 3 ★: Statistics to estimates

Using only `pg_stats` (and `pg_class.reltuples`), predict the planner's row estimate for:

1. `SELECT * FROM orders WHERE status = 'paid';`
2. `SELECT * FROM orders WHERE seller_id = 2000;`

Then check with `EXPLAIN`, and compare both with the true counts. Which one is wrong by more, and why?

**Self-check:** each prediction equals the `EXPLAIN` estimate within ±1 row. The true counts are 1,397 and 3.

<details><summary>Hint</summary>

Is the value in `most_common_vals`? If yes, multiply its frequency by the row count. If not, spread what the MCV list doesn't cover, `1 − sum(most_common_freqs)`, evenly over the `n_distinct − (number of MCVs)` other values (section 8.3).

</details>

---

## Exercise 4 ★★: Hash batches

Run the 2025 revenue-by-status query from section 5.2:

```sql
SELECT o.status, sum(oi.quantity * oi.unit_price)
FROM orders AS o
JOIN order_items AS oi ON oi.order_id = o.id
WHERE o.created_at >= '2025-01-01'
GROUP BY o.status;
```

Find the smallest `work_mem` (in whole megabytes) at which the hash join runs in a single batch. Explain the relationship between your answer, the `Memory Usage` reported by the `Hash` node, and `hash_mem_multiplier`. What would the answer be with `hash_mem_multiplier = 1`?

**Self-check:** at the default `work_mem` the `Hash` node reports `Batches: 2` and the plan shows `temp` buffers; at your answer it reports `Batches: 1` with a memory usage around 13,000 kB and no `temp` buffers. Your answer is a single-digit number of megabytes.

<details><summary>Hint</summary>

`SET work_mem = '5MB';`, `'6MB'`, ... and `EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF)` until the batches drop to 1. The hash table may use `work_mem × hash_mem_multiplier`.

</details>

---

## Exercise 5 ★★: Three ways to join

Seller 42 opens "December orders with their lines":

```sql
SELECT o.id, o.created_at, oi.product_id, oi.quantity, oi.unit_price
FROM orders AS o
JOIN order_items AS oi ON oi.order_id = o.id
WHERE o.seller_id = 42
  AND o.created_at >= '2025-12-01';
```

Make PostgreSQL run it with a nested loop, a hash join, and a merge join in turn (using the `enable_*` settings). For each, record the estimated total cost, the execution time and the buffers, and explain why the losers lose. Which inputs does the merge join read, and how far?

**Self-check:** 278 rows in every variant. The planner's own choice is the nested loop, at under 5 ms; the hash join and the merge join each take hundreds of milliseconds, and the merge join's estimated cost is more than 30 times the nested loop's.

<details><summary>Hint</summary>

Disable the planner's choice first, then the second choice. `enable_nestloop`, `enable_hashjoin`, `enable_mergejoin`. Look at how many rows of `order_items` each plan reads.

</details>

---

## Exercise 6 ★★: Keyset pagination for category pages

The workload's category page ([`examples/workload/category_page.sql`](examples/workload/category_page.sql)) pages through active products of a category by price with `OFFSET`:

```sql
SELECT id, title, price
FROM products
WHERE status = 'active' AND category_id = 6
ORDER BY price, id
LIMIT 24 OFFSET 1176;      -- page 50
```

1. Measure it.
2. Write the keyset version of page 50, given that the last row of page 49 is product 139596 at 48.99, and check that it returns the same 24 rows.
3. Can you make the keyset query a single `Index Scan` without a sort node? What does that cost?

**Self-check:** 3,763 active products in category 6. Page 50 starts with products 147022, 152947 and 160517 (all at 48.99). The `OFFSET` version touches over 1,000 buffers; your keyset version touches under 100.

<details><summary>Hint</summary>

A row comparison `(price, id) > (48.99, 139596)`. For step 3, compare the existing partial index's columns with the `ORDER BY`.

</details>

---

## Exercise 7 ★★: The `NOT IN` trap

1. Leaf categories are those that are nobody's parent. Write the query with `NOT IN (SELECT parent_id FROM categories)` and count the result. Explain it. Then write it correctly.
2. Count the users who have never written a review, first with `NOT IN`, then with `NOT EXISTS`. Set `statement_timeout = '10s'` before trying the `NOT IN` version. Use `EXPLAIN` (without `ANALYZE`) to explain what happens.

**Self-check:** (1) `NOT IN` returns 0; the correct answer is 50 leaf categories. (2) 29,080 users; the `NOT IN` version hits the timeout, and its `EXPLAIN` cost is in the hundreds of millions.

<details><summary>Hint</summary>

What is `parent_id` for the root categories? For (2), look for `hashed SubPlan` versus `SubPlan` with a `Materialize` in the plans, and think about `work_mem`.

</details>

---

## Exercise 8 ★★: Statistics on expressions

Two queries get poor estimates:

```sql
SELECT date_trunc('month', created_at, 'UTC') AS month, count(*)
FROM orders
GROUP BY 1;

SELECT id FROM products WHERE attributes ->> 'brand' = 'Voltix';
```

1. For each, compare the estimated and actual row counts. Where do the estimates come from?
2. Fix both estimates **without creating an index** and without changing the queries.
3. Explain what the first query's plan does differently after the fix, and why the planner cared about the estimate there.

**Self-check:** before: about 495,000 estimated groups (36 actual), and exactly 1,000 estimated rows (4,388 actual). After: 36 groups estimated, and a brand estimate within 10% of 4,388. The `Planned Partitions` line disappears from the first plan.

<details><summary>Hint</summary>

PostgreSQL 14+ accepts expressions in `CREATE STATISTICS`. Don't forget `ANALYZE`.

</details>

---

## Exercise 9 ★★: What does pg_stat_statements say?

Reset the statistics, run the workload for 60 seconds, and answer from `pg_stat_statements` alone:

```sql
SELECT pg_stat_statements_reset();
```

```bash
./modules/12-query-performance/examples/workload/run_workload.sh 60
```

1. Which statement consumed the most total execution time? Which has the highest mean time?
2. Which statement was called most often, and which application pattern explains its call count?
3. Which statement touches the most buffers per returned row?
4. What fraction of all execution time did the top statement take?

**Self-check:** (1) the category page query (`... ORDER BY price, id LIMIT $3 OFFSET $4 * $5`) by total time, the back-office report with sub-queries per seller by mean time; (2) `SELECT id, title, price FROM bazaar.products WHERE id = $1`, called three times per order detail page; (4) somewhere between a third and a half.

<details><summary>Hint</summary>

Filter on `dbid` for the `bazaar` database to hide your own monitoring queries; compute `(shared_blks_hit + shared_blks_read) / nullif(rows, 0)`, and `total_exec_time / sum(total_exec_time) OVER ()`.

</details>

---

## Exercise 10 ★★: Cheapest product per category

The storefront's "deals" page shows, for every category, its cheapest active products (all products tied at the lowest price):

```sql
SELECT p.category_id, p.id, p.title, p.price
FROM products AS p
WHERE p.status = 'active'
  AND p.price = (SELECT min(p2.price)
                 FROM products AS p2
                 WHERE p2.category_id = p.category_id
                   AND p2.status = 'active')
ORDER BY p.category_id, p.id;
```

Measure it, explain the plan, and rewrite it to be at least 20 times faster without adding indexes.

**Self-check:** 2,814 rows in every version. The original runs its sub-plan 170,038 times and takes over a second; a good rewrite takes well under 100 ms (the solution's best takes under 10 ms).

<details><summary>Hint</summary>

How many distinct minimums are there to compute? Compute them once (a grouped sub-query, or a `LATERAL` per category), then find the products matching them. The partial index `products_active_category_id_price_idx` can answer "the minimum price in category X" with one index descent.

</details>

---

## Exercise 11 ★★★: Watch a prepared statement go generic

1. Prepare the `seller_lines` statement from section 9.4. Execute it for sellers 2000, 1999, 1998, 1997, 1996 and 1995 (small sellers). Does PostgreSQL switch to a generic plan? Check `pg_prepared_statements`.
2. Deallocate and repeat with sellers 1 to 6 (big sellers). Now?
3. Explain both outcomes from the costs: compare the cost of the generic plan (`EXPLAIN (GENERIC_PLAN)`) with the costs of the custom plans.
4. Make the session always use custom plans, and prove it with `pg_prepared_statements`.

**Self-check:** after (1), `generic_plans = 0, custom_plans = 6`; after (2), `generic_plans = 1, custom_plans = 5`, and `EXPLAIN EXECUTE seller_lines(6)` shows `seller_id = $1`. After (4), `generic_plans` stops increasing.

<details><summary>Hint</summary>

A generic plan is chosen when its estimated cost is not higher than the average of the custom plans' costs. What is a custom plan's cost for a seller with 3 orders, and for one with 70,000?

</details>

---

## Exercise 12 ★★★: Counting without counting

Write two functions:

1. `count_estimate(query text) RETURNS bigint`, which returns the planner's row estimate for a query without running it, using `EXPLAIN (FORMAT JSON)`.
2. `table_count_estimate(tbl regclass) RETURNS bigint`, which returns the estimated row count of a table from `pg_class`, scaled to the table's current size the way the planner does.

Compare estimate and exact count for: delivered orders; active products in category 6; orders of seller 1500; order lines of December 2025 orders (a join); all order lines. Which estimates would you show in a UI, and which not? Why must `count_estimate` never receive user input?

**Self-check:** the delivered estimate is within 1% of 452,767 and returns in milliseconds; the seller-1500 estimate is several times the true 11; `table_count_estimate('order_items')` returns 1,266,928.

<details><summary>Hint</summary>

`EXECUTE 'EXPLAIN (FORMAT JSON) ' || query INTO plan;` then `plan -> 0 -> 'Plan' ->> 'Plan Rows'`. For the table function: `reltuples / relpages × (pg_relation_size / block_size)`.

</details>

---

## Exercise 13 ★★★: The slow function

Install the CRM helper function in [`examples/customer_summary.sql`](examples/customer_summary.sql):

```bash
./project/bin/psql.sh bazaar < modules/12-query-performance/examples/customer_summary.sql
```

`SELECT * FROM customer_summary(1);` takes tens of milliseconds, although each of its three queries should need only a few index lookups. `EXPLAIN ANALYZE` on the call shows only a `Function Scan`. Find the slow statement **without reading the function's code first**, using two tools from this module, then fix the function.

**Self-check:** the result for user 1 is 214 orders, 111,280.69 spent, favourite category Cycling, last order 2025-12-25 18:26:27+00, and stays the same after your fix. After the fix, a warm call takes a few milliseconds.

<details><summary>Hint</summary>

`pg_stat_statements` tracks statements inside functions (the course sets `pg_stat_statements.track = all`). `auto_explain.log_nested_statements = on` logs their plans; read them with `docker logs course-pg`. Look at the `Filter` line of the scan on `orders`.

</details>
