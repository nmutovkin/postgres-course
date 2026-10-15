# Module 26: Project milestone — Alembic takes over Bazaar's schema

<!-- nav:start -->
> [Theory](README.md) · [Exercises](exercises.md) · **Project** · [Solutions](solutions/README.md)  
> [← 25 SQLAlchemy ORM](../25-sqlalchemy-orm/README.md) · [Course home](../../README.md) · [27 Production Python and Postgres →](../27-python-production/README.md)
<!-- nav:end -->

Bazaar's schema has been built by numbered SQL files and `reset.sh`, which drops the database every time. From now on, the schema of a living database changes only through reviewed, versioned migrations that keep the shop online. In this milestone you adopt Alembic on the existing database without recreating anything, make it able to build the same schema from nothing, move Module 23's hand-applied triggers into a migration, ship two features (product slugs, checkout idempotency keys) as zero-downtime revisions, and test all of it.

**Starting state:** `./project/bin/reset.sh 26` (the large dataset, with no Alembic and without the NOTIFY triggers: `reset.sh` rebuilds from `project/db/`, and revision 2601 adds them), and your Module 25 package (or the reference one).

**Reference solution:** [`project/python/`](../../project/python/): `alembic.ini`, `migrations/` (`env.py`, `script.py.mako`, `baseline/`, `versions/2600`–`2604`), `src/bazaar/migrate.py`, `src/bazaar/catalog.py`, `src/bazaar/testing.py` (scratch databases), the new columns in `src/bazaar/sa/tables.py` and `src/bazaar/orm/models.py`, `tests/conftest.py` (the at-head check, `empty_db`, `fresh_db`) and `tests/test_migrations.py`. (Revision `2701` and the rest of `testing.py` belong to Module 27; revision `2801` and the `migrated` marker, which replaced the at-head check, to Module 28.)

## Requirements

### 1. Alembic configuration

- `alembic.ini` in `project/python`, **without** a database URL. File names `<rev>_<slug>.py`; revision ids chosen with `--rev-id` (`2600`, `2601`, ...); generated revisions formatted by ruff (post-write hooks).
- `migrations/env.py`:
  - connects with `BAZAAR_MIGRATION_DSN` (default `BAZAAR_DSN`) through `bazaar.config` and `bazaar.sa.engine.create_engine(..., poolclass=NullPool)`, overridable with `-x dsn=...`, and accepts a connection passed in `config.attributes["connection"]`;
  - `application_name = 'bazaar-migrations'`, no `statement_timeout`, `lock_timeout` 2 s (`-x lock_timeout=`), and retries of a revision that failed with SQLSTATE `55P03` (`-x lock_retries=`, exponential backoff with jitter);
  - creates schema `bazaar` (owned by `bazaar_owner` when the role exists) and does `SET ROLE bazaar_owner`;
  - one migration run per database at a time: a session advisory lock, **polled** with `pg_try_advisory_lock()` outside any transaction (waiting inside `pg_advisory_lock()` deadlocks with `CREATE INDEX CONCURRENTLY`);
  - context options shared with the tests in `bazaar.migrate.context_options()`: `target_metadata = bazaar.sa.tables.metadata`, `version_table_schema="bazaar"`, `include_schemas=False`, `compare_type`, `compare_server_default`, autogenerate plugins (defaults minus comments, plus `alembic.ext.checkconstraint_byname`), `include_name`/`include_object` filters (no `events_*` partitions, no `alembic_version`), `transaction_per_migration=True`;
  - offline mode (`--sql`) that prints `SET lock_timeout` and `SET ROLE` first.
- `bazaar.migrate`: `alembic_config()`, `upgrade()`, `downgrade()`, `stamp()`, `upgrade_sql()`, `head_revisions()`, `current_revisions()`, `check_schema()` (raises `SchemaOutOfDate`), `compare_models()`; and helpers for revisions that work online and offline: `execute_script()`, `create_index_concurrently()` (drops an `INVALID` leftover first), `drop_index_concurrently()`, `add_check_constraint_online()` (`NOT VALID`, commit, `VALIDATE`; rerunnable), `set_not_null_online()`, `backfill()` (key-range batches in autocommit mode, optional pause, optional `session_replication_role = replica`; a `DO` block offline).

### 2. Adoption: baseline 2600

- `migrations/baseline/schema.sql`: a schema-only dump of the reference state (`reset.sh 26`, before 2301), made by `migrations/baseline/make_baseline.sh` (removes `\restrict`/`\unrestrict` and the session `SET`s, makes `CREATE SCHEMA` idempotent).
- Revision `2600` (branch label `bazaar`): as superuser, creates the five roles if missing (`NOLOGIN`, no passwords), executes `schema.sql`, sets the database's `search_path` and `GRANT CONNECT` for the current database, refreshes `seller_sales_daily`, gives `alembic_version` to `bazaar_owner`, and returns to `SET ROLE bazaar_owner`. Its `downgrade()` raises.
- Existing databases are stamped: `alembic stamp 2600`.

### 3. Revisions

- **2601**: `notify_jobs_ready()` + `jobs_notify_ready`, `notify_new_order()` + `orders_notify_new`, copied from `sql/2301_notify.sql` (function bodies flush left, `CREATE OR REPLACE`), with a downgrade that drops them. `tests/conftest.py` stops applying `sql/*.sql`.
- **2602** (`products.slug`, expand): `slugify(text)` (`IMMUTABLE`, `unaccent` with an explicit dictionary), `products.slug text` (nullable), the `BEFORE INSERT` trigger `products_set_slug` (`slugify(title) || '-' || id` when NULL), the grant `SET ON PARAMETER session_replication_role TO bazaar_owner`, a batched backfill of existing products without firing triggers, and `CREATE UNIQUE INDEX CONCURRENTLY products_slug_key`. Rerunnable after a failure half-way.
- **2603** (`products.slug`, contract): `NOT NULL` through a validated `CHECK`.
- **2604**: `orders.idempotency_key text`, `CHECK (length(idempotency_key) BETWEEN 8 AND 200)` added `NOT VALID` and validated, and the unique partial index `orders_idempotency_key_idx (user_id, idempotency_key, seller_id) WHERE idempotency_key IS NOT NULL`, built concurrently.
- `bazaar.sa.tables`: `products.slug` (`NOT NULL`, `server_default=FetchedValue()`), `products_slug_key`, `orders.idempotency_key`, its `CHECK` and index. The ORM: `Product.slug` (`server_default=FetchedValue()`), `Order.idempotency_key`. `bazaar.sa.drift` ignores `alembic_version` and treats `FetchedValue` as "no `DEFAULT` clause".
- `alembic check` reports no new operations at head.

### 4. Tests (`tests/test_migrations.py`)

Fixtures: a session-level check that stops the test run with a clear message unless the database is at head (Module 28 turns it into the `migrated` marker, which skips only the tests that need head); `empty_db` is an empty scratch database; `fresh_db` a copy of a template built by `alembic upgrade head` (named per pytest-xdist worker).

- one head, straight history;
- the reference database is at head; `compare_models()` is empty, and detects drift you create in a rolled-back transaction;
- a fresh database equals the reference database (`bazaar.catalog` snapshots, no differences), and its models match;
- `check_schema()` rejects a database at 2603;
- stairway: every revision downgrades to exactly the previous schema and upgrades to exactly the same schema again; the baseline refuses to downgrade;
- the slug data migration on rows inserted at 2601 (accents, punctuation-only titles), `updated_at` and `audit_log` untouched, `NOT NULL`, a valid unique index, the trigger for new rows, explicit slugs kept, duplicates rejected;
- offline SQL contains the concurrent index after a `COMMIT`, the `NOT VALID`/`VALIDATE` pair and the `DO` backfill;
- the `lock_timeout` retry (a transaction holding a lock for 1.5 s, `lock_timeout=200ms`);
- two concurrent `alembic upgrade head` processes: one does all the work, the other none.

Update `project/python/README.md`: module table, setup (`alembic stamp 2600 && alembic upgrade head`), layout.

## Acceptance checks

```bash
./project/bin/reset.sh 26
cd project/python && source .venv/bin/activate
export BAZAAR_DSN=postgresql://postgres:postgres@localhost:5432/bazaar

alembic current                 # (nothing yet)
alembic stamp 2600
alembic upgrade head            # 2601 ... 2801 (2701 was the head at the end of Module 27; Module 28 adds 2801); the backfill takes about 25 s
alembic current                 # 2801 (head)
alembic check                   # No new upgrade operations detected.
alembic upgrade 2601:2604 --sql | grep -c CONCURRENTLY      # 2

ruff check src tests migrations && ruff format --check src tests migrations
pytest tests/test_migrations.py -v      # 13 passed
pytest                                  # the whole suite still passes
```

And in psql, right after the upgrade (before tests or exercises insert or change products):

```sql
SELECT id, title, slug FROM products WHERE id IN (1, 2, 150000);
```

```text
   id   |               title                |                  slug
--------+------------------------------------+-----------------------------------------
      1 | Fernweh Slim-Fit Tank Top - Black  | fernweh-slim-fit-tank-top-black-1
      2 | The Burning Protocol               | the-burning-protocol-2
 150000 | Kitchara Classic Raised Bed - Navy | kitchara-classic-raised-bed-navy-150000
```

```sql
SELECT count(*) FILTER (WHERE updated_at > now() - interval '1 hour') AS bumped, count(*) FROM products;
```

```text
 bumped | count
--------+--------
      0 | 200000
```

(The backfill touched all 200,000 rows without bumping a single `updated_at`.)
