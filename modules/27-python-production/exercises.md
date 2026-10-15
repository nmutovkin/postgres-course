# Module 27: Exercises

<!-- nav:start -->
> [Theory](README.md) · **Exercises** · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 26 Migrations with Alembic](../26-migrations/README.md) · [Course home](../../README.md) · [28 Capstone: the Bazaar Service →](../28-capstone/README.md)
<!-- nav:end -->

**Setup.** `./project/bin/reset.sh 27`, then in `project/python`: `alembic stamp 2600 && alembic upgrade head`, and `pytest -n 4` passes. Exercises that need committed data run in fresh databases (a copy of the test template, `bazaar.testing.TemplateDatabase`), so the reference database stays as it is. Leave it at revision 2701, and undo any role setting you change.

Solutions are in [`solutions/`](solutions/); run them from `project/python`.

---

<!-- toc:start -->
**Contents**

- [Exercise 1 ★: Classify real failures](#exercise-1--classify-real-failures)
- [Exercise 2 ★: Timeouts for one slow endpoint](#exercise-2--timeouts-for-one-slow-endpoint)
- [Exercise 3 ★: Timeouts that survive PgBouncer](#exercise-3--timeouts-that-survive-pgbouncer)
- [Exercise 4 ★★: A retry decorator with jitter, proven](#exercise-4--a-retry-decorator-with-jitter-proven)
- [Exercise 5 ★★: How much faster are template databases?](#exercise-5--how-much-faster-are-template-databases)
- [Exercise 6 ★★: Pools and `fork()`](#exercise-6--pools-and-fork)
- [Exercise 7 ★★: A concurrency test that can fail](#exercise-7--a-concurrency-test-that-can-fail)
- [Exercise 8 ★★: A projection fed by the outbox](#exercise-8--a-projection-fed-by-the-outbox)
- [Exercise 9 ★★: Database time per request](#exercise-9--database-time-per-request)
- [Exercise 10 ★★★: Size a pool, and prove the size](#exercise-10--size-a-pool-and-prove-the-size)
- [Exercise 11 ★★★: Exactly once, under every crash](#exercise-11--exactly-once-under-every-crash)
- [Exercise 12 ★★★: Checkout through failovers](#exercise-12--checkout-through-failovers)

<!-- toc:end -->

## Exercise 1 ★: Classify real failures

Write a script that **triggers** each of these situations against the reference database (each in its own short-lived connection, changing nothing), catches the exception, and prints its class, SQLSTATE, `bazaar.resilience.classify()` and whether `DEFAULT_POLICY` would retry it, for non-idempotent and idempotent work:

1. inserting a user with an existing email;
2. a query that exceeds a 50 ms `statement_timeout`;
3. `SELECT ... FOR UPDATE NOWAIT` on an order another connection has locked;
4. two `REPEATABLE READ` transactions updating the same inventory row;
5. `CREATE TABLE` as `bazaar_app`;
6. `place_order()` with a product that doesn't exist;
7. connecting as `bazaar_app` with a wrong password;
8. a session terminated by `pg_terminate_backend()` (what a failover does).

Is any classification wrong? Fix it in `bazaar/resilience.py` and add a test.

**Self-check:** the SQLSTATEs are `23505`, `57014`, `55P03`, `40001`, `42501`, `BZ002`, none, `57P01`. Only the lock and serialization cases are retried for any work; the terminated session only for idempotent work.

<details><summary>Hint</summary>

Case 4 needs a thread: the second `UPDATE` blocks on the first transaction's row lock and fails when the first commits. Look closely at case 7: which kind should "wrong password" be, and what does the exception give you to tell it apart from a dropped connection?
</details>

## Exercise 2 ★: Timeouts for one slow endpoint

The seller statement (`bazaar.sa.reports.seller_statement`) may run for up to 2 s and may wait at most 0.5 s for a lock. Everything else must keep the connection defaults (5 s, no lock timeout), also behind PgBouncer. Write `seller_report(engine, seller_id, start, end)` that does this, and show:

1. a normal report (seller 1, November 2025) succeeds;
2. a long one (seller 1, three years) with a 0.5 s limit fails with `57014`;
3. while another session holds `LOCK TABLE orders IN ACCESS EXCLUSIVE MODE` (think: a migration), the November report fails with `55P03` after 0.5 s;
4. a caller in synchronous code that gives up after 0.5 s (`future.result(timeout=0.5)`) on a three-year report: is the server still working afterwards?
5. on the same pooled connection afterwards, `SHOW statement_timeout` and `SHOW lock_timeout` are back to `5s` and `0`.

**Self-check:** case 3 fails at about 0.51 s; in case 4 one client backend is still running the report after the caller gave up, and the report finishes anyway about a second later.

<details><summary>Hint</summary>

`set_config(name, value, true)` is `SET LOCAL`. Filter `pg_stat_activity` by `backend_type = 'client backend'`, or parallel workers will inflate your count.
</details>

## Exercise 3 ★: Timeouts that survive PgBouncer

Section 5.3 showed that PgBouncer refuses (or, worse, silently drops) the `options` startup parameter that carries the package's timeouts. Move the defaults to the server:

- `bazaar_app`: `statement_timeout` 5 s, `idle_in_transaction_session_timeout` 30 s, `application_name` `bazaar`;
- `bazaar_seller`: 3 s and 30 s; `bazaar_analyst`: 15 min and 5 min;

only for the `bazaar` database. Verify by connecting as each role **without** sending any options or `application_name`, as PgBouncer would. Then reset the settings.

**Self-check:** `pg_db_role_setting` has three rows for the `bazaar` database; `bazaar_app` sees `5s`, `30s` and `application_name = bazaar`; a client that sends its own `application_name` still wins.

<details><summary>Hint</summary>

`ALTER ROLE ... IN DATABASE ... SET ...`. Role settings apply when a session starts; PgBouncer's server connections are sessions too.
</details>

## Exercise 4 ★★: A retry decorator with jitter, proven

Without using `bazaar.resilience`, write `retry_transaction(attempts, base, cap, *, sleep, rand)`: a decorator that retries the decorated function on `40001` and `40P01` with **full-jitter** exponential backoff. `sleep` and `rand` are injectable so tests can check the schedule without waiting.

1. With `rand=lambda: 0.5` and an injected `SerializationFailure` on the first three calls, check the exact sleep schedule.
2. Show that a conflict that never goes away is re-raised after `attempts` tries.
3. Produce a **real** `40001`: a write skew under `SERIALIZABLE` in a scratch database ("at least one staff member on duty": two members go off duty at the same moment). Use a barrier so both transactions read before either writes. Show that the retry turns it into one success and one refusal.
4. Simulate why jitter matters: 50 clients fail at the same moment; in 1 ms slots, a retry succeeds only if exactly one client picked that slot. How many rounds until all succeed, with and without jitter?

**Self-check:** the schedule is 25, 50, 100 ms for `base=0.05`; the real case shows exactly one `40001` retry, 3 calls in total, and one staff member still on duty; without jitter, all 50 clients keep colliding forever.

<details><summary>Hint</summary>

Two staff rows, two transactions each updating *its own* row: there's no row lock conflict, so `READ COMMITTED` would happily let both go off duty. If you get `40P01` instead of `40001`, your transactions update the same rows in opposite orders: that's a deadlock, not a write skew.
</details>

## Exercise 5 ★★: How much faster are template databases?

Write a test module with 20 tests, each needing a database of its own (each test: insert a user, seller, category, product and stock, place an order, check the total). Provide one fixture with two modes, selected by an environment variable: `migrate` (`CREATE DATABASE` + `alembic upgrade head` per test) and `template` (a template built once, cloned per test). Measure the suite's run time in both modes, sequentially and with `-n 4`.

**Self-check:** the per-test setup is roughly 0.4–0.8 s with migrations and 0.2–0.4 s with the template; with `-n 4` the template mode is about 3× faster than sequential migrations. (Timings vary with the machine's load; repeat the runs.)

<details><summary>Hint</summary>

`bazaar.testing.TemplateDatabase` and `scratch_database()`. Give databases unique names that include `PYTEST_XDIST_WORKER`. `pytest --durations=3` shows setup times.
</details>

## Exercise 6 ★★: Pools and `fork()`

Create a SQLAlchemy engine (`pool_size=1`), use it once in the parent, then fork three workers with `multiprocessing` (start method `fork`) that use the same engine to run 200 queries each. What happens? Fix it with `bazaar.sa.engine.dispose_after_fork()`, and explain why `dispose(close=False)` and not `dispose()`.

**Self-check:** without the fix, workers fail with a mix of errors that changes from run to run (`consuming input failed: server closed the connection unexpectedly`, `can't change 'autocommit' now: connection in transaction status`, `This result object does not return rows`, even `IdleInTransactionSessionTimeout`); with it, every worker gets its own backend and succeeds.

<details><summary>Hint</summary>

After `fork()`, the parent's pooled connection (a socket) exists in four processes. Who reads which reply?
</details>

## Exercise 7 ★★: A concurrency test that can fail

Module 10's `cancel_order()` (`modules/10-transactions-concurrency/solutions/cancel_order.sql`) releases an order's stock reservation. Write a pytest test that cancels the **same** order ten times at the same moment and checks that the stock is released exactly once. Prove the test is worth something: run it against a naive Python implementation (read the status, then release and update, no lock) and show that it catches the bug.

Use a fresh database per test, built from a template that has the function installed. Set up one product with 10 units and **two** pending orders (2 and 6 units); cancel the first.

**Self-check:** with `cancel_order()`, exactly one call returns `true`, nine return `false`, and `reserved` ends at 6. With the naive version, several calls "succeed" and `reserved` ends below 6.

<details><summary>Hint</summary>

Why two orders? With only one, try it: the `CHECK (reserved >= 0)` on inventory turns the over-release into errors, which hides the bug from a test that only looks at `reserved`. A barrier inside the naive function, after it has read the status, makes every caller read `pending`.
</details>

## Exercise 8 ★★: A projection fed by the outbox

Build `seller_revenue_daily (seller_id, day, orders, revenue)` maintained by a consumer of `order.placed` events. In a fresh database: three sellers, 40 checkouts through `services.checkout()`, the relay publishing to a broker that delivers every third message **twice**, and a consumer that applies each delivery with `outbox.consume_once()`. Then compare the projection with an aggregate over `orders`.

**Self-check:** no mismatches between the projection and `orders`; the number of skipped duplicates equals the number of extra deliveries.

<details><summary>Hint</summary>

The event's `created_at` and the order's `created_at` are both `now()` of the same transaction, so they're identical: use the event's for the day. `INSERT ... ON CONFLICT (seller_id, day) DO UPDATE SET orders = orders + 1, ...` makes each event an increment, which is exactly why duplicates must be filtered.
</details>

## Exercise 9 ★★: Database time per request

Extend the logging so that every request's log line includes its database time and statement count (`db_ms`, `db_statements`), using SQLAlchemy events and a `ContextVar`. Simulate three routes for five users each: Module 25's `order_history()` service, an "old" handler that loads orders and touches `items` and `product` lazily, and a product page. Then write the report a log system would give you: routes by total database time, with average and maximum statements per request.

**Self-check:** the service route uses 2 statements per request; the old handler's average is in the dozens (its maximum is 1 + 20 orders' items + their products); it tops the report by database time.

<details><summary>Hint</summary>

Statements outside a request (startup, scripts) must not be counted: the `ContextVar` is `None` there. Reset it with the token `set()` returns, so nested and concurrent requests don't mix.
</details>

## Exercise 10 ★★★: Size a pool, and prove the size

Take a seller dashboard request (orders and revenue per day since July 2025, for one of the 50 biggest sellers) on an async engine:

1. Measure W, the time a request holds a connection.
2. For λ = 400 requests/s, compute L = λW.
3. Run an **open-loop** load test (Poisson arrivals: `asyncio.sleep(random.expovariate(rate))` between starts) for 4 s against pools of 1, 2, 4, 8 and 16 connections, opened before the clock starts. Report p50/p99 latency and p99 time waiting for the pool.
4. Which pool size would you deploy, and what does a bigger one buy?

**Self-check:** with W around 3–5 ms, L is above 1, and a one-connection pool is unstable (latencies in seconds, growing with the test's length); two connections work but queue at p99; from four on, the latencies stop improving.

<details><summary>Hint</summary>

A closed loop ("N workers, each sends the next request when the previous one finishes") can never overload the pool: it slows down instead. Real users don't wait for each other. `engine.connect()`'s own duration is the pool wait.
</details>

## Exercise 11 ★★★: Exactly once, under every crash

Build the whole pipeline in a fresh database: a producer placing 200 orders with an `order.placed` event each, the relay, an in-memory broker with at-least-once semantics (a message stays until acknowledged), and a consumer whose effect is a row in `emails_sent (order_id)` (deliberately **without** a unique constraint). Inject crashes at random:

- P: the producer's transaction fails after writing the event;
- R1: the broker rejects a publish; R2: the relay dies after publishing, before its commit;
- C1: the consumer's handler fails after writing its effect; C2: the consumer commits but dies before acknowledging.

Run relay and consumer rounds until the outbox and the broker are empty.

**Self-check:** every crash type fired several times; the number of emails equals the number of committed orders, with one email per order and no order without one.

<details><summary>Hint</summary>

`pg_terminate_backend()` on the relay's own backend, from another connection, is a convincing crash. For C2, simply don't call `ack()`. Which component makes each of the five crashes harmless?
</details>

## Exercise 12 ★★★: Checkout through failovers

In a fresh database with two sellers, a client places 150 checkouts (one product from each seller: two orders per checkout) while a thread terminates every application connection every 50–150 ms. In addition, 10% of checkouts **lose their reply after a successful commit** (raise a connection error after the `Session.begin()` block). The client retries connection errors with `call_with_retry(..., idempotent=True)`.

Run it twice: without idempotency keys, then with one key per checkout. Count the orders.

**Self-check:** without keys, there are more than 300 orders (duplicates from the lost replies); with keys, exactly 300, and the stock reserved equals the orders' units.

<details><summary>Hint</summary>

`idempotent=True` is a promise the caller makes. Without keys the promise is false, and the duplicates are the proof. `services.checkout(..., idempotency_key=key, emit_events=False)`.
</details>
