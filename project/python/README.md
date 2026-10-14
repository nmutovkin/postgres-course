# bazaar: the Python side of Bazaar

The `bazaar` package is the Python service that Part IV of the course builds on top of the Bazaar database. Each module adds a layer. The code here is the **reference solution**: every module's `project.md` says which parts it covers.

| Module | Adds | Where |
|---|---|---|
| 22 psycopg 3 | settings, connections and pool, models, domain errors, the sync repository (plain SQL), tests against the real database | `config.py`, `db.py`, `models.py`, `errors.py`, `repo.py`, `tests/test_repo.py` |
| 23 async | async psycopg and asyncpg connections/pools, the async repository, NOTIFY triggers, the job worker | `aio/db.py`, `aio/repo.py`, `worker.py`, `sql/2301_notify.sql`, `tests/test_aio.py`, `tests/test_worker.py` |
| 24 SQLAlchemy Core | engines from the same settings, the schema as Core `Table` metadata, a drift test against the live database, Core reports (seller statement, category revenue roll-up) | `sa/engine.py`, `sa/tables.py`, `sa/drift.py`, `sa/reports.py`, `tests/test_sa_core.py` |
| 25 SQLAlchemy ORM | mapped classes for all entities, sessions with the RLS hook, services (checkout, order history without N+1, seller product CRUD with optimistic locking, reviews), sync and async | `orm/models.py`, `orm/session.py`, `orm/services.py`, `tests/test_orm.py` |
| 26 Alembic | Alembic adoption: a baseline (`stamp 2600` for existing databases, `baseline/schema.sql` for new ones), revisions 2601 (NOTIFY triggers, formerly `sql/2301_notify.sql`), 2602–2603 (`products.slug`, expand/contract with a batched backfill and a concurrent unique index), 2604 (`orders.idempotency_key`); online-DDL helpers, programmatic Alembic, catalog snapshots; migration tests | `alembic.ini`, `migrations/`, `migrate.py`, `catalog.py`, `testing.py` (scratch databases), `tests/test_migrations.py` |
| 27 production | error classification and retries (sync/async, whole transactions), timeouts on every connection, the transactional outbox (revision 2701) with relay and idempotent consumer, idempotent checkout, structured logging, slow-query log, sqlcommenter, pool metrics, health check, OpenTelemetry, read-only engine, fork safety; template databases, xdist, testcontainers, concurrency tests | `resilience.py`, `outbox.py`, `observability.py`, `migrations/versions/2701_outbox.py`, `orm/services.py` (`checkout(..., idempotency_key=)`, `checkout_async`), `tests/test_{resilience,outbox,observability,concurrency,containers}.py` |
| 28 capstone | the FastAPI service: three engines (rw, read-only, seller under RLS), one transaction per request, idempotent checkout with retries, problem+json errors, keyset pagination, health/readiness, startup schema check; revision 2801 (`search_products()` from module 13, two indexes); the outbox relay as a process; API tests; tests that need the migrated schema are marked `migrated` and skipped on earlier stages | `api/`, `relay.py`, `migrations/versions/2801_search_products.py`, `sa/engine.py` (`create_async_read_engine`), `outbox.py` (`ajobs_publisher`), `tests/test_api.py`, `tests/conftest.py` |

<!-- toc:start -->
**Contents**

- [Setup](#setup)
- [Layout](#layout)
- [Running](#running)
  - [The API (module 28)](#the-api-module-28)

<!-- toc:end -->

## Setup

The database runs in the course container. The package runs on your machine and connects to `localhost:5432`.

```bash
./project/bin/reset.sh 28                    # the reference database (project/db up to module 25)

cd project/python
uv sync                       # creates .venv with runtime and dev dependencies
# or: python3 -m venv .venv && .venv/bin/pip install -e '.[otel]' pytest pytest-asyncio \
#        pytest-xdist 'testcontainers[postgres]' ruff pydantic httpx aiohttp
source .venv/bin/activate
export BAZAAR_DSN=postgresql://postgres:postgres@localhost:5432/bazaar

alembic stamp 2600            # once per reset: "this database already has the baseline schema"
alembic upgrade head          # 2601 ... 2801 (about 30 s on the large dataset: the slug backfill)
alembic current               # 2801 (head)
```

From module 26 on, **Alembic owns the schema**: every change is a revision in `migrations/versions/`. Tests that need the migrated schema carry the `migrated` marker and are **skipped, with the reason**, when the database is not at head; on `reset.sh 22` (+ `sql/2301_notify.sql`, modules 22–25) the other tests run as before. `sql/2301_notify.sql` is kept only for modules 23–25 (it became revision 2601; applying it again is harmless). An empty database is built with `alembic upgrade head` alone (revision 2600 runs `migrations/baseline/schema.sql`); it has the schema but no data.

Requires Python 3.12+. Settings come from the environment (`src/bazaar/config.py`):

| Variable | Default |
|---|---|
| `BAZAAR_DSN` | `postgresql://postgres:postgres@localhost:5432/bazaar` (a URI, so that asyncpg can use it too) |
| `BAZAAR_APP_NAME` | `bazaar` (`application_name` in `pg_stat_activity`) |
| `BAZAAR_POOL_MIN_SIZE` / `BAZAAR_POOL_MAX_SIZE` | 2 / 10 |
| `BAZAAR_POOL_TIMEOUT` | 10 s to wait for a pooled connection |
| `BAZAAR_STATEMENT_TIMEOUT` | 5000 ms (0 disables) |
| `BAZAAR_LOCK_TIMEOUT` | 0 (disabled) |
| `BAZAAR_IDLE_TX_TIMEOUT` | 30000 ms (`idle_in_transaction_session_timeout`) |
| `BAZAAR_REPLICA_DSN` | unset: `create_read_engine()` uses the primary, read-only |
| `BAZAAR_MIGRATION_DSN` | `BAZAAR_DSN`: the role Alembic connects as (it does `SET ROLE bazaar_owner`) |
| `BAZAAR_SELLER_DSN` | `BAZAAR_DSN` with user `bazaar_seller` / password `seller`: the API's seller-portal engine (RLS applies to it) |
| `BAZAAR_SELLER_POOL_SIZE` | 3 |
| `BAZAAR_REQUEST_TIMEOUT` | 10000 ms: the API's per-request deadline (0 disables) |
| `BAZAAR_ADMIN_TOKEN` | unset: `/admin/*` disabled; otherwise the `X-Admin-Token` value |
| `BAZAAR_CHECK_SCHEMA` | 1: the API refuses to start unless the schema is at head |
| `BAZAAR_LOG_FORMAT` | unset; `json` makes the API log one JSON object per line |

The default connects as the superuser `postgres`, because the tests and migrations create databases, triggers and tables. A deployed service would connect as `bazaar_app` (Module 15): `postgresql://bazaar_app:app@localhost:5432/bazaar`, with migrations run separately under `BAZAAR_MIGRATION_DSN`.

## Layout

```text
src/bazaar/
├── config.py      Settings from the environment
├── db.py          sync connections, pool, configure() (enum adapters), seller_scope() for RLS
├── models.py      dataclasses shared by all layers; enums; keyset cursor encoding
├── errors.py      domain exceptions and translate() from SQLSTATEs (BZ001...); both drivers
├── repo.py        sync repository: get_product, search_products, list_orders_for_user,
│                  place_order, import_products (COPY), enqueue_job
├── aio/
│   ├── db.py      psycopg AsyncConnection/AsyncConnectionPool; asyncpg connect/pool with jsonb codecs
│   └── repo.py    async twin of repo.py (same SQL constants)
├── worker.py      async job worker on the module-10 queue: python -m bazaar.worker
├── relay.py       the outbox relay as a process: python -m bazaar.relay (module 28)
├── migrate.py     Alembic from Python (upgrade, check_schema), autogenerate options,
│                  online-DDL helpers for revisions (module 26)
├── catalog.py     canonical schema snapshot from the catalogs, and diff (module 26)
├── testing.py     scratch databases, TemplateDatabase (modules 26-27)
├── resilience.py  classify(), RetryPolicy, retry/aretry, run_transaction, run_in_session (27)
├── outbox.py      record/insert_stmt, relay_batch, run_relay, jobs_publisher, consume_once (27)
├── observability.py JSON logging, instrument_engine, sqlcommenter, pool_metrics,
│                  check_health, setup_tracing (27)
├── sa/            SQLAlchemy Core (module 24)
│   ├── engine.py  create_engine() / create_async_engine() from Settings; sa_url(); count_statements()
│   ├── tables.py  MetaData (naming convention) with every table; view_metadata for the two views
│   ├── drift.py   compare(conn, metadata, ...): metadata vs. live schema (also mapping_only)
│   └── reports.py seller_statement(), category_revenue_rollup() and reusable CTEs/filters
├── api/           the FastAPI service (module 28)
│   ├── app.py     create_app(), the lifespan (engines, schema check, shutdown); `app` for uvicorn
│   ├── db.py      Database (rw, read, seller engines + session factories); dependencies
│   ├── errors.py  domain/database errors -> application/problem+json
│   ├── middleware.py request id, deadline, access log; TaggedRoute (sqlcommenter route tags)
│   ├── schemas.py pydantic request/response models
│   └── routes/    health, catalog (products, search), shop (cart, checkout, orders), seller, admin
└── orm/           SQLAlchemy ORM (module 25)
    ├── models.py  Base + User, Seller, Category, Product, Inventory, Order, OrderItem, Payment,
    │              Review, Address, CartItem, Promotion, ActiveProduct (read-only view)
    ├── session.py BazaarSession (RLS after_begin hook, read-only guard), session factories
    └── services.py checkout, order_history(_async), seller product CRUD, submit_review
alembic.ini        Alembic configuration (no URL in it: env.py reads BAZAAR_MIGRATION_DSN)
migrations/
├── env.py         connection, SET ROLE bazaar_owner, lock_timeout + retries
├── baseline/      schema.sql (the reference schema at module 26) and make_baseline.sh
└── versions/      2600 baseline, 2601 notify, 2602-2603 products.slug, 2604 orders.idempotency_key,
                   2701 outbox, 2801 search_products() + browse/trigram indexes
sql/
└── 2301_notify.sql   module 23's NOTIFY triggers (now revision 2601; kept for modules 23-25)
tests/             pytest; every test runs in a rolled-back transaction unless it needs committed data
```

Conventions:

- Repository functions take a connection as their first argument and **never commit**: the caller owns the transaction. Functions that must be atomic use `conn.transaction()`, which nests as a savepoint.
- All values are bound parameters. Optional clauses and identifiers are composed with `psycopg.sql`. Ruff's `S608` rule is enabled.
- One pool per process, created at startup (inside the running event loop for async pools). In a process that uses SQLAlchemy, the engine's pool is that pool: don't stack it on `psycopg_pool`.
- Core reports and ORM services follow the same rule as the repository: a `Connection` or `Session` comes first, and they never commit. ORM service queries declare what they load and end with `raiseload("*")`.
- Alembic owns the schema (module 26): a change is a reviewed revision in `migrations/versions/`, written for zero downtime (no long `ACCESS EXCLUSIVE` locks, indexes `CONCURRENTLY`, expand/contract). `sa/tables.py` and `orm/models.py` describe the schema, and tests fail when the description, the migrations and the database disagree.
- Revisions never import the models; they may use the helpers in `bazaar.migrate`.
- Retries wrap whole transactions (`bazaar.resilience`), never single statements, and retry lost connections only for idempotent work.

## Running

```bash
pytest -v                                      # at head: everything; on reset.sh 22: modules 22-25
pytest -n 4                                    # in parallel (pytest-xdist): 146 passed, 1 skipped, ~16 s
pytest -m "not slow"
BAZAAR_TEST_DOCKER=1 pytest -m docker          # also build Bazaar in a throwaway container
ruff check src tests migrations && ruff format --check src tests migrations
python -m bazaar.worker --queue default --concurrency 4
python -m bazaar.relay                         # outbox -> jobs (module 28)
uvicorn bazaar.api.app:app --workers 4         # the API (module 28); /docs for the OpenAPI UI
alembic upgrade head --sql                     # the SQL of pending revisions, for review
```

### The API (module 28)

```bash
export BAZAAR_ADMIN_TOKEN=change-me           # enables /admin/*
uvicorn bazaar.api.app:app --workers 4 --port 8000 --timeout-graceful-shutdown 20
python -m bazaar.relay                        # order events -> jobs
python -m bazaar.worker --concurrency 4       # runs the jobs
curl -s localhost:8000/ready                  # 200 {"ok": true, "schema": ["2801"], ...}
```

Each worker process creates its three engines (rw, read, seller) in the lifespan, so pools are per process: 4 workers × (10 + 10 + 3) connections at the defaults. Lower `BAZAAR_POOL_MAX_SIZE` (or put PgBouncer in front) to stay under `max_connections`. Identity is a stand-in for authentication: `X-User-Id` (buyer), `X-Seller-Id` (seller portal), `X-Admin-Token`. The endpoints, design and load test are in `modules/28-capstone/README.md`; `/docs` serves the OpenAPI UI.

The tests leave the reference database as they found it: most run inside a transaction that is rolled back (ORM sessions join it with `join_transaction_mode="create_savepoint"`, so even code that commits stays inside it). Tests that need committed data either create their own rows and delete them, or (module 27) get a database of their own: `fresh_db` is a copy of a template database that `alembic upgrade head` built once (`CREATE DATABASE ... TEMPLATE`, about 140 ms), rebuilt automatically when a revision file changes; `empty_db` is an empty database for migration tests. Their names carry the xdist worker id. Sequences (ids) of the reference database do advance. The RLS test connects as `bazaar_seller` (password `seller`, module 15) to the host and port of `BAZAAR_DSN`.
