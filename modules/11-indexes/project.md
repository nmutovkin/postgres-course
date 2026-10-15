# Module 11: Project milestone — indexes for the Bazaar workload

<!-- nav:start -->
> [Theory](README.md) · [Exercises](exercises.md) · **Project** · [Solutions](solutions/README.md)  
> [← 10 Transactions and Concurrency](../10-transactions-concurrency/README.md) · [Course home](../../README.md) · [12 Query Planning and Performance →](../12-query-performance/README.md)
<!-- nav:end -->

<!-- toc:start -->
**Contents**

- [The goal](#the-goal)
- [Requirements](#requirements)
  - [1. Load the large dataset](#1-load-the-large-dataset)
  - [2. Measure the baseline](#2-measure-the-baseline)
  - [3. Index every foreign key](#3-index-every-foreign-key)
  - [4. Make the hot queries fast](#4-make-the-hot-queries-fast)
  - [5. Write it as a migration](#5-write-it-as-a-migration)
  - [6. Document before and after](#6-document-before-and-after)
- [Acceptance checks](#acceptance-checks)
- [Hints](#hints)
- [Reference solution](#reference-solution)

<!-- toc:end -->

## The goal

Bazaar is about to open to real traffic, and the product team has handed you the list of the queries the application runs most. On the large dataset, most of them read entire tables: "my orders" scans half a million orders, a category page reads 80 MB of products, and deleting a draft product scans every order line. Your job is to load the large dataset and design the **smallest set of indexes** that makes every hot query fast, and to **prove** it with before-and-after measurements.

You write the indexes as a migration file, the way the reference implementation does, and you document your reasoning.

## Requirements

### 1. Load the large dataset

```bash
./project/bin/reset.sh 11
./project/bin/psql.sh bazaar -v ON_ERROR_STOP=1 < project/db/1101_seed_large.sql
```

Check the counts against the table in `project/db/NOTES.md` (100,000 users, 200,000 products, 500,000 orders, 1,266,928 order items).

### 2. Measure the baseline

Run the acceptance check below once **before** creating any index and keep its output: it is your "before" column.

### 3. Index every foreign key

Every foreign key column in the `bazaar` schema must be the **leading column of a non-partial index** on the referencing table, unless an existing index (primary key, unique constraint, exclusion constraint, or one of your hot-query indexes) already starts with it. Don't create an index that duplicates the leading columns of one that exists.

### 4. Make the hot queries fast

These are the hot queries. Parameter values are fixed so that results are comparable; the application sends other values of the same shape.

| # | Screen | Query |
|---|---|---|
| 01 | Customer: my orders | `SELECT id, status, total, created_at FROM orders WHERE user_id = 4242 ORDER BY created_at DESC LIMIT 20` |
| 02a | Customer: order detail, lines | `SELECT oi.product_id, p.title, oi.quantity, oi.unit_price FROM order_items oi JOIN products p ON p.id = oi.product_id WHERE oi.order_id = 250000` |
| 02b | Customer: order detail, payments | `SELECT id, amount, provider, status, created_at FROM payments WHERE order_id = 250000 ORDER BY created_at` |
| 03 | Seller dashboard: recent orders | `SELECT id, user_id, status, total, created_at FROM orders WHERE seller_id = 42 AND created_at >= '2025-12-01' ORDER BY created_at DESC LIMIT 50` |
| 04 | Storefront: category page | `SELECT id, title, price FROM products WHERE status = 'active' AND category_id = 6 ORDER BY price LIMIT 24` |
| 05 | Storefront: attribute filter | `SELECT id, title, price FROM products WHERE status = 'active' AND attributes @> '{"brand": "Voltix", "ram_gb": 16}'` |
| 06 | Storefront: tag filter | `SELECT id, title, price FROM products WHERE status = 'active' AND tags @> '{eco,limited-edition}' ORDER BY price LIMIT 24` |
| 07 | Admin: find user by e-mail | `SELECT id, email, full_name, is_active FROM users WHERE lower(email) = lower('Mia.Hill4242@Example.org')` |
| 08 | Worker: dequeue jobs | the inner `SELECT ... FOR UPDATE SKIP LOCKED` of `dequeue_jobs()` (Module 10), on a queue of 200,000 finished and 1,000 waiting jobs |
| 09 | Seller: who bought this product? | `SELECT o.id, o.user_id, o.created_at, oi.quantity FROM order_items oi JOIN orders o ON o.id = oi.order_id WHERE oi.product_id = 4242 ORDER BY o.created_at DESC` |
| 10 | Customer: address book | `SELECT id, label, city, country_code, is_default FROM addresses WHERE user_id = 4242` |
| 11 | Seller: delete a draft product | `DELETE FROM products WHERE id = 1` (product 1 is a draft that was never ordered) |

The exact SQL, including the job queue's data, is in the acceptance check [`solutions/check_hot_queries.sql`](solutions/check_hot_queries.sql).

**Budget.** After your migration, every hot query must:

- read **no table larger than 10,000 rows with a sequential scan**;
- touch **at most 500 shared buffers** (the `buffers` column of the check);
- run in **under 10 ms** on a warm cache (run the check twice; the second run counts).

And the whole set of indexes you add must stay **under 120 MB** (the reference set is 79 MB).

### 5. Write it as a migration

Put your indexes in `workspace/11/1102_schema_indexes.sql`, with a comment above each one naming the query or foreign key it serves. Use plain `CREATE INDEX` (the database is yours alone). Add a comment explaining what you would change to run it against a busy production database.

### 6. Document before and after

In `workspace/11/README.md` (or as a comment block at the top of your migration), record a table with, for each hot query: the plan's access method before and after, the execution time and buffers before and after, and the index that serves it. For at least two of your indexes, explain a design decision you made: column order, partial predicate, operator class, or why you did **not** add an index.

## Acceptance checks

1. **Hot queries.** Run the check (twice, use the second output):

   ```bash
   ./project/bin/psql.sh bazaar < modules/11-indexes/solutions/check_hot_queries.sql
   ```

   It prints one row per query with `ms`, `buffers`, `seq_scans` (tables read sequentially) and `indexes` (indexes used). Every row must have an empty `seq_scans`, `buffers` ≤ 500 and `ms` < 10. The script runs inside `BEGIN ... ROLLBACK`, so its job data and its `DELETE` don't persist, and it vacuums `jobs` at the end.

   With the reference indexes, the output on the course machine was:

   ```text
         query_label       |  ms  | buffers | seq_scans |                 indexes
   ------------------------+------+---------+-----------+-----------------------------------------
    01 my orders           | 0.20 |      21 |           | orders_user_id_created_at_idx
    02a order items        | 0.10 |      12 |           | order_items_pkey, products_pkey
    02b order payments     | 0.10 |       4 |           | payments_order_id_idx
    03 seller dashboard    | 0.36 |      46 |           | orders_seller_id_created_at_idx
    04 category browse     | 0.28 |      26 |           | products_active_category_id_price_idx
    05 attribute filter    | 1.80 |     164 |           | products_attributes_idx
    06 tag filter          | 3.92 |     399 |           | products_tags_idx
    07 admin email lookup  | 0.05 |       4 |           | users_email_lower_key
    08 jobs dequeue        | 0.07 |      14 |           | jobs_ready_idx
    09 buyers of a product | 0.72 |     153 |           | order_items_product_id_idx, orders_pkey
    10 address book        | 0.04 |       5 |           | addresses_user_id_idx
    11 delete a product    | 2.39 |      19 |           | products_pkey
   ```

   and before any index (your baseline should look similar): 40–100 ms and 1,800–10,300 buffers for queries 01, 02b, 03, 04, 05, 06, 09 and 10, and 81 ms for query 11.

2. **Foreign keys.** This query (the solution of exercise 7) must return **0 rows**:

   ```bash
   ./project/bin/psql.sh bazaar < modules/11-indexes/solutions/fk_audit.sql
   ```

3. **No redundant indexes.** This query (the solution of exercise 12) must return **0 rows**:

   ```bash
   ./project/bin/psql.sh bazaar < modules/11-indexes/solutions/redundant_indexes.sql
   ```

4. **Size budget.** The indexes you added total under 120 MB:

   ```sql
   SELECT pg_size_pretty(sum(pg_relation_size(indexrelid)))
   FROM pg_stat_user_indexes
   WHERE schemaname = 'bazaar'
     AND indexrelname NOT IN (SELECT conname FROM pg_constraint WHERE conindid <> 0)
     AND indexrelname NOT IN ('users_email_lower_key', 'addresses_one_default_per_user',
                              'seller_sales_daily_seller_id_day_key', 'jobs_ready_idx');
   ```

   The reference set gives 79 MB.

5. **No invalid indexes.** `SELECT count(*) FROM pg_index WHERE NOT indisvalid;` returns 0.

## Hints

- Section 21 of the theory summarises the reference design; try your own first.
- Several FKs are covered by a hot-query index: `(user_id, created_at DESC)` on orders also serves the FK on `user_id`.
- Query 05 has two conditions: one a GIN index can answer, one it can't. Query 06 has an `ORDER BY price LIMIT 24` that no GIN index can provide; think about which part of the work the index must do.
- Query 08 is already fast thanks to Module 10's partial `jobs_ready_idx`. Check that it is used, and think about what would happen without the `WHERE done_at IS NULL` predicate after a year of finished jobs.
- Query 11 looks fast in the "buffers" column even before indexing, because a plan's buffer count doesn't include the FK-check triggers. Its `ms` does.

## Reference solution

- [`project/db/1102_schema_indexes.sql`](../../project/db/1102_schema_indexes.sql): the 13 reference indexes, with a comment on each.
- [`solutions/check_hot_queries.sql`](solutions/check_hot_queries.sql), [`solutions/fk_audit.sql`](solutions/fk_audit.sql) and [`solutions/redundant_indexes.sql`](solutions/redundant_indexes.sql): the acceptance checks.
- Theory, section 21: the before-and-after table for the reference set.

After this milestone your database is at the reference state for the start of Module 12, except for `1201_schema_stats.sql`, which Module 12 adds. `./project/bin/reset.sh 12` gives the same state from scratch.
