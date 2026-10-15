# Module 27: Solutions

<!-- nav:start -->
> [Theory](../README.md) · [Exercises](../exercises.md) · [Project](../project.md) · **Solutions**  
> [← 26 Migrations with Alembic](../../26-migrations/README.md) · [Course home](../../../README.md) · [28 Capstone: the Bazaar Service →](../../28-capstone/README.md)
<!-- nav:end -->

All solutions ran against PostgreSQL 17.11 with the large dataset at revision 2701, on a 4-vCPU machine shared with other work, with psycopg 3.3.6, asyncpg 0.32.0 and SQLAlchemy 2.1.4. Run them from `project/python` with the venv active. Timings vary with load; repeat runs before drawing conclusions from small differences.

---

<!-- toc:start -->
**Contents**

- [Exercise 1: Classify real failures](#exercise-1-classify-real-failures)
- [Exercise 2: Timeouts for one slow endpoint](#exercise-2-timeouts-for-one-slow-endpoint)
- [Exercise 3: Timeouts that survive PgBouncer](#exercise-3-timeouts-that-survive-pgbouncer)
- [Exercise 4: A retry decorator with jitter, proven](#exercise-4-a-retry-decorator-with-jitter-proven)
- [Exercise 5: How much faster are template databases?](#exercise-5-how-much-faster-are-template-databases)
- [Exercise 6: Pools and `fork()`](#exercise-6-pools-and-fork)
- [Exercise 7: A concurrency test that can fail](#exercise-7-a-concurrency-test-that-can-fail)
- [Exercise 8: A projection fed by the outbox](#exercise-8-a-projection-fed-by-the-outbox)
- [Exercise 9: Database time per request](#exercise-9-database-time-per-request)
- [Exercise 10: Size a pool, and prove the size](#exercise-10-size-a-pool-and-prove-the-size)
- [Exercise 11: Exactly once, under every crash](#exercise-11-exactly-once-under-every-crash)
- [Exercise 12: Checkout through failovers](#exercise-12-checkout-through-failovers)

<!-- toc:end -->

## Exercise 1 ★: Classify real failures

[`ex01_classify.py`](ex01_classify.py):

```text
situation                            exception                  sqlstate kind           retried / if idempotent
duplicate email                      UniqueViolation            23505    permanent      False / False
statement_timeout                    QueryCanceled              57014    timeout        False / False
FOR UPDATE NOWAIT                    LockNotAvailable           55P03    lock_timeout   True / True
concurrent update, REPEATABLE READ   SerializationFailure       40001    serialization  True / True
CREATE TABLE as bazaar_app           InsufficientPrivilege      42501    permanent      False / False
place_order(): unknown product       DatabaseError              BZ002    permanent      False / False
wrong password                       OperationalError           None     permanent      False / False
session terminated (failover)        AdminShutdown              57P01    connection     False / True
```

That's the output **after** the fix. The first run classified the wrong password as `connection` (retried for idempotent work): psycopg raises a plain `OperationalError` without a SQLSTATE for every failed connection attempt, whether the server is unreachable (worth retrying) or refused the password (never worth retrying: it only gets the account locked sooner, or floods the server's log). The only difference is in the message, which libpq takes from the server. `classify()` now checks a short list of fragments (`password authentication failed`, `no pg_hba.conf entry`, `does not exist`, `permission denied for database`, `certificate verify failed`) before treating an `OperationalError` as a connection problem, and `tests/test_resilience.py` has the case. (asyncpg reports the SQLSTATE, `28P01`, so it was already permanent.)

Notes on the others: `BZ002` is a custom SQLSTATE from `place_order()`, so psycopg raises the generic `DatabaseError` with `sqlstate = 'BZ002'` (`bazaar.errors.translate()` turns it into `ProductUnavailable`). The `REPEATABLE READ` case produces `40001` because the second transaction's snapshot predates the first's committed update. Under `READ COMMITTED` the same pair would simply wait and then update the new version.

## Exercise 2 ★: Timeouts for one slow endpoint

[`ex02_report_timeouts.py`](ex02_report_timeouts.py):

```python
def seller_report(engine, seller_id, start, end, *, statement_timeout="2s", lock_timeout="500ms"):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "SELECT set_config('statement_timeout', :st, true),"
                " set_config('lock_timeout', :lt, true)"
            ),
            {"st": statement_timeout, "lt": lock_timeout},
        )
        return reports.seller_statement(conn, seller_id, start, end)
```

```text
seller 1, November 2025                      ok after 0.11s (3705 lines)
seller 1, three years, limit 0.5 s           QueryCanceled sqlstate=57014 kind=timeout after 0.52s
seller 1, November, orders locked            LockNotAvailable sqlstate=55P03 kind=lock_timeout after 0.51s
caller deadline 0.5 s, three years           TimeoutError sqlstate=None kind=timeout after 0.50s
                                             still running on the server: 1
                                             ...and finished anyway after 1.32s (result thrown away)
afterwards: 5s / 0
```

- Both settings are transaction-local (`set_config(..., true)` = `SET LOCAL`), in one round trip, and gone at commit or rollback. Behind PgBouncer that's the only safe kind of `SET`.
- With `orders` locked by a "migration", the report didn't queue for the full 2 s: `lock_timeout` turned the wait into a distinct, retryable error after 0.5 s. (The report reads `orders`; an `ACCESS EXCLUSIVE` lock blocks even reads.)
- The synchronous caller's deadline only stopped the *waiting*. The thread and the server kept going for another 0.8 s, holding a pooled connection, and the result was thrown away. In synchronous code, the server-side `statement_timeout` is the real limit; the async version (psycopg's `AsyncConnection` under `asyncio.timeout()`) sends a cancel request (section 4).
- Counting active backends needs `backend_type = 'client backend'`: the first version counted 3, because the report ran as a parallel query with two worker processes that share its `application_name`.

## Exercise 3 ★: Timeouts that survive PgBouncer

[`ex03_role_defaults.sql`](ex03_role_defaults.sql), then [`ex03_check.py`](ex03_check.py):

```text
    rolname     | datname |                                       setconfig
----------------+---------+----------------------------------------------------------------------------------------
 bazaar_analyst | bazaar  | {statement_timeout=15min,idle_in_transaction_session_timeout=5min}
 bazaar_app     | bazaar  | {statement_timeout=5s,idle_in_transaction_session_timeout=30s,application_name=bazaar}
 bazaar_seller  | bazaar  | {statement_timeout=3s,idle_in_transaction_session_timeout=30s}
(3 rows)

('bazaar_app', '5s', '30s', 'bazaar')
('bazaar_seller', '3s', '30s', '')
('bazaar_analyst', '15min', '5min', '')
a client-sent application_name wins: bazaar-worker
```

`ALTER ROLE ... IN DATABASE bazaar SET` stores the defaults in `pg_db_role_setting`, applied when a session of that role starts in that database, whoever opened it: the application directly or PgBouncer on its behalf. Settings the client sends at startup override them (the worker's `application_name`), and `SET`/`SET LOCAL` override both. Undo with `ALTER ROLE bazaar_app IN DATABASE bazaar RESET ALL` (and likewise for the others); the solution did, to leave the reference cluster as it was.

Where should these statements live? They're per-environment configuration with cluster-wide reach, so provisioning (the same place that sets the passwords) is the natural home. A migration could run them only as a role with `CREATEROLE` and admin rights on these roles, which the migration role shouldn't need for anything else.

## Exercise 4 ★★: A retry decorator with jitter, proven

[`ex04_retry.py`](ex04_retry.py):

```python
def retry_transaction(
    attempts=5, base=0.05, cap=2.0, *, sleep=time.sleep, rand=random.random, log=print
):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except RETRYABLE as e:  # SerializationFailure, DeadlockDetected
                    if attempt == attempts:
                        raise
                    delay = rand() * min(cap, base * 2 ** (attempt - 1))
                    ...
                    sleep(delay)

        return wrapper

    return deco
```

```text
1. injected 40001, deterministic schedule (rand() = 0.5, no real sleeping):
  attempt 1: 40001 -> sleep 25.0 ms
  attempt 2: 40001 -> sleep 50.0 ms
  attempt 3: 40001 -> sleep 100.0 ms
  result: committed after 4 calls; slept [0.025, 0.05, 0.1]
  a permanent conflict is re-raised after 3 attempts
2. real 40001 (SERIALIZABLE write skew, two threads held together by a barrier):
  attempt 1: 40001 -> sleep 17.9 ms
  results {'ana': 'off duty', 'ben': 'refused: last one on duty'}, 3 calls, still on duty: 1
3. 50 clients after a conflict, retries in 1 ms slots:
  no jitter (sleep = base * 2**n)    after 12 retry rounds: 50 of 50 still failing
  full jitter                        after 9 retry rounds: 0 of 50 still failing
```

1. Injecting `sleep` and `rand` makes the schedule a pure function: `0.5 × 50 ms × 2^(n−1)`. Real code uses `random.random` and `time.sleep`.
2. In the real case, both transactions read "one other person on duty", then each updated *its own* row: no row lock conflict, so `READ COMMITTED` (or even `REPEATABLE READ`) would let both go off duty, leaving nobody. `SERIALIZABLE` detected the read/write dependency cycle and failed one transaction with `40001` at commit; its retry re-read the table and refused. (Which of the two wins varies between runs.) The first version of this demo moved stock between two inventory rows in opposite directions and got `40P01` instead: updating the same rows in opposite orders is a deadlock, detected before any serialization check.
3. Without jitter, every client computes the same delay, so they collide in every round, forever. With full jitter, the spread grows with each round until each client finds a free slot.

**Alternatives.** The `tenacity` library provides this decorator (`retry_if_exception_type`, `wait_random_exponential`, `stop_after_attempt`). The value of writing it once is knowing what to configure: retry only the transaction-owning function, on the right errors, with jitter and a cap.

## Exercise 5 ★★: How much faster are template databases?

[`ex05_test_template_speed.py`](ex05_test_template_speed.py), each configuration run twice:

| Mode | sequential | `-n 4` |
|---|---|---|
| `migrate` (`alembic upgrade head` per test) | 11.47 s, 14.83 s | 8.34 s, 7.85 s |
| `template` (clone per test) | 7.74 s, 7.27 s | 3.66 s, 4.15 s |

The slowest setup per test: 0.56–0.86 s with migrations, 0.36–0.38 s with the template (one earlier, quieter run: 0.2 s per clone). Template plus xdist was about 3× faster than sequential migrations, and template mode benefits more from parallelism: migrations are CPU-bound Python (Alembic loads `env.py` and every revision module for each run), which four workers on four cores share with the database server. Cloning is mostly server-side file work.

The gap grows with the schema: every new revision adds to the migration time and nothing to the clone time, and seed data in the template is free per test. Direct measurement of the clone itself: `CREATE DATABASE ... TEMPLATE` 104–158 ms with `STRATEGY WAL_LOG`, 152–194 ms with `FILE_COPY`; `DROP DATABASE` 14–26 ms and 58–87 ms respectively.

## Exercise 6 ★★: Pools and `fork()`

[`ex06_fork.py`](ex06_fork.py), two runs:

```text
--- engine created before fork, used as is (parent's pooled connection: backend 6268)
  worker 0: ProgrammingError: (psycopg.ProgrammingError) can't change 'autocommit' now: connection in transact
  worker 1: ok, backend 6269
  worker 2: OperationalError: (psycopg.OperationalError) consuming input failed: server closed the connection
  parent afterwards: ok
--- engine created before fork, dispose_after_fork() (parent's pooled connection: backend 6271)
  worker 0: ok, backend 6273
  worker 1: ok, backend 6274
  worker 2: ok, backend 6272
  parent afterwards: ok
--- engine created before fork, used as is (parent's pooled connection: backend 6275)
  worker 0: ResourceClosedError: This result object does not return rows. It has been closed automatically.
  worker 1: ResourceClosedError: This result object does not return rows. It has been closed automatically.
  worker 2: InternalError: (psycopg.errors.IdleInTransactionSessionTimeout) terminating connection due to i
  parent afterwards: ok
--- engine created before fork, dispose_after_fork() (parent's pooled connection: backend 6280)
  worker 0: ok, backend 6282
  worker 1: ok, backend 6281
  worker 2: ok, backend 6283
  parent afterwards: ok
```

Each worker inherited the parent's pooled connection: one socket, one PostgreSQL backend, now used by three processes at once. Their protocol messages interleave; a worker reads a reply meant for another (`This result object does not return rows`: it got a command-complete for someone else's statement), finds the connection in a transaction state it didn't create, or the server terminates the session it sees as "idle in transaction" while one worker waits for a reply another one consumed. The errors differ every run, which is typical of this bug in production: "random database errors after the deploy".

`dispose_after_fork()` registers `engine.dispose(close=False)` to run in every child right after `fork()`. `close=False` drops the child's references to the inherited connections **without** closing them: closing would send a Terminate message over the shared socket and end the *parent's* session. Each child then opens its own connections (three different backends). One worker in the first run happened to get its own connection anyway (the pool replaced a connection it considered broken); you can't rely on that.

## Exercise 7 ★★: A concurrency test that can fail

[`ex07_test_cancel.py`](ex07_test_cancel.py) (`pytest -s` to see the prints):

```text
naive_cancel: results ['CheckViolation', 'CheckViolation', 'CheckViolation', 'CheckViolation', 'CheckViolation', 'CheckViolation', 'True', 'True', 'True', 'True'], reserved afterwards 0
.sql_cancel: results ['False', 'False', 'False', 'False', 'False', 'False', 'False', 'False', 'False', 'True'], reserved afterwards 6
2 passed in 1.26s
```

The naive version reads `pending` in all ten sessions (a barrier after the read guarantees it), and then each releases 2 units: four "succeeded" before the reservation hit 0, and the other six failed on `CHECK (reserved >= 0)`. The other order's 6 reserved units were released by mistake: that stock can now be sold twice. `cancel_order()` locks the order row first (`FOR NO KEY UPDATE`); the other nine wait, then see `cancelled` (`READ COMMITTED` re-reads the locked row's newest version) and return `false`.

Two design points. With only one order in the fixture, the over-release would stop at 0, and the final `reserved` (0) would be *correct*: a test that checked only the end state would pass on the buggy code. The second order makes the damage visible. And the template for this test has the function installed (`fingerprint + "-cancel"` makes it a different template from the project's), so each test gets a database with it in ~0.2 s.

## Exercise 8 ★★: A projection fed by the outbox

[`ex08_projection.py`](ex08_projection.py):

```text
events: 81, deliveries: 108, applied: 81, skipped as duplicates: 27
projection vs. orders, mismatches: []
[(1, 30, Decimal('620.00')), (2, 26, Decimal('1060.00')), (3, 25, Decimal('1560.00'))]
```

40 checkouts produced 81 orders (carts span up to three sellers) and 81 events. The broker delivered 108 times (every third message twice); the consumer applied 81 and skipped 27, and the projection matches the orders exactly. The handler is an increment (`ON CONFLICT ... DO UPDATE SET orders = orders + 1, revenue = revenue + EXCLUDED.revenue`), which is not idempotent by itself: `processed_messages` is what makes it safe. An alternative is a handler that is idempotent by construction, for example recomputing the seller's day from `orders` on every event (`INSERT ... SELECT count(*), sum(total) ... ON CONFLICT DO UPDATE SET ... = EXCLUDED...`). That needs no dedup table, at the cost of a query per event, and only works when the consumer can read the source data.

## Exercise 9 ★★: Database time per request

[`ex09_request_db_time.py`](ex09_request_db_time.py):

```text
route                  requests  db ms total  avg stmts  max stmts
GET /me/orders/v0             5        196.8       67.6         79
GET /me/orders                5         53.8        2.0          2
GET /products/{id}            5          8.4        2.0          2
one log line: {"ts": "2026-10-14T11:55:50.489", "level": "INFO", "logger": "bazaar.api", "msg": "request done", "route": "GET /me/orders/v0", "duration_ms": 88.2, "db_ms": 45.1, "db_statements": 79}
```

Two engine listeners accumulate into a `RequestStats` object held in a `ContextVar` that `request()` sets and resets. The report is what a query over the JSON logs gives: the old handler, an N+1 pattern (1 query for the orders, 1 per order for its items, 1 per distinct product), sends 34 times as many statements as the service and costs nearly 4 times its database time, while the service's 2 statements per request don't depend on the data. This per-request count is the production counterpart of Module 25's `count_statements()` test: it finds N+1 patterns in code paths no test covers. Note that database time is less than request time (88.2 ms versus 45.1 ms here): the rest is Python building objects.

## Exercise 10 ★★★: Size a pool, and prove the size

[`ex10_pool_sizing.py`](ex10_pool_sizing.py):

```text
W (connection held per request) = 3.2 ms
lambda = 400 requests/s  ->  L = lambda * W = 1.26 connections busy on average
a pool of ceil(L) = 2 is the bare minimum; bursts need headroom
pool  1: 1304 requests, latency p50 1192.7 ms, p99  2332.4 ms; pool wait p99  2328.4 ms
pool  2: 1298 requests, latency p50   18.8 ms, p99    62.0 ms; pool wait p99    56.9 ms
pool  4: 1274 requests, latency p50    6.3 ms, p99    23.7 ms; pool wait p99    13.2 ms
pool  8: 1294 requests, latency p50    5.8 ms, p99    21.8 ms; pool wait p99     6.1 ms
pool 16: 1293 requests, latency p50    5.4 ms, p99    21.2 ms; pool wait p99     7.1 ms
```

- **Pool 1**: L = 1.26 > 1, so requests arrive faster than one connection can serve them. The queue grows without bound: p50 1.2 s after 4 s of load, and it would keep growing. That's the unstable side of Little's law.
- **Pool 2**: stable, but at 63% utilization queueing theory's waits are already visible: p99 62 ms, almost all of it waiting for the pool.
- **Pool 4**: p99 waits drop to 13 ms. **8 and 16** change little: the remaining latency is the query itself and the event loop, not the pool.

Deploy 4 (per process): about 3× the average need, for bursts. Doubling it buys almost nothing here and costs server connections (Module 17's budget). In the first attempt the query was too fast (W = 1.3 ms at 150 requests/s, L = 0.2) and every pool size looked the same: load tests need a load that matters. Also note the open loop: with a closed loop (each worker waiting for its previous request), the one-connection pool would simply have served fewer requests per second, and the overload would have been invisible.

## Exercise 11 ★★★: Exactly once, under every crash

[`ex11_failure_matrix.py`](ex11_failure_matrix.py):

```text
crashes injected: {'C1 consumer fails before COMMIT': 32, 'C2 consumer dies before ack': 26, 'P  producer fails': 34, 'R1 publish raises': 51, 'R2 relay dies before COMMIT': 17}
orders committed: 166 (in the database: 166), events: 166, broker deliveries: 251, rounds: 37
emails: 166 for 166 distinct orders; orders without email: 0
```

Who makes each crash harmless:

| Crash | What happens | Made safe by |
|---|---|---|
| P: producer fails after writing the event | order and event roll back together (34 of 200 orders never existed, and neither did their events) | the outbox being in the business transaction |
| R1: publish rejected | message stays unpublished, `attempts`/`last_error` recorded; next round retries | the relay marking only after success |
| R2: relay dies after publishing | the broker has the message, the outbox still says pending: published again later | at-least-once + the consumer's dedup |
| C1: handler fails before commit | effect and `processed_messages` row roll back together; the message isn't acked and comes again | the consumer's single transaction |
| C2: consumer commits, dies before ack | redelivered; `processed_messages` says done; skipped | `consume_once()` |

251 deliveries for 166 messages, 37 rounds (a relay round ends at the first rejected publish, to keep order), and in the end exactly one email per order. Remove `consume_once()` and C2 and R2 produce duplicate emails; move `outbox.record()` out of the order's transaction and P produces emails for orders that don't exist.

## Exercise 12 ★★★: Checkout through failovers

[`ex12_failover_checkout.py`](ex12_failover_checkout.py):

```text
without keys: 150 checkouts acknowledged, 39 retries, 23 connections killed; orders in the database: 342 (expected 300), units reserved: 342
with keys   : 150 checkouts acknowledged, 39 retries, 26 connections killed; orders in the database: 300 (expected 300), units reserved: 300
```

Both clients survived every "failover" (the terminated connections were replaced through `pool_pre_ping` or failed an attempt that was retried) and acknowledged all 150 checkouts. Without keys, every lost reply after a successful commit was retried as a new checkout: 21 checkouts were placed twice, 42 extra orders, 42 units of stock reserved for orders nobody wanted. With keys, each retry found its earlier orders (`_replay()`) and returned them: 300 orders, no duplicates, no losses.

`idempotent=True` is a statement the caller makes about the work; the retry helper can't check it. Run 1 is what happens when it's false.
