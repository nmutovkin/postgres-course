# Module 10: Transactions and Concurrency

<!-- nav:start -->
> **Theory** · [Exercises](exercises.md) · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 09 Views, Functions and Triggers](../09-views-functions-triggers/README.md) · [Course home](../../README.md) · [11 Indexes →](../11-indexes/README.md)
<!-- nav:end -->

It is 9:00 on Black Friday. Product 15, the *Titan G15 Gaming Laptop*, has four units in stock, and four hundred shoppers press **Buy** within the same second. Each checkout has to check the stock, reserve it, create one order per seller and add the order items. If two checkouts both read "4 available" and both reserve 3, Bazaar has sold six laptops it does not have. If a checkout crashes halfway, a shopper has an order with no reserved stock, or reserved stock with no order. If checkouts lock rows in different orders, they deadlock and both fail. Up to now every example in this course ran alone. In this module many sessions run at the same time, and you learn the tools PostgreSQL gives you to keep the data correct anyway: transactions, MVCC snapshots, isolation levels and locks.

By the end you will have built `place_order()`, Bazaar's checkout. It is one database call that reserves stock without ever overselling, cannot deadlock against itself, and fails with a clean, catchable error code. You will also build a job queue that many workers can drain at once without processing a job twice. You will prove both with a concurrency stress test.

**What you'll learn**

- What a transaction is, what each letter of ACID means in concrete terms, and how `BEGIN`, `COMMIT`, `ROLLBACK`, autocommit and savepoints behave, including the "current transaction is aborted" state.
- MVCC at the level of intuition: row versions, `xmin`/`xmax`, snapshots, and why readers never block writers.
- The isolation levels PostgreSQL implements (`READ COMMITTED`, `REPEATABLE READ`, `SERIALIZABLE`), the anomalies each one allows (dirty read, non-repeatable read, phantom, lost update, write skew, read-only anomaly), and how `UPDATE` re-checks rows under `READ COMMITTED`.
- Serializable snapshot isolation: how PostgreSQL detects dangerous patterns, why you must retry on `40001`, and when `READ ONLY DEFERRABLE` helps.
- Row-level locks (`FOR UPDATE`, `FOR NO KEY UPDATE`, `FOR SHARE`, `FOR KEY SHARE`), `NOWAIT` and `SKIP LOCKED`, and why foreign keys take `KEY SHARE` locks.
- The eight table-level lock modes, which commands take which mode, the lock queue problem, and `lock_timeout`.
- How to see who blocks whom with `pg_locks`, `pg_stat_activity` and `pg_blocking_pids()`.
- Deadlocks: how they happen, how PostgreSQL detects them, and how a consistent lock order prevents them.
- Advisory locks, and the classic concurrency patterns: atomic conditional updates, pessimistic and optimistic locking, idempotency keys, job queues with `SKIP LOCKED`, retry loops, and timeouts against long transactions.

**Before you start**

```bash
./project/bin/reset.sh 10     # state at the start of module 10 (small dataset + module 09 logic)
./project/bin/psql.sh
```

Many demos in this module need **two or more sessions at the same time**. Open two (sometimes three) terminals and run `./project/bin/psql.sh` in each. The demos are written as step tables:

| Step | Session A | Session B |
|---|---|---|
| 1 | `BEGIN;` | |
| 2 | | `SELECT ...;` |

Run the steps strictly in order, switching windows as the table says. "**(waits)**" means the statement does not return: the session is blocked on a lock, and the result appears later, at the step that releases it. All outputs in this module come from real runs on PostgreSQL 17. Transaction ids, process ids and timestamps will differ on your machine.

If you'd rather watch than type, [`examples/sessions.py`](examples/sessions.py) replays the main demos with real concurrent psql sessions, for example `python3 modules/10-transactions-concurrency/examples/sessions.py modules/10-transactions-concurrency/examples/demos/lost_update.txt`. The demo files are in [`examples/demos/`](examples/demos/). Typing them yourself teaches more, though.

Many demos change data. Run `./project/bin/reset.sh 10` whenever you want a clean slate; it takes about two seconds. Sections 16 and 17 use this module's reference implementation, and tell you how to load it.

A few demos use a scratch schema for small throw-away tables:

```sql
CREATE SCHEMA IF NOT EXISTS lab;
```

---

<!-- toc:start -->
**Contents**

- [1. Why transactions?](#1-why-transactions)
- [2. Controlling transactions](#2-controlling-transactions)
- [3. Errors inside a transaction](#3-errors-inside-a-transaction)
- [4. MVCC: how PostgreSQL lets everybody work at once](#4-mvcc-how-postgresql-lets-everybody-work-at-once)
- [5. Isolation levels](#5-isolation-levels)
- [6. READ COMMITTED](#6-read-committed)
- [7. REPEATABLE READ](#7-repeatable-read)
- [8. SERIALIZABLE](#8-serializable)
- [9. The anomalies table](#9-the-anomalies-table)
- [10. Row-level locks](#10-row-level-locks)
- [11. Table-level locks](#11-table-level-locks)
- [12. Seeing locks and blocking](#12-seeing-locks-and-blocking)
- [13. Deadlocks](#13-deadlocks)
- [14. Advisory locks](#14-advisory-locks)
- [15. Concurrency patterns](#15-concurrency-patterns)
- [16. Worked example: `place_order()`](#16-worked-example-place_order)
- [17. A job queue with `SKIP LOCKED`](#17-a-job-queue-with-skip-locked)
- [18. Long transactions and timeouts](#18-long-transactions-and-timeouts)
- [Summary](#summary)
- [What's next](#whats-next)

<!-- toc:end -->

## 1. Why transactions?

### 1.1 Checkout without a transaction

A checkout touches three tables: it inserts an `orders` row, inserts `order_items`, and increases `inventory.reserved`. Let's do that by hand, as three separate statements, for one unit of product 13 (*KidSafe Volume-Limited Headphones*). Product 13 has `quantity = 1` and `reserved = 1`, so it is already sold out, and the `CHECK (reserved <= quantity)` constraint on `inventory` will reject the reservation:

```sql
INSERT INTO orders (user_id, seller_id) VALUES (8, 1) RETURNING id, status, total;
INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (121, 13, 1, 29.99);
UPDATE inventory SET reserved = reserved + 1 WHERE product_id = 13;
```

```text
 id  | status  | total
-----+---------+-------
 121 | pending |  0.00
(1 row)

INSERT 0 1
INSERT 0 1
ERROR:  new row for relation "inventory" violates check constraint "inventory_reserved_within_quantity"
DETAIL:  Failing row contains (13, 1, 2, 2026-10-14 08:24:09.277132+00).
```

The third statement failed, but the first two had already succeeded and are permanent:

```sql
SELECT id, status, total FROM orders WHERE id = 121;
```

```text
 id  | status  | total
-----+---------+-------
 121 | pending | 29.99
(1 row)
```

Bazaar now has a pending order for headphones it cannot deliver, with no stock reserved for it. The total even says 29.99, because module 09's trigger maintained it. Clean up with `DELETE FROM orders WHERE id = 121;` (the item goes with it through `ON DELETE CASCADE`).

### 1.2 The same checkout in a transaction

Now wrap the three statements in `BEGIN` ... `COMMIT`:

```sql
BEGIN;
INSERT INTO orders (user_id, seller_id) VALUES (8, 1) RETURNING id;
INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (122, 13, 1, 29.99);
UPDATE inventory SET reserved = reserved + 1 WHERE product_id = 13;
COMMIT;
```

```text
BEGIN
 id
-----
 122
(1 row)

INSERT 0 1
INSERT 0 1
ERROR:  new row for relation "inventory" violates check constraint "inventory_reserved_within_quantity"
DETAIL:  Failing row contains (13, 1, 2, 2026-10-14 08:24:09.279571+00).
ROLLBACK
```

Look at the last line: we typed `COMMIT`, and PostgreSQL answered `ROLLBACK`. Because one statement failed, the whole transaction was doomed, and nothing it did survives:

```sql
SELECT count(*) FROM orders WHERE id = 122;
```

```text
 count
-------
     0
(1 row)
```

That is the whole point of a transaction: **a group of statements that succeeds or fails as one unit**, and that other sessions see either completely or not at all.

> **Under the hood:** the order id 122 is gone for good. The next order gets 123. Sequences (and identity columns, which use a sequence) are deliberately *not* transactional: `nextval()` hands out a number immediately and never takes it back, so that concurrent inserts never wait for each other just to get an id. Gaps in ids are normal and never a sign of lost data. Never use ids to count rows or to detect missing ones.

### 1.3 ACID, letter by letter

Database people describe what transactions guarantee with the acronym **ACID**. Each letter is a specific promise.

**A: Atomicity.** All of the transaction's changes take effect, or none do. You just saw it: the order, the item and the reservation either all exist or none of them do. Atomicity is about *failure*: an error, a `ROLLBACK`, a crashed client, a killed server process or a power cut in the middle of a transaction all leave no trace of it. PostgreSQL gets this almost for free from MVCC (section 4). The rows a transaction writes are invisible to everyone else until it commits, and an aborted transaction's rows simply stay invisible forever. No "undo" step has to run.

**C: Consistency.** A transaction takes the database from one valid state to another, where "valid" means every declared rule holds: `NOT NULL`, `CHECK`, `UNIQUE`, foreign keys, exclusion constraints, and anything your triggers enforce. The `inventory_reserved_within_quantity` check above is such a rule: no transaction can ever commit a state where `reserved > quantity`, no matter how it is written. Consistency is the one ACID property that is partly *your* job. The database enforces the rules you declare, but a rule you only keep in application code ("a seller may run at most three open-ended promotions") is enforced only if every transaction's logic is correct *under concurrency*, which is what most of this module is about. Constraints are normally checked at the end of each statement. A constraint declared `DEFERRABLE INITIALLY DEFERRED` (modules 02 and 07) is checked at `COMMIT` instead, so a transaction may pass through invalid intermediate states as long as it ends in a valid one.

**I: Isolation.** Concurrent transactions must not see each other's half-finished work, and the result of running them concurrently should be the same as running them one after another. That last part is the ideal, called **serializability**. Full serializability has a cost, so SQL defines weaker **isolation levels** that allow some well-defined anomalies in exchange for less waiting and fewer errors. Sections 5 to 9 cover them in depth. On Black Friday, isolation is the property that decides whether you sell six of four laptops.

**D: Durability.** Once `COMMIT` returns, the data survives a crash, even a power cut a millisecond later. PostgreSQL implements this with the **write-ahead log (WAL)**: before `COMMIT` reports success, a description of every change is flushed (`fsync`) to the WAL on disk. After a crash, the server replays the WAL to redo committed work that had not yet reached the data files. Module 16 shows the WAL in detail. Module 17 discusses `synchronous_commit = off`, which trades the last few hundred milliseconds of commits on a crash for faster commits, and module 19 extends durability to replicas.

---

## 2. Controlling transactions

### 2.1 Autocommit

When you type a statement in psql without `BEGIN`, PostgreSQL wraps it in a transaction of its own and commits it immediately. This is **autocommit**, and it is the default for psql and for the server:

```sql
\echo :AUTOCOMMIT
```

```text
on
```

So every statement is always in a transaction. The question is only whether the transaction holds one statement (autocommit) or many (an explicit block).

> **Pitfall:** some drivers do the opposite. Python's DB-API, and therefore psycopg (module 22), starts a transaction implicitly before your first statement and waits for you to call `commit()`. Forget to commit, and your connection sits "idle in transaction", holding locks and an old snapshot (section 18). psql can imitate this with `\set AUTOCOMMIT off`: it then sends a hidden `BEGIN` before your first statement.

### 2.2 The transaction statements

| Statement | Effect |
|---|---|
| `BEGIN` (or `START TRANSACTION`) | Starts a transaction block. Options: `ISOLATION LEVEL ...`, `READ ONLY`/`READ WRITE`, `DEFERRABLE`. |
| `COMMIT` (or `END`) | Makes all changes permanent and visible. In a failed transaction it performs a `ROLLBACK` instead. |
| `ROLLBACK` (or `ABORT`) | Discards all changes of the transaction. |
| `SAVEPOINT name` | Marks a point you can roll back to without abandoning the whole transaction (section 3). |
| `ROLLBACK TO SAVEPOINT name` | Undoes everything after the savepoint; the transaction continues. |
| `RELEASE SAVEPOINT name` | Forgets the savepoint, keeping its changes. |
| `COMMIT AND CHAIN` / `ROLLBACK AND CHAIN` | Ends the transaction and immediately starts a new one with the same characteristics (isolation level, read-only). Handy in batch loops. |

`BEGIN` when a transaction is already open only produces a warning (`there is already a transaction in progress`), and `COMMIT` with no open transaction warns `there is no transaction in progress`. Neither is an error, but both mean your idea of the transaction boundaries is wrong.

psql shows the state in the prompt (module 01): `bazaar=#` outside a transaction, `bazaar=*#` inside one, and `bazaar=!#` inside a failed one.

### 2.3 Multi-statement strings are one transaction

A subtle consequence of the protocol: when a client sends several statements in **one** query string, the server runs them as a single implicit transaction. `psql -c` sends its argument as one string:

```bash
./project/bin/psql.sh bazaar -c "UPDATE inventory SET quantity = quantity + 5 WHERE product_id = 25; SELECT 1/0;"
./project/bin/psql.sh bazaar -c "SELECT quantity FROM inventory WHERE product_id = 25"
```

```text
ERROR:  division by zero
 quantity
----------
        0
(1 row)
```

The `UPDATE` was rolled back with the failing `SELECT`. With two separate `-c` options, psql sends two strings, and the `UPDATE` commits on its own:

```bash
./project/bin/psql.sh bazaar -c "UPDATE inventory SET quantity = quantity + 5 WHERE product_id = 25" -c "SELECT 1/0;"
./project/bin/psql.sh bazaar -c "SELECT quantity FROM inventory WHERE product_id = 25"
```

```text
UPDATE 1
ERROR:  division by zero
 quantity
----------
        5
(1 row)
```

Some drivers send a whole migration file as one string, others split it. Know which yours does.

### 2.4 DDL is transactional

In PostgreSQL, almost all DDL obeys transactions, like DML does. That is unusual (MySQL and Oracle commit implicitly on every DDL statement) and very useful: a migration that fails halfway leaves no half-built schema.

```sql
BEGIN;
CREATE TABLE scratch_notes (id int);
ALTER TABLE products ADD COLUMN weight_g int;
ROLLBACK;

SELECT to_regclass('scratch_notes') AS scratch_notes,
       count(*) AS weight_g_columns
FROM pg_attribute
WHERE attrelid = 'products'::regclass AND attname = 'weight_g';
```

```text
 scratch_notes | weight_g_columns
---------------+------------------
               |                0
(1 row)
```

Both the new table and the new column vanished. A few commands cannot run inside a transaction block at all, because they manage their own transactions internally or touch things outside the database:

```sql
BEGIN;
CREATE INDEX CONCURRENTLY inventory_reserved_idx ON inventory (reserved);
```

```text
ERROR:  CREATE INDEX CONCURRENTLY cannot run inside a transaction block
```

The same holds for `VACUUM`, `CREATE DATABASE`, `DROP DATABASE`, `ALTER SYSTEM`, `REINDEX ... CONCURRENTLY` and `DROP INDEX CONCURRENTLY`. Migration tools must run these outside their usual transaction (module 26).

### 2.5 Time stands still inside a transaction

`now()` (and `CURRENT_TIMESTAMP`) returns the time the *transaction* started, not the current time. `statement_timestamp()` gives the start of the current statement, and `clock_timestamp()` the actual wall-clock time:

```sql
BEGIN;
SELECT now() AS tx_start, statement_timestamp() AS stmt_start, clock_timestamp() AS wall_clock;
SELECT pg_sleep(1);
SELECT now() AS tx_start, statement_timestamp() AS stmt_start, clock_timestamp() AS wall_clock;
COMMIT;
```

```text
           tx_start            |          stmt_start           |          wall_clock
-------------------------------+-------------------------------+-------------------------------
 2026-10-14 08:24:30.863141+00 | 2026-10-14 08:24:30.863327+00 | 2026-10-14 08:24:30.863742+00
(1 row)

           tx_start            |          stmt_start           |          wall_clock
-------------------------------+-------------------------------+-------------------------------
 2026-10-14 08:24:30.863141+00 | 2026-10-14 08:24:31.866568+00 | 2026-10-14 08:24:31.866674+00
(1 row)
```

This is a feature: every row a transaction writes gets the same `created_at`, so an order and its audit-log entries agree on when they happened. It also means a transaction that stays open for an hour stamps rows with a time an hour in the past.

---

## 3. Errors inside a transaction

### 3.1 The aborted state

When a statement fails inside a transaction block, PostgreSQL does not roll back on the spot. It marks the transaction as **failed** and refuses to do anything else in it until you end it:

```sql
BEGIN;
UPDATE inventory SET quantity = quantity + 10 WHERE product_id = 1;
SELECT 1/0;
SELECT quantity FROM inventory WHERE product_id = 1;
COMMIT;
```

```text
BEGIN
UPDATE 1
ERROR:  division by zero
ERROR:  current transaction is aborted, commands ignored until end of transaction block
ROLLBACK
```

The `SELECT` after the error was not even executed. Its error has SQLSTATE `25P02` (`in_failed_sql_transaction`); you can see codes with `\errverbose` right after an error:

```text
ERROR:  25P02: current transaction is aborted, commands ignored until end of transaction block
LOCATION:  exec_simple_query, postgres.c:1141
```

The `COMMIT` turned into a `ROLLBACK`, and product 1's quantity is still 25. Why this design? Because after an error, the transaction's state is something you did not plan for. Suppose the failed statement was the `UPDATE inventory` of a checkout: continuing and committing would store an order without its reservation, which is exactly the inconsistency the transaction was meant to prevent. PostgreSQL forces you to decide explicitly: roll back the whole thing, or roll back to a savepoint.

> **Pitfall:** "current transaction is aborted" in an application log is never the real problem. It is a *symptom*: an earlier statement in the same transaction failed, and the code caught the exception and carried on with the same connection. Find the first error. Connection pools that hand out a connection still in the failed state cause the same message in a completely unrelated request; a good pool rolls back before reusing a connection (module 22).

### 3.2 Savepoints

A **savepoint** is a named point inside a transaction that you can roll back to, undoing only the work done after it. Picture a restock import where each line is one inventory change, and a bad line should be skipped without losing the good ones:

```sql
BEGIN;
UPDATE inventory SET quantity = quantity + 10 WHERE product_id = 1;   -- line 1

SAVEPOINT line_2;
UPDATE inventory SET quantity = quantity - 50 WHERE product_id = 2;   -- line 2: bad
ROLLBACK TO SAVEPOINT line_2;                                         -- undo line 2 only

SAVEPOINT line_3;
UPDATE inventory SET quantity = quantity + 5 WHERE product_id = 3;    -- line 3
RELEASE SAVEPOINT line_3;
COMMIT;

SELECT product_id, quantity FROM inventory WHERE product_id IN (1, 2, 3) ORDER BY product_id;
```

```text
ERROR:  new row for relation "inventory" violates check constraint "inventory_quantity_nonnegative"
DETAIL:  Failing row contains (2, -38, 0, 2026-10-14 08:24:41.930761+00).
...
 product_id | quantity
------------+----------
          1 |       35
          2 |       12
          3 |        7
(3 rows)
```

Line 1 and line 3 committed; line 2 was undone and the transaction was healthy again after `ROLLBACK TO SAVEPOINT`. A few rules:

- `ROLLBACK TO SAVEPOINT x` keeps the savepoint, so you can roll back to it again. `RELEASE SAVEPOINT x` removes it (and any savepoints created after it) but keeps the work. Releasing is optional; `COMMIT` releases everything.
- Savepoints nest. Rolling back to an outer savepoint undoes the inner ones too.
- Savepoint names can be reused; the newest one wins.
- Locks taken after a savepoint are released when you roll back to it.

> **Under the hood:** each savepoint starts a **subtransaction** with its own transaction id (once it writes). They are cheap but not free. Each backend caches up to 64 subtransaction ids in shared memory; a transaction with more than 64 active subtransactions "overflows", and every other session that takes a snapshot must then look subtransaction ids up in `pg_subtrans` on disk. Under load, that is a known cause of sudden slowdowns. A loop that opens a savepoint per row over thousands of rows is the usual culprit.

The same machinery powers a PL/pgSQL `BEGIN ... EXCEPTION ... END` block (module 09): entering such a block sets an implicit savepoint, and catching an exception rolls back to it. That is why a function can catch an error and carry on, and also why an `EXCEPTION` clause inside a loop that runs a million times is slow.

### 3.3 `ON_ERROR_ROLLBACK` in psql

Typing a long transaction in psql and losing it to a typo is annoying. psql can wrap every statement you send inside a transaction in its own savepoint, and roll back to it automatically if the statement fails:

```sql
\set ON_ERROR_ROLLBACK on
BEGIN;
UPDATE inventory SET quantity = quantity + 1 WHERE product_id = 4;
UPDATE inventory SET quantity = -1 WHERE product_id = 6;              -- fails
UPDATE inventory SET quantity = quantity + 1 WHERE product_id = 7;    -- still runs
COMMIT;
SELECT product_id, quantity FROM inventory WHERE product_id IN (4, 6, 7) ORDER BY product_id;
```

```text
ERROR:  new row for relation "inventory" violates check constraint "inventory_quantity_nonnegative"
DETAIL:  Failing row contains (6, -1, 0, 2026-10-14 08:24:41.934411+00).
UPDATE 1
COMMIT
 product_id | quantity
------------+----------
          4 |       41
          6 |       30
          7 |       16
(3 rows)
```

The value `interactive` applies this only to statements you type, not to scripts you run with `-f` or `\i`, and is the sensible setting for a `.psqlrc`. Scripts should fail as a whole (`ON_ERROR_STOP`, module 01). Note that this is purely a psql feature: psql sends `SAVEPOINT` and `ROLLBACK TO SAVEPOINT` behind your back. The server knows nothing about it, and your application gets no such help.

---

## 4. MVCC: how PostgreSQL lets everybody work at once

Before isolation levels make sense, you need a mental model of how PostgreSQL stores concurrent changes. It uses **multi-version concurrency control (MVCC)**. Module 16 shows the bytes on disk; here is the intuition.

### 4.1 Row versions and transaction ids

Every transaction that writes gets a **transaction id** (xid), a 32-bit counter that increases. Read-only transactions never get one (they make do with a lightweight "virtual" id), which saves xids:

```sql
BEGIN;
SELECT pg_current_xact_id_if_assigned() AS my_xid;    -- NULL: nothing written yet
UPDATE inventory SET quantity = quantity + 6 WHERE product_id = 15;
SELECT pg_current_xact_id_if_assigned() AS my_xid;
```

```text
 my_xid
--------

(1 row)

 my_xid
--------
    994
(1 row)
```

`pg_current_xact_id()` returns the current xid and assigns one if necessary. `txid_current()` is its older name (both return the same number today, as a 64-bit value that includes an "epoch" so it never wraps around).

An `UPDATE` in PostgreSQL never overwrites a row in place. It writes a **new version** of the row and marks the old version as superseded. Every row version carries two hidden system columns:

- `xmin`: the xid of the transaction that created this version.
- `xmax`: the xid of the transaction that deleted or replaced it (0 if none), or that locked it.

You can select them like ordinary columns. `ctid` is the version's physical address (page, item). Here is product 15 with two sessions:

| Step | Session A | Session B |
|---|---|---|
| 1 | `SELECT xmin, xmax, ctid, quantity, reserved FROM inventory WHERE product_id = 15;` → xmin 962, xmax 0, ctid (0,15), quantity 4 | |
| 2 | `BEGIN;` | |
| 3 | `UPDATE inventory SET quantity = quantity + 6 WHERE product_id = 15;` (xid 994) | |
| 4 | same `SELECT` → xmin **994**, xmax 994, ctid **(0,63)**, quantity **10** | |
| 5 | | same `SELECT` → xmin 962, xmax **994**, ctid (0,15), quantity **4** |
| 6 | `COMMIT;` | |
| 7 | | same `SELECT` → xmin 994, xmax 994, ctid (0,63), quantity 10 |

At step 4 and 5, the table physically contains **two versions** of product 15's row: the old one at `(0,15)` with quantity 4, whose `xmax` says "replaced by 994", and the new one at `(0,63)` with quantity 10, created by 994. Session A sees its own new version. Session B sees the old one, because transaction 994 has not committed. **B was not blocked**: it did not wait for A, it simply read the version it is allowed to see. After A commits, B sees the new version.

> **Under the hood:** why does the *new* version also show `xmax = 994`? Not because it is deleted. The `BEFORE UPDATE` trigger `set_updated_at()` (module 09) makes PostgreSQL lock the row before updating it, and the lock is carried over to the new version. A row lock is recorded in `xmax` too, with a "lock only" flag bit (section 10). So a non-zero `xmax` means "deleted, replaced *or locked* by this xid". Module 16 decodes those flag bits with `pageinspect`.

### 4.2 Snapshots

Which version may a transaction see? That is decided by its **snapshot**, a small data structure that says which transactions had committed at the moment it was taken:

```sql
SELECT pg_current_snapshot();
```

```text
 pg_current_snapshot
---------------------
 994:994:
(1 row)
```

This was taken by session B at step 5. The format is `xmin:xmax:list of in-progress xids`. It reads: "every xid below 994 is finished; 994 and above had not finished when I looked". A row version is visible to a snapshot if the transaction that created it (its `xmin`) had committed according to the snapshot, and the transaction that deleted it (its `xmax`), if any, had not. When B looked, 994 was still running, so B saw the version created by 962 and ignored the one created by 994.

This gives PostgreSQL its central concurrency property:

> **Readers don't block writers, and writers don't block readers.** A plain `SELECT` never waits for a lock held by an `UPDATE`, and an `UPDATE` never waits for a `SELECT`. Only writers that touch the **same row** wait for each other.

The price is that old row versions pile up until no snapshot can need them any more. Then `VACUUM` removes them (module 16), and a transaction that stays open for hours keeps old versions alive for everyone (section 18).

When a snapshot is taken, and how long it is kept, is exactly what distinguishes the isolation levels.

---

## 5. Isolation levels

The SQL standard defines four isolation levels by the anomalies each must prevent. PostgreSQL implements three distinct behaviours:

| Level you ask for | What PostgreSQL gives you | Snapshot |
|---|---|---|
| `READ UNCOMMITTED` | Same as `READ COMMITTED` (PostgreSQL never shows uncommitted data) | new one for every statement |
| `READ COMMITTED` (default) | `READ COMMITTED` | new one for every statement |
| `REPEATABLE READ` | Snapshot isolation | one for the whole transaction |
| `SERIALIZABLE` | Serializable snapshot isolation (SSI) | one for the whole transaction, plus conflict tracking |

Setting the level:

```sql
BEGIN ISOLATION LEVEL REPEATABLE READ;          -- for one transaction
-- or, as the first statement inside a transaction:
SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;

SET default_transaction_isolation = 'repeatable read';     -- for this session's future transactions
ALTER ROLE bazaar_analyst SET default_transaction_isolation = 'repeatable read';  -- per role (the role arrives in module 15)

SHOW transaction_isolation;                     -- the level of the current transaction
```

`SET TRANSACTION` must come before the first query of the transaction, because the snapshot is taken by that query. You can also declare a transaction `READ ONLY`, which makes any write fail; that is a useful safety net for reporting code and, under `SERIALIZABLE`, also a performance optimisation (section 8.5).

In the next four sections we meet each level, the anomalies it lets through, and how they look in Bazaar. Section 9 sums everything up in one table.

---

## 6. READ COMMITTED

### 6.1 A fresh snapshot for every statement

Under `READ COMMITTED`, each **statement** takes a new snapshot when it starts. A statement therefore sees everything committed before it began, and nothing committed while it runs. Two consecutive statements in the same transaction can see different data. Shopper session A looks at product 10 (*QuietWave ANC Headphones*) while the seller, session B, raises the price and schedules another promotion:

| Step | Session A (shopper) | Session B (seller) |
|---|---|---|
| 1 | `BEGIN;` | |
| 2 | `SELECT price FROM products WHERE id = 10;` → **229.00** | |
| 3 | `SELECT count(*) FROM promotions WHERE product_id = 10;` → **2** | |
| 4 | | `UPDATE products SET price = 249.00 WHERE id = 10;` → `UPDATE 1` (autocommit) |
| 5 | | `INSERT INTO promotions (product_id, discount_pct, active_during) VALUES (10, 10, tstzrange('2025-07-01', '2025-07-08'));` → `INSERT 0 1` |
| 6 | `SELECT price FROM products WHERE id = 10;` → **249.00** | |
| 7 | `SELECT count(*) FROM promotions WHERE product_id = 10;` → **3** | |
| 8 | `COMMIT;` | |

Two anomalies just happened inside one transaction:

- **Non-repeatable read** (steps 2 and 6): reading the *same row* twice gives different values, because another transaction updated it and committed in between.
- **Phantom read** (steps 3 and 7): running the *same query* twice returns a different *set of rows*, because another transaction inserted (or deleted) rows that match the condition.

For many applications that is fine: each statement sees a consistent, committed picture, and it sees the latest one. But a checkout that computes a total in one statement and charges a card in another may use two different prices.

What `READ COMMITTED` does guarantee:

- **No dirty reads.** You never see data from a transaction that has not committed (and may still roll back). In MVCC that is automatic: uncommitted versions are not visible to anyone else's snapshot.
- **Each statement is internally consistent.** A single `SELECT` with joins and subqueries sees one snapshot, even if it runs for ten minutes while thousands of commits happen.

### 6.2 The lost update

The most expensive `READ COMMITTED` bug is the **lost update**. It happens when application code reads a value, computes a new value from it, and writes the result back: a *read-modify-write* cycle. Two shoppers each reserve units of product 15 (4 in stock, 0 reserved). The application code reads `reserved`, adds its quantity, and writes back the sum:

| Step | Session A (buys 1) | Session B (buys 3) |
|---|---|---|
| 1 | `BEGIN;` | |
| 2 | `SELECT quantity, reserved FROM inventory WHERE product_id = 15;` → quantity 4, reserved **0** | |
| 3 | | `BEGIN;` |
| 4 | | `SELECT quantity, reserved FROM inventory WHERE product_id = 15;` → quantity 4, reserved **0** |
| 5 | `UPDATE inventory SET reserved = 1 WHERE product_id = 15;` (app computed 0 + 1) → `UPDATE 1` | |
| 6 | | `UPDATE inventory SET reserved = 3 WHERE product_id = 15;` (app computed 0 + 3) → **(waits)** |
| 7 | `COMMIT;` | → `UPDATE 1` |
| 8 | | `COMMIT;` |
| 9 | `SELECT quantity, reserved FROM inventory WHERE product_id = 15;` → quantity 4, reserved **3** | |

Four units were sold and only three are reserved: A's reservation was **lost**, overwritten by B. Notice that B *did* wait for A at step 6. Row locks (section 10) worked perfectly: two writers of the same row never run at the same time. But waiting does not help when the new value was computed from a stale read. B's `SET reserved = 3` is a constant that the application derived from data that was out of date by the time it was written.

There are three standard fixes, and you will see all of them in this module:

1. **Let the database do the arithmetic**: `SET reserved = reserved + 3`. The expression is evaluated against the row version the `UPDATE` actually modifies (next section), so concurrent increments add up. This is the best fix when it fits.
2. **Lock the row before reading it**: `SELECT ... FOR UPDATE` (section 10). The second reader waits at its `SELECT` until the first commits, then reads the fresh value. This is called *pessimistic locking*.
3. **Detect the conflict and retry**: either with a version column (*optimistic locking*, section 15), or by running at `REPEATABLE READ`, where B's `UPDATE` at step 6 would fail with a serialization error instead of silently overwriting (section 7).

### 6.3 How UPDATE re-checks rows: EvalPlanQual

Fix 1 needs a closer look, because `READ COMMITTED` has a special rule for statements that modify or lock rows (`UPDATE`, `DELETE`, `SELECT ... FOR UPDATE/SHARE`, `MERGE`). Let's make the reservation atomic *and* conditional: reserve 3 units only if at least 3 are available.

| Step | Session A | Session B |
|---|---|---|
| 1 | `BEGIN;` | |
| 2 | `UPDATE inventory SET reserved = reserved + 3 WHERE product_id = 15 AND quantity - reserved >= 3 RETURNING quantity, reserved;` → quantity 4, reserved 3, `UPDATE 1` | |
| 3 | | `BEGIN;` |
| 4 | | the same `UPDATE` → **(waits)** |
| 5 | `COMMIT;` | → `(0 rows)`, `UPDATE 0` |
| 6 | | `UPDATE inventory SET reserved = reserved + 1 WHERE product_id = 15 AND quantity - reserved >= 1 RETURNING quantity, reserved;` → quantity 4, reserved 4, `UPDATE 1` |
| 7 | | `COMMIT;` |

No overselling, and no lost update. What happened inside B at step 4 and 5:

1. B's statement took its snapshot. In that snapshot, product 15 has `reserved = 0`, so the row matches `quantity - reserved >= 3`.
2. B tries to update that row and finds that its newest version was written by A, which is still running. B **waits** for A to finish.
3. A commits. Now B does not simply give up, and it does not blindly apply its change to the old version either. It fetches the **newest committed version** of that row (`reserved = 3`) and **re-evaluates the `WHERE` clause against it**: `4 - 3 >= 3` is false. So B skips the row: `UPDATE 0`.
4. If the condition had still been true, B would have applied `reserved + 3` to the *new* version (`reserved = 3`), not to the version in its snapshot.

This re-check is called **EvalPlanQual** (EPQ) after the function in the executor that does it. It is what makes `SET x = x + n` and conditional atomic updates safe under `READ COMMITTED`: the arithmetic and the condition always apply to the latest committed version of the row, while the row is locked. If A had rolled back, B would have proceeded with the version it originally saw.

EPQ has a sharp edge. The re-check applies **only to rows the statement had already found** in its snapshot and is about to modify. Rows that only start matching because of a concurrent change are never found, because the statement does not re-run its scan with a new snapshot. A restock job that adds 20 units to every product with 1 to 4 units left shows it:

| Step | Session A (warehouse corrections) | Session B (restock job) |
|---|---|---|
| 1 | `BEGIN;` | |
| 2 | `UPDATE inventory SET quantity = quantity + 3 WHERE product_id = 60;` (3 → 6: three units found) | |
| 3 | `UPDATE inventory SET quantity = quantity - 7 WHERE product_id = 12;` (10 → 3: seven units damaged) | |
| 4 | | `UPDATE inventory SET quantity = quantity + 20 WHERE quantity BETWEEN 1 AND 4 RETURNING product_id, quantity;` → **(waits)** |
| 5 | `COMMIT;` | → products 3 (22), 13 (21), 15 (24); `UPDATE 3` |

Before the demo, the products with 1 to 4 units were 3, 13, 15 and 60. After it:

```text
 product_id | quantity
------------+----------
          3 |       22
         12 |        3
         13 |       21
         15 |       24
         60 |        6
(5 rows)
```

- Product 60 was in B's snapshot with quantity 3. B waited for A's lock on it, re-checked the newest version (quantity 6), saw that it no longer matched, and skipped it.
- Product 12 had quantity 10 in B's snapshot, so B never considered it. After A's commit it has 3 units, yet the restock job, which was supposed to top up *every* product with 1 to 4 units, left it alone.

Compare with the two serial orders. A then B: product 60 is at 6 and not restocked, product 12 drops to 3 and *is* restocked to 23. B then A: product 60 is restocked to 23 and then gets its 3 found units (26), product 12 ends at 3. The concurrent run (60 at 6, 12 at 3) matches **neither**. Usually this is harmless (the next restock run picks up product 12), but it shows that under `READ COMMITTED` a data-modifying statement is consistent row by row, not as a whole.

> **In production:** `READ COMMITTED` is the default for good reasons. It never raises serialization errors, so code needs no retry loops for them, and its anomalies are easy to avoid in the common cases: use atomic `UPDATE ... SET x = x + n WHERE <guard>`, check the row count, and lock rows with `FOR UPDATE` before a read-modify-write. Bazaar's `place_order()` (section 16) is written for `READ COMMITTED`.

---

## 7. REPEATABLE READ

### 7.1 One snapshot for the whole transaction

Under `REPEATABLE READ`, the snapshot is taken at the **first statement** of the transaction (not at `BEGIN`) and used for every statement until the end. The transaction sees a frozen picture of the database, plus its own changes. This is called **snapshot isolation**. Same scenario as section 6.1:

| Step | Session A (shopper) | Session B (seller) |
|---|---|---|
| 1 | `BEGIN ISOLATION LEVEL REPEATABLE READ;` | |
| 2 | | `UPDATE products SET price = 239.00 WHERE id = 10;` |
| 3 | `SELECT price FROM products WHERE id = 10;` → **239.00** (snapshot taken now) | |
| 4 | `SELECT count(*) FROM promotions WHERE product_id = 10;` → **2** | |
| 5 | | `UPDATE products SET price = 249.00 WHERE id = 10;` |
| 6 | | `INSERT INTO promotions (product_id, discount_pct, active_during) VALUES (10, 10, tstzrange('2025-07-01', '2025-07-08'));` |
| 7 | `SELECT price FROM products WHERE id = 10;` → **239.00** | |
| 8 | `SELECT count(*) FROM promotions WHERE product_id = 10;` → **2** | |
| 9 | `COMMIT;` | |
| 10 | `SELECT price FROM products WHERE id = 10;` → **249.00** | |

Step 3 saw B's first change, because A's snapshot did not exist until that `SELECT`. After that, A's view never changed: no non-repeatable read (step 7) and no phantom (step 8). Note that the SQL standard allows phantoms at `REPEATABLE READ`; PostgreSQL's snapshot implementation prevents them as a side effect.

This is the level to use for reports and exports that run several queries and need them to agree with each other. `pg_dump` (module 18) runs in one `REPEATABLE READ` transaction for exactly this reason: every table in the dump comes from the same instant.

### 7.2 Concurrent updates: serialization failure 40001

What if a `REPEATABLE READ` transaction tries to modify a row that someone else changed after its snapshot was taken? Under `READ COMMITTED`, EPQ would re-check the newest version. That is impossible here: the transaction promised to work on its snapshot, and the newest version is not in it. So PostgreSQL refuses:

| Step | Session A (`REPEATABLE READ`) | Session B (`READ COMMITTED`) |
|---|---|---|
| 1 | `BEGIN ISOLATION LEVEL REPEATABLE READ;` | |
| 2 | `SELECT quantity, reserved FROM inventory WHERE product_id = 15;` → 4, 0 | |
| 3 | | `BEGIN;` |
| 4 | | `UPDATE inventory SET reserved = reserved + 1 WHERE product_id = 15;` → `UPDATE 1` |
| 5 | `UPDATE inventory SET reserved = reserved + 3 WHERE product_id = 15 AND quantity - reserved >= 3;` → **(waits)** | |
| 6 | → `ERROR:  could not serialize access due to concurrent update` | `COMMIT;` |
| 7 | `SELECT ...` → `ERROR:  current transaction is aborted, ...` | |
| 8 | `ROLLBACK;` | |

The error has SQLSTATE **`40001`** (`serialization_failure`). A waited at step 5 because B held the row lock. When B **committed**, A's transaction became impossible to complete consistently, and it was aborted. If B had **rolled back** instead, A's update would have gone ahead (we ran that variant too: A got `UPDATE 1`, and `reserved` ended at 4 after a second round).

A `40001` is not a bug; it is the database telling you "your transaction ran on stale data; run it again". **Retrying the whole transaction from the start** (with a new snapshot) is the correct and only response. On the retry, A reads `reserved = 1` and decides again. This also means that `REPEATABLE READ` prevents the lost update of section 6.2: B's stale `SET reserved = 3` would have failed instead of overwriting.

> **Pitfall:** retry the *transaction*, not the failed statement. The failed statement's input came from earlier statements of the same transaction, which saw stale data. Re-run everything from `BEGIN`, including the reads. Section 15.5 shows the loop.

### 7.3 Write skew

Snapshot isolation prevents lost updates because two transactions writing the *same row* conflict. But what about two transactions that each read the same data, and then write *different* rows? Neither sees the other's write, and neither write conflicts. This is **write skew**.

Bazaar's marketing team has a rule: *a seller may run at most three open-ended promotions* (promotions without an end date are dangerous because nobody remembers to stop them). The rule spans many rows, so no `CHECK` or `UNIQUE` constraint can express it. The admin tool checks it in a transaction before inserting. Seller 4 (*Threadbare Co.*) currently has two open-ended promotions, on products 47 and 48. Two marketing managers each add one at the same moment:

| Step | Session A | Session B |
|---|---|---|
| 1 | `BEGIN ISOLATION LEVEL REPEATABLE READ;` | |
| 2 | count open-ended promotions of seller 4 (query below) → **2**, so one more is allowed | |
| 3 | | `BEGIN ISOLATION LEVEL REPEATABLE READ;` |
| 4 | | the same count → **2**, so one more is allowed |
| 5 | `INSERT INTO promotions (product_id, discount_pct, active_during) VALUES (49, 20, tstzrange('2025-07-01', NULL));` | |
| 6 | | `INSERT INTO promotions (product_id, discount_pct, active_during) VALUES (50, 15, tstzrange('2025-07-01', NULL));` |
| 7 | `COMMIT;` | |
| 8 | | `COMMIT;` |
| 9 | the count → **4** | |

The count query:

```sql
SELECT count(*)
FROM promotions pr
JOIN products p ON p.id = pr.product_id
WHERE p.seller_id = 4
  AND upper_inf(pr.active_during);      -- no upper bound = open-ended
```

Both transactions committed, and the rule is broken: seller 4 now has four open-ended promotions. Each transaction was correct on its own, given its snapshot. Run serially, in either order, the second one would have seen 3 and refused. Snapshot isolation did not notice anything because the two inserted rows are different rows. Other textbook examples of write skew: two doctors both going off call because each saw the other still on call; two withdrawals from accounts with a shared overdraft limit; two bookings of the last two seats that each check "is the flight full?".

The same race happens under `READ COMMITTED`, of course. `REPEATABLE READ` stops anomalies on *the same row*; write skew is about *a set of rows defined by a condition*. The fixes:

- **`SERIALIZABLE`**, which detects exactly this pattern (next section).
- **Materialize the conflict**: make both transactions write the same row, for example by locking the seller's row first (`SELECT ... FROM sellers WHERE id = 4 FOR UPDATE`) in every transaction that adds a promotion. Now the second transaction waits at that lock, and under `READ COMMITTED` it then counts 3. It works, but only if *every* code path remembers to take the lock.
- **A constraint**, when the rule can be reshaped into one (for example a `promotion_slots` table with a `UNIQUE (seller_id, slot)` where `slot` is between 1 and 3).

---

## 8. SERIALIZABLE

### 8.1 The guarantee

`SERIALIZABLE` promises: **the result of any set of concurrently committed serializable transactions is the same as if they had run one at a time, in some order.** If PostgreSQL cannot guarantee that for a transaction, it aborts one of them with a `40001` serialization failure. Your application retries, and on the retry the transaction sees the other's committed work.

That is a remarkably useful promise. It means you can reason about each transaction as if it ran alone: if every transaction keeps the promotion rule when run alone, then the rule holds under any concurrency. You do not need to find every race and add a lock for it.

Here is the write skew demo again at `SERIALIZABLE`:

| Step | Session A | Session B |
|---|---|---|
| 1 | `BEGIN ISOLATION LEVEL SERIALIZABLE;` | |
| 2 | count → **2** | |
| 3 | | `BEGIN ISOLATION LEVEL SERIALIZABLE;` |
| 4 | | count → **2** |
| 5 | `INSERT ... (49, 20, ...)` → `INSERT 0 1` | |
| 6 | | `INSERT ... (50, 15, ...)` → `INSERT 0 1` |
| 7 | `COMMIT;` → `COMMIT` | |
| 8 | | `COMMIT;` → **error** (below) |
| 9 | count → **3** | |

```text
ERROR:  could not serialize access due to read/write dependencies among transactions
DETAIL:  Reason code: Canceled on identification as a pivot, during commit attempt.
HINT:  The transaction might succeed if retried.
```

B failed at commit. When B retries, it counts 3 and refuses to add a fourth promotion. The rule held, without any explicit lock.

### 8.2 How SSI works

PostgreSQL implements `SERIALIZABLE` with **Serializable Snapshot Isolation (SSI)**, an algorithm published in 2008 and added in PostgreSQL 9.1. It runs on top of snapshot isolation (each transaction still reads one snapshot, and readers still never block writers) and adds **conflict tracking**:

1. **Predicate locks (`SIReadLock`).** Every read in a serializable transaction leaves a marker saying "I read this". The marker is not a lock in the blocking sense: it never makes anyone wait. It only records what was read. Its granularity depends on how the data was read: a tuple, an index page, or a whole relation. While B was running in the demo above, `pg_locks` showed:

   ```text
    locktype |  relation  | page | tuple |    mode
   ----------+------------+------+-------+------------
    relation | products   |      |       | SIReadLock
    relation | promotions |      |       | SIReadLock
   (2 rows)
   ```

   Both tables were read with sequential scans (they are tiny), so the "I read this" marker covers each whole table. With an index scan the markers are finer. Reading product 15's inventory row through its primary key, with sequential scans disabled, records one tuple and the index page that was searched:

   ```text
    locktype |    relation    | page | tuple |    mode
   ----------+----------------+------+-------+------------
    page     | inventory_pkey |    1 |       | SIReadLock
    tuple    | inventory      |    0 |    15 | SIReadLock
   (2 rows)
   ```

   The index page marker is what catches phantoms: a concurrent insert of a new row with a key on that page is detected as "writing something A read".

2. **rw-dependencies.** When a transaction writes data that another *concurrent* serializable transaction has read (or reads data that a concurrent one has written, which its snapshot does not show), PostgreSQL records a **read-write antidependency** between them: "T1 read something that T2 then changed, so T1 must come *before* T2 in any equivalent serial order".

3. **Dangerous structures.** Theory shows that every non-serializable execution under snapshot isolation contains a pattern of two consecutive rw-dependencies, `T1 → T2 → T3`, where T2 is the "pivot" with both an incoming and an outgoing edge (T1 and T3 may be the same transaction). When PostgreSQL sees such a structure and the transactions involved could commit in an order that makes it a real cycle, it aborts one of them. In our demo, A read the promotions that B then wrote (A → B), and B read the promotions that A wrote (B → A). B was the pivot, and the check fired when B tried to commit.

SSI is **conservative**: it may abort transactions that would actually have been serializable (false positives), for example when a relation-level `SIReadLock` from a sequential scan makes two transactions look like they read each other's rows when they did not. It never lets a real anomaly through, among serializable transactions.

Some consequences worth knowing:

- **Only serializable transactions are protected.** The guarantee holds among transactions that all run at `SERIALIZABLE`. A `READ COMMITTED` transaction that writes the same data is invisible to the tracking. Set `default_transaction_isolation = 'serializable'` for the whole database or role if you choose this approach, so nobody forgets.
- **Good indexes reduce false positives.** A sequential scan locks the whole table, so any write to that table conflicts with it. An index scan locks only the pages it visited.
- **Predicate locks are bounded.** Shared memory holds a limited number of them (`max_pred_locks_per_transaction`, default 64, times `max_connections`). When a transaction holds too many fine-grained locks on one page or relation, they are promoted to a coarser lock (`max_pred_locks_per_page`, `max_pred_locks_per_relation`), which increases false positives. Under heavy serializable workloads you may need to raise them.
- **Some errors change code.** Under `SERIALIZABLE`, a unique violation caused by a concurrent transaction (for example, both transactions checked that a key was free, then both inserted it) is reported as `40001` instead of `23505` when PostgreSQL can tell, so that the retry logic handles it.
- Hot standby replicas (module 19) cannot run `SERIALIZABLE` transactions.

### 8.3 The read-only anomaly

Here is an anomaly that is surprising because one of the three transactions involved **only reads**, and the anomaly only becomes visible *through* that reader. Bazaar pays sellers in batches. Each sale is recorded in the current, open batch. A finance job closes the batch by incrementing the batch counter, and once a batch is closed, a payout report sums it and pays the sellers. In a scratch schema:

```sql
CREATE TABLE lab.payout_batches (current_batch int NOT NULL);
INSERT INTO lab.payout_batches VALUES (1);
CREATE TABLE lab.batch_sales (batch int NOT NULL, order_id bigint NOT NULL, amount numeric(12,2) NOT NULL);
INSERT INTO lab.batch_sales VALUES (1, 101, 120.00), (1, 102, 80.00);
```

Three sessions, all at `REPEATABLE READ`. S records a sale; C closes the batch; R is the payout report:

| Step | Session S (sale) | Session C (close batch) | Session R (report, read-only) |
|---|---|---|---|
| 1 | `BEGIN ISOLATION LEVEL REPEATABLE READ;` | | |
| 2 | `SELECT current_batch FROM lab.payout_batches;` → **1** | | |
| 3 | | `BEGIN ISOLATION LEVEL REPEATABLE READ;` | |
| 4 | | `UPDATE lab.payout_batches SET current_batch = current_batch + 1;` | |
| 5 | | `COMMIT;` | |
| 6 | | | `BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;` |
| 7 | | | `SELECT current_batch FROM lab.payout_batches;` → **2**, so batch 1 is closed |
| 8 | | | `SELECT sum(amount) FROM lab.batch_sales WHERE batch = 1;` → **200.00**: pay that |
| 9 | | | `COMMIT;` |
| 10 | `INSERT INTO lab.batch_sales VALUES (1, 103, 50.00);` → `INSERT 0 1` | | |
| 11 | `COMMIT;` → `COMMIT` | | |
| 12 | | | `SELECT sum(amount) FROM lab.batch_sales WHERE batch = 1;` → **250.00** |

The report paid out 200.00 for a batch it saw as closed, and afterwards the "closed" batch grew to 250.00. Without R, there is no anomaly: S and C are equivalent to "S, then C" (S recorded its sale in batch 1 before the batch was closed). It is R that saw a state ("batch 1 closed, total 200") that could not exist in any serial order. If S came before C, R would see 250; if S came after C, S would have recorded the sale in batch 2.

At `SERIALIZABLE`, the same steps go differently. R's reads complete the dangerous structure, and S is aborted as soon as it tries to write:

```text
INSERT INTO lab.batch_sales VALUES (1, 103, 50.00);
ERROR:  could not serialize access due to read/write dependencies among transactions
DETAIL:  Reason code: Canceled on identification as a pivot, during write.
HINT:  The transaction might succeed if retried.
```

S retries, reads `current_batch = 2`, and records its sale in batch 2. R's 200.00 stays correct.

### 8.4 Retries are part of the contract

Under `SERIALIZABLE` (and `REPEATABLE READ`), serialization failures are normal events, not exceptions to page someone about. Any transaction can fail with `40001`, at any statement, including `COMMIT`. Correct applications wrap such transactions in a retry loop (section 15.5).

`pgbench`, PostgreSQL's benchmarking tool, can retry by itself, which makes it a nice way to see retries at scale. The scratch table `lab.featured_products` holds a seller's featured products, with the rule "at most 3 per seller", enforced by a conditional insert. The script [`examples/featured_cap.sql`](examples/featured_cap.sql) runs this transaction:

```sql
BEGIN ISOLATION LEVEL SERIALIZABLE;      -- or REPEATABLE READ
INSERT INTO lab.featured_products (seller_id, product_id)
SELECT 4, :pid
WHERE (SELECT count(*) FROM lab.featured_products WHERE seller_id = 4) < 3;
COMMIT;
```

Eight clients each run it 20 times, with `--max-tries=20` so pgbench retries serialization failures. Three runs at each level, starting from an empty table each time:

| Level | Rows at the end (rule: at most 3) | Transactions retried | Total retries |
|---|---|---|---|
| `REPEATABLE READ` | 9, 8, 10 | 0 | 0 |
| `SERIALIZABLE` | 3, 3, 3 | 7, 6, 3 | 19, 12, 3 |

`REPEATABLE READ` never raised an error and broke the rule every time. `SERIALIZABLE` held the rule every time, at the cost of a few percent of transactions being retried.

### 8.5 `READ ONLY DEFERRABLE`

A long read-only report at `SERIALIZABLE` has two problems: it can be aborted (as in 8.3, where the reader was part of the dangerous structure), and its predicate locks make other transactions fail more often. PostgreSQL has a special mode for it:

```sql
BEGIN ISOLATION LEVEL SERIALIZABLE READ ONLY DEFERRABLE;
```

The transaction **waits** at its first query until it can take a **safe snapshot**: one for which no concurrent read-write serializable transaction can create a dangerous structure. From then on it runs with no predicate locks and **can never fail** with a serialization error. While a serializable writer was open in another session, the report's first query waited, visibly:

```text
 pid  | wait_event_type |  wait_event  |                  query
------+-----------------+--------------+------------------------------------------
 1934 | IPC             | SafeSnapshot | SELECT sum(amount) FROM lab.batch_sales;
(1 row)
```

It ran as soon as the writer committed. The wait is usually short, but it is unbounded on a busy system. Use it for reports and exports in a database that uses `SERIALIZABLE`; `pg_dump --serializable-deferrable` (module 18) does exactly this.

### 8.6 Which level should Bazaar use?

- **`READ COMMITTED`** for most OLTP code, with the patterns of sections 10 and 15 (atomic updates, `FOR UPDATE`, consistent lock order). It is the default, and every driver and ORM assumes it. `place_order()` uses it.
- **`REPEATABLE READ`** for multi-query reads that must agree (reports, exports, an API response assembled from several queries), and for read-modify-write transactions where you prefer a `40001` and a retry over a silent lost update.
- **`SERIALIZABLE`** when correctness depends on rules over *sets* of rows (write skew territory), or when the team cannot audit every transaction for races. Budget for retries and for some extra CPU. Many teams run it everywhere and are happy with it.

---

## 9. The anomalies table

| Anomaly | What happens | Bazaar example | `READ COMMITTED` | `REPEATABLE READ` | `SERIALIZABLE` |
|---|---|---|---|---|---|
| **Dirty read** | You read data another transaction has not committed (and may roll back). | A report counts an order whose checkout then fails. | prevented | prevented | prevented |
| **Non-repeatable read** | Reading the same row twice gives different values. | The price changes between showing the cart and computing the total (6.1). | possible | prevented | prevented |
| **Phantom read** | Re-running a query returns a different set of rows. | The number of promotions changes within one transaction (6.1). | possible | prevented (the SQL standard would allow it) | prevented |
| **Lost update** | Two read-modify-write cycles on one row; one overwrites the other. | Two reservations computed from the same stale `reserved` (6.2). | possible | prevented (`40001`) | prevented (`40001`) |
| **Write skew** | Two transactions read overlapping data, then write *different* rows, together breaking a rule. | Two managers each add the "third" open-ended promotion (7.3). | possible | possible | prevented (`40001`) |
| **Read-only anomaly** | A reader sees a state that no serial order could produce. | A payout report sees a batch as closed before its last sale lands (8.3). | possible | possible | prevented (`40001`) |

Two remarks. First, "possible" means the database will not stop it; your code can still prevent it, with the techniques in this module. Second, `READ COMMITTED` shows each *statement* a consistent snapshot, so anomalies only appear *between* statements of a transaction, plus the EPQ quirk of section 6.3 within data-modifying statements.

> **Pitfall:** isolation levels do not map one-to-one between databases. MySQL's InnoDB `REPEATABLE READ` lets an `UPDATE` see and modify the latest committed row version (no `40001`), so code ported from MySQL may suddenly see serialization failures on PostgreSQL. Oracle's `SERIALIZABLE` is really snapshot isolation and allows write skew. Read the actual guarantees, not the names.

---

## 10. Row-level locks

MVCC lets readers and writers coexist. Writers of the **same row** still need to take turns, and that is what row-level locks are for. You have already seen them in action: B's `UPDATE` waiting for A's in sections 6.2 and 6.3.

### 10.1 The four modes

Every `UPDATE` and `DELETE` locks each row it modifies until the end of the transaction. You can also lock rows explicitly when you read them, with a locking clause on `SELECT`. There are four modes, from strongest to weakest:

| Mode | Taken implicitly by | Taken explicitly to... |
|---|---|---|
| `FOR UPDATE` | `DELETE`; an `UPDATE` that changes a column of a unique index that a foreign key could reference (a "key" column) | ...read a row you are about to delete or re-key, or simply "lock it completely". |
| `FOR NO KEY UPDATE` | an `UPDATE` that changes no key column (the usual case) | ...read a row you are about to update. |
| `FOR SHARE` | (nothing implicit) | ...make sure nobody changes the row until you commit, while letting others do the same. |
| `FOR KEY SHARE` | **foreign key checks** on the referenced row | ...make sure the row keeps existing with the same key. |

Which mode blocks which (X = the second request waits):

| Requested \ Held by another transaction | `FOR KEY SHARE` | `FOR SHARE` | `FOR NO KEY UPDATE` | `FOR UPDATE` |
|---|---|---|---|---|
| `FOR KEY SHARE` | | | | X |
| `FOR SHARE` | | | X | X |
| `FOR NO KEY UPDATE` | | X | X | X |
| `FOR UPDATE` | X | X | X | X |

The plain-English version: two writers of the same row always conflict. Share locks coexist with other share locks. `KEY SHARE` is the weakest and conflicts only with `FOR UPDATE`, that is, with deleting the row or changing its key. Row locks never block plain `SELECT`s (that is MVCC's promise).

Locking reads are written like this:

```sql
SELECT product_id, quantity, reserved
FROM inventory
WHERE product_id IN (1, 24)
ORDER BY product_id
FOR UPDATE;                    -- or FOR NO KEY UPDATE / FOR SHARE / FOR KEY SHARE

-- in a join, choose which tables' rows to lock:
SELECT i.product_id, i.quantity - i.reserved AS available, p.price
FROM inventory i
JOIN products p ON p.id = i.product_id
WHERE i.product_id = 15
FOR UPDATE OF i;               -- locks the inventory row only, not the product row
```

A locking `SELECT` under `READ COMMITTED` behaves like an `UPDATE` (section 6.3): if it has to wait for a concurrent writer, it then re-checks the newest version and returns *that*, not the version from its snapshot. That makes "lock, then read" return fresh data. Under `REPEATABLE READ` and `SERIALIZABLE`, it fails with `40001` instead if the row changed after the snapshot.

### 10.2 Why foreign keys take `KEY SHARE`

When you insert an `order_items` row for product 15, PostgreSQL must make sure product 15 exists *and keeps existing* until your transaction commits; otherwise someone could delete the product in between and leave an orphan item. So the foreign key check locks the referenced `products` row in `FOR KEY SHARE` mode. Why that weakest mode? Because the most common concurrent operation on a product is an ordinary update (price, stock status, description) that does not touch its key. `FOR NO KEY UPDATE` and `FOR KEY SHARE` do not conflict, so a seller editing a price does not block checkouts of that product, and vice versa:

| Step | Session A (seller) | Session B (checkout) |
|---|---|---|
| 1 | `BEGIN;` | |
| 2 | `UPDATE products SET price = 1749.00 WHERE id = 15;` (holds `NO KEY UPDATE`) | |
| 3 | | `BEGIN;` |
| 4 | | `INSERT INTO orders (user_id, seller_id) VALUES (8, 1) RETURNING id;` → 121 |
| 5 | | `INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (121, 15, 1, 1799.00);` → `INSERT 0 1`, **no wait** |
| 6 | `ROLLBACK;` | `ROLLBACK;` |

At step 5, the `pgrowlocks` extension (a contrib module that lists the locks recorded in a table's rows: `CREATE EXTENSION pgrowlocks;`) shows both locks on the same product row:

```sql
SELECT locked_row, multi, xids, modes FROM pgrowlocks('products');
```

```text
 locked_row | multi |    xids     |               modes
------------+-------+-------------+-----------------------------------
 (1,25)     | t     | {1430,1431} | {"No Key Update","For Key Share"}
(1 row)
```

Now repeat the experiment, but let A take `FOR UPDATE` (as a `DELETE` would): `SELECT id FROM products WHERE id = 15 FOR UPDATE;`. This time B's `INSERT INTO order_items` **waits** until A ends.

Before PostgreSQL 9.3 there were only `FOR UPDATE` and `FOR SHARE`, foreign key checks took `FOR SHARE`, and every price update on a popular product blocked every checkout of it. The two "key" modes were added for exactly this.

> **Under the hood:** row locks are not stored in the shared lock table. They are written into the row itself: the locker's xid goes into `xmax` with flag bits saying "locked, not deleted" and in which mode (section 4.1's `xmax` puzzle). When several transactions lock the same row, as above, `xmax` holds a **MultiXact id**: a pointer to a list of (xid, mode) pairs stored in `pg_multixact`. That is what `multi = t` means. Consequently, a transaction can lock millions of rows without using shared memory, but locking rows *writes* to the table (dirtying pages, generating WAL), and heavy `FOR SHARE`/`FOR KEY SHARE` traffic can make `pg_multixact` grow and need vacuuming (module 16).

### 10.3 Waiting less: `NOWAIT` and `SKIP LOCKED`

By default a locking statement waits as long as it takes. Two modifiers change that:

| Step | Session A | Session B |
|---|---|---|
| 1 | `BEGIN;` | |
| 2 | `SELECT product_id, quantity, reserved FROM inventory WHERE product_id = 15 FOR UPDATE;` → 15, 4, 0 | |
| 3 | | `SELECT product_id, quantity, reserved FROM inventory WHERE product_id = 15 FOR UPDATE NOWAIT;` → `ERROR:  could not obtain lock on row in relation "inventory"` |
| 4 | | `SELECT product_id, quantity, reserved FROM inventory WHERE product_id IN (13, 15, 16) ORDER BY product_id FOR UPDATE SKIP LOCKED;` → rows 13 and 16 only |
| 5 | | `SELECT product_id, quantity, reserved FROM inventory WHERE product_id IN (13, 15, 16) ORDER BY product_id;` → all three rows |
| 6 | `ROLLBACK;` | |

- **`NOWAIT`** fails immediately with SQLSTATE `55P03` (`lock_not_available`) instead of waiting. Use it for interactive actions where "someone else is editing this, try again later" is a better answer than a spinner.
- **`SKIP LOCKED`** silently leaves out rows it cannot lock right away. The result is deliberately an *inconsistent* view of the table (product 15 exists, but B's step 4 does not return it). That is useless for most queries, but perfect for one: distributing work items between competing workers, where each worker wants *any* available item, not a particular one. Section 17 builds Bazaar's job queue on it.
- Step 5 shows again that plain reads ignore row locks.

`lock_timeout` (section 11.5) is the middle ground: wait, but not longer than a given time.

---

## 11. Table-level locks

### 11.1 Why tables need locks too

While a query reads `products`, nobody may drop the table, rewrite it, or change a column's type underneath it. So every statement also locks the **tables** (and indexes) it touches, in one of eight modes, until the end of its transaction. These locks live in the shared **lock table** in memory, and you see them in `pg_locks`. A simple update takes these:

```sql
BEGIN;
UPDATE inventory SET reserved = reserved + 1 WHERE product_id = 15;
SELECT locktype, relation::regclass AS relation, virtualxid, transactionid, mode, granted
FROM pg_locks
WHERE pid = pg_backend_pid()
ORDER BY locktype, relation::regclass::text;
ROLLBACK;
```

```text
   locktype    |    relation    | virtualxid | transactionid |       mode       | granted
---------------+----------------+------------+---------------+------------------+---------
 relation      | inventory      |            |               | RowExclusiveLock | t
 relation      | inventory_pkey |            |               | RowExclusiveLock | t
 relation      | pg_locks       |            |               | AccessShareLock  | t
 transactionid |                |            |          1453 | ExclusiveLock    | t
 virtualxid    |                | 89/17      |               | ExclusiveLock    | t
(5 rows)
```

`ROW EXCLUSIVE` on the table and its index (the "row" in the name is historical; it is a table lock that writers take). Every transaction also holds an exclusive lock on its own **virtual transaction id**, and, once it writes, on its **transaction id**. Those two exist so that others can *wait for this transaction to end*: a session blocked on one of our rows requests a `ShareLock` on our `transactionid`, which is granted when we commit or roll back (section 12).

### 11.2 The eight modes in plain terms

| # | Mode | Taken by | Conflicts with modes |
|---|---|---|---|
| 1 | `ACCESS SHARE` | `SELECT`, any read | 8 |
| 2 | `ROW SHARE` | `SELECT ... FOR UPDATE/SHARE` | 7, 8 |
| 3 | `ROW EXCLUSIVE` | `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `COPY FROM` | 5, 6, 7, 8 |
| 4 | `SHARE UPDATE EXCLUSIVE` | `VACUUM` (not `FULL`), `ANALYZE`, `CREATE INDEX CONCURRENTLY`, `REINDEX CONCURRENTLY`, `CREATE STATISTICS`, `COMMENT ON`, `ALTER TABLE ... VALIDATE CONSTRAINT`, `ALTER TABLE ... SET STATISTICS` | 4, 5, 6, 7, 8 |
| 5 | `SHARE` | `CREATE INDEX` (not concurrently), `REINDEX` (on the table) | 3, 4, 6, 7, 8 |
| 6 | `SHARE ROW EXCLUSIVE` | `CREATE TRIGGER`, `ALTER TABLE ... ADD FOREIGN KEY` (on both tables) | 3, 4, 5, 6, 7, 8 |
| 7 | `EXCLUSIVE` | `REFRESH MATERIALIZED VIEW CONCURRENTLY` | 2, 3, 4, 5, 6, 7, 8 |
| 8 | `ACCESS EXCLUSIVE` | `DROP TABLE`, `TRUNCATE`, `VACUUM FULL`, `CLUSTER`, `REFRESH MATERIALIZED VIEW`, `LOCK TABLE` (default), and **most `ALTER TABLE` forms**: `ADD COLUMN`, `DROP COLUMN`, `ALTER COLUMN TYPE`, `SET DEFAULT`, `SET NOT NULL`, `ADD CHECK` (even `NOT VALID`), `RENAME` | all, 1 to 8 |

(The lock modes listed for `ADD FOREIGN KEY`, `VALIDATE CONSTRAINT`, `SET STATISTICS`, `SET DEFAULT`, `ADD CHECK ... NOT VALID`, `CREATE TRIGGER`, `REINDEX` and `REFRESH ... CONCURRENTLY` were all checked in `pg_locks` on PostgreSQL 17.)

You do not need to memorize the matrix. Remember these consequences instead:

- **Reads conflict only with `ACCESS EXCLUSIVE`.** Plain `SELECT`s are blocked by nothing else: not by writes, not by `CREATE INDEX`, not by `VACUUM`.
- **Writes (3) conflict with 5 and up.** A plain `CREATE INDEX` (5) blocks all inserts, updates and deletes for as long as the build takes, but not reads. `CREATE INDEX CONCURRENTLY` (4) blocks neither.
- **Mode 4 conflicts with itself**, so two `VACUUM`s, or a `VACUUM` and a `CREATE INDEX CONCURRENTLY`, cannot run on the same table at the same time.
- **`ACCESS EXCLUSIVE` (8) blocks everything, including plain `SELECT`s.** Most `ALTER TABLE` forms take it. Even when the change itself is instant (adding a nullable column only updates the catalog), the statement first has to *get* the lock, and that is where the trouble starts (section 11.4).
- `SHARE` (5) does not conflict with itself: two `CREATE INDEX` statements on the same table can run in parallel.

You can take a table lock explicitly with `LOCK TABLE name IN <mode> MODE`, but that is rarely needed in application code.

### 11.3 `CREATE INDEX` versus `CREATE INDEX CONCURRENTLY`

A plain `CREATE INDEX` holds `SHARE` until its transaction ends. Reads continue, writes wait:

| Step | Session A | Session B |
|---|---|---|
| 1 | `BEGIN;` | |
| 2 | `CREATE INDEX inventory_reserved_idx ON inventory (reserved);` | |
| 3 | | `SELECT quantity, reserved FROM inventory WHERE product_id = 1;` → 25, 0 (no wait) |
| 4 | | `UPDATE inventory SET reserved = reserved + 1 WHERE product_id = 1;` → **(waits)** |
| 5 | `ROLLBACK;` | → `UPDATE 1` |

At step 4, `pg_locks` for `inventory` showed A's granted `ShareLock` and B's ungranted `RowExclusiveLock`. On Bazaar's large `order_items` table (module 11), a plain index build takes long enough to stall every checkout.

`CREATE INDEX CONCURRENTLY` takes only `SHARE UPDATE EXCLUSIVE`, so writes continue. It pays for this by scanning the table twice and **waiting for transactions that might still need the old view of the table**. It also cannot run inside a transaction block (section 2.4). One consequence surprises people: it waits for any transaction holding an older snapshot, *even one that never touched the table being indexed*:

| Step | Session A (long report) | Session B (DBA) | Session C (checkout) |
|---|---|---|---|
| 1 | `BEGIN ISOLATION LEVEL REPEATABLE READ;` | | |
| 2 | `SELECT count(*) FROM orders;` → 120 | | |
| 3 | | `CREATE INDEX CONCURRENTLY inventory_reserved_idx ON inventory (reserved);` → **(waits)** | |
| 4 | | | `UPDATE inventory SET reserved = reserved + 1 WHERE product_id = 1;` → `UPDATE 1` (no wait) |
| 5 | `COMMIT;` | → `CREATE INDEX` | |

While it waited, the progress view said so:

```sql
SELECT phase FROM pg_stat_progress_create_index;
```

```text
           phase
---------------------------
 waiting for old snapshots
(1 row)
```

So a forgotten idle-in-transaction session can make a concurrent index build hang forever. Module 11 covers `CREATE INDEX CONCURRENTLY` in depth, including what happens when it fails halfway (it leaves an `INVALID` index behind).

### 11.4 The lock queue problem

Lock requests on a table wait in a **queue**, and a new request has to wait not only for conflicting locks that are *held*, but also for conflicting requests that are *queued ahead of it*. This rule prevents starvation (otherwise a steady stream of readers could keep an `ALTER TABLE` waiting forever), but it creates the most common self-inflicted outage in PostgreSQL. Watch:

| Step | Session A (analyst) | Session B (migration) | Session C (website) |
|---|---|---|---|
| 1 | `BEGIN;` | | |
| 2 | `SELECT count(*) FROM products;` → 62, then goes to lunch, transaction open | | |
| 3 | | `ALTER TABLE products ADD COLUMN weight_g int;` → **(waits)** | |
| 4 | | | `SELECT title FROM products WHERE id = 1;` → **(waits!)** |
| 5 | `COMMIT;` | → `ALTER TABLE` | → `AeroBook 13 Laptop` |

At step 4, a monitoring session showed this:

```sql
SELECT pid, pg_blocking_pids(pid) AS blocked_by, state, wait_event_type, left(query, 45) AS query
FROM pg_stat_activity
WHERE datname = 'bazaar' AND pid <> pg_backend_pid() AND state <> 'idle'
ORDER BY backend_start;
```

```text
 pid  | blocked_by |        state        | wait_event_type |                     query
------+------------+---------------------+-----------------+-----------------------------------------------
 2415 | {}         | idle in transaction | Client          | SELECT count(*) FROM products;
 2430 | {2415}     | active              | Lock            | ALTER TABLE products ADD COLUMN weight_g int;
 2437 | {2430}     | active              | Lock            | SELECT title FROM products WHERE id = 1;
(3 rows)
```

```sql
SELECT pid, mode, granted
FROM pg_locks
WHERE locktype = 'relation' AND relation = 'products'::regclass
ORDER BY granted DESC, pid;
```

```text
 pid  |        mode         | granted
------+---------------------+---------
 2415 | AccessShareLock     | t
 2430 | AccessExclusiveLock | f
 2437 | AccessShareLock     | f
(3 rows)
```

The website's simple `SELECT` (2437) wants `ACCESS SHARE`, which is perfectly compatible with the analyst's granted `ACCESS SHARE`. But it is queued behind the `ALTER TABLE`'s `ACCESS EXCLUSIVE` request, which conflicts with everything. So the `SELECT` waits for the `ALTER`, which waits for the analyst's lunch. In production, every request that touches `products` piles up behind the migration within seconds, the connection pool runs dry, and the site is down, all because of an `ALTER TABLE` that would have taken a millisecond.

### 11.5 The remedy: `lock_timeout`

The cure is to make the DDL give up quickly if it cannot get its lock, and retry later:

| Step | Session A (analyst) | Session B (migration) | Session C (website) |
|---|---|---|---|
| 1 | `BEGIN;` | | |
| 2 | `SELECT count(*) FROM products;` | | |
| 3 | | `SET lock_timeout = '2s';` | |
| 4 | | `ALTER TABLE products ADD COLUMN weight_g int;` → **(waits)** | |
| 5 | | | `SELECT title FROM products WHERE id = 1;` → **(waits)** |
| 6 | | after 2 s: `ERROR:  canceling statement due to lock timeout` | immediately: `AeroBook 13 Laptop` |
| 7 | `COMMIT;` | | |
| 8 | | `ALTER TABLE products ADD COLUMN weight_g int;` → `ALTER TABLE` | |

The website stalled for two seconds instead of indefinitely, and the migration succeeded on the retry. `lock_timeout` limits how long any single lock wait may take; the error has SQLSTATE `55P03`, like `NOWAIT`. The standard recipe for schema changes on a busy database:

```sql
SET lock_timeout = '2s';        -- per session or per migration; 50 ms to a few seconds
ALTER TABLE products ADD COLUMN weight_g int;
-- on 55P03: wait a bit, retry; after N attempts, alert a human
```

Module 26 builds this into Alembic migrations, together with the other zero-downtime techniques (`NOT VALID` constraints, `CREATE INDEX CONCURRENTLY`, expand/contract). Do not confuse `lock_timeout` with `statement_timeout`, which limits the total run time of a statement (section 18).

---

## 12. Seeing locks and blocking

When something hangs, you need to answer two questions: *who is waiting?* and *on whom?* The tools:

- **`pg_stat_activity`**: one row per connection. `state` (`active`, `idle`, `idle in transaction`, `idle in transaction (aborted)`), `wait_event_type`/`wait_event` (`Lock` + `relation`, `transactionid`, `tuple`... when waiting for a heavyweight lock), `xact_start`, `query_start`, `backend_xmin` and the current or last `query`.
- **`pg_locks`**: one row per lock held *or requested*, with `granted = false` for the requests that wait. The `locktype` says what is locked: `relation` (a table or index), `transactionid` / `virtualxid` (a transaction, used to wait for it to end), `tuple` (a row, used only to queue waiters, see below), `advisory` (section 14), `object`, `page`, and so on.
- **`pg_blocking_pids(pid)`**: the pids that block a given pid, computed from the lock table. Much easier than joining `pg_locks` to itself.

### 12.1 Waiting for a row

Three sessions update the same inventory row:

| Step | Session A (pid 3592) | Session B (pid 3608) | Session C (pid 3616) |
|---|---|---|---|
| 1 | `BEGIN;` | | |
| 2 | `UPDATE inventory SET quantity = quantity + 10 WHERE product_id = 15;` | | |
| 3 | | `UPDATE inventory SET reserved = reserved + 1 WHERE product_id = 15;` → **(waits)** | |
| 4 | | | `UPDATE inventory SET reserved = reserved + 2 WHERE product_id = 15;` → **(waits)** |
| 5 | `COMMIT;` | → `UPDATE 1` | → `UPDATE 1` |

The row-related entries in `pg_locks` at step 4:

```text
 pid  |   locktype    | relation  | page | tuple | xid  |     mode      | granted
------+---------------+-----------+------+-------+------+---------------+---------
 3592 | transactionid |           |      |       | 1957 | ExclusiveLock | t
 3608 | transactionid |           |      |       | 1957 | ShareLock     | f
 3608 | tuple         | inventory |    0 |    15 |      | ExclusiveLock | t
 3616 | tuple         | inventory |    0 |    15 |      | ExclusiveLock | f
(4 rows)
```

Read it like this. A (xid 1957) holds the exclusive lock on its own transaction id. B found the row with A's xid in `xmax`, so it waits for **A's transaction to end** (a `ShareLock` on transactionid 1957, not granted). B is first in line, which it records by holding a **tuple lock** on the row `(0,15)`. C arrived later, and waits for that tuple lock. This two-level dance keeps the waiting order fair without storing every row lock in shared memory. It also explains the `wait_event`s you will see: `transactionid` for the first waiter, `tuple` for the ones behind it.

### 12.2 A blocking report

Here is a query worth saving. It lists every session that blocks others or is blocked, with how long its transaction has been open:

```sql
SELECT pid,
       pg_blocking_pids(pid)                 AS blocked_by,
       state,
       wait_event_type || ':' || wait_event  AS waiting_on,
       now() - xact_start                    AS xact_age,
       left(query, 40)                       AS query
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND state <> 'idle'
ORDER BY backend_start;
```

```text
 pid  | blocked_by |        state        |     waiting_on     |    xact_age     |                  query
------+------------+---------------------+--------------------+-----------------+------------------------------------------
 3592 | {}         | idle in transaction | Client:ClientRead  | 00:00:03.506348 | UPDATE inventory SET quantity = quantity
 3608 | {3592}     | active              | Lock:transactionid | 00:00:01.997125 | UPDATE inventory SET reserved = reserved
 3616 | {3608}     | active              | Lock:tuple         | 00:00:01.291355 | UPDATE inventory SET reserved = reserved
(3 rows)
```

For deeper chains, a recursive CTE (module 06) turns `pg_blocking_pids` into a tree rooted at the sessions that block others without being blocked themselves:

```sql
WITH RECURSIVE activity AS (
    SELECT pid, pg_blocking_pids(pid) AS blocked_by, state, query, xact_start
    FROM pg_stat_activity
    WHERE datname = current_database() AND pid <> pg_backend_pid()
), tree AS (
    SELECT pid, blocked_by, state, query, xact_start, 0 AS depth, ARRAY[pid] AS path
    FROM activity a
    WHERE cardinality(blocked_by) = 0                                   -- not blocked...
      AND EXISTS (SELECT 1 FROM activity b WHERE a.pid = ANY (b.blocked_by))  -- ...but blocking
    UNION ALL
    SELECT a.pid, a.blocked_by, a.state, a.query, a.xact_start, t.depth + 1, t.path || a.pid
    FROM activity a
    JOIN tree t ON t.pid = ANY (a.blocked_by)
    WHERE NOT a.pid = ANY (t.path)                                      -- guard against cycles
)
SELECT repeat('    ', depth) || pid AS pid_tree, state, now() - xact_start AS xact_age, left(query, 50) AS query
FROM tree
ORDER BY path;
```

```text
   pid_tree   |        state        |    xact_age     |                       query
--------------+---------------------+-----------------+----------------------------------------------------
 3592         | idle in transaction | 00:00:04.208147 | UPDATE inventory SET quantity = quantity + 10 WHER
     3608     | active              | 00:00:02.698924 | UPDATE inventory SET reserved = reserved + 1 WHERE
         3616 | active              | 00:00:01.993154 | UPDATE inventory SET reserved = reserved + 2 WHERE
(3 rows)
```

The root, 3592, is **idle in transaction**: it is not doing anything, just holding its locks while the client does something else. That is the typical root of a blocking chain. Note that for an idle session, `query` shows the *last* statement it ran, which is often not the one that took the blocking lock.

### 12.3 Ending a blocker

Two functions, both usable by superusers and by members of `pg_signal_backend` (for non-superuser targets):

- `pg_cancel_backend(pid)`: cancels the **current statement** of that session (like Ctrl+C). It does nothing to an idle-in-transaction session, which has no running statement.
- `pg_terminate_backend(pid)`: closes the **connection**. Its open transaction is rolled back and its locks are released. This is what you use on an idle-in-transaction root blocker.

Before terminating, check what the session is (`application_name`, `client_addr`, `usename` in `pg_stat_activity`). Killing the nightly billing job's connection is a different decision from killing an analyst's forgotten psql.

> **In production:** set `log_lock_waits = on`. Then every lock wait that lasts longer than `deadlock_timeout` (1 s by default) is written to the server log with the waiting and the blocking pids and queries, so you can investigate blocking after the fact. Module 17 covers logging and monitoring.

---

## 13. Deadlocks

### 13.1 Two transactions waiting for each other

A **deadlock** is a cycle of waits: A waits for a lock that B holds, while B waits for a lock that A holds. Neither can ever proceed. Two checkouts reserving products 1 and 2 in opposite orders:

| Step | Session A | Session B |
|---|---|---|
| 1 | `BEGIN;` | |
| 2 | `UPDATE inventory SET reserved = reserved + 1 WHERE product_id = 1;` | |
| 3 | | `BEGIN;` |
| 4 | | `UPDATE inventory SET reserved = reserved + 1 WHERE product_id = 2;` |
| 5 | `UPDATE inventory SET reserved = reserved + 1 WHERE product_id = 2;` → **(waits for B)** | |
| 6 | 1 s after step 5: **deadlock error** (below) | `UPDATE inventory SET reserved = reserved + 1 WHERE product_id = 1;` → (waits for A), then `UPDATE 1` |
| 7 | `COMMIT;` → `ROLLBACK` | |
| 8 | | `ROLLBACK;` |

This run had B's step 6 less than 1 s after A's step 5, so A got the error. If you type the steps by hand, you'll usually take longer than that: then B gets the deadlock error, 1 s after its step 6, and A's `UPDATE` completes. Section 13.2 explains why.

```text
ERROR:  deadlock detected
DETAIL:  Process 2919 waits for ShareLock on transaction 1745; blocked by process 2934.
Process 2934 waits for ShareLock on transaction 1744; blocked by process 2919.
HINT:  See server log for query details.
CONTEXT:  while locking tuple (0,2) in relation "inventory"
```

The server log has the statements involved:

```text
ERROR:  deadlock detected
DETAIL:  Process 2919 waits for ShareLock on transaction 1745; blocked by process 2934.
	Process 2934 waits for ShareLock on transaction 1744; blocked by process 2919.
	Process 2919: UPDATE inventory SET reserved = reserved + 1 WHERE product_id = 2;
	Process 2934: UPDATE inventory SET reserved = reserved + 1 WHERE product_id = 1;
```

The error has SQLSTATE **`40P01`** (`deadlock_detected`). PostgreSQL broke the cycle by aborting A's transaction. B's `UPDATE` then got its lock and completed.

### 13.2 Detection: `deadlock_timeout`

Searching the lock graph for cycles is expensive, and almost all lock waits end on their own within milliseconds. So PostgreSQL does not check on every wait. A waiting backend sleeps for `deadlock_timeout` (default **1 s**); only if it is *still* waiting then does it search for a cycle. If it finds one that includes itself, it aborts its own transaction. Each wait gets this check only once. So the victim is whichever waiter's check first sees the complete cycle. If B closes the cycle within 1 s of A starting to wait, A's check finds it and A is aborted (the run above). If B closes it later, A's check has already come up empty, and B's own check, 1 s after B started waiting, aborts B. Either way, every deadlock costs at least `deadlock_timeout` of waiting, holding locks that block other sessions in the meantime.

Raising `deadlock_timeout` makes detection cheaper but deadlocks slower to resolve; lowering it below a few hundred milliseconds wastes CPU on checks that find nothing. 1 s is a fine default. The same setting decides when `log_lock_waits` logs a wait.

The `deadlocks` column of `pg_stat_database` counts deadlocks since the last statistics reset. It should be (close to) zero; module 17 monitors it.

### 13.3 Prevention: a consistent lock order

Deadlocks require a cycle, and a cycle requires two transactions that take locks in **different orders**. If every transaction that locks several rows of a table locks them in the same global order (for example, ascending primary key), a cycle is impossible: whoever holds the lowest contested row proceeds, and everyone else waits behind them in line.

That is exactly what Bazaar's `place_order()` does (section 16): whatever order the shopper put the products in the cart, it locks the inventory rows in `product_id` order before touching them. The acceptance test of this module's milestone proves it with pgbench: 400 checkouts with the products listed in random orders, zero deadlocks. A naive version that reserves line by line in cart order produced **71 deadlocks out of 400 checkouts** in the same test (see the [project milestone](project.md)).

Practical rules:

- **Sort before locking.** If a transaction must lock several rows, lock them up front, in key order: `SELECT ... WHERE id = ANY ($1) ORDER BY id FOR UPDATE`. Section 16 shows that the `ORDER BY` really determines the locking order.
- **A multi-row `UPDATE` does not guarantee an order.** `UPDATE inventory SET ... WHERE product_id IN (1, 2)` locks rows in whatever order the plan visits them, which can differ between two statements (different plans, different physical row positions). Lock first with an ordered `SELECT ... FOR UPDATE`, then update.
- **Order across tables too.** If some transactions lock `orders` then `inventory`, none should lock `inventory` then `orders`.
- **Keep transactions short.** A shorter lock-holding window means fewer overlaps.
- **Deadlocks can involve more than row locks.** Two transactions that each hold `ROW EXCLUSIVE` on a table and then try `LOCK TABLE ... IN SHARE MODE` deadlock on table locks. Lock escalation in your own code ("read with `FOR SHARE`, then decide to update") is a classic: two sessions holding `FOR SHARE` on the same row both try to upgrade to an update lock, each waits for the other's share lock, and one of them gets `40P01`.
- **Retry on `40P01`.** Even with perfect ordering in your own code, deadlocks can come from places you do not control (foreign key checks, triggers, other applications). A deadlock rolls back the victim's whole transaction, so the same retry loop as for `40001` applies (section 15.5).

---

## 14. Advisory locks

Sometimes you need a lock on something that is not a row or a table: "the nightly payout run", "seller 42's catalog import", "refreshing the sales materialized view". **Advisory locks** are locks on application-defined numbers. PostgreSQL does not attach any meaning to them; it only guarantees the usual lock semantics (one exclusive holder, waiters queue, deadlock detection, cleanup on disconnect). They are "advisory" because nothing forces code to take them: they work only if every participant agrees to.

### 14.1 The API

The key is either one `bigint` or two `int`s. A common trick is to hash a name with `hashtext()` (which returns an `int`):

| Function | Behaviour |
|---|---|
| `pg_advisory_lock(key)` | Waits until it gets the lock. Held until `pg_advisory_unlock(key)` or the end of the **session**. |
| `pg_try_advisory_lock(key)` | Returns `true` if it got the lock, `false` immediately if not. Never waits. |
| `pg_advisory_unlock(key)`, `pg_advisory_unlock_all()` | Releases session-level locks. |
| `pg_advisory_xact_lock(key)`, `pg_try_advisory_xact_lock(key)` | Same, but released automatically at the end of the **transaction**. There is no unlock function. |
| `..._shared` variants | Shared mode: many holders at once, conflicting only with exclusive holders. |

A singleton job, with two sessions trying to run the same refresh:

| Step | Session A | Session B |
|---|---|---|
| 1 | `SELECT pg_try_advisory_lock(hashtext('refresh_seller_sales_daily')) AS got_it;` → `t` | |
| 2 | | the same → `f`: someone else is running it, skip |
| 3 | `BEGIN;` ... `ROLLBACK;` | |
| 4 | | the same → `f` (still held: session-level locks ignore transaction boundaries) |
| 5 | `SELECT pg_advisory_unlock(hashtext('refresh_seller_sales_daily')) AS released;` → `t` | |
| 6 | | the same `pg_try_advisory_lock` → `t` |

In `pg_locks`, an advisory lock appears with `locktype = 'advisory'`. The 64-bit key is split into `classid` (high 32 bits) and `objid` (low 32 bits), and `objsubid` is 1 for the single-`bigint` form and 2 for the two-`int` form:

```text
 pid  | locktype |  classid   |   objid    | objsubid |     mode      | granted
------+----------+------------+------------+----------+---------------+---------
 2990 | advisory | 4294967295 | 3359909605 |        1 | ExclusiveLock | t
(1 row)
```

(`hashtext('refresh_seller_sales_daily')` is −935057691; as a `bigint` its high 32 bits are all ones.)

Transaction-level locks are the safer default, because they cannot leak:

| Step | Session A | Session B |
|---|---|---|
| 1 | `BEGIN;` | |
| 2 | `SELECT pg_advisory_xact_lock(10, 15);` | |
| 3 | | `SELECT pg_advisory_xact_lock(10, 15);` → **(waits)** |
| 4 | `COMMIT;` | → returns; B's own lock is released as soon as its autocommit statement ends |

### 14.2 Use cases and pitfalls

Good uses:

- **Singleton cron jobs.** Several application servers run the same scheduler; each tries `pg_try_advisory_lock(hashtext('job:payouts'))`, and only the winner runs the job. Exercise 11 builds this for the sales materialized view.
- **Serializing work per entity** without a dedicated row to lock: `pg_advisory_xact_lock(hashtext('seller-import'), seller_id)` makes two catalog imports for the same seller run one after the other, while imports for different sellers run in parallel. The two-key form gives you a namespace for each kind of lock.
- **Migrations.** Some migration tools take an advisory lock so that two deploys never run migrations at the same time.

Pitfalls:

- **Session locks and connection pools do not mix.** A session-level advisory lock belongs to the *server connection*. With PgBouncer in transaction mode (module 17), your next transaction may run on a different server connection, and your lock stays behind on the old one, held for some other client. Use the `_xact_` variants there, or session locks only on a dedicated connection.
- **Session locks are re-entrant and counted.** Locking the same key twice needs two unlocks. Unlocking a key you do not hold returns `false` with a warning (`you don't own a lock of type ExclusiveLock`).
- **Hash collisions.** `hashtext()` produces only 2^32 values; two different names can collide and then block each other for no reason. Use the two-key form with a fixed namespace number, or derive keys from real ids.
- **Never in a `WHERE` clause with `LIMIT`.** `SELECT ... WHERE pg_try_advisory_lock(id) LIMIT 1` may evaluate the function on rows the `LIMIT` later discards, locking them and never releasing them. Use `SKIP LOCKED` for work queues instead (section 17).
- Advisory locks live in the shared lock table, whose size is bounded (`max_locks_per_transaction` × connections). Do not take millions of them.

---

## 15. Concurrency patterns

With the mechanics in place, here are the patterns you will use again and again. All of them answer one question: *how do I make a read-then-write correct when other sessions do the same thing at the same time?*

### 15.1 The atomic conditional update

If the decision can be expressed as a condition on the row being changed, put it into the `UPDATE` itself:

```sql
UPDATE inventory
SET reserved = reserved + 3
WHERE product_id = 15
  AND quantity - reserved >= 3          -- the guard
RETURNING quantity, reserved;
```

The application then checks the row count: `UPDATE 1` means "reserved", `UPDATE 0` means "not enough stock" (or "no such product"). Section 6.3 showed why this is safe under `READ COMMITTED`: the guard is re-evaluated on the newest committed version while the row is locked. There is no window between check and write, no explicit lock, and only one round trip. It is the right tool whenever it fits:

- `UPDATE products SET status = 'archived' WHERE id = $1 AND status = 'active'` (a state transition that happens once);
- `UPDATE orders SET status = 'paid', paid_at = now() WHERE id = $1 AND status = 'pending'`;
- `UPDATE promotions SET ... WHERE id = $1 AND upper_inf(active_during)`.

It fits less well when the decision needs data from several rows, or logic too complex for a `WHERE` clause. Then you lock.

### 15.2 Pessimistic locking: lock, then decide

"Pessimistic" because it assumes a conflict is likely and prevents it up front: read the rows with `FOR UPDATE` (or `FOR NO KEY UPDATE`), make your decision in SQL or application code, write, commit. Concurrent transactions wanting the same rows wait at their `SELECT ... FOR UPDATE`, and then read fresh data:

```sql
BEGIN;
SELECT quantity, reserved
FROM inventory
WHERE product_id IN (1, 24)
ORDER BY product_id                      -- consistent order: no deadlocks
FOR NO KEY UPDATE;
-- ... application checks stock, prices, limits ...
UPDATE inventory SET reserved = reserved + 2 WHERE product_id = 1;
UPDATE inventory SET reserved = reserved + 1 WHERE product_id = 24;
COMMIT;
```

`FOR NO KEY UPDATE` is enough when you are going to update non-key columns, and unlike `FOR UPDATE` it does not block foreign key checks (section 10.2). Keep the window between the lock and the commit short: no HTTP calls to a payment provider while holding a row lock on a best-selling product.

### 15.3 Optimistic locking: detect, then retry

"Optimistic" because it assumes conflicts are rare and only detects them: read without locking, let the user think for as long as they like, and when writing, check that the row has not changed since it was read. If it has, report a conflict ("someone else edited this product; reload?") or retry.

The usual implementation is a **version column** that every update increments, and a guard on it. Bazaar's `products` table has no version column (exercise 5 adds one), but its `updated_at` column, which module 09's trigger sets on every update, can serve as a version token. Two sellers' staff edit the price of product 40 (*Chef's Knife 20cm*) in an admin UI, each in their own session. Each reads the row, remembering `updated_at` in a psql variable with `\gset`:

| Step | Session A (editor 1) | Session B (editor 2) |
|---|---|---|
| 1 | `SELECT price, updated_at FROM products WHERE id = 40 \gset seen_` → 89.00, 2023-04-15 12:00:00+00 | |
| 2 | | the same → 89.00, 2023-04-15 12:00:00+00 |
| 3 | `UPDATE products SET price = 95.00 WHERE id = 40 AND updated_at = :'seen_updated_at';` → `UPDATE 1` | |
| 4 | | `UPDATE products SET price = 85.00 WHERE id = 40 AND updated_at = :'seen_updated_at';` → **`UPDATE 0`**: conflict |
| 5 | | `SELECT price, updated_at FROM products WHERE id = 40;` → 95.00, 2026-10-14 08:31:26.258089+00 |

Editor 2's write was refused instead of silently overwriting editor 1's price. The application shows editor 2 the new price and asks again. Note that no transaction was open between reading and writing; the "transaction" spans minutes of human thinking, which no database lock should ever do.

> **Pitfall:** a timestamp is a weaker version token than a counter. `now()` is the *transaction* start time (section 2.5), so two updates in the same transaction write the same value, and two transactions starting in the same microsecond could too. A dedicated `version int` column incremented by a trigger (or by every `UPDATE`) is unambiguous. Some ORMs use the hidden `xmin` column as a free version token; it works because every update creates a new version with a new `xmin`, but it is a 32-bit internal value and changes on updates you did not expect (for example a no-op `UPDATE` by a trigger). Module 25 shows SQLAlchemy's `version_id_col`.

### 15.4 Choosing

| Situation | Pattern |
|---|---|
| The decision is a condition on the row you change | Atomic conditional `UPDATE` (15.1) |
| Several rows, complex logic, short transaction, high contention | Pessimistic `SELECT ... FOR UPDATE`, in a consistent order (15.2) |
| A human or a slow process sits between read and write; conflicts are rare | Optimistic version check (15.3) |
| A rule over a *set* of rows (counts, sums, "at least one") | `SERIALIZABLE` with retries (8), or lock a parent row to materialize the conflict (7.3) |
| A job or task that must run only once at a time | Advisory lock (14) |
| Workers competing for items in a queue | `FOR UPDATE SKIP LOCKED` (17) |

### 15.5 Retry loops

`40001` (`serialization_failure`) and `40P01` (`deadlock_detected`) both mean "your transaction was rolled back because of a concurrent one; it will probably succeed if you run it again". Applications that use `REPEATABLE READ` or `SERIALIZABLE`, or that can deadlock, need a retry loop around the **whole transaction**. In pseudo-code (module 27 writes it in Python with psycopg):

```text
RETRYABLE = {"40001", "40P01"}

function run_in_transaction(work, max_attempts = 5):
    for attempt in 1 .. max_attempts:
        conn.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
        try:
            result = work(conn)            # all reads AND writes happen in here
            conn.execute("COMMIT")         # COMMIT itself can raise 40001
            return result
        except DatabaseError as e:
            conn.execute("ROLLBACK")
            if e.sqlstate not in RETRYABLE or attempt == max_attempts:
                raise
            sleep(random(0, 2 ** attempt * 10 ms))   # jittered exponential backoff
```

The rules behind it:

- **Retry everything from `BEGIN`.** The work function must redo its reads, because they are the reason the transaction failed.
- **Retry only retryable errors.** A unique violation (`23505`), a check violation (`23514`), `BZ001` "insufficient stock": retrying gives the same answer, so report them. `55P03` (`lock_not_available`, from `NOWAIT` or `lock_timeout`) can be retried, but usually after a longer pause.
- **Bound the attempts and back off with jitter.** Under heavy contention, immediate retries collide again; random delays spread them out.
- **No side effects inside the work function.** The database rolls back a failed attempt, but it cannot un-send an email or un-charge a card. Do external actions after `COMMIT`, or record them in the same transaction as a row that a separate worker processes afterwards (the *transactional outbox*, module 27, built on the job queue of section 17).
- **The "unknown outcome" case.** If the connection breaks *during* `COMMIT`, you cannot know whether the transaction committed. Blindly retrying a checkout could then create a second order. Idempotency keys (section 16.4) solve that.

---

## 16. Worked example: `place_order()`

Everything so far comes together in Bazaar's checkout function. It is this module's milestone ([project.md](project.md)), and the reference implementation is [`project/db/1001_schema_concurrency.sql`](../../project/db/1001_schema_concurrency.sql). Load it now to follow along:

```bash
./project/bin/reset.sh 11      # state 10 + this module's reference file (still the small dataset)
```

(Before you build your own version in the milestone, go back to `./project/bin/reset.sh 10`.)

### 16.1 The contract

```sql
place_order(p_user_id bigint, p_items jsonb, p_shipping_address_id bigint DEFAULT NULL)
RETURNS SETOF bigint      -- one new order id per seller, ascending
```

`p_items` is the cart, for example `[{"product_id": 1, "quantity": 2}, {"product_id": 24, "quantity": 1}]`. The function either creates one order per seller with its items and reserves the stock, or raises an error and changes nothing. The errors use **custom SQLSTATEs** so that the application can branch on them (never on message text, which can change):

| SQLSTATE | Meaning |
|---|---|
| `BZ001` | insufficient stock (the `DETAIL` gives requested and available) |
| `BZ002` | product missing or not active |
| `BZ003` | user missing or inactive |
| `BZ004` | shipping address does not belong to the user |
| `22023` | `invalid_parameter_value`: malformed `p_items` |

A SQLSTATE is five characters; the first two are the class. PostgreSQL's own classes use digits and uppercase letters, but none starts with `BZ`, so a class of our own cannot collide with a built-in code. A successful call:

```sql
SELECT * FROM place_order(8, '[{"product_id": 24, "quantity": 1},
                              {"product_id": 1, "quantity": 2},
                              {"product_id": 47, "quantity": 3}]', 9);
```

```text
 place_order
-------------
         121
         122
         123
(3 rows)
```

Three products from three sellers (1, 2 and 4) gave three orders:

```text
 id  | seller_id | status  |  total
-----+-----------+---------+---------
 121 |         1 | pending | 1998.00
 122 |         2 | pending |   11.99
 123 |         4 | pending |   57.00
(3 rows)
```

and stock was reserved (product 47 already had one unit reserved by an older pending order):

```text
 product_id | quantity | reserved
------------+----------+----------
          1 |       25 |        2
         24 |       25 |        1
         47 |       80 |        4
(3 rows)
```

The failure cases, with `\set VERBOSITY verbose` so that psql shows the SQLSTATE:

```text
SELECT * FROM place_order(8, '[{"product_id": 13, "quantity": 1}]');
ERROR:  BZ001: insufficient stock for product 13
DETAIL:  requested 1, available 0

SELECT * FROM place_order(8, '[{"product_id": 14, "quantity": 1}]');
ERROR:  BZ002: product 14 is not available for sale

SELECT * FROM place_order(9, '[{"product_id": 1, "quantity": 1}]');
ERROR:  BZ003: user 9 does not exist or is inactive

SELECT * FROM place_order(8, '[{"product_id": 1, "quantity": 1}]', 13);
ERROR:  BZ004: address 13 does not belong to user 8

SELECT * FROM place_order(8, '[{"product_id": 999, "quantity": 1}]');
ERROR:  BZ002: product(s) {999} do not exist

SELECT * FROM place_order(8, '[{"product_id": 1, "quantity": 0}]');
ERROR:  22023: each item needs a numeric product_id and a positive integer quantity
```

(Product 14 is a draft, user 9 is inactive, and address 13 belongs to user 11; see `project/db/NOTES.md`.)

### 16.2 Walking through the code

**Validation first, without locks.** The function checks the JSON shape, the user and the address before touching inventory. These checks read rows that rarely change, take no locks, and fail fast. Locks are taken as late as possible and held as briefly as possible.

**Normalising the cart.** The items are aggregated into two parallel arrays, sorted by product id, with duplicate lines summed:

```sql
SELECT array_agg(product_id ORDER BY product_id),
       array_agg(quantity   ORDER BY product_id)
INTO v_pids, v_qtys
FROM (
    SELECT (e ->> 'product_id')::bigint AS product_id,
           sum((e ->> 'quantity')::int)  AS quantity
    FROM jsonb_array_elements(p_items) AS e
    GROUP BY 1
) s;
```

Summing duplicates matters for correctness: two lines of "1 × product 15" must be checked as "2 available?", not twice as "1 available?".

**Locking in a global order.** This is the heart of the function:

```sql
FOR r IN
    SELECT i.product_id, i.quantity, i.reserved, q.quantity AS wanted, p.status
    FROM inventory i
    JOIN unnest(v_pids, v_qtys) AS q (product_id, quantity) ON q.product_id = i.product_id
    JOIN products p ON p.id = i.product_id
    ORDER BY i.product_id
    FOR UPDATE OF i
LOOP
    -- BZ002 if r.status <> 'active'
    -- BZ001 if r.quantity - r.reserved < r.wanted
END LOOP;
```

Three details make it correct:

1. **`ORDER BY i.product_id` really is the locking order.** Does `FOR UPDATE` lock rows as they are scanned, or after sorting? `EXPLAIN` answers it:

   ```text
                               QUERY PLAN
   ------------------------------------------------------------------
    LockRows
      ->  Sort
            Sort Key: i.product_id
            ->  Nested Loop
                  Join Filter: (i.product_id = q.product_id)
                  ->  Hash Join
                        Hash Cond: (p.id = q.product_id)
                        ->  Seq Scan on products p
                        ->  Hash
                              ->  Function Scan on q
                  ->  Index Scan using inventory_pkey on inventory i
                        Index Cond: (product_id = p.id)
   (12 rows)
   ```

   The `LockRows` node sits **above** the `Sort`, so it receives rows in `product_id` order and locks them one by one in that order. Two checkouts for products {1, 24} always both try product 1 first; the loser waits there, holding nothing the winner needs. No cycle, no deadlock, regardless of the order of the shopper's cart.

2. **`FOR UPDATE OF i`** locks only the inventory rows. Without `OF i`, the products rows would be locked too, blocking sellers' price edits for the duration of every checkout.

3. **The stock check reads the locked, fresh row.** Under `READ COMMITTED`, if another checkout holds one of the rows, the loop waits, then re-reads the newest committed version (section 10.1). So `r.quantity - r.reserved` always includes reservations committed a microsecond ago. The check and the reservation that follows are protected by the same lock, so nobody can sneak in between.

**Reserving.** With all rows locked, one `UPDATE` reserves everything:

```sql
UPDATE inventory i
SET reserved = i.reserved + q.quantity
FROM unnest(v_pids, v_qtys) AS q (product_id, quantity)
WHERE i.product_id = q.product_id;
```

This multi-row `UPDATE` would be a deadlock risk on its own (13.3), but here every row is already locked by this transaction, so it never waits.

**One order per seller.** For each distinct seller (ascending), insert an `orders` row and its `order_items` in one multi-row `INSERT`. The `order_items` foreign keys take `KEY SHARE` locks on the referenced products and orders rows (10.2), which do not conflict with sellers editing their products. `orders.total` is maintained by module 09's statement-level trigger, so the function does not compute it.

**Atomicity for free.** The function does not `COMMIT` or catch anything. It runs inside the caller's transaction: if any `RAISE` fires, everything it did so far (including reservations and orders for other sellers) is rolled back with the transaction. A caller that wants to try an alternative cart in the same transaction can wrap the call in a savepoint.

### 16.3 Two checkouts for the last units

Product 15 has 4 units, product 60 (*Cedar Raised Garden Bed*) has 3. Two shoppers order both, listing them in opposite orders:

| Step | Session A (user 8) | Session B (user 13) |
|---|---|---|
| 1 | `BEGIN;` | |
| 2 | `SELECT * FROM place_order(8, '[{"product_id": 60, "quantity": 1}, {"product_id": 15, "quantity": 3}]');` → orders 121, 122 | |
| 3 | | `BEGIN;` |
| 4 | | `SELECT * FROM place_order(13, '[{"product_id": 15, "quantity": 3}, {"product_id": 60, "quantity": 1}]');` → **(waits)** |
| 5 | `COMMIT;` | → `ERROR:  insufficient stock for product 15` / `DETAIL:  requested 3, available 1` |
| 6 | | `ROLLBACK;` |

While B waited (step 4), it was blocked by A on a transaction id:

```text
 pid  | blocked_by | wait_event_type |  wait_event
------+------------+-----------------+---------------
 3464 | {3447}     | Lock            | transactionid
(1 row)
```

B was waiting at product 15, the first row in its sorted lock order, holding no lock on product 60. When A committed, B re-read product 15 (4 units, 3 reserved), found only 1 available and raised `BZ001`. Final stock: product 15 has `reserved = 3`, product 60 has `reserved = 1`. Nothing was oversold and nothing deadlocked. The milestone's pgbench test repeats this with eight clients and hundreds of checkouts.

> **In production:** `place_order()` is designed for `READ COMMITTED`. Under `REPEATABLE READ` it would still never oversell, but every checkout that had to wait for another one on the same product would fail with `40001` instead of re-reading, and the application would need to retry it. Under contention on hot products, that is a lot of retries.

### 16.4 Idempotency keys

A mobile app sends the checkout request; the network drops the response; the app retries. Did the first request create an order? Without help, the server cannot tell, and the shopper may be charged twice. The standard fix is an **idempotency key**: the client generates a unique id per *logical* request (a UUID), sends it with every attempt, and the server stores the key with the result. A second request with the same key gets the stored result instead of a new order.

The key table's primary key does the concurrency work. An `INSERT` of a key that another *uncommitted* transaction has just inserted **waits** for that transaction (the unique index must know whether the other row will commit), then either conflicts or proceeds. In a scratch table:

```sql
CREATE TABLE lab.checkout_requests (
    idempotency_key text        PRIMARY KEY,
    user_id         bigint      NOT NULL,
    order_ids       bigint[],
    created_at      timestamptz NOT NULL DEFAULT now()
);
```

The app retries while the first attempt is still running:

| Step | Session A (first attempt) | Session B (retry, same key) |
|---|---|---|
| 1 | `BEGIN;` | |
| 2 | `INSERT INTO lab.checkout_requests (idempotency_key, user_id) VALUES ('7f3c9a1e-checkout', 8) ON CONFLICT DO NOTHING RETURNING idempotency_key;` → 1 row: we own this request | |
| 3 | | `BEGIN;` |
| 4 | | the same `INSERT ... ON CONFLICT DO NOTHING` → **(waits)** |
| 5 | `UPDATE lab.checkout_requests SET order_ids = ARRAY(SELECT place_order(8, '[{"product_id": 12, "quantity": 1}]')) WHERE idempotency_key = '7f3c9a1e-checkout' RETURNING order_ids;` → `{121}` | |
| 6 | `COMMIT;` | → `(0 rows)`, `INSERT 0 0`: someone else owns it |
| 7 | | `SELECT order_ids FROM lab.checkout_requests WHERE idempotency_key = '7f3c9a1e-checkout';` → `{121}` |
| 8 | | `COMMIT;` |

B returned the first attempt's result, order 121, and no second order was created. Because the key row and the orders are written in the **same transaction**, they commit or roll back together: if A had failed, its key row would have vanished with it, and B's insert would have succeeded and placed the order itself. Exercise 13 turns this into a function.

---

## 17. A job queue with `SKIP LOCKED`

Bazaar has background work: confirmation emails after checkout, refreshing the sales materialized view, sending webhooks to sellers. A **job queue** stores the work as rows, and worker processes take jobs out. You could use a dedicated broker (RabbitMQ, Redis, SQS), but a queue in PostgreSQL has a killer feature: enqueuing a job can be part of the same transaction as the business change. The order and its "send confirmation" job commit together or not at all.

The hard part is dequeuing: N workers must each get a *different* job, without waiting for each other, and a job must never be processed twice or lost when a worker crashes. Before `SKIP LOCKED` (added in PostgreSQL 9.5), queues in PostgreSQL were notoriously hard. Now they take a few lines.

### 17.1 The table

From the reference file:

```sql
CREATE TABLE jobs (
    id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    queue         text        NOT NULL DEFAULT 'default',
    kind          text        NOT NULL,                    -- e.g. 'send_email'
    payload       jsonb       NOT NULL DEFAULT '{}',
    run_at        timestamptz NOT NULL DEFAULT now(),      -- not before this time
    attempts      int         NOT NULL DEFAULT 0,
    max_attempts  int         NOT NULL DEFAULT 5,
    locked_at     timestamptz,                             -- lease start
    locked_by     text,                                    -- worker id
    last_error    text,
    done_at       timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    ...
);

CREATE INDEX jobs_ready_idx ON jobs (queue, run_at) WHERE done_at IS NULL;
```

A job is **ready** when it is not done, its `run_at` has come, it has attempts left, and nobody holds a valid **lease** on it. The partial index covers only unfinished jobs, so it stays small however many finished jobs accumulate (module 11 explains partial indexes).

### 17.2 `dequeue_jobs()`

```sql
CREATE FUNCTION dequeue_jobs(
    p_queue  text,
    p_worker text,
    p_limit  int      DEFAULT 1,
    p_lease  interval DEFAULT interval '5 minutes'
)
RETURNS SETOF jobs
LANGUAGE sql
AS $$
    UPDATE jobs j
    SET locked_at = now(),
        locked_by = p_worker,
        attempts  = j.attempts + 1
    WHERE j.id IN (
        SELECT c.id
        FROM jobs c
        WHERE c.queue = p_queue
          AND c.done_at IS NULL
          AND c.run_at <= now()
          AND c.attempts < c.max_attempts
          AND (c.locked_at IS NULL OR c.locked_at < now() - p_lease)  -- free or lease expired
        ORDER BY c.run_at, c.id
        LIMIT p_limit
        FOR UPDATE SKIP LOCKED
    )
    RETURNING j.*;
$$;
```

Reading it from the inside out:

1. The subquery finds the oldest ready jobs and locks them with **`FOR UPDATE SKIP LOCKED`**. If another worker is in the middle of claiming a job (its row is locked), this worker skips it and takes the next one instead of queueing behind the lock. That is what lets N workers claim N different jobs at the same time without waiting.
2. `LIMIT` applies after skipping, so a worker gets up to `p_limit` jobs that it could actually lock.
3. The outer `UPDATE` marks the claimed jobs with a **lease** (`locked_at`, `locked_by`) and counts the attempt. `RETURNING j.*` hands the jobs to the worker.
4. The worker's dequeue transaction then **commits right away**. The row lock disappears, but the lease remains in the data: the `locked_at IS NULL OR locked_at < now() - p_lease` condition keeps other workers away from a leased job for the lease duration.

Why a lease instead of simply keeping the transaction (and the row lock) open while the job runs? Keeping it open would need no lease at all: a crashed worker's connection closes, its transaction rolls back, and the lock disappears. But it holds a connection and an open transaction for the entire job, which might take minutes. That causes the problems of section 18 and caps the number of concurrent jobs at the number of connections. With a lease, a crashed worker's job simply becomes ready again when the lease expires. The price: a job may run twice if a worker is slower than its lease, so job handlers should be idempotent anyway.

After the work, the worker reports back with `complete_job(id)` (sets `done_at`) or `fail_job(id, error)`, which clears the lease and schedules a retry with exponential backoff: 10 s, 20 s, 40 s, ... capped at one hour. A job whose `attempts` reach `max_attempts` without `done_at` is **dead**: no worker will pick it up again, and a human (or an alert) must look at it.

### 17.3 Two workers

Five confirmation-email jobs for the pending orders 115 to 119:

```sql
INSERT INTO jobs (kind, payload)
SELECT 'send_order_email', jsonb_build_object('order_id', id)
FROM orders
WHERE status = 'pending'
ORDER BY id;
```

Two workers dequeue two jobs each; to make the overlap visible, they dequeue inside explicit transactions:

| Step | Worker A | Worker B |
|---|---|---|
| 1 | `BEGIN;` | |
| 2 | `SELECT id, payload, attempts, locked_by FROM dequeue_jobs('default', 'worker-a', 2);` → jobs 1, 2 | |
| 3 | | `BEGIN;` |
| 4 | | `SELECT id, payload, attempts, locked_by FROM dequeue_jobs('default', 'worker-b', 2);` → jobs 4, 3, **no wait** |
| 5 | `COMMIT;` | `COMMIT;` |
| 6 | `SELECT complete_job(1), complete_job(2);` → `t`, `t` | |
| 7 | | `SELECT fail_job(3, 'SMTP timeout');` → `t` |

B did not wait for A: it skipped jobs 1 and 2, which A had locked, and took the next two. (They came back as 4, 3: `RETURNING` does not promise any order.) The queue afterwards:

```sql
SELECT id, attempts, locked_by, done_at IS NOT NULL AS done, run_at - now() AS retry_in, last_error
FROM jobs ORDER BY id;
```

```text
 id | attempts | locked_by | done |     retry_in     |  last_error
----+----------+-----------+------+------------------+--------------
  1 |        1 | worker-a  | t    | -00:00:05.959467 |
  2 |        1 | worker-a  | t    | -00:00:05.959467 |
  3 |        1 |           | f    | 00:00:09.300786  | SMTP timeout
  4 |        1 | worker-b  | f    | -00:00:05.959467 |
  5 |        0 |           | f    | -00:00:05.959467 |
(5 rows)
```

Jobs 1 and 2 are done. Job 3 failed and will be ready again in about 10 seconds. Job 4 is leased to worker B, which is still "working" on it. Job 5 is ready. The milestone's acceptance test runs 2,000 jobs through 8 concurrent pgbench workers and checks that each job is processed exactly once.

> **Pitfall:** without `SKIP LOCKED`, the same subquery with plain `FOR UPDATE` makes every worker queue behind the first one on the same oldest job, so the workers run one at a time. Worse, under `READ COMMITTED` a waiting worker re-checks the row after the first worker commits (section 6.3). If the condition only said `done_at IS NULL`, the job would still match, and the second worker would claim the same job again. The lease condition in the `WHERE` clause is what makes the re-check reject it.

> **In production:** a queue table is a churn machine: every job is inserted, updated two or three times, and eventually deleted. That creates dead row versions quickly, so queue tables need aggressive autovacuum settings (module 17) and a periodic `DELETE` of old finished jobs. Long transactions anywhere in the database stop the cleanup (next section), and a bloated queue table makes every dequeue slower. Workers can avoid polling by listening for a `NOTIFY` sent when a job is enqueued (module 23).

---

## 18. Long transactions and timeouts

### 18.1 Why long transactions hurt

A transaction that stays open for a long time causes damage far beyond its own work:

1. **It holds its locks until the end.** Every row it updated and every table it touched stays locked. As section 11.4 showed, even a harmless `ACCESS SHARE` lock can take down a site when a migration queues behind it.
2. **It holds back cleanup.** Its snapshot may still need old row versions, so `VACUUM` may not remove any version that became dead after the snapshot was taken, *in any table of the database*.
3. **Which causes bloat.** Dead versions pile up, tables and indexes grow, queries read more pages, and the space is not reclaimed even after the transaction ends (module 17).
4. **It ties up a connection**, which is a scarce resource (module 17's PgBouncer section).

Here is point 2 in action. An analyst's `REPEATABLE READ` transaction is open while the checkout traffic (simulated by two no-op updates of every inventory row) runs:

| Step | Session A (analyst) | Session B (app and DBA) |
|---|---|---|
| 1 | `BEGIN ISOLATION LEVEL REPEATABLE READ;` | |
| 2 | `SELECT count(*) FROM orders;` → 120 | |
| 3 | | `UPDATE inventory SET reserved = reserved WHERE product_id <= 62;` → `UPDATE 62`, twice |
| 4 | | `VACUUM (VERBOSE) inventory;` |
| 5 | `COMMIT;` | |
| 6 | | `VACUUM (VERBOSE) inventory;` |

The two `VACUUM` reports (shortened):

```text
-- step 4, analyst's transaction still open
tuples: 0 removed, 186 remain, 124 are dead but not yet removable

-- step 6, after the analyst committed
tuples: 124 removed, 62 remain, 0 are dead but not yet removable
```

The analyst never even read `inventory`; their snapshot alone kept 124 dead row versions alive. Find the culprits with `pg_stat_activity`. `backend_xmin` is the oldest transaction id a session's snapshot still needs, and `age()` says how many transactions ago that was:

```sql
SELECT pid, state, now() - xact_start AS xact_age, backend_xid, backend_xmin, age(backend_xmin) AS xmin_age
FROM pg_stat_activity
WHERE backend_xmin IS NOT NULL AND pid <> pg_backend_pid()
ORDER BY xact_start;
```

```text
 pid  |        state        |    xact_age     | backend_xid | backend_xmin | xmin_age
------+---------------------+-----------------+-------------+--------------+----------
 1253 | idle in transaction | 00:00:03.617147 |             |         7175 |        2
(1 row)
```

On a busy production system, the alarming version of this row has an `xact_age` of hours and an `xmin_age` in the millions. Module 16 explains the visibility rules behind this precisely, and module 17 shows how to monitor it. Replication slots and prepared transactions (`PREPARE TRANSACTION`, two-phase commit) can hold back cleanup in the same way; module 19 covers slots.

> **Under the hood:** under `READ COMMITTED`, an *idle* transaction holds no snapshot (each statement's snapshot is released when the statement ends), so its `backend_xmin` is empty and it does not block cleanup, unless it has written something and therefore has a transaction id (`backend_xid`). It still holds all its locks, though. A `REPEATABLE READ` or `SERIALIZABLE` transaction keeps its snapshot until it ends.

### 18.2 Timeouts as safety nets

PostgreSQL has several timeouts. Each can be set per session (`SET`), per role or database (`ALTER ROLE ... SET`, `ALTER DATABASE ... SET`), or server-wide:

| Setting | Limits | When it fires |
|---|---|---|
| `statement_timeout` | the run time of one statement | `ERROR:  canceling statement due to statement timeout`; the transaction is aborted |
| `lock_timeout` | each single wait for a lock | `ERROR:  canceling statement due to lock timeout` (`55P03`) |
| `idle_in_transaction_session_timeout` | time spent idle *inside* a transaction | `FATAL`: the connection is closed, the transaction rolled back |
| `transaction_timeout` (new in PostgreSQL 17) | the total duration of a transaction, idle or not | `FATAL`: the connection is closed, the transaction rolled back |
| `idle_session_timeout` | time idle *outside* a transaction | `FATAL`: the connection is closed (rarely useful with pools) |

`idle_in_transaction_session_timeout` is the remedy for forgotten transactions. A session locks a row and then wanders off:

```sql
SET idle_in_transaction_session_timeout = '2s';
BEGIN;
SELECT quantity FROM inventory WHERE product_id = 1 FOR UPDATE;
-- ... 2.5 seconds of nothing ...
SELECT 1;
```

```text
FATAL:  terminating connection due to idle-in-transaction timeout
server closed the connection unexpectedly
	This probably means the server terminated abnormally
	before or while processing the request.
```

The server closed the connection after 2 seconds of idleness, rolled back the transaction and released the row lock. (Interactive psql then reconnects automatically: `The connection to the server was lost. Attempting reset: Succeeded.`) Only the idle time counts: a transaction that keeps sending statements is never affected. That gap is what PostgreSQL 17's `transaction_timeout` closes. With `SET transaction_timeout = '2s'`, a transaction that ran `SELECT pg_sleep(1)` and then `SELECT pg_sleep(1.5)` was terminated in the middle of the second statement:

```text
FATAL:  terminating connection due to transaction timeout
```

Reasonable starting points for an OLTP application role such as Bazaar's (module 15 creates it):

```sql
ALTER ROLE bazaar_app SET statement_timeout = '5s';
ALTER ROLE bazaar_app SET lock_timeout = '2s';
ALTER ROLE bazaar_app SET idle_in_transaction_session_timeout = '30s';
```

Long-running reports and migrations use their own roles or override the settings for their session. The point is that no single forgotten `BEGIN` can hold locks and snapshots for hours.

> **Pitfall:** `statement_timeout` set server-wide also applies to `pg_dump`, `VACUUM`, and index builds started by DBAs, which is rarely what you want. Set timeouts on application roles, not globally.

---

## Summary

- A **transaction** groups statements into one unit that commits or rolls back as a whole. Every statement runs in one; autocommit just makes it one statement long. DDL is transactional in PostgreSQL; sequences are not.
- **ACID**: atomicity (all or nothing), consistency (declared rules always hold), isolation (concurrent transactions don't see each other's partial work), durability (committed means on disk, via the WAL).
- After an error, a transaction is **aborted** (`25P02`) until `ROLLBACK`. **Savepoints** undo part of a transaction; psql's `ON_ERROR_ROLLBACK` and PL/pgSQL exception blocks are built on them.
- **MVCC**: updates create new row versions; `xmin`/`xmax` and **snapshots** decide who sees which version. Readers never block writers, and writers never block readers.
- **`READ COMMITTED`** takes a snapshot per statement. It allows non-repeatable reads, phantoms and lost updates (read-modify-write in the application). `UPDATE`/`DELETE`/`FOR UPDATE` re-check the newest version of a row they had to wait for (**EvalPlanQual**), which makes atomic `SET x = x + n WHERE <guard>` safe.
- **`REPEATABLE READ`** uses one snapshot per transaction, prevents lost updates with **`40001`**, but allows **write skew**. **`SERIALIZABLE`** (SSI) tracks read/write dependencies with predicate locks and aborts transactions that could break serializability, including write skew and the read-only anomaly. Both require **retrying the whole transaction**.
- **Row locks**: `FOR UPDATE` > `FOR NO KEY UPDATE` > `FOR SHARE` > `FOR KEY SHARE`; foreign key checks take `KEY SHARE`, so non-key updates and FK inserts don't block each other. `NOWAIT` fails fast; `SKIP LOCKED` skips locked rows.
- **Table locks**: eight modes; reads conflict only with `ACCESS EXCLUSIVE`, which most `ALTER TABLE` forms take. Lock requests queue, so an `ALTER` waiting behind a long transaction blocks everyone behind it. Use **`lock_timeout`** for DDL and `CREATE INDEX CONCURRENTLY` for indexes.
- Diagnose blocking with **`pg_stat_activity`**, **`pg_locks`** and **`pg_blocking_pids()`**; the root is often an *idle in transaction* session.
- **Deadlocks** are detected after `deadlock_timeout` and abort one transaction with `40P01`. Prevent them by locking in a **consistent global order**, as `place_order()` does.
- **Advisory locks** lock application-defined keys; prefer transaction-level ones, especially behind poolers.
- Patterns: atomic conditional updates, pessimistic `FOR UPDATE`, optimistic version checks, idempotency keys, `SKIP LOCKED` queues with leases, retry loops for `40001`/`40P01`. Keep transactions short, and protect the database with `idle_in_transaction_session_timeout`, `transaction_timeout` and `statement_timeout`.

## What's next

Module 11 switches to the large dataset (half a million orders), where `place_order()`'s lookups, the job queue's dequeue query and every foreign key need the right **indexes**. You'll learn how B-trees work, when the planner uses them, and why `CREATE INDEX CONCURRENTLY`, which you met here as a lock-friendly command, is the only way to add them to a busy table.
