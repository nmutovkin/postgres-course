# Module 08: Exercises

**Starting state.** Unless an exercise says otherwise, start from the reference state that includes this module's milestone:

```bash
./project/bin/reset.sh 09
./project/bin/psql.sh
```

That's the state at the end of this module: enums, `email_address`, `products.attributes`/`tags` and `promotions` are all there. Exercises 3, 7 and 12 change the schema. Do them in order (12 builds on 3), and reset when you're done. Every other exercise is read-only, or rolls back.

"Today" in the small dataset is 2025-06-30, and all timestamps are UTC. Solutions: [solutions/README.md](solutions/README.md).

---

## Exercise 1 ★: Laptops with at least 16 GB, three ways

List the **active** products in the Laptops category (id 3) with at least 16 GB of RAM, using:

1. only the containment operator `@>` (no casts, no jsonpath),
2. a jsonpath filter,
3. `->>` with a cast.

Then explain why the containment version is the most fragile of the three, and why it is still worth knowing.

**Self-check:** all three return the same 4 rows: products 1, 2, 3 and 15. Product 5 (archived) and product 4 (4 GB) must not appear.

<details><summary>Hint</summary>

`@>` can only test equality. First find out which `ram_gb` values actually exist (`SELECT DISTINCT attributes -> 'ram_gb' ...`), then combine one containment test per qualifying value. `@> ANY (ARRAY[...]::jsonb[])` is a compact way to write an OR of containments.

</details>

---

## Exercise 2 ★: "You might also like"

On product 10's page, Bazaar wants to show other **active** products that share at least one tag with it. List them with the array of shared tags, the products sharing the most tags first (ties by id).

**Self-check:** 5 rows. The first is product 11 with shared tags `{anc,wireless}`. Products 1 and 25 share only `bestseller`.

<details><summary>Hint</summary>

`&&` finds candidates. To compute the intersection of two arrays there's no built-in operator, but `ARRAY(SELECT unnest(a) INTERSECT SELECT unnest(b))` does it.

</details>

---

## Exercise 3 ★: A new order status

Operations wants to put suspicious paid orders **on hold** before shipping. Add an `on_hold` status to `order_status`, between `paid` and `shipped`. Then write a status report with one row per status, in lifecycle order, with its position and the number of orders. Statuses without orders must appear with 0.

**Self-check:** 7 rows. `on_hold` is in position 3 with 0 orders, and the counts are 5, 6, 0, 4, 91, 10 and 4.

<details><summary>Hint</summary>

Remember the transaction caveat of `ADD VALUE`. For the report, `enum_range(NULL::order_status)` gives all labels in order, and `unnest ... WITH ORDINALITY` numbers them. Then `LEFT JOIN` the orders.

</details>

---

## Exercise 4 ★★: What's actually in `attributes`?

"Schemaless" data drifts. Build an inventory of the attribute keys per category: one row per category that has products, with the category name, the number of products, a JSON object mapping each key to the number of products in that category that have it, and an array of the **universal** keys (present in every product of the category). Order by category id.

**Self-check:** 16 rows. Laptops (7 products) has no universal key, because the laptop sleeve (product 16) shares none of the laptops' keys. Mystery's universal keys are `{author,language}` (product 28 lacks the others). Clothing (category 21, only the sticker pack) must appear with `{}` and `{}`.

<details><summary>Hint</summary>

`jsonb_object_keys` in a lateral join, then count per (category, key). Aggregate once more per category with `jsonb_object_agg` and `array_agg(...) FILTER (WHERE ...)`. Categories whose products have no keys at all disappear unless you start from the categories and use a `LEFT JOIN`.

</details>

---

## Exercise 5 ★★: Tags that travel together

Find all pairs of tags that appear together on **at least 3** products. Show each pair once (alphabetically ordered within the pair) with the number of products, most frequent first, then alphabetically.

**Self-check:** 7 pairs. The first is `book` + `sci-fi` (6 products), and the last is `garden` + `planter` (4).

<details><summary>Hint</summary>

Unnest `tags` twice for the same product (two lateral `unnest` calls) and keep only `a < b`. That removes both self-pairs and mirrored duplicates.

</details>

---

## Exercise 6 ★★: Monitor spec sheet with `JSON_TABLE`

Produce a comparison table for the Monitors category (id 4): product id, brand, size in inches, horizontal and vertical resolution as integers, total pixel count, refresh rate (assume **60 Hz** when the key is missing), number of ports (0 when none are listed), and whether a USB-C port is present. Order by pixel count, highest first (ties by id). Use a single `JSON_TABLE` for the extraction.

**Self-check:** 4 rows. The first is product 7 (3840 × 2160, 8,294,400 pixels, 60 Hz, 3 ports, USB-C). Product 9 has 1 port and USB-C. Products 6 and 8 have 0 ports.

<details><summary>Hint</summary>

`DEFAULT ... ON EMPTY` handles missing keys. A column's path can call an item method (`'$.ports.size()'`), and `EXISTS PATH` with a filter answers "is there a USB-C port?". The resolution is a string like `"1920x1080"`: `split_part` it after extraction.

</details>

---

## Exercise 7 ★★: A `country_code` domain

Both `sellers.country_code` and `addresses.country_code` hold ISO country codes, but only `sellers` checks the format (constraint `sellers_country_code_upper`). Create a domain `country_code` over `char(2)` that accepts exactly two upper-case letters, and convert both columns to it. Drop the now-redundant `CHECK` on `sellers`.

**Self-check:** `information_schema.columns` lists exactly two columns whose `domain_name` is `country_code`. `UPDATE sellers SET country_code = 'it' WHERE id = 3` and `UPDATE addresses SET country_code = 'D' WHERE id = 1` both fail with `value for domain country_code violates check constraint "country_code_format"`.

<details><summary>Hint</summary>

Do it in one transaction. Why does `'D'` fail, even though it's a single upper-case letter? Think about what `char(2)` does to a one-character value *before* the domain check runs.

</details>

---

## Exercise 8 ★★: Will this batch of promotions fit?

Marketing sends a batch of proposed promotions:

```sql
VALUES ('a', 10, 10.00, tstzrange('2025-06-14 00:00+00', '2025-06-21 00:00+00')),
       ('b',  1, 15.00, tstzrange('2025-07-01 00:00+00', '2025-07-08 00:00+00')),
       ('c',  1,  5.00, tstzrange('2025-07-05 00:00+00', '2025-07-12 00:00+00')),
       ('d', 47, 10.00, tstzrange('2025-07-01 00:00+00', '2025-07-31 00:00+00')),
       ('e', 37, 20.00, tstzrange('2025-07-01 00:00+00', '2025-07-15 00:00+00')),
       ('f', 24, 10.00, tstzrange('2024-12-25 00:00+00', '2025-01-01 00:00+00'))
```

(columns: `ref, product_id, discount_pct, active_during`). Before inserting anything, write a query that reports every conflict: a proposal that overlaps an **existing** promotion of the same product, or another **proposal** in the batch for the same product. Show the proposal, what it conflicts with, and the overlapping period. Report each conflicting pair of proposals only once.

Then insert the batch so that the conflicting proposals are skipped and the others are stored, in one statement, without your conflict query.

**Self-check:** the report has 3 rows: `a` conflicts with existing promotion 3 (overlap June 14–15), `c` with proposal `b`, and `d` with existing promotion 6 (the open-ended one). Proposals `e` and `f` are *adjacent* to existing promotions but don't overlap. The insert stores 3 rows (`b`, `e`, `f`). Run it inside `BEGIN ... ROLLBACK`.

<details><summary>Hint</summary>

Two joins with `&&` combined with `UNION ALL`: proposals × `promotions`, and proposals × proposals with `o.ref < n.ref`. The range intersection operator `*` gives the overlap. For the insert, remember which `ON CONFLICT` action works with exclusion constraints.

</details>

---

## Exercise 9 ★★: Promotion calendar for June 2025

For every day of June 2025, show how many products were on promotion at any time during that day, and which ones (a comma-separated list of product ids, ascending). Days without promotions must appear too.

**Self-check:** 30 rows. June 1–14 show `10,37`, June 15–19 show `37`, and June 20–30 show `37,47,48`. The sum of the daily counts is 66.

<details><summary>Hint</summary>

Generate one timestamp per day and turn each into a one-day range, `tstzrange(d, d + interval '1 day')`. Then `LEFT JOIN promotions` on `&&`. Use `count(pr.id)`, not `count(*)`.

</details>

---

## Exercise 10 ★★: Sale prices at a given moment

For the storefront as it looked at **2025-06-10 12:00 UTC**, list every **active** product with its current list price, the discount percentage in effect at that moment (if any), and the effective price, rounded to cents. Products on promotion come first.

**Self-check:** 54 rows. Exactly two are discounted: product 10 (229.00 → **183.20**) and product 37 (119.00 → **104.13**). The sum of all effective prices is **10,168.67** (against 10,229.34 at list price).

<details><summary>Hint</summary>

`LEFT JOIN promotions` with the join condition `active_during @> timestamptz '...'`. The exclusion constraint guarantees at most one match per product, so the join can't multiply rows. Use `coalesce` for the missing discount.

</details>

---

## Exercise 11 ★★★: Did customers get their discount?

The promotions table arrived later than the order data, and the seed's order lines were priced from `product_price_history` alone. Audit every order line. Compute the price the customer *should* have paid at the moment the order was created: the list price valid at that moment (the latest `product_price_history` row with `valid_from <= created_at`), minus the promotion active at that moment, rounded to cents. Report the lines whose `unit_price` differs, with the amount overcharged (quantity × difference).

**Self-check:** 4 lines: order 97 (product 37, 104.13 instead of 119.00), order 111 (products 47 and 48), and order 119 (product 48, quantity 2). Total overcharge: **37.67**. As a spot check of your price logic, product 1 at 2024-11-26 12:00 UTC should cost 899.10 (list price 999.00 since 2024-11-25, minus 10%), and product 34 at 2024-07-03 should cost 33.75.

<details><summary>Hint</summary>

"Latest row at or before a moment" is a `LATERAL` subquery with `ORDER BY valid_from DESC LIMIT 1` (module 04). Put the promotion in a `LEFT JOIN` with `@> o.created_at`. In module 09 you'll wrap exactly this logic in a function.

</details>

---

## Exercise 12 ★★★: Taking a status back

The `on_hold` experiment from exercise 3 is over. Order 120 was put on hold in the meantime: run `UPDATE orders SET status = 'on_hold' WHERE id = 120;` first. Now remove `on_hold` from `order_status` completely. Any order that is on hold must go back to `paid`, and `orders.status` must keep its default of `'pending'`. Do it atomically.

**Self-check:** `SELECT enum_range(NULL::order_status)` returns `{pending,paid,shipped,delivered,cancelled,refunded}`, order 120 is `paid`, `information_schema.columns` shows the default `'pending'::order_status`, and the type `order_status_old` (or whatever you called the temporary type) no longer exists. Then explain what would be different on a database with a view or materialized view that uses `orders.status`, such as the state at the start of module 10.

<details><summary>Hint</summary>

There's no `DROP VALUE`. Rename the old type out of the way, create the new type under the old name, then convert the column with `USING`: through `text`, and with a `CASE` for the label that no longer exists. Section 18 of the theory lists what has to happen to the default.

</details>

---

## Exercise 13 ★★★: Gaps between promotions

Consider the 12 months from 2024-07-01 to 2025-07-01 (half-open).

1. For each product that was on promotion at some point in that window, show the total promoted time inside the window, the **longest** stretch without a promotion, and the full set of unpromoted periods as a multirange.
2. Find the periods in the window when **no product at all** was on promotion, with their lengths.

**Self-check:** (1) 8 products. Product 10 was promoted for 21 days, and its longest gap is 181 days (2024-12-02 to 2025-06-01). Product 34's only promotion started on the first day of the window, so its unpromoted multirange is a single range of 358 days. (2) 4 periods, the longest being 140 days from 2024-07-08 to 2024-11-25.

<details><summary>Hint</summary>

Clip each promotion to the window with the intersection operator `*`, then `range_agg` per product. The unpromoted time is `window::tstzmultirange - promoted`. `unnest` a multirange to measure its pieces with `upper(r) - lower(r)`.

</details>
