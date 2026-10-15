# Module 22: psycopg 3

<!-- nav:start -->
> **Theory** · [Exercises](exercises.md) · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 21 Sharding](../21-sharding/README.md) · [Course home](../../README.md) · [23 Async Python: psycopg async and asyncpg →](../23-async-python/README.md)
<!-- nav:end -->

Until now, every query in this course has been typed into psql. Bazaar's real users never see psql. They click "Buy", and a web process somewhere has to open a connection, send `SELECT place_order(...)` with *their* cart in it, turn the result (or an `insufficient stock` error) into something a program understands, and give the connection back for the next click, thousands of times a minute. That process is written in Python, and the library between Python and PostgreSQL is the **driver**. This module is about psycopg 3, the standard PostgreSQL driver for Python: how it connects, how it passes values safely, how transactions map onto Python blocks, how Postgres types become Python objects, how to move a million rows in and out quickly, and how to share a few connections among many requests. By the end you will have written the first layer of the Bazaar service: a small, tested data-access module in plain SQL.

**What you'll learn**

- What a database driver does: the DB-API model, the PostgreSQL wire protocol (simple and extended query), and why psycopg 3 replaced psycopg2.
- How to connect: connection strings and URIs, keyword arguments, libpq environment variables, `connect_timeout`, `application_name`, `sslmode`, `options`.
- The exact semantics of `with psycopg.connect(...)`, implicit transactions, cursors, and fetching.
- Parameter binding: `%s` and `%(name)s`, server-side binding and its consequences, SQL injection (demonstrated, then prevented), and dynamic SQL with `psycopg.sql`.
- Transactions from Python: autocommit, `conn.transaction()` blocks, savepoints, isolation levels, read-only transactions.
- Row factories (tuples, dicts, named tuples, dataclasses, pydantic models) and type adaptation in both directions, including JSONB, arrays, enums, ranges, composites, and custom adapters.
- Errors: the exception hierarchy, SQLSTATEs, `diag`, mapping Bazaar's custom `BZ001` codes to Python exceptions, and what state the connection is in afterwards.
- Bulk work: `COPY` in and out (with a benchmark of seven loading strategies), server-side cursors for big results, pipeline mode, and automatic prepared statements.
- `LISTEN`/`NOTIFY`, connection pools (`psycopg_pool`), pool sizing, and keeping session state from leaking between requests.
- How to debug what your program actually sends.

**Before you start**

```bash
./project/bin/reset.sh 22          # state at the start of module 22 (large dataset, ~3 minutes)
```

You need Python 3.12 or newer on your machine (not in the container: Python runs on the host and connects to the container's port 5432, exactly like psql with `-h localhost`). The course's Python package lives in [`project/python/`](../../project/python/). Create its virtual environment once:

```bash
cd project/python
uv sync                                   # with uv: creates .venv, installs runtime + dev dependencies
# or, with plain pip:
python3 -m venv .venv && .venv/bin/pip install -e . pytest pytest-asyncio ruff pydantic
source .venv/bin/activate
export BAZAAR_DSN=postgresql://postgres:postgres@localhost:5432/bazaar
python -c "import psycopg; print(psycopg.__version__, psycopg.pq.__impl__)"
```

The runnable scripts for this module are in [`examples/`](examples/). Run them from `project/python` with the virtual environment active, for example `python ../../modules/22-psycopg/examples/01_first_query.py`. The outputs shown here come from psycopg 3.3.6 (with the bundled libpq 18) against PostgreSQL 17.11, on a 4-vCPU container reached through Docker's port forwarding. Timings will differ on your machine; ratios between them are what matter.

> **Note:** examples that *change* data either roll back or undo their change, so you can run them in any order. If you ever get the database into a strange state, `reset.sh 22` brings it back.

---

<!-- toc:start -->
**Contents**

- [1. From psql to Python: what a driver does](#1-from-psql-to-python-what-a-driver-does)
  - [1.1 The DB-API in five objects](#11-the-db-api-in-five-objects)
  - [1.2 What goes over the wire](#12-what-goes-over-the-wire)
  - [1.3 psycopg 3 versus psycopg2](#13-psycopg-3-versus-psycopg2)
  - [1.4 Installing: binary, C, or pure Python](#14-installing-binary-c-or-pure-python)
- [2. Connecting](#2-connecting)
  - [2.1 Three ways to say where](#21-three-ways-to-say-where)
  - [2.2 The parameters that matter](#22-the-parameters-that-matter)
  - [2.3 Environment variables and `.pgpass`](#23-environment-variables-and-pgpass)
  - [2.4 A connection is a process](#24-a-connection-is-a-process)
- [3. Connection lifecycle, `with`, and cursors](#3-connection-lifecycle-with-and-cursors)
  - [3.1 Implicit transactions](#31-implicit-transactions)
  - [3.2 What `with psycopg.connect(...) as conn:` does, exactly](#32-what-with-psycopgconnect-as-conn-does-exactly)
  - [3.3 Cursors](#33-cursors)
  - [3.4 "Idle in transaction": the classic bug](#34-idle-in-transaction-the-classic-bug)
- [4. Passing values: parameters](#4-passing-values-parameters)
  - [4.1 Placeholders](#41-placeholders)
  - [4.2 Server-side binding and what it rules out](#42-server-side-binding-and-what-it-rules-out)
  - [4.3 SQL injection, demonstrated](#43-sql-injection-demonstrated)
  - [4.4 Identifiers and dynamic SQL: `psycopg.sql`](#44-identifiers-and-dynamic-sql-psycopgsql)
  - [4.5 Client-side binding, when you really need it](#45-client-side-binding-when-you-really-need-it)
  - [4.6 Many rows: `executemany()`](#46-many-rows-executemany)
- [5. Transactions](#5-transactions)
  - [5.1 Two styles](#51-two-styles)
  - [5.2 Nested blocks are savepoints](#52-nested-blocks-are-savepoints)
  - [5.3 Isolation levels and read-only transactions](#53-isolation-levels-and-read-only-transactions)
- [6. Row factories: the shape of a row](#6-row-factories-the-shape-of-a-row)
- [7. Type adaptation](#7-type-adaptation)
  - [7.1 From Postgres to Python](#71-from-postgres-to-python)
  - [7.2 From Python to Postgres](#72-from-python-to-postgres)
  - [7.3 Enums, composites, and per-connection registration](#73-enums-composites-and-per-connection-registration)
  - [7.4 Custom loaders and dumpers](#74-custom-loaders-and-dumpers)
  - [7.5 Text and binary formats](#75-text-and-binary-formats)
- [8. Errors](#8-errors)
  - [8.1 The hierarchy](#81-the-hierarchy)
  - [8.2 What the server tells you: `diag`](#82-what-the-server-tells-you-diag)
  - [8.3 Bazaar's error codes, as Python exceptions](#83-bazaars-error-codes-as-python-exceptions)
  - [8.4 The connection after an error](#84-the-connection-after-an-error)
  - [8.5 Notices](#85-notices)
- [9. COPY: moving many rows](#9-copy-moving-many-rows)
  - [9.1 COPY FROM STDIN](#91-copy-from-stdin)
  - [9.2 COPY TO STDOUT](#92-copy-to-stdout)
  - [9.3 Binary COPY](#93-binary-copy)
  - [9.4 Seven ways to load 100,000 rows](#94-seven-ways-to-load-100000-rows)
- [10. Big results: server-side cursors and streaming](#10-big-results-server-side-cursors-and-streaming)
- [11. Pipeline mode](#11-pipeline-mode)
- [12. Prepared statements](#12-prepared-statements)
- [13. LISTEN and NOTIFY](#13-listen-and-notify)
- [14. Connection pools](#14-connection-pools)
  - [14.1 Why and what](#141-why-and-what)
  - [14.2 The knobs](#142-the-knobs)
  - [14.3 When the pool is exhausted](#143-when-the-pool-is-exhausted)
  - [14.4 Session state: `configure`, `reset`, and SET LOCAL](#144-session-state-configure-reset-and-set-local)
  - [14.5 How big?](#145-how-big)
  - [14.6 Threads, processes, and connections](#146-threads-processes-and-connections)
- [15. Seeing what your program sends](#15-seeing-what-your-program-sends)
- [16. The Bazaar data-access layer](#16-the-bazaar-data-access-layer)
  - [Tests against the real database](#tests-against-the-real-database)
- [Summary](#summary)
- [What's next](#whats-next)

<!-- toc:end -->

## 1. From psql to Python: what a driver does

### 1.1 The DB-API in five objects

Python has a standard shape for database drivers, **PEP 249, the DB-API 2.0**. Every relational driver (for SQLite, MySQL, Oracle, PostgreSQL) follows it, so the vocabulary carries over:

| DB-API concept | What it is | psycopg 3 |
|---|---|---|
| `connect()` | opens a session with the server | `psycopg.connect(conninfo, **kwargs)` returns a `Connection` |
| Connection | one session = one server backend process; owns the transaction | `conn.commit()`, `conn.rollback()`, `conn.close()`, `conn.cursor()` |
| Cursor | sends statements and holds their results | `cur.execute()`, `cur.fetchone()`, `fetchmany()`, `fetchall()`, iteration |
| `paramstyle` | how values are written in SQL text | `%s` (positional) and `%(name)s` (named), called `format`/`pyformat` |
| Exceptions | a fixed hierarchy: `Error` → `DatabaseError` → `IntegrityError`, `OperationalError`, ... | the same names, plus one class per SQLSTATE in `psycopg.errors` |

Two DB-API rules surprise newcomers, and both are about transactions. First, **a DB-API connection starts a transaction implicitly** before the first statement and keeps it open until you call `commit()` or `rollback()`. psql works the other way round (autocommit). Second, **closing a connection without committing discards your changes**. Section 3 shows both happening.

### 1.2 What goes over the wire

A driver is a translator between Python calls and the **PostgreSQL frontend/backend protocol**, a message-based protocol over a TCP or Unix socket. You met its outline in Module 01; here are the messages that matter for application code. There are two ways to run a statement:

- The **simple query protocol**: one `Query` message containing SQL text, possibly several statements separated by `;`. The server parses, plans, executes, and streams back `RowDescription`, `DataRow`s, `CommandComplete`, then `ReadyForQuery`. Values must be *inside* the SQL text. psql uses this.
- The **extended query protocol**: the work is split into `Parse` (SQL text with placeholders `$1`, `$2`, ..., producing a *prepared statement*, possibly unnamed), `Bind` (the parameter values, sent separately from the SQL, producing a *portal*), `Describe`, `Execute`, and finally `Sync`, which ends the batch and makes the server answer `ReadyForQuery`. Exactly one statement per `Parse`.

libpq, PostgreSQL's C client library, can log every message it sends (`F`, frontend) and receives (`B`, backend). psycopg exposes that as `conn.pgconn.trace()`. Here is a parameterized query followed by one without parameters:

```python
conn.execute("SELECT id, full_name FROM users WHERE email = %s AND id < %s", ("x' OR '1'='1", 100))
conn.execute("SELECT 1")
```

```text
F	76	Parse	 "" "SELECT id, full_name FROM users WHERE email = $1 AND id < $2" 2 0 21
F	40	Bind	 "" "" 2 0 1 2 12 'x' OR '1'='1' 2 '\x00d' 1 0
F	6	Describe	 P ""
F	9	Execute	 "" 0
F	4	Sync
B	4	ParseComplete
B	4	BindComplete
B	55	RowDescription	 2 "id" 16387 1 20 8 -1 0 "full_name" 16387 3 25 65535 -1 0
B	13	CommandComplete	 "SELECT 0"
B	5	ReadyForQuery	 I
F	13	Query	 "SELECT 1"
B	33	RowDescription	 1 "?column?" 0 0 23 4 -1 0
B	11	DataRow	 1 1 '1'
B	13	CommandComplete	 "SELECT 1"
B	5	ReadyForQuery	 I
```

Read the first block slowly, because the rest of the module builds on it:

- psycopg rewrote `%s` into `$1`, `$2`. The SQL text that reaches the server contains **no values at all**.
- `Parse ... 2 0 21` declares two parameters: the first with type OID 0 ("unknown, server please infer": Python `str` is sent that way, because Python strings could be meant as `text`, `date`, `jsonb`, ...), the second with OID 21 (`int2`, because `100` fits in two bytes).
- `Bind` carries the values: `'x' OR '1'='1'` as 12 bytes of text, and `100` as two binary bytes `\x00d` (0x0064). The quote characters in the first value are just bytes in a value. They are never parsed as SQL.
- `RowDescription` tells the client the result columns: `id` has type OID 20 (`int8`), `full_name` OID 25 (`text`). psycopg uses these OIDs to pick a *loader* that turns each value into a Python object (section 7).
- `ReadyForQuery I` means "idle, no transaction open" (this connection was in autocommit mode). Inside a transaction you would see `T`, and after an error inside a transaction `E`.
- The statement without parameters went through the simple protocol (`Query`). That detail has a security consequence (section 4.3).

### 1.3 psycopg 3 versus psycopg2

psycopg2 was *the* Python driver for 15 years, and you will meet it in older code. psycopg 3 (package name `psycopg`, module `psycopg`) is its successor by the same author. It is a rewrite, not an upgrade:

| | psycopg2 | psycopg 3 |
|---|---|---|
| Parameter binding | client-side: values are quoted and pasted into the SQL text, then sent with the simple protocol | **server-side** by default: `Parse`/`Bind`, values never touch the SQL text |
| Prepared statements | none (only manual `PREPARE`) | automatic after a statement runs 5 times on a connection (section 12) |
| Async | bolted on (`psycopg2` async mode, `aiopg` wrapper) | native: `AsyncConnection`, `AsyncCursor`, `AsyncConnectionPool` with the same API (Module 23) |
| COPY | `copy_expert` with file-like objects | a `copy()` context manager with `write_row()`, `rows()`, binary format, typed |
| Pipeline mode | no | yes (libpq 14+): many statements in flight per round trip (section 11) |
| Typing | untyped | fully type-annotated; generic cursors carry the row type (`Cursor[Product]`) |
| Row factories | `DictCursor`, `NamedTupleCursor` cursor subclasses | `row_factory=dict_row`, `class_row(MyDataclass)`, ... on any cursor or connection |
| `with connection:` | commits or rolls back, but **does not close** | commits or rolls back, **and closes** |
| Connection pool | a basic, thread-unsafe-in-practice one | `psycopg_pool`: thread-safe, async, health checks, configure/reset hooks, stats |
| Binary data format | no | per query or per cursor (`binary=True`) |

Should you still learn psycopg2? Only to maintain old code. Two migration traps are worth knowing: in psycopg 3, `with conn:` commits or rolls back **and closes** the connection (psycopg2 left it open), and server-side binding rejects a few things client-side binding allowed, such as parameters in `SET` or `IN %s` (section 4.2).

### 1.4 Installing: binary, C, or pure Python

`psycopg` itself is pure Python. Its speed-critical parts (parsing rows, adapting types) exist in two implementations, and the base package also needs libpq:

| Install | What you get | Use it when |
|---|---|---|
| `pip install "psycopg[binary]"` | precompiled C speedups **and a bundled libpq** in a wheel; no compiler or system libpq needed | development, most deployments (this course) |
| `pip install "psycopg[c]"` | compiles the C speedups against the system's libpq | you must use the OS's libpq (security patching policy, custom builds) |
| `pip install psycopg` | pure Python, uses the system libpq through `ctypes` | platforms without wheels; slowest |

`psycopg[pool]` adds `psycopg_pool`. Check what you have:

```python
>>> import psycopg
>>> psycopg.__version__, psycopg.pq.__impl__, psycopg.pq.version()
('3.3.6', 'binary', 180006)
```

The bundled libpq is version 18.6. That is fine: a newer libpq talks to older servers. (The reverse caution from Module 00 applies to psql's catalog queries, not to the protocol.) A newer libpq also enables features: pipeline mode needs libpq 14, and the chunked streaming of section 10 needs libpq 17.

---

## 2. Connecting

### 2.1 Three ways to say where

The smallest complete program:

```python
import os
import psycopg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")

with psycopg.connect(DSN, application_name="m22-first-query") as conn:
    print("server version:", conn.info.server_version)
    print("backend pid:   ", conn.info.backend_pid)
    print("status before: ", conn.info.transaction_status.name)
    cur = conn.execute(
        "SELECT id, shop_name, country_code FROM sellers WHERE id <= %s ORDER BY id", (3,)
    )
    print("status after:  ", conn.info.transaction_status.name)
    print("columns:       ", [c.name for c in cur.description])
    for row in cur:
        print(row)
print("closed:", conn.closed)
```

```text
server version: 170011
backend pid:    396
status before:  IDLE
status after:   INTRANS
columns:        ['id', 'shop_name', 'country_code']
(1, 'Happy Outlet 1', 'BR')
(2, 'Little Workshop 2', 'ES')
(3, 'Happy Emporium 3', 'IN')
closed: True
```

(The full script is [`examples/01_first_query.py`](examples/01_first_query.py).) Notice `INTRANS` after the first `SELECT`: the DB-API's implicit transaction from section 1.1, opened by a `BEGIN` that psycopg sent before your statement.

`psycopg.connect()` accepts the connection target in any of the forms Module 00 introduced, and they can be combined:

```python
psycopg.connect("postgresql://postgres:postgres@localhost:5432/bazaar?application_name=report")
psycopg.connect("host=localhost port=5432 dbname=bazaar user=postgres password=postgres")
psycopg.connect(host="localhost", dbname="bazaar", user="postgres", password="postgres")
psycopg.connect("postgresql://localhost/bazaar", user="postgres", connect_timeout=5)
```

Keyword arguments override the same keys in the string. The string is parsed by libpq itself, so every libpq parameter works, whether psycopg knows about it or not. `conninfo` strings are a better fit for configuration (one environment variable), keyword arguments for code that adds a parameter or two. `psycopg.conninfo.make_conninfo()` merges and quotes them correctly if you need to build a string, and `conninfo_to_dict()` parses one.

> **Pitfall:** special characters in a password break a URI unless percent-encoded (`p@ss` becomes `p%40ss`). In key=value form, quote values that contain spaces or quotes: `password='it''s'`. Better still, keep passwords out of the string entirely: `PGPASSWORD`, `~/.pgpass`, or a secret manager.

### 2.2 The parameters that matter

| Parameter | Default | Why you set it |
|---|---|---|
| `connect_timeout` | none: wait forever | Seconds to wait for the TCP connection and authentication. Without it, a black-holed host makes your process hang for minutes (the OS TCP timeout). Use 2–10. |
| `application_name` | empty (psql sets `psql`) | Shows up in `pg_stat_activity`, in server logs (`%a` in `log_line_prefix`), and in `pg_stat_statements` joins via the backend. The first question in any incident is "which program is that?" |
| `sslmode` | `prefer` | `disable`, `allow`, `prefer`, `require`, `verify-ca`, `verify-full`. Only `verify-full` checks that you talk to the right server (Module 15). `prefer` silently falls back to plaintext. |
| `options` | none | Server settings applied at session start: `options="-c statement_timeout=5s -c search_path=bazaar"`. Saves a round trip of `SET` after connecting. |
| `target_session_attrs` | `any` | With several hosts (`host=pg1,pg2,pg3`), pick one that is `read-write`, `read-only`, `primary`, `standby`, or `prefer-standby`. This is the client-side failover of Module 20. |
| `keepalives_idle`, `tcp_user_timeout` | OS defaults | Detect dead connections (a crashed server, a dropped NAT entry) in seconds instead of hours. |

Here is how the first two look from the server's side, while the example script ran:

```text
pg_stat_activity: ('m22-first-query', 'active', True)
```

The row says: application `m22-first-query`, currently `active` (it's running the very query that reads `pg_stat_activity`), and it holds a transaction snapshot.

### 2.3 Environment variables and `.pgpass`

libpq fills in anything the connection string leaves out from `PG*` environment variables (`PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`, `PGSSLMODE`, `PGAPPNAME`, `PGCONNECT_TIMEOUT`, `PGOPTIONS`, ...), then from `~/.pgpass` for the password, then from built-in defaults. So `psycopg.connect("")` with the right environment connects exactly like `psql` does. That is convenient, and also a source of "works on my machine": a stray `PGHOST` in your shell profile silently redirects every script.

The Bazaar package therefore takes one explicit variable, `BAZAAR_DSN`, and leaves the `PG*` variables as fallbacks for anything the DSN omits. The settings live in [`project/python/src/bazaar/config.py`](../../project/python/src/bazaar/config.py).

### 2.4 A connection is a process

Module 16 showed that every connection is a separate backend process forked by the postmaster, with its own memory, catalog caches and so on. Creating one is not free. The client must open a TCP connection, negotiate TLS (if used), and run SCRAM authentication, which deliberately takes thousands of hash iterations. The server must fork, initialize, and load caches. Measured from this machine ([`examples/12_pool.py`](examples/12_pool.py)):

```text
1. new connection per query: 14.73 ms   pooled: 0.52 ms
```

That is about 15 ms to connect, against half a millisecond for the query itself through a pool. A web request that opens its own connection spends most of its database time on the handshake, and at 500 requests per second you would also be forking 500 processes per second on the server. Section 14 fixes this with a pool. For scripts and one-off jobs, a plain `connect()` is right.

---

## 3. Connection lifecycle, `with`, and cursors

### 3.1 Implicit transactions

In psycopg's default mode (`autocommit=False`), the connection's transaction status moves like this:

```text
            first statement (psycopg sends BEGIN first)
   IDLE  ───────────────────────────────────────────▶  INTRANS
    ▲                                                     │  │
    │ commit() / rollback()                               │  │ a statement fails
    └─────────────────────────────────────────────────────┘  ▼
    ▲                                                     INERROR
    └───────────────── rollback() ───────────────────────────┘
```

`conn.info.transaction_status` tells you where you are: `IDLE`, `INTRANS`, `INERROR`, or `ACTIVE` (a statement is running), plus `UNKNOWN` for a broken connection. Even a plain `SELECT` opens a transaction, and the transaction stays open, holding a snapshot (and therefore holding back VACUUM's xmin horizon, Module 16), until you commit or roll back.

### 3.2 What `with psycopg.connect(...) as conn:` does, exactly

The connection's context manager does three things at the end of the block:

1. If the block raised an exception, `rollback()`.
2. Otherwise, `commit()`.
3. In both cases, `close()`.

Watching the server's statement log is the most convincing way to see this. [`examples/02_transaction_trace.py`](examples/02_transaction_trace.py) runs four scenarios on connections with `log_statement=all`. The log for the first two:

```python
# A: default (autocommit off): implicit BEGIN, `with` commits and closes
with scenario("A: with-block, no error") as conn:
    conn.execute("UPDATE inventory SET quantity = quantity WHERE product_id = 16")

# B: exception inside the with-block: rollback, then close
try:
    with scenario("B: with-block, exception") as conn:
        conn.execute("UPDATE inventory SET quantity = quantity WHERE product_id = 16")
        raise RuntimeError("boom")
except RuntimeError:
    pass
```

```text
[398] LOG:  statement: BEGIN
[398] LOG:  statement: UPDATE inventory SET quantity = quantity WHERE product_id = 16
[398] LOG:  statement: COMMIT
...
[399] LOG:  statement: BEGIN
[399] LOG:  statement: UPDATE inventory SET quantity = quantity WHERE product_id = 16
[399] LOG:  statement: ROLLBACK
```

(Each scenario first logs a marker `SELECT`; it is left out here.) The `BEGIN` came from psycopg, not from our code.

Three consequences:

- **Code outside a `with` block must commit itself.** `conn = psycopg.connect(...); conn.execute("UPDATE ..."); conn.close()` changes nothing: `close()` without `commit()` ends the session and the server rolls the open transaction back.
- **A `with` block is one transaction only if you don't commit inside it.** Calling `conn.commit()` in the middle ends that transaction, and the next statement starts another one.
- **psycopg2 behaved differently**: its `with conn:` committed or rolled back but left the connection open. Code ported from psycopg2 that reuses `conn` after the block fails with `the connection is closed`.

> **Pitfall:** the connection is closed at the end of the block, but the Python object is still there. Anything that captured `conn` (a cursor, a closure) fails afterwards with `OperationalError: the connection is closed`.

### 3.3 Cursors

In psycopg 3, a regular cursor is a **client-side** object: a handle for sending a statement and a place to keep its result. It does not correspond to anything on the server (unlike the server-side cursors of section 10). The full form:

```python
with conn.cursor() as cur:
    cur.execute("SELECT id, title, price FROM products WHERE seller_id = %s ORDER BY id", (42,))
    print(cur.rowcount)        # rows in the result (for SELECT), or rows affected (for DML)
    print(cur.statusmessage)   # the CommandComplete tag, e.g. 'SELECT 37' or 'UPDATE 1'
    first = cur.fetchone()     # one row, or None when exhausted
    some = cur.fetchmany(10)   # up to 10 rows (cur.arraysize by default)
    rest = cur.fetchall()      # everything that is left, as a list
```

`conn.execute(...)` is a shortcut that creates a cursor, executes, and returns the cursor, so `conn.execute(sql, params).fetchone()` is the everyday idiom. Cursors are iterable: `for row in cur:` yields rows one at a time.

`cur.description` describes the result columns (name, type OID, size, ...) and is `None` for statements that return no rows, which is how generic code distinguishes "a query" from "a command". Leaving the `with` block (or calling `close()`) releases the result.

> **Under the hood:** with a client-side cursor, `execute()` returns only after **the whole result** has arrived in client memory (libpq collects all `DataRow` messages into one `PGresult`). `fetchone()` and iteration then convert rows to Python objects lazily, one at a time, but the raw data is all there already. Iterating saves you the Python objects, not the network transfer or the libpq buffer. Section 10 measures this and shows what to use for results that don't fit in memory.

### 3.4 "Idle in transaction": the classic bug

The implicit `BEGIN` has a nasty failure mode. A web handler reads something, then calls a slow external API before it commits:

```python
row = conn.execute("SELECT ... FROM orders WHERE id = %s", (order_id,)).fetchone()
send_confirmation_email(row)    # 3 seconds, or 30 if the mail server is slow
conn.commit()
```

For those seconds, the backend sits in state `idle in transaction`: it holds a snapshot, any row locks the transaction took, and a connection slot. Module 17 showed how this blocks VACUUM and how `idle_in_transaction_session_timeout` kills such sessions. From the application's side the rule is simple: **keep transactions short, and never wait for anything except the database inside one.** Either commit before the slow call, or use autocommit for read-only work (section 5).

---

## 4. Passing values: parameters

### 4.1 Placeholders

Values go into SQL through placeholders, never through string formatting:

```python
conn.execute("SELECT * FROM products WHERE seller_id = %s AND price < %s", (42, Decimal("50")))
conn.execute(
    "SELECT * FROM products WHERE seller_id = %(seller)s AND price < %(max)s",
    {"seller": 42, "max": Decimal("50")},
)
```

Positional `%s` takes a sequence (tuple or list), named `%(name)s` takes a mapping. The same name can appear several times. The placeholder is always `%s`, whatever the type: it is not a printf format, and `%d` or `%f` are errors. (There are two variants, `%b` and `%t`, which force binary or text format for that value; you rarely need them.)

A handful of mistakes are so common that it is worth seeing their error messages once ([`examples/04_binding_gotchas.py`](examples/04_binding_gotchas.py)):

```text
FAIL  string instead of a sequence: TypeError: query parameters should be a sequence or a mapping, got str
OK    1-tuple: [(42,)]
FAIL  quoted placeholder: IndeterminateDatatype: could not determine data type of parameter $1
OK    literal % without params: [(19826,)]
FAIL  literal % with params: ProgrammingError: only '%s', '%b', '%t' are allowed as placeholders, got '%@'
OK    %% with params: [(1,)]
OK    pattern as a parameter: [(19826,)]
```

- `execute(sql, "42")` passes a string, which is a sequence of characters. Write `("42",)`, with the trailing comma that makes a one-element tuple, or `["42"]`.
- Never put quotes around a placeholder. `'%s'` is a string literal containing the characters `%s`: psycopg turned it into the literal `'$1'` and the parameter you passed was left unused, so the server could not even work out its type.
- When a query has parameters, a literal `%` must be doubled: `LIKE '%%@example.org'`. Without parameters, no `%` processing happens. The cleanest fix is to pass the pattern itself as a parameter: `email LIKE %s` with `"%@example.org"`.

### 4.2 Server-side binding and what it rules out

Because psycopg 3 sends the SQL text and the values separately (section 1.2), **a placeholder can only stand where the server's grammar allows a parameter**, which is wherever a *value expression* may appear in a statement the server can prepare (`SELECT`, `INSERT`, `UPDATE`, `DELETE`, `VALUES`, `MERGE`, `CALL`). Everything else fails:

```text
FAIL  IN %s with a tuple: SyntaxError: syntax error at or near "$1"
OK    = ANY(%s) with a list: [(3,)]
FAIL  SET with a parameter: SyntaxError: syntax error at or near "$1"
OK    set_config() instead: [('5s',)]
FAIL  table name as a parameter: SyntaxError: syntax error at or near "$1"
OK    sql.Identifier: [(2000,)]
FAIL  untyped NULL comparison: IndeterminateDatatype: could not determine data type of parameter $1
OK    parameter needing a type: [(3,)]
OK    mixed text arithmetic: [('ax',)]
```

| You want | Doesn't work | Do this |
|---|---|---|
| a list of values | `WHERE id IN %s` | `WHERE id = ANY(%s)` with a Python **list** (it becomes one array parameter), or `<> ALL(%s)` for `NOT IN` |
| a setting | `SET statement_timeout = %s` | `SELECT set_config('statement_timeout', %s, false)` (`true` = `SET LOCAL`) |
| a table or column name | `FROM %s` | `psycopg.sql.Identifier` (section 4.4) |
| `LISTEN`/`NOTIFY` channel | `LISTEN %s` | `sql.SQL("LISTEN {}").format(sql.Identifier(ch))`, or `SELECT pg_notify(%s, %s)` |
| DDL with values | `CREATE TABLE t (c int DEFAULT %s)` | `sql.Literal` composition, or a client-side cursor (4.5) |
| a parameter of unknown type | `SELECT %s IS NULL` with `None` | an explicit cast: `%s::text IS NULL` |

`IN %s` is the most common surprise when porting psycopg2 code, which used to expand a tuple into `IN (1, 2, 3)` as text. `= ANY(%s)` with a list is better anyway: it is one parameter whatever the list length, so the statement text is identical for 3 or 300 ids and can be prepared once (section 12).

The type question deserves one more sentence. Python `int`, `float`, `Decimal`, `bool`, `datetime` and friends are sent with a specific type OID. Python `str` is sent as "unknown", so the server infers it from context: `WHERE created_at > %s` with `'2025-12-01'` works and is compared as a `timestamptz`. When there is no context (`SELECT %s IS NULL`, or `pg_typeof(%s)`), inference fails, and an explicit `::type` cast in the SQL fixes it.

### 4.3 SQL injection, demonstrated

SQL injection happens when **data is concatenated into code**: a value supplied by a user ends up inside the SQL text, where the parser treats it as SQL. Here is the bug in its classic form, a lookup by e-mail address ([`examples/03_sql_injection.py`](examples/03_sql_injection.py)):

```python
def find_user_unsafe(conn, email):
    query = f"SELECT id, full_name FROM users WHERE email = '{email}'"   # <- the bug
    return conn.execute(query).fetchall()

def find_user_safe(conn, email):
    return conn.execute("SELECT id, full_name FROM users WHERE email = %s", (email,)).fetchall()
```

Feed both functions three inputs a user could type into a login form:

```text
--- normal input: 'lukas.meyer10462@example.org'
unsafe -> 1 row(s); first: [(10462, 'Lukas Meyer')]
         (product 16 price is now 10.99)
safe   -> 1 row(s)
--- always true: "x' OR '1'='1"
unsafe -> 100000 row(s); first: [(1, 'Hugo Nelson')]
         (product 16 price is now 10.99)
safe   -> 0 row(s)
--- stacked statement: "x'; UPDATE products SET price = 0.01 WHERE id = 16; --"
unsafe -> 0 row(s); first: []
         (product 16 price is now 0.01)
safe   -> 0 row(s)
SELECT id FROM users WHERE email = 'x'' OR ''1''=''1'
```

Take the attacks one at a time:

1. **Always true.** The quote in the input closes the string literal early, so the SQL becomes `WHERE email = 'x' OR '1'='1'`. The `WHERE` clause is true for every row, and the "login" matches all 100,000 users. In a login form, that means signing in as the first user, often an administrator.
2. **Reading other data.** The same trick lets an attacker append clauses of their own, for example a `UNION` with a second query that has the same number of columns, so a page meant to show one user's name can be made to show rows from other tables the connection's role can read. This is why the application should log in as a least-privilege role like `bazaar_app` (Module 15), never as a superuser: privileges bound the damage of any bug.
3. **Stacked statement.** The input closes the query with `;` and starts a new statement. This works here because a query **without parameters** goes through the simple protocol (section 1.2), which accepts several statements in one message. With the extended protocol, the server would have refused: `cannot insert multiple commands into a prepared statement`. That refusal limits the damage, but it does not prevent the first two kinds of attack, so don't rely on it.

The safe version returned 0 rows every time, because the input was compared, as a whole, with the `email` column. The last line shows what the value *means*, rendered the way psycopg2 would have quoted it: the quotes inside the value are doubled. With server-side binding, not even that quoting happens, because the value never enters the SQL text.

The rules that follow are short:

- **Every value goes through a placeholder.** No f-strings, `%` formatting, `+` concatenation or `.format()` with values, ever, not even for "trusted" values like ids you just read from the database: today's trusted value is tomorrow's user input after a refactor.
- **Identifiers** (table and column names, sort columns) cannot be parameters; compose them with `psycopg.sql` from a **whitelist** (next section).
- Static analysis catches the obvious cases: ruff's rule `S608` (from Bandit) flags SQL built with f-strings or `%`. The Bazaar package enables it in `pyproject.toml`.

> **psycopg 3.3 and Python 3.14:** with Python 3.14's template strings (PEP 750), psycopg 3.3 also accepts `conn.execute(t"SELECT * FROM users WHERE email = {email}")`. A t-string is *not* an f-string: it produces a `Template` object that keeps the literal parts and the interpolated values apart, and psycopg turns each `{value}` into a parameter. It reads like the unsafe version but is as safe as `%s`. (Format specifiers such as `{name:i}` mark identifiers.) This course targets Python 3.12+, so its code uses `%s`; both forms are equivalent.

### 4.4 Identifiers and dynamic SQL: `psycopg.sql`

Some SQL must be assembled at runtime: a sort column chosen by the user, a table name from configuration, a list of optional filters. The `psycopg.sql` module builds such statements out of typed pieces, so that each piece is quoted according to what it is:

| Object | Represents | Rendered as |
|---|---|---|
| `sql.SQL("...")` | trusted SQL text written by you (a template with `{}` slots) | verbatim |
| `sql.Identifier("name")` / `sql.Identifier("schema", "name")` | a table, column, schema, channel name | double-quoted, embedded `"` doubled |
| `sql.Literal(value)` | a value embedded in the text (for DDL and other places parameters can't go) | adapted and quoted by psycopg |
| `sql.Placeholder()` / `sql.Placeholder("name")` | a `%s` / `%(name)s` placeholder | the placeholder |
| `sql.SQL(", ").join([...])` | a separator between composables | joined |

```python
from psycopg import sql

q = sql.SQL("SELECT {cols} FROM {table} WHERE {key} = {val} ORDER BY {order} DESC").format(
    cols=sql.SQL(", ").join(map(sql.Identifier, ["id", "title", "price"])),
    table=sql.Identifier("bazaar", "products"),
    key=sql.Identifier("seller_id"),
    val=sql.Placeholder(),
    order=sql.Identifier("price"),
)
print(q.as_string(conn))
print(conn.execute(q, (42,)).fetchone())
```

```text
SELECT "id", "title", "price" FROM "bazaar"."products" WHERE "seller_id" = %s ORDER BY "price" DESC
(60438, 'Summit Performance Road Bike - Grey', Decimal('969.99'))
```

`as_string()` renders the statement for logging and tests. A few more renderings:

```text
sql.SQL("SELECT {}").format(sql.Literal("O'Connor"))      ->  SELECT 'O''Connor'
sql.Identifier('Weird "name"')                             ->  "Weird ""name"""
sql.SQL("LISTEN {}").format(sql.Identifier("new_order"))   ->  LISTEN "new_order"
INSERT built from a column list with named placeholders    ->  INSERT INTO "jobs" ("queue", "kind") VALUES (%(queue)s, %(kind)s)
```

Quoting an identifier makes it *safe to embed*, but not *allowed*: `sql.Identifier(user_input)` still lets a user sort by any column, including ones you'd rather not expose, or make the query fail with a nonexistent column. **Check identifiers against a whitelist first**, then compose:

```python
SORTABLE = {"price", "created_at", "title"}

def list_products(conn, seller_id: int, sort: str, desc: bool):
    if sort not in SORTABLE:
        raise ValueError(f"cannot sort by {sort!r}")
    q = sql.SQL("SELECT id, title, price FROM products WHERE seller_id = %s ORDER BY {} {}").format(
        sql.Identifier(sort), sql.SQL("DESC" if desc else "ASC")
    )
    return conn.execute(q, (seller_id,)).fetchall()
```

The Bazaar repository uses the same technique for optional search filters: `search_query()` in [`repo.py`](../../project/python/src/bazaar/repo.py) appends `AND p.category_id = %(category_id)s` only when a category is given, instead of writing `(%(cat)s IS NULL OR p.category_id = %(cat)s)`. The composed version is a different, simpler statement for each combination of filters, and the planner can use the right index for each.

### 4.5 Client-side binding, when you really need it

For the rare statement that must contain literal values, such as DDL, `SET` in a script, or a server-side `DO` block, psycopg offers `ClientCursor`, which behaves like psycopg2: it quotes the values and splices them into the text before sending. `cur.mogrify(sql, params)` shows the result without executing it:

```python
from psycopg import ClientCursor

with ClientCursor(conn) as cur:
    print(cur.mogrify("SELECT id FROM users WHERE email = %s", ("x' OR '1'='1",)))
    cur.execute("SET statement_timeout = %s", ("5s",))     # works: the value is quoted into the text
```

```text
SELECT id FROM users WHERE email = 'x'' OR ''1''=''1'
```

Client-side binding is safe as long as psycopg does the quoting (it knows the connection's encoding and settings), but you lose server-side binding's other benefits: values are always sent as text, and every distinct value is a distinct statement text (bad for `pg_stat_statements` and for prepared statements). Use it for scripts and tooling, not for the hot path. You can also make it a connection's default with `psycopg.connect(..., cursor_factory=ClientCursor)`, which is occasionally needed behind middleware that only speaks the simple protocol.

### 4.6 Many rows: `executemany()`

`cur.executemany(sql, seq_of_params)` runs one statement for every item of a sequence:

```python
with conn.cursor() as cur:
    cur.executemany(
        "UPDATE products SET price = %s WHERE id = %s",
        [(Decimal("19.99"), 16), (Decimal("24.99"), 47), (Decimal("9.50"), 68)],
    )
```

psycopg 3 sends all of them in **pipeline mode** (section 11), so the whole batch costs about one network round trip instead of one per row. With `returning=True`, you can collect the `RETURNING` rows of each statement by stepping through the results with `cur.nextset()`. `executemany()` is the right tool for a few hundred to a few thousand rows of `UPDATE` or upsert. For loading many new rows, `COPY` (section 9) is an order of magnitude faster still.

---

## 5. Transactions

### 5.1 Two styles

psycopg supports two styles of transaction control, and mixing them carelessly is the main source of confusion:

**Style 1, implicit (the DB-API default).** `autocommit=False`. The first statement opens a transaction, and you end it with `conn.commit()` or `conn.rollback()`, or let the connection's `with` block do it. Good for scripts: one connection, one unit of work.

**Style 2, autocommit plus explicit blocks.** `psycopg.connect(..., autocommit=True)`, or set `conn.autocommit = True` while idle. Each statement commits on its own, like in psql, and you group statements explicitly:

```python
with conn.transaction():          # BEGIN
    conn.execute(...)
    conn.execute(...)
                                  # COMMIT, or ROLLBACK if the block raised
```

This is the style for long-lived code such as services, workers and libraries, because the transaction boundary is visible in the code's indentation and cannot be forgotten. Here is scenario C of the trace script:

```python
with scenario("C: autocommit + nested transaction()") as conn:
    conn.autocommit = True
    conn.execute("SELECT 1")  # its own transaction, no BEGIN needed
    with conn.transaction():
        conn.execute("UPDATE inventory SET quantity = quantity WHERE product_id = 16")
        try:
            with conn.transaction():
                conn.execute("UPDATE inventory SET quantity = -1 WHERE product_id = 16")
        except psycopg.errors.CheckViolation:
            pass  # only the inner block is undone
        conn.execute("UPDATE inventory SET quantity = quantity WHERE product_id = 47")
```

```text
[400] LOG:  statement: SELECT 1
[400] LOG:  statement: BEGIN
[400] LOG:  statement: UPDATE inventory SET quantity = quantity WHERE product_id = 16
[400] LOG:  statement: SAVEPOINT "_pg3_2"
[400] LOG:  statement: UPDATE inventory SET quantity = -1 WHERE product_id = 16
[400] ERROR:  new row for relation "inventory" violates check constraint "inventory_quantity_nonnegative"
[400] DETAIL:  Failing row contains (16, -1, 0, 2026-10-14 08:50:31.911605+00).
[400] STATEMENT:  UPDATE inventory SET quantity = -1 WHERE product_id = 16
[400] LOG:  statement: ROLLBACK TO "_pg3_2"
[400] LOG:  statement: RELEASE "_pg3_2"
[400] LOG:  statement: UPDATE inventory SET quantity = quantity WHERE product_id = 47
[400] LOG:  statement: COMMIT
```

### 5.2 Nested blocks are savepoints

A `transaction()` block inside another one becomes a **savepoint** (Module 10). If the inner block raises, psycopg sends `ROLLBACK TO SAVEPOINT`, which undoes only the inner block's work and takes the transaction out of the aborted state. Then the exception propagates out of the inner `with`. If you catch it, as above, the outer transaction continues and commits everything else. If you don't catch it, it propagates through the outer block too, which rolls back the whole transaction.

This makes transaction blocks **composable**, and it is what the Bazaar repository relies on: `repo.place_order()` wraps its call in `with conn.transaction():`. Called on an idle autocommit connection, that block is the real transaction. Called from inside a bigger unit of work (or from a test that rolls everything back at the end), it is a savepoint, and a failed checkout leaves the caller's transaction usable.

Two more tools:

- `raise psycopg.Rollback()` inside a block rolls it back **without** propagating an exception out of the block (`raise psycopg.Rollback(outer_tx)` rolls back up to an enclosing block).
- `conn.transaction(force_rollback=True)` always rolls back at the end. This is the basis of fast database tests: every test runs in a transaction that is never committed (section 16).

> **Pitfall:** scenario D of the trace shows the trap in mixing the styles. On a connection with `autocommit=False` that is **idle**, `with conn.transaction():` starts a real transaction and **commits** it at the end of the block. If you meant it to be part of a larger implicit transaction, you have to start that transaction first (any statement does it); then the block becomes a savepoint:
>
> ```text
> [401] LOG:  statement: BEGIN
> [401] LOG:  statement: SELECT 2
> [401] LOG:  statement: COMMIT          <- end of the transaction() block
> [401] LOG:  statement: BEGIN
> [401] LOG:  statement: SELECT 3
> [401] LOG:  statement: COMMIT          <- end of the connection's with-block
> ```
>
> Also, `conn.commit()` and `conn.rollback()` are *forbidden* inside a `transaction()` block (psycopg raises `ProgrammingError`), because they would silently end the transaction the block is managing.

### 5.3 Isolation levels and read-only transactions

Module 10 explained isolation levels and their anomalies. From Python you set them per connection, and they apply to every transaction the connection starts afterwards:

```python
from psycopg import IsolationLevel

conn.isolation_level = IsolationLevel.SERIALIZABLE   # or READ_COMMITTED, REPEATABLE_READ, None = server default
conn.read_only = True                                # BEGIN READ ONLY
conn.deferrable = True                               # with SERIALIZABLE + read_only: wait for a safe snapshot
```

These attributes can only be changed while the connection is idle. psycopg turns them into `BEGIN ISOLATION LEVEL SERIALIZABLE` (in autocommit mode with `transaction()` blocks too). For a single transaction you can also write the SQL yourself (`SET TRANSACTION ISOLATION LEVEL ...` as the first statement).

What a serialization failure looks like from Python ([`examples/14_isolation.py`](examples/14_isolation.py)): two connections at `SERIALIZABLE` both read the stock of product 16 and then both update it based on what they read. The first commit wins:

```text
A committed: serializable
B: SerializationFailure (40001): could not serialize access due to concurrent update
read_only: ReadOnlySqlTransaction: cannot execute DELETE in a read-only transaction
```

`SerializationFailure` (SQLSTATE `40001`) and `DeadlockDetected` (`40P01`) are the two errors whose correct handling is "roll back and run the **whole transaction** again", from the start, re-reading everything. Exercise 6 builds a retry decorator for this, and Module 27 turns it into a production pattern.

> **In production:** set `read_only = True` on connections used for reports. It documents intent, prevents accidents, and lets the same code run unchanged against a hot standby (Module 19), where any write fails anyway.

---

## 6. Row factories: the shape of a row

By default a row is a tuple. A **row factory** decides what each row becomes instead. You can set it per cursor (`conn.cursor(row_factory=...)`) or for the whole connection (`conn.row_factory = ...`, which cursors inherit). [`examples/13_row_factories.py`](examples/13_row_factories.py):

```python
from psycopg.rows import class_row, dict_row, namedtuple_row

@dataclass
class Seller:
    id: int
    shop_name: str
    country_code: str

class SellerIn(BaseModel):                       # pydantic
    id: int
    shop_name: str = Field(min_length=3)
    country_code: str = Field(pattern=r"^[A-Z]{2}$")

Q = "SELECT id, shop_name, country_code FROM sellers WHERE id = %s"
```

```text
default:    (1, 'Happy Outlet 1', 'BR')
dict_row:   {'id': 1, 'shop_name': 'Happy Outlet 1', 'country_code': 'BR'}
namedtuple: Row(id=1, shop_name='Happy Outlet 1', country_code='BR') -> Happy Outlet 1
dataclass:  Seller(id=1, shop_name='Happy Outlet 1', country_code='BR')
pydantic:   id=1 shop_name='Happy Outlet 1' country_code='BR'
pydantic rejects: 2 validation errors for SellerIn ...
dataclass, missing column: Seller.__init__() missing 1 required positional argument: 'country_code'
connection default: {'revenue': Decimal('38516237.78')} True
```

| Factory | Row type | Notes |
|---|---|---|
| `tuple_row` (default) | `tuple` | Fastest. Access by position. |
| `dict_row` | `dict` | Keys are column names; duplicate names (two `id` columns from a join) silently overwrite each other, so alias them. |
| `namedtuple_row` | a generated `namedtuple` | Attribute access, immutable, cheap; the class is cached per column set. |
| `class_row(cls)` | `cls(**columns)` | Any class whose constructor takes the column names as keyword arguments: dataclasses, attrs, pydantic models. A missing or extra column is a `TypeError`. |
| `args_row(func)` / `kwargs_row(func)` | `func(*values)` / `func(**columns)` | When construction needs logic. |
| `scalar_row` (3.2+) | the first column's value | For `SELECT id FROM ...` lists. |

`class_row` is what the Bazaar repository uses (`class_row(Product)`), because it gives the rest of the program typed objects, and type checkers understand it: `conn.cursor(row_factory=class_row(Product))` is a `Cursor[Product]`, so `fetchone()` is known to return `Product | None`. A pydantic model as a row class validates every row as it is built; that is useful at a trust boundary (data written by another system), but costs time in a hot path.

> **Pitfall:** the column names in the query must match the class's field names exactly, so write the `SELECT` list with aliases (`i.quantity - i.reserved AS available`) rather than `SELECT *`. `SELECT *` breaks the class mapping the day someone adds a column to the table.

---

## 7. Type adaptation

**Adaptation** is psycopg's word for converting values between PostgreSQL and Python. *Loaders* convert from Postgres (chosen by the result column's type OID), *dumpers* convert to Postgres (chosen by the Python type). Both are registered in an adapters map that exists globally (`psycopg.adapters`), per connection (`conn.adapters`), and per cursor (`cur.adapters`); a registration affects that scope and everything created from it later.


### 7.1 From Postgres to Python

[`examples/09_types.py`](examples/09_types.py) selects one value of each common type from Bazaar's tables (product 20, which has a promotion and events):

```text
price numeric              decimal.Decimal              Decimal('24.99')
created_at timestamptz     datetime.datetime            datetime.datetime(2022, 2, 10, 19, 59, 52, tzinfo=zoneinfo.ZoneInfo(key='Etc/UTC'))
created_on date            datetime.date                datetime.date(2022, 2, 10)
attributes jsonb           builtins.dict                {'vegan': True, 'skin_type': 'sensitive', 'volume_ml': 15}
tags text[]                builtins.list                ['beauty', 'fragrance', 'gift']
status (enum)              builtins.str                 'active'
active_during tstzrange    psycopg.types.range.Range    Range(datetime.datetime(2023, 11, 20, 0, 0, tzinfo=zoneinfo.ZoneInfo(key='Etc/UTC')), datetime.datetime(2023, 11, 28, 0, 0, tzinfo=zoneinfo.ZoneInfo(key='Etc/UTC')), '[)')
discount_pct numeric(5,2)  decimal.Decimal              Decimal('25.00')
session_id uuid            uuid.UUID                    UUID('9c898261-2033-981c-77e8-11b86e304fec')
age interval               datetime.timedelta           datetime.timedelta(days=1706, seconds=46473, microseconds=416327)
ip inet                    ipaddress.IPv4Address        IPv4Address('10.0.0.1')
raw bytea                  builtins.bytes               b'\xde\xad\xbe\xef'
f float8                   builtins.float               1.5
small int2                 builtins.int                 42
nothing (NULL)             builtins.NoneType            None
```

What to notice:

- **`numeric` becomes `Decimal`**, never `float`, so money keeps its cents exactly (Module 02). Summing prices in Python with `Decimal` gives the same result as `sum()` in SQL.
- **`timestamptz` becomes an aware `datetime`** whose `tzinfo` is the **session's `TimeZone` setting**, not your machine's. The instant is the same whatever the zone; only the display changes:

  ```text
  -- after SET TimeZone = 'Asia/Tokyo'
  2022-02-11 04:59:52+09:00
  ```

  `timestamp` (without time zone) becomes a *naive* `datetime`. Mixing naive and aware datetimes in Python raises `TypeError` on comparison, which is one more reason Bazaar uses `timestamptz` everywhere.
- **`jsonb`/`json` become Python objects** (`dict`, `list`, `str`, `int`, `float`, `bool`, `None`) through `json.loads`. Numbers inside JSON become `int` or `float`, not `Decimal`.
- **Arrays become lists**, including multidimensional ones (lists of lists), and `NULL` elements become `None`.
- **Enums become `str`** unless you register them (7.3).
- **Ranges become `psycopg.types.range.Range`** objects with `lower`, `upper`, `bounds` (`'[)'`), `isempty`, and `in` support (`some_datetime in r`).
- **`uuid`, `inet`/`cidr`, `bytea`, `interval`** map to `uuid.UUID`, the `ipaddress` classes, `bytes`, and `timedelta`. One caveat: an `interval` with months (`'1 month'`) has no exact `timedelta` equivalent. psycopg converts a month to 30 days, so keep calendar arithmetic in SQL.

### 7.2 From Python to Postgres

Dumpers pick a Postgres type from the Python value. Asking the server what it received (`SELECT pg_typeof(%s)`):

```text
Jsonb({'a': [1, 2]})                          -> jsonb
[1, 2, 3]                                     -> smallint[]
['a', None]                                   -> IndeterminateDatatype: could not determine data type of parameter $1
datetime.datetime(2025, 1, 1, 0, 0, tzinfo=da -> timestamp with time zone
datetime.datetime(2025, 1, 1, 0, 0)           -> timestamp without time zone
Range(1, 10, '[)')                            -> IndeterminateDatatype: could not determine data type of parameter $1
1099511627776                                 -> bigint
3.5                                           -> double precision
True                                          -> boolean
['a', None] with a cast -> ['a', None]
plain dict -> ProgrammingError: cannot adapt type 'dict' using placeholder '%s' (format: AUTO)
[] -> []
```

- **Python `int` gets the smallest type that holds the value** (`int2`, `int4`, `int8`, or `numeric`), and the server then casts as the context requires. You almost never notice this, but it is why `pg_typeof` says `smallint[]` above.
- **A `dict` is refused.** A Python dict could be meant as `json`, `jsonb`, or `hstore`, so psycopg makes you choose: wrap it as `Jsonb(d)` (or `Json(d)`). Lists are not ambiguous (they are arrays), so to send a JSON *array* you must wrap it too: `Jsonb([1, 2])`. The repository's `place_order` sends the cart as `Jsonb([{"product_id": ..., "quantity": ...}])`.
- **Lists of strings are sent with an unknown element type**, just like single strings. Usually the context supplies the type (`WHERE tags && %s`); when it doesn't, cast: `%s::text[]`. The same goes for a `Range` of plain integers: `%s::int4range`.
- **Naive datetimes are sent as `timestamp without time zone`.** Compared with a `timestamptz` column, the server interprets them in the *session* time zone. On a server whose `TimeZone` isn't UTC, `datetime(2025, 12, 1)` then means a different instant than you think. Always build aware datetimes (`datetime(2025, 12, 1, tzinfo=UTC)`) for `timestamptz` columns.
- **An empty list works** where the type is known from context (`%s::int[]`, or a column), and becomes `'{}'`.

### 7.3 Enums, composites, and per-connection registration

Postgres enums arrive as plain strings. You can register them so they load as Python `Enum` members and Python enum members dump as the Postgres type:

```python
from psycopg.types.enum import EnumInfo, register_enum

info = EnumInfo.fetch(conn, "order_status")     # one catalog query: the type's OID and labels
register_enum(info, conn)                        # without a Python class: one is generated from the labels
```

```text
'paid'                       <- before registering
<Order_Status.paid: 2>       <- after
```

`register_enum(info, conn, OrderStatus, mapping={...})` maps to a class of your own instead. Bazaar's `db.configure()` does exactly that for `order_status` and `product_status`, mapping them onto the `StrEnum`s in [`models.py`](../../project/python/src/bazaar/models.py). A `StrEnum` member also compares equal to its string, so code that compares `order.status == "paid"` keeps working.

**Composite types** are Module 08's row-shaped types, and every table has one with the table's name. Unregistered, a composite value arrives as its text representation; registered, as a named tuple:

```python
from psycopg.types.composite import CompositeInfo, register_composite

print(repr(conn.execute("SELECT s FROM sellers s WHERE id = 1").fetchone()[0]))
register_composite(CompositeInfo.fetch(conn, "sellers"), conn)
seller = conn.execute("SELECT s FROM sellers s WHERE id = 1").fetchone()[0]
```

```text
'(1,37,"Happy Outlet 1",BR,"2022-03-23 17:56:17+00")'
sellers(id=1, user_id=37, shop_name='Happy Outlet 1', country_code='BR', created_at=datetime.datetime(2022, 3, 23, 17, 56, 17, tzinfo=zoneinfo.ZoneInfo(key='Etc/UTC')))
```

Every field went through its own loader (note the aware `datetime`). This is handy for queries that return a nested object per row, such as `SELECT o.id, a FROM orders o JOIN addresses a ON ...` (exercise 11).

Both registrations need the type's OID, which differs between databases (a custom type gets a new OID every time it is created, for instance after `reset.sh`). That is why they are done **per connection at connect time**, not hard-coded. With a pool, the `configure` callback is the place (section 14).

### 7.4 Custom loaders and dumpers

When the built-in mapping is not what you want, register your own. Two examples ([`examples/15_custom_adapters.py`](examples/15_custom_adapters.py)):

**A loader that trades exactness for speed.** An analytics script that sums 330,000 line totals doesn't need `Decimal`. psycopg ships a `FloatLoader` that can be registered for `numeric`, on this connection only:

```python
from psycopg.types.numeric import FloatLoader
conn.adapters.register_loader("numeric", FloatLoader)
```

```text
numeric -> Decimal (default)     Decimal  n=332,750 sum=Decimal('80292464.69')  0.55s
numeric -> float (FloatLoader)   float    n=332,750 sum=80292464.69             0.42s
```

About 25% faster here, and the sum happens to match to the cent. With floats that's luck, not a guarantee, so keep `Decimal` for anything that ends up on an invoice.

**A dumper and loader for a value class of your own:**

```python
@dataclass(frozen=True)
class Sku:
    seller_id: int
    code: str
    def __str__(self) -> str:
        return f"S{self.seller_id}-{self.code}"

class SkuDumper(Dumper):
    oid = psycopg.adapters.types["text"].oid      # send it as text
    def dump(self, obj: Sku) -> bytes:
        return str(obj).encode()

class SkuLoader(Loader):
    def load(self, data) -> Sku:
        seller, code = bytes(data).decode().split("-", 1)
        return Sku(int(seller[1:]), code)

conn.adapters.register_dumper(Sku, SkuDumper)
conn.execute("SELECT id, title FROM products WHERE sku = %s", (Sku(5, "000001"),))
```

```text
dumper: (1, 'Fernweh Slim-Fit Tank Top - Black')
loader: (Sku(seller_id=5, code='000001'),)
```

Loaders are chosen by **type OID**, so registering a loader for `text` changes *every* text column in its scope. That's why the example registers it on one cursor only. You might hope to give the column a domain type (Module 08) such as `CREATE DOMAIN sku AS text` and register a loader for that, but result columns of a domain type are reported with the *base* type's OID, so a loader for the domain never fires. Loaders can only be targeted at real types: base, enum, composite and range types.

### 7.5 Text and binary formats

Every value travels either as text (`'24.99'`) or in a binary format (the server's internal representation). By default, psycopg sends parameters in whichever format suits the Python type (binary for numbers, as the trace in 1.2 showed, text for strings) and asks for results in text. `conn.cursor(binary=True)` or `execute(..., binary=True)` asks for binary results. Binary is faster for some types (timestamps, numbers, bytea, arrays of them) and slower or equal for text-heavy rows. It is worth measuring when a query returns millions of rows of numbers and timestamps, and not worth thinking about otherwise. asyncpg (Module 23) uses binary everywhere.

---

## 8. Errors

### 8.1 The hierarchy

psycopg raises exceptions from the DB-API hierarchy, and under each DB-API class, **one class per SQLSTATE** in `psycopg.errors`:

```text
Exception
 └─ psycopg.Error
     ├─ InterfaceError                  misuse of the driver (e.g. a closed cursor)
     └─ DatabaseError                   anything the server reported
         ├─ DataError                   class 22: bad value (division_by_zero, invalid_text_representation, ...)
         ├─ OperationalError            class 08/53/57/58: connection lost, out of resources, admin shutdown, query_canceled
         ├─ IntegrityError              class 23: unique_violation, foreign_key_violation, check_violation, not_null_violation
         ├─ InternalError               class XX, and 25 (InFailedSqlTransaction)
         ├─ ProgrammingError            class 42: syntax_error, undefined_table, insufficient_privilege, ...
         └─ NotSupportedError           class 0A: feature_not_supported
```

```text
UniqueViolation MRO: UniqueViolation -> IntegrityError -> DatabaseError -> Error -> Exception
lookup('40001'): SerializationFailure
lookup('BZ001'): KeyError 'BZ001'
```

([`examples/10_errors.py`](examples/10_errors.py).) Catch the narrowest class that expresses what you can handle: `except psycopg.errors.UniqueViolation` when a duplicate is a normal outcome ("this e-mail is taken"), `except psycopg.errors.SerializationFailure` in a retry loop. `psycopg.errors.lookup(sqlstate)` maps a code to its class. For codes PostgreSQL doesn't define, such as Bazaar's own `BZ001`, there is no class: those errors arrive as plain `psycopg.DatabaseError`, and you dispatch on `e.sqlstate`.

### 8.2 What the server tells you: `diag`

Every server error carries structured fields, available as `e.diag` (and the code as `e.sqlstate`):

```text
1. UniqueViolation 23505
   message_primary: duplicate key value violates unique constraint "reviews_product_id_user_id_key"
   message_detail:  Key (product_id, user_id)=(17063, 7068) already exists.
   constraint_name: reviews_product_id_user_id_key | table: bazaar.reviews
   status: INERROR
```

The fields you'll use most are `sqlstate`, `message_primary`, `message_detail`, `message_hint`, `constraint_name`, `table_name`, `schema_name`, `column_name`, and `context` (the PL/pgSQL call stack). `constraint_name` is the reliable way to tell *which* rule a write broke. This is where named constraints (Module 02's naming conventions) pay off: `reviews_product_id_user_id_key` maps to "you already reviewed this product" without parsing any message text. Messages may be translated (`lc_messages`) and change between versions; SQLSTATEs and constraint names don't.

### 8.3 Bazaar's error codes, as Python exceptions

`place_order()` reports business failures with custom SQLSTATEs (Module 10): `BZ001` insufficient stock, `BZ002` product unavailable, `BZ003` inactive user, `BZ004` foreign address, and `22023` for a malformed cart:

```text
3. psycopg.DatabaseError BZ001
   message_primary: insufficient stock for product 47
   message_detail:  requested 3, available 2
   context:         PL/pgSQL function place_order(bigint,jsonb,bigint) line 77 at RAISE
```

Code that calls the repository shouldn't need to know SQLSTATEs or driver classes. [`bazaar/errors.py`](../../project/python/src/bazaar/errors.py) defines domain exceptions and one translation function:

```python
class InsufficientStock(BazaarError):
    def __init__(self, message, product_id=None, requested=None, available=None): ...

def translate(exc):
    """Return the domain exception for a database error, or `exc` itself."""
    sqlstate = getattr(exc, "sqlstate", None)
    if sqlstate == "BZ001":
        ...  # parse product id from the message, requested/available from the DETAIL
        return InsufficientStock(message, product_id=..., requested=..., available=...)
    cls = {"22023": InvalidOrder, "BZ002": ProductUnavailable,
           "BZ003": UserInactive, "BZ004": AddressMismatch}.get(sqlstate)
    return cls(message) if cls else exc
```

and `repo.place_order()` uses it like this:

```python
try:
    with conn.transaction():
        rows = conn.execute(PLACE_ORDER, params).fetchall()
except psycopg.Error as e:
    raise translate(e) from e
```

`raise ... from e` keeps the original exception as `__cause__`, so logs still show the SQLSTATE and the full server message. The `try` is *outside* the `with`: the transaction block sees the exception first and rolls back its savepoint, then we translate. The function only reads `.sqlstate`, `.diag` or asyncpg's `.detail`, so Module 23's async code reuses it unchanged.

### 8.4 The connection after an error

What you can do next depends on the error and the transaction state:

- **Inside a transaction (`INTRANS`), any error aborts the transaction** (`INERROR`). Every following statement fails until you roll back:

  ```text
  2. InFailedSqlTransaction: current transaction is aborted, commands ignored until end of transaction block
     after rollback: usable again
  ```

  With `transaction()` blocks this is automatic: the block that saw the error rolls back (to its savepoint, or entirely).
- **In autocommit mode** a failed statement only fails itself. The connection is immediately usable.
- **When the connection itself breaks** (the server restarts, an administrator terminates the backend, the network drops), you get an `OperationalError` subclass, and the connection is dead:

  ```text
  5. AdminShutdown: terminating connection due to administrator command
     closed: True | broken: True
  ```

  `conn.broken` distinguishes "closed because something failed" from "closed by you". A broken connection can't be revived. Open a new one, and **re-run the whole transaction**, since you can't know whether a `COMMIT` that was in flight succeeded. Pools (section 14) discard broken connections automatically.

### 8.5 Notices

Not everything the server says is an error. `RAISE NOTICE` in PL/pgSQL, and informational messages like "table does not exist, skipping", arrive as **notices**. By default psycopg **discards** them. Register a handler to see them:

```python
conn.add_notice_handler(lambda n: print(f"   notice [{n.severity}] {n.message_primary}"))
conn.execute("DO $$ BEGIN RAISE NOTICE 'reindexing % products', 200000; END $$")
conn.execute("DROP TABLE IF EXISTS no_such_table")
```

```text
   notice [NOTICE] reindexing 200000 products
   notice [NOTICE] table "no_such_table" does not exist, skipping
```

Handy for progress reporting from long-running functions, and for tests that check a migration emits the warnings you expect.

---

## 9. COPY: moving many rows

### 9.1 COPY FROM STDIN

Module 03 used `COPY` and psql's `\copy` to load files. From Python, `COPY ... FROM STDIN` streams rows from your program over the existing connection. psycopg wraps it in a context manager:

```python
with conn.cursor() as cur:
    with cur.copy("COPY bench_load (sku, seller_id, price, title, observed_at) FROM STDIN") as copy:
        for row in rows:                  # any iterable: a list, a generator, a CSV reader
            copy.write_row(row)           # Python values -> COPY text format, escaped correctly
```

`write_row()` adapts each value with the same dumpers as query parameters and takes care of COPY's escaping (tabs, newlines, backslashes, `NULL` as `\N`). You never format COPY lines by hand. If the data is already in COPY or CSV format (a file, say), skip the per-row work and pass raw bytes:

```python
with open("prices.csv", "rb") as f, cur.copy("COPY price_staging FROM STDIN (FORMAT csv, HEADER)") as copy:
    while chunk := f.read(64 * 1024):
        copy.write(chunk)
```

COPY is all-or-nothing: one bad row (a constraint violation, a malformed number) fails the whole COPY, and the error message names the line (`CONTEXT: COPY bench_load, line 5123`). That's why importers usually COPY into a forgiving **staging table** first, validate there with SQL, and then insert the good rows into the real table. `repo.import_products()` follows that pattern (section 16), and exercise 5 extends it with row-level rejection reports.

`copy.set_types([...])` declares the column types up front. It is required for binary COPY (the server doesn't describe the columns of a `COPY FROM`, so psycopg must know which binary format to produce) and is also how `write_row()` learns that a Python `dict` should be written as `jsonb`.

### 9.2 COPY TO STDOUT

The other direction streams a query result out:

```python
with conn.cursor() as cur, open("order_items.csv", "wb") as f:
    with cur.copy("COPY (SELECT * FROM order_items WHERE order_id <= 1000) TO STDOUT (FORMAT csv, HEADER)") as copy:
        for chunk in copy:                # bytes, as the server sends them
            f.write(chunk)
```

Iterating `copy` yields raw data chunks, which is the fastest way to dump a result to a file: no Python objects are created per row, and memory stays constant. `copy.rows()` instead parses rows into tuples (call `set_types()` first, or you get strings), which is convenient but costs per-row Python work (section 10's measurements).

### 9.3 Binary COPY

`(FORMAT BINARY)` sends values in their binary representation. It saves parsing on the server for numbers and timestamps, and requires `set_types()` on the client:

```python
with cur.copy("COPY bench_load (sku, seller_id, price, title, observed_at) FROM STDIN (FORMAT BINARY)") as cp:
    cp.set_types(["text", "int8", "numeric", "text", "timestamptz"])
    for r in rows:
        cp.write_row(r)
```

### 9.4 Seven ways to load 100,000 rows

[`examples/05_bulk_load_benchmark.py`](examples/05_bulk_load_benchmark.py) loads the same 100,000 rows (text, bigint, numeric, text, timestamptz, plus an identity primary key) into a fresh table in seven ways. Second run, so caches are warm:

```text
round trip (SELECT 1): 181 µs   rows: 100,000
execute() per row, autocommit (1 txn/row)       66.15 s      1,512 rows/s  (10,000 rows, scaled x10)
execute() per row, one transaction              20.56 s      4,863 rows/s
execute() per row, pipeline mode                 5.97 s     16,752 rows/s
executemany()                                    2.52 s     39,703 rows/s
one INSERT ... SELECT FROM unnest(arrays)        1.25 s     80,291 rows/s
COPY, text format                                0.40 s    249,025 rows/s
COPY, binary format                              0.32 s    311,706 rows/s
```

What the numbers say, from slowest to fastest:

1. **One transaction per row** pays for a WAL flush (`fsync`) at every commit: about 0.66 ms per row here, so 100,000 rows would take over a minute. Never load data in autocommit mode.
2. **One transaction, one `execute()` per row** removes the flushes but still pays a full network round trip per row, plus Python and server overhead per statement: about 200 µs a row. The round trip time (181 µs to `localhost` through Docker's port forwarding) sets the floor; across a real network (0.5–1 ms to a database in another availability zone), this line gets 3–5 times slower, while the COPY lines barely change.
3. **Pipeline mode** sends statements without waiting for each answer (section 11): 3.4 times faster.
4. **`executemany()`** uses the pipeline too, and saves Python overhead by preparing the statement once: 8 times faster than the loop.
5. **A single statement with arrays**: send each column as one array parameter and let `unnest()` turn them back into rows. One round trip, one statement, and a handy trick for medium-sized batches with `ON CONFLICT` or `RETURNING`.
6. **COPY** skips statement processing entirely. It's 50 times faster than the loop and 6 times faster than `executemany()`. Binary and text format were within noise of each other here; between our two runs they swapped places (0.28 s/0.43 s, then 0.40 s/0.32 s), so don't expect binary to be faster by default.

Rule of thumb: rows from a few to a few hundred, `executemany()`; thousands and more, COPY (into a staging table if you need upserts or validation).

> **In production:** these numbers are for an empty table with one index. Each additional index, foreign key, and trigger adds per-row cost on the server, and then the client-side method matters less. Loading into a table with an audit trigger (like `products`) is dominated by the trigger. For very large one-off loads, Module 17's advice applies: load into a table without secondary indexes, then build them.

---

## 10. Big results: server-side cursors and streaming

Section 3.3 said that a regular cursor holds the whole result in client memory. For `order_items` (1.27 million rows), that's a lot of memory. [`examples/06_streaming_memory.py`](examples/06_streaming_memory.py) reads all of it five ways, each in a fresh process so peak memory (RSS) is measured alone:

```text
client_fetchall  rows=1,266,928 units=1,666,261 time= 1.78s peak RSS= 495.1 MiB (+ 461.2 MiB)
client_iterate   rows=1,266,928 units=1,666,261 time= 2.25s peak RSS= 155.8 MiB (+ 121.8 MiB)
named            rows=1,266,928 units=1,666,261 time= 2.12s peak RSS=  41.7 MiB (+   7.5 MiB)
stream           rows=1,266,928 units=1,666,261 time= 0.97s peak RSS=  38.1 MiB (+   4.1 MiB)
copy_out         rows=1,266,928 units=1,666,261 time= 3.75s peak RSS=  37.9 MiB (+   3.9 MiB)
```

- **`fetchall()`**: 461 MB for four small columns: the raw libpq result *plus* 1.27 million Python tuples, each holding Python ints and `Decimal`s.
- **Iterating a client cursor**: Python objects are created and dropped one at a time, but libpq's buffer still holds all the rows (122 MB). Better, not bounded.
- **A named (server-side) cursor**: `conn.cursor(name="...")` sends `DECLARE order_items_export CURSOR FOR ...` and then fetches `itersize` rows per round trip (`FETCH FORWARD 5000`). Memory stays bounded by `itersize` regardless of result size.
- **`cursor.stream()`**: uses libpq's single-row or, with libpq 17+, chunked-rows mode (`size=1000`). The server sends the result normally and the client processes it as it arrives. It was the fastest here, with no extra round trips.
- **`COPY ... TO STDOUT` parsed with `rows()`**: constant memory, but slower here because parsing COPY text into Python values row by row is slower than psycopg's optimized row loaders. To write rows straight to a file without Python objects, iterate the raw chunks (9.2) instead.

How the two streaming approaches differ:

| | named cursor (`DECLARE`/`FETCH`) | `stream()` |
|---|---|---|
| Needs a transaction | yes (or `withhold=True`, which materializes the result at commit) | no |
| Round trips | one per `itersize` rows | one |
| Can run other queries meanwhile on the same connection | yes, between fetches | **no**: the connection is busy until the stream ends |
| Stop early | close the cursor; the server stops | psycopg must drain or cancel the rest of the result |
| Scrolling backwards | `scrollable=True` + `cur.scroll()` | no |
| Works through PgBouncer in transaction mode | yes, within one transaction | yes |

Two server-side details matter for named cursors. First, the planner optimizes cursor queries for fetching the **first 10%** of rows quickly (`cursor_tuple_fraction = 0.1`), which can pick a different plan than the same query run normally. Set `cursor_tuple_fraction = 1` for an export that will read everything. Second, the cursor's transaction holds a snapshot for the whole export, with the usual VACUUM consequences (Module 16). For an hours-long export, a replica (Module 19) is the better place.

---

## 11. Pipeline mode

Normally the client sends one statement and waits for its result before sending the next: **one round trip per statement**. With many small statements, the client spends most of its time waiting for the network. **Pipeline mode** (libpq 14+) lets the client send many statements back to back and read the results afterwards:

```text
 one by one:                         pipelined:
 client        server                client        server
   │──Q1──────▶ │                      │──Q1──────▶ │
   │◀────────R1─│                      │──Q2──────▶ │
   │──Q2──────▶ │                      │──Q3──────▶ │
   │◀────────R2─│                      │──Sync────▶ │
   │──Q3──────▶ │                      │◀────────R1─│
   │◀────────R3─│                      │◀────────R2─│
                                       │◀────────R3─│
 3 round trips                        1 round trip
```

In psycopg it's a block:

```python
with conn.pipeline():
    curs = [conn.execute(LOOKUP, (pid,)) for pid in ids]   # queued and sent; nothing awaited yet
results = [c.fetchone()[0] for c in curs]                   # all results are in by the block's end
```

[`examples/07_pipeline.py`](examples/07_pipeline.py) looks up the stock of 2,000 products one by one and then pipelined:

```text
2000 lookups  one by one:    300 ms   pipelined:     85 ms   speed-up x3.5
```

The libpq trace shows what happened on the wire for three pipelined statements: all the `Bind`/`Execute` messages go out, then one `Sync`, then all the answers come back. (The first two reuse the prepared statement `_pg3_0`, which the benchmark loop had executed often enough to be prepared automatically, section 12.)

```text
F	28	Bind	 "" "_pg3_0" 1 1 1 2 '\x00\x10' 1 0
F	6	Describe	 P ""
F	9	Execute	 "" 0
F	28	Bind	 "" "_pg3_0" 1 1 1 2 '\x00/' 1 0
F	6	Describe	 P ""
F	9	Execute	 "" 0
F	36	Parse	 "" "SELECT count(*) FROM sellers" 0
F	14	Bind	 "" "" 0 0 1 0
F	6	Describe	 P ""
F	9	Execute	 "" 0
F	4	Sync
F	4	Flush
B	4	BindComplete
B	33	RowDescription	 1 "?column?" 0 0 23 4 -1 0
B	11	DataRow	 1 1 '1'
B	13	CommandComplete	 "SELECT 1"
...
B	14	DataRow	 1 4 '2000'
B	13	CommandComplete	 "SELECT 1"
B	5	ReadyForQuery	 I
```

The speed-up grows with network latency. At 181 µs round trip it was 3.5x; at 1 ms it would be over 10x. What pipelines are and aren't:

- Statements still **execute one after another** on the server, in order. A pipeline saves waiting, not server work.
- **You can't use a result to build a later statement in the same pipeline**, because you don't have it yet. Pipelines suit independent statements: batched lookups, many updates, `executemany()` (which uses one internally).
- **Errors**: if statement 3 of 10 fails, statements 4–10 are skipped up to the next `Sync`, and psycopg raises the error when you leave the block (or fetch the failed result). Inside a transaction, the transaction is aborted as usual.
- `conn.pipeline()` blocks can be combined with `conn.transaction()` blocks in either order. `executemany()` already does this for you.

---

## 12. Prepared statements

A **prepared statement** is a statement the server has parsed (and possibly planned) once, kept under a name, and executed many times with different parameters. It saves parse and planning time, which for a query with several joins can exceed the execution time.

psycopg prepares **automatically**: it counts executions of each statement text on each connection, and once a statement has been executed `prepare_threshold` times (default 5), the next execution sends a named `Parse` (`_pg3_0`, `_pg3_1`, ...), and later executions send only `Bind`/`Execute` with that name. [`examples/08_prepared.py`](examples/08_prepared.py) watches `pg_prepared_statements` while a query runs:

```text
prepare_threshold = 5
after execution 1: []
...
after execution 5: []
after execution 6: [('_pg3_0', True, 0, 1), ('_pg3_1', True, 1, 0)]
after execution 7: [('_pg3_0', True, 0, 2), ('_pg3_1', True, 2, 0)]
```

On the sixth execution the orders query became `_pg3_0`. (`_pg3_1` is the monitoring query itself, which had also run 6 times by then.) The last two columns count *generic* and *custom* plans. For the first five executions of a prepared statement, the server plans it with the actual parameter values (custom plans), and after that it switches to a cached **generic plan** if that plan's estimated cost is not much worse. Module 12 discussed the occasional bad generic plan for skewed data. `plan_cache_mode = force_custom_plan` turns the switch off.

The benefit for a moderately complex query (3 joins, `GROUP BY`, `ORDER BY ... LIMIT`), 3,000 executions:

```text
never prepare    3000 executions: 4.41 s (1471 µs each)
prepare at once  3000 executions: 2.33 s (777 µs each)
```

Planning was almost half the cost of this query. You control it with:

- `psycopg.connect(..., prepare_threshold=N)`, or `conn.prepare_threshold = N`: `0` prepares at the first execution, `None` disables automatic preparation.
- `conn.execute(sql, params, prepare=True)` or `prepare=False` for a single statement.
- `conn.prepared_max` (default 100): how many statements a connection keeps prepared before it deallocates the least recently used.

Statements are identified by their **text**, which is one more reason to bind values as parameters: `WHERE id = 42` and `WHERE id = 43` are two different texts, while `WHERE id = %s` is one.

> **PgBouncer:** prepared statements live in one server backend. Behind PgBouncer in **transaction mode** (Module 17), your next transaction may run on a backend that never saw your `Parse`, and fails with `prepared statement "_pg3_0" does not exist`. PgBouncer 1.21+ fixes this when `max_prepared_statements` is non-zero: it tracks each client's protocol-level prepared statements and re-prepares them on whichever backend it picks. With an older PgBouncer, or one configured with `max_prepared_statements = 0`, connect with `prepare_threshold=None`.

---

## 13. LISTEN and NOTIFY

`NOTIFY channel, 'payload'` (or `SELECT pg_notify(channel, payload)`) sends a message to every session that has run `LISTEN channel`. It's Postgres's built-in publish/subscribe: no extra infrastructure, and transactional. psycopg delivers notifications through `conn.notifies()`, a generator ([`examples/11_listen_notify.py`](examples/11_listen_notify.py)):

```python
with psycopg.connect(DSN, autocommit=True) as conn:   # autocommit: LISTEN takes effect immediately
    conn.execute("LISTEN price_changed")
    for n in conn.notifies(timeout=3.0):
        print(n.channel, n.payload, n.pid)
```

A producer thread sends three notifications in one transaction, two of them identical, and later a fourth in autocommit:

```text
 0.51s producer: sent 3, committing
 0.52s got 'price_changed' payload='16' from pid 450
 0.52s got 'price_changed' payload='47' from pid 450
 0.53s got 'price_changed' payload='68' from pid 451
 3.00s timeout (3 s in total) reached, generator ended
```

The semantics you need to rely on:

- **Delivery happens at COMMIT.** Notifications sent in a transaction that rolls back are never delivered. Delivered notifications always describe committed data.
- **Duplicates within one transaction are collapsed**: the two `'16'` became one.
- **The listener must be in autocommit mode** (or commit after `LISTEN`), and notifications are delivered only while it isn't inside a transaction.
- **`notifies(timeout=..., stop_after=...)`**: `timeout` is the total time the generator runs (not an idle timeout, as the output shows), `stop_after` the number of notifications after which it stops. Without them it runs forever. Notifications that arrive while you aren't iterating are kept and delivered at the next `notifies()` call.
- **Nothing is stored.** A listener that is disconnected misses whatever was sent meanwhile. Use notifications as a *wake-up call* to read the real state from a table, never as the only copy of an event.
- Payloads are text, at most 8,000 bytes. The channel name is an identifier, so it can't be a parameter (section 4.2): use `pg_notify(%s, %s)` or compose with `sql.Identifier`.
- **Not through PgBouncer in transaction mode**: `LISTEN` belongs to one backend (Module 17). Listeners need a direct connection.

A synchronous `for n in conn.notifies()` loop blocks the thread, which makes it awkward in a program that does anything else. Module 23 builds an async consumer and a job worker that sleeps until `NOTIFY` wakes it.

---

## 14. Connection pools

### 14.1 Why and what

Section 2.4 measured 15 ms per new connection against half a millisecond for a pooled query. A **connection pool** keeps a few connections open and lends them out:

```python
from psycopg_pool import ConnectionPool

pool = ConnectionPool(DSN, min_size=2, max_size=10, open=False)  # at application start
pool.open(wait=True)                      # fail fast if the database is unreachable

def handle_request(product_id: int):
    with pool.connection() as conn:       # borrow (or wait for) a connection
        return repo.get_product(conn, product_id)
                                          # end of block: commit (or rollback on error), give it back
pool.close()                              # at application shutdown
```

`pool.connection()` has the same transaction semantics as `with psycopg.connect()`, except that the end of the block **returns** the connection instead of closing it. Before handing a connection out again, the pool checks its state: one returned in a transaction or broken is rolled back or discarded.

The pool lives as long as the process, created once at startup. Creating a pool per request, or per function call, is the same mistake as connecting per request, with extra steps. `psycopg_pool` is also thread-safe: one pool serves all the threads of a process.

### 14.2 The knobs

| Parameter | Default | Meaning |
|---|---|---|
| `min_size` | 4 | Connections kept open even when idle. The pool opens them in the background at startup. |
| `max_size` | `min_size` | Upper bound. When all are busy, the pool grows up to this, then makes callers wait. |
| `timeout` | 30 s | How long `connection()` waits for a free connection before raising `PoolTimeout`. |
| `max_waiting` | 0 (unlimited) | Callers allowed to queue; beyond it, `TooManyRequests` immediately. A load-shedding valve. |
| `max_lifetime` | 1 h | Connections are replaced after this long (with jitter), so memory bloat in long-lived backends and stale DNS don't accumulate. |
| `max_idle` | 10 min | Connections above `min_size` idle this long are closed. |
| `reconnect_timeout` | 5 min | How long the pool keeps trying to reconnect after losing the server before giving up (`reconnect_failed` callback). |
| `check` | none | Function run on a connection **before** lending it. `ConnectionPool.check_connection` runs an empty query: one round trip per checkout, but a dead connection is replaced instead of failing the caller's first query. |
| `configure` | none | Function run **once per new connection**: register adapters, set session parameters. |
| `reset` | none | Function run **every time a connection is returned**: clear session state. |
| `kwargs` | none | Extra arguments for `psycopg.connect()` (`autocommit`, `prepare_threshold`, `application_name`, ...). |
| `open` | `True` in 3.x but deprecated | Always pass `open=False` and call `pool.open(wait=True)` explicitly, or use the pool as a context manager. `wait=True` makes startup fail fast if the database is unreachable. |

### 14.3 When the pool is exhausted

With `max_size=2`, two threads holding their connections for two seconds, and a third request with `timeout=1.0`:

```text
2. after 1.00s: PoolTimeout: couldn't get a connection after 1.00 sec
   stats: {'requests_num': 3, 'requests_errors': 1, 'pool_size': 2, 'pool_available': 0}
```

`PoolTimeout` is the pool telling you that demand exceeds capacity: either the pool is too small, or (much more often) connections are held too long, by slow queries, by transactions left open across slow non-database calls (section 3.4), or by code that borrows a connection at the start of a request and keeps it to the end. `pool.get_stats()` (also `pool.pop_stats()`, which resets the counters) exposes waiting time, request counts, errors and connection churn: export them to your metrics system (Module 27).

### 14.4 Session state: `configure`, `reset`, and SET LOCAL

A pooled connection is shared by many requests over time, so any **session state** one request leaves behind is inherited by the next. In Bazaar this is a security issue: the seller portal identifies the seller with the `app.seller_id` setting that the RLS policies of Module 15 read. Set it with plain `SET`, and the next borrower is seller 42 too ([`examples/12_pool.py`](examples/12_pool.py)):

```python
with pool.connection() as conn:   # request 1: seller 42's portal page
    conn.execute("SET app.seller_id = '42'")
with pool.connection() as conn:   # request 2: someone else entirely
    conn.execute("SELECT current_setting('app.seller_id', true)")
```

```text
3. leaked:  app.seller_id = 42
   SET LOCAL: inside the transaction '42', next borrower ''
```

The fix is to scope the setting to the **transaction**: `SET LOCAL app.seller_id = '42'`, or `SELECT set_config('app.seller_id', %s, true)`, which accepts a parameter. The setting disappears at commit or rollback, before the connection goes back to the pool, and it also works behind PgBouncer in transaction mode. The Bazaar package wraps it as a context manager in [`db.py`](../../project/python/src/bazaar/db.py):

```python
@contextmanager
def seller_scope(conn, seller_id: int):
    with conn.transaction():
        conn.execute("SELECT set_config('app.seller_id', %s, true)", (str(seller_id),))
        yield conn

with pool.connection() as conn, seller_scope(conn, 42):
    conn.execute("SELECT ... FROM orders")      # RLS shows seller 42's orders only
```

(The `''` for the next borrower is how `current_setting` reports a custom setting that has been defined in the session and then reset; that's why `current_seller_id()` wraps it in `nullif(..., '')`.)

As a second line of defense, a `reset` callback can clear session state whenever a connection comes back. It must leave the connection **idle**, which trips people up:

```python
def naive_reset(conn):
    conn.execute("RESET ALL")          # BUG: on a non-autocommit connection this opens a transaction

def reset(conn):
    with conn.transaction():           # BEGIN; RESET ALL; COMMIT -> idle again
        conn.execute("RESET ALL")
```

```text
WARNING:psycopg.pool:error resetting connection: connection left in status INTRANS by reset function <function naive_reset at 0x7f121358fec0>: discarded
   reset=naive_reset: next borrower sees None, same backend: False
   reset=reset: next borrower sees '', same backend: True
```

The naive version "worked" (no leak) only because the pool threw the connection away and opened a new one: the 15 ms we wanted to avoid. `RESET ALL` resets settings. `DISCARD ALL` also drops prepared statements, temporary tables, `LISTEN` registrations and session advisory locks, so it is more thorough, and it throws away psycopg's automatically prepared statements on every return.

`configure` is the counterpart for things that *should* persist for a connection's whole life: Bazaar's `configure()` registers the enum adapters (7.3). The same callback can set `statement_timeout` or `search_path` (though connection `options`, 2.2, do that without a round trip). Like `reset`, it must leave the connection idle, so it commits at the end.

### 14.5 How big?

More connections are not more throughput. Postgres executes each connection's queries on one CPU core, and beyond a few active connections per core they just compete for CPU, locks and memory (Module 17). The useful sizing tool is **Little's law**: connections needed = request rate × time each request holds a connection. 400 requests per second that each hold a connection for 5 ms need about 2 connections on average. Size for peaks plus a margin, perhaps 5–10, and keep the *total* across all processes well below `max_connections`:

```text
web: 8 processes × max_size 10   =  80
workers: 4 processes × max_size 4 =  16
cron, migrations, admin sessions  ~ 10
                                   ----
                                    106  > max_connections = 100: some processes can't connect at peak
```

When that sum gets too large, the answer is not a larger `max_connections` but PgBouncer in front of the database (Module 17), with each process keeping a small pool.

### 14.6 Threads, processes, and connections

- A pool is safe to share between **threads**. A single `Connection` is too (psycopg serializes operations with a lock), but then threads wait for each other, so give each thread its own pooled connection.
- A connection must **never cross a `fork()`**: the child would share the socket with the parent and both would read each other's results. Servers like gunicorn fork workers after loading the app (`--preload`); create the pool *after* the fork, in each worker (for example in a post-fork hook or lazily on first use).
- Async code (Module 23) uses `AsyncConnectionPool`, which has the same parameters.

---

## 15. Seeing what your program sends

When the database does something you didn't expect, find out what was actually sent before guessing.

- **`application_name`** on every connection, so `pg_stat_activity` and the server log identify your process (2.2).
- **The server log**: for a debugging session, `options="-c log_statement=all"` on your own connection logs everything it sends (superuser only; `log_min_duration_statement` is the production-safe cousin, Module 17). The trace in section 3.2 was produced this way.
- **The libpq protocol trace**: `conn.pgconn.trace(fileno)` plus `set_trace_flags(psycopg.pq.Trace.SUPPRESS_TIMESTAMPS)`, as in sections 1.2 and 11. Nothing is more precise: it shows exactly which messages, types and formats crossed the wire, including prepared statement names and pipelined batches.
- **Python logging**: psycopg logs internal problems to the `psycopg` logger, and the pool logs its activity (connections opened, discarded, reset failures like the one in 14.4) to `psycopg.pool`; `logging.basicConfig(level=logging.DEBUG)` shows them. Server notices are *not* logged unless you add a notice handler (8.5) that logs them.
- **`conn.info`**: `backend_pid` (to find yourself in `pg_stat_activity`, or to terminate a stuck session), `transaction_status`, `server_version`, `parameter_status("TimeZone")`, `dsn` (without password).
- **`ClientCursor.mogrify()`** or `sql.Composed.as_string(conn)` to print a statement with its values or identifiers rendered, for logs. Never execute a mogrified string you built for logging.

---

## 16. The Bazaar data-access layer

This module's milestone ([project.md](project.md)) is the first piece of the Bazaar service: the `bazaar` package in [`project/python/`](../../project/python/). Here is what's there and why it is shaped this way.

```text
project/python/
├── pyproject.toml           # package "bazaar"; deps: psycopg[binary,pool], asyncpg; dev: pytest, ruff, ...
├── sql/2301_notify.sql      # (module 23) NOTIFY triggers
├── src/bazaar/
│   ├── config.py            # Settings from the environment (BAZAAR_DSN, pool sizes, statement timeout)
│   ├── db.py                # connect(), create_pool(), configure(), seller_scope()
│   ├── models.py            # dataclasses: Product, ProductHit, OrderSummary, OrderPage, NewProduct, ...
│   ├── errors.py            # domain exceptions + translate() from SQLSTATEs
│   ├── repo.py              # the synchronous repository (this module)
│   ├── aio/                 # (module 23) async twins: db.py, repo.py
│   └── worker.py            # (module 23) async job worker
└── tests/                   # pytest, against the real database
```

The repository is a set of plain functions, each taking a connection as its first argument:

| Function | SQL technique |
|---|---|
| `get_product(conn, id)` | `LEFT JOIN inventory`, `class_row(Product)`, enum and jsonb adaptation |
| `search_products(conn, q, limit=, category_id=, max_price=)` | `websearch_to_tsquery` + `ts_rank` on Module 13's `search_vector`; optional filters composed with `psycopg.sql` |
| `list_orders_for_user(conn, user_id, limit=, after=)` | keyset pagination on `(created_at, id)` (Module 12), opaque cursor tokens, `limit + 1` to know if there is a next page |
| `place_order(conn, user_id, items, address_id)` | calls `place_order()`, cart as `Jsonb`, a `transaction()` block, `BZ00x` → domain exceptions |
| `import_products(conn, products)` | COPY into a `TEMP ... ON COMMIT DROP` staging table, then `INSERT ... ON CONFLICT (seller_id, sku) DO UPDATE ... RETURNING (xmax = 0)` to count inserts versus updates, then upsert inventory |
| `enqueue_job(conn, kind, payload, queue=)` | inserts into Module 10's `jobs` |

Design decisions worth copying:

- **Functions never commit.** The caller owns the transaction (a request handler, a script, a test). Functions that must be atomic on their own use `conn.transaction()`, which nests as a savepoint. A caller can therefore combine several repository calls in one transaction, and a test can roll everything back.
- **SQL as module-level constants**, not hidden inside functions, so the async layer of Module 23 runs exactly the same statements, and so they're easy to find with `grep` when `pg_stat_statements` points at one.
- **Explicit column lists with aliases matching dataclass fields**, never `SELECT *` (section 6).
- **Parameters for every value, `psycopg.sql` for every optional clause.**
- **Domain exceptions at the boundary**: callers catch `InsufficientStock`, not `psycopg.DatabaseError` with `sqlstate == "BZ001"`.

The `(xmax = 0)` in `import_products` is a well-known trick. In a row version freshly inserted by this statement, the system column `xmax` is 0. A row that `ON CONFLICT DO UPDATE` updated has the updating transaction's id in `xmax` (Module 16's tuple header), so `RETURNING (xmax = 0)` tells inserts from updates. It relies on an implementation detail, so don't build anything that matters more than a statistic on it. (PostgreSQL 17's `MERGE ... RETURNING merge_action()` reports the action officially, but `MERGE` is not safe against concurrent inserts of the same key the way `ON CONFLICT` is.)

### Tests against the real database

The tests in [`project/python/tests/`](../../project/python/tests/) run against the actual Bazaar database, not mocks: the queries, the constraints, `place_order()` and RLS are the things worth testing. Each test runs inside a transaction that is never committed:

```python
@pytest.fixture
def conn():
    with db.connect(autocommit=True) as c, c.transaction(force_rollback=True):
        yield c
```

The test inserts whatever rows it needs (small factories in `tests/factories.py` create a user, a seller, a product with a given stock) and checks behavior. At the end, `force_rollback=True` undoes everything, and the next test starts from the same state. Because the repository's own `transaction()` blocks become savepoints inside the test's transaction, even failure paths are testable: `test_insufficient_stock_is_translated_and_harmless` checks that a failed checkout reserved nothing *and* that the connection can place an order right afterwards.

```bash
cd project/python && pytest tests/test_repo.py -v
```

Tests that need committed data (several connections at once, `LISTEN`/`NOTIFY`) create their own rows, commit them, and delete them afterwards. Module 23 has examples. Module 27 returns to test isolation with templates and parallel test runs.

---

## Summary

- A driver translates Python calls into the PostgreSQL protocol. psycopg 3 uses the **extended protocol with server-side binding**: SQL text and values travel separately, so values can never be parsed as SQL.
- `with psycopg.connect(...)` **commits on success, rolls back on exception, and closes**. Outside a `with`, nothing is committed unless you call `commit()`. Any statement opens an implicit transaction unless `autocommit=True`.
- Prefer **autocommit connections with `conn.transaction()` blocks** in services: the transaction boundary is visible, and nested blocks are savepoints, which makes code composable and testable with `force_rollback=True`.
- **Every value goes through `%s` / `%(name)s`.** Identifiers go through `psycopg.sql.Identifier` after a whitelist check. String-formatted SQL is an injection bug, demonstrated here returning every user and changing a price. Server-side binding means no parameters in `SET`, `LISTEN`, DDL or `IN %s`: use `set_config()`, `sql.Identifier`, and `= ANY(%s)`.
- Row factories (`dict_row`, `class_row(Dataclass)`) shape rows. Adaptation maps `numeric` to `Decimal`, `timestamptz` to aware `datetime` (in the session time zone), `jsonb` to Python objects (send with `Jsonb()`), arrays to lists, ranges to `Range`. Enums and composites need per-connection registration.
- Errors are classes per SQLSTATE with structured `diag`. Translate business SQLSTATEs (`BZ001`) into domain exceptions at the repository boundary. After an error inside a transaction, the connection is `INERROR` until rollback; after `OperationalError` it may be `broken`.
- For bulk loads, **COPY** is 50x faster than row-by-row inserts and 6x faster than `executemany()`. Never load one row per transaction.
- For big results, use a named cursor or `stream()` (memory bounded at a few MB instead of hundreds), or `COPY TO STDOUT` for exports.
- **Pipeline mode** batches independent statements into one round trip, and **automatic prepared statements** skip parsing and planning after 5 executions. Behind PgBouncer, prepared statements need PgBouncer 1.21+ with `max_prepared_statements`, or `prepare_threshold=None`.
- Use **one connection pool per process**, sized with Little's law, and never let session state leak between borrowers: `SET LOCAL`/`set_config(..., true)` inside a transaction, plus a `reset` callback that leaves the connection idle.

## What's next

Everything in this module blocks: while a query runs, the thread waits. Module 23 does the same work with `asyncio`, psycopg's async API and asyncpg, so one process can keep dozens of queries in flight. It also builds the async half of the Bazaar package, a `LISTEN`/`NOTIFY` consumer, and a job worker.
