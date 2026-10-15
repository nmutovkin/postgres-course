# Module 08 project: Bazaar schema v3

<!-- nav:start -->
> [Theory](README.md) · [Exercises](exercises.md) · **Project** · [Solutions](solutions/README.md)  
> [← 07 Data Modeling](../07-data-modeling/README.md) · [Course home](../../README.md) · [09 Views, Functions and Triggers →](../09-views-functions-triggers/README.md)
<!-- nav:end -->

Bazaar's catalog team wants richer product pages and a promotions calendar, and engineering wants the status columns to stop accepting typos. In this milestone you write the **v3 migration**: you convert the status columns to enums, add an e-mail domain with case-insensitive uniqueness, give products semi-structured attributes and tags, and create a promotions table in which overlapping promotions for the same product are impossible. Then you write the seed that fills the new columns for the small dataset.

**Starting state:** `./project/bin/reset.sh 08` (schema v2 with the small dataset).

You write two files, in the same spirit as the reference: a schema migration (`0801_schema_types.sql`) and a seed (`0802_seed_types.sql`). Run them with:

```bash
docker exec -i course-pg psql -X -U postgres -d bazaar -v ON_ERROR_STOP=1 < my_0801_schema_types.sql
docker exec -i course-pg psql -X -U postgres -d bazaar -v ON_ERROR_STOP=1 < my_0802_seed_types.sql
```

<!-- toc:start -->
**Contents**

- [Requirements](#requirements)
  - [Migration (one transaction)](#migration-one-transaction)
  - [Seed](#seed)
- [Acceptance checks](#acceptance-checks)
- [Reference solution](#reference-solution)

<!-- toc:end -->

## Requirements

### Migration (one transaction)

1. **Enums.** Create three types and convert the columns to them, keeping all existing data:

   | Type | Labels, in this order | Column | Default |
   |---|---|---|---|
   | `order_status` | `pending, paid, shipped, delivered, cancelled, refunded` | `orders.status` | `'pending'` |
   | `product_status` | `draft, active, archived` | `products.status` | `'draft'` |
   | `payment_status` | `pending, succeeded, failed` | `payments.status` | (none) |

   The old `*_status_check` constraints must be gone afterwards, because the enum makes them redundant. Expect PostgreSQL to refuse a naive `ALTER COLUMN ... TYPE` (the theory's section 18 explains the three errors you'll meet).

2. **E-mail domain.** Create a domain `email_address` over `text` with a named constraint that requires the shape `something@something.something` (no whitespace, exactly one `@` separating non-empty parts). Convert `users.email` to it. Replace the case-sensitive unique constraint `users_email_key` with a **unique index on `lower(email)`** named `users_email_lower_key`, so that `Zoe.Martin@Example.COM` (user 17) and `zoe.martin@example.com` can't both exist.

3. **Product attributes and tags.** Add to `products`:
   - `attributes jsonb NOT NULL DEFAULT '{}'`, with a `CHECK` that the value is always a JSON **object**;
   - `tags text[] NOT NULL DEFAULT '{}'`.

4. **Promotions.** Create `promotions`:

   | Column | Type | Rules |
   |---|---|---|
   | `id` | `bigint` identity | primary key |
   | `product_id` | `bigint` | required; references `products`, deleted with the product |
   | `discount_pct` | `numeric(5,2)` | required; strictly between 0 and 100 |
   | `active_during` | `tstzrange` | required; not empty; half-open `[start, end)`, open-ended allowed |
   | `created_at` | `timestamptz` | required; defaults to now |

   Add an **exclusion constraint** named `promotions_no_overlap` so that two promotions for the same product never overlap in time, and install whatever extension that requires.

### Seed

5. Fill `attributes` and `tags` for the products of the small dataset. The documents must vary by category in the way the theory uses them. At minimum:
   - **laptops** (1–5, 15): `brand`, `ram_gb` (number), `cpu`, `screen_in`; products 1–4 also have a nested `storage` object with `type` and `gb`; the gaming laptops (3, 15) have a `gpu`;
   - **monitors** (6–9): `brand`, `size_in`, `resolution` ("WxH" string), `panel`, `refresh_hz` (not on product 9), `ports` arrays on 7 and 9;
   - **headphones** (10–14): `brand`, `wireless` (boolean), `anc` on 10 and 11;
   - **books** (19–32): `author`, `language`, `pages`, `isbn` (product 28 lacks `pages` and `isbn`), and product 33 (the box set) with a `contents` array of `{title, author}` objects;
   - **clothing** (47–56): a `size` **array**, `color`, `material`, `fit`;
   - product **62**: the empty object `{}`.

   Tags are lowercase words such as `laptop`, `gaming`, `bestseller`, `book`, `sci-fi`, `clothing` and `usb-c`. If you'd rather spend your time on the schema, you may write the seed for the laptops and books only and then apply the reference seed `project/db/0802_seed_types.sql` *instead of* your own. The exercises assume the reference values.
6. Insert the nine promotions with **fixed ids** 1–9 exactly as listed in [section 16.1 of the README](README.md#161-built-in-range-types-and-bounds) and the reference seed `project/db/0802_seed_types.sql` (products 47 and 48 open-ended from 2025-06-20; product 10 twice: Black Friday 2024 and June 1–15, 2025), and make the next generated id 10. (An identity column needs `OVERRIDING SYSTEM VALUE` to accept explicit ids.)

## Acceptance checks

Run the provided check script:

```bash
docker exec -i course-pg psql -X -U postgres -d bazaar < modules/08-beyond-scalars/solutions/project_checks.sql
```

It must show:

1. `enum_range` of the three types in exactly the order above.
2. Column types `order_status`, `product_status`, `payment_status`, `email_address`, `jsonb` and `_text` (that's `text[]`), with defaults `'pending'::order_status`, `'draft'::product_status`, `'{}'::jsonb` and `'{}'::text[]`.
3. **Zero** remaining CHECK constraints that mention `status`.
4. The data survived: 120 orders, 91 delivered, 54 active products, 30 failed payments.
5. Two errors: `value for domain email_address violates check constraint ...` and `duplicate key value violates unique constraint "users_email_lower_key"`.
6. With the reference attribute values: 61 products with non-empty attributes, 62 with tags, 3 laptops with SSD storage, 4 products with `ram_gb >= 16` and 3 bestsellers. Setting `attributes` to `'[]'` fails with a CHECK violation.
7. 9 promotions (2 open-ended). The adjacent promotion is accepted (`INSERT 0 1`), and the overlapping one fails with `conflicting key value violates exclusion constraint "promotions_no_overlap"`. The empty range and the 0 % discount are rejected by CHECK constraints.

Two more checks to run by hand:

```sql
-- the whole migration is atomic: introduce a deliberate error at the end of your
-- migration file (e.g. SELECT 1/0;), run it on a fresh reset.sh 08, and confirm:
SELECT to_regtype('order_status') IS NULL AS rolled_back;   -- t

-- nothing else changed: the sanity checks still pass
\i /course/project/db/tools/check.sql
```

(`/course/project` is where the repository's `project/` directory is mounted inside the container. From a shell, `./project/bin/psql.sh < project/db/tools/check.sql` does the same.)

## Reference solution

- [`project/db/0801_schema_types.sql`](../../project/db/0801_schema_types.sql): the migration. Note the order *drop CHECK → drop default → `ALTER ... TYPE ... USING` → set default*, the `btree_gist` extension created `WITH SCHEMA public`, and the extra `promotions_range_not_empty` check, without which an empty range could slip past the exclusion constraint.
- [`project/db/0802_seed_types.sql`](../../project/db/0802_seed_types.sql): the seed, generated by `project/db/tools/gen_small_seed.py`.

After this milestone, `./project/bin/reset.sh 09` gives exactly the reference v3 state. Module 09 builds on it.
