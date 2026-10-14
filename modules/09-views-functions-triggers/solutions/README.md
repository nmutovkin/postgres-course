# Module 09: Solutions

<!-- nav:start -->
> [Theory](../README.md) · [Exercises](../exercises.md) · [Project](../project.md) · **Solutions**  
> [← 08 Beyond Scalars](../../08-beyond-scalars/README.md) · [Course home](../../../README.md) · [10 Transactions and Concurrency →](../../10-transactions-concurrency/README.md)
<!-- nav:end -->

All solutions were run on PostgreSQL 17 against `./project/bin/reset.sh 10`, in order, in one database. The complete script is [`exercises.sql`](exercises.sql):

```bash
./project/bin/reset.sh 10
docker exec -i course-pg psql -X -U postgres -d bazaar < modules/09-views-functions-triggers/solutions/exercises.sql
```

Exercise 10 archives (deletes) orders, so reset afterwards.

---

<!-- toc:start -->
**Contents**

- [Exercise 1 ★: `effective_price()`](#exercise-1--effective_price)
- [Exercise 2 ★: A view on a view](#exercise-2--a-view-on-a-view)
- [Exercise 3 ★: No repricing archived products](#exercise-3--no-repricing-archived-products)
- [Exercise 4 ★★: Price history that keeps itself](#exercise-4--price-history-that-keeps-itself)
- [Exercise 5 ★★: Breadcrumbs](#exercise-5--breadcrumbs)
- [Exercise 6 ★★: Row counts of every table](#exercise-6--row-counts-of-every-table)
- [Exercise 7 ★★: A ratings materialized view](#exercise-7--a-ratings-materialized-view)
- [Exercise 8 ★★: A forgiving price import](#exercise-8--a-forgiving-price-import)
- [Exercise 9 ★★: What happened to this column?](#exercise-9--what-happened-to-this-column)
- [Exercise 10 ★★★: Archive cancelled orders in batches](#exercise-10--archive-cancelled-orders-in-batches)
- [Exercise 11 ★★★: Order status drives inventory](#exercise-11--order-status-drives-inventory)
- [Exercise 12 ★★★: Row-level versus statement-level](#exercise-12--row-level-versus-statement-level)

<!-- toc:end -->

## Exercise 1 ★: `effective_price()`

```sql
CREATE FUNCTION effective_price(p_product_id bigint, p_at timestamptz DEFAULT now())
RETURNS numeric
LANGUAGE sql
STABLE
AS $$
    SELECT round(h.price * (1 - coalesce(pr.discount_pct, 0) / 100), 2)
    FROM (
        SELECT price
        FROM product_price_history
        WHERE product_id = p_product_id
          AND valid_from <= p_at
        ORDER BY valid_from DESC
        LIMIT 1
    ) h
    LEFT JOIN promotions pr
           ON pr.product_id = p_product_id
          AND pr.active_during @> p_at;
$$;

SELECT effective_price(1, '2024-11-26 12:00+00')  AS p1,
       effective_price(10, '2025-06-10 12:00+00') AS p10,
       effective_price(47, '2025-06-30 12:00+00') AS p47,
       effective_price(999)                       AS missing,
       effective_price(1, '2020-01-01')           AS before_listing;
```

```text
   p1   |  p10   |  p47  | missing | before_listing
--------+--------+-------+---------+----------------
 899.10 | 183.20 | 13.30 |         |
(1 row)
```

```sql
SELECT count(*) AS discounted_now
FROM products
WHERE status = 'active'
  AND effective_price(id, '2025-06-30 12:00+00') < price;      -- 3 (products 37, 47, 48)
```

**Why `STABLE`.** The function reads tables, so its result can change whenever someone writes to them, but not during one statement. `IMMUTABLE` would let the planner pre-compute `effective_price(10, '2025-06-10')` once while planning a prepared statement, and keep returning that value after a price change (theory, section 6). `VOLATILE` would be honest but pessimistic: it would block the "evaluate once" optimizations, and it would make the function unusable in index *lookups*.

**Why no `STRICT`.** `p_at` has a default, and a NULL `p_product_id` already gives NULL: the subquery finds no row, and the join over an empty `h` returns no row, so the scalar function returns NULL. `STRICT` would only short-circuit the same answer.

**Design notes.**

- Unknown products and moments before the first history row both give NULL, because the subquery `h` is empty. A `LEFT JOIN` from `h` would *not* help here, because there's nothing to keep.
- The default `now()` is evaluated at *call* time. Defaults are expressions, not constants.
- This function has a `FROM` clause, so it isn't inlined (theory, section 5.3). Used in a `WHERE` over many rows, it runs one subquery per row: fine for 62 products, but on 200,000 products you'd write the join directly, as in module 08's exercise 11.
- With `BEGIN ATOMIC` instead of `$$`, PostgreSQL would record the dependency on `product_price_history` and `promotions`, and refuse to drop a column the function uses.

---

## Exercise 2 ★: A view on a view

```sql
CREATE VIEW low_stock AS
SELECT id, seller_id, shop_name, title, available
FROM active_products
WHERE available <= 3;

SELECT * FROM low_stock ORDER BY available, id;
```

```text
 id | seller_id |    shop_name    |               title               | available
----+-----------+-----------------+-----------------------------------+-----------
  3 |         1 | TechTrove       | Titan G17 Gaming Laptop           |         0
 13 |         1 | TechTrove       | KidSafe Volume-Limited Headphones |         0
 25 |         2 | Bookworm Corner | The Lighthouse Keeper's Secret    |         0
 60 |         5 | Maple & Pine    | Cedar Raised Garden Bed           |         3
(4 rows)
```

**1. The plan.** Both views have vanished. The rewriter expanded `low_stock` into `active_products`, and that into the base tables, before planning:

```text
 Nested Loop Left Join
   Filter: (COALESCE((i.quantity - i.reserved), 0) <= 3)
   ->  Nested Loop
         ->  Nested Loop
               ->  Seq Scan on products p
                     Filter: (status = 'active'::product_status)
               ->  Index Scan using sellers_pkey on sellers s
                     Index Cond: (id = p.seller_id)
         ->  Index Only Scan using categories_pkey on categories c
               Index Cond: (id = p.category_id)
   ->  Index Scan using inventory_pkey on inventory i
         Index Cond: (product_id = p.id)
```

`categories` is still joined, even though `low_stock` doesn't use a single category column, because an inner join could filter rows (a product whose category doesn't exist), so the planner can't remove it. `inventory` *is* needed this time, because `available` is used. Views on views are free in terms of planning, but they hide how much each layer joins. Layering five views, each "just adding one join", is a classic source of slow queries.

**2. The dependency errors.**

```text
ERROR:  cannot alter type of a column used by a view or rule
DETAIL:  rule _RETURN on view active_products depends on column "reserved"

ERROR:  cannot drop view active_products because other objects depend on it
DETAIL:  view low_stock depends on view active_products
```

`active_products` uses `inventory.reserved` in its `available` expression, and the column type is part of the stored view. Changing it would change the view's output type. Changing such a column means `DROP VIEW low_stock, active_products`, `ALTER TABLE`, and recreating both views, in one transaction. A plain `DROP VIEW active_products` fails because `low_stock` depends on it. `CASCADE` would silently drop `low_stock` too.

**Alternative:** `low_stock` written directly against the base tables is independent of `active_products`. That gives fewer dependencies but duplicates the "what counts as active and available" logic. Building on the view keeps one definition of "available", which is usually the better trade.

---

## Exercise 3 ★: No repricing archived products

```sql
CREATE FUNCTION forbid_archived_price_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'cannot change the price of archived product %', OLD.id
        USING ERRCODE = 'BZ020',
              HINT    = 'Reactivate the product first.';
END;
$$;

CREATE TRIGGER products_archived_price_guard
    BEFORE UPDATE OF price ON products
    FOR EACH ROW
    WHEN (OLD.status = 'archived' AND NEW.price IS DISTINCT FROM OLD.price)
    EXECUTE FUNCTION forbid_archived_price_change();
```

All the logic is in the trigger definition. `UPDATE OF price` skips statements that don't mention `price` at all, and the `WHEN` clause fires the function only for the forbidden case, so the function itself just raises. Since `WHEN` is evaluated without calling PL/pgSQL, ordinary updates pay almost nothing.

```text
UPDATE products SET price = 699.00 WHERE id = 5;
ERROR:  BZ020: cannot change the price of archived product 5
HINT:  Reactivate the product first.

UPDATE products SET price = price WHERE id = 5;                  -- UPDATE 1 (not a change)
UPDATE products SET title = 'AeroBook 13 (2023)' WHERE id = 5;   -- UPDATE 1
UPDATE products SET status = 'archived', price = 899.00 WHERE id = 1;   -- UPDATE 1 (OLD.status was active)
```

**The seller-wide markdown.** `UPDATE products SET price = price * 0.9 WHERE seller_id = 2` touches product 23, which is archived. The error aborts the *whole statement*, so none of seller 2's 14 other products is repriced either. Statements are atomic. The fix is to say what you mean: `... WHERE seller_id = 2 AND status <> 'archived'` (`UPDATE 14`).

**`BEFORE` or `AFTER`?** Both work for a pure check. `BEFORE` fails earlier, before the row is written and before indexes are updated, so it's cheaper on the failure path. An `AFTER` trigger would see the final row after every other `BEFORE` trigger has run. Choose that when other `BEFORE` triggers might change the checked values.

**Alternative:** a `CHECK` constraint can't compare old and new values, so this rule *needs* a trigger. You could also refuse *all* updates of archived products except reactivation, but auditors usually want typos in titles fixed.

---

## Exercise 4 ★★: Price history that keeps itself

```sql
CREATE FUNCTION record_price_history()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO product_price_history (product_id, price, valid_from)
    VALUES (NEW.id, NEW.price, now())
    ON CONFLICT (product_id, valid_from) DO UPDATE SET price = EXCLUDED.price;
    RETURN NULL;
END;
$$;

CREATE TRIGGER products_price_history_ins
    AFTER INSERT ON products
    FOR EACH ROW
    EXECUTE FUNCTION record_price_history();

CREATE TRIGGER products_price_history_upd
    AFTER UPDATE OF price ON products
    FOR EACH ROW
    WHEN (OLD.price IS DISTINCT FROM NEW.price)
    EXECUTE FUNCTION record_price_history();
```

The test sequence (71 history rows before):

```sql
UPDATE products SET price = 219.00 WHERE id = 10;   -- new row
UPDATE products SET price = 219.00 WHERE id = 10;   -- WHEN false: nothing
UPDATE products SET title = title WHERE id = 10;    -- price not in SET: nothing
BEGIN;
UPDATE products SET price = 209.00 WHERE id = 11;   -- new row at now()
UPDATE products SET price = 199.00 WHERE id = 11;   -- same now(): conflict -> update to 199.00
COMMIT;
INSERT INTO products (seller_id, category_id, sku, title, price)
VALUES (6, 6, 'SG-AUD-001', 'Sakura Earbuds', 59.00);   -- id 63: initial price
```

```text
 history_after
---------------
            74

 product_id | price
------------+--------
         10 | 249.00
         10 | 229.00
         10 | 219.00
         11 | 129.00
         11 | 199.00

 mismatches
------------
          0
```

**The same-transaction case.** `now()` is the transaction's start time, so both changes to product 11 produce the key `(11, <same timestamp>)`. Without `ON CONFLICT`, the second `UPDATE` fails:

```text
ERROR:  duplicate key value violates unique constraint "product_price_history_pkey"
DETAIL:  Key (product_id, valid_from)=(12, 2026-10-14 08:55:41.601438+00) already exists.
```

What *should* the history say? From the outside, the transaction is atomic: nobody ever saw 209.00. So "199.00 from this moment on" is the correct record, and that's what `DO UPDATE SET price = EXCLUDED.price` produces. (Using `clock_timestamp()` instead of `now()` would record both prices a few microseconds apart. That's technically a history of states nobody could observe, and it wouldn't match `products.updated_at`, which `set_updated_at` sets to `now()`.)

**Why `AFTER`?** The history row should only exist if the product row was really written. A `BEFORE` trigger runs before the `CHECK` constraints and before later `BEFORE` triggers, which might still reject or skip the row. Both happen in the same transaction anyway, but `AFTER` triggers see the final row.

**Alternatives:** do it in the application (and hope every writer remembers), or derive the history from `audit_log` (it holds every old and new price, but it's a heavier table to query, and module 15 restricts who may read it).

---

## Exercise 5 ★★: Breadcrumbs

```sql
CREATE FUNCTION category_breadcrumb(p_category_id int)
RETURNS TABLE (depth int, category_id int, name text, slug text)
LANGUAGE sql
STABLE
AS $$
    WITH RECURSIVE up AS (
        SELECT c.id, c.parent_id, c.name, c.slug, 0 AS steps_up
        FROM categories c
        WHERE c.id = p_category_id
        UNION ALL
        SELECT parent.id, parent.parent_id, parent.name, parent.slug, up.steps_up + 1
        FROM categories parent
        JOIN up ON parent.id = up.parent_id
    )
    SELECT (max(steps_up) OVER () - steps_up + 1)::int, id, name, slug
    FROM up
    ORDER BY 1;
$$;

CREATE FUNCTION category_path(p_category_id int, p_separator text DEFAULT ' > ')
RETURNS text
LANGUAGE sql
STABLE
AS $$
    SELECT string_agg(name, p_separator ORDER BY depth)
    FROM category_breadcrumb(p_category_id);
$$;
```

```text
SELECT * FROM category_breadcrumb(16);
 depth | category_id |      name       |      slug
-------+-------------+-----------------+-----------------
     1 |          14 | Books           | books
     2 |          15 | Fiction         | fiction
     3 |          16 | Science Fiction | science-fiction

SELECT p.id, p.title, category_path(p.category_id) AS path FROM products p WHERE p.id IN (1, 17, 33, 47, 62) ORDER BY p.id;
 id |            title            |               path
----+-----------------------------+-----------------------------------
  1 | AeroBook 13 Laptop          | Electronics > Computers > Laptops
 17 | USB-C Docking Station       | Electronics > Computers
 33 | The Complete Sci-Fi Box Set | Books > Fiction
 47 | Organic Cotton Tee - White  | Clothing > T-Shirts
 62 | Bazaar Sticker Pack         | Clothing
```

`category_breadcrumb(21)` returns only Clothing, `category_breadcrumb(999)` returns nothing (the anchor finds no row), and 47 products sit at depth 3.

**Why walk up, not down?** Starting from one category and following `parent_id` touches only the categories on the path (at most 3 here). Walking *down* from the roots would build the whole tree for every call. The depth is only known once the walk has reached the root, hence the window function over the finished CTE.

**Name clashes.** The output columns `name` and `slug` have the same names as columns of `categories`. In a **SQL** function that's harmless, because a column reference wins over an output name. In PL/pgSQL with `RETURN QUERY`, the same body would raise "column reference "name" is ambiguous" (theory, section 7.5) unless every column is qualified.

**Alternatives:** module 07's materialized-path design (`path text` like `'14.15.16'`) makes breadcrumbs a string split, at the cost of maintaining the path, typically with a trigger. Note also that `category_path` calls `category_breadcrumb`. Neither is inlined (a recursive CTE never is), so listing 200,000 products with their paths would run 200,000 recursive queries. For bulk use, join a precomputed `category_paths` view or materialized view once.

---

## Exercise 6 ★★: Row counts of every table

```sql
CREATE FUNCTION table_row_counts(p_schema text DEFAULT 'bazaar')
RETURNS TABLE (table_name text, row_count bigint, estimate bigint)
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT c.relname, c.reltuples
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = p_schema
          AND c.relkind IN ('r', 'p', 'm')
          AND NOT c.relispartition
        ORDER BY c.relname
    LOOP
        table_name := r.relname;
        estimate   := r.reltuples::bigint;
        EXECUTE format('SELECT count(*) FROM %I.%I', p_schema, r.relname) INTO row_count;
        RETURN NEXT;
    END LOOP;
END;
$$;

SELECT table_name, row_count FROM table_row_counts();
```

```text
      table_name       | row_count
-----------------------+-----------
 addresses             |        32
 audit_log             |         5
 cart_items            |        14
 categories            |        23
 inventory             |        62
 order_items           |       235
 orders                |       120
 payments              |       140
 product_price_history |        74
 products              |        63
 promotions            |         9
 reviews               |        78
 seller_sales_daily    |        96
 sellers               |         6
 users                 |        30
(15 rows)
```

The schema name comes from a parameter, but it's only used in two safe ways: as a *value* in the catalog query (`n.nspname = p_schema`, an ordinary parameter), and through `%I` in the dynamic SQL. `table_row_counts('bazaar; DROP TABLE users')` matches no schema and returns 0 rows. Even if a schema with that odd name existed, `%I` would quote it as one identifier.

**The estimates.** `reltuples` is maintained by `VACUUM`, `ANALYZE` and `CREATE INDEX`. **−1** means "never analysed": the table was created and filled recently, and autovacuum hasn't processed it yet. The planner then guesses from the table's physical size. Run `ANALYZE` and the column fills in. On the large dataset, estimates are how monitoring tools show table sizes without scanning (module 17), while exact counts cost a full scan each.

**Details.** `relkind IN ('r', 'p', 'm')` includes partitioned tables and materialized views, and `NOT relispartition` avoids counting partitions twice (module 14), since the parent's `count(*)` includes them. The function is `STABLE`: it only reads. Dynamic statements are planned at every call, which is fine for this kind of maintenance function.

---

## Exercise 7 ★★: A ratings materialized view

```sql
CREATE MATERIALIZED VIEW product_rating_summary AS
SELECT product_id,
       count(*)                            AS reviews,
       round(avg(rating), 2)               AS avg_rating,
       count(*) FILTER (WHERE rating >= 4) AS positive,
       max(created_at)                     AS last_review_at
FROM reviews
GROUP BY product_id;

CREATE UNIQUE INDEX product_rating_summary_product_id_key
    ON product_rating_summary (product_id);
```

```text
 products | reviews
----------+---------
       44 |      78

 product_id | reviews | avg_rating | positive |     last_review_at
------------+---------+------------+----------+------------------------
         24 |       4 |       4.25 |        3 | 2024-05-08 07:56:46+00
         10 |       3 |       5.00 |        3 | 2025-06-06 15:26:43+00
         42 |       3 |       4.67 |        3 | 2025-05-18 21:49:33+00
```

The unique index on `product_id` does two jobs: it makes `REFRESH ... CONCURRENTLY` possible (old and new rows are matched on it), and it serves the join below.

```sql
SELECT ap.id, ap.title, s.reviews, s.avg_rating
FROM active_products ap
LEFT JOIN product_rating_summary s ON s.product_id = ap.id
WHERE ap.category_id = 3
ORDER BY ap.id;
```

```text
 id |          title          | reviews | avg_rating
----+-------------------------+---------+------------
  1 | AeroBook 13 Laptop      |       1 |       1.00
  2 | AeroBook 15 Pro Laptop  |       2 |       4.50
  3 | Titan G17 Gaming Laptop |       1 |       2.00
  4 | StudyMate 11 Chromebook |         |
 15 | Titan G15 Gaming Laptop |         |
 16 | Laptop Sleeve 13"       |       1 |       4.00
(6 rows)
```

Product 1's single 1-star review is the planted anomaly from NOTES.md, the review by user 7, who never ordered. A ratings feature should probably only count verified purchases. That would be a `WHERE EXISTS (...)` in the materialized view's query, and a good follow-up exercise.

Staleness, inside a transaction:

```text
INSERT INTO reviews (product_id, user_id, rating, title) VALUES (15, 8, 5, 'Great gaming laptop');
SELECT count(*) FROM product_rating_summary WHERE product_id = 15;     -- 0: stale
REFRESH MATERIALIZED VIEW CONCURRENTLY product_rating_summary;
SELECT product_id, reviews, avg_rating FROM product_rating_summary WHERE product_id = 15;
 product_id | reviews | avg_rating
------------+---------+------------
         15 |       1 |       5.00
ROLLBACK;
```

The refresh ran inside the transaction, so it saw the uncommitted review. After the `ROLLBACK`, the refresh is rolled back too. A refresh is an ordinary transactional write.

**Alternative:** a plain table `product_ratings` maintained by a trigger on `reviews` (deltas for count and sum, as with `orders.total`) is always fresh. But it makes every review write update a hot row, and it needs care with deletes and edits. For ratings, a few minutes of staleness is usually acceptable, so the materialized view with a scheduled concurrent refresh is the simpler choice.

---

## Exercise 8 ★★: A forgiving price import

```sql
CREATE FUNCTION import_prices(p_rows jsonb)
RETURNS TABLE (seller_id bigint, sku text, outcome text, error_code text, message text)
LANGUAGE plpgsql
AS $$
DECLARE
    r record;
BEGIN
    FOR r IN
        -- price as text: the cast happens inside the protected block below
        SELECT * FROM jsonb_to_recordset(p_rows) AS x(seller_id bigint, sku text, price text)
    LOOP
        seller_id  := r.seller_id;
        sku        := r.sku;
        outcome    := 'updated';
        error_code := NULL;
        message    := NULL;
        BEGIN
            UPDATE products p
            SET price = r.price::numeric
            WHERE p.seller_id = r.seller_id
              AND p.sku = r.sku;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'no product % for seller %', r.sku, r.seller_id
                    USING ERRCODE = 'no_data_found';
            END IF;
        EXCEPTION WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS error_code = RETURNED_SQLSTATE,
                                    message    = MESSAGE_TEXT;
            outcome := 'failed';
        END;
        RETURN NEXT;
    END LOOP;
END;
$$;
```

```text
 seller_id |    sku     | outcome | error_code |                                        message
-----------+------------+---------+------------+----------------------------------------------------------------------------------------
         1 | TT-LAP-001 | updated |            |
         1 | TT-LAP-005 | failed  | BZ020      | cannot change the price of archived product 5
         2 | BW-SF-001  | failed  | 23514      | new row for relation "products" violates check constraint "products_price_nonnegative"
         2 | BW-XX-999  | failed  | P0002      | no product BW-XX-999 for seller 2
         3 | CC-CU-001  | failed  | 22P02      | invalid input syntax for type numeric: "cheap"
         4 | TB-TS-001  | updated |            |
(6 rows)
```

The points that make it work:

- **Where the cast happens.** If the record type declared `price numeric`, the value `"cheap"` would fail inside `jsonb_to_recordset`, that is, in the loop's query, *outside* any exception block, and the whole import would abort at line 5. Reading the price as text and casting inside the protected block puts that failure where it can be handled. In general, everything that can fail per item must happen inside the per-item block.
- **Each block is a subtransaction.** A failed line's `UPDATE` is rolled back to the block's savepoint, so a failing line leaves no partial changes. The successful lines remain part of the caller's transaction.
- **`NOT FOUND` → explicit error.** An `UPDATE` that matches nothing isn't an error in SQL. The function turns it into one, with the standard `no_data_found` code, so the caller sees it in the same format as the other failures.
- **`GET STACKED DIAGNOSTICS` can write straight into output columns**, which are just variables.
- **The `BZ020` line** shows exercise 3's trigger doing its job through a completely different code path. That's the benefit of putting the rule in the database.

**Cost:** six subtransactions here. For a 100,000-line import, that's 100,000 savepoints and XIDs, and noticeably slower than a set-based `UPDATE ... FROM jsonb_to_recordset(...)`. A common production pattern is to validate set-based first (types with `pg_input_is_valid`, existence with a join), apply all valid lines in one statement, and report the invalid ones, falling back to per-row handling only for errors that can't be predicted, such as trigger rejections.

---

## Exercise 9 ★★: What happened to this column?

```sql
CREATE FUNCTION audit_history(p_table text, p_id bigint, p_column text)
RETURNS TABLE (audit_id bigint, changed_at timestamptz, operation text, old_value jsonb, new_value jsonb)
LANGUAGE sql
STABLE
AS $$
    SELECT a.id, a.changed_at, a.operation, a.old_row -> p_column, a.new_row -> p_column
    FROM audit_log a
    WHERE a.table_name = p_table
      AND a.row_pk @> jsonb_build_object('id', p_id)
      AND (a.old_row -> p_column) IS DISTINCT FROM (a.new_row -> p_column)
    ORDER BY a.id;
$$;
```

```text
SELECT operation, old_value, new_value FROM audit_history('products', 11, 'price');
 operation | old_value | new_value
-----------+-----------+-----------
 UPDATE    | 129.00    | 209.00
 UPDATE    | 209.00    | 199.00

SELECT operation, old_value, new_value FROM audit_history('products', 5, 'title');
 operation |             old_value             |      new_value
-----------+-----------------------------------+----------------------
 UPDATE    | "AeroBook 13 Laptop (2023 model)" | "AeroBook 13 (2023)"

SELECT count(*) FROM audit_history('products', 5, 'price');      -- 0

-- the product inserted in exercise 4
 operation | old_value | new_value
-----------+-----------+-----------
 INSERT    |           | 59.00
```

Compare product 11 with the price *history* from exercise 4: the history kept only 199.00 for that transaction, while the audit log recorded both statements, 129 → 209 and 209 → 199. The two tables answer different questions. The history answers "what was the price at time t?", and the audit log answers "which statements changed it?".

The `IS DISTINCT FROM` condition does the filtering for all three operations: for `INSERT`, `old_row` is NULL, for `DELETE`, `new_row` is NULL, and NULL is distinct from any value. For an unknown column, both sides are NULL, so the function returns nothing rather than an error. You might prefer an error, checked against `information_schema.columns`.

**Indexing on a big audit log:** the `@>` on `row_pk` can use a GIN index (`CREATE INDEX ON audit_log USING gin (row_pk jsonb_path_ops)`), but a B-tree on `(table_name, (row_pk ->> 'id'))` serves this exact lookup better, if you rewrite the condition to match the expression. Module 11 covers both. The no-op comparison happens per candidate row, which is fine once the index has narrowed things down to one row's history.

---

## Exercise 10 ★★★: Archive cancelled orders in batches

```sql
CREATE TABLE orders_archive (
    LIKE orders INCLUDING DEFAULTS INCLUDING CONSTRAINTS,
    archived_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id)
);
CREATE TABLE order_items_archive (
    LIKE order_items INCLUDING DEFAULTS INCLUDING CONSTRAINTS,
    PRIMARY KEY (order_id, product_id)
);
CREATE TABLE payments_archive (
    LIKE payments INCLUDING DEFAULTS INCLUDING CONSTRAINTS,
    PRIMARY KEY (id)
);

CREATE PROCEDURE archive_cancelled_orders(p_before timestamptz, p_batch_size int DEFAULT 100)
LANGUAGE plpgsql
AS $$
DECLARE
    v_ids   bigint[];
    v_batch int := 0;
    v_total int := 0;
BEGIN
    LOOP
        SELECT array_agg(id ORDER BY id) INTO v_ids
        FROM (
            SELECT id
            FROM orders
            WHERE status = 'cancelled'
              AND created_at < p_before
            ORDER BY id
            LIMIT p_batch_size
            FOR UPDATE
        ) batch;
        EXIT WHEN v_ids IS NULL;

        INSERT INTO orders_archive      SELECT o.*, now() FROM orders o      WHERE o.id = ANY (v_ids);
        INSERT INTO order_items_archive SELECT oi.*       FROM order_items oi WHERE oi.order_id = ANY (v_ids);
        INSERT INTO payments_archive    SELECT pm.*       FROM payments pm    WHERE pm.order_id = ANY (v_ids);

        DELETE FROM payments WHERE order_id = ANY (v_ids);
        DELETE FROM orders   WHERE id = ANY (v_ids);        -- order_items follow via ON DELETE CASCADE

        v_batch := v_batch + 1;
        v_total := v_total + cardinality(v_ids);
        COMMIT;
        RAISE NOTICE 'batch %: archived orders % (total %)', v_batch, v_ids, v_total;
    END LOOP;
END;
$$;

CALL archive_cancelled_orders('2025-03-01', 2);
```

```text
NOTICE:  batch 1: archived orders {31,32} (total 2)
NOTICE:  batch 2: archived orders {36,53} (total 4)
NOTICE:  batch 3: archived orders {64,67} (total 6)
NOTICE:  batch 4: archived orders {73} (total 7)
CALL

 orders_archived | items_archived | payments_archived | orders_left | cancelled_left | total_73 | audited_deletes
-----------------+----------------+-------------------+-------------+----------------+----------+-----------------
               7 |             15 |                 3 |         113 | {81,82,84}     |  1917.00 |               7
```

A second `CALL` finds no ids and exits at once.

How the pieces fit:

- **One id list per batch.** Selecting the ids once into an array and using `= ANY (v_ids)` in every statement guarantees that the five statements work on the *same* orders. Re-running the `WHERE status = 'cancelled' AND ...` condition in each statement could pick up a different set if another session cancelled an order in between. `FOR UPDATE` locks the batch's orders, so nobody can modify them between the copy and the delete.
- **Order of operations.** `payments` references `orders` without `ON DELETE CASCADE`, so payments must go before their orders. `order_items` has `ON DELETE CASCADE`, so deleting the orders removes the items. The archive copies are made *before* the deletes.
- **The triggers keep working.** The cascade deletes the items, which fires the statement-level `order_items_total_del` trigger. It tries to subtract from `orders.total` for orders that are already deleted in this statement, and its `UPDATE` matches 0 rows: harmless. The archived totals are the pre-deletion values (order 73: 1917.00). `orders_audit` recorded one `DELETE` per order, with the full old row, so the archive is also traceable through the audit log.
- **`COMMIT` per batch** releases the locks and makes each batch durable. If the procedure is interrupted (an error, a cancelled query, a lost connection) after batch 2, then batches 1 and 2 are committed (4 orders archived and deleted), the batch in progress is rolled back entirely, and the next `CALL` simply carries on with the remaining orders. That's the "re-runnable" property: each batch leaves the data consistent, and the selection condition naturally excludes what's already done.
- **`LIKE ... INCLUDING DEFAULTS INCLUDING CONSTRAINTS`** copies the column definitions and `CHECK` constraints, but not foreign keys, triggers or indexes (other than the primary keys we add). That's what you want for an archive: archived payments shouldn't need their order to still exist in `orders`, and archive tables shouldn't fire the audit trigger.

**At scale:** with the large dataset you'd want an index supporting `status = 'cancelled' AND created_at < ...` (module 11's partial indexes), and batches of a few thousand rows. Archives are also the classic use case for partitioning by time (module 14), where "archiving" becomes detaching a partition.

---

## Exercise 11 ★★★: Order status drives inventory

```sql
CREATE FUNCTION sync_inventory_on_status()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_was_reserved boolean := OLD.status IN ('pending', 'paid');
BEGIN
    IF v_was_reserved AND NEW.status = 'shipped' THEN
        UPDATE inventory i
        SET quantity = i.quantity - l.qty,
            reserved = i.reserved - l.qty
        FROM (SELECT product_id, sum(quantity) AS qty
              FROM order_items WHERE order_id = NEW.id GROUP BY product_id) l
        WHERE i.product_id = l.product_id;
    ELSIF v_was_reserved AND NEW.status = 'cancelled' THEN
        UPDATE inventory i
        SET reserved = i.reserved - l.qty
        FROM (SELECT product_id, sum(quantity) AS qty
              FROM order_items WHERE order_id = NEW.id GROUP BY product_id) l
        WHERE i.product_id = l.product_id;
    ELSIF v_was_reserved AND NEW.status NOT IN ('pending', 'paid') THEN
        RAISE EXCEPTION 'order % cannot go from % to %', NEW.id, OLD.status, NEW.status
            USING ERRCODE = 'BZ030';
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER orders_sync_inventory
    AFTER UPDATE OF status ON orders
    FOR EACH ROW
    WHEN (OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION sync_inventory_on_status();
```

Before, and after shipping 110, cancelling 115 and paying 116:

```text
 product_id | quantity | reserved                 product_id | quantity | reserved
------------+----------+----------               ------------+----------+----------
         49 |       40 |        2       →                 49 |       39 |        0
         62 |      500 |        2                         62 |      500 |        1
```

Order 110 (paid) had one unit of product 49: shipping took it out of stock and out of the reservation. Order 115 (pending) had one unit each of 49 and 62: cancelling released both reservations. Paying 116 changed nothing. The invariant query reports **0** mismatches, and the illegal jump fails:

```text
UPDATE orders SET status = 'delivered' WHERE id = 117;
ERROR:  BZ030: order 117 cannot go from pending to delivered
```

Design notes:

- **Why `AFTER`?** The inventory change should only happen if the order update really happened, after `orders_set_updated_at` and any other `BEFORE` trigger. Raising in an `AFTER` trigger still aborts the statement.
- **Aggregating per product** makes the trigger correct even if an order had the same product on two lines. (The primary key `(order_id, product_id)` forbids that today, but triggers outlive schemas.)
- **Safety nets.** `inventory_reserved_within_quantity` (`reserved <= quantity`, `reserved >= 0`) would turn a logic error, such as cancelling an order twice, into a constraint violation instead of silently corrupting the stock. The `WHEN` makes `UPDATE orders SET status = status` a no-op.
- **What's not covered:** refunds after delivery (restocking returned goods), editing the items of an open order (that needs a trigger on `order_items` too), and the *initial* reservation, which module 10's `place_order()` does at checkout. A real system also has to decide whether stock logic belongs in triggers at all. Here it's attractive, because every status change, from any code path, keeps the stock right.
- **Concurrency:** two orders containing the same products, shipped concurrently, update the same inventory rows in their own transactions. Row locks serialize them correctly, but if order A locks products 1 then 2 while order B locks 2 then 1, they can deadlock. Module 10 explains this and fixes it by always locking in product-id order.

---

## Exercise 12 ★★★: Row-level versus statement-level

The core of the experiment (see `exercises.sql` for the full script):

```sql
BEGIN;
-- (a) reference statement-level triggers
INSERT INTO orders (user_id, seller_id) SELECT 8, 1 FROM generate_series(1, 200);
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT o.id, p.id, 1, p.price
FROM (SELECT id FROM orders ORDER BY id DESC LIMIT 200) o
CROSS JOIN (SELECT id, price FROM products WHERE id BETWEEN 1 AND 10) p;

-- (b) swap in a row-level trigger
DROP TRIGGER order_items_total_ins ON order_items;
DROP TRIGGER order_items_total_upd ON order_items;
DROP TRIGGER order_items_total_del ON order_items;

CREATE FUNCTION order_items_maintain_total_row()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        UPDATE orders SET total = total - OLD.quantity * OLD.unit_price WHERE id = OLD.order_id;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        UPDATE orders SET total = total + NEW.quantity * NEW.unit_price WHERE id = NEW.order_id;
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER order_items_total_row
    AFTER INSERT OR UPDATE OR DELETE ON order_items
    FOR EACH ROW EXECUTE FUNCTION order_items_maintain_total_row();

-- ... repeat the same two INSERTs ...
ROLLBACK;
```

Measured results for the 2,000-line insert:

| | Statement-level (reference) | Row-level |
|---|---|---|
| Time | 57.8 ms | 237.5 ms |
| `UPDATE`s of `orders` (audit log rows) | **200** | **2,000** |
| Wrong totals | 0 | 0 |

Both are *correct*. The difference is how much work they do:

- **Row-level:** the trigger fires 2,000 times, and each firing runs a separate `UPDATE orders`. Each order row is updated 10 times, which creates 10 new row versions (MVCC: every update writes a new version, module 16), fires `orders_set_updated_at` 10 times, and writes 10 audit rows of two full jsonb documents each. That's 1,800 more row versions to vacuum later, and 1,800 more audit rows.
- **Statement-level:** the trigger fires once and runs **one** `UPDATE ... FROM (SELECT order_id, sum(...) FROM new_items GROUP BY order_id)`. Each order is updated exactly once, with the net delta.

The row-level version is simpler to write, and it's perfectly adequate when statements typically touch one row (an API adding one item at a time). The statement-level version wins whenever statements are set-based: imports, `INSERT ... SELECT`, bulk corrections. That's why the reference uses it. The reference's price is three trigger definitions (transition tables allow only one event per trigger) and a function with three branches.

Note that `DROP TRIGGER` and `CREATE TRIGGER` are transactional, so the whole experiment, including the trigger swap, was undone by the `ROLLBACK`. Note too that they take a lock on `order_items` that blocks concurrent writers until the transaction ends. Don't run such experiments on a production database.
