# Module 05: Project milestone — the monthly seller statement

<!-- nav:start -->
> [Theory](README.md) · [Exercises](exercises.md) · **Project** · [Solutions](solutions/README.md)  
> [← 04 Joins and Subqueries](../04-joins-subqueries/README.md) · [Course home](../../README.md) · [06 Advanced SQL →](../06-advanced-sql/README.md)
<!-- nav:end -->

**Goal.** On the first of every month, Bazaar pays its shops. Each shop owner gets a statement for the previous months: how many orders they received, how many items they shipped, how much those orders were worth, how much was refunded, and the net amount Bazaar owes them. The finance team runs the same statement for all shops at once, with a subtotal per shop and a grand total at the bottom, and reconciles it against the bank. A statement that double counts an order, or silently leaves out a month with no sales, gets noticed by exactly the people you don't want to notice it.

In this milestone you write that statement as a psql script, in two versions: a plain one (one row per shop and month) and a `ROLLUP` one (with subtotals and a grand total).

Put your work in `workspace/05/seller_statement.sql`.

<!-- toc:start -->
**Contents**

- [Requirements](#requirements)
  - [Definitions](#definitions)
  - [Part 1: the statement](#part-1-the-statement)
  - [Part 2: the statement with subtotals](#part-2-the-statement-with-subtotals)
  - [Parameters](#parameters)
- [Acceptance checks](#acceptance-checks)
- [Reference solution](#reference-solution)

<!-- toc:end -->

## Requirements

### Definitions

| Column | Definition |
|---|---|
| `orders` | Number of the shop's orders in the month with status `paid`, `shipped`, `delivered` or `refunded`, i.e. orders that took money. Pending and cancelled orders never did, so they're excluded. |
| `items` | Total units (`order_items.quantity`) in those orders. |
| `gross` | Sum of `orders.total` of those orders. |
| `refunds` | Sum of `orders.total` of the `refunded` orders among them. |
| `net` | `gross - refunds`. This equals the revenue of NOTES.md (paid + shipped + delivered). |

An order, and its refund, are booked in the **UTC calendar month** of `orders.created_at`. (At this point the schema has no refund date. Module 07 adds `payments`, with which you could book refunds in the month they happened.)

### Part 1: the statement

- One row per **shop × month** for every month of the period and **every** shop, including shops and months without any orders, shown with `0` (not `NULL`) in every numeric column.
- Columns: `seller_id`, `shop_name`, `month` (formatted `YYYY-MM`), `orders`, `items`, `gross`, `refunds`, `net`.
- Sorted by seller id, then month.
- `items` must not cause double counting: `orders` and `gross` must be the same as they'd be without the items column. (Module 05, section 11: aggregate the lines per order first.)

### Part 2: the statement with subtotals

- The same columns, computed with `GROUP BY ROLLUP`, adding one **subtotal** row per shop (after that shop's months) and one **grand total** row at the very end.
- Label the rows using `GROUPING()`: the shop column shows the shop name on detail and subtotal rows and `ALL SHOPS` on the grand total, and the month column shows `YYYY-MM`, `subtotal` or `total`.

### Parameters

The period comes from two psql variables with defaults, so the file runs as-is and can be pointed at other periods:

- `period_start`: the first month, inclusive (default `2025-01-01`);
- `period_end`: the first month *after* the period, exclusive (default `2025-07-01`).

Use them as `:'period_start'::timestamptz`. A half-open range (`>= start AND < end`) avoids the classic "last day of the month at 23:59:59.999" bugs.

## Acceptance checks

```bash
./project/bin/reset.sh 05
docker exec -i course-pg psql -X -U postgres -d bazaar < workspace/05/seller_statement.sql
```

**Part 1, default period (2025-01 .. 2025-06).** 36 rows (6 shops × 6 months). Spot checks:

| Shop | Month | orders | items | gross | refunds | net |
|---|---|---|---|---|---|---|
| TechTrove | 2025-01 | 2 | 5 | 1375.00 | 379.00 | 996.00 |
| TechTrove | 2025-02 | 4 | 7 | 647.95 | 69.97 | 577.98 |
| TechTrove | 2025-06 | 8 | 14 | 9492.93 | 0 | 9492.93 |
| Bookworm Corner | 2025-03 | 0 | 0 | 0 | 0 | 0 |
| Threadbare Co. | 2025-05 | 0 | 0 | 0 | 0 | 0 |
| Maple & Pine | every month | 0 | 0 | 0 | 0 | 0 |

**Part 2, default period.** 43 rows (36 + 6 subtotals + 1 total). The last seven:

```text
      shop       |  month   | orders | items |  gross   | refunds |   net    
-----------------+----------+--------+-------+----------+---------+----------
 TechTrove       | subtotal |     17 |    35 | 16847.87 |  448.97 | 16398.90
 Bookworm Corner | subtotal |     10 |    27 |   528.26 |       0 |   528.26
 Casa Cucina     | subtotal |     11 |    24 |  1309.98 |       0 |  1309.98
 Threadbare Co.  | subtotal |      6 |    21 |  1086.00 |       0 |  1086.00
 Maple & Pine    | subtotal |      0 |     0 |        0 |       0 |        0
 Sakura Gadgets  | subtotal |      0 |     0 |        0 |       0 |        0
 ALL SHOPS       | total    |     44 |   107 | 19772.11 |  448.97 | 19323.14
```

(In your output each subtotal follows its shop's six months; the rows above are shown together for brevity.)

**The whole history reconciles with NOTES.md.** Run over every month of the dataset:

```bash
docker exec -i course-pg psql -X -U postgres -d bazaar -v period_start=2024-01-01 -v period_end=2025-07-01 \
  < workspace/05/seller_statement.sql | grep -E 'subtotal|total'
```

```text
 TechTrove       | subtotal |     33 |    73 | 42047.79 |  448.97 | 41598.82
 Bookworm Corner | subtotal |     30 |    85 |  1734.41 |   77.98 |  1656.43
 Casa Cucina     | subtotal |     24 |    60 |  3795.93 |  232.99 |  3562.94
 Threadbare Co.  | subtotal |     18 |    53 |  2277.00 |       0 |  2277.00
 Maple & Pine    | subtotal |      0 |     0 |        0 |       0 |        0
 Sakura Gadgets  | subtotal |      0 |     0 |        0 |       0 |        0
 ALL SHOPS       | total    |    105 |   271 | 49855.13 |  759.94 | 49095.19
```

The `net` subtotals are exactly the seller revenues in NOTES.md (41,598.82; 1,656.43; 3,562.94; 2,277.00), the 105 orders are 120 minus 5 pending minus 10 cancelled, and the refunds total 759.94 is the sum of the 4 refunded orders. Part 2 of this run has 115 rows (6 × 18 + 7).

**No double counting.** The items column must not change the other columns. An independent check of the full-history gross, without any join to `order_items`:

```sql
SELECT sum(total) AS gross, count(*) AS orders
FROM orders
WHERE status IN ('paid', 'shipped', 'delivered', 'refunded');
--   gross   | orders
-- ----------+--------
--  49855.13 |    105
```

and of the items:

```sql
SELECT sum(i.quantity) AS items
FROM order_items AS i
JOIN orders AS o ON o.id = i.order_id
WHERE o.status IN ('paid', 'shipped', 'delivered', 'refunded');
--  items
-- -------
--    271
```

## Reference solution

- [`solutions/seller_statement.sql`](solutions/seller_statement.sql): both parts, with the per-order pre-aggregation of the lines and `ROLLUP ((s.id, s.shop_name), g.month)`.

This module has no file in `project/db/`: the statement is a query, not schema. With module 06 you can restructure it with CTEs and add month-over-month changes with window functions. Module 09 precomputes a daily version of these numbers in the materialized view `seller_sales_daily`.
