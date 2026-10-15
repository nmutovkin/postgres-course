# Module 11: Solutions

<!-- nav:start -->
> [Theory](../README.md) · [Exercises](../exercises.md) · [Project](../project.md) · **Solutions**  
> [← 10 Transactions and Concurrency](../../10-transactions-concurrency/README.md) · [Course home](../../../README.md) · [12 Query Planning and Performance →](../../12-query-performance/README.md)
<!-- nav:end -->

All solutions were run on PostgreSQL 17.11 at state 12 (`reset.sh 12`: the large dataset plus `1102_schema_indexes.sql`), with `SET max_parallel_workers_per_gather = 0;`. Timings come from a 4-vCPU machine with the default configuration and will differ on yours; row counts, values and buffer counts are what to compare. Every solution drops the indexes it creates, unless it says otherwise.

Files in this directory:

| File | Purpose |
|---|---|
| [`check_hot_queries.sql`](check_hot_queries.sql) | The milestone's acceptance check (see [`../project.md`](../project.md)) |
| [`fk_audit.sql`](fk_audit.sql) | Exercise 7: foreign keys without a supporting index |
| [`redundant_indexes.sql`](redundant_indexes.sql) | Exercise 12: redundant indexes |
| [`ex13_stock_load.sql`](ex13_stock_load.sql) | Exercise 13: the `pgbench` write load |

---

<!-- toc:start -->
**Contents**

- [Exercise 1: An index inventory](#exercise-1-an-index-inventory)
- [Exercise 2: Predict the plan](#exercise-2-predict-the-plan)
- [Exercise 3: Leftmost prefix](#exercise-3-leftmost-prefix)
- [Exercise 4: The fulfilment queue](#exercise-4-the-fulfilment-queue)
- [Exercise 5: The seller's rating histogram](#exercise-5-the-sellers-rating-histogram)
- [Exercise 6: Admin autocomplete](#exercise-6-admin-autocomplete)
- [Exercise 7: Audit the foreign keys](#exercise-7-audit-the-foreign-keys)
- [Exercise 8: BRIN for payments](#exercise-8-brin-for-payments)
- [Exercise 9: Three slow queries](#exercise-9-three-slow-queries)
- [Exercise 10: What can a jsonb index answer?](#exercise-10-what-can-a-jsonb-index-answer)
- [Exercise 11: Revenue without the heap](#exercise-11-revenue-without-the-heap)
- [Exercise 12: Redundant indexes](#exercise-12-redundant-indexes)
- [Exercise 13: An index build under load](#exercise-13-an-index-build-under-load)

<!-- toc:end -->

## Exercise 1 ★: An index inventory

```sql
SELECT i.relname AS index_name,
       ix.indisunique AS is_unique,
       pg_size_pretty(pg_relation_size(i.oid)) AS size,
       pg_get_indexdef(i.oid) AS definition
FROM pg_index AS ix
JOIN pg_class AS i ON i.oid = ix.indexrelid
WHERE ix.indrelid = 'orders'::regclass
ORDER BY pg_relation_size(i.oid) DESC;
```

```text
           index_name            | is_unique |  size   |                                               definition
---------------------------------+-----------+---------+--------------------------------------------------------------------------------------------------------
 orders_seller_id_created_at_idx | f         | 15 MB   | CREATE INDEX orders_seller_id_created_at_idx ON bazaar.orders USING btree (seller_id, created_at DESC)
 orders_user_id_created_at_idx   | f         | 15 MB   | CREATE INDEX orders_user_id_created_at_idx ON bazaar.orders USING btree (user_id, created_at DESC)
 orders_pkey                     | t         | 11 MB   | CREATE UNIQUE INDEX orders_pkey ON bazaar.orders USING btree (id)
 orders_shipping_address_id_idx  | f         | 5528 kB | CREATE INDEX orders_shipping_address_id_idx ON bazaar.orders USING btree (shipping_address_id)
```

```sql
SELECT pg_size_pretty(pg_table_size('orders'))   AS table_size,
       pg_size_pretty(pg_indexes_size('orders')) AS index_size,
       round(pg_indexes_size('orders')::numeric / pg_table_size('orders'), 2) AS ratio;
```

```text
 table_size | index_size | ratio
------------+------------+-------
 55 MB      | 46 MB      |  0.83
```

**Reasoning.** `pg_index` is the catalog of indexes; joining `pg_class` gives the index's name and size. `ORDER BY pg_relation_size(...)` sorts by the raw byte count rather than by the pretty-printed text (which would sort `5528 kB` above `15 MB`). The `bigint` divisions need a cast to `numeric`, otherwise integer division returns 0.

Note the sizes. The two composite indexes (`bigint` + `timestamptz` = 16 bytes of key per entry, plus an 8-byte entry header) are bigger than the primary key, and `orders_shipping_address_id_idx` is half the primary key's size thanks to deduplication (about 10 orders share each address). Indexes almost as large as the table are normal for a table with four access paths.

**Alternative.** `\di+ orders*` in psql shows the same sizes; `\d orders` shows the definitions. The query is useful when you want to sort, filter or compare several tables.

---

## Exercise 2 ★: Predict the plan

```text
a) Index Scan using payments_order_id_idx on payments
     Index Cond: (order_id = 123)
b) Bitmap Heap Scan on orders
     Recheck Cond: (seller_id = 2)
     ->  Bitmap Index Scan on orders_seller_id_created_at_idx
c) Aggregate
     ->  Index Only Scan using orders_user_id_created_at_idx on orders
           Index Cond: ((user_id >= 1) AND (user_id <= 1000))
d) Bitmap Heap Scan on products
     Recheck Cond: (category_id = 22)
     ->  Bitmap Index Scan on products_category_id_idx
e) Limit
     ->  Index Scan using orders_seller_id_created_at_idx on orders
           Index Cond: (seller_id = 2)
```

**Reasoning.**

- **(a)** An order has one or two payments: very selective, so a plain index scan on the FK index from `1102`.
- **(b)** 46,196 rows (9% of orders) spread all over the table. Too many for one random heap access per row, too few to read everything: a bitmap scan, which visits each heap page once in physical order. The planner uses `(seller_id, created_at)` because `seller_id` is its leading column; there is no index on `seller_id` alone and none is needed.
- **(c)** `count(*)` needs no column values at all, only to know which rows match, and the condition is on `user_id`, the leading column of `orders_user_id_created_at_idx`. Since the visibility map is up to date after the load's `VACUUM`, an index-only scan never touches the heap.
- **(d)** 4,706 T-shirts. The partial `products_active_category_id_price_idx` can't be used because the query doesn't say `status = 'active'`, so the plain FK index `products_category_id_idx` serves it, via a bitmap.
- **(e)** The same 46,196 rows as (b), but `ORDER BY created_at DESC LIMIT 10` matches the index order after the equality on `seller_id`, so the scan stops after 10 entries. Note that the plan says `Index Scan`, not `Index Scan Backward`: the index was declared `created_at DESC`.

The common thread: the plan depends on the **number of matching rows**, on whether the **index order** helps, and on whether the **heap** must be visited at all.

---

## Exercise 3 ★: Leftmost prefix

| Query | Plan (abbreviated) | Rows | Buffers | Verdict |
|---|---|---:|---:|---|
| a) `seller_id = 7 AND created_at >= ...` | Bitmap Index Scan, both columns in `Index Cond` | 803 | 9 index + 406 heap | searches the index |
| b) `created_at >= '2025-12-24'` | Seq Scan | 5,581 | 7,095 | can't search; a full index scan wasn't worth it here |
| c) `seller_id IN (7, 8) ORDER BY created_at DESC LIMIT 5` | Sort (top-N) over a Bitmap Heap Scan | 23,997 read | 6,939 | searches, but must sort |
| d) `seller_id = 7 ORDER BY created_at LIMIT 5` | Index Scan Backward | 5 | 7 | searches, no sort |
| e) `seller_id >= 1900 AND created_at >= ...` | Bitmap Index Scan, both columns in `Index Cond` | 121 | 5 index + 111 heap | searches a range of sellers |

**Reasoning.**

- **(a)** Equality on the first column, range on the second: one contiguous run of entries, exactly what the index was designed for.
- **(b)** No condition on `seller_id`, so the index can't be searched. In the theory (section 5.3), a `count(*)` over one day got a full *index-only* scan because it needed no heap access. Here the query needs `id`, which isn't in the index, so a full index scan plus a heap access for every match is more expensive than a sequential scan. PostgreSQL 18's skip scan wouldn't help either: with 2,000 distinct sellers, there would be 2,000 descents.
- **(c)** The index returns seller 7's rows newest first, then seller 8's rows newest first. The `ORDER BY created_at DESC` wants the two *merged* by date, which the index order doesn't give, so PostgreSQL reads all 23,997 orders of both sellers and keeps the top 5 with a top-N heapsort: 58 ms.
- **(d)** Equality on `seller_id`, `ORDER BY created_at` ascending: the index is `DESC`, so it is read backward.
- **(e)** A range on the *first* column. All sellers from 1900 to 2000 form one contiguous run, and within it the index checks `created_at` entry by entry: it can't skip directly to December inside each seller. That's fine here because these sellers are tiny (101 sellers, a few hundred orders in all). On big sellers, "range on the first column" is a warning sign.

**Rewriting (c).** Ask each seller for its newest five, which the index answers directly, then merge ten rows:

```sql
SELECT id, created_at
FROM (
    (SELECT id, created_at FROM orders WHERE seller_id = 7 ORDER BY created_at DESC LIMIT 5)
    UNION ALL
    (SELECT id, created_at FROM orders WHERE seller_id = 8 ORDER BY created_at DESC LIMIT 5)
) AS newest
ORDER BY created_at DESC
LIMIT 5;
```

```text
 Limit (actual time=0.058..0.064 rows=5 loops=1)
   Buffers: shared hit=12 read=1
   ->  Merge Append (actual time=0.058..0.063 rows=5 loops=1)
         Sort Key: orders.created_at DESC
         ->  Limit (actual time=0.030..0.031 rows=1 loops=1)
               ->  Index Scan using orders_seller_id_created_at_idx on orders (actual time=0.030..0.030 rows=1 loops=1)
                     Index Cond: (seller_id = 7)
         ->  Limit (actual time=0.026..0.030 rows=5 loops=1)
               ->  Index Scan using orders_seller_id_created_at_idx on orders orders_1 (actual time=0.026..0.029 rows=5 loops=1)
                     Index Cond: (seller_id = 8)
 Execution Time: 0.087 ms
```

```text
   id   |       created_at
--------+------------------------
 499994 | 2025-12-31 23:36:46+00
 499982 | 2025-12-31 23:03:39+00
 499915 | 2025-12-31 21:02:08+00
 499894 | 2025-12-31 20:25:26+00
 499824 | 2025-12-31 19:08:19+00
```

13 buffers instead of 6,939. `Merge Append` merges the two already-sorted streams and stops after 5 rows; it even pulled only one row from seller 7's branch. For a variable list of sellers, the same idea works with `LATERAL` (Module 04), which reads 12 buffers:

```sql
SELECT o.id, o.created_at
FROM unnest('{7,8}'::bigint[]) AS s(seller_id)
CROSS JOIN LATERAL (
    SELECT id, created_at
    FROM orders
    WHERE orders.seller_id = s.seller_id
    ORDER BY created_at DESC
    LIMIT 5
) AS o
ORDER BY o.created_at DESC
LIMIT 5;
```

This "top-N per group, then merge" pattern is how feeds over many sources are built efficiently.

---

## Exercise 4 ★★: The fulfilment queue

Before:

```text
 Sort (actual time=33.744..33.793 rows=957 loops=1)
   Sort Key: created_at
   Buffers: shared hit=6856 read=514
   ->  Bitmap Heap Scan on orders (actual time=24.734..33.534 rows=957 loops=1)
         Recheck Cond: (seller_id = 1)
         Filter: (status = ANY ('{pending,paid,shipped}'::order_status[]))
         Rows Removed by Filter: 68848
         Heap Blocks: exact=7094
         ->  Bitmap Index Scan on orders_seller_id_created_at_idx (actual time=5.602..5.603 rows=69805 loops=1)
               Index Cond: (seller_id = 1)
```

The seller index finds all 69,805 of seller 1's orders, which live on 7,094 of the table's 7,095 pages, and then 98.6% of them are thrown away by the status filter.

```sql
CREATE INDEX orders_open_seller_id_created_at_idx
    ON orders (seller_id, created_at)
    WHERE status IN ('pending', 'paid', 'shipped');

SELECT pg_size_pretty(pg_relation_size('orders_open_seller_id_created_at_idx'));   -- 256 kB
```

After:

```text
 Sort (actual time=0.598..0.642 rows=957 loops=1)
   Sort Key: created_at
   Sort Method: quicksort  Memory: 77kB
   Buffers: shared hit=153 read=5
   ->  Bitmap Heap Scan on orders (actual time=0.097..0.444 rows=957 loops=1)
         Recheck Cond: ((seller_id = 1) AND (status = ANY ('{pending,paid,shipped}'::order_status[])))
         Heap Blocks: exact=153
         ->  Bitmap Index Scan on orders_open_seller_id_created_at_idx (actual time=0.068..0.068 rows=957 loops=1)
               Index Cond: (seller_id = 1)
```

From 34 ms and 7,370 buffers to 0.7 ms and 158 buffers, with an index of 256 kB instead of 15 MB. The planner picked a bitmap scan plus a sort over an ordered index scan because 957 rows on 153 pages are cheaper to read in physical order; for a `LIMIT 20` version, it switches to an ordered index scan.

**Other queries:**

```text
-- WHERE seller_id = 1 AND status = 'paid'
 Bitmap Heap Scan on orders
   Recheck Cond: ((seller_id = 1) AND (status = ANY ('{pending,paid,shipped}'::order_status[])))
   Filter: (status = 'paid'::order_status)
   ->  Bitmap Index Scan on orders_open_seller_id_created_at_idx

-- WHERE seller_id = 1 AND status IN ('pending', 'paid', 'shipped', 'delivered')
 Bitmap Heap Scan on orders
   Recheck Cond: (seller_id = 1)
   Filter: (status = ANY ('{pending,paid,shipped,delivered}'::order_status[]))
   ->  Bitmap Index Scan on orders_seller_id_created_at_idx
```

`status = 'paid'` **implies** `status IN ('pending', 'paid', 'shipped')`, and the planner can prove that, so the partial index is usable (with a filter for the narrower condition). A query that also wants delivered orders needs rows that aren't in the partial index, so it can't use it.

**Alternatives.** `(seller_id, created_at) WHERE status <> 'delivered' AND status <> 'cancelled' AND status <> 'refunded'` would work too, but queries would have to repeat that exact negative form. The positive list matches how the application asks. Some designs add a boolean `is_open` column; a partial index on the status values achieves the same without a column to maintain. Remember the caveat from the theory: with a prepared statement `WHERE status = ANY ($2)`, a generic plan can't prove the implication; keep the status list literal in the query.

```sql
DROP INDEX orders_open_seller_id_created_at_idx;
```

---

## Exercise 5 ★★: The seller's rating histogram

Before:

```text
 GroupAggregate (actual time=63.488..63.684 rows=5 loops=1)
   Buffers: shared hit=3875 read=1032
   ->  Sort (actual time=63.470..63.554 rows=1750 loops=1)
         ->  Hash Join (actual time=8.860..63.238 rows=1750 loops=1)
               Hash Cond: (r.product_id = p.id)
               ->  Seq Scan on reviews r (actual time=0.015..20.958 rows=292355 loops=1)
               ->  Hash (actual time=8.783..8.784 rows=1077 loops=1)
                     ->  Bitmap Heap Scan on products p (actual time=0.551..8.523 rows=1077 loops=1)
                           Recheck Cond: (seller_id = 25)
                           Heap Blocks: exact=1022
                           ->  Bitmap Index Scan on products_seller_id_sku_key (actual time=0.392..0.392 rows=1077 loops=1)
```

Two problems. The product side needs `id`, which isn't in `products_seller_id_sku_key (seller_id, sku)`, so 1,022 heap pages are read to get 1,077 ids. The review side has `reviews_product_id_user_id_key (product_id, user_id)`, which lacks `rating`; looking up 1,077 products through it would mean 1,750 heap visits, so the planner preferred a hash join over a full scan of `reviews`.

```sql
CREATE INDEX products_seller_id_id_idx     ON products (seller_id, id);
CREATE INDEX reviews_product_id_rating_idx ON reviews (product_id) INCLUDE (rating);
VACUUM products;
VACUUM reviews;
```

After:

```text
 Sort (actual time=6.096..6.098 rows=5 loops=1)
   Sort Key: r.rating
   Buffers: shared hit=2624 read=619
   ->  HashAggregate (actual time=6.088..6.091 rows=5 loops=1)
         Group Key: r.rating
         ->  Nested Loop (actual time=0.045..5.769 rows=1750 loops=1)
               ->  Index Only Scan using products_seller_id_id_idx on products p (actual time=0.030..0.191 rows=1077 loops=1)
                     Index Cond: (seller_id = 25)
                     Heap Fetches: 0
               ->  Index Only Scan using reviews_product_id_rating_idx on reviews r (actual time=0.005..0.005 rows=2 loops=1077)
                     Index Cond: (product_id = p.id)
                     Heap Fetches: 0
```

```text
 rating | count
--------+-------
      1 |    71
      2 |   123
      3 |   221
      4 |   456
      5 |   879
```

From 64 ms to 6 ms. The remaining cost is 1,077 index descents into `reviews` (3 buffers each); that's what a nested loop costs, and it's still ten times better than reading 292,355 reviews.

**Design choices.**

- `(seller_id, id)` versus `(seller_id) INCLUDE (id)`: equivalent for this query. The key version also supports `ORDER BY id` within a seller, which a seller's product list uses, so it's the better general-purpose choice. It makes no existing index redundant: `products_seller_id_sku_key` is unique and must stay.
- `(product_id) INCLUDE (rating)` versus `(product_id, rating)`: equivalent here. `INCLUDE` keeps the internal pages slightly smaller. Either way, the new index almost duplicates the unique `(product_id, user_id)` index. An alternative is `(product_id, user_id) INCLUDE (rating)` as the unique index itself, replacing the constraint's index (built concurrently and attached with `ADD CONSTRAINT ... UNIQUE USING INDEX`): one index instead of two, at the cost of a more involved migration.
- Is it worth two indexes (about 9 MB for reviews alone) for one dashboard tile? Only if the tile is shown often. A cheaper alternative is to cache the histogram, for example in a materialized view refreshed nightly (Module 09). Index-only scans are the right tool for *hot* queries.

```sql
DROP INDEX products_seller_id_id_idx, reviews_product_id_rating_idx;
```

---

## Exercise 6 ★★: Admin autocomplete

**Step 1.** The database collation is `en_US.utf8`, so `users_email_lower_key` can't serve `LIKE`. A pattern operator class can:

```sql
CREATE INDEX users_email_lower_pattern_idx ON users (lower(email) text_pattern_ops);

EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT id, email FROM users WHERE lower(email) LIKE 'olumide.s%';
```

```text
 Bitmap Heap Scan on users (actual time=0.060..0.368 rows=134 loops=1)
   Filter: (lower((email)::text) ~~ 'olumide.s%'::text)
   Heap Blocks: exact=132
   Buffers: shared hit=135 read=1
   ->  Bitmap Index Scan on users_email_lower_pattern_idx (actual time=0.035..0.036 rows=134 loops=1)
         Index Cond: ((lower((email)::text) ~>=~ 'olumide.s'::text) AND (lower((email)::text) ~<~ 'olumide.t'::text))
```

**Step 2.** The full query, with `ORDER BY lower(email) LIMIT 10`:

```text
 Limit  (cost=0.42..100.92 rows=10 width=72) (actual time=60.670..60.682 rows=10 loops=1)
   Buffers: shared hit=76168
   ->  Index Scan using users_email_lower_key on users  (cost=0.42..10150.91 rows=1010 width=72) (actual time=60.668..60.679 rows=10 loops=1)
         Filter: (lower((email)::text) ~~ 'olumide.s%'::text)
         Rows Removed by Filter: 75935
```

The planner switched to the *other* index, `users_email_lower_key`, because it returns rows in `ORDER BY lower(email)` order in the database's collation, which `text_pattern_ops` can't provide. The plan is "walk all e-mails alphabetically from `a` and stop at the 10th match". The planner estimated 1,010 matches (it has no good selectivity estimate for `LIKE` on an expression), and assumed they are spread evenly through the alphabet: then the 10th match would come after about 1% of the index, cost 100. In reality there are 134 matches, all bunched together at `olumide.s`, about 76% of the way through the alphabet. The scan read 76,000 entries and as many heap pages. This is the classic **`ORDER BY ... LIMIT` trap**: an index that avoids a sort plus a misestimated filter. Module 12 discusses it in depth.

**Step 3.** Make the sort order match the index that can search. Option A: order by the pattern operator class's own comparison:

```sql
SELECT id, email
FROM users
WHERE lower(email) LIKE 'olumide.s%'
ORDER BY lower(email) USING ~<~
LIMIT 10;
```

```text
 Limit (actual time=0.066..0.100 rows=10 loops=1)
   Buffers: shared hit=10 read=3
   ->  Index Scan using users_email_lower_pattern_idx on users (actual time=0.065..0.097 rows=10 loops=1)
         Index Cond: ((lower((email)::text) ~>=~ 'olumide.s'::text) AND (lower((email)::text) ~<~ 'olumide.t'::text))
         Filter: (lower((email)::text) ~~ 'olumide.s%'::text)
```

```text
  id   |                email
-------+--------------------------------------
  1532 | olumide.santos1532@inbox.example.net
 16034 | olumide.santos16034@mail.example.com
 20334 | olumide.santos20334@mail.example.com
 ...
```

Option B: an index in the `C` collation, queried in the `C` collation:

```sql
DROP INDEX users_email_lower_pattern_idx;
CREATE INDEX users_email_lower_c_idx ON users (lower(email) COLLATE "C");

SELECT id, email
FROM users
WHERE lower(email) COLLATE "C" LIKE 'olumide.s%'
ORDER BY lower(email) COLLATE "C"
LIMIT 10;
```

```text
 Limit (actual time=0.054..0.074 rows=10 loops=1)
   Buffers: shared hit=10 read=3
   ->  Index Scan using users_email_lower_c_idx on users (actual time=0.053..0.071 rows=10 loops=1)
         Index Cond: (((lower((email)::text))::text >= 'olumide.s'::text) AND ((lower((email)::text))::text < 'olumide.t'::text))
```

Both read 13 buffers. For lower-case ASCII e-mail addresses, byte order and linguistic order agree, so the user sees the same list. For names with accents or mixed case, they would differ, and `C` order would look odd to users (`Zoë` before `adam`).

A third option is to sort *after* limiting the search: wrap the `WHERE` in a subquery or CTE marked `MATERIALIZED` and order its result. It works because there are few matches per prefix, but it breaks down for a one-letter prefix with 10,000 matches.

**Which is better?** Option B is cleaner in SQL that humans write; option A keeps one index usable for both `=` and `LIKE`. Note the cost of either: a third index on `users.email` (about 7 MB). If autocomplete needs infix matching or typo tolerance, Module 13's trigram index replaces all of this.

```sql
DROP INDEX users_email_lower_c_idx;
```

---

## Exercise 7 ★★: Audit the foreign keys

[`fk_audit.sql`](fk_audit.sql):

```sql
SELECT c.conrelid::regclass AS table_name,
       c.conname AS fk_name,
       array_agg(a.attname ORDER BY k.ord) AS fk_columns,
       pg_size_pretty(pg_relation_size(c.conrelid)) AS table_size
FROM pg_constraint AS c
CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
JOIN pg_attribute AS a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
WHERE c.contype = 'f'
  AND c.connamespace = 'bazaar'::regnamespace
  AND NOT EXISTS (
        SELECT 1
        FROM pg_index AS ix
        WHERE ix.indrelid = c.conrelid
          AND ix.indpred IS NULL                       -- a partial index cannot serve every lookup
          AND (string_to_array(ix.indkey::text, ' ')::int2[])[1:cardinality(c.conkey)]
              @> c.conkey                              -- the FK columns are the leading columns,
          AND (string_to_array(ix.indkey::text, ' ')::int2[])[1:cardinality(c.conkey)]
              <@ c.conkey                              -- in any order
      )
GROUP BY c.conrelid, c.conname
ORDER BY pg_relation_size(c.conrelid) DESC, fk_name;
```

At state 12: `(0 rows)`. With the module's indexes dropped inside a transaction:

```text
 table_name  |             fk_name             |      fk_columns       | table_size
-------------+---------------------------------+-----------------------+------------
 products    | products_category_id_fkey       | {category_id}         | 81 MB
 order_items | order_items_product_id_fkey     | {product_id}          | 73 MB
 orders      | orders_seller_id_fkey           | {seller_id}           | 55 MB
 orders      | orders_shipping_address_id_fkey | {shipping_address_id} | 55 MB
 orders      | orders_user_id_fkey             | {user_id}             | 55 MB
 payments    | payments_order_id_fkey          | {order_id}            | 48 MB
 reviews     | reviews_user_id_fkey            | {user_id}             | 30 MB
 addresses   | addresses_user_id_fkey          | {user_id}             | 14 MB
 cart_items  | cart_items_product_id_fkey      | {product_id}          | 1168 kB
 categories  | categories_parent_id_fkey       | {parent_id}           | 8192 bytes
```

**Reasoning.**

- `conkey` is an ordinary `int2[]` of the FK's column numbers. `indkey` is an `int2vector`, a legacy type whose subscripts start at 0; converting it through text gives a normal 1-based array that can be sliced.
- The slice `[1:cardinality(conkey)]` takes the index's first *n* columns. `@>` and `<@` together mean "the same set", so an FK on `(a, b)` is covered by an index on `(b, a, c)`. For the FK check, both columns are compared with `=`, so their order in the index doesn't matter.
- Partial indexes are excluded: `addresses_one_default_per_user (user_id) WHERE is_default` holds only default addresses, so it can't find a user's other addresses. That's why `1102` adds a separate `addresses_user_id_idx`.
- Expression indexes need no special treatment: their expression columns have attribute number 0 in `indkey`, which never matches an FK column.
- The query accepts any index *type*. `promotions.product_id` is covered by the GiST index behind the exclusion constraint, whose leading column is `product_id`, and that is enough for the FK check (the theory's delete demo shows 1.6 ms for 100 checks).

The result at the module's start matches the list in `1102_schema_indexes.sql`, which also documents the FKs that were already covered.

---

## Exercise 8 ★★: BRIN for payments

```sql
SELECT tablename, attname, round(correlation::numeric, 4) AS correlation
FROM pg_stats
WHERE schemaname = 'bazaar' AND attname = 'created_at'
ORDER BY tablename;
```

```text
 tablename  |  attname   | correlation
------------+------------+-------------
 addresses  | created_at |      0.6995
 categories | created_at |      1.0000
 orders     | created_at |      0.9997
 payments   | created_at |      1.0000
 products   | created_at |      0.9998
 promotions | created_at |      1.0000
 reviews    | created_at |      0.9560
 sellers    | created_at |      1.0000
 users      | created_at |      1.0000
```

`payments.created_at` has a correlation of 1.0000: the rows are physically in time order, so each 128-page block range covers a short time span. `reviews` at 0.956 is still a reasonable candidate (a test showed 411 lossy pages for one day, against 3,867 for the whole table). `addresses` at 0.70 is a poor one: Work addresses were added after the users' Home addresses, so ranges are wide.

```sql
CREATE INDEX payments_created_at_brin  ON payments USING brin (created_at);
CREATE INDEX payments_created_at_btree ON payments (created_at);
```

```text
          relname          | pg_size_pretty
---------------------------+----------------
 payments_created_at_brin  | 24 kB
 payments_created_at_btree | 12 MB
```

With only the BRIN index:

```text
 Sort (actual time=7.742..7.744 rows=3 loops=1)
   Sort Key: provider
   Buffers: shared hit=8 read=256
   ->  HashAggregate (actual time=7.719..7.722 rows=3 loops=1)
         Group Key: provider
         ->  Bitmap Heap Scan on payments (actual time=2.099..6.615 rows=3278 loops=1)
               Recheck Cond: ((created_at >= '2025-11-28 00:00:00+00'::timestamp with time zone) AND (created_at < '2025-11-29 00:00:00+00'::timestamp with time zone))
               Rows Removed by Index Recheck: 18918
               Filter: (status = 'succeeded'::payment_status)
               Rows Removed by Filter: 332
               Heap Blocks: lossy=256
               ->  Bitmap Index Scan on payments_created_at_brin (actual time=0.032..0.033 rows=2560 loops=1)
```

```text
 provider | count |    sum
----------+-------+------------
 adyen    |   439 |  252243.21
 paypal   |   940 |  697004.92
 stripe   |  1899 | 1342193.91
```

**Reasoning.** The BRIN index can only say "pages 0–127 might contain matching rows". Its bitmap is therefore **lossy**: it marks whole pages, not individual tuples (`Heap Blocks: lossy=256`, two block ranges). Every row on those pages is rechecked against the condition, and 18,918 that fall outside the day are discarded (`Rows Removed by Index Recheck`). Then the ordinary `Filter` on `status` removes 332 failed or pending payments. 264 buffers instead of the 6,101 of a sequential scan (52 ms without any index on this run): an excellent deal for a 24 kB index. The B-tree would read fewer heap pages, but at 500 times the size and with maintenance on every insert.

Black Friday's 3,278 succeeded payments spanning only 256 pages also shows why BRIN is so effective for append-only data: a day's rows sit together physically.

```sql
DROP INDEX payments_created_at_brin, payments_created_at_btree;
```

---

## Exercise 9 ★★: Three slow queries

**(a) Function on the column.**

```text
 Bitmap Heap Scan on orders (actual time=60.998..64.113 rows=443 loops=1)
   Recheck Cond: (seller_id = 1)
   Filter: (date_trunc('day'::text, (created_at AT TIME ZONE 'UTC'::text)) = '2025-11-28 00:00:00'::timestamp without time zone)
   Rows Removed by Filter: 69362
   ->  Bitmap Index Scan on orders_seller_id_created_at_idx (actual time=5.039..5.040 rows=69805 loops=1)
         Index Cond: (seller_id = 1)
```

The index is used for `seller_id` only; the date condition is a filter applied to all 69,805 of seller 1's orders, because the index stores `created_at`, not `date_trunc('day', created_at AT TIME ZONE 'UTC')`. Rewrite the day as a half-open range on the bare column, with explicit UTC offsets so the result doesn't depend on the session's `TimeZone`:

```sql
SELECT id, total
FROM orders
WHERE seller_id = 1
  AND created_at >= timestamptz '2025-11-28 00:00+00'
  AND created_at <  timestamptz '2025-11-29 00:00+00';
```

```text
 Bitmap Heap Scan on orders (actual time=0.043..0.155 rows=443 loops=1)
   Recheck Cond: ((seller_id = 1) AND (created_at >= '2025-11-28 00:00:00+00'::timestamp with time zone) AND (created_at < '2025-11-29 00:00:00+00'::timestamp with time zone))
   Heap Blocks: exact=51
   Buffers: shared hit=55
   ->  Bitmap Index Scan on orders_seller_id_created_at_idx (actual time=0.032..0.032 rows=443 loops=1)
```

443 rows, `sum(total)` = 313,915.35 in both versions. 64 ms became 0.2 ms. Use `>=` and `<`, never `BETWEEN '2025-11-28' AND '2025-11-28 23:59:59'`, which misses the last second's fractions.

**(b) Implicit cast of the column.**

```text
 Seq Scan on payments (actual time=39.139..72.168 rows=2 loops=1)
   Filter: ((order_id)::numeric = 250000.0)
   Rows Removed by Filter: 536814
```

`250000.0` is a `numeric` literal. There is no `bigint = numeric` operator in the B-tree's operator family, and `numeric` doesn't convert losslessly to `bigint`, so PostgreSQL converts the *column* to `numeric`, and the index on `order_id` can't be used. Send an integer: `WHERE order_id = 250000`, which reads 4 buffers. In application code, this means binding the parameter with an integer type (psycopg sends Python `int` as an integer and `Decimal` as `numeric`; Module 22 covers type adaptation). Casting the parameter, `WHERE order_id = $1::bigint`, also works.

**(c) `OR` across a join.**

```text
 Unique (actual time=722.842..722.862 rows=45 loops=1)
   Buffers: shared hit=6848 read=9569 written=32, temp read=6191 written=6191
   ->  Sort (actual time=722.841..722.848 rows=65 loops=1)
         ->  Hash Join (actual time=150.160..722.740 rows=65 loops=1)
               Hash Cond: (oi.order_id = o.id)
               Join Filter: ((o.user_id = 4242) OR (oi.product_id = 4242))
               Rows Removed by Join Filter: 1266863
               ->  Seq Scan on order_items oi (actual time=0.014..141.426 rows=1266928 loops=1)
               ->  Hash (actual time=145.413..145.414 rows=500000 loops=1)
                     Buckets: 131072  Batches: 4  Memory Usage: 7872kB
                     ->  Seq Scan on orders o (actual time=0.007..58.867 rows=500000 loops=1)
```

Neither half of the `OR` can be applied to its table alone: an order line with `product_id = 4242` qualifies even if its order isn't user 4242's, and vice versa. So the condition can only be evaluated *after* joining all 1.27 million order lines with all 500,000 orders, and the hash of orders doesn't fit in `work_mem` (`Batches: 4`, `temp written`). Split the `OR` into two indexable queries and combine them with `UNION`, which also removes duplicates:

```sql
SELECT o.id, o.created_at
FROM orders AS o
WHERE o.user_id = 4242
UNION
SELECT o.id, o.created_at
FROM orders AS o
JOIN order_items AS oi ON oi.order_id = o.id
WHERE oi.product_id = 4242;
```

```text
 Unique (actual time=0.613..0.625 rows=45 loops=1)
   Buffers: shared hit=111 read=60
   ->  Sort (actual time=0.612..0.616 rows=45 loops=1)
         ->  Append (actual time=0.044..0.596 rows=45 loops=1)
               ->  Index Scan using orders_user_id_created_at_idx on orders o (actual time=0.043..0.069 rows=15 loops=1)
                     Index Cond: (user_id = 4242)
               ->  Nested Loop (actual time=0.059..0.519 rows=30 loops=1)
                     ->  Index Scan using order_items_product_id_idx on order_items oi (actual time=0.043..0.237 rows=30 loops=1)
                           Index Cond: (product_id = 4242)
                     ->  Index Scan using orders_pkey on orders o_1 (actual time=0.009..0.009 rows=1 loops=30)
                           Index Cond: (id = oi.order_id)
```

45 rows both ways; 723 ms became 0.67 ms. Note that the original's `DISTINCT` was needed because an order of user 4242 with three lines appears three times in the join; the first branch of the rewrite doesn't join `order_items` at all, which is both faster and more correct. `UNION` (not `UNION ALL`) removes orders that match both branches.

---

## Exercise 10 ★★: What can a jsonb index answer?

| Predicate | Uses `products_attributes_idx`? | Why |
|---|---|---|
| a) `attributes @> '{"brand": "Voltix"}'` | yes | containment is `jsonb_path_ops`'s core operator |
| b) `attributes ->> 'brand' = 'Voltix'` | no | `->>` returns `text`; `=` on text is not a GIN operator |
| c) `attributes ? 'isbn'` | no | key existence is supported only by `jsonb_ops` |
| d) `attributes @> '{"storage": {"type": "ssd"}}'` | yes | nested containment: the path `storage.type` is hashed with its value |
| e) `attributes @? '$.ram_gb ? (@ >= 32)'` | no | GIN can only extract equality conditions from jsonpath; a range gives it nothing to look up |

```text
-- e)
 Seq Scan on products (actual rows=1666 loops=1)
   Filter: (attributes @? '$."ram_gb"?(@ >= 32)'::jsonpath)
   Rows Removed by Filter: 198334
 Execution Time: 89.216 ms
```

(b) is the important lesson: it means the same as (a) to a human, but it's a different operator. Applications should write `@>` when they want the GIN index.

**The 64 GB query.** A range on one key needs a B-tree on that key's value, cast to the right type so that `>=` compares numbers (`'8' > '64'` as text!):

```sql
CREATE INDEX products_ram_gb_idx
    ON products (((attributes ->> 'ram_gb')::int))
    WHERE status = 'active';
ANALYZE products;

EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT count(*)
FROM products
WHERE status = 'active' AND (attributes ->> 'ram_gb')::int >= 64;
```

```text
 Aggregate (actual time=1.158..1.159 rows=1 loops=1)
   Buffers: shared hit=686 read=3
   ->  Bitmap Heap Scan on products (actual time=0.145..1.113 rows=705 loops=1)
         Recheck Cond: ((((attributes ->> 'ram_gb'::text))::integer >= 64) AND (status = 'active'::product_status))
         Heap Blocks: exact=686
         ->  Bitmap Index Scan on products_ram_gb_idx (actual time=0.066..0.066 rows=705 loops=1)
               Index Cond: (((attributes ->> 'ram_gb'::text))::integer >= 64)
               Buffers: shared read=3
```

705 products, 1.2 ms, against 61 ms and 10,315 buffers for the sequential scan. The index is 1.2 MB. Most of its entries are NULL (products without `ram_gb`); `WHERE status = 'active' AND category_id = 3` as the predicate would shrink it to the 3,645 active laptops, but then every query would have to include `category_id = 3`. Only laptops have `ram_gb` in this dataset, so the storefront's laptop filter would naturally say so.

**What could go wrong?** The cast `::int` runs for every row at index build time and on every insert or update. A product with `"ram_gb": "lots"` would make `INSERT` and `UPDATE` fail with `invalid input syntax for type integer`, from an index the seller has never heard of. (Casting the `jsonb` value directly, `(attributes -> 'ram_gb')::int`, fails too: `cannot cast jsonb string to type integer`.) Guard the data instead, so that the bad value is rejected with a message that names the real rule: a `CHECK` constraint such as `CHECK (NOT attributes ? 'ram_gb' OR jsonb_typeof(attributes -> 'ram_gb') = 'number')` (and remember that `16.5` is a number too, which `::int` would reject). The general rule: an expression index on semi-structured data turns an implicit schema into an enforced one. That's often good, but decide it consciously.

```sql
DROP INDEX products_ram_gb_idx;
```

---

## Exercise 11 ★★★: Revenue without the heap

The query reads `seller_id`, `created_at` (key columns of the existing index), `status` and `total`. Covering it means adding `status` and `total` as `INCLUDE` columns. To avoid adding a fifth index, **replace** the existing one, without blocking order taking:

```sql
CREATE INDEX CONCURRENTLY orders_seller_id_created_at_cov_idx
    ON orders (seller_id, created_at DESC) INCLUDE (status, total);
DROP INDEX CONCURRENTLY orders_seller_id_created_at_idx;
ALTER INDEX orders_seller_id_created_at_cov_idx RENAME TO orders_seller_id_created_at_idx;
```

```text
 GroupAggregate (actual time=0.537..0.637 rows=12 loops=1)
   Group Key: (date_trunc('month'::text, created_at, 'UTC'::text))
   Buffers: shared hit=11
   ->  Sort (actual time=0.517..0.547 rows=633 loops=1)
         Sort Key: (date_trunc('month'::text, created_at, 'UTC'::text))
         ->  Index Only Scan using orders_seller_id_created_at_idx on orders (actual time=0.102..0.440 rows=633 loops=1)
               Index Cond: ((seller_id = 42) AND (created_at >= '2025-01-01 00:00:00+00'::timestamp with time zone) AND (created_at < '2026-01-01 00:00:00+00'::timestamp with time zone))
               Filter: (status = ANY ('{paid,shipped,delivered}'::order_status[]))
               Rows Removed by Filter: 53
               Heap Fetches: 0
```

```text
         month          | orders | revenue
------------------------+--------+----------
 2025-01-01 00:00:00+00 |     37 | 11278.93
 2025-02-01 00:00:00+00 |     29 | 11910.00
 ...
 2025-11-01 00:00:00+00 |     72 | 30730.18
 2025-12-01 00:00:00+00 |     89 | 32744.76
```

11 buffers instead of 634 (3.1 ms before, 0.7 ms after). `orders` still has 4 indexes, and the dashboard's "latest 50" query still gets `Limit -> Index Scan using orders_seller_id_created_at_idx` with no sort. Note that `status` is checked as a `Filter` *inside* the index-only scan, on the included value, without visiting the heap.

**Why this sequence.** `CREATE INDEX CONCURRENTLY` doesn't block writes; between the build and the drop, both indexes exist and are maintained (a short period of double write cost). `DROP INDEX CONCURRENTLY` waits for running queries that use the old index instead of blocking new ones. The rename is a quick catalog change. Doing all three in one transaction is impossible (`CONCURRENTLY` refuses), which is why migration tools need a "no transaction" mode for this.

**What it costs.** The index grows from 15 MB to 24 MB, and updates of `status` or `total` can no longer be HOT, so every status transition of an order (pending → paid → shipped → delivered) now writes new entries into all four indexes. On Bazaar, each order changes status about three times in its life. Whether 0.7 ms instead of 3 ms on a dashboard tile is worth that is a real trade-off; on a dashboard opened thousands of times a minute, it probably is.

`date_trunc('month', created_at, 'UTC')` (the three-argument form, PostgreSQL 12+) truncates in an explicit time zone, so months don't depend on the session's `TimeZone` setting.

To restore the reference definition afterwards:

```sql
CREATE INDEX CONCURRENTLY orders_seller_id_created_at_ref ON orders (seller_id, created_at DESC);
DROP INDEX CONCURRENTLY orders_seller_id_created_at_idx;
ALTER INDEX orders_seller_id_created_at_ref RENAME TO orders_seller_id_created_at_idx;
```

---

## Exercise 12 ★★★: Redundant indexes

[`redundant_indexes.sql`](redundant_indexes.sql):

```sql
WITH idx AS (
    SELECT ix.indexrelid,
           ix.indrelid,
           ix.indisunique,
           ix.indpred IS NOT NULL AS is_partial,
           ix.indexprs IS NOT NULL AS has_expressions,
           c.relam,
           string_to_array(ix.indkey::text, ' ')::int2[]   AS cols,
           string_to_array(ix.indclass::text, ' ')::oid[]  AS opclasses,
           string_to_array(ix.indoption::text, ' ')::int2[] AS options,
           ix.indnkeyatts
    FROM pg_index AS ix
    JOIN pg_class AS c ON c.oid = ix.indexrelid
    WHERE c.relnamespace = 'bazaar'::regnamespace
)
SELECT a.indrelid::regclass AS table_name,
       a.indexrelid::regclass AS redundant_index,
       b.indexrelid::regclass AS covered_by,
       pg_size_pretty(pg_relation_size(a.indexrelid)) AS wasted
FROM idx AS a
JOIN idx AS b
  ON  b.indrelid = a.indrelid
  AND b.indexrelid <> a.indexrelid
  AND b.relam = a.relam
WHERE NOT a.indisunique                    -- a unique index enforces a rule: never "redundant"
  AND NOT a.is_partial AND NOT b.is_partial
  AND NOT a.has_expressions AND NOT b.has_expressions
  AND a.indnkeyatts <= b.indnkeyatts
  AND a.cols[1:a.indnkeyatts]      = b.cols[1:a.indnkeyatts]       -- same leading key columns,
  AND a.opclasses[1:a.indnkeyatts] = b.opclasses[1:a.indnkeyatts]  -- same operator classes
  AND a.options[1:a.indnkeyatts]   = b.options[1:a.indnkeyatts]    -- same ASC/DESC, NULLS
  AND (a.indnkeyatts < b.indnkeyatts OR a.indexrelid > b.indexrelid)  -- exact duplicates: report one
ORDER BY pg_relation_size(a.indexrelid) DESC;
```

State 12: `(0 rows)`. Inside the test transaction:

```text
 table_name |        redundant_index         |           covered_by           | wasted
------------+--------------------------------+--------------------------------+---------
 orders     | orders_user_id_created_at_idx2 | orders_user_id_created_at_idx  | 15 MB
 orders     | orders_user_id_idx             | orders_user_id_created_at_idx  | 5296 kB
 orders     | orders_user_id_idx             | orders_user_id_created_at_idx2 | 5296 kB
 products   | products_seller_id_idx         | products_seller_id_sku_key     | 1424 kB
```

**Reasoning.**

- `indnkeyatts` counts only key columns. `INCLUDE` columns (which follow them in `indkey`) don't provide order or searchability, so they are ignored for the prefix comparison. An index `(a) INCLUDE (b)` is reported as covered by `(a, c)`; whether `(a, c)` can replace it for index-only scans needing `b` is a question the query can't answer, so review each finding.
- The *covering* index may be unique (`products_seller_id_sku_key` makes `products (seller_id)` redundant), but the *redundant* one may not, since dropping a unique index drops a rule.
- Operator classes matter: `(email text_pattern_ops)` is not interchangeable with `(email)`, as section 16 showed. Comparing `indclass` catches that.
- Exact duplicates appear twice in a naive self-join (each covers the other). The condition `a.indexrelid > b.indexrelid` reports only one direction, keeping the older index (lower OID).

**What about `orders_seller_asc`?** `(seller_id, created_at)` versus `(seller_id, created_at DESC)` differ in `indoption`, so the strict rule keeps it. In practice, it *is* redundant for almost every query: a B-tree can be read backward, so `ORDER BY created_at` with a fixed seller is served by either. The two differ only for mixed-direction orderings such as `ORDER BY seller_id, created_at` over several sellers. A practical tool would report it as "probably redundant, check the queries". Treat any automated report as a list of candidates, and confirm with `pg_stat_user_indexes` and the actual workload before dropping.

---

## Exercise 13 ★★★: An index build under load

**Steps 1–2.** Terminal 1 runs the load; terminal 2 builds the first index:

```sql
CREATE INDEX CONCURRENTLY inventory_available_idx ON inventory ((quantity - reserved));
```

It completes in about 0.3 s on 200,000 rows, while `pgbench` keeps running. In terminal 3: `BEGIN; SELECT count(*) FROM categories;` (and leave it open). Then in terminal 2:

```sql
SET statement_timeout = '5s';
CREATE INDEX CONCURRENTLY inventory_low_stock_idx
    ON inventory (product_id)
    WHERE quantity - reserved < 5;
```

It hangs.

**Step 3.** From another session:

```sql
SELECT phase, current_locker_pid, lockers_total, lockers_done
FROM pg_stat_progress_create_index;
```

```text
           phase           | current_locker_pid | lockers_total | lockers_done
---------------------------+--------------------+---------------+--------------
 waiting for old snapshots |               1145 |             1 |            0
```

```sql
SELECT pid, state, wait_event_type, wait_event, now() - xact_start AS xact_age, left(query, 60) AS query
FROM pg_stat_activity
WHERE datname = 'bazaar' AND pid <> pg_backend_pid() AND state <> 'idle';
```

```text
 pid  | state  | wait_event_type | wait_event |    xact_age     |                            query
------+--------+-----------------+------------+-----------------+--------------------------------------------------------------
 1145 | active | Timeout         | PgSleep    | 00:00:03.054681 | SELECT pg_sleep(10)
 1152 | active | Lock            | virtualxid | 00:00:02.011126 | CREATE INDEX CONCURRENTLY inventory_low_stock_idx ON invento
```

(In this recorded run the "forgotten" transaction was simulated with a `pg_sleep`; an interactive session shows `idle in transaction` instead.) The build has finished scanning and is in its last waiting phase: it must wait until every transaction whose snapshot might not see the new index's rows is gone. pid 1145 holds an old snapshot, so the builder waits on that transaction's virtual transaction id (`wait_event = virtualxid`). It doesn't matter that pid 1145 only read `categories`, a different table: snapshots are database-wide. After 5 seconds:

```text
ERROR:  canceling statement due to statement timeout
```

and `\d inventory` shows `"inventory_low_stock_idx" btree (product_id) WHERE (quantity - reserved) < 5 INVALID`.

**Step 4.** End the forgotten transaction (`COMMIT` in terminal 3, or `SELECT pg_terminate_backend(1145)` if it's not yours to commit), then:

```sql
DROP INDEX CONCURRENTLY inventory_low_stock_idx;
CREATE INDEX CONCURRENTLY inventory_low_stock_idx
    ON inventory (product_id)
    WHERE quantity - reserved < 5;

SELECT count(*) FROM pg_index WHERE NOT indisvalid;   -- 0
```

`pgbench` finishes with `number of failed transactions: 0` (about 6,000 transactions per second on this machine): neither build blocked a single stock update.

**Lessons.**

- A concurrent build can be held up by **any** long transaction in the database, including idle ones from a forgotten psql session or a connection pool that didn't commit. Before a big build, check `pg_stat_activity` for old `xact_start` values. `idle_in_transaction_session_timeout` (Module 17) protects against the forgotten ones.
- `statement_timeout` is a good safety net for migrations, but on a concurrent build it leaves an invalid index. Your migration must check `indisvalid` and drop-and-retry, and it must not use `CREATE INDEX CONCURRENTLY IF NOT EXISTS` as the retry mechanism.
- The expression `quantity - reserved` appears in two forms here: as an index *key* (supports `ORDER BY quantity - reserved` and ranges on available stock) and in a partial index *predicate* (a tiny index of products running low). The second is the better fit for a "low stock" alert that runs every minute.

To clean up: `DROP INDEX inventory_available_idx, inventory_low_stock_idx;`. The `pgbench` run changed `inventory.quantity` for many products; `reset.sh 12` restores them if needed.
