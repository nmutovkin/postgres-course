# Module 02: Exercises

**Starting state.** Unless an exercise says otherwise, start from the reference core schema, with empty tables:

```bash
./project/bin/reset.sh 03
./project/bin/psql.sh
```

That's the state at the start of module 02 plus this module's milestone (the seven core tables in schema `bazaar`, no rows). If you've finished the milestone, your own schema works just as well. Several exercises create scratch tables. Put those in a `lab` schema, as in the theory:

```sql
CREATE SCHEMA IF NOT EXISTS lab;
SET search_path = lab, bazaar, public;
```

Exercises that insert rows into the Bazaar tables say so. Run `./project/bin/reset.sh 03` afterwards to get back to empty tables.

Solutions: [solutions/README.md](solutions/README.md).

---

## Exercise 1 ★: Every rule in one list

Write a query against `pg_constraint` that lists every `CHECK` constraint in the `bazaar` schema: the table it belongs to, its name, and its definition as SQL. Then answer from the output: which of the seven tables has no `CHECK` constraint at all, and which `CHECK` constraints compare two columns of the same row?

**Self-check:** 9 rows. The table without a `CHECK` is the one whose rules are all `NOT NULL`, `UNIQUE` and its primary key. Two of the checks compare two columns.

<details><summary>Hint</summary>

`contype = 'c'` selects check constraints. `connamespace = 'bazaar'::regnamespace` restricts the query to the schema. `conrelid::regclass` turns the table's OID into its name, and `pg_get_constraintdef(oid)` gives the definition.

</details>

---

## Exercise 2 ★: Which of these are true?

Predict, then run:

```sql
SELECT 0.1::float8 * 3 = 0.3::float8                     AS a,
       0.1 * 3 = 0.3                                     AS b,
       1.10::float8 + 2.20::float8 = 3.30::float8        AS c,
       0.5::float8 + 0.25::float8 = 0.75::float8         AS d;
```

Explain each result. In particular, why is one of the two pure-`float8` comparisons true?

**Self-check:** exactly two of the four are true.

<details><summary>Hint</summary>

`float8` is a binary fraction. Which of 0.1, 0.3, 1.1, 0.5 and 0.25 can be written exactly as a sum of powers of two (1/2, 1/4, 1/8, ...)? And what type is the literal `0.1` when there's no cast?

</details>

---

## Exercise 3 ★: A customer in Sydney

An order was placed at `2025-06-30 23:30:00+00`.

1. What did the wall clock show, date and time, for a shopper in `Australia/Sydney`? For one in `America/Los_Angeles`?
2. Australia's daylight saving time ended on 2025-04-06. How long was that calendar day in Sydney?

Answer both **without** changing your session's `TimeZone`.

**Self-check:** (1) In Sydney it was already the next morning; the two wall clocks are 17 hours apart. (2) The day was longer than 24 hours.

<details><summary>Hint</summary>

`timestamptz AT TIME ZONE 'zone'` gives the wall-clock reading. For (2), turn the two local midnights (wall-clock `timestamp` values) into instants with `timestamp AT TIME ZONE 'Australia/Sydney'`, then subtract.

</details>

---

## Exercise 4 ★★: Design the `coupons` table

Bazaar wants discount coupons. Create `lab.coupons` with:

- `id`: a surrogate key, generated, never supplied by hand;
- `code`: required, unique, 3 to 20 characters, upper-case letters and digits only;
- `percent_off`: a whole number from 1 to 90, **or** `amount_off`: a positive amount of money. **Exactly one** of the two must be set;
- `valid_from`: required, defaulting to the time the coupon is created; `valid_until`: optional (`NULL` means "no end date"), but if set it must be after `valid_from`;
- `max_uses`: optional (`NULL` means unlimited), but if set it must be positive;
- `created_at`, as in every Bazaar entity table.

Give every `CHECK` a meaningful name. Then run these statements, in this order:

```sql
INSERT INTO coupons (code, percent_off, valid_from, valid_until) VALUES ('WELCOME10', 10, '2025-01-01', '2025-12-31');
INSERT INTO coupons (code, amount_off, valid_from) VALUES ('FIVEOFF', 5.00, '2025-06-01');
INSERT INTO coupons (code, percent_off) VALUES ('WELCOME10', 15);
INSERT INTO coupons (code, percent_off) VALUES ('summer25', 25);
INSERT INTO coupons (code, percent_off, amount_off) VALUES ('BOTH', 10, 5);
INSERT INTO coupons (code) VALUES ('NOTHING');
INSERT INTO coupons (code, percent_off) VALUES ('HUGE95', 95);
INSERT INTO coupons (code, percent_off, valid_from, valid_until) VALUES ('BACKWARDS', 10, '2025-07-01', '2025-06-01');
INSERT INTO coupons (code, amount_off, max_uses) VALUES ('ZERO', 3, 0);
INSERT INTO coupons (code, amount_off) VALUES ('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 3);
INSERT INTO coupons (code, amount_off, max_uses) VALUES ('VIP2025', 20, NULL);
```

**Self-check:** exactly three statements succeed (`WELCOME10`, `FIVEOFF`, `VIP2025`), so `SELECT count(*) FROM coupons` returns 3. Each of the eight failures names a *different* reason, except that two of them fail on the same "exactly one discount" rule and two on the code format.

<details><summary>Hint</summary>

"Exactly one of A and B is set" is "A is null" differs from "B is null": `(a IS NULL) <> (b IS NULL)`. A regular expression like `'^[A-Z0-9]{3,20}$'` covers both the length and the alphabet. Remember that a `CHECK` passes when its expression is `NULL`. Which of your checks rely on that?

</details>

---

## Exercise 5 ★★: Sibling categories must have different names

Bazaar's category slugs are globally unique, but nothing stops two categories with the same *name* under the same parent, which would make the shop's menu ambiguous. Add a constraint to `bazaar.categories` so that **siblings** (categories with the same parent) can't share a name, and that includes root categories, which all have `parent_id NULL`. Then test it:

```sql
INSERT INTO categories (id, parent_id, name, slug) OVERRIDING SYSTEM VALUE VALUES
    (1, NULL, 'Electronics', 'electronics'),
    (2, NULL, 'Toys', 'toys'),
    (3, 1, 'Toys', 'electronic-toys');                                                     -- must succeed
INSERT INTO categories (id, parent_id, name, slug) OVERRIDING SYSTEM VALUE VALUES (4, NULL, 'Toys', 'toys-2');      -- must fail
INSERT INTO categories (id, parent_id, name, slug) OVERRIDING SYSTEM VALUE VALUES (5, 1, 'Toys', 'more-toys');      -- must fail
```

Then show that the "obvious" constraint without the extra keywords would have let the duplicate root through.

**Self-check:** the first statement inserts 3 rows. Both other statements fail with a duplicate key error that names your constraint; the first of them reports the key as `(null, Toys)`. Run `./project/bin/reset.sh 03` when you're done.

<details><summary>Hint</summary>

A two-column `UNIQUE (parent_id, name)` treats two `NULL` parents as different. PostgreSQL 15 added a clause for exactly this.

</details>

---

## Exercise 6 ★★: Predict the cascade

Load this tiny shop into the empty Bazaar tables:

```sql
INSERT INTO users (id, email, full_name) OVERRIDING SYSTEM VALUE VALUES
    (1, 'olga@example.com', 'Olga Seller'),
    (2, 'pete@example.com', 'Pete Buyer');
INSERT INTO sellers (id, user_id, shop_name, country_code) OVERRIDING SYSTEM VALUE VALUES
    (1, 1, 'Olga''s Oddities', 'PL');
INSERT INTO categories (id, parent_id, name, slug) OVERRIDING SYSTEM VALUE VALUES
    (1, NULL, 'Home', 'home'),
    (2, 1, 'Lamps', 'lamps');
INSERT INTO products (id, seller_id, category_id, sku, title, price, status) OVERRIDING SYSTEM VALUE VALUES
    (1, 1, 2, 'OO-1', 'Brass Desk Lamp', 59.00, 'active'),
    (2, 1, 2, 'OO-2', 'Paper Lantern', 12.50, 'active');
INSERT INTO inventory (product_id, quantity, reserved) VALUES (1, 10, 0), (2, 40, 0);
INSERT INTO orders (id, user_id, seller_id, status, total) OVERRIDING SYSTEM VALUE VALUES
    (1, 2, 1, 'delivered', 59.00);
INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (1, 1, 1, 59.00);
```

**Before running anything**, write down for each statement below, run in this order, whether it succeeds, and if it does, which rows in which tables disappear. If it fails, which constraint stops it?

```sql
DELETE FROM products WHERE id = 1;     -- (a)
DELETE FROM products WHERE id = 2;     -- (b)
DELETE FROM categories WHERE id = 1;   -- (c)
DELETE FROM users WHERE id = 2;        -- (d)
DELETE FROM orders WHERE id = 1;       -- (e)
DELETE FROM products WHERE id = 1;     -- (f)
DELETE FROM users WHERE id = 2;        -- (g)
```

Then run them and compare. Finish with a count of the rows left in each of the seven tables.

**Self-check:** three of the seven statements fail. At the end, only `users` (1 row), `sellers` (1) and `categories` (2) have rows left.

<details><summary>Hint</summary>

Check `\d+` for each table's "Referenced by" section, and note which foreign keys say `ON DELETE CASCADE`. A self-referencing foreign key counts like any other.

</details>

---

## Exercise 7 ★★: Cleaning up a supplier feed

Casa Cucina's supplier sends a feed that someone loaded as all-`text` columns:

```sql
CREATE TABLE lab.supplier_feed (
    sku        text,
    price      text,
    in_stock   text,
    listed_on  text
);
INSERT INTO lab.supplier_feed VALUES
    ('CC-KI-101', '12,50',  'yes', '30/06/2025'),
    ('CC-KI-102', '7,99',   'no',  '01/07/2025'),
    ('CC-KI-103', '129,00', 'YES', '15/05/2025'),
    ('CC-KI-104', '0,95',   'n',   '02/01/2025');
```

Prices use a decimal comma, stock flags are yes/no in various spellings, and dates are day/month/year. With **one** `ALTER TABLE` statement, convert `price` to `numeric(12,2)`, `in_stock` to `boolean` and `listed_on` to `date`, without losing or misreading any value.

**Self-check:** `SELECT sum(price) FROM lab.supplier_feed` returns `150.44`, two rows are in stock, and `CC-KI-102` was listed on 2025-07-01 (July 1st, not January 7th).

<details><summary>Hint</summary>

One `ALTER TABLE` can contain several comma-separated `ALTER COLUMN ... TYPE ... USING ...` actions. `replace(text, from, to)` swaps characters. `boolean` input accepts `yes`, `no`, `y` and `n` in any case. For dates, `to_date(text, 'DD/MM/YYYY')` parses with an explicit pattern. What would a plain `listed_on::date` do with `'01/07/2025'`?

</details>

---

## Exercise 8 ★★: The sequence that fell behind

A colleague loaded three users with fixed ids into the empty `bazaar.users`:

```sql
INSERT INTO users (id, email, full_name) OVERRIDING SYSTEM VALUE VALUES
    (1, 'alice@example.com', 'Alice'), (2, 'ben@example.com', 'Ben'), (3, 'chloe@example.com', 'Chloe');
```

1. The application then registers `dan@example.com`. What happens? If the application retries the same insert after each failure, which attempt succeeds, and with which id?
2. Write **one** SQL statement that repairs the identity sequence of `users.id` so that the next generated id is `max(id) + 1`. It must also work when the table is **empty** (the next id is then 1). Test it in both situations.

**Self-check:** (1) three attempts fail with `users_pkey` violations, and the fourth gets id 4. (2) Run your statement on the empty table (after `reset.sh 03`); the next insert gets id 1. Load the three users and run it again; the next insert gets id 4.

<details><summary>Hint</summary>

`ALTER TABLE ... RESTART WITH` only accepts a constant, so you need `setval(sequence, value, is_called)`. What does `setval` do when its value is `NULL`, as `max(id)` is on an empty table? What is the smallest value the sequence accepts? The three-argument form with `is_called = false` means "the *next* `nextval` returns exactly this value".

</details>

---

## Exercise 9 ★★: Where does each foreign key point?

Write a query over `pg_constraint` that lists every foreign key in the `bazaar` schema with the referencing (child) table, the constraint name, the referenced (parent) table, and its `ON DELETE` action. Order it by child table, then constraint name. Then use the result to answer:

1. A `DELETE` on `orders` can remove rows from which other tables?
2. Which parent tables are referenced by more than one foreign key?
3. Which foreign key would make deleting a *category* fail even if no product uses it?

**Self-check:** 9 rows, of which exactly 2 cascade.

<details><summary>Hint</summary>

`contype = 'f'`; `conrelid` is the child table and `confrelid` the parent, and both cast to `regclass`. `confdeltype` holds the action as one letter: `a` = no action, `r` = restrict, `c` = cascade, `n` = set null, `d` = set default. `pg_get_constraintdef(oid)` spells it out as SQL.

</details>

---

## Exercise 10 ★★★: Migrating `timestamp` to `timestamptz`

An old order system stored times as `timestamp` (no time zone). The application servers ran in Berlin, so every value is Berlin wall-clock time:

```sql
CREATE TABLE lab.legacy_orders (
    id          int PRIMARY KEY,
    placed_at   timestamp NOT NULL
);
INSERT INTO lab.legacy_orders VALUES
    (1, '2025-01-15 09:00'),
    (2, '2025-03-30 01:30'),
    (3, '2025-03-30 03:30'),
    (4, '2025-06-30 23:30'),
    (5, '2025-10-26 02:30');
```

1. Convert `placed_at` to `timestamptz` so that each value becomes the correct instant. Show what a plain `ALTER COLUMN placed_at TYPE timestamptz` would have done instead, without keeping that result.
2. In real elapsed time, how far apart were orders 2 and 3? Why is it not two hours?
3. Order 5's wall-clock time occurred **twice** that night (clocks went back from 03:00 to 02:00). Which of the two instants did PostgreSQL pick, and how can you tell from the output?

**Self-check:** with the session in UTC, order 1 reads `2025-01-15 08:00:00+00` and order 4 reads `2025-06-30 21:30:00+00`. Orders 2 and 3 are one hour apart.

<details><summary>Hint</summary>

`ALTER COLUMN ... TYPE timestamptz USING expression`, where the expression turns a Berlin wall-clock `timestamp` into an instant. A transaction with `ROLLBACK` lets you look at the wrong version safely. For (3), view the result with `SET timezone = 'Europe/Berlin'` and look at the offset.

</details>

---

## Exercise 11 ★★★: Shifting image positions

Product images are shown in order, and positions must be unique per product:

```sql
CREATE TABLE lab.product_images (
    product_id bigint NOT NULL,
    position   int    NOT NULL,
    url        text   NOT NULL,
    CONSTRAINT product_images_position_key UNIQUE (product_id, position)
);
INSERT INTO lab.product_images VALUES
    (40, 1, 'knife-front.jpg'), (40, 2, 'knife-side.jpg'), (40, 3, 'knife-box.jpg'),
    (41, 1, 'santoku.jpg');
```

The seller uploads a new hero image for product 40 and wants it at position 1, with the others moving down one place.

1. Try `UPDATE lab.product_images SET position = position + 1 WHERE product_id = 40;`. Explain why it fails even though the result would be valid.
2. Change the constraint so that the single `UPDATE` works, then insert `(40, 1, 'knife-hero.jpg')`.
3. Swap the positions of `knife-front.jpg` and `knife-side.jpg` with two separate `UPDATE` statements in one transaction.
4. Name one thing your new constraint can no longer be used for, and give a way to do step 1 that keeps the original, non-deferrable constraint.

**Self-check:** after step 3, product 40's images in position order are `knife-hero.jpg`, `knife-side.jpg`, `knife-front.jpg`, `knife-box.jpg`, and product 41 still has `santoku.jpg` at position 1.

<details><summary>Hint</summary>

A non-deferrable `UNIQUE` constraint is checked as each row is updated, not once at the end of the statement. Only a `DEFERRABLE` constraint is checked at the end of the statement (or at commit, with `SET CONSTRAINTS ... DEFERRED`).

</details>

---

## Exercise 12 ★★★: Every store has a flagship

A new "featured stores" page requires every store to have a **flagship item**, and every item belongs to a store. Model it in `lab` with two tables:

- `stores (id, name, flagship_item_id)`, where `flagship_item_id` is **required**;
- `items (id, store_id, title)`, where `store_id` is required.

Both columns are foreign keys. Then:

1. Show that you can't insert the first store and its first item as two ordinary statements, in either order.
2. Make it possible to create a store together with its flagship item in one transaction.
3. Make it **impossible** for a store's flagship to be an item of a *different* store. Show both a rejected and an accepted example.

**Self-check:** a transaction inserting store `(1, 'Lamp Land', 10)` and item `(10, 1, 'Brass Desk Lamp')` commits. A transaction inserting store `(2, 'Rug Room', 10)` (store 1's item as its flagship) plus item `(20, 2, 'Kilim')` fails at `COMMIT`, and afterwards store 2 doesn't exist.

<details><summary>Hint</summary>

One of the two foreign keys has to be `DEFERRABLE`, and since the tables reference each other, one of them has to be added with `ALTER TABLE` after both tables exist. For (3), a foreign key can span two columns: if `items` has `UNIQUE (store_id, id)`, then `stores` can reference `(store_id, id)` with the pair `(id, flagship_item_id)`.

</details>
