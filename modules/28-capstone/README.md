# Module 28: Capstone: the Bazaar Service

<!-- nav:start -->
> **Theory** · [Exercises](exercises.md) · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 27 Production Python and Postgres](../27-python-production/README.md) · [Course home](../../README.md)
<!-- nav:end -->

Twenty-seven modules ago Bazaar was seven empty tables. Today it has half a million orders, a category tree, full-text search, row-level security for its sellers, an outbox, a job queue, Alembic migrations and a Python package that wraps all of it. What it doesn't have yet is a front door. In this module you build one: an async HTTP service in FastAPI that a storefront, a mobile app and a seller portal can talk to. Every endpoint is a small exercise in something you already know. Browsing a category is a recursive CTE plus keyset pagination. Search is Module 13's ranking function. Checkout is Module 10's `place_order()` behind an idempotency key, with Module 27's retries and outbox. The seller portal is Module 15's RLS policies, applied per request with `SET LOCAL`. This module is mostly a guided build: the design decisions, the code that implements them, the tests that prove them, how to run the service, and a load test that checks the invariants still hold when 80 clients hit it at once.

**What you'll learn**

- How the pieces of the course fit into one service: a map from every endpoint and mechanism to the module that taught it.
- The design decisions of a database-backed API, and their trade-offs: async or sync; ORM, Core or raw SQL per endpoint; one transaction per request, injected as a dependency; three engines (primary, read-only, seller role); RLS with `SET LOCAL`; idempotent checkout; which requests to retry; layered timeouts; keyset pagination; replica routing; health, readiness, the startup schema check and graceful shutdown.
- A step-by-step implementation in `project/python/src/bazaar/api/`, including Alembic revision 2801, which brings Module 13's `search_products()` into the schema.
- Testing a web service against real PostgreSQL: httpx with the ASGI transport, fresh databases per test, concurrency through the API, RLS isolation, optimistic-lock conflicts, timeouts. Also a test suite that runs at every stage of Part IV.
- Operating it: uvicorn workers, pool sizing per worker, migrations in a deploy, and monitoring with `pg_stat_statements` and Module 17's toolkit.
- Load testing with invariants: throughput and latency percentiles, how the numbers change at saturation, and checks afterwards that nothing was oversold, every total is right and the outbox drained.

**Before you start**

```bash
./project/bin/reset.sh 28                  # the large dataset, about 3 minutes
cd project/python
uv sync                                    # adds fastapi, uvicorn, httpx (module 28)
source .venv/bin/activate
export BAZAAR_DSN=postgresql://postgres:postgres@localhost:5432/bazaar
alembic stamp 2600 && alembic upgrade head # 2601 ... 2701, and this module's 2801
pytest -n 4                                # 146 passed, 1 skipped (the opt-in docker test)
```

You need Modules 22–27; the service is built from their package, and this module does not re-teach them. Outputs come from PostgreSQL 17.11, Python 3.13, FastAPI 0.143.0, Starlette 1.8.0, uvicorn 0.54.0, httpx 0.28.1, SQLAlchemy 2.1.4, asyncpg 0.32.0 and psycopg 3.3.6, on a 4-vCPU machine that runs the database, the service and the load generator together.

---

<!-- toc:start -->
**Contents**

- [How to run the capstone](#how-to-run-the-capstone)
- [1. What we are building](#1-what-we-are-building)
  - [1.1 The endpoints](#11-the-endpoints)
  - [1.2 Architecture](#12-architecture)
  - [1.3 The code](#13-the-code)
- [2. Every module, one service](#2-every-module-one-service)
- [3. Design decisions](#3-design-decisions)
  - [3.1 Async, and when sync would do](#31-async-and-when-sync-would-do)
  - [3.2 ORM, Core or raw SQL, per endpoint](#32-orm-core-or-raw-sql-per-endpoint)
  - [3.3 One request, one transaction, injected](#33-one-request-one-transaction-injected)
  - [3.4 Three engines](#34-three-engines)
  - [3.5 The seller portal: RLS with `SET LOCAL`, per request](#35-the-seller-portal-rls-with-set-local-per-request)
  - [3.6 Idempotent checkout](#36-idempotent-checkout)
  - [3.7 Retries: what, and where](#37-retries-what-and-where)
  - [3.8 Timeouts, layered](#38-timeouts-layered)
  - [3.9 Errors as `application/problem+json`](#39-errors-as-applicationproblemjson)
  - [3.10 Pagination by keyset](#310-pagination-by-keyset)
  - [3.11 Search lives in the database (revision 2801)](#311-search-lives-in-the-database-revision-2801)
  - [3.12 Heavy reads on the read engine, read-your-writes on the primary](#312-heavy-reads-on-the-read-engine-read-your-writes-on-the-primary)
  - [3.13 Health, readiness, startup, shutdown](#313-health-readiness-startup-shutdown)
  - [3.14 Identity: a stand-in for authentication](#314-identity-a-stand-in-for-authentication)
- [4. Building it, step by step](#4-building-it-step-by-step)
  - [4.1 Settings](#41-settings)
  - [4.2 Revision 2801: the search function and two indexes](#42-revision-2801-the-search-function-and-two-indexes)
  - [4.3 Engines and the `Database`](#43-engines-and-the-database)
  - [4.4 The app factory and its lifespan](#44-the-app-factory-and-its-lifespan)
  - [4.5 Middleware and route tags](#45-middleware-and-route-tags)
  - [4.6 Errors](#46-errors)
  - [4.7 The catalog](#47-the-catalog)
  - [4.8 Cart and checkout](#48-cart-and-checkout)
  - [4.9 Order history](#49-order-history)
  - [4.10 The seller portal](#410-the-seller-portal)
  - [4.11 Admin](#411-admin)
  - [4.12 The background processes](#412-the-background-processes)
- [5. Testing strategy](#5-testing-strategy)
  - [5.1 The app in-process, the database for real](#51-the-app-in-process-the-database-for-real)
  - [5.2 Which database](#52-which-database)
  - [5.3 What the tests prove](#53-what-the-tests-prove)
  - [5.4 Tests at every stage of Part IV](#54-tests-at-every-stage-of-part-iv)
  - [5.5 What is not tested here](#55-what-is-not-tested-here)
- [6. Operations](#6-operations)
  - [6.1 Running it: uvicorn workers](#61-running-it-uvicorn-workers)
  - [6.2 Pool sizing per worker](#62-pool-sizing-per-worker)
  - [6.3 Migrations in a deploy](#63-migrations-in-a-deploy)
  - [6.4 Monitoring](#64-monitoring)
  - [6.5 Graceful shutdown in practice](#65-graceful-shutdown-in-practice)
- [7. Load testing](#7-load-testing)
  - [7.1 The load generator](#71-the-load-generator)
  - [7.2 Results](#72-results)
  - [7.3 Reading the numbers](#73-reading-the-numbers)
  - [7.4 Invariants](#74-invariants)
- [8. Where to go from here](#8-where-to-go-from-here)
- [Summary](#summary)
- [What's next](#whats-next)

<!-- toc:end -->

## How to run the capstone

Four processes and a database. Each needs `BAZAAR_DSN` (and the virtualenv) as above.

```bash
# 1. the API: four worker processes, each with its own pools
export BAZAAR_ADMIN_TOKEN=change-me        # enables /admin/*; unset = admin disabled
uvicorn bazaar.api.app:app --workers 4 --port 8000 --timeout-graceful-shutdown 20

# 2. the outbox relay: order events -> job queue (exactly once)
python -m bazaar.relay

# 3. the job worker (Module 23): runs the order_confirmation jobs
python -m bazaar.worker --concurrency 4
```

Try it:

```bash
curl -s localhost:8000/ready | python -m json.tool          # 200 when the database is usable
curl -s 'localhost:8000/products?category_id=14&sort=price_asc&limit=2'
curl -s 'localhost:8000/search?q=chef%20knife&limit=2'
curl -s -X POST localhost:8000/cart/items -H 'X-User-Id: 46' \
     -H 'content-type: application/json' -d '{"product_id": 47, "quantity": 2}'
curl -s -i -X POST localhost:8000/checkout -H 'X-User-Id: 46' -H "Idempotency-Key: $(uuidgen)"
curl -s 'localhost:8000/seller/orders?limit=2' -H 'X-Seller-Id: 5'
open http://localhost:8000/docs                            # the generated OpenAPI UI
```

The tests (`pytest -n 4`) and the load test (`python modules/28-capstone/examples/loadtest.py`, section 7) run from `project/python`. `/docs` lists every endpoint with its parameters and response models, generated from the code.

---

## 1. What we are building

### 1.1 The endpoints

| Method and path | What it does | Engine |
|---|---|---|
| `GET /health` | liveness: the process answers (no database) | none |
| `GET /ready` | readiness: primary reachable, not a standby, schema at head; pool usage | rw |
| `GET /products` | browse: category subtree, price range, JSONB attribute filter, in-stock, three sorts, keyset pages | read |
| `GET /products/{id}` | product page: seller, category breadcrumb, stock, rating | read |
| `GET /search?q=` | full-text search with business ranking, fuzzy fallback for typos | read |
| `POST /cart/items`, `DELETE /cart/items/{id}`, `GET /cart` | the cart, with current prices and stock | rw |
| `POST /checkout` | the cart becomes one order per seller; `Idempotency-Key` required | rw |
| `GET /users/{id}/orders` | order history, newest first, keyset pages | rw |
| `GET /seller/orders` | the seller's orders, filtered by RLS | seller |
| `GET /seller/products`, `GET /seller/products/{id}` | the seller's catalogue, with a `version` per product | seller |
| `PATCH /seller/products/{id}` | edit with optimistic locking: a stale `version` gets 409 | seller |
| `GET /seller/stats` | daily sales from the `seller_sales_daily` materialized view | read |
| `POST /admin/refresh-stats` | `REFRESH MATERIALIZED VIEW CONCURRENTLY`, behind `X-Admin-Token` | rw |

Two background processes complete the picture: `python -m bazaar.relay` publishes the outbox into the job queue, and `python -m bazaar.worker` (Module 23) runs the jobs.

### 1.2 Architecture

```mermaid
flowchart LR
    C[Storefront / app / seller portal] -->|HTTP| U

    subgraph U[uvicorn: N worker processes]
        direction TB
        A[FastAPI app<br/>middleware, routes, dependencies] --> RW[rw engine<br/>AsyncEngine, asyncpg<br/>role: app]
        A --> RO[read engine<br/>read-only<br/>BAZAAR_REPLICA_DSN or primary]
        A --> SE[seller engine<br/>role: bazaar_seller<br/>SET LOCAL app.seller_id]
    end

    RW --> B{{PgBouncer<br/>transaction mode<br/>optional}}
    SE --> B
    B --> P[(PostgreSQL primary)]
    RO --> R[(replica<br/>optional)]
    RO -. no replica .-> P
    P -->|streaming replication| R

    subgraph BG[background processes]
        RL[bazaar.relay<br/>outbox to jobs]
        W[bazaar.worker<br/>job handlers]
    end
    P -- "NOTIFY outbox" --> RL
    RL -- "INSERT INTO jobs" --> P
    P -- "NOTIFY jobs_ready" --> W
    W -- "dequeue_jobs / complete_job" --> P
```

Each uvicorn worker is a separate process with its own event loop and its own three connection pools, created in the app's lifespan after the fork. The relay and the job worker hold `LISTEN` connections, so they connect to PostgreSQL directly (or through a session-mode pool), never through a transaction-mode PgBouncer (Module 27, section 5.3).

A checkout, end to end:

```text
POST /checkout  (X-User-Id: 46, Idempotency-Key: k)
 │  RequestContext middleware: request id, 10 s deadline
 │  arun_in_session(sessions, attempt, CHECKOUT_RETRY, idempotent=True)
 │  ┌─ BEGIN ─────────────────────────────────────────────────────────────┐
 │  │ SELECT orders WHERE user_id = 46 AND idempotency_key = k   (replay?) │
 │  │ DELETE FROM cart_items WHERE user_id = 46 RETURNING ...  (take cart) │
 │  │ SELECT id FROM addresses WHERE ... is_default        (default addr.) │
 │  │ SAVEPOINT                                                            │
 │  │ SELECT place_order(46, '[...]', 46)    locks + reserves stock        │
 │  │ UPDATE orders SET idempotency_key = k WHERE id IN (...)              │
 │  │ INSERT INTO outbox ... SELECT ... FROM orders            (+ NOTIFY)  │
 │  │ RELEASE SAVEPOINT; SELECT orders + items                             │
 │  └─ COMMIT ─────────────────────────────────────────────────────────────┘
 │  201 {"orders": [...], "replayed": false}
 ▼
bazaar.relay (woken by NOTIFY outbox): outbox row -> jobs row, one transaction
bazaar.worker (woken by NOTIFY jobs_ready): order_confirmation -> complete_job()
```

### 1.3 The code

```text
project/python/src/bazaar/
├── api/
│   ├── app.py         create_app(): lifespan (engines, schema check, shutdown), routers
│   ├── db.py          Database (three engines + session factories), dependencies
│   ├── errors.py      domain and database errors -> application/problem+json
│   ├── middleware.py  request id, deadline, access log; TaggedRoute (SQL route tags)
│   ├── schemas.py     pydantic request and response models
│   └── routes/        health.py, catalog.py, shop.py, seller.py, admin.py
├── relay.py           python -m bazaar.relay
└── ... (modules 22-27, unchanged except for small additions noted below)
migrations/versions/2801_search_products.py
tests/test_api.py
```

About 1,600 lines of new application code, a third of them docstrings and comments. The rest is the package you already have.

---

## 2. Every module, one service

The service is mostly a composition of things the course already built. This table is the map; the rest of the module explains the decisions it implies.

| Feature in the service | Where it comes from |
|---|---|
| Tables, keys, one order per seller, `numeric(12,2)` money (serialized as strings) | [02](../02-types-and-ddl/), [07](../07-data-modeling/) |
| Cart upsert: `INSERT ... ON CONFLICT (user_id, product_id) DO UPDATE` | [03](../03-crud/) |
| `EXISTS` guards in the cart insert, `LATERAL` rating aggregate on the product page | [04](../04-joins-subqueries/) |
| Seller statistics: daily aggregates and totals | [05](../05-aggregation/) |
| Category subtree and breadcrumb (recursive CTEs); per-category top-N merge for browsing | [06](../06-advanced-sql/) |
| Attribute filter `attributes @> '{"ram_gb": 32}'`, enums for statuses | [08](../08-beyond-scalars/) |
| `seller_sales_daily` materialized view; triggers for `orders.total`, `updated_at`, audit | [09](../09-views-functions-triggers/) |
| `place_order()`: per-seller split, row locks in id order, `BZ001`; the job queue with `SKIP LOCKED` | [10](../10-transactions-concurrency/) |
| Partial and GIN indexes; `CREATE INDEX CONCURRENTLY` in revision 2801 | [11](../11-indexes/) |
| `EXPLAIN` of the listing query, `pg_stat_statements`, keyset pagination | [12](../12-query-performance/) |
| `/search`: `websearch_to_tsquery`, ranking with business signals, `ts_headline`, trigram fallback, `unaccent` | [13](../13-full-text-search/) |
| (Not used by the API: the partitioned `events` table is the analytics side; see section 8) | [14](../14-partitioning/) |
| Seller portal: `bazaar_seller` role, RLS policies, `SET LOCAL app.seller_id`; `SECURITY DEFINER` refresh | [15](../15-security-rls/) |
| Optimistic locking on `xmin`; why `rate_limits` would be `fillfactor`-tuned (HOT) | [16](../16-internals/) |
| Timeouts, `pg_stat_activity` by `application_name`, slow-query log, PgBouncer constraints | [17](../17-operations/) |
| Backups and restore drills: not code, but the service's data is only as safe as they are | [18](../18-backup-recovery/) |
| Read engine for heavy reads, read-your-writes on the primary | [19](../19-replication/) |
| Readiness checks `pg_is_in_recovery()`; retries on connection loss during failover | [20](../20-high-availability/) |
| `seller_id` everywhere a shard key would need it (orders never span sellers) | [21](../21-sharding/) |
| psycopg in the relay, the factories and the load test's setup | [22](../22-psycopg/) |
| asyncpg under SQLAlchemy, `LISTEN`/`NOTIFY`, the job worker | [23](../23-async-python/) |
| Core: the dynamic listing statement, the read engine | [24](../24-sqlalchemy-core/) |
| ORM: checkout, order history without N+1, seller product edits, `run_sync` for sync services | [25](../25-sqlalchemy-orm/) |
| Alembic revision 2801, `check_schema()` at startup | [26](../26-migrations/) |
| Retries, the outbox and relay, idempotency keys, timeouts, observability, health checks, test fixtures | [27](../27-python-production/) |

---

## 3. Design decisions

### 3.1 Async, and when sync would do

The service is async: FastAPI handlers are coroutines, and the database is reached through SQLAlchemy's `AsyncEngine` on asyncpg. The reason is the shape of the work. A request spends most of its life waiting: for a connection from the pool, for PostgreSQL to answer, for a row lock in `place_order()`. An event loop serves hundreds of waiting requests in one process with a few kilobytes each, where a thread per request would cost a thread stack each and a context switch per wake-up (Module 23, section 1).

Async doesn't make a query faster, and it doesn't make CPU-bound work concurrent. With N uvicorn workers, at most N requests run Python code at the same moment. That's why there is one worker process per CPU core, and why anything CPU-heavy (rendering a PDF, resizing an image) goes to the job queue instead of a handler. A sync service (Flask or Django with psycopg and a thread pool) would be a perfectly good choice for Bazaar too. It's simpler to debug and has no "forgot an `await`" bugs. Choose it when the team knows it better. The SQL, the transactions and everything in sections 3.3–3.13 would be the same.

The relay and the worker are async too, but for a different reason: each holds a `LISTEN` connection and waits for notifications while it works.

### 3.2 ORM, Core or raw SQL, per endpoint

There is no single right layer. Each endpoint uses the one that says its query most clearly:

| Endpoint | Layer | Why |
|---|---|---|
| `GET /products` | Core | a dynamic statement: filters and sort chosen per request, composed safely; flat rows for JSON |
| `GET /products/{id}`, `/cart`, `/seller/orders`, `/seller/stats` | `text()` | fixed statements, easier to read as SQL than as Python |
| `GET /search` | `text()` calling `search_products()` | the logic lives in the database (revision 2801), where it is fast and shared |
| `POST /checkout` | ORM (`services.checkout_async`) | loads orders *with* their items in two statements, the module 25 service with the module 27 additions |
| `GET /users/{id}/orders` | ORM | `selectinload(Order.items)` + `raiseload("*")`: no N+1, impossible by construction |
| `PATCH /seller/products/{id}` | ORM (`services.update_product`) | the unit of work and `version_id_col` on `xmin` give optimistic locking for free |
| `POST /cart/items` | `text()` | one `INSERT ... SELECT ... WHERE EXISTS ... ON CONFLICT` does validation and upsert in one round trip |

The rule from Module 25 (section 15) holds: use the ORM for aggregates you load and change as objects, and Core or SQL for reports, bulk work and statements where the database does the thinking. Mixing them is normal, and they share one session and one transaction.

Responses are pydantic models built from plain values, never ORM objects handed to the serializer. By the time FastAPI serializes, the transaction has committed and the session may be closed. A lazy load at that point would raise (async) or run a query outside the transaction (sync).

### 3.3 One request, one transaction, injected

A handler declares what it needs, and FastAPI's dependency injection provides it:

```python
async def _rw_session(db: DB) -> AsyncIterator[AsyncSession]:
    async with db.sessions.begin() as session:  # COMMIT on success, ROLLBACK on error
        yield session


# scope="function": the code after `yield` (COMMIT) runs before the response is sent.
RwSession = Annotated[AsyncSession, Depends(_rw_session, scope="function")]


@router.post("/cart/items", response_model=Cart, status_code=201)
async def add_to_cart(item: CartItemIn, user_id: UserId, session: RwSession) -> Cart: ...
```

The transaction boundary is the request: the handler runs inside `BEGIN`, and the dependency commits when the handler returns or rolls back when it raises. Two details matter.

1. **When the commit happens.** A yield-dependency's exit code can run before or after the response is sent. With `scope="request"`, the default for yield dependencies, it runs after: the client may already have its `201 Created` when the `COMMIT` fails (a deferred constraint, a serialization failure, a lost connection). `scope="function"` ends the dependency when the handler returns, before the response starts, so the status code always describes a committed transaction. (The CSV export of exercise 6 is the one place that needs `scope="request"`: its session must outlive the handler while the body streams.)
2. **Reads are transactions too.** `ReadSession` also runs in `BEGIN ... COMMIT`, in a read-only transaction. That gives the product page's two statements one snapshot under `REPEATABLE READ` if you ever need it. It also gives `SET LOCAL` a scope: search uses one to tighten its timeout.

Handlers that must **retry** (checkout) don't take a session. They take the factory and call `arun_in_session()`, which opens a new session and transaction for every attempt (Module 27, section 3.1). A retry inside a dependency-provided transaction would retry inside an aborted one.

### 3.4 Three engines

| Engine | Connects as | Pool (default) | Used for |
|---|---|---|---|
| `rw` | the app DSN (`BAZAAR_DSN`) | `BAZAAR_POOL_MAX_SIZE` = 10 | writes, read-your-writes reads, readiness |
| `read` | `BAZAAR_REPLICA_DSN` or `BAZAAR_DSN`, `default_transaction_read_only=on`, `application_name=bazaar-ro` | 10 | catalog, search, statistics |
| `seller` | `BAZAAR_SELLER_DSN`, default: `BAZAAR_DSN` with `bazaar_seller`/`seller`; `application_name=bazaar-seller` | `BAZAAR_SELLER_POOL_SIZE` = 3 | the seller portal, under RLS |

Each is created once per process (in the lifespan) and owns its pool. There is no `psycopg_pool` stacked on top (Module 24, section 2.5). The read engine is new in this module, `create_async_read_engine()` in `bazaar/sa/engine.py`. It is the async twin of Module 27's `create_read_engine()`, and it sends `default_transaction_read_only=on` as a startup parameter, so it costs no round trip. Without a replica it points at the primary, still read-only, so an accidental write through it fails in development (`25006`) instead of only in production.

Why not one engine for everything? The seller portal must connect as a role that RLS applies to (next section), and a read-only pool to a replica must be separate anyway. The price is more connections; section 6.2 does the arithmetic.

### 3.5 The seller portal: RLS with `SET LOCAL`, per request

Module 15 made the database enforce "a seller sees only their own products and orders": RLS policies for `bazaar_seller` compare `seller_id` with `current_seller_id()`, which reads the setting `app.seller_id`. The service's job is to set that setting for exactly the duration of a request, on a connection whose role the policies apply to.

```python
async def _seller_session(db: DB, seller_id: SellerId) -> AsyncIterator[AsyncSession]:
    async with db.seller_sessions(info={SELLER_KEY: seller_id}) as session, session.begin():
        yield session
```

`BazaarSession`'s `after_begin` hook (Module 25, section 12) runs `SELECT set_config('app.seller_id', '42', true)` as the first statement of every transaction. That's `SET LOCAL`: the setting ends at `COMMIT` or `ROLLBACK`. The pooled connection goes back to the pool clean, which is also what makes the pattern safe behind PgBouncer in transaction mode. A session-level `SET` would leak seller 42's identity into whatever request gets that server connection next (Module 27, section 5.3 shows it happening).

`GET /seller/orders` has **no** `WHERE seller_id = ...`. The policy adds it:

```sql
SELECT o.id, o.user_id, o.status, o.total, o.created_at,
       (SELECT count(*) FROM order_items oi WHERE oi.order_id = o.id) AS item_count
FROM orders o
WHERE true
ORDER BY o.created_at DESC, o.id DESC LIMIT $1
```

That's deliberate. The isolation test (section 5) then proves the *policy*, not a `WHERE` clause someone wrote correctly once. And an endpoint added next year can't forget the filter. Elsewhere, the ORM services filter explicitly *and* rely on RLS, with RLS as the safety net. Both styles are defensible; what isn't defensible is a portal whose only protection is application code.

`current_seller_id()` is `STABLE`, so the planner evaluates it once per statement and can use `orders_seller_id_created_at_idx` (Module 15, section 12.1). A product of another seller is `404 Not Found`, not `403`: under RLS the row doesn't exist for this role, and saying "forbidden" would leak that it does.

Alternatives, and why not here:

- **`SET LOCAL ROLE bazaar_seller` on the main pool.** One pool instead of two. But `bazaar_app` must then be a member of `bazaar_seller`, and a bug that skips the `SET LOCAL ROLE` silently runs as `bazaar_app`, whose policy is `USING (true)`: a fail-open design. A separate pool that can only ever be `bazaar_seller` fails closed.
- **A pool per seller.** 2,000 sellers × a few connections: no.
- **Filtering in the application only.** The first forgotten `WHERE` is a data breach.

`GET /seller/stats` is the exception that needs care. It reads the `seller_sales_daily` materialized view, and materialized views can't have RLS policies, so `bazaar_seller` was never granted access to it. It runs on the read engine (the application role) with an explicit `WHERE seller_id = :seller_id`. Whenever you serve tenant data from a materialized view, a cache or a search index, the database no longer enforces the isolation for you, and your code has to.

### 3.6 Idempotent checkout

A buyer taps "Pay", the phone loses signal, the app retries. Without protection that's two orders. Module 27 added `orders.idempotency_key` (revision 2604) and `checkout(..., idempotency_key=)`. The HTTP layer adds three rules:

1. **The key is required.** A checkout without `Idempotency-Key` is `400`. A request that can't be retried safely shouldn't exist in an API whose clients retry. Clients generate a UUID when the buyer presses the button and reuse it for every retry of that press.
2. **The key identifies the request, not the cart.** A repeated key returns the first answer (`200`, `Idempotent-Replayed: true`, the same orders) even if the cart changed in between. This is the semantics of the IETF `Idempotency-Key` draft and of payment APIs. Keys are per user: the same string from another user is that user's own first request.
3. **The cart is taken atomically.** The checkout transaction starts with `DELETE FROM cart_items WHERE user_id = :user_id RETURNING product_id, quantity`. The `DELETE` locks the cart rows, so two concurrent checkouts of one cart (two keys, a double click with a buggy client) run one after the other. The second waits for the first to commit, then finds the cart empty and, if its key matches the first's orders, replays them. If `place_order()` fails (`409`, out of stock), the rollback puts the cart back.

Five identical requests fired at once produce one `201` and four `200`s with identical bodies, and stock reserved once (`test_concurrent_retries_with_one_key_order_once`). The unique index `orders_idempotency_key_idx` is the last line of defence if two requests with the same key get past the first `SELECT` concurrently: the loser's `UPDATE` violates it, its savepoint rolls back (releasing its reservation), and `checkout_async` re-reads the winner's orders.

The same `DELETE ... RETURNING` has a second purpose, explained in section 7: it does its locking before `place_order()` locks the hot inventory rows.

### 3.7 Retries: what, and where

Module 27's rule is to retry whole transactions, and only errors that mean "try again". In the service that becomes:

- **Checkout** runs in `arun_in_session(db.sessions, attempt, CHECKOUT_RETRY, idempotent=True)` with `RetryPolicy(attempts=3, base_delay=0.05, deadline=5.0)`. Serialization failures, deadlocks and lock timeouts are retried. So is a lost connection, even mid-`COMMIT`, *because* the request carries an idempotency key: if the first attempt did commit, the retry finds its orders and replays them. Exercise 9 kills the connection in the middle of a checkout and watches this happen.
- **Reads are not retried.** A `GET` that fails returns `503` with `Retry-After: 1`, and the client (or the load balancer) retries. Retrying inside the service as well would multiply load exactly when the database is struggling: three layers each retrying three times is 27 attempts.
- **Other writes are not retried** either. The cart upsert is naturally idempotent in effect only if you squint (quantities add up), and the seller's `PATCH` has its own conflict protocol.

Retries cost time from the request's budget. The deadline of 5 s keeps a retrying checkout inside the 10-second request deadline.

### 3.8 Timeouts, layered

| Layer | Setting | What happens |
|---|---|---|
| connecting | `connect_timeout` 5 s (`timeout` for asyncpg) | the pool raises; `503` |
| waiting for a pooled connection | `BAZAAR_POOL_TIMEOUT` 10 s | `sqlalchemy.exc.TimeoutError`, classified `overloaded`; `503` |
| one statement | `statement_timeout` 5 s on every connection; `SET LOCAL statement_timeout = '2s'` for search, `'120s'` for the admin refresh | `57014`; `503 timeout` |
| an idle transaction | `idle_in_transaction_session_timeout` 30 s | the server ends the session |
| the whole request | `BAZAAR_REQUEST_TIMEOUT` 10 s, `asyncio.timeout()` in the middleware | the handler is cancelled at its current `await`; `504` |
| a checkout's retries | `RetryPolicy(deadline=5.0)` | no retry starts that would end after it |

Each inner limit is shorter than the outer one, so the innermost layer that knows what went wrong is the one that reports it. A slow statement becomes `503 timeout` (with the SQLSTATE in the log), not a vague `504`. The request deadline is the backstop for everything else: a lock wait under `lock_timeout = 0`, a pool wait, a sequence of fast statements that add up.

Search gets a tighter limit because users choose its cost. `"the"` is cheap, but a common word ranks every product that contains it. The admin refresh gets a looser one because the materialized view takes 3.2 s to refresh `CONCURRENTLY` on the large dataset, which is over the 5-second default once the machine is busy. Both use `SET LOCAL`, so the exception ends with the transaction.

When the middleware's deadline cancels a handler during a query, asyncpg sends a cancel request to the server, and SQLAlchemy discards the connection rather than return it to the pool in an unknown state. `test_statement_timeout_is_503_and_deadline_is_504` checks both outcomes, and that the pool has no connection checked out afterwards.

### 3.9 Errors as `application/problem+json`

Every error has the same shape (RFC 9457), so a client needs one parser:

```text
HTTP/1.1 409 Conflict
content-type: application/problem+json
x-request-id: b5c680bd34b745e291a66fb5aba7773d

{"type":"https://bazaar.example/problems/insufficient-stock","title":"Insufficient stock",
 "status":409,"detail":"insufficient stock for product 47","product_id":47,"requested":3,
 "available":2,"request_id":"b5c680bd34b745e291a66fb5aba7773d"}
```

The mapping lives in one table, `DOMAIN_STATUS` in `api/errors.py`:

| Domain error (SQLSTATE) | Status |
|---|---|
| `InsufficientStock` (`BZ001`) | 409, with `product_id`, `requested`, `available` |
| `ProductUnavailable` (`BZ002`), `AddressMismatch` (`BZ004`), `InvalidOrder` (`22023`) | 422 |
| `UserInactive` (`BZ003`) | 403 |
| `NotFound` (including "hidden by RLS") | 404 |
| `ConcurrentUpdate` (optimistic lock) | 409 |
| `IdempotencyConflict` | 422 |

Database errors nobody translated go through `classify()` (Module 27, section 2). Transient kinds (timeout, overloaded, connection, read-only after a failover, serialization that outlived its retries) become `503` with `Retry-After: 1`. An integrity error becomes `409` with the constraint's name. Anything else becomes `500` with no detail at all. The full error goes to the log, never to the client: error messages contain table names, values, sometimes other users' data. Validation errors from pydantic become `422` with the list of problems.

### 3.10 Pagination by keyset

Every list endpoint pages by keyset (Module 12, section 12.2): the client sends back an opaque `cursor`, which encodes the sort key of the last row it saw, and the next page is `WHERE (price, id) > (:price, :id) ORDER BY price, id LIMIT n + 1`. Fetching one extra row tells whether there is a next page without a `count(*)`. The cost of page 1,000 is the cost of page 1.

The cursor is base64 of JSON, and it **names its sort**: `["price_asc", "4.99", 295]`. A cursor made for `sort=price_asc` sent with `sort=newest` is rejected with `400`. Otherwise `4.99` would be read as an id. Clients must treat cursors as opaque. If they start constructing them, you can never change the encoding.

`sort=newest` orders by `id DESC`, not `created_at DESC`. Identity ids are assigned in insertion order, so for "newest listings" they are equivalent, and the primary key serves the order for free. A `created_at` sort would need another index on a 200,000-row table that is updated constantly.

### 3.11 Search lives in the database (revision 2801)

Module 13's milestone wrote `search_products(q, category_id, max_results)`: `websearch_to_tsquery` (user input can never be a syntax error), 200 candidates by `ts_rank_cd`, re-ranked with a Bayesian-average rating and units sold, `ts_headline` snippets for the returned rows only, and a trigram fallback on unaccented titles for typos (`headphnes`). But it lives in `modules/13-full-text-search/solutions/`, not in `project/db/`, so the reference database has the search *column* and not the search *function*.

The options were to reimplement the ranking in Python, to call the SQL file from the service at startup, or to make it part of the schema. Only the last is right. Ranking 200 candidates against reviews and order lines is a job for the database, next to the data. And the schema belongs to Alembic now (Module 26), so the function arrives as **revision 2801** (section 4.2).

### 3.12 Heavy reads on the read engine, read-your-writes on the primary

The catalog, search and statistics are read-heavy, tolerant of a second of staleness, and the bulk of the traffic: in the load test they're half the requests. They use the read engine, which points at a replica when `BAZAAR_REPLICA_DSN` is set. Everything a user just changed is read from the primary: the cart after "add to cart", the order history after checkout. A replica may be milliseconds or seconds behind (Module 19, section 9.2), and "my order disappeared" is a worse experience than a slightly slower page. Exercise 7 makes the history endpoint smarter: replica when it has caught up with the user's last write, primary otherwise.

### 3.13 Health, readiness, startup, shutdown

- **`/health`** answers `{"status": "ok"}` without touching the database. Liveness asks "should this process be restarted?", and restarting the API because the database is down only slows recovery.
- **`/ready`** runs Module 27's `check_health()` on a pooled connection with a 1-second timeout: reachable (with latency), `pg_is_in_recovery()` false (after a failover a pool may still point at the old primary, now a standby), schema at the head this code expects. It caches the result for 2 seconds, so three load balancers polling every second aren't a load of their own. It adds the pool usage of all three engines and returns `503` while shutting down.
- **Startup** runs `migrate.check_schema()` (Module 26): a process whose code expects revision 2801 refuses to start against a database at 2701, with a message that says so. It fails at deploy time instead of on the first search request. Set `BAZAAR_CHECK_SCHEMA=0` for the deployment patterns that start new code before the migration (Module 26, section 11.2).
- **Shutdown**: on `SIGTERM`, uvicorn stops accepting connections and waits up to `--timeout-graceful-shutdown` seconds for in-flight requests. Then the lifespan's `finally` marks the app as shutting down and disposes the three engines, so PostgreSQL sees clean disconnects instead of dead sockets.

### 3.14 Identity: a stand-in for authentication

The service trusts `X-User-Id`, `X-Seller-Id` and `X-Admin-Token` headers. That's the one deliberate shortcut in this module. Authentication (OAuth 2 / OIDC tokens, sessions, API keys) is its own subject, and nothing in it is specific to PostgreSQL. The dependencies `current_user_id()` and `current_seller_id()` are where a real token check would go: verify a JWT, look up a session, and return the id. Every handler gets the id from a dependency, never from a header directly, so swapping the implementation changes one function. Trusted identity headers do exist in production, behind an authenticating gateway that strips them from outside traffic. Never expose such a service directly.

---

## 4. Building it, step by step

### 4.1 Settings

`bazaar/config.py` gains five settings, read from the environment like the rest (plus `BAZAAR_LOG_FORMAT`, read by the lifespan):

| Variable | Default | Used by |
|---|---|---|
| `BAZAAR_SELLER_DSN` | `BAZAAR_DSN` with user `bazaar_seller`, password `seller` | the seller engine |
| `BAZAAR_SELLER_POOL_SIZE` | 3 | the seller engine |
| `BAZAAR_REQUEST_TIMEOUT` | 10000 ms (0 = none) | the middleware's deadline |
| `BAZAAR_ADMIN_TOKEN` | unset: `/admin/*` answers 403 | `require_admin` |
| `BAZAAR_CHECK_SCHEMA` | 1 | the startup check |
| `BAZAAR_LOG_FORMAT` | unset; `json`: one JSON object per log line (Module 27's `JsonFormatter`) | the lifespan |

`Settings` stays a frozen dataclass whose fields default to environment lookups, so a test builds one with `dataclasses.replace(get_settings(), dsn=fresh_db, admin_token="...")`.

### 4.2 Revision 2801: the search function and two indexes

```python
revision = "2801"
down_revision = "2701"


def upgrade() -> None:
    execute_script(IMMUTABLE_UNACCENT)
    op.execute("SELECT public.similarity('a', 'b')")  # load pg_trgm: see below
    execute_script(SEARCH_PRODUCTS)
    create_index_concurrently(  # products is hot: no long locks
        "products_title_unaccent_trgm_idx",
        "products",
        "immutable_unaccent(title) gin_trgm_ops",
        using="gin",
    )
    create_index_concurrently(
        "products_active_category_id_id_idx",
        "products",
        "category_id, id",
        where="status = 'active'",
    )
```

The function body is copied into the revision verbatim. A revision must do the same thing in a year, so it never reads a file that might change. `immutable_unaccent()` and the trigram index on the unaccented title come with the function, because the fuzzy fallback needs them. The second index serves "newest first" per category in the listing (section 4.7). Both are built `CONCURRENTLY` through Module 26's helper, which commits the revision's work so far and is safe to re-run. On the large dataset the whole revision takes 4.5 seconds.

One surprise from the first run of this revision:

```text
psycopg.errors.InsufficientPrivilege: permission denied to set parameter "pg_trgm.word_similarity_threshold"
```

The function has a `SET pg_trgm.word_similarity_threshold = 0.5` clause. Alembic runs as `bazaar_owner` (Module 26's `SET ROLE`). Until the `pg_trgm` library is loaded in the session, the server knows that parameter only as a *placeholder*, and only a superuser may attach a placeholder setting to a function. Calling any `pg_trgm` function first loads the library and turns the placeholder into a real, user-settable parameter. Migrations hit corners like this that interactive `psql` sessions as `postgres` never do. That's one more reason to test them as the role that really runs them.

`bazaar.sa.tables` describes both indexes, including the expression index (`sa.func.immutable_unaccent(products.c.title).label("title_unaccent")` with `postgresql_ops={"title_unaccent": "gin_trgm_ops"}`), so `test_models_match_migrated_schema` stays empty.

### 4.3 Engines and the `Database`

```python
@dataclass
class Database:
    settings: Settings
    rw: AsyncEngine
    read: AsyncEngine
    seller: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    read_sessions: async_sessionmaker[AsyncSession]
    seller_sessions: async_sessionmaker[AsyncSession]
    stats: dict[str, obs.QueryStats] = field(default_factory=dict)

    @classmethod
    def create(cls, settings: Settings) -> Database:
        rw = create_async_engine(settings)
        read = create_async_read_engine(settings)
        seller = create_async_engine(
            _seller_settings(settings), pool_size=settings.seller_pool_size
        )
        stats = {
            name: obs.instrument_engine(engine, slow_ms=200)
            for name, engine in (("rw", rw), ("read", read), ("seller", seller))
        }
        for engine in (rw, read, seller):
            obs.install_sqlcommenter(engine, static={"app": "bazaar-api"})
        ...
```

Every engine is instrumented the Module 27 way: per-statement timings, slow-query warnings over 200 ms, failure logs with SQLSTATE and kind, and a sqlcommenter tag. The tags are deliberately low-cardinality, only the app and the route template (section 4.5). asyncpg caches prepared statements by their text, so a request id in the comment would make every statement new.

The session factories are Module 25's `make_async_sessionmaker()`. The sessions are `BazaarSession`s underneath, so the RLS hook and the read-only guard for views come along.

The read engine's read-only mode is set where the driver connects:

```python
@event.listens_for(engine.sync_engine, "do_connect")
def _read_only(dialect, conn_rec, cargs, cparams) -> None:
    if driver == "asyncpg":
        cparams["server_settings"] = {
            **cparams.get("server_settings", {}),
            "default_transaction_read_only": "on",
        }
```

`do_connect` receives the keyword arguments for `asyncpg.connect()` (the same dict for every new connection, so the change must be idempotent), and the setting travels in the startup packet like the timeouts.

### 4.4 The app factory and its lifespan

```python
def create_app(settings=None, *, extra_routers=(), **fastapi_kwargs) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        s = settings or get_settings()
        db = Database.create(s)
        try:
            if s.check_schema:
                async with db.rw.connect() as conn:
                    await conn.run_sync(migrate.check_schema)
            app.state.db = db
            app.state.shutting_down = False
            yield
        finally:
            app.state.shutting_down = True
            await db.dispose()

    app = FastAPI(title="Bazaar", version="0.28.0", lifespan=lifespan, **fastapi_kwargs)
    errors.install(app)
    app.add_middleware(RequestContext, timeout_ms=(settings or get_settings()).request_timeout_ms)
    for router in (health.router, catalog.router, shop.router, seller.router, admin.router):
        app.include_router(router)
    ...
    return app


app = create_app()
```

Three things to notice. The engines are created *in the lifespan*, which runs in each worker process after uvicorn forks it, inside its event loop. An engine created at import time would be created before the fork and shared between processes (Module 27, section 5.7), and an async pool can't move between event loops at all. `check_schema()` is sync code (Alembic's `MigrationContext`); `run_sync` runs it on the async connection's sync facade, IO included. And `create_app()` takes `extra_routers` and FastAPI keyword arguments, which is how the exercises plug in without editing the package.

`app = create_app()` at module level builds the app object only. Settings are read and connections opened at startup, so importing the module is cheap and side-effect free.

### 4.5 Middleware and route tags

`RequestContext` is a pure ASGI middleware: no extra task per request, and it sees the whole response, including streaming bodies. It takes `X-Request-ID` from the client or proxy (or generates one), stores it where the error handlers find it, adds it to the response, wraps the request in `asyncio.timeout()`, and writes one access-log line with status and duration. On a timeout before the response started, it sends a `504` problem itself.

Route tags come from a custom route class:

```python
class TaggedRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        tag = f"{','.join(sorted(self.methods))} {self.path_format}"

        async def tagged(request: Request):
            with obs.sql_context(route=tag):
                return await handler(request)

        return tagged
```

Every router uses `route_class=TaggedRoute`, so every statement, including the `BEGIN` and `set_config` that dependencies run, carries `/*app='bazaar-api',route='GET%20%2Fproducts%2F%7Bproduct_id%7D'*/`. In `pg_stat_activity` and the server's slow-query log you can see which endpoint sent a query. That's the route *template*, never the URL with its ids.

### 4.6 Errors

`errors.install(app)` registers four handlers: domain errors (the table of section 3.9), `ApiError` (errors of the HTTP layer itself: `Unauthorized`, `Forbidden`, `EmptyCart`, `InvalidCursor`), database errors (`sa.exc.DBAPIError` and the pool's `sa.exc.TimeoutError`, through `classify()`), and validation errors. The database handler first tries `translate()` on the wrapped driver error, because a `BZ001` raised outside a service's own translation is still a domain error:

```python
async def _on_database_error(request: Request, exc: Exception) -> JSONResponse:
    orig = getattr(exc, "orig", None)
    if orig is not None:
        translated = domain.translate(orig)
        if isinstance(translated, domain.BazaarError):
            return await _on_domain_error(request, translated)
    kind = classify(exc)
    if kind in TRANSIENT:
        slug, title = TRANSIENT[kind]
        return problem(request, 503, slug, title, headers={"Retry-After": "1"})
    ...
```

### 4.7 The catalog

**Browsing.** `GET /products` builds its statement with Core: optional filters, then a sort, then the keyset condition. The first version filtered with `category_id IN (SELECT id FROM subtree)` over a recursive CTE, and it passed every test. The load test (section 7) then showed this statement averaging **2.8 seconds** per call in `pg_stat_statements` under load. `EXPLAIN` on an idle database shows why:

```text
 Limit (actual time=123.851..127.995 rows=21 loops=1)
   ->  Nested Loop (actual time=123.848..127.983 rows=21 loops=1)
         ->  Nested Loop Semi Join (actual time=123.794..127.565 rows=21 loops=1)
               Join Filter: (subtree.id = p.category_id)
               Rows Removed by Join Filter: 23074
               ->  Gather Merge (actual time=120.520..124.103 rows=1361 loops=1)
                     ->  Sort (actual time=113.673..113.808 rows=972 loops=3)
                           Sort Key: p.price, p.id
                           Sort Method: external merge  Disk: 3032kB
                           ->  Parallel Seq Scan on products p (actual time=0.056..43.422 rows=56679 loops=3)
                                 Filter: (status = 'active'::product_status)
 Execution Time: 128.605 ms
```

The planner can't know which categories the recursive CTE will produce. It sorts *all* 170,038 active products by price (spilling to disk), then walks the sorted list, checking each against the subtree, until 21 match. Module 11's index on `(category_id, price) WHERE status = 'active'` can't help a single scan: it is ordered by price only *within* each category.

It can help 17 small scans, though. The fix is a pattern from Module 06 (top-N per group) used the other way round. For each category of the subtree, take the first 21 rows straight from the index, then merge the 17 short lists:

```python
top = (
    stmt.where(p.c.category_id == cats.c.id)  # one category per LATERAL call
    .order_by(*order)
    .limit(limit + 1)
    .lateral("top")
)
return (
    sa.select(*top.c)
    .select_from(cats.join(top, sa.true()))  # FROM subtree JOIN LATERAL (...) top ON true
    .order_by(*outer_order)
    .limit(limit + 1)
)
```

```text
 Limit (actual time=10.278..10.284 rows=21 loops=1)
   ->  Sort (actual time=10.277..10.279 rows=21 loops=1)
         Sort Key: p.price, p.id
         Sort Method: top-N heapsort  Memory: 29kB
         ->  Nested Loop (actual time=0.726..10.183 rows=252 loops=1)
               ->  CTE Scan on subtree (actual time=0.006..0.111 rows=17 loops=1)
               ->  Limit (actual time=0.587..0.590 rows=15 loops=17)
                     ->  Incremental Sort (actual time=0.586..0.587 rows=15 loops=17)
                           Sort Key: p.price, p.id
                           Presorted Key: p.price
                           ->  Nested Loop (actual time=0.024..0.569 rows=33 loops=17)
                                 ->  Index Scan using products_active_category_id_price_idx on products p ...
                                       Index Cond: (category_id = subtree.id)
 Execution Time: 10.342 ms
```

From 128.6 ms to 10.3 ms, and the cost no longer grows with the size of the category. The same shape serves `sort=newest` per category through the new `(category_id, id) WHERE status = 'active'` index of revision 2801. Without a category filter, the "categories" are all 69 of them. Two cases keep the plain shape: an attribute filter, where the GIN index on `attributes` is the selective access path and `@>` drives a bitmap scan, and `sort=newest` over the whole catalogue, which is a backward scan of the primary key.

One more detail in that statement:

```python
.where(sa.text("products.status = 'active'"))   # not: p.c.status == "active"
```

`p.c.status == "active"` compiles to `status = $1`. asyncpg prepares statements, and after five executions PostgreSQL may switch a prepared statement to a *generic* plan, one that doesn't know `$1`. A generic plan can't prove `status = $1` implies the index predicate `status = 'active'`, so it can't use a partial index at all. A literal in the SQL text keeps the partial indexes usable in every plan. The same applies to any partial index you rely on from an application that prepares statements.

**The product page** is one statement with a `LATERAL` rating aggregate, plus a recursive CTE for the breadcrumb, both `text()`. Drafts are `404`. **Search** is one call, after tightening the timeout:

```python
await session.execute(sa.text("SET LOCAL statement_timeout = '2s'"))
rows = await session.execute(SEARCH, {"q": q, "category_id": category_id, "limit": limit})
```

```json
{"query": "chef knife",
 "items": [{"product_id": 52, "title": "Casa Verde Vintage Chef's Knife", "price": "12.99",
            "category_name": "Cutlery",
            "snippet": "<mark>chef</mark>'s <mark>knife</mark> from Casa Verde, made of terracotta",
            "score": "0.9341", "matched_by": "fts"}, ...]}
```

### 4.8 Cart and checkout

The cart upsert validates and writes in one round trip. The `SELECT ... WHERE` yields no row (so nothing is inserted) for an inactive product or user, and only on that failure path does a second query find out which:

```sql
INSERT INTO cart_items AS c (user_id, product_id, quantity)
SELECT :user_id, p.id, :quantity
FROM products p
WHERE p.id = :product_id
  AND p.status = 'active'
  AND EXISTS (SELECT 1 FROM users u WHERE u.id = :user_id AND u.is_active)
ON CONFLICT (user_id, product_id)
    DO UPDATE SET quantity = c.quantity + EXCLUDED.quantity
RETURNING c.quantity
```

Checkout is the core of the service:

```python
async def place_cart_order(session, user_id, key, shipping_address_id):
    existing = (await session.scalars(_orders_with_key(user_id, key))).all()
    if existing:
        return existing, True  # a retried request
    taken = await session.execute(TAKE_CART, {"user_id": user_id})  # DELETE ... RETURNING
    items = sorted((r.product_id, r.quantity) for r in taken)
    if not items:
        existing = (await session.scalars(_orders_with_key(user_id, key))).all()
        if existing:
            return existing, True  # a concurrent twin won
        raise EmptyCart("add items to the cart before checking out")
    if shipping_address_id is None:
        shipping_address_id = await session.scalar(DEFAULT_ADDRESS, {"user_id": user_id})
    orders = await services.checkout_async(
        session, user_id, items, shipping_address_id, idempotency_key=key
    )
    return orders, False


@router.post("/checkout", response_model=CheckoutOut, status_code=201)
async def checkout(response: Response, user_id: UserId, db: DB, body=None, idempotency_key=None):
    ...
    orders, replayed = await arun_in_session(db.sessions, attempt, CHECKOUT_RETRY, idempotent=True)
    if replayed:
        response.status_code = 200
        response.headers["Idempotent-Replayed"] = "true"
    return CheckoutOut(orders=[OrderOut.model_validate(o) for o in orders], replayed=replayed)
```

Everything below `checkout_async` is Module 27's code, unchanged: `place_order()` in a savepoint, the key stored on the new orders, one `order.placed` outbox event per order. The ORM objects outlive the session because the factories use `expire_on_commit=False`, and their items were loaded eagerly, so building the response after the commit needs no further query.

One change was needed below the API: `Product.xmin` is mapped with a small custom type, `XID`, that sent the version as a string. That's what psycopg wants, because the server casts text to `xid`. asyncpg prepares the statement, learns the parameter's type is `xid`, and wants an `int`:

```text
asyncpg.exceptions.DataError: invalid input for query argument $3: '6339' (an integer is required)
```

`XID.bind_processor` now returns `int` for the asyncpg dialect. It's a small reminder that "the same SQLAlchemy code on another driver" is a claim to test, not to assume.

### 4.9 Order history

```python
stmt = (
    sa.select(Order)
    .where(Order.user_id == user_id)
    .order_by(Order.created_at.desc(), Order.id.desc())
    .limit(limit + 1)
    .options(selectinload(Order.items), raiseload("*"))
)
if cursor:
    created_at, last_id = decode_cursor(cursor)
    stmt = stmt.where(sa.tuple_(Order.created_at, Order.id) < sa.tuple_(created_at, last_id))
```

Two statements per page, whatever the page size: the orders, then `SELECT ... FROM order_items WHERE order_id IN (...)`. `orders_user_id_created_at_idx` serves the order and the keyset condition. The cursor encoding is Module 22's `encode_cursor()`. The path's user id must match `X-User-Id`; otherwise `403`.

### 4.10 The seller portal

The orders list is the RLS query of section 3.5. Products come from Module 25's synchronous services, run on the async session with `run_sync`:

```python
@router.patch("/products/{product_id}", response_model=SellerProduct)
async def update_seller_product(product_id: int, patch: ProductPatch, session: SellerSession):
    changes = patch.model_dump(exclude_unset=True, exclude={"version"})

    def update(s):  # a sync Session
        return services.update_product(s, product_id, expected_version=patch.version, **changes)

    return _out(await session.run_sync(update))
```

`run_sync` passes the `AsyncSession`'s underlying `Session` to a plain function and runs it inside SQLAlchemy's greenlet, so its IO goes through asyncpg on the same connection and in the same transaction. That's how a codebase with sync services adopts async handlers without rewriting them.

The **version** is the row's `xmin` (Module 25, section 9): every update creates a new row version with a new `xmin`. The client gets it with the product and sends it back. If someone changed the product in between, the versions don't match:

```text
$ curl -s -X PATCH localhost:8000/seller/products/47 -H 'X-Seller-Id: 5' \
       -H 'content-type: application/json' -d '{"version": 869, "price": "13.49"}'
{"price": "13.49", "version": 111847}           # (fields abbreviated)
$ curl -s -X PATCH localhost:8000/seller/products/47 -H 'X-Seller-Id: 5' \
       -H 'content-type: application/json' -d '{"version": 869, "title": "Burning Garden (2nd ed.)"}'
{"type":"https://bazaar.example/problems/concurrent-update","title":"Changed by someone else",
 "status":409,"detail":"product 47 changed since version 869",...}
```

The service checks the version after loading the row. The ORM's `version_id_col` *also* puts `AND xmin = :seen` into the `UPDATE`, which catches a change that lands between that check and the write. The HTTP-purist alternative is `ETag: "869"` on `GET` and `If-Match: "869"` on `PATCH`, with `412 Precondition Failed`. It's the same mechanism with different headers. `ProductPatch` has `extra="forbid"`, so trying to change the `sku` is a `422`, not a silent no-op.

### 4.11 Admin

```python
@router.post("/refresh-stats")
async def refresh_stats(session: RwSession) -> dict[str, float | int]:
    await session.execute(sa.text("SET LOCAL statement_timeout = '120s'"))
    await session.execute(sa.text("SELECT refresh_seller_sales_daily()"))
    ...
```

```text
{"refreshed_rows":152915,"duration_ms":3167.2}
```

`refresh_seller_sales_daily()` is Module 15's `SECURITY DEFINER` function. The application role doesn't own the view and couldn't refresh it directly. That's the point: the app may trigger the refresh, but it can't drop or redefine the view.

### 4.12 The background processes

`python -m bazaar.relay` is Module 27's `run_relay()` as a process, with one change to the library: `run_relay()` accepts `make_publish`, a function that builds the publisher from the relay's own working connection. That lets the new `ajobs_publisher()` insert the jobs in the same transaction that marks the outbox rows published:

```python
ROUTES = {"order.placed": ["order_confirmation"]}  # bazaar.worker has the handler

total = await outbox.run_relay(
    None,
    stop,
    poll_interval=poll_interval,
    batch_size=batch_size,
    make_publish=lambda conn: outbox.ajobs_publisher(conn, ROUTES),
)
```

So a job exists if and only if its event was marked published: exactly once, for this one destination. `SIGINT`/`SIGTERM` set `stop`; the relay finishes its batch and closes its two connections. The job worker is Module 23's, unchanged.

---

## 5. Testing strategy

### 5.1 The app in-process, the database for real

```python
@asynccontextmanager
async def running(settings: Settings, **app_kw) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, **app_kw)
    async with app.router.lifespan_context(app):  # startup ... shutdown
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://bazaar") as client:
            yield client
```

`httpx.ASGITransport` calls the ASGI app directly: no server, no port, no sockets. Below the app, everything is real: the lifespan builds the three engines and runs the schema check, and requests go through the middleware, the dependencies, asyncpg, RLS and `place_order()`. Running the lifespan explicitly matters. `ASGITransport` doesn't send lifespan events itself, so without it `app.state.db` wouldn't exist. The tests use the same `create_app()` as production, with settings built from `get_settings()` and a different DSN.

### 5.2 Which database

- **Read-only tests** (listing, keyset pages, search relevance, product page, statistics) use the reference database with its realistic volume and skew. They change nothing.
- **Tests that commit** (checkout, the portal, concurrency, timeouts) get a database of their own: Module 27's `fresh_db`, a copy of the migrated template (`CREATE DATABASE ... TEMPLATE`, about 140 ms). They build exactly the rows they need with `tests.factories` (`make_shop` creates a category, a buyer, a seller and products with stock). Nothing to clean up: the database is dropped afterwards.

The two halves complement each other. The reference data finds plan and relevance problems that ten hand-made rows never would. Fresh databases make committed-data tests independent and safe to run in parallel (`pytest -n 4`).

### 5.3 What the tests prove

`tests/test_api.py` has 20 tests. Some highlights:

```python
async def test_concurrent_checkouts_never_oversell(fresh_client, fresh_conn) -> None:
    """20 buyers, 5 units, 20 checkouts in flight at once through the API."""
    shop = make_shop(fresh_conn, products=1, stock=5)
    pid = shop.products[0]
    buyers = [make_user(fresh_conn) for _ in range(20)]
    for b in buyers:
        await fill_cart(fresh_client, b, [(pid, 1)])
    responses = await asyncio.gather(
        *(fresh_client.post("/checkout", headers={**user(b), **key()}) for b in buyers)
    )
    codes = sorted(r.status_code for r in responses)
    assert codes == [201] * 5 + [409] * 15
    assert stock(fresh_conn, pid) == (5, 5)
```

The twenty requests really are concurrent. `gather` starts them together, each gets its own pooled connection (the rw pool has 10), and they meet at the inventory row lock inside `place_order()`. Unlike Module 27's tests, there's no barrier: through HTTP we can't place one at the critical point, but twenty requests for five units are enough contention to fail loudly if locking were wrong.

- **Idempotency**: a repeated key gets `200` with identical orders and stock reserved once; a changed cart with a used key still replays; another user's identical key is a new checkout. Five concurrent identical requests give `[200, 200, 200, 200, 201]`.
- **RLS isolation**: two sellers with one order each. Each sees exactly their buyer's order in `/seller/orders`, the other's product is `404`, and an unknown seller sees an empty list.
- **Optimistic locking**: a `PATCH` with the current version succeeds and changes the version; a second `PATCH` with the old one is `409` and doesn't overwrite.
- **Problem details**: `409` for `BZ001` with `product_id`, `requested` and `available`; `422` for an empty cart, a draft product, another user's address; `400` without an idempotency key; `401` without identity.
- **Search relevance**: "wireless headphones" returns 10 full-text hits whose titles contain both words, in descending score, with `<mark>` snippets. "headphnes" returns only `fuzzy` matches, all headphones. "the" returns nothing, without error.
- **Keyset pages**: three pages by cursor equal the first 60 rows of one big query; a cursor from another sort is `400`.
- **Timeouts**: a test-only router (`extra_routers=[slow]`) runs `pg_sleep`. With `SET LOCAL statement_timeout = 100` the answer is `503 timeout` with `Retry-After`; past a 300 ms request deadline it's `504`. Afterwards the pool has no connection checked out, and the next request works.
- **Startup and readiness**: a fresh database stamped back to 2701 makes startup raise `SchemaOutOfDate`; changing it after startup makes `/ready` answer `503` with the reason.
- **The background path**: a checkout's event goes through `run_relay(make_publish=ajobs_publisher)` and becomes one `order_confirmation` job with the right order id; the outbox is empty afterwards.

### 5.4 Tests at every stage of Part IV

Until this module, `tests/conftest.py` refused to run at all unless the database was at the Alembic head. Learners now run the migrations from Module 22 on (`alembic stamp 2600 && alembic upgrade head` after every `reset.sh`), so the suite normally runs in full. Someone who skipped that step, or whose database is behind after a pull, shouldn't be locked out of everything, though. Now tests that need the migrated schema carry a marker, and the session decides what to skip:

```python
def pytest_collection_modifyitems(config, items) -> None:
    state = _schema_state()  # reachable? current revision? head?
    if not state.reachable:
        pytest.exit(f"cannot connect to BAZAAR_DSN: {state.error}", returncode=2)
    if state.at_head:
        return
    for item in items:
        marker = item.get_closest_marker("migrated")
        if marker is not None:
            why = marker.kwargs.get("reason")
            reason = f"{why}: {state.skip_reason()}" if why else state.skip_reason()
            item.add_marker(pytest.mark.skip(reason=reason))
```

Files from Modules 26–28 have `pytestmark = pytest.mark.migrated`. In the Module 24–25 files, the tests that touch columns added by later revisions are marked individually. The reference ORM mapping includes `Product.slug` (2602) and `Order.idempotency_key` (2604), so loading a product or an order on a database without the migrations fails. The two runs:

```text
# reset.sh 22 + sql/2301_notify.sql (no Alembic): modules 22-25
37 passed, 110 skipped
NOTE: tests marked 'migrated' were skipped: needs the schema at Alembic head ['2801'];
the database is not under Alembic (no revision). Modules 26+: `alembic stamp 2600 &&
alembic upgrade head` after reset.sh

# alembic upgrade head: everything
146 passed, 1 skipped
```

A database that is under Alembic but behind (2701 after you pulled this module's code) also skips the migrated tests, with "the database is at ['2701']" in the reason and the note in the summary. Skipping is safer than failing hundreds of tests with `UndefinedColumn`, and louder than silently passing.

### 5.5 What is not tested here

Killing connections mid-request (exercise 9), traces (exercise 8), a real replica (exercise 7, with Module 19's lab) and load (section 7). The load test isn't a pytest test on purpose. It needs minutes, a running server with workers and a quiet machine. Its invariants are checked by the script itself.

---

## 6. Operations

### 6.1 Running it: uvicorn workers

`uvicorn bazaar.api.app:app --workers 4` starts a supervisor and four worker processes. Each imports the app, runs the lifespan (its own engines, its own schema check) and serves requests on a shared socket. One worker per CPU core is the starting point: a worker is one Python thread of execution, and more workers than cores just time-slice. In containers, the common pattern is one worker per container and the orchestrator scaling containers; the pool arithmetic below is the same, per process.

Useful flags: `--timeout-graceful-shutdown 20` (finish in-flight requests on `SIGTERM`; keep it below the orchestrator's kill timeout, 30 s in Kubernetes), `--log-level warning` plus `BAZAAR_LOG_FORMAT=json` for one JSON object per line, and `--proxy-headers` behind a load balancer.

### 6.2 Pool sizing per worker

Connections are counted per process, per engine:

```text
per worker:  rw 10 + read 10 + seller 3        = 23
4 workers:                                      = 92
relay 2 + job worker 5 + migrations/admin 1-2   ≈ 100
max_connections (course container default)      = 100   <- full
```

The defaults don't fit four workers into a default server. The load test ran with `BAZAAR_POOL_MAX_SIZE=8`: 4 × (8 + 8 + 3) = 76, plus the relay and the worker. The ways out, from Module 27, section 5.1:

- **Size from demand, not from hope.** Little's law: connections in use ≈ requests per second × time a request holds a connection. Run A of the load test (section 7) spent 130 s of statement time in 30 s at 258 requests/s, about 17 ms per request, so roughly 4 connections were busy at any moment across all workers (somewhat more counting the gaps between a transaction's statements). A pool of 8 per engine per worker is generous. Pools that are always exhausted mean connections are held too long (slow queries, transactions around slow work), more often than they mean the pool is too small.
- **PgBouncer in transaction mode** between the engines and PostgreSQL (`poolclass=NullPool` or a small pool in the app). The service is ready for it: every per-request setting is `SET LOCAL`, there are no session-level prepared statements beyond what PgBouncer 1.21+ handles (`max_prepared_statements`), and the timeouts must move to the role (`ALTER ROLE bazaar_app SET statement_timeout = '5s'`), because PgBouncer can't forward the `options` startup parameter. The relay and the worker `LISTEN`, so they bypass it.
- **Fewer engines per process.** Without a replica, the read engine could share the rw engine's pool (read-only enforced with `SET TRANSACTION READ ONLY` per transaction instead).

`/ready` shows the three pools per worker. Alert when `checked_out` stays near `size`.

### 6.3 Migrations in a deploy

The startup check makes the order explicit: **migrate, then deploy**. A deploy pipeline runs `alembic upgrade head` once (a job, not every replica at startup: ten processes racing to migrate is what Module 26's advisory lock prevents, but it's still pointless), as the migration role (`BAZAAR_MIGRATION_DSN`), and only then rolls out the new code. Revisions are written to be safe while the *old* code is still serving (expand/contract, `CONCURRENTLY`, `NOT VALID`), because during a rolling deploy both versions run at once. 2801 is a pure "expand": a new function and two new indexes, invisible to the old code. A "contract" step (dropping something the old code used) waits for the release after.

If a deployment must start new code before the migration (some platforms do), relax the startup check to "at least revision X" and make the new code tolerate the old schema. That's more code, which is why the default here is strict.

### 6.4 Monitoring

Module 17's toolkit, applied to this service:

- **Who is connected, doing what.** The engines have distinct `application_name`s, so `pg_stat_activity` separates them:

  ```sql
  SELECT application_name, state, wait_event_type, count(*)
  FROM pg_stat_activity
  WHERE datname = 'bazaar'
  GROUP BY 1, 2, 3
  ORDER BY 4 DESC;
  ```

  The `query` column carries the route tag: an `idle in transaction` row with `route='POST%20%2Fcheckout'` points straight at the code.
- **What costs the most.** `pg_stat_statements` ordered by `total_exec_time`. During the load test (section 7) it said, in order: `search_products()`, `place_order()`, the listing. That's the right order to optimize in. Exercise 1 puts this view behind `/admin/slow-queries`.
- **The slow-query log** (`log_min_duration_statement`, `auto_explain`) on the server, and the application's own slow-query warnings (`instrument_engine`, over 200 ms), both with the route tag.
- **Queues.** `outbox.pending_count()` (unpublished events: the relay is down or failing) and the jobs backlog (`SELECT count(*) FROM jobs WHERE done_at IS NULL AND run_at <= now()`). Both should hover near zero. Alert when they don't.
- **The pools**: `/ready`, or `/metrics` (exercise 2).
- **The materialized view's age.** A scheduler calls `POST /admin/refresh-stats` (better: a scheduled job on Module 10's queue). If it stops, sellers see yesterday's numbers forever.

### 6.5 Graceful shutdown in practice

A rolling deploy sends `SIGTERM` to a worker. The orchestrator also removes it from the load balancer, but not instantly: requests keep arriving for a second or two. The usual recipe is a short `preStop` sleep (so the load balancer catches up), then uvicorn's graceful shutdown (in-flight requests finish within `--timeout-graceful-shutdown`), then the lifespan disposes the pools. A checkout interrupted by `SIGKILL` anyway is rolled back by PostgreSQL. The client retries with the same idempotency key and gets either the committed result or a fresh attempt, never two orders.

---

## 7. Load testing

### 7.1 The load generator

`modules/28-capstone/examples/loadtest.py` is a small async load generator with a checklist at the end. Setup creates, with direct SQL, a seller with N "hot" products and fresh buyers with default addresses (fresh, so no existing cart rows take part). Then two crowds run for a fixed time:

- **shoppers** pick one of their own buyers, put 1–3 hot products in the cart (1–2 units each), and check out with a new `Idempotency-Key`. On `409` (sold out) they drop that product from the cart and try again, at most three times. 10% of successful checkouts are sent a second time with the same key, like a client retrying after a timeout.
- **browsers** list a random root category with a random sort (sometimes the next page too), open a product page, or search for one of 18 terms (including typos like `headphnes`).

It reports requests per second and p50/p95/p99 latency per endpoint, then checks the invariants in the database (section 7.4), and deletes everything it created.

This is a **closed-loop** generator: each virtual client sends its next request only after the previous answer. That's realistic for "N concurrent users". It also means that when the service slows down, the offered load drops with it, so throughput plateaus rather than collapsing.

**Measure your load generator first.** The first version used httpx, and the service seemed slow. A trivial benchmark of `GET /health` (no database) showed whose fault that was:

| Client, concurrency | 8 | 32 | 80 |
|---|---|---|---|
| httpx `AsyncClient` | 522 req/s | 310 req/s | 216 req/s |
| aiohttp `ClientSession` | 5,358 req/s | 6,837 req/s | 6,817 req/s |

The server handled 6,800 requests per second. httpx, a fine client for tests and ordinary calls, managed a few hundred, and *fewer* with more connections. Measured through it, every endpoint looked slow by hundreds of milliseconds that were spent in the client. The load generator uses aiohttp (in the dev dependencies since this module). The tests still use httpx, where its ASGI transport is the point.

### 7.2 Results

Four uvicorn workers with `BAZAAR_POOL_MAX_SIZE=8`, the relay, a job worker with concurrency 4, PostgreSQL 17 with default settings (128 MB `shared_buffers`), and the generator, all on one 4-vCPU machine, against the large dataset. 20 hot products.

**Run A: 8 shoppers, 8 browsers, 1,000 units per product, 30 s.**

```text
7764 requests in 30.1 s = 258 req/s
endpoint                    count   req/s   p50 ms   p95 ms   p99 ms  codes
GET /products                 851    28.2     49.3     98.5    148.8  200:851
GET /products (page 2)        449    14.9     47.5     91.6    114.6  200:449
GET /products/{id}            851    28.2     36.9     80.8    123.8  200:851
GET /search                  1023    33.9    108.9    321.9    493.9  200:1023
POST /cart/items             2953    97.9     29.6     82.5    114.2  201:2953
POST /checkout               1491    49.5     79.5    158.3    211.3  201:1491
POST /checkout (retry)        146     4.8     26.8     75.2    110.6  200:146
```

**Run B: 40 shoppers, 40 browsers, same stock, 30 s.**

```text
8273 requests in 30.5 s = 272 req/s
endpoint                    count   req/s   p50 ms   p95 ms   p99 ms  codes
GET /products                 956    31.4    169.3    590.0    883.6  200:956
GET /products (page 2)        506    16.6    170.0    545.4    823.6  200:506
GET /products/{id}            956    31.4    125.7    513.8    705.6  200:956
GET /search                  1195    39.2    511.0   1375.6   1903.3  200:1193 503:2
POST /cart/items             3004    98.6    145.3    416.1    549.5  201:3004
POST /checkout               1510    49.6    346.5   1191.8   1952.9  201:1510
POST /checkout (retry)        146     4.8    142.6    420.6    560.6  200:146
```

**Run C, a flash sale: 40 shoppers, no browsers, 5 products × 50 units, 15 s.**

```text
5481 requests in 15.6 s = 352 req/s
endpoint                    count   req/s   p50 ms   p95 ms   p99 ms  codes
DELETE /cart/items/{id}      1569   100.9     64.6    264.7    396.1  204:1569
POST /cart/items             1735   111.6     74.8    302.9    422.6  201:1735
POST /checkout               2171   139.6     92.7    305.6    456.2  201:85 409:1569 422:517
POST /checkout (retry)          6     0.4    200.2    222.1    222.6  200:6
```

### 7.3 Reading the numbers

**The machine is saturated at about 270 requests/s for this mix.** Five times more clients (A to B) bought 5% more throughput and three to four times the latency. That's Little's law in action: 80 clients ÷ 272 requests/s ≈ 294 ms per request, which is about where the medians sit. During run B, `top` showed PostgreSQL at 181% CPU, the Python processes (four API workers, the generator, relay and worker) at 165%, and Docker's port proxy at 36%: 382% of 400%. Beyond saturation, more concurrency only builds queues: in the pools, in the run queue, at row locks.

**Where the database time goes.** `pg_stat_statements`, reset before run A:

```text
 calls | mean_ms | total_s |                              q
-------+---------+---------+--------------------------------------------------------------
  1023 |   92.92 |    95.1 | SELECT * FROM search_products($1, $2, $3) /*app='bazaar-api'
  1491 |   11.54 |    17.2 | SELECT place_order($1::BIGINT, $2::JSONB, $3::BIGINT) AS pla
  5670 |    1.20 |     6.8 | SELECT id, queue, kind, payload, attempts, max_attempts FROM
   285 |   14.85 |     4.2 | WITH RECURSIVE subtree(id) AS (SELECT categories.id AS id FR
```

Search is 13% of the requests and most of the database time. `search_products()` ranks *every* full-text match before keeping 200: a query like "coffee" or "jeans" matches thousands of products, and `ts_rank_cd` and the per-candidate rating and sales lookups cost about 90 ms even on a quiet machine. In run B, two searches hit the 2-second statement timeout and were answered with `503` and `Retry-After`. That's the timeout design of section 3.8 doing its job: the request failed fast and cleanly, nothing else was affected. The next steps, in order of payoff: cache popular queries' results for a minute; precompute the business signals (rating, units sold) into a table refreshed with the statistics instead of aggregating per candidate; cap the candidate set with a cheaper first pass. Module 13, section 7.2 measures the ranking cost in detail.

`place_order()` averages 11.5 ms in run A and 203 ms in run B. The function itself didn't get slower. In run B, 40 shoppers compete for 20 inventory rows. Each checkout holds its row locks from `place_order()` until `COMMIT`, and the waits are counted in the statement's time. That's why the checkout transaction takes the cart *before* calling `place_order()` (section 3.6): every round trip between the inventory locks and the commit is paid by every buyer queued behind them. Sampling `pg_stat_activity` during a 40+40 run showed `Lock:transactionid` waits next to CPU and I/O as the top wait events. A flash sale is a contention problem first and a throughput problem second.

**The listing.** In the first load test, with the `IN (subtree)` version of section 4.7, the listing statement averaged 2.8 s per call under load and dominated `pg_stat_statements`. After the per-category merge, its variants average 7–15 ms in run A. The test suite caught none of this: the functional answer was right both times. Only realistic data and concurrency show plan problems.

### 7.4 Invariants

The numbers say how fast. The invariants say whether it was *right*, and they were checked after every run, after giving the relay and the worker time to drain. Run B's checklist:

```text
invariants
  [ok] no oversold product (reserved <= quantity)
  [ok] reserved == units in this run's orders: 4519 of 20000 units sold
  [ok] order totals == sum of their items: 1510 orders
  [ok] orders in the database == orders returned by 201 responses
  [ok] one order per (user, key) (single-seller carts)
  [ok] retried checkouts replayed the same orders: 146 retries
  [ok] carts emptied by checkout: 458 buyers
  [ok] outbox drained (relay): 0 unpublished
  [ok] one order_confirmation job per order, all done (worker): 1510 jobs, 0 open

ALL INVARIANTS HOLD
```

And the flash sale's: `250 of 250 units sold`, 85 orders, 1,569 `409`s. Every unit was sold exactly once while 40 clients fought over them, every order's total equals its items, every `201` corresponds to exactly one order and every order to one `201`, every retried request got the same orders back, every order produced exactly one confirmation job, and no event was left in the outbox. Most of these properties are enforced by the database (row locks in `place_order()`, the `CHECK (reserved <= quantity)` constraint, the unique idempotency index, the trigger-maintained totals, the outbox in the same transaction), which is why they hold under load without any special effort in Python. A load test without such checks only measures how fast the service *answers*; with them, it shows the answers are correct.

---

## 8. Where to go from here

You started this course with `SELECT 1` in `psql`. You've built a schema and normalized it, written queries that windows and recursive CTEs make short, made them fast with indexes and plans you can read, searched text, partitioned a five-million-row table, secured it row by row, looked inside pages and the WAL, operated, backed up, replicated, failed over and sharded it, and finally put a service in front of it that survives concurrency, retries and failures. Some directions from here, each building on what you have:

- **Deepen the service.** The exercises add order cancellation, reviews, rate limiting, a CSV export, replica routing with read-your-writes, tracing and chaos tests. Beyond them: real authentication (OIDC) with the user id mapped to RLS for buyers too; payments as an outbox-driven saga (authorize, capture, refund) with idempotent consumers; inventory release on cancellation and expiry of unpaid orders by the job queue.
- **Analytics.** The `events` table (Module 14) is where clicks and searches would go. Ingest them in batches with `COPY` from the API (never one `INSERT` per event), roll them up into partitioned summaries, and feed the search ranking with real click-through data. When the analytical queries outgrow the primary, logical replication (Module 19) to a separate analytics database, or a columnar store, is the next step.
- **Search.** When ranking quality or facets outgrow `search_products()`, Module 13, section 14 discusses dedicated engines. The outbox you already have is how the index would be kept in sync.
- **Scale.** Module 21 prepared the shard key, and this service never lets an order span sellers. Routing seller-scoped requests to shards by `seller_id`, keeping users and categories as reference data, is a natural evolution once one primary isn't enough.
- **Operate for real.** Put the service on Module 20's Patroni cluster and run the load test while you trigger a switchover: watch `/ready`, the retries in the logs and the invariants. Add Module 18's backups and restore drills to the deploy pipeline. Turn the monitoring of section 6.4 into dashboards and alerts.
- **Read the source.** PostgreSQL's documentation is excellent, but after this course its source is within reach too: `src/backend/access/heap/`, `src/backend/optimizer/`, the WAL. The mailing list `pgsql-hackers` is where its future is discussed, in the open.

PostgreSQL rewards understanding. Almost every problem in this course had a solution that looked simple once you knew how the database works: a partial index, a `SKIP LOCKED`, a `SET LOCAL`, a constraint instead of a check in code. Keep looking for those.

---

## Summary

- The Bazaar service is a composition of the course: Module 10's `place_order()` behind Module 27's idempotency and retries, Module 13's search as revision 2801, Module 15's RLS for the seller portal, Module 09's materialized view for statistics, keyset pagination from Module 12, and Modules 22–27's package underneath.
- One request, one transaction, provided by a dependency with `scope="function"`, so the commit happens before the response. Handlers that retry take the session factory and run `arun_in_session()`, a new transaction per attempt.
- Three engines per process: rw (primary), read (replica or primary, read-only at connect), seller (`bazaar_seller`, `SET LOCAL app.seller_id` per transaction). RLS is the filter in the portal, and the isolation test proves the policy. Materialized views have no RLS, so tenant filters there are your code's job.
- Checkout requires an `Idempotency-Key`; replays return the first answer; the cart is taken with `DELETE ... RETURNING` before the stock locks; retries cover deadlocks, serialization failures and lost connections. Reads aren't retried in the service: `503` with `Retry-After`.
- Timeouts are layered from inner to outer (statement, then request deadline, with per-endpoint `SET LOCAL`), and every error is `application/problem+json` with no internals.
- `GET /products` merges per-category top-N lists from partial indexes instead of sorting the catalogue: 128.6 ms down to 10.3 ms. Use literals, not parameters, for predicates that partial indexes depend on.
- Tests run the real app in-process with httpx's ASGI transport, against the reference data for reads and fresh template copies for writes, including concurrency, RLS, optimistic locking and timeouts. Tests needing the migrated schema are marked and skipped with a reason on earlier stages.
- Size pools per worker and per engine against `max_connections`; migrate before deploying; watch `pg_stat_statements`, `pg_stat_activity` by `application_name` and route tag, the outbox and the job backlog.
- Load-test with a generator that isn't the bottleneck, and with invariants: on 4 vCPUs the service saturates around 270 requests/s for this mix, search dominates database time, and under every load nothing was oversold, every total was right and the outbox drained.

## What's next

This is the last module of the course. The [exercises](exercises.md) extend the service in the directions section 8 describes, and the [project milestone](project.md) is the capstone checklist: when every item on it holds, Bazaar is done, and so is the course.
