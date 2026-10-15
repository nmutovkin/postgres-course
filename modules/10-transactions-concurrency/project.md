# Module 10: Project milestone — a checkout that survives Black Friday

<!-- nav:start -->
> [Theory](README.md) · [Exercises](exercises.md) · **Project** · [Solutions](solutions/README.md)  
> [← 09 Views, Functions and Triggers](../09-views-functions-triggers/README.md) · [Course home](../../README.md) · [11 Indexes →](../11-indexes/README.md)
<!-- nav:end -->

Bazaar is about to run its first big sale. Until now an "order" was something we inserted by hand, one statement at a time, in a quiet database. From this milestone on, checkout is **one database call** that hundreds of shoppers make at the same time, and it must never oversell, never deadlock, and never leave half an order behind. Bazaar also needs a place to put background work (confirmation emails, seller webhooks, refreshing the sales report) that several worker processes can drain in parallel without doing any job twice.

You build:

1. **`place_order()`**: the checkout function.
2. **A job queue**: the `jobs` table and the `dequeue_jobs()`, `complete_job()` and `fail_job()` functions.
3. **Extension: `cancel_order()`**, which gives reserved stock back when an order is cancelled. Nothing in the reference implementation does that yet.

Then you prove that they work under concurrency with `pgbench`.

<!-- toc:start -->
**Contents**

- [Getting started](#getting-started)
- [Requirements](#requirements)
  - [1. `place_order(p_user_id bigint, p_items jsonb, p_shipping_address_id bigint DEFAULT NULL) RETURNS SETOF bigint`](#1-place_orderp_user_id-bigint-p_items-jsonb-p_shipping_address_id-bigint-default-null-returns-setof-bigint)
  - [2. The job queue](#2-the-job-queue)
  - [3. Extension: `cancel_order(p_order_id bigint) RETURNS boolean`](#3-extension-cancel_orderp_order_id-bigint-returns-boolean)
- [Acceptance checks](#acceptance-checks)
  - [A. The contract, in one session](#a-the-contract-in-one-session)
  - [B. Concurrency: no overselling, no deadlocks, no double processing](#b-concurrency-no-overselling-no-deadlocks-no-double-processing)
  - [C. Extension: cancellations under concurrency](#c-extension-cancellations-under-concurrency)
- [Reference solution](#reference-solution)

<!-- toc:end -->

## Getting started

```bash
./project/bin/reset.sh 10          # state at the start of module 10
```

Write your solution in a file of your own, for example `workspace/1001_my_concurrency.sql`. The course container mounts `workspace/` read-only at `/course/workspace`, so you can load it from psql with `\i /course/workspace/1001_my_concurrency.sql`, or from the shell with `./project/bin/psql.sh < workspace/1001_my_concurrency.sql`. Wrap the file in `BEGIN; ... COMMIT;` so that a typo leaves nothing half-created (section 2.4). Reset and reload as often as you like.

## Requirements

### 1. `place_order(p_user_id bigint, p_items jsonb, p_shipping_address_id bigint DEFAULT NULL) RETURNS SETOF bigint`

`p_items` is a JSON array like `[{"product_id": 1, "quantity": 2}, {"product_id": 24, "quantity": 1}]`.

Behaviour:

- **Validate the input** and raise `invalid_parameter_value` (`22023`) if `p_items` is not a non-empty array, or if any element lacks a numeric `product_id` or a positive integer `quantity`.
- Raise **`BZ003`** if the user does not exist or is inactive (`users.is_active`).
- If an address is given, raise **`BZ004`** unless it belongs to the user.
- **Sum duplicate lines** for the same product before checking stock.
- **Lock the `inventory` rows of all requested products in ascending `product_id` order**, regardless of the order in the cart.
- Raise **`BZ002`** if any requested product does not exist or its status is not `active`.
- Raise **`BZ001`** if any product has fewer than the requested units available (`quantity - reserved`). Put "requested N, available M" in the error's `DETAIL`.
- **Reserve** the stock: increase `inventory.reserved` by the requested quantities.
- Create **one order per seller** (status `pending`, the given shipping address), with one `order_items` row per product at the product's **current price**. Rely on module 09's trigger for `orders.total`.
- **Return the new order ids, ascending.**
- Do not `COMMIT`, `ROLLBACK` or swallow errors inside the function: it runs in the caller's transaction, and any error must undo everything it did.
- It must be correct under `READ COMMITTED`, the default isolation level.

Use `RAISE EXCEPTION '...' USING ERRCODE = 'BZ001', DETAIL = '...'` for the custom codes. Section 16 of the theory walks through the reference design; try to write yours before re-reading it.

### 2. The job queue

A `jobs` table with at least these columns: `id` (identity primary key), `queue text` (default `'default'`), `kind text`, `payload jsonb` (default `'{}'`), `run_at timestamptz` (default `now()`, "not before"), `attempts int` (default 0), `max_attempts int` (default 5), `locked_at timestamptz` and `locked_by text` (the lease), `last_error text`, `done_at timestamptz`, `created_at timestamptz`. Add `CHECK` constraints that `attempts >= 0` and `max_attempts > 0`, and a partial index for the "ready jobs of a queue, oldest first" lookup.

Functions:

- **`dequeue_jobs(p_queue text, p_worker text, p_limit int DEFAULT 1, p_lease interval DEFAULT interval '5 minutes') RETURNS SETOF jobs`**: claims up to `p_limit` ready jobs of the queue, oldest `run_at` first, and returns them. A job is ready when it is not done, `run_at <= now()`, `attempts < max_attempts`, and it is not leased (or its lease is older than `p_lease`). Claiming sets `locked_at`, `locked_by` and increments `attempts`. Concurrent callers must get **different** jobs **without waiting for each other**.
- **`complete_job(p_id bigint) RETURNS boolean`**: marks the job done; returns true if it did.
- **`fail_job(p_id bigint, p_error text) RETURNS boolean`**: stores the error, clears the lease, and reschedules the job with exponential backoff (10 s × 2^(attempts − 1), at most 1 hour).

### 3. Extension: `cancel_order(p_order_id bigint) RETURNS boolean`

`project/db/NOTES.md` points out that nothing ever releases reserved stock. Fix that for cancellations:

- Only `pending` and `paid` orders hold reservations and can be cancelled. Cancelling sets the status to `cancelled` and decreases `inventory.reserved` by the order's item quantities. Return `true`.
- Cancelling an order that is **already cancelled** returns `false` and changes nothing (so a client retry is harmless).
- `shipped`, `delivered` and `refunded` orders raise **`BZ005`**; a missing order raises `no_data_found` (`P0002`).
- Two sessions cancelling the **same order at the same time** must not release the stock twice: exactly one of them returns `true`.
- Cancellations running alongside checkouts on the same products must never deadlock.

The reference solution is [`solutions/cancel_order.sql`](solutions/cancel_order.sql). It is deliberately *not* part of `project/db/`: later modules' reference states do not include it.

## Acceptance checks

All check scripts are in [`solutions/`](solutions/). The course container mounts `modules/` at `/course/modules`, so pgbench (which runs inside the container) can read the scripts directly.

### A. The contract, in one session

```bash
./project/bin/psql.sh < modules/10-transactions-concurrency/solutions/acceptance_functional.sql
```

It tests every error code, a failing multi-line cart (nothing may be created or reserved), a successful checkout across three sellers with a duplicate line, and the queue functions (only ready jobs are claimed, leases, completion, failure with backoff, expired leases). It runs in a transaction that is rolled back. All 24 lines must start with `PASS`:

```text
PASS  BZ001 when stock is insufficient (product 13 is fully reserved)
PASS  BZ002 for a draft product (14)
...
PASS  a job whose lease expired is picked up by another worker
```

### B. Concurrency: no overselling, no deadlocks, no double processing

```bash
# 1. setup: a 'bench' schema with helper functions, exactly 40 units available
#    of products 1, 24 and 47, and 2,000 ready jobs in queue 'bench'
./project/bin/psql.sh < modules/10-transactions-concurrency/solutions/bench_setup.sql

# 2. 8 clients x 50 checkouts; carts list 2-3 of the contested products in random orders
docker exec course-pg pgbench -U postgres -d bazaar -n -c 8 -j 4 -t 50 \
    -f /course/modules/10-transactions-concurrency/solutions/pgbench_place_order.sql

# 3. 8 workers x 300 dequeue attempts for the 2,000 jobs
docker exec course-pg pgbench -U postgres -d bazaar -n -c 8 -j 4 -t 300 \
    -f /course/modules/10-transactions-concurrency/solutions/pgbench_queue_worker.sql

# 4. the verdict
./project/bin/psql.sh < modules/10-transactions-concurrency/solutions/bench_check.sql
```

How it works: [`pgbench_place_order.sql`](solutions/pgbench_place_order.sql) calls `bench.try_place_order()`, which calls your `place_order()` and records the outcome (success or SQLSTATE) in `bench.outcomes`. (pgbench aborts a client on any error other than a serialization failure or deadlock, so business errors such as `BZ001` have to be caught in SQL.) Every cart contains product 1 and one or two of products 24 and 47, listed in five different orders: a function that locked rows in cart order would deadlock. [`pgbench_queue_worker.sql`](solutions/pgbench_queue_worker.sql) calls `bench.work_one()`, which dequeues one job, logs it in `bench.processed`, and completes it.

Expected outcome with the reference implementation (the split between successes and `BZ001` varies from run to run, because the carts are random):

```text
--- place_order outcomes by SQLSTATE (00000 = success, BZ001 = out of stock)
 sqlstate | attempts
----------+----------
 00000    |       30
 BZ001    |      370
(2 rows)

--- per product: units reserved during the run vs units in the new orders
 product_id | available_before | reserved_during_run | units_in_new_orders | available_after
------------+------------------+---------------------+---------------------+-----------------
          1 |               40 |                  40 |                  40 |               0
         24 |               40 |                  25 |                  25 |              15
         47 |               40 |                  26 |                  26 |              14
(3 rows)

--- queue: processed jobs
 processed_rows | distinct_jobs | workers_used
----------------+---------------+--------------
           2000 |          2000 |            8
(1 row)

--- verdict
                         check_name                          | result
-------------------------------------------------------------+--------
 no deadlocks (40P01) in place_order                         | PASS
 only success or BZ001 outcomes                              | PASS
 no overselling: reserved <= quantity for every product      | PASS
 reservations match the units in the new orders              | PASS
 one order per seller per successful checkout                | PASS
 order totals match their items                              | PASS
 some checkouts failed with BZ001 (the stock really ran out) | PASS
 queue: every job processed exactly once                     | PASS
 queue: all bench jobs done, each after one attempt          | PASS
(9 rows)
```

What the numbers mean: product 1 is in every cart, so it sells out first: exactly its 40 available units were reserved, and from then on every checkout fails with `BZ001`. Products 24 and 47 still have stock left, because the carts that would have bought them also contained the sold-out product 1 and failed as a whole. The units reserved during the run equal, product by product, the units in the orders the run created: no reservation without an order, no order without a reservation. Both pgbench runs report `number of failed transactions: 0`. Your throughput (tps) will differ; it doesn't matter here.

> **Test the test.** A concurrency test that never fails proves little. [`solutions/place_order_naive.sql`](solutions/place_order_naive.sql) replaces `place_order()` with a version that reserves stock line by line in cart order (each line an atomic conditional `UPDATE`, so it never oversells). On the reference machine, the same bench produced `00000: 29`, **`40P01: 71`**, `BZ001: 300`, and the first two checks failed. Reset afterwards (`./project/bin/reset.sh 11`, or reload your own file).

### C. Extension: cancellations under concurrency

```bash
./project/bin/psql.sh < modules/10-transactions-concurrency/solutions/acceptance_cancel.sql   # 9 x PASS

# after a fresh bench_setup.sql and pgbench_place_order.sql run:
LO=$(docker exec course-pg psql -U postgres -d bazaar -Atc "SELECT max_order_id + 1 FROM bench.order_watermark")
HI=$(docker exec course-pg psql -U postgres -d bazaar -Atc "SELECT max(id) FROM bazaar.orders")
docker exec course-pg pgbench -U postgres -d bazaar -n -c 8 -j 4 -t 100 -D lo=$LO -D hi=$HI \
    -f /course/modules/10-transactions-concurrency/solutions/pgbench_cancel_orders.sql@3 \
    -f /course/modules/10-transactions-concurrency/solutions/pgbench_place_order.sql@1
./project/bin/psql.sh < modules/10-transactions-concurrency/solutions/bench_cancel_check.sql
```

The mixed run cancels random bench orders (75% of transactions; the same order is often cancelled by two clients at once) while new checkouts keep arriving (25%). The check then cancels whatever is still open and verifies that `reserved` is back exactly where it started. Expected:

```text
--- concurrent cancel outcomes
 result | count
--------+-------
 false  |   530
 true   |    72
(2 rows)

--- sweep: cancel every bench order that is still open
 cancelled_now
---------------
            65
(1 row)

--- verdict
                     check_name                     | result
----------------------------------------------------+--------
 each order cancelled successfully at most once     | PASS
 no deadlocks or unexpected errors while cancelling | PASS
 all bench orders are cancelled                     | PASS
 reserved stock is back to its pre-bench value      | PASS
 no deadlocks in the checkouts that ran alongside   | PASS
(5 rows)
```

## Reference solution

- [`project/db/1001_schema_concurrency.sql`](../../project/db/1001_schema_concurrency.sql): `place_order()`, `jobs`, `dequeue_jobs()`, `complete_job()`, `fail_job()`. `./project/bin/reset.sh 11` loads it on top of state 10.
- [`solutions/cancel_order.sql`](solutions/cancel_order.sql): the extension.
- [`solutions/README.md`](solutions/README.md) discusses the design choices (and the exercises).

When you're done, compare your functions with the reference: the lock order, how the stock check reads fresh data, and where each error is raised. Then reset with `./project/bin/reset.sh 11` so that your database matches the reference state for the exercises and for module 11.
