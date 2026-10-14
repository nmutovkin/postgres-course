# Module 03: Exercises

**Starting state.** Every exercise uses the small dataset:

```bash
./project/bin/reset.sh 04
./project/bin/psql.sh
```

That's the core schema plus [`0301_seed_small.sql`](../../project/db/0301_seed_small.sql). The notable rows are listed in [`project/db/NOTES.md`](../../project/db/NOTES.md). "Today" is 2025-06-30, so use fixed dates, not `now()`, when an exercise talks about time. Results assume the session time zone is UTC (the default).

Exercises that change data say so. Do them inside `BEGIN` ... `ROLLBACK` unless told otherwise, so that the next exercise starts from the same data. If something does get committed by accident, `./project/bin/reset.sh 04` restores everything.

Solutions: [solutions/README.md](solutions/README.md).

---

## Exercise 1 ★: Cheap reads

A customer wants a book from Bookworm Corner (seller 2) for under 15 euros. List the seller's **active** products under 15, cheapest first; for equal prices, alphabetically by title. Show id, title and price.

**Self-check:** 7 rows. The first is *Echoes of Andromeda* (9.99), the last *The Long Orbit* (14.99).

<details><summary>Hint</summary>

Three conditions joined with `AND`, and two sort keys.

</details>

---

## Exercise 2 ★: Where are our users?

Marketing wants to know which users are **not** on a `.com` email domain. List id, full name and the domain (the part after `@`) in lower case, ordered by domain and then id. Treat email addresses case-insensitively.

**Self-check:** 20 rows, starting with Amelia Fischer (`example.at`). User 17 (Zoë Martin) must **not** be in the list. If she is, look at her email address.

<details><summary>Hint</summary>

`split_part(email, '@', 2)` extracts the domain, and `lower()` normalises it. Compare on the lower-cased value, not the original.

</details>

---

## Exercise 3 ★: Page 3 of the catalogue

The shop shows active products 10 per page, cheapest first. Write the query for **page 3**. Make sure the order is fully deterministic, so that a product never appears on two pages or on none.

**Self-check:** 10 rows. The first is product 58 (*Self-Watering Planter*, 29.00) and the last is product 18 (*Wireless Ergonomic Mouse*, 39.99). Products 13 and 29 both cost 29.99. Your query must always put them in the same order.

<details><summary>Hint</summary>

Page 3 skips 20 rows. What do you add to `ORDER BY price` so that equal prices are always ordered the same way?

</details>

---

## Exercise 4 ★★: The first quarter of 2025

List every order placed in **Q1 2025** (January to March, UTC) that was not cancelled: id, the date, the abbreviated weekday name (`Mon`, `Tue`, ...), status and total, in chronological order. Then answer: how many of those orders were placed on a weekend? And how many would it be if "weekend" meant a Saturday or Sunday **in Berlin**?

**Self-check:** 14 rows, from order 68 (a Tuesday) to order 85. Three are weekend orders in UTC, but only two in Berlin.

<details><summary>Hint</summary>

Use a half-open range on `created_at`. `to_char(ts, 'Dy')` gives the weekday name, and `extract(isodow FROM ts)` gives 6 and 7 for Saturday and Sunday. For Berlin, convert first with `AT TIME ZONE 'Europe/Berlin'`. Look closely at order 79's time.

</details>

---

## Exercise 5 ★★: How fast do we deliver?

For every order **delivered** that was placed in 2025, show the id, the delivery time (`delivered_at - created_at`), the number of **full days** it took, and a speed label: `fast` under 5 days, `normal` from 5 up to and including 7 days, `slow` above 7 days. Show only the slowest three by full days, but include every order that ties with the third.

**Self-check:** 7 rows, all `slow`: two orders took 9 full days (94 and 88) and five took 8.

<details><summary>Hint</summary>

`extract(day FROM interval)` gives the days part of an interval. Compare intervals with intervals (`< interval '5 days'`). `FETCH FIRST 3 ROWS WITH TIES`. Think about whether you may add `id` to the `ORDER BY` here.

</details>

---

## Exercise 6 ★★: A `NULL` audit

Three questions from colleagues, each with a trap:

1. "How many products don't mention cotton in their description, in any capitalisation? Products without a description obviously don't mention it."
2. "How many categories are *not* direct children of Computers (category 2)?"
3. "Show the five products with the highest share of their stock reserved (`reserved / quantity` as a percentage, one decimal), highest first. Products without stock can't have a share, and they must not crowd out the real answers."

**Self-check:** (1) 58. (2) 21. (3) products 3 and 13 at 100.0 %, then 17 (14.3), 34 (10.0) and 4 (7.5).

<details><summary>Hint</summary>

(1) What does `NOT ILIKE` return for a `NULL` description? (2) What does `parent_id <> 2` return for a root category? (3) Division by zero, and where `NULL` sorts in descending order.

</details>

---

## Exercise 7 ★★: A price rise with nice endings

Threadbare Co. (seller 4) raises the prices of its active T-shirts (category 22) by 8 %, and wants every new price to end in **.99**: the smallest `x.99` that's at least the raised price. 19.00 becomes 20.99 (19.00 × 1.08 = 20.52), and 22.00 becomes 23.99. Also set `updated_at` to `2025-07-01 00:00+00`.

Do it with one `UPDATE` inside a transaction, have it return the id, title and new price, check the total of the new prices, and roll back. Then describe an input price for which your formula would *not* deliver a full 8 % rise.

**Self-check:** 5 rows updated. The new prices sum to 134.95, and `Linen Summer Shirt` goes from 39.00 to 42.99.

<details><summary>Hint</summary>

`ceil(x)` rounds up to the next whole number, so the next `.99` is related to `ceil(x) - 0.01`. Try it on a price whose raised value is exactly a whole number.

</details>

---

## Exercise 8 ★★: A stock delivery

A delivery arrives with these units:

| product_id | units |
|---|---|
| 5 | 10 |
| 14 | 25 |
| 25 | 40 |
| 62 | 100 |

Write **one** statement that adds the units to each product's stock and sets `updated_at` to `2025-07-01 08:00+00`. It must also work for a product that has no inventory row yet: then the row is created with the delivered quantity. Return product id, new quantity and reserved. (Use a transaction and roll back.)

Then answer: what happens if the delivery note lists product 25 twice? And if it lists a product id that doesn't exist?

**Self-check:** product 62 ends at 600 units, the other three at exactly the delivered amount (they were out of stock), and `SELECT sum(quantity) FROM inventory` goes from 1670 to 1845 inside the transaction. To test the "no inventory row" case, delete product 14's inventory row inside the same transaction first. The statement then inserts it with 25 units.

<details><summary>Hint</summary>

`INSERT ... ON CONFLICT (product_id) DO UPDATE SET quantity = inventory.quantity + EXCLUDED.quantity`. `EXCLUDED` can also supply `updated_at`.

</details>

---

## Exercise 9 ★★: URL slugs

Product pages need URL slugs like `the-lighthouse-keeper-s-secret-25`: the title in lower case, every run of characters other than `a`–`z` and `0`–`9` replaced by a single `-`, no leading or trailing `-`, then `-` and the product id. Accented letters must become their plain equivalent (`é` → `e`), not disappear. Write a query that returns id, title and slug for all products. How many slugs are longer than 30 characters?

**Self-check:** product 25 → `the-lighthouse-keeper-s-secret-25`; product 38 (*Copper Sauté Pan*) → `copper-saute-pan-38`, not `copper-saut-pan-38`; product 51 → `graphic-tee-ctrl-alt-del-51`; product 6 → `clearview-24-1080p-monitor-6`. 7 slugs are longer than 30 characters.

<details><summary>Hint</summary>

`regexp_replace(text, '[^a-z0-9]+', '-', 'g')` collapses runs. `trim(BOTH '-' FROM text)` removes leading and trailing dashes. `translate(text, 'éè...', 'ee...')` replaces characters one for one. The order of the steps matters: translate and lower-case first.

</details>

---

## Exercise 10 ★★★: Ship the paid orders

The warehouse ships every order that is still `paid` and was placed before 2025-06-28. Shipping an order means:

- its status becomes `shipped`, with `shipped_at = '2025-07-01 09:00+00'`;
- the stock it reserved is consumed: for each of its lines, both `quantity` and `reserved` of that product's inventory row go down by the line's quantity.

Write the two statements in a transaction, check the results, and roll back. The order update must be safe to run twice (the second run changes nothing).

**Self-check:** 5 orders are shipped (110 to 114) and 8 inventory rows change. Total `reserved` across all inventory goes from 24 to 15. Product 3 ends with quantity 0 and reserved 0, and product 62 with 499 and 1.

<details><summary>Hint</summary>

The first statement is a conditional `UPDATE ... RETURNING id`. The second is an `UPDATE inventory ... FROM order_items` (section 10.2) whose `WHERE` connects `order_items.product_id` to `inventory.product_id` and restricts `order_items.order_id` to the orders you just shipped. Why is it important that no product appears in two of these orders? Check before you trust the result.

</details>

---

## Exercise 11 ★★★: Catalogue clean-up

Delete the products that are either **archived**, or have been in **draft** for more than six months as of 2025-06-30, but never a product that appears in any order. Return what you deleted. (Transaction, then roll back.)

**Self-check:** exactly 2 products are deleted (59 and 61), 60 remain, and their inventory rows disappear with them. If your statement fails with a foreign key error, look at its `WHERE` clause again.

<details><summary>Hint</summary>

Precedence: `AND` binds tighter than `OR`. "Never ordered" can be written as `id NOT IN (SELECT product_id FROM order_items)`. Section 4.5 warned about `NOT IN`. Explain why it's safe *here*.

</details>

---

## Exercise 12 ★★★: Is every order's history consistent?

Each order's timestamps must agree with its status:

| status | `paid_at` | `shipped_at` | `delivered_at` |
|---|---|---|---|
| `pending` | null | null | null |
| `paid` | set | null | null |
| `shipped` | set | set | null |
| `delivered` | set | set | set |
| `cancelled` | any | null | null |
| `refunded` | set | any | set only if `shipped_at` is set |

Also, the timestamps that are set must be in order: `created_at` ≤ `paid_at` ≤ `shipped_at` ≤ `delivered_at`.

Write **one** query that returns every order that breaks any of these rules. Then, in a transaction, break three orders on purpose (for example, clear order 1's `delivered_at`, move order 2's `shipped_at` before its `paid_at`, and set order 115's status to `paid`), check that your query finds exactly those three, and roll back.

**Self-check:** on the clean data, the query returns 0 rows. With the three deliberate errors, it returns orders 1, 2 and 115.

<details><summary>Hint</summary>

A searched `CASE` on `status` can return the boolean "the timestamps fit this status", and `NOT` of that finds violations. Combine with the ordering rules using `OR`. What does `shipped_at < paid_at` return when one side is `NULL`, and does that cause false alarms?

</details>
