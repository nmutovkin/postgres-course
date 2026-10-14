# Module 21: Sharding

Bazaar's board has a slide titled "10× in two years". Marketing wants every seller to have a live dashboard, finance wants to keep every order forever, and the Black Friday peak of 3,527 orders in a day is supposed to become 35,000. Modules 19 and 20 gave you replicas for reads and automatic failover, but every `INSERT`, every stock reservation and every row of every table still lives on **one primary**. One day that box is the biggest one you can buy, and it is still too small. **Sharding** splits the data itself across several independent PostgreSQL servers, so that each one stores and writes only a slice. It is the most powerful scaling tool in this course and also the most expensive one: it changes your keys, your queries, your transactions and your operations. This module teaches you when it is worth that price, how it works underneath, and how to do it three ways on Bazaar: in the application, with PostgreSQL's own `postgres_fdw` and partitioning, and with the Citus extension.

## What you'll learn

- When you actually need sharding, and the cheaper alternatives to exhaust first (a bigger server, read replicas, partitioning, archiving, caching).
- The core vocabulary: shard, shard key (distribution column), logical shard versus physical node, router, coordinator.
- How to choose a **shard key**: cardinality, even distribution, query locality, and **co-location** of related tables. Why `seller_id` fits Bazaar, and where it hurts.
- **Hot shards and skew**, measured on Bazaar's Zipf-distributed sellers (seller 1 has 14% of all orders), and what you can do about them.
- Hash, range and directory (lookup) sharding, and why you create many more logical shards than you have servers.
- **Global IDs**: why per-shard identity columns collide, and the fixes (offset sequences, snowflake IDs, UUIDv7, Citus's per-node ranges).
- **Cross-shard queries** (scatter-gather, partial aggregation) and **cross-shard transactions**: two-phase commit with `PREPARE TRANSACTION`, its failure modes, and sagas.
- Approach 1: **application-level sharding** with a small Python router.
- Approach 2: **`postgres_fdw` + hash partitioning**, with pushdown of filters, aggregates and joins, asynchronous execution, and exactly where it breaks.
- Approach 3: **Citus**: coordinator, workers and metadata; distributed, reference and co-located tables; router versus multi-shard plans; repartition joins; distributed transactions; the shard rebalancer and tenant isolation; schema-based sharding; columnar storage; the limitations, and why Bazaar needs the `2101` migration.
- What sharding costs you in day-to-day operations.

## Before you start

This module has its own lab in `modules/21-sharding/lab/`: Docker Compose files for a Citus cluster and for a plain-PostgreSQL "do it yourself" cluster, plus scripts that load Bazaar into them. Nothing here touches your usual `course-pg` container.

```bash
cd modules/21-sharding/lab
docker compose up -d                    # Citus: m21-coord, m21-worker1, m21-worker2
./scripts/citus-build.sh small          # load Bazaar, apply 2101, distribute (about 10 s)
docker exec -it m21-coord psql -U postgres -d bazaar
```

`citus-build.sh small` runs four steps that sections 13 and 14 explain one by one: `load-bazaar.sh` (`reset.sh 11` plus the schema-only migrations of modules 11–15, then `project/db/sharding/2101_schema_shard_prep.sql`), `citus-init-nodes.sh` (registers the workers), `distribute.sql` and `citus-adapt.sql`. The **small** dataset (30 users, 6 sellers, 120 orders) keeps outputs readable and is what most examples use. `citus-build.sh large` loads the 500,000-order dataset of modules 12–20 instead. It takes about 6 minutes and roughly 4 GB of disk across the containers. The timings in sections 4, 14 and 16 come from that large run. A few sections use the second lab, which you start when you get there:

| Container | Profile | Role |
|---|---|---|
| `m21-coord` | default | Citus coordinator: clients connect here. Holds the metadata and copies of reference tables. |
| `m21-worker1`, `m21-worker2` | default | Citus workers: hold the shards. |
| `m21-worker3` | `scale` | A third worker, added in section 16 to practise rebalancing. |
| `m21-fdw-coord`, `m21-shard1`, `m21-shard2` | `fdw` | Plain PostgreSQL 17: the `postgres_fdw` cluster (section 11) and the nodes of the application router (section 10). |
| `m21-app` | `fdw` | Python 3.12 with psycopg 3, runs `lab/router/router.py`. |

The lab uses the `citusdata/citus:14.2.0-pg17` image (Citus 14.2 on PostgreSQL 17.10). Authentication inside the lab network is `trust`, so you can concentrate on sharding. Section 19 lists what changes in production.

This module leans on three earlier ones: **Module 14** (hash partitioning, pruning, partition-wise joins and aggregates; a sharded table is a partitioned table whose partitions live on other machines), **Module 10** (`place_order()`, row locks, deadlock avoidance) and **Modules 19–20** (each shard node is still a PostgreSQL server that needs replicas and failover).

---

## 1. Do you need to shard?

### 1.1 What one PostgreSQL server can do

Most applications never need sharding. Be suspicious of anyone, including yourself, who proposes it before the numbers demand it. A single modern server is very large: 64–192 cores, 1–4 TB of RAM, and tens of TB of NVMe that does hundreds of thousands of random reads per second. Well-indexed PostgreSQL on such a machine serves tens of thousands of transactions per second and databases of several terabytes. Bazaar's large dataset, with 500,000 orders, 5 million events and every module's indexes, is **1.9 GB**. It fits in the RAM of a laptop.

What eventually breaks on one server, roughly in the order teams meet it:

1. **Write throughput.** Every write goes to the primary. WAL is written by one stream and flushed with `fsync`, and each write also updates indexes and dirties pages that the checkpointer must later write out. Replicas (Module 19) do not help, because they replay the same writes.
2. **Working set.** Queries are fast while the hot data (the "working set": recent orders, active products, the upper levels of the indexes) fits in `shared_buffers` plus the OS page cache. When the working set outgrows RAM, every miss becomes a disk read and latency jumps.
3. **Data size and maintenance.** A 20 TB table means hours for an index build or a `pg_dump`, days for a restore, and VACUUM passes that never finish. The table may fit on disk, but you can no longer operate it.
4. **Single-node limits on concurrency.** Lock contention on hot rows, one `max_connections`, one WAL insertion path, one checkpointer.

### 1.2 Try these first

Each step below is cheaper than sharding. Most of them remain useful after you shard.

| Problem | Cheaper fix | Covered in |
|---|---|---|
| CPU or RAM saturated | **Scale up**: a bigger instance. Often the cheapest engineering decision there is. | |
| Slow queries | Indexes, better plans, fewer round trips, no N+1 queries | Modules 11, 12, 25 |
| Too many reads | **Read replicas**, plus a cache (Redis, materialized views) for hot, rarely changing data | Modules 9, 19 |
| Huge, ever-growing tables | **Partitioning** with retention (`DROP` old partitions), **archiving** cold rows to cheaper storage | Module 14 |
| Analytics hurting OLTP | A separate analytics replica or warehouse fed by logical replication or CDC | Module 19 |
| Too many connections | A pooler (PgBouncer) | Module 17 |
| Write volume of non-critical data (clickstream, logs) | Move it out of the OLTP database: a queue, a log store, a separate cluster | |

A useful rule of thumb: **shard when the write workload or the working set of a single logical database will outgrow the largest machine you are willing to run, within the planning horizon, after the fixes above.** "We might be big one day" is not a reason. Retrofitting sharding is hard but not impossible (this module does exactly that to Bazaar), and running a sharded system you did not need costs you every day.

> **In production:** sharding is also an organisational decision. Before you shard, ask whether the problem is really one database or several products sharing one. Splitting `events` (clickstream) into its own cluster is not sharding, but it often buys years.

### 1.3 Why Bazaar shards anyway

Bazaar is a course project, so we shard it to learn. But the business does have the shape that makes sharding work well. It is a **multi-tenant** marketplace: almost everything belongs to a seller, and design decision #1 from the project spec, *one order belongs to exactly one seller*, means that a seller's products, stock, orders, payments and reviews form a self-contained island. Systems shaped like this (SaaS products where each customer's data is separate, marketplaces, game servers per world) shard well. Systems where every query joins everything with everything (a social graph, a global ledger) shard badly.

---

## 2. Sharding vocabulary and architecture

**Sharding** (horizontal partitioning across servers) splits the *rows* of a table across several independent database servers. Each server, a **node**, stores a subset of rows in ordinary tables and indexes. Every subset is a **shard**.

```text
                         application
                              │
                       ┌──────┴──────┐
                       │   router    │   knows: shard key value → shard → node
                       └──┬───────┬──┘   (in the app, in a proxy, or in a coordinator DB)
                          │       │
          ┌───────────────┘       └───────────────┐
   ┌──────┴───────┐                        ┌──────┴───────┐
   │   node A     │                        │   node B     │
   │ shards 0..15 │                        │ shards 16..31│
   │ (sellers     │                        │ (sellers     │
   │  hashing     │                        │  hashing     │
   │  there)      │                        │  there)      │
   └──────────────┘                        └──────────────┘
```

- The **shard key** (Citus calls it the **distribution column**) is the column whose value decides where a row lives. In Bazaar it is `seller_id`.
- The **router** maps a shard key value to a shard and the shard to a node. It can live in the application (section 10), in a coordinator database (sections 11 and 12), or in a proxy.
- A **logical shard** is a unit of data you can move as a whole (for example "all sellers whose hash falls into bucket 17"). A **physical node** is a server. You want many more logical shards than nodes (section 6).
- A query that only needs one shard is a **single-shard** or **router** query. One that needs all of them is a **multi-shard** or **scatter-gather** query.
- Tables that are small and needed everywhere (categories, countries) are not split at all: they are copied in full to every node as **reference tables** (also called broadcast or global tables).

Sharding differs from the techniques you already know:

| | Partitioning (Module 14) | Replication (Module 19) | Sharding |
|---|---|---|---|
| Splits | rows, on one server | nothing: full copies | rows, across servers |
| Scales | maintenance, pruning | reads, availability | writes, storage, working set |
| Queries see | one table | the same data on each copy | one logical table, if a coordinator hides the split |
| Cross-part transactions | free (one server) | n/a | expensive (2PC) or impossible |

In practice you combine all three. Every shard node is itself a primary with replicas and failover (Module 20), and a shard can be a partitioned table.

---

## 3. Choosing the shard key

The shard key is the most consequential decision in a sharded design. Changing it later means moving every row, so it is effectively permanent. Judge a candidate on four criteria.

1. **Cardinality.** There must be many more distinct values than shards, or some shards stay empty. `country_code` (a few dozen values) is a bad key. `seller_id` (2,000 values, growing) is fine. `user_id` (100,000) is better still.
2. **Even distribution.** Rows and, more importantly, *load* should spread evenly. A key with a few huge values creates hot shards (section 4).
3. **Query locality.** The most frequent queries should carry the key, so they can be answered by one shard. A query without the key must ask every shard. This is the criterion people underestimate, and it is the one that decides how your system performs.
4. **Co-location.** Rows that are joined or modified together should share the key, so that they land on the same shard. Then joins, foreign keys and transactions between them stay on one node.

### 3.1 Candidates for Bazaar

| Candidate | Cardinality | Distribution | Locality | Co-location | Verdict |
|---|---|---|---|---|---|
| `seller_id` | 2,000 | Zipf-skewed (seller 1 = 14% of orders) | Seller dashboard, fulfilment, stock and checkout (per seller) are single-shard. Buyer "my orders" is not. | products, inventory, orders, items, payments, reviews, promotions all belong to one seller | **Chosen** |
| `user_id` (buyer) | 100,000 | Even (max 234 orders per buyer) | "My orders" is single-shard. Seller dashboard, stock and checkout are not. | orders by buyer yes; but products and inventory belong to sellers, so every checkout touches stock on other shards | Rejected |
| `order_id` | 500,000 | Perfectly even | Only "fetch order X" is single-shard; everything else is scatter-gather | order_items yes; nothing else | Rejected |
| `created_at` (range) | Continuous | All new writes hit the newest shard | Time-range reports | none | Rejected (a write hot spot) |
| `country_code` of the seller | ~10 | Very uneven | Regional reports | yes | Rejected (cardinality) |

`seller_id` wins because checkout reserves stock (`inventory`, a seller table) and creates orders that belong to the seller. Under `seller_id`, a one-seller checkout runs in one shard transaction. Under `user_id`, every checkout would modify inventory rows living on other shards: every checkout would be a distributed transaction. The losing query, "show buyer 8 their order history", becomes a scatter-gather. It is rarer, it is read-only, and if it ever matters you can maintain a small per-buyer index table distributed by `user_id`.

> **Pitfall:** "most queries must include the key" means the *literal value* must be available when the query is built. A product page URL like `/products/24` does not tell you the seller. Either the URL carries it (`/shops/2/products/24`), or the application first looks it up (a small directory, a cache), or the query fans out to all shards. Design the API around the shard key.

### 3.2 Co-location and the shape of keys

If `orders` is sharded by `seller_id` but `order_items` only has `order_id`, the database cannot tell which shard an item belongs to without first finding its order. So every child table needs the shard key **as a column**, even when it is redundant in a normalized model (Module 7 would call it a deliberate denormalization). And every primary key and unique constraint needs it too, because a shard can only check uniqueness among the rows it holds:

```text
products     PK (id)                  →  PK (seller_id, id)
order_items  PK (order_id, product_id) → PK (seller_id, order_id, product_id)
FK order_items(order_id) → orders(id)  →  FK (seller_id, order_id) → orders (seller_id, id)
```

This reshaping is exactly what `2101_schema_shard_prep.sql` does to Bazaar. Section 13 walks through it line by line. It is also why you should pick the shard key *early*: in a schema designed for it, `seller_id` is part of every key from day one, and the migration is unnecessary.

### 3.3 Tables that don't fit the key

Some tables belong to no single seller:

- **Small, shared lookup data** (`categories`, `sellers` itself, `users`, `addresses`): replicate it to every node as a **reference table**. Joins to it are then always local.
- **Data with a different natural owner** (`cart_items` belongs to a buyer and spans sellers; `events` belongs to a session): shard it by its own key, in a separate co-location group, and accept that joins to seller data are cross-shard.
- **Operational tables** (`jobs`, `audit_log`): often simplest to keep on one node (the coordinator), or move out of the sharded database.

---

## 4. Hot shards and skew

Hashing spreads *keys* evenly. It does not spread *load* evenly when some keys are far bigger than others. Bazaar's large dataset is deliberately Zipf-skewed: seller 1 has 18,500 products and 69,805 of the 500,000 orders (14.0%), seller 2 has 46,196, and seller 2000 has about a dozen. Hash the 2,000 sellers into 32 buckets and count orders per bucket. This plain SQL runs on an ordinary single-node Bazaar (`hashint8` is PostgreSQL's built-in hash for `bigint`, and the bucket arithmetic is exactly what Citus uses, as section 5 shows):

```sql
WITH per_shard AS (
    SELECT ((hashint8(seller_id)::bigint + 2147483648) * 32 / 4294967296) AS shard,
           count(*)                  AS orders,
           count(DISTINCT seller_id) AS sellers
    FROM orders
    GROUP BY 1
)
SELECT count(*) AS shards, min(orders), round(avg(orders)) AS avg, max(orders),
       round(max(orders) / avg(orders), 2) AS max_over_avg
FROM per_shard;
```

```text
 shards | min  |  avg  |  max  | max_over_avg
--------+------+-------+-------+--------------
     32 | 2373 | 15625 | 82687 |         5.29
```

Every bucket holds between 51 and 75 sellers, so the *keys* are spread evenly. But the bucket that contains seller 1 has 82,687 orders, 5.3 times the average and 35 times the smallest bucket. That shard's node will run seller 1's dashboard, seller 1's checkouts and seller 1's Black Friday all by itself.

A counter-intuitive consequence: **more shards make per-shard skew worse, not better**, because one tenant cannot be split by its own key:

| Shard count | Largest shard ÷ average |
|---|---|
| 8 | 1.91 |
| 32 | 5.29 |
| 128 | 20.18 |
| 512 | 70.97 |

What matters in the end is the load per *node*. With two nodes, seller 1's 14% is no problem. With ten nodes the average is 10%, and the node that hosts seller 1 carries at least 14% plus whatever else lives there. The largest tenant's share is a hard floor on the load of the busiest node.

What you can do about hot tenants:

1. **Measure first.** Rows are a proxy. What hurts is CPU, I/O and lock contention per shard. Per-node `pg_stat_statements` shows the real load. Citus also has a `citus_stat_tenants` view with per-tenant query counts and CPU time, once you set `citus.stat_tenants_track = 'all'` (it is `none` by default).
2. **Isolate the tenant.** Give the big seller a shard of its own and put that shard on its own node (Citus: `isolate_tenant_to_new_shard`, section 16; app-level: a directory override, section 10).
3. **Scale that node up.** Nodes do not have to be identical.
4. **Use a compound key** for the hot tenant only, for example `(seller_id, warehouse_id)`, so that one seller spans several shards. You pay with cross-shard queries for that seller.
5. **Keep the hottest rows out of contention.** One product with 1,000 checkouts per second is a row-lock problem (Module 10) that no shard key fixes.

> **In production:** skew changes over time. The tenant that is small at launch becomes the big customer. Build per-shard and per-tenant dashboards *before* you need them, and rehearse moving a tenant.

---

## 5. Hash, range and directory sharding

There are three ways to map a key to a shard.

```text
hash:       shard = bucket(hash(seller_id))          even spread, no range scans across keys
range:      shard = the range containing created_at  range scans; new keys pile onto the last shard
directory:  shard = lookup_table[seller_id]          full control, an extra lookup and a table to keep available
```

**Hash sharding** applies a hash function and splits the 32-bit hash space into equal ranges. Citus does exactly this: `hashint8(seller_id)` gives a signed 32-bit integer, and with 32 shards each shard owns 2³²/32 = 134,217,728 consecutive hash values:

```sql
SELECT shardid, shardminvalue, shardmaxvalue
FROM pg_dist_shard
WHERE logicalrelid = 'orders'::regclass
ORDER BY shardid
LIMIT 3;
```

```text
 shardid | shardminvalue | shardmaxvalue
---------+---------------+---------------
  102076 | -2147483648   | -2013265921
  102077 | -2013265920   | -1879048193
  102078 | -1879048192   | -1744830465
```

`hashint8(1)` is −1,905,060,026, which falls into shard 102077, the second one. That is the bucket formula from section 4: `(hash + 2³¹) × 32 / 2³²` = 1. In the lab, this formula agreed with Citus's own `get_shard_id_for_distribution_column()` for all 2,000 sellers. Hashing gives an even spread of keys and never needs a lookup, but neighbouring keys end up on different shards, so a range query on the key (`seller_id BETWEEN 1 AND 100`) must visit every shard.

**Range sharding** assigns contiguous key ranges to shards: sellers 1–1000 on shard A, 1001–2000 on shard B, or January on one shard and February on the next. Range scans stay local and adding a range is easy. But monotonically growing keys (ids, timestamps) send *all new writes* to the last shard, and you must split ranges by hand as they grow. Time-series systems use it (often combined with hashing: hash by device, range by time within each shard), and Module 14's monthly `events` partitions are range partitioning on one node.

**Directory-based sharding** keeps an explicit table `seller_id → shard`. It gives total control: you can put seller 1 alone on a big node, move a single seller, or place sellers by region for data residency. The cost is an extra lookup on every request (cache it), a directory that must be highly available and consistent, and a migration protocol for every move. Many real systems use hashing by default plus a small directory of exceptions: that is what the router in section 10 does.

> **Under the hood:** never use a language's built-in hash (`hash()` in Python, `hashCode()` in Java) for routing. Python salts string hashes per process, and implementations change between versions. A router must use a stable, documented hash such as `hashint8`, MurmurHash, xxHash or BLAKE2.

---

## 6. Logical shards versus physical nodes

The naive scheme `node = hash(key) % number_of_nodes` breaks the moment you add a node. With 2 nodes, a key goes to `h % 2`. With 3 nodes, to `h % 3`, and about two thirds of all keys change node. Adding capacity would mean moving most of your data while the system runs.

The standard fix is two levels of mapping:

```text
seller_id ──hash──▶ logical shard (fixed count, e.g. 32 or 256) ──map──▶ node
```

The number of logical shards is fixed forever (choose it generously). Only the small *map* from shard to node changes. Adding a node means moving *some whole shards* to it, typically 1/(N+1) of the data, and nothing is re-hashed. In Citus, every distributed table has 32 shards by default (`citus.shard_count`), and with two workers each worker holds 16 of them. Section 16 adds a third worker and moves about a third of the shards there.

How many logical shards? Enough that you can spread them over the largest cluster you expect (a few per node, or many more for finer balancing), but not so many that per-shard overhead dominates. Each shard is a real table with its own indexes, files and statistics, and every multi-shard query opens one task per shard. Citus's default of 32 is a sensible start for clusters of up to about 16 workers. `events` in section 14.6 shows how quickly shard counts multiply: one partitioned table with 25 partitions and 32 shards each becomes 832 tables.

> **Under the hood:** *consistent hashing* (a hash ring with virtual nodes, used by Cassandra and Dynamo) solves the same problem differently: each node owns many small arcs of the ring, so adding a node steals a few arcs from each existing node. Fixed logical shards plus a map achieve the same result with less machinery, and they are what PostgreSQL-based systems use.

---

## 7. Global IDs

On one server, `id bigint GENERATED ALWAYS AS IDENTITY` hands out unique ids from one sequence. Put the same table on two independent servers and each server's sequence starts at 1: both shards create an order 501, and anything that identifies an order by `id` alone (a URL, a payment provider's reference, a log line, a join after you merge data for analytics) now points at two rows. The options:

| Scheme | How | Sortable by time | Size | Caveats |
|---|---|---|---|---|
| **Offset sequences** | shard *k* of *N*: `START k INCREMENT BY N` | roughly | 8 bytes | Fixed *N* (choose generously, e.g. 1024). Ids interleave across shards. |
| **Range per node** | node *g* uses ids from g·2⁴⁸ upward | within a node | 8 bytes | What Citus does for sequences used on workers (below). |
| **Snowflake-style** | 41 bits of milliseconds, 10 bits of shard or node, 12 bits of counter | yes | 8 bytes | Needs sane clocks; at most 4,096 ids per ms per node. |
| **UUIDv4** | 122 random bits | no | 16 bytes | Random inserts scatter across the B-tree (page splits, poor cache locality: Module 11). |
| **UUIDv7** | 48 bits of Unix ms + random bits | yes | 16 bytes | Time-ordered, so B-tree inserts go to the right edge like a sequence. No coordination at all. |
| **Central allocator** | a ticket server hands out blocks of ids | yes | 8 bytes | One more service that must be highly available. |

**Offset sequences** are the simplest to set up with plain PostgreSQL:

```sql
-- on shard 3 of 16 (and START 1..16 on the others)
CREATE SEQUENCE orders_id_seq START 3 INCREMENT BY 16;
```

**A snowflake generator** fits in one SQL function:

```sql
CREATE SEQUENCE public.snowflake_seq;

-- 41 bits: ms since 2024-01-01 | 10 bits: shard id | 12 bits: per-node counter
CREATE OR REPLACE FUNCTION public.snowflake_id(p_shard_id int)
RETURNS bigint
LANGUAGE sql
VOLATILE
AS $$
    -- <<, | and & all have the SAME precedence and associate left: parenthesise every term
    SELECT (((extract(epoch FROM clock_timestamp()) * 1000)::bigint - 1704067200000) << 22)
         | ((p_shard_id::bigint & 1023) << 12)
         | (nextval('public.snowflake_seq') & 4095);
$$;

SELECT id,
       (id >> 12) & 1023                                      AS shard,
       id & 4095                                              AS seq,
       to_timestamp(((id >> 22) + 1704067200000) / 1000.0)    AS created
FROM (SELECT public.snowflake_id(7) AS id FROM generate_series(1, 3)) s;
```

```text
         id         | shard | seq |          created
--------------------+-------+-----+----------------------------
 368687447741067268 |     7 |   4 | 2026-10-14 09:12:17.423+00
 368687447741067269 |     7 |   5 | 2026-10-14 09:12:17.423+00
 368687447741067270 |     7 |   6 | 2026-10-14 09:12:17.423+00
```

> **Pitfall:** the comment in that function is there for a reason. Our first version wrote `a << 22 | b << 12 | c` without the inner parentheses, and PostgreSQL parsed it as `((a << 22) | b) << 12 ...`. The ids overflowed into negative numbers dated 2005. In PostgreSQL, all of these "other" operators bind equally and from left to right.

**UUIDv7** is built into PostgreSQL 18 as `uuidv7()`. On PostgreSQL 17 you can build one from `gen_random_uuid()` by overwriting the first 48 bits with the current Unix time in milliseconds and setting the version bits to 7:

```sql
CREATE OR REPLACE FUNCTION public.uuid_v7()
RETURNS uuid
LANGUAGE sql
VOLATILE
AS $$
    SELECT encode(
        set_bit(set_bit(
            overlay(uuid_send(gen_random_uuid())
                    PLACING substring(int8send((extract(epoch FROM clock_timestamp()) * 1000)::bigint) FROM 3)
                    FROM 1 FOR 6),
            52, 1), 53, 1),       -- version nibble 0100 (v4) becomes 0111 (v7)
        'hex')::uuid;
$$;

SELECT public.uuid_v7() FROM generate_series(1, 3);
```

```text
               uuid_v7
--------------------------------------
 01a139ae-d277-760a-847d-1cd4b2add5f9
 01a139ae-d277-76ed-9b09-dd978a86cc78
 01a139ae-d277-73dd-8cc6-f056dc3c800a
```

All three share the same millisecond prefix `01a139ae-d277`, and the `7` in the third group is the version.

> **PG 18:** `uuidv7()` is built in (`uuidv7(interval)` shifts the timestamp), and `uuid_extract_timestamp()` reads the time back out of a v7 UUID. On PostgreSQL 17, `uuid_extract_timestamp()` only understands version 1 and returns NULL for our v7 values, while `uuid_extract_version()` correctly returns 7.

**What Citus does.** In Citus, an identity column on a distributed table keeps working: when you insert through the coordinator, the coordinator evaluates `nextval()` and sends the literal id to the shard. Because workers can also run distributed queries, each node's copy of the sequence gets its own range, starting at its group id × 2⁴⁸. An insert run directly on `m21-worker1` (group 1) in the lab produced:

```text
       id        | node_group_bits
-----------------+-----------------
 281474976710657 |               1
```

That is 2⁴⁸ + 1, while the coordinator's next id was 123. Ids remain unique across the cluster, but they are no longer dense or ordered by time across nodes. Never use them for ordering. That is what `created_at` is for.

> **Pitfall:** whatever scheme you pick, uniqueness of `id` *alone* is no longer enforced by the database once the primary key is `(seller_id, id)` (section 13). The generator guarantees it. If someone inserts an explicit id (a data import, a restore), nothing will stop a duplicate.

---

## 8. Cross-shard queries

A query that does not pin the shard key has to run on every shard and merge the answers: **scatter-gather**. How cheap that is depends on how much of the work the shards can do before they send anything back.

```text
SELECT status, count(*) FROM orders GROUP BY status;

coordinator:  sum the partial counts per status        ◀── 32 × (≤ 6 rows)
                       ▲            ▲            ▲
shard 0:  SELECT status, count(*) ... GROUP BY status
shard 1:  SELECT status, count(*) ... GROUP BY status
...
```

- **Filters and projections** run on the shards, so only the matching rows and columns travel.
- **Aggregates** split into a partial step on each shard and a combine step on the coordinator: `count` → sum of counts, `sum` → sum of sums, `avg` → sum of sums ÷ sum of counts, `min`/`max` → min/max of partials. A `GROUP BY` that includes the shard key needs no combine step at all, because each group lives entirely on one shard.
- **`ORDER BY ... LIMIT n`** can push the sort and `LIMIT n` into each shard and merge the n·shards candidates.
- **`count(DISTINCT x)`** cannot be combined from per-shard counts, because the same `x` may appear on several shards. The coordinator must pull the distinct values (or use a sketch such as HyperLogLog for an approximate answer). It is cheap only when `x` is the shard key.
- **Joins** between tables sharded on the join key (co-located) run on the shards. Other joins require moving data between nodes (section 14.4).

Two properties of scatter-gather matter in production:

1. **Latency is the slowest shard's latency** plus the coordinator's work. With 32 tasks, a single slow shard (a vacuum, a cold cache, the hot tenant's shard) slows down every global query. The 99th percentile of a fan-out query is much worse than any single shard's 99th percentile.
2. **Cost scales with the number of shards, not with the result.** A query that finds one row by a non-key column still does 32 index lookups and opens up to 32 connections. Fine for a report; poor for a request handler running 1,000 times per second.

So the design rule from section 3 becomes concrete: **requests on the hot path must be single-shard. Scatter-gather is for reports, back-office tools and analytics.**

---

## 9. Cross-shard transactions

### 9.1 The atomicity problem

A buyer's cart holds products from sellers 1 and 2, which live on different nodes. Checkout must reserve stock and create an order on *both*, or on neither. Each node can commit its own part atomically, but nothing in plain SQL makes two independent servers commit together. If the client sends `COMMIT` to node A, then to node B, and B fails (a constraint checked at commit, a crash, a network partition), A has already committed. Section 11.6 reproduces exactly that with `postgres_fdw`.

### 9.2 Two-phase commit (2PC)

**Two-phase commit** splits commit into a vote and a decision.

```text
coordinator                       node A                      node B
    │── BEGIN; UPDATE ...  ─────────▶│                            │
    │── BEGIN; UPDATE ...  ──────────┼───────────────────────────▶│
    │                                │                            │
    │   phase 1: PREPARE TRANSACTION 'gid'                        │
    │── PREPARE ────────────────────▶│ writes everything durably, │
    │◀─ ok (vote yes) ───────────────│ keeps locks, survives crash│
    │── PREPARE ─────────────────────┼───────────────────────────▶│
    │◀─ ok (vote yes) ───────────────┼────────────────────────────│
    │                                                             │
    │   decision: write "commit gid" durably (THE commit point)   │
    │                                                             │
    │   phase 2: COMMIT PREPARED 'gid' on every participant       │
    │── COMMIT PREPARED ────────────▶│                            │
    │── COMMIT PREPARED ─────────────┼───────────────────────────▶│
```

After `PREPARE TRANSACTION`, a participant has promised that it *can* commit: its changes, locks and the transaction itself are stored durably and survive a crash or restart. It has not yet committed: other sessions don't see the changes. Only the coordinator's decision, recorded durably before phase 2 starts, determines the outcome. If any participant votes no (its `PREPARE` fails), the coordinator sends `ROLLBACK PREPARED` to the others.

PostgreSQL implements the participant side: `PREPARE TRANSACTION 'gid'`, `COMMIT PREPARED 'gid'`, `ROLLBACK PREPARED 'gid'`, and the view `pg_prepared_xacts`. It does **not** implement a coordinator: that is the job of your application, a transaction manager, or Citus. The feature is off by default, because `max_prepared_transactions` defaults to 0. The fdw lab nodes set it to 10 (`PREPARE TRANSACTION` fails if it is 0), and Citus sets it automatically.

Here is a prepared transaction on `m21-shard1` (start the `fdw` profile and run `./scripts/fdw-setup.sh` first, see section 11):

```sql
BEGIN;
UPDATE shard.orders_p0 SET status = 'shipped' WHERE seller_id = 1 AND id = 2;
PREPARE TRANSACTION 'checkout-7f3a-shard1';
-- the session is now free: the transaction no longer belongs to it
SELECT gid, prepared, owner, database, transaction FROM pg_prepared_xacts;
```

```text
         gid          |           prepared            |  owner   | database | transaction
----------------------+-------------------------------+----------+----------+-------------
 checkout-7f3a-shard1 | 2026-10-14 09:11:21.134451+00 | postgres | bazaar   |         845
```

Now restart the server (`docker restart m21-shard1`). The transaction is still there, still holding its row lock, now owned by no session at all (its virtual transaction id starts with `-1/`):

```sql
SELECT l.locktype, l.relation::regclass, l.mode, l.granted, l.virtualtransaction
FROM pg_locks l
WHERE l.virtualtransaction LIKE '-1/%';
```

```text
   locktype    |                 relation                 |       mode       | granted | virtualtransaction
---------------+------------------------------------------+------------------+---------+--------------------
 relation      | shard.orders_p0                          | RowExclusiveLock | t       | -1/845
 relation      | shard.orders_p0_pkey                     | RowExclusiveLock | t       | -1/845
 relation      | shard.orders_p0_seller_id_created_at_idx | RowExclusiveLock | t       | -1/845
 transactionid |                                          | ExclusiveLock    | t       | -1/845
```

```sql
SET lock_timeout = '2s';
UPDATE shard.orders_p0 SET status = 'delivered' WHERE seller_id = 1 AND id = 2;
```

```text
ERROR:  canceling statement due to lock timeout
CONTEXT:  while updating tuple (0,2) in relation "orders_p0"
```

### 9.3 Why prepared transactions are dangerous

An orphaned prepared transaction, one whose coordinator died or forgot it, is worse than a long-running transaction:

- It **holds its locks forever**, across restarts. Every writer to those rows queues behind it.
- It **holds back the xmin horizon**, so VACUUM cannot remove any dead row newer than it, anywhere in the cluster. On the same node, deleting 10,000 rows and running `VACUUM (VERBOSE)` showed `10000 are dead but not yet removable` with `removable cutoff: 845`, exactly the prepared transaction's xid. After `ROLLBACK PREPARED`, the same VACUUM removed all 10,000.
- Left long enough, it blocks freezing and drives the cluster towards **transaction ID wraparound** (Module 16).
- No session owns it, so `pg_terminate_backend` can't kill it and `idle_in_transaction_session_timeout` doesn't apply.

Only `COMMIT PREPARED` or `ROLLBACK PREPARED`, run by the same role or a superuser **connected to the same database**, ends it. Which of the two you run must follow the coordinator's decision: if the coordinator recorded "commit" and another participant already committed, rolling this one back breaks atomicity. Exercise 7 makes you resolve such a case.

> **In production:** if you enable `max_prepared_transactions`, alert on `SELECT gid, prepared FROM pg_prepared_xacts WHERE prepared < now() - interval '5 minutes'`. Never use prepared transactions as a convenience ("park a transaction and finish it later"): that is exactly how orphans happen.

Two-phase commit has other costs. Every distributed commit pays extra round trips and extra durable writes (each participant flushes its PREPARE). If the coordinator dies between the phases, participants are stuck *in doubt* until it comes back: 2PC is a **blocking** protocol. Citus mitigates that last part by recording its decisions in a table and recovering automatically (section 15).

### 9.4 Sagas: avoiding distributed transactions

The other approach is to not need atomic commit across shards at all. A **saga** splits a business operation into local transactions, each of which commits on its own shard, and defines a **compensating action** for each step:

```text
checkout of a cart with sellers 1 and 2
  T1 (shard of seller 1): reserve stock, create order A (status 'pending')   compensate: cancel A, release stock
  T2 (shard of seller 2): reserve stock, create order B (status 'pending')   compensate: cancel B, release stock
  T3 (payment service):   charge the buyer once for A + B                   compensate: refund
  T4 (each shard):        mark A and B 'paid'
If T2 fails (out of stock): run the compensation of T1, tell the buyer.
```

Between steps, the system is visibly in an intermediate state (order A exists, B doesn't yet), so the business must tolerate it. Bazaar can, which is another reason "one order per seller" is a good design. Each step must be **idempotent** (it may be retried after a timeout), and the saga's progress must be stored durably, typically with the **transactional outbox** pattern of Module 27. Sagas trade atomicity for availability and scale. Distributed transactions trade the opposite way. Most large systems use sagas for long, multi-service flows and keep 2PC for short operations inside one database system such as Citus.

---

## 10. Approach 1: application-level sharding

The oldest way to shard, and still a very common one, is to make the application responsible for routing. The databases are ordinary, independent PostgreSQL servers that know nothing about each other. The application holds the shard map, opens connections to the right server, and merges results itself.

Start the second lab and load the small dataset into `m21-fdw-coord`, which serves as the "old monolith" the router copies orders from:

```bash
docker compose --profile fdw up -d      # m21-fdw-coord, m21-shard1, m21-shard2, m21-app
./scripts/fdw-setup.sh                  # also used by section 11
```

`lab/router/router.py` is about 150 lines of Python with psycopg 3 (Module 22 teaches psycopg properly; here we only connect, execute and fetch). Its design has three layers:

```text
seller_id ──blake2b hash──▶ logical shard 0..15 ──SHARD_MAP──▶ node (a connection string)
                ▲
     DIRECTORY overrides (seller → logical shard), for sellers you place by hand
```

```python
N_LOGICAL = 16  # fixed forever: changing it re-hashes every seller

NODES = {
    "shard1": os.environ.get("SHARD1_DSN", "host=m21-shard1 user=postgres dbname=approuter"),
    "shard2": os.environ.get("SHARD2_DSN", "host=m21-shard2 user=postgres dbname=approuter"),
}

# Logical shard -> node. Rebalancing = changing entries here (after moving data).
SHARD_MAP = {s: ("shard1" if s < 8 else "shard2") for s in range(N_LOGICAL)}

DIRECTORY: dict[int, int] = {}


def logical_shard(seller_id: int) -> int:
    """Stable hash of the shard key. Never use Python's hash(): it is salted per process."""
    if seller_id in DIRECTORY:
        return DIRECTORY[seller_id]
    digest = hashlib.blake2b(seller_id.to_bytes(8, "big", signed=True), digest_size=8).digest()
    return int.from_bytes(digest, "big") % N_LOGICAL


def route(seller_id: int) -> tuple[str, str]:
    """Return (node name, schema name) for a seller."""
    shard = logical_shard(seller_id)
    return SHARD_MAP[shard], f"s{shard:02d}"
```

Each logical shard is a **schema** (`s00` … `s15`) on its node, containing the same tables. Moving a logical shard to another node then means copying one schema (`pg_dump -n s05 | psql`) and changing one entry of `SHARD_MAP`. Exercise 12 does exactly that. A single-shard query names one node and one schema:

```python
def dashboard(seller_id: int) -> None:
    node, schema = route(seller_id)
    with connect(node) as conn:
        rows = conn.execute(
            f"""SELECT status, count(*), sum(total)
                FROM {schema}.orders
                WHERE seller_id = %s
                GROUP BY status ORDER BY status""",
            (seller_id,),
        ).fetchall()
```

(The schema name comes from our own code, never from user input, so formatting it into the SQL is safe. The value goes through a `%s` parameter, as Module 22 explains.)

```bash
docker exec m21-app python router.py init
docker exec m21-app python router.py load
docker exec m21-app python router.py where 1 2 3 4 5 6
docker exec m21-app python router.py dashboard 1
docker exec m21-app python router.py top-sellers
```

```text
created 16 logical shards on 2 nodes
loaded 120 orders
seller    1 -> logical shard s08 on shard2
seller    2 -> logical shard s05 on shard1
seller    3 -> logical shard s02 on shard1
seller    4 -> logical shard s11 on shard2
seller    5 -> logical shard s01 on shard1
seller    6 -> logical shard s14 on shard2
seller 1 (s08 on shard2)
  cancelled     3      4012.98
  delivered    27     34942.85
  paid          3      4678.97
  pending       2       855.97
  refunded      2       448.97
  shipped       1      1977.00
queried 16 logical shards
  seller    1 revenue     41598.82
  seller    3 revenue      3562.94
  seller    4 revenue      2277.00
```

The revenues match the single-node values in `project/db/NOTES.md` (seller 1: 41,598.82). `top-sellers` is a scatter-gather written by hand. It runs the same aggregate on all 16 schemas, and because each seller lives on exactly one shard, the per-shard sums are already final: the router only merges and re-sorts them with `heapq.nlargest`. A global *average* order value would need more care: you would fetch sums and counts per shard and divide at the end, never average the averages.

**What you gain:** total control (any placement, any mix of hardware, sellers pinned by contract or region), no extra infrastructure, and plain PostgreSQL on every node with all its features: triggers, any extension, `place_order()` unchanged inside a shard.

**What you pay:**

- Every query path in the application must know the shard key and the map. A query that forgets is not slow, it is *wrong*: it silently reads one shard's data.
- Cross-shard queries, aggregation, sorting and pagination are your code. So are cross-shard transactions (sagas or your own 2PC).
- Global IDs, schema migrations on N nodes, backups of N nodes that must be consistent with each other, and the shard map itself (versioned, cached, consistent across all app instances) are your problem.
- Rebalancing is a project: copy, catch up, cut over, verify, delete, while the app keeps running. A stale shard map on one app server reads from or writes to the wrong node.

Large companies run this way successfully (often with a routing library or a proxy such as Vitess in the MySQL world). For a small team it is usually the most expensive option in engineering time.

---

## 11. Approach 2: `postgres_fdw` + partitioning

PostgreSQL can do a lot of the routing itself. Module 14 showed that a partitioned table routes rows to partitions by the partition key and prunes partitions a query cannot touch. **`postgres_fdw`** (foreign data wrapper) makes a table on another PostgreSQL server look like a local table: queries against a *foreign table* are translated into SQL and sent to the remote server. Combine the two, and a partition can be a foreign table on another server:

```text
m21-fdw-coord                                 m21-shard1             m21-shard2
dist.orders  (PARTITION BY HASH (seller_id))
 ├── dist.orders_p0  FOREIGN TABLE ─────────▶ shard.orders_p0
 └── dist.orders_p1  FOREIGN TABLE ─────────────────────────────────▶ shard.orders_p1
```

The coordinator stores no rows. It parses the query, prunes partitions, and ships "remote SQL" to the shards.

### 11.1 Building it

`./scripts/fdw-setup.sh` (which you ran in section 10) does three things. It loads the small Bazaar into `m21-fdw-coord` with `reset.sh 11`, so that schema `bazaar` holds the source data. It creates real tables on each shard node (`lab/fdw/shard.sql`), for example on `m21-shard1`:

```sql
CREATE TABLE shard.orders_p0 (
    seller_id    bigint        NOT NULL,
    id           bigint        NOT NULL,
    user_id      bigint        NOT NULL,
    status       text          NOT NULL,
    total        numeric(12,2) NOT NULL,
    created_at   timestamptz   NOT NULL,
    PRIMARY KEY (seller_id, id)
);
CREATE TABLE shard.order_items_p0 (
    seller_id    bigint        NOT NULL,
    order_id     bigint        NOT NULL,
    product_id   bigint        NOT NULL,
    quantity     int           NOT NULL,
    unit_price   numeric(12,2) NOT NULL,
    PRIMARY KEY (seller_id, order_id, product_id),
    -- shard-local FK: possible because both rows always live on this node
    FOREIGN KEY (seller_id, order_id) REFERENCES shard.orders_p0 (seller_id, id)
);
```

And it builds the coordinator side (`lab/fdw/coord.sql`):

```sql
CREATE EXTENSION postgres_fdw;

CREATE SERVER shard1 FOREIGN DATA WRAPPER postgres_fdw
    OPTIONS (host 'm21-shard1', dbname 'bazaar', async_capable 'true',
             fetch_size '1000', batch_size '500');
CREATE SERVER shard2 FOREIGN DATA WRAPPER postgres_fdw
    OPTIONS (host 'm21-shard2', dbname 'bazaar', async_capable 'true',
             fetch_size '1000', batch_size '500');
CREATE USER MAPPING FOR postgres SERVER shard1 OPTIONS (user 'postgres');
CREATE USER MAPPING FOR postgres SERVER shard2 OPTIONS (user 'postgres');

CREATE TABLE dist.orders (
    seller_id    bigint        NOT NULL,
    id           bigint        NOT NULL,
    user_id      bigint        NOT NULL,
    status       text          NOT NULL,
    total        numeric(12,2) NOT NULL,
    created_at   timestamptz   NOT NULL
) PARTITION BY HASH (seller_id);

CREATE FOREIGN TABLE dist.orders_p0 PARTITION OF dist.orders FOR VALUES WITH (MODULUS 2, REMAINDER 0)
    SERVER shard1 OPTIONS (schema_name 'shard', table_name 'orders_p0');
CREATE FOREIGN TABLE dist.orders_p1 PARTITION OF dist.orders FOR VALUES WITH (MODULUS 2, REMAINDER 1)
    SERVER shard2 OPTIONS (schema_name 'shard', table_name 'orders_p1');
-- ... the same for products and order_items

INSERT INTO dist.orders
SELECT seller_id, id, user_id, status::text, total, created_at FROM bazaar.orders;
```

The options deserve a word each. A **server** is a remote PostgreSQL instance and a **user mapping** says which remote role a local role becomes (with a password in production). `fetch_size` is the number of rows fetched per round trip when reading (default 100). `batch_size` is the number of rows sent per round trip when inserting (default 1, which makes bulk loads painfully slow). `async_capable` lets the coordinator scan several shards concurrently. The final `INSERT` shows tuple routing: each row goes to the partition its `seller_id` hashes to, and that foreign table forwards it to its shard. The result:

```text
m21-shard1: order_items_p0=143 orders_p0=74 products_p0=33
m21-shard2: order_items_p1=92 orders_p1=46 products_p1=29
```

Sellers 1 and 2 hashed to `p0`, sellers 3, 4 and 5 to `p1`, so shard 1 got 74 orders against shard 2's 46. With six sellers and two buckets, you can see skew even on toy data. (PostgreSQL's hash partitioning uses a seeded variant of the hash, `hashint8extended`, so it places keys differently from Citus. `satisfies_hash_partition('dist.orders'::regclass, 2, 0, 3)` tells you whether seller 3 belongs to remainder 0.)

> **Pitfall:** there are no primary keys, unique constraints or indexes on `dist.orders`: PostgreSQL cannot build an index on a foreign table, so the parent can't have one either. Uniqueness and foreign keys exist only inside each shard, and nothing stops the same `(seller_id, id)` from being inserted directly into the wrong shard. All writes must go through the coordinator.

### 11.2 What gets pushed down

The crucial question for every query is how much work happens on the shards. `EXPLAIN (VERBOSE)` shows a `Remote SQL:` line for every foreign scan, and that is exactly what each shard executes. Run the following on `m21-fdw-coord` with `SET search_path = dist;`.

**Filters on the shard key prune, and other filters are shipped:**

```sql
EXPLAIN (VERBOSE, COSTS OFF)
SELECT status, count(*), sum(total) FROM orders WHERE seller_id = 3 GROUP BY status;
```

```text
 GroupAggregate
   Output: orders.status, count(*), sum(orders.total)
   Group Key: orders.status
   ->  Foreign Scan on dist.orders_p1 orders
         Output: orders.status, orders.total
         Remote SQL: SELECT status, total FROM shard.orders_p1 WHERE ((seller_id = 3)) ORDER BY status ASC NULLS LAST
```

Only `orders_p1` is scanned (partition pruning) and the `WHERE` runs remotely. But look at what is *not* pushed: the `GROUP BY`. The shard sends every one of seller 3's orders, sorted, and the coordinator counts them. The aggregation is planned on the partitioned parent, and postgres_fdw only pushes aggregates that are planned directly on a foreign table.

**A `GROUP BY` on the shard key is pushed with partition-wise aggregation** (off by default, Module 14):

```sql
SET enable_partitionwise_aggregate = on;
EXPLAIN (VERBOSE, COSTS OFF)
SELECT seller_id, count(*), sum(total) FROM orders GROUP BY seller_id;
```

```text
 Append
   ->  Async Foreign Scan
         Output: orders.seller_id, (count(*)), (sum(orders.total))
         Relations: Aggregate on (dist.orders_p0 orders)
         Remote SQL: SELECT seller_id, count(*), sum(total) FROM shard.orders_p0 GROUP BY 1
   ->  Async Foreign Scan
         Output: orders_1.seller_id, (count(*)), (sum(orders_1.total))
         Relations: Aggregate on (dist.orders_p1 orders_1)
         Remote SQL: SELECT seller_id, count(*), sum(total) FROM shard.orders_p1 GROUP BY 1
```

Now each shard aggregates its own sellers and returns one row per seller, and **Async Foreign Scan** means both shards work at the same time. Without the setting, the plan is a `Merge Append` that pulls every order row to the coordinator.

**A `GROUP BY` on another column is not pushed**, even with the setting:

```sql
EXPLAIN (VERBOSE, COSTS OFF)
SELECT status, count(*) FROM orders GROUP BY status;
```

```text
 Finalize GroupAggregate
   Output: orders.status, count(*)
   Group Key: orders.status
   ->  Merge Append
         Sort Key: orders.status
         ->  Partial GroupAggregate
               Output: orders.status, PARTIAL count(*)
               Group Key: orders.status
               ->  Foreign Scan on dist.orders_p0 orders
                     Output: orders.status
                     Remote SQL: SELECT status FROM shard.orders_p0 ORDER BY status ASC NULLS LAST
         ->  Partial GroupAggregate
               ...
```

The plan has the right *shape*, with partial aggregates per partition and a final combine step, but the partial aggregation runs on the coordinator. PostgreSQL 17's postgres_fdw cannot ship a partial aggregate, so every row's `status` crosses the network. For a report over millions of orders that is the difference between kilobytes and gigabytes.

**Joins are pushed when both sides live on the same server and the join includes the partition key**:

```sql
SET enable_partitionwise_join = on;
EXPLAIN (VERBOSE, COSTS OFF)
SELECT o.seller_id, sum(i.quantity * i.unit_price)
FROM orders o
JOIN order_items i ON i.order_id = o.id AND i.seller_id = o.seller_id
WHERE o.status = 'delivered'
GROUP BY o.seller_id;
```

```text
 Append
   ->  Async Foreign Scan
         Output: o.seller_id, (sum(((i.quantity)::numeric * i.unit_price)))
         Relations: Aggregate on ((dist.orders_p0 o) INNER JOIN (dist.order_items_p0 i))
         Remote SQL: SELECT r4.seller_id, sum((r6.quantity * r6.unit_price)) FROM (shard.orders_p0 r4 INNER JOIN shard.order_items_p0 r6 ON (((r4.id = r6.order_id)) AND ((r4.seller_id = r6.seller_id)) AND ((r4.status = 'delivered')))) GROUP BY 1
   ->  Async Foreign Scan
         Output: o_1.seller_id, (sum(((i_1.quantity)::numeric * i_1.unit_price)))
         Relations: Aggregate on ((dist.orders_p1 o_1) INNER JOIN (dist.order_items_p1 i_1))
         Remote SQL: SELECT r5.seller_id, sum((r7.quantity * r7.unit_price)) FROM (shard.orders_p1 r5 INNER JOIN shard.order_items_p1 r7 ON (((r5.id = r7.order_id)) AND ((r5.seller_id = r7.seller_id)) AND ((r5.status = 'delivered')))) GROUP BY 1
```

This is the ideal plan: partition-wise join plus partition-wise aggregate, each pair of co-located partitions joined *and* aggregated on its own shard, both shards in parallel. It only works because `orders` and `order_items` are partitioned identically on `seller_id`, the join condition says `i.seller_id = o.seller_id`, and each pair of matching partitions lives on the same foreign server. That is co-location, built by hand. Without both settings, the same query becomes a coordinator-side `Hash Join` over two `Append`s that pull every delivered order and every order item.

### 11.3 Surprises

These are the cases where pushdown silently fails. Exercise 8 has more.

**A single seller's join is *not* pushed.** Ask for seller 3 only, and the same join is executed on the coordinator:

```sql
EXPLAIN (VERBOSE, COSTS OFF)
SELECT o.id, o.total, sum(i.quantity)
FROM orders o
JOIN order_items i ON i.order_id = o.id AND i.seller_id = o.seller_id
WHERE o.seller_id = 3
GROUP BY o.id, o.total;
```

```text
 GroupAggregate
   ...
   ->  Merge Join
         Merge Cond: (o.id = i.order_id)
         ->  Foreign Scan on dist.orders_p1 o
               Remote SQL: SELECT seller_id, id, total FROM shard.orders_p1 WHERE ((seller_id = 3)) ORDER BY id ASC NULLS LAST, total ASC NULLS LAST
         ->  Sort
               Sort Key: i.order_id
               ->  Foreign Scan on dist.order_items_p1 i
                     Remote SQL: SELECT seller_id, order_id, quantity FROM shard.order_items_p1 WHERE ((seller_id = 3))
```

The planner turned `o.seller_id = 3` and `i.seller_id = o.seller_id` into two separate filters, `seller_id = 3` on each side (an *equivalence class*), so no join clause on the partition key remains, and partition-wise join does not apply. `WHERE o.seller_id IN (3, 4)` (both in `p1`) keeps the join clause, and that join *is* pushed. It is counter-intuitive: the most selective query, the single-shard one, is the one that is not pushed down. The workaround is to query the foreign partition directly (`FROM dist.orders_p1`), which means the application has to know the partition: application routing again.

**Only immutable built-in functions and operators are shipped.** postgres_fdw sends an expression to the remote server only if it is guaranteed to mean the same thing there: built-in, immutable, and no local collation. `now()` is stable, so this filter runs on the coordinator after fetching *every* row:

```sql
EXPLAIN (VERBOSE, COSTS OFF)
SELECT count(*) FROM orders WHERE created_at > now() - interval '30 days';
```

```text
 Aggregate
   Output: count(*)
   ->  Append
         ->  Async Foreign Scan on dist.orders_p0 orders_1
               Filter: (orders_1.created_at > (now() - '30 days'::interval))
               Remote SQL: SELECT created_at FROM shard.orders_p0
         ->  Async Foreign Scan on dist.orders_p1 orders_2
               Filter: (orders_2.created_at > (now() - '30 days'::interval))
               Remote SQL: SELECT created_at FROM shard.orders_p1
```

Compute the boundary in the application and pass a literal or parameter (`created_at > '2025-06-01'`) and it is pushed. Your own functions are never shipped unless they belong to an extension listed in the server's `extensions` option and installed on both sides.

**`ORDER BY ... LIMIT` is not pushed through the partitioned table.** `SELECT id, total FROM orders ORDER BY total DESC LIMIT 3` sends `ORDER BY total DESC` to each shard but no `LIMIT`. The coordinator reads the sorted streams with a `Merge Append` and stops after three rows, which is cheap here only because the fetch size limits how much is buffered.

**Joins with local tables run locally.** `dist.orders` joined to `bazaar.sellers` (a normal table on the coordinator) fetches the matching orders from both shards and joins on the coordinator. There are no reference tables. If you want the join on the shard, you must keep a copy of `sellers` on every shard and expose it through per-server foreign tables yourself.

### 11.4 Statistics and estimates

The planner has no statistics for foreign tables until you `ANALYZE` them (which samples rows from the shard). For partitions that change constantly, `use_remote_estimate 'true'` makes the planner ask the shard for an `EXPLAIN` estimate during planning, which costs a round trip per foreign scan but gives better join orders. Neither changes the pushdown limits above.

### 11.5 Writes and transactions

`INSERT`, `UPDATE` and `DELETE` through `dist.orders` are routed like reads: an `UPDATE ... WHERE seller_id = 3 AND id = 4` is pruned to one partition and sent as one remote `UPDATE`. postgres_fdw opens one remote transaction per server per local transaction, and uses `SERIALIZABLE` on the remote side when the local transaction is `SERIALIZABLE`, otherwise `REPEATABLE READ`, so that several scans within one statement see a consistent snapshot of each shard. Note *of each shard*: there is no snapshot that is consistent across shards. A report can see seller 1's order on shard 1 committed and the matching row on shard 2 not yet.

At commit, postgres_fdw commits the remote transactions **one after another, without two-phase commit**. (`parallel_commit` in PostgreSQL 15+ sends the COMMITs at the same time, which is faster but still not atomic.)

### 11.6 Seeing the atomicity hole

Make shard 2 reject a commit with a deferred constraint trigger, an integrity check that runs only at `COMMIT`:

```sql
-- on m21-shard2
CREATE FUNCTION shard.no_negative_totals() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.total < 0 THEN RAISE EXCEPTION 'order % total would be negative', NEW.id; END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER orders_total_check AFTER UPDATE ON shard.orders_p1
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION shard.no_negative_totals();
```

Then run one transaction on the coordinator that changes an order on each shard:

```sql
-- on m21-fdw-coord
SET search_path = dist;
BEGIN;
UPDATE orders SET total = total + 100 WHERE seller_id = 1 AND id = 40;   -- shard 1
UPDATE orders SET total = -1 WHERE seller_id = 3 AND id = 4;             -- shard 2: fails at COMMIT
COMMIT;
SELECT seller_id, id, total FROM orders WHERE (seller_id, id) IN ((1, 40), (3, 4)) ORDER BY 1;
```

```text
ERROR:  order 4 total would be negative
CONTEXT:  PL/pgSQL function shard.no_negative_totals() line 3 at RAISE
remote SQL command: COMMIT TRANSACTION
 seller_id | id |  total
-----------+----+---------
         1 | 40 | 9040.00
         3 |  4 |  280.00
```

The client was told that the transaction failed, yet order 40 now has 100.00 more (it was 8,940.00): shard 1 committed before shard 2 refused. In our runs the coordinator always committed shard 1 first, even when the statements were in the opposite order. The order depends on postgres_fdw's internal connection list, not on your SQL. (Put order 40 back with `UPDATE orders SET total = 8940.00 WHERE seller_id = 1 AND id = 40;` and drop the trigger on shard 2.) A network failure between the two `COMMIT`s produces the same result without any trigger.

### 11.7 Where it breaks down

`postgres_fdw` + partitioning is a good tool for **federating** data (querying another database, migrating, archiving cold partitions to another server) and for read-mostly analytics where you control the queries. As a general-purpose sharding layer it has hard limits:

- **No atomic cross-shard commit**, as just shown. There is no built-in two-phase commit in postgres_fdw.
- **No consistent cross-shard snapshot.**
- **Limited pushdown:** no partial aggregates, no `LIMIT` through the partitioned parent, single-partition joins not pushed, only immutable built-in expressions, and no joins with tables that live elsewhere.
- **No global constraints:** no primary key, unique constraint or foreign key on the parent.
- **No rebalancing.** Moving data means a new hash layout (`MODULUS 4`), which re-hashes every row, or building your own directory with list partitions. There is no tool that moves a partition while writes continue.
- **No metadata sync.** DDL must be repeated on every shard by hand, and the foreign table definitions must match.
- **The coordinator is a single point of failure and the merge point for every query.**

These limits are exactly what Citus was built to remove.

