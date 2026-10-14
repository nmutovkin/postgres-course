# Module 05: Aggregation and Grouping

At the end of every month, each Bazaar shop owner gets the same email: how many orders they received, how much they sold, how much was refunded, and what they'll be paid. The finance team wants the same numbers for all shops at once, with a subtotal per shop and a grand total at the bottom. The marketing team wants to know what share of customers come back for a second order. None of these questions asks for rows. They ask for **summaries** of rows: counts, sums, averages, medians, per shop, per month, per category. This module teaches SQL's machinery for turning many rows into a few meaningful numbers, and the traps (double counting, missing zero months, `NULL`s that silently vanish) that make reports wrong without anyone noticing.

**What you'll learn**

- The aggregate functions `count`, `sum`, `avg`, `min` and `max`, and exactly how each treats `NULL`s and empty inputs. The difference between `count(*)`, `count(col)` and `count(DISTINCT col)`.
- `GROUP BY` semantics: what may appear in the `SELECT` list, and why grouping by a primary key lets you select the table's other columns.
- `HAVING` versus `WHERE`, and the full logical order of a `SELECT`.
- `FILTER (WHERE ...)` and conditional aggregation for pivot-style reports.
- Collecting values with `array_agg`, `string_agg` and `jsonb_agg`, ordered inside the aggregate. `bool_and` and `bool_or`.
- Ordered-set aggregates: `percentile_cont`, `percentile_disc` and `mode`.
- `DISTINCT ON` for "the latest row per group", compared with the `GROUP BY` and `LATERAL` approaches.
- Subtotals and grand totals with `GROUPING SETS`, `ROLLUP`, `CUBE` and the `GROUPING()` function.
- Time-series reports with `date_trunc`, and filling gaps (months with zero sales) with `generate_series` and a `LEFT JOIN`.
- The fan-out double-counting bug when you join before aggregating, and how to fix it by aggregating first.
- Performance intuition: `HashAggregate` versus `GroupAggregate`.

**Before you start**

```bash
./project/bin/reset.sh 05
./project/bin/psql.sh
```

This is the same small dataset as in module 04: 30 users, 6 sellers, 62 products, 120 orders, 235 order lines, all deterministic. NOTES.md (`project/db/NOTES.md`) lists the notable rows, including each seller's revenue, which you'll reproduce in this module. Outputs show `NULL` as `∅` (`\pset null '∅'`, as in module 01's `.psqlrc`).

You need joins, subqueries and `LATERAL` from [module 04](../04-joins-subqueries/), in particular section 8 on fan-out. Two business definitions are used throughout, and they match NOTES.md:

- **Revenue** is the sum of `orders.total` over orders with status `paid`, `shipped` or `delivered`. Pending orders haven't been paid, cancelled orders never were, and refunded orders were paid back.
- **Months** are calendar months of `orders.created_at` in UTC, the server's time zone in the course container. Section 10 explains why that matters.

---

## 1. Aggregate functions

An **aggregate function** takes a whole set of rows and returns one value. Without any grouping, the set is "all rows that survived `WHERE`", and the result is exactly one row:

```sql
SELECT count(*)        AS orders,
       sum(total)      AS sum_of_totals,
       min(total)      AS smallest,
       max(total)      AS largest,
       avg(total)      AS average,
       min(created_at) AS first_order,
       max(created_at) AS last_order
FROM orders;
```

```text
 orders | sum_of_totals | smallest | largest |       average        |      first_order       |       last_order       
--------+---------------+----------+---------+----------------------+------------------------+------------------------
    120 |      55341.98 |     9.99 | 8940.00 | 461.1831666666666667 | 2024-01-12 10:39:36+00 | 2025-06-30 09:54:52+00
(1 row)
```

120 rows went in, one row came out. The largest and smallest orders (40 and 20) and the date range match NOTES.md. A few things to notice:

- `min` and `max` work on any type that can be sorted: numbers, dates, timestamps, text (alphabetically, by the column's collation).
- `sum` of a `numeric(12,2)` column is a `numeric` without the scale limit, so it never overflows the column's precision. `sum` of an `integer` column returns `bigint`, and `sum` of a `bigint` returns `numeric`, for the same reason.
- `avg` of `numeric` returns a `numeric` with many decimals. That's exact arithmetic, not noise, but nobody wants to read it. Round for display: `round(avg(total), 2)`. `avg` of an `integer` column also returns `numeric` (never an integer division), and `avg` of `float8` returns `float8`.

### 1.1 `count(*)`, `count(col)` and `count(DISTINCT col)`

The three forms of `count` answer three different questions:

```sql
SELECT count(*)                  AS products,
       count(description)        AS with_description,
       count(DISTINCT seller_id) AS sellers_with_products,
       count(DISTINCT category_id) AS categories_used
FROM products;
```

```text
 products | with_description | sellers_with_products | categories_used 
----------+------------------+-----------------------+-----------------
       62 |               54 |                     5 |              16
(1 row)
```

- `count(*)` counts **rows**. It doesn't look at any column, so `NULL`s are irrelevant.
- `count(expr)` counts rows where `expr` **is not `NULL`**. Eight products have no description (NOTES.md), so 62 − 8 = 54.
- `count(DISTINCT expr)` counts **distinct non-`NULL` values**. Five sellers have products (seller 6 has none).

That `count(col)` skips `NULL`s isn't a quirk of `count`. It's the rule for every ordinary aggregate.

### 1.2 Aggregates ignore `NULL`s

`sum`, `avg`, `min`, `max` and friends skip `NULL` inputs entirely, as if those rows weren't there. Usually that's what you want. Sometimes it changes the meaning of the answer. A tiny example with a made-up table of three ratings, one missing:

```sql
SELECT count(*)                  AS rows,
       count(rating)             AS rated,
       sum(rating)               AS total,
       avg(rating)               AS avg_of_known,
       avg(coalesce(rating, 0))  AS avg_missing_as_zero
FROM (VALUES (4), (NULL), (5)) AS r (rating);
```

```text
 rows | rated | total |    avg_of_known    | avg_missing_as_zero 
------+-------+-------+--------------------+---------------------
    3 |     2 |     9 | 4.5000000000000000 |  3.0000000000000000
(1 row)
```

`avg(rating)` is 4.5: the average of the *known* ratings, 9 / 2. Treating the missing rating as zero gives 3.0, which is a different claim ("the average rating, counting non-raters as zero"). Neither is wrong; they answer different questions. Decide which one your report means, and write it explicitly.

On Bazaar, `orders.paid_at` is `NULL` for orders that were never paid. "Average time from order to payment" with `avg(paid_at - created_at)` therefore averages over paid orders only, which is the useful definition:

```sql
SELECT count(*)                    AS orders,
       count(paid_at)              AS paid_ever,
       avg(paid_at - created_at)   AS avg_time_to_pay,
       max(paid_at - created_at)   AS slowest
FROM orders;
```

```text
 orders | paid_ever | avg_time_to_pay | slowest  
--------+-----------+-----------------+----------
    120 |       105 | 01:21:15.428571 | 02:59:00
(1 row)
```

(`avg` and `max` work on `interval`s too.) 105 orders have a payment time: everything except the 5 pending and the 10 cancelled orders, which were never paid.

### 1.3 Empty input

What does an aggregate return when no rows match? Maple & Pine (seller 5) has never received an order:

```sql
SELECT count(*) AS orders, sum(total) AS revenue, max(created_at) AS last_order
FROM orders
WHERE seller_id = 5;
```

```text
 orders | revenue | last_order 
--------+---------+------------
      0 |       ∅ | ∅
(1 row)
```

Still exactly one row. `count` returns 0, but `sum`, `max`, `avg` and the rest return `NULL`: "the sum of nothing" is undefined, not zero, in SQL. That bites in reports and in application code (a Python `None` where you expected `Decimal('0')`). When a missing sum means zero, say so:

```sql
SELECT coalesce(sum(total), 0) AS revenue
FROM orders
WHERE seller_id = 5;
```

```text
 revenue 
---------
       0
(1 row)
```

> **Pitfall:** An aggregate query *without* `GROUP BY` always returns exactly one row, even for an empty input. An aggregate query *with* `GROUP BY` returns one row per group, and an empty input has no groups, so it returns **zero rows**. Code that does `cursor.fetchone()[0]` handles the first case and crashes on the second.

## 2. `GROUP BY`

`GROUP BY` splits the rows into groups that share the same values of the listed expressions, and then computes the aggregates once per group. The result has one row per group.

```sql
SELECT status, count(*) AS orders, sum(total) AS sum_of_totals
FROM orders
GROUP BY status
ORDER BY orders DESC;
```

```text
  status   | orders | sum_of_totals 
-----------+--------+---------------
 delivered |     91 |      42136.29
 cancelled |     10 |       4538.90
 paid      |      6 |       4864.97
 pending   |      5 |        947.95
 shipped   |      4 |       2093.93
 refunded  |      4 |        759.94
(6 rows)
```

Conceptually:

```text
  orders (120 rows)                 grouped by status                one row per group
  id  status     total              ┌ delivered: 91 rows ┐            delivered  91  42136.29
   1  delivered  50.48     ──▶      ├ cancelled: 10 rows ┤    ──▶     cancelled  10   4538.90
   2  delivered  2198.00            ├ paid:       6 rows ┤            paid        6   4864.97
  ...                               ├ pending:    5 rows ┤            pending     5    947.95
 120  paid       90.00              ├ shipped:    4 rows ┤            shipped     4   2093.93
                                    └ refunded:   4 rows ┘            refunded    4    759.94
```

The numbers check out against NOTES.md: 91 delivered, 10 cancelled, and so on. `GROUP BY` doesn't sort the result. Groups come out in whatever order the algorithm produces (section 12), so add `ORDER BY` whenever order matters.

You can group by several columns (one group per distinct *combination*), and by expressions:

```sql
SELECT seller_id,
       extract(year FROM created_at) AS year,
       count(*)                      AS orders
FROM orders
GROUP BY seller_id, extract(year FROM created_at)
ORDER BY seller_id, year;
```

```text
 seller_id | year | orders 
-----------+------+--------
         1 | 2024 |     17
         1 | 2025 |     21
         2 | 2024 |     24
         2 | 2025 |     12
         3 | 2024 |     13
         3 | 2025 |     12
         4 | 2024 |     12
         4 | 2025 |      9
(8 rows)
```

`NULL`s in a grouping column form **one** group together, even though `NULL = NULL` isn't true. For grouping (as for `DISTINCT` and set operations), `NULL`s count as "not distinct" from each other.

### 2.1 What can appear in the `SELECT` list

After grouping, each output row stands for a whole group. So every expression in `SELECT` (and in `HAVING` and `ORDER BY`) must have a single value per group. That means it must be one of:

- a grouping expression (or built from grouping expressions),
- an aggregate,
- a constant.

Anything else is ambiguous, and PostgreSQL says so:

```sql
SELECT seller_id, status, count(*)
FROM orders
GROUP BY seller_id;
```

```text
ERROR:  column "orders.status" must appear in the GROUP BY clause or be used in an aggregate function
LINE 1: SELECT seller_id, status, count(*)
                          ^
```

Within seller 1's group there are orders with six different statuses. Which one should the row show? There's no answer, so it's an error. (MySQL used to pick an arbitrary one silently. PostgreSQL never does.) Fix it by grouping by `status` too, or by aggregating it, for instance `string_agg(DISTINCT status, ', ')` (section 5).

### 2.2 Functional dependency: grouping by the primary key

There's one important exception. Join sellers to orders and group by the seller's id:

```sql
SELECT s.id, s.shop_name, s.country_code, count(o.id) AS orders
FROM sellers AS s
LEFT JOIN orders AS o ON o.seller_id = s.id
GROUP BY s.id
ORDER BY s.id;
```

```text
 id |    shop_name    | country_code | orders 
----+-----------------+--------------+--------
  1 | TechTrove       | US           |     38
  2 | Bookworm Corner | GB           |     36
  3 | Casa Cucina     | IT           |     25
  4 | Threadbare Co.  | DE           |     21
  5 | Maple & Pine    | CA           |      0
  6 | Sakura Gadgets  | JP           |      0
(6 rows)
```

`shop_name` and `country_code` aren't in `GROUP BY` and aren't aggregated, yet this works. Because `s.id` is the **primary key** of `sellers`, each group contains rows of exactly one seller, so every other column of `sellers` is guaranteed to have a single value per group. PostgreSQL knows this: when you group by a table's primary key, you may select any column **of that table**. The formal name is *functional dependency*: `shop_name` is determined by `id`.

The rule is precise:

- It applies to the primary key (all columns of it), not to a `UNIQUE` constraint, and not to columns that happen to be unique in today's data.
- It covers only columns of the table whose key you grouped by. In the query above, a column of `orders` would still need aggregating.

Without this rule you'd write `GROUP BY s.id, s.shop_name, s.country_code`, which works everywhere and costs nothing in clarity for three columns, but becomes noisy with ten. Both styles are common.

### 2.3 Counting through an outer join: `count(*)` versus `count(col)`

Look again at the output above: Maple & Pine and Sakura Gadgets show **0** orders. That's because the query counted `count(o.id)`. With `count(*)` they'd show 1:

```sql
SELECT s.id, s.shop_name,
       count(*)    AS count_star,
       count(o.id) AS count_orders
FROM sellers AS s
LEFT JOIN orders AS o ON o.seller_id = s.id
GROUP BY s.id
ORDER BY s.id;
```

```text
 id |    shop_name    | count_star | count_orders 
----+-----------------+------------+--------------
  1 | TechTrove       |         38 |           38
  2 | Bookworm Corner |         36 |           36
  3 | Casa Cucina     |         25 |           25
  4 | Threadbare Co.  |         21 |           21
  5 | Maple & Pine    |          1 |            0
  6 | Sakura Gadgets  |          1 |            0
(6 rows)
```

The `LEFT JOIN` produces one `NULL`-padded row for each seller with no orders (module 04, section 5). `count(*)` counts that row. `count(o.id)` counts only non-`NULL` order ids, so the padded row contributes 0. When counting the "many" side of an outer join, always count a non-nullable column of that side, usually its primary key.

## 3. `HAVING` versus `WHERE`

Both filter, at different moments:

- `WHERE` filters **rows**, before grouping. It can't use aggregates, because no groups exist yet.
- `HAVING` filters **groups**, after aggregation. It can use aggregates.

"Customers who placed at least 10 non-cancelled orders":

```sql
SELECT user_id, count(*) AS orders, sum(total) AS spent
FROM orders
WHERE status <> 'cancelled'           -- row filter: drop cancelled orders before counting
GROUP BY user_id
HAVING count(*) >= 10                 -- group filter: keep customers with 10+
ORDER BY orders DESC;
```

```text
 user_id | orders |  spent   
---------+--------+----------
       8 |     19 |  4890.85
      13 |     18 | 14588.95
      11 |     13 | 10875.36
(3 rows)
```

Try to put the aggregate condition in `WHERE`, and PostgreSQL stops you:

```sql
SELECT user_id, count(*)
FROM orders
WHERE count(*) >= 10
GROUP BY user_id;
```

```text
ERROR:  aggregate functions are not allowed in WHERE
LINE 3: WHERE count(*) >= 10
              ^
```

The reverse mistake isn't an error, just waste: a condition on a plain column, such as `HAVING user_id > 5`, works in `HAVING`, but filters after all the grouping work. In `WHERE`, rows are dropped before they're grouped. Put each condition at the earliest point where it can be evaluated: row conditions in `WHERE`, aggregate conditions in `HAVING`. (PostgreSQL's planner does push simple `HAVING` conditions on grouping columns down into the scan, but don't rely on it for readability.)

### 3.1 The logical order of a `SELECT`, complete

Module 04 gave the order of evaluation with a placeholder for grouping. Here it is in full:

```text
 1. FROM / JOIN      build the combined rows
 2. WHERE            keep rows where the condition is TRUE       (no aggregates)
 3. GROUP BY         form groups
 4. aggregates       compute aggregates per group
 5. HAVING           keep groups where the condition is TRUE     (aggregates allowed)
 6. SELECT           compute output expressions, assign aliases  (window functions: module 06)
 7. DISTINCT         remove duplicate output rows
 8. ORDER BY         sort                                        (output aliases allowed)
 9. LIMIT / OFFSET   cut
```

The order explains which names are visible where. An output alias such as `AS spent` is created in step 6, so `ORDER BY spent` (step 8) works, but `HAVING spent > 1000` (step 5) doesn't:

```sql
SELECT user_id, sum(total) AS spent
FROM orders
GROUP BY user_id
HAVING spent > 1000;
```

```text
ERROR:  column "spent" does not exist
LINE 4: HAVING spent > 1000;
               ^
```

Repeat the expression instead: `HAVING sum(total) > 1000`. PostgreSQL computes it once even though it's written twice. (`GROUP BY` is the one early clause where PostgreSQL *does* accept an output alias or position, `GROUP BY 1` or `GROUP BY year`, as a convenience. It's a PostgreSQL extension of the standard. Positions are handy at the psql prompt and brittle in saved code, because they break when someone reorders the select list.)

## 4. `FILTER` and conditional aggregation: pivot-style reports

Grouping by status gives one *row* per status. Business reports often want one *column* per status instead, with sellers as rows: a **pivot**. The tool is the aggregate `FILTER` clause: `agg(...) FILTER (WHERE condition)` feeds only the rows that satisfy the condition into that one aggregate.

```sql
SELECT s.shop_name,
       count(o.id)                                          AS orders,
       count(o.id) FILTER (WHERE o.status = 'delivered')    AS delivered,
       count(o.id) FILTER (WHERE o.status IN ('paid', 'shipped')) AS in_progress,
       count(o.id) FILTER (WHERE o.status = 'pending')      AS pending,
       count(o.id) FILTER (WHERE o.status = 'cancelled')    AS cancelled,
       count(o.id) FILTER (WHERE o.status = 'refunded')     AS refunded
FROM sellers AS s
LEFT JOIN orders AS o ON o.seller_id = s.id
GROUP BY s.id
ORDER BY s.id;
```

```text
    shop_name    | orders | delivered | in_progress | pending | cancelled | refunded 
-----------------+--------+-----------+-------------+---------+-----------+----------
 TechTrove       |     38 |        27 |           4 |       2 |         3 |        2
 Bookworm Corner |     36 |        27 |           2 |       1 |         5 |        1
 Casa Cucina     |     25 |        21 |           2 |       0 |         1 |        1
 Threadbare Co.  |     21 |        16 |           2 |       2 |         1 |        0
 Maple & Pine    |      0 |         0 |           0 |       0 |         0 |        0
 Sakura Gadgets  |      0 |         0 |           0 |       0 |         0 |        0
(6 rows)
```

Each `FILTER` sees only its own subset of each group's rows. The same group is scanned once, and seven aggregates are computed from it in a single pass. That's much cheaper than seven separate queries, and it guarantees the columns are consistent with each other (the row totals add up).

`FILTER` is standard SQL, but not every database supports it. The portable spelling uses `CASE` inside the aggregate, relying on the fact that aggregates ignore `NULL`s:

```sql
SELECT s.shop_name,
       count(CASE WHEN o.status = 'delivered' THEN 1 END)                    AS delivered,
       sum(CASE WHEN o.status IN ('paid', 'shipped', 'delivered') THEN o.total END) AS revenue,
       sum(CASE WHEN o.status IN ('paid', 'shipped', 'delivered') THEN o.total ELSE 0 END) AS revenue_zero
FROM sellers AS s
LEFT JOIN orders AS o ON o.seller_id = s.id
GROUP BY s.id
ORDER BY s.id;
```

```text
    shop_name    | delivered | revenue  | revenue_zero 
-----------------+-----------+----------+--------------
 TechTrove       |        27 | 41598.82 |     41598.82
 Bookworm Corner |        27 |  1656.43 |      1656.43
 Casa Cucina     |        21 |  3562.94 |      3562.94
 Threadbare Co.  |        16 |  2277.00 |      2277.00
 Maple & Pine    |         0 |        ∅ |            0
 Sakura Gadgets  |         0 |        ∅ |            0
(6 rows)
```

A `CASE` without `ELSE` yields `NULL` for non-matching rows, which `count` and `sum` skip. With `ELSE 0`, `sum` adds zeros instead, which is how you get 0.00 rather than `NULL` for sellers without revenue (Maple & Pine and Sakura Gadgets). With `FILTER` you'd write `coalesce(sum(o.total) FILTER (WHERE ...), 0)` for the same effect. The revenue column reproduces NOTES.md: TechTrove 41,598.82, Bookworm Corner 1,656.43, Casa Cucina 3,562.94, Threadbare Co. 2,277.00.

Use `FILTER` in PostgreSQL: it's shorter, it states the intent, and it's slightly faster. Recognize the `CASE` form, because you'll meet it in code written for other databases.

### 4.1 Ratios and shares

Pivot columns often become ratios. "What share of each seller's gross order value was actually earned (delivered), is still in flight, or was lost to cancellations and refunds?"

```sql
SELECT s.shop_name,
       sum(o.total) AS gross,
       round(100 * sum(o.total) FILTER (WHERE o.status = 'delivered') / sum(o.total), 1)
           AS pct_delivered,
       round(100 * sum(o.total) FILTER (WHERE o.status IN ('pending', 'paid', 'shipped')) / sum(o.total), 1)
           AS pct_in_flight,
       round(100 * sum(o.total) FILTER (WHERE o.status IN ('cancelled', 'refunded')) / sum(o.total), 1)
           AS pct_lost
FROM orders AS o
JOIN sellers AS s ON s.id = o.seller_id
GROUP BY s.id
ORDER BY gross DESC;
```

```text
    shop_name    |  gross   | pct_delivered | pct_in_flight | pct_lost 
-----------------+----------+---------------+---------------+----------
 TechTrove       | 46916.74 |          74.5 |          16.0 |      9.5
 Casa Cucina     |  3856.93 |          89.1 |           3.2 |      7.6
 Threadbare Co.  |  2496.00 |          87.4 |           6.3 |      6.3
 Bookworm Corner |  2072.31 |          76.0 |           5.4 |     18.6
(4 rows)
```

Two habits that prevent wrong ratios:

- **Do the arithmetic in `numeric`.** `100 * sum(...) / sum(...)` on `numeric` columns is exact. If both sides were `integer` (say, `count(*) FILTER (...) / count(*)`), the division would be integer division and give 0. Cast one side (`count(*)::numeric`) or multiply by `100.0` first.
- **Guard against division by zero.** A group whose denominator is 0 raises `division by zero`. `x / NULLIF(y, 0)` turns that into `NULL`, which is usually the honest answer ("undefined"). Here every group has at least one order, but a report over a filtered period might not.

## 5. Collecting values: `array_agg`, `string_agg`, `jsonb_agg`

Not every aggregate produces a number. Some collect the group's values into one structured value.

- `array_agg(expr)` builds an array (module 08 covers arrays in depth).
- `string_agg(text_expr, separator)` concatenates text.
- `jsonb_agg(expr)` builds a JSON array, and `jsonb_object_agg(key, value)` a JSON object (module 08 covers JSONB).

All of them accept an `ORDER BY` **inside** the parentheses, which controls the order of the collected elements. Without it the order is whatever order the rows happened to arrive in, and that can change from one run to the next.

```sql
SELECT o.id AS order_id,
       count(*)                                                AS lines,
       array_agg(i.product_id ORDER BY i.product_id)           AS product_ids,
       string_agg(p.title, ' | ' ORDER BY p.title)             AS titles
FROM orders AS o
JOIN order_items AS i ON i.order_id = o.id
JOIN products    AS p ON p.id = i.product_id
WHERE o.id IN (40, 91, 102)
GROUP BY o.id
ORDER BY o.id;
```

```text
 order_id | lines | product_ids |                                                      titles                                                      
----------+-------+-------------+------------------------------------------------------------------------------------------------------------------
       40 |     4 | {1,2,7,10}  | AeroBook 13 Laptop | AeroBook 15 Pro Laptop | ClearView 27" 4K Monitor | QuietWave ANC Headphones
       91 |     3 | {42,43,46}  | Bread Knife 23cm | Espresso Moka Pot 6-cup | Paring Knife Set (3 pcs)
      102 |     4 | {3,4,9,16}  | Laptop Sleeve 13" | PortaScreen 15.6" USB-C Portable Monitor | StudyMate 11 Chromebook | Titan G17 Gaming Laptop
(3 rows)
```

Note the syntax: the `ORDER BY` goes after the *last* argument, with no comma: `string_agg(p.title, ' | ' ORDER BY p.title)`. `DISTINCT` works inside these too, to collect each value once. "Which statuses has each seller ever had?":

```sql
SELECT seller_id,
       string_agg(DISTINCT status, ', ' ORDER BY status) AS statuses_seen
FROM orders
GROUP BY seller_id
ORDER BY seller_id;
```

```text
 seller_id |                     statuses_seen                      
-----------+--------------------------------------------------------
         1 | cancelled, delivered, paid, pending, refunded, shipped
         2 | cancelled, delivered, pending, refunded, shipped
         3 | cancelled, delivered, paid, refunded, shipped
         4 | cancelled, delivered, paid, pending
(4 rows)
```

(With `DISTINCT`, the `ORDER BY` inside the aggregate must use the same expression as the argument.)

`jsonb_agg` is the usual way to return a parent row with its children nested, for an API, in one query:

```sql
SELECT o.id,
       o.total,
       jsonb_agg(jsonb_build_object('product_id', i.product_id, 'qty', i.quantity)
                 ORDER BY i.product_id) AS items
FROM orders AS o
JOIN order_items AS i ON i.order_id = o.id
WHERE o.id = 1
GROUP BY o.id;
```

```text
 id | total |                                           items                                            
----+-------+--------------------------------------------------------------------------------------------
  1 | 50.48 | [{"qty": 1, "product_id": 20}, {"qty": 1, "product_id": 21}, {"qty": 2, "product_id": 22}]
(1 row)
```

> **In production:** These aggregates build their whole result in memory, per group. `string_agg` over a million rows produces one enormous string. They're great for "a handful of children per parent" and dangerous as a way to dump a table.

## 6. `bool_and` and `bool_or`

Two aggregates answer "for all" and "for some" questions over a group directly:

- `bool_and(condition)`: true if the condition is true for **every** row of the group (`NULL`s ignored). The SQL standard name is `every()`, which PostgreSQL also accepts.
- `bool_or(condition)`: true if the condition is true for **at least one** row.

"For each customer with at least five orders: were all of their orders delivered? Did they ever cancel?"

```sql
SELECT user_id,
       count(*)                          AS orders,
       bool_and(status = 'delivered')    AS all_delivered,
       bool_or(status = 'cancelled')     AS ever_cancelled
FROM orders
GROUP BY user_id
HAVING count(*) >= 5
ORDER BY user_id;
```

```text
 user_id | orders | all_delivered | ever_cancelled 
---------+--------+---------------+----------------
       8 |     19 | f             | f
      11 |     16 | f             | t
      12 |      6 | t             | f
      13 |     19 | f             | t
      14 |      7 | f             | t
      17 |      5 | f             | t
      20 |      7 | f             | f
      25 |      5 | f             | f
      26 |      5 | f             | f
(9 rows)
```

They read better than the counting equivalents (`count(*) FILTER (WHERE status <> 'delivered') = 0`), and they compose nicely with `HAVING`: `HAVING bool_and(status = 'delivered')` keeps the groups where everything was delivered. Like the "for all" queries in module 04, `bool_and` over a group whose conditions are all `NULL` returns `NULL`, not true.

## 7. Ordered-set aggregates: medians, percentiles and the mode

The average is easily dragged around by outliers. TechTrove's average order is inflated by order 40 (8,940.00), so for "a typical order" you usually want the **median**: the value in the middle when the orders are sorted. Computing it requires sorting the group, so it belongs to a special family, the **ordered-set aggregates**, with their own syntax: `func(fraction) WITHIN GROUP (ORDER BY expr)`.

```sql
SELECT seller_id,
       count(*)                                                AS orders,
       round(avg(total), 2)                                    AS mean,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY total)      AS median_cont,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY total)      AS median_disc,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY total)      AS p90
FROM orders
WHERE status IN ('paid', 'shipped', 'delivered')
GROUP BY seller_id
ORDER BY seller_id;
```

```text
 seller_id | orders |  mean   | median_cont | median_disc |        p90         
-----------+--------+---------+-------------+-------------+--------------------
         1 |     31 | 1341.90 |         399 |      399.00 |               2998
         2 |     29 |   57.12 |       41.95 |       41.95 |             108.58
         3 |     23 |  154.91 |       88.99 |       88.99 |              294.2
         4 |     18 |  126.50 |        79.5 |       78.00 | 306.70000000000005
(4 rows)
```

For TechTrove the mean is several times the median: a handful of laptop orders pull the average up, while the typical order is much smaller. A dashboard showing only the mean would mislead.

- `percentile_cont(f)` (*continuous*) **interpolates**: if the requested position falls between two values, it returns a value between them. The median of 10, 20, 30, 40 is 25. The result is `double precision` (even for `numeric` input), so round it for display.
- `percentile_disc(f)` (*discrete*) returns an **actual value** from the group: the first one whose position reaches the fraction. The median of 10, 20, 30, 40 is 20. The result has the input's type. Use it when the answer must be a real data point (a real order total, a real date).

Both accept an array of fractions to compute several percentiles in one sort: `percentile_cont(ARRAY[0.25, 0.5, 0.75]) WITHIN GROUP (ORDER BY total)` returns an array.

Look at the last column: Threadbare Co.'s p90 is `306.70000000000005`. That's `double precision` arithmetic showing through, the floating-point rounding module 02 warned about for money. Round it for display, `round(percentile_cont(0.9) WITHIN GROUP (ORDER BY total)::numeric, 2)`, or use `percentile_disc` when an actual order total is what you want.

`mode()` returns the most frequent value. "Each seller's most frequently ordered product (by number of order lines)":

```sql
SELECT p.seller_id,
       mode() WITHIN GROUP (ORDER BY i.product_id) AS most_ordered_product,
       count(*)                                    AS lines
FROM order_items AS i
JOIN products AS p ON p.id = i.product_id
GROUP BY p.seller_id
ORDER BY p.seller_id;
```

```text
 seller_id | most_ordered_product | lines 
-----------+----------------------+-------
         1 |                    4 |    67
         2 |                   19 |    76
         3 |                   36 |    47
         4 |                   48 |    45
(4 rows)
```

TechTrove's most-ordered product is the StudyMate 11 Chromebook (4), which appears in 8 order lines. If several values are equally frequent, `mode()` picks one of them (in practice the first in sort order, but the documentation only promises "arbitrarily"), so don't build logic on which one wins a tie. Note also that `mode()` counts *rows*: the Chromebook is the most frequent line, not necessarily the most units or the most revenue. For those, you group and sort, as in the exercises.

## 8. `DISTINCT ON`: the first row of each group

A very common question isn't really an aggregation: "the **latest order** of each customer" asks for a whole *row* per group (id, date, status, total), picked by an ordering, not a computed value. `max(created_at)` gives the date, but not the rest of the row.

PostgreSQL has a compact, non-standard tool for this: `SELECT DISTINCT ON (expressions) ...`. It sorts the rows by `ORDER BY`, and then keeps only the **first** row for each distinct value of the `DISTINCT ON` expressions:

```sql
SELECT DISTINCT ON (o.user_id)
       o.user_id, o.id AS order_id, o.created_at::date AS ordered, o.status, o.total
FROM orders AS o
ORDER BY o.user_id, o.created_at DESC, o.id DESC
LIMIT 8;
```

```text
 user_id | order_id |  ordered   |  status   | total  
---------+----------+------------+-----------+--------
       1 |       91 | 2025-04-27 | delivered | 283.00
       2 |       18 | 2024-04-04 | refunded  | 232.99
       3 |      119 | 2025-06-29 | pending   |  38.00
       4 |       94 | 2025-05-15 | delivered |  34.99
       5 |      108 | 2025-06-22 | delivered | 108.00
       8 |      110 | 2025-06-25 | paid      |  24.00
       9 |        6 | 2024-02-01 | delivered | 251.00
      10 |      116 | 2025-06-28 | pending   | 756.00
(8 rows)
```

(The `LIMIT` just keeps the output short. The full result has one row for each of the 21 users who ever ordered.) The rules:

- The `ORDER BY` must **start with** the `DISTINCT ON` expressions, in the same order. They define the groups. The rest of the `ORDER BY` decides which row of each group comes first. Here, `created_at DESC` puts the latest order first.
- Add a unique tie-breaker (`o.id DESC`) at the end. Without it, two orders at the same timestamp would make the choice arbitrary.
- If you want the final output in a different order (say, latest order first across all users), wrap the query in a derived table and sort outside.

### 8.1 Three ways to get "the latest row per group"

The same question can be answered with tools from module 04 and this module. Each has its place.

**1. `GROUP BY` + join back.** Find the latest date per user, then join back to `orders` to fetch the rest of the row:

```sql
SELECT o.user_id, o.id AS order_id, o.created_at::date AS ordered, o.status
FROM orders AS o
JOIN (
    SELECT user_id, max(created_at) AS last_at
    FROM orders
    GROUP BY user_id
) AS latest ON latest.user_id = o.user_id AND latest.last_at = o.created_at
ORDER BY o.user_id
LIMIT 4;
```

```text
 user_id | order_id |  ordered   |  status   
---------+----------+------------+-----------
       1 |       91 | 2025-04-27 | delivered
       2 |       18 | 2024-04-04 | refunded
       3 |      119 | 2025-06-29 | pending
       4 |       94 | 2025-05-15 | delivered
(4 rows)
```

Standard SQL, works everywhere. But it reads `orders` twice, and if two orders of the same user shared the latest timestamp, *both* would come back: the join can't break ties.

**2. `DISTINCT ON`**, as above: one pass, tie-breaking by the `ORDER BY`, PostgreSQL-only, and only for "top 1".

**3. `LATERAL` with `ORDER BY ... LIMIT 1`** (module 04, section 12): starts from `users`, so it naturally includes users with no orders (with `LEFT JOIN LATERAL`), generalizes to top N, and with an index on `orders (user_id, created_at)` it's the fastest when there are few parents and many children per parent.

Module 06 adds a fourth: the window function `row_number()`, standard SQL, top N, any database. As a rule of thumb: `DISTINCT ON` for a quick "latest per group" over a whole table, `LATERAL` when you start from the parent table or need the top N, and window functions when you need ranks alongside other columns.

## 9. Subtotals: `GROUPING SETS`, `ROLLUP` and `CUBE`

Finance wants revenue by seller **and** year, with a subtotal for each seller and a grand total. With plain `GROUP BY` that's three queries (by seller and year, by seller, overall) glued with `UNION ALL`. `GROUP BY` can do it in one pass.

### 9.1 `GROUPING SETS`

`GROUP BY GROUPING SETS ((a, b), (a), ())` computes the aggregates once for each listed **grouping set**, and stacks the results. A column that isn't part of the current grouping set comes out as `NULL` in those rows:

```sql
SELECT seller_id,
       extract(year FROM created_at) AS year,
       sum(total) AS revenue
FROM orders
WHERE status IN ('paid', 'shipped', 'delivered')
GROUP BY GROUPING SETS ((seller_id, extract(year FROM created_at)), (seller_id), ())
ORDER BY seller_id, year;
```

```text
 seller_id | year | revenue  
-----------+------+----------
         1 | 2024 | 25199.92
         1 | 2025 | 16398.90
         1 |    ∅ | 41598.82
         2 | 2024 |  1128.17
         2 | 2025 |   528.26
         2 |    ∅ |  1656.43
         3 | 2024 |  2252.96
         3 | 2025 |  1309.98
         3 |    ∅ |  3562.94
         4 | 2024 |  1191.00
         4 | 2025 |  1086.00
         4 |    ∅ |  2277.00
         ∅ |    ∅ | 49095.19
(13 rows)
```

Rows with a year are the detail rows. Rows with `∅` as the year are seller subtotals, and the row with `∅` in both columns is the grand total, 49,095.19. That's the sum of the four seller revenues in NOTES.md.

### 9.2 `ROLLUP` and `CUBE`

Two shorthands generate common lists of grouping sets:

- `ROLLUP (a, b, c)` means the **hierarchy** `(a, b, c), (a, b), (a), ()`: drop columns from the right, one at a time. Perfect for "detail, subtotals, grand total" along a natural hierarchy (country → seller → month, year → month → day).
- `CUBE (a, b)` means **every combination**: `(a, b), (a), (b), ()`. With *n* columns that's 2ⁿ grouping sets, so keep *n* small. Useful for cross-tab reports where both dimensions get totals.

The query above is exactly `GROUP BY ROLLUP (seller_id, extract(year FROM created_at))`. Here's `CUBE` over the year and the status, giving totals per year, per status and overall:

```sql
SELECT extract(year FROM created_at) AS year,
       status,
       count(*) AS orders
FROM orders
GROUP BY CUBE (extract(year FROM created_at), status)
ORDER BY year, status;
```

```text
 year |  status   | orders 
------+-----------+--------
 2024 | cancelled |      5
 2024 | delivered |     59
 2024 | refunded  |      2
 2024 | ∅         |     66
 2025 | cancelled |      5
 2025 | delivered |     32
 2025 | paid      |      6
 2025 | pending   |      5
 2025 | refunded  |      2
 2025 | shipped   |      4
 2025 | ∅         |     54
    ∅ | cancelled |     10
    ∅ | delivered |     91
    ∅ | paid      |      6
    ∅ | pending   |      5
    ∅ | refunded  |      4
    ∅ | shipped   |      4
    ∅ | ∅         |    120
(18 rows)
```

You can mix plain grouping columns with these: `GROUP BY seller_id, ROLLUP (year, month)` gives per-seller detail and per-seller subtotals, but no cross-seller total.

### 9.3 `GROUPING()`: telling subtotal `NULL`s from data `NULL`s

There's an ambiguity in those outputs: does `∅` mean "this is a subtotal row" or "this column really is `NULL` in the data"? In Bazaar's `orders` none of the grouped columns is nullable, but grouping by, say, `products.description` would produce both kinds. The `GROUPING(col, ...)` function resolves it: it returns 0 if the column is part of the current grouping set, and 1 if it was rolled up (with several arguments, it returns a bit mask). Use it to label rows:

```sql
SELECT CASE WHEN GROUPING(s.shop_name) = 1 THEN 'ALL SHOPS' ELSE s.shop_name END AS shop,
       CASE WHEN GROUPING(o.status)    = 1 THEN 'total'     ELSE o.status    END AS status,
       count(*)     AS orders,
       sum(o.total) AS value
FROM orders AS o
JOIN sellers AS s ON s.id = o.seller_id
WHERE o.created_at >= '2025-06-01'
GROUP BY ROLLUP (s.shop_name, o.status)
ORDER BY GROUPING(s.shop_name), s.shop_name, GROUPING(o.status), o.status;
```

```text
      shop       |  status   | orders |  value   
-----------------+-----------+--------+----------
 Bookworm Corner | delivered |      1 |    69.46
 Bookworm Corner | pending   |      1 |    29.98
 Bookworm Corner | shipped   |      2 |    81.93
 Bookworm Corner | total     |      4 |   181.37
 Casa Cucina     | delivered |      1 |   108.00
 Casa Cucina     | paid      |      1 |    90.00
 Casa Cucina     | shipped   |      1 |    35.00
 Casa Cucina     | total     |      3 |   233.00
 TechTrove       | delivered |      4 |  2836.96
 TechTrove       | paid      |      3 |  4678.97
 TechTrove       | pending   |      2 |   855.97
 TechTrove       | shipped   |      1 |  1977.00
 TechTrove       | total     |     10 | 10348.90
 Threadbare Co.  | delivered |      1 |   132.00
 Threadbare Co.  | paid      |      2 |    96.00
 Threadbare Co.  | pending   |      2 |    62.00
 Threadbare Co.  | total     |      5 |   290.00
 ALL SHOPS       | total     |     22 | 11053.27
(18 rows)
```

Sorting by `GROUPING(...)` puts each subtotal after its detail rows and the grand total last, regardless of how the labels sort alphabetically. This shape is exactly what this module's milestone, the monthly seller statement, asks for.

> **Under the hood:** PostgreSQL computes grouping sets in as few passes as it can. All the sets of a `ROLLUP` can be produced from one sorted pass (a `GroupAggregate` with several "group keys"), and unrelated sets can be hashed in parallel (`MixedAggregate`). Either way it's cheaper than the equivalent `UNION ALL` of separate queries, which would read the table once per set.

## 10. Time series: bucketing by date and filling the gaps

Most business reports are time series: revenue per month, signups per week, orders per day. Two problems come up every time: putting timestamps into buckets, and making the buckets with no data show up as zeros instead of disappearing.

### 10.1 Bucketing with `date_trunc`

`date_trunc(unit, timestamp)` rounds a timestamp **down** to the start of its unit: `'month'` maps 2025-06-18 14:03 to 2025-06-01 00:00. Every timestamp in the same month maps to the same value, which makes it a perfect grouping key:

```sql
SELECT date_trunc('month', created_at) AS month,
       count(*)                        AS orders,
       sum(total) FILTER (WHERE status IN ('paid', 'shipped', 'delivered')) AS revenue
FROM orders
WHERE created_at >= '2025-01-01'
GROUP BY date_trunc('month', created_at)
ORDER BY month;
```

```text
         month          | orders | revenue  
------------------------+--------+----------
 2025-01-01 00:00:00+00 |      4 |  1015.95
 2025-02-01 00:00:00+00 |      7 |   650.94
 2025-03-01 00:00:00+00 |      8 |   798.99
 2025-04-01 00:00:00+00 |      7 |  2820.98
 2025-05-01 00:00:00+00 |      6 |  3930.96
 2025-06-01 00:00:00+00 |     22 | 10105.32
(6 rows)
```

Units go from `microseconds` up to `millennium`. The useful ones are `hour`, `day`, `week` (ISO weeks, starting on Monday), `month`, `quarter` and `year`. `date_trunc` keeps the input type, so on a `timestamptz` it returns a `timestamptz`. Cast to `date` for display (`date_trunc(...)::date`), or format with `to_char(month, 'YYYY-MM')`.

`extract(field FROM ts)` (module 03) is the other tool. It returns a *number*, such as the month 6, which loses the year: grouping by `extract(month ...)` puts June 2024 and June 2025 into the same bucket. That's right for seasonality ("are Junes better than Januaries?"), and wrong for a timeline. For a timeline, use `date_trunc`.

> **Pitfall:** For `timestamptz`, "the start of the month" depends on a time zone. `date_trunc` on a `timestamptz` uses the session's `TimeZone` setting, which is UTC in the course container, but a client in another zone (or a server configured differently) gets different buckets from the same query.

Five Bazaar orders were placed close enough to a month boundary to land in a different month in Los Angeles or Tokyo. Four of them:

```sql
SELECT id, created_at,
       date_trunc('month', created_at AT TIME ZONE 'UTC')::date                 AS month_utc,
       date_trunc('month', created_at AT TIME ZONE 'America/Los_Angeles')::date AS month_la,
       date_trunc('month', created_at AT TIME ZONE 'Asia/Tokyo')::date          AS month_tokyo
FROM orders
WHERE id IN (17, 48, 71, 93);
```

```text
 id |       created_at       | month_utc  |  month_la  | month_tokyo 
----+------------------------+------------+------------+-------------
 17 | 2024-03-31 18:53:21+00 | 2024-03-01 | 2024-03-01 | 2024-04-01
 48 | 2024-10-01 04:14:04+00 | 2024-10-01 | 2024-09-01 | 2024-10-01
 71 | 2025-02-01 04:48:13+00 | 2025-02-01 | 2025-01-01 | 2025-02-01
 93 | 2025-05-01 03:52:53+00 | 2025-05-01 | 2025-04-01 | 2025-05-01
(4 rows)
```

Order 71 was placed on 1 February in UTC, but on the evening of 31 January in Los Angeles. Order 17 goes the other way: 31 March in UTC and Los Angeles, already 1 April in Tokyo.

`ts AT TIME ZONE 'zone'` converts a `timestamptz` into the local wall-clock time (a plain `timestamp`) in that zone, and `date_trunc` then truncates the wall-clock time, so the month is the local month. PostgreSQL 12+ also has a three-argument form, `date_trunc('month', ts, 'Asia/Tokyo')`, which returns a `timestamptz`: *the instant* the Tokyo month began, 15:00 UTC on the last day of the previous month. Casting that to `date` in a UTC session prints the previous day, a classic source of off-by-one bugs, so for labels use the `AT TIME ZONE` form. Either way, decide which zone defines your business month (often the zone of the company's accounting) and write it into the query. NOTES.md fixes Bazaar's reporting zone as UTC, and module 09's `seller_sales_daily` uses UTC days too. A report built from the session's zone gives different numbers to different users, and nobody can tell why.

### 10.2 Missing buckets vanish

Here's Threadbare Co.'s monthly revenue for the first half of 2025:

```sql
SELECT date_trunc('month', created_at)::date AS month,
       count(*)   AS orders,
       sum(total) AS revenue
FROM orders
WHERE seller_id = 4
  AND status IN ('paid', 'shipped', 'delivered')
  AND created_at >= '2025-01-01' AND created_at < '2025-07-01'
GROUP BY 1
ORDER BY 1;
```

```text
   month    | orders | revenue 
------------+--------+---------
 2025-03-01 |      1 |  376.00
 2025-04-01 |      2 |  482.00
 2025-06-01 |      3 |  228.00
(3 rows)
```

Three rows for six months. January, February and May had no sales, so they have no rows, so they have no groups. A chart drawn from this result would connect March to April to June with straight lines and silently hide three empty months. A dashboard computing "average monthly revenue" would divide by 3 instead of 6. GROUP BY can only create groups for values that exist in the data. To get a row for each month, you need a table that *contains* every month, and then you outer join the data to it.

### 10.3 `generate_series`: the calendar table

`generate_series(start, stop, step)` is a **set-returning function**: used in `FROM`, it produces one row per value. With timestamps and an interval step, it produces a calendar:

```sql
SELECT month::date
FROM generate_series(timestamptz '2025-01-01', timestamptz '2025-06-01', interval '1 month') AS g (month);
```

```text
   month    
------------
 2025-01-01
 2025-02-01
 2025-03-01
 2025-04-01
 2025-05-01
 2025-06-01
(6 rows)
```

(It works on integers too: `generate_series(1, 10)`.) Now `LEFT JOIN` the orders to the calendar, so every month survives, and aggregate:

```sql
SELECT g.month::date AS month,
       count(o.id)                     AS orders,
       coalesce(sum(o.total), 0)       AS revenue
FROM generate_series(timestamptz '2025-01-01', timestamptz '2025-06-01', interval '1 month') AS g (month)
LEFT JOIN orders AS o
       ON o.created_at >= g.month
      AND o.created_at <  g.month + interval '1 month'
      AND o.seller_id = 4
      AND o.status IN ('paid', 'shipped', 'delivered')
GROUP BY g.month
ORDER BY g.month;
```

```text
   month    | orders | revenue 
------------+--------+---------
 2025-01-01 |      0 |       0
 2025-02-01 |      0 |       0
 2025-03-01 |      1 |  376.00
 2025-04-01 |      2 |  482.00
 2025-05-01 |      0 |       0
 2025-06-01 |      3 |  228.00
(6 rows)
```

Six rows, zeros where there were no sales. Every detail of that query matters:

- **The calendar is on the left**, so its rows are preserved.
- **All order conditions are in `ON`**, not in `WHERE`. A `WHERE o.seller_id = 4` would remove the padded rows of empty months and undo the whole exercise (module 04, section 5.3).
- **`count(o.id)`, not `count(*)`**: the padded row of an empty month would count as 1 (section 2.3).
- **`coalesce(sum(...), 0)`**: the sum over a padded row is `NULL`.
- **The join condition is a range** (`>= month AND < month + 1 month`) rather than `date_trunc('month', o.created_at) = g.month`. Both are correct, but the range form lets the planner use an index on `orders.created_at` (module 11), while wrapping the column in a function prevents it. This "keep the column bare" habit is worth forming now.

### 10.4 One series per seller: `CROSS JOIN` the calendar

A per-seller report needs one row per seller *per month*, including sellers and months with no sales at all. That's every combination of sellers and months, which is a Cartesian product: module 04's `CROSS JOIN`, in one of its legitimate uses. Then outer join the facts:

```sql
SELECT s.shop_name,
       g.month::date               AS month,
       count(o.id)                 AS orders,
       coalesce(sum(o.total), 0)   AS revenue
FROM sellers AS s
CROSS JOIN generate_series(timestamptz '2025-03-01', timestamptz '2025-06-01', interval '1 month') AS g (month)
LEFT JOIN orders AS o
       ON o.seller_id   = s.id
      AND o.created_at >= g.month
      AND o.created_at <  g.month + interval '1 month'
      AND o.status IN ('paid', 'shipped', 'delivered')
WHERE s.id IN (3, 4, 5)
GROUP BY s.id, g.month
ORDER BY s.id, g.month;
```

```text
   shop_name    |   month    | orders | revenue 
----------------+------------+--------+---------
 Casa Cucina    | 2025-03-01 |      3 |  383.00
 Casa Cucina    | 2025-04-01 |      2 |  362.00
 Casa Cucina    | 2025-05-01 |      3 |  331.98
 Casa Cucina    | 2025-06-01 |      3 |  233.00
 Threadbare Co. | 2025-03-01 |      1 |  376.00
 Threadbare Co. | 2025-04-01 |      2 |  482.00
 Threadbare Co. | 2025-05-01 |      0 |       0
 Threadbare Co. | 2025-06-01 |      3 |  228.00
 Maple & Pine   | 2025-03-01 |      0 |       0
 Maple & Pine   | 2025-04-01 |      0 |       0
 Maple & Pine   | 2025-05-01 |      0 |       0
 Maple & Pine   | 2025-06-01 |      0 |       0
(12 rows)
```

3 sellers × 4 months = 12 rows, guaranteed, whatever the data says. Maple & Pine, which has never sold anything, gets four rows of zeros, which is exactly what its monthly statement should say. Notice that the `WHERE s.id IN (3, 4, 5)` filter is fine in `WHERE`: it restricts the *preserved* side (the sellers), which is what `WHERE` is for.

The pattern generalizes to any "dense" report: categories × weeks, products × days, countries × statuses. Build the full grid of dimension values with cross joins, then left join the facts and aggregate.

### 10.5 Aggregating before joining the grid

The grid query above joins raw orders to the grid, then groups. An equivalent shape aggregates the orders *first*, into one row per (seller, month), and then joins that small summary to the grid:

```sql
SELECT s.shop_name,
       g.month::date                    AS month,
       coalesce(m.orders, 0)            AS orders,
       coalesce(m.revenue, 0)           AS revenue
FROM sellers AS s
CROSS JOIN generate_series(timestamptz '2025-03-01', timestamptz '2025-06-01', interval '1 month') AS g (month)
LEFT JOIN (
    SELECT seller_id,
           date_trunc('month', created_at) AS month,
           count(*)   AS orders,
           sum(total) AS revenue
    FROM orders
    WHERE status IN ('paid', 'shipped', 'delivered')
    GROUP BY seller_id, date_trunc('month', created_at)
) AS m ON m.seller_id = s.id AND m.month = g.month
WHERE s.id IN (3, 4, 5)
ORDER BY s.id, g.month;
```

```text
   shop_name    |   month    | orders | revenue 
----------------+------------+--------+---------
 Casa Cucina    | 2025-03-01 |      3 |  383.00
 Casa Cucina    | 2025-04-01 |      2 |  362.00
 Casa Cucina    | 2025-05-01 |      3 |  331.98
 Casa Cucina    | 2025-06-01 |      3 |  233.00
 Threadbare Co. | 2025-03-01 |      1 |  376.00
 Threadbare Co. | 2025-04-01 |      2 |  482.00
 Threadbare Co. | 2025-05-01 |      0 |       0
 Threadbare Co. | 2025-06-01 |      3 |  228.00
 Maple & Pine   | 2025-03-01 |      0 |       0
 Maple & Pine   | 2025-04-01 |      0 |       0
 Maple & Pine   | 2025-05-01 |      0 |       0
 Maple & Pine   | 2025-06-01 |      0 |       0
(12 rows)
```

Same 12 rows. This "aggregate first, then join" shape needs `coalesce` instead of `count(col)` tricks, because the outer query does no aggregating: a missing summary row simply shows up as `NULL`. It's often clearer, and it's the cure for the problem in the next section.

## 11. Fan-out: the double-counting bug, and how to fix it

Module 04 (section 8) showed that joining a one-to-many relationship repeats the "one" side once per match, and that summing a column of the repeated side then counts it several times. In aggregation queries this is the single most common source of wrong numbers, because the inflated result *looks* reasonable.

### 11.1 The bug

The sales team wants a seller dashboard: for each shop, the number of orders, the revenue, and the number of units sold. Units live in `order_items`, so someone joins it in:

```sql
-- WRONG: revenue is inflated
SELECT s.shop_name,
       count(o.id)       AS orders,
       sum(o.total)      AS revenue,
       sum(i.quantity)   AS units
FROM sellers AS s
JOIN orders      AS o ON o.seller_id = s.id
JOIN order_items AS i ON i.order_id  = o.id
WHERE o.status IN ('paid', 'shipped', 'delivered')
GROUP BY s.id
ORDER BY s.id;
```

```text
    shop_name    | orders | revenue  | units 
-----------------+--------+----------+-------
 TechTrove       |     51 | 95823.68 |    69
 Bookworm Corner |     62 |  3958.63 |    81
 Casa Cucina     |     43 |  8771.89 |    57
 Threadbare Co.  |     39 |  5485.00 |    53
(4 rows)
```

Compare with NOTES.md: TechTrove's revenue is 41,598.82, not 95,823.68, and it has 31 such orders, not 51. The units are right; the orders and the revenue are wrong. After the join to `order_items`, each row is an order *line*. `sum(i.quantity)` sums a column that lives at that grain, so it's correct. `count(o.id)` and `sum(o.total)` sum columns that live at the *order* grain, and each order is repeated once per line.

### 11.2 Band-aids that don't work

`count(DISTINCT o.id)` repairs the order count, because it counts each order once however often it's repeated. The tempting next step is `sum(DISTINCT o.total)`:

```sql
-- STILL WRONG: sum(DISTINCT) adds each distinct *value* once
SELECT s.shop_name,
       count(DISTINCT o.id)  AS orders,
       sum(DISTINCT o.total) AS revenue
FROM sellers AS s
JOIN orders      AS o ON o.seller_id = s.id
JOIN order_items AS i ON i.order_id  = o.id
WHERE o.status IN ('paid', 'shipped', 'delivered')
GROUP BY s.id
ORDER BY s.id;
```

```text
    shop_name    | orders | revenue  
-----------------+--------+----------
 TechTrove       |     31 | 40782.83
 Bookworm Corner |     29 |  1620.48
 Casa Cucina     |     23 |  3413.96
 Threadbare Co.  |     18 |  2258.00
(4 rows)
```

The order counts are now right, and the revenues are *too low*. `DISTINCT` removes duplicate *values*, not duplicate *orders*: Bookworm Corner sold several orders for exactly 19.95 or 16.00, and `sum(DISTINCT ...)` counts each amount once. The result is wrong by a small amount, which is the worst kind of wrong: nobody notices. Never use `sum(DISTINCT ...)` to repair fan-out.

### 11.3 The fix: aggregate each table at its own grain, then join

The robust fix is to compute each measure at the grain where it lives, in its own subquery, and join the already-aggregated results. Each summary has at most one row per seller, so the final joins are one-to-one, and nothing can fan out:

```sql
SELECT s.shop_name,
       coalesce(ord.orders, 0)   AS orders,
       coalesce(ord.revenue, 0)  AS revenue,
       coalesce(itm.units, 0)    AS units
FROM sellers AS s
LEFT JOIN (
    SELECT seller_id, count(*) AS orders, sum(total) AS revenue
    FROM orders
    WHERE status IN ('paid', 'shipped', 'delivered')
    GROUP BY seller_id
) AS ord ON ord.seller_id = s.id
LEFT JOIN (
    SELECT o.seller_id, sum(i.quantity) AS units
    FROM orders AS o
    JOIN order_items AS i ON i.order_id = o.id
    WHERE o.status IN ('paid', 'shipped', 'delivered')
    GROUP BY o.seller_id
) AS itm ON itm.seller_id = s.id
ORDER BY s.id;
```

```text
    shop_name    | orders | revenue  | units 
-----------------+--------+----------+-------
 TechTrove       |     31 | 41598.82 |    69
 Bookworm Corner |     29 |  1656.43 |    81
 Casa Cucina     |     23 |  3562.94 |    57
 Threadbare Co.  |     18 |  2277.00 |    53
 Maple & Pine    |      0 |        0 |     0
 Sakura Gadgets  |      0 |        0 |     0
(6 rows)
```

Revenue now matches NOTES.md exactly, the unit counts are unchanged from the (correct) units column of the broken query, and the shops without sales appear with zeros because the summaries are left-joined to `sellers`.

Two other shapes do the same job:

- **Aggregate the lines per order first**, then join to orders. `(SELECT order_id, sum(quantity) AS units FROM order_items GROUP BY order_id)` has one row per order, so joining it to `orders` keeps the order grain, and `sum(o.total)` and `sum(u.units)` are both correct in the outer `GROUP BY`. This is the natural shape when you also need per-order values, as in this module's milestone.
- **Correlated scalar subqueries** in the `SELECT` list, one per measure (module 04, section 10.2). Very readable for a handful of measures over a few parents, and slow for many parents.

### 11.4 The two-children version

The worst case is two independent one-to-many relationships under the same parent: products and orders under a seller. Join both and each seller's products are cross-multiplied with its orders:

```sql
-- WRONG: products × orders per seller
SELECT s.shop_name,
       count(p.id) AS products,
       count(o.id) AS orders
FROM sellers AS s
LEFT JOIN products AS p ON p.seller_id = s.id
LEFT JOIN orders   AS o ON o.seller_id = s.id
GROUP BY s.id
ORDER BY s.id;
```

```text
    shop_name    | products | orders 
-----------------+----------+--------
 TechTrove       |      684 |    684
 Bookworm Corner |      540 |    540
 Casa Cucina     |      325 |    325
 Threadbare Co.  |      231 |    231
 Maple & Pine    |        5 |      0
 Sakura Gadgets  |        0 |      0
(6 rows)
```

TechTrove "has" 684 products and 684 orders, which is 18 × 38. Here `count(DISTINCT ...)` happens to rescue both counts (each product and each order has a unique id), but any `sum` would still be wrong, and the query builds 684 intermediate rows for one seller. With 18,500 products and 70,000 orders, as seller 1 has in the large dataset, that's 1.3 billion rows. Aggregate each child separately, exactly as in 11.3.

> **Pitfall:** Whenever an aggregation query joins more than one table on the "many" side, ask for each aggregate: "at what grain does this column live, and what is the grain of the rows being aggregated?" If they differ, the number is wrong. The cure is always the same: aggregate each table at its own grain first, then join the summaries.

## 12. Performance intuition: how PostgreSQL groups

You don't need to tune queries yet (module 12 does that properly), but it helps to know what a `GROUP BY` costs, because it explains several behaviors you've already seen, like results coming back in no particular order.

PostgreSQL has two main strategies for grouping:

- **`HashAggregate`**: read the input once, keep a hash table with one entry per group (key → running aggregate state), update the entry for each row, and emit all groups at the end. The input doesn't need any order, and the output comes out in hash order, which is effectively random. Memory grows with the **number of groups**, not the number of rows. If the hash table exceeds `work_mem` (4 MB by default), PostgreSQL 13 and later spill parts of it to disk, which works but is slower.
- **`GroupAggregate`**: requires input **sorted** by the grouping keys, walks it, and emits a group each time the key changes. It needs almost no memory and produces sorted output, but if the input isn't already sorted (by an index, or by an earlier step), it needs a `Sort` first, which costs O(n log n) and possibly disk.

`EXPLAIN` shows which one the planner picked:

```sql
EXPLAIN (COSTS OFF)
SELECT seller_id, count(*), sum(total)
FROM orders
GROUP BY seller_id;
```

```text
        QUERY PLAN        
--------------------------
 HashAggregate
   Group Key: seller_id
   ->  Seq Scan on orders
(3 rows)
```

A hash table with four entries is the obvious choice. Asking for sorted output doesn't necessarily change the strategy:

```sql
EXPLAIN (COSTS OFF)
SELECT order_id, sum(quantity * unit_price)
FROM order_items
GROUP BY order_id
ORDER BY order_id;
```

```text
             QUERY PLAN              
-------------------------------------
 Sort
   Sort Key: order_id
   ->  HashAggregate
         Group Key: order_id
         ->  Seq Scan on order_items
(5 rows)
```

On 235 rows the planner hashes them into 120 groups and then sorts the 120 results, which is cheaper than sorting all 235 input rows. To see the other strategy, switch hash aggregation off for the session (a planner setting meant for experiments like this one, never for production):

```sql
SET enable_hashagg = off;

EXPLAIN (COSTS OFF)
SELECT order_id, sum(quantity * unit_price)
FROM order_items
GROUP BY order_id
ORDER BY order_id;

RESET enable_hashagg;
```

```text
SET
             QUERY PLAN              
-------------------------------------
 GroupAggregate
   Group Key: order_id
   ->  Sort
         Sort Key: order_id
         ->  Seq Scan on order_items
(5 rows)

RESET
```

Now the rows are sorted first and `GroupAggregate` walks the sorted stream, emitting each group when `order_id` changes, already in the requested order. Which plan wins depends on the number of rows, the number of groups, `work_mem`, and whether an index can deliver the rows pre-sorted. On the large dataset of module 11, with an index on `order_items (order_id)`, the planner often reads the index in order and skips the sort entirely. Some aggregates sort no matter what: `count(DISTINCT ...)`, aggregates with an `ORDER BY` inside, and ordered-set aggregates like `percentile_cont` all sort the rows of each group.

Rules of thumb you can use now:

- Filter early. `WHERE` reduces the rows that reach the aggregate, `HAVING` only reduces the groups that leave it.
- Aggregate before joining when a join would multiply rows (section 11). Besides being correct, it shrinks the join's inputs.
- `count(DISTINCT x)` is much more expensive than `count(*)` on large inputs, because it must sort or hash the distinct values. Use it when you need it, not as a reflex.
- Large `GROUP BY`s with few groups are cheap in memory (hash). Large `GROUP BY`s with millions of groups need memory or a sort, and benefit from an index that delivers the rows pre-sorted.

Module 12 covers reading these plans with real timings, `work_mem` tuning, and parallel aggregation (several workers each hash part of the table, then a `Finalize` step combines them). Module 09's materialized views show how to precompute expensive aggregates once instead of on every request.

---

## Summary

- Aggregates collapse a set of rows into one value. `count(*)` counts rows, `count(col)` counts non-`NULL` values, `count(DISTINCT col)` counts distinct non-`NULL` values. All other ordinary aggregates skip `NULL`s, and return `NULL` (not 0) on empty input: use `coalesce` when a missing sum means zero.
- `GROUP BY` produces one row per distinct combination of grouping values, in no particular order. The `SELECT` list may contain grouping expressions, aggregates and constants, plus any column of a table whose primary key is grouped (functional dependency).
- When counting the "many" side of an outer join, count its primary key, not `*`.
- `WHERE` filters rows before grouping; `HAVING` filters groups after aggregation. Output aliases are visible in `ORDER BY` but not in `WHERE` or `HAVING`.
- `agg(...) FILTER (WHERE ...)` computes several conditional aggregates in one pass: the standard way to pivot statuses into columns. Do ratios in `numeric` and guard denominators with `NULLIF`.
- `array_agg`, `string_agg` and `jsonb_agg` collect values. Put an `ORDER BY` inside the call for a deterministic order. `bool_and`/`bool_or` answer "for all" and "for any" per group.
- `percentile_cont` interpolates, `percentile_disc` returns a real value, `mode()` returns the most frequent value. All use `WITHIN GROUP (ORDER BY ...)`. Medians resist outliers that drag averages around.
- `DISTINCT ON (keys) ... ORDER BY keys, choice` keeps the first row per group: the compact PostgreSQL answer to "latest row per group". Always add a tie-breaker.
- `GROUPING SETS`, `ROLLUP` (hierarchical subtotals) and `CUBE` (all combinations) compute several groupings in one query. `GROUPING()` tells subtotal rows from data `NULL`s and helps sort and label them.
- Bucket time with `date_trunc` (and an explicit time zone for `timestamptz`). Fill empty buckets by generating the calendar with `generate_series` (and cross joining it with other dimensions), then left joining the facts with all their conditions in `ON`.
- Joining before aggregating multiplies parent rows. `sum(DISTINCT ...)` does not fix it. Aggregate each table at its own grain in a subquery, then join the summaries.
- `HashAggregate` needs memory per group and returns unordered output; `GroupAggregate` needs sorted input and returns sorted output. Filter early and aggregate before joining.

## What's next

[Module 06](../06-advanced-sql/) takes the next step: common table expressions to structure multi-step queries like the ones in sections 10 and 11, recursive CTEs to walk the category tree to any depth, and **window functions**, which compute aggregates *alongside* each row instead of collapsing the rows: running totals, ranks, month-over-month changes and top N per group.
