# Module 06: Solutions

<!-- nav:start -->
> [Theory](../README.md) · [Exercises](../exercises.md) · [Project](../project.md) · **Solutions**  
> [← 05 Aggregation and Grouping](../../05-aggregation/README.md) · [Course home](../../../README.md) · [07 Data Modeling →](../../07-data-modeling/README.md)
<!-- nav:end -->

Every solution was run on PostgreSQL 17 against `./project/bin/reset.sh 06`. Solutions that change data run inside `BEGIN ... ROLLBACK`.

Files in this directory:

| File | What |
|---|---|
| [`milestone_category_tree.sql`](milestone_category_tree.sql) | Project milestone, part 1: subtree, breadcrumb, revenue roll-up. |
| [`milestone_leaderboard.sql`](milestone_leaderboard.sql) | Project milestone, part 2: seller leaderboard with month-over-month rank change. |

---

<!-- toc:start -->
**Contents**

- [Exercise 1 ★: Better than average](#exercise-1--better-than-average)
- [Exercise 2 ★: A customer's lifetime, row by row](#exercise-2--a-customers-lifetime-row-by-row)
- [Exercise 3 ★: A weekly calendar, recursively](#exercise-3--a-weekly-calendar-recursively)
- [Exercise 4 ★★: Products in the wrong place](#exercise-4--products-in-the-wrong-place)
- [Exercise 5 ★★: Expire stale pending orders, in one statement](#exercise-5--expire-stale-pending-orders-in-one-statement)
- [Exercise 6 ★★: The longest silence](#exercise-6--the-longest-silence)
- [Exercise 7 ★★: Unusually large orders](#exercise-7--unusually-large-orders)
- [Exercise 8 ★★: Bestsellers per department](#exercise-8--bestsellers-per-department)
- [Exercise 9 ★★: Selling streaks and dry spells](#exercise-9--selling-streaks-and-dry-spells)
- [Exercise 10 ★★: Sync a seller's catalog with MERGE](#exercise-10--sync-a-sellers-catalog-with-merge)
- [Exercise 11 ★★★: Move a category without breaking the tree](#exercise-11--move-a-category-without-breaking-the-tree)
- [Exercise 12 ★★★: Top 3 and "Other"](#exercise-12--top-3-and-other)
- [Exercise 13 ★★★: The best 30 days](#exercise-13--the-best-30-days)

<!-- toc:end -->

## Exercise 1 ★: Better than average

```sql
WITH customer_spend AS (
    SELECT user_id, count(*) AS orders, sum(total) AS spent
    FROM orders
    WHERE status IN ('paid', 'shipped', 'delivered')
    GROUP BY user_id
),
average AS (
    SELECT avg(spent) AS avg_spent FROM customer_spend
)
SELECT u.id, u.full_name, c.orders, c.spent,
       round(c.spent / a.avg_spent, 2) AS times_average
FROM customer_spend AS c
CROSS JOIN average AS a
JOIN users AS u ON u.id = c.user_id
WHERE c.spent > a.avg_spent
ORDER BY c.spent DESC;
```

```text
 id |   full_name   | orders |  spent   | times_average 
----+---------------+--------+----------+---------------
 13 | Olivia Brown  |     17 | 14564.95 |          5.93
 11 | Emma Schmidt  |     12 | 10496.36 |          4.28
  8 | Marcus Lee    |     19 |  4890.85 |          1.99
 12 | Liam O'Connor |      6 |  3472.91 |          1.41
 19 | Mia Rossi     |      3 |  3053.99 |          1.24
 26 | Daniel Novák  |      5 |  2977.45 |          1.21
(6 rows)
```

**Reasoning.** `customer_spend` is referenced twice (by `average` and by the main query), so PostgreSQL materializes it and computes the aggregation once. With subqueries you'd write the `GROUP BY` twice. `average` is a one-row CTE, and `CROSS JOIN` attaches its value to every row.

Note what "average customer" means here: the average over the 20 customers with at least one paid order (2,454.76). Averaging over *all* 30 users, counting non-buyers as 0, gives a different and smaller number, and more rows. Which one is right is a business question; say which one you computed.

**Alternative.** A window function computes the average without a separate CTE: `avg(spent) OVER ()` in a CTE over `customer_spend`, then filter outside. Same plan shape, one fewer step.

---

## Exercise 2 ★: A customer's lifetime, row by row

```sql
SELECT id, created_at::date AS ordered_on, total,
       sum(total) OVER w AS running_spend,
       round(100 * sum(total) OVER w / sum(total) OVER (), 1) AS pct_of_lifetime
FROM orders
WHERE user_id = 13
  AND status IN ('paid', 'shipped', 'delivered')
WINDOW w AS (ORDER BY created_at, id)
ORDER BY created_at, id;
```

```text
 id  | ordered_on |  total  | running_spend | pct_of_lifetime 
-----+------------+---------+---------------+-----------------
   1 | 2024-01-12 |   50.48 |         50.48 |             0.3
   5 | 2024-01-24 |   34.00 |         84.48 |             0.6
   8 | 2024-02-16 |   42.00 |        126.48 |             0.9
  15 | 2024-03-19 |   79.00 |        205.48 |             1.4
  27 | 2024-05-15 | 1628.00 |       1833.48 |            12.6
  33 | 2024-06-24 |  277.00 |       2110.48 |            14.5
  34 | 2024-06-24 |  521.00 |       2631.48 |            18.1
  40 | 2024-08-06 | 8940.00 |      11571.48 |            79.4
  46 | 2024-09-17 |   45.00 |      11616.48 |            79.8
  47 | 2024-09-29 |   34.99 |      11651.47 |            80.0
  50 | 2024-10-02 |   19.00 |      11670.47 |            80.1
  55 | 2024-10-28 |  147.00 |      11817.47 |            81.1
  72 | 2025-02-03 |  319.98 |      12137.45 |            83.3
  78 | 2025-03-02 |  297.00 |      12434.45 |            85.4
  96 | 2025-05-17 |   24.50 |      12458.95 |            85.5
 104 | 2025-06-18 |  129.00 |      12587.95 |            86.4
 106 | 2025-06-22 | 1977.00 |      14564.95 |           100.0
(17 rows)
```

**Reasoning.** `sum(total) OVER w` with an `ORDER BY` uses the default frame (start of partition to current row), which is a running sum. `sum(total) OVER ()` has no `ORDER BY`, so its frame is the whole result: the lifetime total. The window sees only the rows that passed `WHERE`, so cancelled and pending orders are excluded from both.

Orders 33 and 34 were placed on the same day, but at different times, so `created_at` alone happens to be unique here. The `id` tie-breaker costs nothing and protects the running total from the "peers jump together" effect if two orders ever share a timestamp (section 8.2 of the theory).

---

## Exercise 3 ★: A weekly calendar, recursively

```sql
WITH RECURSIVE weeks (week_start) AS (
    SELECT date '2025-03-31'
    UNION ALL
    SELECT week_start + 7
    FROM weeks
    WHERE week_start + 7 <= date '2025-06-30'
)
SELECT w.week_start,
       count(o.id) AS orders,
       coalesce(sum(o.total), 0) AS revenue
FROM weeks AS w
LEFT JOIN orders AS o
       ON o.created_at >= w.week_start
      AND o.created_at <  w.week_start + 7
      AND o.status <> 'cancelled'
GROUP BY w.week_start
ORDER BY w.week_start;
```

```text
 week_start | orders | revenue 
------------+--------+---------
 2025-03-31 |      1 |  105.98
 2025-04-07 |      1 | 1837.00
 2025-04-14 |      2 |  435.00
 2025-04-21 |      2 |  362.00
 2025-04-28 |      2 |  200.48
 2025-05-05 |      0 |       0
 2025-05-12 |      3 | 3514.49
 2025-05-19 |      1 |  208.00
 2025-05-26 |      2 |  108.98
 2025-06-02 |      1 |  132.00
 2025-06-09 |      1 |   69.46
 2025-06-16 |      7 | 4978.92
 2025-06-23 |     11 | 5762.90
 2025-06-30 |      1 |   90.00
(14 rows)
```

**Reasoning.** `date + integer` adds days and stays a `date`, so the recursive term has the same type as the anchor. The termination condition is checked on the *next* value (`week_start + 7 <= ...`), so the last week emitted is 2025-06-30.

Three details make the counts right:

- The status filter is in the `ON` clause, not in `WHERE`. In `WHERE`, `o.status <> 'cancelled'` would be NULL for the empty week (no order matched), and the week would disappear: the classic outer-join trap from module 04.
- `count(o.id)` counts only matched orders; `count(*)` would count 1 for the empty week.
- The half-open interval `>= start AND < start + 7` puts an order placed at exactly midnight in exactly one week. Comparing a `timestamptz` with a `date` converts the date to midnight in the session's time zone (UTC here). With a different `TimeZone` setting, the week boundaries shift.

**Alternative.** `generate_series(date '2025-03-31', date '2025-06-30', interval '7 days')` is the idiomatic way (module 05); the recursive version is here to practice the mechanism.

---

## Exercise 4 ★★: Products in the wrong place

```sql
WITH RECURSIVE crumbs AS (
    SELECT p.id AS product_id, c.id AS category_id, c.parent_id, ARRAY[c.name] AS names
    FROM products AS p
    JOIN categories AS c ON c.id = p.category_id
    WHERE EXISTS (SELECT 1 FROM categories AS child WHERE child.parent_id = p.category_id)
    UNION ALL
    SELECT cr.product_id, c.id, c.parent_id, c.name || cr.names
    FROM crumbs AS cr
    JOIN categories AS c ON c.id = cr.parent_id
)
SELECT p.id, p.title, array_to_string(cr.names, ' › ') AS breadcrumb
FROM crumbs AS cr
JOIN products AS p ON p.id = cr.product_id
WHERE cr.parent_id IS NULL
ORDER BY p.id;
```

```text
 id |            title            |       breadcrumb        
----+-----------------------------+-------------------------
 17 | USB-C Docking Station       | Electronics › Computers
 18 | Wireless Ergonomic Mouse    | Electronics › Computers
 33 | The Complete Sci-Fi Box Set | Books › Fiction
 46 | Espresso Moka Pot 6-cup     | Home & Garden › Kitchen
 60 | Cedar Raised Garden Bed     | Home & Garden › Garden
 62 | Bazaar Sticker Pack         | Clothing
(6 rows)
```

**Reasoning.** The anchor starts one walk per product, and `product_id` travels with every step, so 6 walks run side by side in the same iterations. `text || text[]` *prepends* the parent's name, so the array is already root-first when the walk reaches the root. The finished walk is the row whose `parent_id` is NULL: earlier rows of the same walk are partial breadcrumbs.

"Non-leaf" is "some category has me as its parent", an `EXISTS` semi-join (module 04).

**Alternative.** Compute the breadcrumb of *every category* once (a top-down walk from the roots, carrying the path), then join products to it. That's cheaper when many products share categories, because each category's path is computed once rather than once per product. For a product page showing one product, the bottom-up walk of section 3.5 is ideal.

---

## Exercise 5 ★★: Expire stale pending orders, in one statement

```sql
BEGIN;
SELECT sum(reserved) AS reserved_before FROM inventory;

WITH expired AS (
    UPDATE orders
    SET status = 'cancelled'
    WHERE status = 'pending'
      AND created_at < timestamptz '2025-06-30 12:00+00' - interval '24 hours'
    RETURNING id
),
released AS (
    SELECT oi.product_id, sum(oi.quantity) AS qty
    FROM order_items AS oi
    JOIN expired AS e ON e.id = oi.order_id
    GROUP BY oi.product_id
),
updated AS (
    UPDATE inventory AS i
    SET reserved = i.reserved - r.qty
    FROM released AS r
    WHERE i.product_id = r.product_id
    RETURNING i.product_id, i.reserved
)
SELECT (SELECT array_agg(id ORDER BY id) FROM expired) AS cancelled_orders,
       (SELECT count(*) FROM updated)                  AS inventory_rows,
       (SELECT sum(qty) FROM released)                 AS units_released;

SELECT sum(reserved) AS reserved_after FROM inventory;
```

```text
 reserved_before 
-----------------
              24
(1 row)

 cancelled_orders | inventory_rows | units_released 
------------------+----------------+----------------
 {115,116,117}    |              5 |              8
(1 row)

 reserved_after 
----------------
             16
(1 row)
```

The consistency check, as a separate statement:

```sql
SELECT count(*) AS mismatches
FROM inventory AS i
LEFT JOIN (
    SELECT oi.product_id, sum(oi.quantity) AS units
    FROM order_items AS oi
    JOIN orders AS o ON o.id = oi.order_id
    WHERE o.status IN ('pending', 'paid')
    GROUP BY oi.product_id
) AS open ON open.product_id = i.product_id
WHERE i.reserved <> coalesce(open.units, 0);
ROLLBACK;
```

```text
 mismatches 
------------
          0
(1 row)
```

**Reasoning.**

- `expired` changes the orders and passes their ids on through `RETURNING`. `released` reads `order_items` (which this statement doesn't change, so the snapshot is fine) joined to `expired`.
- **Aggregate before `UPDATE ... FROM`.** If two expired orders contained the same product, an unaggregated `FROM` would produce two source rows for one inventory row, and `UPDATE ... FROM` silently applies only *one* of them. Releasing 1 unit instead of 2 would leave stock reserved forever. Summing per product first makes the join one-to-one. It doesn't matter for today's three orders, but it would for tomorrow's.
- **The check must be a separate statement.** Inside the `WITH` statement, every part sees the snapshot from before the statement, so a check there would still see the orders as pending and the old `reserved` values.
- The cut-off is a fixed timestamp, not `now()`, because the seed data never depends on the current date. In a real job, `now() - interval '24 hours'` is the natural form.

**Alternative.** Two statements in one transaction (update orders, then update inventory from a subquery on `status = 'cancelled'`) are just as atomic, but the second statement has to *find* the orders again, and a condition like "cancelled" would also catch orders cancelled earlier. `RETURNING` passes exactly the affected ids. Module 10 adds what this solution ignores: concurrent checkouts touching the same inventory rows.

---

## Exercise 6 ★★: The longest silence

```sql
WITH gaps AS (
    SELECT user_id, id AS order_id, created_at,
           lag(created_at) OVER (PARTITION BY user_id ORDER BY created_at) AS prev_at
    FROM orders
    WHERE status <> 'cancelled'
)
SELECT g.user_id, u.full_name,
       g.prev_at::date AS from_date, g.created_at::date AS to_date,
       g.created_at::date - g.prev_at::date AS days
FROM gaps AS g
JOIN users AS u ON u.id = g.user_id
WHERE g.prev_at IS NOT NULL
ORDER BY days DESC
LIMIT 5;
```

```text
 user_id |   full_name    | from_date  |  to_date   | days 
---------+----------------+------------+------------+------
       3 | Giulia Rossi   | 2024-03-01 | 2025-02-26 |  362
       4 | Lukas Becker   | 2024-07-11 | 2025-05-15 |  308
      12 | Liam O'Connor  | 2024-08-17 | 2025-05-28 |  284
       5 | Chloé Tremblay | 2024-12-02 | 2025-06-22 |  202
      14 | Mateo García   | 2024-04-26 | 2024-11-04 |  192
(5 rows)
```

**Reasoning.** `lag` pairs each order with the customer's previous one. The filter on `prev_at` must happen in the outer query, because window results can't be used in `WHERE` at the same level. Note that the status filter, in contrast, *must* be inside: it decides which orders count as "consecutive". Moving it outside would compare an order with a cancelled predecessor.

Users 3, 4 and 5 are sellers' owners who also shop; nothing special about that, but worth noticing when you sanity-check results.

**Alternative.** Subtracting the `timestamptz` values directly gives an `interval` (`361 days 22:13:...`), which sorts correctly but is less readable. `extract(epoch FROM ...) / 86400` gives fractional days.

---

## Exercise 7 ★★: Unusually large orders

```sql
WITH scored AS (
    SELECT id, seller_id, created_at, total,
           avg(total) OVER prev3 AS prev3_avg,
           count(*)   OVER prev3 AS prev_n
    FROM orders
    WHERE status IN ('paid', 'shipped', 'delivered')
    WINDOW prev3 AS (PARTITION BY seller_id ORDER BY created_at, id
                     ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING)
)
SELECT id, seller_id, created_at::date AS ordered_on, total,
       round(prev3_avg, 2) AS prev3_avg,
       round(total / prev3_avg, 1) AS ratio
FROM scored
WHERE prev_n = 3
  AND total > 3 * prev3_avg
  AND created_at >= '2025-01-01'
ORDER BY ratio DESC;
```

```text
 id  | seller_id | ordered_on |  total  | prev3_avg | ratio 
-----+-----------+------------+---------+-----------+-------
  87 |         1 | 2025-04-09 | 1837.00 |    162.99 |  11.3
  78 |         3 | 2025-03-02 |  297.00 |     42.99 |   6.9
 114 |         1 | 2025-06-27 | 4399.98 |    752.00 |   5.9
  95 |         1 | 2025-05-17 | 3455.00 |    668.66 |   5.2
  91 |         3 | 2025-04-27 |  283.00 |     55.00 |   5.1
  86 |         2 | 2025-04-01 |  105.98 |     30.97 |   3.4
  85 |         4 | 2025-03-20 |  376.00 |    119.00 |   3.2
(7 rows)
```

**Reasoning.** The frame `ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING` contains up to three rows and excludes the current one. Early orders have fewer predecessors, and the average of one or two orders is a weak baseline, so `count(*)` over the same named window filters them out. (For a seller's very first order, the frame is empty: `avg` returns NULL and `count` 0.)

The 2025 filter must come *after* the window: a January 2025 order's baseline consists of 2024 orders. Filtering in the CTE's `WHERE` would remove them before the window function sees them.

**Alternative.** `EXCLUDE CURRENT ROW` with `ROWS BETWEEN 3 PRECEDING AND CURRENT ROW` gives the same frame. For a sturdier baseline, use the median of the last N orders; ordered-set aggregates like `percentile_cont` can't be used as window functions, so that needs a `LATERAL` subquery per order.

---

## Exercise 8 ★★: Bestsellers per department

```sql
WITH RECURSIVE roots AS (
    SELECT id AS category_id, id AS root_id, name AS root_name
    FROM categories
    WHERE parent_id IS NULL
    UNION ALL
    SELECT c.id, r.root_id, r.root_name
    FROM categories AS c
    JOIN roots AS r ON c.parent_id = r.category_id
),
units AS (
    SELECT oi.product_id, sum(oi.quantity) AS units
    FROM order_items AS oi
    JOIN orders AS o ON o.id = oi.order_id
    WHERE o.status IN ('paid', 'shipped', 'delivered')
    GROUP BY oi.product_id
),
ranked AS (
    SELECT r.root_name, p.id, p.title, u.units,
           rank() OVER (PARTITION BY r.root_id ORDER BY u.units DESC) AS rnk
    FROM units AS u
    JOIN products AS p ON p.id = u.product_id
    JOIN roots AS r ON r.category_id = p.category_id
)
SELECT root_name, rnk, id, left(title, 30) AS title, units
FROM ranked
WHERE rnk <= 3
ORDER BY root_name, rnk, id;
```

```text
   root_name   | rnk | id |            title            | units 
---------------+-----+----+-----------------------------+-------
 Books         |   1 | 24 | Murder at Willow Lane       |    12
 Books         |   2 | 22 | Echoes of Andromeda         |    11
 Books         |   3 | 27 | The Silent Witness          |     8
 Clothing      |   1 | 48 | Organic Cotton Tee - Black  |    11
 Clothing      |   2 | 53 | Heavyweight Pullover Hoodie |     8
 Clothing      |   2 | 54 | Kids' Rainbow Hoodie        |     8
 Electronics   |   1 |  2 | AeroBook 15 Pro Laptop      |     9
 Electronics   |   1 | 10 | QuietWave ANC Headphones    |     9
 Electronics   |   3 |  1 | AeroBook 13 Laptop          |     8
 Home & Garden |   1 | 43 | Bread Knife 23cm            |     7
 Home & Garden |   2 | 36 | Non-Stick Frying Pan 28cm   |     6
 Home & Garden |   2 | 37 | Dutch Oven 5.5L Enamel      |     6
 Home & Garden |   2 | 39 | Pasta Pot with Strainer 6L  |     6
(13 rows)
```

**Reasoning.** The recursive CTE pushes each root's id down the tree, producing a lookup table "category → department" (23 rows). Products in non-leaf categories (like product 62 directly in Clothing) are covered because every category, not only leaves, gets a root.

`rank()` is the function that matches the spec: tied products share a rank, and "top 3" still means three *positions*. With `rank`, Electronics shows 1, 1, 3: two winners and one third place. `dense_rank()` would have continued with rank 2 and could return many more rows (all products with the three highest *distinct* unit counts). `row_number()` would drop one of the tied products arbitrarily.

**Alternative.** If you need this often, store the department on the category or keep a closure table (module 07 discusses tree representations), and the recursion disappears from the query.

---

## Exercise 9 ★★: Selling streaks and dry spells

(a) Longest streaks:

```sql
WITH seller_months AS (
    SELECT DISTINCT seller_id, date_trunc('month', created_at)::date AS month
    FROM orders
    WHERE status IN ('paid', 'shipped', 'delivered')
),
islands AS (
    SELECT seller_id, month,
           (extract(year FROM month) * 12 + extract(month FROM month))::int
             - row_number() OVER (PARTITION BY seller_id ORDER BY month) AS grp
    FROM seller_months
),
streaks AS (
    SELECT seller_id, min(month) AS first_month, max(month) AS last_month, count(*) AS months,
           rank() OVER (PARTITION BY seller_id ORDER BY count(*) DESC) AS rnk
    FROM islands
    GROUP BY seller_id, grp
)
SELECT seller_id, first_month, last_month, months
FROM streaks
WHERE rnk = 1
ORDER BY seller_id, first_month;
```

```text
 seller_id | first_month | last_month | months 
-----------+-------------+------------+--------
         1 | 2024-08-01  | 2025-06-01 |     11
         2 | 2024-01-01  | 2024-05-01 |      5
         2 | 2024-10-01  | 2025-02-01 |      5
         3 | 2024-01-01  | 2024-04-01 |      4
         3 | 2024-06-01  | 2024-09-01 |      4
         3 | 2025-03-01  | 2025-06-01 |      4
         4 | 2024-08-01  | 2024-11-01 |      4
(7 rows)
```

(b) Gaps:

```sql
WITH seller_months AS (
    SELECT DISTINCT seller_id, date_trunc('month', created_at)::date AS month
    FROM orders
    WHERE status IN ('paid', 'shipped', 'delivered')
),
with_next AS (
    SELECT seller_id, month,
           lead(month) OVER (PARTITION BY seller_id ORDER BY month) AS next_month
    FROM seller_months
)
SELECT seller_id,
       (month + interval '1 month')::date      AS gap_from,
       (next_month - interval '1 month')::date AS gap_to,
       (extract(year FROM age(next_month, month)) * 12
          + extract(month FROM age(next_month, month)))::int - 1 AS empty_months
FROM with_next
WHERE next_month > month + interval '1 month'
ORDER BY seller_id, gap_from;
```

```text
 seller_id |  gap_from  |   gap_to   | empty_months 
-----------+------------+------------+--------------
         1 | 2024-02-01 | 2024-02-01 |            1
         1 | 2024-06-01 | 2024-07-01 |            2
         2 | 2024-06-01 | 2024-06-01 |            1
         2 | 2024-08-01 | 2024-09-01 |            2
         2 | 2025-03-01 | 2025-03-01 |            1
         3 | 2024-05-01 | 2024-05-01 |            1
         3 | 2024-10-01 | 2024-11-01 |            2
         3 | 2025-01-01 | 2025-02-01 |            2
         4 | 2024-04-01 | 2024-05-01 |            2
         4 | 2024-07-01 | 2024-07-01 |            1
         4 | 2024-12-01 | 2025-02-01 |            3
         4 | 2025-05-01 | 2025-05-01 |            1
(12 rows)
```

**Reasoning.** (a) is the island trick from the theory, plus a window function applied *to an aggregate* (`rank() OVER (... ORDER BY count(*) DESC)`) to rank each seller's islands by length in the same query level as the `GROUP BY`. `rank` (not `row_number`) keeps tied streaks, which is why seller 3 shows three rows.

(b) works from the other side: an island ends where the next selling month is more than one month away, and everything between is a gap. `age(a, b)` returns a "symbolic" interval in years and months, which converts cleanly to a month count; plain subtraction of dates would give days. Only gaps *between* the first and last selling month are found; months after a seller's last sale (none here, all sellers sold in June 2025) would need a comparison with the current month.

**Alternative.** Build a seller × month grid with `generate_series` and mark empty months; then both islands and gaps come out of the same island trick applied to "has sales" flags. More work, but it also handles leading and trailing gaps.

---

## Exercise 10 ★★: Sync a seller's catalog with MERGE

The feed, from the exercise:

```sql
BEGIN;
CREATE TEMP TABLE catalog_feed (sku text PRIMARY KEY, price numeric(12,2) NOT NULL) ON COMMIT DROP;
INSERT INTO catalog_feed VALUES
    ('BW-SF-001', 15.99), ('BW-SF-002', 12.50), ('BW-SF-003', 18.00), ('BW-SF-004', 9.99),
    ('BW-SF-005', 16.00), ('BW-MY-001', 11.99), ('BW-MY-002', 13.49), ('BW-MY-004', 12.00),
    ('BW-CK-001', 27.50), ('BW-CK-002', 22.00), ('BW-CK-003', 24.50), ('BW-FIC-001', 79.00),
    ('BW-NF-001', 21.00);
```

The sync:

```sql
MERGE INTO products AS p
USING catalog_feed AS f
ON p.seller_id = 2 AND p.sku = f.sku
WHEN MATCHED AND p.price <> f.price THEN
    UPDATE SET price = f.price, updated_at = now()
WHEN NOT MATCHED BY SOURCE AND p.seller_id = 2 AND p.status = 'active' THEN
    UPDATE SET status = 'archived', updated_at = now()
RETURNING merge_action() AS action, p.id, p.sku, p.price, p.status;
```

```text
 action | id |    sku    | price |  status  
--------+----+-----------+-------+----------
 UPDATE | 19 | BW-SF-001 | 15.99 | active
 UPDATE | 26 | BW-MY-003 | 10.00 | archived
 UPDATE | 29 | BW-CK-001 | 27.50 | active
 UPDATE | 32 | BW-CK-004 | 19.95 | archived
(4 rows)

MERGE 4
```

```sql
SELECT f.sku AS unknown_sku
FROM catalog_feed AS f
WHERE NOT EXISTS (SELECT 1 FROM products AS p WHERE p.seller_id = 2 AND p.sku = f.sku);

SELECT status, count(*) FROM products WHERE seller_id = 2 GROUP BY status ORDER BY status;
ROLLBACK;
```

```text
 unknown_sku 
-------------
 BW-NF-001
(1 row)

  status  | count 
----------+-------
 active   |    11
 archived |     3
 draft    |     1
(3 rows)
```

**Reasoning.**

- `ON p.seller_id = 2 AND p.sku = f.sku`: SKUs are unique only per seller, so the seller must be part of the match.
- `WHEN NOT MATCHED BY SOURCE` applies to **every target row that no source row matched**, which includes all 47 products of the other sellers. The clause's own `AND p.seller_id = 2` is what keeps them safe. Without it, this statement would archive the whole marketplace. That's the pitfall from section 12.2 in its most dangerous form.
- `AND p.status = 'active'` leaves the draft (28) and the already archived products alone.
- `WHEN NOT MATCHED` (the feed's unknown SKU) has no clause, so `MERGE` ignores that row. `MERGE` can't report rows it didn't act on, hence the separate anti-join. In a real import you would insert those into a "rejected lines" table, which a data-modifying CTE could do in the same statement.
- Both actions are reported as `UPDATE` by `merge_action()`; the `status` column tells them apart. `RETURNING` has no `ORDER BY`, so the rows come back in whatever order `MERGE` processed them, which can differ from run to run. If order matters, wrap the `MERGE` in a CTE and sort outside (`WITH m AS (MERGE ... RETURNING ...) SELECT * FROM m ORDER BY id`, PostgreSQL 17). You could add `CASE` in `RETURNING` for a clearer label.

**Alternative.** Without `NOT MATCHED BY SOURCE` (PostgreSQL 15 and 16), you'd run an `UPDATE` for prices and a second `UPDATE ... WHERE NOT EXISTS (feed)` for archiving, in one transaction. One more statement, same result.

---

## Exercise 11 ★★★: Move a category without breaking the tree

The guarded move:

```sql
BEGIN;
\set cat 15
\set new_parent 17
WITH RECURSIVE subtree AS (
    SELECT id FROM categories WHERE id = :cat
    UNION ALL
    SELECT c.id FROM categories AS c JOIN subtree AS s ON c.parent_id = s.id
)
UPDATE categories
SET parent_id = :new_parent
WHERE id = :cat
  AND :new_parent NOT IN (SELECT id FROM subtree)
RETURNING id, parent_id;
```

```text
 id | parent_id 
----+-----------
(0 rows)

UPDATE 0
```

```sql
\set cat 19
\set new_parent 9
-- the same statement again
WITH RECURSIVE subtree AS (
    SELECT id FROM categories WHERE id = :cat
    UNION ALL
    SELECT c.id FROM categories AS c JOIN subtree AS s ON c.parent_id = s.id
)
UPDATE categories
SET parent_id = :new_parent
WHERE id = :cat
  AND :new_parent NOT IN (SELECT id FROM subtree)
RETURNING id, parent_id;
```

```text
 id | parent_id 
----+-----------
 19 |         9
(1 row)

UPDATE 1
```

The detector, then the corruption, then the detector again:

```sql
WITH RECURSIVE walk AS (
    SELECT id AS start_id, id, parent_id FROM categories
    UNION ALL
    SELECT w.start_id, c.id, c.parent_id
    FROM walk AS w
    JOIN categories AS c ON c.id = w.parent_id
) CYCLE id SET is_cycle USING path
SELECT DISTINCT start_id FROM walk WHERE is_cycle ORDER BY start_id;
```

```text
 start_id 
----------
(0 rows)
```

```sql
UPDATE categories SET parent_id = 16 WHERE id = 14;    -- Books under Science Fiction

WITH RECURSIVE walk AS (
    SELECT id AS start_id, id, parent_id FROM categories
    UNION ALL
    SELECT w.start_id, c.id, c.parent_id
    FROM walk AS w
    JOIN categories AS c ON c.id = w.parent_id
) CYCLE id SET is_cycle USING path
SELECT start_id, path FROM walk WHERE is_cycle ORDER BY start_id;
ROLLBACK;
```

```text
 start_id |              path               
----------+---------------------------------
       14 | {(14),(16),(15),(14)}
       15 | {(15),(14),(16),(15)}
       16 | {(16),(15),(14),(16)}
       17 | {(17),(15),(14),(16),(15)}
       18 | {(18),(14),(16),(15),(14)}
       20 | {(20),(18),(14),(16),(15),(14)}
(6 rows)
```

**Reasoning.**

- **The guard.** A move of X under Y creates a cycle exactly when Y is X itself or a descendant of X: then walking up from Y reaches X, and X now points at Y. The recursive CTE computes X's subtree (including X) and the `WHERE` refuses the update if Y is in it. The CTE is attached to the `UPDATE`, so it is one statement and the check and the change see the same snapshot.
- **The detector** starts a walk at *every* category (`start_id` remembers where) and walks up. On a healthy tree every walk ends at a root. In a cycle, a walk revisits a node, and `CYCLE` flags it and stops extending that path. Categories outside the loop but *above* it in the walk (17, 18, 20 lead into the loop) are reported too, because walking up from them never reaches a root. Cooking (19) isn't reported because step 2 moved it to Kitchen.
- `NOT IN` with a subquery is dangerous when the subquery can return NULL (module 04): any NULL makes the whole `NOT IN` NULL. Here `id` is the primary key, never NULL, so it's safe; `NOT EXISTS` is the defensive alternative.

**Concurrency caveat.** Two admins moving two categories at the same time can each pass the check (each sees the old tree in its snapshot) and together create a cycle: A moves X under Y while B moves Y under X. Making this airtight needs locking (for example `LOCK TABLE categories IN SHARE ROW EXCLUSIVE MODE` before the check, or `SERIALIZABLE` isolation), which module 10 covers. Module 09 shows how to put the check in a trigger, so no code path can skip it.

---

## Exercise 12 ★★★: Top 3 and "Other"

```sql
WITH product_revenue AS (
    SELECT p.seller_id, p.id, p.title, sum(oi.quantity * oi.unit_price) AS revenue
    FROM order_items AS oi
    JOIN orders AS o ON o.id = oi.order_id
    JOIN products AS p ON p.id = oi.product_id
    WHERE o.status IN ('paid', 'shipped', 'delivered')
    GROUP BY p.seller_id, p.id, p.title
),
ranked AS (
    SELECT seller_id, title, revenue,
           row_number() OVER (PARTITION BY seller_id ORDER BY revenue DESC, id) AS rn
    FROM product_revenue
)
SELECT seller_id,
       least(rn, 4)                                             AS pos,
       CASE WHEN rn <= 3 THEN left(title, 28) ELSE 'Other' END AS label,
       count(*)                                                 AS products,
       sum(revenue)                                             AS revenue,
       round(100 * sum(revenue) / sum(sum(revenue)) OVER (PARTITION BY seller_id), 1) AS pct
FROM ranked
GROUP BY seller_id, least(rn, 4), CASE WHEN rn <= 3 THEN left(title, 28) ELSE 'Other' END
ORDER BY seller_id, pos;
```

```text
 seller_id | pos |            label            | products | revenue  | pct  
-----------+-----+-----------------------------+----------+----------+------
         1 |   1 | AeroBook 15 Pro Laptop      |        1 | 13491.00 | 32.4
         1 |   2 | Titan G17 Gaming Laptop     |        1 | 10999.95 | 26.4
         1 |   3 | AeroBook 13 Laptop          |        1 |  8492.00 | 20.4
         1 |   4 | Other                       |       12 |  8615.87 | 20.7
         2 |   1 | The Complete Sci-Fi Box Set |        1 |   553.00 | 33.4
         2 |   2 | Murder at Willow Lane       |        1 |   143.88 |  8.7
         2 |   3 | The Weeknight Pasta Book    |        1 |   110.00 |  6.6
         2 |   4 | Other                       |       11 |   849.55 | 51.3
         3 |   1 | Copper Sauté Pan            |        1 |   756.00 | 21.2
         3 |   2 | Dutch Oven 5.5L Enamel      |        1 |   654.00 | 18.4
         3 |   3 | Chef's Knife 20cm           |        1 |   356.00 | 10.0
         3 |   4 | Other                       |       10 |  1796.94 | 50.4
         4 |   1 | Merino Wool Hoodie          |        1 |   833.00 | 36.6
         4 |   2 | Heavyweight Pullover Hoodie |        1 |   472.00 | 20.7
         4 |   3 | Kids' Rainbow Hoodie        |        1 |   272.00 | 11.9
         4 |   4 | Other                       |        7 |   700.00 | 30.7
(16 rows)
```

**Reasoning.** The query has three layers, each impossible to merge with the next:

1. Aggregate items to products (`GROUP BY` product).
2. Rank products within the seller (`row_number`, a window over the aggregated rows). `row_number` with an `id` tie-breaker guarantees exactly 3 named products per seller.
3. Group again by a **bucket expression**: positions 1–3 keep their own bucket, everything from 4 on collapses into bucket 4. The share uses a window over that second aggregate: `sum(sum(revenue)) OVER (PARTITION BY seller_id)`.

The copper sauté pan (product 38) is archived but still had sales, so it ranks first for Casa Cucina. Reports about the past should include products that are no longer listed. The row totals per seller match the seller revenue from the theory (for example 13,491.00 + 10,999.95 + 8,492.00 + 8,615.87 = 41,598.82), which also confirms that `orders.total` agrees with the order items.

**Alternative.** `GROUPING SETS` can't express "top 3 plus the rest". A `UNION ALL` of "top 3" and "the rest" works, but needs the ranking twice. With `rank()` instead of `row_number()`, ties at position 3 would produce more than three named rows: that might be what the business wants, so ask.

---

## Exercise 13 ★★★: The best 30 days

```sql
WITH windows AS (
    SELECT seller_id, created_at,
           sum(total) OVER w AS revenue_30d,
           count(*)   OVER w AS orders_30d
    FROM orders
    WHERE status IN ('paid', 'shipped', 'delivered')
    WINDOW w AS (PARTITION BY seller_id ORDER BY created_at
                 RANGE BETWEEN CURRENT ROW AND interval '30 days' FOLLOWING)
)
SELECT DISTINCT ON (seller_id)
       seller_id,
       created_at::date                        AS window_start,
       (created_at + interval '30 days')::date AS window_end,
       orders_30d,
       revenue_30d
FROM windows
ORDER BY seller_id, revenue_30d DESC, created_at;
```

```text
 seller_id | window_start | window_end | orders_30d | revenue_30d 
-----------+--------------+------------+------------+-------------
         1 | 2024-08-06   | 2024-09-05 |          3 |    12256.00
         2 | 2024-05-01   | 2024-05-31 |          3 |      284.49
         3 | 2024-06-02   | 2024-07-02 |          2 |      933.99
         4 | 2025-03-20   | 2025-04-19 |          2 |      777.00
(4 rows)
```

**Reasoning.** A `RANGE` frame with an interval offset measures *time*, not rows: for each order, the frame contains every order of the same seller from that moment up to exactly 30 days later. Frames can look forward as well as back. Starting windows only at order timestamps is enough: any optimal 30-day window can be slid forward until its start hits an order without losing any revenue. `DISTINCT ON` then picks the best window per seller, with `created_at` as the tie-breaker for "earliest".

The bound is inclusive: an order exactly 30 days later is in the window. For "within 30 days, exclusive", use `interval '30 days' - interval '1 microsecond'`, or compare explicitly with a `LATERAL` subquery.

**Alternative.** A `LATERAL` subquery per order (`SELECT sum(total) FROM orders o2 WHERE o2.seller_id = o.seller_id AND o2.created_at BETWEEN o.created_at AND o.created_at + interval '30 days'`) gives the same result. It's easier to adapt to complicated conditions but does one lookup per order (fine with an index, quadratic without one). The window version is one sort and one pass.
