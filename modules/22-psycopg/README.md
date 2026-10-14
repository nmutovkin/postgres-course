# Module 22: psycopg 3

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

Should you still learn psycopg2? Only to maintain old code. Two migration traps are worth knowing: psycopg 3 does not close a connection at the end of `with conn:` the same way (it *does* close), and server-side binding rejects a few things client-side binding allowed, such as parameters in `SET` or `IN %s` (section 4.2).

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

