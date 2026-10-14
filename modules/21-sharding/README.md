# Module 21: Sharding

<!-- nav:start -->
> **Theory** · [Exercises](exercises.md) · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 20 High Availability](../20-high-availability/README.md) · [Course home](../../README.md) · [22 psycopg 3 →](../22-psycopg/README.md)
<!-- nav:end -->

Bazaar's board has a slide titled "10× in two years". Marketing wants every seller to have a live dashboard, finance wants to keep every order forever, and the Black Friday peak of 3,527 orders in a day is supposed to become 35,000. Modules 19 and 20 gave you replicas for reads and automatic failover, but every `INSERT`, every stock reservation and every row of every table still lives on **one primary**. One day that box is the biggest one you can buy, and it is still too small. **Sharding** splits the data itself across several independent PostgreSQL servers, so that each one stores and writes only a slice. It is the most powerful scaling tool in this course and also the most expensive one: it changes your keys, your queries, your transactions and your operations. This module teaches you when it is worth that price, how it works underneath, and how to do it three ways on Bazaar: in the application, with PostgreSQL's own `postgres_fdw` and partitioning, and with the Citus extension.

<!-- toc:start -->
**Contents**

- [What you'll learn](#what-youll-learn)
- [Before you start](#before-you-start)
- [1. Do you need to shard?](#1-do-you-need-to-shard)
  - [1.1 What one PostgreSQL server can do](#11-what-one-postgresql-server-can-do)
  - [1.2 Try these first](#12-try-these-first)
  - [1.3 Why Bazaar shards anyway](#13-why-bazaar-shards-anyway)
- [2. Sharding vocabulary and architecture](#2-sharding-vocabulary-and-architecture)
- [3. Choosing the shard key](#3-choosing-the-shard-key)
  - [3.1 Candidates for Bazaar](#31-candidates-for-bazaar)
  - [3.2 Co-location and the shape of keys](#32-co-location-and-the-shape-of-keys)
  - [3.3 Tables that don't fit the key](#33-tables-that-dont-fit-the-key)
- [4. Hot shards and skew](#4-hot-shards-and-skew)
- [5. Hash, range and directory sharding](#5-hash-range-and-directory-sharding)
- [6. Logical shards versus physical nodes](#6-logical-shards-versus-physical-nodes)
- [7. Global IDs](#7-global-ids)
- [8. Cross-shard queries](#8-cross-shard-queries)
- [9. Cross-shard transactions](#9-cross-shard-transactions)
  - [9.1 The atomicity problem](#91-the-atomicity-problem)
  - [9.2 Two-phase commit (2PC)](#92-two-phase-commit-2pc)
  - [9.3 Why prepared transactions are dangerous](#93-why-prepared-transactions-are-dangerous)
  - [9.4 Sagas: avoiding distributed transactions](#94-sagas-avoiding-distributed-transactions)
- [10. Approach 1: application-level sharding](#10-approach-1-application-level-sharding)
- [11. Approach 2: `postgres_fdw` + partitioning](#11-approach-2-postgres_fdw--partitioning)
  - [11.1 Building it](#111-building-it)
  - [11.2 What gets pushed down](#112-what-gets-pushed-down)
  - [11.3 Surprises](#113-surprises)
  - [11.4 Statistics and estimates](#114-statistics-and-estimates)
  - [11.5 Writes and transactions](#115-writes-and-transactions)
  - [11.6 Seeing the atomicity hole](#116-seeing-the-atomicity-hole)
  - [11.7 Where it breaks down](#117-where-it-breaks-down)
- [12. Approach 3: Citus architecture](#12-approach-3-citus-architecture)
- [13. Preparing Bazaar for Citus](#13-preparing-bazaar-for-citus)
  - [13.1 What `2101_schema_shard_prep.sql` changes, and why](#131-what-2101_schema_shard_prepsql-changes-and-why)
  - [13.2 Distributing the tables](#132-distributing-the-tables)
  - [13.3 What broke on the way](#133-what-broke-on-the-way)
- [14. Querying Citus](#14-querying-citus)
  - [14.1 Router queries](#141-router-queries)
  - [14.2 Multi-shard queries](#142-multi-shard-queries)
  - [14.3 Reference tables join locally](#143-reference-tables-join-locally)
  - [14.4 Joins that are not co-located](#144-joins-that-are-not-co-located)
  - [14.5 SQL that needs rewriting](#145-sql-that-needs-rewriting)
  - [14.6 Measuring: the seller dashboard and the global report](#146-measuring-the-seller-dashboard-and-the-global-report)
- [15. Writes and distributed transactions in Citus](#15-writes-and-distributed-transactions-in-citus)
  - [15.1 Checkout: what happens to `place_order()`](#151-checkout-what-happens-to-place_order)
  - [15.2 Two-phase commit under the hood](#152-two-phase-commit-under-the-hood)
  - [15.3 Delegating a whole function to a worker](#153-delegating-a-whole-function-to-a-worker)
- [16. Growing the cluster: rebalancing and tenant isolation](#16-growing-the-cluster-rebalancing-and-tenant-isolation)
  - [16.1 Adding a worker and rebalancing](#161-adding-a-worker-and-rebalancing)
  - [16.2 Isolating a hot tenant](#162-isolating-a-hot-tenant)
- [17. More Citus features](#17-more-citus-features)
  - [17.1 Schema-based sharding](#171-schema-based-sharding)
  - [17.2 Columnar storage](#172-columnar-storage)
- [18. Limitations checklist](#18-limitations-checklist)
- [19. The operational cost of sharding](#19-the-operational-cost-of-sharding)
- [Summary](#summary)
- [What's next](#whats-next)

<!-- toc:end -->

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

This module has its own lab in `modules/21-sharding/lab/`: one Docker Compose file with a Citus cluster and a plain-PostgreSQL "do it yourself" cluster, plus scripts that load Bazaar into them. Nothing here touches your usual `course-pg` container.

```bash
cd modules/21-sharding/lab
docker compose up -d                    # Citus: m21-coord, m21-worker1, m21-worker2
./scripts/citus-build.sh small          # load Bazaar, apply 2101, distribute (about 10 s)
docker exec -it m21-coord psql -U postgres -d bazaar
```

`citus-build.sh small` runs four steps that sections 13 and 14 explain one by one: `load-bazaar.sh` (`reset.sh 11` plus the schema-only migrations of modules 11–15, then `project/db/sharding/2101_schema_shard_prep.sql`), `citus-init-nodes.sh` (registers the workers), `distribute.sql` and `citus-adapt.sql`. The **small** dataset (30 users, 6 sellers, 120 orders) keeps outputs readable and is what most examples use. `citus-build.sh large` loads the 500,000-order dataset of modules 12–20 instead. It takes about 6 minutes and roughly 5 GB of disk across the containers (including WAL). The timings in sections 4, 14 and 16 come from that large run. A few sections use the second lab, which you start when you get there:

| Container | Profile | Role |
|---|---|---|
| `m21-coord` | default | Citus coordinator: clients connect here. Holds the metadata and copies of reference tables. |
| `m21-worker1`, `m21-worker2` | default | Citus workers: hold the shards. |
| `m21-worker3` | `scale` | A third worker, added in section 16 to practise rebalancing. |
| `m21-fdw-coord`, `m21-shard1`, `m21-shard2` | `fdw` | Plain PostgreSQL 17: the `postgres_fdw` cluster (section 11) and the nodes of the application router (section 10). |
| `m21-app` | `fdw` | Python 3.12 with psycopg 3, runs `lab/router/router.py`. |

The lab uses the `citusdata/citus:14.2.0-pg17` image (Citus 14.2 on PostgreSQL 17.10). Authentication inside the lab network is `trust`, so you can concentrate on sharding. Section 19 lists what changes in production.

This module leans on three earlier ones: **[Module 14](../14-partitioning/)** (hash partitioning, pruning, partition-wise joins and aggregates; a sharded table is a partitioned table whose partitions live on other machines), **[Module 10](../10-transactions-concurrency/)** (`place_order()`, row locks, deadlock avoidance) and **[Modules 19](../19-replication/)–[20](../20-high-availability/)** (each shard node is still a PostgreSQL server that needs replicas and failover).

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

How many logical shards? Enough that you can spread them over the largest cluster you expect (a few per node, or many more for finer balancing), but not so many that per-shard overhead dominates. Each shard is a real table with its own indexes, files and statistics, and every multi-shard query opens one task per shard. Citus's default of 32 is a sensible start for clusters of up to about 16 workers. Exercise 11 shows how quickly shard counts multiply: distributing the monthly-partitioned `events` table (a parent plus 25 partitions) with 32 shards each creates 832 shard tables.

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

The planner has no statistics for foreign tables until you `ANALYZE` them (which samples rows from the shard). For partitions that change constantly, `use_remote_estimate 'true'` makes the planner ask the shard for an `EXPLAIN` estimate during planning, which costs a round trip per foreign scan but gives better join orders. Neither changes the pushdown limits above, but remember that pushdown *within* those limits is a cost-based decision. With `use_remote_estimate` switched on, an aggregate over 25 rows of one partition was executed on the coordinator instead of the shard, because the planner estimated that to be cheaper. Small test data can produce different plans from production data.

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

---

## 12. Approach 3: Citus architecture

**Citus** is an open-source PostgreSQL extension (originally from Citus Data, now Microsoft) that turns a group of PostgreSQL servers into one distributed database. It plugs into the planner and executor through PostgreSQL's hooks, so clients talk to it with ordinary SQL and ordinary drivers.

```text
            clients (psql, the app, PgBouncer)
                         │
                ┌────────┴────────┐
                │   coordinator   │  m21-coord: metadata (pg_dist_*), distributed planner,
                │                 │  copies of reference tables, local tables
                └──┬───────────┬──┘
     SQL per shard │           │ SQL per shard      (workers also hold the metadata,
         ┌─────────┴──┐     ┌──┴─────────┐           so they can run queries too)
         │  worker 1  │     │  worker 2  │
         │ orders_102076    │ orders_102077 ...    shards = ordinary tables named <table>_<shardid>
         │ users_102008│    │ users_102008│        reference table: one shard, a copy on every node
         └────────────┘     └────────────┘
```

- The **coordinator** holds the metadata and plans queries. Applications connect to it.
- **Workers** store **shards**: ordinary PostgreSQL tables named `<table>_<shardid>`, with ordinary indexes, constraints and triggers. Each worker is a full PostgreSQL server, so `EXPLAIN` on a worker shows ordinary plans.
- A **distributed table** is hash-partitioned on its distribution column into `citus.shard_count` shards (default 32), spread across the workers.
- A **reference table** has a single shard, replicated to every node, including the coordinator.
- **Co-location groups**: distributed tables with the same distribution column type, shard count and placement share a `colocation_id`. Their shard *n* always covers the same hash range and lives on the same node.
- Since Citus 11, the metadata is synchronised to every worker, so any node can plan and run queries ("query from any node"). In this lab we only use the coordinator.

The metadata lives in catalog tables that you can query. After `citus-build.sh small`:

```sql
SELECT nodeid, nodename, groupid, noderole, shouldhaveshards FROM pg_dist_node ORDER BY nodeid;
```

```text
 nodeid |  nodename   | groupid | noderole | shouldhaveshards
--------+-------------+---------+----------+------------------
      1 | m21-coord   |       0 | primary  | f
      2 | m21-worker1 |       1 | primary  | t
      3 | m21-worker2 |       2 | primary  | t
```

```sql
SELECT logicalrelid, partmethod, column_to_column_name(logicalrelid, partkey) AS dist_col,
       colocationid, repmodel
FROM pg_dist_partition
WHERE logicalrelid IN ('orders'::regclass, 'users'::regclass, 'cart_items'::regclass);
```

```text
 logicalrelid | partmethod | dist_col  | colocationid | repmodel
--------------+------------+-----------+--------------+----------
 users        | n          |           |            1 | t
 orders       | h          | seller_id |            2 | s
 cart_items   | h          | user_id   |            3 | s
```

`partmethod` `h` is hash distribution, `n` none (reference). `repmodel` `t` means a two-phase-commit-replicated reference table, and `s` means streaming (one copy of each shard; use PostgreSQL replication for redundancy). `pg_dist_shard` holds each shard's hash range (section 5), and `pg_dist_placement` says which node group holds it. The friendlier views `citus_tables` and `citus_shards` join it all together:

```sql
SELECT table_name, shardid, shard_name, nodename, shard_size
FROM citus_shards
WHERE table_name IN ('orders'::regclass, 'users'::regclass)
ORDER BY shardid
LIMIT 6;
```

```text
 table_name | shardid |      shard_name      |  nodename   | shard_size
------------+---------+----------------------+-------------+------------
 users      |  102008 | bazaar.users_102008  | m21-worker1 |      49152
 users      |  102008 | bazaar.users_102008  | m21-worker2 |      49152
 users      |  102008 | bazaar.users_102008  | m21-coord   |      49152
 orders     |  102076 | bazaar.orders_102076 | m21-worker1 |      32768
 orders     |  102077 | bazaar.orders_102077 | m21-worker2 |      73728
 orders     |  102078 | bazaar.orders_102078 | m21-worker1 |      32768
```

`get_shard_id_for_distribution_column('orders', 1)` returns 102077: seller 1's orders live in `orders_102077` on `m21-worker2`, next to `products_102013`, `order_items_102109` and so on: the shards with the same index in the co-location group.

> **Under the hood:** on a worker, `\dt` doesn't list the shard tables. Since Citus 11, shards are hidden from catalog queries so that tools and ORMs connected to a worker don't see thousands of tables. `SET citus.override_table_visibility = off` shows them.

---

## 13. Preparing Bazaar for Citus

### 13.1 What `2101_schema_shard_prep.sql` changes, and why

Citus imposes the rules from section 3 as hard errors. The prep migration rewrites Bazaar's keys so that all seller tables can be distributed by `seller_id` and co-located. Here is each change with the error you get without it (all errors are real, from the lab).

**1. Primary keys, unique and exclusion constraints must include the distribution column.** A shard can only check uniqueness among its own rows. `orders.id` alone could be duplicated across shards without any shard noticing, so Citus refuses:

```text
ERROR:  cannot create constraint on "products"
DETAIL:  Distributed relations cannot have UNIQUE, EXCLUDE, or PRIMARY KEY constraints that do not include the partition column (with an equality operator if EXCLUDE).
```

So 2101 changes `products`, `orders`, `payments`, `reviews` and `promotions` to `PRIMARY KEY (seller_id, id)`. `inventory` becomes `(seller_id, product_id)`, `order_items` `(seller_id, order_id, product_id)` and `product_price_history` `(seller_id, product_id, valid_from)`. The unique "one review per buyer and product" becomes `UNIQUE (seller_id, product_id, user_id)`. That is equivalent, because a product has exactly one seller. The exclusion constraint against overlapping promotions becomes `EXCLUDE USING gist (seller_id WITH =, product_id WITH =, active_during WITH &&)`: Citus accepts an exclusion constraint only if it compares the distribution column with `=`. The ids stay identity columns and remain unique in practice, but the database no longer *enforces* uniqueness of `id` alone (section 7).

**2. A foreign key between distributed tables must include the distribution column on both sides**, so the check never leaves the shard. 2101 adds `seller_id` to `inventory`, `order_items`, `payments`, `reviews`, `product_price_history` and `promotions`, backfills it from the parent (`UPDATE order_items t SET seller_id = o.seller_id FROM orders o WHERE o.id = t.order_id`), makes it `NOT NULL`, and replaces the single-column FKs with composite ones:

```sql
ALTER TABLE order_items ADD CONSTRAINT order_items_order_fkey
    FOREIGN KEY (seller_id, order_id) REFERENCES orders (seller_id, id) ON DELETE CASCADE;
```

A composite FK needs a unique constraint on exactly those referenced columns, which is why the parents' primary keys had to change first. And because `(seller_id, order_id)` is now the lookup path, the old single-column FK indexes from Module 11 are replaced by `(seller_id, ...)` indexes.

**3. Foreign keys from distributed tables to reference tables are fine** in any form. `orders.user_id → users`, `products.category_id → categories` and `orders.shipping_address_id → addresses` stay unchanged, because every node has a full copy of `users`, `categories` and `addresses`.

**4. `cart_items` does not fit.** It belongs to a buyer and holds products of many sellers, so it will be distributed by `user_id`. It can't keep a foreign key to `products` (distributed by `seller_id`), because Citus does not support FKs between tables in different co-location groups:

```text
ERROR:  cannot create foreign key constraint since relations are not colocated or not referencing a reference table
DETAIL:  A distributed table can only have foreign keys if it is referencing another colocated hash distributed table or a reference table
```

So 2101 drops `cart_items_product_id_fkey`. Checkout validates every product anyway. A cart may now contain a product that has since been deleted, and the cart UI must cope with that.

**5. Compatibility triggers.** Code written before 2101, such as `place_order()`, inserts `order_items` without `seller_id`. 2101 adds `BEFORE INSERT` triggers that fill `seller_id` from the parent row. Its own comment warns that on Citus you should drop them and pass `seller_id` explicitly. We do that in a moment.

**6. Shard-local `orders.total` maintenance and simpler RLS.** The totals trigger now updates `orders` by `(seller_id, id)`, and the row-level security policies on `inventory` and `order_items` compare their own `seller_id` instead of running a subquery against the parent.

2101 is an offline migration: it rewrites large tables and takes `ACCESS EXCLUSIVE` locks (about 45 s on the large dataset). Module 26 shows how to make such changes online.

### 13.2 Distributing the tables

With the schema prepared, `lab/scripts/distribute.sql` assigns every table a role. The core of it:

```sql
SET citus.shard_count = 32;
SET citus.multi_shard_modify_mode = 'sequential';
SET citus.enable_local_reference_table_foreign_keys = off;
SET citus.enable_unsafe_triggers = on;

SELECT create_reference_table('users');
SELECT create_reference_table('sellers');
SELECT create_reference_table('categories');
SELECT create_reference_table('addresses');

SELECT create_distributed_table('products',              'seller_id');
SELECT create_distributed_table('inventory',             'seller_id', colocate_with => 'products');
SELECT create_distributed_table('orders',                'seller_id', colocate_with => 'products');
SELECT create_distributed_table('order_items',           'seller_id', colocate_with => 'products');
SELECT create_distributed_table('payments',              'seller_id', colocate_with => 'products');
SELECT create_distributed_table('reviews',               'seller_id', colocate_with => 'products');
SELECT create_distributed_table('product_price_history', 'seller_id', colocate_with => 'products');
SELECT create_distributed_table('promotions',            'seller_id', colocate_with => 'products');

SELECT create_distributed_table('cart_items', 'user_id', colocate_with => 'none');
```

`create_reference_table` and `create_distributed_table` create the shards on the workers, copy the existing rows into them, and leave the original table on the coordinator as an empty "shell" that the planner rewrites queries against. (The copied rows stay on the coordinator's disk until `truncate_local_data_after_distributing_table()`, which the script calls at the end.) `colocate_with => 'products'` puts every seller table into products' co-location group. Without it, Citus would still co-locate tables with the same distribution column type and shard count, but writing it down protects you from surprises. `colocate_with => 'none'` gives `cart_items` its own group. On the large dataset, the whole script took 90 seconds; `order_items` (1.27 million rows) alone took 24.6 s.

`events`, `audit_log` and `jobs` remain ordinary local tables on the coordinator. Queries that touch only them are plain PostgreSQL. Exercise 11 distributes `events`.

### 13.3 What broke on the way

The settings, the dropped triggers and the two `ALTER` statements at the top of `distribute.sql` each exist because of a problem we hit. These are the kind of surprises a real migration to Citus produces.

1. **Automatic "Citus managed" local tables.** After `create_reference_table('users')`, Citus noticed that local tables (`orders`, `reviews`, ...) have foreign keys to the new reference table and silently added them to its metadata as *Citus local tables*. When we then distributed `products`, the conversion failed with `ERROR: cannot complete operation on a table with identity column`. `SET citus.enable_local_reference_table_foreign_keys = off` disables the automatic conversion. That's safe here because every table gets an explicit role within the same script. The same trap catches **new** tables later: `CREATE TABLE ... REFERENCES users (id)` makes the new table a Citus local table on the spot. On a running cluster, create a new table without foreign keys, distribute it, and then add its foreign keys with one `ALTER TABLE ... ADD CONSTRAINT` each (exercise 9).
2. **Parallel DDL and reference-table foreign keys.** With the default parallel mode, Citus refused: `cannot execute parallel DDL on table "inventory" after DDL command on reference table "categories" because there is a foreign key between them`. When distributed tables have FKs to reference tables, Citus must use a single connection per worker to keep the FK checks consistent. `citus.multi_shard_modify_mode = 'sequential'` tells it to do so.
3. **Triggers.** `cannot distribute relation "products" because it has triggers`. A trigger on a distributed table runs **on the worker, against one shard**, and Citus cannot verify what it does. `citus.enable_unsafe_triggers = on` allows them, and then it is on you to keep only safe ones:
   - `products_audit` and `orders_audit` insert into `audit_log`, which exists only on the coordinator. On a worker: `ERROR: relation "audit_log" does not exist`. Dropped (auditing moves to CDC from the WAL, Module 19, or into the application).
   - The `orders.total` triggers are statement-level triggers with **transition tables** (`REFERENCING NEW TABLE AS new_items`). Citus cannot run them on shards: `ERROR: executor could not find named tuplestore "new_items"`. Dropped. The new checkout computes the total itself.
   - The six 2101 `fill_seller_id_*` triggers look up the parent row in another distributed table: a hidden distributed query inside every row insert. Dropped. The application passes `seller_id`.
   - The three `set_updated_at` triggers only modify `NEW`. Kept.
4. **The NOLOGIN owner.** Citus opens connections to the workers **as the current role**. Our migrations run as `SET ROLE bazaar_owner` (Module 15), and `REFRESH MATERIALIZED VIEW` runs as the view's owner, which is also `bazaar_owner`. Both failed with `FATAL: role "bazaar_owner" is not permitted to log in` on the workers. The script runs `ALTER ROLE bazaar_owner LOGIN` (Citus propagates roles and `ALTER ROLE` to all nodes). In production, every role that runs distributed queries also needs credentials on the workers, through `.pgpass` or Citus's `pg_dist_authinfo`.
5. **Settings don't reach the workers.** Module 15's seller policies compare `seller_id` with `current_seller_id()`, which reads the setting `app.seller_id` that the seller portal sets with `SET LOCAL`. The policies were distributed along with the tables, but the setting wasn't: as `bazaar_seller` with `app.seller_id = '2'`, `SELECT count(*) FROM orders` returned **0** instead of 36, because on the worker connections the setting was empty. `citus.propagate_set_commands = 'local'` makes Citus forward every `SET LOCAL` to the workers taking part in the transaction. The script sets it for the whole database with `ALTER DATABASE bazaar SET ...`.

Then `lab/scripts/citus-adapt.sql` fixes the SQL that no longer runs. That is section 14.5 and section 15.

---

## 14. Querying Citus

### 14.1 Router queries

When a query filters every distributed table on the distribution column with `=`, Citus prunes it to one shard and sends the whole query to that shard's node. This is a **router query**, and it is how the hot path should look. On the large dataset (seller 42):

```sql
EXPLAIN (COSTS OFF)
SELECT status, count(*) FROM orders WHERE seller_id = 42 GROUP BY status;
```

```text
 Custom Scan (Citus Adaptive)
   Task Count: 1
   Tasks Shown: All
   ->  Task
         Node: host=m21-worker2 port=5432 dbname=bazaar
         ->  HashAggregate
               Group Key: status
               ->  Bitmap Heap Scan on orders_102103 orders
                     Recheck Cond: (seller_id = 42)
                     ->  Bitmap Index Scan on orders_pkey_102103
                           Index Cond: (seller_id = 42)
```

`Custom Scan (Citus Adaptive)` is Citus's executor node. `Task Count: 1` means one shard. Below the `Task` is an ordinary PostgreSQL plan, as executed on `m21-worker2` against `orders_102103`. Router queries support all of SQL that PostgreSQL supports, because the worker runs the query as it is: window functions, CTEs, `FOR UPDATE`, anything. Joins between co-located tables on the same seller stay router queries too (small dataset, seller 3):

```sql
EXPLAIN (COSTS OFF)
SELECT p.id, p.title, sum(i.quantity) AS units
FROM order_items i
JOIN orders o   ON o.seller_id = i.seller_id AND o.id = i.order_id
JOIN products p ON p.seller_id = i.seller_id AND p.id = i.product_id
WHERE i.seller_id = 3 AND o.status = 'delivered'
GROUP BY p.id, p.title
ORDER BY units DESC
LIMIT 3;
```

```text
 Custom Scan (Citus Adaptive)
   Task Count: 1
   Tasks Shown: All
   ->  Task
         Node: host=m21-worker2 port=5432 dbname=bazaar
         ->  Limit
               ->  Sort
                     Sort Key: (sum(i.quantity)) DESC
                     ->  GroupAggregate
                           ...
                                       ->  Nested Loop
                                             ->  Index Scan using products_pkey_102027 on products_102027 p
                                                   Index Cond: (seller_id = 3)
                                             ->  Bitmap Heap Scan on orders_102091 o
                                                   Recheck Cond: (seller_id = 3)
                                                   Filter: (status = 'delivered'::order_status)
                                                   ...
                                       ->  Index Scan using order_items_seller_id_product_id_idx_102123 on order_items_102123 i
                                             Index Cond: ((seller_id = 3) AND (product_id = p.id))
```

Three co-located shards (`products_102027`, `orders_102091`, `order_items_102123`), all on `m21-worker2`, joined locally. The `LIMIT` runs on the worker too.

### 14.2 Multi-shard queries

Without a distribution-column filter, Citus sends one task per shard and combines the results on the coordinator:

```sql
EXPLAIN (COSTS OFF)
SELECT status, count(*) FROM orders GROUP BY status;
```

```text
 HashAggregate
   Group Key: remote_scan.status
   ->  Custom Scan (Citus Adaptive)
         Task Count: 32
         Tasks Shown: One of 32
         ->  Task
               Node: host=m21-worker1 port=5432 dbname=bazaar
               ->  HashAggregate
                     Group Key: status
                     ->  Seq Scan on orders_102076 orders
```

Read it bottom-up. Each of the 32 tasks aggregates its shard. The coordinator's `HashAggregate` over `remote_scan` combines the partial counts. Unlike postgres_fdw, Citus **does** push partial aggregates for any `GROUP BY`. It also pushes `ORDER BY ... LIMIT` into each task:

```sql
EXPLAIN (COSTS OFF)
SELECT o.id, o.total FROM orders o ORDER BY o.total DESC LIMIT 3;
```

```text
 Limit
   ->  Sort
         Sort Key: remote_scan.total DESC
         ->  Custom Scan (Citus Adaptive)
               Task Count: 32
               Tasks Shown: One of 32
               ->  Task
                     Node: host=m21-worker1 port=5432 dbname=bazaar
                     ->  Limit
                           ->  Sort
                                 Sort Key: total DESC
                                 ->  Seq Scan on orders_102076 o
```

Each shard returns its own top 3, so the coordinator sorts 96 rows. A `GROUP BY` on the distribution column needs no combine step at all: `SELECT o.seller_id, sum(...) ... GROUP BY o.seller_id` shows just a `Custom Scan` with 32 tasks, because every seller's group is complete within its shard. `count(DISTINCT user_id)` is the expensive case from section 8: each task returns its distinct `user_id`s, and the coordinator counts distinct values across all of them.

The executor is *adaptive*: for a multi-shard query it opens several connections per worker and runs tasks in parallel, bounded by `citus.max_adaptive_executor_pool_size` (per query) and `citus.max_shared_pool_size` (per worker, for all sessions together).

### 14.3 Reference tables join locally

Every node has a full copy of a reference table, so joining a distributed table to it never moves data:

```sql
EXPLAIN (COSTS OFF)
SELECT s.id, s.shop_name, count(*) AS orders, sum(o.total) AS revenue
FROM orders o
JOIN sellers s ON s.id = o.seller_id
WHERE o.created_at >= '2025-01-01' AND o.created_at < '2026-01-01'
GROUP BY s.id, s.shop_name
ORDER BY revenue DESC
LIMIT 5;
```

```text
 Limit
   ->  Sort
         Sort Key: (sum(remote_scan.revenue)) DESC
         ->  HashAggregate
               Group Key: remote_scan.id, remote_scan.shop_name
               ->  Custom Scan (Citus Adaptive)
                     Task Count: 32
                     Tasks Shown: One of 32
                     ->  Task
                           Node: host=m21-worker1 port=5432 dbname=bazaar
                           ->  HashAggregate
                                 Group Key: s.id
                                 ->  Hash Join
                                       Hash Cond: (o.seller_id = s.id)
                                       ->  Seq Scan on orders_102076 o
                                             Filter: ...
                                       ->  Hash
                                             ->  Seq Scan on sellers_102009 s
```

`sellers_102009` is the worker's local copy. The price of reference tables is paid on writes: every `INSERT`, `UPDATE` or `DELETE` on a reference table is applied on every node in a distributed transaction. That is fine for `categories` and `sellers`. Whether it is fine for `users` (100,000 rows, a new row at every sign-up, an update at every login if you store `last_login_at` there) is a judgement call. Section 18 comes back to it.

### 14.4 Joins that are not co-located

A join between distributed tables on anything other than their distribution columns needs data moved between nodes. Citus refuses by default. `cart_items` is distributed by `user_id`, `orders` by `seller_id`:

```sql
SELECT count(*) FROM cart_items c JOIN orders o ON o.user_id = c.user_id;
```

```text
ERROR:  the query contains a join that requires repartitioning
HINT:  Set citus.enable_repartition_joins to on to enable repartitioning
```

With `SET citus.enable_repartition_joins = on`, Citus runs a **repartition join**: every shard of both tables is re-hashed on the join key (a *map* step writing intermediate files), the pieces are shuffled between workers, and matching pieces are joined (*merge* tasks). The plan shows `MapMergeJob` with 32 map tasks per side and 12 merge tasks. On the large dataset it took **7.0 s** and returned 99,178 rows. It works, but it is a batch-analytics tool, not something for a request handler.

The same idea also fails for the *same* tables joined without the distribution column. On the small dataset, `orders JOIN order_items ON i.order_id = o.id` (no `seller_id`) raises the same repartition error. Add `AND i.seller_id = o.seller_id` and it becomes a co-located join with 32 independent tasks. **Every join between seller tables must carry `seller_id`.**

Often a query can be rephrased so that the non-co-located part is small. "Buyers who have something in their cart and have ordered from seller 42 before" as a join needs repartitioning (4.7 s). Written with a CTE:

```sql
WITH buyers AS MATERIALIZED (
    SELECT DISTINCT user_id FROM orders WHERE seller_id = 42
)
SELECT count(DISTINCT c.user_id)
FROM cart_items c
JOIN buyers b ON b.user_id = c.user_id;
```

```text
 count
-------
   130
```

it took **231 ms**. Citus runs the CTE first as a separate router query (it shows up as `Distributed Subplan 3_1` in `EXPLAIN`), ships its result to the workers as an *intermediate result*, and joins it to each `cart_items` shard locally (`read_intermediate_result` in the worker plan). This technique, **recursive planning**, is how Citus handles most subqueries and CTEs it can't push down whole: plan the inner part separately, then treat its result like a small reference table.

### 14.5 SQL that needs rewriting

These are the failures we met on Bazaar's existing SQL, with the fixes `citus-adapt.sql` and `queries.sql` use:

| Symptom | Cause | Fix |
|---|---|---|
| `active_products`: `complex joins are only supported when all distributed tables are co-located and joined on their distribution columns` | `LEFT JOIN inventory i ON i.product_id = p.id` | add `i.seller_id = p.seller_id` to the join |
| `seller_sales_daily` matview: the same error | subquery groups `order_items` by `order_id` only | group and join by `(seller_id, order_id)` |
| `recursive CTEs are only supported when they contain a filter on the distribution column` | `WITH RECURSIVE` over `categories` joined to distributed tables | join a fixed number of levels (`LEFT JOIN categories c2 ON c2.id = c1.parent_id ...`), or compute the tree in a `MATERIALIZED` CTE that is recursively planned first |
| `correlated subqueries are not supported when the FROM clause contains a reference table` | `SELECT s.shop_name, (SELECT count(*) FROM products p WHERE p.seller_id = s.id) FROM sellers s`, and `EXISTS (...)` per user | rewrite as a join with `GROUP BY`, or a non-correlated `IN (SELECT ...)` |
| `modifying the partition value of rows is not allowed` | `UPDATE orders SET seller_id = ...` | a row cannot change shards; delete and re-insert |
| `could not run distributed query with FOR UPDATE/SHARE commands` | `place_order()` locks inventory by `product_id` only | section 15 |

Notice the pattern: in each case, the query already *meant* "within one seller", but it didn't *say* so in a way the planner can prove. On a single node the extra `seller_id = seller_id` is redundant; on Citus it is the difference between a local join and an error.

### 14.6 Measuring: the seller dashboard and the global report

`lab/scripts/queries.sql` holds four representative queries, written shard-aware: Q1 and Q2 are the seller dashboard (monthly revenue; top products of the quarter, a three-table join), Q3 is a global report (2025 revenue per root category: all orders, all items, all products) and Q4 is the top five sellers of 2025 with their shop names. We ran them three times each, on the large dataset, first on a single PostgreSQL 17 node (the same data after 2101, before distribution) and then on the Citus cluster with two workers. Both results are identical row for row.

| Query | Single node | Citus, 2 workers |
|---|---|---|
| Q1 dashboard, seller 42 (1,402 orders) | 5–12 ms | 17–23 ms |
| Q2 top products, seller 42 | 7–11 ms | 8–13 ms |
| Q1 dashboard, seller 1 (69,805 orders) | 40–56 ms | 36–52 ms |
| Q2 top products, seller 1 | 146–199 ms | 77–118 ms |
| Q3 revenue per root category (all sellers) | 0.87–1.02 s | 1.83–2.42 s |
| Q4 top 5 sellers with shop names | 58–92 ms | 145–200 ms |

Read this table honestly. All four containers share **one 4-vCPU machine**, so the "cluster" has no more CPU, memory or disk than the single node; it only adds network hops, planning and coordination. Router queries cost a few milliseconds of extra overhead (Q1 for a small seller). Queries over a large seller can even get faster, because the seller's data sits in smaller tables and indexes (Q2 for seller 1). Global reports got about twice as slow, because 32 tasks compete for the same four cores and every partial result passes through the coordinator. On real hardware, each worker brings its own cores, RAM and disks, and the global reports scale out: that is the point of the exercise. Sharding is not an optimisation for one machine. It is a way to use *more* machines.

> **In production:** measure on a cluster sized like production, with production-like concurrency. A distributed database's single-query latency is usually slightly worse than a single node's. What improves is throughput under load and the ceiling on data size.

---

## 15. Writes and distributed transactions in Citus

### 15.1 Checkout: what happens to `place_order()`

`place_order()` from Module 10 is the heart of Bazaar. On the distributed schema it fails immediately:

```sql
SELECT * FROM place_order(8, '[{"product_id": 1, "quantity": 1}]');
```

```text
ERROR:  could not run distributed query with FOR UPDATE/SHARE commands
HINT:  Consider using an equality filter on the distributed table's partition column.
CONTEXT:  SQL statement "SELECT i.product_id, i.quantity, i.reserved, q.quantity AS wanted, p.status
        FROM inventory i
        JOIN unnest(v_pids, v_qtys) AS q (product_id, quantity) ON q.product_id = i.product_id
        JOIN products p ON p.id = i.product_id
        ORDER BY i.product_id
        FOR UPDATE OF i"
```

Its stock-locking query names inventory rows by `product_id` only. Citus would have to lock rows on every shard, and it doesn't support `FOR UPDATE` in multi-shard queries. Even past that point, the function would fail twice more: it inserts `order_items` without `seller_id` (the fill trigger is gone, so `NOT NULL` fails), and it relies on the `orders.total` trigger, which is gone too.

`lab/scripts/citus-adapt.sql` adds **`place_order_sharded()`**, which keeps the original contract (same arguments, same `SETOF bigint` result with one order per seller, same SQLSTATEs `BZ001`–`BZ004` and `22023`) and changes four things:

1. **Items carry the shard key**: `[{"seller_id": 1, "product_id": 1, "quantity": 2}, ...]`. The cart UI knows each product's seller. Lines without `seller_id` still work: the function looks them up with one `SELECT ... FROM products WHERE id = ANY (...)`, a multi-shard query (32 tasks), the expensive path.
2. **It works one seller at a time, in `seller_id` order.** For each seller, the lock query filters on `i.seller_id = v_seller`, so it is a router query, and Citus allows `FOR UPDATE` there:

   ```sql
   FOR r IN
       SELECT i.product_id, i.quantity, i.reserved, q.quantity AS wanted, p.status
       FROM inventory i
       JOIN products p ON p.seller_id = i.seller_id AND p.id = i.product_id
       JOIN unnest(v_pids, v_qtys) AS q (product_id, quantity) ON q.product_id = i.product_id
       WHERE i.seller_id = v_seller
       ORDER BY i.product_id
       FOR UPDATE OF i
   LOOP
   ```

   Locks are taken in `(seller_id, product_id)` order across the whole checkout, the distributed version of Module 10's deadlock-avoidance rule.
3. **It computes `orders.total`** from the same rows it inserts, then inserts the order with `INSERT ... VALUES`, which Citus routes straight to the shard. (Our first version used `INSERT ... SELECT ... FROM unnest(...)`: Citus evaluated the `SELECT` on the coordinator and shipped the row as an intermediate result first. It works, but costs an extra round trip.)
4. **It inserts `order_items` one `INSERT ... VALUES` per line, with `seller_id`.** The original single `INSERT ... SELECT` worked when we tested as `postgres`. Run as `bazaar_app` (the role the application uses, subject to Module 15's row-level security), it failed: Citus evaluated the `SELECT` on the coordinator and sent the rows to the shard with `COPY`, and the worker refused with `COPY FROM not supported with row-level security`. A few router inserts per order avoid both the coordinator detour and the `COPY`.

The `RETURN NEXT` loop and the `SETOF` result work unchanged: the function itself runs on the coordinator, and every statement inside it is planned by Citus like any other query. All the error cases behave as before (`insufficient stock for product 3`, `product 5 is not available for sale`, `product(s) {999} do not exist`). Under concurrency it stays correct: in section 16.2, four pgbench clients ran 3,710 checkouts of one product of seller 1 in 30 s (124 per second) while that seller's shard was being moved to another node, and afterwards `inventory.reserved` was exactly the sum of the ordered quantities.

### 15.2 Two-phase commit under the hood

A cart with products of sellers 1 and 2 touches shards on both workers. Turn on `citus.log_remote_commands` to see what Citus sends:

```sql
SET citus.log_remote_commands = on;
BEGIN;
SELECT * FROM place_order_sharded(8, '[{"seller_id":1,"product_id":1,"quantity":1},{"seller_id":2,"product_id":24,"quantity":2}]');
COMMIT;
```

```text
NOTICE:  issuing BEGIN TRANSACTION ISOLATION LEVEL READ COMMITTED;SELECT assign_distributed_transaction_id(0, 269, '2026-10-14 09:35:06.861406+00');
DETAIL:  on server postgres@m21-worker2:5432 connectionId: 1
NOTICE:  issuing SELECT i.product_id, i.quantity, i.reserved, ... FROM ((bazaar.inventory_102045 i JOIN bazaar.products_102013 p ... FOR UPDATE OF i
...
NOTICE:  issuing INSERT INTO bazaar.orders_102077 (id, user_id, seller_id, status, total, ...) VALUES ('123'::bigint, '8'::bigint, '1'::bigint, 'pending'::bazaar.order_status, 999.00::numeric(12,2), ...) RETURNING id
NOTICE:  issuing INSERT INTO bazaar.order_items_102109 (order_id, product_id, quantity, unit_price, seller_id) VALUES ('123'::bigint, '1'::bigint, 1, 999.00::numeric(12,2), '1'::bigint)
NOTICE:  issuing BEGIN TRANSACTION ISOLATION LEVEL READ COMMITTED;SELECT assign_distributed_transaction_id(0, 269, '2026-10-14 09:35:06.861406+00');
DETAIL:  on server postgres@m21-worker1:5432 connectionId: 2
...
NOTICE:  issuing INSERT INTO bazaar.orders_102100 (...) VALUES ('124'::bigint, '8'::bigint, '2'::bigint, 'pending'::bazaar.order_status, 23.98::numeric(12,2), ...) RETURNING id
NOTICE:  issuing INSERT INTO bazaar.order_items_102132 (...) VALUES ('124'::bigint, '24'::bigint, 2, 11.99::numeric(12,2), '2'::bigint)
NOTICE:  issuing PREPARE TRANSACTION 'citus_0_8288_269_0'
DETAIL:  on server postgres@m21-worker2:5432 connectionId: 1
NOTICE:  issuing PREPARE TRANSACTION 'citus_0_8288_269_1'
DETAIL:  on server postgres@m21-worker1:5432 connectionId: 2
NOTICE:  issuing COMMIT PREPARED 'citus_0_8288_269_0'
DETAIL:  on server postgres@m21-worker2:5432 connectionId: 1
NOTICE:  issuing COMMIT PREPARED 'citus_0_8288_269_1'
DETAIL:  on server postgres@m21-worker1:5432 connectionId: 2
COMMIT
```

This is section 9's protocol, run by Citus:

- The id `123` was generated on the coordinator and sent as a literal: that is section 7's sequence handling.
- Each worker transaction is tagged with a **distributed transaction id** (`assign_distributed_transaction_id`). Citus uses it for **distributed deadlock detection**: the maintenance daemon builds a cluster-wide wait-for graph from all nodes and cancels a transaction in a cycle, which no single node could detect.
- At `COMMIT`, Citus prepares on every worker that did writes, then **records the commit decision in `pg_dist_transaction`** in the coordinator's local commit (one row per participant, with the `gid`), and only then sends `COMMIT PREPARED`.
- A transaction that wrote to a single worker (a one-seller checkout) skips 2PC entirely: one ordinary `COMMIT`.

If the coordinator crashes after preparing, or a worker is unreachable during phase 2, prepared transactions are left behind on the workers. Citus's maintenance daemon runs `recover_prepared_transactions()` regularly (every `citus.recover_2pc_interval`, 1 minute by default). It compares each worker's `pg_prepared_xacts` with `pg_dist_transaction`: a prepared transaction *with* a commit record is committed, one *without* is rolled back. To see it work, prepare a transaction on `m21-worker1` with a Citus-style gid that has no commit record:

```sql
-- on m21-worker1
CREATE TABLE public.scratch (id int);
BEGIN;
INSERT INTO public.scratch VALUES (1);
PREPARE TRANSACTION 'citus_0_424242_7_0';
```

```sql
-- on m21-coord
SELECT recover_prepared_transactions();
```

```text
 recover_prepared_transactions
-------------------------------
                             1
```

`pg_prepared_xacts` on the worker is empty again and `public.scratch` has no rows: rolled back, because the coordinator never decided to commit.

> **Pitfall:** Citus's 2PC makes multi-shard writes **atomic**, but not **isolated** across nodes. Each worker runs its own MVCC, and there is no cluster-wide snapshot: a multi-shard `SELECT` running during the checkout above can see seller 1's new order on worker 2 and not yet seller 2's on worker 1. For Bazaar's dashboards that is acceptable. For a report that must balance to the cent, either read from one consistent point (a restore point, a replica stopped at the same LSN) or design the data so the invariant lives inside one shard.

### 15.3 Delegating a whole function to a worker

`place_order_sharded()` runs on the coordinator and sends every statement to a worker separately: a dozen or so round trips per seller. For functions that work on a **single** tenant, Citus can ship the *call itself* to the worker that holds the tenant:

```sql
CREATE FUNCTION seller_open_orders(p_seller_id bigint)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
AS $$
BEGIN
    RETURN (SELECT jsonb_object_agg(status, n)
            FROM (SELECT o.status, count(*) AS n
                  FROM orders o
                  WHERE o.seller_id = p_seller_id AND o.status IN ('pending', 'paid', 'shipped')
                  GROUP BY o.status) s);
END;
$$;

SELECT create_distributed_function('seller_open_orders(bigint)', 'p_seller_id', colocate_with => 'orders');

SET citus.log_remote_commands = on;
SELECT seller_open_orders(42);
```

```text
NOTICE:  issuing SELECT bazaar.seller_open_orders('42'::bigint) AS seller_open_orders
DETAIL:  on server postgres@m21-worker2:5432 connectionId: 2
```

One round trip: the whole function runs on `m21-worker2`, next to seller 42's shards. Delegation applies to calls of the form `SELECT f(...)` (or `CALL p(...)` for procedures) whose distribution argument is a constant. When we tried the same body as a set-returning **SQL** function called as `SELECT * FROM f(42)`, Citus refused (`parameterized queries for SQL functions referencing distributed tables are not supported`) and recommended PL/pgSQL. A per-seller variant of the checkout (`place_seller_order(seller_id, items)`) would be a natural candidate.

---

## 16. Growing the cluster: rebalancing and tenant isolation

### 16.1 Adding a worker and rebalancing

The large cluster had 128 seller shards on each of two workers (8 co-located tables × 16 shards). Adding a node takes three commands:

```bash
docker compose --profile scale up -d worker3
./scripts/citus-init-nodes.sh m21-worker3      # database + extension, then citus_add_node()
```

```sql
SELECT * FROM citus_rebalance_start();
```

```text
NOTICE:  Scheduled 18 moves as job 1
DETAIL:  Rebalance scheduled as background job
HINT:  To monitor progress, run: SELECT * FROM citus_rebalance_status();
```

A new node gets no shards by itself: `citus_add_node()` only registers it (reference tables are copied to it when the first shard arrives). `get_rebalance_table_shards_plan()` shows what the rebalancer would do without doing it. `citus_rebalance_start()` schedules the moves as **background tasks** and returns immediately. Each move handles a whole **shard group** (shard *n* of all eight co-located tables), so co-location is never broken. `citus_rebalance_status()` reports progress per move, including the phase and the replication lag:

```text
{"LSN": {"lag": "7984 bytes", "source": "0/443388F8", "target": "0/443369C8"},
 "size": {"source": "61 MB", "target": "51 MB"}, "hosts": {"source": "m21-worker1:5432", "target": "m21-worker3:5432"},
 "phase": "Creating Constraints", "state": "running",
 "command": "SELECT pg_catalog.citus_move_shard_placement(102018,2,4,'auto')", ...}
```

A move is **online** when `wal_level = logical` (the lab sets it): Citus creates the shard on the target, copies the data, streams the changes made in the meantime through **logical replication**, briefly blocks writes to that shard to catch up the last changes, switches the metadata, and drops the old copy later. Writes to the shard continue during the copy. While the rebalancer ran, four pgbench clients kept checking out one product of seller 42 without a single failed transaction, until all 299 units were sold (exactly 299 orders, `reserved` = 299). All 18 moves (8 seller shard groups and 10 `cart_items` shards) took **38 seconds**:

```text
  nodename   | seller_shards | cart_shards
-------------+---------------+-------------
 m21-worker1 |            96 |          11
 m21-worker2 |            96 |          11
 m21-worker3 |            64 |          10
```

The default strategy, `by_disk_size`, balances bytes rather than shard counts, within a 10% threshold. Its result here was 268 MB, 207 MB and 380 MB of distributed data per worker: worker 3 received the four largest shard groups, including seller 1's 103 MB group (the average group is 27 MB). Shard groups are lumpy, and a single huge tenant can make any bin-packing imperfect. That is the next problem to solve.

### 16.2 Isolating a hot tenant

`isolate_tenant_to_new_shard` splits a shard so that one distribution value gets a shard of its own, for all co-located tables at once (`'CASCADE'`):

```sql
SELECT isolate_tenant_to_new_shard('orders', 1, 'CASCADE', shard_transfer_mode => 'block_writes');
```

```text
 isolate_tenant_to_new_shard
-----------------------------
                      102331
Time: 5215.337 ms (00:05.215)
```

```sql
SELECT shardid, shardminvalue, shardmaxvalue
FROM pg_dist_shard
WHERE shardid = get_shard_id_for_distribution_column('orders', 1);
```

```text
 shardid | shardminvalue | shardmaxvalue
---------+---------------+---------------
  102331 | -1905060026   | -1905060026
```

The old shard's hash range was split into three: the hashes below `hashint8(1)`, exactly `hashint8(1)` (seller 1 alone), and the hashes above. `orders` now has 34 shards. Seller 1's shard group can be moved like any other, for example to a node of its own:

```sql
SELECT citus_move_shard_placement(102331, 'm21-worker3', 5432, 'm21-worker1', 5432,
                                  shard_transfer_mode => 'force_logical');
```

That move took 7.5 s, online, while four pgbench clients placed 3,710 orders for seller 1 (section 15.1).

> **Pitfall:** we first ran the isolation with `shard_transfer_mode => 'force_logical'` (online) while checkouts were updating seller 1's inventory. It never finished: on the target worker, the logical replication apply worker failed every five seconds with `publisher did not send replica identity column expected by the logical replication target relation "bazaar.inventory_102304"`. We cancelled it after 5 minutes and cleaned up with `CALL citus_cleanup_orphaned_resources()`. It happened again on the small dataset and on a freshly created two-column table, every time rows were updated during the split, with Citus 14.2.0 on PostgreSQL 17.10. Online *moves* worked every time. With `block_writes`, the split blocked writes to seller 1's shard group for about 5 s. Test shard splits under write load on your own version before relying on them, and schedule them for a quiet period.

---

## 17. More Citus features

### 17.1 Schema-based sharding

Row-based sharding ("every table has `seller_id`") is the model of this module. Since Citus 12 there is a second model for applications that use **one schema per tenant**: each schema becomes a unit of distribution, and all its tables live together on one node, with no distribution column and no schema changes.

```sql
SET citus.enable_schema_based_sharding = on;
CREATE SCHEMA shop_acme;
CREATE TABLE shop_acme.products (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                                 title text NOT NULL, price numeric(12,2) NOT NULL);
CREATE TABLE shop_acme.orders (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                               product_id bigint NOT NULL REFERENCES shop_acme.products (id),
                               user_id bigint NOT NULL REFERENCES bazaar.users (id),
                               qty int NOT NULL);
RESET citus.enable_schema_based_sharding;

SELECT table_name, citus_table_type, colocation_id, shard_count
FROM citus_tables WHERE table_name::text LIKE 'shop_%';
```

```text
     table_name     | citus_table_type | colocation_id | shard_count
--------------------+------------------+---------------+-------------
 shop_acme.orders   | schema           |             4 |           1
 shop_acme.products | schema           |             4 |           1
```

Each schema gets its own co-location group with one shard per table. Joins within the schema and to reference tables (`bazaar.users` here) are router queries on the schema's node, and the rebalancer moves whole schemas. `citus_schema_distribute('name')` converts an existing schema. This model fits applications with few, large, independent tenants whose tables may even differ per tenant. With 2,000 sellers sharing one schema, Bazaar fits the row-based model better: thousands of schemas mean thousands of copies of every table and index, and every migration runs thousands of times.

### 17.2 Columnar storage

The `citus_columnar` extension (installed separately: `CREATE EXTENSION citus_columnar`) adds a `columnar` table access method. It stores data column by column in compressed stripes. It suits append-mostly analytical data such as old orders:

```sql
CREATE TABLE orders_archive_2023 (LIKE orders INCLUDING DEFAULTS) USING columnar;
SELECT create_distributed_table('orders_archive_2023', 'seller_id', colocate_with => 'orders');
INSERT INTO orders_archive_2023 SELECT * FROM orders WHERE created_at < '2024-01-01';
```

The 95,976 orders of 2023 took 4,272 kB as a distributed columnar table and 12 MB as an identical heap table, about a third. Columnar tables have historically had restrictions on `UPDATE`/`DELETE` and indexes, so use them for data you write once and scan often. The typical pattern combines them with Module 14: the current months of a partitioned table in heap, old partitions converted to columnar.

---

## 18. Limitations checklist

What Citus supports, conditionally supports and does not support, as met in this module:

| Feature | On distributed tables |
|---|---|
| PK / UNIQUE / EXCLUDE | only if they include the distribution column (with `=` for EXCLUDE) |
| FK distributed → reference | yes |
| FK distributed → distributed | only between co-located tables, including the distribution column on both sides |
| FK reference → distributed, local → distributed | no |
| Changing the distribution column of a row | no (delete and re-insert) |
| Triggers | only with `citus.enable_unsafe_triggers`. Row triggers that touch only `NEW` are fine. Triggers with transition tables or that touch other tables are not. |
| `FOR UPDATE` | router queries only |
| Joins | co-located on the distribution column, or with reference tables. Others need `enable_repartition_joins` or a rewrite. |
| Correlated subqueries, recursive CTEs | when they can be pushed to shards; otherwise rewrite (section 14.5) |
| Identity and sequences | yes, `bigint` (per-node ranges on workers) |
| Materialized views | as local objects on the coordinator, built by a distributed query |
| Transactions | atomic across shards via 2PC; no cluster-wide snapshot isolation |
| DDL | propagated automatically (`ALTER TABLE`, `CREATE INDEX`, roles, functions, types); `CREATE DATABASE` is not propagated by default |

**Reference tables** have a cost too: every write is a distributed transaction to all nodes, so keep them small and rarely written. And if one is very large (`users` at 100 million rows), a copy on every node multiplies its storage.

This list is also why `2101` exists: almost every row of the table is a reason why Bazaar's original keys could not be distributed as they were.

---

## 19. The operational cost of sharding

Before you shard, add up what you will run every day afterwards:

- **More servers, each needing everything a single server needed.** Every worker is a primary that needs streaming replicas, failover (a Patroni cluster per worker, Module 20), monitoring, OS patching and capacity planning. A 1 + 4-node Citus cluster with one replica each is 10 PostgreSQL servers.
- **Backups and restores across nodes.** A backup of each node taken at a slightly different moment does not restore to a consistent cluster. With Citus, take continuous WAL archiving (Module 18) on every node and create cluster-wide restore points with `citus_create_restore_point('name')`, then recover every node to that same named point. Test it. A restore drill now involves N servers.
- **Schema migrations.** Citus propagates DDL, but a migration now locks and rewrites shards on every node, and the "2101-style" rules apply to every new table and constraint. Migration tools (Module 26) must know which tables to distribute.
- **Connections.** Each client connection to the coordinator can open several connections to each worker for multi-shard queries. Put PgBouncer in front of the coordinator, and size `citus.max_shared_pool_size` and the workers' `max_connections` together.
- **Authentication.** Nodes connect to each other as the user running the query: every role needs to log in on every node, with credentials in `.pgpass` or `pg_dist_authinfo`. The lab's `trust` setting is for the lab only.
- **Observability per node and per shard.** `pg_stat_statements` on each node, `citus_stat_tenants`, `citus_shards` sizes, `citus_rebalance_status()`, orphaned resources (`pg_dist_cleanup`), and alerts on `pg_prepared_xacts` older than a few minutes.
- **Upgrades.** A Citus upgrade means upgrading the extension on every node in the right order, and PostgreSQL major upgrades cover the whole cluster.
- **People.** Developers must learn the shard-key rules (section 14.5) and review every query for them. Support engineers need to know which node holds which tenant.

None of this is a reason not to shard when the numbers demand it. It is the reason section 1 asks you to exhaust the alternatives first.

---

## Summary

- Shard only when write throughput, working set or data size of one logical database will outgrow the largest machine you are willing to run, after scaling up, read replicas, partitioning, archiving and caching. Most applications never get there.
- The **shard key** decides everything: high cardinality, even load, **query locality** (hot-path queries carry the key) and **co-location** of related tables. For Bazaar, `seller_id` makes checkout, fulfilment and the seller dashboard single-shard, and buyer history a scatter-gather.
- Skew is about load, not key count. Seller 1 has 14% of all orders, so its shard holds 5.3× the average in 32 shards, and more shards make that ratio *worse*. Isolate and move hot tenants, and watch per-tenant load.
- Use **hash** sharding into many **logical shards** mapped to fewer nodes. Rebalancing then moves whole shards and never re-hashes.
- Per-shard identity columns collide. Use offset sequences, snowflake IDs, UUIDv7 or Citus's per-node ranges, and remember that uniqueness of `id` alone is no longer enforced.
- Cross-shard queries are scatter-gather: push filters, partial aggregates and limits down, and accept that latency is the slowest shard's. Cross-shard transactions need **2PC** (`PREPARE TRANSACTION`, `max_prepared_transactions`), whose orphans hold locks and block VACUUM until resolved, or **sagas** with compensations.
- **Application-level sharding** gives full control and costs engineering on every query path. **`postgres_fdw` + partitioning** routes and prunes, pushes co-located joins and aggregates with partition-wise settings, but has no atomic commit, no partial aggregate pushdown, no global constraints and no rebalancing.
- **Citus** distributes tables (`create_distributed_table`), replicates reference tables, co-locates related tables, runs router and multi-shard queries, commits multi-node writes with 2PC and recovers them automatically, and rebalances online. It requires shard-key-aware keys (hence `2101`), shard-key-aware joins, and adapted triggers and functions (hence `place_order_sharded()`).
- Measured on one 4-vCPU host, Citus added a few ms to router queries and made global reports about twice as slow. Sharding buys capacity on *more* hardware, not speed on the same hardware.

## What's next

You have scaled Bazaar's database as far as this course goes: indexed, partitioned, secured, replicated, highly available and sharded. [Module 22: psycopg 3](../22-psycopg/) starts Part IV, where you build the Python service on top of it. The ordinary, unsharded schema comes back (`reset.sh 22` never applies `2101`), but the habit of passing the tenant's key explicitly in every query will serve you there too.
