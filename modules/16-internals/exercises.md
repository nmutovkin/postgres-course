# Module 16: Exercises

<!-- nav:start -->
> [Theory](README.md) · **Exercises** · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 15 Security and Row-Level Security](../15-security-rls/README.md) · [Course home](../../README.md) · [17 Configuration, Maintenance and Monitoring →](../17-operations/README.md)
<!-- nav:end -->

**Starting state.** Unless an exercise says otherwise:

```bash
./project/bin/reset.sh 16
./project/bin/psql.sh
```

then create the extensions and the `lab` schema from the theory's [Before you start](README.md#module-16-internals-deep-dive) section. The exercises create their own tables in `lab` (prefix `ex`) and never change Bazaar's data permanently. Exercises that need two sessions say so; open a second terminal with `./project/bin/psql.sh`.

Some values (transaction IDs, OIDs, LSNs, sample-based statistics) differ on every machine. The self-checks rely only on values that don't.

Solutions: [solutions/README.md](solutions/README.md).

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

Write one query that lists every relation that stores part of `products`: the table itself, its TOAST table, the TOAST table's index, and every index on `products`. For each, show the kind (table, TOAST table, index), the file path relative to the data directory, and its size on disk counting **all forks**.

**Self-check:** 11 rows. The sum of your size column equals `pg_total_relation_size('products')` exactly.

<details><summary>Hint</summary>

`pg_class.reltoastrelid` leads to the TOAST table; the TOAST table's own index is in `pg_index` like any other (`indrelid` = the TOAST table). Sum `pg_relation_size(oid, fork)` over the forks `main`, `fsm`, `vm` (and `init`, which is 0 here).

</details>

---

## Exercise 2 ★: Page arithmetic

Predict, **before running anything**, how many rows fit on one 8 KB page of

```sql
CREATE TABLE lab.ex_pairs (a bigint, b boolean);
```

(a) when `b` is never NULL, and (b) when `b` is always NULL. Then verify both predictions: insert 1,000 rows of each kind into two such tables and find the number of rows on page 0.

**Self-check:** (a) 185 and (b) 226. Your verification query must return these numbers, and your prediction must explain them byte by byte.

<details><summary>Hint</summary>

Tuple = 23-byte header (+ null bitmap if any column is NULL), padded to 8; then the data with its alignment; then the whole tuple is padded to a multiple of 8. Each tuple also needs a 4-byte line pointer, and the page header takes 24 bytes. `(ctid::text::point)[0]` extracts the page number from a `ctid`.

</details>

---

## Exercise 3 ★: The tuple behind order 250000

Find the `ctid` of the order with `id = 250000`. Then, using only `pageinspect` on that page (no `SELECT ... FROM orders`), show its line pointer, `t_xmin`, `t_xmax`, `t_ctid`, and the names of its infomask flags. Explain each flag you see.

**Self-check:** `t_xmin` from `pageinspect` equals the `xmin` system column of the order, `t_xmax` is 0, and `t_ctid` equals the `ctid` you started from.

<details><summary>Hint</summary>

`get_raw_page('orders', <block>)` and `heap_page_items(...) WHERE lp = <item>`; `heap_tuple_infomask_flags(t_infomask, t_infomask2)` names the flags.

</details>

---

## Exercise 4 ★★: Does the events column order cost disk?

`events` has the columns `id bigint, occurred_at timestamptz, user_id bigint, session_id uuid, event_type text, product_id bigint, payload jsonb` in that order. Work out where alignment padding occurs, propose a better order, and **measure** how many bytes the better order would save over all events, taking into account that every tuple is padded to a multiple of 8 bytes. Is it worth rewriting a 4.98-million-row table for that? Name a schema change that would save more.

**Self-check:** with the order `id, occurred_at, user_id, product_id, session_id, event_type, payload`, the saving is between 14 and 17 MB (about 3% of the table data).

<details><summary>Hint</summary>

`pg_column_size(e.*)` is the size of a row as stored; `pg_column_size(ROW(e.id, e.occurred_at, ...))` is the size of the same values in another order. Round each up to a multiple of 8 before summing (`ceil(x / 8.0) * 8`). Which column follows a variable-length one and needs 8-byte alignment?

</details>

---

## Exercise 5 ★★: The index that killed HOT

Show, with numbers, how a single new index can turn every `inventory` update into a non-HOT update:

1. Rewrite `inventory` with `fillfactor = 90` (so there is room on every page), reset its statistics counters with `pg_stat_reset_single_table_counters`, run 1,000 single-row updates of `reserved` (each its own transaction), and measure the HOT percentage.
2. Create an index on `inventory (updated_at)`, reset the counters, run the same 1,000 updates again, and measure.
3. Drop the index and put `fillfactor` back to its default.

Explain the second result. (Hint: what does the `inventory_set_updated_at` trigger from Module 09 do?)

**Self-check:** run 1 has 100% HOT updates, run 2 has 0%.

<details><summary>Hint</summary>

A `DO` block can `COMMIT` between updates when it runs outside an explicit transaction. Use `pg_stat_force_next_flush()` before reading `pg_stat_user_tables`. `ALTER TABLE ... SET (fillfactor = 90)` followed by `VACUUM FULL inventory` applies the fillfactor to all pages.

</details>

---

## Exercise 6 ★★: Visibility on paper

A transaction in `REPEATABLE READ` took the snapshot `1000:1010:1003,1007`. It reads a page with these seven tuples. The commit log says: XIDs 990, 995, 1001 and 1005 committed, 1003 and 1007 committed *after* the snapshot was taken, 998 aborted, 1012 is still running.

| tuple | `xmin` | `xmax` |
|---|---|---|
| A | 990 | 0 |
| B | 995 | 1001 |
| C | 990 | 1003 |
| D | 1005 | 0 |
| E | 1007 | 0 |
| F | 998 | 0 |
| G | 1001 | 1012 |

Decide for each tuple whether the transaction sees it, and say which rule decides. Then check the snapshot part of your reasoning with `pg_visible_in_snapshot()`.

**Self-check:** exactly 4 of the 7 tuples are visible.

<details><summary>Hint</summary>

`pg_visible_in_snapshot('1003'::xid8, '1000:1010:1003,1007'::pg_snapshot)` tells you whether the *effects* of XID 1003 are visible to the snapshot (it assumes the XID committed). A tuple is visible if its inserter is visible and its deleter (if any) is not.

</details>

---

## Exercise 7 ★★: Find the TOAST threshold

Create `lab.ex_toast (id int, body text)` and find the **smallest** length `n` of an incompressible `body` for which the value is stored out of line. Generate incompressible text with `left((SELECT string_agg(md5(g::text), '') FROM generate_series(1, 200) AS g), n)`. Explain the number you find from the layout of the row.

**Self-check:** `n` is between 1990 and 2010, and for `n - 1` the value is stored inline.

<details><summary>Hint</summary>

Insert one row per candidate length (`generate_series(1950, 2050)`), then check which ones produced chunks in the TOAST table (`chunk_id` = the value's `pg_column_toast_chunk_id(body)`, new in PG 17, which returns NULL for inline values). The row must stay under 2032 bytes: header + `id` + the value's 4-byte varlena header + data.

</details>

---

## Exercise 8 ★★: What an index costs in WAL

Create three empty copies of `inventory`'s structure: `lab.ex_inv0` without any index, `lab.ex_inv1` with a primary key, and `lab.ex_inv3` with a primary key plus indexes on `quantity` and `updated_at`. Insert the same 50,000 rows from `inventory` into each and measure the WAL each insert generated. Express the cost of the indexes as a multiple of the no-index WAL volume.

**Self-check:** `ex_inv1` generates roughly twice the WAL of `ex_inv0`, and `ex_inv3` roughly 3.5 to 5 times.

<details><summary>Hint</summary>

`CREATE TABLE ... (LIKE inventory INCLUDING DEFAULTS)` copies the columns without indexes. Capture `pg_current_wal_lsn()` before and after each `INSERT` with `\gset`, and subtract the LSNs.

</details>

---

## Exercise 9 ★★: Make a multixact

Using two sessions, make two transactions hold a `FOR SHARE` lock on the same `inventory` row at the same time. While both are open, inspect the row with `pageinspect` and list the members of the multixact with `pg_get_multixact_members()`. What happens to `xmax` when one of the two commits?

**Self-check:** `raw_flags` contains `HEAP_XMAX_IS_MULTI`, and `pg_get_multixact_members(t_xmax)` returns two rows with mode `sh`, whose XIDs are the two sessions' `pg_current_xact_id()`.

<details><summary>Hint</summary>

A transaction that only reads gets no XID: call `pg_current_xact_id()` in each session first, both to see it and to make sure the lock is recorded under that XID. `pg_get_multixact_members` takes an `xid`; `t_xmax` from `heap_page_items` already is one.

</details>

---

## Exercise 10 ★★: Freeze a table and unfreeze one page

Copy 10,000 rows of `orders` into `lab.ex_frozen` (with `CREATE TABLE ... AS`). Then:

1. Show `age(relfrozenxid)` of the table and the visibility map summary.
2. Run `VACUUM (FREEZE)` on it, and show both again.
3. Update a single row and show the visibility map summary once more.

Explain every change.

**Self-check:** after step 2, `all_frozen` equals the table's number of pages (which may be smaller than in step 1: explain why) and `age(relfrozenxid)` is 0 or 1 on an otherwise idle server; after step 3, `all_frozen` and `all_visible` are both **two** pages lower.

<details><summary>Hint</summary>

`pg_visibility_map_summary(regclass)` returns `all_visible` and `all_frozen` page counts; `pg_relation_size(...) / 8192` gives the number of pages. Think about which page(s) the update touched, and where a new row version goes when its page is full.

</details>

---

## Exercise 11 ★★★: Estimate a conjunction by hand

For

```sql
SELECT * FROM orders WHERE status = 'cancelled' AND total < 50;
```

compute the planner's row estimate yourself from `pg_stats` (`status`'s MCV list, `total`'s MCVs and histogram) and `pg_class.reltuples`, assuming, as the planner does, that the two conditions are independent. Compare your number with `EXPLAIN` and with the actual count. Is the independence assumption good for this pair of columns? What would you do if it weren't?

**Self-check:** your estimate matches `EXPLAIN`'s `rows=` within 1%. The actual count is 3,973.

<details><summary>Hint</summary>

Selectivity of the conjunction = product of the two selectivities. For `total < 50`, reuse the calculation from section 13.3 of the theory. To check independence, compare the share of cancelled orders among cheap orders with the share among all orders.

</details>

---

## Exercise 12 ★★★: Bloat with and without VACUUM

Create `lab.ex_churn (id int PRIMARY KEY, qty int, pad text)` with `autovacuum_enabled = off`, fill it with 50,000 rows (`pad` = 50 characters), and record its size. Then run 10 rounds of "update every row once" in two variants:

- (a) without any VACUUM between rounds;
- (b) with a plain `VACUUM` after each round (recreate the table first).

Record the heap size after each round in a results table, and explain the shape of both curves. Why does variant (b) stabilize at about twice the original size rather than at the original size?

**Self-check:** in variant (a), the size after round 10 is about 11 times the original; in variant (b), every size from round 1 on is about 2 times the original.

<details><summary>Hint</summary>

A `DO` block can loop over rounds, run the `UPDATE`, optionally `VACUUM`... except that `VACUUM` can't run inside a function or `DO` block. Use psql instead: put one round in a variable and repeat it with `\gexec`, or simply write the 10 rounds out. Record sizes with `INSERT INTO lab.ex_results SELECT round, pg_relation_size('lab.ex_churn')`.

</details>

---

## Exercise 13 ★★★: Walk a HOT chain

In `lab.ex_chain (id int PRIMARY KEY, v int) WITH (fillfactor = 50)`, insert one row and then update it three times **in one transaction**, so that no version can be pruned yet. Before committing, write a recursive CTE over `heap_page_items(get_raw_page('lab.ex_chain', 0))` that starts at the row's root line pointer (the one the index points to) and follows `t_ctid` from version to version, printing for each step the line pointer, `t_xmin`, `t_xmax`, and whether the version is `HEAP_HOT_UPDATED` and/or `HEAP_ONLY_TUPLE`. Then commit, run `SELECT * FROM lab.ex_chain WHERE id = 1`, and look at the page again: was it pruned? Explain why or why not. Finally run a plain `VACUUM` on the table and run your CTE again. What changed, and why does the index still find the row?

**Self-check:** before the commit, the chain has 4 steps (line pointers 1 → 2 → 3 → 4), and the first three are `HEAP_HOT_UPDATED`, the last three `HEAP_ONLY_TUPLE`. After pruning, line pointer 1 is a redirect (`lp_flags = 2`) whose `lp_off` is 4.

<details><summary>Hint</summary>

The chain ends at the version whose `t_ctid` points to itself. In the recursive step, join on `t_ctid = ('(0,' || lp || ')')::tid`. Within one transaction, the `cmin`/`cmax` fields let the transaction see its own latest version. Pruning can only happen once the transaction has committed. Opportunistic pruning during a read only kicks in when the page is getting full: when its free space has dropped below the larger of the fillfactor reserve and 10% of the page. A redirect has no tuple header (the `t_*` columns are NULL), so handle it in the CTE: follow `lp_off` instead.

</details>
