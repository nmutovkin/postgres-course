# Module 09: Views, Functions and Triggers

<!-- nav:start -->
> **Theory** · [Exercises](exercises.md) · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 08 Beyond Scalars](../08-beyond-scalars/README.md) · [Course home](../../README.md) · [10 Transactions and Concurrency →](../10-transactions-concurrency/README.md)
<!-- nav:end -->

Bazaar's rules have been living in many places. The storefront query that lists "active products with stock" is copy-pasted into three services. `products.updated_at` is only correct when the developer remembered to set it. Nobody can answer "who changed the price of product 10, and when?". `orders.total` is supposed to equal the sum of the order's lines, but nothing enforces that. And the seller dashboard recomputes a year of sales on every page load. PostgreSQL can hold this logic next to the data: **views** name and share queries, **materialized views** cache expensive ones, **functions** and **procedures** package logic in SQL or PL/pgSQL, and **triggers** run that logic automatically whenever rows change. This module teaches all four, with the mechanics that decide whether they help or hurt: inlining, volatility, subtransactions, trigger ordering and recursion. It ends with an honest discussion of what belongs in the database and what belongs in the application.

**What you'll learn**

- Views: how they're expanded into queries, what `CREATE OR REPLACE VIEW` can and can't change, how PostgreSQL tracks dependencies (and why you suddenly can't drop a column), `security_barrier`, automatically updatable views and `WITH CHECK OPTION`.
- Materialized views: `REFRESH`, `REFRESH ... CONCURRENTLY` and its unique-index requirement, and strategies for living with stale data.
- SQL functions (parameters, defaults, named notation, `VARIADIC`, `OUT` parameters, `RETURNS TABLE`, `SETOF`, inlining) and PL/pgSQL (blocks, variables, `%TYPE`/`%ROWTYPE`, control flow, loops over queries, `RETURN QUERY`/`RETURN NEXT`).
- Volatility (`IMMUTABLE`/`STABLE`/`VOLATILE`), `STRICT`, `PARALLEL SAFE`, and why mislabelling them produces wrong answers.
- Errors: `RAISE` levels, custom SQLSTATEs, `EXCEPTION` blocks and their real cost, `GET STACKED DIAGNOSTICS`.
- Dynamic SQL with `EXECUTE format()` and `%I`/`%L`, and how SQL injection happens inside the database.
- `SECURITY DEFINER` (a preview), procedures with `COMMIT` for batch jobs, and `DO` blocks.
- Triggers: `BEFORE`/`AFTER`/`INSTEAD OF`, row and statement level, `NEW`/`OLD`, the `TG_*` variables, `WHEN` conditions, transition tables, firing order, recursion, constraint triggers, and what triggers cost.

**Before you start**

```bash
./project/bin/reset.sh 09
./project/bin/psql.sh
```

This is the v3 schema from module 08. Sections 1–12 create small example functions and views as they go. Most examples that change data run inside `BEGIN ... ROLLBACK`. When an example commits something (a procedure, a constraint trigger), the text says so. The worked examples from the reference migration `project/db/0901_schema_logic.sql` appear in sections 2, 4, 13, 14 and 15. You can type them in as you read, but the [project milestone](project.md) asks you to assemble them into your own migration, so reset to `09` before you start it. To jump straight to the finished state, use `./project/bin/reset.sh 10`.

You need the SQL of modules 03–06 (especially CTEs and `LATERAL`) and the types of module 08 (JSONB, arrays, enums).

---

<!-- toc:start -->
**Contents**

- [1. Logic in the database: what's on offer](#1-logic-in-the-database-whats-on-offer)
- [2. Views](#2-views)
  - [2.1 A view is a stored query](#21-a-view-is-a-stored-query)
  - [2.2 Dependencies: why you can't drop that column](#22-dependencies-why-you-cant-drop-that-column)
  - [2.3 `CREATE OR REPLACE VIEW` and its rules](#23-create-or-replace-view-and-its-rules)
  - [2.4 `security_barrier`: views as a security boundary](#24-security_barrier-views-as-a-security-boundary)
- [3. Updatable views and `WITH CHECK OPTION`](#3-updatable-views-and-with-check-option)
- [4. Materialized views](#4-materialized-views)
  - [4.1 Refreshing](#41-refreshing)
  - [4.2 Living with staleness](#42-living-with-staleness)
- [5. Functions in SQL](#5-functions-in-sql)
  - [5.1 Anatomy](#51-anatomy)
  - [5.2 Functions that return rows](#52-functions-that-return-rows)
  - [5.3 Inlining: why SQL functions can be free](#53-inlining-why-sql-functions-can-be-free)
  - [5.4 String bodies versus `BEGIN ATOMIC`](#54-string-bodies-versus-begin-atomic)
- [6. Volatility, `STRICT` and friends: promises to the planner](#6-volatility-strict-and-friends-promises-to-the-planner)
- [7. PL/pgSQL](#7-plpgsql)
  - [7.1 Block structure and variables](#71-block-structure-and-variables)
  - [7.2 Conditionals](#72-conditionals)
  - [7.3 Loops](#73-loops)
  - [7.4 Returning sets: `RETURN QUERY` and `RETURN NEXT`](#74-returning-sets-return-query-and-return-next)
  - [7.5 The `RETURNS TABLE` name trap](#75-the-returns-table-name-trap)
- [8. Errors and exceptions](#8-errors-and-exceptions)
  - [8.1 `RAISE`](#81-raise)
  - [8.2 Catching errors: `EXCEPTION` blocks](#82-catching-errors-exception-blocks)
- [9. Dynamic SQL](#9-dynamic-sql)
- [10. `SECURITY DEFINER`: a preview](#10-security-definer-a-preview)
- [11. Procedures and `DO` blocks](#11-procedures-and-do-blocks)
  - [11.1 Procedures](#111-procedures)
  - [11.2 Functions or procedures?](#112-functions-or-procedures)
  - [11.3 `DO` blocks](#113-do-blocks)
- [12. Triggers: the model](#12-triggers-the-model)
  - [12.1 The dimensions of a trigger](#121-the-dimensions-of-a-trigger)
  - [12.2 Inside a trigger function](#122-inside-a-trigger-function)
  - [12.3 Return values](#123-return-values)
  - [12.4 Firing order](#124-firing-order)
- [13. Worked example: `set_updated_at`](#13-worked-example-set_updated_at)
  - [13.1 A `WHEN` condition: alerts only on the crossing](#131-a-when-condition-alerts-only-on-the-crossing)
  - [13.2 `INSTEAD OF` triggers: making a join view writable](#132-instead-of-triggers-making-a-join-view-writable)
- [14. Worked example: a generic audit log](#14-worked-example-a-generic-audit-log)
- [15. Worked example: `orders.total` with transition tables](#15-worked-example-orderstotal-with-transition-tables)
- [16. Trigger pitfalls](#16-trigger-pitfalls)
  - [16.1 Recursion](#161-recursion)
  - [16.2 Hidden work and surprising semantics](#162-hidden-work-and-surprising-semantics)
  - [16.3 Constraint triggers: checks at commit time](#163-constraint-triggers-checks-at-commit-time)
  - [16.4 What triggers cost](#164-what-triggers-cost)
- [17. Logic in the database or in the application?](#17-logic-in-the-database-or-in-the-application)
- [Summary](#summary)
- [What's next](#whats-next)

<!-- toc:end -->

## 1. Logic in the database: what's on offer

| Object | What it is | Runs when | Typical use |
|---|---|---|---|
| View | A named `SELECT` | Expanded into every query that uses it | Share a query; hide joins; a stable interface |
| Materialized view | A `SELECT` whose result is stored | `REFRESH` | Cache expensive aggregates |
| Function | Named code returning a value or rows | Called from SQL | Reusable calculations, set-returning helpers, trigger bodies |
| Procedure | Named code that returns nothing and may `COMMIT` | `CALL` | Batch jobs and maintenance |
| Trigger | A function bound to a table event | Automatically on `INSERT`/`UPDATE`/`DELETE`/`TRUNCATE` | Derived columns, audit logs, invariants |

They are all **catalog objects**: created with DDL, stored in the database, versioned with your migrations, and visible to every client, whatever language it's written in. That last point is the main argument for them, and section 17 weighs it against the costs.

---

## 2. Views

### 2.1 A view is a stored query

`CREATE VIEW name AS SELECT ...` stores the query's parse tree under a name. The view holds no data. Every time a query mentions it, PostgreSQL's **rewriter** replaces the view name with its definition, and the planner then optimizes the combined query as a whole. Here is Bazaar's storefront view from the reference migration:

```sql
CREATE VIEW active_products AS
SELECT
    p.id,
    p.seller_id,
    s.shop_name,
    p.category_id,
    c.name                                         AS category_name,
    p.sku,
    p.title,
    p.description,
    p.price,
    p.attributes,
    p.tags,
    coalesce(i.quantity - i.reserved, 0)           AS available,
    p.created_at,
    p.updated_at
FROM products p
JOIN sellers s         ON s.id = p.seller_id
JOIN categories c      ON c.id = p.category_id
LEFT JOIN inventory i  ON i.product_id = p.id
WHERE p.status = 'active';
```

It reads like a table:

```sql
SELECT id, title, shop_name, category_name, price, available
FROM active_products
WHERE category_name = 'Laptops'
ORDER BY price DESC;
```

```text
 id |          title          | shop_name | category_name |  price  | available
----+-------------------------+-----------+---------------+---------+-----------
  3 | Titan G17 Gaming Laptop | TechTrove | Laptops       | 2199.99 |         0
 15 | Titan G15 Gaming Laptop | TechTrove | Laptops       | 1799.00 |         4
  2 | AeroBook 15 Pro Laptop  | TechTrove | Laptops       | 1499.00 |        12
  1 | AeroBook 13 Laptop      | TechTrove | Laptops       |  999.00 |        25
  4 | StudyMate 11 Chromebook | TechTrove | Laptops       |  249.00 |        37
 16 | Laptop Sleeve 13"       | TechTrove | Laptops       |   19.99 |        59
(6 rows)
```

Because the view is merged into the outer query, using a view costs nothing compared with writing the query by hand. Sometimes it's even cheaper than you'd expect:

```sql
EXPLAIN (COSTS OFF) SELECT title FROM active_products WHERE id = 3;
```

```text
                                QUERY PLAN
--------------------------------------------------------------------------
 Nested Loop
   ->  Nested Loop
         ->  Seq Scan on products p
               Filter: ((status = 'active'::product_status) AND (id = 3))
         ->  Index Only Scan using sellers_pkey on sellers s
               Index Cond: (id = p.seller_id)
   ->  Index Only Scan using categories_pkey on categories c
         Index Cond: (id = p.category_id)
(8 rows)
```

`inventory` has disappeared from the plan. This is **join removal**: the query doesn't use any inventory column, and a `LEFT JOIN` on inventory's primary key can't add or remove rows, so the planner drops it. The `WHERE id = 3` was also pushed down into the scan of `products`. (It's a sequential scan only because the table has 62 rows.) The inner joins to `sellers` and `categories` stay: an inner join *could* remove rows, so the planner can't skip them.

Two mechanics to understand before you rely on views:

- **The column list is fixed when the view is created.** `CREATE VIEW v AS SELECT * FROM sellers` expands `*` at creation time. A column added to `sellers` later does *not* appear in `v`.
- **A view stores references, not names.** If you rename `products.title`, the view follows the rename automatically, because it refers to the column by its internal number.

### 2.2 Dependencies: why you can't drop that column

PostgreSQL records every column, table and function a view uses in the catalog `pg_depend`, and refuses changes that would break the view:

```sql
BEGIN;
ALTER TABLE products DROP COLUMN description;
ROLLBACK;
```

```text
ERROR:  cannot drop column description of table products because other objects depend on it
DETAIL:  view active_products depends on column description of table products
HINT:  Use DROP ... CASCADE to drop the dependent objects too.
```

The same goes for changing a column's type (`ALTER TABLE products ALTER COLUMN title TYPE varchar(200)` fails with "cannot alter type of a column used by a view or rule"), and for dropping a view that another view uses. Module 08's exercise 12 hit the same wall with a materialized view.

`CASCADE` silently drops the dependent views, along with their grants, comments, and any views built on *them*. In a migration, that's almost never what you want. The usual sequence is: drop the views explicitly, alter the table, and recreate the views, all in one transaction. To find what depends on a table:

```sql
SELECT DISTINCT v.relname AS dependent_view
FROM pg_depend d
JOIN pg_rewrite r ON r.oid = d.objid
JOIN pg_class v   ON v.oid = r.ev_class
WHERE d.classid = 'pg_rewrite'::regclass
  AND d.refobjid = 'products'::regclass
  AND v.oid <> d.refobjid;
```

```text
 dependent_view
-----------------
 active_products
(1 row)
```

> **In production:** dependency errors are the most common surprise when a team starts using views, because the migration that worked in development fails on a database where someone created a reporting view. Keep views in version control like tables, and when a migration needs to change a column used by views, recreate those views in the same migration.

### 2.3 `CREATE OR REPLACE VIEW` and its rules

`CREATE OR REPLACE VIEW` swaps the definition while keeping the view's identity, so dependent objects and grants survive. To keep that safe, the new definition must produce the **same columns, with the same names and types, in the same order**. It may only **add columns at the end**:

```sql
BEGIN;
CREATE OR REPLACE VIEW active_products AS SELECT p.id, p.seller_id FROM products p;
ROLLBACK;
```

```text
ERROR:  cannot drop columns from view
```

Renaming `shop_name` to `shop` in the new definition fails with `cannot change name of view column "shop_name" to "shop"` and the hint to use `ALTER VIEW ... RENAME COLUMN`. Appending `s.country_code AS seller_country` at the end works. Anything else needs `DROP VIEW` + `CREATE VIEW` in one transaction, which also means re-granting privileges.

### 2.4 `security_barrier`: views as a security boundary

A view is often used to *restrict* what someone can see: "seller 1 can query `seller1_products`, which only shows seller 1's products". Module 15 does this properly with row-level security. But note a subtlety now. Because the view is merged into the outer query, the planner is free to evaluate the *caller's* conditions before the view's own `WHERE`. A caller can therefore pass a cheap function that leaks every row it sees:

```sql
BEGIN;
CREATE VIEW seller1_products AS SELECT id, title, price FROM products WHERE seller_id = 1;
CREATE FUNCTION peek(t text) RETURNS boolean LANGUAGE plpgsql COST 0.0000001 AS $$
BEGIN RAISE NOTICE 'saw: %', t; RETURN true; END $$;

SELECT count(*) FROM seller1_products WHERE peek(title);
```

```text
NOTICE:  saw: AeroBook 13 Laptop
...
NOTICE:  saw: Bazaar Sticker Pack
 count
-------
    18
```

All 62 titles were printed, including those of other sellers, because the planner ran the very cheap `peek()` first. Marking the view as a **security barrier** forces the view's own conditions to run first:

```sql
ALTER VIEW seller1_products SET (security_barrier = true);
SELECT count(*) FROM seller1_products WHERE peek(title);   -- prints only seller 1's 18 titles
ROLLBACK;
```

The price is optimization freedom: the planner can no longer push outer conditions into the view, except functions marked `LEAKPROOF`. Use `security_barrier` only on views that exist to hide rows. Since PostgreSQL 15 there's also `security_invoker`, which makes a view check permissions as the *calling* user instead of the view's owner. Module 15 covers both.

---

## 3. Updatable views and `WITH CHECK OPTION`

A view over **a single table**, without aggregates, `DISTINCT`, `GROUP BY`, `LIMIT`, window functions or set operations, is **automatically updatable**: `INSERT`, `UPDATE` and `DELETE` on the view are translated into the same operations on the underlying table. A drafts editor:

```sql
BEGIN;

CREATE VIEW draft_products AS
SELECT id, seller_id, category_id, sku, title, price, status
FROM products
WHERE status = 'draft';

SELECT * FROM draft_products ORDER BY id;
```

```text
 id | seller_id | category_id |    sku     |            title             | price | status
----+-----------+-------------+------------+------------------------------+-------+--------
 14 |         1 |           6 | TT-AUD-005 | BassBoom Over-Ear Headphones | 89.00 | draft
 28 |         2 |          17 | BW-MY-005  | Midnight on the Orient Line  | 15.00 | draft
 55 |         4 |          23 | TB-HD-004  | Fleece-Lined Winter Hoodie   | 69.00 | draft
 59 |         5 |          13 | MP-PL-003  | Hanging Macramé Planter      | 22.00 | draft
(4 rows)
```

```sql
UPDATE draft_products SET price = 79.00 WHERE id = 14;      -- UPDATE 1
UPDATE draft_products SET status = 'active' WHERE id = 14;  -- UPDATE 1: the row leaves the view!

INSERT INTO draft_products (seller_id, category_id, sku, title, price, status)
VALUES (1, 3, 'TT-LAP-099', 'Prototype laptop', 1.00, 'active');   -- INSERT 0 1, invisible in the view

ROLLBACK;
```

The last two statements show the problem. Through a view of *drafts*, you just published a product and created an active one. The view's `WHERE` filters what you *see*, not what you *write*. **`WITH CHECK OPTION`** closes the gap: every row written through the view must satisfy its condition.

```sql
BEGIN;
CREATE VIEW draft_products AS
SELECT id, seller_id, category_id, sku, title, price, status
FROM products
WHERE status = 'draft'
WITH CHECK OPTION;

UPDATE draft_products SET price = 79.00 WHERE id = 14;       -- fine
UPDATE draft_products SET status = 'active' WHERE id = 14;
ROLLBACK;
```

```text
ERROR:  new row violates check option for view "draft_products"
DETAIL:  Failing row contains (14, 1, 6, TT-AUD-005, BassBoom Over-Ear Headphones, null, 79.00, active, ...).
```

For views built on views, `WITH CASCADED CHECK OPTION` (the default) also checks the conditions of the underlying views, and `WITH LOCAL CHECK OPTION` checks only this view's own condition.

`active_products` has joins, so it isn't automatically updatable:

```text
ERROR:  cannot insert into view "active_products"
DETAIL:  Views that do not select from a single table or view are not automatically updatable.
HINT:  To enable inserting into the view, provide an INSTEAD OF INSERT trigger or an unconditional ON INSERT DO INSTEAD rule.
```

The hint's `INSTEAD OF` trigger is shown in section 13.2. (Rules, `CREATE RULE`, are an older and much trickier mechanism. Don't write new ones.)

---

## 4. Materialized views

A **materialized view** runs its query once and **stores the result** like a table. Reading it is as fast as reading a table, because it *is* one. In exchange, it's a snapshot: it doesn't change until you refresh it. Bazaar's seller dashboard shows daily sales, which would otherwise mean aggregating every order on every page load:

```sql
CREATE MATERIALIZED VIEW seller_sales_daily AS
SELECT
    o.seller_id,
    (o.created_at AT TIME ZONE 'UTC')::date  AS day,
    count(*)                                 AS orders_count,
    sum(i.items)                             AS items_sold,
    sum(o.total)                             AS revenue
FROM orders o
JOIN (
    SELECT order_id, sum(quantity) AS items
    FROM order_items
    GROUP BY order_id
) i ON i.order_id = o.id
WHERE o.status IN ('paid', 'shipped', 'delivered')
GROUP BY o.seller_id, (o.created_at AT TIME ZONE 'UTC')::date;
```

```text
SELECT 96
```

Two design details matter:

- **`items_sold` is aggregated per order first**, in the subquery. Joining `orders` directly to `order_items` would repeat each order once per line, and `sum(o.total)` would count an order's total once for every line it has. That's the classic fan-out bug from module 05.
- **The day is computed in UTC** (`AT TIME ZONE 'UTC'`). `created_at::date` would use the `TimeZone` setting of whoever runs the refresh, so the same data could land on different days depending on who refreshed it.

The per-seller totals match the revenue figures in NOTES.md:

```sql
SELECT seller_id, count(*) AS days, sum(orders_count) AS orders, sum(items_sold) AS items, sum(revenue) AS revenue
FROM seller_sales_daily
GROUP BY seller_id
ORDER BY seller_id;
```

```text
 seller_id | days | orders | items | revenue
-----------+------+--------+-------+----------
         1 |   28 |     31 |    69 | 41598.82
         2 |   29 |     29 |    81 |  1656.43
         3 |   22 |     23 |    57 |  3562.94
         4 |   17 |     18 |    53 |  2277.00
(4 rows)
```

A materialized view can have indexes like any table, and it has a size on disk (`relkind = 'm'` in `pg_class`).

### 4.1 Refreshing

`REFRESH MATERIALIZED VIEW seller_sales_daily` re-runs the query and replaces the contents. Until then, the data is stale:

```sql
BEGIN;
INSERT INTO orders (user_id, seller_id, status, total, created_at, paid_at)
VALUES (8, 2, 'paid', 25.00, '2025-06-30 10:00+00', '2025-06-30 10:05+00');
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
VALUES (currval('orders_id_seq'), 21, 1, 18.00), (currval('orders_id_seq'), 22, 1, 7.00);

SELECT * FROM seller_sales_daily WHERE seller_id = 2 AND day = '2025-06-30';            -- 0 rows
REFRESH MATERIALIZED VIEW seller_sales_daily;
SELECT * FROM seller_sales_daily WHERE seller_id = 2 AND day = '2025-06-30';
ROLLBACK;
```

```text
 seller_id |    day     | orders_count | items_sold | revenue
-----------+------------+--------------+------------+---------
         2 | 2025-06-30 |            1 |          2 |   25.00
(1 row)
```

A plain `REFRESH` builds the new contents in a new file and swaps it in, holding an **`ACCESS EXCLUSIVE`** lock: while it runs, even `SELECT`s on the materialized view wait. For a dashboard read all day, a refresh that takes 30 seconds is 30 seconds of hung page loads.

**`REFRESH MATERIALIZED VIEW CONCURRENTLY`** avoids this. It computes the new result into a temporary table, compares it with the current contents, and applies only the differences as ordinary inserts, updates and deletes. It takes an `EXCLUSIVE` lock, which blocks other refreshes and writes but **not reads**. To match old and new rows, it needs a **unique index** on plain columns (no expression, no `WHERE`):

```sql
REFRESH MATERIALIZED VIEW CONCURRENTLY seller_sales_daily;
```

```text
ERROR:  cannot refresh materialized view "bazaar.seller_sales_daily" concurrently
HINT:  Create a unique index with no WHERE clause on one or more columns of the materialized view.
```

```sql
CREATE UNIQUE INDEX seller_sales_daily_seller_id_day_key
    ON seller_sales_daily (seller_id, day);

REFRESH MATERIALIZED VIEW CONCURRENTLY seller_sales_daily;   -- now works
```

The trade-off: a concurrent refresh is *slower* than a plain one (it does the diff, and it bloats the table like any `UPDATE`-heavy table), so it's only worth it when readers must not be blocked. It can't be used for the very first population (`WITH NO DATA` views).

### 4.2 Living with staleness

How stale may the data be, and who refreshes it? Common strategies:

| Strategy | How | Staleness | Cost |
|---|---|---|---|
| Scheduled refresh | cron, a job runner, or the **pg_cron** extension (`SELECT cron.schedule('*/15 * * * *', 'REFRESH MATERIALIZED VIEW CONCURRENTLY seller_sales_daily')`) | Up to one interval | One full recomputation per interval, whether or not anything changed |
| Refresh after batch loads | The ETL job runs `REFRESH` as its last step | Zero after the load | Couples the job to the view |
| Refresh on demand | The app refreshes when a user clicks "update" | Whatever the user accepts | Unpredictable load |
| Incremental summary table | A normal table maintained by triggers or by the writing code (section 15 shows the trigger technique) | Zero | Every write pays; more code to get right |

A materialized view always recomputes everything. PostgreSQL has no built-in incremental refresh. When "one full recomputation" becomes too expensive, the next step is a summary table that you maintain yourself, or a summary per closed period (yesterday's sales never change) plus a live query for today.

> **In production:** show users the refresh time. A small table `matview_refreshes(name, refreshed_at)` updated by the refresh job, or a comment, lets the dashboard say "data as of 10:15", which turns "the numbers are wrong!" support tickets into "the numbers are 15 minutes old".

---

## 5. Functions in SQL

### 5.1 Anatomy

```sql
CREATE FUNCTION price_with_vat(p_price numeric, p_rate numeric DEFAULT 0.20)
RETURNS numeric
LANGUAGE sql
IMMUTABLE
RETURN round(p_price * (1 + p_rate), 2);
```

- **Parameters** have a name and a type, and optionally a `DEFAULT`. A `p_` prefix is a common convention that avoids collisions with column names, which matters in PL/pgSQL (section 7.5).
- **`RETURNS`** gives the result type: a scalar, a composite type, `SETOF type` for a set of rows, `TABLE (...)` for a set of rows with named columns, or `void`.
- **`LANGUAGE`** is `sql`, `plpgsql`, or one of the others (`plpython3u` and friends, C).
- **Attributes** such as `IMMUTABLE`, `STRICT` and `PARALLEL SAFE` tell the planner what it may assume (section 6).
- The **body** is either a string, usually written with dollar quoting `$$ ... $$` so that quotes inside need no escaping, or (for SQL functions, PostgreSQL 14+) a standard SQL body: `RETURN expr` or `BEGIN ATOMIC ... END`.

Calls can use **positional** notation, **named** notation (`=>`), or a mix where positional arguments come first:

```sql
SELECT price_with_vat(100),
       price_with_vat(100, 0.07),
       price_with_vat(p_rate => 0.07, p_price => 100),
       price_with_vat(100, p_rate => 0.1);
```

```text
 price_with_vat | price_with_vat | price_with_vat | price_with_vat
----------------+----------------+----------------+----------------
         120.00 |         107.00 |         107.00 |         110.00
(1 row)
```

Named notation keeps calls readable and robust when a function has several optional parameters. Functions are identified by **name plus argument types**, so you can overload `f(int)` and `f(text)`. Overloading combined with defaults leads to "function is not unique" errors, so use it sparingly. `CREATE OR REPLACE FUNCTION` can change the body and attributes, but not the argument types (that would be a different function) or the return type.

A function that queries tables:

```sql
CREATE FUNCTION seller_revenue(p_seller_id bigint,
                               p_from timestamptz DEFAULT '-infinity',
                               p_to   timestamptz DEFAULT 'infinity')
RETURNS numeric
LANGUAGE sql
STABLE
AS $$
    SELECT coalesce(sum(total), 0)
    FROM orders
    WHERE seller_id = p_seller_id
      AND status IN ('paid', 'shipped', 'delivered')
      AND created_at >= p_from
      AND created_at <  p_to;
$$;

SELECT id, shop_name, seller_revenue(id) AS all_time, seller_revenue(id, '2025-01-01') AS in_2025
FROM sellers
ORDER BY id;
```

```text
 id |    shop_name    | all_time | in_2025
----+-----------------+----------+----------
  1 | TechTrove       | 41598.82 | 16398.90
  2 | Bookworm Corner |  1656.43 |   528.26
  3 | Casa Cucina     |  3562.94 |  1309.98
  4 | Threadbare Co.  |  2277.00 |  1086.00
  5 | Maple & Pine    |        0 |        0
  6 | Sakura Gadgets  |        0 |        0
(6 rows)
```

A SQL function returns the result of its **last** statement. For a scalar return type, that's the first column of the first row (or NULL if there are no rows).

### 5.2 Functions that return rows

`RETURNS SETOF products` returns rows of the table's composite type. Call it in `FROM`, where it behaves like a table:

```sql
CREATE FUNCTION products_in_category(p_category_id int)
RETURNS SETOF products
LANGUAGE sql
STABLE
AS $$
    SELECT * FROM products WHERE category_id = p_category_id AND status = 'active';
$$;

SELECT id, title, price FROM products_in_category(4) ORDER BY price;
```

```text
 id |                  title                   | price
----+------------------------------------------+--------
  6 | ClearView 24" 1080p Monitor              | 159.99
  9 | PortaScreen 15.6" USB-C Portable Monitor | 189.00
  7 | ClearView 27" 4K Monitor                 | 379.00
  8 | UltraWide 34" Curved Monitor             | 549.00
(4 rows)
```

`RETURNS TABLE (...)` declares the output columns inline, which is the usual choice for reports:

```sql
CREATE FUNCTION top_products(p_seller_id bigint, p_limit int DEFAULT 3)
RETURNS TABLE (product_id bigint, title text, units bigint, revenue numeric)
LANGUAGE sql
STABLE
AS $$
    SELECT p.id, p.title, sum(oi.quantity), sum(oi.quantity * oi.unit_price)
    FROM order_items oi
    JOIN orders o   ON o.id = oi.order_id
    JOIN products p ON p.id = oi.product_id
    WHERE p.seller_id = p_seller_id
      AND o.status IN ('paid', 'shipped', 'delivered')
    GROUP BY p.id, p.title
    ORDER BY 4 DESC
    LIMIT p_limit;
$$;

SELECT * FROM top_products(3);
```

```text
 product_id |         title          | units | revenue
------------+------------------------+-------+---------
         38 | Copper Sauté Pan       |     4 |  756.00
         37 | Dutch Oven 5.5L Enamel |     6 |  654.00
         40 | Chef's Knife 20cm      |     4 |  356.00
(3 rows)
```

Combined with `LATERAL`, a set-returning function becomes a parameterised subquery. The best seller of every shop:

```sql
SELECT s.shop_name, t.title, t.revenue
FROM sellers s
CROSS JOIN LATERAL top_products(s.id, 1) AS t
ORDER BY s.id;
```

```text
    shop_name    |            title            | revenue
-----------------+-----------------------------+----------
 TechTrove       | AeroBook 15 Pro Laptop      | 13491.00
 Bookworm Corner | The Complete Sci-Fi Box Set |   553.00
 Casa Cucina     | Copper Sauté Pan            |   756.00
 Threadbare Co.  | Merino Wool Hoodie          |   833.00
(4 rows)
```

**`OUT` parameters** are another way to return several values: one row with named columns. `RETURNS TABLE` is shorthand for `OUT` parameters plus `SETOF`:

```sql
CREATE FUNCTION customer_stats(p_user_id bigint,
                               OUT orders bigint, OUT spent numeric, OUT last_order timestamptz)
LANGUAGE sql
STABLE
AS $$
    SELECT count(*), coalesce(sum(total), 0), max(created_at)
    FROM orders
    WHERE user_id = p_user_id AND status IN ('paid', 'shipped', 'delivered');
$$;

SELECT * FROM customer_stats(13);        -- as a row source: columns
SELECT customer_stats(13);               -- in the select list: one composite value
SELECT (customer_stats(13)).spent;       -- one field of it
```

```text
 orders |  spent   |       last_order
--------+----------+------------------------
     17 | 14564.95 | 2025-06-22 02:42:57+00

             customer_stats
----------------------------------------
 (17,14564.95,"2025-06-22 02:42:57+00")

  spent
----------
 14564.95
```

The composite value in the middle is module 08's composite type at work. Prefer the `FROM` form. `SELECT (f(x)).*` can call the function once *per output column*.

**`VARIADIC`** collects any number of trailing arguments into an array:

```sql
CREATE FUNCTION products_tagged(VARIADIC p_tags text[])
RETURNS SETOF bigint
LANGUAGE sql
STABLE
AS $$
    SELECT id FROM products WHERE tags @> p_tags ORDER BY id;
$$;

SELECT array_agg(x) FROM products_tagged('book', 'mystery') AS x;                 -- {24,25,26,27,28}
SELECT array_agg(x) FROM products_tagged(VARIADIC ARRAY['laptop', 'gaming']) AS x; -- {3,15}
```

The second call passes an existing array with the `VARIADIC` keyword. That's how a client passes a list it already has.

### 5.3 Inlining: why SQL functions can be free

A `LANGUAGE sql` function that is a single `SELECT` can be **inlined**: the planner pastes its body into the calling query, as it does with a view, and optimizes the whole thing. A PL/pgSQL function is a black box that the planner can only call. Compare the two versions of "products in a category" with an extra filter:

```sql
EXPLAIN (COSTS OFF) SELECT id FROM products_in_category(4) WHERE price > 200;
```

```text
                                             QUERY PLAN
----------------------------------------------------------------------------------------------------
 Seq Scan on products
   Filter: ((price > '200'::numeric) AND (category_id = 4) AND (status = 'active'::product_status))
(2 rows)
```

```text
-- the same body written in PL/pgSQL with RETURN QUERY (section 7):
EXPLAIN (COSTS OFF) SELECT id FROM products_in_category_plpgsql(4) WHERE price > 200;

 Function Scan on products_in_category_plpgsql
   Filter: (price > '200'::numeric)
```

The inlined SQL version became an ordinary scan of `products` with all three conditions combined. Given an index, the planner could have used it for any of them. The PL/pgSQL version runs the function to completion, materializes *all* the category's products, and only then applies `price > 200`. It also estimates 1,000 result rows, whatever the truth, which can mislead the rest of the plan.

Scalar SQL functions are inlined too. `EXPLAIN VERBOSE` shows `price_with_vat(price)` turned into plain arithmetic:

```text
EXPLAIN (VERBOSE, COSTS OFF) SELECT price_with_vat(price) FROM products WHERE id = 1;

 Seq Scan on bazaar.products
   Output: round((price * 1.20), 2)
   Filter: (products.id = 1)
```

Inlining has conditions. For a scalar function: the body is a single `SELECT` of an expression, without a `FROM` clause (so `seller_revenue` above isn't inlined), and the function isn't `SECURITY DEFINER`, has no `SET` clauses, and isn't declared more lax than its body (for example `IMMUTABLE` around a `STABLE` expression). For a set-returning function: a single `SELECT` whose contents are no more volatile than the function's label (section 6), not `STRICT`, not `SECURITY DEFINER`, and returning `SETOF` or `TABLE`. When a helper is on a hot path, write it in SQL and keep it simple enough to be inlined.

### 5.4 String bodies versus `BEGIN ATOMIC`

A body in `$$ ... $$` is stored as text. For SQL functions it's parsed when you create the function, so typos are caught early: `CREATE FUNCTION f() ... AS $$ SELECT count(*) FROM no_such_table $$` fails with `relation "no_such_table" does not exist`. But the tables it uses are **not** recorded as dependencies. You can drop a column the function needs, and you'll only find out at the next call. PL/pgSQL bodies are checked even less: only their syntax is validated at creation, and table names are resolved at the first execution.

The SQL-standard body (PostgreSQL 14+) is parsed and stored like a view, **with dependencies**:

```sql
CREATE FUNCTION active_product_count(p_seller_id bigint)
RETURNS bigint
LANGUAGE sql
STABLE
BEGIN ATOMIC
    SELECT count(*) FROM products WHERE seller_id = p_seller_id AND status = 'active';
END;
```

Now `ALTER TABLE products DROP COLUMN seller_id` fails with "function active_product_count(bigint) depends on column seller_id". A `RENAME COLUMN` is followed automatically, as with views. The trade-off is that you can't create such a function *before* the tables it uses exist, and it is subject to the same "can't change the column" rules as a view. For small, schema-bound helpers, `BEGIN ATOMIC` is the safer default. `price_with_vat` above used the single-expression form, `RETURN expr`.

---

## 6. Volatility, `STRICT` and friends: promises to the planner

Every function carries a **volatility** label. It's a *promise* you make to the planner, which uses it to decide when it may evaluate the function once instead of many times, and where the function may be used. PostgreSQL does not verify the promise.

| Label | Promise | Examples | Consequences |
|---|---|---|---|
| `IMMUTABLE` | Same arguments ⇒ same result, **forever**. No database access, no dependence on settings | `lower()`, `round()`, `jsonb_path_exists()` | May be evaluated at **plan time**; allowed in **index expressions**, generated columns and partition bounds |
| `STABLE` | Same arguments ⇒ same result **within one statement**. May read the database, depends on settings | `now()`, `to_char()`, most functions that `SELECT` | Evaluated once per statement where useful; usable for index *lookups* (`WHERE created_at > now() - interval '1 day'`) but not in index definitions |
| `VOLATILE` (default) | Anything: different results per call, side effects | `random()`, `clock_timestamp()`, `nextval()`, functions that write | Re-evaluated for every row, never optimized away |

You can see the difference in one query:

```sql
SELECT count(DISTINCT now())             AS distinct_now,
       count(DISTINCT clock_timestamp()) AS distinct_clock,
       count(DISTINCT random())          AS distinct_random
FROM generate_series(1, 1000);
```

```text
 distinct_now | distinct_clock | distinct_random
--------------+----------------+-----------------
            1 |            100 |            1000
(1 row)
```

`now()` is the transaction's start time, and it's `STABLE`. `clock_timestamp()` reads the clock on every call, and it's `VOLATILE`. (It returned 100 distinct values rather than 1,000 because consecutive calls can happen within the same clock tick.)

**Why it matters for indexes.** An index stores computed values. If the function could return something else tomorrow, the index would silently become wrong. So only `IMMUTABLE` functions are allowed in index definitions, and the built-in labels can surprise you:

```sql
BEGIN;
CREATE INDEX ON orders ((created_at::date));
ROLLBACK;
```

```text
ERROR:  functions in index expression must be marked IMMUTABLE
```

Converting a `timestamptz` to a `date` depends on the session's `TimeZone` (midnight in Tokyo isn't midnight in London), so the cast is only `STABLE`. `date_trunc('day', created_at)` fails for the same reason. `(created_at AT TIME ZONE 'UTC')::date` fixes the time zone explicitly, which is `IMMUTABLE`, and so it's allowed. It's also why `seller_sales_daily` uses it.

**Why lying is dangerous.** Mark a function `IMMUTABLE` when it isn't, and PostgreSQL will believe you:

```sql
CREATE TABLE settings (key text PRIMARY KEY, value numeric NOT NULL);
INSERT INTO settings VALUES ('vat_rate', 0.20);

CREATE FUNCTION vat_rate() RETURNS numeric
LANGUAGE sql
IMMUTABLE   -- WRONG: the result depends on a table
AS $$ SELECT value FROM settings WHERE key = 'vat_rate' $$;

PREPARE gross AS SELECT round(100 * (1 + vat_rate()), 2) AS gross;
EXECUTE gross;                                              -- 120.00
UPDATE settings SET value = 0.25 WHERE key = 'vat_rate';
EXECUTE gross;                                              -- still 120.00!
SELECT round(100 * (1 + vat_rate()), 2) AS fresh;           -- 125.00
```

The planner evaluated `vat_rate()` once, while planning, and baked `0.20` into the cached plan of the prepared statement as a constant. Prepared statements are what drivers and PL/pgSQL use all the time, so this isn't an exotic scenario. In an index, a mislabelled function is worse: the index keeps the old values, and queries return wrong rows until a `REINDEX`. (Clean up with `DEALLOCATE gross; DROP FUNCTION vat_rate(); DROP TABLE settings;`.)

The rule: **label honestly, and as strictly as is true.** A function that reads tables is `STABLE` at best. One that writes, or uses `random()`/`clock_timestamp()`/`nextval()`, is `VOLATILE`. Pure computation on its arguments is `IMMUTABLE`, unless it depends on settings such as `TimeZone`, `DateStyle` or the text search configuration.

**`STRICT`** (also spelled `RETURNS NULL ON NULL INPUT`) means "if any argument is NULL, the result is NULL". PostgreSQL then doesn't call the function at all for NULL inputs. It's right for most pure computations, and it's what makes `jsonb_set(doc, path, NULL)` return NULL (module 08). Without it, your function must handle NULLs itself. (`price_with_vat(NULL)` returns NULL either way, because the arithmetic propagates NULL.) Note that a `STRICT` set-returning function can't be inlined.

**`PARALLEL SAFE`** tells the planner that the function can run in parallel worker processes. That's the case if it doesn't write, doesn't use temporary tables or session state, and doesn't depend on transaction-local things like sequences. Functions default to `PARALLEL UNSAFE`, and a single unsafe function in a query disables parallelism for the whole query. That rarely matters on Bazaar's small tables, but on the large dataset (module 12) an unsafe function in a big aggregate query can turn a 4-worker plan into a single-process one. Label functions `PARALLEL SAFE` when they are.

**`COST`** and **`ROWS`** give the planner estimates: the execution cost in units of `cpu_operator_cost`, and the number of rows for set-returning functions. The `peek()` function in section 2.4 used `COST 0.0000001` to get itself evaluated first.

> **PG 17:** maintenance operations (`CREATE INDEX`, `REINDEX`, `REFRESH MATERIALIZED VIEW`, `VACUUM`, `ANALYZE`, `CLUSTER`) now run functions with a safe `search_path` of `pg_catalog, pg_temp`. A function used in an index expression or a materialized view that refers to `products` without a schema fails during maintenance with "relation does not exist". Schema-qualify the names inside such functions (`bazaar.products`), or give the function its own `SET search_path` (section 10).

---

## 7. PL/pgSQL

SQL functions are one query. When you need variables, branches, loops or error handling, use **PL/pgSQL**, PostgreSQL's built-in procedural language. It looks like Ada or PL/SQL, and its superpower is that SQL is embedded natively: any SQL statement can appear in the code, and PL/pgSQL variables can appear in the SQL.

### 7.1 Block structure and variables

```sql
CREATE FUNCTION order_summary(p_order_id bigint)
RETURNS text
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_order    orders%ROWTYPE;              -- a whole row of orders
    v_shop     sellers.shop_name%TYPE;      -- same type as that column
    v_lines    int := 0;
    v_result   text;
    r          record;                      -- a row of whatever shape
BEGIN
    SELECT * INTO v_order FROM orders WHERE id = p_order_id;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    SELECT shop_name INTO v_shop FROM sellers WHERE id = v_order.seller_id;

    v_result := format('Order %s from %s (%s)', v_order.id, v_shop, v_order.status);

    FOR r IN
        SELECT p.title, oi.quantity, oi.unit_price
        FROM order_items oi
        JOIN products p ON p.id = oi.product_id
        WHERE oi.order_id = p_order_id
        ORDER BY p.title
    LOOP
        v_lines := v_lines + 1;
        v_result := v_result || format(E'\n  %s x %s @ %s', r.quantity, r.title, r.unit_price);
    END LOOP;

    RETURN v_result || format(E'\n  %s line(s), total %s', v_lines, v_order.total);
END;
$$;

SELECT order_summary(40);
```

```text
              order_summary
-----------------------------------------
 Order 40 from TechTrove (delivered)    +
   3 x AeroBook 13 Laptop @ 1099.00     +
   3 x AeroBook 15 Pro Laptop @ 1499.00 +
   1 x ClearView 27" 4K Monitor @ 399.00+
   3 x QuietWave ANC Headphones @ 249.00+
   4 line(s), total 8940.00
(1 row)
```

The pieces:

- A function body is a **block**: an optional `DECLARE` section, then `BEGIN ... END`. Blocks can be nested, and each can have its own `DECLARE` and `EXCEPTION` sections.
- **Variables** are declared with a type and an optional initial value (`:=` or `DEFAULT`). Uninitialised variables are NULL. `CONSTANT` and `NOT NULL` are available.
- **`%TYPE`** borrows the type of a column (`sellers.shop_name%TYPE`), and **`%ROWTYPE`** the row type of a table. If the column's type changes, the function follows at its next compilation. A **`record`** variable takes the shape of whatever row is assigned to it.
- **`SELECT ... INTO var`** runs a query and stores the first row. Afterwards, the special variable **`FOUND`** says whether a row was found. Extra rows are silently ignored. Write `INTO STRICT` to demand *exactly one* row: zero rows raises `NO_DATA_FOUND` ("query returned no rows"), and more than one raises `TOO_MANY_ROWS`.
- `format()` builds strings with `%s` placeholders (and `%I`/`%L`, section 9). `E'\n'` is an escape-string literal containing a newline.
- After an `INSERT`, `UPDATE` or `DELETE`, **`GET DIAGNOSTICS v := ROW_COUNT`** gives the number of affected rows, and `FOUND` is true if there was at least one.

> **Under the hood:** PL/pgSQL compiles a function the first time a session calls it, and prepares each embedded SQL statement the first time it runs. The plans are cached for the rest of the session, much like the prepared statement in section 6. After five executions, PostgreSQL considers switching from a "custom" plan, made for the actual parameter values, to a "generic" plan reused for all values. Usually that's good. Occasionally it produces a plan that's bad for some values (module 12 explains `plan_cache_mode`). DDL on the referenced tables invalidates the cached plans automatically.

### 7.2 Conditionals

`IF ... THEN ... ELSIF ... ELSE ... END IF` works as you'd expect. `CASE` comes in two forms: a simple form (`CASE x WHEN 1 THEN ...`) and a searched form:

```sql
CREATE FUNCTION customer_tier(p_user_id bigint)
RETURNS text
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_spent numeric;
BEGIN
    SELECT coalesce(sum(total), 0) INTO v_spent
    FROM orders
    WHERE user_id = p_user_id AND status IN ('paid', 'shipped', 'delivered');

    CASE
        WHEN v_spent >= 10000 THEN RETURN 'platinum';
        WHEN v_spent >= 1000  THEN RETURN 'gold';
        WHEN v_spent > 0      THEN RETURN 'regular';
        ELSE                       RETURN 'prospect';
    END CASE;
END;
$$;

SELECT tier, count(*) FROM (SELECT customer_tier(id) AS tier FROM users) t GROUP BY tier ORDER BY count(*) DESC;
```

```text
   tier   | count
----------+-------
 regular  |    11
 prospect |    10
 gold     |     7
 platinum |     2
(4 rows)
```

Unlike SQL's `CASE` *expression*, the PL/pgSQL `CASE` *statement* raises `CASE_NOT_FOUND` if no branch matches and there's no `ELSE`. Note also that this query calls `customer_tier` once per user, and each call runs its own aggregate query. With 30 users that's fine. With 100,000 users it's the "N+1 queries" problem in the database. A single `GROUP BY` query with a `CASE` expression would do the same job in one pass. Functions make logic reusable, but they don't make row-by-row processing fast.

### 7.3 Loops

| Form | Use |
|---|---|
| `LOOP ... END LOOP` with `EXIT [WHEN cond]` | General loop |
| `WHILE cond LOOP ... END LOOP` | Pre-tested loop |
| `FOR i IN 1..10 [BY 2] LOOP` / `FOR i IN REVERSE 10..1 LOOP` | Integer ranges |
| `FOR r IN query LOOP` | Iterate over the rows of a query |
| `FOREACH x IN ARRAY arr LOOP` | Iterate over an array's elements |
| `CONTINUE [WHEN cond]` | Skip to the next iteration |

An `EXIT`-controlled loop, finding the first free SKU in a sequence:

```sql
CREATE FUNCTION next_free_sku(p_seller_id bigint, p_prefix text)
RETURNS text
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_n   int := 1;
    v_sku text;
BEGIN
    LOOP
        v_sku := p_prefix || '-' || lpad(v_n::text, 3, '0');
        EXIT WHEN NOT EXISTS (SELECT 1 FROM products WHERE seller_id = p_seller_id AND sku = v_sku);
        v_n := v_n + 1;
    END LOOP;
    RETURN v_sku;
END;
$$;

SELECT next_free_sku(1, 'TT-LAP'), next_free_sku(2, 'BW-SF'), next_free_sku(5, 'MP-NEW');
```

```text
 next_free_sku | next_free_sku | next_free_sku
---------------+---------------+---------------
 TT-LAP-008    | BW-SF-006     | MP-NEW-001
(1 row)
```

(Seller 1 has `TT-LAP-001` to `TT-LAP-007`.) `FOR r IN query` appeared in `order_summary`. It fetches rows in batches through a cursor, so it works for large results without loading them all into memory. `FOREACH` iterates over an array, and `CONTINUE WHEN` skips elements. A function that adds normalised tags to a product, skipping blanks and duplicates:

```sql
CREATE FUNCTION add_tags(p_product_id bigint, VARIADIC p_tags text[])
RETURNS text[]
LANGUAGE plpgsql
AS $$
DECLARE
    v_tags text[];
    v_tag  text;
BEGIN
    SELECT tags INTO STRICT v_tags FROM products WHERE id = p_product_id FOR UPDATE;

    FOREACH v_tag IN ARRAY p_tags LOOP
        v_tag := lower(trim(v_tag));
        CONTINUE WHEN v_tag = '' OR v_tag = ANY (v_tags);
        v_tags := v_tags || v_tag;
    END LOOP;

    UPDATE products SET tags = v_tags WHERE id = p_product_id;
    RETURN v_tags;
END;
$$;

BEGIN;
SELECT add_tags(10, 'Sale', 'anc', '  ', 'summer');
SELECT add_tags(999, 'x');
ROLLBACK;
```

```text
                     add_tags
--------------------------------------------------
 {headphones,wireless,anc,bestseller,sale,summer}
(1 row)

ERROR:  query returned no rows
CONTEXT:  PL/pgSQL function add_tags(bigint,text[]) line 6 at SQL statement
```

`INTO STRICT` turned "no such product" into an error instead of silently tagging nothing. `FOR UPDATE` locks the product row between the read and the write, so two concurrent `add_tags` calls on the same product can't overwrite each other's tags. Module 10 explains this "lost update" problem. This function writes, so it is (correctly) left `VOLATILE`.

### 7.4 Returning sets: `RETURN QUERY` and `RETURN NEXT`

A PL/pgSQL function declared `RETURNS SETOF ...` or `RETURNS TABLE (...)` builds its result incrementally. **`RETURN QUERY sql`** appends all rows of a query. **`RETURN NEXT`** appends one row: for `RETURNS TABLE`, the current values of the output columns, which are variables. The function ends at the final `RETURN` (or `END`), and only then does the caller receive the rows:

```sql
CREATE FUNCTION price_bands(p_width numeric DEFAULT 500)
RETURNS TABLE (band text, product_count int)
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_low numeric := 0;
    v_max numeric;
BEGIN
    SELECT max(price) INTO v_max FROM products WHERE status = 'active';
    WHILE v_low <= v_max LOOP
        band := format('%s-%s', v_low, v_low + p_width);
        SELECT count(*) INTO product_count
        FROM products p
        WHERE p.status = 'active' AND p.price >= v_low AND p.price < v_low + p_width;
        RETURN NEXT;               -- emit the current values of band and product_count
        v_low := v_low + p_width;
    END LOOP;
END;
$$;

SELECT * FROM price_bands();
```

```text
   band    | product_count
-----------+---------------
 0-500     |            49
 500-1000  |             2
 1000-1500 |             1
 1500-2000 |             1
 2000-2500 |             1
(5 rows)
```

> **Under the hood:** PL/pgSQL set-returning functions *materialize* their whole result (in memory, spilling to disk beyond `work_mem`) before returning anything. A function returning a million rows builds all million first. The caller can't stop early with `LIMIT` and save the work, and the planner can't push conditions into the function (section 5.3). For "a parameterised query", prefer a SQL function. Use PL/pgSQL when you need procedural logic between the rows.

### 7.5 The `RETURNS TABLE` name trap

In PL/pgSQL, `OUT` parameters, including `RETURNS TABLE` columns, are **variables**, visible inside every SQL statement in the function. If a column of a table has the same name, PostgreSQL refuses to guess:

```sql
CREATE FUNCTION root_categories()
RETURNS TABLE (id int, name text)
LANGUAGE plpgsql
STABLE
AS $$
BEGIN
    RETURN QUERY SELECT id, name FROM categories WHERE parent_id IS NULL ORDER BY id;
END;
$$;

SELECT * FROM root_categories();
```

```text
ERROR:  column reference "id" is ambiguous
LINE 1: SELECT id, name FROM categories WHERE parent_id IS NULL ORDE...
               ^
DETAIL:  It could refer to either a PL/pgSQL variable or a table column.
```

The fixes: qualify every column with a table alias (`SELECT c.id, c.name FROM categories c ...`), name output columns so they don't collide, or put `#variable_conflict use_column` at the top of the function body. The same applies to parameters, which is why this course prefixes them with `p_` and local variables with `v_`. SQL functions don't have this problem: there, a column name always wins over a parameter name.

---

## 8. Errors and exceptions

### 8.1 `RAISE`

`RAISE level 'format', args...` reports a message. `%` placeholders are replaced by the arguments.

| Level | Effect |
|---|---|
| `DEBUG`, `LOG`, `INFO`, `NOTICE`, `WARNING` | A message to the client and/or server log, depending on `client_min_messages` and `log_min_messages`. Execution continues |
| `EXCEPTION` (the default level) | Raises an **error**: the statement fails, and the transaction is aborted unless an exception handler catches it |

An error carries a **SQLSTATE**, a five-character code that clients use to recognize the error programmatically. Class `23` is integrity violations (`23505` unique, `23503` foreign key, `23514` check), `22` is data exceptions, `40001` is a serialization failure (module 10), and `P0001` is the generic default for `RAISE EXCEPTION`. You can attach your own code and extra fields with `USING`:

```sql
CREATE FUNCTION set_product_status(p_product_id bigint, p_new product_status)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    v_old product_status;
BEGIN
    SELECT status INTO v_old FROM products WHERE id = p_product_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'product % does not exist', p_product_id
            USING ERRCODE = 'no_data_found';
    END IF;

    IF v_old = p_new THEN
        RAISE NOTICE 'product % is already %', p_product_id, p_new;
        RETURN;
    END IF;

    IF NOT (v_old, p_new) IN (('draft', 'active'), ('active', 'archived'), ('archived', 'active')) THEN
        RAISE EXCEPTION 'cannot change product % from % to %', p_product_id, v_old, p_new
            USING ERRCODE = 'BZ010',
                  DETAIL  = 'Allowed: draft -> active, active <-> archived.',
                  HINT    = 'Archive the product instead of moving it back to draft.';
    END IF;

    UPDATE products SET status = p_new WHERE id = p_product_id;
END;
$$;
```

```sql
BEGIN;
SELECT set_product_status(14, 'active');   -- draft -> active: fine
SELECT set_product_status(14, 'active');   -- NOTICE:  product 14 is already active
SELECT set_product_status(3, 'draft');
ROLLBACK;
```

```text
ERROR:  cannot change product 3 from active to draft
DETAIL:  Allowed: draft -> active, active <-> archived.
HINT:  Archive the product instead of moving it back to draft.
CONTEXT:  PL/pgSQL function set_product_status(bigint,product_status) line 17 at RAISE
```

With `\set VERBOSITY verbose`, psql also shows the codes: `ERROR:  BZ010: cannot change product 3 ...` and `ERROR:  P0002: product 999 does not exist`. Error codes can be given by name (`'no_data_found'`, `'check_violation'`, `'invalid_parameter_value'`) or as five characters. For your own errors, pick a class that PostgreSQL doesn't use. Bazaar uses `BZ`: module 10's `place_order()` raises `BZ001`–`BZ004` (insufficient stock, missing product, ...). A client can then branch on `e.sqlstate == 'BZ001'` instead of parsing message text, which you are free to reword later.

`ASSERT condition, 'message'` is a debugging aid. It raises `ASSERT_FAILURE` when the condition is false, and can be switched off globally with `plpgsql.check_asserts = off`. Don't use it for validating input.

### 8.2 Catching errors: `EXCEPTION` blocks

A block can end with an `EXCEPTION` section that catches errors raised inside the block:

```sql
CREATE FUNCTION try_int(p_text text)
RETURNS int
LANGUAGE plpgsql
IMMUTABLE
AS $$
BEGIN
    RETURN p_text::int;
EXCEPTION
    WHEN invalid_text_representation OR numeric_value_out_of_range THEN
        RETURN NULL;
END;
$$;

SELECT try_int('42') AS ok, try_int('lots') AS bad, try_int('99999999999') AS too_big;   -- 42 | NULL | NULL
```

Conditions are matched by name, by `SQLSTATE 'xxxxx'`, or with `OTHERS` (everything except query cancellation and assertion failures). Inside the handler, `SQLSTATE` and `SQLERRM` hold the code and message, and **`GET STACKED DIAGNOSTICS`** retrieves everything else: `MESSAGE_TEXT`, `PG_EXCEPTION_DETAIL`, `PG_EXCEPTION_HINT`, `PG_EXCEPTION_CONTEXT` (the call stack), and `COLUMN_NAME`/`CONSTRAINT_NAME`/`TABLE_NAME` where they apply. `RAISE;` without arguments inside a handler re-raises the current error.

**How it works, and what it costs.** When a block with an `EXCEPTION` section starts, PL/pgSQL creates a **subtransaction** (a savepoint). If an error occurs, everything the block did is rolled back to that savepoint, the handler runs, and the function continues. Without the savepoint, an error would abort the whole transaction. This has real costs:

- **Every entry into the block** creates a subtransaction, even when no error happens. On this machine, 200,000 calls of `try_int` took about **166 ms**, against **100 ms** for the same function without the `EXCEPTION` clause. When every call *fails*, the cost was about **1,060 ms**, because raising and cleaning up an error is expensive.
- A subtransaction that **writes** gets its own transaction ID. A loop with an exception block that writes in every iteration burns one XID per iteration, which speeds up the need for anti-wraparound vacuuming (module 16). Once a transaction has more than 64 subtransactions, it overflows the shared subtransaction cache, which slows visibility checks for *all* sessions, including on replicas.

So: put exception blocks around the *specific* statements that may fail and that you can genuinely handle, not around everything "just in case". For validation, prefer functions that check without raising. PostgreSQL 16 added `pg_input_is_valid(text, type)` and `pg_input_error_info(text, type)`:

```sql
SELECT pg_input_is_valid('lots', 'integer') AS valid,
       (pg_input_error_info('99999999999', 'integer')).message AS why;
```

```text
 valid |                         why
-------+------------------------------------------------------
 f     | value "99999999999" is out of range for type integer
(1 row)
```

**When an exception block is right:** processing a batch where one bad item must not stop the others, with the failure recorded. A one-off price import, as a `DO` block (section 11.3):

```sql
BEGIN;
DO $$
DECLARE
    r          record;
    v_state    text;
    v_message  text;
    v_detail   text;
    v_ok       int := 0;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES (1, 949.00), (2, -5.00), (999, 10.00), (4, 229.00)) AS v(product_id, new_price)
    LOOP
        BEGIN
            UPDATE products SET price = r.new_price WHERE id = r.product_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'no product %', r.product_id USING ERRCODE = 'no_data_found';
            END IF;
            v_ok := v_ok + 1;
        EXCEPTION WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS
                v_state   = RETURNED_SQLSTATE,
                v_message = MESSAGE_TEXT,
                v_detail  = PG_EXCEPTION_DETAIL;
            RAISE WARNING 'product %: [%] % %', r.product_id, v_state, v_message, coalesce(v_detail, '');
        END;
    END LOOP;
    RAISE NOTICE '% of 4 prices updated', v_ok;
END;
$$;
ROLLBACK;
```

```text
WARNING:  product 2: [23514] new row for relation "products" violates check constraint "products_price_nonnegative" Failing row contains (2, 1, 3, TT-LAP-002, ...).
WARNING:  product 999: [P0002] no product 999
NOTICE:  2 of 4 prices updated
```

Products 1 and 4 got their new prices. The negative price and the unknown id were reported and skipped, and each failed `UPDATE` was rolled back to its own savepoint.

> **Pitfall: swallowing errors.** `EXCEPTION WHEN OTHERS THEN NULL;` hides every bug, including ones you never imagined, such as a misspelled column after a migration. If you catch `OTHERS`, log what you caught (as above), or re-raise with `RAISE;`.

---

## 9. Dynamic SQL

PL/pgSQL statements are planned once and reused, so their structure is fixed: table and column names can't come from variables. When the *structure* of a query depends on runtime values, you build the SQL text and run it with **`EXECUTE`**. Typical cases: a table name chosen at runtime (partition maintenance in module 14), a user-chosen sort column, or a maintenance loop over many tables.

```text
EXECUTE 'SELECT ...' [INTO target] [USING value1, value2, ...];
RETURN QUERY EXECUTE 'SELECT ...' USING ...;
```

The danger is obvious as soon as the text contains user input. A product listing with a caller-chosen sort order, written naively:

```sql
CREATE FUNCTION products_sorted_unsafe(p_sort text)
RETURNS SETOF products
LANGUAGE plpgsql
STABLE
AS $$
BEGIN
    RETURN QUERY EXECUTE 'SELECT * FROM products WHERE status = ''active'' ORDER BY ' || p_sort || ' LIMIT 3';
END;
$$;

SELECT id, title, price FROM products_sorted_unsafe('price DESC');   -- works as intended
SELECT id FROM products_sorted_unsafe('(SELECT email::int FROM users WHERE id = 17)');
```

```text
ERROR:  invalid input syntax for type integer: "Zoe.Martin@Example.COM"
CONTEXT:  SQL statement "SELECT * FROM products WHERE status = 'active' ORDER BY (SELECT email::int FROM users WHERE id = 17) LIMIT 3"
```

The "sort column" was a subquery, and the error message *leaked another user's e-mail address*. That's a textbook error-based SQL injection, and it happened inside the database, behind a function that the application called with properly bound parameters. Parameter binding in the client (module 22) protects the call to the function. It can't protect SQL that the function builds by concatenation.

The safe pattern has three parts:

```sql
CREATE FUNCTION products_sorted(p_sort_column text, p_descending boolean DEFAULT false, p_limit int DEFAULT 3)
RETURNS SETOF products
LANGUAGE plpgsql
STABLE
AS $$
BEGIN
    IF p_sort_column NOT IN ('id', 'title', 'price', 'created_at') THEN            -- 1. whitelist
        RAISE EXCEPTION 'cannot sort by %', p_sort_column USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY EXECUTE format(
        'SELECT * FROM products WHERE status = $1 ORDER BY %I %s LIMIT $2',      -- 2. %I for identifiers
        p_sort_column,
        CASE WHEN p_descending THEN 'DESC' ELSE 'ASC' END)
    USING 'active'::product_status, p_limit;                                        -- 3. values via USING
END;
$$;

SELECT id, title, price FROM products_sorted('price', true);
```

```text
 id |          title          |  price
----+-------------------------+---------
  3 | Titan G17 Gaming Laptop | 2199.99
 15 | Titan G15 Gaming Laptop | 1799.00
  2 | AeroBook 15 Pro Laptop  | 1499.00
(3 rows)
```

1. **Whitelist** what may vary structurally. Here it's the sort column. `DESC`/`ASC` comes from a boolean, never from text.
2. **`format()` with `%I`** quotes an identifier (table or column name) when needed, so even a malicious "column name" becomes a harmless, non-existent quoted identifier. **`%L`** quotes a literal value, turning NULL into `NULL`. `%s` inserts text *as is*, so use it only for fragments you constructed yourself.

   ```sql
   SELECT format('SELECT %I, %L, %s', 'Order', 'O''Connor', 42);   -- SELECT "Order", 'O''Connor', 42
   ```

3. **Pass values with `USING`** as `$1`, `$2`, ..., exactly like bound parameters. They're never parsed as SQL at all. Prefer `USING` over `%L` whenever the value is data rather than structure.

Dynamic statements are planned on every execution (no plan caching), which is usually fine, because statements that need dynamic SQL tend to be the expensive kind anyway.

---

## 10. `SECURITY DEFINER`: a preview

By default a function runs with the privileges of the **caller** (`SECURITY INVOKER`). A `SECURITY DEFINER` function runs with the privileges of its **owner**, like a setuid program. That makes it the standard tool for controlled privilege escalation: an application role with no `UPDATE` privilege on `products` may still call `set_product_status()`, which does exactly one well-validated thing. Module 15 builds Bazaar's roles around this, and makes the audit trigger below a `SECURITY DEFINER` function so that application roles can't write to `audit_log` directly.

The one rule to learn now: **every `SECURITY DEFINER` function must pin its `search_path`**:

```sql
CREATE FUNCTION archive_product(p_product_id bigint)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = bazaar, pg_temp
AS $$
    UPDATE products SET status = 'archived' WHERE id = p_product_id AND status = 'active';
$$;
```

Without the `SET`, an unqualified `products` inside the function would be resolved through the *caller's* `search_path`. A caller who can create objects in some schema could put their own `products` table, or a malicious `=` operator, earlier in the path, and the function would happily run their object *with the owner's privileges*. `pg_temp` is listed last explicitly, because otherwise the session's temporary schema is searched *first*. A `SET` clause also prevents inlining (section 5.3), which rarely matters for this kind of function.

---

## 11. Procedures and `DO` blocks

### 11.1 Procedures

A function runs inside the caller's transaction: everything it does commits or rolls back with the statement that called it. A **procedure** (PostgreSQL 11+) is invoked with `CALL`, returns no value (except through `INOUT` parameters), and, the crucial difference, **can commit and start transactions in its body**. That makes procedures the right tool for batch jobs. A huge `DELETE` in one transaction holds its locks for the whole run, can't be interrupted without losing all its progress, and keeps old row versions alive for the entire time, which blocks vacuum. Splitting it into batches with a `COMMIT` after each one avoids all three problems.

Purging abandoned carts in batches:

```sql
CREATE PROCEDURE purge_stale_carts(p_before timestamptz, p_batch_size int DEFAULT 1000)
LANGUAGE plpgsql
AS $$
DECLARE
    v_deleted int;
    v_total   int := 0;
BEGIN
    LOOP
        DELETE FROM cart_items
        WHERE (user_id, product_id) IN (
            SELECT user_id, product_id
            FROM cart_items
            WHERE added_at < p_before
            LIMIT p_batch_size
        );
        GET DIAGNOSTICS v_deleted = ROW_COUNT;
        EXIT WHEN v_deleted = 0;

        v_total := v_total + v_deleted;
        COMMIT;   -- end this batch's transaction; a new one starts automatically
        RAISE NOTICE 'deleted % (total %)', v_deleted, v_total;
    END LOOP;
END;
$$;

CALL purge_stale_carts('2025-06-25', 3);
```

```text
NOTICE:  deleted 3 (total 3)
NOTICE:  deleted 3 (total 6)
NOTICE:  deleted 2 (total 8)
CALL
```

(This one really deletes: 8 of the 14 cart items are gone afterwards. Reset when you're done.) If the procedure fails in the third batch, the first two stay committed, so write batch jobs to be **re-runnable**: each batch must leave the data consistent, and running the procedure again must simply continue. The `LIMIT` subquery picks *any* matching rows, and that's fine for a purge. On the large dataset you'd want an index on `added_at` so that each batch doesn't rescan the table (module 11).

The rules around transaction control:

- `COMMIT`/`ROLLBACK` work only when the procedure is called at the top level, or from other procedures or `DO` blocks called that way. Calling it inside an explicit transaction block fails:

  ```text
  BEGIN;
  CALL purge_stale_carts('2025-06-27', 3);
  ERROR:  invalid transaction termination
  ```

- No `COMMIT` inside a block that has an `EXCEPTION` section (the subtransaction can't outlive its parent), and none in a procedure called from a function.
- `SELECT purge_stale_carts(...)` is an error (`... is a procedure`, `HINT: To call a procedure, use CALL.`).
- Locks and snapshots are released at every `COMMIT`. The next batch sees data committed by others in between, which is exactly what you want for long-running maintenance.

### 11.2 Functions or procedures?

| | Function | Procedure |
|---|---|---|
| Called with | Any SQL expression (`SELECT f()`, in `WHERE`, in triggers) | `CALL p()` only |
| Returns | A value, a row, or a set | Nothing (`INOUT` parameters only) |
| Transaction control | No | `COMMIT`/`ROLLBACK` inside |
| Typical use | Calculations, queries, trigger bodies, API-style operations | Batch jobs, maintenance, migrations of data |

### 11.3 `DO` blocks

`DO $$ ... $$` runs an anonymous PL/pgSQL block once, without creating anything: for scripts, migrations and one-off fixes. The price import in section 8.2 was one. A `DO` block takes no parameters and returns nothing (use `RAISE NOTICE` to report), and it can `COMMIT` when run at the top level, just like a procedure. psql variables are not interpolated inside dollar-quoted bodies. A common trick to pass values in is `SET my.var = '...'` followed by `current_setting('my.var')` inside the block.

---

## 12. Triggers: the model

A **trigger** binds a function to an event on a table: "after every `UPDATE` of a row in `products`, run `audit_trigger()`". The application doesn't call it and can't forget it, and every client, migration script and psql session gets the same behaviour. That's the power and the danger of triggers in one sentence.

### 12.1 The dimensions of a trigger

```text
CREATE [CONSTRAINT] TRIGGER name
    { BEFORE | AFTER | INSTEAD OF } { INSERT | UPDATE [OF column, ...] | DELETE | TRUNCATE } [OR ...]
    ON table
    [REFERENCING { OLD | NEW } TABLE AS name ...]
    [FOR EACH { ROW | STATEMENT }]
    [WHEN (condition)]
    EXECUTE FUNCTION function_name(arguments);
```

| Choice | Options | Meaning |
|---|---|---|
| Timing | `BEFORE` | Runs before the row is written. A row trigger can **modify** the row (`NEW`) or **skip** it |
| | `AFTER` | Runs after the row is written (in fact, at the end of the statement). It sees the final row and can react: write elsewhere, or raise to abort |
| | `INSTEAD OF` | Views only, row level: replaces the operation entirely (section 13.2) |
| Level | `FOR EACH ROW` | Once per affected row, with `NEW`/`OLD` |
| | `FOR EACH STATEMENT` (default) | Once per statement, **even if it affects zero rows**. Sees all rows through transition tables (section 15) |
| Filter | `UPDATE OF col, ...` | Fire only if one of these columns appears in the `SET` list (even if its value didn't change) |
| | `WHEN (condition)` | Fire only if the condition on `OLD`/`NEW` is true. Evaluated *before* calling the function, so it's cheap |

A trigger **function** is a function with no declared arguments that `RETURNS trigger`. Values given in `CREATE TRIGGER ... EXECUTE FUNCTION f('a', 'b')` reach the function as the text array `TG_ARGV`. One function can serve many triggers on many tables, which is how the generic audit trigger in section 14 works.

### 12.2 Inside a trigger function

PL/pgSQL gives a trigger function special variables:

| Variable | Content |
|---|---|
| `NEW` | The new row (`INSERT`/`UPDATE`, row level). NULL for `DELETE` |
| `OLD` | The old row (`UPDATE`/`DELETE`, row level). NULL for `INSERT` |
| `TG_OP` | `'INSERT'`, `'UPDATE'`, `'DELETE'` or `'TRUNCATE'` |
| `TG_WHEN` / `TG_LEVEL` | `'BEFORE'`/`'AFTER'`/`'INSTEAD OF'`, and `'ROW'`/`'STATEMENT'` |
| `TG_NAME`, `TG_TABLE_NAME`, `TG_TABLE_SCHEMA`, `TG_RELID` | Which trigger fired, on which table |
| `TG_ARGV`, `TG_NARGS` | The trigger's arguments, as a text array **indexed from 0** |

A tracer that prints them all, attached three ways to `inventory`:

```sql
BEGIN;
CREATE FUNCTION show_trigger_context()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE NOTICE '% % % % on %.% args=% depth=%',
        TG_NAME, TG_WHEN, TG_LEVEL, TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_ARGV, pg_trigger_depth();
    IF TG_LEVEL = 'ROW' THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    RETURN NULL;
END;
$$;
CREATE TRIGGER a_before_row  BEFORE UPDATE ON inventory FOR EACH ROW       EXECUTE FUNCTION show_trigger_context('hello', 42);
CREATE TRIGGER b_before_stmt BEFORE UPDATE ON inventory FOR EACH STATEMENT EXECUTE FUNCTION show_trigger_context();
CREATE TRIGGER c_after_row   AFTER  UPDATE ON inventory FOR EACH ROW       EXECUTE FUNCTION show_trigger_context();

UPDATE inventory SET quantity = quantity + 1 WHERE product_id IN (1, 2);
UPDATE inventory SET quantity = quantity + 1 WHERE false;
ROLLBACK;
```

```text
NOTICE:  b_before_stmt BEFORE STATEMENT UPDATE on bazaar.inventory args=<NULL> depth=1
NOTICE:  a_before_row BEFORE ROW UPDATE on bazaar.inventory args=[0:1]={hello,42} depth=1
NOTICE:  a_before_row BEFORE ROW UPDATE on bazaar.inventory args=[0:1]={hello,42} depth=1
NOTICE:  c_after_row AFTER ROW UPDATE on bazaar.inventory args=<NULL> depth=1
NOTICE:  c_after_row AFTER ROW UPDATE on bazaar.inventory args=<NULL> depth=1
UPDATE 2

NOTICE:  b_before_stmt BEFORE STATEMENT UPDATE on bazaar.inventory args=<NULL> depth=1
UPDATE 0
```

The order of events for one statement:

```text
BEFORE STATEMENT triggers
for each row:
    BEFORE ROW triggers  (may change NEW, or return NULL to skip the row)
    write the row, check constraints, update indexes
    queue the AFTER ROW events
AFTER ROW triggers, for all rows, in order       (at the end of the statement)
AFTER STATEMENT triggers
```

Note that the statement-level trigger fired for an `UPDATE` that matched no rows, and that `TG_ARGV` really does start at index 0 (`[0:1]={hello,42}`). That's one of the few places where PostgreSQL uses a non-default array lower bound.

### 12.3 Return values

| Trigger | Return |
|---|---|
| `BEFORE ... FOR EACH ROW` | `NEW` (possibly modified) to proceed; `NULL` to **silently skip** this row; for `DELETE`, return `OLD` to proceed |
| `AFTER` (any level), `BEFORE ... FOR EACH STATEMENT` | Ignored. Return `NULL` by convention. Raise an error to abort |
| `INSTEAD OF` | `NEW` (or `OLD` for delete) to count the row as processed; `NULL` to report it as not processed |

Returning NULL from a `BEFORE` row trigger by accident is a classic bug, because nothing complains:

```sql
BEGIN;
CREATE FUNCTION skip_free_products() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.price = 0 THEN
        RETURN NULL;     -- silently skip this row
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER products_skip_free BEFORE UPDATE ON products FOR EACH ROW EXECUTE FUNCTION skip_free_products();

UPDATE products SET price = 0 WHERE id IN (61, 62);          -- UPDATE 0
UPDATE products SET title = title || '!' WHERE id IN (61, 62); -- UPDATE 1: product 62 (free) is skipped
ROLLBACK;
```

The command tag reports the rows that were actually written, and there is no error or warning. If a `BEFORE` trigger means "this must not happen", it should `RAISE` an error, not return NULL.

### 12.4 Firing order

When several triggers fire for the same event and timing, PostgreSQL runs them in **alphabetical order of their names**. That's the whole rule. There's no priority setting. When order matters (one `BEFORE` trigger normalises a value that another one validates), make the names say so: `products_10_normalize_tags`, `products_20_validate`. `BEFORE` triggers chain: each one receives the `NEW` returned by the previous one.

---

## 13. Worked example: `set_updated_at`

Every `UPDATE` should set `updated_at`, and relying on every query to say `SET ..., updated_at = now()` fails the first time someone runs a manual fix in psql. A `BEFORE UPDATE` row trigger makes it automatic:

```sql
CREATE FUNCTION set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- A BEFORE trigger may modify NEW; the modified row is what gets stored.
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER products_set_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER inventory_set_updated_at
    BEFORE UPDATE ON inventory
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
```

The same function serves every table with an `updated_at` column. Orders don't have one yet, so the reference migration adds it and backfills it with the latest lifecycle timestamp before attaching the third trigger:

```sql
ALTER TABLE orders ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
UPDATE orders SET updated_at = greatest(created_at, paid_at, shipped_at, delivered_at);   -- greatest() ignores NULLs

CREATE TRIGGER orders_set_updated_at
    BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
```

(The order matters: if the trigger existed before the backfill, the backfill's `UPDATE` would set every `updated_at` to `now()`. Adding a column with a constant or `STABLE` default like `now()` doesn't rewrite the table. The default is evaluated once and stored in the catalog.)

```sql
BEGIN;
SELECT now() AS tx_start;
UPDATE products SET price = 219.00 WHERE id = 10 RETURNING id, price, updated_at;
UPDATE products SET price = 219.00, updated_at = '2000-01-01' WHERE id = 10 RETURNING updated_at;
ROLLBACK;
```

```text
           tx_start
-------------------------------
 2026-10-14 08:44:47.993519+00

 id | price  |          updated_at
----+--------+-------------------------------
 10 | 219.00 | 2026-10-14 08:44:47.993519+00

          updated_at
-------------------------------
 2026-10-14 08:44:47.993519+00
```

(Your timestamps will differ.) Two details. `now()` is the *transaction's* start time, so every row changed in one transaction gets the same `updated_at`. That's usually what you want, because it says "these changes belong together". Use `clock_timestamp()` if you need the actual time of each write. And the trigger wins over the explicit `updated_at = '2000-01-01'`: the application can no longer forge the column. That's the point, but it also means a data migration that *wants* to set historical values has to disable the trigger (`ALTER TABLE ... DISABLE TRIGGER products_set_updated_at`) for its duration.

### 13.1 A `WHEN` condition: alerts only on the crossing

`WHEN` makes a trigger fire only on interesting changes, and it's checked without calling the function. A low-stock alert that fires when availability *drops to* 2 or below, not on every update of an already-low product:

```sql
BEGIN;
CREATE TABLE stock_alerts (
    product_id bigint NOT NULL,
    available  int NOT NULL,
    raised_at  timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION raise_stock_alert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO stock_alerts (product_id, available)
    VALUES (NEW.product_id, NEW.quantity - NEW.reserved);
    RETURN NULL;
END;
$$;

CREATE TRIGGER inventory_low_stock
    AFTER UPDATE OF quantity, reserved ON inventory
    FOR EACH ROW
    WHEN (NEW.quantity - NEW.reserved <= 2 AND OLD.quantity - OLD.reserved > 2)
    EXECUTE FUNCTION raise_stock_alert();

UPDATE inventory SET reserved = reserved + 2 WHERE product_id IN (15, 16);   -- 15: 4 -> 2 available; 16: 59 -> 57
UPDATE inventory SET reserved = reserved + 1 WHERE product_id = 15;          -- 15: 2 -> 1, already low
SELECT product_id, available FROM stock_alerts;
ROLLBACK;
```

```text
 product_id | available
------------+-----------
         15 |         2
(1 row)
```

One alert, for the crossing only. `WHEN` can't refer to `NEW` in a `DELETE` trigger or `OLD` in an `INSERT` trigger, and a statement-level trigger's `WHEN` can't refer to `NEW` or `OLD` at all, since there is no single row. The most common `WHEN` of all is `WHEN (OLD.x IS DISTINCT FROM NEW.x)`, "only if x actually changed", which you'll use in the exercises. Note `IS DISTINCT FROM` rather than `<>`, which would be NULL (and so not true) when either side is NULL.

### 13.2 `INSTEAD OF` triggers: making a join view writable

Section 3 left `active_products` read-only. An `INSTEAD OF` trigger on a view receives each row the statement targets (`OLD`) and the requested new values (`NEW`), and does whatever is appropriate instead:

```sql
BEGIN;
CREATE FUNCTION active_products_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id <> OLD.id OR NEW.shop_name <> OLD.shop_name OR NEW.available <> OLD.available THEN
        RAISE EXCEPTION 'only title, description, price, attributes and tags can be changed through active_products';
    END IF;
    UPDATE products
    SET title = NEW.title, description = NEW.description, price = NEW.price,
        attributes = NEW.attributes, tags = NEW.tags
    WHERE id = OLD.id;
    RETURN NEW;
END $$;

CREATE TRIGGER active_products_update
    INSTEAD OF UPDATE ON active_products
    FOR EACH ROW EXECUTE FUNCTION active_products_update();

UPDATE active_products SET price = 899.00 WHERE id = 1 RETURNING id, title, price;   -- 1 | AeroBook 13 Laptop | 899.00
UPDATE active_products SET available = 100 WHERE id = 1;                             -- ERROR: only title, ...
ROLLBACK;
```

`INSTEAD OF` triggers are how you keep an old table name working as a view while a migration restructures the table underneath (module 26), or how you expose a simplified write interface. They are row-level only.

---

## 14. Worked example: a generic audit log

"Who changed the price of product 10, and when?" An audit trigger records every change to chosen tables. The reference design uses **one table and one function for all audited tables**, which relies on two module 08 tools: `to_jsonb(row)` serialises any row without knowing its columns, and `jsonb` stores rows of any shape.

```sql
CREATE TABLE audit_log (
    id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_name  text        NOT NULL,
    operation   text        NOT NULL,      -- 'INSERT' | 'UPDATE' | 'DELETE'
    row_pk      jsonb,                     -- e.g. {"id": 42}
    old_row     jsonb,                     -- NULL for INSERT
    new_row     jsonb,                     -- NULL for DELETE
    changed_by  text        NOT NULL DEFAULT current_user,
    changed_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT audit_log_operation_check CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE'))
);

CREATE FUNCTION audit_trigger()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_old  jsonb;
    v_new  jsonb;
    v_pk   jsonb;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        v_old := to_jsonb(OLD);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        v_new := to_jsonb(NEW);
    END IF;

    -- Skip no-op updates (UPDATE t SET x = x): nothing worth auditing.
    -- updated_at is ignored in the comparison because set_updated_at() changes
    -- it on every UPDATE.
    IF TG_OP = 'UPDATE' AND (v_old - 'updated_at') = (v_new - 'updated_at') THEN
        RETURN NULL;
    END IF;

    -- Build {"pk_col": value, ...} from the trigger arguments.
    SELECT jsonb_object_agg(col, coalesce(v_new, v_old) -> col)
    INTO v_pk
    FROM unnest(TG_ARGV) AS col;

    INSERT INTO audit_log (table_name, operation, row_pk, old_row, new_row, changed_by)
    VALUES (TG_TABLE_NAME, TG_OP, v_pk, v_old, v_new, session_user);

    RETURN NULL;  -- the return value of an AFTER row trigger is ignored
END;
$$;

CREATE TRIGGER products_audit
    AFTER INSERT OR UPDATE OR DELETE ON products
    FOR EACH ROW EXECUTE FUNCTION audit_trigger('id');

CREATE TRIGGER orders_audit
    AFTER INSERT OR UPDATE OR DELETE ON orders
    FOR EACH ROW EXECUTE FUNCTION audit_trigger('id');
```

The design decisions:

- **`AFTER`, not `BEFORE`.** An audit record should describe what was *actually* written, after every `BEFORE` trigger (including `set_updated_at`) has had its say, and only for rows that weren't skipped.
- **The primary-key columns come in as trigger arguments** (`audit_trigger('id')`), so the function has no table-specific code. A table with a composite key would say `audit_trigger('order_id', 'product_id')`.
- **No-op updates are skipped.** The comparison ignores `updated_at`, because `set_updated_at` changes it on every `UPDATE`. jsonb's equality operator compares whole documents regardless of key order (module 08).
- **`session_user`, not `current_user`.** Module 15 makes this function `SECURITY DEFINER`, and inside such a function `current_user` is the function's *owner*. `session_user` is the role that logged in. In a real application, every request uses the same database role, so you'd also record the application's user id, typically from a setting like `current_setting('app.user_id', true)` that the application sets per transaction.

```sql
BEGIN;
UPDATE products SET price = 219.00 WHERE id = 10;
UPDATE products SET price = price WHERE id = 11;                     -- no-op: not audited
INSERT INTO products (seller_id, category_id, sku, title, price)
VALUES (6, 6, 'SG-AUD-001', 'Sakura Earbuds', 59.00)
RETURNING id AS new_id \gset
DELETE FROM products WHERE id = :new_id;

SELECT id, table_name, operation, row_pk, changed_by FROM audit_log ORDER BY id;
```

`\gset` stores the new product's id in the psql variable `new_id`. Here it's 63. If you ran the rolled-back `INSERT` of section 3, yours is 64, because a rollback doesn't give sequence values back.

```text
 id | table_name | operation |   row_pk   | changed_by
----+------------+-----------+------------+------------
  1 | products   | UPDATE    | {"id": 10} | postgres
  2 | products   | INSERT    | {"id": 63} | postgres
  3 | products   | DELETE    | {"id": 63} | postgres
(3 rows)
```

And "what exactly changed?" is a module 08 query over the two documents:

```sql
SELECT key, old_row -> key AS old_value, new_row -> key AS new_value
FROM audit_log, jsonb_object_keys(new_row) AS key
WHERE id = 1
  AND old_row -> key IS DISTINCT FROM new_row -> key;
ROLLBACK;
```

```text
    key     |          old_value          |             new_value
------------+-----------------------------+------------------------------------
 price      | 229.00                      | 219.00
 updated_at | "2025-01-15T00:00:00+00:00" | "2026-10-14T08:44:56.182108+00:00"
(2 rows)
```

> **In production:** an audit table only grows. Plan its retention from day one: partition it by month and drop old partitions (module 14), or ship it to cheaper storage. Storing full old and new rows is simple and robust, but it costs about two row copies per change. Storing only the changed keys is smaller but harder to query. Logical decoding (module 19) can produce the same change stream with no trigger overhead at all.

---

## 15. Worked example: `orders.total` with transition tables

`orders.total` is denormalised: it must equal `sum(quantity * unit_price)` over the order's items (module 07 justified the denormalisation). Keeping it correct is a job for a trigger on `order_items`, and it shows off **statement-level triggers with transition tables**.

A row-level trigger would work, but it fires once per item. Inserting an order with 3 items would update the same `orders` row 3 times, creating 3 new row versions (MVCC, module 10). A **statement-level** trigger fires once per statement, and since PostgreSQL 10 it can see *all* the affected rows through **transition tables**, declared with `REFERENCING`:

- `REFERENCING NEW TABLE AS new_items`: the rows inserted, or the new versions of updated rows;
- `REFERENCING OLD TABLE AS old_items`: the rows deleted, or the old versions of updated rows.

Inside the function they're queried like ordinary tables, so one set-based `UPDATE` handles any number of orders:

```sql
CREATE FUNCTION order_items_maintain_total()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- A transition table only exists for the events that define it, so each
    -- branch references only the tables available for TG_OP.
    IF TG_OP = 'INSERT' THEN
        UPDATE orders o
        SET total = o.total + d.delta
        FROM (
            SELECT order_id, sum(quantity * unit_price) AS delta
            FROM new_items
            GROUP BY order_id
        ) d
        WHERE o.id = d.order_id
          AND d.delta <> 0;
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE orders o
        SET total = o.total - d.delta
        FROM (
            SELECT order_id, sum(quantity * unit_price) AS delta
            FROM old_items
            GROUP BY order_id
        ) d
        WHERE o.id = d.order_id
          AND d.delta <> 0;
    ELSE  -- UPDATE (may even move an item to another order)
        UPDATE orders o
        SET total = o.total + d.delta
        FROM (
            SELECT order_id, sum(delta) AS delta
            FROM (
                SELECT order_id, quantity * unit_price AS delta FROM new_items
                UNION ALL
                SELECT order_id, -(quantity * unit_price) FROM old_items
            ) changes
            GROUP BY order_id
        ) d
        WHERE o.id = d.order_id
          AND d.delta <> 0;
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER order_items_total_ins
    AFTER INSERT ON order_items
    REFERENCING NEW TABLE AS new_items
    FOR EACH STATEMENT EXECUTE FUNCTION order_items_maintain_total();

CREATE TRIGGER order_items_total_upd
    AFTER UPDATE ON order_items
    REFERENCING OLD TABLE AS old_items NEW TABLE AS new_items
    FOR EACH STATEMENT EXECUTE FUNCTION order_items_maintain_total();

CREATE TRIGGER order_items_total_del
    AFTER DELETE ON order_items
    REFERENCING OLD TABLE AS old_items
    FOR EACH STATEMENT EXECUTE FUNCTION order_items_maintain_total();
```

Why three triggers for one function? A trigger with transition tables may fire on only **one** event:

```text
CREATE TRIGGER bad AFTER INSERT OR DELETE ON order_items
    REFERENCING NEW TABLE AS n FOR EACH STATEMENT EXECUTE FUNCTION order_items_maintain_total();
ERROR:  transition tables cannot be specified for triggers with more than one event
```

**Why deltas, not recomputation?** The obvious alternative is `SET total = (SELECT sum(...) FROM order_items WHERE order_id = o.id)`. Under concurrency, it loses updates. Two transactions add an item to the same order at the same time. The second one's `UPDATE orders` waits for the first to commit, then re-reads the *latest* orders row, but its `SELECT sum(...)` still sees the order items as of its own (older) snapshot, *without* the other transaction's item. The total is then silently wrong. With deltas, the second transaction adds its own change to the latest committed total, which is correct whatever the other transaction did. Module 10 explains exactly why the `UPDATE` re-reads the latest row while the subquery doesn't. The price of deltas: they assume `total` was right to begin with, and anything that bypasses the trigger (a `TRUNCATE`, `session_replication_role = replica` during a bulk load, `ALTER TABLE ... DISABLE TRIGGER`) breaks that assumption. `project/db/tools/check.sql` verifies the invariant, and a periodic check job is wise in production.

With both the audit trigger on `orders` and the total triggers in place:

```sql
BEGIN;
INSERT INTO orders (user_id, seller_id, shipping_address_id)
VALUES (8, 2, (SELECT id FROM addresses WHERE user_id = 8 AND is_default))
RETURNING id, total;                                                       -- e.g. 121 | 0.00
SELECT currval('orders_id_seq') AS o \gset

INSERT INTO order_items (order_id, product_id, quantity, unit_price)
VALUES (:o, 19, 2, 14.99), (:o, 21, 1, 18.00), (:o, 24, 1, 11.99);
SELECT id, total FROM orders WHERE id = :o;                                -- 59.97

UPDATE order_items SET quantity = 3 WHERE order_id = :o AND product_id = 19;
DELETE FROM order_items WHERE order_id = :o AND product_id = 24;

SELECT total, (SELECT sum(quantity * unit_price) FROM order_items WHERE order_id = :o) AS recomputed
FROM orders WHERE id = :o;
SELECT operation, old_row ->> 'total' AS old_total, new_row ->> 'total' AS new_total
FROM audit_log WHERE table_name = 'orders' ORDER BY id;
ROLLBACK;
```

```text
 total | recomputed
-------+------------
 62.97 |      62.97
(1 row)

 operation | old_total | new_total
-----------+-----------+-----------
 INSERT    |           | 0.00
 UPDATE    | 0.00      | 59.97
 UPDATE    | 59.97     | 74.96
 UPDATE    | 74.96     | 62.97
(4 rows)
```

(`\gset` stores the new order id in the psql variable `:o`, as in module 01, so the example works whatever id the sequence hands out.) The three-row insert produced **one** update of the order (0.00 → 59.97). The audit log also shows triggers cascading: a statement on `order_items` fired a trigger that updated `orders`, which fired `orders_set_updated_at` and `orders_audit`.

> **Under the hood:** foreign keys are implemented as triggers too. `SELECT tgname FROM pg_trigger WHERE tgrelid = 'order_items'::regclass` lists four `RI_ConstraintTrigger_...` entries (two per foreign key) next to the three total triggers. They're internal (`tgisinternal`), so `\d` hides them, but they fire for every row like any other trigger, which is part of why bulk loads into tables with many foreign keys are slow.

---

## 16. Trigger pitfalls

### 16.1 Recursion

A trigger that modifies its own table fires itself again. "When a product changes, touch its seller's other products":

```sql
BEGIN;
CREATE FUNCTION touch_seller_products() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE products SET updated_at = now() WHERE seller_id = NEW.seller_id AND id <> NEW.id;
    RETURN NULL;
END $$;
CREATE TRIGGER products_touch AFTER UPDATE ON products FOR EACH ROW EXECUTE FUNCTION touch_seller_products();
UPDATE products SET price = price + 1 WHERE id = 57;
ROLLBACK;
```

```text
ERROR:  stack depth limit exceeded
HINT:  Increase the configuration parameter "max_stack_depth" (currently 2048kB), after ensuring the platform's stack depth limit is adequate.
CONTEXT:  ...
SQL statement "UPDATE products SET updated_at = now() WHERE seller_id = NEW.seller_id AND id <> NEW.id"
PL/pgSQL function touch_seller_products() line 3 at SQL statement
SQL statement "UPDATE products SET updated_at = now() WHERE seller_id = NEW.seller_id AND id <> NEW.id"
PL/pgSQL function touch_seller_products() line 3 at SQL statement
... (hundreds more)
```

Each update of the other products fired the trigger for *them*, and so on. Fixes, from best to worst:

1. **Don't write to the same table from an `AFTER` trigger.** If the change concerns the row itself, use a `BEFORE` trigger and modify `NEW`, which fires nothing.
2. **Narrow the trigger** so that the recursive write doesn't match: `AFTER UPDATE OF price` (the inner update only touches `updated_at`), or a `WHEN` condition.
3. **Guard with `pg_trigger_depth()`**, which returns how deeply nested the current trigger is (1 for a trigger fired by a top-level statement):

   ```text
   IF pg_trigger_depth() > 1 THEN
       RETURN NULL;   -- fired by our own UPDATE: stop here
   END IF;
   ```

   This works, but it also stops the trigger when the update legitimately comes from *another* trigger, so use it knowingly.

### 16.2 Hidden work and surprising semantics

- **Triggers are invisible at the call site.** `UPDATE products SET price = ...` looks like one write. With Bazaar's triggers it's also an `updated_at` change and an `audit_log` insert. Teams that use triggers should list them in the table's documentation (`COMMENT ON TRIGGER`) and know `\d table`, which shows them.
- **`TRUNCATE` doesn't fire row triggers** (only `ON TRUNCATE` statement triggers), so a truncated `order_items` leaves every `orders.total` stale. `COPY FROM`, on the other hand, fires row triggers like `INSERT` does. Bulk tools that set `session_replication_role = replica` disable ordinary triggers *and* foreign-key checks.
- **Statement triggers fire for zero rows**, and `AFTER` row triggers run at the end of the statement, not immediately after each row. A row trigger that queries its own table sees the statement's earlier rows but not its later ones.
- **Ordering is alphabetical** (section 12.4), so renaming a trigger can change behaviour.

### 16.3 Constraint triggers: checks at commit time

Some rules can only be checked once a transaction is complete. "Every order has at least one item" is false right after the `INSERT INTO orders`, and true once the items are inserted. A **constraint trigger** is an `AFTER ... FOR EACH ROW` trigger that can be **deferred** to commit time, like a deferrable foreign key:

```sql
CREATE FUNCTION check_order_has_items() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM order_items WHERE order_id = NEW.id) THEN
        RAISE EXCEPTION 'order % has no items', NEW.id USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER orders_must_have_items
    AFTER INSERT ON orders
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION check_order_has_items();
```

```text
BEGIN;
INSERT INTO orders (user_id, seller_id) VALUES (8, 2);
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
VALUES (currval('orders_id_seq'), 19, 1, 14.99);
COMMIT;                                                   -- fine

BEGIN;
INSERT INTO orders (user_id, seller_id) VALUES (8, 2);   -- no items
COMMIT;
ERROR:  order 124 has no items
```

(This example commits, so reset afterwards.) Constraint triggers are useful but limited. They only check the row that fired them, so deleting the last item of an order isn't caught unless you add a second trigger on `order_items`. And they're not concurrency-proof: two transactions can each check a condition that the other one is about to invalidate. Real constraints (`CHECK`, `UNIQUE`, `EXCLUDE`, foreign keys) don't have that weakness, so prefer them whenever the rule can be expressed as one.

### 16.4 What triggers cost

Triggers run inside the statement, for every row. A rough measurement on this machine, updating all 200,000 rows of a scratch table `(id bigint PRIMARY KEY, n int, updated_at timestamptz)`:

| Triggers on the table | `UPDATE trigger_bench SET n = n + 1` |
|---|---|
| None | ≈ 1.07 s |
| `set_updated_at` (BEFORE ROW, PL/pgSQL) | ≈ 1.7 s |
| + `audit_trigger` (AFTER ROW, writes a jsonb row per change) | ≈ 7.5 s |

A trivial PL/pgSQL `BEFORE` trigger cost about 60% here. The audit trigger made the update about seven times slower, because every changed row now also serialises two rows to jsonb and inserts them into another table, with its own WAL and index updates. `AFTER` row triggers also queue an event per row in memory until the end of the statement. For single-row OLTP writes, the overhead is a fraction of a millisecond and doesn't matter. For bulk operations it dominates, and that's when to consider statement-level triggers with transition tables (one `INSERT ... SELECT` into the audit log per statement), or disabling triggers for a controlled bulk load.

---

## 17. Logic in the database or in the application?

There's no universal answer, and anyone who offers one is selling something. Here are the honest arguments.

**For putting logic in the database:**

- **One enforcement point.** Every writer goes through it: the API, the admin tool, the nightly import, the psql session during an incident. Invariants enforced only in application code are only as good as the least careful writer.
- **Data locality.** Set-based logic next to the data avoids round trips. Computing a seller report in one SQL function call beats fetching 10,000 rows into Python.
- **Atomicity for free.** A trigger runs in the same transaction as the change that caused it. An audit row and the change it describes commit or roll back together, with no outbox pattern needed.
- **Language independence.** A Python service, a Go worker and a BI tool all see the same views and the same rules.

**For putting logic in the application:**

- **Tooling.** Application code has better editors, debuggers, unit-test frameworks, code review habits, logging and profilers. PL/pgSQL is testable (pgTAP, or tests through the application, module 27), but most teams are less fluent in it.
- **Deployment and versioning.** Database logic changes through migrations, and with many application instances running during a rolling deploy, old and new code must work with the same function definitions.
- **Scaling.** The primary database is the hardest component to scale out. CPU spent in PL/pgSQL on the primary is the most expensive CPU you have. Stateless application servers are cheap to add.
- **Visibility.** Triggers act at a distance. A developer reading `UPDATE products SET price = ...` doesn't see the audit insert, and surprises erode trust.
- **Coupling.** Complex business workflows (sending e-mails, calling payment providers) can't run in the database at all, and splitting one workflow across both layers makes it harder to follow.

**A rule of thumb that works for most teams:**

| Put in the database | Keep in the application |
|---|---|
| Data integrity: constraints first, triggers where constraints can't express the rule | Workflows, orchestration and anything that talks to the outside world |
| Mechanical bookkeeping: `updated_at`, audit logs, derived columns such as `orders.total` | Business rules that change often or need product-manager-readable tests |
| Set-based reporting and shared read models: views, materialized views, SQL functions | Per-request presentation logic |
| Operations that must be atomic and concurrency-safe at the data level: reserving stock, `place_order` in module 10 | Validation whose job is a friendly UI error message (the database still guards the invariant) |
| Batch maintenance: procedures run by a scheduler | |

Bazaar follows it. `updated_at`, the audit log and `orders.total` are triggers because they're mechanical and must never be forgotten. `active_products` and `seller_sales_daily` are shared read models. Stock reservation becomes a function in module 10 because it must be atomic under concurrency. Checkout orchestration, payment calls and e-mails live in the Python service of Part IV.

---

## Summary

- A **view** is a stored query, expanded and optimized together with the outer query (including join removal), so it costs nothing by itself. Its column list is fixed at creation. Dependencies are tracked, so dropping or retyping a used column fails. `CREATE OR REPLACE VIEW` can only append columns. `security_barrier` makes a filtering view safe against leaky functions.
- Single-table views are **automatically updatable**. Add **`WITH CHECK OPTION`** so writes can't create rows the view wouldn't show. Join views need `INSTEAD OF` triggers.
- A **materialized view** stores its result and changes only on `REFRESH`. `REFRESH ... CONCURRENTLY` doesn't block readers but needs a unique index. Choose a staleness strategy explicitly, and tell users how old the data is.
- **SQL functions** are best for queries, since single-statement ones are **inlined**. **PL/pgSQL** adds variables, `%TYPE`/`%ROWTYPE`, `IF`/`CASE`, loops (`FOR r IN query`, `FOREACH`), `RETURN QUERY`/`RETURN NEXT` (materialized), and the `RETURNS TABLE` name trap. Use defaults, named notation (`=>`), `OUT` parameters, `SETOF`, `VARIADIC` and `BEGIN ATOMIC` bodies that track dependencies.
- **Volatility** is a promise: `IMMUTABLE` (plan-time evaluation, indexes), `STABLE` (one value per statement) or `VOLATILE`. Mislabelling gives stale or wrong results. Also know `STRICT` and `PARALLEL SAFE`.
- `RAISE EXCEPTION ... USING ERRCODE` with your own class (`BZ...`), plus `DETAIL`/`HINT`. `EXCEPTION` blocks are **subtransactions**: they cost time on every entry and an XID if they write. Catch narrowly and log what you catch (`GET STACKED DIAGNOSTICS`).
- **Dynamic SQL**: `EXECUTE format('... %I ...', ident) USING values`, plus a whitelist for structure. Concatenating input invites SQL injection *inside* the database.
- `SECURITY DEFINER` functions must `SET search_path`. **Procedures** can `COMMIT` between batches. `DO` runs one-off blocks.
- **Triggers**: `BEFORE` row triggers modify or skip `NEW`. `AFTER` triggers react, and abort by raising. Statement triggers with **transition tables** do set-based work once per statement. `WHEN` filters cheaply. Same-event triggers fire in alphabetical order. Guard against recursion, prefer real constraints over constraint triggers, and measure the cost on bulk paths.
- Put integrity, bookkeeping and shared read models in the database, and workflows and fast-changing business rules in the application.

## What's next

Several things in this module, such as `FOR UPDATE` in `add_tags`, the delta-based totals and the constraint trigger's concurrency weakness, pointed at the same subject: what happens when transactions run at the same time. [Module 10](../10-transactions-concurrency/) covers it in depth, from MVCC and isolation levels to locks, deadlocks and `SKIP LOCKED` job queues, and builds `place_order()`, the concurrency-safe checkout function.
