# Module 24 project: the Core layer, a drift test, and two reports

Finance wants two reports that the plain-SQL repository makes painful to write: a **monthly statement per seller** (every order with its units, payments and refunds, plus totals) and a **revenue roll-up over the category tree** (every category with its own revenue and the revenue of everything below it). Both must run as single statements inside the caller's transaction. In this milestone you add SQLAlchemy Core to the `bazaar` package: an engine built from the existing settings, a description of the whole Bazaar schema as `Table` metadata, a test that fails the moment that description drifts from the real database, and the two reports written with the expression language.

**Starting state:** `./project/bin/reset.sh 24` plus `project/python/sql/2301_notify.sql`, and your Module 23 package (or the reference one).

**Reference solution:** [`project/python/`](../../project/python/): `src/bazaar/sa/{engine,tables,drift,reports}.py`, `tests/test_sa_core.py`, the SQLAlchemy fixtures in `tests/conftest.py`, and `PaymentStatus` in `src/bazaar/models.py`.

## Requirements

### 1. Dependencies and fixtures

- Add `sqlalchemy[asyncio]>=2.0.30` to the runtime dependencies (the extra brings `greenlet` for the async engine).
- Test fixtures: a session-scoped `sa_engine`; `sa_conn`, a Core connection inside a transaction that is rolled back after the test; `async_engine`, created per test (an async pool belongs to one event loop).

### 2. Engines (`bazaar.sa.engine`)

- `sa_url(dsn, driver="psycopg")` converts `BAZAAR_DSN` into a SQLAlchemy URL for `psycopg` or `asyncpg`, and raises `ValueError` for a libpq `key=value` string.
- `create_engine(settings=None, **overrides)` and `create_async_engine(settings=None, driver="asyncpg", **overrides)` use the same settings as `bazaar.db`: `application_name`, `statement_timeout` as a startup option (asyncpg: `server_settings`), `connect_timeout`; a pool of `pool_max_size` connections with **no overflow**, a pool timeout, `pool_pre_ping`, `pool_recycle`. Keyword overrides win; `poolclass=NullPool` must work (drop the pool-size arguments it doesn't accept).
- `count_statements(engine)`: a context manager that records every statement sent through a sync or async engine (a `before_cursor_execute` listener, removed on exit).

### 3. Tables (`bazaar.sa.tables`)

- One `MetaData` with a naming convention that reproduces PostgreSQL's default names (`<table>_pkey`, `<table>_<col>_fkey`, `<table>_<cols>_key`, `<table>_<cols>_idx`, `<table>_<name>` for checks).
- A `Table` for each of the 16 tables of the schema at state 24: users, sellers, categories, products, inventory, orders, order_items, addresses, payments, reviews, cart_items, product_price_history, promotions, audit_log, jobs, events (partitioned by range; partitions are managed in SQL, not described). Every column with its PostgreSQL type, nullability, identity, server default and generated expression; every primary key, foreign key (with `ON DELETE`), unique and check constraint; every index, including partial, GIN, operator-class and expression indexes, and the exclusion constraint on promotions.
- The existing enum types and the `email_address` domain are used with `create_type=False`. The enum columns return the Python enums of `bazaar.models` (add `PaymentStatus`).
- A separate `view_metadata` describing the `active_products` view and the `seller_sales_daily` materialized view, so that `metadata.create_all()` never creates them.

### 4. Drift detection (`bazaar.sa.drift`)

`compare(conn, metadata, view_metadata=None) -> list[str]` returns human-readable differences between the metadata and the live schema, found through the Inspector: missing and extra tables (ignoring partitions), missing and extra columns, column types (compare compiled types), nullability, identity, generated columns, presence of server defaults, primary keys, foreign keys with their `ON DELETE` action, unique and check constraint names, index names with uniqueness (counting the indexes behind EXCLUDE constraints); for views, columns and types. (Module 25 adds a `mapping_only` mode; you may add it now.)

### 5. Reports (`bazaar.sa.reports`)

All functions take a `Connection` first and never commit. Days are UTC days; a **sale** is an order with status paid, shipped or delivered (the rule of `seller_sales_daily`), exposed as reusable pieces (`SALE_STATUSES`, `is_sale()`).

- `seller_statement(conn, seller_id, start, end) -> SellerStatement | None` for orders created in `[start, end)`: one `StatementLine` per order (order id, created_at, status, units, total, succeeded payments, succeeded refunds as a positive number), oldest first, plus `orders`, `sales` (sum of totals of sales), `paid`, `refunded` and a `net_cash` property. `None` for an unknown seller. The lines come from **one** statement (`seller_statement_query()`, reusable from async code) that does not multiply rows across the two one-to-many joins and does not aggregate the whole `order_items` table to report on one seller's month.
- `category_revenue_rollup(conn, start, end) -> list[CategoryRevenue]`: every category (also those without sales) with `depth`, `path` (`'Electronics > Computers > Laptops'`), `own_revenue`, `revenue` (own plus all descendants) and `units`, ordered by path, in **one** statement built from reusable CTE functions (`category_tree_cte()`, `category_closure_cte()`).

### 6. Tests (`tests/test_sa_core.py`)

- The metadata matches the database: `compare(...) == []`.
- The comparison detects drift: inside the rolled-back test transaction, change a column type, a nullability, drop an index, add a column and a table, and assert the exact list of complaints.
- `sa_url()` handles escaped passwords, ports and query parameters, and rejects key=value strings.
- An engine connection reports the configured `application_name` and `statement_timeout`; an async engine returns enum columns as Python enums.
- The seller statement agrees with independent plain SQL (order count, refunds) and with `seller_sales_daily` (sales); totals are `Decimal`; lines are in time order; an unknown seller gives `None`.
- The roll-up: one row per category; every node equals its own revenue plus its children's; the roots add up to the grand total computed with plain SQL.
- Statement counts (`count_statements`): the roll-up is one statement, a seller statement two (the shop name, then all lines).

## Acceptance checks

```bash
cd project/python && source .venv/bin/activate
export BAZAAR_DSN=postgresql://postgres:postgres@localhost:5432/bazaar
ruff check src tests && ruff format --check src tests
pytest tests/test_sa_core.py -v
pytest                                   # the whole suite: modules 22-24 still pass
```

Expected: ruff passes; the 10 tests of `test_sa_core.py` pass, and so do the 25 tests of Modules 22–23.

A quick look at the reports:

```bash
python - <<'EOF'
from datetime import date
from bazaar.sa.engine import create_engine
from bazaar.sa import reports
with create_engine().connect() as conn:
    st = reports.seller_statement(conn, 42, date(2025, 11, 1), date(2025, 12, 1))
    print(st.shop_name, st.orders, st.sales, st.paid, st.refunded, st.net_cash)
    for r in reports.category_revenue_rollup(conn, date(2025, 1, 1), date(2026, 1, 1))[:4]:
        print(f"{'  ' * r.depth}{r.name:<{28 - 2 * r.depth}} {r.own_revenue:>12} {r.revenue:>12} {r.units:>7}")
EOF
```

Expected:

```text
Royal Outlet 42 79 30730.18 32096.04 1365.86 30730.18
Beauty                                  0   2029675.70   31730
  Fragrance                    1401302.65   1401302.65   15935
  Skincare                      628373.05    628373.05   15795
Books                                   0   3015496.52  153148
```

The seller statement for seller 1 (3,705 orders in November 2025) should take well under 100 ms; if it takes most of a second, look at its plan (`EXPLAIN ANALYZE` of `str(seller_statement_query(...).compile(engine))` with the parameters filled in).
