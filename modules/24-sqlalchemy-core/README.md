# Module 24: SQLAlchemy Core

<!-- nav:start -->
> **Theory** · [Exercises](exercises.md) · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 23 Async Python: psycopg async and asyncpg](../23-async-python/README.md) · [Course home](../../README.md) · [25 SQLAlchemy ORM →](../25-sqlalchemy-orm/README.md)
<!-- nav:end -->

Bazaar's data-access layer from Module 22 is honest, fast, plain SQL. It is also starting to strain. The search function glues optional `AND` clauses together with `psycopg.sql`; the order list repeats the same column list in three places; the finance team wants a seller statement and a category revenue roll-up with five optional filters each, and every new filter means another string fragment that nobody can test in isolation. SQLAlchemy Core is the layer that keeps the SQL (you still decide every join and every `GROUP BY`) but lets you build it out of Python objects: tables you describe once, expressions you can pass around and combine, and a compiler that turns them into correct, parameterized PostgreSQL. This module covers Core on its own: engines and pools, connections and transactions in SQLAlchemy 2.0 style, describing the Bazaar schema as `Table` metadata, reflection, the SQL expression language from `SELECT` to recursive CTEs and window functions, the PostgreSQL-specific constructs (`ON CONFLICT`, JSONB, arrays, full-text search, `DISTINCT ON`, `SKIP LOCKED`), bulk loading, async engines, and custom types. Module 25 builds the ORM on top of it.

**What you'll learn**

- What SQLAlchemy is made of (engine, pool, dialect, Core, ORM), what it buys you over the plain SQL of Module 22, and what it costs (measured).
- Engines: URLs for psycopg 3 and asyncpg, the `create_engine()` options that matter (`pool_size`, `max_overflow`, `pool_pre_ping`, `pool_recycle`, `echo`, `isolation_level`, `connect_args`), how the `QueuePool` works, why you must not stack it on `psycopg_pool`, and when to use `NullPool` (PgBouncer).
- Connections and transactions in 2.0 style: "commit as you go", `engine.begin()`, savepoints with `begin_nested()`, isolation levels and `AUTOCOMMIT`, `text()` with bound parameters, and the `Result` API.
- Describing the schema with `MetaData`/`Table`/`Column`: PostgreSQL types (`JSONB`, `ARRAY`, existing `ENUM`s and domains, `UUID`, `TSTZRANGE`, `TSVECTOR`, `numeric` as `Decimal`), identity and generated columns, constraints, indexes, and a naming convention that reproduces Postgres's own names.
- Reflection (`autoload_with`, `MetaData.reflect`, the `Inspector`), its trade-offs, and a **drift test** that proves your metadata matches the database.
- The expression language: `select`, joins, aggregates with `FILTER`, aliases, subqueries, CTEs (including recursive), window functions, `LATERAL`, `EXISTS`, set operations, `CASE`, casts, and dynamic queries with keyset pagination.
- PostgreSQL specifics: `INSERT ... ON CONFLICT` with `excluded`, `RETURNING`, `UPDATE ... FROM`, JSONB and array operators, full-text search with `websearch_to_tsquery` and `ts_rank`, `DISTINCT ON`, `FOR UPDATE SKIP LOCKED`, set-returning functions, and `COPY` through the raw driver connection.
- Reading the SQL SQLAlchemy generates: `str()`, `compile()`, `literal_binds` (and why not to execute it), `echo`, the statement cache, cursor events.
- Bulk inserts: `executemany` and "insertmanyvalues" against `COPY`, with real numbers.
- Async Core with `create_async_engine` and `AsyncConnection`, and custom types with `TypeDecorator`.

**Before you start**

```bash
./project/bin/reset.sh 24                                     # large dataset, ~3 minutes
cd project/python
uv sync                    # or: .venv/bin/pip install -e . pytest pytest-asyncio ruff pydantic
source .venv/bin/activate
export BAZAAR_DSN=postgresql://postgres:postgres@localhost:5432/bazaar
alembic stamp 2600 && alembic upgrade head   # the package's schema (module 23's triggers included)
python -c "import sqlalchemy; print(sqlalchemy.__version__)"
```

The reference package always matches its latest migrations, so bring the database to its schema right after `reset.sh` with the `alembic` line above (about 30 s); Module 26 explains what this does.

The package now depends on `sqlalchemy[asyncio]` (the `[asyncio]` extra installs `greenlet`, which the async engine needs). The outputs in this module come from **SQLAlchemy 2.1.4** with psycopg 3.3.6 and asyncpg 0.32.0, against PostgreSQL 17.11 in a 4-vCPU container reached through Docker's port forwarding (a `SELECT 1` round trip takes about 130–250 µs). Everything uses the **2.0-style API**, which 2.1 keeps; the few places where 2.1 differs from 2.0 are marked. The scripts are in [`examples/`](examples/); run them from `project/python` with the virtual environment active, for example `python ../../modules/24-sqlalchemy-core/examples/01_engine_and_transactions.py`.

---

<!-- toc:start -->
**Contents**

- [1. Why SQLAlchemy, and which part](#1-why-sqlalchemy-and-which-part)
- [2. Engines](#2-engines)
- [3. Connections and transactions](#3-connections-and-transactions)
- [4. Describing the schema: `MetaData`, `Table`, `Column`](#4-describing-the-schema-metadata-table-column)
- [5. Reflection](#5-reflection)
- [6. The SQL expression language](#6-the-sql-expression-language)
- [7. Writing data](#7-writing-data)
- [8. PostgreSQL-specific constructs](#8-postgresql-specific-constructs)
- [9. Seeing the SQL](#9-seeing-the-sql)
- [10. Async Core](#10-async-core)
- [11. Custom types with `TypeDecorator`](#11-custom-types-with-typedecorator)
- [12. The Bazaar Core layer](#12-the-bazaar-core-layer)
- [Summary](#summary)
- [What's next](#whats-next)

<!-- toc:end -->

## 1. Why SQLAlchemy, and which part

### 1.1 What hurt in Module 22

Look at `bazaar/repo.py` from Module 22 with fresh eyes. Three things in it are not about the database at all:

1. **Composing SQL from optional pieces.** `search_query()` appends `AND p.category_id = %(category_id)s` when a category is given, and `orders_query()` appends a keyset condition when a cursor is given. With two optional filters this is fine. With the eight filters and four sort orders of a real product listing (Exercise 5), string assembly becomes the hardest code in the file to get right, and its correctness is only visible at run time.
2. **Saying the same thing twice.** Column lists, the "a sale is paid, shipped or delivered" rule, and the category-tree CTE are copied between queries. When the rule changes, you grep.
3. **Translating types by hand.** Enum adapters registered per connection, `Jsonb()` wrappers, `class_row` factories.

None of these are SQL problems. They are *program construction* problems, and that is what SQLAlchemy solves.

### 1.2 The layers

SQLAlchemy is two libraries stacked on each other:

```text
 ┌─────────────────────────────────────────────────────────────┐
 │ ORM (module 25)   mapped classes, Session, unit of work,    │
 │                   identity map, relationships, lazy loading │
 ├─────────────────────────────────────────────────────────────┤
 │ Core (this module)                                          │
 │   schema:      MetaData, Table, Column, types, constraints  │
 │   expressions: select(), insert(), func.*, joins, CTEs ...  │
 │   compiler:    expression tree  ->  SQL string + params     │
 │   engine:      Engine -> Pool -> Connection -> Result       │
 │   dialect:     "postgresql+psycopg": SQL flavour + driver   │
 ├─────────────────────────────────────────────────────────────┤
 │ DBAPI driver: psycopg 3 / asyncpg (modules 22, 23)          │
 └─────────────────────────────────────────────────────────────┘
```

- The **dialect** knows two things: how PostgreSQL spells SQL (`ILIKE`, `ON CONFLICT`, `RETURNING`, `::casts`) and how to talk to one specific driver (psycopg's `%(name)s` placeholders, asyncpg's `$1`).
- The **engine** owns a connection **pool** and hands out `Connection` objects. Every statement goes through it, so it is also where logging, events and the statement cache live.
- **Core** is the schema description (`Table`) plus the **expression language**: Python objects that represent SQL (`products.c.price > 100` is not a boolean, it is a `BinaryExpression`) and a **compiler** that turns a tree of them into a SQL string and a parameter dictionary.
- The **ORM** maps Python classes to tables and adds a `Session` that tracks objects. It emits its SQL *through Core*. Everything in this module is still true when you use the ORM, and when the ORM is the wrong tool (reports, bulk work), you drop to Core without changing engines, connections or transactions.

### 1.3 What it costs

Core is not free. You pay in three currencies:

- **CPU per statement.** Building an expression tree, computing its cache key, compiling (or finding it in the cache) and processing results all run Python code. [`examples/02_overhead.py`](examples/02_overhead.py) runs 10,000 primary-key lookups each way on one connection:

  ```text
  psycopg, conn.execute()                        163.7 µs/query
  psycopg, conn.execute(), no prepared stmts     261.2 µs/query
  Core text()                                    321.8 µs/query
  Core select(), built per call, cached          454.1 µs/query
  Core select(), compiled_cache=None             700.0 µs/query
  round trip (SELECT 1 through Core)             248.4 µs
  ```

  Plain psycopg with its automatic prepared statements (Module 22, section 12) is the floor. A `text()` statement through Core costs roughly 60–150 µs more, a `select()` built from scratch on every call another ~150 µs (building the tree and its cache key), and without the compiled-statement cache the compiler itself shows up: another 250 µs. Runs vary by ±20 % on this machine; the ordering never changed. Per *request*, with five queries, that's about a millisecond of CPU. That matters for a hot endpoint doing 2,000 requests per second per process; for almost everything else, a single slow query plan costs more than all of it.
- **Indirection.** The SQL you debug is generated. You need to know how to see it (section 9) and to read it, and some PostgreSQL features need a detour (`COPY`, section 8.7).
- **A second vocabulary.** `.where(a, b)` for `AND`, `~` for `NOT`, `.in_()`, `.is_(None)`, `.label()`. The SQL knowledge from Modules 03–14 carries over one to one; the spelling has to be learned once.

What you get in return: queries are values you can build, combine, test and reuse; parameters are always bound (injection is structurally impossible unless you use `text()` with f-strings); types round-trip without per-connection setup; one pool and one transaction API for sync and async, psycopg and asyncpg; and a schema description that tests can check against the real database.

> **In production:** the Bazaar package keeps both. `bazaar.repo` (plain SQL) stays for the hot paths it already serves well; new reporting code is Core (`bazaar.sa.reports`), and Module 25's services use the ORM. They share the same database conventions: functions take a connection first and never commit.

### 1.4 SQLAlchemy 2.0 style

SQLAlchemy 1.x had several overlapping APIs: "autocommit" mode, `engine.execute()`, `Query` objects, implicit connections. Version 2.0 removed them and kept one consistent style, which 2.1 continues:

- You always execute on an explicit `Connection` (or `Session`), never on the engine.
- A connection is **always in a transaction** once you execute something; nothing is committed unless you commit.
- `select()` is the single query construct for Core and ORM.

Old tutorials that call `engine.execute()` or `session.query()` are 1.x style. Everything here is 2.0 style.

> **SQLAlchemy 2.1:** the differences that touch this module: a plain `postgresql://` URL now means psycopg 3 (in 2.0 it meant psycopg2); `greenlet` is no longer installed automatically, so install `sqlalchemy[asyncio]`; and `DISTINCT ON` is spelled `.ext(postgresql.distinct_on(col))` instead of `.distinct(col)` (the old form still works with a deprecation warning).

---

## 2. Engines

### 2.1 URLs

An engine is created from a URL that names the dialect and the driver:

```text
postgresql+psycopg://user:password@host:5432/bazaar?sslmode=require
└──┬─────┘ └──┬──┘
 dialect    driver: psycopg (3, sync or async) | asyncpg | psycopg2 | pg8000
```

- `postgresql+psycopg://` is psycopg 3. The same URL works for `create_engine()` and `create_async_engine()`; SQLAlchemy picks psycopg's async API for the latter.
- `postgresql+asyncpg://` is asyncpg (async only).
- Query-string parameters are passed to the driver (`sslmode`, `application_name`, `connect_timeout`).

Passwords with special characters must be URL-escaped. Instead of escaping by hand, build the URL from parts:

```python
url = sa.URL.create(
    "postgresql+psycopg", username="bazaar_app", password="p@ss/w:rd",
    host="db.internal", port=5432, database="bazaar",
    query={"sslmode": "require", "application_name": "web"},
)
print(url)                                        # the password is masked
print(url.render_as_string(hide_password=False))  # escaped, for passing on
```

```text
postgresql+psycopg://bazaar_app:***@db.internal:5432/bazaar?application_name=web&sslmode=require
postgresql+psycopg://bazaar_app:p%40ss%2Fw%3Ard@db.internal:5432/bazaar?application_name=web&sslmode=require
```

Bazaar's settings hold a libpq URI (`BAZAAR_DSN`, Module 22). `bazaar.sa.engine.sa_url()` turns it into a SQLAlchemy URL by swapping the driver name, and rejects libpq `key=value` strings with a clear error, as Module 23 did for asyncpg.

### 2.2 `create_engine()` options

```python
engine = sa.create_engine(
    "postgresql+psycopg://postgres:postgres@localhost:5432/bazaar",
    pool_size=10,          # connections kept open in the pool
    max_overflow=0,        # extra connections allowed beyond pool_size (closed when returned)
    pool_timeout=10,       # seconds to wait for a free connection before TimeoutError
    pool_pre_ping=True,    # test each connection on checkout
    pool_recycle=1800,     # replace connections older than 30 minutes
    pool_use_lifo=False,   # True: reuse the most recent connection, let idle ones time out
    echo=False,            # True: log every statement (section 9)
    isolation_level=None,  # e.g. "REPEATABLE READ" for every connection of this engine
    connect_args={         # passed to psycopg.connect()
        "application_name": "bazaar-web",
        "connect_timeout": 5,
        "options": "-c statement_timeout=5000 -c lock_timeout=1000",
    },
)
```

| Option | What it does | Bazaar's choice |
|---|---|---|
| `pool_size` | Connections the pool keeps. Opened lazily, on first demand. | `BAZAAR_POOL_MAX_SIZE` (10) |
| `max_overflow` | Extra connections beyond `pool_size` under load, closed on return. `-1` = unlimited. | `0`: a hard ceiling |
| `pool_timeout` | How long a checkout waits when everything is in use. | 10 s |
| `pool_pre_ping` | Before handing out a pooled connection, run a cheap ping; if it fails, reconnect transparently. Costs a round trip per checkout. | on |
| `pool_recycle` | Close connections older than N seconds on checkout. Protects against firewalls and load balancers that drop idle TCP connections. | 1800 |
| `echo` | Log SQL to the `sqlalchemy.engine` logger. `"debug"` also logs rows. | off |
| `isolation_level` | Engine-wide isolation (Module 10). Usually set per connection instead (section 3.4). | default |
| `connect_args` | Keyword arguments for the driver's `connect()`. With psycopg that means libpq parameters and `options` for server settings at startup. | name, timeouts |

`connect_args["options"] = "-c statement_timeout=5000"` is the same startup option Module 22 used: the setting is part of the session from the first moment, before any query, and survives the pool's reset (which only rolls back).

> **Pitfall:** `create_engine()` connects to nothing. The first connection is opened when you first call `engine.connect()`. A wrong password shows up then, not at startup. If you want to fail fast, connect once at startup: `with engine.connect(): pass`.

`bazaar.sa.engine.create_engine()` builds all of this from the same `Settings` that `bazaar.db` uses, so a psycopg pool and a SQLAlchemy engine created from the same environment behave the same way (name, statement timeout, size).

### 2.3 What happens at first connect

Run [`examples/01_engine_and_transactions.py`](examples/01_engine_and_transactions.py). It turns on the engine log (exactly what `echo=True` does) and walks through the transaction patterns. The first lines show what the dialect does on the first connection of an engine:

```text
engine: Engine(postgresql+psycopg://postgres:***@localhost:55424/bazaar)
pool: Pool size: 2  Connections in pool: 0 Current Overflow: -2 Current Checked out connections: 0
    SQL> select pg_catalog.version()
    SQL> select current_schema()
    SQL> show standard_conforming_strings
```

The dialect asks for the server version (to enable features), the default schema (used by reflection) and a string-escaping setting, once per engine. The pool starts empty ("Current Overflow: -2" means "2 below `pool_size`", that is, two connections may still be opened without overflowing).

### 2.4 The QueuePool

The default pool is a `QueuePool`: a thread-safe FIFO queue of idle connections.

```text
checkout:  idle connection in queue?  ── yes ──> (pre-ping) ──> hand it out
                │ no
                ▼
           opened < pool_size + max_overflow ?  ── yes ──> open a new one
                │ no
                ▼
           wait up to pool_timeout for a return, else raise TimeoutError

return:    ROLLBACK (reset_on_return), then
           if more than pool_size connections exist: close it (it was overflow)
           else: put it back in the queue
```

[`examples/12_pooling.py`](examples/12_pooling.py) makes the pool run out: `pool_size=2, max_overflow=1`, three threads each hold a connection for 1.5 s, and a fourth checkout arrives:

```text
1. Pool size: 2  Connections in pool: 0 Current Overflow: 1 Current Checked out connections: 3
   4th checkout gave up after 1.00 s: QueuePool limit of size 2 overflow 1 reached, connection tim...
   after: Pool size: 2  Connections in pool: 2 Current Overflow: 0 Current Checked out connections: 0 (the overflow connection was closed on return)
```

The reset on return deserves attention: whatever a request left behind (an open transaction, uncommitted writes) is rolled back before the connection is reused. Session-level state is *not* reset: a plain `SET search_path` or `SET statement_timeout` sticks to the connection and leaks to the next user. Module 22's rule still applies: use `SET LOCAL` (or `set_config(..., true)`), which ends with the transaction.

**Dead connections.** A database restart, a failover (Module 20) or an idle-connection timeout leaves pooled connections that look fine to Python but are closed on the server side. The same example kills the backend of a pooled connection with `pg_terminate_backend()` and then uses the pool:

```text
2. pre_ping=False: killed 1, next query FAILED: AdminShutdown
   connection_invalidated=True; the pool was emptied:
   ...and the query after that works again
2. pre_ping=True: killed 1, next query OK
```

Without pre-ping, one request fails; SQLAlchemy notices the disconnect (`connection_invalidated=True`), throws away *every* pooled connection of that age, and the next checkout reconnects. With pre-ping, the dead connection is detected at checkout and replaced before your query runs. Pre-ping cannot help with a connection that dies *during* a transaction; that needs retries at the application level (Module 27).

### 2.5 One pool, not two: psycopg_pool, PgBouncer, NullPool

Module 22 built a `psycopg_pool.ConnectionPool`. A SQLAlchemy engine has its own pool. **Use one of them, not both stacked.** If an engine were built on connections borrowed from a psycopg pool, each layer would keep its own idle connections, its own size limit and its own health checks, and a connection "returned" to SQLAlchemy would not be returned to psycopg_pool. Sizing becomes guesswork. In a process that uses SQLAlchemy, the engine's pool *is* the pool; the psycopg pool of `bazaar.db` is for code that uses psycopg directly.

There is one legitimate reason to switch SQLAlchemy's pooling off: **something else already pools**. Behind PgBouncer in transaction mode (Module 17), connections from the application to PgBouncer are cheap, and keeping them open in a client-side pool mostly wastes PgBouncer's client slots. `NullPool` opens a real connection on every checkout and closes it on return:

```python
from sqlalchemy.pool import NullPool
engine = create_engine(poolclass=NullPool)   # bazaar.sa.engine accepts the same override
```

What a real connect costs, from the same example:

```text
3. per checkout+query: NullPool 10.30 ms, QueuePool 0.84 ms
```

Ten milliseconds per connection to PostgreSQL directly (a new backend process, Module 16) is why you pool. To a PgBouncer on the same host it is well under a millisecond, which is why `NullPool` is acceptable there. Two more PgBouncer details: psycopg's automatic prepared statements need PgBouncer 1.21+ with `max_prepared_statements`, or `connect_args={"prepare_threshold": None}`; with asyncpg, SQLAlchemy's dialect needs `connect_args={"prepared_statement_name_func": ...}` producing unique names, or a statement cache size of 0 (Module 23, section 4.8 covers why).

### 2.6 One engine per process

An engine is expensive (pool, caches, dialect initialization) and thread-safe. Create **one per database per process** at startup and share it. Never create an engine per request: you would open a new pool, and therefore a new connection, every time.

> **Pitfall:** `fork()` and pools don't mix. A pool created before a web server forks its workers (gunicorn with `preload_app`, multiprocessing) shares TCP sockets between processes, and their traffic interleaves. Create the engine after the fork, or call `engine.dispose(close=False)` in the child as its first action, so the child starts with an empty pool and leaves the parent's connections alone.

---

## 3. Connections and transactions

### 3.1 `engine.connect()`: commit as you go

```python
with engine.connect() as conn:
    n = conn.execute(sa.text("SELECT count(*) FROM jobs WHERE queue = :q"), {"q": "m24"}).scalar()
    conn.execute(sa.text("INSERT INTO jobs (queue, kind) VALUES ('m24', 'demo')"))
    conn.commit()
    conn.execute(sa.text("INSERT INTO jobs (queue, kind) VALUES ('m24', 'not kept')"))
    # leaving the block without commit(): the second INSERT is rolled back
```

```text
    SQL> BEGIN (implicit)
    SQL> SELECT count(*) FROM jobs WHERE queue = %(q)s
    SQL> [generated in 0.00021s] {'q': 'm24'}
    SQL> INSERT INTO jobs (queue, kind) VALUES ('m24', 'demo')
    SQL> COMMIT
    SQL> BEGIN (implicit)
    SQL> INSERT INTO jobs (queue, kind) VALUES ('m24', 'not kept')
    SQL> ROLLBACK
```

The first `execute()` starts a transaction ("autobegin"; "implicit" means the driver sends `BEGIN` with the first statement, exactly psycopg's non-autocommit behavior from Module 22). `conn.commit()` ends it; the next statement begins a new one. When the block exits, anything not committed is rolled back and the connection goes back to the pool. This is "commit as you go": good for scripts and for code that commits at several points.

> **Pitfall:** the 1.x habit of expecting writes to "just happen" is the most common 2.0 surprise: without `commit()`, an `INSERT` in an `engine.connect()` block is silently rolled back at the end.

### 3.2 `engine.begin()`: one block, one transaction

```python
with engine.begin() as conn:
    conn.execute(sa.text("UPDATE jobs SET kind = 'demo2' WHERE queue = 'm24'"))
```

```text
    SQL> BEGIN (implicit)
    SQL> UPDATE jobs SET kind = 'demo2' WHERE queue = 'm24'
    SQL> COMMIT
```

`engine.begin()` checks out a connection and starts a transaction that **commits when the block ends normally and rolls back if it raises**. This is the shape of almost every unit of work in a service, the same as `with conn.transaction():` in psycopg. On an existing connection, `with conn.begin():` does the same thing.

### 3.3 Savepoints: `begin_nested()`

```python
with engine.begin() as conn:
    conn.execute(sa.text("INSERT INTO jobs (queue, kind) VALUES ('m24', 'outer')"))
    try:
        with conn.begin_nested():
            conn.execute(sa.text("INSERT INTO jobs (queue, kind, attempts) VALUES ('m24', 'bad', -1)"))
    except sa.exc.IntegrityError as e:
        print(f"caught: {type(e.orig).__name__}")
    kinds = conn.execute(sa.text("SELECT kind FROM jobs WHERE queue = 'm24' ORDER BY id")).scalars()
```

```text
    SQL> SAVEPOINT sa_savepoint_1
    SQL> INSERT INTO jobs (queue, kind, attempts) VALUES ('m24', 'bad', -1)
    SQL> ROLLBACK TO SAVEPOINT sa_savepoint_1
    caught: CheckViolation
    SQL> SELECT kind FROM jobs WHERE queue = 'm24' ORDER BY id
    kinds now: ['demo2', 'outer']
    SQL> COMMIT
```

`begin_nested()` is a `SAVEPOINT` (Module 10): a failure inside it rolls back to the savepoint, and the outer transaction stays usable. This is the Core equivalent of psycopg's nested `conn.transaction()` blocks, and it is how `bazaar.sa.reports` and the Module 25 services make individual operations atomic without committing the caller's transaction.

Notice `type(e.orig)`: SQLAlchemy wraps driver exceptions in its own hierarchy (`IntegrityError`, `OperationalError`, `ProgrammingError`, `DBAPIError`), and keeps the original psycopg exception in `.orig`, with its `sqlstate` and `diag` (Module 22, section 8). `bazaar.errors.translate()` works on `e.orig` unchanged.

### 3.4 Isolation levels and AUTOCOMMIT

Isolation is a property of the connection (Module 10). Set it per connection with execution options; the pool restores the default when the connection is returned:

```python
with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
    ...                     # every transaction on this connection is REPEATABLE READ
```

`isolation_level="AUTOCOMMIT"` puts the driver in autocommit mode: no `BEGIN`, every statement commits on its own. You need it for statements that cannot run in a transaction (`CREATE INDEX CONCURRENTLY`, `VACUUM`, `CREATE DATABASE`):

```text
-- D. AUTOCOMMIT: no BEGIN at all
    SQL> BEGIN (implicit; DBAPI should not BEGIN due to autocommit mode)
    SQL> DELETE FROM jobs WHERE queue = 'm24'
```

(The log line is SQLAlchemy's bookkeeping; no `BEGIN` reaches the server.) Exercise 1 checks that a snapshot really is held under `REPEATABLE READ` and that the pool resets the level.

### 3.5 `text()` and bound parameters

`text()` runs SQL you write yourself, with `:name` placeholders that SQLAlchemy converts to the driver's style:

```python
stmt = sa.text(
    "SELECT id, status, total FROM orders WHERE user_id = :uid AND id IN :ids ORDER BY id"
).bindparams(sa.bindparam("ids", expanding=True))
conn.execute(stmt, {"uid": 8, "ids": [501, 971, 12345]}).all()
```

```text
[(501, 'delivered', Decimal('151.96')), (971, 'delivered', Decimal('529.98'))]
```

- Values are always passed as parameters, never formatted into the string. An f-string inside `text()` is exactly as injectable as one in `cursor.execute()`.
- `expanding=True` turns a list into `IN (%(ids_1)s, %(ids_2)s, ...)`. (With psycopg directly you would write `= ANY(%s)`; that works in `text()` too.)
- `text()` has no idea what the columns are, so values arrive as the driver decodes them: `status` above is the string `'delivered'`. Declare the result columns to get type processing:

```python
typed = sa.text("SELECT id, status, total FROM orders WHERE id = :id").columns(
    sa.column("id", sa.BigInteger), sa.column("status", order_status), sa.column("total", sa.Numeric(12, 2))
)
row = conn.execute(typed, {"id": 501}).one()
```

```text
(501, <OrderStatus.DELIVERED: 'delivered'>, Decimal('151.96'))
```

`text()` is the right tool for SQL that the expression language cannot express nicely, for DDL, and for one-off statements. For anything you compose, filter or reuse, use the expression language.

### 3.6 Results

`conn.execute()` returns a `CursorResult`. The methods worth knowing:

| Call | Returns | Raises if |
|---|---|---|
| `.all()` | list of `Row` | |
| `.first()` | first `Row` or `None` (discards the rest) | |
| `.one()` | exactly one `Row` | zero rows (`NoResultFound`) or more than one (`MultipleResultsFound`) |
| `.one_or_none()` | a `Row` or `None` | more than one |
| `.scalar()` | first column of the first row, or `None` | |
| `.scalar_one()` / `.scalar_one_or_none()` | the single value | like `one()` |
| `.scalars()` | a result of first-column values (`.all()`, iteration) | |
| `.mappings()` | rows as read-only dicts | |
| `.partitions(n)` / `.yield_per(n)` | batches, for big results (section 9.3) | |

A `Row` behaves like a named tuple: `row.total`, `row[2]`, `tuple(row)`, `row._mapping["total"]`, `row._asdict()`. `conn.scalar(stmt)` is short for `conn.execute(stmt).scalar()`.

```text
RMKeyView(['id', 'shop_name'])                                         # result.keys()
[{'id': 1, 'shop_name': 'Happy Outlet 1'}, {'id': 2, ...}, ...]        # .mappings().all()
[1, 2, 3]                                                              # .scalars().all()
NoResultFound: No row was found when one was required                  # .one() on no rows
```

Use `.one()` when your logic assumes exactly one row: a silent `None` from `.first()` is a bug that surfaces three functions later.

---

## 4. Describing the schema: `MetaData`, `Table`, `Column`

### 4.1 A table

The expression language needs to know your tables: their names, columns and types. You describe them as `Table` objects collected in a `MetaData`. Here is `products` as it exists after Module 13, from [`bazaar/sa/tables.py`](../../project/python/src/bazaar/sa/tables.py) (as of this module; Module 26 adds `slug`):

```python
metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)

products = sa.Table(
    "products",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("seller_id", sa.BigInteger, sa.ForeignKey("sellers.id"), nullable=False),
    sa.Column("category_id", sa.Integer, sa.ForeignKey("categories.id"), nullable=False, index=True),
    sa.Column("sku", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("description", sa.Text),
    sa.Column("price", sa.Numeric(12, 2), nullable=False),
    sa.Column("status", product_status, nullable=False, server_default="draft"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("attributes", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("tags", pg.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'::text[]")),
    sa.Column(
        "search_vector", pg.TSVECTOR,
        sa.Computed("setweight(to_tsvector('english', coalesce(title, '')), 'A') || ...", persisted=True),
    ),
    sa.UniqueConstraint("seller_id", "sku"),
    sa.CheckConstraint("price >= 0", name="price_nonnegative"),
    sa.CheckConstraint("jsonb_typeof(attributes) = 'object'", name="attributes_is_object"),
    sa.Index("products_active_category_id_price_idx", "category_id", "price",
             postgresql_where=sa.text("status = 'active'")),
    sa.Index("products_attributes_idx", "attributes",
             postgresql_using="gin", postgresql_ops={"attributes": "jsonb_path_ops"}),
    ...
)
```

Read it column by column; each line carries one decision from earlier modules:

- `sa.Identity(always=True)` is `GENERATED ALWAYS AS IDENTITY` (Module 02). SQLAlchemy will not send a value for `id` and reads it back with `RETURNING`.
- `nullable=False` is `NOT NULL`. Columns are nullable by default, except primary keys.
- `server_default=` is a `DEFAULT` that the *database* applies. A plain string is quoted as a literal (`'draft'`); `sa.text(...)` and `sa.func.now()` are passed as SQL. (There is also `default=`, a Python-side default that SQLAlchemy computes and sends. Bazaar uses server defaults, so that `psql`, the plain-SQL repository and Core all get the same value.)
- `sa.Computed(..., persisted=True)` is a stored generated column (Module 13). SQLAlchemy never writes it.
- Constraints and indexes, with PostgreSQL options as `postgresql_*` keywords: `postgresql_where` (partial index), `postgresql_using` (GIN, GiST, BRIN), `postgresql_ops` (operator classes), `postgresql_include` (covering index).

`products.c` is the column collection: `products.c.price` is the `Column` object you use in expressions.

### 4.2 PostgreSQL types and their Python values

[`examples/03_types.py`](examples/03_types.py) reads a product through the table above:

```text
products.id          int            20
products.price       Decimal        Decimal('24.99')
products.status      ProductStatus  <ProductStatus.ACTIVE: 'active'>
products.attributes  dict           {'vegan': True, 'skin_type': 'sensitive', 'volume_ml': 15}
products.tags        list           ['beauty', 'fragrance', 'gift']
products.created_at  datetime       datetime.datetime(2022, 2, 10, 19, 59, 52, tzinfo=zoneinfo.ZoneInfo(key='Etc/UTC'))
users.email       str            'steven.durand17@example.com'
promotions.active_during Range(lower=datetime.datetime(2023, 11, 20, 0, 0, tzinfo=...), upper=datetime.datetime(2023, 11, 28, 0, 0, tzinfo=...), bounds='[)', empty=False)
events.session_id UUID('a13ecde2-231f-9484-c373-c1cd1d25ba1e')
```

| PostgreSQL | SQLAlchemy type | Python value | Notes |
|---|---|---|---|
| `bigint`, `int`, `smallint` | `BigInteger`, `Integer`, `SmallInteger` | `int` | |
| `numeric(12,2)` | `Numeric(12, 2)` | `Decimal` | `asdecimal=True` by default. Never `Float` for money. |
| `text`, `varchar(n)`, `char(2)` | `Text`, `String(n)`, `CHAR(2)` | `str` | |
| `boolean` | `Boolean` | `bool` | |
| `timestamptz` | `DateTime(timezone=True)` | aware `datetime` | in the session `TimeZone` (here UTC). `DateTime()` alone means `timestamp without time zone`. |
| `date` | `Date` | `date` | |
| `jsonb` | `postgresql.JSONB` | `dict`/`list`/... | serialized with `json.dumps`; no `Jsonb()` wrapper needed |
| `text[]` | `postgresql.ARRAY(Text)` | `list` | |
| enum type | `postgresql.ENUM(..., create_type=False)` | `str` or a Python `Enum` | section 4.3 |
| domain | `postgresql.DOMAIN(..., create_type=False)` | base type | |
| `uuid` | `postgresql.UUID(as_uuid=True)` (or `Uuid`) | `uuid.UUID` | |
| `tstzrange` | `postgresql.TSTZRANGE` | `postgresql.Range` | `Range(lower, upper, bounds='[)')` |
| `tsvector` | `postgresql.TSVECTOR` | `str` | you rarely read it |

Writing works the other way round. In the same example, an insert with `attributes={"ram_gb": 16, "storage": {...}}`, `tags=["demo", "m24"]` and `active_during=Range(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 8, tzinfo=UTC))` needs no adapters, and the range operator works in queries:

```python
contains = conn.execute(
    sa.select(promotions.c.active_during.contains(datetime(2026, 1, 3, tzinfo=UTC)))
    .where(promotions.c.product_id == pid)
).scalar_one()                         # True
```

Compare this with Module 22, where enums needed `register_enum()` per connection and JSONB needed `Jsonb()`: the type information now lives in the `Table`, so every connection gets it.

### 4.3 Existing enums and domains: `create_type=False`

```python
order_status = pg.ENUM(
    OrderStatus, name="order_status", create_type=False,
    values_callable=lambda e: [m.value for m in e],
)
email_address = pg.DOMAIN("email_address", sa.Text(), create_type=False)
```

`pg.ENUM` (and `DOMAIN`) are types that SQLAlchemy *can* create and drop. By default, `metadata.create_all()` would emit `CREATE TYPE order_status AS ENUM (...)` before creating a table that uses it, and `drop_all()` would drop it. Bazaar's types already exist (Module 08) and belong to the SQL files, so `create_type=False` says: this type is someone else's; just use its name.

Passing the Python `OrderStatus` enum makes results come back as enum members. `values_callable` tells SQLAlchemy to use the members' *values* (`'pending'`) rather than their *names* (`'PENDING'`) as database labels; without it, inserting `OrderStatus.PENDING` would send `'PENDING'`, which the database rejects.

> **Pitfall:** adding a value to a PostgreSQL enum (`ALTER TYPE order_status ADD VALUE 'on_hold'`) does not update your Python `Enum`. Reading a row with the new value then fails in SQLAlchemy's enum processing. Deploy the code that knows the new value *before* the database starts producing it. Module 26 treats this as an expand/contract migration.

### 4.4 Naming conventions

Every constraint and index has a name in PostgreSQL. If you don't give one, Postgres invents it (`products_pkey`, `products_seller_id_fkey`, `products_seller_id_sku_key`), and SQLAlchemy, by default, lets it. That's fine until you need to *refer* to a constraint: `ON CONFLICT ON CONSTRAINT ...`, the drift test, or a migration that drops it (Module 26). A `naming_convention` on the `MetaData` makes the names deterministic. Bazaar's reproduces PostgreSQL's own defaults, so names generated from Python and names created by the SQL files agree:

```python
NAMING_CONVENTION = {
    "pk": "%(table_name)s_pkey",
    "fk": "%(table_name)s_%(column_0_name)s_fkey",
    "uq": "%(table_name)s_%(column_0_N_name)s_key",
    "ix": "%(table_name)s_%(column_0_N_name)s_idx",
    "ck": "%(table_name)s_%(constraint_name)s",
}
```

`column_0_N_name` joins all column names (`seller_id_sku`). For CHECK constraints there is no column to derive a name from, so the convention prefixes the table name to the name you give: `CheckConstraint(..., name="price_nonnegative")` becomes `products_price_nonnegative`. Expression indexes (`lower(email)`) have no column name either; give those an explicit name.

### 4.5 What metadata is for, and what it is not for

`metadata.create_all(engine)` creates every table (in foreign-key order) that doesn't exist yet; `drop_all()` drops them. It's perfect for throwaway schemas: a benchmark table ([`examples/07_bulk_insert_benchmark.py`](examples/07_bulk_insert_benchmark.py) creates and drops its own), a test database, a scratch schema (Exercise 11).

It is **not** a migration tool. `create_all()` never alters an existing table: it won't add a column, change a type or create a missing index on an existing table. Bazaar's schema is owned by the SQL files in `project/db/` until Module 26, where Alembic takes over and uses this same metadata to *generate* migrations. Until then, the metadata is a description, and descriptions drift. Section 5.3 catches that.

> **Under the hood:** views have no place in a `MetaData` that you `create_all()`: SQLAlchemy would create them as tables. Bazaar describes `active_products` and `seller_sales_daily` as `Table` objects in a **separate** `view_metadata`, so queries can use them like tables while `create_all()` never touches them.

---

## 5. Reflection

### 5.1 Loading tables from the database

Instead of describing a table, you can ask the database:

```python
reviews = sa.Table("reviews", sa.MetaData(), autoload_with=engine)
```

[`examples/11_reflection.py`](examples/11_reflection.py):

```text
1. Table('reviews', autoload_with=engine) in 174 ms
   id          BIGINT()               nullable=False
   product_id  BIGINT()               nullable=False
   user_id     BIGINT()               nullable=False
   rating      SMALLINT()             nullable=False
   title       TEXT()                 nullable=True
   body        TEXT()                 nullable=True
   created_at  TIMESTAMP(timezone=True) nullable=False
   constraints: ['reviews_pkey', 'reviews_product_id_fkey', 'reviews_product_id_user_id_key', 'reviews_rating_range', 'reviews_user_id_fkey']
   foreign keys pulled in: ['categories', 'products', 'reviews', 'sellers', 'users']

2. MetaData.reflect(views=True): 45 tables/views in 68 ms
   partitions are tables too: ['events_y2025m01', 'events_y2025m02', 'events_y2025m03']
```

Reflecting one table follows its foreign keys and reflects `products`, `users`, and *their* targets too: five tables for one. (The first call also pays for the engine's first connection, hence 174 ms against 68 ms for everything.) `MetaData.reflect()` loads the whole schema in one go, partitions of `events` included.

### 5.2 The Inspector

When you want answers rather than `Table` objects, `sa.inspect(engine)` (or a connection) returns an `Inspector` with methods like `get_table_names()`, `get_columns()`, `get_pk_constraint()`, `get_foreign_keys()`, `get_indexes()`, `get_unique_constraints()`, `get_check_constraints()`, `get_enums()`, `get_view_names()`, `get_table_comment()`:

```text
4. inspector.get_indexes('orders'):
    orders_seller_id_created_at_idx ['seller_id', 'created_at'] {'created_at': ('desc',)}
    orders_shipping_address_id_idx ['shipping_address_id']
    orders_user_id_created_at_idx ['user_id', 'created_at'] {'created_at': ('desc',)}
   get_check_constraints('inventory'): ['inventory_quantity_nonnegative', 'inventory_reserved_within_quantity']
   enums: [('order_status', ['pending', 'paid', 'shipped', 'delivered', 'cancelled', 'refunded']), ...]
```

Exercise 8 builds a Markdown data dictionary from these calls.

### 5.3 Trade-offs, and the drift test

Reflection is tempting: no table definitions to write. Three things argue against using it as your *only* schema description in an application:

- **It happens at run time.** Every process start pays the round trips, and the code's correctness depends on the database it happened to connect to. Your editor, type checker and code review see nothing.
- **It knows less than you do.** From the same example:

  ```text
  3. products.status: ENUM('draft', 'active', 'archived', name='product_status')
     python_type of status: <class 'str'>
  ```

  A reflected enum returns strings, not `OrderStatus` members; a reflected JSONB column doesn't know it should be a `MutableDict` (Module 25); and expressions come back as normalized text (`price >= 0::numeric`, a `search_vector` expression with `'english'::regconfig` casts).
- **It hides intent.** A declared table says "this is what the code relies on". Reflection says "whatever is there".

So Bazaar declares its tables and uses reflection for the opposite job: **checking** the declaration. [`bazaar/sa/drift.py`](../../project/python/src/bazaar/sa/drift.py) compares a `MetaData` with the live schema through the Inspector: tables, columns, compiled types, nullability, identity and generated columns, server-default presence, primary keys, foreign keys with `ON DELETE`, unique and check constraint names, and index names with their uniqueness. The milestone test is one line:

```python
def test_tables_match_database(sa_conn):
    assert compare(sa_conn, metadata, view_metadata) == []
```

A comparison is only as good as its failures, so a second test changes the schema inside its rolled-back transaction and expects precise complaints:

```text
jobs.kind: type text in metadata, varchar(40) in database
products.sku: nullable=False in metadata, True in database
products: index ('products_tags_idx', False) only in metadata
reviews.helpful_votes: column not in metadata
wishlist: table not in metadata
```

What this comparison cannot see: CHECK *expressions* (`rating BETWEEN 1 AND 10` with the old name passes) and index *definitions* behind a matching name. Exercise 11 closes that gap by creating the metadata in a scratch schema and letting PostgreSQL compare canonical definitions; for Bazaar's metadata that reports zero differences across all 101 constraints and indexes. Module 26's Alembic `autogenerate` does a similar comparison to write migrations.

---

## 6. The SQL expression language

The examples in this section come from [`examples/04_select.py`](examples/04_select.py) and [`examples/05_advanced_select.py`](examples/05_advanced_select.py), which print the SQL the psycopg dialect generates and the first rows. Run a single block with `python 04_select.py C1`.

### 6.1 `select()`, `where()`, `order_by()`, `limit()`

```python
p = products
stmt = (
    sa.select(p.c.id, p.c.title, p.c.price)
    .where(p.c.category_id == 3, p.c.status == "active", p.c.price < 500)
    .order_by(p.c.price.desc(), p.c.id)
    .limit(3)
)
```

```text
SELECT products.id, products.title, products.price
FROM products
WHERE products.category_id = %(category_id_1)s::INTEGER AND products.status = %(status_1)s AND products.price < %(price_1)s::INTEGER ORDER BY products.price DESC, products.id
 LIMIT %(param_1)s::INTEGER
    (14120, 'Quanta Wireless Ultrabook 512', Decimal('499.99'))
    (19596, 'Brightline Pro Gaming Laptop 149', Decimal('499.99'))
    (20228, 'Zentro Studio Laptop 508', Decimal('499.99'))
```

The mechanics:

- `p.c.category_id == 3` does not compare anything. Python calls `Column.__eq__`, which returns a `BinaryExpression` holding the column, the operator and a **bind parameter** for `3`. All values become parameters (`%(category_id_1)s`), never literals in the SQL text.
- `.where(a, b, c)` ANDs its arguments; calling `.where()` again ANDs more. Each call returns a **new** statement: statements are immutable, so you can build a base query once and derive variants from it safely.
- The FROM clause is inferred from the columns you use.
- `::INTEGER`, `::VARCHAR`: the psycopg dialect adds **bind casts** to parameters. PostgreSQL's extended protocol needs a type for every parameter (Module 22, section 4.2), and the cast tells it which one SQLAlchemy meant. The cast comes from the type of the *Python value*, not always the column: `price < 500` sends `500::INTEGER`, which is harmless for a comparison. Section 6.9 shows when it isn't.

Operators you will use constantly:

```python
p.c.seller_id.in_([7, 8, 9])                 # IN (...); .not_in([...])
p.c.price.between(Decimal("10"), Decimal("20"))
p.c.title.ilike("%lamp%") | p.c.title.ilike("%light%")   # | is OR, & is AND, ~ is NOT
p.c.description.is_not(None)                 # IS NOT NULL; .is_(None) is IS NULL
sa.not_(p.c.tags.any_() == "sale")           # NOT (... = ANY (tags))
sa.and_(a, b), sa.or_(a, b)                  # when you build lists of conditions
p.c.title.startswith("Voltix"), .contains("lamp"), .endswith("527")
```

> **Pitfall:** Python's `and`, `or` and `not` cannot be overloaded. `p.c.price > 100 and p.c.status == "active"` raises `TypeError: Boolean value of this clause is not defined` (SQLAlchemy refuses to guess). Use `&`, `|`, `~` (with parentheses: `(a > 1) & (b < 2)`, because `&` binds tighter than `>`), `sa.and_()`, or several arguments to `where()`. Exercise 3 collects this and four other look-alikes.

### 6.2 Joins

```python
sa.select(o.c.id, users.c.full_name, sellers.c.shop_name, o.c.total)
    .join(users, users.c.id == o.c.user_id)
    .join(sellers, sellers.c.id == o.c.seller_id)
    .where(o.c.created_at >= datetime(2025, 12, 31, tzinfo=UTC))
    .order_by(o.c.total.desc())
    .limit(3)
```

```text
FROM orders JOIN users ON users.id = orders.user_id JOIN sellers ON sellers.id = orders.seller_id
    (499898, 'Richard Hill', 'Bright Corner 49', Decimal('7413.93'))
    (499492, 'Olivia Andersson', 'Rustic Emporium 5', Decimal('6515.93'))
    (499476, 'Marco Martinez', 'Nordic Shop 1936', Decimal('6167.95'))
```

`.join(target, onclause)` joins from the statement's FROM; `.outerjoin()` is `LEFT OUTER JOIN`; `select_from()` sets the left side explicitly; `sa.join(a, b, on)` builds a join object you can pass around.

The ON clause is optional when exactly one foreign key path exists, and that is a trap:

```python
    .join(users, users.c.id == o.c.user_id)
    .join(sellers)                       # ON clause inferred... from which table?
```

```text
FROM orders JOIN users ON users.id = orders.user_id JOIN sellers ON users.id = sellers.user_id
    (499427, 'Wei Davis', 'Golden Goods 963', Decimal('4049.97'))
    (499832, 'Olga Williams', 'Wild Goods 1883', Decimal('4004.92'))
    (499494, 'Søren Thompson', 'Happy Store 6', Decimal('2510.94'))
```

SQLAlchemy inferred the join from `users` (the most recent join target, which has a foreign key from `sellers.user_id`), not from `orders.seller_id`. The query runs and returns plausible rows: "orders placed by people who also own a shop, with the buyer's own shop". No error, wrong report. **Always write the ON clause** when the tables have more than one relationship path.

An anti-join, "users who never ordered", the `LEFT JOIN ... IS NULL` way and the `NOT EXISTS` way (section 6.5); both return the 15,942 from the data dictionary:

```python
sa.select(sa.func.count()).select_from(users.outerjoin(o, o.c.user_id == users.c.id)).where(o.c.id.is_(None))
```

### 6.3 Aggregates, `GROUP BY`, `HAVING`, `FILTER`

```python
revenue = sa.func.sum(o.c.total).label("revenue")
stmt = (
    sa.select(
        o.c.seller_id,
        sa.func.count().label("orders"),
        sa.func.count().filter(o.c.status == "refunded").label("refunded"),
        revenue,
    )
    .where(o.c.status.in_(["paid", "shipped", "delivered", "refunded"]),
           o.c.created_at >= datetime(2025, 1, 1, tzinfo=UTC))
    .group_by(o.c.seller_id)
    .having(sa.func.sum(o.c.total) > 1_000_000)
    .order_by(revenue.desc())
    .limit(3)
)
```

```text
SELECT orders.seller_id, count(*) AS orders, count(*) FILTER (WHERE orders.status = %(status_1)s) AS refunded, sum(orders.total) AS revenue
FROM orders
WHERE orders.status IN (__[POSTCOMPILE_status_2]) AND orders.created_at >= %(created_at_1)s::TIMESTAMP WITH TIME ZONE GROUP BY orders.seller_id
HAVING sum(orders.total) > %(sum_1)s::INTEGER ORDER BY revenue DESC
 LIMIT %(param_1)s::INTEGER
    (1, 29075, 888, Decimal('19028027.32'))
    (2, 18778, 532, Decimal('11838846.15'))
    (5, 16551, 516, Decimal('11034909.00'))
```

- `sa.func.<name>(...)` renders any SQL function by name: `func.sum`, `func.count`, `func.coalesce`, `func.date_trunc`, `func.websearch_to_tsquery`, your own `func.place_order`. Unknown names are passed through as written, so `func.anything()` works; known ones (`sum`, `count`, `now`, ...) also get a sensible return type.
- `.label("revenue")` names the column (`AS revenue`) and lets you reuse the labeled expression in `order_by()`, which renders as `ORDER BY revenue`.
- `.filter(cond)` on an aggregate is PostgreSQL's `FILTER (WHERE ...)` (Module 05).
- `__[POSTCOMPILE_status_2]` is a placeholder for an IN-list that is expanded at execution time into one parameter per element. The statement (and its cache entry) stays the same whatever the list length.

### 6.4 Aliases and self-joins

A table used twice needs an alias, exactly as in SQL:

```python
parent = c.alias("parent")
sa.select(c.c.name, parent.c.name.label("parent"))
    .join(parent, c.c.parent_id == parent.c.id)
    .where(parent.c.parent_id.is_(None))
```

```text
FROM categories JOIN categories AS parent ON categories.parent_id = parent.id
    ('Fragrance', 'Beauty')
    ('Skincare', 'Beauty')
    ("Children's Books", 'Books')
    ('Fiction', 'Books')
```

### 6.5 Subqueries, scalar subqueries, `EXISTS`

Three shapes, three methods:

```python
# 1. a correlated scalar subquery in the SELECT list
review_count = (
    sa.select(sa.func.count())
    .where(reviews.c.product_id == p.c.id)        # refers to the outer query
    .scalar_subquery()
    .label("reviews")
)
sa.select(p.c.id, p.c.title, review_count).where(p.c.id.in_([1, 2, 3]))

# 2. EXISTS / NOT EXISTS
has_orders = sa.exists().where(o.c.user_id == users.c.id)
sa.select(sa.func.count()).select_from(users).where(~has_orders)          # 15942

# 3. a subquery in FROM: .subquery() gives it a name and a .c collection
big = sa.select(o.c.seller_id, sa.func.max(o.c.total).label("biggest")).group_by(o.c.seller_id).subquery("big")
sa.select(sellers.c.shop_name, big.c.biggest).join(big, big.c.seller_id == sellers.c.id)
```

```text
SELECT products.id, products.title, (SELECT count(*) AS count_1
FROM reviews
WHERE reviews.product_id = products.id) AS reviews
FROM products ...
    (1, 'Fernweh Slim-Fit Tank Top - Black', 0)
    (2, 'The Burning Protocol', 167)
    (3, 'Velocita Pro Dumbbell Set', 138)
```

**Correlation** is automatic: a subquery that mentions a table from the enclosing query refers to it (`WHERE reviews.product_id = products.id`) instead of adding it to its own FROM. That is usually what you want; when it isn't, `.correlate(...)` / `.correlate_except(...)` control it. If correlation removes *every* FROM element of a subquery, SQLAlchemy raises "returned no FROM clauses due to auto-correlation". The first draft of the category roll-up in `bazaar/sa/reports.py` hit exactly that, and the fix was an aggregate `FILTER` instead of a subquery.

### 6.6 CTEs, including recursive ones

`.cte()` turns a select into a `WITH` query. A recursive CTE is a CTE whose definition is extended with `.union_all()` of a select that refers to it, which is Module 06's category walk:

```python
tree = (
    sa.select(c.c.id, c.c.name, sa.literal(0).label("depth"))
    .where(c.c.slug == "electronics")
    .cte("tree", recursive=True)
)
child = c.alias("child")
tree = tree.union_all(
    sa.select(child.c.id, child.c.name, tree.c.depth + 1).join(child, child.c.parent_id == tree.c.id)
)
sa.select(tree.c.depth, sa.func.count().label("categories")).group_by(tree.c.depth).order_by(tree.c.depth)
```

```text
WITH RECURSIVE tree(id, name, depth) AS
(SELECT categories.id AS id, categories.name AS name, %(param_1)s::INTEGER AS depth
FROM categories
WHERE categories.slug = %(slug_1)s::VARCHAR UNION ALL SELECT child.id AS id, child.name AS name, tree.depth + %(depth_1)s::INTEGER AS anon_1
FROM tree JOIN categories AS child ON child.parent_id = tree.id)
 SELECT tree.depth, count(*) AS categories
FROM tree GROUP BY tree.depth ORDER BY tree.depth
    (0, 1)
    (1, 4)
    (2, 12)
```

The CTE object is a value: the same `tree` joined to products counts the 44,342 active products anywhere under Electronics. This is where Core starts paying for itself: `bazaar.sa.reports.category_tree_cte()` and `category_closure_cte()` are functions returning CTEs, reused by the roll-up report and by Exercise 5's listing ("everything under *audio*").

### 6.7 Window functions

`func.xxx().over(partition_by=..., order_by=..., rows=(start, end))`:

```python
rank = (
    sa.func.row_number()
    .over(partition_by=p.c.category_id, order_by=(p.c.price.desc(), p.c.id))
    .label("rank")
)
ranked = (
    sa.select(p.c.category_id, p.c.id, p.c.title, p.c.price, rank)
    .where(p.c.status == "active", p.c.category_id.in_([3, 4]))
    .subquery()
)
sa.select(ranked).where(ranked.c.rank <= 2).order_by(ranked.c.category_id, ranked.c.rank)
```

```text
    (3, 88397, 'Nordvik Lite Laptop 233', Decimal('2989.99'), 1)
    (3, 92923, 'Helix Slim Notebook 151', Decimal('2989.99'), 2)
    (4, 58180, 'Sonora Ultra Portable Monitor 673', Decimal('999.99'), 1)
    (4, 64352, 'Quanta Portable Monitor 678', Decimal('999.99'), 2)
```

Top-N per group needs the subquery because a window function cannot appear in `WHERE` (Module 06). Frames are tuples relative to the current row, `None` meaning unbounded: `rows=(None, 0)` is `ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW`, `rows=(-6, 0)` is a 7-row moving window:

```python
sa.func.sum(daily.c.revenue).over(order_by=daily.c.day, rows=(None, 0)).label("to_date"),
sa.func.avg(daily.c.revenue).over(order_by=daily.c.day, rows=(-6, 0)).cast(sa.Numeric(12, 2)).label("avg_7d"),
```

```text
SELECT anon_1.day, anon_1.revenue, sum(anon_1.revenue) OVER (ORDER BY anon_1.day ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS to_date, CAST(avg(anon_1.revenue) OVER (ORDER BY anon_1.day ROWS BETWEEN %(param_1)s::INTEGER PRECEDING AND CURRENT ROW) AS NUMERIC(12, 2)) AS avg_7d
...
    (datetime.datetime(2025, 12, 1, 0, 0, tzinfo=...), Decimal('760167.37'), Decimal('760167.37'), Decimal('760167.37'))
    (datetime.datetime(2025, 12, 2, 0, 0, tzinfo=...), Decimal('770542.06'), Decimal('1530709.43'), Decimal('765354.72'))
    (datetime.datetime(2025, 12, 3, 0, 0, tzinfo=...), Decimal('760820.41'), Decimal('2291529.84'), Decimal('763843.28'))
```

An unnamed `.subquery()` becomes `anon_1`. Name your subqueries when you will read the SQL in logs.

### 6.8 `LATERAL`

A lateral subquery may refer to columns of FROM items to its left (Module 04): "for each X, the top N of Y".

```python
recent = (
    sa.select(o.c.id, o.c.created_at, o.c.total)
    .where(o.c.user_id == sa.literal_column("u.id"))
    .order_by(o.c.created_at.desc())
    .limit(2)
    .lateral("recent")
)
u = sa.select(sa.func.unnest(sa.literal([8, 13, 11])).label("id")).subquery("u")
sa.select(u.c.id.label("user_id"), recent.c.id, recent.c.total)
    .select_from(u).join(recent, sa.true())
    .order_by(u.c.id, recent.c.created_at.desc())
```

```text
FROM (SELECT unnest(%(param_1)s) AS id) AS u JOIN LATERAL (SELECT orders.id AS id, orders.created_at AS created_at, orders.total AS total
FROM orders
WHERE orders.user_id = u.id ORDER BY orders.created_at DESC
 LIMIT %(param_2)s::INTEGER) AS recent ON true ORDER BY u.id, recent.created_at DESC
    (8, 495378, Decimal('187.99'))
    (8, 481208, Decimal('622.98'))
    (11, 482174, Decimal('7.99'))
    ...
```

`JOIN LATERAL ... ON true` is spelled `.join(lateral, sa.true())`. `literal_column("u.id")` inserts raw SQL text for the reference, because `u` is defined after `recent` in the Python code; in a different order you would use `u.c.id` directly.

`LATERAL` also fixed a real performance problem in this module's milestone. The first version of `seller_statement_query()` joined each order to `(SELECT order_id, sum(quantity) FROM order_items GROUP BY order_id)`: correct, and 0.8 s per statement, because PostgreSQL does not push a join condition into an aggregated subquery, so it aggregated all 1.27 million order lines to report on 79 orders. Rewritten with lateral subqueries that aggregate one order at a time through the primary-key index, the same statement takes 13 ms. Read the plan (Module 12) of every report you write; generated SQL deserves the same scrutiny as handwritten SQL.

### 6.9 Set operations, `CASE`, casts, literals

```python
expensive = sa.select(p.c.id, sa.literal("expensive").label("why")).where(p.c.price > 2980)
popular = sa.select(reviews.c.product_id, sa.literal("popular")).group_by(reviews.c.product_id).having(sa.func.count() > 120)
sa.union_all(expensive, popular).order_by("why", "id")       # also union, intersect, except_

band = sa.case(
    (p.c.price < 20, "budget"),
    (p.c.price < 200, "mid"),
    else_="premium",
).label("band")
stmt = (
    sa.select(
        band,
        sa.func.count().label("products"),
        sa.cast(sa.func.avg(p.c.price), sa.Numeric(10, 2)).label("avg_price"),
        sa.literal_column("round(100.0 * count(*) / sum(count(*)) over (), 1)").label("pct"),
    )
    .where(p.c.status == "active")
    .group_by(band)
    .order_by(sa.func.min(p.c.price))
)
```

```text
    ('budget', 34895, Decimal('13.58'), Decimal('20.5'))
    ('mid', 97016, Decimal('74.21'), Decimal('57.1'))
    ('premium', 38127, Decimal('631.17'), Decimal('22.4'))
```

- `sa.case((cond, value), ..., else_=...)` is a searched `CASE`. Grouping by the labeled expression repeats the whole `CASE` in `GROUP BY`, which PostgreSQL requires.
- `sa.cast(expr, Type)` (or `expr.cast(Type)`) renders `CAST(... AS ...)` and also sets the Python-side type for result processing. `sa.type_coerce(expr, Type)` changes only the Python-side type, without a SQL cast.
- `sa.literal(value)` is a bound value used as an expression. `sa.literal_column("sql text")` is raw SQL text treated as a column: an escape hatch for expressions you'd rather write by hand. It is not parameterized: never put user input in it.

> **Pitfall:** a Python `float` is bound as `double precision`. Two consequences seen in this module's exercises. First, `func.round(100.0 * x / y, 1)` fails with `function round(double precision, integer) does not exist`, because PostgreSQL only has a two-argument `round()` for `numeric`. Second, and worse, `sa.func.sum(p.c.price * 1.1)` computes in double precision and then SQLAlchemy coerces the result back to the column's `Numeric(12,2)`: it silently rounds to cents. Use `Decimal("1.1")` for anything numeric.

### 6.10 Dynamic queries and keyset pagination

Because statements are immutable values, optional filters are just conditional method calls:

```python
def product_page(category_id=None, max_price=None, after=None, limit=3):
    stmt = sa.select(p.c.id, p.c.title, p.c.price, p.c.created_at).where(p.c.status == "active")
    if category_id is not None:
        stmt = stmt.where(p.c.category_id == category_id)
    if max_price is not None:
        stmt = stmt.where(p.c.price <= max_price)
    if after is not None:
        stmt = stmt.where(sa.tuple_(p.c.price, p.c.id) > sa.tuple_(*after))
    return stmt.order_by(p.c.price, p.c.id).limit(limit)
```

`sa.tuple_(a, b) > sa.tuple_(x, y)` is the row comparison `(price, id) > (x, y)` from Module 12's keyset pagination. Page 2 starts strictly after the last row of page 1:

```text
--- K. keyset page 1
    (7564, 'Kairo Portable Earbuds 612', Decimal('18.99'), ...)
    (15468, 'Orbitron Ultra Over-Ear Headphones 428', Decimal('18.99'), ...)
    (17375, 'Nordvik Smart In-Ear Monitors 448', Decimal('18.99'), ...)
--- K2. keyset page 2
WHERE ... AND (products.price, products.id) > (%(param_1)s, %(param_2)s::INTEGER) ORDER BY products.price, products.id
    (24163, 'Orbitron Lite In-Ear Monitors 671', Decimal('18.99'), ...)
```

The ties at 18.99 are why the `id` tie-breaker exists. Compare this with `orders_query()` in Module 22: no string assembly, no risk of a missing space or `AND`, and each variant is still one plain, independently planned statement. Exercise 5 extends it to eight filters, four sort orders (including mixed directions, which a row comparison cannot express) and opaque cursors.

---

## 7. Writing data

### 7.1 `insert`, `update`, `delete`, `RETURNING`

From [`examples/06_postgres_specific.py`](examples/06_postgres_specific.py), which rolls everything back:

```python
sa.insert(cart_items).values(user_id=7, product_id=20, quantity=1).returning(cart_items.c.quantity, cart_items.c.added_at)
```

```text
INSERT INTO cart_items (user_id, product_id, quantity) VALUES (%(user_id)s::BIGINT, %(product_id)s::BIGINT, %(quantity)s::INTEGER) RETURNING cart_items.quantity, cart_items.added_at
    (1, datetime.datetime(2026, 10, 14, 9, 53, 35, 499452, tzinfo=...))
```

- `insert(t).values(...)` for one row; `conn.execute(insert(t), [dict, dict, ...])` for many (section 7.4).
- `update(t).where(...).values(col=expr)`: values can be expressions (`quantity=inventory.c.quantity + 1`).
- `delete(t).where(...)`.
- `.returning(...)` on all three (Module 03). `result.rowcount` tells you how many rows a statement without `RETURNING` touched.

PostgreSQL's multi-table forms appear automatically when the `WHERE` clause mentions another table:

```python
sa.update(inventory).where(inventory.c.product_id == p.c.id, p.c.seller_id == 2000).values(quantity=inventory.c.quantity + 1)
sa.delete(cart_items).where(cart_items.c.product_id == p.c.id, p.c.seller_id == 2)
```

```text
UPDATE inventory SET quantity=(inventory.quantity + %(quantity_1)s::INTEGER) FROM products WHERE inventory.product_id = products.id AND products.seller_id = %(seller_id_1)s::BIGINT RETURNING ...
DELETE FROM cart_items USING products WHERE cart_items.product_id = products.id AND products.seller_id = %(seller_id_1)s::BIGINT
    rowcount: 1632
```

For a batch of *different* values per row, `sa.values(...)` builds a `VALUES` list you can join in `UPDATE ... FROM` (Exercise 7).

### 7.2 `INSERT ... ON CONFLICT`

Upserts are PostgreSQL-specific, so they come from the dialect's own `insert`: `from sqlalchemy.dialects.postgresql import insert`.

```python
ins = pg.insert(cart_items).values(user_id=7, product_id=20, quantity=2)
upsert = ins.on_conflict_do_update(
    index_elements=[cart_items.c.user_id, cart_items.c.product_id],
    set_={"quantity": cart_items.c.quantity + ins.excluded.quantity},
).returning(cart_items.c.quantity, sa.literal_column("xmax = 0").label("inserted"))
```

```text
INSERT INTO cart_items (user_id, product_id, quantity) VALUES (...) ON CONFLICT (user_id, product_id) DO UPDATE SET quantity = (cart_items.quantity + excluded.quantity) RETURNING cart_items.quantity, xmax = 0 AS inserted
    (3, False)
```

- `ins.excluded` is the row that was proposed for insertion (`EXCLUDED` in SQL).
- `index_elements` names the conflict target by columns, or by **expressions** for an expression index: Bazaar's case-insensitive e-mail uniqueness (Module 08) is `index_elements=[sa.func.lower(users.c.email)]`, which renders `ON CONFLICT (lower(email))`.
- `constraint="products_seller_id_sku_key"` names it by constraint instead (`ON CONFLICT ON CONSTRAINT ...`), and `where=` adds the `DO UPDATE ... WHERE` condition. A conflicting row that fails the condition is left alone and returns nothing.
- `on_conflict_do_nothing()` returns no row for the conflicting input, so `RETURNING` cannot tell you the existing id; select it afterwards if you need it.
- `xmax = 0` in `RETURNING` is the inserted-or-updated trick from Module 22's importer.

### 7.3 Calling database functions

A function call is an expression, so a set-returning function such as `place_order()` (Module 10) is just `select(func.place_order(...))`. Bind the JSONB argument with its type, so the dialect sends JSON and casts it:

```python
call = sa.select(
    sa.func.place_order(
        8, sa.bindparam("items", [{"product_id": pid, "quantity": 1}], type_=pg.JSONB)
    ).label("order_id")
)
```

```text
SELECT place_order(%(place_order_1)s::INTEGER, %(items)s::JSONB) AS order_id
    (500056,)
```

A function that returns a table goes in the FROM clause with `.table_valued(<column names>)`:

```python
dq = sa.func.dequeue_jobs("default", "m24-demo", 5).table_valued("id", "kind", "payload")
sa.select(dq.c.id, dq.c.kind)
```

```text
SELECT anon_1.id, anon_1.kind
FROM dequeue_jobs(%(dequeue_jobs_1)s::VARCHAR, %(dequeue_jobs_2)s::VARCHAR, %(dequeue_jobs_3)s::INTEGER) AS anon_1
    (626, 'm24_demo')
```

### 7.4 Many rows: executemany, insertmanyvalues, and COPY

Passing a list of dictionaries to `execute()` inserts many rows with one call:

```python
conn.execute(sa.insert(bench), rows)                                       # 1
conn.execute(sa.insert(bench).returning(bench.c.id, sort_by_parameter_order=True), rows)   # 2
```

The two lines take different paths:

1. **Without `RETURNING`**, SQLAlchemy hands the statement and the whole list to the driver's `executemany()`. psycopg 3 runs that in pipeline mode (Module 22, section 11).
2. **With `RETURNING`**, a driver's `executemany()` can't return rows, so SQLAlchemy 2.0 uses **"insertmanyvalues"**: it rewrites the batch into multi-row statements of up to `insertmanyvalues_page_size` (default 1,000) rows each, and stitches the returned rows back together in parameter order (`sort_by_parameter_order=True`). For a table whose key the server generates, the rows carry a sentinel counter so the order of `RETURNING` can be trusted: `INSERT INTO jobs (kind) SELECT p0::VARCHAR FROM (VALUES (%(kind__0)s::VARCHAR, 0), (%(kind__1)s::VARCHAR, 1)) AS imp_sen(p0, sen_counter) ORDER BY sen_counter RETURNING jobs.id, ...`; otherwise it's a plain `INSERT ... VALUES (...), (...) RETURNING ...`. This is what lets the ORM insert many objects and still learn their generated ids (Module 25).

[`examples/07_bulk_insert_benchmark.py`](examples/07_bulk_insert_benchmark.py) loads the same 100,000 rows as Module 22's benchmark into an equivalent table (second run, warm caches):

```text
insert(table), list of dicts (executemany)             3.16 s     31,627 rows/s
... with RETURNING id (insertmanyvalues)               3.45 s     29,013 rows/s
... with RETURNING, insertmanyvalues_page_size=100     3.32 s     30,150 rows/s
insert().values(list) per 5,000 rows                  10.57 s      9,460 rows/s
COPY through conn.connection.driver_connection         0.27 s    370,647 rows/s
```

- Core's `executemany` path is close to psycopg's own `executemany()` from Module 22 (2.52 s there, about 40,000 rows/s). The difference is SQLAlchemy's per-row parameter processing in Python.
- Getting all generated ids back costs about 10 %.
- `insert().values(list_of_5000_dicts)` is the slowest by far: it builds one statement with 25,000 bind parameters, which must be compiled every time (a different structure is never in the cache). Don't use `.values()` for bulk data.
- **COPY is 10 times faster than anything SQLAlchemy can do**, and SQLAlchemy has no COPY construct. You don't need one: section 8.7 shows how to reach the psycopg connection underneath.

> **In production:** for imports of thousands of rows, use COPY into a staging table and merge with one `INSERT ... SELECT ... ON CONFLICT` (Module 22's `import_products()`), whether the rest of your code is Core, ORM or plain SQL. Use `executemany`/insertmanyvalues for the dozens-to-hundreds of rows a request creates.

---

## 8. PostgreSQL-specific constructs

All from [`examples/06_postgres_specific.py`](examples/06_postgres_specific.py).

### 8.1 JSONB

```python
sa.select(
    p.c.id,
    p.c.attributes["cpu"].astext.label("cpu"),                       # ->> 'cpu'
    p.c.attributes["ram_gb"].as_integer().label("ram"),               # CAST(->> 'ram_gb' AS INTEGER)
    p.c.attributes[("storage", "gb")].astext.label("storage_gb"),     # #>> '{storage,gb}'
).where(
    p.c.category_id == 3,
    p.c.attributes.contains({"cpu": "Ryzon 9"}),                      # @>  (uses the GIN index)
    p.c.attributes.has_key("screen_in"),                              # ?
    p.c.attributes["ram_gb"].as_integer() >= 32,
)
```

```text
SELECT products.id, products.attributes ->> %(attributes_1)s::TEXT AS cpu, CAST(products.attributes ->> %(attributes_2)s::TEXT AS INTEGER) AS ram, products.attributes #>> %(attributes_3)s AS storage_gb
FROM products
WHERE products.category_id = %(category_id_1)s::INTEGER AND products.attributes @> %(attributes_4)s::JSONB AND products.attributes ? %(attributes_5)s::VARCHAR AND CAST((products.attributes ->> %(attributes_6)s::TEXT) AS INTEGER) >= %(param_1)s::INTEGER ORDER BY products.id
    (1947, 'Ryzon 9', 64, '512')
    (2226, 'Ryzon 9', 32, '512')
    (5199, 'Ryzon 9', 64, '2048')
```

| You want (Module 08) | Core |
|---|---|
| `attributes -> 'k'` (jsonb) | `col["k"]` |
| `attributes ->> 'k'` (text) | `col["k"].astext` |
| `(attributes ->> 'k')::int` | `col["k"].as_integer()`, `.as_float()`, `.as_boolean()`, `.as_string()` |
| `attributes #> '{a,b}'` / `#>> '{a,b}'` | `col[("a", "b")]` / `col[("a", "b")].astext` |
| `@>`, `<@` | `.contains(dict)`, `.contained_by(dict)` |
| `?`, `?|`, `?&` | `.has_key(k)`, `.has_any([...])`, `.has_all([...])` |
| jsonpath | `func.jsonb_path_exists(col, path)`, `col.op("@?")(path)` |

The JSONB containment `@>` with a dict is the form the `jsonb_path_ops` GIN index (Module 11) can serve. The `->>` comparison cannot.

> **Pitfall:** psycopg's bind casts break PostgreSQL's usual implicit coercion of untyped literals. `func.jsonb_path_exists(p.c.attributes, '$.storage ? (@.gb >= 2048)')` fails with `function jsonb_path_exists(jsonb, character varying) does not exist`: the Python `str` is sent as `::VARCHAR`, and there is no such function. In psql the same literal would be "unknown" and coerced to `jsonpath`. Give the parameter its real type: `sa.bindparam("path", "$.storage ? (@.gb >= 2048)", pg.JSONPATH)`, and the query counts 1,063 laptops with at least 2 TB. The same applies to any function whose argument type is not text (`regconfig` is handled for you in `to_tsvector`/`websearch_to_tsquery`; your own functions are not).

### 8.2 Arrays

```python
sa.select(
    sa.func.count().filter(p.c.tags.contains(["gift", "eco"])).label("gift_and_eco"),    # @>
    sa.func.count().filter(p.c.tags.overlap(["gift", "eco"])).label("gift_or_eco"),      # &&
    sa.func.count().filter(p.c.tags.any_() == "bestseller").label("bestseller"),         # = ANY(tags)
).where(p.c.status == "active")
```

```text
SELECT count(*) FILTER (WHERE products.tags @> %(tags_1)s::TEXT[]) AS gift_and_eco, count(*) FILTER (WHERE products.tags && %(tags_2)s::TEXT[]) AS gift_or_eco, count(*) FILTER (WHERE %(param_1)s::VARCHAR = ANY (products.tags)) AS bestseller
    (2620, 39859, 13646)
```

`func.unnest(p.c.tags)` turns arrays into rows for grouping (most common tags among active products: electronics 44,342, book 31,855, home 30,270). `col[1]` indexes (1-based, as in SQL), `col[1:3]` slices.

> **Pitfall:** `p.c.tags == ["gift"]` is array *equality*: only products whose tags are exactly `{gift}`. It returns 0 rows, while `.contains(["gift"])` returns 30,093. Exercise 3 again.

### 8.3 Full-text search

```python
q = sa.func.websearch_to_tsquery("english", "wireless headphones -kids")
rank = sa.func.ts_rank(p.c.search_vector, q).label("rank")
sa.select(p.c.id, p.c.title, rank)
    .where(p.c.search_vector.bool_op("@@")(q), p.c.status == "active")
    .order_by(rank.desc(), p.c.id)
    .limit(3)
```

```text
WHERE (products.search_vector @@ websearch_to_tsquery(%(websearch_to_tsquery_1)s::REGCONFIG, %(websearch_to_tsquery_2)s::VARCHAR)) AND products.status = %(status_1)s ORDER BY rank DESC, products.id
    (110124, 'Orbitron Wireless Headphones 251', 0.9991941)
    (127587, 'Quanta Wireless Headphones 447', 0.9991906)
    (134112, 'Helix Wireless Headphones 339', 0.99918926)
```

`col.bool_op("@@")(q)` renders any boolean operator SQLAlchemy has no method for (`.op("...")` does the same for non-boolean results). `col.match("wireless headphones", postgresql_regconfig="english")` is a shortcut for `@@ plainto_tsquery('english', ...)` (643 products). Prefer `websearch_to_tsquery` for user input, as Module 13 explained: it accepts quotes, `or` and `-` and never raises a syntax error.

### 8.4 `DISTINCT ON`

"Each user's latest order", Module 05's favorite:

```python
sa.select(orders.c.user_id, orders.c.id, orders.c.created_at)
    .ext(pg.distinct_on(orders.c.user_id))          # SQLAlchemy 2.0: .distinct(orders.c.user_id)
    .where(orders.c.user_id.in_([8, 11, 13]))
    .order_by(orders.c.user_id, orders.c.created_at.desc())
```

```text
SELECT DISTINCT ON (orders.user_id) orders.user_id, orders.id, orders.created_at ...
    (8, 495378, datetime.datetime(2025, 12, 25, 14, 54, 22, tzinfo=...))
    (11, 482174, datetime.datetime(2025, 12, 14, 11, 5, 53, tzinfo=...))
    (13, 493676, datetime.datetime(2025, 12, 23, 13, 29, 39, tzinfo=...))
```

### 8.5 Row locks: `FOR UPDATE`, `SKIP LOCKED`, `NOWAIT`

```python
sa.select(jobs.c.id).where(jobs.c.queue == "default", jobs.c.done_at.is_(None))
    .order_by(jobs.c.run_at).limit(10)
    .with_for_update(skip_locked=True)
sa.select(inventory.c.quantity).join(p, p.c.id == inventory.c.product_id).where(p.c.id == 20)
    .with_for_update(of=inventory, nowait=True)
```

```text
... LIMIT %(param_1)s::INTEGER FOR UPDATE SKIP LOCKED
... FOR UPDATE OF inventory NOWAIT
```

`with_for_update(read=True)` is `FOR SHARE`, `key_share=True` gives `FOR KEY SHARE`/`FOR NO KEY UPDATE`. The semantics are Module 10's: the locks last until the transaction ends, so the surrounding `engine.begin()` block *is* the lock's lifetime.

### 8.6 Ranges, UUIDs and the rest

Range columns get the range operators as methods: `.contains(value_or_range)`, `.contained_by()`, `.overlaps()` (`&&`), `.strictly_left_of()` (`<<`), `.adjacent_to()` (`-|-`). The value type is `sqlalchemy.dialects.postgresql.Range`. Anything else PostgreSQL offers is reachable through `func.*` and `.op()`/`.bool_op()`.

### 8.7 `COPY`: dropping to the driver

SQLAlchemy has no `COPY` construct, because COPY is not a statement that fits the DBAPI model of "execute, then fetch rows". You don't need a second connection for it: every SQLAlchemy `Connection` wraps a pooled DBAPI connection, and the actual psycopg connection is one attribute away:

```python
with engine.begin() as conn:
    raw = conn.connection.driver_connection        # the psycopg.Connection
    with raw.cursor() as cur, cur.copy("COPY bench_load_sa (sku, seller_id, price, title, observed_at) FROM STDIN") as cp:
        for r in rows:
            cp.write_row([r[c] for c in COLS])
    conn.execute(sa.text("..."))                  # same connection, same transaction
```

- `conn.connection` is SQLAlchemy's pool proxy (`_ConnectionFairy`); `.driver_connection` is the driver object (`dbapi_connection` is the DBAPI-level one, which for psycopg is the same object; for asyncpg under `create_async_engine()` they differ: `driver_connection` is the real `asyncpg.Connection`).
- The raw connection is **inside the same transaction** as the SQLAlchemy connection. Don't commit or close it yourself: let the SQLAlchemy connection do that.
- Exercise 12 goes the other way: it compiles a Core `select()` and streams it out with `COPY (...) TO STDOUT`, binding the parameters client-side through psycopg's `copy(sql, params)`, which is safe, unlike `literal_binds`.

---

## 9. Seeing the SQL

### 9.1 `str()`, `compile()` and `literal_binds`

[`examples/08_compile_and_log.py`](examples/08_compile_and_log.py) compiles one statement four ways:

```python
stmt = sa.select(p.c.id, p.c.title).where(
    p.c.seller_id == 42, p.c.price < Decimal("9.99"), p.c.title.ilike("%o'%")
).limit(2)
```

```text
1. str(stmt) -- the generic dialect, named :params
SELECT products.id, products.title
FROM products
WHERE products.seller_id = :seller_id_1 AND products.price < :price_1 AND lower(products.title) LIKE lower(:title_1)
 LIMIT :param_1

2. compiled for the PostgreSQL dialect (its default driver: psycopg in 2.1)
... WHERE products.seller_id = %(seller_id_1)s::BIGINT AND products.price < %(price_1)s AND products.title ILIKE %(title_1)s::VARCHAR
 LIMIT %(param_1)s::INTEGER
   params: {'seller_id_1': 42, 'price_1': Decimal('9.99'), 'title_1': "%o'%", 'param_1': 2}

3. literal_binds -- for reading/logging only, never for executing
... WHERE products.seller_id = 42 AND products.price < 9.99 AND products.title ILIKE '%%o''%%'
 LIMIT 2
```

- `str(stmt)` uses a generic dialect, which doesn't know `ILIKE` (it emulates it with `lower() LIKE lower()`). Fine for a quick look; misleading for PostgreSQL-specific SQL.
- `stmt.compile(dialect=postgresql.dialect())` or `stmt.compile(engine)` shows exactly what the engine sends. `compiled.params` holds the values.
- `compile_kwargs={"literal_binds": True}` inlines values as literals, so you can paste the query into psql to `EXPLAIN` it. Look at the output: the `'` was doubled (correct) but `%` was doubled too, because the psycopg dialect escapes `%` for its `%(name)s` placeholders. Pasted into psql, `'%%o''%%'` searches for literal `%%`. And literal rendering only exists for simple types; it is a debugging aid, not a way to build SQL. **Never execute literal-bound SQL built from user input.**

### 9.2 The engine log and the statement cache

`echo=True` (or `logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)`) logs each statement with its parameters and a cache note:

```text
    SQL> SELECT products.id
FROM products
WHERE products.seller_id = %(seller_id_1)s::BIGINT AND products.price < %(price_1)s
 LIMIT %(param_1)s::INTEGER
    SQL> [generated in 0.00024s] {'seller_id_1': 42, 'price_1': Decimal('9.99'), 'param_1': 1}
    SQL> SELECT products.id ...
    SQL> [cached since 0.00338s ago] {'seller_id_1': 43, 'price_1': Decimal('9.99'), 'param_1': 1}
```

"generated" means the statement was compiled; "cached since" means its compiled form was found in the engine's **compiled cache**. The cache key is the *structure* of the statement, not its values: the second statement is a new `select()` object with a different seller id, yet compiles to the same SQL, so it is a hit. This is why building statements per call is affordable (section 1.3). "[no key ...]" marks statements that have no cache key at all (the `SAVEPOINT` commands in section 3.3, for example). If the same query keeps logging "generated", something defeats the cache, usually a custom type without `cache_ok = True` (section 11) or values baked into `literal_column()` text, which make every statement structurally different.

### 9.3 Events: timing and counting statements

The engine fires events around every cursor execution. Two listeners give you a per-statement timer:

```python
@event.listens_for(engine, "before_cursor_execute")
def _start(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault("t0", []).append(time.perf_counter())

@event.listens_for(engine, "after_cursor_execute")
def _stop(conn, cursor, statement, parameters, context, executemany):
    ms = (time.perf_counter() - conn.info["t0"].pop()) * 1000
    print(f"{ms:7.2f} ms  {' '.join(statement.split())[:70]}")
```

```text
      55.71 ms  SELECT count(*) AS count_1 FROM products WHERE products.seller_id = %(
      27.34 ms  SELECT count(*) AS count_1 FROM products
```

`bazaar.sa.engine.count_statements(engine)` is the counting version, a context manager that records every statement sent inside it. The milestone tests use it to assert that a report is one statement and, in Module 25, that loading 20 orders with their items takes two queries, not 41.

For big results, `conn.execution_options(stream_results=True, yield_per=5000)` uses a server-side cursor and fetches in batches, the Core spelling of Module 22's named cursors (Exercise 12 streams 78,160 rows that way).

---

## 10. Async Core

`create_async_engine()` gives an `AsyncEngine` with the same API, awaited:

```python
from sqlalchemy.ext.asyncio import create_async_engine

engine = create_async_engine("postgresql+asyncpg://postgres:postgres@localhost/bazaar", pool_size=5)
async with engine.connect() as conn:                       # AsyncConnection
    n = await conn.scalar(sa.select(sa.func.count()).select_from(orders))
    result = await conn.execute(sa.select(orders.c.id, orders.c.status).where(...).limit(2))
    rows = result.all()                                    # the result is already buffered
    stream = await conn.stream(sa.select(orders.c.total).where(orders.c.seller_id == 1))
    total = sum([row.total async for row in stream])       # server-side cursor
async with engine.begin() as conn:                         # BEGIN ... COMMIT
    ...
await engine.dispose()
```

[`examples/09_async_core.py`](examples/09_async_core.py) runs the same code on both async drivers; only the URL differs:

```text
[asyncpg] orders=500000  first two of user 8: [(501, <OrderStatus.DELIVERED: 'delivered'>), (971, <OrderStatus.DELIVERED: 'delivered'>)]
[asyncpg] seller 1, streamed sum of totals: 41831808.31
[asyncpg] 20 revenue queries: sequential 0.26 s, gather 0.12 s
[psycopg] orders=500000  first two of user 8: [(501, <OrderStatus.DELIVERED: 'delivered'>), (971, <OrderStatus.DELIVERED: 'delivered'>)]
[psycopg] seller 1, streamed sum of totals: 41831808.31
[psycopg] 20 revenue queries: sequential 0.29 s, gather 0.10 s
```

The statements are the same objects as in sync code; only execution is awaited. That is the practical win: `bazaar.sa.reports` builds statements in plain functions, and Exercise 9 runs the seller statement on an `AsyncConnection` by reusing `seller_statement_query()` unchanged.

Everything Module 23 taught still holds:

- **One connection per concurrent task.** An `AsyncConnection` runs one statement at a time; `gather` over a shared connection fails. Check out a connection inside each task, as `one()` in the example does.
- **Create the engine inside the running event loop**, and `await engine.dispose()` before the loop ends. An async pool's connections belong to the loop they were created in (the test fixtures create one async engine per test for this reason).
- The asyncpg dialect installs its own JSON codecs and uses prepared statements per connection; behind PgBouncer read section 2.5.

> **Under the hood:** SQLAlchemy's engine and ORM internals are synchronous code. The async extension runs them inside a **greenlet** (a lightweight coroutine from the `greenlet` package) and, at the exact point where the sync code would call the driver, switches out to `await` the async driver. That is why the async API is a thin wrapper rather than a rewrite, and why `greenlet` is required. Module 25 shows the consequence that matters: code that would do IO *implicitly* (lazy loading) cannot work there.

---

## 11. Custom types with `TypeDecorator`

A `TypeDecorator` wraps an existing type and converts values on the way in (`process_bind_param`) and out (`process_result_value`). [`examples/10_money_type.py`](examples/10_money_type.py) turns `numeric(12,2)` into a `Money` value object and refuses floats:

```python
class MoneyType(sa.TypeDecorator):
    impl = sa.Numeric(12, 2)
    cache_ok = True          # instances have no state that changes the SQL

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, Money):
            return value.amount
        if isinstance(value, float):
            raise TypeError("refusing float for money; use Decimal or Money")
        return Decimal(value)

    def process_result_value(self, value, dialect):
        return None if value is None else Money(value)
```

```text
Money(amount=Decimal('24.99'), currency='EUR') -> 24.99 EUR | x3 = 74.97 EUR
products costing at least 2,000.00 EUR: 1913
float rejected: refusing float for money; use Decimal or Money
Decimal('24.99')
```

- The bind side applies to comparisons too: `priced.c.price >= Money(Decimal(2000))` went through `process_bind_param`.
- Types belong to *column objects*: the example declares a lightweight `sa.table("products", sa.column("price", MoneyType()))` next to the regular `products` table, which still returns `Decimal`.
- `cache_ok = True` declares that the type's attributes don't affect the generated SQL, so statements using it can be cached. Leave it out and SQLAlchemy warns and disables caching for those statements.

Exercise 10 uses a stricter variant that rejects results with more than two decimals, which catches a forgotten `round()` in SQL.

---

## 12. The Bazaar Core layer

The milestone (see [project.md](project.md)) adds `bazaar/sa/` to the package:

```text
src/bazaar/sa/
├── engine.py    create_engine() / create_async_engine() from Settings; sa_url(); count_statements()
├── tables.py    MetaData + every Bazaar table, the naming convention, existing enum/domain types;
│                view_metadata with active_products and seller_sales_daily
├── drift.py     compare(conn, metadata, view_metadata) -> list of differences
└── reports.py   seller_statement(), category_revenue_rollup(), and the reusable pieces:
                 SALE_STATUSES, is_sale(), category_tree_cte(), category_closure_cte()
```

The two reports show the patterns of this module working together:

- `seller_statement(conn, seller_id, start, end)` returns a `SellerStatement` dataclass: one `StatementLine` per order (units, total, payments, refunds, aggregated in `LATERAL` subqueries) plus totals. For seller 42 in November 2025: 79 orders, sales 30,730.18, paid 32,096.04, refunded 1,365.86. The test checks the order count and refunds against independent plain SQL, and the sales against the `seller_sales_daily` materialized view from Module 09, which uses the same "a sale is paid, shipped or delivered" rule.
- `category_revenue_rollup(conn, start, end)` combines a recursive CTE for paths and depths, a recursive closure CTE (ancestor, descendant), and an aggregate `FILTER` for each category's own revenue, in one statement:

  ```text
  Beauty                                  0   2029675.70   31730
    Fragrance                    1401302.65   1401302.65   15935
    Skincare                      628373.05    628373.05   15795
  Books                                   0   3015496.52  153148
    Children's Books              169038.15    169038.15   13585
    Fiction                               0   1140304.80   69920
      Fantasy                     220011.73    220011.73   13427
  ```

  The tests check that every node equals its own revenue plus its children's, and that the roots add up to the grand total computed with plain SQL.

Every function takes a `Connection` first and never commits, so the reports run inside a request's transaction, a test's rolled-back transaction, or a `REPEATABLE READ` snapshot for a consistent month-end run.

```bash
cd project/python && pytest tests/test_sa_core.py -v
```

---

## Summary

- SQLAlchemy is **Core** (schema description, expression language, compiler, engine, pool, dialects) plus the **ORM** on top. Core keeps you in control of the SQL and makes queries composable values; it costs roughly 0.1–0.3 ms of CPU per statement over raw psycopg (measured here), which rarely matters next to query plans.
- An **engine** is created once per process from a URL (`postgresql+psycopg://`, `postgresql+asyncpg://`) and owns a **QueuePool**: `pool_size`, `max_overflow`, `pool_timeout`, `pool_pre_ping` (survives server restarts), `pool_recycle`. Never stack it on `psycopg_pool`; use `NullPool` when PgBouncer already pools. Dispose pools after `fork()`.
- 2.0-style transactions: `engine.connect()` + `conn.commit()` ("commit as you go"; uncommitted work is rolled back), `engine.begin()` (commit or roll back the block), `begin_nested()` (SAVEPOINT), isolation per connection via `execution_options`, `AUTOCOMMIT` for statements that can't run in a transaction. Driver exceptions are wrapped; the original is `e.orig`.
- Describe tables with `MetaData`/`Table`/`Column` and PostgreSQL types: `Numeric` gives `Decimal`, `DateTime(timezone=True)` for `timestamptz`, `JSONB`, `ARRAY`, `ENUM(..., create_type=False, values_callable=...)` for existing enums, `DOMAIN`, `UUID`, `TSTZRANGE` with `Range`, `Identity`, `Computed`, server defaults. A **naming convention** makes constraint names deterministic.
- **Reflection** loads tables at run time and knows less than a declaration; use it to **check** declarations instead: the drift test compares metadata with the live schema and fails on differences.
- The expression language mirrors SQL: `where(a, b)` ANDs, `&`/`|`/`~` (never `and`/`or`), `func.*`, `.label()`, `.filter()` for `FILTER`, `.alias()`, `.subquery()`, `.scalar_subquery()`, `exists()`, `.cte(recursive=True)` + `union_all`, `.over()`, `.lateral()`, `union_all()`, `case()`, `cast()`, `tuple_()` for keyset pagination. Always write ON clauses; inferred joins can pick the wrong foreign key.
- PostgreSQL specifics come from `sqlalchemy.dialects.postgresql`: `insert().on_conflict_do_update(index_elements=..., set_=..., where=...)` with `excluded`, `RETURNING`, JSONB (`["k"].astext`, `.contains()`, `.has_key()`), arrays (`.contains()`, `.overlap()`, `.any_()`), full-text search via `func` and `bool_op("@@")`, `distinct_on`, `with_for_update(skip_locked=True)`, `table_valued()` functions.
- Bind casts mean Python types matter: `float` becomes `double precision` (wrong for money, and silently rounded back), `str` becomes `varchar` (breaks implicit casts to `jsonpath`); bind with `Decimal` or an explicit `bindparam(..., type_=...)`.
- See the SQL with `stmt.compile(engine)`, `echo=True`, and cursor events; `literal_binds` is for reading only. The compiled cache keys on structure, not values.
- Bulk: `executemany` (no RETURNING) and **insertmanyvalues** (with RETURNING) both reach ~30,000 rows/s here; **COPY** through `conn.connection.driver_connection` reaches ~370,000.
- **Async Core** is the same API awaited (`create_async_engine`, `AsyncConnection`, `stream()`), running sync internals in a greenlet; statements are shared with sync code.
- `TypeDecorator` adds domain types (`Money`) with `cache_ok = True`.

## What's next

Core gives you SQL as values. Module 25 adds the **ORM**: classes mapped to these tables, a `Session` that tracks objects through a unit of work and an identity map, relationships and loading strategies (and the N+1 problem they can cause), the async ORM, and an honest look at when to drop back down to the Core you just learned.
