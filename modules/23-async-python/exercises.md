# Module 23: Exercises

<!-- nav:start -->
> [Theory](README.md) · **Exercises** · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 22 psycopg 3](../22-psycopg/README.md) · [Course home](../../README.md) · [24 SQLAlchemy Core →](../24-sqlalchemy-core/README.md)
<!-- nav:end -->

**Starting state.** `./project/bin/reset.sh 23`, then `alembic stamp 2600 && alembic upgrade head` in `project/python` (the package's schema, see the README's setup), and Module 22's virtual environment with the `bazaar` package installed. Run solutions from `project/python`:

```bash
cd project/python && source .venv/bin/activate
export BAZAAR_DSN=postgresql://postgres:postgres@localhost:5432/bazaar
python my_exercise.py
```

You may use the package's helpers (`bazaar.aio.db.create_pool`, `create_asyncpg_pool`, `asyncpg_connect`, `bazaar.aio.repo`, `bazaar.worker.Worker`). Exercises that write data clean up after themselves, so the self-checks stay valid on reruns. Timings vary by machine; counts and properties don't.

Solutions: [solutions/README.md](solutions/README.md), with runnable files in [solutions/](solutions/).

---

<!-- toc:start -->
**Contents**

- [Exercise 1 ★: Unblock the loop](#exercise-1--unblock-the-loop)
- [Exercise 2 ★: Port to asyncpg](#exercise-2--port-to-asyncpg)
- [Exercise 3 ★: A ceiling on concurrency](#exercise-3--a-ceiling-on-concurrency)
- [Exercise 4 ★★: The operations dashboard](#exercise-4--the-operations-dashboard)
- [Exercise 5 ★★: A deadline the server enforces](#exercise-5--a-deadline-the-server-enforces)
- [Exercise 6 ★★: Async retries, proven](#exercise-6--async-retries-proven)
- [Exercise 7 ★★: Load a day of clickstream](#exercise-7--load-a-day-of-clickstream)
- [Exercise 8 ★★: A live order counter](#exercise-8--a-live-order-counter)
- [Exercise 9 ★★: Async export with constant memory](#exercise-9--async-export-with-constant-memory)
- [Exercise 10 ★★★: An idempotent job handler](#exercise-10--an-idempotent-job-handler)
- [Exercise 11 ★★★: What is a prepared statement worth?](#exercise-11--what-is-a-prepared-statement-worth)
- [Exercise 12 ★★★: A parallel export from one snapshot](#exercise-12--a-parallel-export-from-one-snapshot)

<!-- toc:end -->

## Exercise 1 ★: Unblock the loop

[`solutions/ex01_blocking_start.py`](solutions/ex01_blocking_start.py) simulates a tiny server: `main()` handles 10 requests with `asyncio.gather`, and each request waits 0.5 s for a "recommendation service" and then runs a query that takes another 0.5 s. Run it and explain the elapsed time. Then rewrite `handle_request()` (and only add setup to `main()`) so the requests overlap.

**Self-check:** the original takes about 10 s; yours takes about 1 s and prints the same first title, `'Fernweh Slim-Fit Tank Top - Black'`.

<details><summary>Hint</summary>

There are two blocking calls, and the second one hides inside a `with`. A pool sized for the concurrency you want is better than one connection per request.

</details>

---

## Exercise 2 ★: Port to asyncpg

Port `get_product()` and `list_orders_for_user()` (with keyset pagination and the same opaque cursors) from `bazaar.repo` to asyncpg. They must return **the same model objects** as the sync versions, including `ProductStatus`/`OrderStatus` enum members and `attributes` as a `dict`. Prove it by comparing with the sync repository: products 1, 20, 24001 and a nonexistent id, and every page of user 8's orders with `limit=7`.

**Self-check:** all comparisons are equal. User 8 has 98 orders on 14 pages, and the newest is order 495378 (`shipped`, 187.99, 1 item).

<details><summary>Hint</summary>

Install a jsonb codec in the pool's `init`. asyncpg returns enums as `str`, so convert them before building the dataclass. With `$n` parameters, two fixed SQL texts (first page and later pages) are simpler than composing an optional clause. Do aware datetimes with different `tzinfo` objects compare equal?

</details>

---

## Exercise 3 ★: A ceiling on concurrency

Compute the revenue (orders `paid`, `shipped` or `delivered`) of each of sellers 1–500 with **one query per seller**, all started at once as tasks, but with **at most 8** queries in flight at any moment, using a pool whose `max_size` is 20. Track the peak number in flight, and check the sum of the 500 results against a single aggregate query.

**Self-check:** peak in flight is 8, the pool opens 8 connections, and both sums are `266989015.34`.

<details><summary>Hint</summary>

`asyncio.Semaphore`. Why doesn't the in-flight counter need a lock?

</details>

---

## Exercise 4 ★★: The operations dashboard

Build `dashboard(pool, deadline=2.0)`, which returns six panels for "today" (2025-12-31 in the large dataset): orders created today; revenue of the last 7 days (paid/shipped/delivered, 2025-12-25 onwards); open orders per status; the top 5 shops by revenue over the last 30 days; the number of active products with at most 2 units available; and the number of ready jobs. Run the panels **concurrently** with a psycopg `AsyncConnectionPool`, and return partial results: a panel that misses the deadline is `None`, and its query must not keep running on the server. Compare with running the panels sequentially, then simulate a slow panel (an extra `pg_sleep(3)` before the top-shops query).

**Self-check:** orders today 694; revenue 2453498.66; open orders `[('paid', 1397), ('pending', 1099), ('shipped', 5141)]`; top shop `Happy Outlet 1` with 2546025.88; low stock 23509; ready jobs 0. With the slow panel, the call returns after about 2 s with only `top_sellers_30d` missing, and no `pg_sleep` is left running on the server.

<details><summary>Hint</summary>

`asyncio.wait(tasks, timeout=...)` returns `done` and `pending` sets without cancelling anything; cancel the pending tasks yourself, then *await* them (with `return_exceptions=True`) so that the cancellation, including the server-side cancel, completes before you return.

</details>

---

## Exercise 5 ★★: A deadline the server enforces

Write `query_with_deadline(pool, seconds, sql, *args)` for an asyncpg pool. The budget starts when the function is called, so **time spent waiting for a pooled connection counts**. Whatever is left becomes the server-side `statement_timeout` of the query's transaction, so the server stops the query itself. A client-side `asyncio.timeout` with a small grace period is only a backstop. A server-side cancellation raises `TimeoutError` like the client-side one. Demonstrate three cases with a pool of **one** connection: a fast query; `pg_sleep(5)` with a 1 s budget; and a `pg_sleep(0.5)` with a 1 s budget while another task holds the only connection for 0.8 s.

**Self-check:** the fast query returns 5 rows immediately; both other cases raise `TimeoutError` after 1.00 s (not 1.3 s in the third case); and afterwards no `pg_sleep` query is active on the server.

<details><summary>Hint</summary>

`SET LOCAL` can't take parameters; `set_config('statement_timeout', $1, true)` can. Compute the remaining milliseconds *after* `acquire()` returns, and raise immediately if nothing is left.

</details>

---

## Exercise 6 ★★: Async retries, proven

Write an async decorator `retry_transaction()` for functions that run one complete asyncpg transaction (they receive a pool and acquire their own connection). It retries on `SerializationError`, `DeadlockDetectedError` and `InvalidCachedStatementError` with jittered exponential backoff. Prove it: create `ex06_counter(id int PRIMARY KEY, n int)` with `n = 0`, and let 20 tasks each run 10 serializable read-modify-write increments, once without and once with the decorator. Drop the table at the end.

**Self-check:** without retries, `n` ends well below 200 and `n + failures = 200`; with retries, `n = 200` with no failures.

<details><summary>Hint</summary>

Let the decorated function take the *pool*, not a connection: each attempt must start a fresh transaction on a connection that isn't in an aborted state. Add an `await asyncio.sleep(0)` between the read and the write so the tasks actually interleave.

</details>

---

## Exercise 7 ★★: Load a day of clickstream

Generate 100,000 events for 2026-01-15 (spread evenly over the day; a quarter with `user_id NULL`; 7 events per `session_id`; `product_id` set for `product_view` and `add_to_cart`; a JSON payload `{"q": ...}` for searches) and load them into the partitioned `events` table with asyncpg's `copy_records_to_table`, then again with `executemany`. For each, report the time, the number of rows for that day, and which partition(s) they landed in, then delete them.

**Self-check:** 100,000 rows, all in `events_y2026m01`, both times. COPY is several times faster than `executemany` (0.5 s versus 2.2 s in the reference run). After cleanup, the day has 0 events.

<details><summary>Hint</summary>

`COPY` into a partitioned table routes rows like `INSERT` does. `tableoid::regclass` tells you which partition a row lives in. Watch the jsonb column: what does asyncpg expect for jsonb if you install no codec, and what if you do?

</details>

---

## Exercise 8 ★★: A live order counter

Keep a live count of new orders per seller from the `new_order` notifications of `2301_notify.sql`, using an asyncpg listener whose callback only hands the payload to an `asyncio.Queue`, and a consumer task that aggregates. Meanwhile, place 30 orders through `bazaar.aio.repo.place_order`: 8 each for sellers 1, 2 and 3, and 6 for seller 4 (use products 822, 368, 5485 and 4241). Also place one order for seller 4 that you **roll back**. Compare the counter with the database, then remove the orders and their stock reservations.

**Self-check:** the counter shows `{1: 8, 2: 8, 3: 8, 4: 6}`, identical to the database; the rolled-back order never appears; afterwards the four products' available stock is back to 203, 122, 57 and 66.

<details><summary>Hint</summary>

Remember Module 22's section 5.2 pitfall when you try to place an order that you can still roll back. To undo reservations, subtract the ordered quantities per product before deleting the orders.

</details>

---

## Exercise 9 ★★: Async export with constant memory

Export all of `order_items` (1,266,928 rows) as NDJSON (one JSON object per line, `unit_price` as a string) from async code, twice: with an asyncpg cursor and with a psycopg async server-side cursor. Then do it the naive way with `fetch()`. Report time and peak RSS for each.

**Self-check:** 1,266,928 lines each time. Peak RSS stays below 60 MiB for both cursors, while the naive version needs several hundred MiB (399 MiB in the reference run).

<details><summary>Hint</summary>

Both cursors need a transaction. Use `prefetch` (asyncpg) or `itersize` (psycopg) of a few thousand. Why should `unit_price` not be a JSON number?

</details>

---

## Exercise 10 ★★★: An idempotent job handler

The worker delivers jobs *at least once*. Write a `send_receipt` handler that e-mails a receipt for `payload.order_id` and is **idempotent**: however many times a receipt job for the same order runs (duplicate enqueues, or a retry after a crash), the buyer gets one e-mail. Use a table `receipts_sent(order_id bigint PRIMARY KEY, sent_at timestamptz)` (create and drop it in your script) as the idempotency record, and a fake mail sender that appends to a list and **fails the first time** it's called. Enqueue receipt jobs for orders 500000, 500000, 499990, 500000 on a fresh queue, run `bazaar.worker.Worker` with your handler, fast-forward the failed job's backoff, and let it finish. Explain the remaining window in which a duplicate could still be sent.

**Self-check:** exactly two e-mails are sent: to `lukas.meyer10462@example.org` for order 500000 and to `james.li77329@example.org` for order 499990. The worker reports 4 successful runs and 1 failure.

<details><summary>Hint</summary>

Claim the key with `INSERT ... ON CONFLICT DO NOTHING RETURNING true` and send inside the same transaction. A failed send then rolls the claim back. `UPDATE jobs SET run_at = now() WHERE queue = ... AND done_at IS NULL` skips the 10-second backoff.

</details>

---

## Exercise 11 ★★★: What is a prepared statement worth?

Run Module 22's 3-join "recent orders of a user" query (with `$1` or `%s` for the user id) 3,000 times on one connection, for users 1 to 3000, and report the median time per query over three rounds for: asyncpg with its statement cache; asyncpg with `statement_cache_size=0`; psycopg async with `prepare_threshold` 0, 5 (default) and `None`. Also report how many statements are prepared on each connection at the end. Explain the ranking, in particular why asyncpg *without* its cache is slower than psycopg without prepared statements.

**Self-check:** both drivers' prepared variants come out close to each other (about 770–840 µs in the reference run); `prepare_threshold=None` is about 1.7x slower; and `statement_cache_size=0` is the slowest variant of all.

<details><summary>Hint</summary>

Think about what asyncpg needs to know before it can encode a parameter in binary, and how it finds out when it can't look it up in its cache. `pg_prepared_statements` shows each connection's statements.

</details>

---

## Exercise 12 ★★★: A parallel export from one snapshot

An export reads `orders` in 4 slices (`id % 4 = k`) over 4 connections concurrently to finish faster, while the shop keeps taking orders. Show that the slices, read in separate transactions, **don't** add up to any single point in time. Then fix it: a coordinator opens a `REPEATABLE READ` transaction, calls `pg_export_snapshot()`, counts all orders, and keeps its transaction open while the 4 workers start `REPEATABLE READ` transactions with `SET TRANSACTION SNAPSHOT`. A writer task inserts an order every few milliseconds throughout; delete its orders at the end.

**Self-check:** with independent transactions, the slices' total (count and sum) doesn't match the coordinator's count (`match: False`); with the shared snapshot it matches exactly (`match: True`), even though about 50 orders were inserted during the export. Afterwards `orders` has 500,000 rows again.

<details><summary>Hint</summary>

`SET TRANSACTION SNAPSHOT` must be the first statement of the transaction and can't take a parameter. Validate the snapshot id before embedding it. Stagger the workers' start (`sleep(0.05 * k)`) to make the inconsistency visible.

</details>
