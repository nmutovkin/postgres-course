# Module 26: Migrations with Alembic

<!-- nav:start -->
> **Theory** · [Exercises](exercises.md) · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 25 SQLAlchemy ORM](../25-sqlalchemy-orm/README.md) · [Course home](../../README.md) · [27 Production Python and Postgres →](../27-python-production/README.md)
<!-- nav:end -->

Product pages need URLs like `/p/voltix-wireless-headphones-527-123456`, so `products` needs a `slug` column: unique, never NULL, filled in for the 200,000 products that already exist. Checkout needs an idempotency key, so that a client that retries after a timeout doesn't buy twice. Until now, every schema change in this course was a numbered SQL file, and `reset.sh` applied them by **dropping the database and rebuilding it**. Production can't do that. Its database has been running for years, it holds the only copy of the orders, and while you change its schema, buyers keep checking out. This module brings Bazaar's schema under **Alembic**: versioned, reviewed, repeatable migrations that take every database from wherever it is to where the code needs it to be, written so that the shop stays online while they run.

**What you'll learn**

- Why schema changes are code, and how change-based migration tools work; how hand-written-SQL tools (Flyway, Sqitch, dbmate) compare with Alembic.
- Alembic's pieces (`alembic.ini`, `env.py`, revisions, the `alembic_version` table) and how Bazaar wires them: the DSN from the environment, `SET ROLE bazaar_owner`, `version_table_schema`, comparison options, filters, `lock_timeout` with retries, and an advisory lock against concurrent runs.
- **Adopting** Alembic on an existing database: a baseline revision, `alembic stamp`, and how a brand-new database gets the same schema (a cleaned `pg_dump --schema-only` snapshot), proved equal catalog by catalog.
- Revisions and the version graph: `upgrade`, `downgrade`, `current`, `history`, relative targets, branches and merges, labels, `stamp`, and offline SQL (`--sql`) for review.
- Autogenerate: what it detects, what it misses (enum values, CHECK expressions, functions, triggers, views, policies, renames), shown on real drift.
- Hand-written revisions: `op.execute` for functions and triggers, data migrations in batches, enum changes and the "unsafe use of new value" rule.
- Zero-downtime patterns, each measured with `pg_locks` and a traffic probe: `lock_timeout`, `ADD COLUMN` with defaults, `NOT NULL` through a validated `CHECK`, foreign keys `NOT VALID`, `CREATE INDEX CONCURRENTLY` in Alembic, type changes, renames by expand/contract, dropping columns, and batched backfills.
- Testing migrations (empty to head, stairway up/down, models match migrations, migrated equals reference) and running them in CI/CD: deployment ordering, roll-forward, squashing.

**Before you start**

```bash
./project/bin/reset.sh 26                  # the large dataset, as at the end of module 25 (about 3 min)
cd project/python
uv sync                                    # now also installs alembic (pyproject.toml)
source .venv/bin/activate
export BAZAAR_DSN=postgresql://postgres:postgres@localhost:5432/bazaar
```

Don't run `alembic stamp` yet: section 4 does it step by step. You need Module 24's `bazaar.sa.tables` (the metadata), Module 15's roles, and Module 10's view of locks. The examples are in [`examples/`](examples/); run them from `project/python`. Outputs come from Alembic 1.20.0, SQLAlchemy 2.1.4 and psycopg 3.3.6 against PostgreSQL 17.11 with the large dataset. Timings depend on the machine; the ratios are what matter.

---

<!-- toc:start -->
**Contents**

- [1. Why migrations](#1-why-migrations)
  - [1.1 The schema is code](#11-the-schema-is-code)
  - [1.2 What `reset.sh` did, and why it stops here](#12-what-resetsh-did-and-why-it-stops-here)
  - [1.3 Two families of tools](#13-two-families-of-tools)
- [2. Alembic's model](#2-alembics-model)
  - [2.1 The pieces](#21-the-pieces)
  - [2.2 A revision](#22-a-revision)
  - [2.3 The version graph and `alembic_version`](#23-the-version-graph-and-alembic_version)
- [3. Wiring Alembic to Bazaar](#3-wiring-alembic-to-bazaar)
  - [3.1 `alembic.ini`](#31-alembicini)
  - [3.2 `env.py`: where to connect, and as whom](#32-envpy-where-to-connect-and-as-whom)
  - [3.3 `env.py`: what Alembic compares, and where it keeps its version](#33-envpy-what-alembic-compares-and-where-it-keeps-its-version)
  - [3.4 Alembic from Python](#34-alembic-from-python)
- [4. Adopting Alembic on an existing database](#4-adopting-alembic-on-an-existing-database)
  - [4.1 Two kinds of databases](#41-two-kinds-of-databases)
  - [4.2 Making the baseline](#42-making-the-baseline)
  - [4.3 The baseline revision](#43-the-baseline-revision)
  - [4.4 Stamping the reference database](#44-stamping-the-reference-database)
  - [4.5 Proving that both paths arrive at the same schema](#45-proving-that-both-paths-arrive-at-the-same-schema)
  - [4.6 Revision 2601: a SQL file becomes a migration](#46-revision-2601-a-sql-file-becomes-a-migration)
- [5. Revisions and the version graph](#5-revisions-and-the-version-graph)
  - [5.1 Creating revisions](#51-creating-revisions)
  - [5.2 Moving around](#52-moving-around)
  - [5.3 Branches and merges](#53-branches-and-merges)
  - [5.4 `stamp` beyond adoption](#54-stamp-beyond-adoption)
  - [5.5 Offline mode: SQL for review](#55-offline-mode-sql-for-review)
  - [5.6 Downgrades](#56-downgrades)
- [6. Autogenerate: what it sees and what it misses](#6-autogenerate-what-it-sees-and-what-it-misses)
  - [6.1 How it works](#61-how-it-works)
  - [6.2 A demonstration on real drift](#62-a-demonstration-on-real-drift)
  - [6.3 What autogenerate detects, and what it doesn't](#63-what-autogenerate-detects-and-what-it-doesnt)
  - [6.4 Filters: `include_name` and `include_object`](#64-filters-include_name-and-include_object)
  - [6.5 The workflow](#65-the-workflow)
- [7. Writing revisions by hand](#7-writing-revisions-by-hand)
  - [7.1 Functions and triggers: `op.execute`](#71-functions-and-triggers-opexecute)
  - [7.2 Data migrations](#72-data-migrations)
  - [7.3 Enums: adding values](#73-enums-adding-values)
- [8. Zero-downtime schema changes](#8-zero-downtime-schema-changes)
  - [8.1 The lock queue, and `lock_timeout`](#81-the-lock-queue-and-lock_timeout)
  - [8.2 Measuring instead of guessing](#82-measuring-instead-of-guessing)
  - [8.3 Adding a column, with and without a default](#83-adding-a-column-with-and-without-a-default)
  - [8.4 `NOT NULL` without a long lock](#84-not-null-without-a-long-lock)
  - [8.5 Foreign keys: `NOT VALID`, then `VALIDATE`](#85-foreign-keys-not-valid-then-validate)
  - [8.6 Indexes: `CONCURRENTLY`, inside Alembic](#86-indexes-concurrently-inside-alembic)
  - [8.7 Changing a column's type](#87-changing-a-columns-type)
  - [8.8 Renaming a column: expand and contract](#88-renaming-a-column-expand-and-contract)
  - [8.9 Dropping a column: application first](#89-dropping-a-column-application-first)
  - [8.10 The patterns at a glance](#810-the-patterns-at-a-glance)
- [9. Bazaar's revisions](#9-bazaars-revisions)
- [10. Testing migrations](#10-testing-migrations)
- [11. Migrations in CI/CD and deployment](#11-migrations-in-cicd-and-deployment)
  - [11.1 Who runs migrations, and when](#111-who-runs-migrations-and-when)
  - [11.2 Deployment ordering](#112-deployment-ordering)
  - [11.3 When a migration fails in production](#113-when-a-migration-fails-in-production)
  - [11.4 Squashing old revisions](#114-squashing-old-revisions)
- [Summary](#summary)
- [What's next](#whats-next)

<!-- toc:end -->

## 1. Why migrations

### 1.1 The schema is code

Your application code assumes a schema. `services.checkout()` writes `orders.idempotency_key`; the product page reads `products.slug`. If the code is deployed to a database without those columns, it fails. If the column is dropped while old code still reads it, the old code fails. The schema and the code have to move together, through every environment: each developer's laptop, the CI database, staging, production, and the analytics replica.

A **migration** is a small, ordered, versioned script that changes the schema from one known state to the next. Treating schema changes as migration files gives you what you already have for code:

- **History and review.** Every change is a file in Git, reviewed in a pull request like any other code. "Who added this index, and why?" has an answer.
- **Reproducibility.** Any environment can be brought to exactly the state a given commit expects, by running the migrations it hasn't run yet. A new developer's database and production converge on the same schema.
- **Environments that can't be rebuilt.** Production can't be dropped and recreated from seed files. Migrations change it in place, preserving the data.
- **A record of the current state.** The database itself stores which migrations it has run, so the tool (and your application, at startup) can tell whether code and schema match.

### 1.2 What `reset.sh` did, and why it stops here

`project/db/` has served the course well: `0201_schema_core.sql`, `0701_schema_modeling.sql`, and so on, applied in name order by `reset.sh` onto a **freshly created database**. That's a migration system of sorts, with two properties that rule it out for production:

1. It always starts from nothing. It drops the database, so it can't take an existing database from state A to state B.
2. It mixes schema and data. `1101_seed_large.sql` truncates and regenerates 500,000 orders; nobody wants that in a migration.

There's also no record inside the database of which files were applied. `reset.sh` doesn't need one because it always applies everything; a tool that runs only the missing changes does.

### 1.3 Two families of tools

**State-based** (declarative) tools keep the *desired* schema as a file (a `schema.sql`, or ORM models) and compute the difference to the live database at deploy time: "add this column, drop that index". Examples are migra, Atlas and pg-schema-diff. They're pleasant for simple changes, but a computed diff can't know your intent: a renamed column looks like a dropped column plus a new one, and the diff can't say "backfill this in batches" or "build this index concurrently".

**Change-based** (imperative) tools keep an ordered list of changes, each written (or at least reviewed) by a person, and record in the database which ones have run. `project/db/` is change-based without the record. Almost all production migration tools are change-based, and they differ in how changes are written and ordered:

| Tool | Changes written in | Order | Down migrations | Notes |
|---|---|---|---|---|
| **Flyway** | SQL files `V7__add_slug.sql` (or Java) | version number in the file name | paid feature ("undo") | JVM; checksums detect edited files |
| **Sqitch** | SQL scripts: `deploy/`, `revert/`, `verify/` | a plan file with explicit dependencies | `revert/` scripts | no ORM; `verify` scripts test each change |
| **dbmate**, **golang-migrate** | SQL: one file with `-- migrate:up` / `-- migrate:down` sections (dbmate), or `NNN_name.up.sql` / `.down.sql` pairs (golang-migrate) | timestamp or number in the file name | the down part | single binaries, language-agnostic |
| **Alembic** | Python files calling `op.*` and `op.execute(sql)` | a graph: each revision names its parent (`down_revision`) | `downgrade()` function | SQLAlchemy's tool: autogenerate from metadata, offline SQL output, branches and merges |

Plain-SQL tools have real strengths: the migration *is* the SQL a DBA reviews, and nothing is generated. Their weakness is everything that isn't a single SQL statement: a batched backfill with progress output, "build this index only if a previous attempt left an invalid one", conditional logic. Alembic gives you Python for those, `op.execute()` for raw SQL, a version graph that tolerates parallel development, **autogenerate** to draft migrations from the SQLAlchemy metadata you wrote in Module 24, and **offline mode** to print the SQL for review anyway. Bazaar's Python service already describes its schema in `bazaar.sa.tables`, so Alembic is the natural choice. Most of Bazaar's revisions still end up as SQL inside Python, because the interesting parts of PostgreSQL schema changes (functions, triggers, locks, concurrency) are SQL.

---

## 2. Alembic's model

### 2.1 The pieces

`alembic init migrations` creates the scaffolding:

```text
Creating directory .../migrations ...  done
Creating directory .../migrations/versions ...  done
Generating .../migrations/script.py.mako ...  done
Generating .../migrations/README ...  done
Generating .../migrations/env.py ...  done
Generating .../alembic.ini ...  done
Please edit configuration/connection/logging settings in .../alembic.ini before proceeding.
```

| Piece | Role |
|---|---|
| `alembic.ini` | Configuration for the `alembic` command: where the scripts are, file naming, hooks, logging. |
| `migrations/env.py` | Python that Alembic runs for every command that touches a database: it creates the connection, configures the *migration context*, and calls `run_migrations()`. This is where all the policy lives. |
| `migrations/script.py.mako` | The template for new revision files. |
| `migrations/versions/*.py` | The revisions. |
| `alembic_version` | A one-column table in the database: the revision(s) it is at. |

(`alembic list_templates` shows variants: `async` for an async driver, `pyproject` to keep the configuration in `pyproject.toml`, `multidb`.)

### 2.2 A revision

A revision is a Python module with four identifiers and two functions:

```python
revision = "2604"                 # this revision's id
down_revision = "2603"            # its parent; None for the first one
branch_labels = None              # optional names for a line of revisions
depends_on = None                 # optional extra dependencies (section 5.3)

def upgrade() -> None:
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS idempotency_key text")
    ...

def downgrade() -> None:
    drop_index_concurrently("orders_idempotency_key_idx")
    op.execute("ALTER TABLE orders DROP COLUMN idempotency_key")
```

`op` is Alembic's operations API: `op.create_table()`, `op.add_column()`, `op.create_index()`, `op.alter_column()`, `op.drop_constraint()` and so on, which render DDL for the database's dialect, plus `op.execute()` for anything else. Revisions don't connect to the database themselves: they run inside the context that `env.py` set up, and `op` sends their statements over its connection (or, offline, prints them).

### 2.3 The version graph and `alembic_version`

Revisions form a **directed graph** through `down_revision`. The **base** is "no revision"; a **head** is a revision that no other revision names as its parent. Normally there's exactly one head, and the graph is a straight line:

```text
<base> -> 2600 -> 2601 -> 2602 -> 2603 -> 2604 -> 2701 -> 2801 (head)
```

This module writes 2600–2604. The package already contains the later ones too: 2701 was the head at the end of Module 27; Module 28 adds 2801.

The database's `alembic_version` table holds the revision it's at. `alembic upgrade head` reads it, computes the path from there to the head, and runs each revision's `upgrade()` in order, updating `alembic_version` after each one. `alembic downgrade 2602` walks backwards, calling `downgrade()`. The ids are opaque strings: Alembic orders revisions *only* by the graph, never by comparing ids. By default it generates random 12-hex-digit ids (`3f1a9c0d2b7e`). Bazaar uses `--rev-id` with the module number and a sequence (`2601`, `2602`), like `project/db/`, which makes the history readable; section 5.1 discusses what that costs.

---

## 3. Wiring Alembic to Bazaar

The reference setup is in [`project/python/alembic.ini`](../../project/python/alembic.ini) and [`migrations/env.py`](../../project/python/migrations/env.py). Most of what's interesting is a deliberate choice, so this section goes through them.

### 3.1 `alembic.ini`

```ini
[alembic]
script_location = %(here)s/migrations
file_template = %%(rev)s_%%(slug)s       # 2602_products_slug_expand_phase_nullable.py
truncate_slug_length = 40
prepend_sys_path = .
timezone = UTC

[post_write_hooks]
hooks = ruff_fix, ruff_format             # generated revisions are linted and formatted
ruff_fix.type = module
ruff_fix.module = ruff
ruff_fix.options = check --fix --quiet REVISION_SCRIPT_FILENAME
...
```

There's **no `sqlalchemy.url`**. The generated file has `sqlalchemy.url = driver://user:pass@localhost/dbname`, which invites committing a password to Git and makes it hard to point the same scripts at different environments. Bazaar's `env.py` takes the DSN from the environment, through the same `bazaar.config.Settings` the application uses.

### 3.2 `env.py`: where to connect, and as whom

```python
def _engine() -> sa.Engine:
    connection = config.attributes.get("connection")
    if connection is not None:              # a caller (tests, bazaar.migrate) passed one in
        return connection
    s = get_settings()
    s = replace(
        s,
        dsn=x_args.get("dsn") or s.migration_dsn,   # alembic -x dsn=... overrides
        app_name="bazaar-migrations",                # visible in pg_stat_activity and the logs
        statement_timeout_ms=0,                      # backfills and index builds may take minutes
        lock_timeout_ms=0,                           # set below, per session
        idle_in_transaction_timeout_ms=0,
    )
    return create_engine(s, poolclass=NullPool)
```

- **`BAZAAR_MIGRATION_DSN`** (falling back to `BAZAAR_DSN`). Migrations need more power than the application: they create tables, functions, extensions. The application should connect as `bazaar_app`, which can't. In the course both are the `postgres` superuser; in production they're different credentials, and only the deployment pipeline has the migration one.
- **`NullPool`**: a migration run uses one connection once; there's nothing to pool.
- **No `statement_timeout`**: the application's 5-second default would kill a legitimate index build. Migrations get a `lock_timeout` instead (section 8.1), which is the limit that protects traffic.

Then the session is prepared:

```python
def _prepare_session(connection: sa.Connection) -> None:
    _acquire_migration_lock(connection)                 # section 11.1
    connection.execute(sa.text("SELECT set_config('lock_timeout', :t, false)"),
                       {"t": _lock_timeout()})          # default 2s, or -x lock_timeout=5s
    has_owner = ...  # does the role bazaar_owner exist?
    owner = f" AUTHORIZATION {OWNER_ROLE}" if has_owner else ""
    connection.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}{owner}"))
    if has_owner:
        connection.execute(sa.text(f"SET ROLE {OWNER_ROLE}"))
    connection.commit()
```

**`SET ROLE bazaar_owner`** is Module 15's rule: every object belongs to `bazaar_owner`, and the default privileges defined *for that role* grant `bazaar_app` and `bazaar_readonly` their access to new tables, sequences and functions automatically. A table created by `postgres` would belong to `postgres`, and the application couldn't read it. Revision 2701 creates the `outbox` table with `op.create_table()` and no `GRANT` at all, and `bazaar_app` can use it, because the default privileges fired. `SET` (not `SET LOCAL`) lasts for the session, across the commits between revisions.

The schema has to exist before Alembic creates `bazaar.alembic_version` in it, hence `CREATE SCHEMA IF NOT EXISTS ... AUTHORIZATION bazaar_owner`. The first version of this `env.py` created the schema *before* checking the role, as `postgres`, and the next statement failed with `permission denied for schema bazaar` once it was running as `bazaar_owner`. Ownership mistakes show up immediately with a fresh database, which is one reason to test migrations on one (section 10).

### 3.3 `env.py`: what Alembic compares, and where it keeps its version

The context options live in `bazaar.migrate.context_options()`, so that the tests use exactly the same ones:

```python
{
    "target_metadata": metadata,                 # bazaar.sa.tables: what the schema should be
    "version_table_schema": "bazaar",            # bazaar.alembic_version
    "include_schemas": False,
    "include_object": include_object,
    "include_name": include_name,
    "compare_type": True,
    "compare_server_default": True,
    "autogenerate_plugins": [
        "alembic.autogenerate.*",
        "~alembic.autogenerate.comments",
        "alembic.ext.checkconstraint_byname",
    ],
    "transaction_per_migration": True,
}
```

- **`version_table_schema="bazaar"`** puts `alembic_version` next to the tables it versions. Without it, Alembic creates the table in whatever schema comes first in the connection's `search_path`; that happens to be `bazaar` here, but a migration role with a different `search_path` would silently create a *second* version table in `public` and think the database is empty.
- **`include_schemas=False`**: the metadata's tables aren't schema-qualified (Module 24 resolves them through `search_path`), so autogenerate compares only the connection's default schema, which is `bazaar`. With `include_schemas=True` it would also reflect `public` (where the extensions live) and treat `bazaar` as "another schema", comparing `bazaar.products` against an unqualified `products` it couldn't match.
- **`compare_type`** detects column type changes (`text` versus `varchar(40)`); **`compare_server_default`** detects `DEFAULT` changes. Both make autogenerate noisier but catch real drift.
- **`autogenerate_plugins`** (Alembic 1.18+) selects the comparison plugins. Bazaar takes the defaults, removes comment comparison (the SQL files put `COMMENT`s on every table, and the metadata doesn't repeat those texts), and adds the opt-in plugin that compares `CHECK` constraints **by name**.
- **`transaction_per_migration=True`**: each revision commits on its own. A failure in revision 3 leaves revisions 1 and 2 applied and recorded. Without it, all pending revisions run in one transaction, which sounds safer but means a long chain holds every lock it took until the end, and it can't coexist with autocommit blocks (section 8.6).

`include_name` and `include_object` filter what autogenerate looks at; section 6.4 explains why Bazaar needs them.

### 3.4 Alembic from Python

The `alembic` command is a thin layer over `alembic.command`. [`bazaar/migrate.py`](../../project/python/src/bazaar/migrate.py) wraps it for tests and deployment scripts:

```python
from bazaar import migrate

migrate.upgrade(dsn)                    # alembic -x dsn=... upgrade head
migrate.downgrade(dsn, "2602")
migrate.upgrade_sql("2601:2604")        # offline SQL, as a string
migrate.current_revisions(conn)         # ('2801',)
migrate.check_schema(conn)              # raises SchemaOutOfDate unless at head
```

`check_schema()` is what the Module 28 service runs at startup (and the test suite checks the reference database with it). Code that expects revision 2801 refuses to start against a database at 2604, instead of failing on the first request that touches the outbox:

```text
schema not at head: database is at ['2603'], code expects ['2801']: run `alembic upgrade head` (or `alembic stamp 2600` first on a database built by project/bin/reset.sh)
```

---

## 4. Adopting Alembic on an existing database

### 4.1 Two kinds of databases

The reference database already exists: `reset.sh 26` built it from the SQL files, and production would have been built the same way, over years. Alembic must take it over without recreating anything. At the same time, a test run, a new developer or a new region needs to build the same schema **from nothing**. Both paths must arrive at the same schema, or "works in CI, fails in production" is guaranteed.

The standard answer is a **baseline revision**: the first revision represents the entire existing schema.

- An **existing** database is *stamped*: `alembic stamp 2600` writes `2600` into `alembic_version` without running anything. "This database already has everything up to the baseline."
- A **new** database runs the baseline's `upgrade()`, which must create that same schema.

What should the baseline's `upgrade()` contain? There are four options:

| Option | New databases | Weakness |
|---|---|---|
| (a) An empty baseline: `upgrade()` does nothing | cannot be built at all | Tests and new environments must run `reset.sh` first, forever. |
| (b) Run the old SQL files from `project/db/` | built by the same files as production | The files mix schema and seeds (`1101` regenerates 500k orders), use psql (`\set`), create roles and grant on a database named `bazaar`, and must never change again, or new databases diverge. The migrations would depend on a directory outside the package. |
| (c) A **schema-only dump** of the reference database | built from a snapshot of exactly what production has | A big generated file; it must be taken once, from the right state, and cleaned. |
| (d) An autogenerated `op.create_table(...)` baseline from the metadata | built from the Python description | Autogenerate can't express functions, triggers, views, RLS policies, grants, the partitioned `events` table, extensions or domains (section 6.3). Half of Bazaar would be missing. |

Bazaar uses **(c)**. A schema-only dump is PostgreSQL's own description of the schema, complete by construction: tables, partitions, constraints, indexes, functions, triggers, views, materialized views, policies, ownership, grants, default privileges, extended statistics. Its content is frozen into the repository, so the baseline can never drift from what it was on adoption day, and tests can prove the two paths converge (section 4.5).

### 4.2 Making the baseline

[`migrations/baseline/make_baseline.sh`](../../project/python/migrations/baseline/make_baseline.sh) dumps the reference state and cleans it:

```bash
./project/bin/reset.sh 26
COURSE_PG_CONTAINER=course-pg project/python/migrations/baseline/make_baseline.sh
```

```bash
docker exec "$CONTAINER" pg_dump -U postgres -d "$DB" --schema-only --no-tablespaces \
  | sed -E \
      -e '/^\\(un)?restrict /d' \
      -e "/^SELECT pg_catalog.set_config\('search_path', '', false\);$/d" \
      -e '/^SET [a-z_]+ = .*;$/d' \
      -e 's/^CREATE SCHEMA bazaar;$/CREATE SCHEMA IF NOT EXISTS bazaar;/'
```

Each edit has a reason:

- **`\restrict` / `\unrestrict`.** Since PostgreSQL 17.6 (and the matching minor releases of older versions), `pg_dump` brackets its output with these psql meta-commands, a defence against a malicious server injecting psql commands into a dump. The baseline is executed through a driver, which doesn't understand psql commands.
- **The `SET`s at the top.** `pg_dump` starts with `SET statement_timeout = 0; SET lock_timeout = 0; ... SELECT pg_catalog.set_config('search_path', '', false);`. Those are *session* settings: executed on the migration connection, they'd outlive the baseline and silently switch off the `lock_timeout` that `env.py` set for every later revision in the same run. Everything in the dump is schema-qualified (`CREATE TABLE bazaar.products`), so the empty `search_path` isn't needed. The one setting that matters, `check_function_bodies = false` (functions may reference objects created later in the file), is set transaction-locally by the revision instead.
- **`CREATE SCHEMA IF NOT EXISTS`**, because `env.py` has already created the schema to hold `alembic_version`.

The cleaned dump of the reference state is 4,102 lines. It must be taken **before** anything else touches the database: in the course, on a fresh `reset.sh 26` database, before the NOTIFY triggers of `sql/2301_notify.sql` are added, because they become revision 2601. It's generated once, on adoption day, and committed. Never regenerate it: databases built from a newer snapshot would differ from the ones that were stamped.

### 4.3 The baseline revision

[`versions/2600_baseline.py`](../../project/python/migrations/versions/2600_baseline.py):

```python
def upgrade() -> None:
    op.execute("RESET ROLE")  # superuser for this revision
    op.execute("SET LOCAL check_function_bodies = false")  # as pg_restore does
    roles = "\n".join(
        f"    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{r}') THEN"
        f" CREATE ROLE {r} NOLOGIN; END IF;"
        for r in ROLES
    )
    execute_script(f"DO $$\nBEGIN\n{roles}\nEND\n$$;\nGRANT bazaar_readonly TO bazaar_analyst;\n")
    execute_script(SCHEMA_SQL.read_text())
    # Database-level settings are not part of a schema dump.
    execute_script("""DO $$
BEGIN
    EXECUTE format('ALTER DATABASE %I SET search_path = bazaar, public', current_database());
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO bazaar_app, bazaar_analyst, bazaar_seller',
                   current_database());
END
$$;
SET search_path = bazaar, public;
REFRESH MATERIALIZED VIEW bazaar.seller_sales_daily;
""")
    op.execute("ALTER TABLE bazaar.alembic_version OWNER TO bazaar_owner")
    op.execute("SET ROLE bazaar_owner")


def downgrade() -> None:
    raise NotImplementedError(
        "the baseline cannot be downgraded: drop the database instead (DROP DATABASE ...)"
    )
```

The things a schema dump *doesn't* contain are added by hand:

- **Roles.** Roles belong to the cluster, not the database, so `pg_dump` never dumps them (`pg_dumpall --roles-only` does). The baseline creates them if missing, `NOLOGIN` and without passwords. Passwords are secrets and belong to provisioning (Module 27, section 9), not to a file in Git. In the course container the roles already exist; in a brand-new cluster (a test container) they don't, and Module 27 tests exactly that path.
- **Database-level settings and privileges.** `ALTER DATABASE bazaar SET search_path` and `GRANT CONNECT ON DATABASE` are properties of the database object, which a schema dump of its contents doesn't include. The database's name differs in every environment (`bazaar`, `bazaar_test_gw0_...`), hence `format('%I', current_database())`.
- **Materialized view contents.** A schema-only dump creates `seller_sales_daily` `WITH NO DATA`, and querying it would fail with "has not been populated". Refreshing an empty database's materialized view takes milliseconds.
- **Superuser rights.** `CREATE EXTENSION` and `ALTER ... OWNER TO` need them, so the baseline runs as the connecting role (`RESET ROLE`) and returns to `bazaar_owner` at the end. It also hands `alembic_version`, which Alembic created as `postgres` before the roles existed, to the owner.
- **No downgrade.** Downgrading the baseline would mean dropping the schema, including `alembic_version` itself. It raises instead.

`execute_script()` (in `bazaar.migrate`) deserves a note. The obvious `op.execute(sql_text)` turns the text into a SQLAlchemy `text()` construct, where `:name` is a bind parameter, and even `exec_driver_sql()` hands psycopg an (empty) parameter list, so psycopg treats every `%` as a placeholder. The first attempt failed exactly so:

```text
psycopg.ProgrammingError: only '%s', '%b', '%t' are allowed as placeholders, got '%I'
```

The dump contains `format('%I', ...)` in a function body. `execute_script()` sends the text through the DBAPI cursor *without* parameters, which psycopg passes on verbatim, using the simple query protocol that allows many statements in one call. Offline, it writes the text to the output unchanged.

### 4.4 Stamping the reference database

```bash
alembic current            # no output after the INFO lines: no alembic_version table yet
alembic stamp 2600
alembic current
```

```text
INFO  [alembic.runtime.migration] Running stamp_revision  -> 2600
2600
```

Stamping created `bazaar.alembic_version` and wrote one row. Nothing else changed. Now the database can be upgraded like any other:

```bash
alembic upgrade head
```

```text
INFO  [alembic.runtime.migration] Running upgrade 2600 -> 2601, notify triggers: jobs_ready and new_order (was project/python/sql/2301_notify.sql)
INFO  [alembic.runtime.migration] Running upgrade 2601 -> 2602, products.slug, expand phase: nullable column, slug trigger, batched backfill, unique index
INFO  [bazaar.migrate] backfill products: 200000 rows in 23.5s (batches of 10000)
INFO  [alembic.runtime.migration] Running upgrade 2602 -> 2603, products.slug NOT NULL, contract phase, without a long ACCESS EXCLUSIVE scan
INFO  [alembic.runtime.migration] Running upgrade 2603 -> 2604, orders.idempotency_key: a client-chosen key that makes checkout safe to retry
INFO  [alembic.runtime.migration] Running upgrade 2604 -> 2701, transactional outbox: outbox + processed_messages (module 27)
INFO  [alembic.runtime.migration] Running upgrade 2701 -> 2801, storefront: search_products() from module 13, its trigram index, a browse index
```

(Revisions 2701 and 2801 belong to Modules 27 and 28; they're already in the reference package, so `head` includes them.) A brand-new database needs no stamp:

```bash
createdb bazaar_new        # or CREATE DATABASE in psql
alembic -x dsn=postgresql://postgres:postgres@localhost:5432/bazaar_new upgrade head
```

The baseline takes under a second on an empty database; the slug backfill has nothing to do.

> **Pitfall:** `alembic stamp` believes you. Stamping a database that does *not* have the baseline schema (an older state, or one with local modifications) records a lie, and the next revision fails half-way or, worse, succeeds on the wrong schema. Before stamping production, compare its schema with the baseline: section 4.5's comparison does it in one call.

### 4.5 Proving that both paths arrive at the same schema

"The stamped database and the database built from nothing are identical" is a claim worth a test. Diffing two `pg_dump --schema-only` outputs is the obvious approach, but it needs a `pg_dump` binary of the server's version on the test machine, and a text diff of 4,000 lines is hard to read. [`bazaar/catalog.py`](../../project/python/src/bazaar/catalog.py) instead describes a schema from the system catalogs, using PostgreSQL's own deparsers, one entry per object:

```python
(
    "index",
    """
    SELECT ci.relname,
           concat_ws(' | ', pg_get_indexdef(i.indexrelid),
               CASE WHEN NOT i.indisvalid THEN 'INVALID' END)
    FROM pg_index i
    JOIN pg_class ci ON ci.oid = i.indexrelid
    ...
    """,
),
```

It covers relations (kind, owner, ACL, RLS flags, partition key and bounds, comments), columns (position, `format_type`, `NOT NULL`, default, identity, generated, collation, column ACLs), constraints (`pg_get_constraintdef` and whether they're validated), indexes (`pg_get_indexdef` and validity), functions (`pg_get_functiondef`, owner, ACL), triggers (`pg_get_triggerdef` and whether enabled), views, policies, enum labels in order, domains, sequences, extended statistics, the schema's ACL, default privileges and extensions. OIDs, physical column numbers, statistics and data are deliberately left out. `diff()` reports differences per object.

Comparing a reference database stamped at 2600 that also had `sql/2301_notify.sql` applied by hand (the way Module 23 first introduced the file) with a fresh database at 2600 found exactly what you'd expect:

```text
646 642
only in bazaar: function:notify_jobs_ready()
only in bazaar: function:notify_new_order()
only in bazaar: trigger:jobs.jobs_notify_ready
only in bazaar: trigger:orders.orders_notify_new
```

The baseline is the state *before* module 23's file; revision 2601 adds those four objects. After upgrading both to head (2801, including Module 27's outbox and Module 28's search function), the comparison found 683 objects on each side and no differences. `tests/test_migrations.py::test_fresh_database_equals_reference` runs this check on every test run.

### 4.6 Revision 2601: a SQL file becomes a migration

Module 23 introduced the triggers as `sql/2301_notify.sql`, a file applied by hand with psql. [`2601_notify_triggers.py`](../../project/python/migrations/versions/2601_notify_triggers.py) contains the same SQL, copied in rather than read from the file: a revision must not change when someone edits a file later. It uses `CREATE OR REPLACE FUNCTION` and `CREATE OR REPLACE TRIGGER` (PostgreSQL 14+), so it's safe on databases where the file was already applied. Stamping at 2600 and upgrading converges either way.

The first version indented the SQL inside the Python string, and the catalog comparison failed:

```text
differs: function:notify_new_order()
    bazaar: CREATE OR REPLACE FUNCTION bazaar.notify_new_order()
 ...
AS $function$
BEGIN
    PERFORM pg_notify('new_order', json_build_object(
    bazaar_mig: CREATE OR REPLACE FUNCTION bazaar.notify_new_order()
 ...
AS $function$
        BEGIN
            PERFORM pg_notify('new_order', json_build_object(
```

PostgreSQL stores a function body **verbatim**, whitespace included. Both versions behave identically, but they are different source code, and a later `CREATE OR REPLACE` from the other path would show up as a change. The revision now keeps the SQL flush left in module-level constants. The file `sql/2301_notify.sql` stays in the repository, with a note that it's superseded; the test suite no longer applies it.

---

## 5. Revisions and the version graph

### 5.1 Creating revisions

```bash
alembic revision -m "orders idempotency key" --rev-id 2604     # an empty revision to fill in
alembic revision --autogenerate -m "..."                         # a draft from the metadata diff
```

The file name follows `file_template` (`2604_orders_idempotency_key.py`), and the post-write hooks run ruff on it. Bazaar's template ([`script.py.mako`](../../project/python/migrations/script.py.mako)) puts a review checklist in every new file's docstring:

```text
Checklist (module 26): does any statement take an ACCESS EXCLUSIVE lock on a big
table for longer than a moment? Is every index on an existing table built
CONCURRENTLY (in an autocommit block)? Can old application code still run
against the new schema? Is downgrade() honest (or does it raise)?
```

**Sequential or random ids?** Readable ids (`2604`) make `alembic history` and code review easier. They collide when two developers each create "the next" revision on their branches: both files say `revision = "2605"`. Alembic refuses to load a directory with a duplicate id, so the collision surfaces in CI and someone renumbers. Random ids never collide, but two developers' revisions with the *same parent* still create two heads (section 5.3). Either way, the graph has to be put right before merging.

### 5.2 Moving around

```bash
alembic current                  # where is the database?
alembic history                  # the graph, newest first
alembic heads                    # the head revision(s) of the scripts
alembic show 2604                # one revision's details
alembic upgrade head             # everything pending
alembic upgrade +1               # one step; downgrade -1 goes back one
alembic downgrade 2602           # back to (and including) 2602's state
alembic upgrade 2601:2604 --sql  # offline: SQL for that range, no database needed
```

```text
$ alembic history
2701 -> 2801 (bazaar) (head), storefront: search_products() from module 13, its trigram index, a browse index
2604 -> 2701 (bazaar), transactional outbox: outbox + processed_messages (module 27)
2603 -> 2604 (bazaar), orders.idempotency_key: a client-chosen key that makes checkout safe to retry
2602 -> 2603 (bazaar), products.slug NOT NULL, contract phase, without a long ACCESS EXCLUSIVE scan
2601 -> 2602 (bazaar), products.slug, expand phase: nullable column, slug trigger, batched backfill, unique index
2600 -> 2601 (bazaar), notify triggers: jobs_ready and new_order (was project/python/sql/2301_notify.sql)
<base> -> 2600 (bazaar), baseline: the schema built by project/db up to module 25
```

`(bazaar)` is a **branch label**: revision 2600 declares `branch_labels = ("bazaar",)`, and every descendant inherits it. Labels let you address a line of history by name (`alembic upgrade bazaar@head`), which matters when one Alembic environment manages independent lines, for example the core schema and an optional reporting schema that some deployments don't have.

### 5.3 Branches and merges

Two developers start from head 2701. One adds a wishlist table (2702), the other gift wrapping (2703); both revisions say `down_revision = "2701"`. After both branches are merged in Git, the graph has two heads. This was run in a scratch copy of the migrations when 2701 was still the head (exercise 9 repeats it on 2801), against a scratch database:

```text
$ alembic heads
2702 (bazaar) (head)
2703 (bazaar) (head)
$ alembic upgrade head
FAILED: Multiple head revisions are present for given argument 'head'; please specify a specific target revision, '<branchname>@head' to narrow to a specific head, or 'heads' for all heads
```

`alembic upgrade heads` (plural) would apply both. The database then records **two** rows:

```text
 version_num
-------------
 2703
 2702
(2 rows)
```

The fix is a **merge revision**, a revision with two parents and (usually) no operations:

```text
$ alembic merge heads -m "merge wishlist and gift wrap" --rev-id 2704
$ alembic history -r 2701:heads
2702, 2703 -> 2704 (bazaar) (head) (mergepoint), merge wishlist and gift wrap
2701 -> 2702 (bazaar), add wishlist
2701 -> 2703 (bazaar), add gift wrap
2604 -> 2701 (bazaar) (branchpoint), transactional outbox: outbox + processed_messages (module 27)
```

```python
revision: str = '2704'
down_revision: str | Sequence[str] | None = ('2702', '2703')
```

Upgrading through the merge point collapses `alembic_version` back to one row (`2704`). The order in which Alembic runs 2702 and 2703 is not defined: a merge is only correct if the two branches are independent. If 2703 needs 2702's table, it should say so with `depends_on = "2702"` (or simply be rebased onto it).

In practice, keep the history linear: CI fails when there's more than one head (`test_one_head_and_a_straight_history`), and the second developer to merge rebases their revision onto the new head (changes `down_revision`, renumbers if needed). Merge revisions are for long-lived parallel lines, such as a hotfix branch that production already ran.

### 5.4 `stamp` beyond adoption

`alembic stamp <rev>` rewrites `alembic_version` without running anything. Besides adoption, it's the repair tool for "the database is right but the record is wrong": a hotfix applied by hand in an incident and later written up as revision 2605 is stamped rather than run. `alembic stamp --purge <rev>` replaces whatever is recorded, even unknown ids. Every stamp is a statement of fact you are responsible for; check the schema first.

### 5.5 Offline mode: SQL for review

Some teams require that a DBA reads the exact SQL before it touches production, or that production is changed only through a SQL-runner tool. `--sql` runs the migrations in **offline mode**: no database connection, the statements are printed instead. Bazaar's `env.py` handles it with a URL that only selects the dialect:

```python
def run_migrations_offline() -> None:
    context.configure(url="postgresql+psycopg://", literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, **_common_options())
    with context.begin_transaction():
        context.execute(f"SET lock_timeout = '{_lock_timeout()}'")
        context.execute(f"SET ROLE {OWNER_ROLE}")
        context.run_migrations()
```

An excerpt of `alembic upgrade 2601:head --sql`:

```sql
SET lock_timeout = '2s';

SET ROLE bazaar_owner;

BEGIN;

-- Running upgrade 2601 -> 2602

CREATE OR REPLACE FUNCTION slugify(p_text text)
...
ALTER TABLE products ADD COLUMN IF NOT EXISTS slug text;
...
COMMIT;

DO $backfill$
DECLARE
    lo bigint := (SELECT min(id) FROM products WHERE slug IS NULL);
    hi bigint := (SELECT max(id) FROM products WHERE slug IS NULL);
BEGIN
    SET session_replication_role = replica;
    WHILE lo <= hi LOOP
        UPDATE products SET slug = slugify(title) || '-' || id
        WHERE id >= lo AND id < lo + 10000 AND (slug IS NULL);
        COMMIT;
        lo := lo + 10000;
        PERFORM pg_sleep(0.0);
    END LOOP;
END
$backfill$;

BEGIN;

COMMIT;

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS products_slug_key ON products (slug);

BEGIN;

UPDATE bazaar.alembic_version SET version_num='2602' WHERE bazaar.alembic_version.version_num = '2601';

COMMIT;
```

Offline mode can't run Python logic that depends on the database (how many rows are left? is there an invalid index?). Bazaar's helpers produce an offline equivalent where one exists: the batched backfill becomes a `DO` block that `COMMIT`s per batch (allowed in a top-level `DO` since PostgreSQL 11), and the concurrent index build uses `IF NOT EXISTS`. The `COMMIT;` before `CREATE INDEX CONCURRENTLY` and the `BEGIN;` after it are the autocommit block (section 8.6) as plain SQL: run the file with `psql` in its default autocommit mode, and the structure is preserved.

### 5.6 Downgrades

Every Bazaar revision except the baseline has a working `downgrade()`, and the stairway test (section 10) proves that each one restores the previous schema exactly. Downgrades are valuable in development and CI: you can go back and forth while writing a revision. In production they're rarely the answer. A downgrade that drops `products.slug` destroys data written since the upgrade, and the old code that the downgrade is meant to serve usually runs fine on the *new* schema anyway, because the schema was changed in a backward-compatible way (section 9). The production habit is **roll forward**: fix the problem with a new revision. Write downgrades honestly. If a downgrade can't restore what it removes (dropping a column with data, merging enum values), make it raise with an explanation rather than pretend.

---

## 6. Autogenerate: what it sees and what it misses

### 6.1 How it works

`alembic revision --autogenerate` connects to the database, **reflects** the schema (Module 24's Inspector), compares it with `target_metadata`, and writes a revision whose `upgrade()` would make the *database* match the *metadata*. `alembic check` does the same comparison and only reports: it fails if there's anything to do. On the reference database at head:

```text
$ alembic check
No new upgrade operations detected.
```

That's the same statement as "the models describe the migrated schema", and the test suite asserts it through `bazaar.migrate.compare_models()`, which calls Alembic's `compare_metadata()` with `env.py`'s options.

### 6.2 A demonstration on real drift

To see what autogenerate catches, [`examples/autogenerate_drift.sql`](examples/autogenerate_drift.sql) changes a scratch database at head behind Alembic's back, in twelve ways:

```sql
ALTER TABLE reviews RENAME COLUMN body TO content;
ALTER TYPE order_status ADD VALUE 'on_hold';
ALTER TABLE products ALTER COLUMN status SET DEFAULT 'active';
ALTER TABLE reviews DROP CONSTRAINT reviews_rating_range;
ALTER TABLE reviews ADD CONSTRAINT reviews_rating_range CHECK (rating BETWEEN 1 AND 10);
ALTER TABLE inventory DROP CONSTRAINT inventory_quantity_nonnegative;
ALTER TABLE jobs ALTER COLUMN kind TYPE varchar(40);
DROP INDEX products_tags_idx;
CREATE TABLE wishlist (user_id bigint, product_id bigint, PRIMARY KEY (user_id, product_id));
DROP POLICY products_seller ON products;
DROP TRIGGER orders_notify_new ON orders;
DROP VIEW active_products;
```

```bash
alembic -x dsn=.../bazaar_scratch revision --autogenerate -m "autogenerate demo" --rev-id 9999
```

```text
INFO  [alembic.autogenerate.compare.tables] Detected removed table 'wishlist'
INFO  [alembic.ext.checkconstraint_byname] Detected added check constraint 'inventory_quantity_nonnegative' on table 'inventory'
INFO  [alembic.autogenerate.compare.types] Detected type change from VARCHAR(length=40) to Text() on 'jobs.kind'
INFO  [alembic.autogenerate.compare.constraints] Detected added index 'products_tags_idx' on '('tags',)'
INFO  [alembic.autogenerate.compare.tables] Detected added column 'reviews.body'
INFO  [alembic.autogenerate.compare.tables] Detected removed column 'reviews.content'
```

The generated `upgrade()` (complete file in [`examples/autogenerate_demo_output.py`](examples/autogenerate_demo_output.py)):

```python
def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table("wishlist")
    op.create_check_constraint(op.f("inventory_quantity_nonnegative"), "inventory", "quantity >= 0")
    op.alter_column(
        "jobs",
        "kind",
        existing_type=sa.VARCHAR(length=40),
        type_=sa.Text(),
        existing_nullable=False,
    )
    op.alter_column(
        "products",
        "status",
        existing_type=postgresql.ENUM("draft", "active", "archived", name="product_status"),
        server_default="draft",
        existing_nullable=False,
    )
    op.create_index("products_tags_idx", "products", ["tags"], unique=False, postgresql_using="gin")
    op.add_column("reviews", sa.Column("body", sa.Text(), nullable=True))
    op.drop_column("reviews", "content")
    # ### end Alembic commands ###
```

Read it as a reviewer would:

1. **The rename became `add_column` + `drop_column`.** Run as written, it would delete every review's text. Autogenerate sees two unrelated columns; only a person knows it's a rename.
2. **The enum value `on_hold` is invisible.** `existing_type` even lists the enum with three values. Enum labels aren't compared.
3. **The rating CHECK now allows 10, and nothing says so.** The by-name plugin noticed the *dropped* inventory CHECK (and wants it back), but a constraint with the same name and a different expression passes.
4. **The dropped policy, trigger and view went unnoticed.** RLS for sellers is gone, new orders no longer notify anyone, and autogenerate reports nothing, because it doesn't model them.
5. **`create_index` is not concurrent.** On a 281 MB `products` table, it would block writes for the whole build (section 8.6).
6. **`drop_table("wishlist")`**: a table nobody described in the metadata is something to *delete*, according to autogenerate. That's correct if it's really stray, and catastrophic if a colleague's feature created it.

### 6.3 What autogenerate detects, and what it doesn't

| Detected | Not detected (write these by hand) |
|---|---|
| tables added/removed | table and column **renames** (seen as drop + add) |
| columns added/removed | enum **value** changes; any change to a composite type or domain constraint |
| nullability | **CHECK expression** changes (names only, with the opt-in plugin) |
| type changes (`compare_type`) | functions, procedures, **triggers** |
| server defaults (`compare_server_default`; can be noisy, because Postgres normalizes the text: `'draft'` comes back as `'draft'::product_status`) | **views**, materialized views |
| indexes (name, columns, uniqueness; partial `WHERE` and `USING` only partly) | **RLS policies**, grants, default privileges, ownership |
| unique constraints, foreign keys (with `ON DELETE`) | sequences' options, identity options, generated column **expressions** |
| table and column comments (Bazaar switched this off) | partitions and partition bounds, extensions, extended statistics |
| | *how*: concurrently, in batches, with `NOT VALID`, in which deploy phase |

Autogenerate also warned on every run about `products.search_vector`:

```text
UserWarning: Computed default on products.search_vector cannot be modified
```

PostgreSQL returns the generated column's expression normalized (`'A'::"char"`, `::regconfig` casts), so it never matches the metadata's text. Changing a generation expression needs hand-written DDL anyway (a new column, or PostgreSQL 17's `ALTER COLUMN ... SET EXPRESSION`, which rewrites the table), so `bazaar.migrate` filters exactly that warning.

### 6.4 Filters: `include_name` and `include_object`

Without filters, autogenerate on Bazaar would propose dropping 25 tables: the monthly partitions of `events` (`events_2025_01`, ..., `events_default`), which `create_events_partition()` creates at run time (Module 14) and the metadata deliberately doesn't list. Two hooks control what's compared:

```python
def include_name(name, type_, parent_names) -> bool:
    """Pre-filter by name, before reflection: only our schema, no partitions."""
    if type_ == "schema":
        return name in (None, SCHEMA)
    if type_ == "table":
        return not _is_partition_name(name)
    return True


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    if type_ == "table" and (name == "alembic_version" or name in IGNORED_TABLES):
        return False
    return True
```

`include_name` runs before reflection (cheap: skipped tables are never inspected); `include_object` sees the reflected object and its metadata counterpart. `reflected=True, compare_to=None` means "in the database, not in the metadata", the case that turns into a `DROP`. Keep the filters narrow and documented: every object they hide is an object whose drift nobody will notice.

### 6.5 The workflow

Autogenerate is a **drafting tool**. The workflow that works:

1. Change the metadata (`bazaar/sa/tables.py`, and the ORM model if it maps the table).
2. Run `alembic revision --autogenerate --rev-id 2605 -m "..."` against a database at head (a scratch one is fine).
3. Read the draft as untrusted code. Remove operations that are drift rather than intent, turn drop + add pairs into renames, make indexes concurrent, split anything that locks a big table, add data migrations, add what autogenerate can't see.
4. Run it on a copy, then run the tests: `alembic check` must be clean, and the migration tests (section 10) must pass.

For Bazaar's revisions, the drafts were mostly thrown away: 2601 is triggers, 2602 is a backfill and a concurrent index, 2603 is a constraint dance. Only 2701 (two new tables) is close to what autogenerate would write. The value is in step 4: `compare_models()` guarantees that the metadata and the migrations never drift apart.

---

## 7. Writing revisions by hand

### 7.1 Functions and triggers: `op.execute`

Anything SQL can say, `op.execute()` can run. Revision 2602 creates a slug function and a trigger:

```python
SLUGIFY = """
CREATE OR REPLACE FUNCTION slugify(p_text text)
RETURNS text
LANGUAGE sql
IMMUTABLE STRICT PARALLEL SAFE
RETURN trim(BOTH '-' FROM
            regexp_replace(lower(public.unaccent('public.unaccent'::regdictionary, p_text)),
                           '[^a-z0-9]+', '-', 'g'))
"""

SET_SLUG = """
CREATE OR REPLACE FUNCTION products_set_slug()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- The identity id is assigned before BEFORE triggers run, so it can make
    -- the slug unique without a lookup.
    IF NEW.slug IS NULL THEN
        NEW.slug := slugify(NEW.title) || '-' || NEW.id;
    END IF;
    RETURN NEW;
END;
$$
"""
```

`unaccent()` is only `STABLE` (its dictionary could change), so a function that wraps it can't honestly be `IMMUTABLE`. Passing the dictionary explicitly (`'public.unaccent'::regdictionary`) is the established way to make the wrapper safe to declare `IMMUTABLE`, so that it could be used in an index expression (Module 13). The body uses the SQL-standard `RETURN` form (PostgreSQL 14+), which records the function's dependencies on `unaccent` and `regexp_replace` like a view does.

Three rules for SQL in revisions:

- **Copy the SQL into the revision.** Don't read it from a file that other code also uses. The revision must produce the same result in a year.
- **Keep function bodies flush left.** They're stored verbatim (section 4.6).
- **Mind the bind syntax.** `op.execute("...")` goes through SQLAlchemy's `text()`: `:word` is a bind parameter (write `\:` or avoid it), and with psycopg `%` may need doubling. For whole scripts, use `bazaar.migrate.execute_script()`.

### 7.2 Data migrations

A **data migration** changes rows, not structure: filling the new `slug` column for the 200,000 existing products. Revision 2602 does it with `bazaar.migrate.backfill()`:

```python
backfill(
    "products",
    "slug = slugify(title) || '-' || id",
    "slug IS NULL",
    batch_size=10_000,
    skip_triggers=True,
)
```

which runs, outside any transaction (in an autocommit block):

```python
lo, hi = conn.execute(sa.text(f"SELECT min({key}), max({key}) FROM {table} WHERE {where}")).one()
update = sa.text(f"UPDATE {table} SET {set_clause}"
                 f" WHERE {key} >= :lo AND {key} < :hi AND ({where})")
with _replica_role(skip_triggers):
    while lo <= hi:
        n = conn.execute(update, {"lo": lo, "hi": lo + batch_size}).rowcount
        ...
        lo += batch_size
        if pause:
            time.sleep(pause)
```

Each rule here comes from something that goes wrong otherwise:

- **Batches, each its own transaction.** One `UPDATE products SET slug = ...` over 200,000 rows would hold row locks on every product until it finishes. A seller editing a product would wait, and so would a checkout that touches its inventory through a trigger. It also generates the whole update's WAL at once (a burst for replicas to absorb), and leaves 200,000 dead tuples that VACUUM can't remove until the transaction ends. 10,000-row batches lock each row for a fraction of a second.
- **Key ranges, not `LIMIT`/`OFFSET`.** `WHERE id >= lo AND id < lo + 10000` uses the primary key and costs the same for the last batch as for the first. `OFFSET` would rescan everything before it.
- **An idempotent `WHERE`.** `slug IS NULL` means an interrupted backfill can simply be run again; rows already done are skipped. The same condition makes the offline `DO` block safe to rerun.
- **Throttling.** `pause=` sleeps between batches, so replication lag (Module 19) and I/O stay bounded on a busy primary. Watch `pg_stat_replication.replay_lag` during a large backfill and pause more if it grows.
- **Don't import the ORM models.** A revision written today runs in a year, when `Product` has new columns, renamed attributes or validation logic. Importing it would make the old revision depend on the new model, and fail or, worse, do something different. Use SQL, or a `sa.table()` stub declared inside the revision with only the columns it touches. Bazaar's revisions import only `bazaar.migrate`, whose helpers are schema-agnostic and must stay backward compatible.
- **Think about triggers.** Every `UPDATE` of `products` fires `set_updated_at()` (bumping `updated_at` to now for all 200,000 products, which would put them all at the top of "recently updated") and `audit_trigger()` (200,000 rows of before/after JSON, including the `search_vector`, in `audit_log`). A backfill is not a business change. `skip_triggers=True` runs the batches with `session_replication_role = replica`, under which ordinary triggers don't fire. That setting is superuser-only, but since PostgreSQL 15 it can be granted, and 2602 does so: `GRANT SET ON PARAMETER session_replication_role TO bazaar_owner`. After the backfill, the reference database had no product with a recent `updated_at`, and `audit_log` hadn't grown. `replica` mode also disables foreign-key triggers, so use it only for updates of columns that no foreign key involves.

The run on the large dataset:

```text
INFO  [bazaar.migrate] backfill products: 200000 rows in 23.5s (batches of 10000)
```

That's about 120 µs per row, because each `UPDATE` rewrites the whole row. The stored generated column `search_vector` is recomputed, and since most pages have no free space, the new row versions go to other pages: after two backfills, `pg_stat_user_tables` showed 400,207 updates of which only 7,114 were HOT (Module 16), so every index of `products`, including three GIN indexes, got new entries. Autovacuum cleans up behind the batches. That's another advantage of small transactions: dead tuples from finished batches can be removed while later batches still run.

> **In production:** For tables in the tens of millions of rows, run the backfill as a separate, resumable job (a script or a job in Module 10's queue) rather than inside `alembic upgrade`, and make the following revision (here 2603) check that it finished. Deployment pipelines have timeouts, and a migration that runs for an hour is a migration that gets killed half-way.

### 7.3 Enums: adding values

Bazaar's statuses are enums (Module 08). Adding a status is `ALTER TYPE ... ADD VALUE`, which has two rules that collide with migration tools.

Since PostgreSQL 12, `ADD VALUE` may run inside a transaction block, but the new value can't be **used** until the transaction has committed:

```sql
BEGIN;
ALTER TYPE order_status ADD VALUE 'on_hold' AFTER 'paid';
SELECT 'on_hold'::order_status;
```

```text
ERROR:  unsafe use of new value "on_hold" of enum type order_status
LINE 1: SELECT 'on_hold'::order_status;
               ^
HINT:  New enum values must be committed before they can be used.
```

Even `SELECT enum_range(NULL::order_status)` in the same transaction fails with the same error. So a revision that adds the value *and* uses it (a `CHECK`, a default, an `UPDATE orders SET status = 'on_hold'`) must commit in between: put the `ADD VALUE` in its own revision before the one that uses it (with `transaction_per_migration`, each revision commits), or inside an autocommit block:

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'on_hold' AFTER 'paid'")
    op.execute("UPDATE orders SET status = 'on_hold' WHERE ...")   # a new transaction: fine
```

`IF NOT EXISTS` makes it rerunnable (`NOTICE: enum label "on_hold" already exists, skipping`). The second rule is that values can't be removed:

```text
ERROR:  dropping an enum value is not implemented
```

So a downgrade of `ADD VALUE` can only raise, or recreate the type (a new enum, `ALTER COLUMN ... TYPE new_enum USING status::text::new_enum`, which rewrites every table that uses it and takes `ACCESS EXCLUSIVE` on each). `ALTER TYPE ... RENAME VALUE` exists and is cheap. If a status set changes often, a `text` column with a `CHECK` constraint, or a lookup table with a foreign key, is easier to migrate than an enum.

Remember the application side too (Module 24's pitfall): reading a row with a value the Python `Enum` doesn't know fails. **Deploy code that understands `on_hold` first, then add the value**, then start writing it.

---

## 8. Zero-downtime schema changes

Every DDL statement takes a lock on the table it changes, and PostgreSQL's DDL is transactional, so the lock is held until the transaction ends. Most production outages caused by migrations aren't caused by slow DDL but by **lock waits**: a statement that needs a strong lock waits, and everything else waits behind it.

### 8.1 The lock queue, and `lock_timeout`

The table-level lock modes from Module 10, and the DDL that takes them:

| Lock | Taken by | Blocks |
|---|---|---|
| `ACCESS SHARE` | `SELECT` | only `ACCESS EXCLUSIVE` |
| `ROW EXCLUSIVE` | `INSERT`, `UPDATE`, `DELETE` | `SHARE` and stronger |
| `SHARE UPDATE EXCLUSIVE` | `VACUUM`, `CREATE INDEX CONCURRENTLY`, `VALIDATE CONSTRAINT`, `ALTER TABLE ... SET STATISTICS` | itself and stronger: **not** reads or writes |
| `SHARE` | `CREATE INDEX` (not concurrently) | writes |
| `SHARE ROW EXCLUSIVE` | `ADD FOREIGN KEY` (on both tables), `CREATE TRIGGER` | writes |
| `ACCESS EXCLUSIVE` | most `ALTER TABLE` forms, `DROP`, `TRUNCATE`, `VACUUM FULL` | everything, including `SELECT` |

`ADD COLUMN` without a volatile default takes `ACCESS EXCLUSIVE` for a millisecond. The danger is getting it. [`examples/lock_queue_demo.py`](examples/lock_queue_demo.py) has three sessions: A, a long transaction that read `products` (a report, or a connection left idle in a transaction); B, the migration's `ALTER TABLE products ADD COLUMN`; C, a product page query. A holds its transaction for 3 seconds:

```text
--- migration with lock_timeout = 0
  A-report     idle in transaction  Client ClientRead blocked_by=[] SELECT count(*) FROM products WHERE id < 100
  B-migration  active               Lock  relation  blocked_by=['A-report'] ALTER TABLE products ADD COLUMN demo_note tex
  C-traffic    active               Lock  relation  blocked_by=['B-migration'] SELECT title FROM products WHERE id = 42
  B (migration): done after 3.68s
  C (traffic):   product page query took 3.38s
--- migration with lock_timeout = 500ms
  A-report     idle in transaction  Client ClientRead blocked_by=[] SELECT count(*) FROM products WHERE id < 100
  B (migration): gave up after 0.50s: canceling statement due to lock timeout
  C (traffic):   product page query took 0.20s
```

C's `ACCESS SHARE` is compatible with A's, but **not** with B's queued `ACCESS EXCLUSIVE`, and PostgreSQL grants locks in queue order so that B isn't starved. So C waits behind B, which waits behind A: `pg_blocking_pids()` shows the chain. Every query on `products` stalls until the report finishes, for a migration that needed 1 ms. With `lock_timeout = 500ms`, B gives up, and C ran after waiting 0.2 s (the time until B gave up).

That's why `env.py` sets `lock_timeout` (2 s by default) on the migration session, and **retries** the revision that timed out:

```python
for attempt in range(1, attempts + 1):
    try:
        ...
            _run(connection)
        return
    except OperationalError as exc:
        if not _is_lock_timeout(exc) or attempt == attempts:
            raise
        delay = min(30.0, 0.5 * 2**attempt) * random.uniform(0.5, 1.0)
        log.warning("lock_timeout (%s) on attempt %d/%d: %s; retrying in %.1fs", ...)
        time.sleep(delay)
```

With `transaction_per_migration`, the revision that hit the timeout was rolled back as a whole, and the ones before it are committed and recorded, so running again resumes at the failed revision. Here a transaction held a lock on `products` for 4 seconds while revision 2603 needed `ACCESS EXCLUSIVE` on it, with `-x lock_timeout=1s`:

```text
INFO  [alembic.runtime.migration] Running upgrade 2602 -> 2603, products.slug NOT NULL, contract phase, without a long ACCESS EXCLUSIVE scan
WARNI [alembic.env] lock_timeout (1s) on attempt 1/5: canceling statement due to lock timeout; retrying in 0.6s
INFO  [alembic.runtime.migration] Running upgrade 2602 -> 2603, products.slug NOT NULL, contract phase, without a long ACCESS EXCLUSIVE scan
WARNI [alembic.env] lock_timeout (1s) on attempt 2/5: canceling statement due to lock timeout; retrying in 1.9s
INFO  [alembic.runtime.migration] Running upgrade 2602 -> 2603, products.slug NOT NULL, contract phase, without a long ACCESS EXCLUSIVE scan
INFO  [alembic.runtime.migration] Running upgrade 2603 -> 2604, orders.idempotency_key: a client-chosen key that makes checkout safe to retry
```

Each failed attempt cost traffic at most one second of waiting. The other half of the defence is on the application side: `idle_in_transaction_session_timeout` (Module 27) stops a forgotten transaction from being "session A" for hours.

`lock_timeout` limits the wait for *each* lock. It is not `statement_timeout`, which limits the whole statement, including legitimate work such as an index build. Migrations want a short `lock_timeout` and no (or a generous) `statement_timeout`.

### 8.2 Measuring instead of guessing

[`examples/lock_probe.py`](examples/lock_probe.py) runs DDL steps on one connection while a traffic thread does `SELECT` + `UPDATE` by primary key on the same table in a tight loop, timing each round trip, and a sampler thread records the DDL session's locks from `pg_locks` every 2 ms:

```bash
python ../../modules/26-migrations/examples/lock_probe.py \
    --ddl "ALTER TABLE orders ADD COLUMN demo_flag boolean NOT NULL DEFAULT false" \
    --undo "ALTER TABLE orders DROP COLUMN demo_flag"
```

The interesting number is the **worst traffic latency** during each step: that's what a user of the shop would have felt. All measurements below are on `orders` (500,000 rows, 55 MB) or its neighbours, with the table in cache. The listings leave out the `transactionid ExclusiveLock` that every writing transaction holds on its own id.

### 8.3 Adding a column, with and without a default

```text
=== D1 const default
DDL  ALTER TABLE orders ADD COLUMN demo_flag boolean NOT NULL DEFAULT false
     ok, took 2 ms
     traffic during it: 7 round trips, worst 2 ms
=== D2 volatile default
DDL  ALTER TABLE orders ADD COLUMN demo_token uuid NOT NULL DEFAULT gen_random_uuid()
     ok, took 1967 ms
     traffic during it: 15 round trips, worst 1896 ms
     locks of the DDL session (in the order first seen):
       transactionid                    ExclusiveLock              granted
       orders                           AccessExclusiveLock        granted
       (new relation 21227)             AccessExclusiveLock        granted
       (new relation 21226)             ShareLock                  granted
       ...
       orders_pkey                      AccessExclusiveLock        granted
       orders_shipping_address_id_idx   AccessExclusiveLock        granted
       ...
```

Since PostgreSQL 11, `ADD COLUMN ... DEFAULT <constant>` doesn't touch the rows. The default is stored once in the catalog (`pg_attribute.attmissingval`), and rows written before the column existed return it when read. That's 2 ms, even with `NOT NULL`. (The sampler didn't even catch the `ACCESS EXCLUSIVE` lock, it was that short.) A **volatile** default (`gen_random_uuid()`, `clock_timestamp()`, `random()`) must be evaluated per row, so PostgreSQL **rewrites the table**: a new copy of the heap (the "new relations"), every index rebuilt, all under `ACCESS EXCLUSIVE`. Traffic stopped for 1.9 seconds on a 55 MB table; on a 50 GB table, that would be many minutes. `now()` is *not* volatile (it's `STABLE`: one value per transaction), so `DEFAULT now()` is fast.

To add a column that needs per-row values: add it nullable without a default (fast), set the default for *new* rows (`ALTER COLUMN ... SET DEFAULT gen_random_uuid()`, fast), backfill existing rows in batches (section 7.2), then add `NOT NULL` (section 8.4).

### 8.4 `NOT NULL` without a long lock

`orders.shipping_address_id` is nullable but has no NULLs in the large dataset, a good candidate:

```text
=== D3 naive not null
DDL  ALTER TABLE orders ALTER COLUMN shipping_address_id SET NOT NULL
     ok, took 111 ms
     traffic during it: 7 round trips, worst 111 ms
     locks of the DDL session (in the order first seen):
       orders                           AccessExclusiveLock        granted
```

`SET NOT NULL` must prove there are no NULLs, so it scans the whole table, under `ACCESS EXCLUSIVE`. 111 ms for 500,000 cached rows; a cold 50 GB table means minutes of complete outage. The online version, in four steps, each its own transaction:

```text
=== D4 online not null
DDL  ALTER TABLE orders ADD CONSTRAINT orders_shipping_address_id_not_null CHECK (shipping_address_id IS NOT NULL) NOT VALID
     ok, took 11 ms
     traffic during it: 7 round trips, worst 6 ms
     locks of the DDL session (in the order first seen):
       orders                           AccessExclusiveLock        granted
DDL  ALTER TABLE orders VALIDATE CONSTRAINT orders_shipping_address_id_not_null
     ok, took 71 ms
     traffic during it: 17 round trips, worst 4 ms
     locks of the DDL session (in the order first seen):
       orders                           ShareUpdateExclusiveLock   granted
DDL  ALTER TABLE orders ALTER COLUMN shipping_address_id SET NOT NULL
     ok, took 1 ms
     traffic during it: 7 round trips, worst 3 ms
DDL  ALTER TABLE orders DROP CONSTRAINT orders_shipping_address_id_not_null
     ok, took 2 ms
     traffic during it: 8 round trips, worst 3 ms
```

1. `ADD CONSTRAINT ... CHECK (... IS NOT NULL) NOT VALID` takes `ACCESS EXCLUSIVE` briefly and scans nothing. From now on, new and updated rows are checked.
2. `VALIDATE CONSTRAINT` scans the table under `SHARE UPDATE EXCLUSIVE`, which doesn't conflict with reads or writes: traffic kept flowing (17 round trips, worst 4 ms).
3. `SET NOT NULL`: since **PostgreSQL 12**, if a valid `CHECK (col IS NOT NULL)` exists, the scan is skipped. 1 ms.
4. The `CHECK` is now redundant; drop it.

Steps 1 and 2 **must be separate transactions**: in one transaction, step 1's `ACCESS EXCLUSIVE` would be held during step 2's scan, and you'd have the naive version with extra steps. `bazaar.migrate.set_not_null_online()` runs them in an autocommit block; revision 2603 is one call to it.

### 8.5 Foreign keys: `NOT VALID`, then `VALIDATE`

Adding a foreign key checks every existing row, and it locks **both** tables:

```text
=== D5 naive fk
DDL  ALTER TABLE order_items ADD CONSTRAINT demo_fk FOREIGN KEY (order_id) REFERENCES orders (id)
     ok, took 483 ms
     traffic during it: 6 round trips, worst 3 ms
     locks of the DDL session (in the order first seen):
       order_items                      AccessShareLock            granted
       order_items                      ShareRowExclusiveLock      granted
       orders                           AccessShareLock            granted
       orders                           RowShareLock               granted
       orders                           ShareRowExclusiveLock      granted
=== D6 online fk
DDL  ALTER TABLE order_items ADD CONSTRAINT demo_fk FOREIGN KEY (order_id) REFERENCES orders (id) NOT VALID
     ok, took 4 ms
     traffic during it: 7 round trips, worst 4 ms
DDL  ALTER TABLE order_items VALIDATE CONSTRAINT demo_fk
     ok, took 396 ms
     traffic during it: 65 round trips, worst 5 ms
     locks of the DDL session (in the order first seen):
       order_items                      AccessShareLock            granted
       orders                           AccessShareLock            granted
       orders                           RowShareLock               granted
       order_items                      ShareUpdateExclusiveLock   granted
```

`SHARE ROW EXCLUSIVE` blocks writes, so during the naive version's half-second scan of 1.27 million `order_items`, nobody could write to `order_items` **or to `orders`**: no new orders, no status changes. (This run's numbers hide it: the probe managed only 6 round trips in 483 ms, against 65 during the online version, and the one that waited was cut off by the measurement window. Two more runs reported the stall directly: worst 381 ms and 448 ms.) `NOT VALID` adds the constraint without checking existing rows (enforced for new ones), and `VALIDATE CONSTRAINT` checks them under `SHARE UPDATE EXCLUSIVE` on the referencing table and `ROW SHARE` on the referenced one: 65 round trips went through during validation, the worst taking 5 ms.

Don't forget the **index** on the referencing column (Module 11): without it, every delete or key update in `orders` scans `order_items`. Create it concurrently, before the constraint.

### 8.6 Indexes: `CONCURRENTLY`, inside Alembic

```text
=== D7 index
DDL  CREATE INDEX demo_idx ON orders (total)
     ok, took 285 ms
     traffic during it: 8 round trips, worst 282 ms
     locks of the DDL session (in the order first seen):
       orders                           ShareLock                  granted
=== D8 index conc
DDL  CREATE INDEX CONCURRENTLY demo_idx ON orders (total)
     ok, took 425 ms
     traffic during it: 67 round trips, worst 5 ms
     locks of the DDL session (in the order first seen):
       orders                           ShareUpdateExclusiveLock   granted
```

Module 11 explained the mechanics: `CONCURRENTLY` builds the index in several passes, waiting for concurrent transactions in between. It takes longer (425 vs 285 ms) but writes continue. It **can't run inside a transaction block**:

```text
ERROR:  CREATE INDEX CONCURRENTLY cannot run inside a transaction block
```

Alembic runs each revision in a transaction, so it offers an escape hatch: `op.get_context().autocommit_block()` commits the revision's transaction so far, switches the connection to autocommit, runs the block, and starts a new transaction after it. `bazaar.migrate.create_index_concurrently()`:

```python
def create_index_concurrently(name, table, columns, *, unique=False, where=None, using=None):
    stmt = (
        f"CREATE {'UNIQUE ' if unique else ''}INDEX CONCURRENTLY IF NOT EXISTS {name}"
        f" ON {table}{f' USING {using}' if using else ''} ({columns})"
        f"{f' WHERE {where}' if where else ''}"
    )
    with _autocommit():
        if not _offline():
            valid = (_bind().execute(
                sa.text("SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass(:n)"),
                {"n": name}).scalar())
            if valid is False:
                log.warning("dropping INVALID index %s left by an earlier attempt", name)
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
        op.execute(stmt)
```

The invalid-index check matters. If a concurrent build fails (a duplicate key in a `UNIQUE` index, a `lock_timeout`, a cancelled deploy), it leaves an index marked `INVALID`: not used by queries, but **still maintained on every write**, and `IF NOT EXISTS` would happily skip over it on the next attempt. The helper drops the leftover first.

A `UNIQUE` index can back a constraint afterwards, if you need one (for a foreign key to point at, or `ON CONFLICT ON CONSTRAINT`): `ALTER TABLE products ADD CONSTRAINT products_slug_key UNIQUE USING INDEX products_slug_key` takes a brief `ACCESS EXCLUSIVE` lock and no scan. Bazaar keeps `products_slug_key` as a plain unique index, which is enough for `ON CONFLICT (slug)`.

> **Pitfall:** An autocommit block makes the revision **non-atomic**. Everything before it is committed. If the revision fails after it, `alembic_version` still says the previous revision, and the next `alembic upgrade` runs the whole revision again. That's why 2602 starts with `CREATE OR REPLACE FUNCTION`, `ADD COLUMN IF NOT EXISTS` and `CREATE OR REPLACE TRIGGER`, the backfill skips rows already done, and `add_check_constraint_online()` checks `pg_constraint` before adding (it found this the hard way: the retry of a timed-out 2603 failed with "constraint already exists" until it did). With `transaction_per_migration=True`, at least only this revision is affected.

### 8.7 Changing a column's type

Whether `ALTER COLUMN ... TYPE` rewrites the table depends on whether the old bytes are valid for the new type:

```text
DDL  ALTER TABLE payments ALTER COLUMN amount TYPE numeric(14,2)
     ok, took 88 ms
     traffic during it: 9 round trips, worst 82 ms
     locks of the DDL session (in the order first seen):
       payments                         AccessExclusiveLock        granted
DDL  ALTER TABLE reviews ALTER COLUMN rating TYPE integer
     ok, took 648 ms
     traffic during it: 13 round trips, worst 592 ms
     locks of the DDL session (in the order first seen):
       reviews                          AccessExclusiveLock        granted
       (new relation 21266)             AccessExclusiveLock        granted
       ...
```

- **No rewrite**: `varchar(n)` to a larger `n` or to `text`, `numeric(p,s)` to a larger `p` with the same `s`, `timestamp` to `timestamptz` when the session time zone is UTC. The relation's file stays the same (`pg_relation_filenode('payments')` was 21258 before and after). Still, the widening took 88 ms under `ACCESS EXCLUSIVE`: `payments` has `CHECK (amount <> 0)`, and PostgreSQL re-checks constraints that involve the column, which means a scan.
- **Rewrite**: `smallint` to `integer` (648 ms on 292,355 reviews), `integer` to `bigint`, `text` to `uuid`, anything with a `USING` expression, and narrowing a type (`numeric(14,2)` back to `(12,2)` produced a new filenode).

Dependent views add a twist:

```text
ERROR:  cannot alter type of a column used by a view or rule
DETAIL:  rule _RETURN on materialized view seller_sales_daily depends on column "quantity"
```

That was `ALTER TABLE order_items ALTER COLUMN quantity TYPE bigint`: the materialized view must be dropped and recreated around the change, and dropping it breaks every query on it in the meantime.

The online way to change a type is the same as a rename (next section): a new column of the new type, dual writes, a batched backfill, switching the readers, dropping the old column. For the classic case, an `integer` primary key that is about to run out (2.1 billion), start long before it's urgent.

### 8.8 Renaming a column: expand and contract

`ALTER TABLE reviews RENAME COLUMN body TO content` is instant. It's also an outage: every running instance of the application still says `body`, and fails until it's redeployed. With several application servers updated one after another, both versions run side by side during every deployment. There's no moment at which a rename is safe.

The pattern that handles this is **expand / migrate / contract**: first make the schema accept both the old and the new code, then move the code, then remove what only the old code needed.

```text
 phase         schema                                   application
 ─────────────────────────────────────────────────────────────────────────────────────
 1 expand      ADD COLUMN content; trigger keeps          v1 (reads/writes body)
               body and content in sync; backfill         keeps running unchanged
 2 migrate     (no change)                                deploy v2: reads and writes content
 3 contract    drop the trigger, DROP COLUMN body         v2 only
               (after every v1 instance is gone)
```

The sync trigger for phase 1 has to handle writers of either version:

```sql
ALTER TABLE reviews ADD COLUMN content text;

CREATE FUNCTION reviews_sync_body_content() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.content := coalesce(NEW.content, NEW.body);
        NEW.body := coalesce(NEW.body, NEW.content);
    ELSIF NEW.content IS DISTINCT FROM OLD.content THEN
        NEW.body := NEW.content;           -- new code wrote content
    ELSIF NEW.body IS DISTINCT FROM OLD.body THEN
        NEW.content := NEW.body;           -- old code wrote body
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER reviews_sync_body_content BEFORE INSERT OR UPDATE ON reviews
    FOR EACH ROW EXECUTE FUNCTION reviews_sync_body_content();
```

Tried on a scratch database: an update by "old code" (`SET body = 'edited by old code'`) showed up in both columns, and so did a later update by "new code" (`SET content = 'edited by new code'`). The backfill (`UPDATE reviews SET content = body WHERE content IS NULL`, in batches) fills the rows written before the trigger existed.

Alternatives:

- **Dual writes in the application** instead of a trigger: v1.5 writes both columns and reads the old one; v2 reads the new one. It needs one more deployment, but keeps logic out of the database.
- **A view** for a table rename: rename the table, and create a view with the old name (`CREATE VIEW old_name AS SELECT * FROM new_name`). Simple views are automatically updatable (Module 09), so old code keeps working against the view until it's gone. A compatibility view that isn't automatically updatable (it joins tables, or computes a renamed column) can route writes with `INSTEAD OF` triggers (Module 09).

Exercise 1 does the full sequence for splitting `users.full_name`.

### 8.9 Dropping a column: application first

`ALTER TABLE ... DROP COLUMN` is fast: it only marks the column as dropped in the catalog (the data stays in the rows until they're rewritten). But it takes `ACCESS EXCLUSIVE`, and any application code that still names the column fails, including code that doesn't name it explicitly: an ORM whose model still maps the column puts it in every `SELECT` and `INSERT`. The order is:

1. Deploy code that no longer uses the column. In the ORM, remove it from the model, or mark it `deferred` and stop writing it. For a `NOT NULL` column, make it nullable (or give it a default) first, so that inserts from code that no longer sends it succeed.
2. Wait until no old instance runs (and no long-lived job still has the old code loaded).
3. Then a revision drops the column, with `lock_timeout`.

The rule generalises: **additive changes go to the database first, removals go to the application first.** Module 19 met the same rule for logical replication.

### 8.10 The patterns at a glance

| Change | Naive form and its cost | Online form |
|---|---|---|
| any DDL on a busy table | waits for locks, and everything queues behind it | `lock_timeout` + retry (`env.py`) |
| add column | volatile default rewrites the table under `ACCESS EXCLUSIVE` | nullable / constant default; backfill in batches |
| add `NOT NULL` | full scan under `ACCESS EXCLUSIVE` | `CHECK ... NOT VALID`, `VALIDATE`, `SET NOT NULL`, drop `CHECK` (PG 12+) |
| add `CHECK` | full scan under `ACCESS EXCLUSIVE` | `NOT VALID`, then `VALIDATE` (separate transactions) |
| add foreign key | scan under `SHARE ROW EXCLUSIVE` on both tables | index first (concurrently), `NOT VALID`, `VALIDATE` |
| add index / unique | `CREATE INDEX` blocks writes | `CREATE [UNIQUE] INDEX CONCURRENTLY` in an autocommit block; drop invalid leftovers |
| unique constraint | build under lock | unique index concurrently, then `ADD CONSTRAINT ... UNIQUE USING INDEX` |
| change type | rewrite under `ACCESS EXCLUSIVE` (unless binary compatible) | new column, sync trigger or dual writes, backfill, switch, drop |
| rename column / table | instant, but breaks running code | expand / migrate / contract (trigger, dual write, or a view) |
| drop column / table | breaks code that still uses it | remove from the code first, then drop |
| backfill | one huge transaction: locks, WAL burst, bloat | key-range batches, idempotent `WHERE`, throttled, triggers considered |
| enum value | unusable in the same transaction | own revision or autocommit block; code knows the value first |

---

## 9. Bazaar's revisions

The milestone's three schema changes, as five revisions on top of the baseline:

| Revision | What | Lock profile |
|---|---|---|
| 2600 | baseline: `baseline/schema.sql` for new databases, `stamp` for existing ones | — |
| 2601 | NOTIFY triggers from `sql/2301_notify.sql` | `CREATE OR REPLACE TRIGGER` takes `SHARE ROW EXCLUSIVE` on `jobs` and `orders` for milliseconds |
| 2602 | `products.slug`, expand: `slugify()`, nullable column, `products_set_slug()` trigger for new rows, backfill of 200,000 rows in batches without firing audit/updated_at triggers, `CREATE UNIQUE INDEX CONCURRENTLY products_slug_key` | brief `ACCESS EXCLUSIVE` for `ADD COLUMN`; row locks per 10,000-row batch; `SHARE UPDATE EXCLUSIVE` for the index |
| 2603 | `products.slug` `NOT NULL`, contract: `set_not_null_online()` | two brief `ACCESS EXCLUSIVE`, the scan under `SHARE UPDATE EXCLUSIVE` |
| 2604 | `orders.idempotency_key text`, `CHECK (length BETWEEN 8 AND 200)` added `NOT VALID` and validated, unique partial index `(user_id, idempotency_key, seller_id) WHERE idempotency_key IS NOT NULL`, built concurrently | brief `ACCESS EXCLUSIVE` for `ADD COLUMN` and `ADD CONSTRAINT` |

Why is the slug trigger permanent rather than just a phase-1 helper? Every deployed version of the application, and every script that inserts products (Module 22's `import_products()` with `COPY`), produces complete rows without knowing about slugs. The ORM maps the column with `server_default=FetchedValue()` (the server provides it), so `INSERT ... RETURNING` brings the generated slug back. A product whose title is all punctuation gets a slug of `-` plus its id, still unique and URL-safe; `tests/test_migrations.py::test_slug_backfill` checks that and the accent handling (`Crème de Lys Café Mug` becomes `creme-de-lys-cafe-mug-<id>`).

Why is the idempotency key unique per `(user_id, idempotency_key, seller_id)`? One checkout creates one order *per seller* (Module 10), all tagged with the same key. A key belongs to a user (two users may pick the same random key), and a repeated checkout collides on its first seller's order. Module 27 builds the idempotent checkout on top of this index.

`bazaar.sa.tables` and the ORM models describe the new columns and indexes, so `compare_models()` is clean at head.

---

## 10. Testing migrations

Migrations are code that runs once per environment, usually at the worst possible moment. They deserve tests. [`tests/test_migrations.py`](../../project/python/tests/test_migrations.py) has these:

| Test | What it proves |
|---|---|
| `test_one_head_and_a_straight_history` | one head; the graph is the expected straight line (fails in CI when two branches each added a revision) |
| `test_reference_database_is_at_head` | the test database ran every revision (`check_schema`) |
| `test_models_match_migrated_schema` | `compare_models()` is empty: the metadata describes the migrated schema |
| `test_model_drift_is_detected` | ... and that check fails when it should (a column added, an index and a CHECK dropped, inside a rolled-back transaction) |
| `test_fresh_database_equals_reference` | a database built from nothing equals the reference database, catalog by catalog (>600 objects) |
| `test_fresh_database_models_match` | the models match a fresh database too |
| `test_check_schema_rejects_an_old_database` | `SchemaOutOfDate` on a database at 2603 |
| `test_stairway` | for each revision: upgrade, downgrade, upgrade again; the schema after each downgrade equals the schema before the upgrade |
| `test_baseline_refuses_to_downgrade` | the baseline raises |
| `test_slug_backfill` | the data migration: slugs for existing rows (accents, punctuation), `updated_at` and `audit_log` untouched, `NOT NULL`, a valid unique index, the trigger for new rows, explicit slugs kept |
| `test_offline_sql_for_review` | `--sql` output has the concurrent index after a `COMMIT`, the `NOT VALID`/`VALIDATE` pair, the `DO` backfill, one version update per revision |
| `test_lock_timeout_retry` | with a transaction holding a lock for 1.5 s and `lock_timeout=200ms`, the upgrade retries and succeeds |
| `test_concurrent_upgrades_are_serialized` | two `alembic upgrade head` processes at once: one does everything, the other nothing |

The **stairway** test is the one that finds the most bugs: a `downgrade()` that forgets an index, a `CREATE` without `OR REPLACE` that fails on re-upgrade, a downgrade that drops a function the previous revision still needs. It runs on an empty database, so it's fast (under 2 s for all revisions):

```python
migrate.upgrade(empty_db, "2600")
before = _snapshot(empty_db)
for rev in REVISIONS[1:]:
    migrate.upgrade(empty_db, rev)
    after = _snapshot(empty_db)
    migrate.downgrade(empty_db, "-1")
    assert diff(before, _snapshot(empty_db), labels=("before", "downgraded")) == [], rev
    migrate.upgrade(empty_db, rev)
    assert diff(after, _snapshot(empty_db), labels=("upgraded", "re-upgraded")) == [], rev
    before = after
```

The **data migration** test starts at 2601 in an empty database, inserts rows the way old code would, upgrades to head and checks the result. Data migrations are where real bugs hide (accents, empty titles, duplicates), and an empty-database test doesn't exercise them at all.

The fixtures `empty_db` and `fresh_db` (in `tests/conftest.py`) create scratch databases through [`bazaar/testing.py`](../../project/python/src/bazaar/testing.py). `fresh_db` is a copy of a **template database** that `alembic upgrade head` built once per test session (and rebuilds when a revision file changes): `CREATE DATABASE ... TEMPLATE` is faster than running the migrations again. Module 27 explains templates and parallel test runs.

The concurrent-upgrade test found a real bug. The first version of the migration lock in `env.py` called `pg_advisory_lock()`, which **waits inside a statement**. The second run waited with a snapshot open, the first run's `CREATE INDEX CONCURRENTLY` (revision 2602) waited for every older snapshot to go away, and PostgreSQL's deadlock detector killed one of them. [`examples/advisory_lock_deadlock.py`](examples/advisory_lock_deadlock.py) reproduces it with two connections and a scratch table:

```text
run 1: index built
run 2: DeadlockDetected: deadlock detected
DETAIL:  Process 4043 waits for ExclusiveLock on advisory lock [16384,0,1257573796,1]; blocked by process 4042.
Process 4042 waits for ShareLock on virtual transaction 97/499; blocked by process 4043.
HINT:  See server log for query details.
```

The fix (`_acquire_migration_lock()`) polls `pg_try_advisory_lock()` and sleeps in Python, with no transaction open. It's an example of why migrations are tested under the same conditions as production: concurrent, with real locks.

```text
$ pytest tests/test_migrations.py
.............                                                            [100%]
13 passed in 8.89s
```

---

## 11. Migrations in CI/CD and deployment

### 11.1 Who runs migrations, and when

Run migrations as a **separate step** of the deployment, before the new application version starts: a CI/CD job, a Kubernetes `Job` or init container that runs once per release, a release-phase command. Don't run `alembic upgrade head` from every application process at startup:

- Ten replicas starting together would run the migrations ten times concurrently. Bazaar's `env.py` serializes runs with an advisory lock (the second run waits, then finds nothing to do), but most setups don't.
- The application role would need DDL rights, which it shouldn't have (section 3.2).
- A slow migration delays every process's startup, and health checks kill processes that start slowly.

The application only *checks* the schema at startup (`check_schema()`). The migration job uses `BAZAAR_MIGRATION_DSN`, with a role that may `SET ROLE bazaar_owner`, from the pipeline's secret store.

### 11.2 Deployment ordering

With rolling deployments, old and new code run side by side against one schema. Every migration must therefore be compatible with the code version before it **and** the one after it:

```text
 release N     migration: expand (add nullable column, new table, index, trigger)
               code N:    still works without using the new things (it ignores them)
 release N+1   code N+1:  uses the new column; tolerates rows written by code N
 release N+2   migration: contract (NOT NULL, drop old column)   -- only after N is gone
```

Bazaar's slug went through exactly this: 2602 (expand) is safe under the module-25 code, which never mentions `slug`; the trigger fills it. 2603 (contract) adds `NOT NULL`, safe because the trigger fills every new row. In a real project, 2602 and 2603 would ship in different releases; `check_schema()` would then accept "at least revision X" instead of "exactly head", so that code N+1 can start while the contract revision is still pending.

A CI pipeline for the migration part:

```bash
alembic heads | wc -l                       # exactly 1
alembic upgrade head --sql > migration.sql  # attach to the review / change ticket
pytest tests/test_migrations.py             # stairway, models match, fresh == reference
alembic -x dsn=$STAGING upgrade head        # staging (a production-sized copy if you can)
```

Run the migrations against a **production-sized** copy at least for anything that touches a big table. `lock_probe.py` on staging tells you what users will feel. A backfill that takes 23 seconds on 200,000 products takes hours on 200 million.

### 11.3 When a migration fails in production

With `transaction_per_migration`, a failed revision is rolled back (except for the parts in autocommit blocks, which is why those are rerunnable). `alembic_version` shows the last revision that succeeded. Fix the cause (a lock that couldn't be obtained, a duplicate that broke a unique index build, a timeout) and rerun. If the revision itself is wrong, write a new one that corrects it, rather than editing the one that may already have run elsewhere: **a revision that has run anywhere outside your laptop is immutable.**

### 11.4 Squashing old revisions

After a few years there are hundreds of revisions, and building a test database replays all of them. Squashing replaces old history with a new baseline:

1. Pick a revision that every environment has passed (say 2900).
2. Make a new baseline from a database at 2900 (the same `make_baseline.sh` procedure), as revision `3000_baseline` with `down_revision = None`.
3. Point the next revision (2901) at `3000_baseline` instead of 2900, and delete revisions 2600–2900 (Git still has them).
4. Every existing database is at 2900 or later; stamp them with `3000_baseline` (or, if they're past it, they're already at a revision that exists in the new graph).
5. Run the convergence test (section 4.5) between a squashed fresh database and a long-lived one before trusting it.

A database still below 2900 can't be upgraded any more. Squash only what nobody needs.

---

## Summary

- Schema changes are code: versioned, reviewed, applied by a tool that knows (from `alembic_version`) which changes each database has. `reset.sh` rebuilt from nothing; migrations change a living database in place.
- Change-based tools record and apply ordered changes; plain-SQL tools (Flyway, Sqitch, dbmate) keep the SQL front and centre, while Alembic adds Python, a revision graph, autogenerate from SQLAlchemy metadata and offline SQL output.
- Bazaar's `env.py`: DSN from the environment (`BAZAAR_MIGRATION_DSN`), `NullPool`, no `statement_timeout` but a `lock_timeout` with retries, `SET ROLE bazaar_owner` so default privileges apply, `version_table_schema="bazaar"`, comparison of types, defaults and CHECKs by name, filters for partitions, one transaction per revision, an advisory lock against concurrent runs (polled, not waited for).
- **Adoption**: a baseline revision. Existing databases are **stamped**; new ones execute a cleaned `pg_dump --schema-only` snapshot (roles, database settings and matview refresh added by hand). A catalog-level comparison proved both paths produce the same 679 objects.
- Revisions form a graph: `upgrade`/`downgrade`/`current`/`history`/`heads`, branches, merge revisions, labels, `stamp`. Keep it linear in practice; CI checks for one head. `--sql` prints reviewable SQL.
- Autogenerate drafts; people write migrations. It missed a rename (proposed dropping the data), an enum value, a changed CHECK, a dropped policy, trigger and view, and generated a blocking index build.
- Revisions use SQL freely (`op.execute`), never import ORM models, and keep function bodies verbatim. Data migrations run in idempotent key-range batches, throttled, with triggers considered (`session_replication_role`, grantable since PG 15).
- Enum values can't be used in the transaction that added them, and can't be dropped.
- Zero downtime is about locks: `lock_timeout` and retries; constant defaults (not volatile ones); `NOT VALID` + `VALIDATE` for CHECKs and FKs; `SET NOT NULL` via a validated CHECK (PG 12+); `CREATE INDEX CONCURRENTLY` in an autocommit block, dropping invalid leftovers; type changes and renames via expand/contract; drops after the code stops using the column. Autocommit blocks make revisions non-atomic, so they must be rerunnable.
- Test migrations: one head, models match, fresh equals reference, stairway, data migrations, offline output, lock retries, concurrent runs.
- Deploy migrations as their own step, before the code, compatible with both code versions; roll forward; never edit a revision that ran; squash with a new baseline when history gets long.

## What's next

The schema is now under control. Module 27 turns to the code around it in production: testing against real PostgreSQL at scale (template databases, parallel runs, containers, concurrency tests), retries and timeouts, the transactional outbox (revision 2701, already in the graph above), PgBouncer gotchas, and observability.
