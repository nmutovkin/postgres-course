# Module 05: Exercises

<!-- nav:start -->
> [Theory](README.md) · **Exercises** · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 04 Joins and Subqueries](../04-joins-subqueries/README.md) · [Course home](../../README.md) · [06 Advanced SQL →](../06-advanced-sql/README.md)
<!-- nav:end -->

**Starting state.** Every exercise runs against the small dataset:

```bash
./project/bin/reset.sh 05
```

The milestone adds no schema, so the exercises don't depend on it. None of them changes data. **Revenue** means the sum of `orders.total` over orders with status `paid`, `shipped` or `delivered`, and months are UTC calendar months, as in the theory. Use fixed dates, never `now()`: "today" in the dataset is 2025-06-30.

Solutions: [solutions/README.md](solutions/README.md).

---

<!-- toc:start -->
**Contents**

- [Exercise 1 ★: Status breakdown](#exercise-1--status-breakdown)
- [Exercise 2 ★: Catalog size per category](#exercise-2--catalog-size-per-category)
- [Exercise 3 ★: Average basket](#exercise-3--average-basket)
- [Exercise 4 ★★: Monthly revenue per seller, zero-filled](#exercise-4--monthly-revenue-per-seller-zero-filled)
- [Exercise 5 ★★: Monthly status pivot](#exercise-5--monthly-status-pivot)
- [Exercise 6 ★★: Repeat-customer rate](#exercise-6--repeat-customer-rate)
- [Exercise 7 ★★: Top category per country](#exercise-7--top-category-per-country)
- [Exercise 8 ★★: Perfect customers](#exercise-8--perfect-customers)
- [Exercise 9 ★★: Typical order value per year](#exercise-9--typical-order-value-per-year)
- [Exercise 10 ★★: Units by category, with subtotals](#exercise-10--units-by-category-with-subtotals)
- [Exercise 11 ★★★: Fix the customer leaderboard](#exercise-11--fix-the-customer-leaderboard)
- [Exercise 12 ★★★: Who slipped in May?](#exercise-12--who-slipped-in-may)
- [Exercise 13 ★★★: Signup cohorts](#exercise-13--signup-cohorts)

<!-- toc:end -->

## Exercise 1 ★: Status breakdown

For each order status, show the number of orders, its percentage of all orders, the summed order value, and its percentage of all order value. Percentages with one decimal. Largest value first.

**Self-check:** 6 rows. `delivered`: 91 orders, 75.8 %, 42,136.29, 76.1 %. Each percentage column adds up to 100 (give or take rounding). If the order percentages show only 0s, or whole numbers such as 75 and 8, check your integer division.

<details><summary>Hint</summary>

The denominators are whole-table totals: a scalar subquery computes each one once. Make the arithmetic `numeric` before dividing.

</details>

---

## Exercise 2 ★: Catalog size per category

For **every** category, show the number of products directly in it, how many of those are active, and the cheapest and dearest price.

**Self-check:** 23 rows. Laptops (3): 7 products, 6 active, 19.99 to 2199.99. Seven categories show 0 products and `∅` prices: Speakers (7), History (20), Audio (5), Non-Fiction (18) and the roots Electronics, Home & Garden and Books. No category shows 1 product where it should show 0.

<details><summary>Hint</summary>

Section 2.3: which `count` stays at 0 for a category without products?

</details>

---

## Exercise 3 ★: Average basket

For orders that are paid, shipped or delivered, compute: the number of orders, the average number of lines per order, the average number of units per order, and the average order value. Two decimals each.

**Self-check:** 101 orders; 1.93 lines, 2.57 units, 486.09 per order. If your average order value is 584.82, you averaged over order lines, not orders.

<details><summary>Hint</summary>

Get one row per order first (with its line count and unit sum), then average those rows.

</details>

---

## Exercise 4 ★★: Monthly revenue per seller, zero-filled

For each seller that has ever received an order, show the number of revenue orders and the revenue for every month from January to June 2025, **including months with no sales** as 0 orders and 0 revenue. Label months as `2025-01` and so on.

**Self-check:** 24 rows (4 sellers × 6 months). Threadbare Co. has zeros in 2025-01, 2025-02 and 2025-05. Bookworm Corner has 0 in 2025-03. The six months of each seller add up to their H1 revenue: TechTrove 16,398.90, Bookworm Corner 528.26, Casa Cucina 1,309.98, Threadbare Co. 1,086.00.

<details><summary>Hint</summary>

Sellers `CROSS JOIN` a `generate_series` of months, then `LEFT JOIN orders` with **all** order conditions in `ON`. "Has ever received an order" is a condition on sellers.

</details>

---

## Exercise 5 ★★: Monthly status pivot

Finance wants one row per month of H1 2025 with the gross order value (all statuses) and one column each for the value that is delivered, in transit (`paid` + `shipped`), pending, cancelled, and refunded, plus the percentage of gross that was lost (cancelled + refunded). Show 0, not `NULL`, for empty cells.

**Self-check:** 6 rows. In every row, the five status columns add up to `gross`. February: gross 2,637.91, cancelled 1,917.00, refunded 69.97, lost 75.3 %. June: 6,958.90 in transit, 947.95 pending, 0 lost.

<details><summary>Hint</summary>

One `GROUP BY` month and a `FILTER`ed `sum` per column. Guard the percentage against division by zero even if this period doesn't need it.

</details>

---

## Exercise 6 ★★: Repeat-customer rate

Among users with at least one non-cancelled order ("buyers"), what share have two or more non-cancelled orders? Show buyers, repeat buyers and the rate in percent. Then list the users who placed orders but have fewer than two non-cancelled ones, with their counts of non-cancelled and cancelled orders.

**Self-check:** 20 buyers, 18 repeat buyers, 90.0 %. The second query returns 3 users: 1 and 9 (one order each) and 18 (zero real orders, one cancelled).

<details><summary>Hint</summary>

Two levels of aggregation (per user, then over users) need a derived table: aggregates can't be nested.

</details>

---

## Exercise 7 ★★: Top category per country

For each seller country, find the product category (the product's own category, whatever its level) with the highest revenue, computed from the order lines (`quantity * unit_price`) of paid, shipped and delivered orders. One row per country, ties broken by the lower category id.

**Self-check:** 4 rows: DE Hoodies 1,712.00; GB Fiction 553.00; IT Cookware 2,228.94; US Laptops 35,315.93.

<details><summary>Hint</summary>

`DISTINCT ON (country)` after a `GROUP BY country, category`, ordered by revenue. Why would summing `orders.total` per category be wrong?

</details>

---

## Exercise 8 ★★: Perfect customers

Find users who placed at least 3 orders and whose orders were **all** delivered. Show their name, number of orders, the order ids as one comma-separated string in id order, and the dates of their first and last orders.

**Self-check:** 2 rows: Liam O'Connor (6 orders: `17, 23, 26, 42, 98, 102`) and Mia Rossi (3 orders: `25, 44, 80`).

<details><summary>Hint</summary>

A "for all rows of the group" condition is one aggregate. `string_agg` needs `text` and accepts an `ORDER BY` inside.

</details>

---

## Exercise 9 ★★: Typical order value per year

For delivered orders, per calendar year: the number of orders, the mean, the median (two decimals), the 90th percentile as an **actual order total** from the data, and the largest order.

**Self-check:** 2 rows. 2024: 59 orders, mean 504.61, median 88.49, p90 1,628.00, largest 8,940.00. 2025: 32 orders, mean 386.38, median 106.99, p90 996.00.

<details><summary>Hint</summary>

`percentile_cont` interpolates and returns `double precision` (cast before rounding); `percentile_disc` returns a value that exists.

</details>

---

## Exercise 10 ★★: Units by category, with subtotals

For delivered orders, report the units sold per product category, with a subtotal per **root** category and a grand total, in one query. Label subtotal rows `subtotal` and the grand-total row `ALL`. Sort each root's categories by units (descending), with its subtotal right after them and the grand total last.

**Self-check:** 19 rows. Root subtotals: Books 77, Clothing 48, Electronics 62, Home & Garden 54. Grand total 241. Products sitting directly in a root (the sticker pack in Clothing) or a second-level category (Computers, Fiction, Kitchen) must be counted under the right root.

<details><summary>Hint</summary>

Module 04 (section 15.2) shows how to find a product's root with two self joins and `coalesce`. Then `ROLLUP (root, category)` and `GROUPING()` for labels and sorting.

</details>

---

## Exercise 11 ★★★: Fix the customer leaderboard

A colleague's leaderboard of the top 5 customers:

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

It says Olivia Brown (13) placed 34 orders and spent 49,446.91. Explain which of the three measures are wrong and why, and write a corrected query (with names) that gets all three right.

**Self-check:** corrected top 5 by spend: 13 Olivia Brown (17 orders, 14,564.95, 25 distinct products), 11 Emma Schmidt (12, 10,496.36, 19), 8 Marcus Lee (19, 4,890.85, 28), 12 Liam O'Connor (6, 3,472.91, 15), 19 Mia Rossi (3, 3,053.99, 3).

<details><summary>Hint</summary>

At what grain does each measure live? Compute order-grain measures from `orders`, and the product measure from the lines, each in its own per-user derived table, then join the summaries.

</details>

---

## Exercise 12 ★★★: Who slipped in May?

Find the sellers whose revenue in May 2025 was lower than in April 2025. Show both months' revenue. A seller with sales in April and none in May has certainly slipped. Don't use window functions.

**Self-check:** 2 rows: Casa Cucina (362.00 → 331.98) and Threadbare Co. (482.00 → 0). If Threadbare Co. is missing, think about what `NULL < 482.00` evaluates to.

<details><summary>Hint</summary>

Compute both months in one pass with two `FILTER`ed sums per seller, and compare them in `HAVING`.

</details>

---

## Exercise 13 ★★★: Signup cohorts

Group users into quarterly cohorts by sign-up date, from 2023-Q1 to 2025-Q2, **including quarters in which nobody signed up**. For each cohort: signups, buyers (users with at least one non-cancelled order) and the conversion rate in percent (`NULL` for an empty cohort). Label cohorts like `2024-Q3`.

**Self-check:** 10 rows. 2023-Q1: 6 signups, 5 buyers, 83.3 %. 2023-Q2 and 2025-Q1: 0 signups, `∅` conversion. 2024-Q4 and 2025-Q2: 1 signup each, 0 buyers, 0.0 %.

<details><summary>Hint</summary>

`generate_series` with a 3-month step gives the quarters; left join users by sign-up range. `FILTER (WHERE EXISTS (...))` can count users with a qualifying order. `to_char(ts, 'YYYY-"Q"Q')` formats the label.

</details>
