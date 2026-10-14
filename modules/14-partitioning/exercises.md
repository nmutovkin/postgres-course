# Module 14: Exercises

**Starting state.** Unless an exercise says otherwise:

```bash
./project/bin/reset.sh 15      # large dataset + the partitioned events table (1401, 1402)
./project/bin/psql.sh
```

and `CREATE SCHEMA IF NOT EXISTS lab;` for the throw-away tables. Exercises 7, 10 and the milestone-based ones use the functions of [`solutions/partition_maintenance.sql`](solutions/partition_maintenance.sql) (`partition_bounds`, `create_monthly_partition`): apply it first, or use your own milestone versions.

Several exercises change `events` temporarily. Each one says how to undo its changes (usually by running everything in a transaction that ends with `ROLLBACK`); if in doubt, `reset.sh 15` restores everything. All counts were measured on PostgreSQL 17 with the reference data.

Solutions: [solutions/README.md](solutions/README.md).

---

## Exercise 1 ★: Pruning puzzles

For each `WHERE` clause below, **predict** before running anything: does `SELECT count(*) FROM events WHERE ...` prune at plan time, at execution time, or not at all, and how many partitions does it end up reading? Then check your predictions with `EXPLAIN (ANALYZE, COSTS OFF)`.

1. `occurred_at >= '2025-06-15' AND occurred_at < '2025-07-15'`
2. `occurred_at = '2025-06-15 12:00:00+00'`
3. `occurred_at IN ('2025-03-01 10:00+00', '2025-09-01 10:00+00')`
4. `occurred_at >= '2025-06-01' AND product_id = 42`
5. `occurred_at > current_date - 30`
6. `occurred_at >= '2025-06-15'::timestamp AND occurred_at < '2025-06-16'::timestamp`
7. `occurred_at AT TIME ZONE 'Europe/Berlin' >= '2025-06-01'`
8. `date_trunc('day', occurred_at) = '2025-06-15'`

**Self-check:** at plan time: (1) 2 partitions, (2) 1, (3) 2, (4) 20. At execution time: (5) a handful, depending on your clock (`Subplans Removed`), (6) 1 (`Subplans Removed: 24`). Not at all: (7) and (8), all 25 partitions.

<details><summary>Hint</summary>

Two questions decide it. Is the partition key column compared *bare*, or wrapped in a function or cast? And is the comparison immutable? `timestamptz` compared with `timestamp` or `date`, and anything involving `current_date` or `now()`, depends on the session's time zone or the clock, so it is only stable. For (4), remember that `>=` has no upper end.

</details>

---

## Exercise 2 ★: Where are the rows?

Write one query that lists every partition of `events` with its bounds, its number of rows (exact, not estimated), its total size including indexes, and its share of all rows, biggest first. The parent must not appear, and the empty partitions must.

**Self-check:** 25 rows. The first is `events_y2025m12`: 671,104 rows, 13.5 % of all events, about 115 MB. Then November (509,415, 10.2 %) and October (441,764, 8.9 %). `events_y2026m01` has 8 rows, and the other twelve (February to December 2026, and the default partition) are empty.

<details><summary>Hint</summary>

`pg_partition_tree('events')` with `isleaf`, `pg_get_expr(relpartbound, oid)` from `pg_class` for the bounds, `tableoid` for exact counts (a `LEFT JOIN` keeps the empty partitions), and a window function for the share.

</details>

---

## Exercise 3 ★: The BETWEEN bug

An analyst counts November's events with `WHERE occurred_at BETWEEN '2025-11-01' AND '2025-11-30'`. How many events does that return, how many does November really have, and exactly which events are missing? Write the correct condition. Does the wrong query prune correctly?

**Self-check:** 479,370 with `BETWEEN`, 509,415 in reality; the 30,045 missing events are those of November 30 after 00:00:00 (none happened at exactly midnight, which `BETWEEN` would have included).

<details><summary>Hint</summary>

`'2025-11-30'` as a `timestamptz` is midnight at the *start* of the 30th.

</details>

---

## Exercise 4 ★★: The funnel, month by month

Produce the 2025 conversion funnel per calendar month (UTC): for each month, the number of sessions that viewed a product, added to cart, started checkout and placed an order, and the conversion rate (ordered / viewed, in %). A session's events count in the month in which they happened. The query must prune: it must not read the 2026 partitions or the default partition.

**Self-check:** 12 rows. January: 50,190 viewed, 5,674 ordered, 11.31 %. November: 78,378 / 9,069 / 11.57 %. December: 103,009 viewed. `EXPLAIN` shows the twelve 2025 partitions and nothing else.

<details><summary>Hint</summary>

Group by month *and* session first (`bool_or` per step), then by month. Grouping by `date_trunc('month', occurred_at, 'UTC')` is fine; it's the *filter* that must use the bare column. The three-argument `date_trunc` truncates in a given time zone, independent of the session's setting.

</details>

---

## Exercise 5 ★★: LIST partitioning by event type

Build `lab.events_by_type`, a copy of `events`' structure LIST-partitioned by `event_type` into three partitions: views (`page_view`, `product_view`), funnel (`add_to_cart`, `remove_from_cart`, `checkout_started`, `order_placed`) and search (`search`). Load November 2025 into it. Then answer, checking with `EXPLAIN`:

1. How many rows went into each partition?
2. Which of these conditions prune: `event_type = 'order_placed'`, `event_type LIKE 'order%'`, `event_type <> 'search'`, `event_type IN ('search', 'add_to_cart')`?
3. With `enable_partitionwise_aggregate = on`, what does the plan of `SELECT event_type, count(DISTINCT session_id) FROM lab.events_by_type GROUP BY event_type` look like, and why is it different from the partial aggregation shown in section 7 of the theory?

**Self-check:** views 403,175, funnel 47,272, search 58,968. `=` prunes to one partition, `LIKE` doesn't prune, `<> 'search'` prunes to two, `IN` to two. The aggregate is computed completely within each partition (one `GroupAggregate` per partition under the `Append`, no `Finalize` step). `order_placed` has 9,069 distinct sessions.

<details><summary>Hint</summary>

`CREATE TABLE ... (LIKE events INCLUDING DEFAULTS INCLUDING CONSTRAINTS) PARTITION BY LIST (event_type)`. For question 3: what does the grouping key have in common with the partition key?

</details>

---

## Exercise 6 ★★: Hash-partitioning cart_items: worth it?

Build `lab.cart_items_h`: `cart_items` with the same columns and primary key, HASH-partitioned by `user_id` into 8 partitions, with an index on `product_id`. Load the 19,843 rows. Then measure and decide:

1. How evenly are rows and users spread over the partitions?
2. Which partition holds user 42's cart? Which queries prune: `user_id = 42`, `user_id IN (42, 43)`, `user_id < 10`, `product_id = 5`?
3. Compare the total size (all partitions with indexes) with the size of `cart_items`.
4. Would you partition `cart_items` in production? Argue with your numbers.

**Self-check:** between 2,337 and 2,543 rows per partition. User 42 is in remainder 2. `=` and `IN` prune (to 1 and 2 partitions), `<` and `product_id = 5` read all 8. The partitioned version takes 2,856 kB against 2,264 kB.

<details><summary>Hint</summary>

`FOR VALUES WITH (MODULUS 8, REMAINDER r)`, created in a loop with `\gexec`. The hash of a `bigint` is the same on every PostgreSQL installation, so the remainders are too.

</details>

---

## Exercise 7 ★★: Rescue the default partition

Late-arriving or mistyped events have landed in `events_default`. Write a function `rescue_default_rows(p_parent regclass) RETURNS TABLE (partition_name text, rows_moved bigint)` that, for every month that has rows in the default partition, creates the monthly partition and moves the rows into it, keeping their ids. It must be safe against concurrent inserts into the default partition while it runs.

Test it inside a transaction that you roll back:

```sql
BEGIN;
INSERT INTO events (occurred_at, session_id, event_type)
SELECT t, md5(t::text)::uuid, 'page_view'
FROM unnest(ARRAY['2027-02-03 10:00+00', '2027-02-27 23:59+00',
                  '2027-03-01 00:00+00', '2027-05-31 12:00+00']::timestamptz[]) AS t;
SELECT * FROM rescue_default_rows('events');
-- ... your checks ...
ROLLBACK;
```

**Self-check:** three rows: `events_y2027m02` 2, `events_y2027m03` 1, `events_y2027m05` 1. Afterwards `events_default` is empty, the four ids are unchanged, and each row's `tableoid` is its new monthly partition.

<details><summary>Hint</summary>

For each month: `DELETE ... RETURNING *` from the default partition into a temporary table, create the partition (now possible, since the default partition no longer holds rows for it), insert the rows back through the parent `OVERRIDING SYSTEM VALUE`. A lock on the default partition at the start keeps new rows out. `pg_partitioned_table.partattrs` tells you the key column.

</details>

---

## Exercise 8 ★★: Archive a month, then bring it back

1. Detach `events_y2025m01` from `events` and move it to a schema `archive`. Check that `events` now has 4,657,594 rows, and that `archive.events_y2025m01` is an ordinary table.
2. Bring it back as a partition **without a validation scan under the attach lock**. Prove that no scan happened.
3. Move it back to schema `bazaar`.

**Self-check:** with `SET client_min_messages = debug1`, the `ATTACH` prints `partition constraint for table "events_y2025m01" is implied by existing constraints` and takes a few milliseconds; `events` has 4,983,725 rows again.

<details><summary>Hint</summary>

A detached partition forgets its bounds. Give it a `CHECK` constraint equivalent to the bounds first, `NOT VALID` and then `VALIDATE`d. Look at the identity column before and after: what happens to it on detach and attach?

</details>

---

## Exercise 9 ★★: Make old months maintenance-free

Old partitions never change, but autovacuum's anti-wraparound work ([Module 16](../16-internals/)) will still visit them every few hundred million transactions unless they are frozen. Write a query that shows, for every partition of `events`: its transaction-ID age (`age(relfrozenxid)`), its number of pages, and how many pages the visibility map marks all-visible and all-frozen. Then freeze every partition of 2025 except the current month (December) and run the query again.

**Self-check:** before, `all_frozen` is 0 everywhere. After `VACUUM (FREEZE)` of January to November, those eleven partitions show an `xid_age` of 0 (or close to it) and `all_frozen` equal to their page count (possibly minus a page or two that holds a dead row version); December still has 0 frozen pages. The eleven `VACUUM`s take a few seconds in total.

<details><summary>Hint</summary>

`CREATE EXTENSION pg_visibility;` gives `pg_visibility_map_summary(regclass)`. Generate the `VACUUM` statements with `format()` and `\gexec` (`VACUUM` can't run inside a function or a transaction block).

</details>

---

## Exercise 10 ★★★: Find an event by id, fast

The support tool looks events up by `id` only (`GET /events/1234567`), and every lookup probes all 25 partitions (section 4.2: about 1 ms per lookup, mostly planning). Ids come from one sequence and are assigned at insert time, so each monthly partition holds an almost contiguous range of ids. Use that:

- a small table `events_id_ranges(partition_name, from_at, to_at, min_id, max_id)` and a procedure `refresh_events_id_ranges()` that fills it from the partitions;
- a function `event_by_id(p_id bigint) RETURNS SETOF events` that finds the time range from the table and queries `events` so that **plan-time** pruning leaves one partition. Unknown ids must still be found (slowly).

**Self-check:** after the refresh, the table has 13 rows (the non-empty partitions); `events_y2025m04` holds ids 989,935 to 1,349,672. `event_by_id(1234567)` returns the `page_view` of 2025-04-21 14:55:00+00, and the query it runs reads only `events_y2025m04` (check with `auto_explain` and `auto_explain.log_nested_statements = on`). With `pgbench`, random lookups take about a quarter of the time of `SELECT * FROM events WHERE id = :id`.

<details><summary>Hint</summary>

Ids at the edges of a month may in reality interleave with the next month's (concurrent inserts), so look for *all* ranges that contain the id and use the min/max of their bounds. To prune at plan time inside PL/pgSQL, the time bounds must be constants in the query text: `RETURN QUERY EXECUTE format(...)`. Why would a static query with variables not be enough?

</details>

---

## Exercise 11 ★★★: Merge three months into a quarter

Old data is queried rarely and only by quarter. Replace `events_y2025m01`, `events_y2025m02` and `events_y2025m03` by a single partition `events_y2025q1` covering `[2025-01-01, 2025-04-01)`, so that the partition count goes down. Requirements: no event is lost or duplicated, ids are kept, and `events` is locked (`ACCESS EXCLUSIVE`) for **milliseconds**, not seconds. Run the final switch in a transaction that ends with `ROLLBACK` (and clean up what you created outside it), or with `COMMIT` if you want to keep the result and `reset.sh 15` later.

**Self-check:** before the rollback, `events_y2025q1` has 989,934 rows, `events` still has 4,983,725, the plan of a February query reads `events_y2025q1` using an index, and the switch transaction (detach three, attach one, drop three) takes about 10 ms.

<details><summary>Hint</summary>

Everything slow happens before the switch: Q1 is closed, so you can copy it while it is still attached, and build the same indexes the parent has (`ATTACH` adopts matching indexes instead of building them). A `CHECK` for the quarter's bounds lets `ATTACH` skip validation. `LIKE events INCLUDING DEFAULTS INCLUDING CONSTRAINTS` gives you the right columns and the `CHECK` constraints with the right names.

</details>

---

## Exercise 12 ★★★: Funnel by session start

Exercise 4 counted a session's events in the month in which each event happened, so a session that starts at 23:55 on November 30 and orders at 00:10 on December 1 is split over two months: November sees the view, December the order. Fix that: count each session in the month in which it **started** (its first event), with all of its events, for November 2025. The query must still prune to the partitions it really needs. (No session in the data lasts longer than 17 minutes.)

**Self-check:** 86,966 sessions started in November; 78,380 of them viewed a product, 21,849 added to cart, 12,039 started checkout and 9,069 ordered. Twelve sessions cross the November/December boundary and six cross October/November. `EXPLAIN` shows three partitions: October, November and December.

<details><summary>Hint</summary>

Read a margin on both sides (an hour before November and an hour after), group by session, and keep the sessions whose `min(occurred_at)` falls in November. Think about which of the two margins changes which counts.

</details>
