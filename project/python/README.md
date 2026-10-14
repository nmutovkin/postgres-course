# bazaar: the Python side of Bazaar

The `bazaar` package is the Python service that Part IV of the course builds on top of the Bazaar database. Each module adds a layer. The code here is the **reference solution**: every module's `project.md` says which parts it covers.

| Module | Adds | Where |
|---|---|---|
| 22 psycopg 3 | settings, connections and pool, models, domain errors, the sync repository (plain SQL), tests against the real database | `config.py`, `db.py`, `models.py`, `errors.py`, `repo.py`, `tests/test_repo.py` |
| 23 async | async psycopg and asyncpg connections/pools, the async repository, NOTIFY triggers, the job worker | `aio/db.py`, `aio/repo.py`, `worker.py`, `sql/2301_notify.sql`, `tests/test_aio.py`, `tests/test_worker.py` |
| 24–25 SQLAlchemy | Core tables and queries, ORM models and sessions | (later) |
| 26 Alembic | migrations; takes over `sql/` and the schema files of `project/db/` | (later) |
| 27 production | retries, outbox, timeouts, observability, test isolation at scale | (later) |
| 28 capstone | the FastAPI service | (later) |

## Setup

The database runs in the course container. The package runs on your machine and connects to `localhost:5432`.

```bash
./project/bin/reset.sh 22                                     # or 23, the same database state
./project/bin/psql.sh < project/python/sql/2301_notify.sql   # module 23's triggers (the tests apply them too)

cd project/python
uv sync                       # creates .venv with runtime and dev dependencies
# or: python3 -m venv .venv && .venv/bin/pip install -e . pytest pytest-asyncio ruff pydantic
source .venv/bin/activate
export BAZAAR_DSN=postgresql://postgres:postgres@localhost:5432/bazaar
```

Requires Python 3.12+. Settings come from the environment (`src/bazaar/config.py`):

| Variable | Default |
|---|---|
| `BAZAAR_DSN` | `postgresql://postgres:postgres@localhost:5432/bazaar` (a URI, so that asyncpg can use it too) |
| `BAZAAR_APP_NAME` | `bazaar` (`application_name` in `pg_stat_activity`) |
| `BAZAAR_POOL_MIN_SIZE` / `BAZAAR_POOL_MAX_SIZE` | 2 / 10 |
| `BAZAAR_STATEMENT_TIMEOUT` | 5000 ms (0 disables) |

The default connects as the superuser `postgres`, because the tests and examples create triggers and tables. A deployed service would connect as `bazaar_app` (Module 15): `postgresql://bazaar_app:app@localhost:5432/bazaar`.

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
└── worker.py      async job worker on the module-10 queue: python -m bazaar.worker
sql/
└── 2301_notify.sql   NOTIFY triggers: jobs_ready (per queue), new_order
tests/             pytest; every test runs in a rolled-back transaction unless it needs committed data
```

Conventions:

- Repository functions take a connection as their first argument and **never commit**: the caller owns the transaction. Functions that must be atomic use `conn.transaction()`, which nests as a savepoint.
- All values are bound parameters. Optional clauses and identifiers are composed with `psycopg.sql`. Ruff's `S608` rule is enabled.
- One pool per process, created at startup (inside the running event loop for async pools).

## Running

```bash
pytest -v                                      # needs the database at state 22/23
ruff check src tests && ruff format --check src tests
python -m bazaar.worker --queue default --concurrency 4
```

The tests leave the database as they found it: most run inside a transaction that is rolled back; the few that need committed data (concurrent checkouts, notifications, the worker) create their own rows and delete them. Sequences (ids) do advance.
