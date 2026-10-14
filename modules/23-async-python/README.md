# Module 23: Async Python: psycopg async and asyncpg

On Black Friday, Bazaar's product pages are requested a few thousand times a second. Each page needs five or six small queries, and each query spends most of its life *waiting*: for the network, for the server, for a disk page. In Module 22's synchronous code, a thread that waits can do nothing else, so serving many pages at once means many threads. **asyncio** takes the opposite approach: one thread, an event loop, and code that says explicitly where it waits (`await`), so the loop can run something else meanwhile. This module teaches asyncio from the angle of database work, then the two async drivers for PostgreSQL: psycopg's async API (the same driver you know, with `await`) and asyncpg (a different design built for speed). It closes with three things async code is good at: fanning out queries concurrently, consuming `LISTEN`/`NOTIFY` events, and running a pool of background job workers. Along the way, a benchmark compares the drivers with real numbers.

**What you'll learn**

- What async I/O buys you and what it doesn't: many concurrent waits in one thread, but no extra CPU, and no extra database capacity.
- The asyncio essentials for database code: coroutines, the event loop, tasks, `gather` versus `TaskGroup`, semaphores, `asyncio.timeout`, and cancellation, including what happens to a query that is running when its task is cancelled.
- psycopg's `AsyncConnection` and `AsyncConnectionPool`: Module 22's API with `async`/`await`.
- asyncpg: its design (its own protocol implementation, binary format, no libpq), its API (`$1` parameters, `fetch`/`fetchrow`/`fetchval`/`execute`, `Record`), transactions, the prepared-statement cache, type codecs, COPY, cursors, and pools with `init`/`setup` hooks.
- Prepared statements behind PgBouncer in transaction mode, demonstrated with both drivers.
- Concurrency correctness: one connection runs one query at a time, so never share a connection between concurrent tasks; one pool per process.
- Async `LISTEN`/`NOTIFY` consumers, and an async job worker on Module 10's `SKIP LOCKED` queue with N concurrent loops, NOTIFY wake-ups, and graceful shutdown.
- A fair benchmark of psycopg async, asyncpg, and sync psycopg with threads.

**Before you start**

```bash
./project/bin/reset.sh 23            # same database state as 22 (the large dataset)
./project/bin/psql.sh < project/python/sql/2301_notify.sql     # NOTIFY triggers for sections 7 and 8
cd project/python && source .venv/bin/activate                  # the venv from Module 22
export BAZAAR_DSN=postgresql://postgres:postgres@localhost:5432/bazaar
```

You need Module 22: this module assumes you know psycopg's connections, transactions, parameters, row factories and pools, and only explains what is different with `async`. The examples are in [`examples/`](examples/); run them like Module 22's, from `project/python`. Section 4.8 also uses a small PgBouncer lab in [`lab/`](lab/).

The outputs come from Python 3.13, psycopg 3.3.6, asyncpg 0.32.0 and PostgreSQL 17.11, with client and server on the same 4-vCPU machine (the server in Docker, reached through port forwarding with a round trip of about 0.2 ms).

---

## 1. Why async, and when not

### 1.1 Waiting is the job

Here is what serving one Bazaar product page looks like in time, from the application's point of view:

```text
  parse request ▌
  query product          ░░░░▌                 ▌ = the CPU works (Python code)
  query stock                 ░░░▌             ░ = waiting (network + server)
  query reviews                  ░░░░░░▌
  query "bought together"              ░░░░░░░░░░░░▌
  render HTML                                      ▌▌
```

Almost all of the elapsed time is spent waiting. A synchronous program waits by blocking its thread inside a system call (`recv()` on the socket), so to serve 200 pages concurrently you need 200 threads. Threads work, and Module 22's pool is thread-safe, but each one costs memory (a stack of up to 8 MB reserved, though less is committed), the OS schedules them preemptively, and in CPython only one thread runs Python code at a time anyway (the GIL).

An **event loop** handles concurrency differently. Everything runs in one thread. When code needs to wait for a socket, it doesn't block: it registers interest in the socket ("tell me when there is data") and gives control back to the loop. The loop runs whatever else is ready, and asks the OS (with `epoll` on Linux) which sockets became readable. A thousand concurrent waits then cost a thousand small objects, not a thousand threads.

### 1.2 What async does not do

Be clear about the limits before rewriting anything:

- **It doesn't make a single query faster.** A 40 ms query takes 40 ms whether you `await` it or not. Async improves *throughput under concurrency* and lets one request run *independent* queries at the same time (section 6).
- **It doesn't add CPU.** One event loop uses one core. CPU-bound work (rendering big JSON, image processing, heavy computation in Python) blocks the loop and every request waiting on it. Run such work in a thread or process pool (`asyncio.to_thread`, `loop.run_in_executor`), or scale out with more processes.
- **It doesn't add database capacity.** Postgres still runs one backend process per connection and does the same work per query. 2,000 concurrent coroutines with a pool of 20 connections still means at most 20 queries running at once. The rest queue in the pool, which is exactly what should happen (Module 17's argument about connection counts holds unchanged).
- **It's contagious.** An `async def` function can only be awaited from another `async def`. Async code needs async libraries all the way down: the driver, the HTTP client, the cache client. One blocking call in the wrong place stalls everything (section 2.2).

Use async when a process spends its time waiting on many things at once: web services with many concurrent requests (the FastAPI capstone in Module 28), fan-out to several databases or services, long-lived listeners, and job workers whose jobs call other services. For a batch script that runs one query after another, sync psycopg is simpler and just as fast.

---

## 2. asyncio for database code

### 2.1 Coroutines, `await`, and the loop

```python
async def stock(conn, product_id: int) -> int:
    cur = await conn.execute("SELECT quantity - reserved FROM inventory WHERE product_id = %s", (product_id,))
    row = await cur.fetchone()
    return row[0]
```

- `async def` defines a **coroutine function**. Calling it does *not* run it: `stock(conn, 16)` returns a coroutine object, a paused computation.
- `await` runs a coroutine (or another awaitable) until it finishes and gives you its result. At every `await` that actually has to wait, the current coroutine is suspended and control goes back to the event loop.
- `asyncio.run(main())` creates an event loop, runs `main()` to completion, and closes the loop. A program has one `asyncio.run()` at the top.

The scheduling is **cooperative**: a coroutine only gives up control at an `await`. Between two `await`s it runs without interruption, which is why async code needs fewer locks than threaded code. A dict updated between two awaits can't be seen half-updated by another task. It's also why a coroutine that never awaits (a long computation, a blocking call) freezes the whole program.

### 2.2 Blocking the loop

[`examples/01_event_loop.py`](examples/01_event_loop.py) runs three one-second waits five ways:

```text
3 x asyncio.sleep(1), gathered                    1.00s  [0, 1, 2]
3 x time.sleep(1) in coroutines, gathered         3.01s  [0, 1, 2]
3 x sync psycopg pg_sleep(1) in coroutines        3.05s  [0, 1, 2]
3 x sync psycopg via asyncio.to_thread            1.03s  [0, 1, 2]
3 x async psycopg pg_sleep(1), gathered           1.02s  [0, 1, 2]
```

`asyncio.sleep()` waits *in the loop*, so the three waits overlap. `time.sleep()` blocks the thread that runs the loop: the three coroutines run one after another, and so does every other request in the process. The sync psycopg driver does the same thing: wrapping a blocking call in `async def` changes nothing. Note that the program still produces the right result, only slowly, so this mistake survives tests and shows up as mysterious latency under load.

There are two fixes: use an async driver (the last line), or push the blocking call to a worker thread with `await asyncio.to_thread(fn, *args)`. The thread version is the escape hatch for libraries that have no async API. It costs a thread per concurrent call, so it doesn't scale like real async I/O.

> **Pitfall:** run development with `PYTHONASYNCIODEBUG=1` (or `asyncio.run(main(), debug=True)`). In debug mode the loop logs every callback that runs longer than 100 ms ("Executing <Task ...> took 1.002 seconds"), which points straight at blocking calls.

### 2.3 Running things concurrently: tasks, `gather`, and `TaskGroup`

Awaiting coroutines one after another runs them sequentially. To run them concurrently, wrap them in **tasks**: a task is a coroutine scheduled on the loop, which runs whenever the current coroutine waits.

```python
t1 = asyncio.create_task(stock(pool_conn_a, 16))
t2 = asyncio.create_task(stock(pool_conn_b, 47))
a, b = await t1, await t2           # both were running while we waited for t1
```

Two higher-level tools collect several tasks, and they differ in how they handle failure. [`examples/09_gather_vs_taskgroup.py`](examples/09_gather_vs_taskgroup.py) runs three queries on a pool: two take one second, one fails immediately with a division by zero.

```text
gather():
   0.00s broken: finished
   0.00s gather raised DivisionByZeroError
   1.01s slow_b: finished
   1.01s slow_a: finished
gather(return_exceptions=True):
   0.00s broken: finished
   1.00s slow_a: finished
   1.00s slow_b: finished
   1.00s results: ['a', DivisionByZeroError('division by zero'), 'b']
TaskGroup:
   0.00s broken: finished
   0.01s slow_a: cancelled
   0.01s slow_a: finished
   0.01s slow_b: cancelled
   0.01s slow_b: finished
   0.01s TaskGroup raised ExceptionGroup('unhandled errors in a TaskGroup', [DivisionByZeroError('division by zero')])
```

- **`asyncio.gather(*coros)`** raises the first exception immediately, but **the other tasks keep running**, unobserved: their queries complete a second later, holding pool connections, and their results or errors go nowhere. That's a resource leak waiting to happen.
- **`gather(..., return_exceptions=True)`** waits for everything and returns exceptions as values in the result list. Good when you want partial results and will inspect each one.
- **`asyncio.TaskGroup`** (Python 3.11+) is *structured concurrency*: when one task fails, the group **cancels the others**, waits for them to finish cancelling, and raises an `ExceptionGroup` with all failures (caught with `except*`). When the `async with` block exits, no task started in it is still running. Prefer it by default.

### 2.4 Bounding concurrency

Starting 10,000 tasks is cheap; having them all hit the database at once is not. Two mechanisms limit how much runs at the same time:

- **The pool.** With `max_size=10`, at most 10 tasks hold a connection, and the rest wait in `pool.connection()`/`pool.acquire()`. That protects the database, but tasks still wait with a timeout (a `PoolTimeout` after 30 s by default), and while waiting they may hold other resources.
- **`asyncio.Semaphore(n)`**: `async with sem:` lets at most `n` tasks into a block. Use it to limit work *before* it competes for connections, or to limit something that isn't the database (calls to a payment provider, say):

```python
sem = asyncio.Semaphore(20)

async def refresh_price(pool, product_id):
    async with sem:                                   # at most 20 in flight
        price = await fetch_price_from_supplier(product_id)    # slow HTTP call, no connection held
    async with pool.connection() as conn:             # borrow a connection only to write
        await conn.execute("UPDATE products SET price = %s WHERE id = %s", (price, product_id))
```

Notice the order: the slow external call happens **without** holding a database connection, and the connection is borrowed only for the short write. Holding a pooled connection across an unrelated `await` is the async version of Module 22's "idle in transaction" bug, and it is easier to write by accident.

### 2.5 Timeouts

`async with asyncio.timeout(seconds):` (Python 3.11+) cancels the block if it hasn't finished in time and raises `TimeoutError`:

```python
try:
    async with asyncio.timeout(2.0):
        rows = await conn.fetch(slow_report_sql)
except TimeoutError:
    rows = None   # show the page without the report
```

`asyncio.wait_for(coro, timeout)` does the same for a single awaitable. A timeout is implemented with **cancellation**: when the deadline passes, the loop cancels the task, a `CancelledError` is raised at the `await` that was waiting, and the timeout context manager turns it into `TimeoutError` on the way out.

### 2.6 Cancellation, precisely

Cancellation is how asyncio stops work: `task.cancel()` (or a timeout, or a `TaskGroup` cleaning up) makes the next `await` in that task raise `asyncio.CancelledError`. Rules for code that holds database resources:

- **Let `CancelledError` propagate.** It inherits from `BaseException`, not `Exception`, precisely so that `except Exception:` doesn't swallow it. If you catch it to clean up, re-raise it.
- **Clean up in `finally` or `async with`.** Pool context managers (`async with pool.connection()`) return the connection even when the block is cancelled.
- **`asyncio.shield(aw)`** protects an inner operation from the cancellation of the outer task. It's useful for "commit even if the request was cancelled" and dangerous elsewhere, because the shielded work keeps running unobserved.
- What cancellation does to **the query on the server** depends on the driver (next section).

---

## 3. psycopg async

### 3.1 The same API with `await`

psycopg's async classes mirror the sync ones method for method. Everything from Module 22 (parameters, `psycopg.sql`, row factories, adapters, `transaction()` blocks, COPY, pipeline mode, notifications) carries over, with `await` on anything that talks to the server and `async with`/`async for` instead of `with`/`for`:

```python
import psycopg
from psycopg.rows import class_row

async def get_product(conn: psycopg.AsyncConnection, product_id: int) -> Product | None:
    async with conn.cursor(row_factory=class_row(Product)) as cur:
        await cur.execute(GET_PRODUCT, {"id": product_id})
        return await cur.fetchone()

async def main():
    async with await psycopg.AsyncConnection.connect(DSN) as conn:   # note: "async with await"
        p = await get_product(conn, 20)
        async with conn.transaction():
            await conn.execute("UPDATE ...")
        async for row in await conn.execute("SELECT ..."):
            ...
```

`AsyncConnection.connect()` is a coroutine, so you `await` it, and the connection it returns is an async context manager, hence `async with await`. The context manager's semantics are the same as in Module 22: commit on success, rollback on exception, close.

That symmetry is what makes the Bazaar async repository, [`bazaar/aio/repo.py`](../../project/python/src/bazaar/aio/repo.py), almost a copy of the sync one: it imports the same SQL constants, the same query builders, the same models, and the same error translation from `bazaar.repo`, and only adds `await`.

### 3.2 `AsyncConnectionPool`

```python
from psycopg_pool import AsyncConnectionPool

pool = AsyncConnectionPool(DSN, min_size=2, max_size=10, open=False, configure=configure)
await pool.open(wait=True, timeout=10)      # at startup: fail fast if the database is down

async with pool.connection() as conn:       # per unit of work
    ...

await pool.close()                          # at shutdown
```

All the parameters of `ConnectionPool` from Module 22 exist. `configure`, `reset` and `check` must be **coroutine functions** here. Two asyncio-specific rules:

- **Open the pool inside the running loop**, typically in the application's startup hook (FastAPI's `lifespan` in Module 28), never at import time. The pool runs background tasks on the loop that opened it. That's also why the constructor's `open=True` is deprecated for async pools.
- **One pool per process and per event loop.** A pool (and every connection) belongs to the loop it was created on. Using it from another loop, such as a second `asyncio.run()` or a thread with its own loop, fails in confusing ways.

`bazaar.aio.db.create_pool()` wraps all of this with the same settings as the sync version.

### 3.3 Notifications and COPY

`conn.notifies()` is an async generator. Like the sync version (Module 22, section 13), it accepts `timeout=` and `stop_after=`, and notifications that arrive while you aren't iterating are kept for the next call:

```python
async for n in conn.notifies(timeout=0.2):
    handle(n)
```

> **Pitfall:** don't wrap `await anext(conn.notifies())` in `asyncio.timeout()` to poll with a deadline. The timeout cancels the generator in the middle of its `await`, which **closes** the generator, and the next `anext()` raises `StopAsyncIteration`. Use the `timeout=` argument, which ends the generator cleanly, and loop around it (section 7 shows the pattern).

COPY works as before, with `await` on writes: `async with cur.copy(...) as copy: await copy.write_row(row)`. Reading: `async for row in copy.rows():`.

### 3.4 What cancellation does to a running query

When the task waiting for a query's result is cancelled, the query is still running in the server's backend process. The client can't simply walk away: the connection would still receive the rest of the result, and the next query on that connection would read it. PostgreSQL offers a **cancel request** for this: a separate, short-lived connection that asks the postmaster to interrupt the backend's current query (the same thing psql does on Ctrl-C, and `pg_cancel_backend()` does from SQL). [`examples/02_cancellation.py`](examples/02_cancellation.py) cancels a `pg_sleep(10)` after 0.5 s and asks `pg_stat_activity` what the server is doing:

```text
psycopg AsyncConnection (autocommit):
   TimeoutError after 0.51s
   right after the timeout:     server says: ('idle', 'SELECT pg_sleep(10)')
   connection status: IDLE | closed: False
   still usable
asyncpg:
   TimeoutError after 0.50s
   right after the timeout:     server says: ('active', 'SELECT pg_sleep(10)')
   50 ms later:                 server says: ('idle', 'SELECT pg_sleep(10)')
   is_closed: False
   still usable (0.9 ms)
statement_timeout instead (asyncpg, inside a transaction):
   QueryCanceledError after 0.50s: canceling statement due to statement timeout
   still usable
```

Both drivers do the right thing: they send a cancel request, discard the rest of the result, and leave the connection usable. psycopg waits until the cancellation has completed before raising, so the server is already idle when your code continues. asyncpg raises immediately and finishes the cleanup in the background; the next operation on that connection waits for it (0.9 ms here).

Inside a transaction, a cancelled statement **aborts the transaction**, exactly like any other error. The enclosing `async with conn.transaction()` block then rolls back as the `CancelledError` passes through it.

Client-side and server-side timeouts complement each other:

| | `asyncio.timeout()` | `statement_timeout` (server) |
|---|---|---|
| Measures | everything in the block: pool wait, network, server time, Python | one statement's execution on the server |
| Stops | the client task; the driver then cancels the query | the query itself, with `QueryCanceledError` (57014) |
| Survives a hung network | yes | no: the server may cancel, but the client keeps waiting for an answer that doesn't arrive |
| Set | per call in code | per role/database (Module 17), per connection (`options`), per transaction (`SET LOCAL`) |

Use `statement_timeout` as the database's safety net (the Bazaar settings default to 5 s), and `asyncio.timeout()` for the request-level deadline that your users actually experience.

---

## 4. asyncpg

### 4.1 A different design

asyncpg, created at MagicStack (the company behind uvloop), is not a wrapper around libpq. It implements the PostgreSQL protocol itself, in Cython, specifically for asyncio:

- **Binary format everywhere.** Parameters and results always use PostgreSQL's binary representation, decoded by C code straight into Python objects. There is no text parsing of numbers and timestamps.
- **Everything is a prepared statement.** Every query is parsed with a named `Parse`, then cached per connection and reused (4.5). In exchange for that, it needs the server to describe parameter types before it can encode them, and it relies on statement names surviving between calls (a problem behind PgBouncer, 4.8).
- **No libpq.** No dependency on the system's C library, but also no libpq features: no `PQtrace`, no `service` files. It does support the usual `PG*` environment variables, `.pgpass`, `sslmode`, multiple hosts and `target_session_attrs`, through its own implementation. Some libpq-only connection parameters aren't understood, and a libpq `key=value` connection string isn't accepted (pass a `postgresql://` URI or keyword arguments).
- **Its own API**, closer to the protocol than to the DB-API: no cursors-as-result-holders, no `%s`, no implicit transactions.

### 4.2 The API

[`examples/04_asyncpg_tour.py`](examples/04_asyncpg_tour.py) walks through it on Bazaar:

```python
conn = await asyncpg.connect(DSN, server_settings={"application_name": "m23-tour"})

rows = await conn.fetch("SELECT id, shop_name FROM sellers WHERE id = ANY($1) ORDER BY id", [1, 2])
row = await conn.fetchrow("SELECT id, title, price, status, tags FROM products WHERE id = $1", 20)
n = await conn.fetchval("SELECT count(*) FROM orders WHERE user_id = $1", 8)
status = await conn.execute("UPDATE inventory SET quantity = quantity WHERE product_id = $1", 16)
```

```text
[<Record id=1 shop_name='Happy Outlet 1'>, <Record id=2 shop_name='Little Workshop 2'>]
Pure Dew Vegan Perfume Oil 24.99 active ['beauty', 'fragrance', 'gift']
98
'UPDATE 1'
```

| Method | Returns |
|---|---|
| `fetch(sql, *args)` | `list[Record]` (the whole result) |
| `fetchrow(sql, *args)` | first `Record` or `None` |
| `fetchval(sql, *args, column=0)` | one value of the first row, or `None` |
| `execute(sql, *args)` | the command status string (`'UPDATE 1'`). Without arguments it uses the simple protocol and may contain several statements. |
| `executemany(sql, args_list)` | runs the statement for each argument tuple, pipelined |
| `copy_records_to_table`, `copy_to_table`, `copy_from_query`, ... | COPY (4.6) |
| `cursor(sql, *args)` | a server-side cursor for async iteration (4.6) |

Parameters are **positional `$1`, `$2`**, the server's own syntax, passed as separate arguments (not a tuple). There are no named parameters; a helper that builds `$n` from a dict is a few lines if you want them. Since the SQL text is sent unchanged, `%` needs no escaping.

A **`Record`** is an immutable row that supports both index and key access (`row[2]`, `row["title"]`, `row.get("status")`), iteration over values, and `dict(row)`. There are no row factories. Convert explicitly (`Product(**row)`) where you want your own types; `bazaar.worker` does `Job(**dict(row))`.

### 4.3 Types: strict on the way in, binary on the way out

Because asyncpg encodes parameters in binary itself, it must know each parameter's type before sending. It gets the types from the server's answer to `Parse`, and then encodes your Python value as that type. That's strict in some places and surprisingly lax in others:

```text
'8'            -> DataError: invalid input for query argument $1: '8' ('str' object cannot be interpreted as an integer)
8.0            -> 98
8.5            -> 98
Decimal('8')   -> 98
```

A string is rejected for a `bigint` parameter (psycopg would send it as text and let the server convert it). Worse, a float `8.5` was silently truncated to user 8. Validate and convert input at your API boundary (pydantic in Module 28) rather than relying on the driver.

Results map much like psycopg's (`numeric` → `Decimal`, `timestamptz` → aware `datetime` in UTC, arrays → lists, `uuid` → `UUID`, ranges → `asyncpg.Range`), with two notable exceptions:

- **`json` and `jsonb` arrive as `str`**, and must be passed as `str`. You decide the decoding with a **type codec**:

  ```python
  await conn.set_type_codec("jsonb", schema="pg_catalog",
                            encoder=json.dumps, decoder=json.loads, format="text")
  ```

  ```text
  '{"vegan": true, "skin_type": "sensitive", "volume_ml": 15}'      <- before
  {'vegan': True, 'skin_type': 'sensitive', 'volume_ml': 15}        <- after
  dict in, jsonb out
  ```

  Codecs are per connection, so with a pool you install them in the pool's `init` hook (4.7). Faster JSON libraries (`orjson`) plug in the same way.
- **Enums arrive as `str`.** There is no enum registration; wrap them yourself if you want Python enums.

`set_type_codec` also handles your own types (a composite as a Python class, a domain with special encoding), and `set_builtin_type_codec` can reuse a built-in codec for an extension type.

### 4.4 Transactions

asyncpg has **no implicit transactions**: every statement outside a transaction block runs in autocommit mode. Transactions are explicit:

```python
async with conn.transaction():                               # BEGIN ... COMMIT / ROLLBACK
    await conn.execute("UPDATE inventory SET quantity = quantity + 1 WHERE product_id = 16")
    try:
        async with conn.transaction():                       # nested: SAVEPOINT
            await conn.execute("UPDATE inventory SET quantity = -1 WHERE product_id = 16")
    except asyncpg.CheckViolationError as e:
        print("inner block rolled back:", e.constraint_name, e.sqlstate)
    await conn.execute("UPDATE inventory SET quantity = quantity - 1 WHERE product_id = 16")
```

```text
inner block rolled back: inventory_quantity_nonnegative 23514
```

`conn.transaction(isolation="serializable", readonly=True, deferrable=True)` sets the transaction's characteristics. Exceptions are classes per SQLSTATE (`asyncpg.UniqueViolationError`, `asyncpg.SerializationError`, ...) with `.sqlstate`, `.detail`, `.hint`, `.constraint_name`, `.table_name` and friends. That's why `bazaar.errors.translate()` works for both drivers. Custom SQLSTATEs like `BZ001` arrive as a generic `asyncpg.PostgresError` with `sqlstate = 'BZ001'`.

### 4.5 Prepared statements and the statement cache

Every query asyncpg runs is a named prepared statement. You can prepare explicitly and keep the handle:

```python
stmt = await conn.prepare("SELECT title, price FROM products WHERE seller_id = $1 ORDER BY id LIMIT $2")
print([a.name + ":" + a.type.name for a in stmt.get_attributes()], [t.name for t in stmt.get_parameters()])
print(await stmt.fetchrow(42, 1))
```

```text
['title:text', 'price:numeric'] ['int8', 'int8']
<Record title='Saltmarsh Organic Pullover Hoodie - Beige' price=Decimal('63.99')>
```

More often you don't: `conn.fetch(sql, ...)` looks the SQL text up in the connection's **statement cache** (an LRU of `statement_cache_size=100` entries by default, each kept for at most `max_cached_statement_lifetime=300` seconds) and reuses the prepared statement. After a handful of queries, the server's view of this connection shows the cached statements:

```text
4 statements prepared on this connection, e.g. __asyncpg_stmt_9__
```

So asyncpg gets Module 22's "prepare after 5 executions" benefit from the first execution. The flip side is that a cached statement depends on the schema it was planned against. When a table changes (a migration adds a column, a type is altered), the cached statement's result shape is stale:

```text
[<Record id=1 note='x'>]
[<Record id=1 note='x' extra=None>] <- outside a transaction: re-prepared silently
inside a transaction: InvalidCachedStatementError: cached statement plan is invalid due to a database schema or configuration change
```

Outside a transaction, asyncpg catches the server's error, re-prepares and retries transparently. Inside a transaction it can't, because the error has already aborted the transaction, so it raises `InvalidCachedStatementError`, a subclass of the errors you retry by re-running the whole transaction. Expect a burst of these right after a deploy that runs migrations against live traffic (Module 26), and include them in your retry logic (Module 27). `await conn.reload_schema_state()` clears the caches on demand.

### 4.6 COPY and cursors

```python
n = await conn.copy_records_to_table(
    "price_import", records=[(f"SKU-{i}", Decimal(i) / 4) for i in range(10_000)]
)
```

```text
COPY 10000
```

`copy_records_to_table(table, records=iterable_of_tuples, columns=[...])` streams Python tuples in binary COPY format. It's the fastest way into Postgres from asyncpg. There are also `copy_to_table` (from a file or async iterable of bytes), `copy_from_table`, and `copy_from_query` (to a file or a callback) for exports.

For results too large to `fetch()`, asyncpg has server-side cursors, which must run inside a transaction:

```python
async with conn.transaction():
    async for r in conn.cursor("SELECT quantity FROM order_items WHERE order_id < $1", 1000, prefetch=500):
        total += r["quantity"]
```

```text
units in the first 999 orders: 2434
```

`prefetch` is the number of rows fetched per round trip (Module 22's `itersize`).

### 4.7 Pools: `init`, `setup` and `reset`

```python
pool = await asyncpg.create_pool(
    DSN,
    min_size=2, max_size=10,
    init=init_connection,                  # once per new connection: type codecs, session settings
    setup=None,                            # every time a connection is acquired
    server_settings={"application_name": "bazaar", "statement_timeout": "5000"},
    max_inactive_connection_lifetime=300,  # close connections idle for 5 minutes
    timeout=5,                             # connect timeout
)

async with pool.acquire(timeout=2) as conn:   # wait at most 2 s for a free connection
    rows = await conn.fetch(...)

n = await pool.fetchval("SELECT count(*) FROM jobs")   # shortcut: acquire, run, release

await pool.close()
```

- **`init(conn)`** runs once when a connection is created. It's the place for `set_type_codec` (codecs are per connection), as in `bazaar.aio.db.init_asyncpg_connection`.
- **`setup(conn)`** runs on every `acquire()`, before your code gets the connection. It's rarely needed.
- **`reset(conn)`** (asyncpg 0.30+) runs when a connection is released. By default asyncpg already resets session state on release with `SELECT pg_advisory_unlock_all(); CLOSE ALL; UNLISTEN *; RESET ALL;`, keeping its prepared statements. So an asyncpg pool cleans up a leaked `SET app.seller_id` from Module 22's example by itself. Within one `acquire()` block, though, a session-level setting is visible to everything that follows, so the rule from Module 22 still stands: set request context with `set_config(..., true)` inside a transaction.
- `server_settings` are sent in the startup packet, like libpq's `options`.

A released connection that is still inside a transaction is rolled back. One that is broken is discarded and replaced.

### 4.8 asyncpg, psycopg and PgBouncer

Module 17 explained the problem: in **transaction** pooling mode, consecutive transactions of one client may run on different server backends, and a prepared statement exists only on the backend where it was prepared. asyncpg prepares *everything*, so it's the driver most exposed to this. The lab in [`lab/`](lab/) starts PgBouncer 1.26 in transaction mode with only **2** server connections for 20 clients:

```bash
cd modules/23-async-python/lab
./run_pgbouncer.sh 0      # max_prepared_statements = 0: PgBouncer does not track prepared statements
```

[`examples/08_pgbouncer_prepared.py`](examples/08_pgbouncer_prepared.py) then runs 20 clients doing 50 parameterized queries each through `localhost:6432`:

```text
asyncpg, default statement cache               failed queries:  491 / 1000
        32 x InvalidSQLStatementNameError: prepared statement "__asyncpg_stmt_9__" does not exist
        29 x InvalidSQLStatementNameError: prepared statement "__asyncpg_stmt_2__" does not exist
        29 x InvalidSQLStatementNameError: prepared statement "__asyncpg_stmt_3__" does not exist
asyncpg, statement_cache_size=0                failed queries:    0 / 1000
psycopg, prepare_threshold=5 (default)         failed queries:  178 / 1000
        27 x InvalidSqlStatementName: prepared statement "_pg3_10" does not exist
        23 x InvalidSqlStatementName: prepared statement "_pg3_11" does not exist
        20 x InvalidSqlStatementName: prepared statement "_pg3_9" does not exist
psycopg, prepare_threshold=None                failed queries:    0 / 1000
```

Half of asyncpg's queries failed, and psycopg failed too, as soon as its statements crossed the 5-execution threshold. Disabling prepared statements in the driver fixes it (`statement_cache_size=0` for asyncpg; asyncpg then uses unnamed statements, re-parsed every time). Now restart PgBouncer with statement tracking and run the same script:

```bash
./run_pgbouncer.sh 200    # max_prepared_statements = 200
```

```text
asyncpg, default statement cache               failed queries:    0 / 1000
asyncpg, statement_cache_size=0                failed queries:    0 / 1000
psycopg, prepare_threshold=5 (default)         failed queries:    0 / 1000
psycopg, prepare_threshold=None                failed queries:    0 / 1000
```

With `max_prepared_statements` set, PgBouncer (1.21+) intercepts each client's `Parse` messages, remembers them, and prepares them on whatever backend it assigns, under names of its own. Both drivers keep their prepared-statement speed. The rule:

- PgBouncer **1.21 or newer with `max_prepared_statements > 0`** (the default is 200 since 1.24): leave the driver defaults alone.
- Older PgBouncer, or other poolers without tracking: `statement_cache_size=0` for asyncpg, `prepare_threshold=None` for psycopg.
- Explicit `conn.prepare()` in asyncpg keeps the statement across `await`s in your code, which can span transactions. Behind a transaction-mode pooler, use it only inside one transaction.

Remove the lab container when done: `docker rm -f m23-pgbouncer`.

### 4.9 psycopg async or asyncpg?

| | psycopg 3 async | asyncpg |
|---|---|---|
| API | DB-API style, identical to sync psycopg; `%s` / `%(name)s` | its own; `$1` only |
| Sync and async in one code base | yes, same driver, same adapters, same SQL | async only |
| Implementation | libpq + C/Cython speedups | own protocol implementation in Cython |
| Raw speed, small queries | good | best (2x in section 9's benchmark) |
| Type handling | rich adapters, row factories, enum/composite registration, text or binary | binary, strict input checks, JSON as `str` until you add a codec |
| Prepared statements | after 5 executions | always (statement cache) |
| Pipeline mode / `executemany` | yes (sync is fast; async `executemany` was slow here, section 9) | `executemany` pipelined and fast |
| Used by SQLAlchemy async | yes (`postgresql+psycopg`) | yes (`postgresql+asyncpg`), the most common choice |

Both are production-grade. The Bazaar package uses **psycopg async** for its repository (one set of SQL and adapters for sync and async code) and **asyncpg** for the job worker, whose hot loop is many tiny queries. That's partly to show both in practice. A single project would usually pick one.

---

## 5. Concurrency correctness

### 5.1 One connection, one query at a time

The protocol is strictly sequential per connection: a client sends a query and must read the whole response before the next query's response can be told apart. Async code makes it easy to break that by accident, by sharing one connection among concurrent tasks. [`examples/03_one_connection.py`](examples/03_one_connection.py):

```text
asyncpg, shared connection: InterfaceError: cannot perform operation: another operation is in progress
psycopg, shared connection: 4 x 0.5 s took 2.01s
psycopg, pool of 4:         4 x 0.5 s took 0.53s
psycopg, pool of 4:         8 x 0.5 s took 1.01s
```

- **asyncpg refuses**: a second operation on a busy connection raises `InterfaceError`.
- **psycopg serializes**: an `AsyncConnection` has an internal lock, so concurrent tasks take turns, without errors but also without concurrency. Four half-second queries took two seconds.
- **A pool** gives each task its own connection, so the waits overlap. With 8 tasks and 4 connections, the second four waited for the first.

There is a worse failure than these two: **transactions**. If two tasks share a psycopg connection, their statements interleave *inside the same transaction*. Task A's `ROLLBACK` undoes task B's work, and B's error aborts A's transaction. Nothing raises an error that points at the cause. The rule is absolute: **a connection (and its transaction) belongs to one task at a time.** Pass connections down explicitly, as the repository functions do, and never store "the current connection" in a module-level variable.

### 5.2 Pools, processes and event loops

- **One pool per process**, created at startup inside the running loop and closed at shutdown. Not one per request, not one per module that happens to need the database.
- **Never share a pool or connection across `fork()`.** With gunicorn or uvicorn workers, each worker process creates its own pool after starting.
- **Never share across event loops**, which includes calling `asyncio.run()` repeatedly in tests with a module-level pool. pytest-asyncio gives each test function its own loop by default, so the Bazaar tests create pools inside each test (or in function-scoped fixtures).
- **Size the pool for the database, not for the number of tasks.** 1,000 concurrent requests and a pool of 10 is a fine combination as long as each request holds its connection briefly (Little's law from Module 22). The total across processes must still fit in `max_connections`.

### 5.3 Transactions across `await`

Every `await` inside a transaction is a point where your task may be suspended for an arbitrary time while the transaction holds locks and its snapshot. That's fine for `await conn.execute(...)`: the database is what we're waiting for. It's a bug for `await http_client.post(...)` or `await asyncio.sleep(...)`. Structure code so that slow, non-database awaits happen before or after the transaction:

```python
quote = await shipping_api.quote(cart)                      # outside: no connection, no transaction
async with pool.connection() as conn, conn.transaction():
    order_ids = await repo.place_order(conn, user_id, items, address_id)
    await repo.enqueue_job(conn, "order_confirmation", {"order_id": order_ids[0]})
await notify_frontend(order_ids)                             # outside again
```

The job is enqueued **in the same transaction** as the order, so it exists if and only if the order does. Enqueuing after the commit could lose it in a crash, and before it could send a confirmation for an order that was rolled back. That's the core of the transactional outbox pattern of Module 27, and the reason the job queue lives in Postgres.

---

## 6. Fan-out: independent queries at the same time

A product page needs several queries that don't depend on each other. Sequentially, the page's database time is their **sum**; run concurrently, each on its own pooled connection, it approaches the **slowest one**. [`examples/05_fanout.py`](examples/05_fanout.py) builds the page for product 2 (the most ordered and most reviewed product) with five queries on an asyncpg pool:

```python
async def run_query(pool, name):
    sql, method = QUERIES[name]
    async with pool.acquire() as conn:
        return name, await getattr(conn, method)(sql, PRODUCT_ID)

async def concurrent(pool):
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(run_query(pool, name)) for name in QUERIES]
    return dict(t.result() for t in tasks)
```

```text
sequential    6.7 ms per page
concurrent    5.0 ms per page
  product            0.4 ms  <Record id=2 title='The Burning Protocol' price=Decimal('22.99') seller_id=5>
  stock              0.3 ms  12
  rating             0.5 ms  <Record round=Decimal('4.29') count=167>
  recent_reviews     0.4 ms  [<Record rating=5 title='Excellent!'>, <Record rating=4 title='Very good'>, <Record rating=5 title='Excellent!'>]
  bought_together    2.8 ms  [<Record product_id=5 n=7>, <Record product_id=40 n=5>, <Record product_id=3 n=4>]
```

The gain is modest here, and the per-query timings show why: the queries add up to about 4.4 ms, the slowest is 2.8 ms, and each of the others costs about one 0.2 ms round trip, so there's little to overlap. (Over three runs, the concurrent version took 4.9–6.2 ms; on a busy 4-core machine that also runs the database, the difference sometimes disappeared.) Fan-out pays off when:

- the network round trip is longer (a database in another availability zone at 1 ms, or another region at 30 ms, makes every sequential query pay that again);
- several queries are individually slow, like the aggregates of a dashboard (exercise 4 measures one);
- the calls go to different systems: a database, a search service, a recommendation API.

It costs one connection per concurrent query for the duration of the page, so a page with five concurrent queries uses five times as much pool capacity, briefly. Under heavy load that can make things worse. Combine fan-out with a request-level deadline, so a slow part degrades the page instead of failing it:

```python
async with asyncio.timeout(0.5):
    async with asyncio.TaskGroup() as tg:
        ...
```

with partial results (`gather(..., return_exceptions=True)` inside the timeout, or a per-query `timeout`) where a missing "bought together" box is better than an error page.

> **In production:** concurrent queries on different connections see **different snapshots**. A page built from five concurrent queries isn't a consistent view of one moment, which is fine for a product page and wrong for an invoice. When consistency matters, run the queries sequentially on one connection in one `REPEATABLE READ` transaction, or export a snapshot (`pg_export_snapshot()` and `SET TRANSACTION SNAPSHOT`) to share it across connections.

---

## 7. LISTEN/NOTIFY, asynchronously

Module 22 showed `LISTEN`/`NOTIFY` with a blocking loop. In async code a listener is just another task, so it fits naturally into a service. The SQL file [`project/python/sql/2301_notify.sql`](../../project/python/sql/2301_notify.sql) adds two triggers to Bazaar:

```sql
-- every new order: a row trigger, payload = ids as JSON
CREATE OR REPLACE FUNCTION notify_new_order()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_notify('new_order', json_build_object(
        'order_id',  NEW.id,
        'seller_id', NEW.seller_id,
        'user_id',   NEW.user_id)::text);
    RETURN NULL;
END;
$$;

CREATE OR REPLACE TRIGGER orders_notify_new
    AFTER INSERT ON orders
    FOR EACH ROW EXECUTE FUNCTION notify_new_order();

-- new jobs: a statement trigger, one notification per queue per INSERT statement
CREATE OR REPLACE FUNCTION notify_jobs_ready()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_notify('jobs_ready', q.queue)
    FROM (SELECT DISTINCT queue FROM new_jobs) AS q;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE TRIGGER jobs_notify_ready
    AFTER INSERT ON jobs
    REFERENCING NEW TABLE AS new_jobs
    FOR EACH STATEMENT EXECUTE FUNCTION notify_jobs_ready();
```

The `new_order` payload carries ids, not the order total, for a precise reason: the row trigger fires when `place_order()` inserts the order row, *before* it inserts the items, so `total` is still 0 at that moment. A notification is a wake-up call; consumers read the current data from the tables. The `jobs_ready` trigger is statement-level with a transition table, so inserting 1,000 jobs in one statement sends one notification, not 1,000.

[`examples/06_order_notifier.py`](examples/06_order_notifier.py) runs two listeners, one per driver, while a third task places orders through `bazaar.aio.repo`:

```python
async def asyncpg_listener(stop):
    conn = await asyncpg.connect(DSN)
    def on_new_order(conn, pid, channel, payload):      # a plain callback, not a coroutine
        print(f"{stamp()} asyncpg  got {channel}: {json.loads(payload)}")
    await conn.add_listener("new_order", on_new_order)
    await stop.wait()
    await conn.close()

async def psycopg_listener(stop):
    conn = await psycopg.AsyncConnection.connect(DSN, autocommit=True)
    await conn.execute("LISTEN new_order")
    while not stop.is_set():
        async for n in conn.notifies(timeout=0.2):      # ends every 0.2 s so `stop` is checked
            print(f"{stamp()} psycopg  got {n.channel}: {json.loads(n.payload)}")
    await conn.close()
```

```text
 0.33s shop     placed 500034 (not yet committed)
 0.84s psycopg  got new_order: {'order_id': 500034, 'seller_id': 5, 'user_id': 8}
 0.84s asyncpg  got new_order: {'order_id': 500034, 'seller_id': 5, 'user_id': 8}
 0.84s shop     committed
 0.85s shop     placed and committed [500035, 500036]
 0.85s asyncpg  got new_order: {'order_id': 500035, 'seller_id': 5, 'user_id': 13}
 0.85s asyncpg  got new_order: {'order_id': 500036, 'seller_id': 5, 'user_id': 21}
 0.85s psycopg  got new_order: {'order_id': 500035, 'seller_id': 5, 'user_id': 13}
 0.85s psycopg  got new_order: {'order_id': 500036, 'seller_id': 5, 'user_id': 21}
```

The first order was placed at 0.33 s, but both listeners got it only at 0.84 s, when the shop task committed. The script removes its orders afterwards.

> **Pitfall:** to hold the first order uncommitted, the shop task opens `conn.transaction()` *before* calling `repo.place_order()`. Without it, the repository's own `transaction()` block, running on an idle pooled connection, would be the outer transaction and commit immediately: the same trap as in Module 22, section 5.2. The first version of this example fell into it, and the notification arrived before the "not yet committed" line.

The two drivers' listener APIs differ in style:

- **asyncpg** calls a **callback** for each notification, on the event loop. The callback must be quick and non-blocking. To do async work, start a task from it, or put the notification on an `asyncio.Queue` that a worker task consumes. The connection must stay open and must not be returned to a pool: `pool.acquire()` with a listener is wrong, because the pool's `reset` removes listeners.
- **psycopg** gives you an **async generator**, which reads naturally in a loop and applies backpressure by itself: notifications pile up in the connection while you are busy and are delivered when you next iterate.

Both need a **dedicated connection** that isn't behind a transaction-mode PgBouncer (Module 17), and both need a plan for reconnection: notifications sent while the listener is disconnected are gone, so after reconnecting, re-read the state from the tables. The job worker below does exactly that by polling.

---

## 8. An async job worker

Module 10 built a job queue in plain Postgres: a `jobs` table, `dequeue_jobs()` with `FOR UPDATE SKIP LOCKED` and leases, `complete_job()`, and `fail_job()` with exponential backoff. This module's milestone puts a worker in front of it: [`bazaar/worker.py`](../../project/python/src/bazaar/worker.py), run as `python -m bazaar.worker`.

### 8.1 Design

```text
                         ┌─────────────── one process, one event loop ────────────────┐
  INSERT INTO jobs ──▶   │  listener connection: LISTEN jobs_ready ──▶ wake-up events │
  (trigger: NOTIFY)      │                                                             │
                         │  loop 0: claim ─▶ handler ─▶ complete/fail ─▶ claim ...     │
                         │  loop 1: claim ─▶ handler ─▶ ...           (TaskGroup of N) │
                         │  ...                                                        │
                         │  asyncpg pool (N + 1 connections)                           │
                         └─────────────────────────────────────────────────────────────┘
```

- **N loops in a `TaskGroup`.** Each loop repeatedly claims **one** job with `SELECT ... FROM dequeue_jobs($1, $2, 1, $3)`, runs the handler registered for `job.kind`, and calls `complete_job()` or `fail_job()`. `SKIP LOCKED` guarantees that concurrent loops, and other worker processes, never claim the same job.
- **Short claims, leases instead of open transactions.** The claim is a single autocommitted statement; no transaction stays open while a handler runs (which may take seconds and may call other services). The lease (`locked_at`, `p_lease` = handler timeout + 30 s) protects the job instead. If the process dies, the lease expires and another worker takes the job. Delivery is therefore **at least once**, and handlers must be idempotent (exercise 10).
- **Wake-ups.** When the queue is empty, a loop sleeps until a `jobs_ready` notification for its queue arrives *or* `poll_interval` passes. The notification makes the worker react in milliseconds; polling is the safety net for notifications lost while the listener was disconnected, and for jobs scheduled in the future (`run_at`, retries with backoff), which never trigger a notification when they become due. Each loop has its own `asyncio.Event`, cleared **before** it looks for work, so a notification that arrives between "found nothing" and "go to sleep" isn't lost.
- **Errors.** Any `Exception` from a handler, including an `asyncio.timeout(job_timeout)` expiry and an unknown job kind, becomes `fail_job(id, repr(e))`. `CancelledError` is deliberately *not* caught (2.6): a cancelled job stays leased and is retried later.
- **Graceful shutdown.** `SIGINT`/`SIGTERM` call `worker.stop()`: loops finish the job in hand and stop claiming. If that takes longer than `--grace` seconds (or on a second Ctrl-C), the run task is cancelled, and in-flight jobs are retried after their lease expires.

The core of a loop:

```python
async def _loop(self, n: int) -> None:
    worker_id = f"{self.name}/{n}"
    wakeup = self._wakeups[n]
    while not self._stopping:
        wakeup.clear()                       # clear BEFORE looking, so a NOTIFY that arrives meanwhile counts
        job = await self._claim(worker_id)
        if job is None:
            try:
                await asyncio.wait_for(wakeup.wait(), self.poll_interval)
            except TimeoutError:
                pass
            continue
        await self._process(job)
```

Handlers are coroutine functions registered by kind:

```python
@handler("order_confirmation")
async def order_confirmation(job: Job, pool: asyncpg.Pool) -> None:
    row = await pool.fetchrow("SELECT o.id, o.total, u.email, count(oi.*) AS items ...", int(job.payload["order_id"]))
    if row is None:
        raise LookupError(f"order {job.payload['order_id']} not found")
    log.info("to %s: order %s, %s item(s), total %s", row["email"], row["id"], row["items"], row["total"])
```

### 8.2 Running it

Enqueue 400 jobs that each "work" for 0.1 s, one real order confirmation, and one confirmation for an order that doesn't exist:

```sql
INSERT INTO jobs (queue, kind, payload)
SELECT 'emails', 'sleep', '{"seconds": 0.1}' FROM generate_series(1, 400);
INSERT INTO jobs (queue, kind, payload) VALUES
    ('emails', 'order_confirmation', '{"order_id": 499990}'),
    ('emails', 'order_confirmation', '{"order_id": -1}');
```

```bash
python -m bazaar.worker --queue emails --concurrency 8      # then Ctrl-C after a few seconds
```

```text
09:21:01,989 INFO bazaar.worker: worker vm:14960: queue=emails concurrency=8
09:21:07,417 INFO bazaar.worker: to james.li77329@example.org: order 499990, 4 item(s), total 1870.94
09:21:07,419 WARNING bazaar.worker: job 620 (order_confirmation) failed on attempt 1: LookupError('order -1 not found')
09:21:08,804 INFO bazaar.worker: shutting down: finishing jobs in progress (up to 30s)
09:21:08,830 INFO bazaar.worker: worker vm:14960 stopped: 401 done, 1 failed
```

400 jobs × 0.1 s of waiting, spread over 8 loops, ideally take 5 s; the worker needed about 5.4 s, all in one thread. The queue's state afterwards:

```sql
SELECT kind,
       count(*) FILTER (WHERE done_at IS NOT NULL) AS done,
       count(*) FILTER (WHERE done_at IS NULL AND attempts >= max_attempts) AS dead,
       count(*) FILTER (WHERE done_at IS NULL AND attempts < max_attempts AND run_at > now()) AS retrying,
       count(DISTINCT locked_by) AS loops
FROM jobs WHERE queue = 'emails' GROUP BY kind ORDER BY kind;
```

```text
        kind        | done | dead | retrying | loops 
--------------------+------+------+----------+-------
 order_confirmation |    1 |    0 |        1 |     1
 sleep              |  400 |    0 |        0 |     8
(2 rows)
```

All 8 loops took part. The failed job waits for its retry (10 s later, then 20, 40, ...) until it reaches `max_attempts` and is "dead", at which point a human looks at `last_error`.

### 8.3 Scaling and operating it

- **More throughput** for I/O-bound handlers: raise `--concurrency`. Each loop needs a connection only for the claim and the completion, so the pool can be smaller than the number of loops for slow handlers. The reference pool is `concurrency + 1` to keep things simple.
- **CPU-bound handlers**: run more worker *processes* (each with its own loop and pool), or push the CPU work to `asyncio.to_thread`. One process can't use more than one core for Python code.
- **Several processes are safe by construction**: `SKIP LOCKED` and the leases don't care whether competing claims come from tasks or machines.
- **Monitoring**: the query above, plus the age of the oldest ready job (`now() - min(run_at) WHERE done_at IS NULL AND run_at <= now()`), are the two numbers to alert on. Done jobs accumulate, so delete them after a retention period. The partial index `jobs_ready_idx` keeps the claim fast however many done rows there are (Module 10), but the table's VACUUM load grows with them (Module 17's autovacuum settings for `jobs`).

---

## 9. Benchmarking the drivers

A fair benchmark keeps everything the same except the thing measured: same SQL, same data, same concurrency, same machine; warm caches; several repetitions with the median reported. [`examples/07_driver_benchmark.py`](examples/07_driver_benchmark.py) does that for three workloads:

```text
asyncpg 0.32.0 | psycopg 3.3.6 binary | Python 3.13.16 | median of 3
(a) 20,000 point lookups, 16 concurrent workers
    asyncpg, pool                              0.95s    21,020 queries/s
    psycopg async, pool                        1.86s    10,738 queries/s
    psycopg sync, pool + 16 threads            4.53s     4,419 queries/s
    psycopg sync, 1 connection, sequential     3.53s     5,667 queries/s
(b) fetch 457,159 rows x 4 columns into Python, one connection
    asyncpg fetch()                            0.58s   791,886 rows/s
    psycopg async fetchall()                   0.65s   707,841 rows/s
    psycopg async fetchall(), binary=True      0.94s   485,309 rows/s
    psycopg sync fetchall()                    0.58s   789,834 rows/s
(c) insert 100,000 rows, one connection
    asyncpg copy_records_to_table()            0.32s   314,962 rows/s
    psycopg async COPY write_row()             0.31s   318,857 rows/s
    psycopg sync COPY write_row()              0.26s   390,396 rows/s
    asyncpg executemany()                      0.57s   174,804 rows/s
    psycopg async executemany()               12.16s     8,221 rows/s
    psycopg sync executemany()                 2.17s    46,113 rows/s
```

What the numbers say, and what they don't:

- **(a) Many small queries**: this is where asyncpg's design shows. It was about **2x faster than psycopg async**, mostly through lower client-side CPU per query (Cython protocol code, cached prepared statements, binary decoding). Both async drivers beat **16 threads** with sync psycopg by far, which in turn lost to a *single* sequential connection. With client and server on the same 4 cores, 16 Python threads fighting for the GIL and 16 busy backends fighting for CPU just add contention. On a machine where the database is elsewhere, threads do better than this; the ranking of the async drivers held in every run.
- **(b) One big result**: all drivers were within about 10% of each other. The time goes into creating 457,000 × 4 Python objects, which is the same work for everyone. psycopg's binary format was *slower* here (the numeric columns are converted to `Decimal` either way, and binary numerics are more work to decode in Python), confirming Module 22's advice to measure before switching.
- **(c) Bulk insert**: COPY wins in every driver, and the drivers are close to each other. The big surprise is **psycopg async `executemany()`: 12 s, six times slower than the sync version** of the same call. It scaled linearly (about 120 µs per row against 20 µs for sync, in a separate check with 2,000 to 40,000 rows), so it's the async pipeline's per-statement overhead, not a cliff. asyncpg's `executemany()` was 20 times faster. The practical lesson for psycopg async: use COPY for bulk writes, or a single statement with arrays (`unnest`, Module 22), not `executemany()`.

Caveats you should apply to any benchmark, including this one: client and server shared one machine with other workloads, so absolute numbers are noisy (the same workload varied by 10–30% between runs). The round trip was 0.2 ms, much shorter than across a real network, which hides the advantage of fewer round trips. The queries were trivial, so driver overhead dominates in (a); with queries that take 5 ms on the server, all drivers would look the same. And versions matter: both drivers improve with every release, so rerun the script before you rely on any of these numbers.

The decision rarely hinges on these numbers alone. At Bazaar's scale, the database's own work dominates long before 10,000 queries per second per process becomes the bottleneck, and API ergonomics, sync/async code sharing, and the ORM's needs (Modules 24–25) usually decide.

---

## 10. The Bazaar async layer

This module's milestone ([project.md](project.md)) adds the async half of the package:

```text
src/bazaar/
├── aio/
│   ├── db.py        # psycopg: connect(), create_pool() with async configure()
│   │                # asyncpg: asyncpg_connect(), create_asyncpg_pool() with jsonb codecs in init
│   └── repo.py      # get_product, search_products, list_orders_for_user, place_order,
│                    # import_products (async COPY), enqueue_job: same SQL as bazaar.repo
└── worker.py        # python -m bazaar.worker
sql/2301_notify.sql  # jobs_ready and new_order triggers
tests/
├── test_aio.py      # async repo, asyncpg codecs, concurrent checkouts, new_order notifications
└── test_worker.py   # exactly-once processing, NOTIFY wake-up, graceful stop, cancellation
```

The tests use **pytest-asyncio** with `asyncio_mode = "auto"` (set in `pyproject.toml`), so `async def test_...` functions and `async` fixtures just work. Each test gets its own event loop, so pools are created inside tests or function-scoped fixtures (5.2). Two tests show patterns worth copying:

**Concurrent checkouts never oversell.** A committed fixture creates a product with exactly 5 units; 20 tasks each try to buy one through a 10-connection pool, all at once:

```python
async with asyncio.TaskGroup() as tg:
    for _ in range(20):
        tg.create_task(checkout())
...
assert outcomes.count("ok") == 5 and outcomes.count("sold out") == 15
assert p.available == 0
```

This is Module 10's `place_order()` locking design, proven from the application side: the row lock on the inventory row serializes the 20 checkouts, and the losers get `InsufficientStock`, translated from `BZ001`.

**The worker processes each job exactly once in the normal case.** 200 jobs, 4 loops, a handler that counts invocations per job id: every id is seen once, all 4 loops took part, and an unknown job kind is failed with backoff. A second test enqueues a job while the worker's loops sleep with a 30-second poll interval and asserts that it's processed in under a second, which proves the NOTIFY path works. A third cancels the worker mid-job and checks that the job is still leased, not lost.

```bash
cd project/python && pytest -v          # 25 tests: Module 22's and Module 23's
```

---

## Summary

- Async I/O lets one thread keep many waits in flight. It speeds up concurrent, I/O-bound work; it doesn't make a query faster, add CPU, or add database capacity.
- A blocking call inside `async def` (sync driver, `time.sleep`) stalls the whole loop. Use async drivers or `asyncio.to_thread`, and debug mode to find offenders.
- Prefer `TaskGroup` to `gather`: on failure it cancels its siblings and leaves nothing running. Bound concurrency with the pool and with semaphores, and never hold a pooled connection across unrelated awaits.
- Timeouts are cancellations. Both drivers cancel the server-side query and keep the connection usable. Combine `asyncio.timeout()` (request deadlines) with `statement_timeout` (the database's safety net).
- psycopg's async API is the sync API with `await`, so the same SQL, adapters, and error translation serve both. Open `AsyncConnectionPool` inside the running loop, one per process.
- asyncpg implements the protocol itself: `$1` parameters, `Record` rows, binary types with strict (and occasionally surprising) input encoding, JSON as `str` until you install a codec in the pool's `init`, explicit transactions, and every statement prepared and cached. Expect `InvalidCachedStatementError` after schema changes inside transactions.
- Behind PgBouncer in transaction mode, prepared statements fail unless PgBouncer ≥ 1.21 has `max_prepared_statements > 0`. Otherwise use `statement_cache_size=0` (asyncpg) or `prepare_threshold=None` (psycopg).
- One connection runs one query at a time: asyncpg raises, psycopg serializes. A connection and its transaction must belong to one task.
- `LISTEN`/`NOTIFY` consumers are tasks with dedicated connections. Notifications are wake-up calls delivered on commit, and are lost while disconnected, so pair them with polling.
- The Bazaar worker runs N loops over `dequeue_jobs()` with leases, NOTIFY wake-ups and graceful shutdown: at-least-once delivery, so idempotent handlers.
- In the benchmark, asyncpg was 2x faster than psycopg async for small queries, the drivers were even for big results and COPY, and psycopg async `executemany()` was very slow. Measure on your own setup.

## What's next

Module 24 replaces hand-written SQL strings with SQLAlchemy Core: the same queries built from Python expressions, with table metadata, Postgres-specific constructs, and the same psycopg and asyncpg drivers underneath.
