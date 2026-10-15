# Module 21: Exercises

<!-- nav:start -->
> [Theory](README.md) · **Exercises** · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 20 High Availability](../20-high-availability/README.md) · [Course home](../../README.md) · [22 psycopg 3 →](../22-psycopg/README.md)
<!-- nav:end -->

Unless an exercise says otherwise, work on the Citus lab after a fresh small build:

```bash
cd modules/21-sharding/lab
docker compose up -d && ./scripts/citus-build.sh small
docker exec -it m21-coord psql -U postgres -d bazaar
```

Exercises 7, 8 and 12 use the plain-PostgreSQL cluster (`docker compose --profile fdw up -d && ./scripts/fdw-setup.sh`). Exercise 4 runs on any single-node Bazaar with the large dataset (`./project/bin/reset.sh 21` in your usual `course-pg`). Rebuild with `citus-build.sh small` whenever an exercise has changed the cluster. Solutions are in [`solutions/README.md`](solutions/README.md).

---

<!-- toc:start -->
**Contents**

- [Exercise 1 ★: Pick the shard key](#exercise-1--pick-the-shard-key)
- [Exercise 2 ★: Router or multi-shard?](#exercise-2--router-or-multi-shard)
- [Exercise 3 ★: Where would seller 42 live?](#exercise-3--where-would-seller-42-live)
- [Exercise 4 ★★: Measure the skew, without Citus](#exercise-4--measure-the-skew-without-citus)
- [Exercise 5 ★★: Make them co-located](#exercise-5--make-them-co-located)
- [Exercise 6 ★★: Global IDs](#exercise-6--global-ids)
- [Exercise 7 ★★: Clean up after a failed two-phase commit](#exercise-7--clean-up-after-a-failed-two-phase-commit)
- [Exercise 8 ★★: postgres_fdw pushdown puzzles](#exercise-8--postgres_fdw-pushdown-puzzles)
- [Exercise 9 ★★: Reference or distributed?](#exercise-9--reference-or-distributed)
- [Exercise 10 ★★★: Give the biggest seller a node of its own](#exercise-10--give-the-biggest-seller-a-node-of-its-own)
- [Exercise 11 ★★★: Distribute the clickstream](#exercise-11--distribute-the-clickstream)
- [Exercise 12 ★★★: Rebalance the application-level shards](#exercise-12--rebalance-the-application-level-shards)

<!-- toc:end -->

## Exercise 1 ★: Pick the shard key

For each system, choose a shard key and name the one important query (or operation) that becomes **multi-shard** with your choice. Then say what you would do about that query.

1. **Invoicely**, a B2B invoicing SaaS: 4,000 customer companies, each with users, clients, invoices and invoice lines. Almost every screen shows one company's data. The support team has a global search for invoices by number.
2. **Chatter**, a messaging app: users, conversations (2 to 500 members), messages. The main screens are "a conversation's last 50 messages" and "my conversations, ordered by latest activity".
3. **Sensorium**, IoT telemetry: 2 million devices, each sending a reading every 10 s. Readings are kept 90 days. Queries: one device's readings over a time range, and a fleet-wide hourly average per region.
4. **Bazaar's `reviews`**, if the product page (which shows a product's reviews) were the most frequent query, and product URLs looked like `/products/24` with no seller in them.

**Self-check:** for each answer you should be able to state cardinality, distribution and locality in one sentence each, and name at least one table that becomes a reference table.

<details><summary>Hint</summary>

Ask "which id is in almost every `WHERE` clause on the hot path?" and "which rows are written in the same transaction?" Time alone is almost never a good shard key, because all current writes go to the same place.

</details>

---

## Exercise 2 ★: Router or multi-shard?

Predict, for each statement, how many shards Citus will query, then check your prediction with `EXPLAIN (COSTS OFF)` and its `Task Count`.

```sql
-- a
SELECT * FROM products WHERE seller_id = 3 AND id = 36;
-- b
SELECT * FROM products WHERE id = 36;
-- c
SELECT count(*) FROM orders WHERE user_id = 8;
-- d
SELECT o.id, u.full_name FROM orders o JOIN users u ON u.id = o.user_id WHERE o.seller_id = 2;
-- e
SELECT * FROM orders WHERE seller_id IN (1, 2);
-- f
SELECT * FROM orders WHERE seller_id = 1 OR user_id = 8;
-- g
SELECT * FROM cart_items WHERE user_id = 8;
-- h
UPDATE orders SET status = 'shipped' WHERE id = 115;      -- only EXPLAIN it
-- i
SELECT r.rating, p.title
FROM reviews r
JOIN products p ON p.seller_id = r.seller_id AND p.id = r.product_id
WHERE r.seller_id = 2 AND r.product_id = 24;
-- j
SELECT * FROM users WHERE id = 8;
```

**Self-check:** exactly five of the ten are single-task queries, and one runs exactly two tasks. For statement h, explain why the application should never send it.

<details><summary>Hint</summary>

A filter on the distribution column with `=` (or `IN`) prunes. Filters on other columns don't, even when they are very selective. Reference tables have a single shard.

</details>

---

## Exercise 3 ★: Where would seller 42 live?

The small dataset has only six sellers. Seller 42 will sign up next week. Find, **in three independent ways**, which `orders` shard and which worker would hold their orders:

1. with Citus's helper function;
2. with `hashint8()` and the hash ranges in `pg_dist_shard`;
3. with the bucket arithmetic of README section 4, without any Citus metadata (then map the bucket number to a shard id).

Then find the `products` shard that would hold seller 42's products, and check that it is on the same worker.

**Self-check:** all three methods give shard `102103` (bucket 27) on `m21-worker2`. The `products` shard is `102039`, also on `m21-worker2`.

<details><summary>Hint</summary>

`get_shard_id_for_distribution_column(table, value)`. In `pg_dist_shard`, `shardminvalue` and `shardmaxvalue` are text: cast them to `int`. Shard ids of one table are consecutive, so bucket *b* is the table's smallest shard id plus *b*.

</details>

---

## Exercise 4 ★★: Measure the skew, without Citus

On a **single-node** Bazaar with the large dataset (`./project/bin/reset.sh 21`), using only plain SQL and `hashint8`:

1. Compute the number of orders in each of 32 hash buckets of `seller_id`, and report min, average, max and max ÷ average.
2. Show the three biggest buckets with their number of sellers and their smallest `seller_id`.
3. Recompute max ÷ average with seller 1 excluded.
4. Citus places even-numbered shards on `m21-worker1` and odd-numbered ones on `m21-worker2` in a two-worker cluster. What share of all orders would each worker hold?

**Self-check:** max ÷ average is 5.29 (max 82,687, average 15,625). Without seller 1 it is 3.85. The workers would hold 56.4% and 43.6%.

<details><summary>Hint</summary>

The bucket is `(hashint8(seller_id)::bigint + 2147483648) * 32 / 4294967296`: shift the signed 32-bit hash into the range 0 … 2³²−1, then cut it into 32 equal slices. `bucket % 2` gives the worker.

</details>

---

## Exercise 5 ★★: Make them co-located

Each query below worked on the single-node Bazaar and fails on the Citus cluster. Run it, read the error, and rewrite it so that it runs **without** `citus.enable_repartition_joins` and gives the same answer.

```sql
-- a: in-stock active products per seller
SELECT p.seller_id, count(*) AS in_stock
FROM products p
JOIN inventory i ON i.product_id = p.id
WHERE p.status = 'active' AND i.quantity - i.reserved > 0
GROUP BY p.seller_id
ORDER BY 1;

-- b: payments of refunded orders, by payment status
SELECT pay.status, count(*), sum(pay.amount)
FROM payments pay
JOIN orders o ON o.id = pay.order_id
WHERE o.status = 'refunded'
GROUP BY pay.status
ORDER BY 1;

-- c: active products per shop, including shops without any
SELECT s.shop_name,
       (SELECT count(*) FROM products p WHERE p.seller_id = s.id AND p.status = 'active') AS active
FROM sellers s
ORDER BY s.id;

-- d: buyers with a pending order
SELECT u.id, u.full_name
FROM users u
WHERE EXISTS (SELECT 1 FROM orders o WHERE o.user_id = u.id AND o.status = 'pending')
ORDER BY u.id;

-- e: products per root category
WITH RECURSIVE tree AS (
    SELECT id, id AS root FROM categories WHERE parent_id IS NULL
    UNION ALL
    SELECT c.id, t.root FROM categories c JOIN tree t ON c.parent_id = t.id
)
SELECT t.root, count(*) FROM products p JOIN tree t ON t.id = p.category_id GROUP BY t.root ORDER BY 1;
```

**Self-check:** (a) 5 rows, starting `1 | 14`. (b) `succeeded | 8 | 0.00` and `failed | 1 | 379.00`. (c) 6 rows, from `TechTrove | 16` to `Sakura Gadgets | 0`. (d) users 3, 10, 13, 20 and 23. (e) roots 1, 8, 14, 21 with 18, 18, 15 and 11 products.

<details><summary>Hint</summary>

(a) and (b): the join must include `seller_id`. (c) and (d): Citus can't run correlated subqueries whose outer table is a reference table; join to a grouped or `DISTINCT` subquery instead. (e): two options, see README section 14.5.

</details>

---

## Exercise 6 ★★: Global IDs

1. Application-level sharding with 16 logical shards: create, on any plain PostgreSQL, the order-id sequences for logical shards 3 and 11 so that ids never collide across the 16 shards. Show the first three ids of each. Which logical shard generated order `1234567`?
2. Decode the snowflake id `368687447741067268` generated by README section 7's function: the millisecond timestamp, the shard id, the counter and the creation time. Until which date can a 41-bit millisecond timestamp with a 2024-01-01 epoch run?
3. On the Citus cluster, insert one order **through `m21-worker1`** and one through the coordinator. Explain both ids. Clean up afterwards.
4. Which of these schemes keep new ids roughly ordered by time across the whole system: offset sequences, snowflake, UUIDv4, UUIDv7, Citus's per-node ranges?

**Self-check:** (1) 3, 19, 35 and 11, 27, 43; order 1234567 came from logical shard 7. (2) shard 7, counter 4, `2026-10-14 09:12:17.423+00`; the scheme runs out in September 2093. (3) the worker's id is 281,474,976,710,657 or larger.

<details><summary>Hint</summary>

`START k INCREMENT BY 16` (with k from 1 to 16). For decoding, shift right and mask: `id >> 22`, `(id >> 12) & 1023`, `id & 4095`. On a worker, Citus's sequences start at group id × 2⁴⁸ + 1.

</details>

---

## Exercise 7 ★★: Clean up after a failed two-phase commit

Start the fdw lab and run the drill:

```bash
docker compose --profile fdw up -d && ./scripts/fdw-setup.sh
./scripts/2pc-drill.sh
```

The script plays a coordinator that refunds a "bundle" of two orders, one on each shard node, with two-phase commit. It writes its decision to `public.twopc_log` on `m21-fdw-coord`, commits on `m21-shard1`, and "crashes" before committing on `m21-shard2` (which it restarts).

1. Find the orphaned transaction. On which node is it, in which database, since when?
2. Prove that it still holds a lock on order 4 of seller 3 (without waiting forever).
3. Prove that it holds back VACUUM on that node.
4. Decide, with evidence, whether to `COMMIT PREPARED` or `ROLLBACK PREPARED` it, and do it.
5. Write the monitoring query you would alert on.

**Self-check:** at the end `pg_prepared_xacts` is empty on both shard nodes, and both orders (`shard.orders_p0` seller 1 order 2, `shard.orders_p1` seller 3 order 4) have status `refunded`.

<details><summary>Hint</summary>

`pg_prepared_xacts`; `SET lock_timeout`; a `DELETE` plus `VACUUM (VERBOSE)` on a scratch table ("dead but not yet removable"). `COMMIT PREPARED` must run in the database where the transaction was prepared. The coordinator's log is the source of truth.

</details>

---

## Exercise 8 ★★: postgres_fdw pushdown puzzles

On `m21-fdw-coord` (after `fdw-setup.sh`, default settings, `SET search_path = dist, public;`), predict for each query what runs **on the shards** and what runs on the coordinator. Then check with `EXPLAIN (VERBOSE, COSTS OFF)` and its `Remote SQL` lines.

```sql
CREATE FUNCTION public.with_vat(numeric) RETURNS numeric
    LANGUAGE sql IMMUTABLE AS $$ SELECT round($1 * 1.20, 2) $$;
CREATE FUNCTION public.with_vat_plpgsql(p numeric) RETURNS numeric
    LANGUAGE plpgsql IMMUTABLE AS $$ BEGIN RETURN round(p * 1.20, 2); END $$;

-- a
SELECT count(*) FROM orders WHERE created_at > '2025-06-30'::date - 30;
-- b
SELECT count(*) FROM orders WHERE created_at > timestamptz '2025-05-31 00:00+00';
-- c
SELECT id FROM orders WHERE with_vat(total) > 1000;
-- d
SELECT id FROM orders WHERE with_vat_plpgsql(total) > 1000;
-- e  (then again after SET enable_partitionwise_aggregate = on)
SELECT seller_id, max(total) FROM orders GROUP BY seller_id;
-- f
SELECT status, count(*), sum(total) FROM orders_p1 WHERE seller_id = 3 GROUP BY status;
-- g
SELECT o.id, p.title
FROM order_items i
JOIN products p ON p.id = i.product_id
JOIN orders o   ON o.id = i.order_id
WHERE o.seller_id = 2 AND o.id = 1;
```

**Self-check:** the `WHERE` clause is shipped in b, c and f but not in a and d. Query e is aggregated remotely only with the setting on. Query f is aggregated remotely. Query g touches foreign tables on **both** shards. Results: a returns 22, c returns 16.

<details><summary>Hint</summary>

postgres_fdw ships only immutable built-in functions and operators. Comparing a `timestamptz` with a `date` depends on `TimeZone`. Simple SQL functions are *inlined* by the planner before postgres_fdw sees them. For g, look at which tables carry a `seller_id` filter.

</details>

---

## Exercise 9 ★★: Reference or distributed?

Bazaar needs four new tables. Decide for each whether it is a reference table, a distributed table co-located with `products`, or something else, then create and distribute them on the Citus cluster with sensible keys and foreign keys:

- `shipping_carriers` (id, name, tracking URL template): about 20 rows; every order will reference one (add `orders.carrier_id`).
- `coupons` (code, percent off, validity range): platform-wide codes such as `WELCOME10`, unique across the platform, checked at every checkout.
- `seller_payouts` (seller, period, amount, paid at): one per seller and month; periods of one seller must not overlap.
- `product_questions` (product, asking user, question, answer): shown on product pages.

Insert one question by user 8 about product 24 (seller 2), and one payout for seller 2 for May 2025.

**Self-check:** `citus_tables` shows two new reference tables and two new distributed tables in co-location group 2. A query joining `product_questions`, `products` and `users` for seller 2 and product 24 has `Task Count: 1` and returns `Is this the hardcover edition? | Murder at Willow Lane | Marcus Lee` (with your own question text). An overlapping payout for seller 2 is rejected by the shard.

<details><summary>Hint</summary>

Every PK, UNIQUE and EXCLUDE of a distributed table needs `seller_id`. On a running Citus cluster, create the table *without* foreign keys, distribute it, then add the FKs, one `ALTER TABLE ... ADD CONSTRAINT` at a time (README section 13.3). Otherwise you will meet `cannot complete operation on a table with identity column`.

</details>

---

## Exercise 10 ★★★: Give the biggest seller a node of its own

Seller 1 is Bazaar's largest seller. On the small Citus cluster, with `m21-worker3` added:

```bash
docker compose --profile scale up -d worker3
./scripts/citus-init-nodes.sh m21-worker3
```

1. Isolate seller 1 into a shard group of its own (all eight co-located tables).
2. Verify that the new shard's hash range contains exactly one value, and which.
3. Move seller 1's shard group to `m21-worker3` while the other shards stay where they are.
4. Show that a router query for seller 1 now runs on `m21-worker3` and one for seller 2 doesn't.
5. On the large dataset, the same operation with `force_logical` while checkouts were running stalled (README section 16.2). What would you do in production to make such a split safe?

**Self-check:** the new `orders` shard has `shardminvalue = shardmaxvalue = -1905060026` (that is `hashint8(1)`), `orders` has 34 shards, and `m21-worker3` holds exactly 8 distributed shards, one of each seller table.

<details><summary>Hint</summary>

`isolate_tenant_to_new_shard(table, value, 'CASCADE', shard_transfer_mode => ...)` and `citus_move_shard_placement(shardid, source_node, source_port, target_node, target_port, ...)`. Moving one shard moves its whole co-location group.

</details>

---

## Exercise 11 ★★★: Distribute the clickstream

`events` (Module 14) is still a local, monthly-partitioned table on the coordinator.

1. Try to distribute it by `session_id`. Explain the error.
2. Change it so that it can be distributed by `session_id`, then distribute it in its own co-location group. Why `session_id` and not `user_id`?
3. How many shard tables exist for `events` now, and why that many? What happens to the count when `create_events_partition('2027-01-01')` adds a month?
4. Insert two events of one session and one anonymous event, then show that "all events of this session on 2025-06-30" is a router query that scans exactly **one** shard of **one** partition.

**Self-check:** 832 shard tables after distributing, 864 after adding January 2027. The final plan has `Task Count: 1` and a single `Index Scan` on `events_y2025m06_<shardid>`.

<details><summary>Hint</summary>

The PK must contain the distribution column **and** the partition key. What does Citus do with a row whose distribution column is NULL? A query needs *both* the shard key and the partition key to prune at both levels.

</details>

---

## Exercise 12 ★★★: Rebalance the application-level shards

In the app-router lab (`docker exec m21-app python router.py init` then `load`), all of logical shard `s05` (seller 2) lives on `shard1`. Move it to `shard2` without changing the code of `router.py`:

1. Record seller 2's dashboard and the top-sellers report.
2. Copy schema `s05` from `m21-shard1` to `m21-shard2` and verify the copy.
3. Switch the router to the new location (the environment variable `SHARD_MOVES` overrides `SHARD_MAP`).
4. Verify the dashboard and the report through the router, then remove the old copy.
5. What happens to an application server that still runs with the old map? What would a production system need on top of these steps if orders were being written during the move?

**Self-check:** before and after the move, seller 2's dashboard shows `delivered 27 1574.50` and `cancelled 5 307.92`, and `top-sellers` still lists sellers 1, 3, 4 with 41,598.82, 3,562.94 and 2,277.00. After the old copy is dropped, a router without `SHARD_MOVES` fails with `relation "s05.orders" does not exist`.

<details><summary>Hint</summary>

`docker exec m21-shard1 pg_dump -U postgres -d approuter -n s05 | docker exec -i m21-shard2 psql -U postgres -d approuter`. Run the router with `docker exec -e SHARD_MOVES=5:shard2 m21-app python router.py ...`.

</details>
