# Module 05: Solutions

Every solution was run on PostgreSQL 17 against `./project/bin/reset.sh 05` (the small dataset). Outputs show `NULL` as `∅`. None of the exercises changes data.

Files in this directory:

| File | What |
|---|---|
| [`seller_statement.sql`](seller_statement.sql) | Project milestone: the monthly seller statement and its `ROLLUP` version. |

Throughout, **revenue** means the sum of `orders.total` over orders with status `paid`, `shipped` or `delivered`, as in the theory and NOTES.md.

---

## Exercise 1 ★: Status breakdown

```sql
SELECT status,
       count(*)                                                    AS orders,
       round(100.0 * count(*) / (SELECT count(*) FROM orders), 1)  AS pct_orders,
       sum(total)                                                  AS value,
       round(100 * sum(total) / (SELECT sum(total) FROM orders), 1) AS pct_value
FROM orders
GROUP BY status
ORDER BY value DESC;
```

```text
  status   | orders | pct_orders |  value   | pct_value 
-----------+--------+------------+----------+-----------
 delivered |     91 |       75.8 | 42136.29 |      76.1
 paid      |      6 |        5.0 |  4864.97 |       8.8
 cancelled |     10 |        8.3 |  4538.90 |       8.2
 shipped   |      4 |        3.3 |  2093.93 |       3.8
 pending   |      5 |        4.2 |   947.95 |       1.7
 refunded  |      4 |        3.3 |   759.94 |       1.4
(6 rows)
```

**Reasoning.** The numerators are per-group aggregates, and the denominators are whole-table aggregates, computed once by scalar subqueries (module 04, section 10.1). `100.0 * count(*)` makes the division `numeric`: with `100 * count(*) / ...` everything would be `bigint`, and integer division would truncate (91 × 100 / 120 = 75, not 75.8).

Note the contrast in the last two columns: cancelled orders are 8.3 % of orders but 8.2 % of value, while paid orders (mostly TechTrove laptops in late June) are 5 % of orders and 8.8 % of value.

**Alternative.** Window functions (module 06) compute the denominator without a subquery: `count(*) * 100.0 / sum(count(*)) OVER ()`. Same result, and the idiomatic form once you know windows.

---

## Exercise 2 ★: Catalog size per category

```sql
SELECT c.id,
       c.name,
       count(p.id)                                     AS products,
       count(p.id) FILTER (WHERE p.status = 'active')  AS active,
       min(p.price)                                    AS cheapest,
       max(p.price)                                    AS dearest
FROM categories AS c
LEFT JOIN products AS p ON p.category_id = c.id
GROUP BY c.id
ORDER BY c.id;
```

```text
 id |      name       | products | active | cheapest | dearest 
----+-----------------+----------+--------+----------+---------
  1 | Electronics     |        0 |      0 |        ∅ |       ∅
  2 | Computers       |        2 |      2 |    39.99 |  129.00
  3 | Laptops         |        7 |      6 |    19.99 | 2199.99
  4 | Monitors        |        4 |      4 |   159.99 |  549.00
  5 | Audio           |        0 |      0 |        ∅ |       ∅
  6 | Headphones      |        5 |      4 |    29.99 |  229.00
  7 | Speakers        |        0 |      0 |        ∅ |       ∅
  8 | Home & Garden   |        0 |      0 |        ∅ |       ∅
  9 | Kitchen         |        1 |      1 |    42.00 |   42.00
 10 | Cookware        |        6 |      5 |    34.99 |  189.00
 11 | Cutlery         |        6 |      6 |    24.00 |   89.00
 12 | Garden          |        1 |      1 |   149.00 |  149.00
 13 | Planters        |        4 |      2 |    18.00 |   35.00
 14 | Books           |        0 |      0 |        ∅ |       ∅
 15 | Fiction         |        1 |      1 |    79.00 |   79.00
 16 | Science Fiction |        5 |      4 |     9.99 |   18.00
 17 | Mystery         |        5 |      4 |    10.00 |   15.00
 18 | Non-Fiction     |        0 |      0 |        ∅ |       ∅
 19 | Cooking         |        4 |      4 |    19.95 |   29.99
 20 | History         |        0 |      0 |        ∅ |       ∅
 21 | Clothing        |        1 |      1 |     0.00 |    0.00
 22 | T-Shirts        |        5 |      5 |    19.00 |   39.00
 23 | Hoodies         |        5 |      4 |    34.00 |  119.00
(23 rows)
```

**Reasoning.** `categories` is the preserved side of the `LEFT JOIN`, so all 23 categories appear. `count(p.id)` counts only matched products, giving 0 for the empty categories. `count(*)` would give 1 for each of them, because the `NULL`-padded row is still a row (section 2.3). `min`/`max` return `NULL` for those categories, which is right: an empty category has no cheapest product. `GROUP BY c.id` is enough to select `c.name`, thanks to the primary key (section 2.2).

This counts *direct* products only. Electronics shows 0 even though its subtree has 18 products. Module 04's exercise 5 showed how to roll products up a fixed-depth tree, and exercise 10 below does it with `ROLLUP`.

---

## Exercise 3 ★: Average basket

Wrong first (it's instructive):

```sql
SELECT round(avg(o.total), 2) AS avg_order_value_WRONG
FROM orders AS o
JOIN order_items AS i ON i.order_id = o.id
WHERE o.status IN ('paid', 'shipped', 'delivered');
```

```text
 avg_order_value_wrong 
-----------------------
                584.82
(1 row)
```

Correct: aggregate the lines per order first, then average the per-order numbers:

```sql
SELECT count(*)                 AS orders,
       round(avg(per_order.lines), 2)  AS avg_lines,
       round(avg(per_order.units), 2)  AS avg_units,
       round(avg(per_order.total), 2)  AS avg_order_value
FROM (
    SELECT o.id, o.total, count(*) AS lines, sum(i.quantity) AS units
    FROM orders AS o
    JOIN order_items AS i ON i.order_id = o.id
    WHERE o.status IN ('paid', 'shipped', 'delivered')
    GROUP BY o.id
) AS per_order;
```

```text
 orders | avg_lines | avg_units | avg_order_value 
--------+-----------+-----------+-----------------
    101 |      1.93 |      2.57 |          486.09
(1 row)
```

**Reasoning.** An average is a sum divided by a count, so it suffers from fan-out exactly like a sum: after the join, an order with four lines contributes its total four times and pulls the average towards big multi-line orders (which, at Bazaar, are the expensive laptop orders). The inner query fixes the grain at one row per order; the outer query averages over orders. `avg(o.total)` directly on `orders` would also give 486.09; the derived table is needed only for the line and unit averages.

**Alternative.** Without the derived table: `count(*)::numeric / count(DISTINCT o.id)` gives lines per order, and `sum(i.quantity)::numeric / count(DISTINCT o.id)` units per order. That works because both are "sum at line grain divided by number of orders", but it's easier to get wrong than the two-step version.

---

## Exercise 4 ★★: Monthly revenue per seller, zero-filled

```sql
SELECT s.id AS seller_id,
       s.shop_name,
       to_char(g.month, 'YYYY-MM')   AS month,
       count(o.id)                   AS orders,
       coalesce(sum(o.total), 0)     AS revenue
FROM sellers AS s
CROSS JOIN generate_series(timestamptz '2025-01-01', timestamptz '2025-06-01', interval '1 month') AS g (month)
LEFT JOIN orders AS o
       ON o.seller_id   = s.id
      AND o.created_at >= g.month
      AND o.created_at <  g.month + interval '1 month'
      AND o.status IN ('paid', 'shipped', 'delivered')
WHERE EXISTS (SELECT 1 FROM orders AS any_o WHERE any_o.seller_id = s.id)
GROUP BY s.id, g.month
ORDER BY s.id, g.month;
```

```text
 seller_id |    shop_name    |  month  | orders | revenue 
-----------+-----------------+---------+--------+---------
         1 | TechTrove       | 2025-01 |      1 |  996.00
         1 | TechTrove       | 2025-02 |      3 |  577.98
         1 | TechTrove       | 2025-03 |      1 |   39.99
         1 | TechTrove       | 2025-04 |      1 | 1837.00
         1 | TechTrove       | 2025-05 |      1 | 3455.00
         1 | TechTrove       | 2025-06 |      8 | 9492.93
         2 | Bookworm Corner | 2025-01 |      1 |   19.95
         2 | Bookworm Corner | 2025-02 |      2 |   72.96
         2 | Bookworm Corner | 2025-03 |      0 |       0
         2 | Bookworm Corner | 2025-04 |      2 |  139.98
         2 | Bookworm Corner | 2025-05 |      2 |  143.98
         2 | Bookworm Corner | 2025-06 |      3 |  151.39
         3 | Casa Cucina     | 2025-01 |      0 |       0
         3 | Casa Cucina     | 2025-02 |      0 |       0
         3 | Casa Cucina     | 2025-03 |      3 |  383.00
         3 | Casa Cucina     | 2025-04 |      2 |  362.00
         3 | Casa Cucina     | 2025-05 |      3 |  331.98
         3 | Casa Cucina     | 2025-06 |      3 |  233.00
         4 | Threadbare Co.  | 2025-01 |      0 |       0
         4 | Threadbare Co.  | 2025-02 |      0 |       0
         4 | Threadbare Co.  | 2025-03 |      1 |  376.00
         4 | Threadbare Co.  | 2025-04 |      2 |  482.00
         4 | Threadbare Co.  | 2025-05 |      0 |       0
         4 | Threadbare Co.  | 2025-06 |      3 |  228.00
(24 rows)
```

Self-check per seller:

```sql
SELECT seller_id, sum(total) AS revenue_h1_2025
FROM orders
WHERE status IN ('paid', 'shipped', 'delivered')
  AND created_at >= '2025-01-01' AND created_at < '2025-07-01'
GROUP BY seller_id
ORDER BY seller_id;
```

```text
 seller_id | revenue_h1_2025 
-----------+-----------------
         1 |        16398.90
         2 |          528.26
         3 |         1309.98
         4 |         1086.00
(4 rows)
```

**Reasoning.** This is section 10.4's grid: sellers × months via `CROSS JOIN`, facts via `LEFT JOIN` with every order condition in `ON`, `count(o.id)` and `coalesce(sum(...), 0)` for the empty cells. The `WHERE EXISTS` restricts the *preserved* side to sellers that have ever received an order (1–4), which is a filter on sellers, so it belongs in `WHERE`. Threadbare Co. has three zero months (January, February, May), Casa Cucina two, Bookworm Corner one (March), and only TechTrove sold something every month.

`to_char(g.month, 'YYYY-MM')` formats the month label. Grouping by `g.month` (the timestamp) and formatting only in `SELECT` keeps the sort chronological. Grouping and sorting by the `text` label would work too, here, because `YYYY-MM` sorts correctly as text, but it's a habit that breaks with formats like `Mon YYYY`.

**Alternative.** Aggregate the orders per (seller, month) in a derived table first and left join it to the grid, as in section 10.5. That shape is preferable when the facts need their own joins (for instance, units from `order_items`): those joins stay inside the derived table and can't fan out against the grid.

---

## Exercise 5 ★★: Monthly status pivot

```sql
SELECT to_char(date_trunc('month', created_at), 'YYYY-MM')                    AS month,
       sum(total)                                                              AS gross,
       coalesce(sum(total) FILTER (WHERE status = 'delivered'), 0)             AS delivered,
       coalesce(sum(total) FILTER (WHERE status IN ('paid', 'shipped')), 0)    AS in_transit,
       coalesce(sum(total) FILTER (WHERE status = 'pending'), 0)               AS pending,
       coalesce(sum(total) FILTER (WHERE status = 'cancelled'), 0)             AS cancelled,
       coalesce(sum(total) FILTER (WHERE status = 'refunded'), 0)              AS refunded,
       round(100 * coalesce(sum(total) FILTER (WHERE status IN ('cancelled', 'refunded')), 0)
                 / NULLIF(sum(total), 0), 1)                                   AS pct_lost
FROM orders
WHERE created_at >= '2025-01-01' AND created_at < '2025-07-01'
GROUP BY date_trunc('month', created_at)
ORDER BY date_trunc('month', created_at);
```

```text
  month  |  gross   | delivered | in_transit | pending | cancelled | refunded | pct_lost 
---------+----------+-----------+------------+---------+-----------+----------+----------
 2025-01 |  1414.90 |   1015.95 |          0 |       0 |     19.95 |   379.00 |     28.2
 2025-02 |  2637.91 |    650.94 |          0 |       0 |   1917.00 |    69.97 |     75.3
 2025-03 |  2833.98 |    798.99 |          0 |       0 |   2034.99 |        0 |     71.8
 2025-04 |  2820.98 |   2820.98 |          0 |       0 |         0 |        0 |      0.0
 2025-05 |  3930.96 |   3930.96 |          0 |       0 |         0 |        0 |      0.0
 2025-06 | 11053.27 |   3146.42 |    6958.90 |  947.95 |         0 |        0 |      0.0
(6 rows)
```

**Reasoning.** One `GROUP BY` month, and one `FILTER`ed sum per status column: each month's rows are read once, and every column comes from the same group, so the five status columns always add up to `gross` (check February: 650.94 delivered + 1,917.00 cancelled + 69.97 refunded = 2,637.91 gross). `coalesce(..., 0)` turns "no orders of this status this month" into 0.00, which is what a finance pivot should show. `NULLIF(sum(total), 0)` guards the division for a month without orders, which can't happen in this period but would in a zero-filled version.

February and March are the bad months: TechTrove's cancelled orders 73 (1,917.00) and 84 (1,816.99) alone are about two thirds of each month's gross order value.

**Alternative.** `crosstab()` from the `tablefunc` extension pivots generic row data into columns, but it needs the column list spelled out anyway and is clumsier than `FILTER`. For a fixed set of statuses, `FILTER` is the standard tool.

---

## Exercise 6 ★★: Repeat-customer rate

```sql
SELECT count(*)                                   AS buyers,
       count(*) FILTER (WHERE per_user.orders >= 2) AS repeat_buyers,
       round(100.0 * count(*) FILTER (WHERE per_user.orders >= 2) / count(*), 1) AS repeat_rate_pct
FROM (
    SELECT user_id, count(*) AS orders
    FROM orders
    WHERE status <> 'cancelled'
    GROUP BY user_id
) AS per_user;
```

```text
 buyers | repeat_buyers | repeat_rate_pct 
--------+---------------+-----------------
     20 |            18 |            90.0
(1 row)
```

Who are the two one-time buyers, and who ordered but isn't a buyer at all?

```sql
SELECT user_id,
       count(*) FILTER (WHERE status <> 'cancelled') AS real_orders,
       count(*) FILTER (WHERE status = 'cancelled')  AS cancelled_orders
FROM orders
GROUP BY user_id
HAVING count(*) FILTER (WHERE status <> 'cancelled') < 2
ORDER BY user_id;
```

```text
 user_id | real_orders | cancelled_orders 
---------+-------------+------------------
       1 |           1 |                0
       9 |           1 |                0
      18 |           0 |                1
(3 rows)
```

**Reasoning.** Two levels of aggregation: first per user (how many real orders?), then over users (how many have at least two?). An aggregate can't be nested directly inside another (`count(count(*))` is an error), so the inner level goes into a derived table. The second query shows the edge cases. Ethan Kim (18) ordered once and cancelled, so with cancelled orders excluded he isn't a buyer at all, and the base is 20 buyers, not the 21 users who ever placed an order. Users 1 (Alice Johnson) and 9 (Sofía Hernández) bought exactly once.

Whether refunded orders count as "real" is a business decision. Here they do: the customer did buy, and came back or didn't. A stricter definition (`status IN ('paid', 'shipped', 'delivered')`) would change the numbers, so write the definition next to the metric.

---

## Exercise 7 ★★: Top category per country

```sql
SELECT DISTINCT ON (s.country_code)
       s.country_code,
       c.name                           AS top_category,
       sum(i.quantity * i.unit_price)   AS revenue
FROM orders AS o
JOIN sellers     AS s ON s.id = o.seller_id
JOIN order_items AS i ON i.order_id = o.id
JOIN products    AS p ON p.id = i.product_id
JOIN categories  AS c ON c.id = p.category_id
WHERE o.status IN ('paid', 'shipped', 'delivered')
GROUP BY s.country_code, c.id
ORDER BY s.country_code, revenue DESC, c.id;
```

```text
 country_code | top_category | revenue  
--------------+--------------+----------
 DE           | Hoodies      |  1712.00
 GB           | Fiction      |   553.00
 IT           | Cookware     |  2228.94
 US           | Laptops      | 35315.93
(4 rows)
```

**Reasoning.** `DISTINCT ON` runs after `GROUP BY` (step 7 of the logical order, after the aggregates in step 4), so it can pick the first row per country from the *aggregated* rows, ordered by revenue. The `ORDER BY` starts with the `DISTINCT ON` key, then the choice (`revenue DESC`), then a tie-breaker (`c.id`).

Revenue here is computed from the lines (`quantity * unit_price`), because a category is a property of a product, not of an order, and an order can span categories. Summing `o.total` per category would be the fan-out bug: an order with a laptop and headphones would add its whole total to both categories. Line revenue summed over all categories equals order revenue, because `orders.total` is the sum of its lines.

Great Britain's top "category" is Fiction, a non-leaf category, because the Complete Sci-Fi Box Set (product 33) sits directly in it.

**Alternative.** Compute the per-(country, category) revenue in a derived table and pick the top with `LATERAL ... ORDER BY revenue DESC LIMIT 1` per country, or with `row_number()` (module 06). Only the window-function version handles "top 3 per country" as easily as "top 1".

---

## Exercise 8 ★★: Perfect customers

```sql
SELECT o.user_id,
       u.full_name,
       count(*)                                     AS orders,
       string_agg(o.id::text, ', ' ORDER BY o.id)   AS order_ids,
       min(o.created_at)::date                      AS first_order,
       max(o.created_at)::date                      AS last_order
FROM orders AS o
JOIN users AS u ON u.id = o.user_id
GROUP BY o.user_id, u.full_name
HAVING bool_and(o.status = 'delivered')
   AND count(*) >= 3
ORDER BY orders DESC, o.user_id;
```

```text
 user_id |   full_name   | orders |        order_ids        | first_order | last_order 
---------+---------------+--------+-------------------------+-------------+------------
      12 | Liam O'Connor |      6 | 17, 23, 26, 42, 98, 102 | 2024-03-31  | 2025-06-18
      19 | Mia Rossi     |      3 | 25, 44, 80              | 2024-05-05  | 2025-03-06
(2 rows)
```

**Reasoning.** `bool_and(status = 'delivered')` in `HAVING` keeps the users for whom *every* order was delivered, which is the "for all" question as one aggregate. `string_agg(o.id::text, ...)` needs a `text` argument, hence the cast, and the `ORDER BY` inside makes the list chronological (ids are chronological in this dataset). Grouping by `u.full_name` is redundant given `o.user_id = u.id`, but the functional-dependency rule only applies when you group by the primary key of the table the column comes from (`u.id`), and we grouped by `o.user_id`. Grouping by `u.id` instead would let you drop `u.full_name` from `GROUP BY`.

**Alternative.** `HAVING count(*) FILTER (WHERE o.status <> 'delivered') = 0`. Equivalent and portable, if less direct.

---

## Exercise 9 ★★: Typical order value per year

```sql
SELECT extract(year FROM created_at)                                              AS year,
       count(*)                                                                   AS orders,
       round(avg(total), 2)                                                       AS mean,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY total))::numeric, 2)    AS median,
       percentile_disc(0.9) WITHIN GROUP (ORDER BY total)                         AS p90_actual_order,
       max(total)                                                                 AS largest
FROM orders
WHERE status = 'delivered'
GROUP BY extract(year FROM created_at)
ORDER BY year;
```

```text
 year | orders |  mean  | median | p90_actual_order | largest 
------+--------+--------+--------+------------------+---------
 2024 |     59 | 504.61 |  88.49 |          1628.00 | 8940.00
 2025 |     32 | 386.38 | 106.99 |           996.00 | 3455.00
(2 rows)
```

**Reasoning.** `percentile_cont` returns `double precision`, so it's cast to `numeric` for `round(x, 2)` (there's no two-argument `round` for `double precision`). `percentile_disc(0.9)` returns an actual order total, a value that exists in the data, which is easier to explain to a business reader ("10 % of delivered orders were at least this big").

The mean is several times the median in both years: Bazaar's averages are dominated by a few big laptop orders. The median delivered order is around 90 to 110, while the mean is around 400 to 500.

---

## Exercise 10 ★★: Units by category, with subtotals

```sql
SELECT CASE WHEN GROUPING(root.name) = 1 THEN 'ALL'      ELSE root.name END AS root_category,
       CASE WHEN GROUPING(c.name)    = 1 THEN 'subtotal' ELSE c.name    END AS category,
       sum(i.quantity) AS units
FROM orders AS o
JOIN order_items AS i ON i.order_id = o.id
JOIN products    AS p ON p.id = i.product_id
JOIN categories  AS c ON c.id = p.category_id
LEFT JOIN categories AS parent ON parent.id = c.parent_id
LEFT JOIN categories AS gp     ON gp.id     = parent.parent_id
JOIN categories  AS root ON root.id = coalesce(gp.id, parent.id, c.id)
WHERE o.status = 'delivered'
GROUP BY ROLLUP (root.name, c.name)
ORDER BY GROUPING(root.name), root.name, GROUPING(c.name), units DESC;
```

```text
 root_category |    category     | units 
---------------+-----------------+-------
 Books         | Mystery         |    29
 Books         | Science Fiction |    28
 Books         | Cooking         |    13
 Books         | Fiction         |     7
 Books         | subtotal        |    77
 Clothing      | Hoodies         |    25
 Clothing      | T-Shirts        |    21
 Clothing      | Clothing        |     2
 Clothing      | subtotal        |    48
 Electronics   | Laptops         |    26
 Electronics   | Headphones      |    22
 Electronics   | Monitors        |     9
 Electronics   | Computers       |     5
 Electronics   | subtotal        |    62
 Home & Garden | Cookware        |    27
 Home & Garden | Cutlery         |    23
 Home & Garden | Kitchen         |     4
 Home & Garden | subtotal        |    54
 ALL           | subtotal        |   241
(19 rows)
```

**Reasoning.** The root category is the last non-`NULL` ancestor: `coalesce(gp.id, parent.id, c.id)` (module 04, section 15.2), joined once more to get its name. `ROLLUP (root.name, c.name)` produces detail rows per category, a subtotal per root, and a grand total. `GROUPING()` labels the rolled-up rows and drives the sort, so each subtotal follows its details and the grand total comes last. The subtotals match module 04's step 5 output (Books 77, Electronics 62, Home & Garden 54, Clothing 48), and the grand total, 241 units, matches `SELECT sum(quantity)` over delivered orders.

The labels need `GROUPING()` rather than `coalesce(c.name, 'subtotal')`: a category name is never `NULL` here, but the habit protects you when the grouped column is nullable.

---

## Exercise 11 ★★★: Fix the customer leaderboard

The bug: the join to `order_items` changes the grain to order lines. `count(o.id)` counts lines, and `sum(o.total)` adds each order's total once per line. Only `count(DISTINCT i.product_id)` is right, because it counts distinct values. The fix computes order-grain measures from `orders` and line-grain measures from `order_items`, each in its own derived table, and joins the summaries:

```sql
SELECT u.id,
       u.full_name,
       ord.orders,
       ord.spent,
       itm.distinct_products
FROM users AS u
JOIN (
    SELECT user_id, count(*) AS orders, sum(total) AS spent
    FROM orders
    WHERE status IN ('paid', 'shipped', 'delivered')
    GROUP BY user_id
) AS ord ON ord.user_id = u.id
JOIN (
    SELECT o.user_id, count(DISTINCT i.product_id) AS distinct_products
    FROM orders AS o
    JOIN order_items AS i ON i.order_id = o.id
    WHERE o.status IN ('paid', 'shipped', 'delivered')
    GROUP BY o.user_id
) AS itm ON itm.user_id = u.id
ORDER BY ord.spent DESC, u.id
LIMIT 5;
```

```text
 id |   full_name   | orders |  spent   | distinct_products 
----+---------------+--------+----------+-------------------
 13 | Olivia Brown  |     17 | 14564.95 |                25
 11 | Emma Schmidt  |     12 | 10496.36 |                19
  8 | Marcus Lee    |     19 |  4890.85 |                28
 12 | Liam O'Connor |      6 |  3472.91 |                15
 19 | Mia Rossi     |      3 |  3053.99 |                 3
(5 rows)
```

For comparison, the broken query's numbers for the same users:

```sql
SELECT u.id, count(o.id) AS orders, sum(o.total) AS spent, count(DISTINCT i.product_id) AS distinct_products
FROM users AS u
JOIN orders      AS o ON o.user_id  = u.id
JOIN order_items AS i ON i.order_id = o.id
WHERE o.status IN ('paid', 'shipped', 'delivered')
GROUP BY u.id
ORDER BY spent DESC
LIMIT 5;
```

```text
 id | orders |  spent   | distinct_products 
----+--------+----------+-------------------
 13 |     34 | 49446.91 |                25
 11 |     20 | 14837.81 |                19
 12 |     16 | 13372.71 |                15
  8 |     39 |  8281.10 |                28
 25 |     10 |  6107.91 |                10
(5 rows)
```

**Reasoning.** The broken version even gets the *ranking* wrong: Liam O'Connor (12) jumps ahead of Marcus Lee (8), because his orders have many lines, and Amelia Fischer (25) pushes Mia Rossi (19) out of the top five. Olivia Brown's "spent" is more than three times the truth. The summaries in the fix have one row per user each, so the final joins are one-to-one. Inner joins are fine here because both summaries contain exactly the users with at least one paid/shipped/delivered order.

**Alternatives.** Correlated scalar subqueries per user work for a short leaderboard. Note that "aggregate the lines per order first, then join to orders" (section 11.3), which works for units, does *not* work for distinct products per user: a product bought in two orders would be counted once per order. That's why this measure gets its own per-user derived table. Picking the right grain for each measure is the whole exercise.

---

## Exercise 12 ★★★: Who slipped in May?

```sql
SELECT s.id,
       s.shop_name,
       coalesce(sum(o.total) FILTER (WHERE o.created_at >= '2025-04-01' AND o.created_at < '2025-05-01'), 0) AS april,
       coalesce(sum(o.total) FILTER (WHERE o.created_at >= '2025-05-01' AND o.created_at < '2025-06-01'), 0) AS may
FROM sellers AS s
JOIN orders AS o ON o.seller_id = s.id
WHERE o.status IN ('paid', 'shipped', 'delivered')
  AND o.created_at >= '2025-04-01' AND o.created_at < '2025-06-01'
GROUP BY s.id
HAVING coalesce(sum(o.total) FILTER (WHERE o.created_at >= '2025-05-01' AND o.created_at < '2025-06-01'), 0)
     < coalesce(sum(o.total) FILTER (WHERE o.created_at >= '2025-04-01' AND o.created_at < '2025-05-01'), 0)
ORDER BY s.id;
```

```text
 id |   shop_name    | april  |  may   
----+----------------+--------+--------
  3 | Casa Cucina    | 362.00 | 331.98
  4 | Threadbare Co. | 482.00 |      0
(2 rows)
```

**Reasoning.** Both months are aggregated in one pass with `FILTER`, so each seller is one row with an `april` and a `may` column, and `HAVING` compares them. The `coalesce` is essential: Threadbare Co. had **no** May sales, so its May sum is `NULL`, and `NULL < 482.00` is `NULL`, which `HAVING` treats as false. Without `coalesce`, the seller with the worst drop, from 482.00 to nothing, would be missing. The `WHERE` restricts the scan to the two months, so the filters inside the aggregates only split them.

Note that `HAVING` can't use the output aliases `april` and `may` (section 3.1), hence the repeated expressions.

**Alternatives.** Two monthly derived tables joined on `seller_id` (a `FULL JOIN`, or `LEFT JOIN`s from `sellers`, to keep sellers missing from one month). Or, in module 06, a window function `lag(revenue) OVER (PARTITION BY seller_id ORDER BY month)` over a zero-filled monthly series, which generalizes to "every month that was worse than the previous one".

---

## Exercise 13 ★★★: Signup cohorts

```sql
SELECT to_char(q.quarter, 'YYYY-"Q"Q')                       AS cohort,
       count(u.id)                                          AS signups,
       count(u.id) FILTER (WHERE EXISTS (
           SELECT 1 FROM orders AS o
           WHERE o.user_id = u.id AND o.status <> 'cancelled'
       ))                                                   AS buyers,
       round(100.0 * count(u.id) FILTER (WHERE EXISTS (
           SELECT 1 FROM orders AS o
           WHERE o.user_id = u.id AND o.status <> 'cancelled'
       )) / NULLIF(count(u.id), 0), 1)                      AS conversion_pct
FROM generate_series(timestamptz '2023-01-01', timestamptz '2025-04-01', interval '3 months') AS q (quarter)
LEFT JOIN users AS u
       ON u.created_at >= q.quarter
      AND u.created_at <  q.quarter + interval '3 months'
GROUP BY q.quarter
ORDER BY q.quarter;
```

```text
 cohort  | signups | buyers | conversion_pct 
---------+---------+--------+----------------
 2023-Q1 |       6 |      5 |           83.3
 2023-Q2 |       0 |      0 |              ∅
 2023-Q3 |       5 |      4 |           80.0
 2023-Q4 |       4 |      3 |           75.0
 2024-Q1 |       4 |      2 |           50.0
 2024-Q2 |       5 |      3 |           60.0
 2024-Q3 |       4 |      3 |           75.0
 2024-Q4 |       1 |      0 |            0.0
 2025-Q1 |       0 |      0 |              ∅
 2025-Q2 |       1 |      0 |            0.0
(10 rows)
```

**Reasoning.** The cohort calendar comes from `generate_series` with a 3-month step, so quarters without signups (2023-Q2, 2025-Q1) appear with 0. `count(u.id)` gives 0 for them, and `NULLIF(count(u.id), 0)` turns the conversion into `NULL` ("undefined") instead of a division-by-zero error. The `FILTER (WHERE EXISTS (...))` counts users with at least one real order. A filter clause can contain any boolean expression, including a correlated subquery. `to_char(..., 'YYYY-"Q"Q')` prints the quarter label: `Q` is the quarter number, and `"Q"` in double quotes is a literal letter.

The 2023-Q1 cohort is the shop owners (users 1–6). Its one non-buyer is Haruto Sato, who owns Sakura Gadgets.

**Alternative.** Compute "is a buyer" once per user in a derived table (`SELECT u.id, u.created_at, EXISTS (...) AS is_buyer FROM users u`) and aggregate that, which avoids writing the subquery twice. Module 06's CTEs make that shape read even better.
