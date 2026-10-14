# Module 21 milestone: Bazaar on Citus

<!-- nav:start -->
> [Theory](README.md) · [Exercises](exercises.md) · **Project** · [Solutions](solutions/README.md)  
> [← 20 High Availability](../20-high-availability/README.md) · [Course home](../../README.md) · [22 psycopg 3 →](../22-psycopg/README.md)
<!-- nav:end -->

<!-- toc:start -->
**Contents**

- [Goal](#goal)
- [Starting point](#starting-point)
- [Requirements](#requirements)
  - [1. A role for every table](#1-a-role-for-every-table)
  - [2. Explain the shard-prep migration](#2-explain-the-shard-prep-migration)
  - [3. Distribute](#3-distribute)
  - [4. Adapt what breaks](#4-adapt-what-breaks)
  - [5. Verify](#5-verify)
  - [6. Measure (recommended: large dataset)](#6-measure-recommended-large-dataset)
  - [7. Scale out](#7-scale-out)
- [Acceptance checks](#acceptance-checks)
- [Reference solution](#reference-solution)

<!-- toc:end -->

## Goal

Bazaar's growth plan says one primary won't be enough in two years. Your job is to prove that the marketplace can run **sharded by seller** on a Citus cluster: every seller's catalogue, stock, orders and payments on one node, checkout and the seller dashboard as single-node operations, and global reports still correct. You also have to write down *why* each table ended up where it did, so the next engineer can extend the schema without breaking co-location.

## Starting point

The Citus lab of this module, with Bazaar loaded but not yet distributed:

```bash
cd modules/21-sharding/lab
docker compose up -d
./scripts/load-bazaar.sh small          # reset.sh 11 + schema migrations 11-15 + 2101
./scripts/citus-init-nodes.sh           # workers registered, nothing distributed yet
```

`load-bazaar.sh` already applies `project/db/sharding/2101_schema_shard_prep.sql`. Read it first: requirement 2 asks you to explain it. (If you want to apply it by hand, run `load-bazaar.sh` with that line commented out, then `docker exec -i m21-coord psql -U postgres -d bazaar -v ON_ERROR_STOP=1 < ../../../project/db/sharding/2101_schema_shard_prep.sql`.)

## Requirements

### 1. A role for every table

Decide for **every** table in schema `bazaar` whether it is a reference table, a distributed table (with which distribution column and which co-location group), or stays local on the coordinator. At minimum, cover: `users`, `sellers`, `categories`, `addresses`, `products`, `inventory`, `orders`, `order_items`, `payments`, `reviews`, `product_price_history`, `promotions`, `cart_items`, `events`, `audit_log`, `jobs`.

Write the decisions down in a short design note (`docs/sharding.md` in your own repository, or wherever your team keeps design notes) as a table: *table | role | distribution column | co-location | why*. Each "why" should name the access pattern or constraint that drove the choice: for example "joined to every order on every shard, 6 rows, written by staff only" or "belongs to a buyer, spans sellers, so it can't keep its FK to products".

### 2. Explain the shard-prep migration

In the same note, explain in a few sentences each why `2101` changes:

- the primary keys of `products` and `orders` to `(seller_id, id)`;
- `order_items`' key and foreign keys, and adds `seller_id` to it;
- the exclusion constraint of `promotions`;
- `cart_items`' foreign key to `products` (dropped);
- the compatibility triggers, and why they must go on Citus.

### 3. Distribute

Write your own distribution script (`distribute.sql`) that:

- creates the reference tables and the distributed tables, with explicit `colocate_with`;
- removes or replaces every trigger that can't run on a shard, with a comment saying why;
- lets Module 15's seller RLS keep working (a seller with `SET LOCAL app.seller_id = '2'` must see their 36 orders, not 0);
- removes the local copies of the data from the coordinator afterwards.

### 4. Adapt what breaks

- `active_products` and `seller_sales_daily` must work on the distributed schema.
- Checkout must work: a `place_order_sharded(p_user_id, p_items, p_shipping_address_id)` with the same result and error contract as Module 10's `place_order()` (one order id per seller, `BZ001`–`BZ004`, `22023`), runnable **as `bazaar_app`**, deadlock-safe, and maintaining `orders.total` correctly.

### 5. Verify

Run the acceptance checks below. All must pass.

### 6. Measure (recommended: large dataset)

Load the large dataset (`./scripts/citus-build.sh large` builds the reference solution; to measure your own, use `load-bazaar.sh large`, then your scripts). Before distributing, run `scripts/queries.sql` on the single node. Afterwards, run it on the cluster: `docker exec -i m21-coord psql -U postgres -d bazaar -v seller=42 < scripts/queries.sql`, three times, for seller 42 and seller 1. Record the timings in your note *with the hardware context* (all containers share one machine) and explain the differences: which queries are router queries, which are multi-shard, and why the global report is not faster here.

### 7. Scale out

Add `m21-worker3`, rebalance, and rerun the acceptance checks. Record how many shard moves the rebalancer scheduled and where seller 1's shard group ended up.

## Acceptance checks

`solutions/milestone-checks.sql` contains all of them:

```bash
docker exec -i m21-coord psql -U postgres -d bazaar < ../solutions/milestone-checks.sql
```

| Check | Expected on the small dataset |
|---|---|
| A1 `citus_tables` | 4 reference tables (users, sellers, categories, addresses); 8 tables distributed by `seller_id` in one co-location group; `cart_items` by `user_id` in its own group |
| A2 local tables | only `audit_log`, `jobs` and the 26 relations of `events` |
| A3 row counts | users 30, products 62, orders 120, order_items 235, `sum(orders.total)` = 55,341.98 |
| A4 `orders.total` vs. items | 0 mismatches |
| A5 seller dashboard `EXPLAIN` | `Task Count: 1` |
| A6 revenue per seller (paid/shipped/delivered) | 41,598.82 / 1,656.43 / 3,562.94 / 2,277.00 for sellers 1–4 |
| A7 views | `active_products` 54 rows; `seller_sales_daily` 96 rows |
| A8 RLS | `bazaar_seller` with `app.seller_id = '2'` sees 36 orders of 1 seller |
| A9 two-seller checkout as `bazaar_app` | two order ids; totals 1,998.00 (seller 1) and 89.00 (seller 3), one line each |
| A10 out-of-stock checkout | `BZ001: insufficient stock for product 3` |

After requirement 7, A1–A10 must give the same results with three workers. In the reference run on the small dataset, `citus_rebalance_start()` scheduled 20 moves that finished in 30 s, leaving 88, 88 and 80 seller shards (11, 11 and 10 shard groups) on the three workers, with seller 1's group on `m21-worker3`. On the large dataset it scheduled 18 moves (38 s): the default `by_disk_size` strategy balances bytes, not shard counts, so the plan depends on the data (README section 16.1).

## Reference solution

- `project/db/sharding/2101_schema_shard_prep.sql`: the shard-prep migration (provided, opt-in).
- `modules/21-sharding/lab/scripts/distribute.sql`: table roles, trigger changes, settings, and the reasons for each.
- `modules/21-sharding/lab/scripts/citus-adapt.sql`: co-location-aware views and `place_order_sharded()`.
- `modules/21-sharding/lab/scripts/citus-build.sh`: runs everything in order.
- `modules/21-sharding/solutions/milestone-checks.sql`: the acceptance checks.
- README sections 13–16 are the design note: the table-by-table reasoning (13.1, 13.2), what broke and why (13.3, 14.5, 15.1), the measurements (14.6) and the scale-out (16).

The reference decisions, in short:

| Table | Role | Why |
|---|---|---|
| `users`, `sellers`, `categories`, `addresses` | reference | small or moderately sized, read on every shard (FK targets of seller data, joined in dashboards and checkout), written rarely relative to reads |
| `products`, `inventory`, `orders`, `order_items`, `payments`, `reviews`, `product_price_history`, `promotions` | distributed by `seller_id`, one co-location group | one order belongs to one seller: checkout, fulfilment and the dashboard stay on one node; composite FKs stay shard-local |
| `cart_items` | distributed by `user_id`, own group | belongs to a buyer and spans sellers; its FK to products is dropped (checkout validates products) |
| `events` | local (exercise 11 distributes it by `session_id`) | append-only analytics; its PK lacks any useful distribution column until changed |
| `audit_log`, `jobs` | local on the coordinator | operational tables; the audit triggers that fed `audit_log` can't run on shards and are dropped (use CDC instead) |

`users` is the debatable one. At 100,000 rows it is a fine reference table. At 100 million, with frequent writes (`last_login_at`), you would distribute it by `user_id` (together with `addresses` and `cart_items`) and accept that the `orders.user_id` foreign key goes away.
