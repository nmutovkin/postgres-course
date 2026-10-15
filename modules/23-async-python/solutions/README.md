# Module 23: Solutions

<!-- nav:start -->
> [Theory](../README.md) · [Exercises](../exercises.md) · [Project](../project.md) · **Solutions**  
> [← 22 psycopg 3](../../22-psycopg/README.md) · [Course home](../../../README.md) · [24 SQLAlchemy Core →](../../24-sqlalchemy-core/README.md)
<!-- nav:end -->

Every solution is a runnable script in this directory, run against `./project/bin/reset.sh 23` brought to the package's schema (`alembic stamp 2600 && alembic upgrade head`), with Python 3.13, psycopg 3.3.6, asyncpg 0.32.0 and PostgreSQL 17.11. The outputs below come from those runs. Run them from `project/python` with the venv active:

```bash
cd project/python && source .venv/bin/activate
python ../../modules/23-async-python/solutions/ex03_bounded.py
```

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

[`ex01_blocking_start.py`](ex01_blocking_start.py) (the starting point), [`ex01_blocking_fixed.py`](ex01_blocking_fixed.py)

```text
10 requests in 10.14s; first: 'Fernweh Slim-Fit Tank Top - Black'     <- start
10 requests in 1.02s; first: 'Fernweh Slim-Fit Tank Top - Black'      <- fixed
```

**Reasoning.** `gather` starts all ten coroutines, but each runs to completion without ever yielding: `time.sleep(0.5)` blocks the thread, and so does every call of the synchronous psycopg driver (the connect, the query, the implicit commit). The loop runs the requests strictly one after another: 10 × (0.5 + 0.5) s. The `async def` in front of `handle_request` changes nothing about that.

The fix replaces both blocking calls with awaitable ones:

```python
async def handle_request(i: int) -> str:
    await asyncio.sleep(0.5)                       # an async HTTP client would await here
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT title FROM products, pg_sleep(0.5) WHERE id = %s", (i + 1,))
        return (await cur.fetchone())[0]
```

and creates a pool of 10 connections **inside** `main()`, in the running loop (section 3.2). With 10 connections, all ten queries run on the server at the same time. With a pool of 5, the total would be about 1.5 s: the waits overlap, but queries queue for connections.

**Alternative.** Wrapping the original blocking body in `await asyncio.to_thread(...)` also brings it to about 1 s, with one thread per request. It's fine as a stopgap for a library without async support; it doesn't scale like the pool version.

---

## Exercise 2 ★: Port to asyncpg

[`ex02_asyncpg_port.py`](ex02_asyncpg_port.py)

```text
products 1, 20, 24001, -1: identical in both drivers
user 8: 98 orders in 14 pages; newest 495378 shipped 187.99 items=1
```

**Reasoning.** Three differences between the drivers had to be bridged:

1. **jsonb**: asyncpg returns `attributes` as a JSON string unless a codec is installed. The pool's `init` hook installs `json.loads`/`json.dumps` for `jsonb`, once per connection.
2. **Enums**: asyncpg has no enum registration, so `get_product` converts `status` explicitly: `ProductStatus(data["status"])`. Because `ProductStatus` is a `StrEnum`, the dataclasses compare equal to the sync ones.
3. **Optional clauses**: psycopg's version composes an optional `AND (o.created_at, o.id) < (...)` with `psycopg.sql`. asyncpg has no composition module, and with numbered parameters the cleanest solution is two fixed statements, one for the first page and one for later pages. Each is a constant text, so each gets one cached prepared statement.

Datetimes compared equal even though asyncpg returns `tzinfo=datetime.timezone.utc` and psycopg returns `ZoneInfo('Etc/UTC')`: aware datetimes compare by the instant they represent. The keyset cursors are interchangeable between the two implementations, because both encode the same `(created_at, id)`.

**Alternative.** You could use `Record`s directly and skip the dataclasses, at the price of a second "shape" of data flowing through the application. Keeping one set of models for both drivers is what lets later layers (FastAPI in Module 28) not care which driver was used.

---

## Exercise 3 ★: A ceiling on concurrency

[`ex03_bounded.py`](ex03_bounded.py)

```text
500 queries in 0.27s, peak in flight: 8, pool connections opened: 8
sum of per-seller revenue: 266989015.34  check: 266989015.34
```

**Reasoning.** All 500 tasks start immediately, and the semaphore admits 8 at a time:

```python
async with sem:
    in_flight += 1
    peak = max(peak, in_flight)
    try:
        return seller_id, await pool.fetchval(REVENUE, seller_id)
    finally:
        in_flight -= 1
```

The counter needs no lock because asyncio switches tasks only at `await`. The read-modify-write of `in_flight` and `peak` happens between two awaits and can't be interrupted. (With threads, the same code would be a race.) The pool, allowed up to 20 connections, only ever opened 8, because no more than 8 tasks asked for one: the semaphore, not the pool, is the limiter, which is what you want when the pool is shared with other work (web requests) that must not be starved by a batch job.

**Alternatives.** A pool of exactly 8 would also cap concurrency, but then the excess tasks wait inside `pool.acquire()` with its timeout, and every other user of the pool waits behind them. A **worker-queue** pattern (8 consumer tasks pulling seller ids from an `asyncio.Queue`) avoids creating 500 tasks at all and is better for millions of items. For this particular question, one `GROUP BY seller_id` query is of course the right answer. The exercise is about the pattern, for the cases where each item needs its own call (an external API per seller, say).

---

## Exercise 4 ★★: The operations dashboard

[`ex04_dashboard.py`](ex04_dashboard.py)

```text
sequential    166 ms
concurrent    115 ms
  orders_today     694
  revenue_7d       2453498.66
  open_orders      [('paid', 1397), ('pending', 1099), ('shipped', 5141)]
  top_sellers_30d  [('Happy Outlet 1', Decimal('2546025.88')), ('Little Workshop 2', Decimal('1625185.68')), ('Rustic Emporium 5', Decimal('1396340.03')), ('Happy Emporium 3', Decimal('739746.15')), ('Cozy Store 4', Decimal('694916.16'))]
  low_stock        23509
  jobs_ready       0
with a slow panel: 2006 ms, missing: ['top_sellers_30d']
pg_sleep still running on the server: 0
```

**Reasoning.** The panels are independent, so their total time approaches that of the slowest panel instead of the sum (166 ms → 115 ms here; the 30-day top-shops aggregate dominates). The deadline logic is the interesting part:

```python
tasks = {name: asyncio.create_task(panel(pool, name, name == slow)) for name in PANELS}
done, pending = await asyncio.wait(tasks.values(), timeout=deadline)
for t in pending:
    t.cancel()                                             # the driver cancels the server-side query
await asyncio.gather(*pending, return_exceptions=True)     # wait until they are really gone
```

`asyncio.wait` with a timeout doesn't cancel anything; it just tells you what finished. Cancelling the pending tasks makes psycopg send a cancel request for their queries (section 3.4), and **awaiting** them afterwards makes sure that cleanup has finished before the function returns. Without that `gather`, the dashboard would return while the slow query is still being cancelled, and the connection would go back to the pool a moment later. The final check confirms no `pg_sleep` was left running.

A `TaskGroup` inside `asyncio.timeout()` would cancel *everything* at the deadline and give no partial results, which is the right behavior for an all-or-nothing operation and the wrong one for a dashboard. A panel that raises an error (not a timeout) is also reported as `None` here; a production version would log the exception.

**Trade-off.** Six concurrent panels take six pooled connections at once. If a hundred people open the dashboard simultaneously, it needs 600 connection-slots' worth of work in a burst. Cache dashboard data for a few seconds, or compute it in the background, rather than sizing pools for that.

---

## Exercise 5 ★★: A deadline the server enforces

[`ex05_deadline.py`](ex05_deadline.py)

```text
fast query, 1 s budget              0.00s  5 row(s)
pg_sleep(5), 1 s budget             1.00s  TimeoutError (statement timeout after 1.0s)
pool busy 0.8 s, query 0.5 s, 1 s   1.00s  TimeoutError (statement timeout after 1.0s)
queries still running on the server: 0
```

**Reasoning.** The third case is the point of the exercise. The caller waited 0.8 s for the only connection, leaving 0.2 s of its 1 s budget. The function computes the remainder *after* `acquire()` and sets it as the transaction's `statement_timeout`:

```python
deadline = time.monotonic() + seconds
async with asyncio.timeout(seconds + GRACE):
    async with pool.acquire(timeout=seconds) as conn:
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            raise TimeoutError("deadline passed while waiting for a connection")
        async with conn.transaction(readonly=True):
            await conn.execute("SELECT set_config('statement_timeout', $1, true)", str(remaining_ms))
            return await conn.fetch(sql, *args)
```

so the 0.5 s query was stopped by the server at the 1.0 s mark overall. With a fixed `statement_timeout = 1s`, it would have run to completion at 1.3 s, past the caller's deadline.

Why both mechanisms? `statement_timeout` is enforced **by the server**, so the query stops even if the client process is stuck. `asyncio.timeout` is enforced **by the client**, so the caller gets an answer even if the network or the server hangs, where a server-side timeout never arrives. The grace period keeps the client backstop from firing first in the normal case, so the error you see is the more informative server-side one. `set_config(..., true)` is transaction-local, so the setting can't leak to the next borrower of the connection. `QueryCanceledError` is mapped to `TimeoutError` so callers handle one exception type. (A production version would also distinguish a statement timeout from a cancel by an administrator, both SQLSTATE 57014, via the message.)

---

## Exercise 6 ★★: Async retries, proven

[`ex06_async_retry.py`](ex06_async_retry.py)

```text
without retry  n =  35 (expected 200), failed = 165, retries = 0
with retry     n = 200 (expected 200), failed = 0, retries = 252
```

**Reasoning.** Twenty tasks on ten pooled connections read `n`, yield, and write `n + 1` in serializable transactions. Most collide: 165 of 200 increments failed with `SerializationError` without retries. The decorator re-runs the whole function:

```python
for attempt in range(1, max_attempts + 1):
    try:
        return await fn(*args, **kwargs)
    except RETRYABLE:
        if attempt == max_attempts:
            raise
        await asyncio.sleep(random.uniform(0, base_delay * 2 ** (attempt - 1)))
```

The decorated function takes the **pool**, so each attempt acquires a connection and opens a new transaction. Retrying on a connection whose transaction is aborted would fail with "current transaction is aborted". Retrying inside an outer transaction would only retry a savepoint, which can't fix a serialization failure (Module 22, exercise 6). `await asyncio.sleep(...)` (never `time.sleep`) for the backoff keeps the other tasks running.

`InvalidCachedStatementError` is in the list because, inside a transaction, it is asyncpg's way of saying "a migration changed a table under my cached statement" (section 4.5). Re-running the transaction re-prepares the statement.

252 retries for 200 successful increments shows how hard twenty tasks contend for one row. Real code would avoid the contention (`UPDATE ... SET n = n + 1` is atomic and never fails this way); retries are for conflicts you can't design away.

---

## Exercise 7 ★★: Load a day of clickstream

[`ex07_load_events.py`](ex07_load_events.py)

```text
copy_records_to_table   0.51s   195,285 rows/s  -> 100,000 rows in ['events_y2026m01']
                       cleanup: DELETE 100000
executemany             2.19s    45,741 rows/s  -> 100,000 rows in ['events_y2026m01']
                       cleanup: DELETE 100000
```

**Reasoning.** `copy_records_to_table("events", records=rows, columns=[...])` streams the tuples in binary COPY format. The identity column `id` is left out of the column list, so it is generated. `COPY` into the partitioned parent routes each row to its partition, here `events_y2026m01` (Module 14), with no partition logic in Python. It was about 4x faster than `executemany`, which is already pipelined in asyncpg. The difference is per-statement overhead on the server (parse/bind/execute and per-statement partition routing) against COPY's streaming of rows.

The jsonb detail: the pool was created with `init=None`, so no jsonb codec is installed, and asyncpg expects jsonb values as **JSON text**, which is why the generator produces `json.dumps(...)` and `"{}"`. With the `bazaar.aio.db` codec installed, you'd pass dicts instead. Passing a dict *without* the codec fails, and passing a pre-serialized string *with* the codec stores a JSON string literal (`"\"{}\""`), not an object. That's an easy mistake that only shows up later, when queries on the payload return nothing.

Cleanup uses the same half-open day range as the check (`occurred_at >= day AND occurred_at < day + 1 day`), which partition pruning can use, so only `events_y2026m01` is touched. Both statements are module-level constants with `$1`, not f-strings.

---

## Exercise 8 ★★: A live order counter

[`ex08_live_counter.py`](ex08_live_counter.py)

```text
orders per seller from notifications: {1: 8, 2: 8, 3: 8, 4: 6}
orders per seller in the database:    {1: 8, 2: 8, 3: 8, 4: 6}
```

**Reasoning.** asyncpg calls listener callbacks synchronously on the event loop, so the callback does the minimum, `queue.put_nowait(payload)`, and a consumer task does the parsing and counting. That separation matters more as the consumer's work grows (a database lookup per order, a websocket push): a slow callback delays every other task in the process.

The rolled-back order never reached the counter, because `NOTIFY` is delivered only on commit. To place it without committing, the script first opens a transaction (`SELECT 1` on a non-autocommit connection) so that `repo.place_order`'s own `transaction()` block becomes a savepoint. Otherwise it would have committed on its own (Module 22, section 5.2). The final assertion also checks that the notified order ids are exactly the created ones.

Cleanup subtracts each order's quantities from `inventory.reserved` before deleting the orders (whose items cascade). Nothing in the schema un-reserves stock automatically (Module 10's notes), so deleting orders alone would leave the stock reserved forever. The products' available stock afterwards is back to 203, 122, 57 and 66.

**Production notes.** A live counter like this can drift: if the listener reconnects, notifications sent while it was disconnected are lost. Treat it as a cache. Re-seed it from `SELECT seller_id, count(*) FROM orders WHERE created_at >= ...` at startup and after every reconnect, and apply notifications on top.

---

## Exercise 9 ★★: Async export with constant memory

[`ex09_async_export.py`](ex09_async_export.py)

```text
asyncpg  1,266,928 lines in   6.6s, peak RSS   41.9 MiB
psycopg  1,266,928 lines in   5.7s, peak RSS   46.4 MiB
fetch    1,266,928 lines in   5.8s, peak RSS  399.0 MiB
{"order_id": 255798, "product_id": 21937, "quantity": 1, "unit_price": "5.99"}
```

**Reasoning.** Both cursor versions hold one batch of 5,000 rows at a time, so memory stays flat regardless of table size. The naive `fetch()` materializes 1.27 million `Record`s first: almost 400 MiB, and it would grow linearly with the table. Times are similar because most of the work is `json.dumps` per row in Python. The database delivers rows faster than Python can serialize them. (The first line shows the rows come in physical order, not `order_id` order: there is no `ORDER BY`, and for an export that is fine and cheaper.)

`unit_price` is written as a string because JSON numbers are usually parsed as binary floats by the reader (JavaScript, many Python tools), which turns `5.99` into `5.9900000000000002131628...`. Strings keep the exact decimal, as `numeric` does.

**Alternatives.** For a format Postgres can produce itself (CSV, or JSON via `row_to_json`), let the server do the formatting and stream bytes: asyncpg's `copy_from_query(query, output=path, format='csv')`, or `COPY (SELECT row_to_json(t) FROM ...) TO STDOUT` with psycopg. That removes the per-row Python work entirely (Module 22's exercise 7 measured 4x).

---

## Exercise 10 ★★★: An idempotent job handler

[`ex10_idempotent_jobs.py`](ex10_idempotent_jobs.py)

```text
job 828 (send_receipt) failed on attempt 1: ConnectionError('mail server timeout')
jobs waiting for a retry: 1
handler runs: 4 ok, 1 failed
e-mails sent: [(499990, 'james.li77329@example.org'), (500000, 'lukas.meyer10462@example.org')]
```

**Reasoning.** Four jobs ran successfully, but only two e-mails went out, one per order:

```python
async with pool.acquire() as conn, conn.transaction():
    claimed = await conn.fetchval(
        "INSERT INTO receipts_sent (order_id) VALUES ($1) ON CONFLICT (order_id) DO NOTHING RETURNING true",
        order_id,
    )
    if not claimed:
        return                       # someone already sent (or is sending) this receipt
    email = await conn.fetchval(...)
    await send_email(order_id, email)
```

- The primary key on `receipts_sent.order_id` is the **idempotency record**. `ON CONFLICT DO NOTHING RETURNING` tells the handler, atomically, whether it won the claim. Two loops processing duplicates of the same order *at the same time* are serialized by the unique index: the second waits for the first transaction, then sees the conflict.
- **The send happens inside the claiming transaction.** When the flaky sender failed on its first call, the exception rolled back the claim, the job was failed with backoff, and the retry claimed and sent successfully. Had the claim been committed *before* sending, that failure would have lost the receipt forever.
- **The remaining window**: if the e-mail is sent and then the `COMMIT` fails (the database crashes at that instant), the claim is rolled back and the retry sends a second e-mail. No local transaction can close that gap, because the e-mail provider isn't part of it. The standard fix is to pass the order id to the provider as *its* idempotency key, so the provider drops the duplicate. Payment APIs work the same way.

Note what didn't help: `complete_job()` is a separate statement after the handler, so the worker's own bookkeeping can't make handlers idempotent. The handler must be correct when run twice, which is the price of at-least-once delivery and of not holding a transaction open across the whole job. Module 27's transactional outbox builds on this.

---

## Exercise 11 ★★★: What is a prepared statement worth?

[`ex11_statement_cache.py`](ex11_statement_cache.py)

```text
3000 executions of a 3-join query, one connection, median of 3
  asyncpg, statement cache (default)          765 µs/query   prepared statements: 5
  asyncpg, statement_cache_size=0            1675 µs/query   prepared statements: 0
  psycopg async, prepare_threshold=0          836 µs/query   prepared statements: 1
  psycopg async, prepare_threshold=5          805 µs/query   prepared statements: 1
  psycopg async, prepare_threshold=None      1360 µs/query   prepared statements: 0
```

**Reasoning.** With a prepared statement, each execution sends only `Bind`/`Execute` and the server reuses the parsed query and (after five custom plans) a cached generic plan. For this query, planning is a large share of the work, so both drivers' prepared variants cost about 0.8 ms, against 1.4 ms when the statement is parsed and planned every time. `prepare_threshold=0` and `5` are the same over 3,000 executions; the threshold only matters for statements that run a handful of times.

asyncpg *without* its cache is the slowest, slower than psycopg without preparation, because of how asyncpg encodes parameters. It sends parameters in binary and must know their types first, so for every uncached query it sends `Parse` + `Describe` and **waits** for the parameter and result descriptions before it can send `Bind`/`Execute`: two round trips per query instead of one. psycopg without preparation sends `Parse`/`Bind`/`Execute`/`Sync` in one go, with parameter types it chose itself (or "unknown" for the server to infer). The "5" statements on the default asyncpg connection are the benchmark query plus the queries asyncpg runs internally and also caches (type introspection, the `pg_prepared_statements` check).

So behind a pooler without prepared-statement support, `statement_cache_size=0` costs asyncpg more than `prepare_threshold=None` costs psycopg, one more reason to run PgBouncer ≥ 1.21 with `max_prepared_statements` (section 4.8).

---

## Exercise 12 ★★★: A parallel export from one snapshot

[`ex12_snapshot_export.py`](ex12_snapshot_export.py)

```text
independent snapshots          slices sum to 500,031 orders; snapshot had 500,005; match: False; 50 orders inserted meanwhile
with SET TRANSACTION SNAPSHOT  slices sum to 500,006 orders; snapshot had 500,006; match: True; 50 orders inserted meanwhile
```

(The exact counts change from run to run with the timing of the writer; `match` doesn't.)

**Reasoning.** Each `REPEATABLE READ` transaction takes its snapshot at its first query. Four workers starting at slightly different moments while orders are being inserted get four different snapshots, so their slices describe four different moments, and the total (500,031) corresponds to no moment at all. For an export that feeds a report or a data warehouse, that's a silent correctness bug: totals that don't reconcile with anything.

`pg_export_snapshot()` returns an identifier for the coordinator's snapshot. A transaction that runs `SET TRANSACTION SNAPSHOT '<id>'` as its **first** statement adopts exactly that snapshot, so all four workers see the database as of the coordinator's moment, and their slices add up to its count (500,006), even though the writer kept inserting. Rules that the script follows:

- The exporting transaction must stay open until every worker has imported the snapshot. Here the workers run inside the coordinator's `async with coordinator.transaction()` block.
- Importers must use `REPEATABLE READ` or `SERIALIZABLE`. A `READ COMMITTED` transaction takes a new snapshot per statement.
- `SET TRANSACTION SNAPSHOT` takes no parameters, so the id is checked against `[0-9A-F-]+` before being embedded in the SQL text.

This is the mechanism `pg_dump --jobs` uses for parallel dumps (Module 18), and logical replication uses for its initial table copy (Module 19).
