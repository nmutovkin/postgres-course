# Module 07: Exercises

**Starting state.** Unless an exercise says otherwise, start from the state *after* this module's milestone, which is the reference schema v2 with its data:

```bash
./project/bin/reset.sh 08
```

If you completed the milestone with the same table and column names as the reference, your own database works too. Exercises that change the schema or data should run inside `BEGIN ... ROLLBACK` (or be followed by a fresh reset).

Exercises 4 and 11 use a spreadsheet table that you create with [`solutions/purchase_sheet.sql`](solutions/purchase_sheet.sql) (it contains only the `CREATE TABLE` and `INSERT`s, no solutions):

```bash
docker exec -i course-pg psql -X -U postgres -d bazaar < modules/07-data-modeling/solutions/purchase_sheet.sql
```

Solutions: [solutions/README.md](solutions/README.md).

---

## Exercise 1 ★: Keys the data can't prove

Two columns look like they could be keys:

1. `payments (provider, provider_ref)`: the provider's transaction reference.
2. `categories (parent_id, name)`: a category's name among its siblings.

For each: (a) write a query that checks whether the current data has duplicates; (b) decide, as a business rule, whether it *should* be a key, and why; (c) if yes, add the constraint inside a transaction and prove it rejects a duplicate. Then explain why `categories.name` alone, which is also unique in the current data, should **not** be a key.

**Self-check:** both checks find 0 duplicates in the data. Adding `UNIQUE (provider, provider_ref)` succeeds and then rejects a second payment with `('stripe', 'st_2698a6c494')` (an existing reference) with error `23505`. For `(parent_id, name)`, think about what happens to two *root* categories with the same name.

<details><summary>Hint</summary>

A duplicate check is `GROUP BY` the candidate columns `HAVING count(*) > 1`. For the roots: in a unique constraint, NULLs are distinct from each other by default. Look up what `UNIQUE NULLS NOT DISTINCT` (PostgreSQL 15+) changes.

</details>

---

## Exercise 2 ★: What happened to prices?

Using only `product_price_history` and `products`, list every product whose price changed at least once, with: number of changes, first price, latest price, and the percentage change from first to latest (1 decimal), biggest increase first. Then write a consistency check between the history table and the current price: for every product, the latest history price must equal `products.price`, and every product must have at least one history row.

**Self-check:** 8 products changed price. The Dutch Oven (37) changed twice, from 99.00 to 119.00 (+20.2%); the AeroBook 13 Laptop (1) and Salt, Fat, Flame (29) both dropped 9.1%. The consistency check reports 62 products and 0 mismatches.

<details><summary>Hint</summary>

`(array_agg(price ORDER BY valid_from))[1]` is the first price of a group; order descending for the latest. For the check, a `LEFT JOIN LATERAL (... ORDER BY valid_from DESC LIMIT 1) ON true` keeps products without history, and `IS DISTINCT FROM` treats a missing history row as a mismatch.

</details>

---

## Exercise 3 ★: Review this design

A developer proposes this table for a new feature: "Users can create named wishlists ('Birthday', 'Kitchen ideas'), each public or private, and add products to them. We count how often a wishlist was shared."

```sql
CREATE TABLE Wishlist (
    WishlistID      serial PRIMARY KEY,
    user_email      varchar(100),
    owner_name      varchar(100),
    name            varchar(50),
    product_ids     text,            -- '12,47,51'
    product_titles  text,            -- 'StudioPro..., Organic Cotton Tee..., Graphic Tee...'
    isPublic        int,             -- 0 or 1
    share_count     float,
    created         date
);
```

(a) List every flaw you can find, naming the principle each one violates (normal form, key choice, type choice, naming, missing constraint). (b) Write the corrected schema and prove it works by creating user 8's public "Birthday" wishlist with products 12, 47 and 51, inside a transaction. (c) Then turn the critical eye on the reference schema v2 (`project/db/0701_schema_modeling.sql`): find at least three weaknesses or deliberate simplifications, and say how you'd fix each.

**Self-check:** (a) there are at least 10 distinct flaws. (b) your design needs two tables; a second wishlist named "Birthday" for user 8 must fail; listing the wishlist's products returns 3 rows with their current titles. (c) one weakness is mentioned in section 7.6 of the theory, another in section 8.2.

<details><summary>Hint</summary>

Go column by column, and ask for each: is this a fact about the wishlist, or about something else? Is this the right type? Can it be NULL, and should it? What stops two rows from contradicting each other?

</details>

---

## Exercise 4 ★★: Dissect a spreadsheet

Casa Cucina (seller 3) has kept its stock purchases in a spreadsheet, imported as the table `purchase_sheet` (create it with `solutions/purchase_sheet.sql`, see the top of this page). Each row is one line of a purchase order (PO). The business rules, according to the seller:

- a PO comes from one supplier, is delivered to one warehouse and has one date;
- a supplier is identified by its email and has one name and one country;
- a warehouse code identifies a warehouse in one city;
- the SKU is Casa Cucina's own Bazaar SKU; the title is that product's title;
- `unit_cost` is the cost agreed for that line of that PO;
- a PO contains a SKU at most once.

(a) Write down the functional dependencies and the candidate key of `purchase_sheet`. Which normal forms does it violate, and through which dependencies? (b) Write **one query** that tests each of the FDs below against the data and reports, for each, the number of groups that violate it: `po_number → (po_date, supplier_email, warehouse_code)`, `supplier_email → supplier_name`, `supplier_email → supplier_country`, `warehouse_code → warehouse_city`, `sku → product_title`, `sku → unit_cost`, `(po_number, sku) → (quantity, unit_cost)`. (c) Explain each violation you find: is the data wrong, or is the "dependency" not a business rule? (d) Give one concrete example each of an update, an insertion and a deletion anomaly in this table.

**Self-check:** 7 result rows. Exactly two FDs are violated: `supplier_email → supplier_name` (1 group) and `sku → unit_cost` (2 groups). One of them is a data-quality problem, the other is not a rule at all.

<details><summary>Hint</summary>

For an FD X → Y, the violating groups are `GROUP BY X HAVING count(DISTINCT Y) > 1`. For several Y columns, `count(DISTINCT (a, b, c))` counts distinct row values. Combine the checks with `UNION ALL`, each as a subquery counting its violating groups.

</details>

---

## Exercise 5 ★★: The catalogue on New Year's Eve

Using `product_price_history`, list the **active** products whose price at the end of 2024 (that is, the price valid just before `2025-01-01 00:00 UTC`) differs from today's price, or that weren't listed at all yet. Show the id, title, the price then (NULL if not listed yet) and the price today.

**Self-check:** 4 rows: products 10 (249.00 → 229.00), 19, 37 (109.00 → 119.00) and 52. Product 29, whose price also changed, is *not* in the list. Why? (Look at when it changed.)

<details><summary>Hint</summary>

"Valid just before midnight" is `valid_from < '2025-01-01 00:00+00'`, the half-open interval again. A product with no history row before that moment must still appear, so the lateral subquery needs `LEFT JOIN LATERAL ... ON true`, and the comparison must treat NULL as different (`IS DISTINCT FROM`).

</details>

---

## Exercise 6 ★★: Did the price change hurt sales?

For every product that changed price, list each **price period** (price, valid from, valid to, with NULL for the current period) and the number of paid orders, units and revenue (`quantity * unit_price`) of that product **within the period**. Paid means `paid`, `shipped` or `delivered`. Periods with no sales must appear with zeros.

**Self-check:** 17 periods for 8 products. Product 37 has three periods with 2, 1 and 2 orders. The ClearView 27" 4K Monitor (7) sold 3 units at 399.00 and none since its price dropped to 379.00 on 2024-09-01. For every period, revenue divided by units equals the period's price (a nice consistency check: why must it?).

<details><summary>Hint</summary>

Build the periods with `lead(valid_from) OVER (PARTITION BY product_id ORDER BY valid_from)`. Then `LEFT JOIN` the order lines whose order was created in `[valid_from, valid_to)`; for the open-ended last period, `valid_to IS NULL` must count as "until forever". Keep the status filter where it can't turn the `LEFT JOIN` into an inner join.

</details>

---

## Exercise 7 ★★: Four tables, many problems

A contractor delivered this schema for three features: images attached to products and photos attached to reviews; per-user settings (language, currency, newsletter opt-in); and seller coupons, where a code is typed in at checkout, gives a percentage off, can expire, and can be withdrawn by the seller. An order can use at most one coupon.

```sql
CREATE TABLE attachments (
    id          bigserial PRIMARY KEY,
    owner_type  text,           -- 'product' or 'review'
    owner_id    bigint,
    url         text
);
CREATE TABLE user_settings (
    user_id  bigint,
    setting  text,              -- 'language', 'currency', 'newsletter'
    value    text
);
CREATE TABLE coupons (
    code         text PRIMARY KEY,
    seller_name  text,
    percent_off  numeric,
    valid_until  text,          -- '2025-08-31'
    deleted      boolean DEFAULT false
);
CREATE TABLE coupon_redemptions (
    id           bigserial PRIMARY KEY,
    coupon_code  text,
    order_id     bigint,
    user_id      bigint,
    discount     float
);
```

Name the anti-patterns and flaws, then write a corrected schema. Prove, inside a transaction, that your coupons: (1) reject a second **live** coupon whose code differs only in case (`SUMMER10` vs `summer10`); (2) reject `percent_off = 150`; (3) reject a second coupon on the same order; (4) **accept** reusing a code once the old coupon has been withdrawn.

**Self-check:** you should find a polymorphic association, an EAV table, a soft-delete design that blocks code reuse, a natural key that will change, a transitive dependency, two wrong types for time and money, and missing foreign keys and `NOT NULL`s. Your four tests give `23505`, `23514`, `23505` and success.

<details><summary>Hint</summary>

`coupon_redemptions.user_id` can be derived from `order_id`. For the code rules, a *partial unique index on an expression* combines two ideas from the theory: `ON coupons (upper(code)) WHERE deleted_at IS NULL`.

</details>

---

## Exercise 8 ★★: Soft-delete users, keep emails reusable

Bazaar decides to soft-delete user accounts: closing an account sets `users.deleted_at`, and the row stays (orders still reference it). A closed account's email must become free for a new registration, but two **live** accounts must never share an email. Implement this inside a transaction: add the column (with a `COMMENT`), replace the existing uniqueness on `email`, close user 29's account, register a new user with user 29's email, and show that a duplicate of a live user's email is still rejected. Finally, explain what happens if someone "undeletes" user 29 now, and why that's the behaviour you want.

**Self-check:** the new registration succeeds (two rows with `nora.berg@example.no`, one with `deleted_at` set). Inserting user 30's email again fails with `duplicate key value violates unique constraint "users_email_live_key"` (or your index name). Undeleting user 29 fails with the same kind of error.

<details><summary>Hint</summary>

The existing rule is a `UNIQUE` constraint named `users_email_key`; `\d users` shows it. A partial unique index can't be declared as a table constraint, so you drop the constraint and create an index.

</details>

---

## Exercise 9 ★★: The address must belong to the buyer

Section 8.2 of the theory mentions a rule v2 doesn't enforce: an order's shipping address must belong to the order's buyer. (a) Check that the current data satisfies it. (b) Enforce it declaratively with a composite foreign key, inside a transaction. (c) Show that shipping order 2 (user 3) to address 1 (user 1's home) is now rejected, while an order with no address is still accepted. (d) Explain why the extra `UNIQUE` constraint you needed is "redundant" as a key, yet necessary.

**Self-check:** (a) 0 violating orders. (b) Both `ALTER TABLE`s succeed. (c) The update fails with `violates foreign key constraint`, and the detail shows `Key (shipping_address_id, user_id)=(1, 3) is not present in table "addresses"`; setting `shipping_address_id = NULL` on order 2 succeeds.

<details><summary>Hint</summary>

A foreign key must reference columns with a unique constraint *on exactly those columns*. Why does a NULL `shipping_address_id` pass the composite foreign key? (Look up `MATCH SIMPLE`, the default.)

</details>

---

## Exercise 10 ★★: Reconcile the payments ledger

Payments are an append-only ledger (theory section 7.2). Write one query that reconciles every order with its payments and reports, per order status: the number of orders, the number of failed and pending payment attempts, and the number of **anomalies**, where:

- paid, shipped and delivered orders must have net succeeded payments equal to `total`;
- refunded orders must have been charged `total` and refunded `total` (succeeded payments only);
- pending and cancelled orders must have net succeeded payments of 0.

**Self-check:** 6 rows, one per status; 0 anomalies everywhere. 30 failed attempts in total, 24 of them on delivered orders; exactly 1 pending payment, on a pending order. Then, inside a transaction, insert a second succeeded payment of 10.00 for order 40 and check that the delivered row reports 1 anomaly.

<details><summary>Hint</summary>

`LEFT JOIN` orders to payments (some orders have none), group by order, and use `sum(amount) FILTER (WHERE status = 'succeeded' AND amount > 0)` for charges and the negative counterpart for refunds. Wrap that in a CTE, then aggregate per status with a `CASE` inside `count(*) FILTER (WHERE ...)`.

</details>

---

## Exercise 11 ★★★: Normalize the spreadsheet

Turn `purchase_sheet` from exercise 4 into a normalized schema (at least 3NF; BCNF if you can), and write the migration that moves the data, as **one transaction** that creates the tables and fills them with `INSERT ... SELECT` from `purchase_sheet`. Requirements:

- every table has a key, every relationship a foreign key; purchase-order lines reference Bazaar's real `products` (seller 3's SKUs), and the product title is not stored again;
- the supplier name inconsistency you found in exercise 4 is resolved by a rule you choose and document (in a `COMMENT`);
- `CHECK` constraints for quantities and costs, `NOT NULL` where the business requires a value, naming per section 12 of the theory.

Prove the migration is correct with a **round-trip query**: rebuild the spreadsheet from your tables with joins and compare it with `purchase_sheet` in both directions with `EXCEPT`.

**Self-check:** 3 suppliers, 2 warehouses, 6 purchase orders, 14 lines. The round trip differs only in the rows whose supplier name you normalised: with the rule "most frequent spelling wins", exactly 2 rows of PO-1005 appear on each side, differing only in `supplier_name`. A second line for the same product on the same PO, or a `quantity` of 0, must be rejected.

<details><summary>Hint</summary>

Order matters: suppliers and warehouses first, then purchase orders (looking up the supplier's new id by email), then lines (looking up the PO's id by number and the product's id by `(seller_id, sku)`). For the name rule, `mode() WITHIN GROUP (ORDER BY supplier_name)` returns the most frequent value of a group. Keep `unit_cost` on the lines: exercise 4 showed it isn't a fact about the product.

</details>

---

## Exercise 12 ★★★: A closure table for the category tree

Module 06 queried the category tree with recursive CTEs. Build the alternative from theory section 7.4, inside a transaction:

1. Create `category_closure (ancestor_id, descendant_id, depth)` with one row for every pair where the first category is an ancestor of the second, **including each category paired with itself at depth 0**. Choose its key, foreign keys and a `CHECK` that ties `depth = 0` to `ancestor_id = descendant_id`. Add the index that makes "all ancestors of X" fast.
2. Fill it from `categories` with a recursive CTE.
3. Without any recursion, query (a) the subtree of Books with depths, and (b) the breadcrumb of product 25.
4. Add a new category "Thrillers" (slug `thrillers`) under Fiction (15) **and** its closure rows in **one statement**.
5. Write a check that compares the closure table with what a recursive CTE computes from `categories`, in both directions.

**Self-check:** step 2 inserts 53 rows. Step 3 gives the 7 Books categories and `Books › Fiction › Mystery`. Step 4 inserts 3 closure rows for the new category (depths 0, 1 and 2). Step 5 reports 0 missing and 0 extra rows.

<details><summary>Hint</summary>

For step 4, a new leaf's ancestors are its parent's ancestors, each one level further away, plus itself. A data-modifying CTE (`WITH new_category AS (INSERT INTO categories ... RETURNING id, parent_id)`) lets the closure `INSERT` use the new id in the same statement.

</details>
