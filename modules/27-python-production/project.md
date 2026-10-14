# Module 27 project: a production-ready data layer

<!-- nav:start -->
> [Theory](README.md) · [Exercises](exercises.md) · **Project** · [Solutions](solutions/README.md)  
> [← 26 Migrations with Alembic](../26-migrations/README.md) · [Course home](../../README.md) · [28 Capstone: the Bazaar Service →](../28-capstone/README.md)
<!-- nav:end -->

Before Bazaar's service goes live (Module 28), its data layer must behave under real conditions: concurrent buyers, failovers, slow queries, retried requests, PgBouncer, and engineers who need to know what happened at 2 a.m. In this milestone you add error classification and retries, timeouts on every connection, an idempotent checkout that publishes its events through a transactional outbox, observability hooks, and a test suite that runs in parallel, gets fresh databases from a template, and proves concurrency properties with barriers.

**Starting state:** `./project/bin/reset.sh 27`, `alembic stamp 2600 && alembic upgrade 2604` (your Module 26 milestone), and your Module 26 package (or the reference one).

**Reference solution:** [`project/python/`](../../project/python/): `src/bazaar/resilience.py`, `src/bazaar/outbox.py`, `src/bazaar/observability.py`, `migrations/versions/2701_outbox.py`, the outbox tables in `src/bazaar/sa/tables.py`, `checkout()`/`checkout_async()` in `src/bazaar/orm/services.py`, `IdempotencyConflict` and `constraint_name()` in `src/bazaar/errors.py`, the new settings in `src/bazaar/config.py`, `create_read_engine()`/`dispose_after_fork()` in `src/bazaar/sa/engine.py`, `TemplateDatabase` in `src/bazaar/testing.py`, the fixtures in `tests/conftest.py`, `tests/factories.py` (`make_shop`), and `tests/test_{resilience,outbox,observability,concurrency,containers}.py`.

## Requirements

### 1. Settings and connections

- New settings: `BAZAAR_LOCK_TIMEOUT` (ms, default 0 = off), `BAZAAR_IDLE_TX_TIMEOUT` (ms, default 30000), `BAZAAR_POOL_TIMEOUT` (s, default 10), `BAZAAR_REPLICA_DSN` (optional). `Settings.session_settings()` returns `application_name` and the non-zero timeouts; `libpq_options()` renders them as `-c name=value`. Every connection factory (`bazaar.db`, `bazaar.aio.db` for both drivers, `bazaar.sa.engine` for both async drivers) uses them, and the pools use the pool timeout.
- `create_read_engine()`: `BAZAAR_REPLICA_DSN` or the primary, `application_name` suffixed `-ro`, `default_transaction_read_only = on` on every connection.
- `dispose_after_fork(engine)`: `engine.dispose(close=False)` in forked children (`os.register_at_fork`).

### 2. Resilience (`bazaar.resilience`)

- `ErrorKind` (`serialization`, `lock_timeout`, `connection`, `read_only`, `overloaded`, `timeout`, `permanent`) and `classify(exc)` for psycopg, asyncpg and SQLAlchemy-wrapped errors, by SQLSTATE first (`40001`, `40P01`, `55P03`, `57P01`–`57P03`, `57P05`, class `08`, `25006`, `25P03`, `25P04`, `57014`, `53xxx`), then by client-side exception (pool timeouts, dropped connections, refused connections with a permanent reason, `TimeoutError`). `sqlstate(exc)`.
- `RetryPolicy(attempts, base_delay, max_delay, deadline, retry_on, idempotent_retry_on)` with full-jitter exponential backoff; connection and read-only errors retried only for idempotent work.
- `call_with_retry()`, `acall_with_retry()` (never retries cancellation), decorators `retry`/`aretry`, and the transaction-owning helpers `run_transaction()`/`arun_transaction()` (psycopg pools, asyncpg pools) and `run_in_session()`/`arun_in_session()` (SQLAlchemy session factories). Each retry is logged at `WARNING` with structured fields.

### 3. Idempotent checkout and the transactional outbox

- Revision **2701**: `outbox` (identity id, `topic`, `aggregate_type`, `aggregate_id text`, `payload`/`headers` JSONB, `created_at`, `published_at`, `attempts`, `last_error`, `CHECK (attempts >= 0)`), the partial index `outbox_unpublished_idx (id) WHERE published_at IS NULL`, a statement-level `NOTIFY outbox` trigger, and `processed_messages (consumer, message_id)`. No explicit grants (the default privileges do it). Described in `bazaar.sa.tables`.
- `services.checkout(session, user_id, items, shipping_address_id=None, *, idempotency_key=None, emit_events=True)` and `checkout_async()` (same statements): with a key, a repeated call returns the first call's orders (also when the calls race: the unique index decides and the loser re-reads); reusing a key for a different cart raises `IdempotencyConflict`; one `order.placed` event per order (`order_id`, `user_id`, `seller_id`, `total`) in the same savepoint as the orders. `bazaar.errors.constraint_name()` works for psycopg and asyncpg errors.
- `bazaar.outbox`: `record()`, `arecord()`, `insert_stmt()` (in the caller's transaction, payload ≤ 64 KB); `relay_batch()`/`arelay_batch()` (`FOR UPDATE SKIP LOCKED` in id order, a savepoint per publish, stop at the first failure and record it); `run_relay()` (LISTEN + polling, stops on an event); `jobs_publisher()` (events to Module 10's jobs, exactly once); `consume_once()` (idempotent consumer); `purge_published()`; `pending_count()`.

### 4. Observability (`bazaar.observability`)

- `JsonFormatter` (extra fields and the current `sql_context()` tags at the top level), `configure_logging()`.
- `sql_context(**tags)` (a `ContextVar`), `sqlcomment()` (sqlcommenter format, URL-encoded, sorted), `install_sqlcommenter(engine, static=...)` (escapes `%` for psycopg's paramstyle).
- `instrument_engine(engine, slow_ms=..., log_all=False)`: timings, `QueryStats` (count, errors, slow, max, histogram), slow-query `WARNING`s and failure `ERROR`s with SQLSTATE and kind, statement text without its comment, never parameter values.
- `pool_metrics()` for SQLAlchemy engines, psycopg pools and asyncpg pools; `check_health(conn)` (latency, primary, schema at head); `setup_tracing(service, engine=..., exporter=...)` with OpenTelemetry (optional extra `otel`).

### 5. Tests

- Fixtures: `admin_dsn`; `head_template` (a `TemplateDatabase` built by `alembic upgrade head`, fingerprinted by the revision files, built once under an advisory lock); `fresh_db` (a clone per test, named per xdist worker); `empty_db`. `make_shop()` in the factories works on an empty database.
- `test_resilience.py`: classification table (including a wrong password), SQLAlchemy-wrapped errors, business errors permanent, backoff bounds and spread, injected retries, give-up, permanent errors not retried, connection errors only when idempotent, deadline, async retry and cancellation; real failures in fresh databases: `SERIALIZABLE` write skew, deadlock, lock timeout, a failover-style disconnect with and without the pool's check, `InsufficientStock` not retried.
- `test_outbox.py`: one event per order with totals, none on failure, idempotent replay (stock reserved once, one event, `IdempotencyConflict`, keys per user), key length `CHECK`; relay order and once-only, stop at failure, four parallel relays, `jobs_publisher` exactly once (also on failure), at-least-once after a relay crash plus `consume_once()`, consumer failure, purge, `run_relay()` woken by `NOTIFY`.
- `test_observability.py`: comment format and escaping, context nesting, tags in `pg_stat_activity`, slow and failed query logging and stats, JSON lines with context, pool metrics, health check (also failing), read engine refuses writes, OpenTelemetry spans and `traceparent`.
- `test_concurrency.py`: 20 simultaneous buyers for 5 units (exactly 5 orders), opposite cart orders without deadlocks, 8 simultaneous retries of one checkout (one order, stock once, one event), pool exhaustion classified as overload.
- `test_containers.py` (opt-in, `BAZAAR_TEST_DOCKER=1`): the migrations build Bazaar in a new cluster, identical to the course database.
- The suite passes with `pytest -n 4`.

Update `project/python/README.md`: module table, settings table, layout, running instructions.

## Acceptance checks

```bash
cd project/python && source .venv/bin/activate
export BAZAAR_DSN=postgresql://postgres:postgres@localhost:5432/bazaar
alembic upgrade head && alembic current          # 2701 (head)
ruff check src tests migrations && ruff format --check src tests migrations
pytest -q                                        # 126 passed, 1 skipped (the container test)
pytest -q -n 4                                   # the same, about 2-2.5x faster
BAZAAR_TEST_DOCKER=1 pytest -q -m docker         # 1 passed (needs Docker)
python ../../modules/27-python-production/examples/outbox_demo.py
```

The outbox demo (inside a rolled-back transaction) must show two orders for a two-seller cart, the replay returning the same orders, one `order.placed` event per order with its total, the relay turning them into four jobs, and a duplicate delivery skipped:

```text
replay returned the same orders: True
...
relay published 2 messages; pending now: 0
...
delivery 1 of message 3: handler ran = True
delivery 2 of message 3: handler ran = False
```

(The order and message ids differ between runs.)
