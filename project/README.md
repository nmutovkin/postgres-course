# Bazaar: the course project

Bazaar is a multi-seller online marketplace, similar in spirit to Etsy. Throughout the course you design, build, tune, secure, scale, and finally wrap it in a Python service.

## The business in one paragraph

**Users** browse **products** organised in a **category tree**. Each product is listed by exactly one **seller** (a seller is a user who has opened a shop). A shopper fills a **cart** with products from several sellers. At checkout, the cart is **split into one order per seller**, because each seller ships independently. An order has **order items**, a **shipping address**, and one or more **payments**. Stock is tracked in **inventory**. When an order is placed, stock is *reserved*; when the order ships, it is consumed. Shoppers leave **reviews**, and sellers run time-boxed **promotions**. Every click and view lands in a high-volume **events** stream used for analytics.

Two design decisions recur throughout the course:

1. **One order belongs to exactly one seller.** This keeps fulfilment simple, and in Module 21 it makes `seller_id` a natural shard key.
2. **Money is `numeric(12,2)`**, never `float`. Module 02 explains why.

## How the reference implementation is organized

```
project/
├── db/              # ordered SQL files: migrations (schema) and seeds (data)
│   ├── 0201_schema_core.sql
│   ├── 0301_seed_small.sql
│   ├── 0701_schema_modeling.sql
│   └── ...
├── bin/
│   ├── reset.sh     # rebuild the database as of the start of module NN
│   └── psql.sh      # open psql against the course database
└── python/          # the Python package built in Part IV (from module 22)
```

File names are `MMNN_kind_name.sql`, where `MM` is the module that introduces the file. `./project/bin/reset.sh 07` drops the `bazaar` database and applies every file whose module number is **lower than 07**, in order. That gives you exactly the state you should have at the *start* of module 07. `./project/bin/reset.sh all` applies everything.

During the course, you write your *own* versions of these files as part of each module's `project.md` milestone. The files here are the reference solutions.

## Conventions

- Database `bazaar`, schema `bazaar` (`search_path` is set to `bazaar, public` at the database level).
- Table names are plural and `snake_case`. Primary keys are `id bigint GENERATED ALWAYS AS IDENTITY`, except where a natural or composite key is clearly better.
- Every table has `created_at timestamptz NOT NULL DEFAULT now()`. Tables that are updated also have `updated_at`.
- Foreign keys are named `<table>_<column>_fkey` (Postgres's default) and are **always indexed** from Module 11 on.

## Schema evolution

| Module | File(s) | What changes |
|---|---|---|
| 02 | `0201_schema_core.sql` | `users`, `sellers`, `categories` (self-referencing tree), `products`, `inventory`, `orders`, `order_items`. Status columns are `text` + `CHECK`. |
| 03 | `0301_seed_small.sql` | A small, hand-readable dataset (dozens of rows per table) that early exercises rely on. Deterministic. |
| 07 | `0701_schema_modeling.sql`, `0702_seed_modeling.sql` | `addresses`, `orders.shipping_address_id`, `payments`, `reviews`, `cart_items`, `product_price_history`. |
| 08 | `0801_schema_types.sql`, `0802_seed_types.sql` | Status columns become **enums**; `products.attributes jsonb`, `products.tags text[]`; an `email_address` domain; `promotions` with a `tstzrange` and an **exclusion constraint** (`btree_gist`). |
| 09 | `0901_schema_logic.sql` | A `set_updated_at()` trigger; `audit_log` + generic audit trigger; trigger-maintained `orders.total`; `active_products` view; `seller_sales_daily` materialized view. |
| 10 | `1001_schema_concurrency.sql` | `place_order()` (per-seller split, deadlock-safe inventory reservation); a `jobs` queue with a `SKIP LOCKED` dequeue function. |
| 11 | `1101_seed_large.sql`, `1102_schema_indexes.sql` | A **large generated dataset** (~100k users, ~200k products, ~500k orders); indexes for FKs and hot queries. |
| 12 | `1201_schema_stats.sql` | `pg_stat_statements`, extended statistics. |
| 13 | `1301_schema_search.sql` | `products.search_vector` (generated, weighted), GIN index, `pg_trgm` for fuzzy titles and autocomplete. |
| 14 | `1401_schema_events.sql`, `1402_seed_events.sql` | `events`, range-partitioned by month, with a partition-management function. |
| 15 | `1501_schema_security.sql` | Roles (`bazaar_owner`, `bazaar_app`, `bazaar_readonly`, `bazaar_analyst`, `bazaar_seller`) and RLS so a seller sees only their own products and orders. |
| 21 | `2101_schema_shard_prep.sql` | Adds `seller_id` to `order_items`/`inventory` and makes PKs shard-key-aware (prerequisite for Citus co-location). |
| 22+ | `python/` | The `bazaar` Python package: psycopg data access → async → SQLAlchemy → Alembic (which takes over migrations from module 26) → FastAPI capstone. |

Modules 16–20 (internals, operations, backup, replication, HA) work *on* the Bazaar database without changing its schema. Their infrastructure lives in `infra/`.
