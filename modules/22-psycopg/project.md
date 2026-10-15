# Module 22: Project milestone — the Bazaar data-access layer

<!-- nav:start -->
> [Theory](README.md) · [Exercises](exercises.md) · **Project** · [Solutions](solutions/README.md)  
> [← 21 Sharding](../21-sharding/README.md) · [Course home](../../README.md) · [23 Async Python: psycopg async and asyncpg →](../23-async-python/README.md)
<!-- nav:end -->

Bazaar is about to get its first piece of Python. Before anyone writes web handlers, the team wants one module through which **all** database access goes: plain SQL, parameterized, typed results, business errors as Python exceptions, and tests that run against the real database. This is that module. Modules 23 to 28 build on it: an async twin and a job worker (23), SQLAlchemy (24–25), Alembic migrations (26), production hardening (27), and the FastAPI service (28).

**Starting state:** `./project/bin/reset.sh 22`, and the virtual environment and the `alembic` line from the module's *Before you start* section.

**Reference solution:** [`project/python/`](../../project/python/), in particular `src/bazaar/{config,db,models,errors,repo}.py` and `tests/test_repo.py`. Try it yourself first: create your own package (for example in `workspace/python/`) with the same layout, or delete the function bodies in a copy of the reference and rewrite them.

## Requirements

### 1. Package skeleton

- A `pyproject.toml` for a package named `bazaar` (src layout), requiring Python 3.12+, with runtime dependency `psycopg[binary,pool]>=3.2` and dev dependencies `pytest` and `ruff`. Enable ruff's `S608` rule (SQL built by string formatting).
- `bazaar.config`: a frozen `Settings` dataclass read from the environment when it is created (not at import time): `BAZAAR_DSN` (default `postgresql://postgres:postgres@localhost:5432/bazaar`), `BAZAAR_APP_NAME` (default `bazaar`), pool sizes, and a statement timeout in milliseconds (default 5000).

### 2. Connections (`bazaar.db`)

- `connect(settings=None, **overrides) -> psycopg.Connection`: a connection with `application_name`, `connect_timeout=5` and `statement_timeout` set as a startup option.
- `configure(conn)`: registers the `order_status` and `product_status` enums so that they load as Python `StrEnum`s (`OrderStatus`, `ProductStatus` in `bazaar.models`) and **leaves the connection idle**. It must still work if the enum types don't exist.
- `create_pool(settings=None) -> ConnectionPool`: one pool per process, using `configure`, a connection check, and opened explicitly with `wait=True`.
- `seller_scope(conn, seller_id)`: a context manager that runs a block in a transaction with `app.seller_id` set **transaction-locally**.

### 3. Models and errors

- `bazaar.models`: dataclasses `Product` (including `available = quantity − reserved`, or `None` without an inventory row), `ProductHit`, `OrderSummary`, `OrderPage(items, next_cursor)`, `NewProduct`, `ImportResult`; opaque keyset cursors (`encode_cursor`/`decode_cursor`).
- `bazaar.errors`: `BazaarError` and subclasses `InsufficientStock` (with `product_id`, `requested`, `available` parsed from the error), `ProductUnavailable`, `UserInactive`, `AddressMismatch`, `InvalidOrder`; and `translate(exc)`, which maps SQLSTATEs `BZ001`–`BZ004` and `22023` to them and returns any other exception unchanged.

### 4. The repository (`bazaar.repo`)

All functions take a connection first and **never commit**. Functions that must be atomic use `conn.transaction()`.

| Function | Behavior |
|---|---|
| `get_product(conn, product_id) -> Product \| None` | product with its available stock |
| `search_products(conn, query, *, limit=20, category_id=None, max_price=None) -> list[ProductHit]` | full-text search (web-search syntax) over **active** products, ranked; optional filters composed with `psycopg.sql`, not `IS NULL OR` tricks |
| `list_orders_for_user(conn, user_id, *, limit=20, after=None) -> OrderPage` | newest first, with item counts; **keyset** pagination on `(created_at, id)`; `next_cursor` is `None` on the last page |
| `place_order(conn, user_id, items, shipping_address_id=None) -> list[int]` | calls the database's `place_order()`; `items` is `[(product_id, quantity), ...]`; raises the domain exceptions with the original error as `__cause__`; on failure nothing is changed and the connection stays usable |
| `import_products(conn, products: Iterable[NewProduct]) -> ImportResult` | upsert keyed by `(seller_id, sku)` via **COPY** into a temporary staging table, plus inventory quantities; reports inserted versus updated; atomic; can be called twice in the same outer transaction |
| `enqueue_job(conn, kind, payload=None, *, queue="default") -> int` | insert into `jobs` |

### 5. Tests

`pytest` tests against the database, each running in a transaction that is rolled back (`force_rollback=True`), creating their own users, sellers and products with small factory functions. At minimum:

- type adaptation of `get_product` (`Decimal`, enum, aware `datetime`, `dict`, `list`);
- search filters, and that drafts are never found; an injection attempt in the search string returns nothing and harms nothing;
- keyset pagination over orders with equal `created_at` values: no duplicates, no gaps, correct order, `None` cursor at the end;
- `place_order` splitting a cart across two sellers with correct totals; `InsufficientStock` with the right numbers, nothing reserved afterwards, and the connection still usable; each other error code mapped to its exception;
- `import_products` inserting 1,000 rows and then updating 10 plus inserting 5; values with tabs, quotes and backslashes survive COPY; a failing row makes the whole import roll back;
- `seller_scope` under the `bazaar_seller` role sees only that seller's products.

## Acceptance checks

```bash
cd project/python && source .venv/bin/activate
export BAZAAR_DSN=postgresql://postgres:postgres@localhost:5432/bazaar
ruff check src tests
pytest tests/test_repo.py -v
```

Expected: ruff reports no errors, and all tests pass (the reference has 16 tests in `test_repo.py`, running in under a second).

A smoke test against the large dataset:

```python
from bazaar.db import connect
from bazaar import repo

with connect() as conn:
    print(repo.get_product(conn, 1).status)                              # ProductStatus.DRAFT
    print([h.id for h in repo.search_products(conn, "wireless headphones", limit=3)])
    page = repo.list_orders_for_user(conn, 8, limit=3)
    print([o.id for o in page.items], page.next_cursor is not None)
```

```text
draft
[110124, 127587, 134112]
[495378, 481208, 480393] True
```

(`print()` of a `StrEnum` member shows its value.) And from psql, while the pool from `create_pool()` is open in a Python shell, check that your connections identify themselves:

```sql
SELECT application_name, state, count(*) FROM pg_stat_activity WHERE datname = 'bazaar' GROUP BY 1, 2;
```

The pool's connections appear as `bazaar` / `idle`, with `min_size` connections (2 by default).
