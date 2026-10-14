# Module 28 project: the Bazaar service (capstone)

<!-- nav:start -->
> [Theory](README.md) · [Exercises](exercises.md) · **Project** · [Solutions](solutions/README.md)  
> [← 27 Production Python and Postgres](../27-python-production/README.md) · [Course home](../../README.md)
<!-- nav:end -->

Bazaar goes online. The storefront, the mobile app and the seller portal need an HTTP API on top of everything the database and the Python package already do: browse and search the catalogue, fill a cart, check out exactly once even when the network misbehaves, let sellers see their own orders (and only theirs), edit their products without overwriting each other, and see their sales. Behind it, order events must reach the job queue, and the whole thing must stay correct when many buyers want the last units at the same moment. This milestone is the capstone: when its checklist holds, Bazaar is done.

**Starting state:** `./project/bin/reset.sh 28`, `alembic stamp 2600 && alembic upgrade 2701` (your Module 27 milestone), and your Module 27 package (or the reference one).

**Reference solution:** [`project/python/`](../../project/python/):

- `src/bazaar/api/` (`app.py`, `db.py`, `errors.py`, `middleware.py`, `schemas.py`, `routes/{health,catalog,shop,seller,admin}.py`);
- `src/bazaar/relay.py`;
- `migrations/versions/2801_search_products.py` and its two indexes in `src/bazaar/sa/tables.py`;
- the additions to `config.py` (seller DSN, request timeout, admin token, schema check), `sa/engine.py` (`create_async_read_engine`), `outbox.py` (`ajobs_publisher`, `run_relay(make_publish=)`) and `orm/models.py` (`XID` for asyncpg);
- `tests/test_api.py`, and the `migrated` marker in `tests/conftest.py`;
- the load generator [`examples/loadtest.py`](examples/loadtest.py).

## Requirements

### 1. Schema (revision 2801)

- Module 13's `search_products(q, category_id, max_results)` with `immutable_unaccent(text)` and the GIN trigram index `products_title_unaccent_trgm_idx` on `immutable_unaccent(title)`, built `CONCURRENTLY`. The function body is embedded in the revision.
- `products_active_category_id_id_idx` on `(category_id, id) WHERE status = 'active'`, built `CONCURRENTLY`.
- Runs as `bazaar_owner` (so `bazaar_app` gets `EXECUTE` through the default privileges), downgrades cleanly, and is described in `bazaar.sa.tables` so that autogenerate sees no difference.

### 2. The service

- `create_app(settings=None, *, extra_routers=(), **fastapi_kwargs)` and a module-level `app` for `uvicorn bazaar.api.app:app`. Settings come from the environment at startup, not at import.
- **Lifespan:** three async engines per process, created at startup: rw; read (`BAZAAR_REPLICA_DSN` or the primary, `default_transaction_read_only=on`); seller (as `bazaar_seller`, `BAZAAR_SELLER_DSN`). All instrumented (timings, slow-query log) and tagged (sqlcommenter: app and route template only). `check_schema()` before serving (switchable with `BAZAAR_CHECK_SCHEMA=0`); engines disposed at shutdown.
- **Transactions:** one per request through dependencies with `scope="function"` (committed before the response is sent); checkout through `arun_in_session()` with a retry policy (idempotent: it has a key); the seller session sets `app.seller_id` with `SET LOCAL` semantics.
- **Endpoints** as in the module's section 1.1: `GET /health`, `GET /ready`, `GET /products` (category subtree, price range, JSONB attribute filter, in-stock, sorts `newest`/`price_asc`/`price_desc`, keyset cursors bound to their sort), `GET /products/{id}`, `GET /search`, the cart (`POST /cart/items`, `DELETE /cart/items/{id}`, `GET /cart`), `POST /checkout` (`Idempotency-Key` required, `201` new, `200` + `Idempotent-Replayed: true` replay), `GET /users/{id}/orders` (keyset, own orders only), `GET /seller/orders` (RLS, no explicit seller filter), `GET /seller/products[/{id}]` (with `version`), `PATCH /seller/products/{id}` (`version` required, `409` on conflict, unknown fields `422`), `GET /seller/stats` (materialized view, explicit seller filter, read engine), `POST /admin/refresh-stats` (`X-Admin-Token`, its own `statement_timeout`).
- **Errors:** `application/problem+json` for everything; the domain table of section 3.9 (`BZ001` → `409` with `product_id`, `requested`, `available`); transient database errors → `503` + `Retry-After`; no internals in `500` bodies; `X-Request-ID` on every response and in problem bodies.
- **Timeouts:** request deadline (`BAZAAR_REQUEST_TIMEOUT`, `504`), connection `statement_timeout` (`503`), `SET LOCAL` overrides for search (tighter) and the refresh (looser).
- **Background:** `python -m bazaar.relay` publishes `order.placed` events into the jobs table in the relay's own transaction (exactly once); `python -m bazaar.worker` processes them.

### 3. Tests

- `tests/test_api.py` with httpx + ASGI transport and the real lifespan; read-only tests on the reference database, committing tests on `fresh_db`. At least: health/readiness/startup schema check; listing filters, subtree and keyset (pages equal one big query; foreign cursor rejected); attribute filter; product page; search relevance smoke test (full text, fuzzy fallback, stop words); cart and checkout (split per seller, default address, cart emptied, outbox events, history); idempotent replay (sequential and 5 concurrent); `409` for insufficient stock with nothing reserved; request errors (`400`, `401`, `422`s); **concurrent checkout oversell test** (20 buyers, 5 units); **RLS isolation** (two sellers); **optimistic lock conflict**; statistics before and after a refresh; outbox → relay → job; statement timeout `503` and deadline `504`.
- `tests/conftest.py`: tests that need the migrated schema are marked `migrated` and skipped, with the reason, when the database isn't at head, so the Modules 22–25 tests run on `reset.sh 22` (+ `sql/2301_notify.sql`).

## Acceptance checks

From `project/python`, with `BAZAAR_DSN` set:

1. **Migrations.**

   ```bash
   alembic upgrade head && alembic current          # 2801 (head)
   ```

   ```sql
   SELECT count(*) FROM search_products('wireless headphones');                -- 20
   SELECT matched_by, count(*) FROM search_products('headphnes') GROUP BY 1;   -- fuzzy | 20
   ```

2. **The whole suite is green, and lint is clean.**

   ```bash
   pytest -n 4                                      # 146 passed, 1 skipped
   ruff check src tests migrations && ruff format --check src tests migrations
   ```

3. **Earlier stages still work.** On `./project/bin/reset.sh 22` plus `sql/2301_notify.sql` (no Alembic): `pytest -n 4` gives `37 passed, 110 skipped`, ending with the note that `migrated` tests were skipped and why.

4. **It runs.** `uvicorn bazaar.api.app:app --workers 4`, `python -m bazaar.relay` and `python -m bazaar.worker` start; `curl localhost:8000/ready` returns `200` with `"ok": true` and `"schema": ["2801"]`; `/docs` lists every endpoint. With `BAZAAR_LOG_FORMAT=json`, stopping uvicorn with Ctrl-C logs `bazaar api stopped (pid ...)` for every worker, and afterwards `pg_stat_activity` has no connections whose `application_name` starts with `bazaar` (the engines were disposed).

5. **Load test invariants hold.** With the three processes running (`BAZAAR_POOL_MAX_SIZE=8`):

   ```bash
   python ../../modules/28-capstone/examples/loadtest.py --duration 30 --shoppers 40 --browsers 40 --stock 1000
   python ../../modules/28-capstone/examples/loadtest.py --duration 15 --shoppers 40 --browsers 0 --products 5 --stock 50
   ```

   Both end with `ALL INVARIANTS HOLD`: no product oversold; `reserved` equals the units of the run's orders (in the second run, exactly `250 of 250 units sold`); every order's total equals its items; the orders in the database are exactly those of the `201` responses; retried checkouts replayed the same orders; carts emptied; the outbox drained; one finished `order_confirmation` job per order. Record your throughput and p50/p95/p99 next to the module's numbers (section 7.2), and explain any large difference.

6. **The deliberate choices are documented** in your code: why the seller list has no `WHERE seller_id`, why `/seller/stats` does, why reads aren't retried, why checkout requires a key, and why the listing uses a literal for `status = 'active'`.
