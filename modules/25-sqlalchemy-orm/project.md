# Module 25: Project milestone — ORM models, sessions and services

<!-- nav:start -->
> [Theory](README.md) · [Exercises](exercises.md) · **Project** · [Solutions](solutions/README.md)  
> [← 24 SQLAlchemy Core](../24-sqlalchemy-core/README.md) · [Course home](../../README.md) · [26 Migrations with Alembic →](../26-migrations/README.md)
<!-- nav:end -->

The seller portal and the checkout need per-object operations: create a product with its stock, edit it without overwriting a colleague's change, list a seller's products, check out a cart, show a buyer's order history, accept a review only from someone who bought the product. In this milestone you map Bazaar's entities with the SQLAlchemy ORM, build session factories that apply Module 15's row-level security automatically, and write those operations as services, tested against the real database, including a test that fails if a page ever regresses into an N+1 query pattern.

**Starting state:** `./project/bin/reset.sh 25`, then `alembic stamp 2600 && alembic upgrade head` in `project/python` (the package's schema, see the README's setup), and your Module 24 package (or the reference one).

**Reference solution:** [`project/python/`](../../project/python/): `src/bazaar/orm/{models,session,services}.py`, the new exceptions in `src/bazaar/errors.py`, `mapping_only` in `src/bazaar/sa/drift.py`, the `session`/`asession` fixtures in `tests/conftest.py`, and `tests/test_orm.py`.

## Requirements

### 1. Mapped classes (`bazaar.orm.models`)

- A `Base(AsyncAttrs, DeclarativeBase)` with Module 24's naming convention and a `type_annotation_map` (`Decimal` → `numeric(12,2)`, `datetime` → `timestamptz`, `dict[str, Any]` → `JSONB`, `list[str]` → `text[]`, `str` → `text`, the three status enums → the existing enum types).
- Typed 2.0-style classes (`Mapped[...]`, `mapped_column()`) for `User`, `Seller`, `Category`, `Product`, `Inventory`, `Order`, `OrderItem`, `Payment`, `Review`, `Address`, `CartItem`, `Promotion`, with every column of the real tables, correct nullability, identity columns, server defaults, the generated `search_vector` (deferred), and foreign keys with their `ON DELETE` action.
- Relationships with `back_populates`: `Category.parent`/`children` (self-referential), `User.seller` and `Product.inventory` (one-to-one), `Order.items`/`OrderItem.order` and `OrderItem.product` (association object with a composite key), `Order.user`, `seller`, `shipping_address`, `payments`, `Product.reviews`, `promotions`, `Seller.products`, `User.addresses`, `orders`, `reviews`.
- `cascade="all, delete-orphan"` plus `passive_deletes=True` for owned children whose foreign key has `ON DELETE CASCADE` (order items, inventory, reviews, promotions, addresses).
- `Product.attributes` tracks in-place changes (`MutableDict`), `Product.tags` too (`MutableList`).
- Optimistic concurrency on `Product` through PostgreSQL's `xmin` (`version_id_col`, `version_id_generator=False`, an `xid` column type).
- `updated_at` columns and `Order.total` marked as changed by the server on update (`server_onupdate=FetchedValue()`).
- A hybrid `Inventory.available` (quantity − reserved) usable in Python and SQL; a hybrid `OrderItem.line_total`; a deferred `column_property` `Order.item_count`.
- `ActiveProduct`, mapped onto the `active_products` view (reuse the Core `Table` from `bazaar.sa.tables.view_metadata`), read-only.

### 2. Sessions (`bazaar.orm.session`)

- `BazaarSession`, a `Session` subclass with two event hooks: `after_begin` runs `set_config('app.seller_id', <id>, true)` when `session.info["seller_id"]` is set; `before_flush` raises `InvalidRequestError` for any pending change to a read-only class.
- `make_sessionmaker(engine=None, **kw)` (default `expire_on_commit=False`, `class_=BazaarSession`), `seller_session(factory, seller_id)` (a context manager: a seller-scoped session inside a transaction), and `make_async_sessionmaker(engine=None, **kw)` using `BazaarSession` as `sync_session_class`.

### 3. Services (`bazaar.orm.services`)

Every function takes a `Session` first and never commits; operations that must be atomic use `begin_nested()`. Every query states its loads and ends with `raiseload("*")`.

- `checkout(session, user_id, items, shipping_address_id=None) -> list[Order]`: calls `place_order()` (typed bind parameters, JSONB items) inside a savepoint, translates its SQLSTATEs into `bazaar.errors` exceptions, and returns the new orders with their items.
- `order_history(session, user_id, limit=20)` and `order_history_async(asession, user_id, limit=20)`: the newest orders with seller, items and the items' products, in **two statements** whatever the limit, from one shared statement builder (`order_history_query()`).
- Seller product management, using `session.info["seller_id"]` (`PermissionError` without it) and an explicit seller filter on top of RLS: `create_product(...)` (with its inventory row; flushes and returns the product), `get_own_product()`, `update_product(session, product_id, *, expected_version=None, **changes)` (only editable fields; `NotFound` for others' or missing products; `ConcurrentUpdate` when `expected_version` doesn't match `xmin` or when the flush hits a `StaleDataError`), `set_stock()`, and `list_products(..., status=None, after_id=None, limit=50)` with keyset pagination.
- `submit_review(session, user_id, product_id, rating, title=None, body=None) -> Review`: validates rating (1–5) and lengths (`InvalidReview`), requires a delivered order of the user containing the product (`ReviewNotAllowed`), inserts in a savepoint, and maps the unique violation to `DuplicateReview`.
- Add `NotFound`, `ConcurrentUpdate`, `InvalidReview`, `ReviewNotAllowed` and `DuplicateReview` to `bazaar.errors`.

### 4. Drift check for the mapping

Extend `bazaar.sa.drift.compare()` with `mapping_only=True`: check the mapped tables' columns, types, nullability, identity/generated columns, defaults, primary and foreign keys, but ignore unmapped tables, indexes and CHECK constraints; skip system columns such as `xmin`.

### 5. Tests (`tests/test_orm.py`)

Fixtures: `session`, a `BazaarSession` bound to the rolled-back `sa_conn` with `join_transaction_mode="create_savepoint"`; `asession`, the async equivalent on a per-test async engine.

- The mapping matches the database (`compare(..., mapping_only=True) == []`).
- Types and relationships: Laptops → Computers → Electronics; `Decimal`, enum, dict; `Inventory.available` on an instance.
- `Inventory.available` and `Order.item_count` work in SQL (compare with plain SQL / with `len(order.items)`).
- An in-place change of `Product.attributes` is flushed and reloaded.
- Flushing a change to `ActiveProduct` raises before any SQL.
- Checkout: one order with the right item, total computed by the trigger, availability now 0; `InsufficientStock` with requested/available numbers, after which the same session can still check out.
- **N+1 guard:** `order_history(session, 8, limit=20)` plus touching sellers, items and products sends exactly **2** statements; the same loop with lazy loading sends more than 40.
- `raiseload` makes an unplanned relationship access raise.
- Optimistic concurrency with two sessions and committed test data (cleaned up): the second writer gets `ConcurrentUpdate`, and so does a stale `expected_version`; the first writer's price survives.
- RLS as the `bazaar_seller` role: a seller counts exactly their own products, can't `get()` another seller's product, gets `NotFound` updating it, can create a product with stock, and a direct ORM insert for another seller fails with a row-level security error. Seller services without a seller raise `PermissionError`.
- Reviews: success (title trimmed), `DuplicateReview` with the first review intact, `InvalidReview` for rating 6 and a 5,001-character body, `ReviewNotAllowed` for a user who never bought the product.
- Async: `order_history_async()` returns fully loaded orders; a lazy load raises `MissingGreenlet`; `awaitable_attrs` works.

Update `project/python/README.md`: the module table and the layout.

## Acceptance checks

```bash
cd project/python && source .venv/bin/activate
export BAZAAR_DSN=postgresql://postgres:postgres@localhost:5432/bazaar
ruff check src tests && ruff format --check src tests
pytest tests/test_orm.py -v
pytest                                    # everything, modules 22-25
```

Expected: ruff passes; the 17 tests of `test_orm.py` pass; with Modules 22–24's, that's 52 tests (`test_repo`, `test_aio`, `test_worker`, `test_sa_core`, `test_orm`), passing in a few seconds. (The finished reference package has more, from Modules 26–28.)

A quick look at the services:

```bash
python - <<'EOF'
from bazaar.orm import services
from bazaar.orm.session import make_sessionmaker
from bazaar.sa.engine import count_statements
Session = make_sessionmaker()
with Session() as s:
    s.connection()
    with count_statements(s.get_bind()) as stmts:
        orders = services.order_history(s, 8, limit=5)
        for o in orders:
            print(o.id, o.seller.shop_name, [(i.product.sku, i.quantity) for i in o.items])
    print(len(stmts), "statements")
    s.rollback()
EOF
```

Expected:

```text
495378 Royal Outlet 196 [('S196-198986', 1)]
481208 Cozy Store 4 [('S4-047195', 1), ('S4-081818', 1)]
480393 Silver Bazaar 984 [('S984-119862', 1)]
480242 Royal Outlet 42 [('S42-022849', 1), ('S42-054078', 2)]
479586 Little Workshop 2 [('S2-001085', 1), ('S2-002166', 4), ('S2-005175', 2), ('S2-031479', 1), ('S2-071202', 1)]
2 statements
```
