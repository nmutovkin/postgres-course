# Module 03: Reading and Writing Data

<!-- nav:start -->
> **Theory** · [Exercises](exercises.md) · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 02 Data Types and Tables](../02-types-and-ddl/README.md) · [Course home](../../README.md) · [04 Joins and Subqueries →](../04-joins-subqueries/README.md)
<!-- nav:end -->

Bazaar's tables exist, and they're empty. Before launch, the team has to get the first users, shops and catalogue into the database, then answer questions about them: Which products cost less than 15 euros? Which orders are still waiting for payment? Who has never bought anything? And they have to change data safely: raise a seller's prices, ship an order, delete a mistake, and load a supplier's catalogue without creating duplicates. All of that is SQL's data manipulation language: `SELECT`, `INSERT`, `UPDATE` and `DELETE`, plus PostgreSQL's `COPY` for bulk loads and `ON CONFLICT` for upserts. This module covers all of them, along with the one idea that trips up more SQL users than any other: `NULL` and three-valued logic.

**What you'll learn**

- Loading data: `INSERT` (single and multi-row, `DEFAULT`, `OVERRIDING SYSTEM VALUE` and repairing sequences afterwards) and `COPY`/`\copy` from CSV files, including PostgreSQL 17's `ON_ERROR ignore`.
- `SELECT` fundamentals: column lists, aliases, expressions, and the **logical order** in which a query's clauses are evaluated.
- Filtering with `WHERE`: comparisons, `AND`/`OR`/`NOT`, `BETWEEN`, `IN`, `LIKE`/`ILIKE` and regular expressions.
- `NULL` in depth: three-valued logic, `IS NULL`, `IS DISTINCT FROM`, the `NOT IN` trap, `COALESCE` and `NULLIF`.
- Sorting and paging: `ORDER BY` with several keys, `NULLS FIRST`/`LAST`, collations; `LIMIT`/`OFFSET` and `FETCH FIRST ... WITH TIES`; `DISTINCT`.
- Operators and functions for text, numbers and dates (`date_trunc`, `extract`, `age`, intervals, `to_char`), casting, and `CASE`.
- Changing data: `INSERT ... RETURNING`, `INSERT ... SELECT`, `UPDATE` (including a first look at `UPDATE ... FROM`), `DELETE`, `TRUNCATE`, and upserts with `ON CONFLICT DO NOTHING` / `DO UPDATE`.
- Transactions as a safety net: `BEGIN`, `COMMIT`, `ROLLBACK`.

**Before you start**

```bash
./project/bin/reset.sh 03
./project/bin/psql.sh
```

This is the state at the end of module 02: the seven core tables exist (from [`0201_schema_core.sql`](../../project/db/0201_schema_core.sql)), and they're all empty. Section 1 loads a few rows by hand and from a CSV file. Then you switch to the canonical small dataset with `./project/bin/reset.sh 04`, and the rest of the module uses that. The data files used in this module live in [`modules/03-crud/data/`](data/). Inside the course container they're mounted at `/course/modules/03-crud/data/`, which is where the examples read them from.

The small dataset is described in [`project/db/NOTES.md`](../../project/db/NOTES.md): 30 users, 6 sellers, 23 categories, 62 products, 120 orders. "Today" in the data is 2025-06-30, and the examples use fixed dates rather than `now()` so that your results match. Results are shown with the session time zone at UTC, the container's default.

---

<!-- toc:start -->
**Contents**

- [1. Getting data in](#1-getting-data-in)
  - [1.1 `INSERT`](#11-insert)
  - [1.2 Fixed ids: `OVERRIDING SYSTEM VALUE`](#12-fixed-ids-overriding-system-value)
  - [1.3 `COPY` and `\copy`: loading files](#13-copy-and-copy-loading-files)
  - [1.4 The canonical small dataset](#14-the-canonical-small-dataset)
- [2. `SELECT` fundamentals](#2-select-fundamentals)
  - [2.1 Columns, expressions and aliases](#21-columns-expressions-and-aliases)
  - [2.2 Writing readable SQL](#22-writing-readable-sql)
  - [2.3 The logical order of a query](#23-the-logical-order-of-a-query)
- [3. Filtering with `WHERE`](#3-filtering-with-where)
  - [3.1 Comparisons and boolean logic](#31-comparisons-and-boolean-logic)
  - [3.2 `BETWEEN` and `IN`](#32-between-and-in)
  - [3.3 Pattern matching: `LIKE`, `ILIKE` and regular expressions](#33-pattern-matching-like-ilike-and-regular-expressions)
- [4. `NULL` and three-valued logic](#4-null-and-three-valued-logic)
  - [4.1 True, false and unknown](#41-true-false-and-unknown)
  - [4.2 Testing for `NULL`](#42-testing-for-null)
  - [4.3 Negations lose `NULL` rows](#43-negations-lose-null-rows)
  - [4.4 `IS DISTINCT FROM`](#44-is-distinct-from)
  - [4.5 The `NOT IN` trap](#45-the-not-in-trap)
  - [4.6 `COALESCE` and `NULLIF`](#46-coalesce-and-nullif)
  - [4.7 `NULL` in expressions](#47-null-in-expressions)
- [5. Sorting with `ORDER BY`](#5-sorting-with-order-by)
  - [5.1 Several sort keys](#51-several-sort-keys)
  - [5.2 Where do `NULL`s go?](#52-where-do-nulls-go)
  - [5.3 Collations: how text sorts](#53-collations-how-text-sorts)
- [6. `LIMIT`, `OFFSET` and `FETCH FIRST`](#6-limit-offset-and-fetch-first)
- [7. `DISTINCT`](#7-distinct)
- [8. Operators and functions](#8-operators-and-functions)
  - [8.1 Text](#81-text)
  - [8.2 Numbers](#82-numbers)
  - [8.3 Dates and times](#83-dates-and-times)
  - [8.4 Casting](#84-casting)
  - [8.5 `CASE`](#85-case)
  - [8.6 `GREATEST` and `LEAST`](#86-greatest-and-least)
- [9. A safety net: transactions](#9-a-safety-net-transactions)
- [10. Changing data](#10-changing-data)
  - [10.1 `INSERT ... RETURNING` and `INSERT ... SELECT`](#101-insert--returning-and-insert--select)
  - [10.2 `UPDATE`](#102-update)
  - [10.3 `DELETE`](#103-delete)
  - [10.4 `TRUNCATE` versus `DELETE`](#104-truncate-versus-delete)
- [11. Upserts: `INSERT ... ON CONFLICT`](#11-upserts-insert--on-conflict)
  - [11.1 `DO NOTHING`](#111-do-nothing)
  - [11.2 `DO UPDATE` and `EXCLUDED`](#112-do-update-and-excluded)
- [Summary](#summary)
- [What's next](#whats-next)

<!-- toc:end -->

## 1. Getting data in

### 1.1 `INSERT`

`INSERT` adds rows to a table. The basic form names the table, the columns you're supplying, and the values:

```sql
INSERT INTO users (email, full_name) VALUES ('alice.johnson@example.com', 'Alice Johnson');
```

```text
INSERT 0 1
```

The reply `INSERT 0 1` is the *command tag*: one row was inserted. (The `0` is a historical leftover that's always 0.) Columns you leave out get their defaults: `id` from the identity sequence, `is_active` true, `created_at` the current time.

```sql
SELECT * FROM users;
```

```text
 id |           email           |   full_name   | is_active |          created_at           
----+---------------------------+---------------+-----------+-------------------------------
  1 | alice.johnson@example.com | Alice Johnson | t         | 2026-10-14 08:44:29.266571+00
(1 row)
```

One statement can insert many rows, and the keyword `DEFAULT` asks for a column's default explicitly. `RETURNING` makes the `INSERT` give back columns of the rows it inserted, which is how applications learn the ids the database generated (section 10 has more):

```sql
INSERT INTO users (email, full_name, is_active)
VALUES ('ben.clarke@example.co.uk', 'Benjamin Clarke', DEFAULT),
       ('giulia.rossi@example.it',  'Giulia Rossi',    true)
RETURNING id, email, is_active;
```

```text
 id |          email           | is_active 
----+--------------------------+-----------
  2 | ben.clarke@example.co.uk | t
  3 | giulia.rossi@example.it  | t
(2 rows)

INSERT 0 2
```

A multi-row `INSERT` is **atomic**: if any row fails, none are inserted.

```sql
INSERT INTO users (email, full_name)
VALUES ('lukas.becker@example.de',   'Lukas Becker'),
       ('chloe.tremblay@example.ca', NULL);
```

```text
ERROR:  null value in column "full_name" of relation "users" violates not-null constraint
DETAIL:  Failing row contains (5, chloe.tremblay@example.ca, null, t, 2026-10-14 08:44:29.269493+00).
```

`SELECT count(*) FROM users` still says 3: Lukas wasn't inserted either. Notice the id in the failing row, though: 5. Lukas's row had already drawn id 4, and Chloé's drew 5. As module 02 explained, sequences never give values back, so those ids are gone. Multi-row inserts are also much faster than one `INSERT` per row: one round trip to the server, one statement to parse, one transaction to commit.

### 1.2 Fixed ids: `OVERRIDING SYSTEM VALUE`

The Bazaar seed data uses fixed ids, so that exercises can talk about "user 7" or "order 42". The `id` columns are `GENERATED ALWAYS AS IDENTITY`, so supplying an id needs `OVERRIDING SYSTEM VALUE`, as you saw in module 02:

```sql
INSERT INTO users (id, email, full_name, created_at) OVERRIDING SYSTEM VALUE
VALUES (4, 'lukas.becker@example.de',   'Lukas Becker',   '2023-03-04 08:00:00+00'),
       (5, 'chloe.tremblay@example.ca', 'Chloé Tremblay', '2023-03-05 07:00:00+00'),
       (6, 'haruto.sato@example.jp',    'Haruto Sato',    '2023-03-06 05:00:00+00');
```

```text
INSERT 0 3
```

The identity sequence knows nothing about these rows. Its last value is 5, from the failed insert above, so the next ordinary insert draws 6, which is now taken:

```sql
INSERT INTO users (email, full_name) VALUES ('priya.patel@example.com', 'Priya Patel');
```

```text
ERROR:  duplicate key value violates unique constraint "users_pkey"
DETAIL:  Key (id)=(6) already exists.
```

Whenever you insert explicit ids, move the sequence past them in the same script. `setval` sets the sequence's current value, so the next `nextval` returns one more:

```sql
SELECT setval(pg_get_serial_sequence('users', 'id'), (SELECT max(id) FROM users));
INSERT INTO users (email, full_name) VALUES ('priya.patel@example.com', 'Priya Patel') RETURNING id;
```

```text
 setval 
--------
      6
(1 row)

 id 
----
  7
(1 row)
```

(`(SELECT max(id) FROM users)` is a *subquery*, a query inside another statement that returns one value. Module 04 covers subqueries. `max` is an aggregate function, module 05.) The reference seed file, [`0301_seed_small.sql`](../../project/db/0301_seed_small.sql), does the same thing with the other syntax from module 02: after inserting every row with `OVERRIDING SYSTEM VALUE`, it runs `ALTER TABLE users ALTER COLUMN id RESTART WITH 31` (and the same for the other tables). `RESTART WITH` needs a literal number, which is fine in a generated file that knows its highest id. `setval` with `max(id)` works for any data.

> **Pitfall:** always write the column list. `INSERT INTO users VALUES (...)` without one assigns values to columns *by position*, in table order. Nothing stops you from swapping two text columns:
>
> ```sql
> INSERT INTO users VALUES (DEFAULT, 'Mateo García', 'mateo.garcia@example.es');
> ```
>
> succeeds and stores a user whose *email* is `Mateo García`. Worse, the statement silently changes meaning when someone adds or reorders columns. With a column list, the statement says what it means.

Foreign keys work the same way with fixed ids. The first three users open their shops:

```sql
INSERT INTO sellers (id, user_id, shop_name, country_code) OVERRIDING SYSTEM VALUE
VALUES (1, 1, 'TechTrove', 'US'), (2, 2, 'Bookworm Corner', 'GB'), (3, 3, 'Casa Cucina', 'IT');
```

```text
INSERT 0 3
```

### 1.3 `COPY` and `\copy`: loading files

For more than a handful of rows, `INSERT` statements are the slow way. **`COPY`** is PostgreSQL's bulk-loading command: it streams rows in a simple text or CSV format straight into a table, without parsing a statement per row. It's typically several times faster than even multi-row `INSERT`s, and it's what `pg_dump` uses (module 18).

Module 01 introduced the two variants:

- `COPY table FROM '/path/file.csv'` runs on the **server**: the server process reads a file from the server's filesystem. That requires superuser rights or membership in `pg_read_server_files`.
- `\copy table FROM 'path/file.csv'` is a psql meta-command. psql reads the file on the **client** side and sends its contents over the connection. Anyone can use it.

Here's the category tree as a CSV file, [`data/categories.csv`](data/categories.csv):

```text
id,parent_id,name,slug,created_at
1,,Electronics,electronics,2023-03-01 08:00:00+00
2,1,Computers,computers,2023-03-01 08:00:00+00
3,2,Laptops,laptops,2023-03-01 08:00:00+00
...
23,21,Hoodies,hoodies,2023-03-01 08:00:00+00
```

The course's `psql.sh` runs psql inside the container, where the module directory is mounted at `/course/modules`, so `\copy` reads it from there:

```sql
\copy categories FROM '/course/modules/03-crud/data/categories.csv' WITH (FORMAT csv, HEADER)
```

```text
COPY 23
```

(With a native psql on your machine, give the path relative to where you started psql, for example `'modules/03-crud/data/categories.csv'`. As a superuser you could also run the server-side `COPY categories FROM '/course/modules/03-crud/data/categories.csv' WITH (FORMAT csv, HEADER);`.)

The options:

- `FORMAT csv` selects CSV: comma-separated fields, with double quotes around fields that contain commas, quotes or newlines (`"O'Connor, Liam"`), and `""` for a quote inside a quoted field. The default format, `text`, is tab-separated with `\N` for `NULL`.
- `HEADER` skips the first line. With `HEADER match`, PostgreSQL also checks that the header names match the column names.
- `DELIMITER ';'` for semicolon-separated files, `NULL 'NA'` to choose how `NULL` is written, `ENCODING 'LATIN1'` for files that aren't UTF-8.
- Without a column list, the file's columns must match the table's columns in order. With one, `\copy users (email, full_name) FROM ...`, the other columns get their defaults.

In CSV, an **unquoted empty field means `NULL`**, and a quoted empty field `""` means the empty string. That's how root categories get a `NULL` `parent_id` above:

```sql
SELECT * FROM categories WHERE parent_id IS NULL;
```

```text
 id | parent_id |     name      |    slug     |       created_at       
----+-----------+---------------+-------------+------------------------
  1 |           | Electronics   | electronics | 2023-03-01 08:00:00+00
  8 |           | Home & Garden | home-garden | 2023-03-01 08:00:00+00
 14 |           | Books         | books       | 2023-03-01 08:00:00+00
 21 |           | Clothing      | clothing    | 2023-03-01 08:00:00+00
(4 rows)
```

> **Pitfall:** `COPY` writes the ids from the file into the identity column, without needing `OVERRIDING SYSTEM VALUE`. It also doesn't advance the sequence. The next ordinary insert collides:
>
> ```sql
> INSERT INTO categories (name, slug) VALUES ('Toys', 'toys');
> ```
>
> ```text
> ERROR:  duplicate key value violates unique constraint "categories_pkey"
> DETAIL:  Key (id)=(1) already exists.
> ```
>
> Repair it right after the load: `ALTER TABLE categories ALTER COLUMN id RESTART WITH 24;` (or `setval`). After that, `Toys` gets id 24.

**When a file has bad rows.** `COPY` is atomic like a multi-row `INSERT`: one bad row and nothing is loaded. Here's a batch of new users from a sign-up form, [`data/users_batch.csv`](data/users_batch.csv):

```text
email,full_name,is_active,created_at
marcus.lee@example.com,Marcus Lee,true,2023-07-26 14:33:00+00
sofia.hernandez@example.es,Sofía Hernández,false,2023-08-16 17:10:00+00
noah.williams@example.com,Noah Williams,yes please,2023-09-06 19:58:00+00
emma.schmidt@example.de,Emma Schmidt,,2023-09-27 09:01:00+00
liam.oconnor@example.ie,"O'Connor, Liam",true,2023-10-18 15:29:00+00
```

```sql
\copy users (email, full_name, is_active, created_at) FROM '/course/modules/03-crud/data/users_batch.csv' WITH (FORMAT csv, HEADER)
```

```text
ERROR:  invalid input syntax for type boolean: "yes please"
CONTEXT:  COPY users, line 4, column is_active: "yes please"
```

The `CONTEXT` line tells you exactly where: line 4 of the file, column `is_active`. **PostgreSQL 17** adds an option to skip rows whose values can't be converted to the column type, instead of failing:

```sql
\copy users (email, full_name, is_active, created_at) FROM '/course/modules/03-crud/data/users_batch.csv' WITH (FORMAT csv, HEADER, ON_ERROR ignore, LOG_VERBOSITY verbose)
```

```text
NOTICE:  skipping row due to data type incompatibility at line 4 for column "is_active": "yes please"
ERROR:  null value in column "is_active" of relation "users" violates not-null constraint
DETAIL:  Failing row contains (13, emma.schmidt@example.de, Emma Schmidt, null, 2023-09-27 09:01:00+00).
CONTEXT:  COPY users, line 5: "emma.schmidt@example.de,Emma Schmidt,,2023-09-27 09:01:00+00"
```

Two lessons in one error. First, `ON_ERROR ignore` only skips **conversion** errors. Constraint violations still abort the whole `COPY`. Second, Emma's empty `is_active` field was read as `NULL`, not as "use the default". An empty CSV field is `NULL`, full stop, and the column is `NOT NULL`. PostgreSQL 16 added a `DEFAULT` option that names a marker meaning "use the column default". It can't be the same as the `NULL` marker, so make `NULL` something else:

```sql
\copy users (email, full_name, is_active, created_at) FROM '/course/modules/03-crud/data/users_batch.csv' WITH (FORMAT csv, HEADER, DEFAULT '', NULL '\N', ON_ERROR ignore, LOG_VERBOSITY verbose)
```

```text
NOTICE:  skipping row due to data type incompatibility at line 4 for column "is_active": "yes please"
NOTICE:  1 row was skipped due to data type incompatibility
COPY 4
```

```sql
SELECT id, email, full_name, is_active FROM users WHERE id > 8;
```

```text
 id |           email            |    full_name    | is_active 
----+----------------------------+-----------------+-----------
 14 | marcus.lee@example.com     | Marcus Lee      | t
 15 | sofia.hernandez@example.es | Sofía Hernández | f
 16 | emma.schmidt@example.de    | Emma Schmidt    | t
 17 | liam.oconnor@example.ie    | O'Connor, Liam  | t
(4 rows)
```

Noah's row was skipped, Emma got the default `true`, and the quoted name with a comma arrived intact. The ids start at 14 because the two failed attempts consumed some sequence values. Silently skipping rows is rarely what a business wants, though: someone has to follow up on Noah. A robust import loads the file into a **staging table** with all-`text` columns (which accepts anything), validates and reports bad rows with SQL, and only then moves the good rows into the real table. That's exactly this module's project milestone.

`COPY ... TO` (or `\copy ... TO`) works in the other direction and accepts a query: `\copy (SELECT id, email FROM users) TO 'users.csv' WITH (FORMAT csv, HEADER)`. Module 01 covered exporting.

### 1.4 The canonical small dataset

You've seen the mechanics, and you've also made a mess: a user whose email is `Mateo García`, gaps in the ids, and a `Toys` category. Time to switch to the real data. The reference seed, [`project/db/0301_seed_small.sql`](../../project/db/0301_seed_small.sql), is a generated file of multi-row `INSERT ... OVERRIDING SYSTEM VALUE` statements wrapped in a transaction, followed by `ALTER TABLE ... RESTART` for every identity column. Load it with the reset script (quit psql first):

```bash
./project/bin/reset.sh 04
./project/bin/psql.sh
```

`reset.sh 04` rebuilds the database as of the start of module 04: the core schema plus the seed. Check the row counts:

```sql
SELECT (SELECT count(*) FROM users)       AS users,
       (SELECT count(*) FROM sellers)     AS sellers,
       (SELECT count(*) FROM categories)  AS categories,
       (SELECT count(*) FROM products)    AS products,
       (SELECT count(*) FROM inventory)   AS inventory,
       (SELECT count(*) FROM orders)      AS orders,
       (SELECT count(*) FROM order_items) AS order_items;
```

```text
 users | sellers | categories | products | inventory | orders | order_items 
-------+---------+------------+----------+-----------+--------+-------------
    30 |       6 |         23 |       62 |        62 |    120 |         235
```

From here on, every example uses this dataset. Whenever an experiment leaves the data in a strange state, `./project/bin/reset.sh 04` puts it back in a second or two.

A quick tour of what's in it, so that results in the rest of the module make sense (the full list is in [`NOTES.md`](../../project/db/NOTES.md)):

- **Users 1–6 own the six shops.** TechTrove (US) sells laptops, monitors and headphones; Bookworm Corner (GB) books; Casa Cucina (IT) cookware and knives; Threadbare Co. (DE) T-shirts and hoodies; Maple & Pine (CA) planters, and has never sold anything; Sakura Gadgets (JP) has no products at all yet.
- **Users 9, 22 and 28 are inactive**, and nine users (6, 7, 15, 16, 21, 24, 28, 29, 30) have never ordered. The data contains the usual real-world awkwardness on purpose: an apostrophe (`Liam O'Connor`), accents (`Chloé`, `Zoë`, `Sofía`), and a mixed-case email (`Zoe.Martin@Example.COM`).
- **Products** have every status (54 active, 4 draft, 4 archived). Eight have no description, one is free (product 62, a sticker pack), and the most expensive is product 3 at 2,199.99. Some titles contain `'` or `"`.
- **Orders** run from 2024-01-12 to 2025-06-30, ids in chronological order, with every status: mostly delivered, plus open orders (pending 115–119, paid 110–114 and 120, shipped 105–107 and 109), ten cancelled and four refunded. The paid, shipped and delivered timestamps are filled in according to each order's status.
- **Inventory** has one row per product. Nine products are out of stock, and `reserved` equals the units in pending and paid orders.


---

## 2. `SELECT` fundamentals

### 2.1 Columns, expressions and aliases

A `SELECT` lists what to return, and `FROM` says where it comes from:

```sql
SELECT shop_name, country_code FROM sellers;
```

```text
    shop_name    | country_code 
-----------------+--------------
 TechTrove       | US
 Bookworm Corner | GB
 Casa Cucina     | IT
 Threadbare Co.  | DE
 Maple & Pine    | CA
 Sakura Gadgets  | JP
(6 rows)
```

`SELECT *` returns every column, which is handy at the psql prompt. In application code, name the columns. `*` returns columns the code doesn't need, it changes meaning when someone adds a column, and it hides which columns a query really depends on.

The select list can contain any **expression**, not just column names. `AS` gives the result column a name, an **alias**:

```sql
SELECT id, title, price, price * 1.2 AS price_with_vat
FROM products
WHERE seller_id = 3;
```

```text
 id |              title               | price  | price_with_vat 
----+----------------------------------+--------+----------------
 34 | Cast Iron Skillet 26cm           |  45.00 |         54.000
 35 | Stainless Steel Saucepan 2L      |  39.00 |         46.800
 36 | Non-Stick Frying Pan 28cm        |  34.99 |         41.988
 37 | Dutch Oven 5.5L Enamel           | 119.00 |        142.800
 38 | Copper Sauté Pan                 | 189.00 |        226.800
 39 | Pasta Pot with Strainer 6L       |  54.00 |         64.800
 40 | Chef's Knife 20cm                |  89.00 |        106.800
 41 | Santoku Knife 18cm               |  79.00 |         94.800
 42 | Paring Knife Set (3 pcs)         |  29.00 |         34.800
 43 | Bread Knife 23cm                 |  35.00 |         42.000
 44 | Bamboo Cutting Board             |  24.00 |         28.800
 45 | Knife Sharpening Stone 1000/6000 |  32.00 |         38.400
 46 | Espresso Moka Pot 6-cup          |  42.00 |         50.400
(13 rows)
```

(`WHERE` filters rows; section 3 covers it.) Notice `price_with_vat` has three decimals: `numeric(12,2) * 1.2` keeps all the digits the multiplication produces. Rounding is your decision (section 8.2). The keyword `AS` is optional (`price * 1.2 price_with_vat` works), but writing it avoids confusion. An alias that contains spaces or capitals needs double quotes, like any identifier: `price * 1.2 AS "Price incl. VAT"`. Without an alias, PostgreSQL invents a name, often `?column?`.

`SELECT` doesn't even need a table. It's a handy calculator:

```sql
SELECT 2 + 2 AS four, upper('bazaar') AS shout, date '2025-06-30' - date '2025-01-01' AS days;
```

```text
 four | shout  | days 
------+--------+------
    4 | BAZAAR |  180
```

### 2.2 Writing readable SQL

A few conventions make queries easier to read, and they're the ones this course uses:

- **Keywords in upper case, names in lower case:** `SELECT title FROM products`. SQL doesn't care (keywords and unquoted names are case-insensitive), but readers do.
- **One clause per line** for anything longer than a line, with conditions indented under `WHERE`.
- **Comments:** `--` starts a comment that runs to the end of the line; `/* ... */` can span lines. Use them to explain *why* a condition is there, especially the non-obvious ones (`-- status IS NOT NULL: drafts imported before 2024 have no status`).
- **Quotes:** single quotes for values (`'active'`), double quotes only for identifiers that need them, which with snake_case names is never. A double-quoted value is a classic beginner error: `WHERE status = "active"` looks for a *column* named `active`.
- **End statements with `;`.** psql sends a statement to the server when it sees the semicolon. Without one, it waits for more input, and the prompt changes from `=#` to `-#`.

### 2.3 The logical order of a query

A `SELECT` is *written* in one order and *evaluated*, logically, in another:

```text
 written:    SELECT ... FROM ... WHERE ... ORDER BY ... LIMIT ...

 evaluated:  1. FROM       which table(s)? (modules 04+ add joins here)
             2. WHERE      keep only rows for which the condition is true
             3. SELECT     compute the output columns (and their aliases)
             4. ORDER BY   sort the result
             5. LIMIT      keep the first n rows
```

Module 05 inserts `GROUP BY` and `HAVING` between `WHERE` and `SELECT`, and module 06 adds window functions, but the principle stays the same. The order explains many rules that otherwise look arbitrary. For example, an alias defined in `SELECT` doesn't exist yet when `WHERE` runs:

```sql
SELECT id, title, price * 1.2 AS gross FROM products WHERE gross > 1000;
```

```text
ERROR:  column "gross" does not exist
LINE 1: ..., title, price * 1.2 AS gross FROM products WHERE gross > 10...
                                                             ^
```

but `ORDER BY` runs after `SELECT`, so it can use the alias:

```sql
SELECT id, title, price * 1.2 AS gross
FROM products
WHERE price * 1.2 > 1000
ORDER BY gross DESC;
```

```text
 id |          title          |  gross   
----+-------------------------+----------
  3 | Titan G17 Gaming Laptop | 2639.988
 15 | Titan G15 Gaming Laptop | 2158.800
  2 | AeroBook 15 Pro Laptop  | 1798.800
  1 | AeroBook 13 Laptop      | 1198.800
(4 rows)
```

"Logical" is the key word. This is the order in which the *meaning* is defined, not necessarily the order in which the server does the work. The planner is free to do anything that produces the same result, for example reading only the rows `WHERE` needs from an index (modules 11 and 12).

---

## 3. Filtering with `WHERE`

`WHERE` takes a boolean condition and keeps the rows for which it's **true**. Rows for which it's false *or unknown* are dropped. That last part is important and gets its own section.

### 3.1 Comparisons and boolean logic

The comparison operators are `=`, `<>` (also written `!=`), `<`, `<=`, `>` and `>=`. They work on numbers, text, dates, booleans and most other types. Conditions combine with `AND`, `OR` and `NOT`:

```sql
SELECT id, title, price
FROM products
WHERE price < 15 AND status = 'active';
```

```text
 id |             title              | price 
----+--------------------------------+-------
 19 | The Long Orbit                 | 14.99
 20 | Children of the Red Dust       | 12.50
 22 | Echoes of Andromeda            |  9.99
 24 | Murder at Willow Lane          | 11.99
 25 | The Lighthouse Keeper's Secret | 13.49
 26 | A Death in Dorset              | 10.00
 27 | The Silent Witness             | 12.00
 62 | Bazaar Sticker Pack            |  0.00
(8 rows)
```

`AND` binds more tightly than `OR`, as multiplication does over addition. "Products of sellers 2 or 3 that cost more than 100" written without parentheses means something else:

```sql
SELECT id, title, seller_id, price
FROM products
WHERE seller_id = 2 OR seller_id = 3 AND price > 100;
```

This returns 17 rows: all 15 of seller 2's products (whatever their price), plus seller 3's two products over 100, because it's read as `seller_id = 2 OR (seller_id = 3 AND price > 100)`. With parentheses:

```sql
SELECT id, title, seller_id, price
FROM products
WHERE (seller_id = 2 OR seller_id = 3) AND price > 100;
```

```text
 id |         title          | seller_id | price  
----+------------------------+-----------+--------
 37 | Dutch Oven 5.5L Enamel |         3 | 119.00
 38 | Copper Sauté Pan       |         3 | 189.00
(2 rows)
```

Whenever a condition mixes `AND` and `OR`, add parentheses, even when they're not strictly needed. The next reader will thank you.

Text comparisons are case-sensitive and exact: `status = 'Active'` matches nothing. String literals are compared with the column's type, so `'2025-02-01'` compared with a `timestamptz` column is read as a timestamp (midnight, in the session time zone).

### 3.2 `BETWEEN` and `IN`

`x BETWEEN a AND b` means `x >= a AND x <= b`, with **both ends included**. `x IN (v1, v2, ...)` means `x = v1 OR x = v2 OR ...`:

```sql
SELECT id, status, total
FROM orders
WHERE status IN ('pending', 'paid')
ORDER BY id;
```

```text
 id  | status  |  total  
-----+---------+---------
 110 | paid    |   24.00
 111 | paid    |   72.00
 112 | paid    |   29.99
 113 | paid    |  249.00
 114 | paid    | 4399.98
 115 | pending |   24.00
 116 | pending |  756.00
 117 | pending |   29.98
 118 | pending |   99.97
 119 | pending |   38.00
 120 | paid    |   90.00
(11 rows)
```

Both have negated forms: `NOT BETWEEN` and `NOT IN`.

> **Pitfall:** `BETWEEN` with timestamps. "Orders in February 2025":
>
> ```sql
> SELECT count(*) FROM orders WHERE created_at BETWEEN '2025-02-01' AND '2025-02-28';
> ```
>
> returns 6, but there were 7. `'2025-02-28'` as a timestamp means *midnight at the start* of February 28th, so order 77, placed at 14:25 that day, falls outside. Use a **half-open interval**, "from the start of the month, up to but not including the start of the next month":
>
> ```sql
> SELECT count(*) FROM orders WHERE created_at >= '2025-02-01' AND created_at < '2025-03-01';
> ```
>
> returns 7. Half-open intervals have no gaps and no overlaps, and they work for any precision: dates, timestamps with microseconds, months of any length. Use them for every time range.

### 3.3 Pattern matching: `LIKE`, `ILIKE` and regular expressions

`LIKE` matches text against a pattern, where `%` matches any sequence of characters (including none) and `_` matches exactly one character:

```sql
SELECT id, title FROM products WHERE title LIKE '%Laptop%';
```

```text
 id |              title              
----+---------------------------------
  1 | AeroBook 13 Laptop
  2 | AeroBook 15 Pro Laptop
  3 | Titan G17 Gaming Laptop
  5 | AeroBook 13 Laptop (2023 model)
 15 | Titan G15 Gaming Laptop
 16 | Laptop Sleeve 13"
(6 rows)
```

A `LIKE` pattern must match the *whole* value, so `'%Laptop%'` means "contains", `'Laptop%'` means "starts with" and `'%Laptop'` means "ends with". `LIKE` is case-sensitive. `ILIKE`, a PostgreSQL extension, ignores case:

```sql
SELECT id, title FROM products WHERE title ILIKE '%knife%';
```

```text
 id |              title               
----+----------------------------------
 40 | Chef's Knife 20cm
 41 | Santoku Knife 18cm
 42 | Paring Knife Set (3 pcs)
 43 | Bread Knife 23cm
 45 | Knife Sharpening Stone 1000/6000
(5 rows)
```

`_` is useful for fixed-format codes. Threadbare Co.'s SKUs look like `TB-TS-001`:

```sql
SELECT id, sku, title FROM products WHERE sku LIKE 'TB-__-00_';
```

That returns all 11 of Threadbare's products. To match a literal `%` or `_`, escape it with a backslash: `LIKE '100\%'` matches the text `100%`.

For anything more complex, PostgreSQL supports POSIX **regular expressions** with `~` (case-sensitive), `~*` (case-insensitive), and `!~`/`!~*` for "doesn't match". A regex matches anywhere in the value unless you anchor it with `^` and `$`. Monitors whose title has a two-digit size in inches:

```sql
SELECT id, title FROM products WHERE title ~ '\d{2}"';
```

```text
 id |            title             
----+------------------------------
  6 | ClearView 24" 1080p Monitor
  7 | ClearView 27" 4K Monitor
  8 | UltraWide 34" Curved Monitor
 16 | Laptop Sleeve 13"
(4 rows)
```

> **In production:** `LIKE '%word%'` and `ILIKE` can't use an ordinary index, so on a big table each such query reads every row. That's fine for 62 products and painful for 200,000. Module 11 shows when an index can help with `LIKE 'prefix%'`, and module 13 covers real search with full-text indexes and trigram indexes.

---

## 4. `NULL` and three-valued logic

### 4.1 True, false and unknown

Module 02 introduced `NULL` as "unknown". In conditions, that gives SQL a **third truth value**: besides true and false, a condition can be *unknown*, which is represented as a boolean `NULL`. Any comparison with `NULL` is unknown: `NULL = 1`, `NULL <> 1`, and even `NULL = NULL`. `AND`, `OR` and `NOT` extend to the third value in the only consistent way: unknown means "could be either", so the result is known only when the other operand settles it.

```sql
SELECT a, b, a AND b AS "a AND b", a OR b AS "a OR b", NOT a AS "NOT a"
FROM (VALUES (true, NULL::boolean), (false, NULL), (NULL, NULL)) AS t(a, b);
```

```text
 a | b | a AND b | a OR b | NOT a 
---+---+---------+--------+-------
 t |   |         | t      | f
 f |   | f       |        | t
   |   |         |        | 
```

(`VALUES` can be used as a little inline table; `AS t(a, b)` names it and its columns.) Read the rows: `true AND unknown` is unknown, since it depends on the unknown part. `false AND unknown` is false whatever the unknown is. `true OR unknown` is true. `NOT unknown` is unknown.

The rule that matters most: **`WHERE` keeps only rows where the condition is true.** Unknown is treated like false for filtering. Most `NULL` bugs come from forgetting that.

### 4.2 Testing for `NULL`

Since `= NULL` is never true, it never matches:

```sql
SELECT count(*) FROM products WHERE description = NULL;
```

```text
 count 
-------
     0
```

The right test is `IS NULL` (and `IS NOT NULL`), which always returns true or false, never unknown:

```sql
SELECT id, title, status FROM products WHERE description IS NULL;
```

```text
 id |                  title                   | status 
----+------------------------------------------+--------
  9 | PortaScreen 15.6" USB-C Portable Monitor | active
 14 | BassBoom Over-Ear Headphones             | draft
 22 | Echoes of Andromeda                      | active
 28 | Midnight on the Orient Line              | draft
 32 | Baking with Grandma                      | active
 39 | Pasta Pot with Strainer 6L               | active
 55 | Fleece-Lined Winter Hoodie               | draft
 60 | Cedar Raised Garden Bed                  | active
(8 rows)
```

### 4.3 Negations lose `NULL` rows

The classic bug: "products whose description doesn't mention laptops".

```sql
SELECT count(*) FROM products WHERE description LIKE '%laptop%';        -- 5
SELECT count(*) FROM products WHERE description NOT LIKE '%laptop%';    -- 49
```

5 + 49 = 54, but there are 62 products. The 8 products without a description are in *neither* result: for them, both `LIKE` and `NOT LIKE` are unknown. If "no description" should count as "doesn't mention laptops", say so:

```sql
SELECT count(*) FROM products
WHERE description NOT LIKE '%laptop%' OR description IS NULL;           -- 57
```

The same happens with `<>`. "Categories whose parent is not Electronics (category 1)", written `WHERE parent_id <> 1`, returns 17 rows and silently drops the four root categories, whose `parent_id` is `NULL`.

### 4.4 `IS DISTINCT FROM`

`a IS DISTINCT FROM b` is a `<>` that treats `NULL` as an ordinary, comparable value: two `NULL`s are not distinct, and `NULL` is distinct from any non-null value. It's always true or false. `IS NOT DISTINCT FROM` is the null-safe `=`:

```sql
SELECT count(*) FROM categories WHERE parent_id IS DISTINCT FROM 1;
```

```text
 count 
-------
    21
```

All 23 categories except the two children of Electronics, roots included. Use it whenever you compare columns that may be `NULL` and want "different" to include "one is missing", for example to detect changed values: `WHERE old.description IS DISTINCT FROM new.description`.

### 4.5 The `NOT IN` trap

`x IN (a, b, c)` is `x = a OR x = b OR x = c`, and `x NOT IN (a, b, c)` is `x <> a AND x <> b AND x <> c`. If the list contains a `NULL`, one of those `<>` comparisons is unknown, and an `AND` with an unknown part can never be true:

```sql
SELECT 3 NOT IN (1, 2) AS a, 3 NOT IN (1, 2, NULL) AS b, 2 NOT IN (1, 2, NULL) AS c;
```

```text
 a | b | c 
---+---+---
 t |   | f
```

`3 NOT IN (1, 2, NULL)` is unknown, not true: maybe the unknown value is 3. With a literal list that looks contrived. It bites when the list comes from a query. "Leaf categories are those that are nobody's parent":

```sql
SELECT id, name FROM categories WHERE id NOT IN (SELECT parent_id FROM categories);
```

```text
 id | name 
----+------
(0 rows)
```

Zero rows, though the tree obviously has leaves. The subquery's list contains the `NULL` parents of the four roots, so `NOT IN` is never true. Excluding the `NULL`s fixes it:

```sql
SELECT id, name
FROM categories
WHERE id NOT IN (SELECT parent_id FROM categories WHERE parent_id IS NOT NULL)
ORDER BY id;
```

which returns the 13 leaf categories (Laptops, Monitors, Headphones, Speakers, Cookware, ...). Module 04 shows the robust alternative, `NOT EXISTS`, which doesn't have this trap. Until then: **never use `NOT IN` with a list that could contain `NULL`.** (`IN` is safe: `3 IN (1, 2, NULL)` is unknown, so the row is dropped, which is the right answer anyway.)

### 4.6 `COALESCE` and `NULLIF`

`coalesce(a, b, ...)` returns its first non-null argument. It's the standard way to substitute a value for `NULL`:

```sql
SELECT id, title, coalesce(description, '(no description yet)') AS description
FROM products
WHERE id BETWEEN 8 AND 10;
```

```text
 id |                  title                   |                          description                           
----+------------------------------------------+----------------------------------------------------------------
  8 | UltraWide 34" Curved Monitor             | Immersive curved ultrawide display for productivity and games.
  9 | PortaScreen 15.6" USB-C Portable Monitor | (no description yet)
 10 | QuietWave ANC Headphones                 | Wireless over-ear headphones with active noise cancelling.
(3 rows)
```

`nullif(a, b)` does the opposite: it returns `NULL` if `a = b`, and `a` otherwise. Its classic use is avoiding division by zero. "What share of each product's stock is reserved?":

```sql
SELECT product_id, quantity, reserved,
       round(100.0 * reserved / quantity, 1) AS pct_reserved
FROM inventory
WHERE product_id IN (3, 4, 5);
```

```text
ERROR:  division by zero
```

Product 5 has a quantity of 0. With `nullif(quantity, 0)`, the division becomes a division by `NULL`, which is `NULL`, an honest "not defined":

```sql
SELECT product_id, quantity, reserved,
       round(100.0 * reserved / nullif(quantity, 0), 1) AS pct_reserved
FROM inventory
WHERE product_id IN (3, 4, 5);
```

```text
 product_id | quantity | reserved | pct_reserved 
------------+----------+----------+--------------
          3 |        2 |        2 |        100.0
          4 |       40 |        3 |          7.5
          5 |        0 |        0 |             
(3 rows)
```

### 4.7 `NULL` in expressions

Almost every operator and function returns `NULL` when an input is `NULL` (these are called *strict* functions). That includes arithmetic (`price + NULL`) and string concatenation with `||`. `concat()` is an exception that treats `NULL` as an empty string:

```sql
SELECT 'Order ' || 42 || ' shipped at ' || NULL       AS with_pipes,
       concat('Order ', 42, ' shipped at ', NULL)     AS with_concat;
```

```text
 with_pipes |     with_concat      
------------+----------------------
            | Order 42 shipped at 
```

Aggregates such as `count(column)`, `sum` and `avg` skip `NULL`s, while `count(*)` counts rows:

```sql
SELECT count(*) AS all_products, count(description) AS with_description FROM products;
```

```text
 all_products | with_description 
--------------+------------------
           62 |               54
```

Module 05 builds on that.

---

## 5. Sorting with `ORDER BY`

Without `ORDER BY`, the order of rows is **undefined**. It's whatever order the server found them in, and it can change after an `UPDATE`, a `VACUUM`, a new index or a different plan. If the order matters, ask for it.

```sql
SELECT id, title, price FROM products WHERE seller_id = 4 ORDER BY price DESC;
```

```text
 id |            title            | price  
----+-----------------------------+--------
 56 | Merino Wool Hoodie          | 119.00
 55 | Fleece-Lined Winter Hoodie  |  69.00
 53 | Heavyweight Pullover Hoodie |  59.00
 52 | Classic Zip Hoodie - Grey   |  49.00
 50 | Linen Summer Shirt          |  39.00
 54 | Kids' Rainbow Hoodie        |  34.00
 49 | Vintage Logo Tee            |  24.00
 51 | Graphic Tee 'Ctrl+Alt+Del'  |  22.00
 47 | Organic Cotton Tee - White  |  19.00
 48 | Organic Cotton Tee - Black  |  19.00
 62 | Bazaar Sticker Pack         |   0.00
(11 rows)
```

`ASC` (ascending) is the default, and `DESC` reverses it. Notice products 47 and 48: both cost 19.00, so their relative order is **not defined**. Today 47 comes first, but nothing guarantees it. When a stable order matters (pagination, reports, tests), add a **tie-breaker** that makes the sort key unique, typically the primary key: `ORDER BY price DESC, id`.

### 5.1 Several sort keys

Keys are applied left to right: sort by the first, then by the second within equal first values, and so on. Each key has its own direction:

```sql
SELECT seller_id, id, title, price
FROM products
WHERE category_id IN (10, 11, 22)
ORDER BY seller_id, price DESC, id;
```

```text
 seller_id | id |              title               | price  
-----------+----+----------------------------------+--------
         3 | 38 | Copper Sauté Pan                 | 189.00
         3 | 37 | Dutch Oven 5.5L Enamel           | 119.00
         3 | 40 | Chef's Knife 20cm                |  89.00
         3 | 41 | Santoku Knife 18cm               |  79.00
         3 | 39 | Pasta Pot with Strainer 6L       |  54.00
         3 | 34 | Cast Iron Skillet 26cm           |  45.00
         3 | 35 | Stainless Steel Saucepan 2L      |  39.00
         3 | 43 | Bread Knife 23cm                 |  35.00
         3 | 36 | Non-Stick Frying Pan 28cm        |  34.99
         3 | 45 | Knife Sharpening Stone 1000/6000 |  32.00
         3 | 42 | Paring Knife Set (3 pcs)         |  29.00
         3 | 44 | Bamboo Cutting Board             |  24.00
         4 | 50 | Linen Summer Shirt               |  39.00
         4 | 49 | Vintage Logo Tee                 |  24.00
         4 | 51 | Graphic Tee 'Ctrl+Alt+Del'       |  22.00
         4 | 47 | Organic Cotton Tee - White       |  19.00
         4 | 48 | Organic Cotton Tee - Black       |  19.00
(17 rows)
```

You can sort by any expression, by an output alias (section 2.3), or by the output column's position (`ORDER BY 2 DESC`). Positions are handy at the prompt but fragile in code, since they break silently when someone edits the select list.

### 5.2 Where do `NULL`s go?

PostgreSQL sorts `NULL` as if it were **larger than every value**: last in ascending order, first in descending order. That's rarely what you want for "most recent first":

```sql
SELECT id, status, shipped_at
FROM orders
WHERE id BETWEEN 103 AND 110
ORDER BY shipped_at DESC;
```

```text
 id  |  status   |       shipped_at       
-----+-----------+------------------------
 110 | paid      | 
 109 | shipped   | 2025-06-26 07:39:49+00
 107 | shipped   | 2025-06-25 21:02:09+00
 108 | delivered | 2025-06-23 17:38:28+00
 106 | shipped   | 2025-06-23 04:52:57+00
 105 | shipped   | 2025-06-22 23:33:47+00
 104 | delivered | 2025-06-21 05:30:36+00
 103 | delivered | 2025-06-21 00:50:55+00
(8 rows)
```

The unshipped order 110 tops the "most recently shipped" list. `NULLS LAST` (or `NULLS FIRST`) overrides the default for one key:

```sql
SELECT id, status, shipped_at
FROM orders
WHERE id BETWEEN 103 AND 110
ORDER BY shipped_at DESC NULLS LAST;
```

```text
 id  |  status   |       shipped_at       
-----+-----------+------------------------
 109 | shipped   | 2025-06-26 07:39:49+00
 107 | shipped   | 2025-06-25 21:02:09+00
 108 | delivered | 2025-06-23 17:38:28+00
 106 | shipped   | 2025-06-23 04:52:57+00
 105 | shipped   | 2025-06-22 23:33:47+00
 104 | delivered | 2025-06-21 05:30:36+00
 103 | delivered | 2025-06-21 00:50:55+00
 110 | paid      | 
(8 rows)
```

### 5.3 Collations: how text sorts

Text is sorted by the column's **collation**, the language rules for ordering and comparing strings. The course database uses `en_US.utf8`, which sorts the way a dictionary does: case and accents only matter to break ties. The `"C"` collation sorts by the raw byte values, so all upper-case ASCII letters come before all lower-case ones, and accented letters (which are multi-byte in UTF-8) come after `z`:

```sql
SELECT x FROM (VALUES ('apple'), ('Banana'), ('cherry'), ('Éclair'), ('eclair'), ('zebra')) AS t(x)
ORDER BY x;
SELECT x FROM (VALUES ('apple'), ('Banana'), ('cherry'), ('Éclair'), ('eclair'), ('zebra')) AS t(x)
ORDER BY x COLLATE "C";
```

```text
   x    
--------
 apple
 Banana
 cherry
 eclair
 Éclair
 zebra
(6 rows)

   x    
--------
 Banana
 apple
 cherry
 eclair
 zebra
 Éclair
(6 rows)
```

`COLLATE` can be applied to any text expression. Linguistic collations are what users expect to see. `"C"` is faster and gives a stable, locale-independent order, which is sometimes what you want for codes and identifiers. Collations also affect indexes and comparisons, a topic modules 11 and 13 return to.

---

## 6. `LIMIT`, `OFFSET` and `FETCH FIRST`

`LIMIT n` returns at most `n` rows, and `OFFSET m` skips the first `m` rows first. Without `ORDER BY` they return an arbitrary subset, so the two practically always go together. The three most expensive active products:

```sql
SELECT id, title, price
FROM products
WHERE status = 'active'
ORDER BY price DESC
LIMIT 3;
```

```text
 id |          title          |  price  
----+-------------------------+---------
  3 | Titan G17 Gaming Laptop | 2199.99
 15 | Titan G15 Gaming Laptop | 1799.00
  2 | AeroBook 15 Pro Laptop  | 1499.00
(3 rows)
```

and "page 2", the next three:

```sql
SELECT id, title, price
FROM products
WHERE status = 'active'
ORDER BY price DESC, id
LIMIT 3 OFFSET 3;
```

```text
 id |            title             | price  
----+------------------------------+--------
  1 | AeroBook 13 Laptop           | 999.00
  8 | UltraWide 34" Curved Monitor | 549.00
  7 | ClearView 27" 4K Monitor     | 379.00
(3 rows)
```

The SQL standard spells `LIMIT n` as `FETCH FIRST n ROWS ONLY` (and `OFFSET m ROWS`). The standard form has one feature `LIMIT` lacks: **`WITH TIES`**, which also returns any further rows that tie with the last one on the `ORDER BY` key. "The five cheapest delivered orders":

```sql
SELECT id, total FROM orders WHERE status = 'delivered' ORDER BY total LIMIT 5;
```

```text
 id | total 
----+-------
 20 |  9.99
 66 | 12.00
  7 | 16.00
 25 | 16.00
 50 | 19.00
(5 rows)
```

```sql
SELECT id, total FROM orders WHERE status = 'delivered' ORDER BY total FETCH FIRST 5 ROWS WITH TIES;
```

```text
 id | total 
----+-------
 20 |  9.99
 66 | 12.00
  7 | 16.00
 25 | 16.00
 50 | 19.00
 57 | 19.00
(6 rows)
```

Order 57 also totals 19.00. With `LIMIT 5`, whether you see order 50 or order 57 is arbitrary. `WITH TIES` gives an answer that doesn't depend on luck.

> **In production:** `OFFSET` pagination gets slower the deeper you page, because the server has to produce and throw away all the skipped rows (page 1,000 of 20 rows reads 20,000 rows). It also shows duplicates or skips rows if data changes between page loads. Module 12 covers **keyset pagination** (`WHERE (price, id) < (last_price, last_id) ORDER BY price DESC, id DESC LIMIT 20`), which doesn't have either problem.

---

## 7. `DISTINCT`

`SELECT DISTINCT` removes duplicate rows from the result. Which sellers have any orders at all?

```sql
SELECT DISTINCT seller_id FROM orders ORDER BY seller_id;
```

```text
 seller_id 
-----------
         1
         2
         3
         4
(4 rows)
```

(Sellers 5 and 6 never sold anything, as NOTES.md promises.) With several columns, `DISTINCT` applies to the *combination*:

```sql
SELECT DISTINCT seller_id, status
FROM orders
WHERE status IN ('pending', 'paid')
ORDER BY seller_id, status;
```

```text
 seller_id | status  
-----------+---------
         1 | paid
         1 | pending
         2 | pending
         3 | paid
         4 | paid
         4 | pending
(6 rows)
```

`count(DISTINCT column)` counts distinct non-null values:

```sql
SELECT count(DISTINCT user_id) AS buyers, count(*) AS orders FROM orders;
```

```text
 buyers | orders 
--------+--------
     21 |    120
```

For `DISTINCT`, two `NULL`s count as duplicates of each other, unlike with `=`. That's the same convention as `IS NOT DISTINCT FROM`, hence its name.

> **Pitfall:** `DISTINCT` is often used to hide duplicates that a query shouldn't have produced in the first place, typically from a join that multiplies rows (module 04). If you're reaching for `DISTINCT` to "fix" a result, first find out where the duplicates come from. PostgreSQL's `DISTINCT ON (...)`, which keeps one row per group, is a different tool, covered in module 05.

---

## 8. Operators and functions

PostgreSQL has hundreds of built-in functions. `\df` lists them, and `\df *trim*` finds them by name (module 01). Here are the ones you'll use daily.

### 8.1 Text

```sql
SELECT full_name,
       upper(full_name)                          AS upper,
       length(full_name)                         AS len,
       split_part(full_name, ' ', 1)             AS first_word,
       split_part(email, '@', 2)                 AS domain,
       left(email, position('@' IN email) - 1)   AS local_part
FROM users
WHERE id IN (5, 12, 17, 18);
```

```text
   full_name    |     upper      | len | first_word |   domain    |    local_part    
----------------+----------------+-----+------------+-------------+------------------
 Chloé Tremblay | CHLOÉ TREMBLAY |  14 | Chloé      | example.ca  | chloe.tremblay
 Liam O'Connor  | LIAM O'CONNOR  |  13 | Liam       | example.ie  | liam.oconnor
 Zoë Martin     | ZOË MARTIN     |  10 | Zoë        | Example.COM | Zoe.Martin
 Ethan Kim      | ETHAN KIM      |   9 | Ethan      | example.com | ethan.kim+bazaar
(4 rows)
```

| Function | Example | Result |
|---|---|---|
| `lower(s)`, `upper(s)`, `initcap(s)` | `initcap('liam o''connor')` | `Liam O'Connor` |
| `length(s)` | `length('Chloé')` | `5` (characters, not bytes) |
| `trim(s)`, `ltrim`, `rtrim` | `trim('  Mug  ')` | `Mug` |
| `substr(s, start, n)`, `left(s, n)`, `right(s, n)` | `substr('TT-LAP-001', 4, 3)` | `LAP` |
| `position(sub IN s)`, `strpos(s, sub)` | `position('@' IN 'a@b')` | `2` (0 if not found) |
| `replace(s, from, to)` | `replace('TT-LAP-001', '-', '')` | `TTLAP001` |
| `split_part(s, delim, n)` | `split_part('a@b.com', '@', 2)` | `b.com` |
| `s1 \|\| s2`, `concat(...)`, `concat_ws(sep, ...)` | `concat_ws(' / ', 'Books', NULL, 'Fiction')` | `Books / Fiction` |
| `regexp_replace(s, pattern, repl, flags)` | `regexp_replace('a  b', '\s+', ' ', 'g')` | `a b` |
| `format(fmt, ...)` | `format('%s costs %s', 'Mug', 12.5)` | `Mug costs 12.5` |

`format` deserves a special mention. `%s` inserts a value as text, `%L` inserts it as a correctly quoted SQL literal, and `%I` as a correctly quoted identifier:

```sql
SELECT format('%s costs %s EUR', title, price)        AS line,
       format('INSERT INTO t VALUES (%L)', title)      AS quoted
FROM products
WHERE id = 25;
```

```text
                      line                      |                          quoted                          
------------------------------------------------+----------------------------------------------------------
 The Lighthouse Keeper's Secret costs 13.49 EUR | INSERT INTO t VALUES ('The Lighthouse Keeper''s Secret')
```

The apostrophe was doubled automatically. That matters when SQL builds SQL (module 09's dynamic SQL, and module 01's `\gexec`).

### 8.2 Numbers

The arithmetic operators are `+`, `-`, `*`, `/`, `%` (remainder) and `^` (power). Module 02 showed that **integer division truncates**. Make one operand `numeric` *before* dividing, since casting the result afterwards is too late:

```sql
SELECT 17 / 5 AS int_div, 17 % 5 AS modulo, 17::numeric / 5 AS num_div, (17 / 5)::numeric AS too_late;
```

```text
 int_div | modulo |      num_div       | too_late 
---------+--------+--------------------+----------
       3 |      2 | 3.4000000000000000 |        3
```

Rounding functions: `round(x, n)` rounds to `n` decimals, half away from zero, for `numeric`. `trunc(x, n)` cuts off. `ceil(x)` and `floor(x)` round up and down to whole numbers. A 15 % sale:

```sql
SELECT title, price,
       round(price * 0.85, 2) AS sale_price,
       trunc(price * 0.85, 2) AS truncated,
       ceil(price)            AS ceil,
       floor(price)           AS floor
FROM products
WHERE id IN (19, 25, 36);
```

```text
             title              | price | sale_price | truncated | ceil | floor 
--------------------------------+-------+------------+-----------+------+-------
 The Long Orbit                 | 14.99 |      12.74 |     12.74 |   15 |    14
 The Lighthouse Keeper's Secret | 13.49 |      11.47 |     11.46 |   14 |    13
 Non-Stick Frying Pan 28cm      | 34.99 |      29.74 |     29.74 |   35 |    34
(3 rows)
```

> **Pitfall:** `round` on `double precision` uses a different rule, round-half-to-even ("banker's rounding"): `round(2.5::float8)` is 2, but `round(2.5)` (a `numeric`) is 3. One more reason money stays `numeric`.

`abs`, `sign`, `power`, `sqrt`, `greatest` and `least` (next section) round out the everyday set.

### 8.3 Dates and times

The current moment is `now()` (the start of the transaction, module 02) and today's date is `current_date`. The seed data never depends on them, so the examples below use fixed timestamps from the orders.

**`date_trunc(unit, ts)`** rounds a timestamp *down* to the start of a unit: `'hour'`, `'day'`, `'week'` (Monday), `'month'`, `'quarter'`, `'year'`. It's the basis of every "per day / per month" report (module 05). **`extract(field FROM ts)`** pulls out one field as a number:

```sql
SELECT id, created_at,
       date_trunc('month', created_at) AS month,
       date_trunc('day', created_at)   AS day,
       extract(year FROM created_at)   AS year,
       extract(dow FROM created_at)    AS weekday,
       extract(hour FROM created_at)   AS hour
FROM orders
WHERE id IN (1, 77, 120);
```

```text
 id  |       created_at       |         month          |          day           | year | weekday | hour 
-----+------------------------+------------------------+------------------------+------+---------+------
   1 | 2024-01-12 10:39:36+00 | 2024-01-01 00:00:00+00 | 2024-01-12 00:00:00+00 | 2024 |       5 |   10
  77 | 2025-02-28 14:25:55+00 | 2025-02-01 00:00:00+00 | 2025-02-28 00:00:00+00 | 2025 |       5 |   14
 120 | 2025-06-30 09:54:52+00 | 2025-06-01 00:00:00+00 | 2025-06-30 00:00:00+00 | 2025 |       1 |    9
(3 rows)
```

`dow` is the day of the week, 0 = Sunday to 6 = Saturday (`isodow` is 1 = Monday to 7 = Sunday). Other fields include `month`, `day`, `quarter`, `week`, `doy` (day of year) and `epoch` (seconds since 1970-01-01 UTC). Both functions work in the session time zone for `timestamptz`: the "day" of an order is the UTC day here, and a Berlin day if the session is in Berlin. For a specific zone, convert first with `AT TIME ZONE` (module 02).

**Arithmetic.** `timestamp ± interval` gives a timestamp, and `timestamp - timestamp` gives an interval:

```sql
SELECT id, created_at, created_at + interval '14 days' AS return_deadline
FROM orders
WHERE id = 120;
```

```text
 id  |       created_at       |    return_deadline     
-----+------------------------+------------------------
 120 | 2025-06-30 09:54:52+00 | 2025-07-14 09:54:52+00
```

```sql
SELECT id,
       delivered_at - created_at AS delivery_time,
       extract(epoch FROM delivered_at - created_at) / 3600 AS hours
FROM orders
WHERE id IN (1, 2, 40);
```

```text
 id |  delivery_time  |        hours         
----+-----------------+----------------------
  1 | 7 days 17:58:00 | 185.9666666666666667
  2 | 7 days 05:46:00 | 173.7666666666666667
 40 | 7 days 04:26:00 | 172.4333333333333333
(3 rows)
```

`extract(epoch FROM interval)` turns a duration into seconds, the easiest way to compare or average durations as numbers. **`age(a, b)`** gives a "human" difference in years, months and days, while subtracting two dates gives a plain number of days:

```sql
SELECT age('2025-06-30', '2024-01-12') AS age, date '2025-06-30' - date '2024-01-12' AS days;
```

```text
          age          | days 
-----------------------+------
 1 year 5 mons 18 days |  535
```

`age(ts)` with one argument measures from `current_date`, which makes it unsuitable for reproducible reports. Pass both ends.

**Formatting.** `to_char(value, pattern)` formats timestamps and numbers as text:

```sql
SELECT to_char(created_at, 'YYYY-MM-DD HH24:MI') AS iso,
       to_char(created_at, 'Dy DD Mon YYYY')     AS human,
       to_char(total, 'FM999G990D00')            AS money
FROM orders
WHERE id = 40;
```

```text
       iso        |      human      |  money   
------------------+-----------------+----------
 2024-08-06 15:10 | Tue 06 Aug 2024 | 8,940.00
```

The opposite direction, text to date or timestamp with an explicit pattern, is `to_date(text, pattern)` and `to_timestamp(text, pattern)`, as in `to_date('30/06/2025', 'DD/MM/YYYY')`. Use them for input in non-ISO formats rather than relying on the `DateStyle` setting. `make_date(2025, 2, 28)` and `make_timestamptz(...)` build values from parts.

Formatting belongs at the edge. Keep values as timestamps and numbers while you compute, sort and compare, and turn them into text only for display, preferably in the application, which knows the user's language.

### 8.4 Casting

A **cast** converts a value from one type to another. PostgreSQL has two spellings: the standard `CAST(value AS type)` and the shorter `value::type`.

```sql
SELECT '42'::int + 1                  AS a,
       CAST('2025-06-30' AS date)     AS b,
       42::text || '!'                AS c,
       '19.99'::numeric(12,2) * 2     AS d;
```

```text
 a  |     b      |  c  |   d   
----+------------+-----+-------
 43 | 2025-06-30 | 42! | 39.98
```

Casting text that isn't valid for the target type is an error:

```sql
SELECT 'abc'::int;
```

```text
ERROR:  invalid input syntax for type integer: "abc"
```

When validating data, as in this module's milestone, you want to *test* whether a value converts, not crash on the first bad one. PostgreSQL 16 added **`pg_input_is_valid(text, type)`**, and its companion `pg_input_error_info` explains why a value fails:

```sql
SELECT pg_input_is_valid('12,50', 'numeric')        AS comma,
       pg_input_is_valid('12.50', 'numeric(12,2)')  AS dot,
       pg_input_is_valid('31/02/2025', 'date')      AS bad_date;
```

```text
 comma | dot | bad_date 
-------+-----+----------
 f     | t   | f
```

```sql
SELECT message, sql_error_code FROM pg_input_error_info('12,50', 'numeric');
```

```text
                    message                     | sql_error_code 
------------------------------------------------+----------------
 invalid input syntax for type numeric: "12,50" | 22P02
```

Many casts happen implicitly. A quoted literal like `'2025-02-01'` has no type of its own (`unknown`) until context gives it one, which is why `created_at >= '2025-02-01'` works without a cast.

### 8.5 `CASE`

`CASE` is SQL's if/else, and it's an *expression*, so it can appear anywhere a value can. The **searched** form tests conditions in order and returns the first match:

```sql
SELECT id, title, price,
       CASE
           WHEN price = 0   THEN 'free'
           WHEN price < 25  THEN 'budget'
           WHEN price < 200 THEN 'mid-range'
           ELSE 'premium'
       END AS price_band
FROM products
WHERE id IN (3, 8, 19, 40, 62);
```

```text
 id |            title             |  price  | price_band 
----+------------------------------+---------+------------
  3 | Titan G17 Gaming Laptop      | 2199.99 | premium
  8 | UltraWide 34" Curved Monitor |  549.00 | premium
 19 | The Long Orbit               |   14.99 | budget
 40 | Chef's Knife 20cm            |   89.00 | mid-range
 62 | Bazaar Sticker Pack          |    0.00 | free
(5 rows)
```

The **simple** form compares one expression against values:

```sql
SELECT id, status,
       CASE status
           WHEN 'pending' THEN 'Awaiting payment'
           WHEN 'paid'    THEN 'Preparing'
           WHEN 'shipped' THEN 'On its way'
           ELSE initcap(status)
       END AS customer_label
FROM orders
WHERE id IN (1, 18, 31, 105, 110, 115);
```

```text
 id  |  status   |  customer_label  
-----+-----------+------------------
   1 | delivered | Delivered
  18 | refunded  | Refunded
  31 | cancelled | Cancelled
 105 | shipped   | On its way
 110 | paid      | Preparing
 115 | pending   | Awaiting payment
(6 rows)
```

Without an `ELSE`, a `CASE` with no matching branch returns `NULL`. And the simple form compares with `=`, so `CASE x WHEN NULL THEN ...` never matches. Use the searched form, `WHEN x IS NULL`.

### 8.6 `GREATEST` and `LEAST`

`greatest(...)` and `least(...)` return the largest and smallest of their arguments, ignoring `NULL`s. They're handy for clamping. "Units we can sell while keeping 5 in reserve, never negative":

```sql
SELECT product_id, quantity, reserved,
       quantity - reserved                   AS available,
       greatest(quantity - reserved - 5, 0)  AS sellable_above_safety
FROM inventory
WHERE product_id IN (3, 4, 13, 15);
```

```text
 product_id | quantity | reserved | available | sellable_above_safety 
------------+----------+----------+-----------+-----------------------
          3 |        2 |        2 |         0 |                     0
          4 |       40 |        3 |        37 |                    32
         13 |        1 |        1 |         0 |                     0
         15 |        4 |        0 |         4 |                     0
(4 rows)
```

---

## 9. A safety net: transactions

The rest of this module changes data. Before you run any `UPDATE` or `DELETE`, learn the habit that makes experiments safe: wrap them in a **transaction**.

A transaction groups statements into one unit that either takes effect completely or not at all. `BEGIN` starts it, `COMMIT` makes its changes permanent and visible to everyone else, and `ROLLBACK` throws all of them away:

```sql
BEGIN;
UPDATE products SET price = price * 2 WHERE seller_id = 2;
SELECT id, title, price FROM products WHERE id IN (19, 20);
```

```text
UPDATE 15
 id |          title           | price 
----+--------------------------+-------
 19 | The Long Orbit           | 29.98
 20 | Children of the Red Dust | 25.00
(2 rows)
```

Inside the transaction you see your own changes. Other sessions don't: until you commit, they keep seeing the old prices. Changed your mind?

```sql
ROLLBACK;
SELECT id, title, price FROM products WHERE id IN (19, 20);
```

```text
 id |          title           | price 
----+--------------------------+-------
 19 | The Long Orbit           | 14.99
 20 | Children of the Red Dust | 12.50
(2 rows)
```

Without `BEGIN`, psql runs in **autocommit** mode: every statement is its own transaction and is committed as soon as it succeeds. There's no undo.

When a statement inside a transaction fails, the transaction is **aborted**: PostgreSQL refuses every further statement until you end it, and ending it can only roll back:

```sql
BEGIN;
SELECT 1/0;
SELECT 1;
ROLLBACK;
```

```text
ERROR:  division by zero
ERROR:  current transaction is aborted, commands ignored until end of transaction block
```

That's what makes a transaction all-or-nothing: nothing after a failure can sneak in. At the psql prompt it's also annoying, since one typo kills an hour of careful experimenting. Module 01's `\set ON_ERROR_ROLLBACK interactive` fixes that: psql then wraps each statement in a savepoint and quietly rolls back just the failed statement.

The workflow for every risky change in this course, and in production:

1. `BEGIN;`
2. Run the `UPDATE` or `DELETE`, ideally with `RETURNING` (next section) so you see what it touched.
3. Check the command tag (`UPDATE 15`) and the data.
4. `COMMIT;` if it's right, `ROLLBACK;` if it isn't.

Transactions do much more than provide an undo button: they isolate concurrent users from each other's half-finished work, and they're how checkout makes "reserve stock, create order, record payment" atomic. Module 10 covers isolation levels, locking and concurrency in depth.

---

## 10. Changing data

### 10.1 `INSERT ... RETURNING` and `INSERT ... SELECT`

Maple & Pine lists a new planter. The application needs the generated id, for the next step and to redirect the seller to the product page. `RETURNING` hands back any columns of the inserted row, including defaults:

```sql
BEGIN;

INSERT INTO products (seller_id, category_id, sku, title, price)
VALUES (5, 13, 'MP-PL-005', 'Concrete Planter 30cm', 39.00)
RETURNING id, status;
```

```text
 id | status 
----+--------
 63 | draft
(1 row)

INSERT 0 1
```

(If you've run other inserts since `reset.sh 04`, your id will be higher. Ids are never reused.) Every product needs an inventory row. `INSERT ... SELECT` inserts the result of a query instead of a `VALUES` list, so the new id doesn't have to travel through the application:

```sql
INSERT INTO inventory (product_id, quantity)
SELECT id, 0
FROM products
WHERE seller_id = 5 AND sku = 'MP-PL-005'
RETURNING product_id, quantity, reserved;

ROLLBACK;
```

```text
 product_id | quantity | reserved 
------------+----------+----------
         63 |        0 |        0
(1 row)

INSERT 0 1
```

`INSERT ... SELECT` is also how you copy data between tables, archive old rows, or move validated rows out of a staging table. (Module 06's common table expressions can chain the two inserts in a single statement.)

### 10.2 `UPDATE`

`UPDATE` changes column values in existing rows. `SET` lists the new values, which can be expressions over the row's *current* values, and `WHERE` chooses the rows. Casa Cucina raises cutlery prices by 10 %:

```sql
BEGIN;

UPDATE products
SET price      = round(price * 1.10, 2),
    updated_at = now()
WHERE seller_id = 3
  AND category_id = 11
RETURNING id, title, price;
```

```text
 id |              title               | price 
----+----------------------------------+-------
 40 | Chef's Knife 20cm                | 97.90
 41 | Santoku Knife 18cm               | 86.90
 42 | Paring Knife Set (3 pcs)         | 31.90
 43 | Bread Knife 23cm                 | 38.50
 44 | Bamboo Cutting Board             | 26.40
 45 | Knife Sharpening Stone 1000/6000 | 35.20
(6 rows)

UPDATE 6
```

```sql
ROLLBACK;
```

All expressions on the right-hand side see the row as it was **before** the update, so `SET a = b, b = a` swaps two columns. `updated_at` has to be set by hand for now. Module 09 adds a trigger that does it automatically.

> **Under the hood:** PostgreSQL never overwrites a row in place. An `UPDATE` writes a complete new version of the row and marks the old one as dead, a design called MVCC that lets readers keep seeing the old version until the change commits (module 10). Dead versions are cleaned up later by `VACUUM` (module 16). One practical consequence: `UPDATE products SET price = price * 1.1` on a 10 GB table writes roughly another 10 GB, plus index entries and write-ahead log. Updating "everything" is never free.

An `UPDATE` must satisfy every constraint, just like an `INSERT`. Product 3 is fully reserved, so reserving one more unit violates the check from module 02:

```sql
UPDATE inventory SET reserved = reserved + 1 WHERE product_id = 3;
```

```text
ERROR:  new row for relation "inventory" violates check constraint "inventory_reserved_within_quantity"
DETAIL:  Failing row contains (3, 2, 3, 2025-06-30 23:00:00+00).
```

That's the guarantee that makes overselling impossible, and module 10 builds checkout on it.

An `UPDATE` whose `WHERE` matches nothing isn't an error. It reports `UPDATE 0`. That makes `WHERE` a good place for *preconditions*. Ship order 110, but only if it's still in the `paid` state:

```sql
BEGIN;

UPDATE orders
SET status = 'shipped', shipped_at = '2025-07-01 10:00+00'
WHERE id = 110 AND status = 'paid'
RETURNING id, status, paid_at, shipped_at;
```

```text
 id  | status  |        paid_at         |       shipped_at       
-----+---------+------------------------+------------------------
 110 | shipped | 2025-06-25 01:35:02+00 | 2025-07-01 10:00:00+00
(1 row)

UPDATE 1
```

Run the same statement again, and it reports `UPDATE 0`: the order isn't `paid` any more, so it can't be shipped twice. An application checks the row count to know whether the state change happened. This "conditional update" pattern is also a simple, race-free way to handle two workers trying to ship the same order (module 10).

```sql
ROLLBACK;
```

**`UPDATE ... FROM`: a preview.** Sometimes the rows to update are chosen by looking at *another* table. "Archive all draft products of the shop called *Maple & Pine*": the shop name is in `sellers`, the status in `products`. PostgreSQL's `UPDATE ... FROM` lists the extra table, and the `WHERE` clause connects the two:

```sql
BEGIN;

UPDATE products AS p
SET status = 'archived'
FROM sellers AS s
WHERE s.id = p.seller_id
  AND s.shop_name = 'Maple & Pine'
  AND p.status = 'draft'
RETURNING p.id, p.title, p.status;
```

```text
 id |          title          |  status  
----+-------------------------+----------
 59 | Hanging Macramé Planter | archived
(1 row)

UPDATE 1
```

```sql
ROLLBACK;
```

`AS p` and `AS s` are table aliases, short names for the tables, and `p.status` means "the `status` column of `p`". Connecting rows of two tables like this is a **join**, the subject of module 04, which explains `UPDATE ... FROM` (and `DELETE ... USING`) in full, including the trap where a target row matches several rows of the other table. The milestone uses this form to look up category ids for imported products.

> **Pitfall:** an `UPDATE` without `WHERE` updates **every row**. `UPDATE products SET price = 0;` reports `UPDATE 62` and makes the whole catalogue free. Inside `BEGIN` you can still roll back. In autocommit mode, only a backup can save you (module 18). Many teams write the `WHERE` clause first, or run the same condition as a `SELECT count(*)` first to see how many rows it matches.

### 10.3 `DELETE`

`DELETE FROM table WHERE condition` removes rows. Like `UPDATE`, it supports `RETURNING`, and without `WHERE` it removes everything. Product 61, an archived planter set, was never ordered:

```sql
BEGIN;

DELETE FROM products WHERE id = 61 RETURNING id, title, status;
```

```text
 id |         title         |  status  
----+-----------------------+----------
 61 | Ceramic Herb Pot Trio | archived
(1 row)

DELETE 1
```

Its inventory row went with it, through the `ON DELETE CASCADE` foreign key (`SELECT count(*) FROM inventory WHERE product_id = 61` now returns 0). Product 5, also archived, *was* ordered, and `order_items` protects it:

```sql
DELETE FROM products WHERE id = 5;
```

```text
ERROR:  update or delete on table "products" violates foreign key constraint "order_items_product_id_fkey" on table "order_items"
DETAIL:  Key (id)=(5) is still referenced from table "order_items".
```

```sql
ROLLBACK;
```

A `DELETE` that matches several rows is all-or-nothing too: `DELETE FROM products WHERE status = 'archived'` fails as a whole because of product 5, so product 61, the one archived product that was never ordered (5, 23 and 38 all were), stays as well. Exercise 11 asks you to delete exactly the ones that can go.

> **In production:** a `DELETE` of millions of rows in one statement holds its row locks until it commits, generates a lot of write-ahead log (which replicas must replay, module 19), and leaves millions of dead rows for `VACUUM`. Large clean-ups are usually done in batches (`DELETE ... WHERE id IN (SELECT id ... LIMIT 10000)`, repeated until `DELETE 0`), each in its own short transaction. For time-based data, partitioning (module 14) turns "delete last year" into dropping a partition, which is instant.

### 10.4 `TRUNCATE` versus `DELETE`

`TRUNCATE table` empties a table. It's DDL-like rather than row-by-row:

| | `DELETE FROM t` | `TRUNCATE t` |
|---|---|---|
| Removes | the rows matching `WHERE` (or all) | all rows, always |
| Speed on a large table | slow: finds and marks every row, writes WAL for each | instant: swaps in new, empty files |
| Disk space | freed later by `VACUUM` (module 16) | freed immediately |
| `RETURNING`, row triggers | yes | no |
| Foreign keys | checks each row: cascades, or errors if referenced | refuses if another table references `t`, unless that table is truncated too or `CASCADE` is given |
| Identity sequences | unchanged | unchanged, or reset with `RESTART IDENTITY` |
| Lock | row locks; others can keep reading and writing | `ACCESS EXCLUSIVE`: blocks everyone, even readers |
| Transactional | yes | yes, in PostgreSQL (can be rolled back) |

The foreign-key rule in action:

```sql
BEGIN;
TRUNCATE orders;
```

```text
ERROR:  cannot truncate a table referenced in a foreign key constraint
DETAIL:  Table "order_items" references "orders".
HINT:  Truncate table "order_items" at the same time, or use TRUNCATE ... CASCADE.
```

```sql
ROLLBACK;
```

`TRUNCATE orders, order_items` (or `TRUNCATE orders CASCADE`) works. `TRUNCATE` is the tool for emptying staging tables, resetting test databases (`TRUNCATE users, sellers, ... RESTART IDENTITY`), and clearing out data you're about to reload. It isn't for production tables that other sessions are using: its exclusive lock blocks them all. (Module 09 notes that `TRUNCATE` also bypasses the triggers that keep `orders.total` correct, which fire on `INSERT`, `UPDATE` and `DELETE`.)

---

## 11. Upserts: `INSERT ... ON CONFLICT`

A very common need: "insert this row, but if it already exists, do something else". Loading a seller's catalogue is the typical case: new SKUs should be inserted, and existing ones updated. Doing it in two steps ("does it exist? then update, else insert") has a race: two sessions can both see "doesn't exist" and both insert, and one fails on the unique constraint. PostgreSQL's **`ON CONFLICT`** clause makes the decision atomically, inside the `INSERT`, using a unique constraint as the referee.

### 11.1 `DO NOTHING`

Inserting a category whose slug already exists fails:

```sql
INSERT INTO categories (name, slug) VALUES ('Laptops', 'laptops');
```

```text
ERROR:  duplicate key value violates unique constraint "categories_slug_key"
DETAIL:  Key (slug)=(laptops) already exists.
```

With `ON CONFLICT (slug) DO NOTHING`, conflicting rows are skipped silently, and the others are inserted:

```sql
BEGIN;

INSERT INTO categories (parent_id, name, slug)
VALUES (1, 'Cameras', 'cameras'),
       (2, 'Laptops', 'laptops')
ON CONFLICT (slug) DO NOTHING
RETURNING id, name;
```

```text
 id |  name   
----+---------
 25 | Cameras
(1 row)

INSERT 0 1
```

```sql
ROLLBACK;
```

`RETURNING` reports only the rows actually inserted. The parentheses after `ON CONFLICT` are the **conflict target**: the column(s) of a unique constraint or unique index. PostgreSQL uses it to find the existing row, and it must match a unique constraint exactly:

```text
ERROR:  there is no unique or exclusion constraint matching the ON CONFLICT specification
```

is what you get for `ON CONFLICT (sku)` on `products`, where only the pair `(seller_id, sku)` is unique. `ON CONFLICT ON CONSTRAINT products_seller_id_sku_key` names the constraint instead. `DO NOTHING` may also omit the target, and then it skips conflicts with *any* unique constraint. That's convenient, but it can hide conflicts you didn't anticipate.

### 11.2 `DO UPDATE` and `EXCLUDED`

`DO UPDATE SET ...` updates the existing row instead. Inside it, the existing row is referred to by the table name, and the row you *tried* to insert is called **`EXCLUDED`**. A stock delivery: add the delivered units to whatever is on the shelf, and create inventory rows for products that don't have one:

```sql
BEGIN;

INSERT INTO inventory (product_id, quantity)
VALUES (4, 20), (13, 5)
ON CONFLICT (product_id) DO UPDATE
SET quantity   = inventory.quantity + EXCLUDED.quantity,
    updated_at = now()
RETURNING product_id, quantity, reserved;
```

```text
 product_id | quantity | reserved 
------------+----------+----------
          4 |       60 |        3
         13 |        6 |        1
(2 rows)

INSERT 0 2
```

```sql
ROLLBACK;
```

Both products already had inventory rows (40 and 1 units), so both were updated. A product without one would have been inserted with the delivered quantity. Note that the command tag still says `INSERT 0 2`: it counts rows inserted *or* updated.

`DO UPDATE` can have its own `WHERE`, which decides whether the existing row is updated at all. A catalogue sync that only touches products whose price really changed (so that `updated_at` stays meaningful):

```sql
BEGIN;

INSERT INTO products (seller_id, category_id, sku, title, price, status)
VALUES (3, 11, 'CC-CU-001', 'Chef''s Knife 20cm', 94.00, 'active'),
       (3, 11, 'CC-CU-007', 'Kitchen Shears',     27.50, 'active')
ON CONFLICT (seller_id, sku) DO UPDATE
SET title      = EXCLUDED.title,
    price      = EXCLUDED.price,
    updated_at = now()
WHERE products.price IS DISTINCT FROM EXCLUDED.price
RETURNING id, sku, title, price;
```

```text
 id |    sku    |       title       | price 
----+-----------+-------------------+-------
 40 | CC-CU-001 | Chef's Knife 20cm | 94.00
 65 | CC-CU-007 | Kitchen Shears    | 27.50
(2 rows)

INSERT 0 2
```

The knife (id 40) existed and its price changed from 89.00, so it was updated. The shears are new, so they were inserted. Run the same statement again and it reports `INSERT 0 0`: both rows now conflict, and neither price differs, so nothing happens. That's an **idempotent** load: running it twice has the same effect as running it once, which is exactly what you want from an import job that might be retried. (The shears got id 65 here because the rolled-back insert in section 10.1 and the knife's conflicting row each consumed an id. Yours may differ.)

```sql
ROLLBACK;
```

Three rules to remember:

- **Each existing row can be affected only once per statement.** If the input contains the same key twice, PostgreSQL refuses rather than guessing which one should win:

  ```sql
  INSERT INTO inventory (product_id, quantity) VALUES (4, 20), (4, 5)
  ON CONFLICT (product_id) DO UPDATE SET quantity = inventory.quantity + EXCLUDED.quantity;
  ```

  ```text
  ERROR:  ON CONFLICT DO UPDATE command cannot affect row a second time
  HINT:  Ensure that no rows proposed for insertion within the same command have duplicate constrained values.
  ```

  De-duplicate the input first (the milestone does this in its staging table).
- **Defaults and identity values are computed before the conflict check.** Even a row that ends up updating (or doing nothing) has consumed an identity value. Busy upserts burn through ids quickly, one more reason ids are `bigint`.
- **Other constraints still apply.** `ON CONFLICT` handles only conflicts on its target. A `CHECK` violation or a duplicate on a *different* unique constraint is still an error.

PostgreSQL 15 also added the standard `MERGE` statement, which can insert, update *and* delete based on a comparison with a source table. Module 06 covers it and compares it with `ON CONFLICT`.

---

## Summary

- **`INSERT`** with an explicit column list; multi-row `VALUES` are atomic and faster; `DEFAULT` asks for a column default. Explicit ids need `OVERRIDING SYSTEM VALUE`, followed by a sequence repair (`setval` or `ALTER ... RESTART`).
- **`COPY`/`\copy`** bulk-load files (server side and client side). In CSV, an empty unquoted field is `NULL`. PG 16 adds a `DEFAULT` marker, and PG 17's `ON_ERROR ignore` skips rows that fail type conversion, but not constraint violations. `COPY` doesn't advance identity sequences. For messy input, load into a staging table first.
- A query is evaluated **FROM → WHERE → SELECT → ORDER BY → LIMIT**, which is why `WHERE` can't see select-list aliases and `ORDER BY` can.
- **`WHERE`** keeps rows where the condition is *true*. `AND` binds tighter than `OR`, so use parentheses. `BETWEEN` includes both ends, so use half-open ranges for time. `LIKE`/`ILIKE` match patterns with `%` and `_`; `~` matches regular expressions.
- **Three-valued logic:** comparisons with `NULL` are unknown, and `WHERE` drops unknown rows. Use `IS NULL`, `IS DISTINCT FROM`, and `coalesce`/`nullif`. Negated conditions (`<>`, `NOT LIKE`) silently lose `NULL` rows. **`NOT IN` with a `NULL` in the list is never true.**
- **`ORDER BY`** is the only way to get a defined order. Add a unique tie-breaker, control `NULL` placement with `NULLS FIRST`/`LAST`, and remember collations decide text order. **`LIMIT`/`OFFSET`** need an `ORDER BY`. **`FETCH FIRST n ROWS WITH TIES`** includes ties. **`DISTINCT`** removes duplicate rows.
- Functions: text (`lower`, `split_part`, `replace`, `format` ...), numbers (`round` vs `trunc`, and integer division truncates), time (`date_trunc`, `extract`, interval arithmetic, `age`, `to_char`, `to_date`). Cast with `::` or `CAST`, and test with `pg_input_is_valid`. **`CASE`** is an expression; `greatest`/`least` clamp values.
- **Transactions:** `BEGIN` ... `COMMIT`/`ROLLBACK`. An error aborts the transaction. Wrap every risky change in one and check what it touched before committing.
- **`UPDATE`** and **`DELETE`** with `WHERE` (without it, *every* row) and `RETURNING`. Conditions in `WHERE` make state changes safe (`UPDATE 0` means "not applicable"). `UPDATE ... FROM` uses another table to choose rows (module 04). Constraints and foreign keys apply to every change. **`TRUNCATE`** empties tables fast, under an exclusive lock.
- **Upserts:** `INSERT ... ON CONFLICT (target) DO NOTHING | DO UPDATE SET ... = EXCLUDED.col [WHERE ...]`. The target must match a unique constraint, each row can be affected once per statement, and a `WHERE` on `DO UPDATE` makes loads idempotent.

## What's next

So far every query has read one table at a time. In [Module 04](../04-joins-subqueries/) you combine tables with joins and subqueries: which products a user bought, which sellers have no sales, and the robust alternatives to `NOT IN`.
