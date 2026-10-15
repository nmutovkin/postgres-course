# Module 23 project: the async layer and the job worker

<!-- nav:start -->
> [Theory](README.md) · [Exercises](exercises.md) · **Project** · [Solutions](solutions/README.md)  
> [← 22 psycopg 3](../22-psycopg/README.md) · [Course home](../../README.md) · [24 SQLAlchemy Core →](../24-sqlalchemy-core/README.md)
<!-- nav:end -->

Bazaar's storefront is going to be an async web service (Module 28), and its background work (order confirmations, receipts, search reindexing) needs a worker that reacts within milliseconds when a job is queued, without hammering the database when the queue is empty. In this milestone you add the async half of the `bazaar` package: async connections and pools for both drivers, an async twin of the Module 22 repository, NOTIFY triggers, and an async job worker you can start with `python -m bazaar.worker`.

**Starting state:** `./project/bin/reset.sh 23` and your Module 22 package (or the reference one).

**Reference solution:** [`project/python/`](../../project/python/): `sql/2301_notify.sql`, `src/bazaar/aio/{db,repo}.py`, `src/bazaar/worker.py`, `tests/test_aio.py`, `tests/test_worker.py`.

## Requirements

### 1. Dependencies and test setup

- Add `asyncpg` to the runtime dependencies and `pytest-asyncio` to the dev dependencies.
- Configure pytest-asyncio's auto mode in `pyproject.toml`, so `async def` tests and fixtures need no decorators.
- Add a session-scoped, autouse pytest fixture that applies every file in `project/python/sql/` (they must be idempotent).

### 2. NOTIFY triggers (`sql/2301_notify.sql`)

An idempotent SQL file (run as `bazaar_owner`, Module 15) that creates:

- a **statement-level** `AFTER INSERT` trigger on `jobs` that sends `pg_notify('jobs_ready', <queue>)` once per distinct queue in the inserted rows;
- a row-level `AFTER INSERT` trigger on `orders` that sends `pg_notify('new_order', '{"order_id": ..., "seller_id": ..., "user_id": ...}')`.

Apply it with `./project/bin/psql.sh < project/python/sql/2301_notify.sql`. (The database from the setup already has these triggers: Alembic revision 2601 contains the same SQL, and applying the file again is harmless. Module 26 shows how a SQL file becomes a migration.)

### 3. Async connections (`bazaar.aio.db`)

- psycopg: `connect()`, `create_pool()` returning an **opened** `AsyncConnectionPool`, and an async `configure()` that registers the enum adapters like the sync one. They use the same settings and connection parameters as `bazaar.db` (don't duplicate them).
- asyncpg: `asyncpg_connect()` and `create_asyncpg_pool()` with `application_name` and `statement_timeout` as `server_settings`, and an `init` hook that installs `json`/`jsonb` codecs. Reject a libpq `key=value` DSN with a clear error, since asyncpg needs a URI.
- Keyword overrides (`create_pool(max_size=20)`) take precedence over the settings.

### 4. Async repository (`bazaar.aio.repo`)

`get_product`, `search_products`, `list_orders_for_user`, `place_order`, `import_products` and `enqueue_job` with the **same signatures (plus `async`), the same SQL, the same models and the same exceptions** as `bazaar.repo`. Import the SQL constants and helper functions from the sync module instead of copying them.

### 5. The worker (`bazaar.worker`)

- A `handler(kind)` decorator registering coroutine functions `async def fn(job: Job, pool: asyncpg.Pool)`, with at least `noop`, `sleep` (`payload.seconds`) and `order_confirmation` (looks up the order and its buyer, and logs the "e-mail"; fails for an unknown order).
- `Worker(pool, queue=, concurrency=, poll_interval=, job_timeout=, handlers=)`, with `run()` and `stop()`:
  - `concurrency` loops in a `TaskGroup`, each claiming **one** job at a time with `dequeue_jobs()`, leased for `job_timeout + 30` seconds, never inside a long transaction;
  - `complete_job()` on success; `fail_job(id, repr(error))` for any `Exception`, including an unknown kind and a handler exceeding `job_timeout`; a `CancelledError` is *not* recorded as a failure;
  - an empty queue makes a loop wait for a `jobs_ready` notification for its queue **or** `poll_interval`, whichever comes first, from a dedicated listener connection; no notification may be lost between "found nothing" and "start waiting";
  - `stop()` lets every loop finish its current job and exit; `run()` then returns.
- `python -m bazaar.worker --queue Q --concurrency N --poll-interval S --grace G`: logs to stderr; `SIGINT`/`SIGTERM` start a graceful shutdown, cancelling in-flight jobs after `G` seconds or on a second signal.

### 6. Tests

- The async repository: types, search, `InsufficientStock` with the right numbers, pagination, all inside rolled-back transactions.
- The asyncpg pool decodes jsonb into dicts and sets `application_name`.
- **20 concurrent checkouts** of a product with 5 units in stock, through a pool: exactly 5 succeed, 15 get `InsufficientStock`, and available stock ends at 0 (committed fixture data, cleaned up afterwards).
- A `new_order` notification arrives only after the placing transaction commits, with the right order id.
- The worker: 200 jobs and 4 loops: each job runs exactly once, all loops take part, and an unknown kind is failed with a future `run_at`. An idle worker with a 30 s poll interval picks up a new job in under a second (NOTIFY works). `stop()` during a job finishes that job and leaves the rest untouched. Cancelling `run()` mid-job leaves the job leased and not done.

## Acceptance checks

```bash
cd project/python && source .venv/bin/activate
export BAZAAR_DSN=postgresql://postgres:postgres@localhost:5432/bazaar
ruff check src tests
pytest -v
```

Expected: ruff passes, and all 25 tests (16 from Module 22, 9 from this module) pass in a few seconds.

An end-to-end run of the worker:

```bash
./project/bin/psql.sh -c "INSERT INTO jobs (queue, kind, payload)
  SELECT 'demo', 'sleep', '{\"seconds\": 0.1}' FROM generate_series(1, 100);
  INSERT INTO jobs (queue, kind, payload) VALUES ('demo', 'order_confirmation', '{\"order_id\": 500000}');"
python -m bazaar.worker --queue demo --concurrency 8      # Ctrl-C after a few seconds
```

Expected: a log line `to lukas.meyer10462@example.org: order 500000, 2 item(s), total 1147.96`, then, after Ctrl-C, `worker ... stopped: 101 done, 0 failed`. The 100 sleeping jobs take about 1.3 s with 8 loops. Then check that nothing is left:

```sql
SELECT count(*) FILTER (WHERE done_at IS NULL) AS open, count(DISTINCT locked_by) AS loops
FROM jobs WHERE queue = 'demo';            -- open = 0, loops = 8 (or fewer, if some loops got none)
DELETE FROM jobs WHERE queue = 'demo';
```
