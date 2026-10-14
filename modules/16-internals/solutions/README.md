# Module 16: Solutions

<!-- nav:start -->
> [Theory](../README.md) · [Exercises](../exercises.md) · [Project](../project.md) · **Solutions**  
> [← 15 Security and Row-Level Security](../../15-security-rls/README.md) · [Course home](../../../README.md) · [17 Configuration, Maintenance and Monitoring →](../../17-operations/README.md)
<!-- nav:end -->

All solutions were run on PostgreSQL 17 against `./project/bin/reset.sh 16` plus the extensions and the `lab` schema from the theory's "Before you start". Transaction IDs, OIDs, LSNs and file names will be different on your machine; the self-check values are not.

```sql
CREATE EXTENSION IF NOT EXISTS pageinspect;
CREATE EXTENSION IF NOT EXISTS pg_visibility;
CREATE EXTENSION IF NOT EXISTS pgstattuple;
CREATE SCHEMA IF NOT EXISTS lab;
```

---

<!-- toc:start -->
**Contents**

- [Exercise 1 ★: Every file of a table](#exercise-1--every-file-of-a-table)
- [Exercise 2 ★: Page arithmetic](#exercise-2--page-arithmetic)
- [Exercise 3 ★: The tuple behind order 250000](#exercise-3--the-tuple-behind-order-250000)
- [Exercise 4 ★★: Does the events column order cost disk?](#exercise-4--does-the-events-column-order-cost-disk)
- [Exercise 5 ★★: The index that killed HOT](#exercise-5--the-index-that-killed-hot)
- [Exercise 6 ★★: Visibility on paper](#exercise-6--visibility-on-paper)
- [Exercise 7 ★★: Find the TOAST threshold](#exercise-7--find-the-toast-threshold)
- [Exercise 8 ★★: What an index costs in WAL](#exercise-8--what-an-index-costs-in-wal)
- [Exercise 9 ★★: Make a multixact](#exercise-9--make-a-multixact)
- [Exercise 10 ★★: Freeze a table and unfreeze one page](#exercise-10--freeze-a-table-and-unfreeze-one-page)
- [Exercise 11 ★★★: Estimate a conjunction by hand](#exercise-11--estimate-a-conjunction-by-hand)
- [Exercise 12 ★★★: Bloat with and without VACUUM](#exercise-12--bloat-with-and-without-vacuum)
- [Exercise 13 ★★★: Walk a HOT chain](#exercise-13--walk-a-hot-chain)

<!-- toc:end -->

## Exercise 1 ★: Every file of a table

A table's storage is spread over several relations, each with its own file(s). Collect their OIDs with a `UNION ALL`, then ask for the path and the size of every fork:

```sql
WITH t AS (
    SELECT oid, reltoastrelid FROM pg_class WHERE oid = 'products'::regclass
), rels AS (
    SELECT 'table' AS kind, t.oid FROM t
    UNION ALL
    SELECT 'TOAST table', t.reltoastrelid FROM t
    UNION ALL
    SELECT 'TOAST index', i.indexrelid FROM pg_index i JOIN t ON i.indrelid = t.reltoastrelid
    UNION ALL
    SELECT 'index', i.indexrelid FROM pg_index i JOIN t ON i.indrelid = t.oid
)
SELECT kind,
       oid::regclass AS relation,
       pg_relation_filepath(oid) AS path,
       pg_relation_size(oid, 'main') + pg_relation_size(oid, 'fsm')
         + pg_relation_size(oid, 'vm') + pg_relation_size(oid, 'init') AS bytes
FROM rels
ORDER BY kind, oid::regclass::text;
```

```text
NOTICE:  schema "lab" already exists, skipping
    kind     |               relation                |       path       |   bytes   
-------------+---------------------------------------+------------------+-----------
 index       | products_active_category_id_price_idx | base/32848/34204 |   5398528
 index       | products_attributes_idx               | base/32848/34205 |   6299648
 index       | products_category_id_idx              | base/32848/34203 |   1417216
 index       | products_pkey                         | base/32848/34201 |   4513792
 index       | products_search_vector_idx            | base/32848/34207 |  12058624
 index       | products_seller_id_sku_key            | base/32848/34202 |   8151040
 index       | products_tags_idx                     | base/32848/34206 |   1490944
 index       | products_title_trgm_idx               | base/32848/34208 |  20561920
 table       | products                              | base/32848/34196 | 145227776
 TOAST index | pg_toast.pg_toast_32899_index         | base/32848/34200 |      8192
 TOAST table | pg_toast.pg_toast_32899               | base/32848/34199 |         0
(11 rows)
```

And the check:

```sql
WITH t AS (
    SELECT oid, reltoastrelid FROM pg_class WHERE oid = 'products'::regclass
), rels AS (
    SELECT t.oid FROM t
    UNION ALL SELECT t.reltoastrelid FROM t
    UNION ALL SELECT i.indexrelid FROM pg_index i JOIN t ON i.indrelid = t.reltoastrelid
    UNION ALL SELECT i.indexrelid FROM pg_index i JOIN t ON i.indrelid = t.oid
)
SELECT sum(pg_relation_size(oid, 'main') + pg_relation_size(oid, 'fsm')
           + pg_relation_size(oid, 'vm') + pg_relation_size(oid, 'init')) AS my_sum,
       pg_total_relation_size('products') AS pg_total
FROM rels;
```

```text
  my_sum   | pg_total  
-----------+-----------
 205127680 | 205127680
(1 row)
```

Observations:

- Eight indexes, of very different sizes: the GIN index for full-text search and the trigram index (Module 13) are among the largest. Indexes are a large part of the table's footprint.
- The TOAST table is empty (0 bytes) but its index already takes one page (8 kB): a B-tree always has a metapage.
- For the indexes only the `main` fork is non-zero. Indexes never have a visibility map, and an index gets a free space map only once VACUUM has deleted whole index pages that can be recycled; these indexes were built recently and never needed one.

`pg_total_relation_size` does exactly this sum. Doing it by hand shows that "the size of a table" is a choice: heap only (`pg_relation_size`), heap plus TOAST and maps (`pg_table_size`), or everything (`pg_total_relation_size`).

---

## Exercise 2 ★: Page arithmetic

**Prediction.**

(a) `b` not NULL: the header is 23 bytes, padded to 24 (no null bitmap). `a` is a `bigint`, aligned to 8: offset 24, 8 bytes. `b` is a `boolean`, 1 byte, no alignment: offset 32. The tuple is 33 bytes, padded to 40. Each tuple also needs a 4-byte line pointer: 44 bytes per row. The page has 8192 − 24 (header) = 8168 bytes for line pointers and tuples: ⌊8168 / 44⌋ = **185**.

(b) `b` NULL: the null bitmap (1 byte for 2 columns) fits in the header's padding (23 + 1 = 24), and the NULL takes no space. Tuple = 24 + 8 = 32 bytes, already aligned; with the line pointer, 36: ⌊8168 / 36⌋ = **226**. A NULL boolean saves 8 bytes per row here, not 1, because of padding.

**Verification.**

```sql
CREATE TABLE lab.ex_pairs (a bigint, b boolean);
CREATE TABLE lab.ex_pairs_null (a bigint, b boolean);
INSERT INTO lab.ex_pairs      SELECT g, true FROM generate_series(1, 1000) AS g;
INSERT INTO lab.ex_pairs_null SELECT g, NULL FROM generate_series(1, 1000) AS g;

SELECT 'b not null' AS variant, count(*) AS rows_on_page_0
FROM lab.ex_pairs WHERE (ctid::text::point)[0] = 0
UNION ALL
SELECT 'b null', count(*)
FROM lab.ex_pairs_null WHERE (ctid::text::point)[0] = 0;
```

```text
CREATE TABLE
CREATE TABLE
INSERT 0 1000
INSERT 0 1000
  variant   | rows_on_page_0 
------------+----------------
 b not null |            185
 b null     |            226
(2 rows)
```

You can also read it off the page header: `pd_lower` = 24 + 4 × 185 = 764 for the first table.

---

## Exercise 3 ★: The tuple behind order 250000

```sql
SELECT ctid, xmin,
       (ctid::text::point)[0]::int AS blk,
       (ctid::text::point)[1]::int AS item
FROM orders WHERE id = 250000 \gset

SELECT :'ctid' AS ctid_from_select, :'xmin' AS xmin_from_select;

SELECT lp, lp_off, lp_len, t_xmin, t_xmax, t_ctid, t_hoff, raw_flags
FROM heap_page_items(get_raw_page('orders', :blk)),
     LATERAL heap_tuple_infomask_flags(t_infomask, t_infomask2)
WHERE lp = :item;
```

```text
 ctid_from_select | xmin_from_select 
------------------+------------------
 (3549,7)         | 41138
(1 row)

 lp | lp_off | lp_len | t_xmin | t_xmax |  t_ctid  | t_hoff |                        raw_flags                         
----+--------+--------+--------+--------+----------+--------+----------------------------------------------------------
  7 |   7408 |    112 |  41138 |      0 | (3549,7) |     24 | {HEAP_HASVARWIDTH,HEAP_XMIN_COMMITTED,HEAP_XMAX_INVALID}
(1 row)
```

- `t_xmin` is the transaction that loaded the large dataset (all rows of `orders` were inserted by one transaction in `1101_seed_large.sql`), and it equals the `xmin` column. `t_xmax = 0`: nobody deleted, updated or locked this version. `t_ctid` points to the tuple itself: it is the newest version.
- `HEAP_HASVARWIDTH`: the row has a variable-width column (`total` is a `numeric`).
- `HEAP_XMIN_COMMITTED`: the hint bit "the inserting transaction committed", set by the first reader (or by the VACUUM that ran after the load), so nobody needs to consult `pg_xact` for this row again.
- `HEAP_XMAX_INVALID`: the hint bit "there is no valid deleter", so readers don't need to look at `t_xmax` either.
- What's *not* there is informative too. No `HEAP_HASNULL`: order 250000 is delivered, so all its timestamps are set, there is no null bitmap, and `t_hoff` is 24 (an order with a NULL timestamp would have a 2-byte bitmap for its 11 columns, and `t_hoff` 32). And the row is not frozen (that would show as `HEAP_XMIN_COMMITTED` **and** `HEAP_XMIN_INVALID`): the load is only tens of thousands of transactions old, far below `vacuum_freeze_min_age` (50 million).

---

## Exercise 4 ★★: Does the events column order cost disk?

Walk through a typical row as defined: `id` (8, at 0), `occurred_at` (8, at 8), `user_id` (8, at 16, or absent when NULL), `session_id` (`uuid`, 16 bytes, alignment `c`: no padding), `event_type` (a short text such as `product_view`, 1-byte header, unaligned), then **`product_id`**, a `bigint` that must start at a multiple of 8: after a 13-byte text, up to 7 bytes of padding. Then `payload` (`jsonb`). Moving `product_id` up next to the other 8-byte columns removes that padding:

```sql
SELECT count(*) AS events,
       pg_size_pretty(sum(ceil(pg_column_size(e.*) / 8.0) * 8)) AS as_defined,
       pg_size_pretty(sum(ceil(pg_column_size(ROW(e.id, e.occurred_at, e.user_id, e.product_id,
                                                  e.session_id, e.event_type, e.payload)) / 8.0) * 8)) AS reordered,
       pg_size_pretty(sum(ceil(pg_column_size(e.*) / 8.0) * 8)
                      - sum(ceil(pg_column_size(ROW(e.id, e.occurred_at, e.user_id, e.product_id,
                                                        e.session_id, e.event_type, e.payload)) / 8.0) * 8)) AS saving,
       round(100 * (1 - sum(ceil(pg_column_size(ROW(e.id, e.occurred_at, e.user_id, e.product_id,
                                                    e.session_id, e.event_type, e.payload)) / 8.0) * 8)
                        / sum(ceil(pg_column_size(e.*) / 8.0) * 8)), 1) AS saving_pct
FROM events e;
```

```text
 events  | as_defined | reordered | saving | saving_pct 
---------+------------+-----------+--------+------------
 4983725 | 514 MB     | 499 MB    | 15 MB  |        2.9
(1 row)
```

(The sums count tuple data and header, not line pointers or page overhead, which don't change.)

Is it worth it? About 3% of the table, for a full rewrite of 5 million rows: on its own, no. A rewrite takes an exclusive lock (or a tool like `pg_repack`, Module 17) and lots of I/O. But `events` is partitioned by month (Module 14), and that offers a cheap way out: a partition doesn't need the parent's physical column order. You can create next month's partition as a standalone table with the better order (same column names, types, `NOT NULL`s and `CHECK` constraints) and `ALTER TABLE events ATTACH PARTITION ...` it; PostgreSQL maps the columns by name. New data gets the compact layout, old partitions age out with the retention policy, and nothing is rewritten.

Bigger wins: `event_type` as a `text` costs 9 to 17 bytes per row; an `enum` (4 bytes) or a `smallint` code (2 bytes) placed next to the other fixed-width columns would save about 10 bytes per row (≈ 50 MB here) and make the column cheaper to compare. For an append-only stream of billions of rows, that kind of design choice pays for itself many times over.

---

## Exercise 5 ★★: The index that killed HOT

```sql
ALTER TABLE inventory SET (fillfactor = 90);
VACUUM FULL inventory;

-- run 1: no extra index
SELECT pg_stat_reset_single_table_counters('inventory'::regclass);
DO $$
BEGIN
    FOR i IN 1..1000 LOOP
        UPDATE inventory SET reserved = reserved WHERE product_id = i * 97;
        COMMIT;
    END LOOP;
END
$$;
SELECT pg_stat_force_next_flush();
SELECT 'without index' AS run, n_tup_upd, n_tup_hot_upd,
       round(100.0 * n_tup_hot_upd / n_tup_upd) AS hot_pct
FROM pg_stat_user_tables WHERE relname = 'inventory';

-- run 2: with an index on updated_at
CREATE INDEX ex_inventory_updated_at_idx ON inventory (updated_at);
SELECT pg_stat_reset_single_table_counters('inventory'::regclass);
DO $$
BEGIN
    FOR i IN 1..1000 LOOP
        UPDATE inventory SET reserved = reserved WHERE product_id = i * 97;
        COMMIT;
    END LOOP;
END
$$;
SELECT pg_stat_force_next_flush();
SELECT 'with index on updated_at' AS run, n_tup_upd, n_tup_hot_upd,
       round(100.0 * n_tup_hot_upd / n_tup_upd) AS hot_pct
FROM pg_stat_user_tables WHERE relname = 'inventory';

-- clean up
DROP INDEX ex_inventory_updated_at_idx;
ALTER TABLE inventory RESET (fillfactor);
VACUUM FULL inventory;
```

```text
ALTER TABLE
VACUUM
 pg_stat_reset_single_table_counters 
-------------------------------------
 
(1 row)

DO
 pg_stat_force_next_flush 
--------------------------
 
(1 row)

      run      | n_tup_upd | n_tup_hot_upd | hot_pct 
---------------+-----------+---------------+---------
 without index |      1000 |          1000 |     100
(1 row)

CREATE INDEX
 pg_stat_reset_single_table_counters 
-------------------------------------
 
(1 row)

DO
 pg_stat_force_next_flush 
--------------------------
 
(1 row)

           run            | n_tup_upd | n_tup_hot_upd | hot_pct 
--------------------------+-----------+---------------+---------
 with index on updated_at |      1000 |             0 |       0
(1 row)

DROP INDEX
ALTER TABLE
VACUUM
```

The update only "changes" `reserved` to itself, but the `inventory_set_updated_at` trigger (Module 09) sets `updated_at = now()` on every update. Once `updated_at` is indexed, every update changes an indexed column, so none can be HOT, and every update inserts a new entry into both indexes. The statement itself never mentions `updated_at`, which is what makes this so easy to miss.

Alternatives if you really need to find recently changed rows: a BRIN index on `updated_at` (PG 16+: BRIN is a "summarizing" index and doesn't prevent HOT), or an append-only change log table written by a trigger.

---

## Exercise 6 ★★: Visibility on paper

The snapshot `1000:1010:1003,1007` means: XIDs below 1000 are finished; XIDs from 1010 on are treated as running; between them, 1003 and 1007 were running when the snapshot was taken, everything else had finished. Combined with the commit log:

| tuple | inserter (`xmin`) | deleter (`xmax`) | visible? |
|---|---|---|---|
| A | 990: before the snapshot, committed: visible | none | **yes** |
| B | 995: visible | 1001: finished before the snapshot and committed, so the delete is visible | no |
| C | 990: visible | 1003: in `xip`, still running for this snapshot (it committed later, which doesn't matter) | **yes** |
| D | 1005: between `xmin` and `xmax`, not in `xip`, committed | none | **yes** |
| E | 1007: in `xip`, invisible even though it committed later | none | no |
| F | 998: aborted | none | no |
| G | 1001: visible | 1012: ≥ `xmax`, "in the future" | **yes** |

Four visible: A, C, D and G. The snapshot part can be checked in SQL:

```sql
SELECT x AS xid, pg_visible_in_snapshot(x::text::xid8, '1000:1010:1003,1007'::pg_snapshot) AS visible_in_snapshot
FROM unnest(ARRAY[990, 995, 998, 1001, 1003, 1005, 1007, 1012]) AS x;
```

```text
 xid  | visible_in_snapshot 
------+---------------------
  990 | t
  995 | t
  998 | t
 1001 | t
 1003 | f
 1005 | t
 1007 | f
 1012 | f
(8 rows)
```

Note that `pg_visible_in_snapshot` says `true` for 998: the function only answers "had this XID finished before the snapshot?" Whether it committed or aborted comes from the commit log, which is why tuple F is invisible. The real visibility check needs both pieces of information, which is exactly what section 6.3 of the theory describes.

---

## Exercise 7 ★★: Find the TOAST threshold

One row per candidate length, then find the first one that went to the TOAST table:

```sql
CREATE TABLE lab.ex_toast (id int, body text);
INSERT INTO lab.ex_toast
SELECT n, left((SELECT string_agg(md5(g::text), '') FROM generate_series(1, 200) AS g), n)
FROM generate_series(1950, 2050) AS n;

SELECT id AS n, octet_length(body) AS raw_bytes, pg_column_size(body) AS stored_bytes,
       pg_column_compression(body) AS compression,
       pg_column_toast_chunk_id(body) IS NOT NULL AS out_of_line
FROM lab.ex_toast
WHERE id BETWEEN 1998 AND 2003
ORDER BY id;

SELECT min(id) AS threshold FROM lab.ex_toast WHERE pg_column_toast_chunk_id(body) IS NOT NULL;
```

```text
CREATE TABLE
INSERT 0 101
  n   | raw_bytes | stored_bytes | compression | out_of_line 
------+-----------+--------------+-------------+-------------
 1998 |      1998 |         2002 |             | f
 1999 |      1999 |         2003 |             | f
 2000 |      2000 |         2004 |             | f
 2001 |      2001 |         2001 |             | t
 2002 |      2002 |         2002 |             | t
 2003 |      2003 |         2003 |             | t
(6 rows)

 threshold 
-----------
      2001
(1 row)
```

Values up to 2,000 bytes stay inline (stored with a 4-byte header: 2,004 bytes); from 2,001 bytes on, the value goes to the TOAST table. The row layout explains the number exactly: 24 bytes of tuple header (23, padded to 8), 4 bytes for `id`, then the text's 4-byte length header and its data. 24 + 4 + 4 + 2,000 = 2,032 bytes, which is precisely `TOAST_TUPLE_THRESHOLD`; one more byte and the row is too big. Since the data doesn't compress (no `compression` shown), PostgreSQL moves it out of line.

The threshold is per **row**, not per value: in a table with more columns, a smaller text would already be toasted. And `toast_tuple_target` (a storage parameter, `ALTER TABLE ... SET (toast_tuple_target = 256)`) lowers the target size to which values are compressed or moved, if you want wide values out of the way of sequential scans.

---

## Exercise 8 ★★: What an index costs in WAL

```sql
CREATE TABLE lab.ex_inv0 (LIKE inventory INCLUDING DEFAULTS);
CREATE TABLE lab.ex_inv1 (LIKE inventory INCLUDING DEFAULTS);
ALTER TABLE lab.ex_inv1 ADD PRIMARY KEY (product_id);
CREATE TABLE lab.ex_inv3 (LIKE inventory INCLUDING DEFAULTS);
ALTER TABLE lab.ex_inv3 ADD PRIMARY KEY (product_id);
CREATE INDEX ON lab.ex_inv3 (quantity);
CREATE INDEX ON lab.ex_inv3 (updated_at);

SELECT pg_current_wal_lsn() AS l0 \gset
INSERT INTO lab.ex_inv0 SELECT * FROM inventory WHERE product_id <= 50000;
SELECT pg_current_wal_lsn() AS l1 \gset
INSERT INTO lab.ex_inv1 SELECT * FROM inventory WHERE product_id <= 50000;
SELECT pg_current_wal_lsn() AS l2 \gset
INSERT INTO lab.ex_inv3 SELECT * FROM inventory WHERE product_id <= 50000;
SELECT pg_current_wal_lsn() AS l3 \gset

SELECT pg_size_pretty(:'l1'::pg_lsn - :'l0') AS no_index,
       pg_size_pretty(:'l2'::pg_lsn - :'l1') AS pk_only,
       pg_size_pretty(:'l3'::pg_lsn - :'l2') AS pk_plus_two,
       round((:'l2'::pg_lsn - :'l1') / (:'l1'::pg_lsn - :'l0'), 2) AS pk_ratio,
       round((:'l3'::pg_lsn - :'l2') / (:'l1'::pg_lsn - :'l0'), 2) AS three_ratio;
```

```text
CREATE TABLE
CREATE TABLE
ALTER TABLE
CREATE TABLE
ALTER TABLE
CREATE INDEX
CREATE INDEX
INSERT 0 50000
INSERT 0 50000
INSERT 0 50000
 no_index | pk_only | pk_plus_two | pk_ratio | three_ratio 
----------+---------+-------------+----------+-------------
 3918 kB  | 7153 kB | 14 MB       |     1.83 |        3.60
(1 row)
```

The primary key almost doubles the WAL (each row also needs a `Btree/INSERT_LEAF` record next to its `Heap/INSERT` record), and two more indexes bring it to about 3.6 times the no-index volume. Indexes on low-cardinality or random columns cost a bit more than the primary key's sequential inserts, because they touch more distinct leaf pages, each of which needs a full-page image the first time after a checkpoint. All of that WAL is written at commit time, archived (Module 18) and shipped to every replica (Module 19): an unused index costs much more than its disk space.

---

## Exercise 9 ★★: Make a multixact

Session A and session B each run:

```sql
BEGIN;
SELECT pg_current_xact_id();
SELECT product_id FROM inventory WHERE product_id = 42 FOR SHARE;
```

While both transactions are open, either session (or a third) runs:

```sql
SELECT t_xmax, raw_flags
FROM heap_page_items(get_raw_page('inventory',
         (SELECT (ctid::text::point)[0]::int FROM inventory WHERE product_id = 42))),
     LATERAL heap_tuple_infomask_flags(t_infomask, t_infomask2)
WHERE lp = (SELECT (ctid::text::point)[1]::int FROM inventory WHERE product_id = 42);

SELECT * FROM pg_get_multixact_members(
    (SELECT t_xmax FROM heap_page_items(get_raw_page('inventory',
         (SELECT (ctid::text::point)[0]::int FROM inventory WHERE product_id = 42)))
     WHERE lp = (SELECT (ctid::text::point)[1]::int FROM inventory WHERE product_id = 42)));
```

```text
 A       |              57559
 B       |              57560
 t_xmax |                                                               raw_flags                                                               
--------+---------------------------------------------------------------------------------------------------------------------------------------
      4 | {HEAP_XMAX_KEYSHR_LOCK,HEAP_XMAX_EXCL_LOCK,HEAP_XMAX_LOCK_ONLY,HEAP_XMIN_COMMITTED,HEAP_XMIN_INVALID,HEAP_XMAX_IS_MULTI,HEAP_UPDATED}
(1 row)

  xid  | mode 
-------+------
 57559 | sh
 57560 | sh
(2 rows)
```

The first line of each pair is the session's XID (`session | pg_current_xact_id`). `t_xmax` is not a transaction ID but a **multixact ID** (a separate counter, starting at 1 on a fresh cluster), as `HEAP_XMAX_IS_MULTI` says. (`HEAP_UPDATED` only means that this version was itself created by an `UPDATE`; it appears if the row was updated before, for example by the project's workload.) `HEAP_XMAX_LOCK_ONLY` says the members only lock the row, and `HEAP_XMAX_KEYSHR_LOCK` + `HEAP_XMAX_EXCL_LOCK` together encode a `SHARE` lock. `pg_get_multixact_members` lists both transactions with mode `sh` (share).

When one session commits, the multixact is **not** rewritten: `xmax` keeps pointing to it, and readers find out from the member list that one member is gone and the other still holds its lock. When both are finished, the multixact is simply obsolete; the next reader sets the `HEAP_XMAX_INVALID` hint bit, and VACUUM eventually removes the reference when it freezes the row. That's why multixact IDs are consumed even though nothing is "stored" for long, and why they need their own freezing and wraparound protection (section 11.4).

---

## Exercise 10 ★★: Freeze a table and unfreeze one page

```sql
CREATE TABLE lab.ex_frozen AS SELECT * FROM orders WHERE id <= 10000;

SELECT 'after CREATE' AS step, age(relfrozenxid) AS xid_age,
       pg_relation_size(oid) / 8192 AS pages, v.all_visible, v.all_frozen
FROM pg_class, pg_visibility_map_summary('lab.ex_frozen') v
WHERE oid = 'lab.ex_frozen'::regclass;

VACUUM (FREEZE) lab.ex_frozen;

SELECT 'after VACUUM (FREEZE)' AS step, age(relfrozenxid) AS xid_age,
       pg_relation_size(oid) / 8192 AS pages, v.all_visible, v.all_frozen
FROM pg_class, pg_visibility_map_summary('lab.ex_frozen') v
WHERE oid = 'lab.ex_frozen'::regclass;

UPDATE lab.ex_frozen SET total = total WHERE id = 5000;

SELECT 'after UPDATE' AS step, age(relfrozenxid) AS xid_age,
       pg_relation_size(oid) / 8192 AS pages, v.all_visible, v.all_frozen
FROM pg_class, pg_visibility_map_summary('lab.ex_frozen') v
WHERE oid = 'lab.ex_frozen'::regclass;
```

```text
SELECT 10000
     step     | xid_age | pages | all_visible | all_frozen 
--------------+---------+-------+-------------+------------
 after CREATE |       1 |   192 |           0 |          0
(1 row)

VACUUM
         step          | xid_age | pages | all_visible | all_frozen 
-----------------------+---------+-------+-------------+------------
 after VACUUM (FREEZE) |       1 |   142 |         142 |        142
(1 row)

UPDATE 1
     step     | xid_age | pages | all_visible | all_frozen 
--------------+---------+-------+-------------+------------
 after UPDATE |       2 |   142 |         140 |        140
(1 row)
```

Step by step:

- **After `CREATE TABLE AS`**: `relfrozenxid` is the XID of the creating transaction, so the age is tiny (it counts transactions since then). The visibility map is empty: nothing has been vacuumed, so no page is known to be all-visible. The table has 192 pages, although 10,000 orders need only 142: since PostgreSQL 16 a bulk insert extends the file by many pages at a time, and the unused ones stay at the end until a VACUUM truncates them.
- **After `VACUUM (FREEZE)`**: every tuple was frozen (`FREEZE` behaves as if `vacuum_freeze_min_age` were 0), so every page is all-visible **and** all-frozen, and `relfrozenxid` advanced to the newest possible value: the age is 1 again. The empty pages at the end were truncated: 142 pages.
- **After the `UPDATE`**: two pages lost both bits, not one. The page holding the old version was modified (its `xmax` is set), and the new version had to go elsewhere because the pages are full (fillfactor 100): to the page with the most free space according to the FSM. That page now contains a tuple that isn't visible to everyone yet. With free space on the original page (a HOT update), only one page would have been affected. The table's `relfrozenxid` doesn't change: the unfrozen new tuple is younger than it, which is allowed.

---

## Exercise 11 ★★★: Estimate a conjunction by hand

The planner multiplies the two selectivities. `status = 'cancelled'` comes straight from the MCV list; `total < 50` is the MCV-plus-histogram calculation from section 13.3:

```sql
WITH st AS (
    SELECT f AS sel_status
    FROM pg_stats, unnest(most_common_vals::text::text[], most_common_freqs) AS m (v, f)
    WHERE tablename = 'orders' AND attname = 'status' AND v = 'cancelled'
), tt AS (
    SELECT histogram_bounds::text::numeric[] AS b,
           most_common_vals::text::numeric[] AS mcv,
           most_common_freqs AS mcf,
           null_frac
    FROM pg_stats WHERE tablename = 'orders' AND attname = 'total'
), tk AS (
    SELECT tt.*, (SELECT max(i) FROM generate_subscripts(b, 1) AS i WHERE b[i] <= 50) AS k FROM tt
), sel_total AS (
    SELECT (SELECT coalesce(sum(f), 0) FROM unnest(mcv, mcf) AS m (v, f) WHERE v < 50)
           + ((k - 1) + (50 - b[k]) / (b[k + 1] - b[k])) / (array_length(b, 1) - 1)
             * (1 - (SELECT sum(f) FROM unnest(mcf) AS f) - null_frac) AS sel_total
    FROM tk
)
SELECT round(st.sel_status::numeric, 5) AS sel_status,
       round(sel_total.sel_total::numeric, 5) AS sel_total,
       round(c.reltuples * st.sel_status * sel_total.sel_total) AS my_estimate
FROM st, sel_total, pg_class c
WHERE c.oid = 'orders'::regclass;

EXPLAIN SELECT * FROM orders WHERE status = 'cancelled' AND total < 50;

SELECT count(*) AS actual FROM orders WHERE status = 'cancelled' AND total < 50;
```

```text
 sel_status | sel_total | my_estimate 
------------+-----------+-------------
    0.05013 |   0.15594 |        3909
(1 row)

                                     QUERY PLAN                                     
------------------------------------------------------------------------------------
 Gather  (cost=1000.00..11610.70 rows=3907 width=83)
   Workers Planned: 2
   ->  Parallel Seq Scan on orders  (cost=0.00..10220.00 rows=1628 width=83)
         Filter: ((total < '50'::numeric) AND (status = 'cancelled'::order_status))
(4 rows)

 actual 
--------
   3973
(1 row)
```

The hand calculation reproduces the planner's estimate. The actual count is 3,973, within 2% of the estimate: for this pair of columns, independence holds.

Is independence a good assumption here? Compare the share of cancelled orders among cheap orders with the share overall:

```sql
SELECT round(avg((status = 'cancelled')::int) FILTER (WHERE total < 50), 4) AS cancelled_share_cheap,
       round(avg((status = 'cancelled')::int), 4)                          AS cancelled_share_all
FROM orders;
```

```text
 cancelled_share_cheap | cancelled_share_all 
-----------------------+---------------------
                0.0504 |              0.0501
(1 row)
```

About 5% of orders are cancelled whatever their total: knowing that an order is cheap tells you nothing about whether it was cancelled. That's exactly the case in which multiplying selectivities is correct. (In Bazaar's generated data, 5% of orders are cancelled at random, independently of their amount; the other statuses depend on the order's age.)

If the columns were correlated (say, cheap orders were cancelled far more often), the product of selectivities would be wrong, and the fix would be extended statistics: `CREATE STATISTICS orders_status_total (mcv) ON status, total FROM orders; ANALYZE orders;` gives the planner a multi-column MCV list (Module 12). For a numeric column with thousands of distinct values, a multi-column MCV list helps only for the most common combinations, so in practice you would rather add a cheap/expensive bucket column or accept the estimate.

---

## Exercise 12 ★★★: Bloat with and without VACUUM

`VACUUM` can't run inside a `DO` block, so we let psql generate the statements and run them with `\gexec`, which executes every column of every result row as a separate command. (`\set QUIET on` suppresses the 40-odd `UPDATE 50000` / `VACUUM` / `INSERT 0 1` messages.)

```sql
\set QUIET on
CREATE TABLE lab.ex_results (variant text, round int, bytes bigint);

-- variant (a): no VACUUM
CREATE TABLE lab.ex_churn (id int PRIMARY KEY, qty int, pad text) WITH (autovacuum_enabled = off);
INSERT INTO lab.ex_churn SELECT g, 0, repeat('x', 50) FROM generate_series(1, 50000) AS g;
INSERT INTO lab.ex_results VALUES ('a', 0, pg_relation_size('lab.ex_churn'));
SELECT 'UPDATE lab.ex_churn SET qty = qty + 1',
       format('INSERT INTO lab.ex_results VALUES (''a'', %s, pg_relation_size(''lab.ex_churn''))', r)
FROM generate_series(1, 10) AS r \gexec

-- variant (b): VACUUM after every round
DROP TABLE lab.ex_churn;
CREATE TABLE lab.ex_churn (id int PRIMARY KEY, qty int, pad text) WITH (autovacuum_enabled = off);
INSERT INTO lab.ex_churn SELECT g, 0, repeat('x', 50) FROM generate_series(1, 50000) AS g;
INSERT INTO lab.ex_results VALUES ('b', 0, pg_relation_size('lab.ex_churn'));
SELECT 'UPDATE lab.ex_churn SET qty = qty + 1',
       'VACUUM lab.ex_churn',
       format('INSERT INTO lab.ex_results VALUES (''b'', %s, pg_relation_size(''lab.ex_churn''))', r)
FROM generate_series(1, 10) AS r \gexec
\set QUIET off

SELECT round,
       pg_size_pretty(max(bytes) FILTER (WHERE variant = 'a')) AS a_no_vacuum,
       round(max(bytes) FILTER (WHERE variant = 'a')::numeric
             / (SELECT bytes FROM lab.ex_results WHERE variant = 'a' AND round = 0), 1) AS a_ratio,
       pg_size_pretty(max(bytes) FILTER (WHERE variant = 'b')) AS b_vacuum,
       round(max(bytes) FILTER (WHERE variant = 'b')::numeric
             / (SELECT bytes FROM lab.ex_results WHERE variant = 'b' AND round = 0), 1) AS b_ratio
FROM lab.ex_results
GROUP BY round ORDER BY round;
```

```text
 round | a_no_vacuum | a_ratio | b_vacuum | b_ratio 
-------+-------------+---------+----------+---------
     0 | 4552 kB     |     1.0 | 4552 kB  |     1.0
     1 | 9096 kB     |     2.0 | 9096 kB  |     2.0
     2 | 13 MB       |     3.0 | 9096 kB  |     2.0
     3 | 18 MB       |     4.0 | 9096 kB  |     2.0
     4 | 22 MB       |     5.0 | 9096 kB  |     2.0
     5 | 27 MB       |     6.0 | 9096 kB  |     2.0
     6 | 31 MB       |     7.0 | 9096 kB  |     2.0
     7 | 35 MB       |     8.0 | 9096 kB  |     2.0
     8 | 40 MB       |     9.0 | 9096 kB  |     2.0
     9 | 44 MB       |    10.0 | 9096 kB  |     2.0
    10 | 49 MB       |    11.0 | 9096 kB  |     2.0
(11 rows)
```

**Variant (a)** grows by one full copy of the table per round: every `UPDATE` writes 50,000 new versions, and with autovacuum off nothing ever makes the old ones reusable. During the next round, page pruning (section 7 of the theory) *could* clean up some pages, but only on pages that are being updated in place, and since every page is full, every new version goes to a fresh page at the end of the file. After 10 rounds the table is 11 times its original size, 91% of it dead.

**Variant (b)** doubles once and then stays flat. Round 1 needs room for 50,000 new versions while the 50,000 old ones are still there (they're only dead after the `UPDATE` commits), so the file doubles. The `VACUUM` then marks the old versions' space as free in the FSM. Round 2's new versions go into that free space, and the round-1 versions become dead; the next `VACUUM` frees them, and so on. The table oscillates between "half live, half dead" and "half live, half free" at twice its original size. That's the normal steady state of a table that gets fully rewritten between vacuums, and the reason why "VACUUM doesn't shrink tables" is not a problem in practice: the space is reused. To get back to 4.5 MB you would need a rewrite (`VACUUM FULL`); to avoid the doubling in the first place, you'd update in smaller batches with vacuums in between (or let HOT do the work with a lower fillfactor).

---

## Exercise 13 ★★★: Walk a HOT chain

```sql
CREATE TABLE lab.ex_chain (id int PRIMARY KEY, v int) WITH (fillfactor = 50);
INSERT INTO lab.ex_chain VALUES (1, 0);

BEGIN;
UPDATE lab.ex_chain SET v = 1 WHERE id = 1;
UPDATE lab.ex_chain SET v = 2 WHERE id = 1;
UPDATE lab.ex_chain SET v = 3 WHERE id = 1;

WITH RECURSIVE items AS (
    SELECT lp, lp_flags, lp_off, t_xmin, t_xmax, t_ctid,
           (t_infomask2 & 16384) > 0 AS hot_updated,     -- HEAP_HOT_UPDATED
           (t_infomask2 & 32768) > 0 AS heap_only        -- HEAP_ONLY_TUPLE
    FROM heap_page_items(get_raw_page('lab.ex_chain', 0))
), chain AS (
    SELECT 1 AS step, i.* FROM items i WHERE i.lp = 1    -- the root: what the index points to
    UNION ALL
    SELECT c.step + 1, i.*
    FROM chain c
    JOIN items i
      ON i.lp = CASE WHEN c.lp_flags = 2 THEN c.lp_off   -- redirect: lp_off is the target
                     ELSE (c.t_ctid::text::point)[1]::int END
    WHERE c.lp_flags = 2                                 -- follow a redirect, or
       OR c.t_ctid <> format('(0,%s)', c.lp)::tid        -- follow t_ctid until it points to itself
)
SELECT step, lp, lp_flags, lp_off, t_xmin, t_xmax, t_ctid, hot_updated, heap_only
FROM chain ORDER BY step;
COMMIT;
```

```text
CREATE TABLE
INSERT 0 1
BEGIN
UPDATE 1
UPDATE 1
UPDATE 1
 step | lp | lp_flags | lp_off | t_xmin | t_xmax | t_ctid | hot_updated | heap_only 
------+----+----------+--------+--------+--------+--------+-------------+-----------
    1 |  1 |        1 |   8160 |  57613 |  57614 | (0,2)  | t           | f
    2 |  2 |        1 |   8128 |  57614 |  57614 | (0,3)  | t           | t
    3 |  3 |        1 |   8096 |  57614 |  57614 | (0,4)  | t           | t
    4 |  4 |        1 |   8064 |  57614 |      0 | (0,4)  | f           | t
(4 rows)

COMMIT
```

Four versions on the same page, linked by `t_ctid`: 1 → 2 → 3 → 4. The first three are `hot_updated` (their successor is a heap-only tuple on the same page); the last three are `heap_only` (no index entry points to them). All four carry the same `t_xmin`/`t_xmax` XID because one transaction made all three updates; inside it, the command IDs (`cmin`/`cmax`) decide which version each statement sees. The primary key index still has a single entry, pointing at line pointer 1.

Now read the row and look at line pointer 1:

```sql
SELECT * FROM lab.ex_chain WHERE id = 1;
SELECT lp, lp_flags, lp_off FROM heap_page_items(get_raw_page('lab.ex_chain', 0));
```

```text
 id | v 
----+---
  1 | 3
(1 row)

 lp | lp_flags | lp_off 
----+----------+--------
  1 |        1 |   8160
  2 |        1 |   8128
  3 |        1 |   8096
  4 |        1 |   8064
(4 rows)
```

Nothing was pruned. The page is almost empty, and opportunistic pruning only runs when the page's free space falls below the larger of the fillfactor reserve (here 50% of the page) and 10%. Pruning a page that has plenty of room would cost work and WAL for no benefit. VACUUM prunes regardless:

```sql
VACUUM lab.ex_chain;

WITH RECURSIVE items AS (
    SELECT lp, lp_flags, lp_off, t_xmin, t_xmax, t_ctid,
           (t_infomask2 & 16384) > 0 AS hot_updated,
           (t_infomask2 & 32768) > 0 AS heap_only
    FROM heap_page_items(get_raw_page('lab.ex_chain', 0))
), chain AS (
    SELECT 1 AS step, i.* FROM items i WHERE i.lp = 1
    UNION ALL
    SELECT c.step + 1, i.*
    FROM chain c
    JOIN items i
      ON i.lp = CASE WHEN c.lp_flags = 2 THEN c.lp_off
                     ELSE (c.t_ctid::text::point)[1]::int END
    WHERE c.lp_flags = 2
       OR c.t_ctid <> format('(0,%s)', c.lp)::tid
)
SELECT step, lp, lp_flags, lp_off, t_xmin, t_xmax, t_ctid, hot_updated, heap_only
FROM chain ORDER BY step;

SELECT lp, lp_flags, lp_off FROM heap_page_items(get_raw_page('lab.ex_chain', 0));
```

```text
VACUUM
 step | lp | lp_flags | lp_off | t_xmin | t_xmax | t_ctid | hot_updated | heap_only 
------+----+----------+--------+--------+--------+--------+-------------+-----------
    1 |  1 |        2 |      4 |        |        |        |             | 
    2 |  4 |        1 |   8160 |  57614 |      0 | (0,4)  | f           | t
(2 rows)

 lp | lp_flags | lp_off 
----+----------+--------
  1 |        2 |      4
  2 |        0 |      0
  3 |        0 |      0
  4 |        1 |   8160
(4 rows)
```

After pruning, the chain is two steps: line pointer 1 is a **redirect** (`lp_flags = 2`) with no tuple of its own, and `lp_off = 4` names the live version. Line pointers 2 and 3 are `unused` (`lp_flags = 0`), free for new tuples; their space was reclaimed. The index still points to `(0,1)`, and following the redirect leads to the live version, which is why HOT pruning never needs to touch indexes. Line pointer 1 can never be reused while the row exists, because the index needs it: a redirect costs 4 bytes, forever.
