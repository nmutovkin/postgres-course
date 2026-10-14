# Module 02: Project milestone — the Bazaar core schema

<!-- nav:start -->
> [Theory](README.md) · [Exercises](exercises.md) · **Project** · [Solutions](solutions/README.md)  
> [← 01 psql and the Postgres Landscape](../01-psql/README.md) · [Course home](../../README.md) · [03 Reading and Writing Data →](../03-crud/README.md)
<!-- nav:end -->

**Goal.** Bazaar needs a home for its data before the first shopper arrives. In this milestone you write the **core schema**: the seven tables that every later module builds on. You write it the way real teams do, as a migration file that's applied once, in a single transaction, to an empty schema. The tables must enforce the business rules themselves, so that no application bug, bulk import or late-night `UPDATE` can store a negative price, an order without a buyer, or more reserved stock than there is on the shelf.

Write one file: **`workspace/02/0201_schema_core.sql`**. It's your version of the reference file [`project/db/0201_schema_core.sql`](../../project/db/0201_schema_core.sql). Try not to look at the reference until your file passes the acceptance checks, or until you're truly stuck.

<!-- toc:start -->
**Contents**

- [Requirements](#requirements)
  - [The file](#the-file)
  - [The design rules (from the theory, section 2)](#the-design-rules-from-the-theory-section-2)
  - [The tables](#the-tables)
- [How to run it](#how-to-run-it)
- [Acceptance checks](#acceptance-checks)
  - [1. The structure matches the reference](#1-the-structure-matches-the-reference)
  - [2. The schema rejects bad data](#2-the-schema-rejects-bad-data)
  - [3. It's a single, clean transaction](#3-its-a-single-clean-transaction)
- [Reference solution](#reference-solution)
- [Going further (optional)](#going-further-optional)

<!-- toc:end -->

## Requirements

### The file

- Starts with `SET search_path = bazaar, public;` so it doesn't depend on the caller's settings.
- Creates the tables in dependency order. It assumes the `bazaar` schema exists and is empty (the state of `./project/bin/reset.sh 02`).
- Runs without errors under `psql -v ON_ERROR_STOP=1 -1` (`-1` wraps the whole file in one transaction, so a mistake leaves nothing half-created).
- Follows the course's SQL style: upper-case keywords, `snake_case` names, one column per line.
- Comments the non-obvious decisions, both as `--` comments in the file and as `COMMENT ON TABLE` for each of the seven tables.

### The design rules (from the theory, section 2)

- Surrogate keys are `bigint GENERATED ALWAYS AS IDENTITY`, except `categories.id`, which is an `int` identity. Every foreign key column has exactly the type of the key it references.
- Money is `numeric(12,2)`. Points in time are `timestamptz`. Strings are `text`, except the ISO country code, which is `char(2)`.
- Status columns are `text` with a `CHECK` listing the allowed values.
- No secondary indexes. The only indexes are the ones created by `PRIMARY KEY` and `UNIQUE` (module 11 adds the rest).
- Constraint names: use PostgreSQL's default names for primary keys (`<table>_pkey`), foreign keys (`<table>_<column>_fkey`) and single-column `UNIQUE` constraints (`<table>_<column>_key`). Name the multi-column `UNIQUE` and **every `CHECK`** explicitly, as listed below. The acceptance checks compare names.

### The tables

Columns are listed in table order. "Required" means `NOT NULL`.

**`users`**: everybody with an account.

| Column | Type | Rules |
|---|---|---|
| `id` | `bigint` | identity (always), primary key |
| `email` | `text` | required, unique |
| `full_name` | `text` | required |
| `is_active` | `boolean` | required, default `true` |
| `created_at` | `timestamptz` | required, default `now()` |

**`sellers`**: a user who opened a shop; one shop per user.

| Column | Type | Rules |
|---|---|---|
| `id` | `bigint` | identity (always), primary key |
| `user_id` | `bigint` | required, unique, references `users` |
| `shop_name` | `text` | required, unique |
| `country_code` | `char(2)` | required; `sellers_country_code_upper`: exactly two upper-case letters A–Z |
| `created_at` | `timestamptz` | required, default `now()` |

**`categories`**: the category tree (adjacency list).

| Column | Type | Rules |
|---|---|---|
| `id` | `int` | identity (always), primary key |
| `parent_id` | `int` | optional (roots have none), references `categories` |
| `name` | `text` | required |
| `slug` | `text` | required, unique |
| `created_at` | `timestamptz` | required, default `now()` |

Plus `categories_not_own_parent`: a category can't be its own parent.

**`products`**: one seller, one category.

| Column | Type | Rules |
|---|---|---|
| `id` | `bigint` | identity (always), primary key |
| `seller_id` | `bigint` | required, references `sellers` |
| `category_id` | `int` | required, references `categories` |
| `sku` | `text` | required |
| `title` | `text` | required |
| `description` | `text` | optional |
| `price` | `numeric(12,2)` | required; `products_price_nonnegative`: zero or more |
| `status` | `text` | required, default `'draft'`; `products_status_check`: `draft`, `active` or `archived` |
| `created_at` | `timestamptz` | required, default `now()` |
| `updated_at` | `timestamptz` | required, default `now()` |

Plus `products_seller_id_sku_key`: a SKU is unique per seller.

**`inventory`**: one row per product.

| Column | Type | Rules |
|---|---|---|
| `product_id` | `bigint` | primary key; references `products`, and **deleting the product deletes its inventory row** |
| `quantity` | `int` | required, default 0; `inventory_quantity_nonnegative`: zero or more |
| `reserved` | `int` | required, default 0 |
| `updated_at` | `timestamptz` | required, default `now()` |

Plus `inventory_reserved_within_quantity`: `reserved` is at least 0 and at most `quantity`.

**`orders`**: one buyer, one seller.

| Column | Type | Rules |
|---|---|---|
| `id` | `bigint` | identity (always), primary key |
| `user_id` | `bigint` | required, references `users` |
| `seller_id` | `bigint` | required, references `sellers` |
| `status` | `text` | required, default `'pending'`; `orders_status_check`: `pending`, `paid`, `shipped`, `delivered`, `cancelled` or `refunded` |
| `total` | `numeric(12,2)` | required, default 0 |
| `created_at` | `timestamptz` | required, default `now()` |
| `paid_at`, `shipped_at`, `delivered_at` | `timestamptz` | optional |

Add a `COMMENT ON COLUMN orders.total` explaining that it's denormalised.

**`order_items`**: the lines of an order; a product appears at most once per order.

| Column | Type | Rules |
|---|---|---|
| `order_id` | `bigint` | required; references `orders`, and **deleting the order deletes its lines** |
| `product_id` | `bigint` | required, references `products` (a product that was sold can't be deleted) |
| `quantity` | `int` | required; `order_items_quantity_positive`: more than 0 |
| `unit_price` | `numeric(12,2)` | required; `order_items_unit_price_nonnegative`: zero or more |

The primary key is `(order_id, product_id)`.

## How to run it

```bash
mkdir -p workspace/02
# ... write workspace/02/0201_schema_core.sql ...

./project/bin/reset.sh 02
docker exec -i course-pg psql -X -U postgres -d bazaar -v ON_ERROR_STOP=1 -1 < workspace/02/0201_schema_core.sql
echo "exit=$?"
```

Exit status 0 and no errors means the file applied. (With a native psql: `psql -X -d bazaar -v ON_ERROR_STOP=1 -1 -f workspace/02/0201_schema_core.sql`.) If it fails, fix the file and repeat both commands: `reset.sh 02` gives you an empty schema again.

## Acceptance checks

### 1. The structure matches the reference

[`solutions/schema_check.sql`](solutions/schema_check.sql) prints a canonical description of the schema: the tables, every column's type, nullability and default or identity, every primary key, unique, foreign key and check constraint, and whether each table has a comment. Run it against the reference and against your schema, and compare:

```bash
./project/bin/reset.sh 03          # the reference schema
docker exec -i course-pg psql -X -U postgres -d bazaar < modules/02-types-and-ddl/solutions/schema_check.sql > /tmp/reference.txt

./project/bin/reset.sh 02          # empty again, then your file
docker exec -i course-pg psql -X -U postgres -d bazaar -v ON_ERROR_STOP=1 -1 < workspace/02/0201_schema_core.sql
docker exec -i course-pg psql -X -U postgres -d bazaar < modules/02-types-and-ddl/solutions/schema_check.sql > /tmp/mine.txt

diff /tmp/reference.txt /tmp/mine.txt && echo "schema matches"
```

**Pass:** `schema matches`. Every line `diff` prints is a difference to fix. For example, a forgotten `NOT NULL` and a missing check look like this:

```text
22c22
<  inventory   | reserved     | integer                  | NO       | 0
---
>  inventory   | reserved     | integer                  | YES      | 0
78d77
<  products    | products_price_nonnegative         | c    | CHECK ((price >= (0)::numeric))
```

Lines starting with `<` are the reference, and lines with `>` are yours. One kind of difference is acceptable: a `CHECK` whose definition is written differently but means the same thing (say, `CHECK (0 <= price)`). The constraint names and everything else must match.

For reference, the columns and constraints part of the expected output:

```text
== columns ==
 table_name  | column_name  |           type           | nullable | default_or_identity 
-------------+--------------+--------------------------+----------+---------------------
 categories  | id           | integer                  | NO       | identity ALWAYS
 categories  | parent_id    | integer                  | YES      | (null)
 categories  | name         | text                     | NO       | (null)
 categories  | slug         | text                     | NO       | (null)
 categories  | created_at   | timestamp with time zone | NO       | now()
 inventory   | product_id   | bigint                   | NO       | (null)
 inventory   | quantity     | integer                  | NO       | 0
 inventory   | reserved     | integer                  | NO       | 0
 inventory   | updated_at   | timestamp with time zone | NO       | now()
 order_items | order_id     | bigint                   | NO       | (null)
 order_items | product_id   | bigint                   | NO       | (null)
 order_items | quantity     | integer                  | NO       | (null)
 order_items | unit_price   | numeric(12,2)            | NO       | (null)
 orders      | id           | bigint                   | NO       | identity ALWAYS
 orders      | user_id      | bigint                   | NO       | (null)
 orders      | seller_id    | bigint                   | NO       | (null)
 orders      | status       | text                     | NO       | 'pending'::text
 orders      | total        | numeric(12,2)            | NO       | 0
 orders      | created_at   | timestamp with time zone | NO       | now()
 orders      | paid_at      | timestamp with time zone | YES      | (null)
 orders      | shipped_at   | timestamp with time zone | YES      | (null)
 orders      | delivered_at | timestamp with time zone | YES      | (null)
 products    | id           | bigint                   | NO       | identity ALWAYS
 products    | seller_id    | bigint                   | NO       | (null)
 products    | category_id  | integer                  | NO       | (null)
 products    | sku          | text                     | NO       | (null)
 products    | title        | text                     | NO       | (null)
 products    | description  | text                     | YES      | (null)
 products    | price        | numeric(12,2)            | NO       | (null)
 products    | status       | text                     | NO       | 'draft'::text
 products    | created_at   | timestamp with time zone | NO       | now()
 products    | updated_at   | timestamp with time zone | NO       | now()
 sellers     | id           | bigint                   | NO       | identity ALWAYS
 sellers     | user_id      | bigint                   | NO       | (null)
 sellers     | shop_name    | text                     | NO       | (null)
 sellers     | country_code | character(2)             | NO       | (null)
 sellers     | created_at   | timestamp with time zone | NO       | now()
 users       | id           | bigint                   | NO       | identity ALWAYS
 users       | email        | text                     | NO       | (null)
 users       | full_name    | text                     | NO       | (null)
 users       | is_active    | boolean                  | NO       | true
 users       | created_at   | timestamp with time zone | NO       | now()

== constraints (NOT NULL is covered by the column list) ==
 table_name  |          constraint_name           | type |                                                               definition                                                               
-------------+------------------------------------+------+----------------------------------------------------------------------------------------------------------------------------------------
 categories  | categories_not_own_parent          | c    | CHECK ((parent_id <> id))
 categories  | categories_parent_id_fkey          | f    | FOREIGN KEY (parent_id) REFERENCES categories(id)
 categories  | categories_pkey                    | p    | PRIMARY KEY (id)
 categories  | categories_slug_key                | u    | UNIQUE (slug)
 inventory   | inventory_quantity_nonnegative     | c    | CHECK ((quantity >= 0))
 inventory   | inventory_reserved_within_quantity | c    | CHECK (((reserved >= 0) AND (reserved <= quantity)))
 inventory   | inventory_product_id_fkey          | f    | FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
 inventory   | inventory_pkey                     | p    | PRIMARY KEY (product_id)
 order_items | order_items_quantity_positive      | c    | CHECK ((quantity > 0))
 order_items | order_items_unit_price_nonnegative | c    | CHECK ((unit_price >= (0)::numeric))
 order_items | order_items_order_id_fkey          | f    | FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
 order_items | order_items_product_id_fkey        | f    | FOREIGN KEY (product_id) REFERENCES products(id)
 order_items | order_items_pkey                   | p    | PRIMARY KEY (order_id, product_id)
 orders      | orders_status_check                | c    | CHECK ((status = ANY (ARRAY['pending'::text, 'paid'::text, 'shipped'::text, 'delivered'::text, 'cancelled'::text, 'refunded'::text])))
 orders      | orders_seller_id_fkey              | f    | FOREIGN KEY (seller_id) REFERENCES sellers(id)
 orders      | orders_user_id_fkey                | f    | FOREIGN KEY (user_id) REFERENCES users(id)
 orders      | orders_pkey                        | p    | PRIMARY KEY (id)
 products    | products_price_nonnegative         | c    | CHECK ((price >= (0)::numeric))
 products    | products_status_check              | c    | CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'archived'::text])))
 products    | products_category_id_fkey          | f    | FOREIGN KEY (category_id) REFERENCES categories(id)
 products    | products_seller_id_fkey            | f    | FOREIGN KEY (seller_id) REFERENCES sellers(id)
 products    | products_pkey                      | p    | PRIMARY KEY (id)
 products    | products_seller_id_sku_key         | u    | UNIQUE (seller_id, sku)
 sellers     | sellers_country_code_upper         | c    | CHECK ((country_code ~ '^[A-Z]{2}$'::text))
 sellers     | sellers_user_id_fkey               | f    | FOREIGN KEY (user_id) REFERENCES users(id)
 sellers     | sellers_pkey                       | p    | PRIMARY KEY (id)
 sellers     | sellers_shop_name_key              | u    | UNIQUE (shop_name)
 sellers     | sellers_user_id_key                | u    | UNIQUE (user_id)
 users       | users_pkey                         | p    | PRIMARY KEY (id)
 users       | users_email_key                    | u    | UNIQUE (email)
```

The output also lists the seven tables and confirms each has a comment (`has_comment` is `t`).

### 2. The schema rejects bad data

[`solutions/acceptance_inserts.sql`](solutions/acceptance_inserts.sql) inserts a few valid rows, then tries 20 statements that a correct schema must reject, and reports the error each one got. It finally checks that the two cascades work. It runs in a transaction that it rolls back, so it leaves your tables empty.

```bash
docker exec -i course-pg psql -X -U postgres -d bazaar < modules/02-types-and-ddl/solutions/acceptance_inserts.sql
```

**Pass:** every row says `PASS`:

```text
 n  |           description            |                                           got                                           | result 
----+----------------------------------+-----------------------------------------------------------------------------------------+--------
  1 | user without a name              | 23502 null value in column "full_name" of relation "users" violates not-null constraint | PASS
  2 | duplicate email                  | 23505 users_email_key                                                                   | PASS
  3 | explicit user id                 | 428C9 cannot insert a non-DEFAULT value into column "id"                                | PASS
  4 | second shop for the same user    | 23505 sellers_user_id_key                                                               | PASS
  5 | duplicate shop name              | 23505 sellers_shop_name_key                                                             | PASS
  6 | lower-case country code          | 23514 sellers_country_code_upper                                                        | PASS
  7 | shop for a missing user          | 23503 sellers_user_id_fkey                                                              | PASS
  8 | category that is its own parent  | 23514 categories_not_own_parent                                                         | PASS
  9 | duplicate category slug          | 23505 categories_slug_key                                                               | PASS
 10 | negative price                   | 23514 products_price_nonnegative                                                        | PASS
 11 | unknown product status           | 23514 products_status_check                                                             | PASS
 12 | duplicate SKU within a seller    | 23505 products_seller_id_sku_key                                                        | PASS
 13 | product in a missing category    | 23503 products_category_id_fkey                                                         | PASS
 14 | negative stock                   | 23514 inventory_quantity_nonnegative                                                    | PASS
 15 | reserving more than in stock     | 23514 inventory_reserved_within_quantity                                                | PASS
 16 | second inventory row             | 23505 inventory_pkey                                                                    | PASS
 17 | unknown order status             | 23514 orders_status_check                                                               | PASS
 18 | zero quantity line               | 23514 order_items_quantity_positive                                                     | PASS
 19 | same product twice in an order   | 23505 order_items_pkey                                                                  | PASS
 20 | deleting a product that was sold | 23503 order_items_product_id_fkey                                                       | PASS

  check   | result 
----------+--------
 cascades | PASS
```

A `FAIL (expected ...)` row tells you which rule is missing or misnamed. `no error` means your schema accepted bad data.

### 3. It's a single, clean transaction

Break your file on purpose, for example by misspelling a type in the *last* table, and apply it to a fresh `reset.sh 02` with `-1`. psql must stop with an error, and `\dt bazaar.*` must show **no** tables: the earlier `CREATE TABLE`s were rolled back with the rest (DDL is transactional). Then fix the file again.

## Reference solution

[`project/db/0201_schema_core.sql`](../../project/db/0201_schema_core.sql). From now on, `./project/bin/reset.sh 03` (or any later state) builds the schema from that file, so later modules don't depend on your version. Keep yours in `workspace/02/`. It's the first of the migration files you'll write in this course.

## Going further (optional)

- Add a `CHECK` that `orders.paid_at`, `shipped_at` and `delivered_at` are in chronological order when they're set. What does the `CHECK` evaluate to while some of them are `NULL`, and is that what you want?
- Which of the module's design decisions would you defend in a code review, and which would you question? (Single currency? `full_name` rather than first/last name? Case-sensitive email uniqueness?) Module 07 revisits the model, and module 08 fixes the email weakness.
