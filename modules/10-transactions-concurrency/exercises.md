# Module 10 exercises: Transactions and Concurrency

Unless an exercise says otherwise, start from the state at the end of this module's milestone:

```bash
./project/bin/reset.sh 11      # state 10 + the module 10 reference (place_order, jobs); small dataset
```

Many exercises need two or three sessions at once: open several terminals with `./project/bin/psql.sh`, or write the steps in a demo file and replay it with [`examples/sessions.py`](examples/sessions.py). Reset between exercises: most of them change data. Solutions are in [`solutions/README.md`](solutions/README.md), with runnable files in [`solutions/exercises/`](solutions/exercises/).

---

## Exercise 1 ★: Savepoint bookkeeping

Without running it, predict the final `quantity` of products 6, 7, 8 and 9 (initially 30, 15, 8 and 20) after this script, typed into psql with default settings:

```sql
BEGIN;
UPDATE inventory SET quantity = quantity + 5 WHERE product_id = 6;
SAVEPOINT a;
UPDATE inventory SET quantity = quantity + 5 WHERE product_id = 7;
SAVEPOINT b;
UPDATE inventory SET quantity = quantity - 100 WHERE product_id = 8;
UPDATE inventory SET quantity = quantity + 5 WHERE product_id = 9;
ROLLBACK TO SAVEPOINT a;
UPDATE inventory SET quantity = quantity + 1 WHERE product_id = 8;
RELEASE SAVEPOINT a;
SAVEPOINT c;
UPDATE inventory SET reserved = reserved + 1 WHERE product_id = 9;
ROLLBACK TO c;
COMMIT;
```

Then predict the results of two variants (reset in between):

- **(b)** the line `ROLLBACK TO SAVEPOINT a;` becomes `ROLLBACK TO SAVEPOINT b;`.
- **(c)** the lines from `SAVEPOINT c;` to `ROLLBACK TO c;` are replaced by a single `ROLLBACK TO SAVEPOINT a;`.

Finally, run all three and compare.

**Self-check:** the original script ends with products 6, 7, 8, 9 at 35, 15, 9, 20. One of the variants changes nothing at all.

<details><summary>Hint</summary>

Which statements run while the transaction is in the aborted state? What does `RELEASE` do to a savepoint, and what happens if you then roll back to it?

</details>

---

## Exercise 2 ★: Row-version forensics

Run one checkout and then find every row version it wrote, using only the hidden system columns:

1. In a transaction, save your transaction id with `SELECT pg_current_xact_id() AS my_xid \gset`, call `place_order(13, '[{"product_id": 20, "quantity": 2}, {"product_id": 44, "quantity": 1}]')`, and commit.
2. For each of `inventory`, `orders`, `order_items`, `audit_log` and `products`, count the rows whose `xmin` is your transaction id. (`xmin` has type `xid`; `:'my_xid'::xid8::xid` converts the saved value.)
3. Explain each count. In particular: the function inserted two orders and then the total trigger updated both of them, yet `orders` has only two rows with your `xmin`. Why not four?
4. Now look at `xmin` and `xmax` of products 20 and 44. Your transaction never updated them, yet one of those columns contains your transaction id. Which one, and why?

**Self-check:** the counts are 2, 2, 2, 4 and 0.

<details><summary>Hint</summary>

Section 4.1 explains what `xmax` can mean besides "deleted"; section 10.2 explains which lock an `order_items` insert takes on the referenced product.

</details>

---

## Exercise 3 ★: When is the snapshot taken?

Predict what session A sees at each `SELECT` (product 57 starts with `quantity = 30`; every step of B is an autocommitted `UPDATE inventory SET quantity = quantity + 10 WHERE product_id = 57;`):

| Step | Session A | Session B |
|---|---|---|
| 1 | `BEGIN ISOLATION LEVEL REPEATABLE READ;` | |
| 2 | | +10 |
| 3 | `SELECT quantity FROM inventory WHERE product_id = 57;` → **?** | |
| 4 | | +10 |
| 5 | `SELECT quantity FROM inventory WHERE product_id = 57;` → **?** | |
| 6 | `COMMIT;` | |
| 7 | `BEGIN ISOLATION LEVEL REPEATABLE READ;` | |
| 8 | `SELECT now() > '2020-01-01' AS started;` | |
| 9 | | +10 |
| 10 | `SELECT quantity FROM inventory WHERE product_id = 57;` → **?** | |
| 11 | `COMMIT;` | |
| 12 | `BEGIN;` (`READ COMMITTED`) | |
| 13 | `SELECT quantity FROM inventory WHERE product_id = 57;` → **?** | |
| 14 | | +10 |
| 15 | `SELECT quantity FROM inventory WHERE product_id = 57;` → **?** | |
| 16 | `SELECT quantity, (SELECT quantity FROM inventory WHERE product_id = 57) AS again, pg_sleep(2) IS NOT NULL AS slept, (SELECT quantity FROM inventory WHERE product_id = 57) AS after_sleep FROM inventory WHERE product_id = 57;` (runs for 2 s) | |
| 17 | | +10, while A's step 16 is still sleeping |
| 18 | `COMMIT;` | |

Then run it and check your predictions.

**Self-check:** the five single-value answers add up to 260. The three columns at step 16 are equal.

<details><summary>Hint</summary>

Under `REPEATABLE READ`, the snapshot is taken by the first *statement*, whatever it reads. Under `READ COMMITTED`, by every statement, and a statement's subqueries share it.

</details>

---

## Exercise 4 ★★: Fix a lost-update bug

The warehouse team wrote this function to record deliveries:

```sql
CREATE OR REPLACE FUNCTION restock(p_product_id bigint, p_units int)
RETURNS int
LANGUAGE plpgsql
AS $$
DECLARE
    v_qty int;
BEGIN
    SELECT quantity INTO v_qty
    FROM inventory
    WHERE product_id = p_product_id;

    PERFORM pg_sleep(0.001);          -- (imagine a call to the warehouse system here)

    UPDATE inventory
    SET quantity = v_qty + p_units
    WHERE product_id = p_product_id;

    RETURN v_qty + p_units;
END;
$$;
```

(It is also in [`solutions/exercises/ex04_restock_buggy.sql`](solutions/exercises/ex04_restock_buggy.sql).)

1. Create it, set product 57's quantity to 30, and run 8 concurrent clients that each call `restock(57, 1)` 100 times with the pgbench script [`solutions/exercises/ex04_restock_bench.sql`](solutions/exercises/ex04_restock_bench.sql) (the command is in the file's header). What should the quantity be afterwards, and what is it?
2. Explain, with a two-session step table, how a restock gets lost.
3. Fix the function in two different ways: once without any explicit lock, and once with a locking read. Rerun the benchmark for each.
4. Compare the two fixes' throughput (pgbench prints `tps`). Why is one of them clearly slower here?

**Self-check:** after a fix, the quantity is exactly 830. With the buggy function it is far lower (most of the 800 restocks are lost).

<details><summary>Hint</summary>

Section 6.2 lists three fixes for the read-modify-write pattern. Look at where the `pg_sleep` sits relative to the lock in each version.

</details>

---

## Exercise 5 ★★: Optimistic locking for product edits

Sellers edit their products in a web form: the form loads the product, the seller edits for a few minutes, then saves. Two members of a seller's staff sometimes edit the same product, and the second save silently overwrites the first.

1. Add a column `products.version int NOT NULL DEFAULT 1`, and a trigger that increments it on **every** update of a product, whoever makes it.
2. Write `update_product(p_id bigint, p_expected_version int, p_title text, p_price numeric(12,2)) RETURNS int` that saves the edit only if the product is still at `p_expected_version`, and returns the new version. If someone else changed the product in the meantime, raise SQLSTATE `BZ006` with a `DETAIL` showing both versions. Raise `P0002` (`no_data_found`) for a missing product.
3. Demonstrate with two sessions: both load product 40, A saves, B saves.
4. What happens if B calls `update_product` while A's save is in a transaction that has not committed yet? Predict, then try it.
5. Why is a version counter better than the `updated_at` timestamp used in section 15.3?

**Self-check:** after A's successful save the product is at version 2, B gets `BZ006` with `DETAIL: your version 1, current version 2`, and a plain `UPDATE products SET price = price WHERE id = 40` also bumps the version.

<details><summary>Hint</summary>

Put the version check in the `UPDATE`'s `WHERE` clause and look at `FOUND` afterwards. For question 4, remember how `UPDATE` behaves under `READ COMMITTED` when the row it found is locked (section 6.3).

</details>

---

## Exercise 6 ★★★: A deadlock-free stock transfer

A seller sometimes moves stock between two of their own products (a mislabelled delivery, a bundle split into its parts). Write `transfer_stock(p_from bigint, p_to bigint, p_units int) RETURNS void` that:

- raises `22023` if the arguments make no sense (non-positive units, the same product twice, or products of different sellers);
- raises `BZ002` if either product has no inventory row;
- raises `BZ001` (with a `DETAIL` like `place_order`'s) if `p_from` has fewer than `p_units` **available** units (`quantity - reserved`);
- otherwise decreases `p_from`'s `quantity` and increases `p_to`'s by `p_units`, atomically;
- **never deadlocks**, even when transfers in opposite directions between the same two products run concurrently.

Prove the last point. Set products 1 and 2 to 1,000 units each, then run 8 clients × 200 random transfers of one unit in either direction with [`solutions/exercises/ex06_transfer_bench.sql`](solutions/exercises/ex06_transfer_bench.sql). Check the total and the `deadlocks` counter in `pg_stat_database` before and after. Then run the same benchmark against the obvious implementation (subtract from one row, then add to the other) and compare.

**Self-check:** with your function, the bench reports 0 failed transactions, `deadlocks` does not change, and the sum of the two quantities is still 2,000. The obvious implementation also keeps the sum (failed transactions roll back), but produces hundreds of deadlocks and runs far slower.

<details><summary>Hint</summary>

Section 13.3: lock both rows first, in ascending `product_id` order, regardless of the transfer's direction. Which `FOR ...` mode is enough?

</details>

---

## Exercise 7 ★★: Diagnose a blocking chain

It is 14:02 and Bazaar's product pages time out. A colleague captured this snapshot from `pg_stat_activity` (`blocked_by` is `pg_blocking_pids(pid)`):

```text
@@SNAPSHOT_ACTIVITY@@
```

and the locks on `inventory` and on transaction ids:

```text
@@SNAPSHOT_LOCKS@@
```

Answer from the snapshot alone, then reproduce it to check:

1. Draw the wait-for graph. Which session is the root of the problem, and what is it doing?
2. Session @@PID_PAGE@@ only reads. Why is it blocked, and by whom?
3. Session @@PID_CHECKOUT2@@ wants product 44 only, which nobody has locked. Why does it wait?
4. You may run **one** command right now to get the product pages back as fast as possible. Which one? What happens next, step by step? What would you do second?
5. Which two settings would have prevented this incident, and on which roles would you set them?

**Self-check:** the root blocker is the session in state `idle in transaction`; the read-only query is blocked by the `ALTER TABLE`, not by the root.

<details><summary>Hint</summary>

Sections 11.4 (lock queue), 12 (reading `pg_locks`) and 18.2 (timeouts). `pg_cancel_backend` and `pg_terminate_backend` do different things to an idle session.

</details>

---

## Exercise 8 ★★: Write skew on the support rota

Bazaar's marketplace support team has a rule: **at least one admin must be on call at all times**. Create a scratch table:

```sql
CREATE SCHEMA IF NOT EXISTS lab;
CREATE TABLE lab.support_rota (
    admin    text    PRIMARY KEY,
    on_call  boolean NOT NULL
);
INSERT INTO lab.support_rota VALUES ('amara', true), ('bruno', true), ('chen', false);
```

The "go off call" button runs: count the admins on call; if there are at least two, set mine to `false`; commit.

1. With two sessions at `REPEATABLE READ`, have amara and bruno press the button at the same time. Show that both succeed and nobody is on call.
2. Repeat at `SERIALIZABLE`. Which session fails, at which statement, with which SQLSTATE? What does that session see when it retries?
3. Make the button safe under **`READ COMMITTED`** without changing the isolation level, and show that the second session now waits and then refuses.
4. Name one advantage and one disadvantage of your fix in 3 compared to `SERIALIZABLE`.

**Self-check:** after (1), `SELECT count(*) FROM lab.support_rota WHERE on_call` returns 0. After (2) and after (3), it returns 1.

<details><summary>Hint</summary>

For 3, make the two transactions conflict on the same rows before they count: section 7.3 calls this *materializing the conflict*.

</details>

---

## Exercise 9 ★★: Predict the outcome

For each scenario, predict whether session B's last statement returns immediately, waits, or fails (and with what), and what the data looks like at the end. Then run it. Reset before each one.

**(a) Changing a key column.** Product SKUs are in a unique constraint `(seller_id, sku)`.

| Step | Session A | Session B |
|---|---|---|
| 1 | `BEGIN;` | |
| 2 | `UPDATE products SET sku = sku || '-V2' WHERE id = 15;` | |
| 3 | | `BEGIN;` |
| 4 | | `INSERT INTO orders (user_id, seller_id) VALUES (8, 1) RETURNING id;` |
| 5 | | `INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (<that id>, 15, 1, 1799.00);` → **?** |
| 6 | `COMMIT;` | `ROLLBACK;` |

**(b) A snapshot from an unrelated table.**

| Step | Session A | Session B |
|---|---|---|
| 1 | `BEGIN ISOLATION LEVEL REPEATABLE READ;` | |
| 2 | `SELECT count(*) FROM orders;` | |
| 3 | | `UPDATE inventory SET reserved = reserved + 1 WHERE product_id = 15;` |
| 4 | `UPDATE inventory SET quantity = quantity + 1 WHERE product_id = 15;` → **?** | |

**(c) Updating a row that is being deleted.**

| Step | Session A | Session B |
|---|---|---|
| 1 | `BEGIN;` | |
| 2 | `DELETE FROM promotions WHERE id = 9;` | |
| 3 | | `UPDATE promotions SET discount_pct = 15 WHERE id = 9;` → **?** |
| 4 | `COMMIT;` | → **?** |

**(d) Share, then upgrade.**

| Step | Session A | Session B |
|---|---|---|
| 1 | `BEGIN;` | `BEGIN;` |
| 2 | `SELECT reserved FROM inventory WHERE product_id = 15 FOR SHARE;` | |
| 3 | | `SELECT reserved FROM inventory WHERE product_id = 15 FOR SHARE;` → **?** |
| 4 | `UPDATE inventory SET reserved = reserved + 1 WHERE product_id = 15;` → **?** | |
| 5 | | `UPDATE inventory SET reserved = reserved + 1 WHERE product_id = 15;` → **?** |

**Self-check:** exactly one of the four scenarios ends with a deadlock, and exactly one with a serialization failure.

<details><summary>Hint</summary>

(a) Section 10.1: which `UPDATE`s take `FOR UPDATE` instead of `FOR NO KEY UPDATE`? (b) Section 7.1: when was A's snapshot taken? (c) Section 6.3: what does EvalPlanQual do with a row that was deleted?

</details>

---

## Exercise 10 ★★: Ship orders with `SKIP LOCKED`

Warehouse pickers each press "next order" on a handheld scanner. Write `ship_next_order(p_picker text) RETURNS bigint` that:

- takes the **oldest `paid` order** that no other picker is shipping right now (pickers must never wait for each other or get the same order);
- marks it `shipped` with `shipped_at = now()`;
- **consumes** its stock: for each item, decreases both `inventory.quantity` and `inventory.reserved` by the item's quantity (the reservation becomes a real stock movement), locking inventory rows in `product_id` order;
- returns the order id, or `NULL` when there is nothing to ship.

Demonstrate with three sessions that each call it twice inside an open transaction (`BEGIN; SELECT ship_next_order('p1'); SELECT ship_next_order('p1');` and leave it open), then a fourth call from another session.

**Self-check:** state 11 has six paid orders (110 to 114 and 120). The three pickers get six different orders without waiting, and the fourth call returns `NULL` immediately. After all commit, there are no `paid` orders, and for every product `reserved` equals the units in `pending` orders.

<details><summary>Hint</summary>

The dequeue pattern of section 17.2, but on `orders`: an `UPDATE ... WHERE id = (SELECT ... ORDER BY ... LIMIT 1 FOR UPDATE SKIP LOCKED)`. Do the inventory part after the order is claimed.

</details>

---

## Exercise 11 ★★: A singleton report refresh

`REFRESH MATERIALIZED VIEW CONCURRENTLY seller_sales_daily` (module 09) is triggered by a cron job on every application server, so several refreshes sometimes start at once. They queue behind each other on the matview's `EXCLUSIVE` lock and do the same work several times.

Write `refresh_sales_report() RETURNS text` that refreshes the matview and returns `'refreshed'`, unless another refresh is already running, in which case it returns `'skipped'` immediately without waiting. Use an advisory lock.

1. Show it with two sessions: A runs `BEGIN; SELECT refresh_sales_report();` and keeps the transaction open; B calls the function.
2. Which advisory lock function did you choose (session or transaction level, waiting or `try`), and why?
3. A's refresh finished long ago, but A's transaction is still open. Is B's `skipped` the right answer? What does this tell you about where to call the function from?

**Self-check:** while A's transaction is open, B gets `skipped` instantly. After A commits, B gets `refreshed`. `pg_locks` shows one `advisory` lock while A is open and none afterwards.

<details><summary>Hint</summary>

`pg_try_advisory_xact_lock(hashtext('refresh_sales_report'))`, or the two-key form with a namespace. Section 14.2 explains why session-level locks are risky behind poolers.

</details>

---

## Exercise 12 ★★★: A migration that cannot take the site down

Write a procedure `add_column_safely(p_table regclass, p_column text, p_type text)` that adds a column but **never blocks other sessions for more than 200 ms**:

- every attempt uses `lock_timeout = '200ms'`;
- on `lock_not_available` (`55P03`) it waits a little (say 0.5 s, growing with each attempt) and tries again, up to 10 attempts;
- it reports progress with `RAISE NOTICE` and gives up with an error after the last attempt;
- adding a column that already exists is a no-op (so the migration can be re-run).

Test it: in session A, open a transaction that reads `products` and keep it open; in session B, call `CALL add_column_safely('products', 'weight_g', 'int');`; in session C, run `SELECT title FROM products WHERE id = 1;` a few times; then commit A after a couple of seconds.

**Self-check:** C's queries return within about 200 ms even while B is retrying; B's notices show several failed attempts and then success after A commits; `\d products` shows `weight_g`. Calling the procedure again prints that the column already exists.

<details><summary>Hint</summary>

A procedure can `COMMIT` between attempts, which also ends the lock wait cleanly; or catch the error in a `BEGIN ... EXCEPTION WHEN lock_not_available THEN ... END` block. `set_config('lock_timeout', '200ms', true)` sets it for the current transaction only. Use `format('%I', ...)` for the identifiers.

</details>

---

## Exercise 13 ★★★: Idempotent checkout

Build the idempotency-key pattern of section 16.4 into Bazaar:

1. A table `checkout_requests` with the key (`text`, primary key), the user, a fingerprint of the request (for example `md5(p_items::text)`), the resulting order ids, and `created_at`.
2. A function `place_order_idempotent(p_key text, p_user_id bigint, p_items jsonb, p_shipping_address_id bigint DEFAULT NULL) RETURNS SETOF bigint` that places the order the first time it sees a key, and returns the **same** order ids for every later call with that key, even if the later call arrives while the first is still running.
3. Reusing a key with a **different** request (another user or another cart) must fail with SQLSTATE `BZ007`.
4. If the first call fails (say, `BZ001`), the key must not be "burned": a later call with the same key tries again.

Prove 2 under concurrency: run 8 pgbench clients that each submit the same 5 keys (`'k1'` ... `'k5'`, picked at random, 50 calls per client) for user 8 buying one unit of product 18.

**Self-check:** after the bench, `checkout_requests` has 5 rows, exactly 5 new orders exist, product 18's `reserved` went up by exactly 5, and all 400 calls succeeded.

<details><summary>Hint</summary>

`INSERT ... ON CONFLICT DO NOTHING RETURNING` tells you whether you own the key. A concurrent insert of the same key waits for the owner's transaction. If you don't own it, read the stored row, compare fingerprints, and return its order ids. For 4, think about what happens to the key row when the transaction that inserted it rolls back.

</details>
