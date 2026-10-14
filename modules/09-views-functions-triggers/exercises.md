# Module 09: Exercises

<!-- nav:start -->
> [Theory](README.md) · **Exercises** · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 08 Beyond Scalars](../08-beyond-scalars/README.md) · [Course home](../../README.md) · [10 Transactions and Concurrency →](../10-transactions-concurrency/README.md)
<!-- nav:end -->

**Starting state.** The exercises build on this module's milestone. Start from the reference state that includes it:

```bash
./project/bin/reset.sh 10
./project/bin/psql.sh
```

That's the state at the end of this module: `set_updated_at`, `audit_log` with `audit_trigger`, the `orders.total` triggers, `active_products` and `seller_sales_daily` all exist. Do the exercises **in order** in one database. Some later exercises interact with earlier ones (exercise 8 meets the trigger from exercise 3), and the self-checks assume that order. Exercise 10 deletes data, so reset when you're done.

"Today" in the small dataset is 2025-06-30, and all timestamps are UTC. Solutions: [solutions/README.md](solutions/README.md).

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

Turn the price logic of module 08's exercise 11 into a reusable function:

```sql
effective_price(p_product_id bigint, p_at timestamptz DEFAULT now()) RETURNS numeric
```

It returns the list price valid at `p_at` (from `product_price_history`), minus the promotion active at `p_at` (if any), rounded to cents. It returns NULL for an unknown product, or for a moment before the product's first price. Write it in SQL and choose its volatility label correctly.

**Self-check:** `effective_price(1, '2024-11-26 12:00+00')` = 899.10, `effective_price(10, '2025-06-10 12:00+00')` = 183.20, `effective_price(47, '2025-06-30 12:00+00')` = 13.30, `effective_price(999)` and `effective_price(1, '2020-01-01')` are NULL. Among the active products, exactly 3 have `effective_price(id, '2025-06-30 12:00+00') < price`.

<details><summary>Hint</summary>

A subquery that picks the latest history row (`ORDER BY valid_from DESC LIMIT 1`), `LEFT JOIN`ed to `promotions` on `active_during @> p_at`. Think about which volatility label is honest for a function that reads tables, and why `IMMUTABLE` would be a lie that the planner might believe.

</details>

---

## Exercise 2 ★: A view on a view

The warehouse team wants a `low_stock` view: active products with **3 or fewer** units available, showing id, seller id, shop name, title and available units. Build it **on top of `active_products`**. Then:

1. Look at the plan of `SELECT * FROM low_stock`. Where did the two views go?
2. Try `ALTER TABLE inventory ALTER COLUMN reserved TYPE bigint` and `DROP VIEW active_products` (each inside `BEGIN ... ROLLBACK`). Explain both outcomes.

**Self-check:** 4 rows: products 3, 13 and 25 with 0 available, and product 60 with 3. The `ALTER` fails with `cannot alter type of a column used by a view or rule`, and the `DROP VIEW` fails because `low_stock` depends on `active_products`.

<details><summary>Hint</summary>

Views are expanded by the rewriter before planning, so the plan only shows base tables. Dependencies are recorded column by column in `pg_depend`.

</details>

---

## Exercise 3 ★: No repricing archived products

Archived products must keep their last price, since it's what old invoices and the audit trail refer to. Write a trigger that rejects any `UPDATE` that **changes** the price of a product whose status (before the update) is `archived`. Use the custom SQLSTATE `BZ020` and a hint that tells the user what to do. Updates to other columns of archived products, and "updates" that set the price to the same value, must still work.

**Self-check:** `UPDATE products SET price = 699 WHERE id = 5` fails with `BZ020` (use `\set VERBOSITY verbose` to see the code). `UPDATE products SET price = price WHERE id = 5` and `UPDATE products SET title = 'AeroBook 13 (2023)' WHERE id = 5` succeed. Archiving product 1 and changing its price in the same statement is allowed. `UPDATE products SET price = price * 0.9 WHERE seller_id = 2` fails as a whole. Explain why, and fix the statement.

<details><summary>Hint</summary>

`BEFORE UPDATE OF price ... FOR EACH ROW WHEN (...)`: the `WHEN` clause can compare `OLD` and `NEW`, so the function body only needs to raise. Which product of seller 2 is archived?

</details>

---

## Exercise 4 ★★: Price history that keeps itself

`product_price_history` has been maintained by hand (the seed). Make it automatic: whenever a product's price **actually changes**, record the new price with `valid_from = now()`, and record the initial price of every newly inserted product. Use an `AFTER` trigger with `WHEN (OLD.price IS DISTINCT FROM NEW.price)` for updates.

Then test the awkward case: two price changes to the same product **in one transaction**. What does the primary key `(product_id, valid_from)` do to your trigger, and what should the history contain afterwards?

**Self-check:** starting from 71 history rows: after `UPDATE products SET price = 219.00 WHERE id = 10` (run twice), `UPDATE products SET title = title WHERE id = 10`, a transaction that sets product 11 to 209.00 and then 199.00, and an insert of a new product priced 59.00, there are **74** history rows (one each for products 10, 11 and the new product). Product 11's newest row says 199.00. This query returns 0:

```sql
SELECT count(*) FROM products p
WHERE p.price IS DISTINCT FROM (SELECT h.price FROM product_price_history h
                                WHERE h.product_id = p.id ORDER BY h.valid_from DESC LIMIT 1);
```

<details><summary>Hint</summary>

`now()` is the same for the whole transaction, so the second change in a transaction produces the same key as the first. `INSERT ... ON CONFLICT (product_id, valid_from) DO UPDATE` decides what "the price from this moment on" should be.

</details>

---

## Exercise 5 ★★: Breadcrumbs

Product pages show a breadcrumb such as *Books › Fiction › Science Fiction*. Write a **set-returning** SQL function `category_breadcrumb(p_category_id int)` that returns one row per level from the root down: `depth` (1 for the root), `category_id`, `name` and `slug`. Then write `category_path(p_category_id int, p_separator text DEFAULT ' > ') RETURNS text` on top of it, and use it to show the path of products 1, 17, 33, 47 and 62.

**Self-check:** `category_breadcrumb(16)` returns 3 rows, Books (1), Fiction (2), Science Fiction (3). `category_breadcrumb(21)` returns only Clothing, and `category_breadcrumb(999)` returns no rows. Product 1's path is `Electronics > Computers > Laptops`, product 33's is `Books > Fiction`, and product 62's is `Clothing`. 47 products sit at depth 3.

<details><summary>Hint</summary>

A recursive CTE (module 06) that walks *up* the tree from the given category, counting steps. The root has the most steps, so `depth = max(steps) OVER () - steps + 1`. `string_agg(name, sep ORDER BY depth)` builds the path.

</details>

---

## Exercise 6 ★★: Row counts of every table

Write a function `table_row_counts(p_schema text DEFAULT 'bazaar')` that returns, for every table and materialized view in the schema, its name, its **exact** row count (`count(*)`) and the planner's **estimate** (`pg_class.reltuples`). Use dynamic SQL safely.

**Self-check:** at this point in the exercise sequence, 15 rows. Among them: `products` 63 (62 plus the product from exercise 4), `product_price_history` 74, `seller_sales_daily` 96, and `orders` 120. Some estimates are −1. Explain what that means. `table_row_counts('nope')` returns no rows, and `table_row_counts('bazaar; DROP TABLE users')` does no harm.

<details><summary>Hint</summary>

Loop over `pg_class` joined to `pg_namespace` (`relkind IN ('r', 'p', 'm')`), and `EXECUTE format('SELECT count(*) FROM %I.%I', ...) INTO ...`. For `RETURNS TABLE`, assign the output variables and `RETURN NEXT`.

</details>

---

## Exercise 7 ★★: A ratings materialized view

Product listings need "★ 4.25 (4 reviews)" without aggregating `reviews` on every page view. Create a materialized view `product_rating_summary` with, per reviewed product: number of reviews, average rating (2 decimals), number of reviews rated 4 or 5, and the date of the latest review. Make it refreshable **without blocking readers**. Then show the laptops (category 3) from `active_products` with their rating, including unrated ones. Finally, inside a transaction, add a review for product 15 and demonstrate staleness and refresh.

**Self-check:** 44 rows, with 78 reviews in total. Product 24 leads with 4 reviews, average 4.25. The laptop list has 6 rows, with products 4 and 15 unrated. After the new review and a concurrent refresh, product 15 shows 1 review with average 5.00.

<details><summary>Hint</summary>

`REFRESH ... CONCURRENTLY` needs a unique index on plain columns. Use a `LEFT JOIN` from `active_products` so unrated products stay. User 8 hasn't reviewed product 15 yet, and `reviews` has a unique key on `(product_id, user_id)`.

</details>

---

## Exercise 8 ★★: A forgiving price import

Sellers upload price lists as JSON: `[{"seller_id": 1, "sku": "TT-LAP-001", "price": 949.00}, ...]`. Write a function `import_prices(p_rows jsonb)` that applies each line **independently**: valid lines are applied, and invalid ones are skipped. It returns one row per input line with `seller_id`, `sku`, `outcome` (`updated` or `failed`), and for failures the SQLSTATE and the error message. Test it inside `BEGIN ... ROLLBACK` with:

```json
[ {"seller_id": 1, "sku": "TT-LAP-001", "price": 949.00},
  {"seller_id": 1, "sku": "TT-LAP-005", "price": 699.00},
  {"seller_id": 2, "sku": "BW-SF-001",  "price": -1},
  {"seller_id": 2, "sku": "BW-XX-999",  "price": 10.00},
  {"seller_id": 3, "sku": "CC-CU-001",  "price": "cheap"},
  {"seller_id": 4, "sku": "TB-TS-001",  "price": 21.00} ]
```

**Self-check:** 6 result rows. Lines 1 and 6 are `updated`. Line 2 fails with `BZ020` (exercise 3's trigger: product 5 is archived), line 3 with `23514`, line 4 with `P0002` (unknown SKU, raised by your function), and line 5 with `22P02` (`invalid input syntax for type numeric: "cheap"`). Inside the transaction, product 1 costs 949.00 and product 47 costs 21.00.

<details><summary>Hint</summary>

One `BEGIN ... EXCEPTION WHEN OTHERS ... END` block per line, with `GET STACKED DIAGNOSTICS` in the handler. Be careful *where* each value is converted: a cast that fails in the loop's `FOR ... IN SELECT` is outside your exception block, and it aborts the whole import.

</details>

---

## Exercise 9 ★★: What happened to this column?

The audit log stores whole rows. Write a function `audit_history(p_table text, p_id bigint, p_column text)` that returns the history of **one column of one row**: audit id, time, operation, old value and new value (both `jsonb`), for every audit entry where that column actually changed, oldest first.

**Self-check:** after exercises 3 and 4, `audit_history('products', 11, 'price')` returns 2 rows (129.00 → 209.00, then 209.00 → 199.00). `audit_history('products', 5, 'title')` returns 1 row, and `audit_history('products', 5, 'price')` returns none (the rejected change was rolled back, and `price = price` wasn't audited). The new product from exercise 4 shows one `INSERT` row for `price`, with old value NULL.

<details><summary>Hint</summary>

`row_pk @> jsonb_build_object('id', p_id)`, and `old_row -> p_column IS DISTINCT FROM new_row -> p_column`. For an `INSERT`, `old_row` is NULL, so `old_row -> p_column` is NULL too. A SQL function is enough. How would you index `audit_log` for this lookup on a big table?

</details>

---

## Exercise 10 ★★★: Archive cancelled orders in batches

Cancelled orders older than **2025-03-01** clutter the hot tables. Create `orders_archive`, `order_items_archive` and `payments_archive` (same columns as the originals; `orders_archive` also gets `archived_at`). Then write a **procedure** `archive_cancelled_orders(p_before timestamptz, p_batch_size int DEFAULT 100)` that moves the matching orders with their items and payments into the archive, **committing after every batch**, and reporting progress with `RAISE NOTICE`. Run it with a batch size of 2.

**Self-check:** 4 batches (`{31,32}`, `{36,53}`, `{64,67}`, `{73}`). Afterwards the archive holds 7 orders, 15 items and 3 payments. `orders` has 113 rows, 3 cancelled orders remain (81, 82, 84), the archived totals are unchanged (order 73: 1917.00), and the audit log has 7 new `DELETE` rows for `orders`. Running the procedure a second time does nothing. Explain what happens if it's interrupted after batch 2.

<details><summary>Hint</summary>

`payments.order_id` has no `ON DELETE CASCADE`, so payments must move first. `order_items` cascades, and what does the `orders.total` delete trigger do while its orders are being deleted? Collect each batch's ids into an array (`array_agg ... LIMIT n`, with `FOR UPDATE` in the subquery) and use `= ANY (v_ids)` in every statement. Remember that `CALL` must not be inside an explicit transaction.

</details>

---

## Exercise 11 ★★★: Order status drives inventory

NOTES.md says that `inventory.reserved` equals the units in **pending and paid** orders, and that nothing maintains it when orders move on. Write a trigger on `orders` that keeps inventory in step with **status changes**:

- pending/paid → `shipped`: the stock leaves the warehouse, so `quantity` and `reserved` both decrease by the order's units;
- pending/paid → `cancelled`: the reservation is released, so `reserved` decreases;
- pending → paid: nothing changes;
- pending/paid → anything else (for example straight to `delivered`): reject with SQLSTATE `BZ030`.

Test it inside a transaction: ship order 110, cancel order 115, pay order 116, then try to deliver order 117.

**Self-check:** before the test, product 49 has stock 40 with 2 reserved, and product 62 has 500 with 2 reserved. Afterwards, product 49 has 39 with 0 reserved, and product 62 has 500 with 1 reserved. The invariant query (reserved = units in pending and paid orders, per product) finds **0** mismatches. Delivering order 117 fails with `BZ030`.

<details><summary>Hint</summary>

`AFTER UPDATE OF status ... FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status)`. Aggregate the order's lines per product (an order may in principle list a product twice) and apply them in one `UPDATE inventory ... FROM (...)`. The `CHECK (reserved <= quantity)` on inventory is your safety net.

</details>

---

## Exercise 12 ★★★: Row-level versus statement-level

The reference maintains `orders.total` with statement-level triggers. Measure why. In **one transaction** that you roll back at the end:

1. Create 200 empty orders, then insert 2,000 order lines (10 per order) with a single `INSERT ... SELECT`. Record the time, the number of `orders` updates the audit log recorded, and whether all totals are correct.
2. Replace the three statement-level triggers with **one row-level trigger** that does the same job, and repeat step 1.

Compare the time, the number of `orders` row versions written, and the audit log growth, and explain the difference.

**Self-check:** both variants produce correct totals (0 orders where `total` differs from the sum of their lines). With the statement-level triggers, the audit log records **200** `UPDATE`s of `orders` for the 2,000-line insert. With the row-level trigger, it records **2,000**. The row-level variant should be several times slower (on the course machine, about 60 ms against 240 ms for the 2,000-line insert; your times will differ, the ratio less so).

<details><summary>Hint</summary>

`DROP TRIGGER` is transactional, so the whole experiment can be rolled back. A row-level version needs `OLD` for `UPDATE`/`DELETE` and `NEW` for `INSERT`/`UPDATE`, and it can serve all three events from one trigger. Count with `SELECT count(*) FROM audit_log WHERE table_name = 'orders' AND operation = 'UPDATE'` before and after.

</details>
