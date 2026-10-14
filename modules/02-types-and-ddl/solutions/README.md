# Module 02: Solutions

<!-- nav:start -->
> [Theory](../README.md) · [Exercises](../exercises.md) · [Project](../project.md) · **Solutions**  
> [← 01 psql and the Postgres Landscape](../../01-psql/README.md) · [Course home](../../../README.md) · [03 Reading and Writing Data →](../../03-crud/README.md)
<!-- nav:end -->

Every solution was run on PostgreSQL 17 against `./project/bin/reset.sh 03` (the reference core schema with empty tables), with the session time zone at UTC. Scratch tables live in the `lab` schema:

```sql
CREATE SCHEMA IF NOT EXISTS lab;
SET search_path = lab, bazaar, public;
```

Timestamps produced by `now()` and random UUIDs will differ on your machine. Everything else should match.

Files in this directory:

| File | What |
|---|---|
| [`schema_check.sql`](schema_check.sql) | Milestone acceptance check, part 1: prints a canonical description of the core schema to diff against the reference. |
| [`acceptance_inserts.sql`](acceptance_inserts.sql) | Milestone acceptance check, part 2: 20 statements that a correct schema must reject, plus the two cascades. |

The milestone's reference solution itself is [`project/db/0201_schema_core.sql`](../../../project/db/0201_schema_core.sql).

---

<!-- toc:start -->
**Contents**

- [Exercise 1 ★: Every rule in one list](#exercise-1--every-rule-in-one-list)
- [Exercise 2 ★: Which of these are true?](#exercise-2--which-of-these-are-true)
- [Exercise 3 ★: A customer in Sydney](#exercise-3--a-customer-in-sydney)
- [Exercise 4 ★★: Design the `coupons` table](#exercise-4--design-the-coupons-table)
- [Exercise 5 ★★: Sibling categories must have different names](#exercise-5--sibling-categories-must-have-different-names)
- [Exercise 6 ★★: Predict the cascade](#exercise-6--predict-the-cascade)
- [Exercise 7 ★★: Cleaning up a supplier feed](#exercise-7--cleaning-up-a-supplier-feed)
- [Exercise 8 ★★: The sequence that fell behind](#exercise-8--the-sequence-that-fell-behind)
- [Exercise 9 ★★: Where does each foreign key point?](#exercise-9--where-does-each-foreign-key-point)
- [Exercise 10 ★★★: Migrating `timestamp` to `timestamptz`](#exercise-10--migrating-timestamp-to-timestamptz)
- [Exercise 11 ★★★: Shifting image positions](#exercise-11--shifting-image-positions)
- [Exercise 12 ★★★: Every store has a flagship](#exercise-12--every-store-has-a-flagship)

<!-- toc:end -->

## Exercise 1 ★: Every rule in one list

```sql
SELECT conrelid::regclass        AS table_name,
       conname,
       pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE connamespace = 'bazaar'::regnamespace
  AND contype = 'c'
ORDER BY 1, 2;
```

```text
 table_name  |              conname               |                                                               definition                                                               
-------------+------------------------------------+----------------------------------------------------------------------------------------------------------------------------------------
 sellers     | sellers_country_code_upper         | CHECK ((country_code ~ '^[A-Z]{2}$'::text))
 categories  | categories_not_own_parent          | CHECK ((parent_id <> id))
 products    | products_price_nonnegative         | CHECK ((price >= (0)::numeric))
 products    | products_status_check              | CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'archived'::text])))
 inventory   | inventory_quantity_nonnegative     | CHECK ((quantity >= 0))
 inventory   | inventory_reserved_within_quantity | CHECK (((reserved >= 0) AND (reserved <= quantity)))
 orders      | orders_status_check                | CHECK ((status = ANY (ARRAY['pending'::text, 'paid'::text, 'shipped'::text, 'delivered'::text, 'cancelled'::text, 'refunded'::text])))
 order_items | order_items_quantity_positive      | CHECK ((quantity > 0))
 order_items | order_items_unit_price_nonnegative | CHECK ((unit_price >= (0)::numeric))
(9 rows)
```

- **`users`** has no `CHECK`. Its rules are `NOT NULL` on every column, `UNIQUE (email)` and the primary key.
- **Two checks compare columns of the same row:** `categories_not_own_parent` (`parent_id <> id`) and `inventory_reserved_within_quantity` (`reserved <= quantity`). Both had to be written as *table* constraints, since a column constraint is meant to talk about its own column.

**Reasoning.** `pg_constraint` has one row per constraint. `contype` distinguishes the kinds, and `connamespace` is the schema. `conrelid::regclass` displays the table's name, but it *sorts* by OID, which is why the rows come out in the order the tables were created (sellers before categories) rather than alphabetically. Sort by `conrelid::regclass::text` if you want names in alphabetical order.

Note how PostgreSQL normalised the definitions: `status IN ('draft', ...)` became `status = ANY (ARRAY[...])`, and `price >= 0` gained a cast `(0)::numeric`. The stored form is a parsed expression tree, and `pg_get_constraintdef` prints it back as SQL. So the text you get back isn't necessarily the text you wrote.

**Alternative.** `information_schema.check_constraints` joined to `information_schema.table_constraints` gives the same information portably. It also lists `NOT NULL` constraints as checks named like `2200_16390_1_not_null`, which is noisy. `\d table` shows the checks for one table at a time.

---

## Exercise 2 ★: Which of these are true?

```text
 a | b | c | d 
---+---+---+---
 f | t | f | t
```

- **(a) false.** `0.1` has no exact binary representation, and neither has `0.3`. `0.1::float8 * 3` is `0.30000000000000004`, the nearest double to `0.3` is a different number, and `=` compares them bit by bit.
- **(b) true.** Without a cast, `0.1` and `0.3` are `numeric` literals, so `0.1 * 3` is exactly `0.3`.
- **(c) false.** Same story as (a): `1.10::float8 + 2.20::float8` is `3.3000000000000003`.
- **(d) true.** `0.5` is 1/2 and `0.25` is 1/4, both exact binary fractions, and so is their sum 3/4. Floats are exact for values that are sums of powers of two that fit in 53 bits. The problem isn't that floats are "inaccurate", it's that most *decimal* fractions aren't representable.

```sql
SELECT 1.10::float8 + 2.20::float8 AS c_value, 0.1::float8 * 3 AS a_value;
```

```text
      c_value       |       a_value       
--------------------+---------------------
 3.3000000000000003 | 0.30000000000000004
```

**Takeaway.** The result of (d) is the dangerous one. Tests with "nice" values like 0.5 and 0.25 pass, and production values like 19.99 fail. That's why money is `numeric`.

---

## Exercise 3 ★: A customer in Sydney

```sql
SELECT '2025-06-30 23:30:00+00'::timestamptz AT TIME ZONE 'Australia/Sydney'    AS sydney,
       '2025-06-30 23:30:00+00'::timestamptz AT TIME ZONE 'America/Los_Angeles' AS los_angeles;
```

```text
       sydney        |     los_angeles     
---------------------+---------------------
 2025-07-01 09:30:00 | 2025-06-30 16:30:00
```

In June, Sydney is on standard time (UTC+10, it's winter there) and Los Angeles on daylight time (UTC−7), so the two wall clocks are 17 hours apart, and on different dates.

```sql
SELECT ('2025-04-07 00:00'::timestamp AT TIME ZONE 'Australia/Sydney')
     - ('2025-04-06 00:00'::timestamp AT TIME ZONE 'Australia/Sydney') AS apr6_length;
```

```text
  apr6_length   
----------------
 1 day 01:00:00
```

**Reasoning.** For (2), the two midnights are *wall-clock* readings, so they start as `timestamp` values. `timestamp AT TIME ZONE 'Australia/Sydney'` turns each one into the instant it denotes in Sydney (a `timestamptz`). Subtracting two `timestamptz` values gives the elapsed time: 25 hours, because clocks went back from 03:00 to 02:00 that night. PostgreSQL displays 25 hours as `1 day 01:00:00`. It "justifies" whole 24-hour blocks into days in the result of a `timestamptz` subtraction. Compare the Berlin example in the theory, where the 23-hour day shows as `23:00:00`.

**Alternative.** `SET timezone = 'Australia/Sydney'` followed by `SELECT '2025-04-07 00:00'::timestamptz - '2025-04-06 00:00'::timestamptz` gives the same result, but the exercise asked you not to change the session. In application code, `AT TIME ZONE` with an explicit zone is the safer habit, because the result doesn't depend on whoever configured the connection.

---

## Exercise 4 ★★: Design the `coupons` table

```sql
CREATE TABLE coupons (
    id           bigint        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code         text          NOT NULL,
    percent_off  int,
    amount_off   numeric(12,2),
    valid_from   timestamptz   NOT NULL DEFAULT now(),
    valid_until  timestamptz,
    max_uses     int,
    created_at   timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT coupons_code_key UNIQUE (code),
    CONSTRAINT coupons_code_format CHECK (code ~ '^[A-Z0-9]{3,20}$'),
    CONSTRAINT coupons_percent_off_range CHECK (percent_off BETWEEN 1 AND 90),
    CONSTRAINT coupons_amount_off_positive CHECK (amount_off > 0),
    CONSTRAINT coupons_one_discount CHECK ((percent_off IS NULL) <> (amount_off IS NULL)),
    CONSTRAINT coupons_valid_period CHECK (valid_until > valid_from),
    CONSTRAINT coupons_max_uses_positive CHECK (max_uses > 0)
);
```

The test statements, with the results:

| # | Statement | Result |
|---|---|---|
| 1 | `WELCOME10`, 10 %, 2025 | `INSERT 0 1` |
| 2 | `FIVEOFF`, 5.00 off, open-ended | `INSERT 0 1` |
| 3 | `WELCOME10` again | `duplicate key value violates unique constraint "coupons_code_key"` |
| 4 | `summer25` | `violates check constraint "coupons_code_format"` |
| 5 | both discounts | `violates check constraint "coupons_one_discount"` |
| 6 | no discount | `violates check constraint "coupons_one_discount"` |
| 7 | 95 % | `violates check constraint "coupons_percent_off_range"` |
| 8 | ends before it starts | `violates check constraint "coupons_valid_period"` |
| 9 | `max_uses` 0 | `violates check constraint "coupons_max_uses_positive"` |
| 10 | 26-character code | `violates check constraint "coupons_code_format"` |
| 11 | `VIP2025`, unlimited | `INSERT 0 1` |

`SELECT count(*) FROM coupons` returns 3.

**Reasoning.**

- **Each optional column relies on "`NULL` passes a `CHECK`".** `percent_off BETWEEN 1 AND 90` is unknown when `percent_off` is `NULL`, so the row passes, which is exactly what we want for an amount-based coupon. The same goes for `valid_until > valid_from` (no end date) and `max_uses > 0` (unlimited). If you'd written `CHECK (valid_until IS NULL OR valid_until > valid_from)`, that's equivalent, just more explicit. Some teams prefer it for readability.
- **"Exactly one of two"** is the interesting rule. `(percent_off IS NULL) <> (amount_off IS NULL)` compares two booleans that are never `NULL` themselves (`IS NULL` always returns true or false), so it's true exactly when one is set and the other isn't. PostgreSQL also has `num_nonnulls(a, b, ...)`, which counts non-null arguments: `CHECK (num_nonnulls(percent_off, amount_off) = 1)` says the same and scales to three or more alternatives.
- **The code format** is one regular expression: `^` and `$` anchor it to the whole string, `[A-Z0-9]` is the alphabet, and `{3,20}` is the length. A `varchar(20)` would only have covered the upper bound and would have produced a less helpful error.
- `valid_from` is `NOT NULL DEFAULT now()`: a coupon is valid from the moment it's created unless you say otherwise.

**Alternatives.** Splitting the code rule into `CHECK (length(code) BETWEEN 3 AND 20)` and `CHECK (code ~ '^[A-Z0-9]+$')` gives more precise error messages ("too long" versus "bad characters"). Statements 4 and 10 would then fail on different constraints. A `percent_off` of type `numeric(5,2)` would allow 12.5 % if the business wants it. Module 08 introduces range types, where `valid_from`/`valid_until` become a single `tstzrange` column.

---

## Exercise 5 ★★: Sibling categories must have different names

```sql
ALTER TABLE categories
    ADD CONSTRAINT categories_parent_id_name_key UNIQUE NULLS NOT DISTINCT (parent_id, name);
```

Running the tests:

```text
INSERT 0 3
ERROR:  duplicate key value violates unique constraint "categories_parent_id_name_key"
DETAIL:  Key (parent_id, name)=(null, Toys) already exists.
ERROR:  duplicate key value violates unique constraint "categories_parent_id_name_key"
DETAIL:  Key (parent_id, name)=(1, Toys) already exists.
```

Now the "obvious" version:

```sql
ALTER TABLE categories DROP CONSTRAINT categories_parent_id_name_key;
ALTER TABLE categories ADD CONSTRAINT categories_parent_id_name_key UNIQUE (parent_id, name);
INSERT INTO categories (id, parent_id, name, slug) OVERRIDING SYSTEM VALUE VALUES (4, NULL, 'Toys', 'toys-2');
SELECT id, parent_id, name, slug FROM categories ORDER BY id;
```

```text
INSERT 0 1
 id | parent_id |    name     |      slug       
----+-----------+-------------+-----------------
  1 |           | Electronics | electronics
  2 |           | Toys        | toys
  3 |         1 | Toys        | electronic-toys
  4 |           | Toys        | toys-2
(4 rows)
```

Two root categories called *Toys*.

**Reasoning.** Uniqueness compares the *pair* `(parent_id, name)`. For roots, `parent_id` is `NULL`, and by default two `NULL`s count as different (unknown = unknown is unknown), so `(NULL, 'Toys')` never collides with another `(NULL, 'Toys')`. `NULLS NOT DISTINCT` (PostgreSQL 15+) makes the constraint treat `NULL`s as equal. `Toys` under Electronics `(1, 'Toys')` and the root `Toys` `(NULL, 'Toys')` are still different pairs, so they coexist, as the business wants.

**Alternatives.** Before PostgreSQL 15 the usual trick was a unique index on an expression, `CREATE UNIQUE INDEX ... ON categories (coalesce(parent_id, 0), name)`, which maps all roots to the pseudo-parent 0 (expression indexes are module 11). Another is a *partial* unique index just for roots, `... ON categories (name) WHERE parent_id IS NULL`, alongside the plain two-column constraint (module 07). `NULLS NOT DISTINCT` is the clearest when it's available. Should "Toys" and "toys" also collide? Then you need `lower(name)` in an expression index (module 08 does exactly that for emails).

Reset with `./project/bin/reset.sh 03`.

---

## Exercise 6 ★★: Predict the cascade

```text
DELETE FROM products WHERE id = 1;     -- (a)
ERROR:  update or delete on table "products" violates foreign key constraint "order_items_product_id_fkey" on table "order_items"
DETAIL:  Key (id)=(1) is still referenced from table "order_items".
DELETE FROM products WHERE id = 2;     -- (b)
DELETE 1
DELETE FROM categories WHERE id = 1;   -- (c)
ERROR:  update or delete on table "categories" violates foreign key constraint "categories_parent_id_fkey" on table "categories"
DETAIL:  Key (id)=(1) is still referenced from table "categories".
DELETE FROM users WHERE id = 2;        -- (d)
ERROR:  update or delete on table "users" violates foreign key constraint "orders_user_id_fkey" on table "orders"
DETAIL:  Key (id)=(2) is still referenced from table "orders".
DELETE FROM orders WHERE id = 1;       -- (e)
DELETE 1
DELETE FROM products WHERE id = 1;     -- (f)
DELETE 1
DELETE FROM users WHERE id = 2;        -- (g)
DELETE 1
```

| | Outcome | What disappears |
|---|---|---|
| (a) | fails: `order_items_product_id_fkey` | nothing; product 1 was sold, and `order_items → products` has no action (the default) |
| (b) | succeeds | product 2 **and its inventory row** (`inventory_product_id_fkey` cascades) |
| (c) | fails: `categories_parent_id_fkey` | nothing; category 2 (*Lamps*) has category 1 as its parent. The self-reference blocks the delete before `products_category_id_fkey` even matters |
| (d) | fails: `orders_user_id_fkey` | nothing; Pete has an order |
| (e) | succeeds | order 1 **and its order item** (`order_items_order_id_fkey` cascades) |
| (f) | succeeds now | product 1 **and its inventory row**; the order item that blocked (a) is gone |
| (g) | succeeds now | user 2; his order is gone |

```sql
SELECT (SELECT count(*) FROM users) AS users, (SELECT count(*) FROM sellers) AS sellers,
       (SELECT count(*) FROM categories) AS categories, (SELECT count(*) FROM products) AS products,
       (SELECT count(*) FROM inventory) AS inventory, (SELECT count(*) FROM orders) AS orders,
       (SELECT count(*) FROM order_items) AS order_items;
```

```text
 users | sellers | categories | products | inventory | orders | order_items 
-------+---------+------------+----------+-----------+--------+-------------
     1 |       1 |          2 |        0 |         0 |      0 |           0
```

(Each `(SELECT count(*) FROM ...)` is a small subquery that returns one number. Module 04 covers subqueries.)

**Reasoning.** The two cascades in the core schema both point from a "part" to its "whole": inventory is part of a product, and lines are part of an order. Everything else is protected by the default `NO ACTION`, which encodes the business rule that history must survive: you can't delete a product that was sold or a user who ordered. Notice the order dependence: (f) and (g) succeed only because (e) removed the order first. The safe way to "delete" a sold product in Bazaar is `status = 'archived'`.

Reset with `./project/bin/reset.sh 03`.

---

## Exercise 7 ★★: Cleaning up a supplier feed

```sql
ALTER TABLE lab.supplier_feed
    ALTER COLUMN price     TYPE numeric(12,2) USING replace(price, ',', '.')::numeric(12,2),
    ALTER COLUMN in_stock  TYPE boolean       USING in_stock::boolean,
    ALTER COLUMN listed_on TYPE date          USING to_date(listed_on, 'DD/MM/YYYY');

SELECT * FROM lab.supplier_feed;
```

```text
    sku    | price  | in_stock | listed_on  
-----------+--------+----------+------------
 CC-KI-101 |  12.50 | t        | 2025-06-30
 CC-KI-102 |   7.99 | f        | 2025-07-01
 CC-KI-103 | 129.00 | t        | 2025-05-15
 CC-KI-104 |   0.95 | f        | 2025-01-02
```

`SELECT sum(price) FROM lab.supplier_feed` gives `150.44`, and `SELECT count(*) FROM lab.supplier_feed WHERE in_stock` gives 2.

**Reasoning.**

- **`price`**: `numeric` input wants a decimal point, so `replace(price, ',', '.')` fixes the format before the cast. A value like `1.234,50` (a thousands separator) would need a second replace, removing `.` first. Real feeds are messy, and module 03's milestone validates such values before converting them.
- **`in_stock`**: `boolean` input already accepts `yes`/`no`/`y`/`n` in any case (and `true`, `on`, `1`, ...), so a plain cast in `USING` works.
- **`listed_on`**: this is the trap. A plain `listed_on::date` uses the session's `DateStyle` setting, which defaults to month-first for slash-separated dates. On this data it fails on `'15/05/2025'` (there's no month 15), and that's the lucky outcome. Without that row it would have **silently** read `'01/07/2025'` as January 7th and `'02/01/2025'` as February 1st:

  ```sql
  SELECT '01/07/2025'::date AS ambiguous;
  ```

  ```text
   ambiguous  
  ------------
   2025-01-07
  ```

  `to_date(text, 'DD/MM/YYYY')` states the format explicitly, so it doesn't depend on any setting.

**Alternatives.** `SET datestyle = 'ISO, DMY'` before a plain cast also works, but it makes the conversion depend on session state. Prefer the explicit pattern in anything you'll run more than once. Doing three separate `ALTER TABLE` statements works too, but a type change rewrites the table, so one statement with three actions rewrites it once instead of three times.

---

## Exercise 8 ★★: The sequence that fell behind

**1.** The identity sequence never saw the explicit ids, so it still starts at 1. Every attempt draws the next value, collides, and burns that value:

```text
INSERT INTO users (email, full_name) VALUES ('dan@example.com', 'Dan');
ERROR:  duplicate key value violates unique constraint "users_pkey"
DETAIL:  Key (id)=(1) already exists.
INSERT INTO users (email, full_name) VALUES ('dan@example.com', 'Dan');
ERROR:  duplicate key value violates unique constraint "users_pkey"
DETAIL:  Key (id)=(2) already exists.
INSERT INTO users (email, full_name) VALUES ('dan@example.com', 'Dan');
ERROR:  duplicate key value violates unique constraint "users_pkey"
DETAIL:  Key (id)=(3) already exists.
INSERT INTO users (email, full_name) VALUES ('dan@example.com', 'Dan') RETURNING id;
 id 
----
  4
```

Three failures, then success with **id 4**. With a thousand explicit ids that's a thousand failed sign-ups, which is why the repair belongs in the load script, not in an incident review.

**2.** One statement for any state of the table:

```sql
SELECT setval(pg_get_serial_sequence('bazaar.users', 'id'), coalesce(max(id), 0) + 1, false)
FROM bazaar.users;
```

On the empty table:

```text
 setval 
--------
      1
```

and the next `INSERT ... RETURNING id` returns 1. After loading Alice, Ben and Chloe:

```text
 setval 
--------
      4
```

and the next insert returns 4.

**Reasoning.**

- `pg_get_serial_sequence` finds the sequence behind the identity column, so the statement doesn't hard-code `users_id_seq`.
- The naive `setval(seq, (SELECT max(id) FROM users))` has two problems on an empty table. `max(id)` is `NULL`, and `setval` is a *strict* function: called with a `NULL` argument it returns `NULL` and does nothing at all, silently. Replacing `NULL` with 0 doesn't help either, because sequences start at 1: `setval: value 0 is out of bounds for sequence "users_id_seq" (1..9223372036854775807)`.
- The three-argument form `setval(seq, n, false)` sets the sequence so that the **next** `nextval` returns exactly `n` (`is_called = false` means "n hasn't been handed out yet"). With `n = coalesce(max(id), 0) + 1` that's 1 for an empty table and `max + 1` otherwise.
- `ALTER TABLE users ALTER COLUMN id RESTART WITH ...` only takes a literal number, so it can't compute `max(id)` itself. In a psql script you could compute the value with `\gset` and interpolate it, but `setval` does it in one statement.

> **In production:** sequence repair after a bulk load with explicit ids is a routine step, so make it part of the load script, as Bazaar's seed does. Also note that `setval` takes effect immediately and is not rolled back if the surrounding transaction aborts.

---

## Exercise 9 ★★: Where does each foreign key point?

```sql
SELECT conrelid::regclass        AS child_table,
       conname,
       confrelid::regclass       AS parent_table,
       confdeltype               AS on_delete,
       pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE connamespace = 'bazaar'::regnamespace
  AND contype = 'f'
ORDER BY conrelid::regclass::text, conname;
```

```text
 child_table |           conname           | parent_table | on_delete |                             definition                             
-------------+-----------------------------+--------------+-----------+--------------------------------------------------------------------
 categories  | categories_parent_id_fkey   | categories   | a         | FOREIGN KEY (parent_id) REFERENCES categories(id)
 inventory   | inventory_product_id_fkey   | products     | c         | FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
 order_items | order_items_order_id_fkey   | orders       | c         | FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
 order_items | order_items_product_id_fkey | products     | a         | FOREIGN KEY (product_id) REFERENCES products(id)
 orders      | orders_seller_id_fkey       | sellers      | a         | FOREIGN KEY (seller_id) REFERENCES sellers(id)
 orders      | orders_user_id_fkey         | users        | a         | FOREIGN KEY (user_id) REFERENCES users(id)
 products    | products_category_id_fkey   | categories   | a         | FOREIGN KEY (category_id) REFERENCES categories(id)
 products    | products_seller_id_fkey     | sellers      | a         | FOREIGN KEY (seller_id) REFERENCES sellers(id)
 sellers     | sellers_user_id_fkey        | users        | a         | FOREIGN KEY (user_id) REFERENCES users(id)
(9 rows)
```

1. **Deleting an order** can only remove rows from `order_items` (`order_items_order_id_fkey` is the one foreign key whose parent is `orders`, and it cascades). Nothing references `order_items`, so the cascade stops there.
2. **Referenced more than once:** `products` (by `inventory` and `order_items`), `sellers` (by `orders` and `products`), `users` (by `orders` and `sellers`) and `categories` (by `products` and by itself).
3. **`categories_parent_id_fkey`**, the self-reference: a category with children can't be deleted even if no product is in it.

**Reasoning.** `ORDER BY conrelid::regclass::text` sorts by the table *name*. Sorting by `conrelid::regclass` alone would sort by OID, that is, by creation order. To answer "who points at products?" directly, filter on the parent instead: `WHERE contype = 'f' AND confrelid = 'bazaar.products'::regclass`. That's exactly what `\d products` does to build its "Referenced by" section (try `psql -E`, module 01).

**Alternatives.** `information_schema.referential_constraints` has a readable `delete_rule` column (`NO ACTION`, `CASCADE`, ...), but it only knows the constraint names. Getting the tables needs joins with `information_schema.table_constraints` (module 04). Module 03 introduces `CASE`, which turns the one-letter codes into words: `CASE confdeltype WHEN 'c' THEN 'CASCADE' ... END`.

---

## Exercise 10 ★★★: Migrating `timestamp` to `timestamptz`

**1.** First, the wrong way, inside a transaction we throw away:

```sql
BEGIN;
ALTER TABLE lab.legacy_orders ALTER COLUMN placed_at TYPE timestamptz;
SELECT * FROM lab.legacy_orders ORDER BY id;
ROLLBACK;
```

```text
 id |       placed_at        
----+------------------------
  1 | 2025-01-15 09:00:00+00
  2 | 2025-03-30 01:30:00+00
  3 | 2025-03-30 03:30:00+00
  4 | 2025-06-30 23:30:00+00
  5 | 2025-10-26 02:30:00+00
```

The automatic conversion interprets each wall-clock value in the **session** time zone, UTC here, so every order is off by one or two hours. It runs without any error, which is what makes it dangerous. Now the right way:

```sql
ALTER TABLE lab.legacy_orders
    ALTER COLUMN placed_at TYPE timestamptz
    USING placed_at AT TIME ZONE 'Europe/Berlin';

SELECT * FROM lab.legacy_orders ORDER BY id;
```

```text
 id |       placed_at        
----+------------------------
  1 | 2025-01-15 08:00:00+00
  2 | 2025-03-30 00:30:00+00
  3 | 2025-03-30 01:30:00+00
  4 | 2025-06-30 21:30:00+00
  5 | 2025-10-26 01:30:00+00
```

`timestamp AT TIME ZONE 'Europe/Berlin'` reads the wall-clock value *as Berlin time* and returns the instant, which is exactly the conversion we need. Winter values move back one hour (CET, UTC+1), summer values two (CEST, UTC+2).

**2.** Orders 2 and 3 were placed at 01:30 and 03:30 Berlin time on the night clocks sprang forward, and the stored instants are 00:30 and 01:30 UTC:

```sql
SELECT '2025-03-30 01:30:00+00'::timestamptz - '2025-03-30 00:30:00+00'::timestamptz AS elapsed;
```

```text
 elapsed  
----------
 01:00:00
```

One hour, not two: the wall-clock hour from 02:00 to 03:00 didn't exist that night.

**3.** Show the data in Berlin time:

```sql
SET timezone = 'Europe/Berlin';
SELECT * FROM lab.legacy_orders ORDER BY id;
```

```text
 id |       placed_at        
----+------------------------
  1 | 2025-01-15 09:00:00+01
  2 | 2025-03-30 01:30:00+01
  3 | 2025-03-30 03:30:00+02
  4 | 2025-06-30 23:30:00+02
  5 | 2025-10-26 02:30:00+01
```

Order 5 shows offset `+01`: PostgreSQL chose the **second** 02:30, after the clocks went back (standard time). The first 02:30 of that night was `02:30+02`, an hour earlier. The original `timestamp` value simply doesn't contain the information to decide between them, which is the core argument against storing instants as `timestamp`. If the old system had logged in UTC, or with offsets, there'd be no ambiguity.

```sql
SET timezone = 'UTC';
```

**Alternatives.** On a large table, rather than an in-place `ALTER` (which rewrites the table under an exclusive lock), you'd add a new `timestamptz` column, back-fill it in batches with the same `AT TIME ZONE` expression, switch the application over, and then drop the old column. That's the expand/contract pattern of module 26. One more subtlety: since PostgreSQL 12, `ALTER COLUMN ... TYPE timestamptz` *without* `USING` skips the table rewrite when the session time zone is UTC, because then the stored bits don't change. That's another reason the wrong version is so tempting: it's instant and silent.

---

## Exercise 11 ★★★: Shifting image positions

**1.**

```sql
UPDATE lab.product_images SET position = position + 1 WHERE product_id = 40;
```

```text
ERROR:  duplicate key value violates unique constraint "product_images_position_key"
DETAIL:  Key (product_id, "position")=(40, 2) already exists.
```

A non-deferrable unique constraint is enforced by the unique index **as each row is updated**. The `UPDATE` happened to process `knife-front.jpg` first, moving it from 1 to 2, while `knife-side.jpg` still occupied position 2. The final state would have been valid, but an intermediate state isn't, and that's enough. (Whether it fails depends on the order in which the rows are visited, which you don't control.)

**2.** Make the constraint deferrable. A constraint's deferrability can't be changed in place for `UNIQUE`, so drop and re-add it:

```sql
ALTER TABLE lab.product_images DROP CONSTRAINT product_images_position_key;
ALTER TABLE lab.product_images
    ADD CONSTRAINT product_images_position_key UNIQUE (product_id, position)
    DEFERRABLE INITIALLY IMMEDIATE;

UPDATE lab.product_images SET position = position + 1 WHERE product_id = 40;
INSERT INTO lab.product_images VALUES (40, 1, 'knife-hero.jpg');
```

```text
UPDATE 3
INSERT 0 1
```

`DEFERRABLE INITIALLY IMMEDIATE` still checks at the end of every statement, unless a transaction defers it, but the end of the *statement* is all the `UPDATE` needs. A real duplicate is still caught at once:

```sql
UPDATE lab.product_images SET position = 1 WHERE product_id = 40 AND position = 4;
```

```text
ERROR:  duplicate key value violates unique constraint "product_images_position_key"
DETAIL:  Key (product_id, "position")=(40, 1) already exists.
```

**3.** A swap needs two statements, and after the first one two rows share position 3, so the check has to wait until commit:

```sql
BEGIN;
SET CONSTRAINTS product_images_position_key DEFERRED;
UPDATE lab.product_images SET position = 3 WHERE url = 'knife-front.jpg';
UPDATE lab.product_images SET position = 2 WHERE url = 'knife-side.jpg';
COMMIT;

SELECT * FROM lab.product_images ORDER BY product_id, position;
```

```text
 product_id | position |       url       
------------+----------+-----------------
         40 |        1 | knife-hero.jpg
         40 |        2 | knife-side.jpg
         40 |        3 | knife-front.jpg
         40 |        4 | knife-box.jpg
         41 |        1 | santoku.jpg
```

`SET CONSTRAINTS` only affects the current transaction. (A swap could also be done in one `UPDATE`, with a `CASE` expression from module 03, in which case `INITIALLY IMMEDIATE` would suffice.)

**4.** A deferrable unique constraint **can't be the arbiter of `ON CONFLICT`** (module 03's upserts):

```text
ERROR:  ON CONFLICT does not support deferrable unique constraints/exclusion constraints as arbiters
```

It also costs a little more to check, and the planner can't rely on it to prove uniqueness in some optimisations. To shift positions while keeping the original constraint, move the rows out of the way first, through values no real row uses:

```sql
UPDATE lab.product_images SET position = -position WHERE product_id = 40;      -- 1,2,3 → -1,-2,-3
UPDATE lab.product_images SET position = -position + 1 WHERE product_id = 40;  -- → 2,3,4
```

Negative positions never collide with positive ones, so neither statement passes through a duplicate. Run both in one transaction so nobody sees the negative values. (A `CHECK (position > 0)` would forbid the trick, so it's a trade-off.)

---

## Exercise 12 ★★★: Every store has a flagship

```sql
CREATE TABLE lab.stores (
    id                 bigint PRIMARY KEY,
    name               text   NOT NULL,
    flagship_item_id   bigint NOT NULL
);
CREATE TABLE lab.items (
    id        bigint PRIMARY KEY,
    store_id  bigint NOT NULL REFERENCES lab.stores (id),
    title     text   NOT NULL,
    CONSTRAINT items_store_id_id_key UNIQUE (store_id, id)
);
ALTER TABLE lab.stores
    ADD CONSTRAINT stores_flagship_fkey
    FOREIGN KEY (id, flagship_item_id) REFERENCES lab.items (store_id, id)
    DEFERRABLE INITIALLY DEFERRED;
```

**1.** Before making it deferrable (that is, with a plain `FOREIGN KEY (flagship_item_id) REFERENCES items (id)`), neither order works:

```text
INSERT INTO stores VALUES (1, 'Lamp Land', 10);
ERROR:  insert or update on table "stores" violates foreign key constraint "stores_flagship_item_id_fkey"
DETAIL:  Key (flagship_item_id)=(10) is not present in table "items".
INSERT INTO items VALUES (10, 1, 'Brass Desk Lamp');
ERROR:  insert or update on table "items" violates foreign key constraint "items_store_id_fkey"
DETAIL:  Key (store_id)=(1) is not present in table "stores".
```

A store needs its item to exist, and the item needs its store. Making `flagship_item_id` nullable would break the cycle, but the requirement says it's required.

**2 and 3.** With the deferred, two-column foreign key above, a store and its flagship go in together:

```sql
BEGIN;
INSERT INTO lab.stores VALUES (1, 'Lamp Land', 10);
INSERT INTO lab.items VALUES (10, 1, 'Brass Desk Lamp'), (11, 1, 'Paper Lantern');
COMMIT;
```

```text
COMMIT
```

A store that tries to use store 1's lamp as its flagship is rejected at commit:

```sql
BEGIN;
INSERT INTO lab.stores VALUES (2, 'Rug Room', 10);
INSERT INTO lab.items VALUES (20, 2, 'Kilim');
COMMIT;
```

```text
ERROR:  insert or update on table "stores" violates foreign key constraint "stores_flagship_fkey"
DETAIL:  Key (id, flagship_item_id)=(2, 10) is not present in table "items".
```

The failed commit rolls back both inserts, so store 2 doesn't exist. With its own item as flagship (`(2, 'Rug Room', 20)`) the same transaction commits. Changing a flagship later is checked too, at the end of the (implicit) transaction:

```sql
UPDATE lab.stores SET flagship_item_id = 11 WHERE id = 1;   -- UPDATE 1: item 11 is Lamp Land's
UPDATE lab.stores SET flagship_item_id = 20 WHERE id = 1;   -- fails: item 20 belongs to Rug Room
```

```text
ERROR:  insert or update on table "stores" violates foreign key constraint "stores_flagship_fkey"
DETAIL:  Key (id, flagship_item_id)=(1, 20) is not present in table "items".
```

**Reasoning.**

- **Breaking the cycle.** Only one of the two foreign keys has to be deferred. The other (`items.store_id`) stays immediate, so the store must be inserted first within the transaction. Because each table references the other, the second foreign key can only be added with `ALTER TABLE` once both tables exist.
- **"Belongs to the same store"** is a rule about two tables, which a `CHECK` can't express. But a foreign key can, if it spans both columns: the pair `(stores.id, stores.flagship_item_id)` must exist as a pair `(items.store_id, items.id)`. A foreign key needs a unique constraint on its target columns, hence `UNIQUE (store_id, id)` on `items`. It's logically redundant, since `id` alone is unique, but it's what makes the composite reference possible. This "composite foreign key to enforce same-parent" pattern comes back in module 21, where sharded tables reference `(seller_id, id)`.
- `INITIALLY DEFERRED` means every transaction is deferred automatically. With `INITIALLY IMMEDIATE` you'd write `SET CONSTRAINTS stores_flagship_fkey DEFERRED` in the transactions that need it, and everything else would get immediate checking. Both are fine. Immediate-by-default gives earlier errors.

**Alternative.** Many schemas avoid the cycle altogether: `flagship_item_id` becomes nullable, and the rule "every *published* store has a flagship" is checked by the application or by a `CHECK (status <> 'published' OR flagship_item_id IS NOT NULL)`. Mutually required rows make every insert path, test fixture and data import more awkward, so use them only when the invariant really matters.
