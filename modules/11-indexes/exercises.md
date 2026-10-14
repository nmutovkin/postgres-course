# Module 11: Exercises

<!-- nav:start -->
> [Theory](README.md) · **Exercises** · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 10 Transactions and Concurrency](../10-transactions-concurrency/README.md) · [Course home](../../README.md) · [12 Query Planning and Performance →](../12-query-performance/README.md)
<!-- nav:end -->

**Starting state.** Unless an exercise says otherwise, the exercises run on the database **after** this module's milestone: the large dataset plus the reference indexes.

```bash
./project/bin/reset.sh 12          # = reset.sh 11 + 1101_seed_large.sql + 1102_schema_indexes.sql (about 2 minutes)
./project/bin/psql.sh
```

If you followed the theory, you can instead apply `project/db/1102_schema_indexes.sql` to the database you already have, after dropping any `lab_` indexes you left behind. Most exercises create indexes; each solution drops them again so the exercises stay independent. Exercise 13 also needs `pgbench`, which ships in the course container.

Timings in the self-checks are only rough guides, since your machine differs. Row counts, values and buffer counts in the order of magnitude given are what to check. Turning off parallel query (`SET max_parallel_workers_per_gather = 0;`) makes your plans match the solutions more closely.

Solutions: [solutions/README.md](solutions/README.md).

---

<!-- toc:start -->
**Contents**

- [Exercise 1 ★: An index inventory](#exercise-1--an-index-inventory)
- [Exercise 2 ★: Predict the plan](#exercise-2--predict-the-plan)
- [Exercise 3 ★: Leftmost prefix](#exercise-3--leftmost-prefix)
- [Exercise 4 ★★: The fulfilment queue](#exercise-4--the-fulfilment-queue)
- [Exercise 5 ★★: The seller's rating histogram](#exercise-5--the-sellers-rating-histogram)
- [Exercise 6 ★★: Admin autocomplete](#exercise-6--admin-autocomplete)
- [Exercise 7 ★★: Audit the foreign keys](#exercise-7--audit-the-foreign-keys)
- [Exercise 8 ★★: BRIN for payments](#exercise-8--brin-for-payments)
- [Exercise 9 ★★: Three slow queries](#exercise-9--three-slow-queries)
- [Exercise 10 ★★: What can a jsonb index answer?](#exercise-10--what-can-a-jsonb-index-answer)
- [Exercise 11 ★★★: Revenue without the heap](#exercise-11--revenue-without-the-heap)
- [Exercise 12 ★★★: Redundant indexes](#exercise-12--redundant-indexes)
- [Exercise 13 ★★★: An index build under load](#exercise-13--an-index-build-under-load)

<!-- toc:end -->

## Exercise 1 ★: An index inventory

Write one query that lists every index on `orders` with its name, whether it is unique, its size (pretty-printed) and its full definition, largest first. Then write a second query that shows the table's size without indexes, the total size of its indexes, and the ratio between the two, rounded to two decimals.

**Self-check:** 4 indexes; the two largest are the composite indexes from `1102_schema_indexes.sql`, at 15 MB each. The ratio is about 0.83: the indexes of `orders` take almost as much space as its data.

<details><summary>Hint</summary>

`pg_index` links an index (`indexrelid`) to its table (`indrelid`); `pg_get_indexdef(oid)` returns the `CREATE INDEX` statement. `pg_table_size()` and `pg_indexes_size()` take a table.

</details>

---

## Exercise 2 ★: Predict the plan

For each query, predict **before running `EXPLAIN`** whether PostgreSQL will use a sequential scan, an index scan, a bitmap scan or an index-only scan, and which index. Write down one sentence of reasoning for each. Then check with `EXPLAIN (COSTS OFF)`.

```sql
-- a)
SELECT * FROM payments WHERE order_id = 123;
-- b)
SELECT id, total FROM orders WHERE seller_id = 2;
-- c)
SELECT count(*) FROM orders WHERE user_id BETWEEN 1 AND 1000;
-- d)
SELECT id, title FROM products WHERE category_id = 22;
-- e)
SELECT * FROM orders WHERE seller_id = 2 ORDER BY created_at DESC LIMIT 10;
```

**Self-check:** Across the five queries there are **no** sequential scans, two bitmap scans, two index scans and one index-only scan. If you predicted differently for a query, the solution explains which fact you missed.

<details><summary>Hint</summary>

Seller 2 is the second-biggest shop (46,196 orders). Category 22 (T-Shirts) has 4,706 products. Users 1 to 1000 have about 22,000 orders. Ask yourself for each query: how many rows match, how scattered are they, does the query need columns that aren't in the index, and is there an `ORDER BY ... LIMIT` the index can serve?

</details>

---

## Exercise 3 ★: Leftmost prefix

The index `orders_seller_id_created_at_idx` is on `(seller_id, created_at DESC)`. For each query, decide whether the index can be used to **search** (descend to the first match and read a contiguous run), whether it can at best be **scanned in full**, or not at all, and whether a **sort** step remains. Verify with `EXPLAIN (ANALYZE, BUFFERS)`.

```sql
-- a)
SELECT id FROM orders WHERE seller_id = 7 AND created_at >= '2025-12-01';
-- b)
SELECT id FROM orders WHERE created_at >= '2025-12-24';
-- c)
SELECT id, created_at FROM orders WHERE seller_id IN (7, 8) ORDER BY created_at DESC LIMIT 5;
-- d)
SELECT id, created_at FROM orders WHERE seller_id = 7 ORDER BY created_at LIMIT 5;
-- e)
SELECT id FROM orders WHERE seller_id >= 1900 AND created_at >= '2025-12-01';
```

Then rewrite query (c) so that it reads fewer than 30 buffers and returns the same five rows.

**Self-check:** (a), (d) and (e) search the index; (b) doesn't use it (sequential scan, 5,581 rows); (c) uses it but needs a `Sort` over 23,997 rows. Your rewrite of (c) returns orders 499994, 499982, 499915, 499894 and 499824 (in that order) and has no `Sort` node over thousands of rows.

<details><summary>Hint</summary>

For (c), the index delivers each seller's orders newest first, but not the two sellers merged. Ask each seller separately for its newest five (`UNION ALL` of two `LIMIT 5` subqueries, or a `LATERAL` join over the list of sellers), then take the newest five of those ten.

</details>

---

## Exercise 4 ★★: The fulfilment queue

Sellers have a "to ship" screen listing their **open** orders (status `pending`, `paid` or `shipped`), oldest first:

```sql
SELECT id, user_id, status, total, created_at
FROM orders
WHERE seller_id = 1
  AND status IN ('pending', 'paid', 'shipped')
ORDER BY created_at;
```

Measure it, then create the smallest index you can that makes it fast. Compare your index's size with `orders_seller_id_created_at_idx`. Finally, check whether your index is also used by `WHERE seller_id = 1 AND status = 'paid'` and by `WHERE seller_id = 1 AND status IN ('pending', 'paid', 'shipped', 'delivered')`, and explain the difference.

**Self-check:** 957 rows. Your index is under 300 kB (versus 15 MB), the query reads fewer than 200 buffers instead of about 7,400, and the plan has no `Filter` line on `status`. The `'paid'` query uses your index; the one including `'delivered'` doesn't.

<details><summary>Hint</summary>

Only about 7,600 of 500,000 orders are open. A partial index stores only those. The planner uses it when it can prove from the query's `WHERE` that every row it needs is in the index.

</details>

---

## Exercise 5 ★★: The seller's rating histogram

The seller portal shows how a shop's products are rated:

```sql
SELECT r.rating, count(*)
FROM reviews AS r
JOIN products AS p ON p.id = r.product_id
WHERE p.seller_id = 25
GROUP BY r.rating
ORDER BY r.rating;
```

Make this query run without reading any table pages: both tables must be read with an **index-only scan** and `Heap Fetches: 0`. Report the time before and after.

**Self-check:** 5 rows; the counts are 71, 123, 221, 456 and 879 (1,750 reviews). Before: a hash join over a sequential scan of all 292,355 reviews, about 60 ms. After: a nested loop of two index-only scans, under 10 ms, `Heap Fetches: 0` on both.

<details><summary>Hint</summary>

For `products`, the query needs `seller_id` (to filter) and `id` (to join). For `reviews`, it needs `product_id` (to join) and `rating` (to group). Look at the existing indexes: which columns do they lack? After creating indexes, run `VACUUM` on the tables so the visibility map is current.

</details>

---

## Exercise 6 ★★: Admin autocomplete

The admin tool suggests users as you type the start of an e-mail address, ten at a time, alphabetically:

```sql
SELECT id, email
FROM users
WHERE lower(email) LIKE 'olumide.s%'
ORDER BY lower(email)
LIMIT 10;
```

1. Create an index that lets PostgreSQL answer the `LIKE` with an index range scan. Check the plan of the query **without** the `ORDER BY ... LIMIT` first.
2. Now run the full query. Explain what the planner does and why it reads tens of thousands of buffers.
3. Make the full query read fewer than 30 buffers.

**Self-check:** 134 users match the prefix. The first result is user 1532, `olumide.santos1532@inbox.example.net`. In step 2 you should see `Rows Removed by Filter` of about 76,000. In step 3, the plan has an `Index Cond` containing `~>=~` and `~<~` (or `>=` and `<` with `COLLATE "C"`).

<details><summary>Hint</summary>

In step 2, compare the estimated and actual row counts, and think about which index can return rows in `ORDER BY lower(email)` order *in the database's collation*. In step 3, `ORDER BY ... USING ~<~` orders by the pattern operator class's byte-wise comparison; alternatively, think about `COLLATE "C"`.

</details>

---

## Exercise 7 ★★: Audit the foreign keys

Write a catalog query that lists every foreign key in the `bazaar` schema whose columns are **not** the leading columns of any non-partial index on the referencing table. Show the table, the constraint name, the FK columns and the table's size, biggest table first. A composite FK counts as covered if its columns are the first columns of an index, in any order.

**Self-check:** At state 12, your query returns **0 rows**. Run it again inside a transaction after dropping the module's indexes, then roll back:

```sql
BEGIN;
\i /course/modules/11-indexes/examples/drop_1102_indexes.sql
-- your query here
ROLLBACK;
```

It now returns **10 rows**, starting with `products_category_id_fkey` and `order_items_product_id_fkey`.

<details><summary>Hint</summary>

`pg_constraint` with `contype = 'f'` has the FK columns as an array of attribute numbers in `conkey`. `pg_index.indkey` is an `int2vector`; `string_to_array(indkey::text, ' ')::int2[]` turns it into an ordinary 1-based array. Compare the first `cardinality(conkey)` elements with `conkey` using `@>` and `<@`.

</details>

---

## Exercise 8 ★★: BRIN for payments

`payments` has 536,816 rows inserted in time order. Finance asks for the succeeded payments of one day, per provider:

```sql
SELECT provider, count(*), sum(amount)
FROM payments
WHERE created_at >= '2025-11-28' AND created_at < '2025-11-29'
  AND status = 'succeeded'
GROUP BY provider
ORDER BY provider;
```

1. Using `pg_stats`, show why `payments.created_at` is a good BRIN candidate. Is `reviews.created_at` as good? `addresses.created_at`?
2. Create a BRIN index and a B-tree index on `payments.created_at`, compare their sizes, then drop the B-tree and look at the plan. Explain `Heap Blocks: lossy` and `Rows Removed by Index Recheck`.

**Self-check:** 3 rows: adyen 439, paypal 940, stripe 1899 (3,278 payments). BRIN index about 24 kB versus about 12 MB for the B-tree. With only the BRIN index, the query reads a few hundred pages instead of all 6,101.

<details><summary>Hint</summary>

The `correlation` column of `pg_stats` measures how well the physical row order follows the column's sort order.

</details>

---

## Exercise 9 ★★: Three slow queries

Each of these queries returns the right answer but ignores the indexes that exist. For each, explain why from the plan, then rewrite it (don't create indexes) so that it uses one, and check that the result doesn't change.

```sql
-- a) Seller 1's orders on Black Friday 2025 (UTC)
SELECT id, total
FROM orders
WHERE seller_id = 1
  AND date_trunc('day', created_at AT TIME ZONE 'UTC') = '2025-11-28';

-- b) The payments of one order, as sent by an application that uses Decimal for ids
SELECT id, amount, status FROM payments WHERE order_id = 250000.0;

-- c) Orders that user 4242 placed OR that contain product 4242
SELECT DISTINCT o.id, o.created_at
FROM orders AS o
JOIN order_items AS oi ON oi.order_id = o.id
WHERE o.user_id = 4242 OR oi.product_id = 4242;
```

**Self-check:** (a) 443 orders with a total of 313,915.35, under 100 buffers. (b) 2 payments, under 10 buffers. (c) 45 orders, under 200 buffers and under 5 ms (the original takes several hundred milliseconds and spills a hash to disk).

<details><summary>Hint</summary>

(a) What would the index need to store for this condition to be searchable? Can you state the same condition on the bare column? (b) Look at the `Filter` line: which side got the cast? (c) An `OR` that spans two tables can only be checked after the join. Split it into two queries that each have one indexable condition.

</details>

---

## Exercise 10 ★★: What can a jsonb index answer?

`products_attributes_idx` is `USING gin (attributes jsonb_path_ops)`. For each predicate, decide whether that index can be used, then verify with `EXPLAIN`:

```sql
-- a) attributes @> '{"brand": "Voltix"}'
-- b) attributes ->> 'brand' = 'Voltix'
-- c) attributes ? 'isbn'
-- d) attributes @> '{"storage": {"type": "ssd"}}'
-- e) attributes @? '$.ram_gb ? (@ >= 32)'
```

Then make "how many active products have at least 64 GB of RAM?" fast, written as `WHERE status = 'active' AND (attributes ->> 'ram_gb')::int >= 64`. Discuss what could go wrong with your index if a seller ever stored `"ram_gb": "lots"`.

**Self-check:** (a) and (d) use the index; (b), (c) and (e) don't. The final count is 705, read through an `Index Cond` on the `ram_gb` expression with under 1,000 buffers (versus the whole table, about 10,300).

<details><summary>Hint</summary>

GIN answers *operators* of its operator class, and `jsonb_path_ops` only knows containment and jsonpath *equality* checks. For a range on one key, a B-tree expression index is the tool. Remember to `ANALYZE` after creating an expression index.

</details>

---

## Exercise 11 ★★★: Revenue without the heap

The seller dashboard's revenue chart:

```sql
SELECT date_trunc('month', created_at, 'UTC') AS month,
       count(*) AS orders,
       sum(total) AS revenue
FROM orders
WHERE seller_id = 42
  AND created_at >= '2025-01-01'
  AND created_at <  '2026-01-01'
  AND status IN ('paid', 'shipped', 'delivered')
GROUP BY 1
ORDER BY 1;
```

Make it an index-only scan with `Heap Fetches: 0`, **without increasing the number of indexes on `orders`**. The seller dashboard's "latest 50 orders" query (`WHERE seller_id = $1 AND created_at >= $2 ORDER BY created_at DESC LIMIT 50`) must still be served by an index without a sort. Do it the way you would on a production system that takes orders all day, and state what your change costs.

**Self-check:** 12 rows; December 2025 has 89 orders and 32,744.76 of revenue. The plan shows `Index Only Scan` with `Heap Fetches: 0` and fewer than 20 buffers, and `orders` still has exactly 4 indexes.

<details><summary>Hint</summary>

Replace `orders_seller_id_created_at_idx` with a covering version: build the new one concurrently, drop the old one concurrently, and rename. Which columns does the query read besides the key columns?

</details>

---

## Exercise 12 ★★★: Redundant indexes

An index is **redundant** if another index on the same table, of the same type, starts with the same key columns (same operator classes, same sort options) and makes it unnecessary. For example, `orders (user_id)` is redundant next to `orders (user_id, created_at DESC)`. Write a query that reports redundant indexes: table, redundant index, the index that covers it, and the wasted size. Never report unique indexes (they enforce rules), partial indexes or expression indexes. Report exact duplicates only once.

Test it inside a transaction:

```sql
BEGIN;
CREATE INDEX orders_user_id_idx ON orders (user_id);
CREATE INDEX products_seller_id_idx ON products (seller_id);
CREATE INDEX orders_user_id_created_at_idx2 ON orders (user_id, created_at DESC);
CREATE INDEX orders_seller_asc ON orders (seller_id, created_at);
-- your query
ROLLBACK;
```

**Self-check:** 0 rows at state 12. Inside the transaction: 4 rows, covering `orders_user_id_idx` (twice: by each of the two `(user_id, created_at DESC)` indexes), `products_seller_id_idx` (covered by the unique `products_seller_id_sku_key`) and one of the two identical `(user_id, created_at DESC)` indexes. `orders_seller_asc` is not reported by the strict rule; the solution discusses whether it should be.

<details><summary>Hint</summary>

Turn `indkey`, `indclass` and `indoption` into arrays as in exercise 7. `indnkeyatts` is the number of key columns (excluding `INCLUDE` columns). Compare slices `[1:a.indnkeyatts]`.

</details>

---

## Exercise 13 ★★★: An index build under load

This exercise needs three terminals and **changes stock quantities** in `inventory` (reset afterwards if you care).

1. Terminal 1: start a write load of small stock updates for 60 seconds with `pgbench`, using the script [`solutions/ex13_stock_load.sql`](solutions/ex13_stock_load.sql):

   ```bash
   docker exec course-pg pgbench -U postgres -n -c 4 -T 60 -f /course/modules/11-indexes/solutions/ex13_stock_load.sql bazaar
   ```

2. Terminal 2: while it runs, build an index on the *available* stock (`quantity - reserved`) of `inventory` without blocking the load. Then simulate a forgotten transaction: in terminal 3, run `BEGIN; SELECT count(*) FROM categories;` and leave it open. Back in terminal 2, set `statement_timeout = '5s'` and try to build a second index, a partial one on `inventory (product_id)` for products with fewer than 5 units available.
3. While the second build waits, find out from terminal 3 (in a new session or after `COMMIT`ting) *what* it is waiting for and *who* it is waiting for.
4. Clean up after the failure so that the second index exists and is usable.

**Self-check:** `pgbench` reports `number of failed transactions: 0`. During step 3, `pg_stat_progress_create_index.phase` shows a `waiting for ...` phase and `current_locker_pid` is the pid of the open transaction. At the end, `SELECT count(*) FROM pg_index WHERE NOT indisvalid` returns 0 and both new indexes are listed in `\d inventory` without `INVALID`.

<details><summary>Hint</summary>

`CREATE INDEX CONCURRENTLY` waits for transactions that might hold old snapshots. A failed concurrent build leaves an invalid index; `IF NOT EXISTS` won't repair it.

</details>
