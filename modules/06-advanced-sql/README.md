# Module 06: Advanced SQL

<!-- nav:start -->
> **Theory** · [Exercises](exercises.md) · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 05 Aggregation and Grouping](../05-aggregation/README.md) · [Course home](../../README.md) · [07 Data Modeling →](../07-data-modeling/README.md)
<!-- nav:end -->

Bazaar's analysts have started asking harder questions. "Show me the whole Books category tree with revenue rolled up to every level." "Which seller climbed the leaderboard this month?" "Who are each seller's three best customers?" "How many months in a row has this shopper bought something?" "Archive last year's cancelled orders, and do it in one statement so nothing gets lost halfway." You can answer some of these with the joins, subqueries and `GROUP BY` from modules 04 and 05, but the queries get long, repetitive and slow. This module adds the four tools that make such questions routine: **common table expressions** (`WITH`) to structure big queries and chain data changes, **recursive CTEs** to walk trees and graphs, **window functions** to compute rankings, running totals and row-to-row differences without collapsing rows, and **`MERGE`** to synchronise one table with another in a single statement.

**What you'll learn**

- How to break a large query into named steps with `WITH`, and how PostgreSQL decides whether to inline a CTE or compute it once (`MATERIALIZED` / `NOT MATERIALIZED`).
- Data-modifying CTEs: chaining `INSERT`/`UPDATE`/`DELETE ... RETURNING` in one statement, and the snapshot rule that governs what each part can see.
- Recursive CTEs explained mechanically (working table, iterations, termination), and how to walk the Bazaar category tree down (subtrees) and up (breadcrumbs) with depth and path arrays, detect cycles with `CYCLE`, and order results with `SEARCH`.
- Window functions in depth: `OVER`, `PARTITION BY`, `ORDER BY`; ranking (`row_number`, `rank`, `dense_rank`, `ntile`, `percent_rank`, `cume_dist`); offsets (`lag`, `lead`, `first_value`, `last_value`, `nth_value`); aggregates as windows; frames (`ROWS`, `RANGE`, `GROUPS`, `EXCLUDE`), the default-frame trap, and named windows.
- Classic patterns: top-N per group (three ways), deduplication, gaps and islands, sessionization, month-over-month change.
- `MERGE`, including PostgreSQL 17's `RETURNING` and `WHEN NOT MATCHED BY SOURCE`, and when to prefer `INSERT ... ON CONFLICT` instead.

**Before you start**

```bash
./project/bin/reset.sh 06
./project/bin/psql.sh
```

This is the core schema from module 02 with the small dataset from module 03 (30 users, 6 sellers, 23 categories, 62 products, 120 orders, 235 order items). Every example below runs against exactly that state, and every output shown was produced by it. Examples that change data are wrapped in `BEGIN ... ROLLBACK`, so your database stays at the reference state.

A few facts about the data that come up repeatedly (they are listed in [`project/db/NOTES.md`](../../project/db/NOTES.md)):

- "Revenue" means orders with status `paid`, `shipped` or `delivered`. Seller revenue is: 1 TechTrove 41,598.82; 2 Bookworm Corner 1,656.43; 3 Casa Cucina 3,562.94; 4 Threadbare Co. 2,277.00. Seller 5 has products but no sales; seller 6 has no products.
- The category tree has four roots (1 Electronics, 8 Home & Garden, 14 Books, 21 Clothing) and is three levels deep, except Clothing, which has two.
- "Today" is 2025-06-30. Orders run from 2024-01-12 to 2025-06-30.

---

<!-- toc:start -->
**Contents**

- [1. Common table expressions](#1-common-table-expressions)
  - [1.1 The idea: name the steps](#11-the-idea-name-the-steps)
  - [1.2 Inlined or materialized? How PostgreSQL executes a CTE](#12-inlined-or-materialized-how-postgresql-executes-a-cte)
- [2. Data-modifying CTEs](#2-data-modifying-ctes)
  - [2.1 Chaining changes with RETURNING](#21-chaining-changes-with-returning)
  - [2.2 The snapshot rule](#22-the-snapshot-rule)
- [3. Recursive CTEs](#3-recursive-ctes)
  - [3.1 Why recursion](#31-why-recursion)
  - [3.2 The mechanics: anchor, recursive term, working table](#32-the-mechanics-anchor-recursive-term-working-table)
  - [3.3 Walking down: a subtree with depth](#33-walking-down-a-subtree-with-depth)
  - [3.4 Paths: arrays and strings](#34-paths-arrays-and-strings)
  - [3.5 Walking up: breadcrumbs](#35-walking-up-breadcrumbs)
  - [3.6 Cycles: when the tree isn't a tree](#36-cycles-when-the-tree-isnt-a-tree)
  - [3.7 Ordering with SEARCH](#37-ordering-with-search)
  - [3.8 Generating series recursively](#38-generating-series-recursively)
- [4. Window functions: the idea](#4-window-functions-the-idea)
  - [4.1 Aggregates that don't collapse rows](#41-aggregates-that-dont-collapse-rows)
  - [4.2 Where window functions run](#42-where-window-functions-run)
  - [4.3 The parts of OVER](#43-the-parts-of-over)
- [5. Ranking functions](#5-ranking-functions)
- [6. Offset and value functions](#6-offset-and-value-functions)
  - [6.1 lag and lead](#61-lag-and-lead)
  - [6.2 first_value, last_value, nth_value, and the default-frame trap](#62-first_value-last_value-nth_value-and-the-default-frame-trap)
- [7. Aggregates as windows: running totals and moving averages](#7-aggregates-as-windows-running-totals-and-moving-averages)
- [8. Frames in depth](#8-frames-in-depth)
  - [8.1 The frame clause](#81-the-frame-clause)
  - [8.2 ROWS, RANGE and GROUPS, and why the default bites running totals](#82-rows-range-and-groups-and-why-the-default-bites-running-totals)
  - [8.3 RANGE over time: a real 3-month moving average](#83-range-over-time-a-real-3-month-moving-average)
  - [8.4 EXCLUDE](#84-exclude)
- [9. Named windows: the WINDOW clause](#9-named-windows-the-window-clause)
- [10. Filtering on window results](#10-filtering-on-window-results)
- [11. Classic patterns](#11-classic-patterns)
  - [11.1 Top-N per group: three ways](#111-top-n-per-group-three-ways)
  - [11.2 Deduplication](#112-deduplication)
  - [11.3 Gaps and islands](#113-gaps-and-islands)
  - [11.4 Sessionization (a preview)](#114-sessionization-a-preview)
  - [11.5 Month-over-month change](#115-month-over-month-change)
- [12. MERGE](#12-merge)
  - [12.1 What MERGE does](#121-what-merge-does)
  - [12.2 A full sync: seller_month_stats](#122-a-full-sync-seller_month_stats)
  - [12.3 One source row per target row](#123-one-source-row-per-target-row)
  - [12.4 MERGE versus INSERT ... ON CONFLICT](#124-merge-versus-insert--on-conflict)
- [Summary](#summary)
- [What's next](#whats-next)

<!-- toc:end -->

## 1. Common table expressions

### 1.1 The idea: name the steps

A **common table expression** (CTE) is a named subquery that you write *before* the main query, with `WITH`:

```sql
WITH name AS (
    SELECT ...
)
SELECT ... FROM name ...;
```

Inside the main query, `name` behaves like a table that exists only for this one statement. Nothing is stored in the catalog, and nobody else can see it.

You already know how to do this with a subquery in `FROM` (module 04). The difference is readability. A derived table is read inside-out: you have to find the innermost query, understand it, then work outwards. A CTE is read top to bottom, like a program: step 1, step 2, step 3, result. Each step has a name that says what it means.

Here is a question with three steps: "For each seller with sales, show the number of paid orders, the revenue, and how that revenue compares to the average seller."

```sql
WITH paid_orders AS (
    SELECT id, seller_id, total
    FROM orders
    WHERE status IN ('paid', 'shipped', 'delivered')
),
seller_revenue AS (
    SELECT seller_id, count(*) AS orders, sum(total) AS revenue
    FROM paid_orders
    GROUP BY seller_id
),
overall AS (
    SELECT avg(revenue) AS avg_revenue FROM seller_revenue
)
SELECT s.shop_name, r.orders, r.revenue,
       round(r.revenue / o.avg_revenue, 2) AS vs_average
FROM seller_revenue AS r
JOIN sellers AS s ON s.id = r.seller_id
CROSS JOIN overall AS o
ORDER BY r.revenue DESC;
```

```text
    shop_name    | orders | revenue  | vs_average 
-----------------+--------+----------+------------
 TechTrove       |     31 | 41598.82 |       3.39
 Casa Cucina     |     23 |  3562.94 |       0.29
 Threadbare Co.  |     18 |  2277.00 |       0.19
 Bookworm Corner |     29 |  1656.43 |       0.13
(4 rows)
```

Points worth noticing:

- **Multiple CTEs** are separated by commas, with a single `WITH` at the front.
- **A CTE can refer to any CTE defined before it.** `seller_revenue` reads `paid_orders`, and `overall` reads `seller_revenue`. It can't refer to one defined *after* it (except in a recursive CTE, section 3).
- **A CTE can be referenced more than once.** `seller_revenue` is used by both `overall` and the main query. With subqueries you would have written that aggregation twice.
- **`overall` returns exactly one row**, so `CROSS JOIN` attaches the average to every seller row. That's a common idiom for "a constant computed by a query".

You can also name the output columns in the CTE header, which is handy when the query's own column names are awkward:

```sql
WITH seller_revenue (seller_id, orders, revenue) AS (
    SELECT seller_id, count(*), sum(total)
    FROM orders
    WHERE status IN ('paid', 'shipped', 'delivered')
    GROUP BY seller_id
)
SELECT * FROM seller_revenue WHERE revenue > 2000;
```

A CTE can be attached to `SELECT`, `INSERT`, `UPDATE`, `DELETE` and `MERGE`. And a CTE can contain any of those, which is section 2.

> **In production:** a 120-line query written as six well-named CTEs is far easier to review, debug and change than the same query as nested subqueries. To debug it, you can replace the final `SELECT` with `SELECT * FROM step_3` and look at the intermediate result. That habit alone saves hours.

### 1.2 Inlined or materialized? How PostgreSQL executes a CTE

There are two ways a database can execute a CTE:

1. **Materialize it.** Run the CTE's query once, store the result in a temporary in-memory structure (spilling to disk if it is large), and let every reference read from that stored result.
2. **Inline it.** Treat the CTE as if you had written it as a subquery at the place where it is used, and let the planner optimise the whole thing as one query. Conditions from the outer query can then be pushed *into* the CTE, indexes on the base tables can be used, and joins can be reordered.

Before PostgreSQL 12, every CTE was materialized. CTEs were an **optimisation fence**: the planner could not push a `WHERE` from outside into the CTE. Many old blog posts still warn about this. Since PostgreSQL 12, the rule is:

> A CTE is **inlined automatically** when it is (a) not recursive, (b) free of side effects (it is a plain `SELECT` with no volatile functions such as `random()` or `nextval()`), and (c) referenced **exactly once** in the rest of the query. Otherwise it is materialized.

You can see the difference with `EXPLAIN` (module 12 teaches plans in depth; here we only look at the shape). `COSTS OFF` hides the numbers so the structure stands out.

```sql
EXPLAIN (COSTS OFF)
WITH big_orders AS (
    SELECT * FROM orders WHERE total > 1000
)
SELECT id, total FROM big_orders WHERE seller_id = 1;
```

```text
                        QUERY PLAN                         
-----------------------------------------------------------
 Seq Scan on orders
   Filter: ((total > '1000'::numeric) AND (seller_id = 1))
(2 rows)
```

The CTE has disappeared: both conditions are applied in one scan of `orders`. That's inlining. If there were an index on `seller_id`, the planner could use it.

You can force the old behaviour with `AS MATERIALIZED`:

```sql
EXPLAIN (COSTS OFF)
WITH big_orders AS MATERIALIZED (
    SELECT * FROM orders WHERE total > 1000
)
SELECT id, total FROM big_orders WHERE seller_id = 1;
```

```text
                 QUERY PLAN                  
---------------------------------------------
 CTE Scan on big_orders
   Filter: (seller_id = 1)
   CTE big_orders
     ->  Seq Scan on orders
           Filter: (total > '1000'::numeric)
(5 rows)
```

Now the plan has a separate `CTE big_orders` sub-plan that computes *all* orders over 1000, and a `CTE Scan` that filters the stored result by seller afterwards. The `seller_id = 1` condition could not be pushed down.

When the CTE is referenced **twice**, the default flips to materialized:

```sql
EXPLAIN (COSTS OFF)
WITH big_orders AS (
    SELECT * FROM orders WHERE total > 1000
)
SELECT a.id, b.id
FROM big_orders a
JOIN big_orders b ON a.user_id = b.user_id AND a.id < b.id;
```

```text
                 QUERY PLAN                  
---------------------------------------------
 Hash Join
   Hash Cond: (a.user_id = b.user_id)
   Join Filter: (a.id < b.id)
   CTE big_orders
     ->  Seq Scan on orders
           Filter: (total > '1000'::numeric)
   ->  CTE Scan on big_orders a
   ->  Hash
         ->  CTE Scan on big_orders b
(9 rows)
```

and `AS NOT MATERIALIZED` asks for inlining anyway, which duplicates the scan:

```sql
EXPLAIN (COSTS OFF)
WITH big_orders AS NOT MATERIALIZED (
    SELECT * FROM orders WHERE total > 1000
)
SELECT a.id, b.id
FROM big_orders a
JOIN big_orders b ON a.user_id = b.user_id AND a.id < b.id;
```

```text
                    QUERY PLAN                    
--------------------------------------------------
 Hash Join
   Hash Cond: (orders.user_id = orders_1.user_id)
   Join Filter: (orders.id < orders_1.id)
   ->  Seq Scan on orders
         Filter: (total > '1000'::numeric)
   ->  Hash
         ->  Seq Scan on orders orders_1
               Filter: (total > '1000'::numeric)
(8 rows)
```

**When does the choice matter?**

- **Use the default** almost always. Inlining lets the planner do its job.
- **Use `MATERIALIZED`** when the CTE is expensive and referenced several times. The default already does this, so you write it explicitly mainly as documentation, or when a single reference would otherwise be inlined into a place where it gets re-evaluated many times (for example inside a correlated subquery). It is also the classic "planner hint" trick: when the planner picks a bad plan because it misestimates a condition, isolating that part in a materialized CTE forces a particular evaluation order. Treat that as a last resort and leave a comment saying why.
- **Use `NOT MATERIALIZED`** when a CTE is referenced twice but each reference is filtered very selectively (say, by an indexed key). Computing the whole CTE once can be much more expensive than two small index lookups.
- **Volatile functions** pin the CTE: `WITH r AS (SELECT random() AS x) SELECT * FROM r, r AS r2` computes `random()` once and both references see the same value. Inlining would call it twice. PostgreSQL never inlines such a CTE, because that would change the result.
- **Data-modifying CTEs** (next section) are always materialized and always executed exactly once.

> **Under the hood:** a materialized CTE is stored in a *tuplestore*, the same structure that holds sorted or buffered rows elsewhere in the executor. It stays in memory up to `work_mem` (module 17) and spills to a temporary file beyond that. A materialized CTE has no indexes and no statistics: the planner guesses how many rows a `CTE Scan` filter keeps, which is another reason inlining usually wins.

---

## 2. Data-modifying CTEs

### 2.1 Chaining changes with RETURNING

A CTE may contain `INSERT`, `UPDATE` or `DELETE` (and, since PostgreSQL 17, `MERGE`) with a `RETURNING` clause. The rows that `RETURNING` produces become the CTE's result, which later CTEs and the main query can read. This lets you chain several changes into **one statement**, which means one round trip and, more importantly, one atomic unit: either everything happens or nothing does, even without an explicit transaction.

A classic use is **archive and delete**. Bazaar wants to move cancelled orders from 2024 into archive tables. Done naively in an application, this is four statements, and a crash between them can lose or duplicate data. With a data-modifying CTE it is one:

```sql
BEGIN;
CREATE TABLE orders_archive (LIKE orders);
CREATE TABLE order_items_archive (LIKE order_items);

WITH doomed AS (
    SELECT id
    FROM orders
    WHERE status = 'cancelled'
      AND created_at < '2025-01-01'
),
moved_items AS (
    DELETE FROM order_items
    WHERE order_id IN (SELECT id FROM doomed)
    RETURNING *
),
moved_orders AS (
    DELETE FROM orders
    WHERE id IN (SELECT id FROM doomed)
    RETURNING *
),
archived_items AS (
    INSERT INTO order_items_archive
    SELECT * FROM moved_items
    RETURNING order_id
),
archived_orders AS (
    INSERT INTO orders_archive
    SELECT * FROM moved_orders
    RETURNING id
)
SELECT (SELECT count(*) FROM archived_orders) AS orders_archived,
       (SELECT count(*) FROM archived_items)  AS items_archived;
```

```text
 orders_archived | items_archived 
-----------------+----------------
               5 |             11
(1 row)
```

```sql
SELECT id, status, created_at::date, total FROM orders_archive ORDER BY id;
ROLLBACK;
```

```text
 id |  status   | created_at | total  
----+-----------+------------+--------
 31 | cancelled | 2024-06-09 | 107.99
 32 | cancelled | 2024-06-21 | 278.99
 36 | cancelled | 2024-07-09 |  34.00
 53 | cancelled | 2024-10-18 | 123.98
 64 | cancelled | 2024-12-18 |  22.00
(5 rows)
```

`CREATE TABLE ... (LIKE orders)` copies the column names, types and `NOT NULL` constraints, but not the identity, defaults, keys or other constraints. That's what you want for an archive: the rows keep their original ids. The `DELETE ... RETURNING *` passes the deleted rows, unchanged, to the `INSERT ... SELECT`.

Another everyday use is **inserting a parent and its children together**, using the generated id of the parent:

```sql
BEGIN;
WITH new_order AS (
    INSERT INTO orders (user_id, seller_id, status, total)
    VALUES (7, 2, 'pending', 2 * 14.99 + 12.50)
    RETURNING id
)
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT new_order.id, item.product_id, item.quantity, p.price
FROM new_order
CROSS JOIN (VALUES (19, 2), (20, 1)) AS item (product_id, quantity)
JOIN products AS p ON p.id = item.product_id
RETURNING *;
ROLLBACK;
```

```text
 order_id | product_id | quantity | unit_price 
----------+------------+----------+------------
      121 |         19 |        2 |      14.99
      121 |         20 |        1 |      12.50
(2 rows)
```

(If you run this twice, the second run shows `order_id` 122: a `ROLLBACK` undoes the rows but not the sequence behind the identity column. Gaps in ids are normal, and module 02 explains why sequences are never rolled back.) Without the CTE you would insert the order, read back its id in the application, then insert the items. In module 10 the reference `place_order()` function does this (plus stock reservation) for real.

### 2.2 The snapshot rule

This is the part that surprises people. **All sub-statements of a `WITH` statement run against the same snapshot of the database**, the one taken when the statement started. They can't see each other's changes to the tables. The *only* way to pass changed data from one part to another is through `RETURNING`.

```sql
BEGIN;
WITH bumped AS (
    UPDATE inventory
    SET quantity = quantity + 10
    WHERE product_id = 1
    RETURNING quantity
)
SELECT (SELECT quantity FROM inventory WHERE product_id = 1) AS seen_by_main_query,
       (SELECT quantity FROM bumped)                         AS returned_by_cte;
```

```text
 seen_by_main_query | returned_by_cte 
--------------------+-----------------
                 25 |              35
(1 row)
```

```sql
SELECT quantity FROM inventory WHERE product_id = 1;
ROLLBACK;
```

```text
 quantity 
----------
       35
(1 row)
```

Inside the statement, reading `inventory` directly still shows the old value, 25. The new value, 35, is visible only through `bumped`, and to the *next* statement. That's why the archive example used `IN (SELECT id FROM doomed)` in both `DELETE`s, and why its `INSERT`s read from `moved_items`/`moved_orders` instead of trying to find the rows in `order_items`/`orders` again.

Three consequences follow:

1. **Order of execution is not something you control.** Sub-statements with side effects run concurrently with each other and with the main query, in an order PostgreSQL chooses. Because none of them can see the others' changes, the order normally doesn't matter, and your logic must not depend on it.
2. **Data-modifying CTEs always run to completion**, even if nothing reads their result and even if the main query has a `LIMIT`. (A plain `SELECT` CTE runs only as far as its rows are needed.)
3. **Never modify the same row twice in one statement.** The second change doesn't see the first, and only one survives:

   ```sql
   BEGIN;
   WITH a AS (
       UPDATE inventory SET quantity = quantity + 10 WHERE product_id = 1 RETURNING quantity
   )
   UPDATE inventory SET quantity = quantity + 1 WHERE product_id = 1 RETURNING quantity;
   ```

   ```text
    quantity 
   ----------
          26
   (1 row)
   ```

   The `+10` is silently lost. PostgreSQL doesn't raise an error, and the docs say explicitly that which change wins is unpredictable. (`MERGE`, section 12, is stricter and *does* raise an error in its own version of this situation.)

> **Pitfall:** foreign-key checks and `ON DELETE CASCADE` actions fire at the end of the whole statement, not after each CTE. In the archive example, the explicit `DELETE FROM order_items` matters for the archive copy, not for integrity: if you left it out, the cascade from `orders` would still delete the items, but you would have no `RETURNING` rows to archive.

> **In production:** batch your archive jobs. A single statement that moves ten million rows holds its locks and its snapshot for the whole run. Add a `LIMIT` to the `doomed` CTE (`... ORDER BY id LIMIT 5000`) and loop until it returns 0 rows. Module 10 explains locks, and module 14 shows how partitioning turns archiving into a metadata operation (`DETACH PARTITION`).

---

## 3. Recursive CTEs

### 3.1 Why recursion

The `categories` table is an **adjacency list**: each row stores its parent's id.

```sql
SELECT id, parent_id, name FROM categories WHERE id <= 7 ORDER BY id;
```

```text
 id | parent_id |    name     
----+-----------+-------------
  1 |           | Electronics
  2 |         1 | Computers
  3 |         2 | Laptops
  4 |         2 | Monitors
  5 |         1 | Audio
  6 |         5 | Headphones
  7 |         5 | Speakers
(7 rows)
```

"All categories under Electronics" means: Electronics, its children, their children, and so on, to whatever depth exists. With joins you would need one self-join per level, and you'd have to know the depth in advance. A recursive CTE handles any depth.

### 3.2 The mechanics: anchor, recursive term, working table

A recursive CTE has this shape:

```sql
WITH RECURSIVE name (columns) AS (
    anchor query                -- non-recursive term: runs once
    UNION ALL                   -- or UNION
    recursive query             -- references "name" exactly once
)
SELECT ... FROM name;
```

Despite the name, it's executed as an **iteration**, not as a function calling itself. PostgreSQL keeps two temporary tables:

1. Run the **anchor** (the non-recursive term). Its rows go to the **result** and also into the **working table**.
2. Run the **recursive term**, with every reference to `name` reading **only the working table**, that is, only the rows produced by the previous step. Its output goes to the result and into a fresh **intermediate table**.
3. Replace the working table with the intermediate table.
4. If the working table is empty, stop. Otherwise go back to step 2.

The smallest possible example counts to five:

```sql
WITH RECURSIVE counter (n) AS (
    SELECT 1
    UNION ALL
    SELECT n + 1 FROM counter WHERE n < 5
)
SELECT n FROM counter;
```

```text
 n 
---
 1
 2
 3
 4
 5
(5 rows)
```

Trace it:

```text
iteration   working table   recursive term produces    result so far
---------   -------------   -----------------------    -------------
anchor      -               {1}                        1
1           {1}             {2}                        1 2
2           {2}             {3}                        1 2 3
3           {3}             {4}                        1 2 3 4
4           {4}             {5}                        1 2 3 4 5
5           {5}             {}  (5 < 5 is false)       stop
```

Two things follow from this mechanism:

- **Each iteration sees only the previous iteration's rows**, not the whole result. That's why the recursive term can join `counter` and only extend the newest rows.
- **Termination is your job.** The loop stops only when an iteration produces no rows. Here, `WHERE n < 5` guarantees that. Forget it and the query runs until it exhausts memory, disk or your `statement_timeout`.

**`UNION ALL` versus `UNION`.** With `UNION ALL`, every row produced is kept. With `UNION`, PostgreSQL discards any row that is a duplicate of a row already in the result (and duplicates within the same iteration). A row that is discarded doesn't enter the working table either, so `UNION` can terminate a loop that revisits the same rows. That only works when the revisited rows are *exactly* identical, and it costs a hash of everything seen so far. For trees, `UNION ALL` is correct and cheaper. For graphs with cycles, use the cycle detection in section 3.6.

**Restrictions on the recursive term.** It must reference the CTE exactly once, and that reference can't be inside a subquery or on the nullable side of an outer join (`LEFT JOIN subtree` is an error; `JOIN subtree` is fine). Aggregate functions are not allowed at all ("aggregate functions are not allowed in a recursive query's recursive term"). `DISTINCT`, `GROUP BY`, `ORDER BY ... LIMIT` and window functions *are* accepted, but remember what they see: only the working table, that is, one iteration's rows, never the whole result. A `LIMIT 10` in the recursive term limits each iteration, not the output.

**Column types are fixed by the anchor.** If the recursive term produces a wider type, you get an error:

```sql
WITH RECURSIVE tree AS (
    SELECT id, name::varchar(50) AS path FROM categories WHERE parent_id IS NULL
    UNION ALL
    SELECT c.id, t.path || ' > ' || c.name
    FROM categories c JOIN tree t ON c.parent_id = t.id
)
SELECT * FROM tree;
```

```text
ERROR:  recursive query "tree" column 2 has type character varying(50) in non-recursive term but type character varying overall
LINE 2:     SELECT id, name::varchar(50) AS path FROM categories WHE...
                       ^
HINT:  Cast the output of the non-recursive term to the correct type.
```

The fix is to make the anchor produce the general type: `name::text`, or `0::bigint` for a counter that might otherwise be an `int`.

### 3.3 Walking down: a subtree with depth

The anchor selects the root of the subtree. The recursive term finds the children of the rows found in the previous iteration:

```sql
WITH RECURSIVE subtree AS (
    SELECT id, parent_id, name, 0 AS depth
    FROM categories
    WHERE id = 1
    UNION ALL
    SELECT c.id, c.parent_id, c.name, s.depth + 1
    FROM categories AS c
    JOIN subtree AS s ON c.parent_id = s.id
)
SELECT * FROM subtree;
```

```text
 id | parent_id |    name     | depth 
----+-----------+-------------+-------
  1 |           | Electronics |     0
  2 |         1 | Computers   |     1
  5 |         1 | Audio       |     1
  3 |         2 | Laptops     |     2
  4 |         2 | Monitors    |     2
  6 |         5 | Headphones  |     2
  7 |         5 | Speakers    |     2
(7 rows)
```

The output arrives **level by level** (breadth-first), because each iteration produces one level. That order is an implementation detail, though, not a promise. If you need an order, say so with `ORDER BY`.

`depth` is the classic extra column: the anchor starts it at 0, and every step adds 1. You can use it to stop early, for example `WHERE s.depth < 1` in the recursive term to fetch only the root and its direct children.

### 3.4 Paths: arrays and strings

Carrying the **path** from the root along with each row gives you three things at once: a readable label, a sort key that produces a proper indented tree, and the raw material for cycle detection.

```sql
WITH RECURSIVE tree AS (
    SELECT id, name, 0 AS depth, ARRAY[id] AS id_path, name AS name_path
    FROM categories
    WHERE parent_id IS NULL
    UNION ALL
    SELECT c.id, c.name, t.depth + 1,
           t.id_path || c.id,
           t.name_path || ' > ' || c.name
    FROM categories AS c
    JOIN tree AS t ON c.parent_id = t.id
)
SELECT repeat('    ', depth) || name AS category, depth, id_path, name_path
FROM tree
ORDER BY id_path;
```

```text
        category         | depth |  id_path   |             name_path              
-------------------------+-------+------------+------------------------------------
 Electronics             |     0 | {1}        | Electronics
     Computers           |     1 | {1,2}      | Electronics > Computers
         Laptops         |     2 | {1,2,3}    | Electronics > Computers > Laptops
         Monitors        |     2 | {1,2,4}    | Electronics > Computers > Monitors
     Audio               |     1 | {1,5}      | Electronics > Audio
         Headphones      |     2 | {1,5,6}    | Electronics > Audio > Headphones
         Speakers        |     2 | {1,5,7}    | Electronics > Audio > Speakers
 Home & Garden           |     0 | {8}        | Home & Garden
     Kitchen             |     1 | {8,9}      | Home & Garden > Kitchen
         Cookware        |     2 | {8,9,10}   | Home & Garden > Kitchen > Cookware
         Cutlery         |     2 | {8,9,11}   | Home & Garden > Kitchen > Cutlery
     Garden              |     1 | {8,12}     | Home & Garden > Garden
         Planters        |     2 | {8,12,13}  | Home & Garden > Garden > Planters
 Books                   |     0 | {14}       | Books
     Fiction             |     1 | {14,15}    | Books > Fiction
         Science Fiction |     2 | {14,15,16} | Books > Fiction > Science Fiction
         Mystery         |     2 | {14,15,17} | Books > Fiction > Mystery
     Non-Fiction         |     1 | {14,18}    | Books > Non-Fiction
         Cooking         |     2 | {14,18,19} | Books > Non-Fiction > Cooking
         History         |     2 | {14,18,20} | Books > Non-Fiction > History
 Clothing                |     0 | {21}       | Clothing
     T-Shirts            |     1 | {21,22}    | Clothing > T-Shirts
     Hoodies             |     1 | {21,23}    | Clothing > Hoodies
(23 rows)
```

A few array basics (module 08 covers arrays fully): `ARRAY[id]` builds a one-element array, `||` appends an element, and arrays compare **element by element**, like words in a dictionary. So `{1,2,4}` sorts after `{1,2,3}` and before `{1,5}`, and `ORDER BY id_path` gives **depth-first order**: every category directly followed by its own subtree. Sorting by `name_path` (the text) would *almost* work, but text comparison treats `' > '` as ordinary characters, and a name containing characters that sort before a space would break the hierarchy. To sort siblings alphabetically, carry an array of names (`ARRAY[name]`, then `|| c.name`) and sort by that; the milestone does exactly this.

### 3.5 Walking up: breadcrumbs

To walk **up**, flip the join: the recursive term finds the *parent* of the previous row (`c.id = a.parent_id`) instead of its children. A product page needs this for its breadcrumb. Product 3, the Titan G17 Gaming Laptop, sits in Laptops:

```sql
WITH RECURSIVE ancestors AS (
    SELECT c.id, c.parent_id, c.name, 0 AS up
    FROM products AS p
    JOIN categories AS c ON c.id = p.category_id
    WHERE p.id = 3
    UNION ALL
    SELECT c.id, c.parent_id, c.name, a.up + 1
    FROM categories AS c
    JOIN ancestors AS a ON c.id = a.parent_id
)
SELECT * FROM ancestors;
```

```text
 id | parent_id |    name     | up 
----+-----------+-------------+----
  3 |         2 | Laptops     |  0
  2 |         1 | Computers   |  1
  1 |           | Electronics |  2
(3 rows)
```

The walk stops by itself: at the root, `parent_id` is NULL, and `c.id = NULL` matches nothing. Every step up produces exactly one row, so this is cheap: one primary-key lookup per level.

To turn the rows into one string, aggregate them in the order root first (highest `up` first):

```sql
WITH RECURSIVE ancestors AS (
    -- same as above
    SELECT c.id, c.parent_id, c.name, 0 AS up
    FROM products AS p
    JOIN categories AS c ON c.id = p.category_id
    WHERE p.id = 3
    UNION ALL
    SELECT c.id, c.parent_id, c.name, a.up + 1
    FROM categories AS c
    JOIN ancestors AS a ON c.id = a.parent_id
)
SELECT string_agg(name, ' › ' ORDER BY up DESC) AS breadcrumb FROM ancestors;
```

```text
            breadcrumb             
-----------------------------------
 Electronics › Computers › Laptops
(1 row)
```

Walking down from a node visits its whole subtree, which can be large. Walking up visits at most `depth` rows. Both are the same pattern with the join reversed.

### 3.6 Cycles: when the tree isn't a tree

Nothing in the schema guarantees that `categories` is a tree. The `categories_not_own_parent` CHECK stops a category from being its own parent, but not a longer loop. Suppose an admin tool sets Electronics' parent to Laptops, which is *inside* Electronics:

```sql
BEGIN;
UPDATE categories SET parent_id = 3 WHERE id = 1;   -- Electronics now hangs under Laptops
SET LOCAL statement_timeout = '2s';
SAVEPOINT before_loop;
```

Now Computers → Laptops → Electronics → Computers → ... is a cycle, and the subtree query never terminates:

```sql
WITH RECURSIVE subtree AS (
    SELECT id, name, 0 AS depth FROM categories WHERE id = 2
    UNION ALL
    SELECT c.id, c.name, s.depth + 1
    FROM categories AS c JOIN subtree AS s ON c.parent_id = s.id
)
SELECT * FROM subtree;
```

```text
ERROR:  canceling statement due to statement timeout
```

(The `statement_timeout` is our safety net here. Without it, the query would run until it filled the disk with temporary files.) An error aborts the whole transaction, so go back to the savepoint before continuing (module 10 explains savepoints):

```sql
ROLLBACK TO before_loop;
```

Interestingly, adding `LIMIT` to the *outer* query does stop it, because PostgreSQL evaluates a recursive CTE lazily, only as far as the outer query pulls rows:

```sql
WITH RECURSIVE subtree AS (
    SELECT id, name, 0 AS depth FROM categories WHERE id = 2
    UNION ALL
    SELECT c.id, c.name, s.depth + 1
    FROM categories AS c JOIN subtree AS s ON c.parent_id = s.id
)
SELECT * FROM subtree LIMIT 12;
```

```text
 id |    name     | depth 
----+-------------+-------
  2 | Computers   |     0
  3 | Laptops     |     1
  4 | Monitors    |     1
  1 | Electronics |     2
  2 | Computers   |     3
  5 | Audio       |     3
  3 | Laptops     |     4
  4 | Monitors    |     4
  6 | Headphones  |     4
  7 | Speakers    |     4
  1 | Electronics |     5
  2 | Computers   |     6
(12 rows)
```

You can see the loop: Computers reappears at depth 3 and 6. `LIMIT` is a debugging trick, not a fix: it fails as soon as the outer query sorts or aggregates (both need all rows).

**The manual fix** is to carry the path and stop extending a row whose id is already in it:

```sql
WITH RECURSIVE subtree AS (
    SELECT id, name, ARRAY[id] AS path, false AS is_cycle
    FROM categories WHERE id = 2
    UNION ALL
    SELECT c.id, c.name, s.path || c.id, c.id = ANY (s.path)
    FROM categories AS c
    JOIN subtree AS s ON c.parent_id = s.id
    WHERE NOT s.is_cycle
)
SELECT * FROM subtree;
```

```text
 id |    name     |    path     | is_cycle 
----+-------------+-------------+----------
  2 | Computers   | {2}         | f
  3 | Laptops     | {2,3}       | f
  4 | Monitors    | {2,4}       | f
  1 | Electronics | {2,3,1}     | f
  2 | Computers   | {2,3,1,2}   | t
  5 | Audio       | {2,3,1,5}   | f
  6 | Headphones  | {2,3,1,5,6} | f
  7 | Speakers    | {2,3,1,5,7} | f
(8 rows)
```

`c.id = ANY (s.path)` is true when the new id already appears on the path. That row is emitted once, flagged, and never extended (`WHERE NOT s.is_cycle`), so the loop ends.

**The `CYCLE` clause (PostgreSQL 14+)** does exactly this for you. You name the column(s) that identify a node, a column to hold the flag, and a column to hold the path:

```sql
WITH RECURSIVE subtree AS (
    SELECT id, parent_id, name FROM categories WHERE id = 2
    UNION ALL
    SELECT c.id, c.parent_id, c.name
    FROM categories AS c JOIN subtree AS s ON c.parent_id = s.id
) CYCLE id SET is_cycle USING path
SELECT * FROM subtree;
```

```text
 id | parent_id |    name     | is_cycle |         path          
----+-----------+-------------+----------+-----------------------
  2 |         1 | Computers   | f        | {(2)}
  3 |         2 | Laptops     | f        | {(2),(3)}
  4 |         2 | Monitors    | f        | {(2),(4)}
  1 |         3 | Electronics | f        | {(2),(3),(1)}
  2 |         1 | Computers   | t        | {(2),(3),(1),(2)}
  5 |         1 | Audio       | f        | {(2),(3),(1),(5)}
  6 |         5 | Headphones  | f        | {(2),(3),(1),(5),(6)}
  7 |         5 | Speakers    | f        | {(2),(3),(1),(5),(7)}
(8 rows)
```

The path is an array of *records* (hence `(2)` instead of `2`), because you can list several columns after `CYCLE` when a node is identified by more than one column. The flag is a boolean by default; you can choose other marker values with `SET is_cycle TO 'Y' DEFAULT 'N'`. To find just the offending rows:

```sql
WITH RECURSIVE subtree AS (
    SELECT id, parent_id, name FROM categories WHERE id = 2
    UNION ALL
    SELECT c.id, c.parent_id, c.name
    FROM categories AS c JOIN subtree AS s ON c.parent_id = s.id
) CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' USING path
SELECT id, name, is_cycle FROM subtree WHERE is_cycle = 'Y';
ROLLBACK;
```

```text
 id |   name    | is_cycle 
----+-----------+----------
  2 | Computers | Y
(1 row)
```

> **In production:** for real trees, prevent cycles at write time (exercise 11 shows a guarded `UPDATE`; module 09 shows how to enforce it with a trigger), and *also* add a cheap safety net to read queries: either `CYCLE`, or a depth limit such as `WHERE s.depth < 50` in the recursive term. A corrupted row should give you a wrong answer you can investigate, not a runaway query that takes the database down.

### 3.7 Ordering with SEARCH

`SEARCH` (also PostgreSQL 14+) adds a column you can sort by to get **depth-first** or **breadth-first** order, without building the path array yourself:

```sql
WITH RECURSIVE tree AS (
    SELECT id, name FROM categories WHERE id = 8
    UNION ALL
    SELECT c.id, c.name FROM categories AS c JOIN tree AS t ON c.parent_id = t.id
) SEARCH DEPTH FIRST BY id SET ord
SELECT * FROM tree ORDER BY ord;
```

```text
 id |     name      |       ord       
----+---------------+-----------------
  8 | Home & Garden | {(8)}
  9 | Kitchen       | {(8),(9)}
 10 | Cookware      | {(8),(9),(10)}
 11 | Cutlery       | {(8),(9),(11)}
 12 | Garden        | {(8),(12)}
 13 | Planters      | {(8),(12),(13)}
(6 rows)
```

```sql
WITH RECURSIVE tree AS (
    SELECT id, name FROM categories WHERE id = 8
    UNION ALL
    SELECT c.id, c.name FROM categories AS c JOIN tree AS t ON c.parent_id = t.id
) SEARCH BREADTH FIRST BY id SET ord
SELECT * FROM tree ORDER BY ord;
```

```text
 id |     name      |  ord   
----+---------------+--------
  8 | Home & Garden | (0,8)
  9 | Kitchen       | (1,9)
 12 | Garden        | (1,12)
 10 | Cookware      | (2,10)
 11 | Cutlery       | (2,11)
 13 | Planters      | (2,13)
(6 rows)
```

`DEPTH FIRST` builds a path array (exactly like our `id_path`). `BREADTH FIRST` builds a `(depth, id)` record. `BY` decides how siblings are ordered; `SEARCH DEPTH FIRST BY name SET ord` would put Garden before Kitchen. `SEARCH` only produces the sort key: you still need the `ORDER BY ord`.

### 3.8 Generating series recursively

Recursion isn't only for trees. Any "start here, then step until a condition" sequence works, for example the first day of each month in the first half of 2024:

```sql
WITH RECURSIVE months (month) AS (
    SELECT date '2024-01-01'
    UNION ALL
    SELECT (month + interval '1 month')::date
    FROM months
    WHERE month < date '2024-06-01'
)
SELECT month FROM months;
```

```text
   month    
------------
 2024-01-01
 2024-02-01
 2024-03-01
 2024-04-01
 2024-05-01
 2024-06-01
(6 rows)
```

(`date + interval` gives a `timestamp`, so the cast back to `date` keeps the column type equal to the anchor's.)

For plain number and date series, `generate_series()` from module 05 is shorter and faster. Recursion earns its place when each step depends on the previous one in a way `generate_series` can't express: compound interest, "the next business day after a holiday list", following a chain of `replaced_by_product_id` links, or walking a graph.

> **Under the hood:** PostgreSQL also uses the iterative mechanism for graph searches, such as "which products are bought together, two hops away". On graphs, the number of paths grows exponentially with depth, even when the number of nodes is small. Always bound graph recursion with a depth limit *and* cycle detection, and expect to need a different design (precomputed edges, a closure table) once the graph gets large.

---

## 4. Window functions: the idea

### 4.1 Aggregates that don't collapse rows

`GROUP BY` (module 05) answers "per group" questions, but it **collapses** each group into one row. Often you want the per-group number *next to* each detail row: each order with its seller's total, each product with its rank in its category, each month with the previous month's revenue.

A **window function** computes a value over a set of rows related to the current row (its **window**) and attaches the result to the current row. Rows are not collapsed. The syntax is a function call followed by `OVER (...)`:

```sql
SELECT id, seller_id, total,
       sum(total) OVER (PARTITION BY seller_id) AS seller_total,
       round(100 * total / sum(total) OVER (PARTITION BY seller_id), 1) AS pct_of_seller,
       count(*) OVER () AS all_rows
FROM orders
WHERE status = 'delivered' AND created_at >= '2025-05-01'
ORDER BY seller_id, id;
```

```text
 id  | seller_id |  total  | seller_total | pct_of_seller | all_rows 
-----+-----------+---------+--------------+---------------+----------
  95 |         1 | 3455.00 |      6291.96 |          54.9 |       13
  99 |         1 |   19.99 |      6291.96 |           0.3 |       13
 102 |         1 | 2657.98 |      6291.96 |          42.2 |       13
 103 |         1 |   29.99 |      6291.96 |           0.5 |       13
 104 |         1 |  129.00 |      6291.96 |           2.1 |       13
  93 |         2 |  119.48 |       213.44 |          56.0 |       13
  96 |         2 |   24.50 |       213.44 |          11.5 |       13
 101 |         2 |   69.46 |       213.44 |          32.5 |       13
  94 |         3 |   34.99 |       439.98 |           8.0 |       13
  97 |         3 |  208.00 |       439.98 |          47.3 |       13
  98 |         3 |   88.99 |       439.98 |          20.2 |       13
 108 |         3 |  108.00 |       439.98 |          24.5 |       13
 100 |         4 |  132.00 |       132.00 |         100.0 |       13
(13 rows)
```

The query returns 13 rows, one per order, the same as without the window functions. Each row gets:

- `sum(total) OVER (PARTITION BY seller_id)`: the sum over all rows with the same `seller_id` as this row. The rows are divided into **partitions**, and the function sees only the current row's partition.
- `count(*) OVER ()`: an empty `OVER ()` means one partition containing every row of the result. It's an easy way to show "n of N" or a percentage of the grand total.

The same `sum` in a `GROUP BY` query would have given 4 rows. Any ordinary aggregate (`sum`, `count`, `avg`, `min`, `max`, `string_agg`, `array_agg`, `bool_and`, ...) becomes a window function when you add `OVER`.

### 4.2 Where window functions run

Window functions are evaluated **late**, after the rows have been filtered and grouped. The logical order of a `SELECT` is:

```text
FROM / JOIN  →  WHERE  →  GROUP BY  →  HAVING  →  window functions  →  SELECT list
             →  DISTINCT  →  ORDER BY  →  LIMIT / OFFSET
```

This explains three rules you'll otherwise learn the hard way:

1. **A window function sees only the rows that survived `WHERE`** (and `GROUP BY`/`HAVING`). In the example above, `all_rows` is 13, not 120: the window ran over the 13 filtered rows.
2. **You can't use a window function in `WHERE`, `GROUP BY` or `HAVING`**, because they have already run (section 10 shows how to filter on window results).
3. **You *can* apply a window function to an aggregate**, because aggregation has already happened. This ranks sellers by revenue and computes their market share in one pass:

```sql
SELECT seller_id,
       sum(total) AS revenue,
       rank() OVER (ORDER BY sum(total) DESC) AS revenue_rank,
       round(100 * sum(total) / sum(sum(total)) OVER (), 1) AS pct_of_marketplace
FROM orders
WHERE status IN ('paid', 'shipped', 'delivered')
GROUP BY seller_id
ORDER BY seller_id;
```

```text
 seller_id | revenue  | revenue_rank | pct_of_marketplace 
-----------+----------+--------------+--------------------
         1 | 41598.82 |            1 |               84.7
         2 |  1656.43 |            4 |                3.4
         3 |  3562.94 |            2 |                7.3
         4 |  2277.00 |            3 |                4.6
(4 rows)
```

`sum(sum(total)) OVER ()` looks odd but reads naturally inside out: the inner `sum(total)` is the per-seller aggregate from `GROUP BY`; the outer `sum(...) OVER ()` adds those four values up across the whole result.

### 4.3 The parts of OVER

`OVER` has three optional parts, always in this order:

```sql
function(args) OVER (
    PARTITION BY ...    -- which rows belong together (default: all rows)
    ORDER BY ...        -- order of rows inside each partition
    frame clause        -- which rows around the current one the function sees
)
```

- **`PARTITION BY`** splits the rows into independent groups, like `GROUP BY` without collapsing them.
- **`ORDER BY`** orders rows *within each partition*. Ranking and offset functions (`rank`, `lag`, ...) need it. For aggregates, adding `ORDER BY` changes the meaning, from "the whole partition" to "from the start of the partition up to this row". That's how running totals work (section 7).
- The **frame** narrows the rows an aggregate or value function sees, for example "the previous 2 rows and this one". Section 8 covers frames in detail.

The window's `ORDER BY` is completely independent of the query's final `ORDER BY`. A query can compute ranks in one order and print rows in another. If you want rows printed in a particular order, you always need the outer `ORDER BY`.

---

## 5. Ranking functions

Ranking functions take no arguments; they depend entirely on the window's `ORDER BY`. Seller 4's products by price show all of them at once (the `WINDOW` clause names a window definition so it isn't repeated six times; section 9 explains it):

```sql
SELECT id, title, price,
       row_number() OVER w AS row_number,
       rank()       OVER w AS rank,
       dense_rank() OVER w AS dense_rank,
       ntile(3)     OVER w AS ntile_3,
       round(percent_rank() OVER w::numeric, 2) AS pct_rank,
       round(cume_dist() OVER w::numeric, 2) AS cume_dist
FROM products
WHERE seller_id = 4
WINDOW w AS (ORDER BY price DESC)
ORDER BY price DESC, id;
```

```text
 id |            title            | price  | row_number | rank | dense_rank | ntile_3 | pct_rank | cume_dist 
----+-----------------------------+--------+------------+------+------------+---------+----------+-----------
 56 | Merino Wool Hoodie          | 119.00 |          1 |    1 |          1 |       1 |     0.00 |      0.09
 55 | Fleece-Lined Winter Hoodie  |  69.00 |          2 |    2 |          2 |       1 |     0.10 |      0.18
 53 | Heavyweight Pullover Hoodie |  59.00 |          3 |    3 |          3 |       1 |     0.20 |      0.27
 52 | Classic Zip Hoodie - Grey   |  49.00 |          4 |    4 |          4 |       1 |     0.30 |      0.36
 50 | Linen Summer Shirt          |  39.00 |          5 |    5 |          5 |       2 |     0.40 |      0.45
 54 | Kids' Rainbow Hoodie        |  34.00 |          6 |    6 |          6 |       2 |     0.50 |      0.55
 49 | Vintage Logo Tee            |  24.00 |          7 |    7 |          7 |       2 |     0.60 |      0.64
 51 | Graphic Tee 'Ctrl+Alt+Del'  |  22.00 |          8 |    8 |          8 |       2 |     0.70 |      0.73
 47 | Organic Cotton Tee - White  |  19.00 |          9 |    9 |          9 |       3 |     0.80 |      0.91
 48 | Organic Cotton Tee - Black  |  19.00 |         10 |    9 |          9 |       3 |     0.80 |      0.91
 62 | Bazaar Sticker Pack         |   0.00 |         11 |   11 |         10 |       3 |     1.00 |      1.00
(11 rows)
```

The two tees at 19.00 are **peers**: rows with equal values in the window's `ORDER BY`. The functions differ exactly in how they treat peers:

| Function | What it returns | Peers 47 and 48 | Next row (62) |
|---|---|---|---|
| `row_number()` | 1, 2, 3, ... with no ties | 9 and 10 (arbitrary!) | 11 |
| `rank()` | 1 + number of rows *before* this row's peer group; ties share, then a gap | 9 and 9 | 11 (skips 10) |
| `dense_rank()` | 1 + number of *distinct values* before; ties share, no gaps | 9 and 9 | 10 |
| `ntile(n)` | splits the partition into `n` buckets as equal in size as possible | 3 and 3 | 3 |
| `percent_rank()` | `(rank − 1) / (rows − 1)`: 0 for the first, 1 for the last | 0.80 | 1.00 |
| `cume_dist()` | (rows with value ≤ this row's, peers included) / rows | 10/11 = 0.91 | 1.00 |

Choosing between them is a business decision:

- **"Exactly one row per position"** (pagination, picking one row per group): `row_number()`. But break ties explicitly, for example `ORDER BY price DESC, id`. Otherwise which tee gets 9 and which 10 is arbitrary, and can change between runs or after a `VACUUM`. That's a classic source of flaky tests and duplicated/missing rows across pages.
- **"Medal table" rankings** where ties share a place and the next place is skipped: `rank()`.
- **"The 3 highest prices"** (distinct price levels, however many products share them): `dense_rank()`.
- **Buckets**: `ntile(4)` gives quartiles by *row count*. With 11 rows and 3 buckets, the sizes are 4, 4, 3: the first buckets get the extra rows. Note that `ntile` splits peers if a bucket boundary falls between them; it doesn't know about ties.
- **Relative position**: `percent_rank()` answers "what fraction of the others are ranked ahead of me", `cume_dist()` answers "what fraction is at or ahead of me". Both return `double precision`; the `::numeric` cast is there so `round(x, 2)` works (`round` with a digits argument only exists for `numeric`).

A real use of `ntile`: split Bazaar's paying customers into spending quartiles and see how concentrated revenue is.

```sql
WITH spend AS (
    SELECT user_id, sum(total) AS spent
    FROM orders
    WHERE status IN ('paid', 'shipped', 'delivered')
    GROUP BY user_id
)
SELECT quartile, count(*) AS customers, sum(spent) AS revenue,
       round(100 * sum(spent) / sum(sum(spent)) OVER (), 1) AS pct
FROM (
    SELECT user_id, spent, ntile(4) OVER (ORDER BY spent DESC) AS quartile
    FROM spend
) AS q
GROUP BY quartile
ORDER BY quartile;
```

```text
 quartile | customers | revenue  | pct  
----------+-----------+----------+------
        1 |         5 | 36479.06 | 74.3
        2 |         5 |  9616.77 | 19.6
        3 |         5 |  2178.42 |  4.4
        4 |         5 |   820.94 |  1.7
(4 rows)
```

The top quarter of customers brings in three quarters of the revenue. Note the structure: the window function runs in a subquery, and the outer query groups by its result. You can't `GROUP BY` a window function in the same query level (rule 2 in section 4.2).

---

## 6. Offset and value functions

### 6.1 lag and lead

`lag(expr)` returns `expr` evaluated on the **previous** row of the partition (in the window's order); `lead(expr)` on the **next** row. Both take an optional offset (default 1) and a default value for when there is no such row (default NULL): `lag(expr, offset, default)`.

The time between a customer's orders is a typical use. Here's user 12 (Liam O'Connor):

```sql
SELECT id, created_at::date AS ordered_on, total,
       lag(created_at::date) OVER w AS previous_order,
       created_at::date - lag(created_at::date) OVER w AS days_since_previous,
       lead(total, 1, 0) OVER w AS next_total
FROM orders
WHERE user_id = 12
WINDOW w AS (ORDER BY created_at)
ORDER BY created_at;
```

```text
 id  | ordered_on |  total  | previous_order | days_since_previous | next_total 
-----+------------+---------+----------------+---------------------+------------
  17 | 2024-03-31 |   25.99 |                |                     |      54.97
  23 | 2024-04-26 |   54.97 | 2024-03-31     |                  26 |     566.98
  26 | 2024-05-07 |  566.98 | 2024-04-26     |                  11 |      78.00
  42 | 2024-08-17 |   78.00 | 2024-05-07     |                 102 |      88.99
  98 | 2025-05-28 |   88.99 | 2024-08-17     |                 284 |    2657.98
 102 | 2025-06-18 | 2657.98 | 2025-05-28     |                  21 |          0
(6 rows)
```

The first row has no previous order, so `lag` returns NULL (and the subtraction is NULL too). The last row has no next order, and `lead(total, 1, 0)` returns the default we gave it, 0. (`date - date` is an integer number of days in PostgreSQL.)

Before window functions, "compare each row with the previous one" needed a self-join on "the greatest id smaller than mine", a correlated subquery per row. `lag` does it in one ordered pass.

> **Pitfall:** "previous row" means previous row *in the result*, not "previous month" or "previous day". If a month has no row, `lag` happily compares March with January. Section 11.5 shows how to fill the gaps first so `lag` really compares adjacent periods.

### 6.2 first_value, last_value, nth_value, and the default-frame trap

`first_value(expr)`, `last_value(expr)` and `nth_value(expr, n)` return `expr` from the first, last or n-th row of the **window frame**. That last word is the trap. Here are the headphones (category 6) ordered by price:

```sql
SELECT id, left(title, 22) AS title, price,
       first_value(left(title, 22)) OVER w AS cheapest,
       last_value(left(title, 22))  OVER w AS last_value,
       nth_value(left(title, 22), 2) OVER w AS second
FROM products
WHERE category_id = 6
WINDOW w AS (ORDER BY price)
ORDER BY price;
```

```text
 id |         title          | price  |        cheapest        |       last_value       |         second         
----+------------------------+--------+------------------------+------------------------+------------------------
 13 | KidSafe Volume-Limited |  29.99 | KidSafe Volume-Limited | KidSafe Volume-Limited | 
 14 | BassBoom Over-Ear Head |  89.00 | KidSafe Volume-Limited | BassBoom Over-Ear Head | BassBoom Over-Ear Head
 11 | QuietWave Buds         | 129.00 | KidSafe Volume-Limited | QuietWave Buds         | BassBoom Over-Ear Head
 12 | StudioPro Monitor Head | 149.00 | KidSafe Volume-Limited | StudioPro Monitor Head | BassBoom Over-Ear Head
 10 | QuietWave ANC Headphon | 229.00 | KidSafe Volume-Limited | QuietWave ANC Headphon | BassBoom Over-Ear Head
(5 rows)
```

`first_value` works. `last_value` just echoes the current row, and `nth_value(..., 2)` is NULL on the first row. Why? When a window has an `ORDER BY`, the **default frame** is "from the start of the partition up to the current row (and its peers)". At the first row the frame contains one row, so there is no second row, and the last row of the frame is always the current row.

To see the whole partition, widen the frame explicitly:

```sql
SELECT id, left(title, 22) AS title, price,
       last_value(left(title, 22)) OVER w AS most_expensive,
       nth_value(left(title, 22), 2) OVER w AS second
FROM products
WHERE category_id = 6
WINDOW w AS (ORDER BY price
             ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
ORDER BY price;
```

```text
 id |         title          | price  |     most_expensive     |         second         
----+------------------------+--------+------------------------+------------------------
 13 | KidSafe Volume-Limited |  29.99 | QuietWave ANC Headphon | BassBoom Over-Ear Head
 14 | BassBoom Over-Ear Head |  89.00 | QuietWave ANC Headphon | BassBoom Over-Ear Head
 11 | QuietWave Buds         | 129.00 | QuietWave ANC Headphon | BassBoom Over-Ear Head
 12 | StudioPro Monitor Head | 149.00 | QuietWave ANC Headphon | BassBoom Over-Ear Head
 10 | QuietWave ANC Headphon | 229.00 | QuietWave ANC Headphon | BassBoom Over-Ear Head
(5 rows)
```

An even simpler fix for "most expensive" is `first_value(...) OVER (ORDER BY price DESC)`, which needs no frame at all, because the first row is always in the default frame.

---

## 7. Aggregates as windows: running totals and moving averages

Add `ORDER BY` to an aggregate's window, and the default frame turns it into a **running** aggregate. Casa Cucina's monthly revenue with a running total and a 3-row moving average:

```sql
WITH monthly AS (
    SELECT date_trunc('month', created_at)::date AS month, sum(total) AS revenue
    FROM orders
    WHERE seller_id = 3 AND status IN ('paid', 'shipped', 'delivered')
    GROUP BY 1
)
SELECT month, revenue,
       sum(revenue) OVER (ORDER BY month) AS running_total,
       round(avg(revenue) OVER (ORDER BY month
                                ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2) AS moving_avg_3
FROM monthly
ORDER BY month;
```

```text
   month    | revenue | running_total | moving_avg_3 
------------+---------+---------------+--------------
 2024-01-01 |  280.00 |        280.00 |       280.00
 2024-02-01 |  293.00 |        573.00 |       286.50
 2024-03-01 |   79.00 |        652.00 |       217.33
 2024-04-01 |  201.99 |        853.99 |       191.33
 2024-06-01 |  933.99 |       1787.98 |       404.99
 2024-07-01 |  253.00 |       2040.98 |       462.99
 2024-08-01 |   83.00 |       2123.98 |       423.33
 2024-09-01 |   34.99 |       2158.97 |       123.66
 2024-12-01 |   93.99 |       2252.96 |        70.66
 2025-03-01 |  383.00 |       2635.96 |       170.66
 2025-04-01 |  362.00 |       2997.96 |       279.66
 2025-05-01 |  331.98 |       3329.94 |       358.99
 2025-06-01 |  233.00 |       3562.94 |       308.99
(13 rows)
```

The running total ends at 3,562.94, Casa Cucina's revenue from the introduction. The moving average averages the current row and the two rows before it. Look closely, though: Casa Cucina had **no sales** in May 2024, October and November 2024, or January and February 2025. Those months have no rows, so the "3-row" average for 2025-03-01 averages March 2025 with December and September 2024. That's not a 3-month average at all. Section 8.3 fixes this with a `RANGE` frame.

Note that the running total is computed in one pass over sorted rows. The equivalent correlated subquery (`SELECT sum(...) FROM monthly m2 WHERE m2.month <= m1.month`) re-reads the table once per row, which is quadratic.

---

## 8. Frames in depth

### 8.1 The frame clause

The **frame** is the subset of the partition that an aggregate or value function (`first_value`, `last_value`, `nth_value`) sees for the current row. Ranking functions and `lag`/`lead` ignore the frame. Its full form is:

```sql
{ ROWS | RANGE | GROUPS } BETWEEN frame_start AND frame_end [ EXCLUDE ... ]
```

where each bound is one of:

| Bound | Meaning |
|---|---|
| `UNBOUNDED PRECEDING` | the first row of the partition |
| `n PRECEDING` | n rows / n value units / n peer groups before the current row (depends on the mode) |
| `CURRENT ROW` | the current row (`ROWS`) or its first/last peer (`RANGE`, `GROUPS`) |
| `n FOLLOWING` | n rows / value units / peer groups after |
| `UNBOUNDED FOLLOWING` | the last row of the partition |

`ROWS 2 PRECEDING` is shorthand for `ROWS BETWEEN 2 PRECEDING AND CURRENT ROW`.

**Defaults**, which you should know by heart:

- Without `ORDER BY` in the window: the frame is the **whole partition**.
- With `ORDER BY`: `RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW`, which means "from the start up to the current row **and all its peers**".

### 8.2 ROWS, RANGE and GROUPS, and why the default bites running totals

The three modes differ in what they count:

- **`ROWS`** counts physical rows. `1 PRECEDING` is exactly one row back, whatever its value.
- **`RANGE`** works on the *value* of the single `ORDER BY` column. `5 PRECEDING` means "rows whose value is between (current value − 5) and the current value". `CURRENT ROW` includes all peers.
- **`GROUPS`** counts **peer groups**: sets of rows with equal `ORDER BY` values. `1 PRECEDING` means "the previous group of ties, and this one".

The default frame uses `RANGE`, which treats peers as a unit. Watch what that does to a running total when the order has ties. Seller 4's products under 40.00, ordered by price, include the two 19.00 tees:

```sql
SELECT id, price,
       sum(price) OVER (ORDER BY price) AS default_frame,
       sum(price) OVER (ORDER BY price
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS rows_frame,
       sum(price) OVER (ORDER BY price, id) AS unique_order
FROM products
WHERE seller_id = 4 AND price < 40
ORDER BY price, id;
```

```text
 id | price | default_frame | rows_frame | unique_order 
----+-------+---------------+------------+--------------
 62 |  0.00 |          0.00 |       0.00 |         0.00
 47 | 19.00 |         38.00 |      19.00 |        19.00
 48 | 19.00 |         38.00 |      38.00 |        38.00
 51 | 22.00 |         60.00 |      60.00 |        60.00
 49 | 24.00 |         84.00 |      84.00 |        84.00
 54 | 34.00 |        118.00 |     118.00 |       118.00
 50 | 39.00 |        157.00 |     157.00 |       157.00
(7 rows)
```

Products 47 and 48 tie at 19.00. With the **default frame** (`RANGE ... CURRENT ROW`), "up to the current row" includes all peers, so *both* tees get 38.00: each row's running total already includes the other tee. With **`ROWS`**, the frame stops at the physical current row, so the totals step 19.00 → 38.00. But which tee comes first among peers is arbitrary: in this run 47 came first, and next time it might be 48.

The robust fix is the third column: make the `ORDER BY` **unique** by adding a tie-breaker (`price, id`). Then there are no peers, `RANGE` and `ROWS` agree, and the result is deterministic.

> **Pitfall:** running totals over a non-unique order are the most common window-function bug in reports. The numbers look plausible, the final total is correct, and only the rows with ties are wrong. Rule of thumb: for running totals and anything with `row_number()`, always end the window's `ORDER BY` with a unique column.

Now the three modes side by side, with offsets that make the difference visible. The `ROWS` column uses `ORDER BY price, id` so that its result is deterministic:

```sql
SELECT id, price,
       sum(price) OVER (ORDER BY price, id ROWS   BETWEEN 1 PRECEDING AND CURRENT ROW) AS rows_1,
       sum(price) OVER (ORDER BY price     RANGE  BETWEEN 5 PRECEDING AND CURRENT ROW) AS range_5,
       sum(price) OVER (ORDER BY price     GROUPS BETWEEN 1 PRECEDING AND CURRENT ROW) AS groups_1
FROM products
WHERE seller_id = 4 AND price < 40
ORDER BY price, id;
```

```text
 id | price | rows_1 | range_5 | groups_1 
----+-------+--------+---------+----------
 62 |  0.00 |   0.00 |    0.00 |     0.00
 47 | 19.00 |  19.00 |   38.00 |    38.00
 48 | 19.00 |  38.00 |   38.00 |    38.00
 51 | 22.00 |  41.00 |   60.00 |    60.00
 49 | 24.00 |  46.00 |   84.00 |    46.00
 54 | 34.00 |  58.00 |   34.00 |    58.00
 50 | 39.00 |  73.00 |   73.00 |    73.00
(7 rows)
```

Take the row for product 49 (24.00):

- `ROWS 1 PRECEDING`: the previous row (51, 22.00) and this one: 46.00.
- `RANGE 5 PRECEDING`: every row with a price between 19.00 and 24.00: 19 + 19 + 22 + 24 = 84.00.
- `GROUPS 1 PRECEDING`: the previous peer group ({22.00}) and this one ({24.00}): 46.00. For product 51 (22.00), the previous peer group is *both* tees, so `groups_1` is 19 + 19 + 22 = 60.00, while `rows_1` is only 19 + 22 = 41.00.

`RANGE` with an offset requires exactly one `ORDER BY` column, of a type you can add and subtract: numbers, dates, timestamps (with an `interval` offset).

### 8.3 RANGE over time: a real 3-month moving average

Back to Casa Cucina's gappy months from section 7. What the business wants is "the average monthly revenue over the current and the two previous *calendar* months", counting a month without sales as 0. A `RANGE` frame with an `interval` offset selects rows by date distance, not by row count:

```sql
WITH monthly AS (
    SELECT date_trunc('month', created_at)::date AS month, sum(total) AS revenue
    FROM orders
    WHERE seller_id = 3 AND status IN ('paid', 'shipped', 'delivered')
    GROUP BY 1
)
SELECT month, revenue,
       round(avg(revenue) OVER (ORDER BY month
                                ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2) AS avg_3_rows,
       count(*) OVER (ORDER BY month
                      RANGE BETWEEN interval '2 months' PRECEDING AND CURRENT ROW) AS n,
       round(sum(revenue) OVER (ORDER BY month
                                RANGE BETWEEN interval '2 months' PRECEDING AND CURRENT ROW) / 3, 2) AS avg_3_months
FROM monthly
ORDER BY month;
```

```text
   month    | revenue | avg_3_rows | n | avg_3_months 
------------+---------+------------+---+--------------
 2024-01-01 |  280.00 |     280.00 | 1 |        93.33
 2024-02-01 |  293.00 |     286.50 | 2 |       191.00
 2024-03-01 |   79.00 |     217.33 | 3 |       217.33
 2024-04-01 |  201.99 |     191.33 | 3 |       191.33
 2024-06-01 |  933.99 |     404.99 | 2 |       378.66
 2024-07-01 |  253.00 |     462.99 | 2 |       395.66
 2024-08-01 |   83.00 |     423.33 | 3 |       423.33
 2024-09-01 |   34.99 |     123.66 | 3 |       123.66
 2024-12-01 |   93.99 |      70.66 | 1 |        31.33
 2025-03-01 |  383.00 |     170.66 | 1 |       127.67
 2025-04-01 |  362.00 |     279.66 | 2 |       248.33
 2025-05-01 |  331.98 |     358.99 | 3 |       358.99
 2025-06-01 |  233.00 |     308.99 | 3 |       308.99
(13 rows)
```

The `n` column shows how many rows the `RANGE` frame actually contains. For 2025-03-01 it is 1: December 2024 is more than two months back, so only March itself is in the frame, and the 3-month average is 383.00 / 3 = 127.67, treating January and February as zero. The row-based average (170.66) mixed in months from last year. Dividing the sum by 3, rather than using `avg`, is what counts missing months as zero. If you would rather average only the months that had sales, `avg()` over the `RANGE` frame does that.

(The other approach is to fill in the missing months first, with `generate_series` and a `LEFT JOIN` as in module 05, and then use `ROWS`. Section 11.5 does that for month-over-month change, where `lag` has no `RANGE` equivalent.)

The first two rows show a classic edge effect: in January 2024 there are no earlier months at all, so the "3-month average" includes two months before the seller existed. Whether that is right is a business question; filtering on `n = 3`, or on `month >= first month + 2 months`, are the usual answers.

### 8.4 EXCLUDE

The `EXCLUDE` option removes rows *from* the frame:

| Option | Removes |
|---|---|
| `EXCLUDE NO OTHERS` | nothing (the default) |
| `EXCLUDE CURRENT ROW` | the current row |
| `EXCLUDE GROUP` | the current row and all its peers |
| `EXCLUDE TIES` | the current row's peers, but keeps the current row itself |

```sql
SELECT id, price,
       sum(price) OVER (ORDER BY price ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                        EXCLUDE CURRENT ROW) AS ex_current,
       sum(price) OVER (ORDER BY price ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                        EXCLUDE GROUP) AS ex_group,
       sum(price) OVER (ORDER BY price ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                        EXCLUDE TIES) AS ex_ties
FROM products
WHERE seller_id = 4 AND price < 40
ORDER BY price, id;
```

```text
 id | price | ex_current | ex_group | ex_ties 
----+-------+------------+----------+---------
 62 |  0.00 |     157.00 |   157.00 |  157.00
 47 | 19.00 |     138.00 |   119.00 |  138.00
 48 | 19.00 |     138.00 |   119.00 |  138.00
 51 | 22.00 |     135.00 |   135.00 |  157.00
 49 | 24.00 |     133.00 |   133.00 |  157.00
 54 | 34.00 |     123.00 |   123.00 |  157.00
 50 | 39.00 |     118.00 |   118.00 |  157.00
(7 rows)
```

The full frame sums to 157.00. For a tee, `EXCLUDE CURRENT ROW` removes one 19.00 (138.00), `EXCLUDE GROUP` removes both (119.00), and `EXCLUDE TIES` removes the *other* tee (also 138.00). For rows without peers, `EXCLUDE TIES` removes nothing.

The practical use is "compare me with the others". How does each active cookware product's price compare with the average of the *other* cookware products?

```sql
SELECT id, left(title, 26) AS title, category_id, price,
       round(avg(price) OVER others, 2) AS avg_of_others,
       round(price / avg(price) OVER others, 2) AS ratio
FROM products
WHERE category_id = 10 AND status = 'active'
WINDOW others AS (PARTITION BY category_id
                  ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                  EXCLUDE CURRENT ROW)
ORDER BY price DESC;
```

```text
 id |           title            | category_id | price  | avg_of_others | ratio 
----+----------------------------+-------------+--------+---------------+-------
 37 | Dutch Oven 5.5L Enamel     |          10 | 119.00 |         43.25 |  2.75
 39 | Pasta Pot with Strainer 6L |          10 |  54.00 |         59.50 |  0.91
 34 | Cast Iron Skillet 26cm     |          10 |  45.00 |         61.75 |  0.73
 35 | Stainless Steel Saucepan 2 |          10 |  39.00 |         63.25 |  0.62
 36 | Non-Stick Frying Pan 28cm  |          10 |  34.99 |         64.25 |  0.54
(5 rows)
```

Including the Dutch oven in its own comparison would have dragged the average up and hidden how much of an outlier it is. Without `EXCLUDE`, you'd write `(sum(price) OVER w - price) / (count(*) OVER w - 1)`, which works but needs a guard for one-product categories.

---

## 9. Named windows: the WINDOW clause

When several functions share a window definition, name it once in a `WINDOW` clause (placed after `HAVING`, before `ORDER BY`) and refer to it by name. You've seen it in sections 5, 6 and 8.4. A named window can also be a **base** that a function extends with an `ORDER BY` or a frame:

```sql
SELECT id, seller_id, created_at::date AS ordered_on, total,
       count(*)   OVER per_seller                       AS seller_orders,
       sum(total) OVER (per_seller ORDER BY created_at, id) AS seller_running_total
FROM orders
WHERE status = 'delivered' AND seller_id IN (2, 4) AND created_at >= '2025-04-01'
WINDOW per_seller AS (PARTITION BY seller_id)
ORDER BY seller_id, created_at;
```

```text
 id  | seller_id | ordered_on | total  | seller_orders | seller_running_total 
-----+-----------+------------+--------+---------------+----------------------
  86 |         2 | 2025-04-01 | 105.98 |             5 |               105.98
  88 |         2 | 2025-04-17 |  34.00 |             5 |               139.98
  93 |         2 | 2025-05-01 | 119.48 |             5 |               259.46
  96 |         2 | 2025-05-17 |  24.50 |             5 |               283.96
 101 |         2 | 2025-06-12 |  69.46 |             5 |               353.42
  89 |         4 | 2025-04-18 | 401.00 |             3 |               401.00
  92 |         4 | 2025-04-30 |  81.00 |             3 |               482.00
 100 |         4 | 2025-06-05 | 132.00 |             3 |               614.00
(8 rows)
```

The rules for extending: the referencing window may add an `ORDER BY` only if the base has none, and it may add a frame. It can't change the base's `PARTITION BY`. Note the parentheses: `OVER w` uses `w` as is, `OVER (w ORDER BY ...)` extends it. And `OVER (w)` with nothing else is allowed only if `w` has no frame clause; write `OVER w` instead.

Named windows are also a performance hint to yourself: functions that share the *same* window (same partition and order) are computed in a single sort and a single pass. Each distinct `PARTITION BY`/`ORDER BY` combination needs its own sort. The plan shows one `WindowAgg` node per distinct window. With large tables, five different orderings mean five sorts, so it pays to align them where you can.

---

## 10. Filtering on window results

Since window functions run after `WHERE` (section 4.2), you can't filter on them directly:

```sql
SELECT id, seller_id, total,
       rank() OVER (PARTITION BY seller_id ORDER BY total DESC) AS r
FROM orders
WHERE rank() OVER (PARTITION BY seller_id ORDER BY total DESC) <= 2;
```

```text
ERROR:  window functions are not allowed in WHERE
LINE 4: WHERE rank() OVER (PARTITION BY seller_id ORDER BY total DES...
              ^
```

and the alias from the `SELECT` list doesn't exist yet when `WHERE` runs either:

```sql
SELECT id, seller_id, total,
       rank() OVER (PARTITION BY seller_id ORDER BY total DESC) AS r
FROM orders
WHERE r <= 2;
```

```text
ERROR:  column "r" does not exist
LINE 4: WHERE r <= 2;
              ^
```

The solution is always the same: compute the window function in a subquery or CTE, and filter in the outer query, where it is just a column. The next section is full of examples. (Some other databases offer a `QUALIFY` clause for this; PostgreSQL 17 does not.)

> **Under the hood:** the planner can sometimes stop a window computation early. When the outer query filters `row_number() OVER (...) <= N` or `rank() ... <= N` (and similar monotonic conditions), PostgreSQL 15+ uses a "run condition" to stop evaluating a partition once the condition can no longer be true. So the subquery pattern is not as wasteful as it looks.

---

## 11. Classic patterns

### 11.1 Top-N per group: three ways

"Each seller's two largest paid orders" is the archetypal top-N-per-group question. There are three idiomatic solutions in PostgreSQL.

**1. Window function in a subquery.** Number the rows within each group, keep the first N:

```sql
SELECT seller_id, id, created_at::date, total
FROM (
    SELECT o.*,
           row_number() OVER (PARTITION BY seller_id ORDER BY total DESC, id) AS rn
    FROM orders AS o
    WHERE status IN ('paid', 'shipped', 'delivered')
) AS ranked
WHERE rn <= 2
ORDER BY seller_id, rn;
```

```text
 seller_id | id  | created_at |  total  
-----------+-----+------------+---------
         1 |  40 | 2024-08-06 | 8940.00
         1 | 114 | 2025-06-27 | 4399.98
         2 |  29 | 2024-05-29 |  237.00
         2 |  93 | 2025-05-01 |  119.48
         3 |  34 | 2024-06-24 |  521.00
         3 |  30 | 2024-06-02 |  412.99
         4 |  89 | 2025-04-18 |  401.00
         4 |  85 | 2025-03-20 |  376.00
(8 rows)
```

Swap `row_number()` for `rank()` to include ties (possibly more than N rows per group), or `dense_rank()` for "the top N distinct values".

**2. `LATERAL` with `LIMIT`** (module 04). For each seller, run a small "top 2" query:

```sql
SELECT s.id AS seller_id, s.shop_name, top.id, top.total
FROM sellers AS s
CROSS JOIN LATERAL (
    SELECT o.id, o.total
    FROM orders AS o
    WHERE o.seller_id = s.id
      AND o.status IN ('paid', 'shipped', 'delivered')
    ORDER BY o.total DESC, o.id
    LIMIT 2
) AS top
ORDER BY s.id, top.total DESC;
```

```text
 seller_id |    shop_name    | id  |  total  
-----------+-----------------+-----+---------
         1 | TechTrove       |  40 | 8940.00
         1 | TechTrove       | 114 | 4399.98
         2 | Bookworm Corner |  29 |  237.00
         2 | Bookworm Corner |  93 |  119.48
         3 | Casa Cucina     |  34 |  521.00
         3 | Casa Cucina     |  30 |  412.99
         4 | Threadbare Co.  |  89 |  401.00
         4 | Threadbare Co.  |  85 |  376.00
(8 rows)
```

With `LEFT JOIN LATERAL (...) AS top ON true`, sellers 5 and 6 would also appear, with NULLs, which the window version can't do because it only sees sellers that have orders.

**3. `DISTINCT ON`** (module 05), only for N = 1:

```sql
SELECT DISTINCT ON (seller_id) seller_id, id, total
FROM orders
WHERE status IN ('paid', 'shipped', 'delivered')
ORDER BY seller_id, total DESC, id;
```

```text
 seller_id | id |  total  
-----------+----+---------
         1 | 40 | 8940.00
         2 | 29 |  237.00
         3 | 34 |  521.00
         4 | 89 |  401.00
(4 rows)
```

**Which one to use?**

| | Window + filter | `LATERAL ... LIMIT` | `DISTINCT ON` |
|---|---|---|---|
| N | any | any | 1 only |
| Ties | choose: `row_number`/`rank`/`dense_rank` | `LIMIT` cuts arbitrarily (or use `FETCH FIRST n ROWS WITH TIES`) | one row, chosen by `ORDER BY` |
| Groups with no rows | no | yes, with `LEFT JOIN LATERAL` | no |
| Work done | reads and sorts **all** rows of all groups | one small query **per group**; with an index on `(seller_id, total DESC)` each is a short index scan | reads and sorts all rows |
| Best when | groups are small or you need the whole table anyway; portable SQL | there are few groups, each with many rows, and a matching index | N = 1 and you want the shortest query |

On Bazaar's large dataset (module 11 on), "the latest 3 orders of each of 2,000 sellers" is far faster with `LATERAL` and an index than ranking 500,000 orders. "The best-selling product per category" over aggregated data is naturally a window query. Module 12 measures this.

### 11.2 Deduplication

Imported data often contains duplicates. Suppose Casa Cucina uploads a stock feed in which some SKUs appear more than once, and the newest line per SKU wins:

```sql
CREATE TEMP TABLE stock_feed (
    sku          text        NOT NULL,
    quantity     int         NOT NULL,
    received_at  timestamptz NOT NULL
);
INSERT INTO stock_feed VALUES
    ('CC-CW-001', 40, '2025-06-29 08:00'),
    ('CC-CW-001', 38, '2025-06-29 14:00'),
    ('CC-CW-002', 12, '2025-06-29 08:00'),
    ('CC-CU-001', 25, '2025-06-29 08:00'),
    ('CC-CU-001', 25, '2025-06-29 08:00'),
    ('CC-CU-001', 22, '2025-06-30 08:00'),
    ('CC-KI-001',  9, '2025-06-29 08:00');

SELECT sku, quantity, received_at,
       row_number() OVER (PARTITION BY sku ORDER BY received_at DESC) AS rn
FROM stock_feed
ORDER BY sku, received_at DESC;
```

```text
    sku    | quantity |      received_at       | rn 
-----------+----------+------------------------+----
 CC-CU-001 |       22 | 2025-06-30 08:00:00+00 |  1
 CC-CU-001 |       25 | 2025-06-29 08:00:00+00 |  2
 CC-CU-001 |       25 | 2025-06-29 08:00:00+00 |  3
 CC-CW-001 |       38 | 2025-06-29 14:00:00+00 |  1
 CC-CW-001 |       40 | 2025-06-29 08:00:00+00 |  2
 CC-CW-002 |       12 | 2025-06-29 08:00:00+00 |  1
 CC-KI-001 |        9 | 2025-06-29 08:00:00+00 |  1
(7 rows)
```

Rows with `rn = 1` are the keepers. To **read** deduplicated data, filter `WHERE rn = 1` in an outer query (or use `DISTINCT ON (sku) ... ORDER BY sku, received_at DESC`). To **delete** the duplicates, you need a way to point at a physical row, because the two `CC-CU-001 | 25` lines are identical in every column. Every PostgreSQL row has a hidden system column, `ctid`, its physical location (page, slot):

```sql
DELETE FROM stock_feed
WHERE ctid IN (
    SELECT ctid
    FROM (
        SELECT ctid,
               row_number() OVER (PARTITION BY sku ORDER BY received_at DESC) AS rn
        FROM stock_feed
    ) AS ranked
    WHERE rn > 1
);
SELECT * FROM stock_feed ORDER BY sku;
```

```text
DELETE 3
    sku    | quantity |      received_at       
-----------+----------+------------------------
 CC-CU-001 |       22 | 2025-06-30 08:00:00+00
 CC-CW-001 |       38 | 2025-06-29 14:00:00+00
 CC-CW-002 |       12 | 2025-06-29 08:00:00+00
 CC-KI-001 |        9 | 2025-06-29 08:00:00+00
(4 rows)
```

`stock_feed` is a **temporary table**: only your session can see it, and it disappears when the session ends. Keep the session open, because section 12 uses the cleaned feed.

> **Pitfall:** `ctid` changes whenever a row is updated (an update writes a new row version, module 16) and after `VACUUM FULL`. Use it only within one statement or one transaction, as here, never store it. If the table has a primary key, use that instead. Then put a unique constraint on the natural key so the duplicates can't come back.

### 11.3 Gaps and islands

"For each customer, find runs of consecutive months in which they bought something." A run of consecutive values is an **island**; the missing values between islands are **gaps**. The classic trick:

1. Turn each month into a consecutive integer: `month_no = year * 12 + month`.
2. Number each customer's months in order with `row_number()`.
3. Within an island, both `month_no` and `rn` go up by exactly 1 per row, so **`month_no − rn` is constant**. When a gap occurs, `month_no` jumps but `rn` doesn't, so the difference changes. That difference identifies the island.

Here are the steps for user 8, Marcus Lee, one of the top buyers:

```sql
WITH user_months AS (
    SELECT DISTINCT user_id, date_trunc('month', created_at)::date AS month
    FROM orders
    WHERE status <> 'cancelled'
),
numbered AS (
    SELECT user_id, month,
           (extract(year FROM month) * 12 + extract(month FROM month))::int AS month_no,
           row_number() OVER (PARTITION BY user_id ORDER BY month) AS rn
    FROM user_months
)
SELECT user_id, month, month_no, rn, month_no - rn AS island
FROM numbered
WHERE user_id = 8
ORDER BY month;
```

```text
 user_id |   month    | month_no | rn | island 
---------+------------+----------+----+--------
       8 | 2024-01-01 |    24289 |  1 |  24288
       8 | 2024-02-01 |    24290 |  2 |  24288
       8 | 2024-03-01 |    24291 |  3 |  24288
       8 | 2024-04-01 |    24292 |  4 |  24288
       8 | 2024-07-01 |    24295 |  5 |  24290
       8 | 2024-08-01 |    24296 |  6 |  24290
       8 | 2024-10-01 |    24298 |  7 |  24291
       8 | 2024-11-01 |    24299 |  8 |  24291
       8 | 2024-12-01 |    24300 |  9 |  24291
       8 | 2025-02-01 |    24302 | 10 |  24292
       8 | 2025-03-01 |    24303 | 11 |  24292
       8 | 2025-04-01 |    24304 | 12 |  24292
       8 | 2025-05-01 |    24305 | 13 |  24292
       8 | 2025-06-01 |    24306 | 14 |  24292
(14 rows)
```

Four islands: January–April 2024, July–August 2024, October–December 2024 and February–June 2025. The `island` value itself means nothing; it's only a label that is equal within an island. Now group by it, and keep each user's longest streak of at least 3 months:

```sql
WITH user_months AS (
    SELECT DISTINCT user_id, date_trunc('month', created_at)::date AS month
    FROM orders
    WHERE status <> 'cancelled'
),
islands AS (
    SELECT user_id, month,
           (extract(year FROM month) * 12 + extract(month FROM month))::int
             - row_number() OVER (PARTITION BY user_id ORDER BY month) AS island
    FROM user_months
),
streaks AS (
    SELECT user_id, min(month) AS first_month, max(month) AS last_month, count(*) AS months
    FROM islands
    GROUP BY user_id, island
)
SELECT DISTINCT ON (user_id) user_id, first_month, last_month, months
FROM streaks
WHERE months >= 3
ORDER BY user_id, months DESC, first_month;
```

```text
 user_id | first_month | last_month | months 
---------+-------------+------------+--------
       8 | 2025-02-01  | 2025-06-01 |      5
      11 | 2025-04-01  | 2025-06-01 |      3
      12 | 2024-03-01  | 2024-05-01 |      3
      13 | 2024-01-01  | 2024-03-01 |      3
      14 | 2024-02-01  | 2024-04-01 |      3
      25 | 2025-03-01  | 2025-06-01 |      4
(6 rows)
```

The same trick works for consecutive days (`day - rn * interval '1 day'`, or `date - integer` directly), consecutive ids, or "consecutive rows with the same status" (there, use the difference of two `row_number()`s: one over the user, one over the user and status). **Gaps** come from the other direction: `lead(month)` tells you the next month with activity, and any `next_month > month + 1 month` is a gap (exercise 9).

### 11.4 Sessionization (a preview)

Web analytics groups a user's clicks into **sessions**: a new session starts when more than 30 minutes have passed since the user's previous click. It's gaps-and-islands with a threshold instead of "exactly one apart", and the standard solution is two window passes:

1. `lag` flags each event that **starts** a session (first event, or gap > 30 minutes).
2. A **running sum of the flags** numbers the sessions.

Bazaar's real `events` table arrives in module 14. Until then, a few inline rows show the technique:

```sql
WITH clicks (user_id, occurred_at) AS (
    VALUES (8, timestamptz '2025-06-01 09:00'), (8, '2025-06-01 09:04'), (8, '2025-06-01 09:20'),
           (8, '2025-06-01 11:15'), (8, '2025-06-01 11:16'),
           (13, '2025-06-01 09:30'), (13, '2025-06-01 10:05'), (13, '2025-06-01 10:20')
),
flagged AS (
    SELECT user_id, occurred_at,
           CASE WHEN occurred_at - lag(occurred_at) OVER (PARTITION BY user_id ORDER BY occurred_at)
                     <= interval '30 minutes'
                THEN 0 ELSE 1 END AS starts_session
    FROM clicks
)
SELECT user_id, occurred_at, starts_session,
       sum(starts_session) OVER (PARTITION BY user_id ORDER BY occurred_at) AS session_no
FROM flagged
ORDER BY user_id, occurred_at;
```

```text
 user_id |      occurred_at       | starts_session | session_no 
---------+------------------------+----------------+------------
       8 | 2025-06-01 09:00:00+00 |              1 |          1
       8 | 2025-06-01 09:04:00+00 |              0 |          1
       8 | 2025-06-01 09:20:00+00 |              0 |          1
       8 | 2025-06-01 11:15:00+00 |              1 |          2
       8 | 2025-06-01 11:16:00+00 |              0 |          2
      13 | 2025-06-01 09:30:00+00 |              1 |          1
      13 | 2025-06-01 10:05:00+00 |              1 |          2
      13 | 2025-06-01 10:20:00+00 |              0 |          2
(8 rows)
```

For the first event of each user, `lag` is NULL, so the comparison is NULL, which `CASE` treats as "not true", and the event starts a session. That's exactly the behaviour we want, but make sure you understand why it works (three-valued logic, module 03) rather than relying on it by accident. Two window functions can't be nested directly (`sum(lag(...)) OVER` is an error), which is why the flags are computed in a CTE first.

### 11.5 Month-over-month change

"How did each seller's revenue change compared with the previous month?" is `lag` over monthly totals, but with the gap problem from section 6.1: if a seller had no sales in March, `lag` in April would compare with February. The fix is to build the full seller × month grid first, then apply `lag`:

```sql
WITH months AS (
    SELECT generate_series(date '2025-01-01', date '2025-06-01', interval '1 month')::date AS month
),
revenue AS (
    SELECT seller_id, date_trunc('month', created_at)::date AS month, sum(total) AS revenue
    FROM orders
    WHERE status IN ('paid', 'shipped', 'delivered')
    GROUP BY 1, 2
),
grid AS (
    SELECT s.id AS seller_id, m.month, coalesce(r.revenue, 0) AS revenue
    FROM sellers AS s
    CROSS JOIN months AS m
    LEFT JOIN revenue AS r ON r.seller_id = s.id AND r.month = m.month
    WHERE s.id IN (2, 3)
)
SELECT seller_id, month, revenue,
       lag(revenue) OVER w AS prev_month,
       revenue - lag(revenue) OVER w AS change,
       round(100 * (revenue - lag(revenue) OVER w) / nullif(lag(revenue) OVER w, 0), 1) AS pct_change
FROM grid
WINDOW w AS (PARTITION BY seller_id ORDER BY month)
ORDER BY seller_id, month;
```

```text
 seller_id |   month    | revenue | prev_month | change | pct_change 
-----------+------------+---------+------------+--------+------------
         2 | 2025-01-01 |   19.95 |            |        |           
         2 | 2025-02-01 |   72.96 |      19.95 |  53.01 |      265.7
         2 | 2025-03-01 |       0 |      72.96 | -72.96 |     -100.0
         2 | 2025-04-01 |  139.98 |          0 | 139.98 |           
         2 | 2025-05-01 |  143.98 |     139.98 |   4.00 |        2.9
         2 | 2025-06-01 |  151.39 |     143.98 |   7.41 |        5.1
         3 | 2025-01-01 |       0 |            |        |           
         3 | 2025-02-01 |       0 |          0 |      0 |           
         3 | 2025-03-01 |  383.00 |          0 | 383.00 |           
         3 | 2025-04-01 |  362.00 |     383.00 | -21.00 |       -5.5
         3 | 2025-05-01 |  331.98 |     362.00 | -30.02 |       -8.3
         3 | 2025-06-01 |  233.00 |     331.98 | -98.98 |      -29.8
(12 rows)
```

Details that matter in real reports:

- `nullif(lag(...), 0)` turns a zero denominator into NULL, so growth from 0 is NULL ("not meaningful") instead of a division-by-zero error.
- January has no previous month *in the grid*, so `lag` is NULL, even though December 2024 had data. If you need it, start the grid one month earlier and filter afterwards. Filtering the months in `WHERE` would remove December before the window runs.
- The same `lag` over the same window appears three times. PostgreSQL computes one sort for window `w` and evaluates the three calls in a single pass, so the repetition costs little. If it bothers you, compute `prev_month` in a CTE and do the arithmetic outside.

The milestone builds on this pattern: a seller leaderboard with month-over-month *rank* change.

---

## 12. MERGE

### 12.1 What MERGE does

`MERGE` (standard SQL; PostgreSQL 15+) synchronises a **target** table with a **source** (a table, a subquery or a `VALUES` list) in one statement. It joins the two, then for each row decides what to do based on whether it **matched**:

```sql
MERGE INTO target AS t
USING source AS s
ON join_condition
WHEN MATCHED [AND condition] THEN
    UPDATE SET ... | DELETE | DO NOTHING
WHEN NOT MATCHED [BY TARGET] [AND condition] THEN
    INSERT (...) VALUES (...) | DO NOTHING
WHEN NOT MATCHED BY SOURCE [AND condition] THEN     -- PostgreSQL 17+
    UPDATE SET ... | DELETE | DO NOTHING
[RETURNING ...];                                    -- PostgreSQL 17+
```

- **`WHEN MATCHED`**: the row exists in both. You may `UPDATE` the target row, `DELETE` it, or do nothing.
- **`WHEN NOT MATCHED`** (or `NOT MATCHED BY TARGET`): the source row has no partner in the target. The only actions are `INSERT` or nothing.
- **`WHEN NOT MATCHED BY SOURCE`** (PostgreSQL 17): a target row that no source row matched. You may `UPDATE` or `DELETE` it. This is what makes a real "make the target look like the source" sync possible in one statement.
- Each `WHEN` can have an extra `AND condition`. The clauses are tried **in order**, and the **first** clause whose kind and condition match wins. A row that matches no clause is left alone.
- **`RETURNING`** (PostgreSQL 17) returns the affected rows. The special function `merge_action()` tells you which action happened: `'INSERT'`, `'UPDATE'` or `'DELETE'`.

### 12.2 A full sync: seller_month_stats

Suppose reporting keeps a summary table, refreshed by a nightly job. On 2025-06-20 the job stored June's numbers so far; now June is over and the job runs again. The table also contains a stale June row for seller 5 (who had no sales, perhaps an order was later reversed) and a May row that must not be touched:

```sql
BEGIN;
CREATE TABLE seller_month_stats (
    seller_id   bigint        NOT NULL REFERENCES sellers (id),
    month       date          NOT NULL,
    orders      int           NOT NULL,
    revenue     numeric(12,2) NOT NULL,
    PRIMARY KEY (seller_id, month)
);
-- the snapshot taken on 2025-06-20
INSERT INTO seller_month_stats (seller_id, month, orders, revenue)
SELECT seller_id, date '2025-06-01', count(*), sum(total)
FROM orders
WHERE status IN ('paid', 'shipped', 'delivered')
  AND created_at >= '2025-06-01' AND created_at < '2025-06-20'
GROUP BY seller_id;
INSERT INTO seller_month_stats VALUES (5, '2025-06-01', 1, 149.00);  -- must disappear
INSERT INTO seller_month_stats VALUES (1, '2025-05-01', 1, 3455.00); -- other month: keep
SELECT * FROM seller_month_stats ORDER BY month, seller_id;
```

```text
 seller_id |   month    | orders | revenue 
-----------+------------+--------+---------
         1 | 2025-05-01 |      1 | 3455.00
         1 | 2025-06-01 |      4 | 2836.96
         2 | 2025-06-01 |      1 |   69.46
         4 | 2025-06-01 |      1 |  132.00
         5 | 2025-06-01 |      1 |  149.00
(5 rows)
```

Now one `MERGE` brings June up to date:

```sql
MERGE INTO seller_month_stats AS t
USING (
    SELECT seller_id, date '2025-06-01' AS month, count(*) AS orders, sum(total) AS revenue
    FROM orders
    WHERE status IN ('paid', 'shipped', 'delivered')
      AND created_at >= '2025-06-01' AND created_at < '2025-07-01'
    GROUP BY seller_id
) AS s
ON t.seller_id = s.seller_id AND t.month = s.month
WHEN MATCHED AND (t.orders, t.revenue) IS DISTINCT FROM (s.orders, s.revenue) THEN
    UPDATE SET orders = s.orders, revenue = s.revenue
WHEN MATCHED THEN
    DO NOTHING
WHEN NOT MATCHED THEN
    INSERT (seller_id, month, orders, revenue)
    VALUES (s.seller_id, s.month, s.orders, s.revenue)
WHEN NOT MATCHED BY SOURCE AND t.month = date '2025-06-01' THEN
    DELETE
RETURNING merge_action() AS action, t.*;
```

```text
 action | seller_id |   month    | orders | revenue 
--------+-----------+------------+--------+---------
 UPDATE |         1 | 2025-06-01 |      8 | 9492.93
 UPDATE |         2 | 2025-06-01 |      3 |  151.39
 UPDATE |         4 | 2025-06-01 |      3 |  228.00
 DELETE |         5 | 2025-06-01 |      1 |  149.00
 INSERT |         3 | 2025-06-01 |      3 |  233.00
(5 rows)

MERGE 5
```

```sql
SELECT * FROM seller_month_stats ORDER BY month, seller_id;
ROLLBACK;
```

```text
 seller_id |   month    | orders | revenue 
-----------+------------+--------+---------
         1 | 2025-05-01 |      1 | 3455.00
         1 | 2025-06-01 |      8 | 9492.93
         2 | 2025-06-01 |      3 |  151.39
         3 | 2025-06-01 |      3 |  233.00
         4 | 2025-06-01 |      3 |  228.00
(5 rows)
```

Walk through the clauses:

- Sellers 1, 2 and 4 matched and their numbers changed: the first `WHEN MATCHED` updated them. A row that matched with *unchanged* numbers would fall through to `WHEN MATCHED THEN DO NOTHING`, which avoids writing a new row version for nothing (an update always writes, even if the values are the same, module 16). The second clause is actually redundant, because a matched row that matches no clause is left alone anyway; it's there to make the intent explicit.
- Seller 3 had no row yet: `WHEN NOT MATCHED` inserted it.
- Seller 5's June row matched no source row: `WHEN NOT MATCHED BY SOURCE` deleted it. The `AND t.month = date '2025-06-01'` condition is essential: the source only describes June, so the May row also has "no matching source row". Without the condition, the `MERGE` would have deleted May.
- In `RETURNING`, `t.*` shows the new values for inserts and updates and the old values for deletes. You can also refer to source columns (`s.*`); for `NOT MATCHED BY SOURCE` rows they are NULL.

`(a, b) IS DISTINCT FROM (c, d)` compares the row values NULL-safely: unlike `<>`, it treats two NULLs as equal and a NULL vs a value as different. That makes it the right operator for "did anything change".

> **Pitfall:** with `WHEN NOT MATCHED BY SOURCE`, the source must describe *everything* in the part of the target you are syncing. If the source query is filtered (one month, one seller), the clause must be filtered the same way. Forgetting this is the `MERGE` equivalent of a `DELETE` without `WHERE`.

### 12.3 One source row per target row

If two source rows match the same target row, `MERGE` refuses, instead of silently applying one of them the way an `UPDATE ... FROM` would. Had you run the `MERGE` below on the raw stock feed from section 11.2, *before* deleting the duplicates, you would have got:

```text
ERROR:  MERGE command cannot affect row a second time
HINT:  Ensure that not more than one source row matches any one target row.
```

So deduplicate the source first (section 11.2), then merge. Here is the deduplicated feed applied to `inventory`. The business rule: never set the stock below what is already reserved for open orders; skip such lines instead.

```sql
SELECT product_id, quantity, reserved FROM inventory WHERE product_id IN (34, 35, 40, 46) ORDER BY 1;
```

```text
 product_id | quantity | reserved 
------------+----------+----------
         34 |       20 |        2
         35 |       18 |        0
         40 |       15 |        0
         46 |       16 |        0
(4 rows)
```

```sql
BEGIN;
MERGE INTO inventory AS i
USING (
    SELECT p.id AS product_id, f.quantity
    FROM stock_feed AS f
    JOIN products AS p ON p.sku = f.sku AND p.seller_id = 3
) AS s
ON i.product_id = s.product_id
WHEN MATCHED AND s.quantity < i.reserved THEN
    DO NOTHING
WHEN MATCHED THEN
    UPDATE SET quantity = s.quantity, updated_at = now()
RETURNING merge_action(), i.product_id, i.quantity, i.reserved;
ROLLBACK;
```

```text
 merge_action | product_id | quantity | reserved 
--------------+------------+----------+----------
 UPDATE       |         34 |       38 |        2
 UPDATE       |         35 |       12 |        0
 UPDATE       |         40 |       22 |        0
 UPDATE       |         46 |        9 |        0
(4 rows)

MERGE 4
```

The join to `products` translates the seller's SKUs into product ids, and `AND p.seller_id = 3` is important: SKUs are only unique per seller (`products_seller_id_sku_key`), so without it, another seller's identical SKU could match. Rows skipped by `DO NOTHING` don't appear in `RETURNING`.

### 12.4 MERGE versus INSERT ... ON CONFLICT

Module 03 introduced the upsert, `INSERT ... ON CONFLICT (...) DO UPDATE`. Both statements can "insert or update", so which should you use?

**`INSERT ... ON CONFLICT` is built for concurrency.** It relies on a unique index (or constraint) as its arbiter. If another transaction inserts the same key at the same moment, `ON CONFLICT` waits for it and then takes the `DO UPDATE` path. It guarantees *either an insert or an update*, never a duplicate-key error, under any concurrency.

**`MERGE` decides first, then acts.** It performs the join with its snapshot, and if it finds no match it tries to insert. If a concurrent transaction inserted the same key in the meantime, the insert fails. Here is a demonstration with two psql sessions and a scratch table:

```sql
CREATE TABLE scratch_counters (name text PRIMARY KEY, hits int NOT NULL);
```

| Session A | Session B |
|---|---|
| `BEGIN;` | |
| `INSERT INTO scratch_counters VALUES ('home', 1);` | |
| | `MERGE INTO scratch_counters AS t`<br>`USING (VALUES ('home')) AS s (name) ON t.name = s.name`<br>`WHEN MATCHED THEN UPDATE SET hits = t.hits + 1`<br>`WHEN NOT MATCHED THEN INSERT VALUES (s.name, 1);`<br>*(waits for A, because of the unique index)* |
| `COMMIT;` | |
| | `ERROR:  duplicate key value violates unique constraint "scratch_counters_pkey"`<br>`DETAIL:  Key (name)=(home) already exists.` |

Repeat it with an empty table, and in session B use the upsert instead:

```sql
INSERT INTO scratch_counters VALUES ('home', 1)
ON CONFLICT (name) DO UPDATE SET hits = scratch_counters.hits + 1
RETURNING *;
```

Session B waits for A in the same way, and after A commits it returns:

```text
 name | hits 
------+------
 home |    2
(1 row)
```

(Clean up with `DROP TABLE scratch_counters;`.) Module 10 explains the locking behind this.

The rest of the comparison:

| | `INSERT ... ON CONFLICT` | `MERGE` |
|---|---|---|
| Needs a unique index on the key | yes (it is the arbiter) | no; any join condition |
| Concurrent inserts of the same key | safe: becomes an update | can fail with a unique violation (retry it), or insert duplicates if there is no unique index |
| Actions | insert, or update / do nothing on conflict | insert, update, delete, do nothing; several conditional branches |
| Rows only in the target | can't touch them | `WHEN NOT MATCHED BY SOURCE` (17+) |
| Source with duplicate keys | error "ON CONFLICT DO UPDATE command cannot affect row a second time" | error "MERGE command cannot affect row a second time" |
| `RETURNING` | yes | 17+, with `merge_action()` |
| Portability | PostgreSQL (and SQLite) only | SQL standard (Oracle, SQL Server, DB2, ...) |

Rules of thumb:

- **Online traffic** (an API call adds a product to a cart, increments a counter, records "last seen"): `INSERT ... ON CONFLICT`. It's the statement that is correct under concurrency without retry logic.
- **Batch synchronisation** (load a feed, refresh a summary table, apply a diff, where some rows must be deleted, or different conditions lead to different actions): `MERGE`. Batch jobs usually run alone, or they take a lock on the target first, so the concurrency caveat matters less. Still, handle a unique-violation error with a retry.
- **Pure inserts that should skip duplicates**: `INSERT ... ON CONFLICT DO NOTHING`.

---

## Summary

- A **CTE** (`WITH name AS (...)`) names a step of a query. Several CTEs can be chained, and each can read the earlier ones. Since PostgreSQL 12, a side-effect-free CTE referenced once is **inlined**; otherwise it is computed once and **materialized**. `MATERIALIZED`/`NOT MATERIALIZED` override the choice.
- **Data-modifying CTEs** chain `INSERT`/`UPDATE`/`DELETE`/`MERGE ... RETURNING` in one atomic statement. All parts see the **same snapshot**: they can't see each other's changes, only each other's `RETURNING` rows. Never change the same row twice in one statement.
- A **recursive CTE** is an iteration: the anchor fills the working table, the recursive term runs on the previous iteration's rows until it produces nothing. Walk down with `c.parent_id = s.id`, up with `c.id = a.parent_id`. Carry `depth` and a path array; sort by the path for depth-first order.
- Protect recursive queries against cycles: the `CYCLE` clause (PG 14+), a path check with `= ANY(path)`, or a depth limit. `SEARCH DEPTH FIRST`/`BREADTH FIRST` produce sort keys.
- **Window functions** compute over related rows without collapsing them. They run after `WHERE`/`GROUP BY`/`HAVING`, so they can use aggregates but can't be filtered in `WHERE`: wrap them in a subquery or CTE.
- `row_number` gives unique positions (break ties explicitly!), `rank` skips after ties, `dense_rank` doesn't, `ntile` makes buckets, `percent_rank`/`cume_dist` give relative positions. `lag`/`lead` read neighbouring rows.
- With `ORDER BY`, the **default frame** is `RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW` including peers. It makes `last_value` useless and running totals jump on ties. Use an explicit frame, a unique order, or both.
- `ROWS` counts rows, `RANGE` measures values (including `interval` offsets on dates), `GROUPS` counts peer groups. `EXCLUDE` removes the current row, its group or its ties.
- Patterns: top-N per group (window, `LATERAL ... LIMIT`, `DISTINCT ON`), dedup with `row_number` (and `ctid` for exact duplicates), gaps and islands (`value − row_number`), sessionization (`lag` flag + running sum), month-over-month (`lag` over a gap-filled grid).
- **`MERGE`** synchronises a target with a source: `WHEN MATCHED`, `WHEN NOT MATCHED`, and in PG 17 `WHEN NOT MATCHED BY SOURCE` and `RETURNING merge_action()`. Use `INSERT ... ON CONFLICT` for concurrent single-row upserts; use `MERGE` for batch syncs with multiple actions.

## What's next

You can now ask almost any question of the Bazaar schema. Module 07 steps back and asks whether the schema itself is right: how to model entities and relationships, normalise away anomalies, decide when to denormalise on purpose, and keep history. You'll design Bazaar's schema v2: addresses, payments, reviews, carts and price history.
