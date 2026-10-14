# Module 27: Production Python and Postgres

On Black Friday 2025, Bazaar took 3,527 orders in one day. Somewhere in that day, the primary failed over at two in the morning and every pooled connection died at once; a buyer's phone lost signal after tapping "Pay" and the app retried; two checkouts for the last unit of a product arrived in the same millisecond; a report held a lock that a migration wanted; a slow query pinned the pool at its limit while requests queued; and an order confirmation email had to go out exactly once. Code that works on a laptop meets all of these in production. This module is about the code around the queries: tests that prove behaviour against a real PostgreSQL under concurrency, errors classified and retried correctly, timeouts at every layer, pools sized and configured for PgBouncer, events published reliably with the transactional outbox, and enough observability to know what happened afterwards.

**What you'll learn**

- Testing against real PostgreSQL: why not SQLite or mocks; fixture strategies (rolled-back transactions, template databases, a database per pytest-xdist worker, testcontainers); factories; tests that **force** concurrency with barriers and prove that checkout never oversells.
- Classifying database errors (transient versus permanent: `40001`, `40P01`, `55P03`, `57014`, `57P01`, `08xxx`, `25006`, pool timeouts) and retrying **whole transactions** with exponential backoff, full jitter, deadlines and idempotency.
- Timeouts at every layer (`connect_timeout`, `statement_timeout`, `lock_timeout`, `idle_in_transaction_session_timeout`, PostgreSQL 17's `transaction_timeout`, pool timeouts, `asyncio.timeout`), triggered on purpose, and how they interact.
- Connection management: pool sizing with Little's law, PgBouncer transaction mode with psycopg, asyncpg and SQLAlchemy, `SET LOCAL` versus `SET`, read replicas, health checks, graceful shutdown, fork safety.
- The **transactional outbox**: events written in the order's transaction, a relay with `SKIP LOCKED` and `LISTEN`/`NOTIFY`, at-least-once delivery, idempotent consumers; LISTEN/NOTIFY's limits.
- Observability: `application_name`, structured logs, slow-query logging through SQLAlchemy events, sqlcommenter tags and what `pg_stat_statements` does with them, OpenTelemetry, pool metrics.
- Security in the application (least-privilege DSNs, secrets, PII in logs), and a production readiness checklist.

**Before you start**

```bash
./project/bin/reset.sh 27                       # same database state as module 26
cd project/python && uv sync && source .venv/bin/activate
export BAZAAR_DSN=postgresql://postgres:postgres@localhost:5432/bazaar
alembic stamp 2600 && alembic upgrade head      # includes revision 2701 (this module's outbox)
pytest -n 4                                     # 125 tests, about 10 s
```

You need Module 26's migrations and `bazaar.testing`; the outbox builds on Module 10's job queue and Module 23's worker. The PgBouncer lab in [`lab/`](lab/) needs Docker (section 5.3). Outputs come from PostgreSQL 17.11, psycopg 3.3.6, asyncpg 0.32.0, SQLAlchemy 2.1.4, pytest-xdist 3.8.0, testcontainers 4.15.0 and OpenTelemetry 1.45.1.

---

## 1. Testing against real PostgreSQL

### 1.1 Why not SQLite, why not mocks

The tempting shortcut is to run the tests against SQLite ("it's SQL too") or to mock the database driver. For Bazaar, neither can test anything that matters:

- `place_order()` is a PL/pgSQL function with custom SQLSTATEs (`BZ001`...), `SELECT ... FOR UPDATE` in product id order, and deltas applied by a statement-level trigger with transition tables. SQLite has none of these.
- The job queue relies on `FOR UPDATE SKIP LOCKED`; seller isolation on row-level security and `SET LOCAL`; reviews on a partial unique index; promotions on an exclusion constraint; search on `tsvector` and `pg_trgm`. Every one of these is the *feature*, not an implementation detail.
- Concurrency semantics (`READ COMMITTED` re-evaluation, serialization failures, deadlocks, lock queues) only exist in the real engine. Section 1.8's tests found two bugs that a mock could never reproduce.
- A mock asserts that your code sent the SQL you *think* is right. It can't tell you that the SQL is wrong.

So every Bazaar test runs against PostgreSQL 17, the same major version as production. The cost is speed and isolation, and the rest of this section is about getting both back.

### 1.2 Fixture strategies

| Strategy | Isolation | Speed | Can test commits, several connections, NOTIFY, `CREATE INDEX CONCURRENTLY` | Used by Bazaar for |
|---|---|---|---|---|
| a transaction per test, rolled back | perfect, for what happens inside it | fastest (no cleanup) | no | most tests (Modules 22–25) |
| committed rows, deleted afterwards | depends on the cleanup | fast | yes | a few Module 23–25 tests |
| a **copy of a template database** per test | complete | about 140 ms per database | yes | `fresh_db`: concurrency, outbox, migrations |
| a database per pytest-xdist worker | per worker | once per worker | yes | worker-scoped names |
| a **container** per test session | complete, including roles and server settings | seconds | yes | `tests/test_containers.py` (opt-in) |

Most real suites mix them, as Bazaar's does: rollback for the bulk, fresh databases where commits are the point.

### 1.3 Rolled-back transactions, and their limits

Module 22's `conn` fixture and Module 25's `session` fixture wrap each test in a transaction that's rolled back at the end. Code under test that "commits" commits a savepoint (`join_transaction_mode="create_savepoint"`), so nothing escapes. What doesn't work inside such a test:

- **anything another connection must see**: a second session never sees uncommitted rows, so concurrency can't be tested;
- **`NOTIFY`**, which is delivered at commit;
- statements that can't run in a transaction block (`CREATE INDEX CONCURRENTLY`, `VACUUM`, `ALTER TYPE ... ADD VALUE` before PostgreSQL 12);
- **sequences**, which advance even when the transaction rolls back (ids in the reference database keep growing);
- code that checks `pg_stat_*` views, which count committed activity.

### 1.4 Template databases

`CREATE DATABASE new TEMPLATE tpl` creates a database as a copy of another one, at the file level: schema and data, in milliseconds to seconds, without replaying anything. PostgreSQL itself creates every database this way (from `template1`). For tests, build a template once (`alembic upgrade head`, plus seed rows if you want), then clone it for every test that needs a clean, committed-data database.

[`bazaar/testing.py`](../../project/python/src/bazaar/testing.py) wraps it:

```python
class TemplateDatabase:
    def ensure(self) -> bool:
        """Build the template if needed. Returns True if it was (re)built."""
        with _admin(self.admin_dsn) as lock:
            lock.execute("SELECT pg_advisory_lock(hashtext(%s))", (self.name,))
            try:
                if self.fingerprint and self._current_fingerprint() == self.fingerprint:
                    return False
                drop_database(self.admin_dsn, self.name)
                dsn = create_database(self.admin_dsn, self.name)
                self.build(dsn)
                ... COMMENT ON DATABASE <name> IS <fingerprint>
                return True
            finally:
                lock.execute("SELECT pg_advisory_unlock(hashtext(%s))", (self.name,))

    @contextmanager
    def clone(self, name: str, *, keep: bool = False) -> Iterator[str]:
        with scratch_database(self.admin_dsn, name, template=self.name, keep=keep) as dsn:
            yield dsn
```

and `tests/conftest.py` uses it:

```python
@pytest.fixture(scope="session")
def head_template(admin_dsn: str) -> TemplateDatabase:
    """An empty Bazaar schema at head, rebuilt only when a revision file changes."""
    tpl = TemplateDatabase(
        admin_dsn,
        "bazaar_test_template",
        build=lambda dsn: migrate.upgrade(dsn),
        fingerprint=migrations_fingerprint(migrate.PROJECT_DIR / "migrations" / "versions"),
    )
    tpl.ensure()
    return tpl


@pytest.fixture
def fresh_db(head_template: TemplateDatabase, request: pytest.FixtureRequest) -> Iterator[str]:
    """DSN of a brand-new database at head (empty tables), dropped after the test."""
    name = f"bazaar_test_{WORKER}_{abs(hash(request.node.nodeid)) % 10**8}"
    with head_template.clone(name) as dsn:
        yield dsn
```

Design points, each learned the hard way:

- **The fingerprint.** A hash of every revision file is stored as the template's `COMMENT`. The template survives between test runs (no 0.4 s rebuild every time) and is rebuilt automatically when a migration changes. A stale template is the classic source of "the tests pass on my machine".
- **A template must have no other connections** while it's copied (`source database "..." is being accessed by other users`). `build()` connects, works and disconnects; nobody else ever connects to the template.
- **Database-level settings and privileges aren't copied.** A clone gets the template's *contents*. `ALTER DATABASE ... SET search_path` and `GRANT CONNECT ON DATABASE` are attributes of the database object, stored in shared catalogs keyed by the database's OID. The first clones had no `search_path`, and every unqualified query failed. `create_database()` copies those attributes explicitly from `pg_db_role_setting` and `datacl`.
- **Parallel builders.** Under pytest-xdist, four workers call `ensure()` at once. A session advisory lock (Module 10) on the admin connection makes one build while the others wait, then find the fingerprint and reuse the template.

How much faster is it? [`examples/template_benchmark.py`](examples/template_benchmark.py) creates an empty Bazaar schema at head five times each way:

```text
alembic upgrade head   median     399 ms   ( 1.0x)
TEMPLATE (WAL_LOG)     median     138 ms   ( 2.9x)
TEMPLATE (FILE_COPY)   median     217 ms   ( 1.8x)
```

Since PostgreSQL 15, `CREATE DATABASE` copies a template block by block through the WAL by default (`STRATEGY WAL_LOG`); `FILE_COPY`, the old behaviour, copies files but has to checkpoint, which dominates for a small template. For the 2 GB reference database the order flips: `WAL_LOG` took 8.2 s, `FILE_COPY` 4.0 s. The gap to the migrations grows with every revision and every seed row the template contains: a template with Module 03's seed data would be a few hundred milliseconds to clone, against seconds to rebuild.

### 1.5 Parallel tests with pytest-xdist

`pytest -n 4` runs four worker processes, each with its own share of the tests. For that to be safe, tests must not interfere:

- Tests in rolled-back transactions on the shared reference database are safe in parallel: nothing they do is visible to others. (Two tests waiting for each other's row locks would be the exception: Bazaar's tests lock only rows they create.)
- Tests with committed data use `fresh_db`, whose name includes the worker id (`PYTEST_XDIST_WORKER`, `gw0`...), so two workers never share a scratch database.
- Session-scoped fixtures run once *per worker*: the template's advisory lock handles that.

```text
$ pytest -q
125 passed, 1 skipped in 25.03s
$ pytest -q -n 4
125 passed, 1 skipped in 9.93s
```

Not 4× (startup and the slowest tests set a floor), but a 2.5× speedup for one flag. The skipped test is the container test of the next section.

### 1.6 testcontainers: a server per test session

[testcontainers](https://testcontainers-python.readthedocs.io/) starts a Docker container from Python and gives you its connection URL; the container is removed afterwards. Isolation can't get stronger: a new cluster, with its own roles, settings and extensions. [`tests/test_containers.py`](../../project/python/tests/test_containers.py) uses it to test something no other test can, the baseline revision building Bazaar in a cluster where **no role exists yet**:

```python
@pytest.fixture(scope="module")
def pg_container() -> Iterator[str]:
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer(
        "postgres:17", username="postgres", password="postgres", dbname="bazaar", driver=None
    ) as pg:
        yield pg.get_connection_url()


def test_migrations_build_bazaar_in_a_new_cluster(pg_container: str) -> None:
    migrate.upgrade(pg_container)
    ...
    assert [r[0] for r in roles] == [
        "bazaar_analyst", "bazaar_app", "bazaar_owner", "bazaar_readonly", "bazaar_seller"
    ]
    assert not any(r[1] for r in roles)          # NOLOGIN: passwords are provisioning
    assert diff(want, got, labels=("course container", "testcontainer")) == []
```

```text
$ BAZAAR_TEST_DOCKER=1 pytest tests/test_containers.py -q --durations=3
1.99s setup    tests/test_containers.py::test_migrations_build_bazaar_in_a_new_cluster
0.44s teardown tests/test_containers.py::test_migrations_build_bazaar_in_a_new_cluster
0.42s call     tests/test_containers.py::test_migrations_build_bazaar_in_a_new_cluster
1 passed in 2.86s
```

Two seconds to start the server (with the image cached), and the catalog of the new cluster's database matched the course container's exactly. The test found one thing: with testcontainers' default superuser `test`, the baseline failed with `role "postgres" does not exist`, because the reference schema has one object owned by `postgres` (the extended statistics created in Module 12). A schema dump carries role names; a new environment needs the same roles.

The test is opt-in (`BAZAAR_TEST_DOCKER=1`, marker `docker`): it needs a Docker daemon, which a developer laptop has and many CI runners have, but a test run inside a container often doesn't. Typical uses: CI pipelines that have Docker but no database service; testing against several PostgreSQL versions (`postgres:16`, `postgres:18`) before an upgrade; testing server settings you can't change on a shared database.

### 1.7 Test data: factories

Tests against the reference database can use its notable rows (user 8 has many orders; Module 25's N+1 test relies on it). Tests in a fresh database start empty and need their own rows. [`tests/factories.py`](../../project/python/tests/factories.py) has small functions that insert valid rows and return ids, plus one that builds a whole shop:

```python
def make_shop(conn, *, products=2, stock=10, price=Decimal("10.00")) -> Shop:
    """Works on an EMPTY database (fresh_db) as well as on the reference one."""
    category = make_category(conn)
    buyer = make_user(conn)
    seller = make_seller(conn)
    pids = [make_product(conn, seller, stock=stock, price=price, category_id=category)
            for _ in range(products)]
    return Shop(buyer, make_address(conn, buyer), seller, pids)
```

Factories generate unique values (`uuid4().hex[:10]` in emails, SKUs and shop names), so tests never collide on unique constraints, and they take a connection, so they work inside a rolled-back transaction or with autocommit in a fresh database. Libraries like factory_boy add declarative definitions and ORM integration; for a schema whose rules live in the database (triggers fill `slug` and `total`), a few plain functions are enough.

### 1.8 Testing concurrency

A concurrency bug needs an interleaving to show up. A test that starts 20 tasks often runs them one after the other: each finishes before the next one gets going. **Barriers** make every task reach the critical point before any of them continues. [`tests/test_concurrency.py`](../../project/python/tests/test_concurrency.py):

```python
async def test_place_order_never_oversells(fresh_db: str) -> None:
    """20 buyers, 5 units, all calling place_order() at the same moment."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        shop = make_shop(c, products=1, stock=5)
    pid = shop.products[0]
    barrier = asyncio.Barrier(N)
    pool = await _asyncpg_pool(fresh_db)
    outcomes: list[str] = []

    async def buy() -> None:
        async with pool.acquire() as conn:
            await barrier.wait()  # everyone holds a connection; now go
            try:
                await conn.fetchval("SELECT place_order($1, $2::jsonb, NULL)", shop.buyer,
                                    f'[{{"product_id": {pid}, "quantity": 1}}]')
                outcomes.append("ok")
            except asyncpg.PostgresError as e:
                assert isinstance(translate(e), InsufficientStock), e
                outcomes.append("sold out")
    ...
    assert outcomes.count("ok") == 5 and outcomes.count("sold out") == N - 5
    assert (qty, reserved) == (5, 5)
```

Each task first acquires its own connection, *then* waits at the barrier, so all 20 `place_order()` calls are in flight together. Exactly 5 succeed; the inventory row's lock serializes them (Module 10). The same file proves that carts `[A, B]` and `[B, A]` placed concurrently never deadlock (`place_order()` locks rows in product id order), that eight simultaneous retries of one checkout with the same idempotency key create one order (section 3.2), and that a pool with fewer connections than requests fails fast with an overload error (section 2). For thread-based code, `threading.Barrier` does the same; `tests/test_resilience.py` uses it to force a write skew and a deadlock (section 3.5).

Two of this module's bugs were found exactly this way. The idempotent checkout first recognized the unique-index conflict only for psycopg's error objects; under asyncpg (the async ORM), the concurrent test failed with an unhandled `UniqueViolationError`. And Module 26's migration lock deadlocked with `CREATE INDEX CONCURRENTLY` only when two upgrade processes ran at the same moment.

---

## 2. Failure is normal: classifying database errors

Everything that talks to a database over a network fails sometimes. The question is never "can it fail?" but "what does this particular failure mean, and what should the code do?". [`bazaar/resilience.py`](../../project/python/src/bazaar/resilience.py) answers it in one place:

| Kind | SQLSTATE / exception | Typical cause | Retry? |
|---|---|---|---|
| `serialization` | `40001` serialization_failure, `40P01` deadlock_detected | concurrent transactions; the server rolled yours back | yes: the whole transaction |
| `lock_timeout` | `55P03` lock_not_available | `lock_timeout` or `NOWAIT` | yes, with backoff |
| `connection` | `57P01` admin_shutdown, `57P02` crash_shutdown, `57P03` cannot_connect_now, `25P03` idle-in-transaction timeout, class `08` (connection exceptions), psycopg `OperationalError` without a SQLSTATE, asyncpg `ConnectionDoesNotExistError` | failover, restart, network, a killed session | only if the work is idempotent (the commit may have happened) |
| `read_only` | `25006` read_only_sql_transaction | connected to a node that's now a standby | only if idempotent, on a new connection |
| `timeout` | `57014` query_canceled, `25P04` transaction_timeout (PG 17), `TimeoutError` | `statement_timeout`, a cancel, `asyncio.timeout` | no (by default): the work is too slow, retrying adds load |
| `overloaded` | `53300` too_many_connections, `53200` out_of_memory, `53100` disk_full, `PoolTimeout`, SQLAlchemy's pool `TimeoutError` | demand exceeds capacity | no: shed load |
| `permanent` | everything else: `23505` unique_violation, `42xxx` syntax/undefined, `BZ001` insufficient stock, ... | a bug or a business rule | no |

```python
def classify(exc: BaseException) -> ErrorKind:
    """What kind of failure `exc` is, for psycopg, asyncpg and SQLAlchemy errors."""
    inner = _unwrap(exc)                     # SQLAlchemy's DBAPIError.orig
    code = getattr(inner, "sqlstate", None)  # psycopg and asyncpg both have .sqlstate
    if code:
        if code in _SQLSTATES:
            return _SQLSTATES[code]
        if code.startswith("08"):  # connection_exception class
            return ErrorKind.CONNECTION
        return ErrorKind.PERMANENT
    name = type(inner).__name__
    ...
    if name == "PoolTimeout" or (module.startswith("sqlalchemy") and name == "TimeoutError"):
        return ErrorKind.OVERLOADED
    if getattr(exc, "connection_invalidated", False):
        return ErrorKind.CONNECTION
    mro = {c.__name__ for c in type(inner).__mro__}
    if mro & {"OperationalError", "InterfaceError", "ConnectionDoesNotExistError", ...}:
        return ErrorKind.CONNECTION
    if isinstance(inner, TimeoutError):  # asyncio.timeout(); before OSError (a subclass)
        return ErrorKind.TIMEOUT
    if isinstance(inner, ConnectionError | OSError):
        return ErrorKind.CONNECTION
    return ErrorKind.PERMANENT
```

Classify by **SQLSTATE**, not by exception class or message: SQLSTATEs are stable across drivers and versions, and both drivers expose them (`exc.sqlstate`). Messages are translated (`lc_messages`) and change between versions. Errors without a SQLSTATE come from the client side: a dropped socket, a refused connection, a pool or client timeout. Two traps from writing this function: `TimeoutError` is a subclass of `OSError`, so it must be checked first; and psycopg's `ConnectionTimeout` (the `connect_timeout` expiring) is a *subclass* of `OperationalError`, which only the MRO check catches. `tests/test_resilience.py` pins down 21 cases, plus SQLAlchemy-wrapped errors from real statements.

### 2.1 Failover, as the application sees it

Module 20 measured what clients see during a failover: `57P01` for the sessions a clean shutdown terminates, `08006` for connections that just die, `57P03` while the new primary starts, `25006` for a connection that ended up on a node that's now a standby. `pg_terminate_backend()` produces the first one on demand, which is how `test_failover_style_disconnect` simulates a failover against a one-connection psycopg pool:

```python
for check in (True, False):
    caplog.clear()
    kw = {"check": psycopg_pool.ConnectionPool.check_connection} if check else {}
    with psycopg_pool.ConnectionPool(fresh_db, min_size=1, max_size=1, **kw) as pool:
        kill_pool_connections(pool)
        assert run_transaction(pool, read_stock, FAST, idempotent=True) == 3
    retried = [r for r in caplog.records if "retrying after connection" in r.getMessage()]
    assert len(retried) == (0 if check else 1), f"check={check}"
```

With the pool's **check on checkout** (`ConnectionPool.check_connection`, which `bazaar.db.create_pool()` configures; SQLAlchemy's equivalent is `pool_pre_ping=True`), the dead connection is noticed and replaced before the work starts: no error, no retry. Without it, the first attempt fails with `57P01`, `classify()` says `connection`, the read is idempotent, and the retry gets a fresh connection:

```text
WARNING  psycopg.pool:pool.py:730 discarding closed connection: <psycopg.Connection [BAD] at 0x7fbb71cd7f20>
WARNING  bazaar.resilience:resilience.py:166 retrying after connection (attempt 1, sqlstate 57P01): sleeping 0.001s
```

The check costs a round trip per checkout and still can't catch a connection that dies *between* the check and the query, so you need both: the check for the common case (all idle connections died together), retries for the rest.

---

## 3. Retrying correctly

### 3.1 Retry whole transactions

After `40001`, `40P01` or a lost connection, the transaction is gone. Retrying only the failed statement doesn't work:

```text
first: DivisionByZero
retry in the same transaction: InFailedSqlTransaction - current transaction is aborted, commands ignored until end of transaction block
```

and even when the statement *could* be retried (a new connection), everything the transaction read before it is stale: the stock level it checked, the count it computed. The unit of retry is the **whole transaction, from `BEGIN`, re-reading everything** (Module 10). That's why the helpers that own the transaction are the retry boundary:

```python
def run_transaction(pool, work, policy=DEFAULT_POLICY, *, idempotent=False):
    """work(conn) in a transaction on a connection from a psycopg ConnectionPool,
    retried as a whole."""

    def attempt():
        with pool.connection() as conn, conn.transaction():
            return work(conn)

    return call_with_retry(attempt, policy, idempotent=idempotent)


def run_in_session(factory, work, policy=DEFAULT_POLICY, *, idempotent=False):
    """work(session) in `with factory.begin() as session:`, retried as a whole:
    every attempt gets a new Session, so no stale objects leak into the next one."""
```

with async twins `arun_transaction()` (psycopg's `AsyncConnectionPool` or an asyncpg pool) and `arun_in_session()` (`async_sessionmaker`). For the ORM, a new `Session` per attempt matters: the failed attempt's identity map holds objects loaded in a transaction that no longer exists.

The decorator form, `@retry(policy)` / `@aretry(policy)`, is for functions that open and commit their own transaction. Don't decorate a function that runs inside its caller's transaction: it would retry inside an aborted one.

### 3.2 The unknown commit, and idempotency

The connection can die after `COMMIT` was sent and before the reply arrived. Did the order commit? The client can't know (Module 20, section 14.2, saw it happen in a `kill -9` drill). Retrying may create a second order; not retrying may tell the user "failed" for a purchase that went through. Hence the rule in `RetryPolicy`: `connection` and `read_only` errors are retried **only when the caller says the work is idempotent**:

```python
@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 5
    base_delay: float = 0.05
    max_delay: float = 2.0
    deadline: float | None = 10.0
    retry_on: frozenset[ErrorKind] = frozenset({SERIALIZATION, LOCK_TIMEOUT})
    idempotent_retry_on: frozenset[ErrorKind] = frozenset({CONNECTION, READ_ONLY})
```

A read is idempotent. A write becomes idempotent with an **idempotency key**: a value the client chooses per logical request (a UUID generated when the user opens the checkout page) and sends with every attempt. Module 20's lab stored it in a side table; Bazaar's package stores it on the orders themselves (`orders.idempotency_key`, revision 2604, unique per `(user_id, idempotency_key, seller_id)`), and `services.checkout()` uses it:

```python
def checkout(session, user_id, items, shipping_address_id=None, *,
             idempotency_key=None, emit_events=True) -> list[Order]:
    if idempotency_key is not None:
        existing = list(session.scalars(_keyed_orders(user_id, idempotency_key)))
        if existing:
            return _replay(existing, items, idempotency_key)     # the earlier attempt committed
    try:
        with session.begin_nested():
            call = _place_order_call(user_id, items, shipping_address_id)
            order_ids = list(session.scalars(call))
            for stmt in _tag_and_record(order_ids, idempotency_key, emit_events):
                session.execute(stmt)                             # key on the orders + outbox events
    except IntegrityError as e:
        if idempotency_key is None or not _is_key_conflict(e):
            raise
        # A concurrent request with the same key committed first.
        existing = list(session.scalars(_keyed_orders(user_id, idempotency_key)))
        return _replay(existing, items, idempotency_key)
    except DBAPIError as e:
        _reraise_translated(e)
    return list(session.scalars(_orders_query(Order.id.in_(order_ids))))
```

Three cases:

1. **First attempt**: no orders with this key; `place_order()` creates them, the key is written onto them, all in one transaction.
2. **A retry after an unknown commit**: the first query finds the orders; they're returned as if just created. `_replay()` also checks that the cart is the same, and raises `IdempotencyConflict` when a key is reused for a different request (a client bug, best reported loudly).
3. **Two attempts at the same time** (the client timed out and retried while the first was still running): both pass the first check; the second one's `UPDATE ... SET idempotency_key` waits on the unique index entry the first one created, then fails with a unique violation once the first commits. Its savepoint rolls back (including its stock reservation), and it reads the first one's orders.

The concurrency test runs case 3 with eight tasks through the async ORM:

```python
async def attempt() -> list[int]:
    async with Session() as session, session.begin():
        await session.execute(sa.text("SELECT 1"))  # connection checked out
        await barrier.wait()
        orders = await services.checkout_async(
            session, shop.buyer, cart, shop.address, idempotency_key="retry-me-0001"
        )
        return [o.id for o in orders]
...
assert n_orders == 1  # both products belong to the same seller: one order
assert len({tuple(r) for r in results}) == 1
assert reserved == 3 and events == 1
```

One order, the same ids returned to all eight callers, stock reserved once, one outbox event. Its first run failed: the conflict check read `e.orig.diag.constraint_name`, which exists on psycopg's exceptions, while asyncpg (under SQLAlchemy's async engine) reports `constraint_name` on its own exception, wrapped once more. `bazaar.errors.constraint_name()` now handles both drivers.

With idempotent writes, the retry policy for checkout can include connection errors safely: `run_in_session(Session, lambda s: services.checkout(s, ..., idempotency_key=key), idempotent=True)`.

### 3.3 Backoff with full jitter, within a deadline

When many clients fail together (a failover drops every connection at once; a hot row makes 50 transactions conflict), retrying immediately makes them collide again. **Exponential backoff** waits longer after each failure; **jitter** randomizes the wait so the clients spread out:

```python
def backoff(self, attempt: int) -> float:
    """Full jitter: uniform(0, min(max_delay, base_delay * 2**(attempt-1)))."""
    return random.uniform(0, min(self.max_delay, self.base_delay * 2 ** (attempt - 1)))
```

"Full jitter" (a uniform draw between zero and the exponential ceiling) spreads retries best for the total work done; adding a small random amount to a fixed delay keeps the clients in lockstep. The test checks the bounds and the spread over 2,000 draws per attempt, with `base_delay=0.1` and `max_delay=1.0`: attempt 1 within 0–100 ms, attempt 4 within 0–800 ms, from attempt 5 capped at 1 s, and the draws cover the whole range.

A **deadline** bounds the total: `call_with_retry()` doesn't start a retry that would end after `deadline` seconds. In a web request, make it the time left in the request's budget; there's no point in succeeding after the client gave up.

```python
def _next_delay(policy, attempt, exc, idempotent, started) -> float | None:
    if attempt >= policy.attempts or not policy.should_retry(exc, idempotent=idempotent):
        return None
    delay = policy.backoff(attempt)
    if policy.deadline is not None and time.monotonic() - started + delay > policy.deadline:
        return None
    return delay
```

The async version retries on `Exception`, never on `asyncio.CancelledError`, which is a `BaseException` since Python 3.8: a cancelled request must stop, not retry. `test_async_retry_and_cancellation` wraps a retried coroutine in `asyncio.timeout(0.05)` and checks that it ran exactly once.

### 3.4 Retries with real failures

Injected exceptions test the policy; real ones test the assumptions. `tests/test_resilience.py` has both:

- **A write skew under `SERIALIZABLE`.** The rule "a seller may run at most 2 promotions" is checked in the application: count, then insert. Two transactions (threads, held together by a `threading.Barrier` after their count) both see 1 promotion and both insert. Under `SERIALIZABLE`, PostgreSQL detects the dangerous structure and fails one with `40001`; `call_with_retry()` reruns it, the rerun counts 2 and refuses. Result: `["added", "limit reached"]`, 3 attempts in total, 2 promotions.
- **A deadlock.** Two transactions update two inventory rows in opposite orders, with a barrier between their first and second update. PostgreSQL detects the deadlock after `deadlock_timeout` (1 s), kills one with `40P01`, the retry succeeds, and the stock sum is unchanged: 3 attempts, about 1.1 s.
- **A lock timeout.** Another connection holds a row lock for 300 ms; the work runs with `lock_timeout = '50ms'` and is retried until the lock is released.
- **Business errors aren't retried.** `place_order()` raising `BZ001` (insufficient stock) runs exactly once and surfaces as `InsufficientStock`.

### 3.5 What not to retry, and how much

- **Timeouts.** A query killed by `statement_timeout` will most likely be killed again; retrying multiplies the load at the worst moment. Fix the query or raise the timeout for that path.
- **Overload.** A `PoolTimeout` means requests already wait for connections. Retrying adds more waiters. Return an error (HTTP 503 with `Retry-After`) and let the load balancer or the client back off. `test_pool_exhaustion_is_overload_not_retried` runs four requests against a two-connection pool with a 0.2 s pool timeout: two succeed, two fail fast, both classified `overloaded`.
- **Retries multiply.** If the API gateway retries 3 times, the service 3 times and the database helper 5 times, one user click can become 45 attempts. Retry at **one** layer, the one that owns the transaction, and give the others a deadline.
- **Log and count retries.** `_log_retry()` logs each one at `WARNING` with the kind, attempt and SQLSTATE as structured fields. A rising retry rate is often the first sign of contention or an unstable primary.

---

## 4. Timeouts at every layer

A request that waits forever holds a pool connection forever, and enough of those take the service down. Every layer that can wait needs a limit, and the limits must fit inside each other:

```text
 client / load balancer request timeout          30 s   (the user gives up)
 └─ request deadline in the app (asyncio.timeout)   10 s
    ├─ pool checkout timeout                         10 s   BAZAAR_POOL_TIMEOUT
    ├─ connect_timeout                                5 s
    └─ per statement on the server:
       ├─ statement_timeout                           5 s   BAZAAR_STATEMENT_TIMEOUT
       │  └─ lock_timeout                        0 / 1 s   BAZAAR_LOCK_TIMEOUT
       ├─ idle_in_transaction_session_timeout       30 s   BAZAAR_IDLE_TX_TIMEOUT
       └─ transaction_timeout (PG 17)             (unset)
```

[`examples/timeouts.py`](examples/timeouts.py) triggers each one on purpose:

```text
statement_timeout = 200ms          after  0.22s  QueryCanceled sqlstate=57014 kind=timeout: canceling statement due to statement timeout
lock_timeout = 300ms               after  0.33s  LockNotAvailable sqlstate=55P03 kind=lock_timeout: canceling statement due to lock timeout
idle_in_transaction = 300ms        after  0.61s  IdleInTransactionSessionTimeout sqlstate=25P03 kind=connection: terminating connection due to idle-in-transaction timeout
transaction_timeout = 500ms (PG17) after  0.51s  TransactionTimeout sqlstate=25P04 kind=timeout: terminating connection due to transaction timeout
                                         (server still running the query: 0)
asyncio.timeout(0.3)               after  0.32s  TimeoutError sqlstate=None kind=timeout: 
pool timeout = 0.5s                after  0.51s  PoolTimeout sqlstate=None kind=overloaded: couldn't get a connection after 0.50 sec
connect_timeout = 2s               after  2.00s  ConnectionTimeout sqlstate=None kind=connection: connection timeout expired
```

- **`statement_timeout`** cancels a statement that runs too long, whatever it's doing. The transaction is aborted; the session survives. It also covers lock waits, unless `lock_timeout` is shorter.
- **`lock_timeout`** cancels a statement that *waits for a lock* too long. Shorter than `statement_timeout`, it turns "blocked" into a distinct, retryable error (`55P03`) instead of a generic timeout. Bazaar's default is 0 (off) for the application, because `place_order()` legitimately waits for inventory row locks under contention; set it per transaction where failing fast is better (`SET LOCAL lock_timeout = '1s'`), and always for migrations (Module 26).
- **`idle_in_transaction_session_timeout`** ends the **session** of a connection that sat in an open transaction (the classic leaked transaction, Module 22, section 3.4) holding locks and blocking VACUUM. The client finds out on its next statement (`25P03`, a connection error). Bazaar sets 30 s.
- **`transaction_timeout`** (new in PostgreSQL 17) limits a whole transaction, however many short statements it has. The demo ran ten 100 ms statements against a 500 ms limit; the fifth was interrupted, and the session was terminated (`25P04`), not just the transaction. Useful as a backstop for batch jobs; too blunt as a default for request handlers.
- **`asyncio.timeout()`** is the client-side deadline. When it fires during `await conn.execute(...)`, psycopg's async connection sends a **cancel request** to the server, so the server stopped working too (`server still running the query: 0`). Without the cancel, the server would keep running a query whose result nobody will read, holding a pool connection. (Module 23, section 3.4, covered the mechanics for both drivers.)
- **Pool timeout**: how long a request waits for a free connection. It must be shorter than the request deadline, and its failures mean overload (section 3.5).
- **`connect_timeout`**: without it, connecting to an address that drops packets waits for the operating system's TCP timeout, often more than a minute. After a failover, the old primary's address often does exactly that.

The server-side timeouts are set once per connection, at connect time, from `Settings.session_settings()`:

```python
def session_settings(self) -> dict[str, str]:
    out = {"application_name": self.app_name}
    for name, value in (
        ("statement_timeout", self.statement_timeout_ms),
        ("lock_timeout", self.lock_timeout_ms),
        ("idle_in_transaction_session_timeout", self.idle_in_transaction_timeout_ms),
    ):
        if value:
            out[name] = str(value)
    return out
```

psycopg and SQLAlchemy's psycopg dialect send them as the libpq `options` startup parameter (`-c statement_timeout=5000 -c idle_in_transaction_session_timeout=30000`); asyncpg as `server_settings`. Startup parameters cost no round trip, but there's a catch behind PgBouncer (section 5.3). Two more layers worth knowing: **TCP keepalives** (`keepalives_idle=30` in libpq, `tcp_keepalives_idle` on the server) detect a peer that vanished without closing the connection, and **per-role defaults** (`ALTER ROLE bazaar_app SET statement_timeout = '5s'`) apply whatever the client sends.

---

## 5. Connection management in production

### 5.1 How big should the pool be?

**Little's law**: the average number of requests *in* a system equals the arrival rate times the average time each spends there, L = λ × W. For a pool, L is the number of connections busy on average, λ the rate of transactions, W how long each holds a connection.

A checkout in the reference package (`services.checkout()`: `place_order()`, the outbox insert, loading the orders) measured on this machine:

```text
checkout: median 6.6 ms, p95 8.6 ms, p99 11.3 ms
```

At Black Friday's peak, say 50 checkouts per second plus 1,000 product-page queries at 1 ms: L = 50 × 0.0066 + 1000 × 0.001 ≈ 0.33 + 1.0 = **1.3 connections busy on average**. Pools are sized for bursts, not averages: with arrivals bunching up, a pool of 4–5 per process rarely makes anyone wait. Compare that with "max_size = 100, just in case".

The other side of the calculation is the server. Module 17's rule: the number of connections **actively running** queries should stay around 2–4× the CPU cores. Beyond that, adding connections adds context switching and lock contention, not throughput. So:

```text
processes × pool size  ≤  what the server can run efficiently (≈ 2–4 × cores, e.g. 16–32 on 8 cores)
                       ≤  max_connections − reserved (superuser, replication, monitoring, migrations)
```

Ten application processes with `max_size=10` can open 100 connections that are mostly idle. If that's more than the server should run, put **PgBouncer** in front (Module 17): clients get their 100 connections, the server sees 20. And what usually fills a pool is not traffic but **time held**: a slow query, or a transaction kept open across a call to a payment provider, raises W for everyone. Measure W (section 8) before raising the pool size.

### 5.2 One pool per process, and its settings

- One pool per process, created at startup (inside the event loop for async pools), closed at shutdown. In a process that uses SQLAlchemy, the engine's pool is the pool; don't stack it on `psycopg_pool` (Module 24).
- `pool_pre_ping=True` (SQLAlchemy) / `check=ConnectionPool.check_connection` (psycopg): one round trip per checkout to catch dead connections (section 2.1).
- `pool_recycle=1800`: replace connections after 30 minutes, so that server-side memory (catalog caches, prepared statements) doesn't grow forever and a connection pinned to an old node after a DNS change eventually moves.
- `max_overflow=0` with `pool_size=max`: a hard ceiling you sized on purpose (Module 24's `_pool_args()`).
- Behind PgBouncer in transaction mode: `poolclass=NullPool` (PgBouncer is the pool; an application pool on top holds server-side slots idle) or a small application pool to save the connect round trip to PgBouncer. Either works; two big pools stacked is what fails.

### 5.3 PgBouncer in transaction mode, with this package

Module 17 explained transaction pooling and what breaks in it; Module 23 measured prepared statements through it. [`lab/run_pgbouncer.sh`](lab/run_pgbouncer.sh) starts PgBouncer 1.26 in transaction mode with **two** server connections, and [`examples/pgbouncer_gotchas.py`](examples/pgbouncer_gotchas.py) runs the Bazaar package through it as `bazaar_app`:

```bash
cd modules/27-python-production/lab && ./run_pgbouncer.sh          # strict
cd project/python && python ../../modules/27-python-production/examples/pgbouncer_gotchas.py
```

```text
--- 1. our timeouts travel as a startup parameter: options='-c statement_timeout=5000 ...'
refused: connection failed: connection to server at "127.0.0.1", port 56427 failed: FATAL:  unsupported startup parameter in options: statement_timeout

--- 2. the fix: defaults on the role, applied when PgBouncer opens a server connection
SHOW statement_timeout = 5s

--- 3. session state: SET vs SET LOCAL, through 2 shared server connections
SET:       later requests see app.seller_id = ['42', '42', '42', '42']
SET LOCAL: later requests see app.seller_id = ['', '', '', '']

--- 4. SQLAlchemy + asyncpg (prepares every statement) with max_prepared_statements=200
200 queries from 10 clients over 2 server connections: 0 errors
```

1. **Startup options.** PgBouncer opens server connections itself and shares them between clients, so it can't apply one client's `-c statement_timeout=...`. By default it refuses the connection. The common "fix" found online, `ignore_startup_parameters = options`, makes it worse: run again with `./run_pgbouncer.sh ignore`, and the connection succeeds with **no timeouts at all**:

   ```text
   --- 1. our timeouts travel as a startup parameter: options='-c statement_timeout=5000 ...'
   connected; SHOW statement_timeout = 0
             SHOW idle_in_transaction_session_timeout = 0
   ```

   The application thinks it's protected by a 5-second limit and isn't.
2. **The fix**: put the defaults on the **role** (or the database): `ALTER ROLE bazaar_app SET statement_timeout = '5s'` (likewise `idle_in_transaction_session_timeout`, `lock_timeout`). PostgreSQL applies them when PgBouncer's server connection logs in as `bazaar_app`. Set `BAZAAR_STATEMENT_TIMEOUT=0` etc. in the application so it sends no options. Exceptions for one transaction go through `SET LOCAL`.
3. **Session state leaks.** "Request 1" ran `SET app.seller_id = '42'` (session level) and committed. Four later requests, with new client connections, all saw `42`: they got the same server connection. With Module 15's RLS, that's seller 42's data shown to someone else. `SET LOCAL` (or `set_config(..., true)`, which `BazaarSession` and `seller_scope()` use) disappears at commit and never leaks. (After a `SET LOCAL`, the setting reads as `''` rather than NULL: the placeholder variable now exists, empty. `current_seller_id()` must treat both as "no seller".)
4. **Prepared statements.** asyncpg prepares every statement; with PgBouncer 1.21+ and `max_prepared_statements > 0` (200 is the default since 1.24), PgBouncer re-prepares them on whichever server connection a transaction lands on. 200 queries through 2 server connections, no errors. With an older pooler: `prepared_statement_cache_size=0` for SQLAlchemy's asyncpg dialect, `prepare_threshold=None` for psycopg (Module 23, section 4.8).

What else doesn't survive transaction pooling: `LISTEN` (a listener must hold its connection, so the outbox relay of section 6 connects directly, or through a session-mode pool), session advisory locks (Module 26's migration lock and the template builder use direct connections), temporary tables across transactions, `WITH HOLD` cursors.

### 5.4 Read replicas

Module 19 suggested two pools: one to the primary, one to replicas for reads that may be slightly stale. `bazaar.sa.engine.create_read_engine()`:

```python
def create_read_engine(settings=None, **overrides) -> Engine:
    s = settings or get_settings()
    s = replace(s, dsn=s.replica_dsn or s.dsn, app_name=f"{s.app_name}-ro")
    engine = create_engine(s, **overrides)

    @event.listens_for(engine, "connect")
    def _read_only(dbapi_conn, record) -> None:
        autocommit = dbapi_conn.autocommit
        dbapi_conn.autocommit = True
        dbapi_conn.execute("SET default_transaction_read_only = on")
        dbapi_conn.autocommit = autocommit

    return engine
```

It connects to `BAZAAR_REPLICA_DSN` when set, else to the primary, and in both cases **read-only**, so code that accidentally writes through it fails in development too, instead of only in production where the replica refuses:

```text
sqlalchemy.exc.InternalError: (psycopg.errors.ReadOnlySqlTransaction) cannot execute UPDATE in a read-only transaction
```

(`test_read_engine_refuses_writes` checks the `25006`.) Read-your-own-writes paths (the order confirmation page right after checkout) must use the primary: a replica may lag (Module 19, section 9.2).

### 5.5 Health checks

Orchestrators ask two questions. **Liveness**: is the process alive, or should it be restarted? Don't touch the database for that: when the database is down, restarting every application process doesn't help and makes recovery slower. **Readiness**: should this process receive traffic? That one should check the database, *for this service's needs*:

```python
def check_health(conn, *, require_primary=True, check_migrations=True) -> dict[str, Any]:
    t0 = time.perf_counter()
    in_recovery = conn.execute(sa.text("SELECT pg_is_in_recovery()")).scalar_one()
    out = {"latency_ms": round((time.perf_counter() - t0) * 1000, 2), "in_recovery": in_recovery}
    problems = []
    if require_primary and in_recovery:
        problems.append("connected to a standby")
    if check_migrations:
        current, heads = migrate.current_revisions(conn), migrate.head_revisions()
        out["schema"] = list(current)
        if set(current) != set(heads):
            problems.append(f"schema at {list(current)}, code expects {list(heads)}")
    out["ok"] = not problems
    out["problems"] = problems
    return out
```

Reachable (with the latency), connected to a primary (after a failover, a pool can still point at the demoted node), schema at the revision the code expects (Module 26). For an `AsyncConnection`: `await conn.run_sync(check_health)`. Run it on a pooled connection with a short timeout, and cache the result for a second or two: a health endpoint polled every second by three load balancers shouldn't become a database load of its own.

### 5.6 Graceful shutdown

On deploy, the orchestrator sends `SIGTERM`, waits (Kubernetes: 30 s by default), then `SIGKILL`. In those seconds:

1. Stop accepting new work: fail readiness, stop the listener (the web server does this on `SIGTERM`).
2. Finish in-flight requests, within their deadlines. Module 23's worker finishes the job in hand and leaves unstarted ones in the queue (its `test_graceful_stop_finishes_job_in_hand`).
3. Close the pools: `pool.close()` (psycopg), `await engine.dispose()` (SQLAlchemy), `await pool.close()` (asyncpg). This closes server connections cleanly instead of leaving PostgreSQL to notice dead sockets later.

A process killed in the middle of a transaction is not a disaster: PostgreSQL rolls back. It's the work *around* the database (a payment captured, an email sent) that needs the outbox (section 6) and idempotency to stay consistent.

### 5.7 Fork safety

gunicorn with `--preload`, `multiprocessing`, and Celery's prefork workers create processes with `fork()`. A pool created before the fork is copied into every child, with its **sockets**: parent and children then talk over the same PostgreSQL connections, interleaving protocol messages, and the results are corrupt data or "lost synchronization with server" errors. The rules:

- Create pools and engines **after** the fork: in the worker's startup hook (gunicorn `post_fork`, FastAPI's `lifespan`, which runs in each worker). The Module 28 service does this.
- If an engine must exist before the fork, make the child drop its inherited connections without closing them (closing would send a Terminate message on the parent's behalf): `bazaar.sa.engine.dispose_after_fork(engine)` registers `engine.dispose(close=False)` with `os.register_at_fork(after_in_child=...)`.
- asyncio event loops and async pools don't survive a fork at all; create them in the child.

---

## 6. The transactional outbox

### 6.1 The dual-write problem

After checkout, Bazaar must tell other systems: send the confirmation email, notify the seller, update analytics, maybe call a warehouse API. The naive code writes the order and then publishes a message (to a broker, a webhook, a queue). That's two systems, and no transaction spans both:

| Order of operations | Failure in between | Result |
|---|---|---|
| commit the order, then publish | process dies, broker down | order without event: no email, seller never notified |
| publish, then commit | commit fails (constraint, `40001`, lost connection) | event without order: email for an order that doesn't exist |
| publish inside the open transaction | rollback after publishing | the same, and the transaction was held open during a network call |

Module 23 avoided it for one consumer by enqueueing a job in the order's transaction. The **transactional outbox** generalizes that: write the *event* into a table in the same transaction as the change, and let a separate process deliver it afterwards. The event exists if and only if the order does.

### 6.2 The outbox table (revision 2701)

```python
op.create_table(
    "outbox",
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("topic", sa.Text, nullable=False),            # "order.placed"
    sa.Column("aggregate_type", sa.Text, nullable=False),   # "order"
    sa.Column("aggregate_id", sa.Text, nullable=False),     # "500215"
    sa.Column("payload", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("headers", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("created_at", ..., server_default=sa.func.now()),
    sa.Column("published_at", sa.DateTime(timezone=True)),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("last_error", sa.Text),
    sa.CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
)
op.create_index("outbox_unpublished_idx", "outbox", ["id"],
                postgresql_where=sa.text("published_at IS NULL"))
```

plus a statement-level `AFTER INSERT` trigger that sends `NOTIFY outbox` (no payload, a wake-up call), and the consumer-side table `processed_messages (consumer, message_id)` (section 6.4). New, empty tables: plain `CREATE TABLE` and `CREATE INDEX` are fine (nobody can be blocked on a table nobody uses yet). The tables were created as `bazaar_owner`, so `bazaar_app` got DML on them through Module 15's default privileges, without a single `GRANT` in the revision.

The **partial index** on unpublished ids keeps the relay's query cheap however many published rows the table keeps: it contains only the pending ones, usually a handful.

`services.checkout()` writes one `order.placed` event per order inside its savepoint, with an `INSERT ... SELECT` from the new orders (so the payload has the total the trigger computed):

```python
sa.insert(outbox_table).from_select(
    ["topic", "aggregate_type", "aggregate_id", "payload"],
    sa.select(
        sa.literal("order.placed"), sa.literal("order"), sa.cast(o.c.id, sa.Text),
        sa.func.jsonb_build_object("order_id", o.c.id, "user_id", o.c.user_id,
                                   "seller_id", o.c.seller_id, "total", sa.cast(o.c.total, sa.Text)),
    ).where(o.c.id.in_(order_ids)).order_by(o.c.id),
)
```

For other producers, `bazaar.outbox.record(conn, topic, aggregate_type, aggregate_id, payload)` (psycopg), `arecord()` (async psycopg) and `insert_stmt()` (any SQLAlchemy connection or session, sync or async) all insert in the **caller's** transaction and never commit. Tests prove both directions: a successful checkout records exactly one event per order (with the right totals), and a checkout that fails with `InsufficientStock` records none.

### 6.3 The relay

```python
_CLAIM = """
SELECT id, topic, aggregate_type, aggregate_id, payload, headers, created_at, attempts
FROM outbox
WHERE published_at IS NULL
ORDER BY id
LIMIT %s
FOR UPDATE SKIP LOCKED
"""

def relay_batch(conn, publish, limit=100) -> int:
    done: list[int] = []
    with conn.transaction():
        for row in conn.execute(_CLAIM, (limit,)).fetchall():
            msg = Message(*row)
            try:
                with conn.transaction():  # a savepoint: a failing publish undoes only itself
                    publish(msg)
            except Exception as exc:
                log.warning("publishing outbox message %d failed: %s", msg.id, exc)
                if done:
                    conn.execute(_DONE, (done,))
                conn.execute(_FAILED, (f"{type(exc).__name__}: {exc}"[:1000], msg.id))
                return len(done)
            done.append(msg.id)
        if done:
            conn.execute(_DONE, (done,))
    return len(done)
```

- **`FOR UPDATE SKIP LOCKED`** (Module 10's job queue pattern): several relay processes can run side by side; each claims different rows. `test_parallel_relays_never_publish_twice` runs four relays over 200 messages: 200 published, 200 distinct.
- **Order.** Messages are claimed in id order, and on the first failure the batch **stops**: later messages wait, so they aren't published before an earlier one. That's head-of-line blocking, the price of ordering. Ordering usually matters only per aggregate ("order.placed" before "order.shipped" for the same order); a relay can partition by `aggregate_id` to keep one stuck order from blocking everything.
- **The failure is recorded** (`attempts`, `last_error`) in the same transaction, for monitoring.
- **A savepoint per publish**, so a publisher that writes to the database (next section) and fails half-way leaves nothing behind.

`run_relay()` is the long-running loop: one connection `LISTEN`s on `outbox`, another does the work; the loop drains the table, then sleeps until a notification arrives, the poll interval passes, or it's told to stop:

```python
while not stop.is_set():
    wake.clear()
    while not stop.is_set() and (n := await arelay_batch(worker, publish, batch_size)):
        total += n
    waits = [asyncio.create_task(wake.wait()), asyncio.create_task(stop.wait())]
    await asyncio.wait(waits, timeout=poll_interval, return_when="FIRST_COMPLETED")
    for t in waits:
        t.cancel()
```

`test_run_relay_wakes_up_on_notify` uses a 30-second poll interval and still sees the message delivered within a second of the insert's commit: only the `NOTIFY` can explain that. The poll is the safety net for notifications that are lost (section 7).

### 6.4 Delivery guarantees, and idempotent consumers

Between "publish succeeded" and "the relay's transaction committed", the relay can die. The message was delivered, but the outbox still says unpublished, and the next relay run delivers it **again**. `test_at_least_once_delivery_and_idempotent_consumer` makes it happen: the publisher appends the message to an in-memory "broker", then terminates the relay's own backend before its commit:

```python
def publish_then_die(m: outbox.Message) -> None:
    broker.append(m)
    fresh_conn.execute("SELECT pg_terminate_backend(%s)", (relay_conn.info.backend_pid,))
...
assert outbox.pending_count(fresh_conn) == 1  # never marked published
assert outbox.relay_batch(fresh_conn, broker.append) == 1
assert [m.id for m in broker] == [mid, mid]  # delivered twice
```

The three delivery guarantees:

- **At most once**: mark first, publish after. A crash in between loses the message.
- **At least once**: publish first, mark after. A crash in between duplicates it. The outbox does this, because losing an order confirmation is worse than sending a duplicate, *if* duplicates are harmless.
- **Exactly once** delivery across two systems that don't share a transaction doesn't exist. What you can have is an **exactly-once effect**: at-least-once delivery plus an **idempotent consumer**.

The consumer records each message it has handled, in the same transaction as the handler's effect:

```python
def consume_once(conn, consumer, message_id, handler) -> bool:
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO processed_messages (consumer, message_id) VALUES (%s, %s)"
            " ON CONFLICT DO NOTHING RETURNING 1",
            (consumer, message_id),
        ).fetchone()
        if row is None:
            return False          # seen before: skip
        handler(conn)
        return True
```

The duplicate in the test was consumed twice: `[True, False]`, one email row. If the handler fails, the marker rolls back with it, and the message is handled on the next delivery. Concurrent duplicates are safe too: the second `INSERT` waits on the primary key until the first transaction commits or rolls back. When the consumer's effect is in *another* system (an email provider), pass the message id as that system's idempotency key, if it has one.

One case is truly exactly once: when the destination is **the same database**. `jobs_publisher()` turns events into jobs in Module 10's queue, inside the relay's transaction; the jobs and the "published" mark commit together or not at all. `test_jobs_publisher_is_exactly_once` also checks the failure path: a publisher that inserts one job and then raises leaves no job behind (the savepoint) and the message pending.

[`examples/outbox_demo.py`](examples/outbox_demo.py) runs the whole flow inside one rolled-back transaction: a two-seller checkout with an idempotency key, its replay, the relay into jobs, and a consumer receiving one message twice:

```text
orders: [(500215, 2, '109.98'), (500216, 5, '22.99')]
replay returned the same orders: True
outbox: (3, 'order.placed', '500215', {'total': '109.98', 'user_id': 8, 'order_id': 500215, 'seller_id': 2})
outbox: (4, 'order.placed', '500216', {'total': '22.99', 'user_id': 8, 'order_id': 500216, 'seller_id': 5})
relay published 2 messages; pending now: 0
jobs: [('notify_seller', '500215'), ('notify_seller', '500216'), ('send_order_confirmation', '500215'), ('send_order_confirmation', '500216')]
delivery 1 of message 3: handler ran = True
delivery 2 of message 3: handler ran = False
```

### 6.5 Running an outbox

- **Retention.** Published rows are history; `purge_published(conn, "7 days")` deletes old ones. On a busy system, partition the outbox by day (Module 14) and drop partitions instead of deleting rows.
- **Monitoring.** `pending_count()` (unpublished messages) is the metric to alert on: a growing number means the relay is down or every publish fails. Also alert on `attempts` above a threshold, and on the age of the oldest pending message.
- **Payloads.** Keep them small: ids and the facts a consumer needs to decide, not documents. `record()` refuses payloads above 64 KB. Consumers that need more read the current state by id.
- **Change data capture instead of a relay.** A CDC tool reading the WAL through logical decoding (Module 19, section 14) can stream the `outbox` table's inserts to a broker with no polling at all; Debezium's "outbox event router" does exactly this. The table and the producer side stay the same.

---

## 7. LISTEN/NOTIFY: a wake-up call, not a queue

The relay uses `NOTIFY` only to wake up. Module 23 used it the same way for the job worker. Its limits explain why the data always lives in a table:

- **No persistence.** A notification goes only to sessions that are `LISTEN`ing at the moment of the commit. A relay that's restarting, or whose connection was just dropped, never sees it. Hence the polling fallback.
- **Payload limit.** 8,000 bytes:

  ```text
  SELECT pg_notify('x', repeat('a', 8000));
  ERROR:  payload string too long
  ```

  (`repeat('a', 7999)` is accepted.)
- **Bound to a connection.** A listener must keep its own connection open, outside any pool that might hand it to someone else. Behind PgBouncer in transaction mode, `LISTEN` doesn't work (the next transaction may run on another server connection).
- **Transactional, and collapsed.** Notifications are delivered at commit, in commit order; identical `(channel, payload)` pairs in one transaction are delivered once (why the outbox trigger is statement-level with an empty payload).
- **A queue with a size limit.** Undelivered notifications for slow listeners accumulate in a shared queue (8 GB by default); a listener that never reads them eventually makes `NOTIFY` fail for everyone.
- **A global lock at commit.** Committing a transaction that sent `NOTIFY` takes a database-wide lock to append to the queue, which serializes such commits. At thousands of notifying transactions per second, this shows up as contention.

A queue **table** (Module 10's `jobs`, the outbox) has none of these problems: durable, any size, consumable by any connection, through any pooler. Use `NOTIFY` to reduce latency on top of it.

---

## 8. Observability

When a checkout is slow at 2 a.m., three questions need answers: which requests, which queries, and why. That takes the application and the database looking at the same events.

### 8.1 Name your connections

Every Bazaar connection sets `application_name` (`bazaar`, `bazaar-ro` for the read engine, `bazaar-migrations` for Alembic, `bazaar-worker`...). It shows up in `pg_stat_activity`, in server log lines (with `%a` in `log_line_prefix`) and in `pg_stat_statements`-adjacent tooling. Module 26's migration probe found the migration's backend by it. Use one name per service and role, not per request.

### 8.2 Structured logs

`bazaar.observability.JsonFormatter` writes one JSON object per log line, with the fields passed as `extra=` at the top level, plus the current request's context:

```python
def format(self, record: logging.LogRecord) -> str:
    out = {
        "ts": ..., "level": record.levelname, "logger": record.name, "msg": record.getMessage(),
    }
    out.update(_sql_context.get())  # request_id, route, ... of the current request
    for k, v in record.__dict__.items():
        if k not in _STANDARD and not k.startswith("_"):
            out[k] = v
    ...
    return json.dumps(out, default=str)
```

The request context lives in a `ContextVar`, set with `with obs.sql_context(route=..., request_id=...):`. Each asyncio task (each request) sees its own values, and nested blocks add to the outer ones. Log aggregation systems (Loki, Elasticsearch, CloudWatch) can then answer "all slow queries of route X in the last hour" without regular expressions.

### 8.3 Timing every query

`instrument_engine(engine, slow_ms=200)` hangs three listeners on the engine (Module 24's events): `before_cursor_execute` pushes a start time onto `conn.info`, `after_cursor_execute` computes the duration, updates in-process counters (`QueryStats`: count, errors, slow, max, a histogram), and logs a `WARNING` for slow statements; `handle_error` logs failures with their SQLSTATE and error kind. [`examples/observability_demo.py`](examples/observability_demo.py) simulates two requests:

```text
{"ts": "2026-10-14T11:13:22.093", "level": "INFO", "logger": "bazaar.api", "msg": "request done", "route": "GET /products/{id}", "request_id": "99ba972a", "status": 200}
{"ts": "2026-10-14T11:13:22.159", "level": "WARNING", "logger": "bazaar.sql", "msg": "slow query", "route": "GET /sellers/{id}/report", "request_id": "39edda0f", "duration_ms": 65.07, "statement": "SELECT date_trunc('month', created_at) AS m, sum(total) FROM orders WHERE seller_id = %(s)s GROUP BY 1 ORDER BY 1", "rowcount": 36, "params": 1, "executemany": false}
{"ts": "2026-10-14T11:13:22.162", "level": "INFO", "logger": "bazaar.api", "msg": "request done", "route": "GET /sellers/{id}/report", "request_id": "39edda0f", "status": 200}

pg_stat_activity.query: SELECT query FROM pg_stat_activity WHERE pid = pg_backend_pid() /*application='bazaar',request_id='39edda0f',route='GET%20%2Fsellers%2F%7Bid%7D%2Freport'*/
pool: {'kind': 'QueuePool', 'size': 3, 'checked_out': 0, 'idle': 1, 'overflow': -2}
stats: 3 statements, 1 slow, max 65 ms, histogram {'<=1': 0, '<=5': 2, '<=10': 0, '<=50': 0, '<=100': 1, '<=500': 0, '<=1000': 0, '<=5000': 0, '>5000': 0}
```

(The demo's threshold is 25 ms, so that the report query, 65 ms here, counts as slow.) The slow-query line carries the request's route and id, the statement *text* with its placeholders, the row count and the **number** of parameters, never their **values**: parameters are where personal data lives (emails, addresses), and a log line is copied to more places than a database row. `log_all=True` logs every statement at `DEBUG`, for development.

### 8.4 sqlcommenter: tags the database can see

The application knows the route and request; the database knows the query and its plan. **sqlcommenter** (a convention from Google, supported by OpenTelemetry and several ORMs) joins them by appending a comment to each statement: `/*key='value',...*/`. `install_sqlcommenter(engine, static={"application": "bazaar"})` appends the current `sql_context()` tags. The demo's `pg_stat_activity.query` line above shows the result as the server sees it, URL-encoded so that a value can never contain `*/` and close the comment early (that would be SQL injection through a request header; `test_sqlcomment_format` checks it).

The first version broke every tagged query:

```text
psycopg.ProgrammingError: only '%s', '%b', '%t' are allowed as placeholders, got '%2'
```

The URL-encoded `/` is `%2F`, and psycopg's parameter style treats `%` as the start of a placeholder. The listener now doubles `%` when the dialect's paramstyle is `format`/`pyformat` (it's not needed for asyncpg). OpenTelemetry's commenter had the same problem to solve.

Where the comments help: `pg_stat_activity` (who is running this?), the server log (`log_min_duration_statement`, `auto_explain`, lock-wait messages from `log_lock_waits`), and anything that reads them. Where they don't: **`pg_stat_statements`** computes its query id after removing comments, so all variants of a statement share one entry, and the text it keeps is the **first one seen**:

```sql
SELECT pg_stat_statements_reset();
SELECT title FROM products WHERE id = 42 /*route='GET%20%2Fproducts',request_id='a1'*/;
SELECT title FROM products WHERE id = 43 /*route='GET%20%2Fsearch',request_id='b2'*/;
SELECT title FROM products WHERE id = 44;
SELECT calls, query FROM pg_stat_statements WHERE query LIKE '%FROM products WHERE id%';
```

```text
 calls |                                         query
-------+----------------------------------------------------------------------------------------
     3 | SELECT title FROM products WHERE id = $1 /*route='GET%20%2Fproducts',request_id='a1'*/
(1 row)
```

Three calls from two routes (and one untagged), attributed to the first route's text. Don't read per-route statistics from `pg_stat_statements`. There's also a cost: the statement text changes per request, so client-side prepared statement caches (asyncpg's, psycopg's `prepare_threshold`) see new statements every time. Keep the tags low-cardinality (route, not request id) where that matters.

### 8.5 OpenTelemetry

OpenTelemetry is the vendor-neutral standard for traces, metrics and logs. A **trace** is a request's tree of **spans** (timed operations); database instrumentation adds a span per statement, as a child of the request's span. `setup_tracing("bazaar-api", engine=engine)` configures the SDK with a console exporter (swap in an OTLP exporter for Jaeger, Tempo, Honeycomb, Datadog...) and instruments the engine. [`examples/otel_console.py`](examples/otel_console.py), abbreviated:

```text
{
    "name": "connect",
    "context": {"trace_id": "0xc7e18167e92bb97110012110f5893182", "span_id": "0xe528844ceed94b49", ...},
    "kind": "SpanKind.CLIENT",
    "parent_id": "0xabbd2856ad2401d7",
    "attributes": {"net.peer.name": "localhost", "net.peer.port": 55426, "db.name": "bazaar",
                   "db.user": "postgres", "db.system": "postgresql"},
    ...
}
{
    "name": "SELECT bazaar",
    "context": {"trace_id": "0xc7e18167e92bb97110012110f5893182", "span_id": "0xcc6430ea5b5a2d3a", ...},
    "parent_id": "0xabbd2856ad2401d7",
    "attributes": {..., "db.statement": "SELECT title FROM products WHERE id = %(id)s",
                   "db.system": "postgresql", "db.operation": "SELECT bazaar"},
    ...
}
{
    "name": "GET /products/{id}",
    "context": {"trace_id": "0xc7e18167e92bb97110012110f5893182", "span_id": "0xabbd2856ad2401d7", ...},
    "kind": "SpanKind.INTERNAL",
    "parent_id": null,
    ...
}
```

The connect and the query are children of the request span (`parent_id` = the request's `span_id`), all in one trace. With `enable_commenter=True`, the instrumentation also appends a `traceparent` comment, which `test_opentelemetry_spans_and_traceparent` found in `pg_stat_activity`:

```text
SELECT query FROM pg_stat_activity WHERE pid = pg_backend_pid() /*db_driver='psycopg',db_framework='sqlalchemy%3A2.1.4',traceparent='00-19819bd75619b6e9c92a827589fc4bd3-7bc942d03d39b4ac-03'*/
```

A slow-query line in the server log can then be pasted into the tracing UI. Two practical notes. The SQLAlchemy instrumentation (0.66b1) declares support for SQLAlchemy below 2.1 and silently instruments nothing on 2.1 (`SQLAlchemyInstrumentor only instruments "sqlalchemy >= 1.0.0, < 2.1.0"`); the engine events it uses are unchanged, so `setup_tracing()` passes `skip_dep_check=True`. Check that spans appear after every upgrade. And for code that uses psycopg or asyncpg directly, `PsycopgInstrumentor` and `AsyncPGInstrumentor` (in the `otel` extra) instrument the drivers themselves.

### 8.6 Metrics

`pool_metrics()` reads the pool's state for SQLAlchemy, psycopg_pool and asyncpg:

```text
pool: {'kind': 'QueuePool', 'size': 3, 'checked_out': 0, 'idle': 1, 'overflow': -2}
```

(SQLAlchemy creates connections lazily: one connection existed, idle; `overflow` is negative while fewer than `pool_size` exist.) For psycopg's pool, `get_stats()` adds `requests_waiting` and error counters. Export these, `QueryStats` and the retry counts to your metrics system (Prometheus, StatsD) from a periodic task or a `/metrics` route. What to alert on: requests waiting for the pool, `checked_out` close to `size` for minutes, the slow-query rate, retries per minute, `outbox.pending_count()`. On the server side, Module 17's views (`pg_stat_activity` states, `pg_stat_statements`, `pg_stat_database` deadlocks and conflicts) complete the picture.

---

## 9. Security in the application

### 9.1 Least privilege

The application connects as `bazaar_app` (Module 15): DML on the tables, no DDL, no ownership.

```sql
SET ROLE bazaar_app;
CREATE TABLE bazaar.app_was_here (id int);
ALTER TABLE orders ADD COLUMN x int;
```

```text
ERROR:  permission denied for schema bazaar
ERROR:  must be owner of table orders
```

A SQL injection bug or a compromised dependency in the application can then read and write data, which is bad enough, but not drop tables or create functions. Migrations run with a different credential (`BAZAAR_MIGRATION_DSN`) that only the deployment pipeline has (Module 26). The seller portal connects as `bazaar_seller`, under RLS. Analysts get `bazaar_analyst`, read-only, ideally on a replica. Note what `bazaar_app` *can* read: `audit_log`, with full before/after images of orders. Least privilege is a design, not a default; review the grants when you add a table.

### 9.2 Secrets

- Connection strings come from the environment (`BAZAAR_DSN`), set by the deployment platform from a secret store (Vault, AWS Secrets Manager, Kubernetes secrets), never from a file in Git. `alembic.ini` has no URL for that reason.
- Don't log DSNs. SQLAlchemy's `URL` hides the password when printed (`render_as_string(hide_password=True)` is the default); psycopg's `conn.info.dsn` omits it. Your own error messages might not.
- Rotation: a PostgreSQL role has one password. Rotate with two login roles that are members of one privilege role (`bazaar_app_a`, `bazaar_app_b` in `bazaar_app`): set the new password on the idle one, switch the deployment, then change the other. Or use short-lived credentials from IAM/Vault where the platform supports them. `VALID UNTIL` puts an expiry on a password.
- Encrypt in transit: `sslmode=verify-full` with the server's CA, not `require` (which encrypts but doesn't check who you're talking to). Module 15 covers the server side.

### 9.3 SQL injection, one last time

Every value is a bound parameter; identifiers that must be dynamic are composed with `psycopg.sql` (Module 22) or SQLAlchemy constructs; ruff's `S608` rule flags string-formatted SQL. This module added two places where text reaches SQL: sqlcommenter tags (URL-encoded, so they can't close the comment) and the DDL helpers of `bazaar.migrate` (identifiers from revision files, never from users; `S608` is switched off for exactly those files).

### 9.4 Personal data

Bazaar holds names, emails, addresses and order histories. In the application layer:

- **Logs**: no parameter values, no row dumps (section 8.3). An email in a log line is a copy of personal data that your deletion process won't find.
- **Traces**: `db.statement` contains the statement text with placeholders, not values; don't add values as span attributes.
- **Error reports**: exception messages can contain values (`DETAIL: Key (email)=(zoe@example.com) already exists`). Scrub them before sending to an error tracker.
- **Copies**: `audit_log`, the outbox payloads and `pg_dump` files all contain personal data, and need the same retention and deletion rules as the tables.

---

## 10. A production readiness checklist

| Area | Check | Bazaar |
|---|---|---|
| Tests | run against the production major version of PostgreSQL; concurrency forced with barriers; migrations tested | Sections 1, Module 26 |
| Connections | one pool per process, sized by Little's law; `processes × pool` within the server's budget; pre-ping | `create_pool()`, `create_engine()` |
| PgBouncer | timeouts as role defaults, not startup options; only `SET LOCAL`; prepared statements supported (1.21+) or disabled | Section 5.3 |
| Timeouts | connect, pool, statement, lock (where useful), idle-in-transaction, request deadline | `Settings` |
| Errors | classified by SQLSTATE; whole-transaction retries with backoff, jitter, deadline; no retry of timeouts and overload | `bazaar.resilience` |
| Idempotency | client-chosen keys on writes that clients retry | `orders.idempotency_key`, `checkout()` |
| Side effects | outbox in the same transaction; at-least-once delivery; idempotent consumers | `bazaar.outbox` |
| Migrations | separate step, before the code; `lock_timeout`; expand/contract | Module 26 |
| Health | liveness without the database; readiness checks primary and schema | `check_health()` |
| Shutdown | drain on `SIGTERM`, close pools; pools created after fork | Sections 5.6, 5.7 |
| Observability | `application_name`; JSON logs with request ids; slow-query log; traces; pool and outbox metrics | `bazaar.observability` |
| Security | least-privilege roles; secrets from the environment; TLS `verify-full`; no personal data in logs | Section 9 |
| Database side | `pg_stat_statements`, `auto_explain`, `log_lock_waits`, autovacuum, backups tested, replicas monitored | Modules 12, 17–20 |

## 11. The Bazaar production layer

| Module | Contents |
|---|---|
| `bazaar/resilience.py` | `ErrorKind`, `classify()`, `sqlstate()`, `RetryPolicy` (full-jitter backoff, deadline, idempotent-only kinds), `call_with_retry()`, `acall_with_retry()`, `@retry`, `@aretry`, `run_transaction()`, `arun_transaction()`, `run_in_session()`, `arun_in_session()` |
| `bazaar/outbox.py` | `record()`, `arecord()`, `insert_stmt()`, `relay_batch()`, `arelay_batch()`, `run_relay()`, `jobs_publisher()`, `consume_once()`, `purge_published()`, `pending_count()` |
| `bazaar/observability.py` | `JsonFormatter`, `configure_logging()`, `sql_context()`, `sqlcomment()`, `install_sqlcommenter()`, `instrument_engine()` + `QueryStats`, `pool_metrics()`, `check_health()`, `setup_tracing()` |
| `bazaar/config.py` | `lock_timeout_ms`, `idle_in_transaction_timeout_ms`, `pool_timeout_s`, `replica_dsn`, `session_settings()`, `libpq_options()` |
| `bazaar/sa/engine.py` | `create_read_engine()`, `dispose_after_fork()` |
| `bazaar/orm/services.py` | `checkout(..., idempotency_key=, emit_events=)`, `checkout_async()` |
| `bazaar/errors.py` | `IdempotencyConflict`, `constraint_name()` (psycopg and asyncpg) |
| `bazaar/testing.py` | `TemplateDatabase`, `scratch_database()`, `create_database()` (copies database settings and grants), `migrations_fingerprint()` |
| `migrations/versions/2701_outbox.py` | `outbox`, `processed_messages`, `notify_outbox()` |
| tests | `test_resilience.py` (35), `test_outbox.py` (12), `test_observability.py` (9), `test_concurrency.py` (4), `test_containers.py` (1, opt-in); fixtures `fresh_db`, `empty_db`, `head_template` |

---

## Summary

- Test against real PostgreSQL: the features are the database's. Roll back for speed; use **template databases** (`CREATE DATABASE ... TEMPLATE`, 138 ms per copy here, rebuilt by fingerprint, settings and grants copied explicitly) where commits matter; **pytest-xdist** with per-worker names (25 s → 10 s); **testcontainers** for a brand-new cluster. **Force** concurrency with barriers.
- Classify errors by SQLSTATE: serialization and deadlocks, lock timeouts, connection loss (failover), standby connections, timeouts, overload, permanent. Unwrap SQLAlchemy's wrappers; mind the exception hierarchy (`TimeoutError` is an `OSError`).
- Retry **whole transactions** with exponential backoff, full jitter and a deadline; never retry timeouts, overload or business errors; retry lost connections only for idempotent work, because a commit's outcome can be unknown. **Idempotency keys** make writes retryable; a unique index settles concurrent duplicates.
- Every layer gets a timeout, nested sensibly: request deadline, pool, connect, statement, lock, idle-in-transaction, PostgreSQL 17's transaction timeout. Client-side cancellation (psycopg async) stops the server's work too.
- Size pools with Little's law and the server's core budget, one pool per process; behind PgBouncer, put timeouts on the **role** (startup options are refused or silently ignored), use only `SET LOCAL`, and rely on prepared-statement tracking (1.21+). Read-only engines for replicas; readiness checks primary and schema; drain on shutdown; create pools after fork.
- The **transactional outbox** writes events in the business transaction; a `SKIP LOCKED` relay publishes them **at least once**, woken by `NOTIFY` and backed by polling; idempotent consumers (`processed_messages`) turn that into an exactly-once effect. LISTEN/NOTIFY is a wake-up call: no persistence, 8,000-byte payloads, connection-bound, not through transaction pooling.
- Observe: `application_name`, JSON logs with request context, a slow-query log without parameter values, sqlcommenter tags (visible in `pg_stat_activity` and logs, *not* split in `pg_stat_statements`), OpenTelemetry spans with `traceparent`, pool and outbox metrics.
- Least privilege, secrets from the environment, TLS with verification, no personal data in logs.

## What's next

Module 28 puts it all together: an async FastAPI service on the Bazaar database, with checkout (idempotency keys and the outbox), search, a seller portal under RLS, background jobs and the relay, migrations, health checks, and a test suite built on this module's fixtures.
