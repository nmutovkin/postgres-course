# Module 21: Solutions

<!-- nav:start -->
> [Theory](../README.md) · [Exercises](../exercises.md) · [Project](../project.md) · **Solutions**  
> [← 20 High Availability](../../20-high-availability/README.md) · [Course home](../../../README.md) · [22 psycopg 3 →](../../22-psycopg/README.md)
<!-- nav:end -->

All SQL was run on the module's lab: Citus 14.2 on PostgreSQL 17.10 (`citus-build.sh small`), plain PostgreSQL 17 for the fdw and router cluster (`fdw-setup.sh`), and a single PostgreSQL 17 node with the large dataset for exercise 4. The runnable files are next to this README:

| File | Exercise |
|---|---|
| `ex03_where_is_seller42.sql` | 3 |
| `ex04_skew.sql` | 4 |
| `ex05_colocated_rewrites.sql` | 5 |
| `ex08_fdw_puzzles.sql` | 8 |
| `ex09_new_tables.sql` | 9 |
| `ex10_isolate_seller1.sql` | 10 |
| `ex11_distribute_events.sql` | 11 |
| `milestone-checks.sql` | the project milestone |

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

There is no single right answer. What matters is that you weigh cardinality, distribution, locality and co-location explicitly.

**1. Invoicely: shard by `company_id` (the tenant).** 4,000 companies is plenty of cardinality for 32–256 shards. Every table that belongs to a company (users, clients, invoices, invoice lines) carries `company_id` and is co-located, so every screen is a router query and an invoice with its lines is written in one shard transaction. Distribution is probably skewed (a few large customers), so plan tenant isolation from the start. Global reference data (currencies, tax rates, countries) become reference tables.
*Multi-shard:* the support team's global search by invoice number. Options: include the company in the invoice number (`ACME-2025-0042`) so it can be routed; keep a small lookup table `invoice_number → company_id` (distributed by invoice number, or a reference table if it is small enough); or accept a scatter-gather, since support search is rare and not latency-critical. This is the classic multi-tenant SaaS case, and Citus's row-based model was designed for it.

**2. Chatter: shard `messages` by `conversation_id`.** "Last 50 messages of a conversation" is the hottest query, and the messages of one conversation must be co-located with the conversation and its membership list. `user_id` would scatter a conversation's messages over the shards of all its senders.
*Multi-shard:* "my conversations, ordered by latest activity", because a user belongs to conversations on many shards. Fix it with a second, small table distributed by `user_id`: `user_inbox(user_id, conversation_id, last_message_at, unread)`. It is updated whenever a message arrives (asynchronously, or in the same distributed transaction for small conversations). That is denormalization across shard keys, the standard answer when two hot access paths have different keys. Users' profile data could be a reference table at first, then distributed by `user_id` as it grows.

**3. Sensorium: shard by `device_id`, and range-partition by time inside each shard.** 2 million devices give high cardinality and even load (each device sends at the same rate). A device's time-range query is a router query that also prunes partitions. Monthly or daily partitions make the 90-day retention a `DROP TABLE` (Module 14). Never shard by time alone: all current writes would go to one shard.
*Multi-shard:* the fleet-wide hourly average per region. It is a scatter-gather with partial aggregation (sum and count per shard, combined on the coordinator), which Citus does well. If it is run constantly, maintain a rollup table of hourly aggregates per device or region (a "materialized rollup"). Region and device metadata (`devices`, if small enough, or `regions`) become reference tables.

**4. `reviews` for product pages with seller-less URLs.** Sharding `reviews` by `product_id` would make the product page a router query, but `reviews` would no longer be co-located with `products` (sharded by `seller_id`), and Bazaar's FK from reviews to products would have to go, like `cart_items`' in 2101. The better answer is to keep `seller_id` as the shard key and get the seller into the request: change the URL scheme (`/shops/2/products/24`), or resolve `product_id → seller_id` once through a small cache or lookup table, then run router queries for both the product and its reviews. Name the cost: one extra lookup (cached) on the product page, against keeping checkout, fulfilment and reviews co-located.

---

## Exercise 2 ★: Router or multi-shard?

```text
a  Task Count: 1     both columns of products' key, seller_id = 3 prunes
b  Task Count: 32    id alone says nothing about the shard
c  Task Count: 32    user_id is not the distribution column of orders
d  Task Count: 1     orders pruned to seller 2; users is a reference table, joined locally
e  Task Count: 2     IN on the distribution column prunes to the two shards of sellers 1 and 2
f  Task Count: 32    OR with a non-key condition: any shard may hold user 8's orders
g  Task Count: 1     cart_items is distributed by user_id
h  Task Count: 32    UPDATE by id only
i  Task Count: 1     reviews and products co-located, both filtered on seller_id = 2
j  Task Count: 1     reference table: one shard (read from the local copy)
```

Five single-task statements (a, d, g, i, j) and one two-task statement (e).

Why the application should never send **h**: order 115 exists on exactly one shard, but Citus can't know which, so it sends the `UPDATE` to all 32 shards in a multi-shard transaction. Each of them searches its `orders` shard. Since the statement modified shards on both workers (even if 31 of the 32 shards updated nothing), the commit uses two-phase commit: with `citus.log_remote_commands` on, we saw a `PREPARE TRANSACTION` on each of the connections it had opened. The checkout code always knows the seller. It must pass it: `WHERE seller_id = 1 AND id = 115`. (Without the primary key on `id` alone, the shards even have to use a sequential scan or a non-unique index to find the row.)

The general rule: `EXPLAIN` every query on the hot path and assert `Task Count: 1`. Some teams put that assertion into their test suite.

---

## Exercise 3 ★: Where would seller 42 live?

`ex03_where_is_seller42.sql`:

```sql
SELECT get_shard_id_for_distribution_column('orders', 42) AS shardid;
```

```text
 shardid
---------
  102103
```

```sql
SELECT hashint8(42) AS hash, s.shardid, s.shardminvalue, s.shardmaxvalue
FROM pg_dist_shard s
WHERE s.logicalrelid = 'orders'::regclass
  AND hashint8(42) BETWEEN s.shardminvalue::int AND s.shardmaxvalue::int;
```

```text
    hash    | shardid | shardminvalue | shardmaxvalue
------------+---------+---------------+---------------
 1509752520 |  102103 | 1476395008    | 1610612735
```

```sql
SELECT b AS bucket,
       (SELECT min(shardid) FROM pg_dist_shard WHERE logicalrelid = 'orders'::regclass) + b AS shardid
FROM (SELECT (hashint8(42)::bigint + 2147483648) * 32 / 4294967296 AS b) x;
```

```text
 bucket | shardid
--------+---------
     27 |  102103
```

```sql
SELECT table_name, shardid, nodename
FROM citus_shards
WHERE shardid IN (get_shard_id_for_distribution_column('orders', 42),
                  get_shard_id_for_distribution_column('products', 42))
ORDER BY table_name::text;
```

```text
 table_name | shardid |  nodename
------------+---------+-------------
 orders     |  102103 | m21-worker2
 products   |  102039 | m21-worker2
```

**Reasoning.** The three methods are the same computation seen from different sides: Citus hashes the value with the column type's hash function (`hashint8` for `bigint`), and shard *b* of a 32-shard table owns the *b*-th slice of the 2³² hash values. That is why you can compute placement without asking Citus. A router outside the database, or a data import script that pre-sorts rows by target shard, can rely on it. Notice that the seller doesn't have to exist: placement is a pure function of the value, which is what makes hash sharding need no directory.

`products_102039` is shard 27 of `products` (its shards start at 102012), so it covers the same hash range as `orders_102103`. Co-location guarantees that both are on the same node, which is what makes seller 42's joins local.

*Alternative:* `SELECT * FROM citus_shards WHERE shardid = ...` already includes `shard_name` and size. On a worker, `SET citus.override_table_visibility = off` and `\dt bazaar.orders_102103` shows the actual table.

---

## Exercise 4 ★★: Measure the skew, without Citus

`ex04_skew.sql` on a single-node large Bazaar:

```text
 shards | min  |  avg  |  max  | max_over_avg
--------+------+-------+-------+--------------
     32 | 2373 | 15625 | 82687 |         5.29

 shard | orders | sellers | smallest_seller_id
-------+--------+---------+--------------------
     1 |  82687 |      75 |                  1
    24 |  51699 |      51 |                  2
     6 |  45796 |      63 |                  5

  max  |  avg  | max_over_avg
-------+-------+--------------
 51699 | 13444 |         3.85

   worker    | orders | pct
-------------+--------+------
 m21-worker1 | 281890 | 56.4
 m21-worker2 | 218110 | 43.6
```

**Reasoning.** The key formula is `(hashint8(seller_id)::bigint + 2147483648) * 32 / 4294967296`: shift the signed hash into 0 … 2³²−1, then integer-divide into 32 equal slices. `hashint8` returns an `int4`. The arithmetic must happen in 64 bits, because the shifted hash doesn't fit in an `int4`. PostgreSQL would promote it anyway, since the literal 2147483648 is too large for `int4` and becomes a `bigint`, but the explicit cast makes the intent obvious and survives someone rewriting the constant.

The keys are spread evenly (every bucket has 51–75 sellers). The skew comes entirely from the sellers' sizes: the biggest three buckets are exactly the buckets of sellers 1, 2 and 5, the three largest sellers. Removing seller 1 lowers the ratio from 5.29 to 3.85, but not to 1, because sellers 2–5 are also huge. At the node level, the 56/44 split is tolerable. It comes from which big sellers happen to share a worker: seller 1 is in an odd bucket (worker 2), sellers 2 and 5 in even ones (worker 1).

What to conclude: with a Zipf-distributed tenant size, per-shard balance is impossible with a pure hash. Balance at the node level (the rebalancer's `by_disk_size` strategy) and isolate the largest tenants. Exercise 10 does that.

*Alternative:* `width_bucket(hashint8(seller_id), -2147483648, 2147483648, 32) - 1` gives the same buckets. On a Citus cluster, `get_shard_id_for_distribution_column('orders', seller_id)` grouped and counted gives the same numbers (we checked: all 2,000 sellers agree), but it runs as a distributed query.

---

## Exercise 5 ★★: Make them co-located

All five originals fail on Citus:

```text
a: ERROR:  the query contains a join that requires repartitioning
b: ERROR:  the query contains a join that requires repartitioning
c: ERROR:  correlated subqueries are not supported when the FROM clause contains a reference table
d: ERROR:  correlated subqueries are not supported when the FROM clause contains a reference table
e: ERROR:  recursive CTEs are only supported when they contain a filter on the distribution column
```

The rewrites are in `ex05_colocated_rewrites.sql`.

**a and b: say what you mean.** `inventory.product_id = products.id` is true only for rows of the same seller, but Citus can't know that. It only knows that the two tables are hash-distributed by `seller_id`. Adding `i.seller_id = p.seller_id` (and `o.seller_id = pay.seller_id`) turns the join into a co-located join: 32 independent tasks, each joining two shards on one node.

```sql
SELECT p.seller_id, count(*) AS in_stock
FROM products p
JOIN inventory i ON i.seller_id = p.seller_id AND i.product_id = p.id
WHERE p.status = 'active' AND i.quantity - i.reserved > 0
GROUP BY p.seller_id
ORDER BY 1;
```

```text
 seller_id | in_stock
-----------+----------
         1 |       14
         2 |       12
         3 |       12
         4 |       10
         5 |        3
```

```text
  status   | count |  sum
-----------+-------+--------
 succeeded |     8 |   0.00
 failed    |     1 | 379.00
```

(b's 0.00 is a nice sanity check: each refunded order has one successful payment and one negative refund of the same amount.)

**c: correlated scalar subquery → outer join.**

```sql
SELECT s.shop_name, count(p.id) AS active
FROM sellers s
LEFT JOIN products p ON p.seller_id = s.id AND p.status = 'active'
GROUP BY s.id, s.shop_name
ORDER BY s.id;
```

```text
    shop_name    | active
-----------------+--------
 TechTrove       |     16
 Bookworm Corner |     13
 Casa Cucina     |     12
 Threadbare Co.  |     10
 Maple & Pine    |      3
 Sakura Gadgets  |      0
```

`count(p.id)`, not `count(*)`: Sakura Gadgets has no products, and the outer join produces one row with NULLs for it. The join between a reference table and a distributed table on the distribution column is pushed to the shards: each task joins its products shard with the local copy of `sellers`.

**d: correlated `EXISTS` → join to a `DISTINCT` subquery, or a non-correlated `IN`.**

```sql
SELECT u.id, u.full_name
FROM users u
JOIN (SELECT DISTINCT user_id FROM orders WHERE status = 'pending') o ON o.user_id = u.id
ORDER BY u.id;
```

```text
 id |    full_name
----+------------------
  3 | Giulia Rossi
 10 | Noah Williams
 13 | Olivia Brown
 20 | Oliver Smith
 23 | Charlotte Dubois
```

The `IN (SELECT user_id FROM orders WHERE status = 'pending')` form gives the same five rows: a non-correlated subquery can be planned on its own (recursive planning), and its result is joined to `users`. The `DISTINCT` matters in the join form, or a user with two pending orders would appear twice.

**e: two options.** The tree has three levels, so a fixed-depth join to the reference table works:

```sql
SELECT coalesce(c3.id, c2.id, c1.id) AS root, count(*)
FROM products p
JOIN categories c1      ON c1.id = p.category_id
LEFT JOIN categories c2 ON c2.id = c1.parent_id
LEFT JOIN categories c3 ON c3.id = c2.parent_id
GROUP BY 1
ORDER BY 1;
```

```text
 root | count
------+-------
    1 |    18
    8 |    18
   14 |    15
   21 |    11
```

Or keep the recursion, but isolate it in a `MATERIALIZED` CTE that touches only the reference table. Citus runs it first, as a separate step, and joins its result (an intermediate result) on the shards. It returns the same four rows. The first form breaks silently if someone adds a fourth level. The second form keeps working, at the cost of one extra step per query. A third option is to store the root (or the full path) in a column of `categories`, maintained by a trigger on the coordinator: that is fine for a reference table.

---

## Exercise 6 ★★: Global IDs

**1. Offset sequences.**

```sql
CREATE SEQUENCE orders_id_s03 START 3 INCREMENT BY 16;   -- on logical shard 3
CREATE SEQUENCE orders_id_s11 START 11 INCREMENT BY 16;  -- on logical shard 11
SELECT nextval('orders_id_s03') AS s03, nextval('orders_id_s11') AS s11 FROM generate_series(1, 3);
```

```text
 s03 | s11
-----+-----
   3 |  11
  19 |  27
  35 |  43
```

Shard *k* generates exactly the ids ≡ *k* (mod 16), with shard 16 producing the multiples of 16, so no two shards can collide, and `id % 16` tells you which shard generated an id: `1234567 % 16 = 7`. The catch is that the increment is fixed forever: you can't go to 32 logical shards without new sequences that avoid the old ids. Pick a generous number (1,024), since unused residues cost nothing. Note that "generated by shard 7" is not "lives on shard 7" if rows can move. In Bazaar the *seller* determines placement, not the id.

**2. Decoding the snowflake id.**

```sql
SELECT 368687447741067268 >> 22                AS ms,
       (368687447741067268 >> 12) & 1023       AS shard,
       368687447741067268 & 4095               AS seq,
       to_timestamp(((368687447741067268 >> 22) + 1704067200000) / 1000.0) AS created;
SELECT to_timestamp(((2::numeric ^ 41) - 1 + 1704067200000) / 1000) AS snowflake_end;
```

```text
     ms      | shard | seq |          created
-------------+-------+-----+----------------------------
 87901937423 |     7 |   4 | 2026-10-14 09:12:17.423+00

       snowflake_end
----------------------------
 2093-09-06 15:47:35.551+00
```

41 bits of milliseconds last about 69.7 years from the chosen epoch. Starting the epoch at 2024 rather than 1970 is what makes 41 bits enough.

**3. Citus.** Through the worker:

```sql
-- on m21-worker1
INSERT INTO bazaar.orders (user_id, seller_id, total) VALUES (8, 3, 0) RETURNING id, id >> 48 AS node_group;
```

```text
       id        | node_group
-----------------+------------
 281474976710657 |          1
```

The same `INSERT` on the coordinator returned the next small id (123 in our run). Citus gives each node's copy of the sequence a disjoint range starting at *group id* × 2⁴⁸ + 1 (`m21-worker2` would start at 562,949,953,421,313), so ids from different nodes never collide without any coordination per insert. Clean up with `DELETE FROM orders WHERE seller_id = 3 AND total = 0 AND id > 120;`.

**4. Ordering by time.** Snowflake and UUIDv7 put a timestamp in the most significant bits, so they sort by creation time across the whole system (to the precision of the clocks). Offset sequences are roughly ordered (each shard's sequence advances with its own write rate, so ids from a busy shard run ahead). UUIDv4 is random. Citus's per-node ranges are ordered within a node but not across nodes: any worker-generated id is larger than every coordinator-generated one. Never sort by any of these when you mean "by time": use `created_at`.

---

## Exercise 7 ★★: Clean up after a failed two-phase commit

After `./scripts/2pc-drill.sh`:

**1. Find it.** Look on every participant:

```sql
-- on m21-shard2
SELECT gid, prepared, owner, database FROM pg_prepared_xacts;
```

```text
           gid            |           prepared            |  owner   | database
--------------------------+-------------------------------+----------+----------
 refund-bundle-1791969731 | 2026-10-14 09:22:12.144335+00 | postgres | bazaar
```

(`m21-shard1` returns no rows: its part was committed. Your `gid` has a different timestamp.)

**2. The lock.**

```sql
SET lock_timeout = '1s';
UPDATE shard.orders_p1 SET total = total WHERE seller_id = 3 AND id = 4;
```

```text
ERROR:  canceling statement due to lock timeout
CONTEXT:  while updating tuple (0,1) in relation "orders_p1"
```

The prepared transaction survived the restart and still holds its row lock. `pg_locks` shows its locks with a `virtualtransaction` of `-1/<xid>`: no session owns them, so there is no backend to terminate.

**3. VACUUM.**

```sql
CREATE TABLE public.churn AS SELECT g AS id FROM generate_series(1, 10000) g;
DELETE FROM public.churn;
VACUUM (VERBOSE) public.churn;
```

```text
tuples: 0 removed, 10000 remain, 10000 are dead but not yet removable
removable cutoff: 947, which was 4 XIDs old when operation ended
```

`pg_prepared_xacts.transaction` was 947 in this run: the removable cutoff is pinned at the prepared transaction's xid, so no dead row created after it can be removed, in any table of the cluster. Drop `public.churn` when you are done.

**4. Decide and resolve.** The coordinator wrote its decision before phase 2:

```sql
-- on m21-fdw-coord
SELECT gid, decision FROM public.twopc_log;
```

```text
           gid            | decision
--------------------------+----------
 refund-bundle-1791969731 | commit
```

and shard 1 already shows order 2 as `refunded`. The decision was "commit", and one participant has committed, so the only consistent resolution is to commit the other:

```sql
-- on m21-shard2, connected to database bazaar
COMMIT PREPARED 'refund-bundle-1791969731';
```

Running it while connected to the `postgres` database fails with `ERROR: prepared transaction belongs to another database` and `HINT: Connect to the database where the transaction was prepared to finish it.` Afterwards `pg_prepared_xacts` is empty on both nodes and both orders are `refunded`. If the log had said "abort", or had no entry at all (the coordinator crashed *before* deciding, so no participant can have committed), `ROLLBACK PREPARED` would be correct. That is exactly the rule Citus's `recover_prepared_transactions()` applies. Rolling back here would have left order 2 refunded and order 4 not: a half-refunded bundle.

**5. Monitoring.**

```sql
SELECT gid, database, prepared, now() - prepared AS age
FROM pg_prepared_xacts
WHERE prepared < now() - interval '5 minutes';
```

Alert on any row, on every node. Also alert on `age(datfrozenxid)` growing (Module 16), which an orphan causes over time.

---

## Exercise 8 ★★: postgres_fdw pushdown puzzles

From `ex08_fdw_puzzles.sql`:

| Query | Shipped to the shards | On the coordinator | Why |
|---|---|---|---|
| a | `SELECT created_at FROM shard.orders_p…` (no `WHERE`) | `Filter: (created_at > '2025-05-31'::date)` | `timestamptz > date` converts the date using the session's `TimeZone`: it is only *stable*, so it can't be shipped |
| b | `WHERE ((created_at > '2025-05-31 00:00:00+00'))` | the `count(*)` | a `timestamptz` literal compares without any conversion |
| c | `WHERE ((round((total * 1.20), 2) > 1000::numeric))` | nothing but the `Append` | `with_vat` is a single-statement SQL function: the planner **inlines** it into `round(total * 1.20, 2)` before postgres_fdw looks at the expression |
| d | `SELECT id, total FROM shard.orders_p…` | `Filter: (with_vat_plpgsql(total) > 1000)` | a PL/pgSQL function can't be inlined, and user-defined functions are never shipped (unless they belong to an extension listed in the server's `extensions` option) |
| e (default) | `SELECT seller_id, total ... ORDER BY seller_id` | `GroupAggregate` over a `Merge Append` | partition-wise aggregation is off |
| e (`enable_partitionwise_aggregate`) | `SELECT seller_id, max(total) ... GROUP BY 1` per shard | `Append` only | grouping by the partition key: each partition's groups are complete |
| f | `SELECT status, count(*), sum(total) ... WHERE ((seller_id = 3)) GROUP BY 1` | nothing | querying the foreign table directly: aggregate pushdown applies |
| g | `products_p0` and `products_p1` read **completely**, `order_items_p0` and `p1` filtered by `order_id = 1` | both joins | only `orders` carries `seller_id = 2`; the other tables must be searched on both shards |

Results: a returns 22, c returns 16.

**Reasoning.** postgres_fdw's rule is about *meaning*: it ships an expression only if the remote server is guaranteed to evaluate it exactly as the local one would. Built-in immutable functions and operators qualify. Anything that depends on settings (`TimeZone`, `DateStyle`, the current time), on collations other than the default, or on code that might not exist remotely (your functions) does not. The fix for a is the same as for `now()` in the README: compute the boundary as a `timestamptz` literal or parameter.

c versus d is the most surprising pair. Write small helper functions in SQL rather than PL/pgSQL, and they behave like macros. f versus README section 11.2's query through the parent shows that the shard-pruned single-partition case is handled better when you name the partition. And g shows the theme of the whole module: the `seller_id` filter must be on *every* table. With `p.seller_id = i.seller_id` and `o.seller_id = i.seller_id` added (`g-rewritten` in the file), all three scans are pruned to `p0` with `seller_id = 2` (the joins still run on the coordinator, because of the equivalence-class effect from section 11.3).

> **Pitfall:** pushdown decisions are cost-based. With `use_remote_estimate 'true'` on the servers, we saw f's aggregate executed locally instead: with 25 rows, the planner estimated that local aggregation was cheaper. Small test data can show different plans than production data.

---

## Exercise 9 ★★: Reference or distributed?

`ex09_new_tables.sql`. The decisions:

| Table | Choice | Why |
|---|---|---|
| `shipping_carriers` | reference | ~20 rows, almost never written, joined to orders on every shard |
| `coupons` | reference | small, written rarely by staff, read on every checkout on every shard; `UNIQUE (code)` across the platform is enforceable on a reference table (it has one shard) |
| `seller_payouts` | distributed by `seller_id`, co-located with products | belongs to one seller; joined with that seller's orders when computing payouts; the no-overlap rule is per seller |
| `product_questions` | distributed by `seller_id`, co-located | shown with the product on its page, belongs to the product's seller; FK to products must include `seller_id` |

The key DDL, in the order that works on a running cluster:

```sql
CREATE TABLE seller_payouts (
    seller_id  bigint        NOT NULL,
    id         bigint        GENERATED ALWAYS AS IDENTITY,
    period     daterange     NOT NULL,
    amount     numeric(12,2) NOT NULL,
    paid_at    timestamptz,
    PRIMARY KEY (seller_id, id),
    EXCLUDE USING gist (seller_id WITH =, period WITH &&)
);
SELECT create_distributed_table('seller_payouts', 'seller_id', colocate_with => 'products');
ALTER TABLE seller_payouts ADD FOREIGN KEY (seller_id) REFERENCES sellers (id);
```

Two things went wrong on our first attempt, and both are worth knowing:

1. With `seller_id bigint NOT NULL REFERENCES sellers (id)` written inside `CREATE TABLE`, Citus immediately turned the new table into a *Citus managed local table* (because it references a reference table), and `create_distributed_table` then failed with `cannot complete operation on a table with identity column`. Then `product_questions`, with an FK to a distributed table in its `CREATE TABLE`, failed with `foreign keys from reference tables and local tables to distributed tables are not supported`. **Create, distribute, then add foreign keys.**
2. `ALTER TABLE product_questions ADD FOREIGN KEY ..., ADD FOREIGN KEY ...` failed with `cannot execute ADD CONSTRAINT command with other subcommands`. One constraint per statement. And `ALTER TABLE orders ADD COLUMN carrier_id int REFERENCES shipping_carriers (id)` worked but named the constraint `orders__fkey`, so the solution names it explicitly.

The checks:

```text
    table_name     | citus_table_type | distribution_column | colocation_id
-------------------+------------------+---------------------+---------------
 shipping_carriers | reference        | <none>              |             1
 coupons           | reference        | <none>              |             1
 seller_payouts    | distributed      | seller_id           |             2
 product_questions | distributed      | seller_id           |             2
```

```text
 Custom Scan (Citus Adaptive)
   Task Count: 1
   Tasks Shown: All
   ->  Task
         Node: host=m21-worker1 port=5432 dbname=bazaar
         ->  Nested Loop
               ->  Nested Loop
                     ->  Index Scan using product_questions_seller_id_product_id_idx_102358 on product_questions_102358 q
                           Index Cond: ((seller_id = 2) AND (product_id = 24))
                     ->  Index Scan using products_pkey_102036 on products_102036 p
                           Index Cond: ((seller_id = 2) AND (id = 24))
               ->  Index Scan using users_pkey_102008 on users_102008 u
                     Index Cond: (id = q.user_id)
```

```text
            question            |         title         | full_name
--------------------------------+-----------------------+------------
 Is this the hardcover edition? | Murder at Willow Lane | Marcus Lee
```

and the overlapping payout is rejected *by the shard*:

```text
ERROR:  conflicting key value violates exclusion constraint "seller_payouts_seller_id_period_excl_102326"
DETAIL:  Key (seller_id, period)=(2, [2025-05-15,2025-06-15)) conflicts with existing key (seller_id, period)=(2, [2025-05-01,2025-06-01)).
CONTEXT:  while executing command on m21-worker1:5432
```

**Alternatives.** `coupons` could be distributed by `code` if there were millions of single-use codes, but then checking a coupon at checkout becomes a cross-shard lookup inside the seller's transaction, and the usage counter becomes a hot row. The usual design is a reference table of coupon *definitions* plus a distributed `coupon_redemptions(seller_id, order_id, code)` co-located with orders (with an FK to `coupons`, which is allowed). If `product_questions` were mainly browsed by the *asking user* ("my questions"), that query would be a scatter-gather; it is rare enough here.

---

## Exercise 10 ★★★: Give the biggest seller a node of its own

`ex10_isolate_seller1.sql`, after adding `m21-worker3`:

```text
 new_shard
-----------
    102307

 shardid | shardminvalue | shardmaxvalue |  hash_of_1  |  nodename
---------+---------------+---------------+-------------+-------------
  102307 | -1905060026   | -1905060026   | -1905060026 | m21-worker2

  nodename   | shards |                                             tables
-------------+--------+------------------------------------------------------------------------------------------------
 m21-worker3 |      8 | inventory, order_items, orders, payments, product_price_history, products, promotions, reviews

 shards_per_table
------------------
               34
```

and the router queries:

```text
EXPLAIN ... WHERE seller_id = 1   ->  Task Count: 1, Node: host=m21-worker3
EXPLAIN ... WHERE seller_id = 2   ->  Task Count: 1, Node: host=m21-worker1
```

**Reasoning.** `isolate_tenant_to_new_shard('orders', 1, 'CASCADE', ...)` splits the shard that contains `hashint8(1)` into three: the hash values below it, exactly it, and those above it. `'CASCADE'` does the same for every table in the co-location group, so all eight seller tables are split identically and co-location is preserved. Each table now has 34 shards. The new shard can only ever contain rows whose `seller_id` hashes to −1,905,060,026: seller 1, plus any other id that happens to collide on the 32-bit hash (vanishingly rare, and harmless). `citus_move_shard_placement` moves the whole group: one call moved 8 shards.

We used `block_writes` for the split and `force_logical` for the move, which is the combination that worked reliably in this lab.

**5. Making the split safe in production.** In our lab, an online (`force_logical`) split stalled when rows of the split shard were updated during the split (README section 16.2). We would:

- reproduce it on a staging cluster with the exact Citus and PostgreSQL versions and a production-like write load, and check the release notes for a fix;
- if it reproduces, schedule the split in a quiet window with `block_writes`, measure the blocking time on staging first (5 s for seller 1 on the large dataset), and make the application retry writes that time out (they are idempotent anyway, Module 27);
- set a `statement_timeout` on the split so that a stall is noticed in minutes, and know the cleanup (`citus_cleanup_orphaned_resources()`);
- monitor replication slots on the workers during any online operation: a stalled split keeps a logical slot that retains WAL (Module 19).

Isolating early, while a tenant is still moderately large, is cheaper than isolating the giant later.

---

## Exercise 11 ★★★: Distribute the clickstream

`ex11_distribute_events.sql`:

```text
ERROR:  cannot create constraint on "events"
DETAIL:  Distributed relations cannot have UNIQUE, EXCLUDE, or PRIMARY KEY constraints that do not include the partition column (with an equality operator if EXCLUDE).
```

Module 14 made the primary key `(id, occurred_at)` because a partitioned table's unique constraints must contain its *partition* key. Citus adds the same rule for the *distribution* column. So the new key needs both:

```sql
BEGIN;
ALTER TABLE events DROP CONSTRAINT events_pkey;
ALTER TABLE events ADD PRIMARY KEY (session_id, id, occurred_at);
COMMIT;
SELECT create_distributed_table('events', 'session_id', colocate_with => 'none');
```

```text
 shard_tables
--------------
          832

 relations
-----------
        26

 shard_tables_after
--------------------
                864
```

**Why `session_id`.** It is `NOT NULL`, has very high cardinality (one per visit), spreads evenly, and the analytics questions that matter (funnels: view → add to cart → checkout within a session) need all of a session's events together. `user_id` is NULL for anonymous visitors, and Citus refuses rows with a NULL distribution column (we checked: `ERROR: cannot perform an INSERT with NULL in the partition column`). Per-user analytics then become multi-shard, which is fine for batch jobs. `events` gets its own co-location group because nothing else is distributed by session.

**832 = 26 × 32.** Every relation of the partitioned table (the parent plus 25 partitions: 24 months and the default) is a distributed table with 32 shards of its own. A new month adds 32 more shard tables, and `create_events_partition()` from Module 14 keeps working unchanged, because Citus distributes new partitions of a distributed parent automatically. This multiplication is the reason to keep partition counts and shard counts modest when you combine them: two years of *daily* partitions would be 730 × 32 = 23,360 tables.

**Two levels of pruning.** With only `session_id = ...`, the query is a router query (`Task Count: 1`), but the worker scans that session's shard of all 26 partitions (26 index scans in the plan). Adding the partition key prunes the second level:

```text
 Custom Scan (Citus Adaptive)
   Task Count: 1
   Tasks Shown: All
   ->  Task
         Node: host=m21-worker2 port=5432 dbname=bazaar
         ->  Sort
               Sort Key: events.occurred_at
               ->  Index Scan using events_y2025m06_pkey_102507 on events_y2025m06_102507 events
                     Index Cond: ((session_id = '6f0e8d7c-...'::uuid) AND (occurred_at >= ...) AND (occurred_at < ...))
```

One node, one shard, one partition, and the new primary key `(session_id, id, occurred_at)` serves as the index.

*Alternative:* keep `events` out of the OLTP cluster altogether: ship it to a column store or warehouse through CDC (Module 19). For Bazaar's volumes that is often the better operational choice, and it is what README section 1.2 suggests before sharding.

---

## Exercise 12 ★★★: Rebalance the application-level shards

```bash
docker exec m21-app python router.py dashboard 2
docker exec m21-app python router.py top-sellers

# 2. copy the schema (structure + data) and verify
docker exec m21-shard1 pg_dump -U postgres -d approuter -n s05 \
  | docker exec -i m21-shard2 psql -U postgres -d approuter -q -v ON_ERROR_STOP=1
docker exec m21-shard1 psql -U postgres -d approuter -Atc "SELECT count(*), sum(total) FROM s05.orders"
docker exec m21-shard2 psql -U postgres -d approuter -Atc "SELECT count(*), sum(total) FROM s05.orders"

# 3./4. route to the new node, verify, then drop the old copy
docker exec -e SHARD_MOVES=5:shard2 m21-app python router.py dashboard 2
docker exec m21-shard1 psql -U postgres -d approuter -c "DROP SCHEMA s05 CASCADE"
docker exec -e SHARD_MOVES=5:shard2 m21-app python router.py top-sellers
docker exec m21-app python router.py dashboard 2          # a stale router
```

```text
seller 2 (s05 on shard1)
  cancelled     5       307.92
  delivered    27      1574.50
  pending       1        29.98
  refunded      1        77.98
  shipped       2        81.93
36|2072.31
36|2072.31
seller 2 (s05 on shard2)
  cancelled     5       307.92
  delivered    27      1574.50
  pending       1        29.98
  refunded      1        77.98
  shipped       2        81.93
queried 16 logical shards
  seller    1 revenue     41598.82
  seller    3 revenue      3562.94
  seller    4 revenue      2277.00
...
psycopg.errors.UndefinedTable: relation "s05.orders" does not exist
```

**Reasoning.** Because each logical shard is a schema, the move is "copy one schema, flip one map entry". The verification compares a row count *and* a checksum-like aggregate on both sides before the switch. (For real tables you would compare per-table counts and a hash of the rows, for example `sum(hashtext(t::text))`.) Seller 2's revenue (paid, shipped, delivered: 1,574.50 + 81.93 = 1,656.43) matches `project/db/NOTES.md` before and after.

**The stale router** fails loudly here only because we dropped the old copy. Had we kept it, the stale server would have *silently* read old data, and worse, written new orders to the old location. A production move needs:

1. **A write freeze or dual writes** for the shard being moved: stop writes to `s05` (a flag the router checks), copy, catch up, switch, unfreeze. Or replicate continuously with logical replication (Module 19) from the old to the new location, then do a short freeze to let the replica catch up and switch. That is exactly what Citus's online move automates.
2. **A versioned shard map that every app server reloads** (from a config table or service), with the old location refusing writes after the switch (for example, revoke `INSERT` on `s05` there) so that stale servers fail instead of diverging.
3. **Verification and a delayed delete** of the old copy, after every app server has acknowledged the new map version.

That is a lot of machinery for one seller's orders, and it is the strongest argument in this module for letting the database (Citus) own placement and rebalancing.
