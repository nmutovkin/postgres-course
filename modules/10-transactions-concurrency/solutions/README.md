# Module 10: Solutions

<!-- nav:start -->
> [Theory](../README.md) · [Exercises](../exercises.md) · [Project](../project.md) · **Solutions**  
> [← 09 Views, Functions and Triggers](../../09-views-functions-triggers/README.md) · [Course home](../../../README.md) · [11 Indexes →](../../11-indexes/README.md)
<!-- nav:end -->

All solutions were run on PostgreSQL 17 against `./project/bin/reset.sh 11` (state 10 plus the module 10 reference). Two-session solutions were run with real concurrent psql sessions ([`examples/sessions.py`](../examples/sessions.py)); transaction ids and process ids will differ on your machine.

Files in this directory:

| File | What it is |
|---|---|
| [`cancel_order.sql`](cancel_order.sql) | Milestone extension: reference `cancel_order()` |
| [`acceptance_functional.sql`](acceptance_functional.sql), [`acceptance_cancel.sql`](acceptance_cancel.sql) | Milestone single-session checks |
| [`bench_setup.sql`](bench_setup.sql), [`pgbench_place_order.sql`](pgbench_place_order.sql), [`pgbench_queue_worker.sql`](pgbench_queue_worker.sql), [`pgbench_cancel_orders.sql`](pgbench_cancel_orders.sql), [`bench_check.sql`](bench_check.sql), [`bench_cancel_check.sql`](bench_cancel_check.sql) | Milestone concurrency tests (see [`project.md`](../project.md)) |
| [`place_order_naive.sql`](place_order_naive.sql) | A deliberately deadlock-prone `place_order()`, to check that the test can fail |
| [`exercises/`](exercises/) | Runnable solutions for exercises 4, 5, 6, 10, 11, 12, 13 |

---

<!-- toc:start -->
**Contents**

- [Milestone notes](#milestone-notes)
- [Exercise 1 ★: Savepoint bookkeeping](#exercise-1--savepoint-bookkeeping)
- [Exercise 2 ★: Row-version forensics](#exercise-2--row-version-forensics)
- [Exercise 3 ★: When is the snapshot taken?](#exercise-3--when-is-the-snapshot-taken)
- [Exercise 4 ★★: Fix a lost-update bug](#exercise-4--fix-a-lost-update-bug)
- [Exercise 5 ★★: Optimistic locking for product edits](#exercise-5--optimistic-locking-for-product-edits)
- [Exercise 6 ★★★: A deadlock-free stock transfer](#exercise-6--a-deadlock-free-stock-transfer)
- [Exercise 7 ★★: Diagnose a blocking chain](#exercise-7--diagnose-a-blocking-chain)
- [Exercise 8 ★★: Write skew on the support rota](#exercise-8--write-skew-on-the-support-rota)
- [Exercise 9 ★★: Predict the outcome](#exercise-9--predict-the-outcome)
- [Exercise 10 ★★: Ship orders with `SKIP LOCKED`](#exercise-10--ship-orders-with-skip-locked)
- [Exercise 11 ★★: A singleton report refresh](#exercise-11--a-singleton-report-refresh)
- [Exercise 12 ★★★: A migration that cannot take the site down](#exercise-12--a-migration-that-cannot-take-the-site-down)
- [Exercise 13 ★★★: Idempotent checkout](#exercise-13--idempotent-checkout)

<!-- toc:end -->

## Milestone notes

The reference `place_order()` is discussed line by line in section 16 of the theory. A few design choices deserve a second look when you compare it with yours:

- **Validate before locking.** User, address and JSON checks take no locks, so a malformed request never makes anyone wait.
- **One locking query, sorted.** All inventory rows are locked by one `SELECT ... ORDER BY i.product_id FOR UPDATE OF i`. A loop that runs one `SELECT ... FOR UPDATE` per product in a *sorted* array is equally correct; a loop over the cart *as sent by the client* is not (that is `place_order_naive.sql`, which produced 71 deadlocks in 400 checkouts).
- **Duplicates are summed before the check.** Otherwise a cart with two lines of 3 units checks "3 available?" twice and reserves 6.
- **No error handling inside.** Catching errors in the function would leave the caller's transaction committed with a half-done checkout. Errors propagate; the transaction rolls back.
- **Alternatives.** An atomic `UPDATE inventory ... WHERE quantity - reserved >= wanted` per product (in sorted order) would also work and needs no explicit `FOR UPDATE`, but you then have to detect *which* product failed from the row count, and the `BZ002` (inactive product) check needs a separate read. The single locking query keeps check and reservation obviously under one lock.

`cancel_order()` locks the **order row first**, then the inventory rows in `product_id` order. The order lock is what prevents a double release; here are two concurrent cancels of order 116 (product 4 had `reserved = 3`, product 17 had 2; the order holds 2 of each):

| Step | Session A | Session B |
|---|---|---|
| 1 | `BEGIN;` | |
| 2 | `SELECT cancel_order(116);` → `t` | |
| 3 | | `SELECT cancel_order(116);` → **(waits on the order row)** |
| 4 | `COMMIT;` | → `f` |

Afterwards product 4 has `reserved = 1` and product 17 has `0`: released exactly once. Without the `FOR NO KEY UPDATE` on the order, both sessions would read status `pending`, both would subtract, and product 17 would hit the `reserved >= 0` check (or, with more stock, silently go wrong). An alternative is an atomic state transition, `UPDATE orders SET status = 'cancelled' WHERE id = $1 AND status IN ('pending', 'paid') RETURNING id`, and releasing stock only if a row came back. That is just as safe, and one statement shorter, but it cannot distinguish "already cancelled" from "shipped" without a second read.

The lock order "orders before inventory" is shared with exercise 10's `ship_next_order()`, and `place_order()` never locks existing orders, so none of the three can deadlock with another.

---

## Exercise 1 ★: Savepoint bookkeeping

**Original script:** 6 → **35**, 7 → **15**, 8 → **9**, 9 → **20**.

Walk through it:

1. Product 6 +5 (before any savepoint): kept.
2. `SAVEPOINT a`, product 7 +5, `SAVEPOINT b`.
3. Product 8 −100 fails the `CHECK`: the transaction is now aborted.
4. Product 9 +5 is **not executed** at all: `current transaction is aborted`.
5. `ROLLBACK TO SAVEPOINT a` undoes everything after `a` (product 7's +5) and clears the aborted state.
6. Product 8 +1: 9. `RELEASE SAVEPOINT a` keeps it.
7. `SAVEPOINT c`, the reservation on 9, `ROLLBACK TO c` undoes it.
8. `COMMIT` commits 6 = 35 and 8 = 9.

**Variant (b)**, `ROLLBACK TO SAVEPOINT b`: rolling back to `b` also clears the aborted state but keeps product 7's +5 (it happened before `b`). Result: **35, 20, 9, 20**. Product 9 is still 20: its `UPDATE` never ran.

**Variant (c)**, `ROLLBACK TO SAVEPOINT a` after `RELEASE SAVEPOINT a`: the savepoint no longer exists, so this fails with `ERROR:  savepoint "a" does not exist`, which aborts the transaction, and `COMMIT` becomes `ROLLBACK`. **Nothing changes** (30, 15, 8, 20). A released savepoint is gone for good; that is the variant that "changes nothing".

The lesson: in an aborted transaction, statements are skipped, not queued; rolling back to a savepoint is the only way back, and only to one that still exists.

---

## Exercise 2 ★: Row-version forensics

```sql
BEGIN;
SELECT pg_current_xact_id() AS my_xid \gset
SELECT * FROM place_order(13, '[{"product_id": 20, "quantity": 2}, {"product_id": 44, "quantity": 1}]');
COMMIT;

SELECT 'inventory' AS tbl, count(*) FROM inventory WHERE xmin = :'my_xid'::xid8::xid
UNION ALL SELECT 'orders',      count(*) FROM orders      WHERE xmin = :'my_xid'::xid8::xid
UNION ALL SELECT 'order_items', count(*) FROM order_items WHERE xmin = :'my_xid'::xid8::xid
UNION ALL SELECT 'audit_log',   count(*) FROM audit_log   WHERE xmin = :'my_xid'::xid8::xid
UNION ALL SELECT 'products',    count(*) FROM products    WHERE xmin = :'my_xid'::xid8::xid;
```

```text
     tbl     | count
-------------+-------
 inventory   |     2
 orders      |     2
 order_items |     2
 audit_log   |     4
 products    |     0
(5 rows)
```

(The function returned orders 121 and 122, for sellers 2 and 3.)

- **inventory 2:** the reservation `UPDATE` wrote a new version of the rows of products 20 and 44.
- **order_items 2:** one item per order.
- **orders 2, not 4:** each order was inserted (version 1, `total` 0.00) and then updated by the total trigger (version 2). Both versions have your `xmin`, but version 1 also has your `xmax`: it was replaced by your own transaction, so it is invisible to everyone, including you. A query sees only the current version of each row. The dead versions are still on disk until `VACUUM` (module 16).
- **audit_log 4:** the audit trigger logged the two `INSERT`s and the two `UPDATE`s of `orders` (`total` changed from 0.00 to 25.00 and 24.00):

  ```text
   operation |   row_pk    | total
  -----------+-------------+-------
   INSERT    | {"id": 121} | 0.00
   UPDATE    | {"id": 121} | 25.00
   INSERT    | {"id": 122} | 0.00
   UPDATE    | {"id": 122} | 24.00
  ```

  The inventory updates are not audited (no audit trigger on `inventory`).
- **products 0 by `xmin`**, but:

  ```text
   xmin | xmax
  ------+------
   7545 | 7548
   7545 | 7548
  ```

  `xmax` of products 20 and 44 is your transaction id. Inserting an `order_items` row runs the foreign key check, which takes a `FOR KEY SHARE` lock on the referenced product, and row locks are recorded in `xmax` with a "lock only" flag (section 10.2). The rows were locked, not deleted. After your commit, the lock is meaningless and the next writer simply overwrites `xmax`.

An alternative to `xmin` comparisons: `pg_xact_commit_timestamp(xmin)` tells you *when* a row version was committed, but only with `track_commit_timestamp = on` (off by default).

---

## Exercise 3 ★: When is the snapshot taken?

| Step | Value | Why |
|---|---|---|
| 3 | **40** | `BEGIN` takes no snapshot. The first `SELECT` does, after B's first +10. |
| 5 | **40** | Same snapshot for the whole `REPEATABLE READ` transaction. |
| 10 | **50** | The snapshot was taken by `SELECT now() ...` at step 8, which reads no table at all. Any statement takes it. B's step 9 (+10 → 60) is invisible. |
| 13 | **60** | `READ COMMITTED`: a fresh snapshot per statement. |
| 15 | **70** | Fresh snapshot again: sees step 14. |
| 16 | **70, 70, 70** | One statement, one snapshot: the outer query and both subqueries see 70, even though B committed +10 (→ 80) while the statement was sleeping. |

Sum of the five single values: 40 + 40 + 50 + 60 + 70 = 260.

The practical lesson of step 10: if you want a report's snapshot to start "now", run the first query right away; any statement, even `SELECT 1`, pins it. Conversely, `BEGIN ISOLATION LEVEL REPEATABLE READ` followed by a minute of application work and then the first query gives you a snapshot from a minute later than you think.

---

## Exercise 4 ★★: Fix a lost-update bug

Measured (product 57 set to 30, 8 clients × 100 calls of `restock(57, 1)`):

| Version | Final quantity (expected 830) | tps |
|---|---|---|
| buggy | 136 and 137 (two runs) | 1,444 / 1,327 |
| fix 1: atomic `UPDATE` | **830** | 984 |
| fix 2: `SELECT ... FOR NO KEY UPDATE` | **830** | 311 |

About 694 of 800 restocks were lost. How one gets lost:

| Step | Session A `restock(57, 1)` | Session B `restock(57, 1)` |
|---|---|---|
| 1 | `SELECT quantity` → 30 | |
| 2 | | `SELECT quantity` → 30 |
| 3 | `UPDATE ... SET quantity = 31` (30 + 1) | |
| 4 | commit | |
| 5 | | `UPDATE ... SET quantity = 31` (30 + 1): A's unit is lost |

The `pg_sleep(0.001)` only widens the window; without it the bug still happens, just less often (which is worse: it survives testing).

**Fix 1** ([`ex04_restock_fixed.sql`](exercises/ex04_restock_fixed.sql), `restock`): `UPDATE inventory SET quantity = quantity + p_units ... RETURNING quantity`. The addition is evaluated on the newest committed version of the row while it is locked (EvalPlanQual), so increments compose. No read, no window. The "warehouse call" now happens *before* the update, outside any lock.

**Fix 2** (`restock_locking`): `SELECT ... FOR NO KEY UPDATE` before reading. The second caller waits at the `SELECT` and then reads the fresh value. It is correct, but three times slower here because the row lock is held across the 1 ms "warehouse call": all 8 clients queue behind it, one at a time. Fix 1 holds the lock only for the instant of the `UPDATE`. That is the general trade-off: pessimistic locking serializes everything inside the locked window, so keep that window tiny.

A third fix: run the original function under `REPEATABLE READ` and retry on `40001` (`pgbench --max-tries=...` can do it). Correct, but many transactions would be retried under this much contention on a single row.

---

## Exercise 5 ★★: Optimistic locking for product edits

Solution: [`exercises/ex05_optimistic.sql`](exercises/ex05_optimistic.sql). The heart of it:

```sql
UPDATE products
SET title = p_title,
    price = p_price
WHERE id = p_id
  AND version = p_expected_version
RETURNING version INTO v_new_version;
```

and a `BEFORE UPDATE` trigger that sets `NEW.version := OLD.version + 1`. If no row matched, the function reads the current version to decide between `P0002` (no such product) and `BZ006` (stale version).

The demo (product 40 starts at version 1):

| Step | Session A | Session B |
|---|---|---|
| 1 | load: version 1 | |
| 2 | | load: version 1 |
| 3 | `SELECT update_product(40, 1, 'Chef''s Knife 20cm (Damascus)', 99.00);` → `2` | |
| 4 | | `SELECT update_product(40, 1, 'Chef''s Knife 20cm', 79.00);` → error below |

```text
ERROR:  product 40 was changed by someone else
DETAIL:  your version 1, current version 2
HINT:  Reload the product and apply your change again.
```

**Question 4** (B saves while A's save is uncommitted): we ran it with both editors at version 2. A, in an open transaction, saved (version 3, not committed). B's `UPDATE ... WHERE version = 2` found the row at committed version 2 in its snapshot, but the row was locked by A, so B **waited**. When A committed, EvalPlanQual re-checked `version = 2` against the new version 3, the row no longer matched, `UPDATE 0`, and B got `BZ006` with `DETAIL: your version 2, current version 3`. If A had rolled back, B's save would have succeeded. So the version check stays correct even with overlapping transactions under `READ COMMITTED`.

A plain `UPDATE products SET price = price WHERE id = 40` also bumped the version (to 4 in our run): the trigger fires on every update, so even scripts and psql sessions that bypass `update_product()` invalidate stale forms. That is the point of a trigger rather than `SET version = version + 1` in the function only.

**Question 5:** a counter is exact. `updated_at` is set to `now()`, the *transaction start time*: two updates in one transaction get the same value, an update in a transaction that started earlier can even write an *older* timestamp than a concurrent one, and the value depends on clocks. A counter changes on every update, by exactly one. (The audit log also gets a clean version history for free.)

Alternatives: the hidden `xmin` column as the token (no schema change, but opaque and it changes on no-op updates too); or `SELECT ... FOR UPDATE NOWAIT` when the form is opened, which is pessimistic locking across user think-time and should be avoided (it holds a transaction open for minutes).

---

## Exercise 6 ★★★: A deadlock-free stock transfer

Solution: [`exercises/ex06_transfer_stock.sql`](exercises/ex06_transfer_stock.sql). The essential part:

```sql
FOR r IN
    SELECT i.product_id, i.quantity, i.reserved
    FROM inventory i
    WHERE i.product_id IN (p_from, p_to)
    ORDER BY i.product_id
    FOR NO KEY UPDATE
LOOP
    -- count the rows; BZ001 if r is p_from and has too little available
END LOOP;
-- BZ002 if fewer than 2 rows, 22023 if different sellers
UPDATE inventory SET quantity = quantity - p_units WHERE product_id = p_from;
UPDATE inventory SET quantity = quantity + p_units WHERE product_id = p_to;
```

`transfer(1, 2)` and `transfer(2, 1)` both lock product 1 first, so two opposite transfers queue at product 1 instead of each holding one row and waiting for the other. `FOR NO KEY UPDATE` is enough because only `quantity` changes, and it doesn't block foreign key checks from concurrent checkouts. The availability check uses `quantity - reserved`, so a transfer can never take units that are reserved for pending orders (the `CHECK (reserved <= quantity)` would reject it anyway, but with a less helpful error).

Measured, products 1 and 2 at 1,000 units each, random directions, one unit per transfer:

| Implementation | Clients × transactions | Processed | Failed (deadlocks) | tps | Sum afterwards |
|---|---|---|---|---|---|
| `transfer_stock` (sorted locks) | 8 × 200 | 1600 | 0 | 1,579 | 2000 |
| naive (subtract, then add) | 8 × 200 | 205 | 1395 | 0.30 | 2000 |
| naive (subtract, then add) | 8 × 10 | 9 | 71 | 0.22 | 2000 |

`pg_stat_database.deadlocks` rose by exactly the number of failed transactions in the naive runs and not at all with the sorted version. The naive version still conserves the total, because a deadlocked transaction is rolled back as a whole (atomicity), but almost nothing gets done, and each deadlock costs at least `deadlock_timeout` (1 s) of waiting while holding locks. That is why the throughput collapses by four orders of magnitude.

Alternative: lock with one statement, `SELECT ... WHERE product_id IN (...) ORDER BY product_id FOR NO KEY UPDATE` without the loop, then read both rows into variables; or call `pg_advisory_xact_lock` on the smaller id first (more complicated, no benefit).

---

## Exercise 7 ★★: Diagnose a blocking chain

1. **The graph:**

   ```text
   2923 admin-tool (idle in transaction, holds row lock on inventory product 18 + RowExclusive on inventory)
    ├── 2948 checkout (waits for 2923's transaction: wants product 18's row)
    └── 2964 ALTER TABLE inventory (wants ACCESS EXCLUSIVE; blocked by 2923 and 2948, which hold table locks)
         ├── 2979 product page SELECT (queued behind the ALTER)
         └── 2994 checkout for product 44 (queued behind the ALTER)
   ```

   The root is **2923**, an admin tool that ran `place_order()` in a transaction eight seconds ago and never committed. It is idle: it does nothing, it just holds its locks.

2. **2979 only reads** through the `active_products` view, which joins `inventory`. It needs `ACCESS SHARE` on `inventory`, compatible with every lock that is actually *granted*. But it is queued behind 2964's ungranted `ACCESS EXCLUSIVE` request, and a new request must wait for conflicting requests ahead of it in the queue (section 11.4). `pg_blocking_pids` says so: `{2964}`.

3. **2994** wants product 44, which nobody holds: 2948 also wants product 44, but locks in `product_id` order and is still waiting at product 18, so it holds no lock on 44 yet (its granted `tuple` lock is on item 18 of page 0, the row of product 18 it is queued for). 2994 waits only because `place_order()`'s locking `SELECT` needs `ROW SHARE` on `inventory`, and that request is also queued behind the `ALTER TABLE`.

4. **First: cancel the migration**, `SELECT pg_cancel_backend(2964);`. Its statement fails (`canceling statement due to user request`), the `ACCESS EXCLUSIVE` request leaves the queue, and 2979 and 2994 proceed immediately (we got the product page and order 122 at once). The pages are back. 2948 is still waiting for 2923, but only that one checkout. **Second: deal with the root**: find out what `admin-tool` is (`application_name`, `usename`, `client_addr`), then `SELECT pg_terminate_backend(2923);`. `pg_cancel_backend` would not help: an idle session has no statement to cancel. Terminating it rolls back its order 121 and releases product 18, and 2948 completes (orders 123 and 124 in our run). Finally, re-run the migration with a `lock_timeout`.

   Terminating 2923 *first* would also unblock everything, but less directly: 2948 would run, then the `ALTER` would get its lock and run (a short exclusive window), then the readers. And it is the more drastic action (you roll back someone's work), which deserves a check of who it is.

5. **`lock_timeout`** on the migration role (or in the migration itself), so the `ALTER` gives up after a second or two instead of queueing everyone behind it; and **`idle_in_transaction_session_timeout`** on the application roles (admin tool included), so a forgotten transaction is ended automatically. `transaction_timeout` (PostgreSQL 17) is a stricter variant of the latter.

---

## Exercise 8 ★★: Write skew on the support rota

**1. `REPEATABLE READ`:**

| Step | Session A (amara) | Session B (bruno) |
|---|---|---|
| 1 | `BEGIN ISOLATION LEVEL REPEATABLE READ;` | |
| 2 | `SELECT count(*) FROM lab.support_rota WHERE on_call;` → 2 | |
| 3 | | `BEGIN ISOLATION LEVEL REPEATABLE READ;` |
| 4 | | the same count → 2 |
| 5 | `UPDATE lab.support_rota SET on_call = false WHERE admin = 'amara';` | |
| 6 | | `UPDATE lab.support_rota SET on_call = false WHERE admin = 'bruno';` |
| 7 | `COMMIT;` | `COMMIT;` |

Both commit; `SELECT admin FROM lab.support_rota WHERE on_call` returns **0 rows**. Each transaction updated a different row, so there is no write-write conflict to detect.

**2. `SERIALIZABLE`:** the same steps; A commits, and B fails **at `COMMIT`** with `40001`:

```text
ERROR:  could not serialize access due to read/write dependencies among transactions
DETAIL:  Reason code: Canceled on identification as a pivot, during commit attempt.
HINT:  The transaction might succeed if retried.
```

Bruno is still on call (1 row). When B retries, its count returns 1, so the button refuses. (Which session fails depends on timing; with these steps it is the one committing second.)

**3. `READ COMMITTED` fix:** lock the rows you base the decision on, before counting:

```sql
BEGIN;
SELECT admin FROM lab.support_rota WHERE on_call ORDER BY admin FOR UPDATE;
-- if fewer than 2 rows: refuse (ROLLBACK)
UPDATE lab.support_rota SET on_call = false WHERE admin = 'amara';
COMMIT;
```

| Step | Session A (amara) | Session B (bruno) |
|---|---|---|
| 1 | `BEGIN;` | |
| 2 | the locking `SELECT` → amara, bruno | |
| 3 | | `BEGIN;` |
| 4 | | the locking `SELECT` → **(waits)** |
| 5 | `UPDATE ... 'amara'`; `COMMIT;` | → **bruno only** (1 row): refuse, `ROLLBACK;` |

B waited at amara's row; after A committed, EvalPlanQual re-checked `on_call` on amara's new version, found it false and dropped the row. B saw one on-call admin and refused. 1 admin remains on call. The `ORDER BY` keeps lock order consistent if several admins press the button.

**4. Trade-offs.** The explicit lock needs no retry loop and no change of isolation level, and it is very visible in the code. But it only protects code paths that remember to take it, and it does not cover *new* rows (if "chen goes on call" and "amara goes off call" race, the locked set doesn't include chen's row; harmless for this rule, fatal for others, such as "at most N"). `SERIALIZABLE` protects every transaction that runs at that level, including ones written next year by someone who never heard of this rule, at the cost of retries and some overhead.

---

## Exercise 9 ★★: Predict the outcome

**(a) Changing a key column: B waits.** `sku` is part of the unique constraint `(seller_id, sku)`, which a foreign key *could* reference, so A's `UPDATE` takes `FOR UPDATE` instead of `FOR NO KEY UPDATE` (`pgrowlocks` showed mode `{Update}`). B's foreign key check needs `FOR KEY SHARE`, which conflicts only with `FOR UPDATE`. B waits until A commits, then `INSERT 0 1`. Compare section 10.2, where a price update did not block the same insert. Changing a column that is in *any* unique index (not partial, not an expression) makes updates heavier for concurrency.

**(b) A snapshot from an unrelated table: `40001`.** A's snapshot was taken at `SELECT count(*) FROM orders`, before B's update of product 15. A never read `inventory`, but its snapshot covers the whole database, and the version of product 15 it would update is not the latest. `ERROR:  could not serialize access due to concurrent update`.

**(c) Updating a row that is being deleted: B waits, then `UPDATE 0`.** B found promotion 9 in its snapshot, waited for A's lock, and after A committed the delete, there is no newer version to re-check: EvalPlanQual skips the row. No error; the application must look at the row count. (Under `REPEATABLE READ`, B would get `40001` instead.)

**(d) Share, then upgrade: deadlock.** Both `FOR SHARE` locks are granted (they are compatible). A's `UPDATE` needs `FOR NO KEY UPDATE`, which conflicts with B's share lock: A waits for B. B's `UPDATE` then waits for A's share lock: a cycle. Which one is aborted with `40P01` depends on timing (README section 13.2). If B's step 5 came less than 1 s (`deadlock_timeout`) after A's step 4, A's check finds the cycle: A is aborted and B's update goes through. If you waited longer before step 5, A's check has already found nothing, and B's own check, 1 s after step 5, aborts B: A's update goes through. Either way, `reserved` ends at 1. The fix: lock with the mode you will need in the end (`FOR NO KEY UPDATE`), not a weaker one you later upgrade.

So: (b) is the serialization failure and (d) the deadlock.

---

## Exercise 10 ★★: Ship orders with `SKIP LOCKED`

Solution: [`exercises/ex10_ship_next_order.sql`](exercises/ex10_ship_next_order.sql).

```sql
UPDATE orders
SET status = 'shipped', shipped_at = now()
WHERE id = (
    SELECT o.id FROM orders o
    WHERE o.status = 'paid'
    ORDER BY o.created_at, o.id
    LIMIT 1
    FOR UPDATE SKIP LOCKED
)
RETURNING id INTO v_order_id;
```

then, if an order was claimed, lock its inventory rows in `product_id` order and subtract the item quantities from both `quantity` and `reserved`.

The run: pickers p1, p2, p3, each in an open transaction, got orders **110, 111, 112** and then **113, 114, 120** (in `created_at` order) without waiting; p4 got `NULL` immediately, because every paid order was locked by someone. After the three commits: 0 `paid` orders, 10 `shipped` (4 already existed), and 0 products where `reserved` differs from the units in `pending` orders.

Why it is correct: the claim is an atomic state transition (`paid` → `shipped`) on a row nobody else can lock at the same time; if a picker's transaction rolls back (scanner crash), the order goes back to `paid` with its stock untouched. There is no lease here because the claim and the work (the stock movement) happen in one short transaction; a real warehouse flow with minutes of picking would add a `picking` status or lease columns, like the job queue.

Lock order: order row first, inventory rows after, the same as `cancel_order()`. `place_order()` locks inventory but never an existing order, so no cycle is possible.

Alternative: `UPDATE ... FROM (SELECT ... FOR UPDATE SKIP LOCKED) c WHERE orders.id = c.id`, equivalent. Without `SKIP LOCKED`, the three pickers would queue on order 110, and after the first commits, the others would re-check `status = 'paid'`, skip it and move on, so it is still correct, but the pickers run one at a time.

---

## Exercise 11 ★★: A singleton report refresh

Solution: [`exercises/ex11_refresh_singleton.sql`](exercises/ex11_refresh_singleton.sql):

```sql
IF NOT pg_try_advisory_xact_lock(10, 1) THEN
    RETURN 'skipped';
END IF;
REFRESH MATERIALIZED VIEW CONCURRENTLY seller_sales_daily;
RETURN 'refreshed';
```

| Step | Session A | Session B |
|---|---|---|
| 1 | `BEGIN;` | |
| 2 | `SELECT refresh_sales_report();` → `refreshed` | |
| 3 | | `SELECT refresh_sales_report();` → `skipped` (instantly) |
| 4 | `COMMIT;` | |
| 5 | | `SELECT refresh_sales_report();` → `refreshed` |

While A was open, `pg_locks` showed one advisory lock (`classid 10, objid 1, objsubid 2`: the two-key form); afterwards none.

**2. Choice.** `try` because a cron job should skip, not queue (queuing is exactly the problem we are solving). Transaction-level because it is released automatically at commit or rollback, even if the function errors halfway, and because it works behind a transaction-pooling PgBouncer. A session-level lock would survive the transaction and have to be unlocked explicitly, and an error between lock and unlock would leave it held until the connection closes. The two-key form `(10, 1)` avoids `hashtext` collisions by giving each module its own namespace.

**3.** The refresh finished at step 2, yet B is told "skipped" until A commits, and A's refreshed data is not visible to others until then either. So the lock correctly covers "the refresh is not finished (committed) yet". The consequence: call the function in its own short transaction (autocommit), not inside a long application transaction; otherwise every other server skips while the long transaction runs.

Alternatives: a row in a `job_locks` table locked with `FOR UPDATE NOWAIT` (works, but needs a table and a row per job), or the job queue of section 17 with a single job per refresh.

---

## Exercise 12 ★★★: A migration that cannot take the site down

Solution: [`exercises/ex12_add_column_safely.sql`](exercises/ex12_add_column_safely.sql). The retry loop:

```sql
LOOP
    v_attempt := v_attempt + 1;
    BEGIN
        PERFORM set_config('lock_timeout', '200ms', true);
        EXECUTE format('ALTER TABLE %s ADD COLUMN %I %s', p_table, p_column, p_type);
        RETURN;
    EXCEPTION WHEN lock_not_available THEN
        IF v_attempt >= p_max_attempts THEN RAISE ...; END IF;
        PERFORM pg_sleep(0.5 * v_attempt);
    END;
END LOOP;
```

The run (A holds `ACCESS SHARE` on `products` in an open transaction; A commits after about 3 s):

```text
NOTICE:  attempt 1: products is busy, retrying in 0.5 s
NOTICE:  attempt 2: products is busy, retrying in 1.0 s
NOTICE:  attempt 3: products is busy, retrying in 1.5 s
NOTICE:  attempt 4: products is busy, retrying in 2.0 s
NOTICE:  attempt 5: column products.weight_g added
CALL
```

Meanwhile session C's `SELECT title FROM products WHERE id = 1` took 2.2 ms, **177 ms** (it arrived during an attempt and queued behind the `ALTER` for the rest of its 200 ms), and 0.5 ms. Never more than 200 ms. A second `CALL` printed `column products.weight_g already exists, nothing to do`.

Details that matter:

- The `EXCEPTION` block makes each attempt a subtransaction: a failed attempt is rolled back, which also drops its place in the lock queue, so the procedure holds nothing while it sleeps.
- `set_config(..., true)` sets `lock_timeout` for the current transaction only, so the caller's own setting is unaffected afterwards.
- `%I` quotes the column name; `p_table` is a `regclass`, whose text form is already quoted. `p_type` is inserted as-is, so only trusted migration code may call this.
- Growing back-off gives a busy period time to end; an alerting threshold (10 attempts) stops it from retrying forever.

Alternatives: a client-side loop in the migration tool (module 26 does this with Alembic), or a procedure that `COMMIT`s between attempts instead of using an exception block (procedures may commit, but not inside a block that has an `EXCEPTION` clause).

---

## Exercise 13 ★★★: Idempotent checkout

Solution: [`exercises/ex13_idempotent_checkout.sql`](exercises/ex13_idempotent_checkout.sql). The logic:

1. `INSERT INTO checkout_requests (...) VALUES (p_key, ...) ON CONFLICT (idempotency_key) DO NOTHING RETURNING *`.
2. If a row came back, this call owns the key: run `place_order()`, store the ids, return them. All in the caller's transaction.
3. Otherwise read the stored row; if its fingerprint differs from this request's (`md5` of user, cart and address), raise `BZ007`; else return the stored ids.

The bench ([`ex13_idempotent_bench.sql`](exercises/ex13_idempotent_bench.sql): 8 clients × 50 calls over keys `k1`..`k5`): 400 of 400 calls succeeded, `checkout_requests` has 5 rows, exactly 5 new orders exist, and product 18's `reserved` went from 2 to 7:

```text
 idempotency_key | order_ids
-----------------+-----------
 k1              | {123}
 k2              | {125}
 k3              | {122}
 k4              | {124}
 k5              | {121}
(5 rows)
```

Why concurrency is handled: when two calls with the same new key overlap, the second `INSERT` hits the unique index entry of the first, uncommitted row and **waits** for that transaction. If it commits, the second gets `DO NOTHING` and reads the committed row, which already holds the order ids (they were written in the same transaction). If it rolls back, the second call's insert succeeds and it places the order itself.

That last case is requirement 4: a failed first call (we used product 13, which is sold out: `BZ001`) rolls back the whole transaction, *including the key row*, so the key is not burned (`count(*)` for `'k-fail'` was 0), and a retry after a restock created order 126. Replaying with the same key and a different cart gave `BZ007: idempotency key k1 was already used for a different request`.

Notes and alternatives:

- The stored row never has `order_ids` NULL as seen by others, because the insert and the update commit together. A design that commits the key first ("in progress") and the result later needs an explicit state and a way to recover stuck "in progress" keys; keeping it in one transaction avoids that.
- Under `REPEATABLE READ`, step 3 could fail with `40001` (the committed key row is newer than the snapshot); under `READ COMMITTED` the `SELECT` after `DO NOTHING` sees it.
- Production systems expire keys after a day or so (`DELETE ... WHERE created_at < now() - interval '24 hours'`), and scope them per user (`PRIMARY KEY (user_id, idempotency_key)`) so that one client cannot probe another's keys.
