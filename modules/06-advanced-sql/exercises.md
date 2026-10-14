# Module 06: Exercises

**Starting state.** Every exercise runs against:

```bash
./project/bin/reset.sh 06
```

Exercises that change data say so; wrap them in `BEGIN ... ROLLBACK` (or re-run the reset) so the next exercise starts from the reference state. "Revenue" and "paid orders" always mean orders with status `paid`, `shipped` or `delivered`. "Today" is 2025-06-30, and all timestamps are UTC.

Solutions: [solutions/README.md](solutions/README.md). Try each exercise before you look.

---

## Exercise 1 ★: Better than average

Using CTEs (no subqueries in `FROM` or `WHERE`), list the customers whose total spend on paid orders is **above the average customer's** spend, where the average is taken over customers who have at least one paid order. Show user id, full name, number of paid orders, total spent, and how many times the average they spent (2 decimals), biggest spender first.

**Self-check:** 6 rows. The average customer spent 2,454.76. The first row is user 13, Olivia Brown, with 17 orders, 14,564.95, and 5.93 times the average. The last row is user 26.

<details><summary>Hint</summary>

Three steps: spend per customer, the average of that, then the join/filter. The "average" CTE returns one row, so you can `CROSS JOIN` it.

</details>

---

## Exercise 2 ★: A customer's lifetime, row by row

For user 13's paid orders, in chronological order, show the order id, the date, the total, the **running** spend so far, and the running spend as a percentage of the customer's lifetime spend (1 decimal).

**Self-check:** 17 rows. Order 40 (8,940.00 on 2024-08-06) takes the running total from 2,631.48 to 11,571.48, which is 79.4% of the lifetime value. The last row shows 14,564.95 and 100.0.

<details><summary>Hint</summary>

Two windows: one with `ORDER BY created_at` (a running sum) and one with an empty `OVER ()` (the lifetime total). Think about whether ties in `created_at` could hurt you here, and how you'd protect against them.

</details>

---

## Exercise 3 ★: A weekly calendar, recursively

Without using `generate_series`, produce one row per week for Q2 2025, where weeks start on Monday: the first week starts on 2025-03-31 and the last on 2025-06-30. For each week, show the number of non-cancelled orders placed in that week and their total revenue. Weeks without orders must appear with 0 and 0.

**Self-check:** 14 rows. The week of 2025-05-05 has 0 orders; the week of 2025-06-23 has 11 orders worth 5,762.90; the week of 2025-06-30 has 1 order (90.00).

<details><summary>Hint</summary>

A recursive CTE whose anchor is `date '2025-03-31'` and whose recursive term adds 7 (`date + integer` adds days). Then `LEFT JOIN` the orders on a half-open interval `[week_start, week_start + 7)`, and count a column from `orders`, not `*`.

</details>

---

## Exercise 4 ★★: Products in the wrong place

Most products sit in a leaf category, but some were placed in a category that has children (NOTES.md mentions them). List those products with their full breadcrumb, root first, in **one** query that handles all of them at once (not one recursive query per product).

**Self-check:** 6 rows: products 17, 18, 33, 46, 60 and 62. Product 33's breadcrumb is `Books › Fiction`, and product 62's is just `Clothing`.

<details><summary>Hint</summary>

Put the product id into the anchor rows and carry it through the recursion, so each walk up the tree "remembers" which product it belongs to. Prepend each parent's name to an array (`c.name || names`). The finished walk is the row whose `parent_id` is NULL.

</details>

---

## Exercise 5 ★★: Expire stale pending orders, in one statement

Pending orders that are more than 24 hours old at `2025-06-30 12:00+00` must be cancelled, and the stock they reserved must be released (`inventory.reserved` goes down by the quantities in their items). Write **one statement** that does both and reports the cancelled order ids, the number of inventory rows changed, and the total units released. Run it inside `BEGIN ... ROLLBACK`.

**Self-check:** orders 115, 116 and 117 are cancelled; 5 inventory rows change; 8 units are released; total `reserved` over all inventory goes from 24 to 16. After the statement (still inside the transaction), `reserved` must equal the units in pending and paid orders for every product: write that check too, it must return 0 mismatches.

<details><summary>Hint</summary>

A data-modifying CTE: `UPDATE orders ... RETURNING id`, then compute the units to release per product from `order_items` joined to that CTE, then `UPDATE inventory ... FROM` that result. Why should the units be aggregated per product before the `UPDATE ... FROM`, even if no product appears twice today? And why must the check run as a separate statement?

</details>

---

## Exercise 6 ★★: The longest silence

For every pair of consecutive non-cancelled orders by the same customer, compute the number of days between them. Show the five longest gaps: user id, name, date of the earlier order, date of the later one, days.

**Self-check:** the longest gap is 362 days, by user 3 (Giulia Rossi), from 2024-03-01 to 2025-02-26. Fifth place is user 14 with 192 days.

<details><summary>Hint</summary>

`lag(created_at) OVER (PARTITION BY user_id ORDER BY created_at)`. Subtracting two `date`s gives an integer number of days. The first order of each customer has no predecessor; exclude it.

</details>

---

## Exercise 7 ★★: Unusually large orders

Fraud review wants to see paid orders from 2025 whose total is **more than 3 times the average of the same seller's previous three paid orders** (chronologically, excluding the order itself). Only flag orders that actually have three previous orders. Show the order id, seller, date, total, the previous-3 average and the ratio (1 decimal), highest ratio first.

**Self-check:** 7 rows. The first is order 87 (seller 1, 2025-04-09, 1,837.00) at 11.3 times the average of 162.99. The last is order 85 (seller 4) at 3.2.

<details><summary>Hint</summary>

A frame that ends *before* the current row: `ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING`. A `count(*)` over the same frame tells you whether three previous orders exist. Filter on 2025 only *after* the window has been computed. Why?

</details>

---

## Exercise 8 ★★: Bestsellers per department

For each **root** category (department), list the top 3 products by units sold in paid orders, counting products in any subcategory of the department. Products tied on units share a rank, and all tied products are shown, even if that means more than 3 rows for a department.

**Self-check:** 13 rows. Electronics has two products tied at rank 1 (products 2 and 10, 9 units each) and product 1 at rank 3. Home & Garden has product 43 at rank 1 and three products tied at rank 2. Clothing's winner is product 48 (11 units).

<details><summary>Hint</summary>

A recursive CTE that maps every category to its root (`category_id → root_id`): the anchor is the roots mapping to themselves, and each step passes the root down to the children. Then aggregate units per product, join, and rank with the function that gives ties the same rank *and* keeps "top 3" meaning three positions.

</details>

---

## Exercise 9 ★★: Selling streaks and dry spells

For each seller with sales, find (a) their **longest run of consecutive calendar months** with at least one paid order (if several runs tie for longest, show them all), and (b) every **gap**: the first and last month of each run of months without sales between their first and last selling month, with the number of empty months.

**Self-check:** (a) 7 rows; seller 1's longest streak is 11 months, from 2024-08 to 2025-06; seller 3 has three 4-month streaks. (b) 12 gaps; seller 4's longest gap runs from 2024-12 to 2025-02 (3 months).

<details><summary>Hint</summary>

(a) is gaps-and-islands (`month number − row_number()`), then rank the islands per seller by length. (b) is easier with `lead(month)`: a gap exists when the next selling month is more than one month later.

</details>

---

## Exercise 10 ★★: Sync a seller's catalog with MERGE

Bookworm Corner (seller 2) sends its complete active catalog as a feed of `(sku, price)`:

```sql
BEGIN;
CREATE TEMP TABLE catalog_feed (sku text PRIMARY KEY, price numeric(12,2) NOT NULL) ON COMMIT DROP;
INSERT INTO catalog_feed VALUES
    ('BW-SF-001', 15.99), ('BW-SF-002', 12.50), ('BW-SF-003', 18.00), ('BW-SF-004', 9.99),
    ('BW-SF-005', 16.00), ('BW-MY-001', 11.99), ('BW-MY-002', 13.49), ('BW-MY-004', 12.00),
    ('BW-CK-001', 27.50), ('BW-CK-002', 22.00), ('BW-CK-003', 24.50), ('BW-FIC-001', 79.00),
    ('BW-NF-001', 21.00);
```

Write one `MERGE` that:

- updates the price (and `updated_at`) of seller 2's products whose price in the feed differs;
- **archives** seller 2's *active* products that are missing from the feed (other statuses are left alone);
- doesn't touch other sellers' products and doesn't insert anything;
- returns the action, product id, SKU, new price and status of every changed row.

Then list the SKUs in the feed that match no product of seller 2, and finish with `ROLLBACK`.

**Self-check:** `MERGE 4`: two `UPDATE`s of price (products 19 and 29) and two archivings (products 26 and 32). The draft product 28 is also missing from the feed but stays `draft`; the archived product 23 is in the feed with an unchanged price and isn't touched. The unknown SKU is `BW-NF-001`. Afterwards seller 2 has 11 active, 3 archived and 1 draft product.

<details><summary>Hint</summary>

The `ON` condition must restrict the target to seller 2, but `ON` alone doesn't stop `WHEN NOT MATCHED BY SOURCE` from seeing every other seller's products: every target row that matched nothing is "not matched by source". The clause needs its own condition.

</details>

---

## Exercise 11 ★★★: Move a category without breaking the tree

Admins can move a category under a new parent. Write a **single `UPDATE` statement** that sets `parent_id = :new_parent` for category `:cat` only if the move keeps the tree acyclic, that is, only if `:new_parent` is not `:cat` itself and not anywhere in `:cat`'s subtree. It must report the moved row, or nothing if the move was refused. Use psql variables (`\set cat 15`, `\set new_parent 17`) and test it inside a transaction:

1. moving Fiction (15) under Mystery (17) must be refused (0 rows);
2. moving Cooking (19) under Kitchen (9) must succeed (1 row: `19 | 9`).

Then write a **whole-table cycle detector**: a query that returns every category from which walking *up* the tree never reaches a root. It must return 0 rows on the reference data. Prove it works by deliberately corrupting the tree in the same transaction (`UPDATE categories SET parent_id = 16 WHERE id = 14;`) and running it again.

**Self-check:** after the corruption, the detector reports 6 categories: 14, 15, 16, 17, 18 and 20 (all of Books except Cooking, which you moved to Kitchen in step 2).

<details><summary>Hint</summary>

The guard is a recursive CTE attached to the `UPDATE` (`WITH RECURSIVE subtree AS (...) UPDATE ... WHERE ... AND :new_parent NOT IN (SELECT id FROM subtree)`). For the detector, start a walk from *every* category at once (carry a `start_id`), walk up, and let the `CYCLE` clause flag revisits.

</details>

---

## Exercise 12 ★★★: Top 3 and "Other"

For each seller, show their three best products by revenue (from order items of paid orders: `quantity * unit_price`), followed by a single **"Other"** row that sums all their remaining products. For every row show the number of products it covers, the revenue, and its share of the seller's revenue (1 decimal). Order by seller, then position (1, 2, 3, Other last).

**Self-check:** 16 rows (4 sellers × 4). Seller 1's top product is the AeroBook 15 Pro Laptop with 13,491.00 (32.4%), and its "Other" row covers 12 products with 8,615.87 (20.7%). Each seller's revenue rows add up to the seller revenue from the module introduction, for example 1,656.43 for seller 2, whose "Other" row is the largest at 51.3%.

<details><summary>Hint</summary>

Rank products per seller with `row_number()`, then `GROUP BY` a *bucket* expression: position `least(rn, 4)` and a label that is the title for the top 3 and `'Other'` otherwise. The share needs a window function over an aggregate: `sum(sum(revenue)) OVER (PARTITION BY seller_id)`.

</details>

---

## Exercise 13 ★★★: The best 30 days

For each seller, find the **30-day period with the highest revenue**: a window that starts at one of the seller's paid orders and includes every paid order of that seller placed up to 30 days later (inclusive). Show the seller, window start and end dates, number of orders and revenue in the window. If two windows tie, take the earliest.

**Self-check:** 4 rows. Seller 1: from 2024-08-06 to 2024-09-05, 3 orders, 12,256.00. Seller 4: from 2025-03-20 to 2025-04-19, 2 orders, 777.00.

<details><summary>Hint</summary>

A `RANGE` frame with an interval offset can look *forward*: `RANGE BETWEEN CURRENT ROW AND interval '30 days' FOLLOWING`, ordered by `created_at`. Then pick the best row per seller (one of the top-N techniques with N = 1).

</details>
