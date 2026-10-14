# Bazaar reference database: notes for module authors

Everything here was verified on PostgreSQL 17.11 (`course-pg`). `./project/bin/reset.sh NN` succeeds for every NN; tested: 03, 07, 08, 09, 10, 11, 12, 13, 14, 15, 16, 21, 22 and `all`. Sanity checks: `docker exec -i course-pg psql -U postgres -d bazaar < project/db/tools/check.sql`. All checks show 0 at states 07, 10 and `all`. The only exception is the deliberate "(info)" row: 1 on small data, 6,060 on large data.

## Files

| File | Module | Content |
|---|---|---|
| `0201_schema_core.sql` | 02 | users, sellers, categories, products, inventory, orders, order_items. Named CHECKs. No secondary indexes. |
| `0301_seed_small.sql` | 03 | Small hand-readable dataset (generated, static INSERTs) |
| `0701_schema_modeling.sql` / `0702_seed_modeling.sql` | 07 | addresses (partial unique `addresses_one_default_per_user`), `orders.shipping_address_id`, payments, reviews, cart_items, product_price_history |
| `0801_schema_types.sql` / `0802_seed_types.sql` | 08 | enums `order_status`/`product_status`/`payment_status`, domain `email_address`, unique index `users_email_lower_key` on `lower(email)`, `products.attributes jsonb` / `tags text[]`, promotions + `EXCLUDE` (btree_gist) |
| `0901_schema_logic.sql` | 09 | `set_updated_at()` (products, inventory, orders; adds `orders.updated_at`), `audit_log` + `audit_trigger(pk cols...)` on products/orders, statement-level delta trigger for `orders.total`, view `active_products`, matview `seller_sales_daily` (+ unique index) |
| `1001_schema_concurrency.sql` | 10 | `place_order()`, `jobs`, `dequeue_jobs()`, `complete_job()`, `fail_job()` |
| `1101_seed_large.sql` / `1102_schema_indexes.sql` | 11 | Large generated dataset (TRUNCATEs the small one); FK and hot-query indexes |
| `1201_schema_stats.sql` | 12 | `pg_stat_statements`; `CREATE STATISTICS addresses_country_city_stats` |
| `1301_schema_search.sql` | 13 | pg_trgm, unaccent, `products.search_vector` (generated: title A, description B, tags C via immutable `tags_to_text()`), GIN indexes |
| `1401_schema_events.sql` / `1402_seed_events.sql` | 14 | `events` partitioned monthly (2025-01..2026-12 + DEFAULT), `create_events_partition(date)`; about 5M events |
| `1501_schema_security.sql` | 15 | Roles, ownership, grants, default privileges, RLS |
| `sharding/2101_schema_shard_prep.sql` | 21 | seller_id-aware keys (see below) |
| `tools/gen_small_seed.py` | | Deterministic generator for 0301/0702/0802 (`python3 project/db/tools/gen_small_seed.py`) |
| `tools/check.sql` | | Sanity checks that work at every state |

## Small dataset (states 03–11)

Row counts at state 07 (0201+0301 only): users 30, sellers 6, categories 23, products 62, inventory 62, orders 120, order_items 235. Module 07 adds addresses 32, payments 140, reviews 78, cart_items 14 and product_price_history 71. Module 08 adds promotions 9.

- **Users.** Users 1–6 own sellers 1–6. Inactive users: 9, 22, 28; users 9 and 22 still have old orders. Users who never ordered: 6, 7, 15, 16, 21, 24, 28, 29, 30. User 30 signed up on 2025-06-25. Top buyers: 8 and 13 (19 orders each), 11 (16). User 17 has the mixed-case email `Zoe.Martin@Example.COM`. User 12 is `Liam O'Connor`. Accented names: 5, 9, 14, 17, 26. Name collisions: Johnson (1, 27), Martin (16, 17), Rossi (3, 19).
- **Sellers.** 1 TechTrove (US), 2 Bookworm Corner (GB), 3 Casa Cucina (IT), 4 Threadbare Co. (DE), 5 Maple & Pine (CA), 6 Sakura Gadgets (JP). Seller 5 has products but no sales. Seller 6 has no products. Revenue (paid/shipped/delivered): seller 1 = 41,598.82; seller 2 = 1,656.43; seller 3 = 3,562.94; seller 4 = 2,277.00.
- **Categories.** Four roots: 1 Electronics, 8 Home & Garden, 14 Books, 21 Clothing. The tree is 3 levels deep. Clothing has only 2 levels. Empty leaves: 7 Speakers, 20 History. Some products sit in non-leaf categories: 17 and 18 (Computers), 33 (Fiction), 46 (Kitchen), 60 (Garden), 62 (Clothing).
- **Products.** There are 62 products. Drafts: 14, 28, 55, 59. Archived: 5, 23, 38, 61. NULL description: 9, 14, 22, 28, 32, 39, 55, 60. Product 62 is free (0.00). The most expensive is product 3 (2199.99). Product 25 (*The Lighthouse Keeper's Secret*), product 40 (*Chef's Knife*), product 51 (*Graphic Tee 'Ctrl+Alt+Del'*) and product 54 have apostrophes. Products 6–9 and 16 contain `"`. Never ordered: 14, 15, 28, 55, 57–61. Product 15 is active and in stock but has no orders.
- **Price changes.** Products 1, 7, 10, 19, 29, 34, 37 (two changes) and 52 changed price, so 20 orders have a `unit_price` that differs from the current price. `product_price_history` matches `order_items.unit_price` exactly.
- **Inventory.** Out of stock (quantity 0): 5, 14, 23, 25, 28, 38, 55, 59, 61. `reserved` equals the units in pending and paid orders. Products 3 and 13 are fully reserved (available 0).
- **Orders.** Order ids are chronological, from 2024-01-12 to 2025-06-30 (66 in 2024, 54 in 2025). By status:
  - delivered: 91
  - pending: 115–119
  - paid: 110–114, 120
  - shipped: 105–107, 109
  - cancelled: 31, 32, 36, 53, 64, 67, 73, 81, 82, 84
  - refunded: 18, 28, 69, 74

  The largest order is 40 (8,940.00) and the smallest is 20 (9.99). The free product 62 is in orders 10, 46, 82, 111 and 115. Order 1 has `shipping_address_id` NULL. Twelve orders ship to a non-default (Work) address.
- **Addresses.** Users 24, 28, 29 and 30 have none. Users 1, 8, 11, 13, 17 and 20 have a Home (default) and a Work address. User 7 has an address but no default.
- **Payments.** A failed attempt precedes the successful payment for some orders. Each refunded order has one negative refund payment.
- **Reviews.** Every review is by a buyer after delivery, with one deliberate exception: user 7 (who never ordered) reviewed product 1. Product 24 has the most reviews (4).
- **Promotions.** Products 47 and 48 have an open-ended promotion that is still running. Product 10 has two promotions that do not overlap.

"Today" in the small data is 2025-06-30. **Seeds never depend on `now()`**, so use fixed dates in exercises.

## Large dataset (state 12 and later; module 11 starts from the small data)

| Table | Rows | Table | Rows |
|---|---|---|---|
| users | 100,000 | payments | 536,816 |
| sellers | 2,000 | reviews | 292,355 (6,060 unverified) |
| categories | 69 (ids 1–23 as in the small data) | cart_items | 19,843 |
| products | 200,000 (170,038 active / 13,968 draft / 15,994 archived) | product_price_history | 230,263 |
| inventory | 200,000 | promotions | 15,047 |
| orders | 500,000 | addresses | 125,017 |
| order_items | 1,266,928 | events | 4,983,725 |

- Orders run from 2023-01-01 to 2025-12-31 ("today" is 2025-12-31). Volume grows over time, with Nov/Dec peaks and Black Friday spikes; 2025-11-28 has 3,527 orders. Statuses: 452,767 delivered, 25,025 cancelled, 14,571 refunded. Open orders (1,099 pending, 1,397 paid, 5,141 shipped) all fall in the last 12 days.
- Sellers are Zipf-skewed and ranked by size: seller 1 has 18,500 products and about 70k orders, seller 2 has 10,592 products, and seller 2000 has about 13. Seller `s` is owned by user `s*37`. Sellers 1–5 are general stores; the others specialise in one root category.
- Buyers are skewed: median 4 orders, p99 26, max 234. 15,942 users never ordered. User and product ids follow sign-up and creation order, so `created_at` is correlated with `id`. That correlation is useful for BRIN lessons.
- Every user has a default address whose id equals the user id. 25% also have a Work address.
- Extended statistics demo: `WHERE country_code='DE' AND city='Berlin'` on addresses is estimated at 179 rows without statistics; the actual count is 1,827. London exists in both GB and CA.
- Titles and descriptions are built from vocabularies, for example "Voltix Wireless Headphones 527" or "The Silent Witness". Some words carry accents (Lumière, Crème de Lys, Café). Event search payloads include typos ("headphnes"). Attributes depend on the category, for example laptops (category 3) have `ram_gb`, `cpu`, `screen_in` and a nested `storage`.
- Generation is deterministic: it hashes row keys (`hashint8extended`) rather than calling `random()`.
- Timings on this machine (4 vCPU, default config):

  | Step | Time |
  |---|---|
  | 1101 | ≈70 s |
  | 1102 | 3 s |
  | 1301 | 16 s |
  | 1402 | ≈65 s |
  | 2101 | ≈45 s |
  | `reset.sh all` | about 3–3.5 min |

  The DB is about 1.9 GB at `all`. Disk on this shared host filled up once during testing, so check `df` if a reset fails with "No space left".

## Things module authors must know

1. **Error codes from `place_order`.** These are custom SQLSTATEs, not P0001:
   - `BZ001`: insufficient stock (DETAIL gives requested/available)
   - `BZ002`: product missing or not active
   - `BZ003`: user missing or inactive
   - `BZ004`: address not the user's
   - `22023`: malformed `p_items`

   Duplicate product lines are summed. The function returns one order id per seller, in ascending order. It only *reserves* stock; nothing un-reserves or consumes stock on ship or cancel (that is a good exercise). Tested with pgbench: 8 clients ordering [A,B] and [B,A] in opposite orders against 40 units gave exactly 40 successes, no deadlocks and correct totals.
2. **Job queue.** `dequeue_jobs(queue, worker, limit=1, lease=interval '5 minutes')` increments `attempts` and takes a lease (`locked_at`). `fail_job` backs off 10 s·2^(attempts−1), capped at 1 h. A job is dead when `attempts >= max_attempts` and `done_at` is NULL. Tested: 2,000 jobs, 8 concurrent workers, each job processed once.
3. **`orders.total` trigger (09+).** It applies deltas (concurrency-safe), so `total` only stays correct if it starts correct. A `TRUNCATE` or `session_replication_role=replica` bypasses it.
4. **Emails after 08.** Uniqueness comes from an expression index, so use `ON CONFLICT (lower(email))` and `WHERE lower(email) = lower($1)`.
5. **Status types.** Statuses are enums from 08 on. Compare with literals; cast with `::text` if code must work at both earlier and later states.
6. **Day boundaries.** `seller_sales_daily.day` is the UTC day of `created_at`.
7. **Security (15+).** Every object is owned by `bazaar_owner`. Login roles: `bazaar_app`/`app`, `bazaar_analyst`/`analyst`, `bazaar_seller`/`seller`.
   - RLS is enabled on products, inventory, orders and order_items.
   - The app and readonly roles get explicit `USING (true)` policies; without them, enabling RLS would deny them everything.
   - Sellers must set `SET LOCAL app.seller_id = '42'` (read through `current_seller_id()`); when it is unset, they see nothing.
   - `audit_trigger`, `create_events_partition` and the new `refresh_seller_sales_daily()` are SECURITY DEFINER. `audit_log.changed_by` uses `session_user`.
   - Run migrations as `SET ROLE bazaar_owner` so that default privileges apply.
8. **Search (13+).** `search_vector` includes tags (weight C) through `tags_to_text(text[])`, a function declared IMMUTABLE. `to_jsonb(products)` rows in `audit_log` now include `search_vector`.

## Spec deviations and issues to resolve

- **2101 is opt-in.** It lives in `project/db/sharding/`, so `reset.sh` (which only applies top-level files) never applies it, including `reset.sh all`. Module 21 applies it explicitly, on top of `reset.sh 21` or `reset.sh all`. It changes the primary key of orders, products, payments, reviews and promotions to `(seller_id, id)` and adds `seller_id` columns to child tables. Python modules (22+) therefore run against the ordinary, unsharded schema.
- **2101 choices.**
  - Reference tables: users, sellers, categories, addresses.
  - Distributed by `seller_id`: products, inventory, orders, order_items, payments, reviews, product_price_history, promotions.
  - `cart_items` keeps PK `(user_id, product_id)` and its FK to products is **dropped** (it would be distributed by user).
  - events, audit_log and jobs are unchanged.
  - The FK indexes `order_items_product_id_idx` and `payments_order_id_idx` are replaced by `(seller_id, …)` versions.
- **Tables without `created_at`.** `cart_items` (has `added_at`), `product_price_history` (has `valid_from`), `inventory` (has `updated_at`) and `events` (has `occurred_at`) have no `created_at`. This follows the detailed spec, not the README's blanket rule.
- **Extra objects beyond the spec:**
  - CHECKs: `sellers_country_code_upper`, `categories_not_own_parent`, `products_attributes_is_object`, `promotions_range_not_empty`
  - index `jobs_ready_idx`
  - functions `tags_to_text`, `current_seller_id`, `refresh_seller_sales_daily`
  - the optional `p_lease` parameter of `dequeue_jobs`
- **Products/sellers statistics.** `CREATE STATISTICS` is on `addresses(country_code, city)` only. Statistics on products `(seller_id, category_id)` made the GROUP BY estimate worse, so they were left out.
- **0702 data changed.** It was regenerated after WIP commit b19aa92 (street names now match countries), so payment and review rows differ from that commit. The ids above are current.


## Pointers added later

- Releasing reserved stock: nothing in `project/db` releases or consumes reservations. Module 10's solutions define `cancel_order()` (`modules/10-transactions-concurrency/solutions/cancel_order.sql`, error `BZ005`) and an exercise-level `ship_next_order`, as learner extensions. Custom SQLSTATEs used in modules: `BZ001`–`BZ004` (place_order), `BZ005`–`BZ007` (module 10 solutions), `BZ010`/`BZ020`/`BZ030` (module 09 examples).
