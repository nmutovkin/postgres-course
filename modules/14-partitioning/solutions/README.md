# Module 14: Solutions

All solutions were run on PostgreSQL 17 against `./project/bin/reset.sh 15` (the large dataset with the partitioned `events` table), with [`partition_maintenance.sql`](partition_maintenance.sql) applied where noted. Timings come from a 4-vCPU container with a warm cache; yours will differ, the counts won't. "Today" for `now()` and `current_date` was 2026-10-14 when these ran.

Runnable files in this directory:

| File | What |
|---|---|
| [`partition_maintenance.sql`](partition_maintenance.sql) | Milestone part 2: `partition_bounds`, `create_monthly_partition`, `maintain_monthly_partitions` |
| [`audit_log_seed.sql`](audit_log_seed.sql) | Milestone part 3: 1.5 million audit rows |
| [`audit_log_migration.sql`](audit_log_migration.sql) | Milestone part 3: the live migration |
| [`rescue_default_rows.sql`](rescue_default_rows.sql) | Exercise 7 |
| [`event_by_id.sql`](event_by_id.sql) | Exercise 10 |
| [`merge_quarter.sql`](merge_quarter.sql) | Exercise 11 |

---

## Exercise 1 ★: Pruning puzzles

| # | `WHERE` | Pruning | Partitions read |
|---|---|---|---|
| 1 | `occurred_at >= '2025-06-15' AND occurred_at < '2025-07-15'` | plan time | 2 (June, July) |
| 2 | `occurred_at = '2025-06-15 12:00:00+00'` | plan time | 1 |
| 3 | `occurred_at IN ('2025-03-01 10:00+00', '2025-09-01 10:00+00')` | plan time | 2 |
| 4 | `occurred_at >= '2025-06-01' AND product_id = 42` | plan time | 20 |
| 5 | `occurred_at > current_date - 30` | execution time | 5 here (`Subplans Removed: 20`) |
| 6 | `occurred_at >= '2025-06-15'::timestamp AND occurred_at < '2025-06-16'::timestamp` | execution time | 1 (`Subplans Removed: 24`) |
| 7 | `occurred_at AT TIME ZONE 'Europe/Berlin' >= '2025-06-01'` | none | 25 |
| 8 | `date_trunc('day', occurred_at) = '2025-06-15'` | none | 25 |

The reasoning:

- **(1)–(3)**: the bare key compared with constants by immutable operators. Untyped literals like `'2025-06-15'` become `timestamptz` constants (interpreted once, at parse time, in the session's time zone), so the comparison is `timestamptz` against `timestamptz`. `IN` is an `= ANY(array)`, and the planner prunes each element.
- **(4)**: the condition on `product_id` doesn't matter for pruning; `>= '2025-06-01'` has no upper end, so June to December 2025 (7), all twelve 2026 partitions, and the default partition (which could hold anything) stay: 20. A forgotten upper bound is a common reason for "pruning doesn't work".
- **(5)**: `current_date` is stable (it changes at midnight), so the planner can't prune; the executor does at startup. Which partitions remain depends on your clock: with 2026-10-14 they were September to December 2026 and the default.
- **(6)**: comparing `timestamptz` with `timestamp` (without time zone) converts using the session's `TimeZone`, so the operator is only stable: execution-time pruning. The query is right, but the plan carries all 25 partitions.
- **(7), (8)**: the key is wrapped in an expression. `AT TIME ZONE` turns it into a `timestamp`; `date_trunc` into another value. The planner can't map either back to the partition bounds.

Verify any of them with:

```sql
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF)
SELECT count(*) FROM events
WHERE occurred_at >= '2025-06-15'::timestamp AND occurred_at < '2025-06-16'::timestamp;
```

and look for the partition names, `Subplans Removed`, and `(never executed)`.

---

## Exercise 2 ★: Where are the rows?

```sql
WITH counts AS (
    SELECT tableoid AS oid, count(*) AS n
    FROM events
    GROUP BY tableoid
)
SELECT t.relid AS partition,
       pg_get_expr(c.relpartbound, c.oid) AS bounds,
       coalesce(n.n, 0) AS rows,
       pg_size_pretty(pg_total_relation_size(t.relid)) AS size,
       round(100.0 * coalesce(n.n, 0) / sum(coalesce(n.n, 0)) OVER (), 1) AS pct
FROM pg_partition_tree('events') t
JOIN pg_class c ON c.oid = t.relid
LEFT JOIN counts n ON n.oid = t.relid
WHERE t.isleaf
ORDER BY rows DESC, t.relid::text;
```

```text
    partition    |                                  bounds                                  |  rows  |  size  | pct  
-----------------+--------------------------------------------------------------------------+--------+--------+------
 events_y2025m12 | FOR VALUES FROM ('2025-12-01 00:00:00+00') TO ('2026-01-01 00:00:00+00') | 671104 | 115 MB | 13.5
 events_y2025m11 | FOR VALUES FROM ('2025-11-01 00:00:00+00') TO ('2025-12-01 00:00:00+00') | 509415 | 88 MB  | 10.2
 events_y2025m10 | FOR VALUES FROM ('2025-10-01 00:00:00+00') TO ('2025-11-01 00:00:00+00') | 441764 | 76 MB  |  8.9
 events_y2025m08 | FOR VALUES FROM ('2025-08-01 00:00:00+00') TO ('2025-09-01 00:00:00+00') | 426099 | 73 MB  |  8.5
 events_y2025m09 | FOR VALUES FROM ('2025-09-01 00:00:00+00') TO ('2025-10-01 00:00:00+00') | 419575 | 72 MB  |  8.4
 events_y2025m07 | FOR VALUES FROM ('2025-07-01 00:00:00+00') TO ('2025-08-01 00:00:00+00') | 404757 | 70 MB  |  8.1
 events_y2025m06 | FOR VALUES FROM ('2025-06-01 00:00:00+00') TO ('2025-07-01 00:00:00+00') | 381151 | 66 MB  |  7.6
 events_y2025m05 | FOR VALUES FROM ('2025-05-01 00:00:00+00') TO ('2025-06-01 00:00:00+00') | 380180 | 66 MB  |  7.6
 events_y2025m04 | FOR VALUES FROM ('2025-04-01 00:00:00+00') TO ('2025-05-01 00:00:00+00') | 359738 | 62 MB  |  7.2
 events_y2025m03 | FOR VALUES FROM ('2025-03-01 00:00:00+00') TO ('2025-04-01 00:00:00+00') | 356053 | 62 MB  |  7.1
 events_y2025m01 | FOR VALUES FROM ('2025-01-01 00:00:00+00') TO ('2025-02-01 00:00:00+00') | 326131 | 57 MB  |  6.5
 events_y2025m02 | FOR VALUES FROM ('2025-02-01 00:00:00+00') TO ('2025-03-01 00:00:00+00') | 307750 | 54 MB  |  6.2
 events_y2026m01 | FOR VALUES FROM ('2026-01-01 00:00:00+00') TO ('2026-02-01 00:00:00+00') |      8 | 96 kB  |  0.0
 events_default  | DEFAULT                                                                  |      0 | 64 kB  |  0.0
 events_y2026m02 | FOR VALUES FROM ('2026-02-01 00:00:00+00') TO ('2026-03-01 00:00:00+00') |      0 | 32 kB  |  0.0
 ...
 events_y2026m12 | FOR VALUES FROM ('2026-12-01 00:00:00+00') TO ('2027-01-01 00:00:00+00') |      0 | 32 kB  |  0.0
(25 rows)
```

(`events_default` shows 64 kB here because rows were inserted into it and removed again while testing; on a fresh reset it is 32 kB, like the other empty partitions. An "empty" partition isn't 0 bytes: each of its three indexes has a metapage and a root page.)

Points:

- **`tableoid` gives exact counts per partition in one scan**; `pg_class.reltuples` would give estimates instantly, which is usually enough for monitoring.
- **The `LEFT JOIN`** keeps empty partitions, which a `GROUP BY tableoid` alone can't produce.
- **`WHERE t.isleaf`** removes the parent (and, with sub-partitioning, intermediate levels, which have no storage either).
- The growth through the year (54 MB in February, 115 MB in December) is worth a look before choosing a granularity: at this rate, monthly partitions stay well below a gigabyte for years.

---

## Exercise 3 ★: The BETWEEN bug

```sql
SELECT count(*) FROM events WHERE occurred_at BETWEEN '2025-11-01' AND '2025-11-30';           -- 479370
SELECT count(*) FROM events WHERE occurred_at >= '2025-11-01' AND occurred_at < '2025-12-01';   -- 509415
SELECT count(*) FROM events WHERE occurred_at >= '2025-11-30' AND occurred_at < '2025-12-01';   -- 30045
SELECT count(*) FROM events WHERE occurred_at = '2025-11-30';                                   -- 0
```

`BETWEEN a AND b` means `>= a AND <= b`, and `'2025-11-30'` is the instant 2025-11-30 00:00:00. So `BETWEEN` includes that one instant of November 30 (no event happened exactly then) and excludes the rest of the day: 30,045 events, 479,370 + 30,045 = 509,415.

It prunes perfectly, which is what makes the bug dangerous: the plan looks ideal.

```text
               ->  Parallel Seq Scan on events_y2025m11 events
                     Filter: ((occurred_at >= '2025-11-01 00:00:00+00'::timestamp with time zone) AND (occurred_at <= '2025-11-30 00:00:00+00'::timestamp with time zone))
```

The fix is the half-open range, the same shape as the partition bounds: `occurred_at >= '2025-11-01' AND occurred_at < '2025-12-01'`. "Fixing" it with `BETWEEN '2025-11-01' AND '2025-11-30 23:59:59'` still misses events in the last second (timestamps have microseconds), and `BETWEEN ... AND '2025-12-01'` double-counts events at exactly midnight in two consecutive months.

---

## Exercise 4 ★★: The funnel, month by month

```sql
WITH s AS (
    SELECT date_trunc('month', occurred_at, 'UTC')::date AS month,
           session_id,
           bool_or(event_type = 'product_view')     AS viewed,
           bool_or(event_type = 'add_to_cart')      AS carted,
           bool_or(event_type = 'checkout_started') AS checked_out,
           bool_or(event_type = 'order_placed')     AS ordered
    FROM events
    WHERE occurred_at >= '2025-01-01' AND occurred_at < '2026-01-01'
    GROUP BY 1, 2
)
SELECT month,
       count(*) FILTER (WHERE viewed)      AS viewed,
       count(*) FILTER (WHERE carted)      AS carted,
       count(*) FILTER (WHERE checked_out) AS checked_out,
       count(*) FILTER (WHERE ordered)     AS ordered,
       round(100.0 * count(*) FILTER (WHERE ordered) / count(*) FILTER (WHERE viewed), 2) AS conversion_pct
FROM s
GROUP BY month
ORDER BY month;
```

```text
   month    | viewed | carted | checked_out | ordered | conversion_pct 
------------+--------+--------+-------------+---------+----------------
 2025-01-01 |  50190 |  13979 |        7610 |    5674 |          11.31
 2025-02-01 |  46995 |  13201 |        7238 |    5500 |          11.70
 2025-03-01 |  54547 |  15279 |        8490 |    6352 |          11.65
 2025-04-01 |  55110 |  15516 |        8611 |    6450 |          11.70
 2025-05-01 |  58499 |  16206 |        8926 |    6744 |          11.53
 2025-06-01 |  58574 |  16253 |        8889 |    6617 |          11.30
 2025-07-01 |  62163 |  17312 |        9443 |    6964 |          11.20
 2025-08-01 |  65446 |  18316 |       10039 |    7553 |          11.54
 2025-09-01 |  64324 |  18035 |        9941 |    7409 |          11.52
 2025-10-01 |  68029 |  19016 |       10481 |    7791 |          11.45
 2025-11-01 |  78378 |  21848 |       12039 |    9069 |          11.57
 2025-12-01 | 103009 |  28837 |       16009 |   12014 |          11.66
(12 rows)
```

About 6 seconds: the work is grouping 4.98 million rows into 850,066 (month, session) groups, which pruning can't avoid. The filter uses the bare column, so `EXPLAIN` shows the twelve 2025 partitions and nothing else; the `date_trunc` in the `SELECT` and `GROUP BY` is harmless, because pruning only looks at `WHERE`.

The three-argument `date_trunc(field, timestamptz, zone)` (PostgreSQL 12+) truncates in UTC whatever the session's `TimeZone`, so the months match the partitions. With the two-argument form, a session in `America/New_York` would move five hours of events across each month boundary.

**Alternatives:** with `enable_partitionwise_aggregate = on` the inner grouping is done per partition (partially, since the key isn't a grouping column), which reduces the size of each hash table. For a dashboard, compute the funnel once per day into a summary table (`funnel_daily(day, sessions_viewed, ...)`) and keep this query for ad-hoc analysis.

---

## Exercise 5 ★★: LIST partitioning by event type

```sql
CREATE TABLE lab.events_by_type (LIKE events INCLUDING DEFAULTS INCLUDING CONSTRAINTS)
    PARTITION BY LIST (event_type);
CREATE TABLE lab.events_views  PARTITION OF lab.events_by_type
    FOR VALUES IN ('page_view', 'product_view');
CREATE TABLE lab.events_funnel PARTITION OF lab.events_by_type
    FOR VALUES IN ('add_to_cart', 'remove_from_cart', 'checkout_started', 'order_placed');
CREATE TABLE lab.events_search PARTITION OF lab.events_by_type
    FOR VALUES IN ('search');

INSERT INTO lab.events_by_type
SELECT * FROM events WHERE occurred_at >= '2025-11-01' AND occurred_at < '2025-12-01';   -- 509,415 rows
ANALYZE lab.events_by_type;

SELECT tableoid::regclass, count(*) FROM lab.events_by_type GROUP BY 1 ORDER BY 1;
```

```text
     tableoid      | count  
-------------------+--------
 lab.events_views  | 403175
 lab.events_funnel |  47272
 lab.events_search |  58968
(3 rows)
```

(`LIKE` without `INCLUDING IDENTITY` copies `id` as a plain `bigint`, which is what we want for a copy; the `CHECK` constraint on `event_type` comes along.)

**2. Pruning.** `EXPLAIN (COSTS OFF) SELECT count(*) FROM lab.events_by_type WHERE ...`:

- `event_type = 'order_placed'`: `Seq Scan on events_funnel` only.
- `event_type LIKE 'order%'`: all three partitions. `LIKE` is not an operator the pruning code understands, even with a fixed prefix.
- `event_type <> 'search'`: `events_views` and `events_funnel`. LIST pruning can use `<>`: a partition whose *only* value is the excluded one can't match.
- `event_type IN ('search', 'add_to_cart')`: `events_search` and `events_funnel`.

**3. Partitionwise aggregate.**

```sql
SET enable_partitionwise_aggregate = on;
EXPLAIN (COSTS OFF)
SELECT event_type, count(DISTINCT session_id) FROM lab.events_by_type GROUP BY event_type;
```

```text
                                       QUERY PLAN                                       
----------------------------------------------------------------------------------------
 Gather
   Workers Planned: 2
   ->  Parallel Append
         ->  GroupAggregate
               Group Key: events_by_type_1.event_type
               ->  Sort
                     Sort Key: events_by_type_1.event_type, events_by_type_1.session_id
                     ->  Seq Scan on events_views events_by_type_1
         ->  GroupAggregate
               Group Key: events_by_type_2.event_type
               ->  Sort
                     Sort Key: events_by_type_2.event_type, events_by_type_2.session_id
                     ->  Seq Scan on events_search events_by_type_2
         ->  GroupAggregate
               Group Key: events_by_type.event_type
               ->  Sort
                     Sort Key: events_by_type.event_type, events_by_type.session_id
                     ->  Seq Scan on events_funnel events_by_type
(18 rows)
```

Here the grouping column *is* the partition key, so no group can span two partitions. Each partition computes its groups completely (even `count(DISTINCT ...)`, which can't be combined from partial results), and the `Append` just concatenates them: no `Finalize` step. In the theory's example, grouping `events` by `session_id` didn't include the key, so each partition could only produce partial counts. The results:

```text
    event_type    | count 
------------------+-------
 add_to_cart      | 21848
 checkout_started | 12039
 order_placed     |  9069
 page_view        | 86967
 product_view     | 78378
 remove_from_cart |  4316
 search           | 39206
(7 rows)
```

Would you partition `events` like this in production? Only as a second level under time (section 2.4), and only if the event types had different retention periods (for example: keep searches 3 months, orders 3 years). By itself, LIST by type gives no retention benefit.

---

## Exercise 6 ★★: Hash-partitioning cart_items: worth it?

```sql
CREATE TABLE lab.cart_items_h (
    LIKE cart_items INCLUDING DEFAULTS INCLUDING CONSTRAINTS,
    PRIMARY KEY (user_id, product_id)
) PARTITION BY HASH (user_id);

SELECT format('CREATE TABLE lab.cart_items_h_%s PARTITION OF lab.cart_items_h
               FOR VALUES WITH (MODULUS 8, REMAINDER %s)', r, r)
FROM generate_series(0, 7) AS r
\gexec

CREATE INDEX ON lab.cart_items_h (product_id);
INSERT INTO lab.cart_items_h SELECT * FROM cart_items;
ANALYZE lab.cart_items_h;

SELECT tableoid::regclass, count(*), count(DISTINCT user_id) AS users
FROM lab.cart_items_h GROUP BY 1 ORDER BY 1;
```

```text
      tableoid      | count | users 
--------------------+-------+-------
 lab.cart_items_h_0 |  2536 |  1142
 lab.cart_items_h_1 |  2501 |  1113
 lab.cart_items_h_2 |  2337 |  1049
 lab.cart_items_h_3 |  2436 |  1084
 lab.cart_items_h_4 |  2463 |  1070
 lab.cart_items_h_5 |  2530 |  1119
 lab.cart_items_h_6 |  2543 |  1135
 lab.cart_items_h_7 |  2497 |  1129
(8 rows)
```

1. **Spread**: 2,337 to 2,543 rows (±4 % around 2,480), and similarly for users. Hashing spreads keys well; what it can't fix is skew *within* a key (one user with a million rows would still be in one partition).
2. **Pruning**: `user_id = 42` reads only `cart_items_h_2`; `user_id IN (42, 43)` reads `_2` and `_6`; `user_id < 10` reads all 8 (a hash says nothing about order); `product_id = 5` reads all 8, through each partition's `product_id` index.
3. **Size**: `SELECT pg_size_pretty(sum(pg_total_relation_size(relid))) FROM pg_partition_tree('lab.cart_items_h')` gives 2,856 kB, against 2,264 kB for `cart_items`: 26 % more, from eight sets of nearly empty index and heap pages.
4. **Verdict: no.** `cart_items` is 2 MB. Every query on it is already an index lookup by `user_id`, which partitioning doesn't speed up; queries by `product_id` (the FK cascade from products, "how many carts contain X") get eight index probes instead of one; and the table has no lifecycle that partitions could help with. Hash partitioning starts to pay off for tables of hundreds of gigabytes, where VACUUM and index builds on one huge table take too long, or as preparation for sharding by the same key ([Module 21](../21-sharding/)).

---

## Exercise 7 ★★: Rescue the default partition

[`rescue_default_rows.sql`](rescue_default_rows.sql) (needs `create_monthly_partition` from `partition_maintenance.sql`):

```sql
CREATE OR REPLACE FUNCTION rescue_default_rows(p_parent regclass)
RETURNS TABLE (partition_name text, rows_moved bigint)
LANGUAGE plpgsql
AS $$
DECLARE
    v_default  regclass;
    v_key      text;
    v_month    date;
    v_name     text;
    v_moved    bigint;
BEGIN
    SELECT c.oid::regclass INTO v_default
    FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
    WHERE i.inhparent = p_parent AND pg_get_expr(c.relpartbound, c.oid) = 'DEFAULT';
    IF v_default IS NULL THEN
        RAISE EXCEPTION '% has no default partition', p_parent;
    END IF;

    SELECT a.attname INTO v_key
    FROM pg_partitioned_table pt
    JOIN pg_attribute a ON a.attrelid = pt.partrelid AND a.attnum = pt.partattrs[0]
    WHERE pt.partrelid = p_parent AND pt.partstrat = 'r' AND pt.partnatts = 1;
    IF v_key IS NULL THEN
        RAISE EXCEPTION '% is not RANGE-partitioned on a single column', p_parent;
    END IF;

    EXECUTE format('LOCK TABLE %s IN EXCLUSIVE MODE', v_default);

    FOR v_month IN
        EXECUTE format('SELECT DISTINCT date_trunc(''month'', %I, ''UTC'')::date FROM %s ORDER BY 1',
                       v_key, v_default)
    LOOP
        EXECUTE format('CREATE TEMP TABLE rescued ON COMMIT DROP AS
                            WITH d AS (DELETE FROM %s
                                       WHERE %I >= %L AND %I < %L
                                       RETURNING *)
                            SELECT * FROM d',
                       v_default,
                       v_key, v_month::text || ' 00:00:00+00',
                       v_key, (v_month + interval '1 month')::date::text || ' 00:00:00+00');
        GET DIAGNOSTICS v_moved = ROW_COUNT;

        v_name := create_monthly_partition(p_parent, v_month);
        IF v_name IS NULL THEN
            RAISE EXCEPTION 'month % is covered by another partition', v_month;
        END IF;

        EXECUTE format('INSERT INTO %s OVERRIDING SYSTEM VALUE SELECT * FROM rescued', p_parent);
        DROP TABLE rescued;

        partition_name := v_name;
        rows_moved := v_moved;
        RETURN NEXT;
    END LOOP;
END;
$$;
```

The test from the exercise:

```text
 partition_name  | rows_moved 
-----------------+------------
 events_y2027m02 |          2
 events_y2027m03 |          1
 events_y2027m05 |          1
(3 rows)

    tableoid     | count 
-----------------+-------
 events_y2027m02 |     2
 events_y2027m03 |     1
 events_y2027m05 |     1
(3 rows)
```

and `SELECT count(*) FROM events_default` returns 0.

Design points:

- **The order of operations is forced**: the partition can't be created while the default partition holds rows for its range, so the rows leave first (into a temporary table) and come back through the parent, which routes them to the new partition.
- **`OVERRIDING SYSTEM VALUE`** keeps the ids: `id` is `GENERATED ALWAYS`, and without it the insert would fail (or, with `OVERRIDING USER VALUE`, assign new ids and break every reference to the old ones).
- **The lock**: `EXCLUSIVE` on the default partition blocks inserts into it (reads continue) until the transaction ends, so no new row for a month being rescued can slip in between the `DELETE` and the `CREATE TABLE`. Creating each partition also takes `ACCESS EXCLUSIVE` on `events` briefly; for a big rescue, run it with a `lock_timeout` and outside peak hours.
- **Everything is one transaction** (it is a function): either all months are rescued, or none.
- **Generic**: the key column comes from `pg_partitioned_table` (`partattrs` is an `int2vector`, indexed from 0), so the function works for `audit_log` too, as long as the key is a single `timestamptz` column. The `SELECT * FROM rescued` relies on the default partition having the parent's column order, which is true for partitions created with `PARTITION OF`.

---

## Exercise 8 ★★: Archive a month, then bring it back

```sql
CREATE SCHEMA IF NOT EXISTS archive;
ALTER TABLE events DETACH PARTITION events_y2025m01;
ALTER TABLE events_y2025m01 SET SCHEMA archive;
SELECT count(*) FROM events;                           -- 4657594
\d archive.events_y2025m01
```

```text
                       Table "archive.events_y2025m01"
   Column    |           Type           | Collation | Nullable |   Default   
-------------+--------------------------+-----------+----------+-------------
 id          | bigint                   |           | not null | 
 occurred_at | timestamp with time zone |           | not null | 
 ...
Indexes:
    "events_y2025m01_pkey" PRIMARY KEY, btree (id, occurred_at)
    "events_y2025m01_occurred_at_idx" btree (occurred_at)
    "events_y2025m01_product_id_idx" btree (product_id)
Check constraints:
    "events_event_type_check" CHECK (event_type = ANY (ARRAY['page_view'::text, ...]))
```

An ordinary table: no `Partition of:` line, the indexes and the inherited `CHECK` constraint stay, and `id` lost its identity property (a partition uses the parent's identity; a standalone table doesn't get one). Note what is missing: nothing says "only January" any more. The bound was a property of the attachment.

Bring it back without a validation scan under the attach lock:

```sql
SET client_min_messages = debug1;
ALTER TABLE archive.events_y2025m01 ADD CONSTRAINT events_y2025m01_bounds
    CHECK (occurred_at >= '2025-01-01 00:00:00+00' AND occurred_at < '2025-02-01 00:00:00+00') NOT VALID;
ALTER TABLE archive.events_y2025m01 VALIDATE CONSTRAINT events_y2025m01_bounds;
-- DEBUG:  verifying table "events_y2025m01"          (49 ms, no write-blocking lock)
ALTER TABLE events ATTACH PARTITION archive.events_y2025m01
    FOR VALUES FROM ('2025-01-01 00:00:00+00') TO ('2025-02-01 00:00:00+00');
-- DEBUG:  partition constraint for table "events_y2025m01" is implied by existing constraints
-- DEBUG:  verifying table "events_default"            (2.5 ms)
RESET client_min_messages;
ALTER TABLE archive.events_y2025m01 DROP CONSTRAINT events_y2025m01_bounds;
ALTER TABLE archive.events_y2025m01 SET SCHEMA bazaar;
SELECT count(*) FROM events;                           -- 4983725
```

Two `DEBUG` lines after the attach: the new partition's check was skipped thanks to the constraint, but the **default partition** was still verified (that no row in it belongs to January), which is fast only because it is empty. After the attach, `\d events_y2025m01` shows `id ... generated always as identity` again.

A partition can live in a different schema than its parent; moving it back to `bazaar` is only for tidiness. For an archive that must survive a `reset` or move to other storage, `pg_dump -t archive.events_y2025m01` before dropping it.

---

## Exercise 9 ★★: Make old months maintenance-free

```sql
CREATE EXTENSION IF NOT EXISTS pg_visibility;

SELECT c.relname,
       age(c.relfrozenxid)              AS xid_age,
       pg_relation_size(c.oid) / 8192   AS pages,
       v.all_visible,
       v.all_frozen
FROM pg_inherits i
JOIN pg_class c ON c.oid = i.inhrelid,
     pg_visibility_map_summary(c.oid) v
WHERE i.inhparent = 'events'::regclass
  AND c.relname < 'events_y2026'
ORDER BY 1;
```

Before: `all_frozen` is 0 for every partition (1402 ran `VACUUM (ANALYZE)`, which marks pages all-visible but doesn't freeze them). Then:

```sql
SELECT format('VACUUM (FREEZE) %I', c.relname)
FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
WHERE i.inhparent = 'events'::regclass
  AND c.relname BETWEEN 'events_y2025m01' AND 'events_y2025m11'
ORDER BY 1
\gexec
```

Eleven `VACUUM`s of 0.2–0.4 s each. Afterwards:

```text
     relname     | xid_age | pages | all_visible | all_frozen 
-----------------+---------+-------+-------------+------------
 events_default  |    3463 |     1 |           0 |          0
 events_y2025m01 |       0 |  4502 |        4502 |       4502
 events_y2025m02 |       0 |  4247 |        4247 |       4247
 events_y2025m03 |       0 |  4914 |        4914 |       4914
 events_y2025m04 |       0 |  4965 |        4965 |       4965
 events_y2025m05 |       0 |  5246 |        5245 |       5245
 ...
 events_y2025m11 |       0 |  7028 |        7028 |       7028
 events_y2025m12 |    3486 |  9259 |        9259 |          0
(13 rows)
```

An `xid_age` of 0 means that `relfrozenxid` was advanced to the current transaction ID: every row is frozen, so these partitions are immune to wraparound, and autovacuum's anti-wraparound scans can skip every page (the visibility map says all-frozen). (The one page of May that isn't all-visible holds a dead row version from a rolled-back test update; your numbers will show every page frozen.)

The `\gexec` route is necessary because `VACUUM` can't run inside a transaction block, so it can't run in a function or a `DO` block. In production, the monthly maintenance job freezes last month's partition right after the month closes, when it will never change again. Exclude the current month: freezing it is wasted work, because new rows keep arriving and pages keep being modified.

---

## Exercise 10 ★★★: Find an event by id, fast

[`event_by_id.sql`](event_by_id.sql) (needs `partition_bounds` from `partition_maintenance.sql`). The id ranges after `CALL refresh_events_id_ranges();`:

```text
 partition_name  |        from_at         |         to_at          | min_id  | max_id  
-----------------+------------------------+------------------------+---------+---------
 events_y2025m01 | 2025-01-01 00:00:00+00 | 2025-02-01 00:00:00+00 |       1 |  326131
 events_y2025m02 | 2025-02-01 00:00:00+00 | 2025-03-01 00:00:00+00 |  326132 |  633881
 events_y2025m03 | 2025-03-01 00:00:00+00 | 2025-04-01 00:00:00+00 |  633882 |  989934
 events_y2025m04 | 2025-04-01 00:00:00+00 | 2025-05-01 00:00:00+00 |  989935 | 1349672
 ...
 events_y2026m01 | 2026-01-01 00:00:00+00 | 2026-02-01 00:00:00+00 | 4983718 | 4983725
(13 rows)
```

The lookup function:

```sql
CREATE OR REPLACE FUNCTION event_by_id(p_id bigint)
RETURNS SETOF events
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_from timestamptz;
    v_to   timestamptz;
BEGIN
    SELECT min(from_at), max(to_at) INTO v_from, v_to
    FROM events_id_ranges
    WHERE p_id BETWEEN min_id AND max_id;

    IF v_from IS NULL THEN
        RETURN QUERY SELECT * FROM events WHERE id = p_id;    -- slow path
        RETURN;
    END IF;

    RETURN QUERY EXECUTE format(
        'SELECT * FROM events WHERE id = %s AND occurred_at >= %L AND occurred_at < %L',
        p_id, v_from, v_to);
END;
$$;
```

With `LOAD 'auto_explain'; SET auto_explain.log_min_duration = 0; SET auto_explain.log_nested_statements = on; SET client_min_messages = log;`, the call `SELECT * FROM event_by_id(1234567)` logs the inner query's plan:

```text
Query Text: SELECT * FROM events WHERE id = 1234567 AND occurred_at >= '2025-04-01 00:00:00+00' AND occurred_at < '2025-05-01 00:00:00+00'
Index Scan using events_y2025m04_pkey on events_y2025m04 events  (cost=0.42..8.44 rows=1 width=83)
  Index Cond: ((id = 1234567) AND (occurred_at >= '2025-04-01 00:00:00+00'::timestamp with time zone) AND (occurred_at < '2025-05-01 00:00:00+00'::timestamp with time zone))
```

One partition, one index probe that uses both primary-key columns. With `pgbench` (random ids, one client, 10 s): 0.90 ms per lookup with `SELECT * FROM events WHERE id = :id`, 0.26 ms with `SELECT * FROM event_by_id(:id)`.

Why `EXECUTE`: a static `RETURN QUERY SELECT ... WHERE occurred_at >= v_from` is a prepared statement with parameters. PL/pgSQL uses custom plans for its first executions and may then switch to a generic plan, which can only prune at executor startup, after planning all 25 partitions: exactly the cost we want to avoid. Dynamic SQL with the bounds as literals is planned for those values every time, and pruning happens at plan time. (`%s` is safe for `p_id` because it is a `bigint`; the timestamps go through `%L`.)

Why min/max over *all* matching ranges: with concurrent inserts, the ids at the end of one month and the start of the next can interleave, so an id can fall inside two ranges. The function then searches both months, which is still 2 partitions instead of 25.

Maintenance: ids of rows inserted after the last refresh are not in any range and take the slow path. Refresh daily from the maintenance job, or keep the current month's range open-ended (`max_id = 9223372036854775807`) and only fix it when the month closes. **Alternative designs**: put the month into the public id (`2025-04/1234567`), so the client provides the partition key; or switch to time-ordered UUIDs (UUIDv7, built into PostgreSQL 18 as `uuidv7()`), from which the timestamp can be extracted.

---

## Exercise 11 ★★★: Merge three months into a quarter

[`merge_quarter.sql`](merge_quarter.sql). The slow part runs before any lock on `events`:

```sql
CREATE TABLE events_y2025q1 (
    LIKE events INCLUDING DEFAULTS INCLUDING CONSTRAINTS,
    CONSTRAINT events_y2025q1_bounds
        CHECK (occurred_at >= '2025-01-01 00:00:00+00' AND occurred_at < '2025-04-01 00:00:00+00')
);

INSERT INTO events_y2025q1                                         -- 1.3 s, 989,934 rows
SELECT * FROM events
WHERE occurred_at >= '2025-01-01 00:00:00+00' AND occurred_at < '2025-04-01 00:00:00+00'
ORDER BY occurred_at;

SET maintenance_work_mem = '512MB';
ALTER TABLE events_y2025q1 ADD PRIMARY KEY (id, occurred_at);     -- 0.4 s
CREATE INDEX ON events_y2025q1 (occurred_at);                     -- 0.4 s
CREATE INDEX ON events_y2025q1 (product_id);                      -- 0.3 s
RESET maintenance_work_mem;
VACUUM (ANALYZE) events_y2025q1;
```

Then the switch, catalog changes only:

```sql
BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE events DETACH PARTITION events_y2025m01;               -- 1.2 ms
ALTER TABLE events DETACH PARTITION events_y2025m02;               -- 1.0 ms
ALTER TABLE events DETACH PARTITION events_y2025m03;               -- 0.9 ms
ALTER TABLE events ATTACH PARTITION events_y2025q1
    FOR VALUES FROM ('2025-01-01 00:00:00+00') TO ('2025-04-01 00:00:00+00');   -- 1.2 ms
ALTER TABLE events_y2025q1 DROP CONSTRAINT events_y2025q1_bounds;
DROP TABLE events_y2025m01, events_y2025m02, events_y2025m03;      -- 2.6 ms

SELECT count(*) FROM events_y2025q1;    -- 989934
SELECT count(*) FROM events;            -- 4983725
EXPLAIN (COSTS OFF)
SELECT count(*) FROM events WHERE occurred_at >= '2025-02-01' AND occurred_at < '2025-03-01';
ROLLBACK;
DROP TABLE events_y2025q1;              -- after the ROLLBACK only
```

```text
                                                                                  QUERY PLAN                                                                                  
------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
 Finalize Aggregate
   ->  Gather
         Workers Planned: 2
         ->  Partial Aggregate
               ->  Parallel Index Only Scan using events_y2025q1_occurred_at_idx on events_y2025q1 events
                     Index Cond: ((occurred_at >= '2025-02-01 00:00:00+00'::timestamp with time zone) AND (occurred_at < '2025-03-01 00:00:00+00'::timestamp with time zone))
(6 rows)
```

The switch took about 8 ms. Why it is safe and fast:

- **Q1 is closed**: no new rows arrive for January to March, so copying while the months are still attached and in use loses nothing. (For a month that still receives writes, you would need the lock during the copy, or a trigger.)
- **`ATTACH` adopts existing indexes**: the parent has partitioned indexes on `(id, occurred_at)`, `occurred_at` and `product_id`; the new table has matching ones, so `ATTACH` attaches them instead of building new ones under its lock. Without them, the attach would have built three indexes on a million rows inside the critical section (about 1 s, measured in a first version of this solution that did everything in one transaction: 2.5 s of `ACCESS EXCLUSIVE` on `events`).
- **The `CHECK` constraint** spares the validation scan; the empty default partition is still checked, instantly.
- **`LIKE ... INCLUDING CONSTRAINTS`** copies `events_event_type_check` under its own name, which `ATTACH` requires; `LIKE` never copies the identity, which `ATTACH` forbids.
- After the switch, a February query reads the quarter through its `occurred_at` index instead of scanning a month: pruning is coarser, which is the trade-off you accept for old, rarely-queried data.

---

## Exercise 12 ★★★: Funnel by session start

```sql
WITH s AS (
    SELECT session_id,
           min(occurred_at)                         AS started_at,
           bool_or(event_type = 'product_view')     AS viewed,
           bool_or(event_type = 'add_to_cart')      AS carted,
           bool_or(event_type = 'checkout_started') AS checked_out,
           bool_or(event_type = 'order_placed')     AS ordered
    FROM events
    WHERE occurred_at >= '2025-10-31 23:00:00+00'      -- 1 hour before November
      AND occurred_at <  '2025-12-01 01:00:00+00'      -- 1 hour after
    GROUP BY session_id
)
SELECT count(*)                            AS sessions,
       count(*) FILTER (WHERE viewed)      AS viewed,
       count(*) FILTER (WHERE carted)      AS carted,
       count(*) FILTER (WHERE checked_out) AS checked_out,
       count(*) FILTER (WHERE ordered)     AS ordered
FROM s
WHERE started_at >= '2025-11-01 00:00:00+00'
  AND started_at <  '2025-12-01 00:00:00+00';
```

```text
 sessions | viewed | carted | checked_out | ordered 
----------+--------+--------+-------------+---------
    86966 |  78380 |  21849 |       12039 |    9069
(1 row)
```

350 ms, reading `events_y2025m10` (through its index, only the last hour), `events_y2025m11` (sequentially) and `events_y2025m12` (through its index, only the first hour).

The two margins do different jobs:

- **The margin after November** completes the sessions that started in November and continued into December: without it, a session that started at 23:55 on November 30 and added to cart at 00:05 would count as "viewed but not carted". It changed `viewed` from 78,378 to 78,380 and `carted` from 21,848 to 21,849.
- **The margin before November** is needed to *exclude* correctly: a session that started at 23:55 on October 31 has events in November, and without the margin its `min(occurred_at)` would be its first *November* event, so it would be wrongly counted as a November session. The outer `WHERE started_at >= ...` can only throw it out if its October events are visible.

The counts of boundary-crossing sessions (12 at the end of November, 6 at the end of October) come from the same idea:

```sql
SELECT count(*)
FROM (
    SELECT session_id
    FROM events
    WHERE occurred_at >= '2025-11-30 23:00+00' AND occurred_at < '2025-12-01 01:00+00'
    GROUP BY session_id
    HAVING min(occurred_at) < '2025-12-01' AND max(occurred_at) >= '2025-12-01'
) x;     -- 12
```

The margin must be at least the longest session (17 minutes here, from `max(max(occurred_at) - min(occurred_at))` per session). One hour is a safe round number; in production, take it from the session timeout of your analytics definition (often 30 minutes of inactivity).

Write the margins as literal timestamps. `timestamptz '2025-11-01 00:00:00+00' - interval '1 hour'` gives the same answer, but `timestamptz ± interval` is a stable operator (the length of a day depends on the time zone's DST rules), so it prunes only at execution time: `EXPLAIN` then shows `Subplans Removed: 22` instead of a plan with three partitions.
