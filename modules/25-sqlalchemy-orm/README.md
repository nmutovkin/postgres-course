# Module 25: SQLAlchemy ORM

<!-- nav:start -->
> **Theory** · [Exercises](exercises.md) · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 24 SQLAlchemy Core](../24-sqlalchemy-core/README.md) · [Course home](../../README.md) · [26 Migrations with Alembic →](../26-migrations/README.md)
<!-- nav:end -->

The seller portal needs screens, not reports: "my products" with their stock, "edit this product", "my latest orders with their items and buyers", "ship this order". Each screen reads a small graph of related rows, changes a few of them, and saves. Writing that with Core (Module 24) means hand-building every join, mapping rows to objects, tracking which fields changed, and ordering the `INSERT`s so that foreign keys are satisfied. The **ORM** does that bookkeeping: Python classes mapped to Bazaar's tables, relationships you can navigate (`order.items[0].product.seller`), a `Session` that remembers every object it loaded and writes back exactly what changed, in the right order, in one transaction. It also makes it dangerously easy to send 400 queries to render one page. This module teaches the ORM as a PostgreSQL user should learn it: what SQL each feature produces, when it produces it, how to keep the query count under control, and when to put the ORM aside and write the query yourself.

**What you'll learn**

- The ORM's core ideas: the **identity map**, the **unit of work**, and the `Session` as a transaction-scoped workspace.
- 2.0-style typed declarative mapping (`DeclarativeBase`, `Mapped[]`, `mapped_column()`, `type_annotation_map`) for all of Bazaar's entities: a self-referential category tree, a one-to-one inventory, an association object with a composite key, enums, JSONB, arrays, ranges.
- Relationships: `relationship()`, `back_populates`, `foreign_keys`, `remote_side`, cascades (`delete-orphan`), `passive_deletes` to let `ON DELETE CASCADE` work, and the `lazy` options.
- The `Session` lifecycle: `sessionmaker`, `Session.begin()`, object states (transient, pending, persistent, detached, expired), flush versus commit, `expire_on_commit`, and autoflush surprises.
- Querying in 2.0 style: `select(Model)`, `session.scalars()`/`execute()`/`get()`, joins along relationships, `any()`/`has()`, `contains_eager()`.
- The **N+1 problem**, demonstrated and counted, and the loading strategies that fix it: `selectinload`, `joinedload`, `subqueryload`, `raiseload` as a guard, and which default to choose.
- Bulk operations in 2.0: `insert(Model)` with lists of dicts, bulk update by primary key, ORM-enabled `UPDATE`/`DELETE` with `synchronize_session`.
- Optimistic concurrency with `version_id_col` (using PostgreSQL's `xmin`), hybrid properties, `column_property`, server-computed values (`FetchedValue`, `Computed`, `Identity`), mapping a view read-only, calling `place_order()`.
- Row-level security (Module 15) with the ORM: `SET LOCAL app.seller_id` in a session event.
- The async ORM: `AsyncSession`, `async_sessionmaker`, the greenlet underneath, why lazy loading raises `MissingGreenlet`, and how to design for it.
- Testing with a session joined to an outer transaction (`join_transaction_mode="create_savepoint"`), and an honest account of when to drop down to Core or raw SQL.

**Before you start**

```bash
./project/bin/reset.sh 25                                     # same database as module 24
cd project/python && source .venv/bin/activate
export BAZAAR_DSN=postgresql://postgres:postgres@localhost:5432/bazaar
alembic stamp 2600 && alembic upgrade head                    # the package's schema
```

The reference package always matches its latest migrations, so bring the database to its schema right after `reset.sh` with the `alembic` line above (about 30 s); Module 26 explains what this does.

You need Module 24's package (`bazaar.sa`: engine, tables, `count_statements`). The examples are in [`examples/`](examples/); run them from `project/python`. Outputs come from SQLAlchemy 2.1.4 (2.0-style API), psycopg 3.3.6 and asyncpg 0.32.0 against PostgreSQL 17.11. Ids of rows that examples insert and roll back vary between runs, because sequences advance even when transactions roll back (Module 10).

---

<!-- toc:start -->
**Contents**

- [1. What an ORM does](#1-what-an-orm-does)
  - [1.1 Three ideas](#11-three-ideas)
  - [1.2 The Session is a transaction-scoped workspace](#12-the-session-is-a-transaction-scoped-workspace)
- [2. Declarative mapping, 2.0 style](#2-declarative-mapping-20-style)
  - [2.1 The base class and typed columns](#21-the-base-class-and-typed-columns)
  - [2.2 Bazaar's entities](#22-bazaars-entities)
  - [2.3 Enums, JSONB, arrays and ranges](#23-enums-jsonb-arrays-and-ranges)
- [3. Sessions](#3-sessions)
  - [3.1 Creating sessions](#31-creating-sessions)
  - [3.2 Object states](#32-object-states)
  - [3.3 Flush versus commit](#33-flush-versus-commit)
  - [3.4 `expire_on_commit`](#34-expire_on_commit)
  - [3.5 Autoflush: helpful, until it isn't](#35-autoflush-helpful-until-it-isnt)
  - [3.6 The identity map in practice](#36-the-identity-map-in-practice)
- [4. Querying, 2.0 style](#4-querying-20-style)
  - [4.1 Joins along relationships](#41-joins-along-relationships)
  - [4.2 `any()` and `has()`](#42-any-and-has)
  - [4.3 `contains_eager()`: use your join to fill a collection](#43-contains_eager-use-your-join-to-fill-a-collection)
- [5. Relationships](#5-relationships)
  - [5.1 `relationship()` and `back_populates`](#51-relationship-and-back_populates)
  - [5.2 The association object](#52-the-association-object)
  - [5.3 A self-referential tree](#53-a-self-referential-tree)
  - [5.4 `foreign_keys`: when there are two paths](#54-foreign_keys-when-there-are-two-paths)
  - [5.5 Cascades](#55-cascades)
  - [5.6 `passive_deletes`: let `ON DELETE CASCADE` do it](#56-passive_deletes-let-on-delete-cascade-do-it)
  - [5.7 `lazy`: when a relationship is loaded](#57-lazy-when-a-relationship-is-loaded)
- [6. The N+1 problem](#6-the-n1-problem)
  - [6.1 What it looks like](#61-what-it-looks-like)
  - [6.2 Seeing it](#62-seeing-it)
- [7. Loading strategies](#7-loading-strategies)
  - [7.1 The options](#71-the-options)
  - [7.2 `raiseload`: make the N+1 impossible](#72-raiseload-make-the-n1-impossible)
  - [7.3 Choosing](#73-choosing)
  - [7.4 The right default in the mapping](#74-the-right-default-in-the-mapping)
- [8. Writing: the unit of work and bulk operations](#8-writing-the-unit-of-work-and-bulk-operations)
  - [8.1 What the unit of work is good at](#81-what-the-unit-of-work-is-good-at)
  - [8.2 Bulk INSERT with `insert(Model)`](#82-bulk-insert-with-insertmodel)
  - [8.3 Bulk UPDATE by primary key](#83-bulk-update-by-primary-key)
  - [8.4 ORM-enabled `UPDATE` and `DELETE` with `WHERE`](#84-orm-enabled-update-and-delete-with-where)
- [9. Optimistic concurrency with `version_id_col`](#9-optimistic-concurrency-with-version_id_col)
- [10. Values the database computes](#10-values-the-database-computes)
  - [10.1 Identity, defaults, generated columns, triggers](#101-identity-defaults-generated-columns-triggers)
  - [10.2 Hybrid properties](#102-hybrid-properties)
  - [10.3 `column_property`](#103-column_property)
- [11. Views and database functions](#11-views-and-database-functions)
  - [11.1 A view, read-only](#111-a-view-read-only)
  - [11.2 Calling `place_order()`](#112-calling-place_order)
- [12. Row-level security with the ORM](#12-row-level-security-with-the-orm)
- [13. The async ORM](#13-the-async-orm)
  - [13.1 `AsyncSession`](#131-asyncsession)
  - [13.2 The greenlet, and why lazy loading raises](#132-the-greenlet-and-why-lazy-loading-raises)
  - [13.3 Designing for it](#133-designing-for-it)
- [14. Testing with the ORM](#14-testing-with-the-orm)
- [15. When to drop to Core or raw SQL](#15-when-to-drop-to-core-or-raw-sql)
- [16. The Bazaar ORM layer](#16-the-bazaar-orm-layer)
- [Summary](#summary)
- [What's next](#whats-next)

<!-- toc:end -->

## 1. What an ORM does

### 1.1 Three ideas

An **object-relational mapper** connects classes to tables. SQLAlchemy's ORM rests on three ideas, and every surprise you will meet comes from one of them.

1. **Mapping.** A class describes a table: `Product.price` is both a Python attribute holding a `Decimal` and a SQL column expression you can use in `select()`. Relationships describe how rows connect: `Order.items` is a list of `OrderItem` objects, loaded through the `order_items.order_id` foreign key.
2. **The identity map.** Within one `Session`, each database row is represented by **exactly one** Python object, keyed by `(class, primary key)`. Load product 20 twice, through two different queries, and you get the same object both times. That makes object identity meaningful (`a is b`), lets the session know which object to update, and lets `session.get(Product, 20)` skip the database when the object is already loaded.
3. **The unit of work.** You don't write `UPDATE` statements. You change attributes, add new objects, mark objects deleted, and the session records it. At **flush** time it computes the minimal set of `INSERT`/`UPDATE`/`DELETE` statements, orders them so foreign keys are satisfied (sellers before products, products before inventory), and sends them in the session's transaction. At **commit**, it flushes and commits.

```text
            your code                         Session                            PostgreSQL
  p = session.get(Product, 20)  ──►  identity map: {(Product,20): p} ──► SELECT ... WHERE id=20
  p.price = Decimal("9.99")     ──►  p marked dirty (price changed)
  session.add(Review(...))      ──►  review pending
  session.commit()              ──►  flush: UPDATE products ...;     ──► (same transaction)
                                            INSERT INTO reviews ...
                                     COMMIT                          ──► COMMIT
```

### 1.2 The Session is a transaction-scoped workspace

A `Session` is not a connection and not a cache you keep around. It's a **workspace for one unit of work**: one web request, one job, one script step. It begins a transaction on first use, holds the objects you load and change, and at the end you commit or roll back and close it. After a commit, the session's objects are (by default) **expired**: their loaded values are discarded, because other transactions may change the rows from now on, and reading an attribute will load it again in a new transaction.

That model is what PostgreSQL's MVCC (Module 10) suggests anyway: data you read is a snapshot that is valid for your transaction. The ORM aligns object lifetimes with transactions. Keep sessions short; never share one between threads or concurrent tasks; never keep one open across user think time.

---

## 2. Declarative mapping, 2.0 style

### 2.1 The base class and typed columns

[`bazaar/orm/models.py`](../../project/python/src/bazaar/orm/models.py) maps all of Bazaar's entities. It starts with a base class:

```python
class Base(AsyncAttrs, DeclarativeBase):
    metadata = sa.MetaData(naming_convention=core.NAMING_CONVENTION)
    type_annotation_map = {
        Decimal: sa.Numeric(12, 2),
        datetime: sa.DateTime(timezone=True),
        dict[str, Any]: pg.JSONB,
        list[str]: pg.ARRAY(sa.Text),
        OrderStatus: core.order_status,      # the ENUM types from module 24, create_type=False
        ProductStatus: core.product_status,
        PaymentStatus: core.payment_status,
        str: sa.Text,
    }
```

- `DeclarativeBase` creates a **registry** of mapped classes and a `MetaData` holding their tables. Bazaar's uses the naming convention of Module 24, so constraint names agree with the database.
- `type_annotation_map` says which column type a Python annotation means. Every `Mapped[Decimal]` becomes `numeric(12,2)`, every `Mapped[datetime]` a `timestamptz`, every `Mapped[str]` `text` (SQLAlchemy's default would be `VARCHAR`), every `Mapped[OrderStatus]` the existing `order_status` enum.
- `AsyncAttrs` adds the `awaitable_attrs` accessor for the async ORM (section 13).

A mapped class:

```python
class Product(Base):
    __tablename__ = "products"
    __table_args__ = (sa.UniqueConstraint("seller_id", "sku"),)

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(always=True), primary_key=True)
    seller_id: Mapped[int] = mapped_column(sa.ForeignKey("sellers.id"))
    category_id: Mapped[int] = mapped_column(sa.ForeignKey("categories.id"), index=True)
    sku: Mapped[str]
    title: Mapped[str]
    description: Mapped[str | None]
    price: Mapped[Decimal]
    status: Mapped[ProductStatus] = mapped_column(server_default="draft")
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=sa.func.now(),
                                                 server_onupdate=sa.FetchedValue())
    attributes: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(pg.JSONB), server_default=sa.text("'{}'::jsonb"))
    tags: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(pg.ARRAY(sa.Text)), server_default=sa.text("'{}'::text[]"))
    search_vector: Mapped[str | None] = mapped_column(pg.TSVECTOR, sa.Computed("...", persisted=True),
                                                      deferred=True)
    ...
```

Read the annotations as the schema:

- `Mapped[str]` is `NOT NULL`; `Mapped[str | None]` is nullable. The annotation is the single source of nullability, and type checkers (mypy, pyright) understand it: `p.description` is `str | None` in your editor.
- A bare annotation (`sku: Mapped[str]`) needs no `mapped_column()` at all. You add `mapped_column(...)` for anything beyond type and nullability: keys, defaults, specific types.
- `deferred=True` leaves a column out of the default `SELECT`; it is loaded on first access, or eagerly with `.options(undefer(Product.search_vector))`. A `tsvector` is large and rarely useful in Python.

### 2.2 Bazaar's entities

| Class | Table | Notable mapping |
|---|---|---|
| `User` | users | `email` uses the `email_address` domain; `seller` is a one-to-one back-reference |
| `Seller` | sellers | `user_id` unique: one shop per user |
| `Category` | categories | self-referential adjacency list: `parent` / `children` |
| `Product` | products | enum, `MutableDict` JSONB, `MutableList` array, generated `search_vector`, `xmin` version |
| `Inventory` | inventory | one-to-one with `Product` (the PK is the FK); hybrid `available` |
| `Order` | orders | trigger-maintained `total`; `column_property` `item_count` |
| `OrderItem` | order_items | association object between `Order` and `Product`; composite primary key |
| `Payment`, `Review`, `Address`, `CartItem`, `Promotion` | | `Promotion.active_during` is a `Range[datetime]` |
| `ActiveProduct` | the `active_products` view | read-only |

The database still owns the schema (Module 26 hands it to Alembic). The ORM classes describe what the ORM needs to know: columns, types, keys and relationships. CHECK constraints and most indexes live in the SQL files and in Module 24's Core tables. The milestone tests run Module 24's drift comparison in a `mapping_only` mode that checks exactly those things:

```python
def test_mapping_matches_database(sa_conn):
    assert compare(sa_conn, Base.metadata, mapping_only=True) == []
```

> **Under the hood:** you could map classes onto the existing Core tables instead (`__table__ = tables.products`), which gives one `MetaData` for both layers. Bazaar maps declaratively because that's the style you'll read in most code, and because annotations carry nullability and types to the type checker. The view mapping (section 12) shows the `__table__` form.

### 2.3 Enums, JSONB, arrays and ranges

- **Enums.** `Mapped[OrderStatus]` maps to the existing PostgreSQL enum through `type_annotation_map`, so `order.status` is an `OrderStatus` member and `Order.status == OrderStatus.PAID` (or `== "paid"`) works in queries.
- **JSONB and mutation tracking.** The ORM detects changes by **attribute assignment**: `p.title = "x"` fires an event that marks `title` changed. A plain `JSONB` column holding a dict cannot know that you changed the dict *in place*: `p.attributes["color"] = "red"` mutates the dict object without assigning the attribute, so the change would be silently lost at flush. `MutableDict.as_mutable(pg.JSONB)` wraps the value in a dict subclass that reports in-place changes to its parent. It tracks only the **top level**:

  ```text
  {'cpu': 'Ryzon 9', 'brand': 'Zentro', 'color': 'Blue', 'ram_gb': 64, 'storage': {'gb': 512, 'type': 'ssd'}, ...}
  p.attributes["storage"]["gb"] = 4096      -> nested change tracked? False
  p.attributes["color"] = p.attributes["color"]  -> tracked? True
  ```

  For nested changes, either reassign the top-level key (`attrs = dict(p.attributes); attrs["storage"] = {...}; p.attributes = attrs`) or call `flag_modified(p, "attributes")`. `MutableList` does the same for arrays.
- **Ranges.** `Mapped[Range[datetime]]` with `pg.TSTZRANGE` gives `sqlalchemy.dialects.postgresql.Range` objects, which also implement `contains()` in Python (Exercise 9 uses that).

---

## 3. Sessions

### 3.1 Creating sessions

```python
from sqlalchemy.orm import sessionmaker

Session = sessionmaker(engine, expire_on_commit=False)   # a factory, created once

with Session() as session:              # close() at the end; rollback of anything uncommitted
    ...
    session.commit()

with Session.begin() as session:        # BEGIN ... COMMIT, or ROLLBACK on exception, then close
    session.add(...)
```

`sessionmaker` is configured once per process (with the engine) and produces sessions. `Session.begin()` is the ORM twin of `engine.begin()`: the block is one transaction. `bazaar.orm.session.make_sessionmaker()` builds Bazaar's factory, with the RLS and read-only hooks of sections 12 and 15.

**Bazaar's service convention** is the same as for the plain-SQL repository and the Core reports: a service function takes a `Session` first and **never commits**. The caller (a request handler, a job, a test) owns the transaction. A function that must be atomic on its own uses `session.begin_nested()`, a `SAVEPOINT`.

### 3.2 Object states

Every mapped object is in one of these states with respect to a session ([`examples/01_session_basics.py`](examples/01_session_basics.py)):

```text
2. Object states, flush and commit
   new object:         transient | id = None
   after add():        pending | id = None
    SQL> INSERT INTO users (email, full_name) VALUES (%(email)s, %(full_name)s::VARCHAR) RETURNING users.id, users.is_a
   after flush():      persistent | id = 100097 | is_active = True
   after commit():     persistent (expired: addresses, created_at, email, ...)
    SQL> SELECT users.id, users.email, users.full_name, users.is_active, users.created_at FROM users WHERE users.id = %
   reading u.full_name -> Demo Buyer
   after delete+flush: deleted
   after close():      detached
```

| State | Meaning |
|---|---|
| **transient** | A plain Python object; no session, no row. |
| **pending** | `session.add()`ed; will be inserted at the next flush. |
| **persistent** | In the session's identity map with a database row. |
| **deleted** | `session.delete()`d and flushed; the `DELETE` is sent but not committed. |
| **detached** | Has a row, but belongs to no session (the session was closed, or `expunge()`). |
| **expired** | Not a separate state: a persistent object whose attribute values were discarded. The next access reloads them with a `SELECT`. |

Look at the `INSERT`: it ends with `RETURNING users.id, users.is_active, users.created_at`. On PostgreSQL the ORM fetches the identity value **and the server defaults** in the same statement, so after the flush `u.is_active` is `True` without another query.

### 3.3 Flush versus commit

**Flush** sends pending changes as SQL inside the current transaction. **Commit** flushes, then commits. After a flush you have ids and server-generated values, constraint violations have been raised, and other statements in the same transaction see the rows; nothing is visible to other transactions until commit. Services flush when they need an id or want errors to surface at a predictable place (`session.flush()` in `create_product()` below).

### 3.4 `expire_on_commit`

By default, `commit()` expires every object in the session. The next attribute access starts a new transaction and reloads the row, which guarantees you never read stale data from a previous transaction. It also has costs:

- After `with Session.begin() as s: ...`, the session is closed and the objects are **detached** and **expired**: reading `order.total` raises `DetachedInstanceError` (there's no session to reload with). Exercise 3 hits exactly this.
- In async code an expired attribute can't be reloaded implicitly (section 13).
- A web handler that commits and then serializes the objects reloads every one of them.

Bazaar's sessions use `expire_on_commit=False`: after commit, objects keep the values they had at the end of the transaction. That's what a request handler wants (commit, then render), and what async code needs. The price is explicit: those values are a snapshot, not "live". Call `session.refresh(obj)` when you need the current row.

### 3.5 Autoflush: helpful, until it isn't

Before every query, the session **autoflushes** pending changes, so the query sees them:

```text
3. Autoflush: a query sees pending changes, because they are flushed first
    SQL> UPDATE products SET price=%(price)s WHERE products.id = %(products_id)s::BIGINT AND products.xmin = %(products
    SQL> SELECT count(*) AS count_1 FROM products WHERE products.price <= %(price_1)s::INTEGER AND products.seller_id =
   products <= 1.00 of that seller: 1
   after rollback, p.price = 24.99 (expired and reloaded)
```

Without autoflush, the count would have ignored our own change: a query would contradict the objects in memory. The flip side: a flush can fail **inside a query that has nothing to do with the bug**:

```text
4. ...and an autoflush can fail in a place that has nothing to do with the bug
   the SELECT raised: NotNullViolation - null value in column "title" of relation "products" violates not-null constraint
   inside no_autoflush the same kind of query works: 3458
```

The incomplete `Product` was added three lines earlier; the error surfaced at the next `SELECT`. When you see an `IntegrityError` raised by a query, look for a pending object. `with session.no_autoflush:` suspends autoflush for a block (useful while you assemble an object graph that is temporarily incomplete); `Session(autoflush=False)` disables it entirely, which mostly trades one surprise for another.

### 3.6 The identity map in practice

```text
1. The identity map: one object per row per session
   same object: True | session.get again issues SQL? no
```

`session.get(Product, 20)` looks in the identity map first and only queries when the object isn't there. A `select()` always queries, but rows that map to an object already in the session return *that* object, **without overwriting its attributes** (your unflushed changes win). If you need the database's current values for objects you already hold, use `.execution_options(populate_existing=True)` on the query or `session.refresh(obj)`.

The identity map is also why a session must be short-lived: it holds a strong reference to every object it has loaded, plus a copy of their original state for change detection. A session that loads 100,000 orders holds 100,000 objects until it's closed (section 16 measures that).

---

## 4. Querying, 2.0 style

The ORM uses the same `select()` as Core, with classes and their attributes in place of tables and columns:

```python
session.get(Product, 20)                                    # by primary key (identity map first)
session.scalars(select(Product).where(Product.seller_id == 42).order_by(Product.id)).all()
session.scalars(select(Order).where(Order.id == 501)).one()
session.execute(select(Product.id, Product.title)).all()    # rows of columns, not objects
session.scalar(select(func.count()).select_from(Product))
```

- `session.scalars(select(Model))` returns objects. `session.execute(select(Model))` returns `Row`s whose first element is the object; use it when you select several entities or entity plus columns: `select(Order, User.full_name).join(Order.user)`.
- `.unique()` is required on results when a **collection** is joined-eager-loaded (section 9), because the join repeats the parent row once per child.
- The old `session.query(Product).filter_by(...)` API still exists for legacy code; 2.0-style code uses `select()`.

### 4.1 Joins along relationships

A relationship attribute can be the target of a join, and supplies the ON clause from its own definition, which removes Module 24's "inferred join" trap:

```python
stmt = (
    sa.select(User.full_name, sa.func.count(Order.id))
    .join(User.orders)
    .join(Order.seller)
    .where(Seller.shop_name == "Happy Outlet 1")
    .group_by(User.id)
    .order_by(sa.func.count(Order.id).desc(), User.id)
    .limit(3)
)
```

```text
SELECT users.full_name, count(orders.id) AS count_1 FROM users JOIN orders ON users.id = orders.user_id JOIN sellers ON sellers.id = orders.seller_id WHERE sellers.shop_name = %(shop_name_1)s::VARCHAR GROUP BY users.id ORDER BY count(orders.id) DESC, users.id LIMIT %(param_1)s::INTEGER
[('Hugo Nelson', 32), ('Francesca Schneider', 24), ('Kevin Clark', 20)]
```

### 4.2 `any()` and `has()`

Relationships also give you `EXISTS` subqueries: `collection.any(criteria)` for one-to-many, `reference.has(criteria)` for many-to-one.

```python
select(func.count()).select_from(Product).where(Product.category_id == 3, Product.reviews.any(Review.rating == 1))
select(func.count()).select_from(Review).where(Review.product.has(Product.category_id == 3))
```

```text
laptops with a 1-star review: 295
SELECT count(*) AS count_1 FROM reviews WHERE EXISTS (SELECT 1 FROM products WHERE products.id = reviews.product_id AND products.category_id = %(category_id_1)s::INTEGER)
reviews of laptops: 6225
```

### 4.3 `contains_eager()`: use your join to fill a collection

When you join to a collection *and* want that collection populated from the join, `contains_eager()` tells the ORM to read the joined columns into the relationship instead of loading it separately:

```python
stmt = (
    sa.select(Order)
    .join(Order.items)
    .where(Order.user_id == 8, OrderItem.unit_price > 100)
    .options(contains_eager(Order.items))
    .order_by(Order.id)
)
orders = session.scalars(stmt).unique().all()
```

Read the result carefully: 63 orders of user 8 have an item over 100, and order 1058 shows `len(order.items) == 2`, although it has **4** items. `contains_eager` filled the collection with the rows your `WHERE` let through. That's sometimes exactly what you want ("show only the expensive lines") and a nasty bug elsewhere, because the same `order` object in the identity map now *claims* to have two items for the rest of the session. Use `contains_eager` for filtered collections deliberately, ideally on a fresh session or with `populate_existing`; for complete collections use the loader options of section 9.

---

## 5. Relationships

### 5.1 `relationship()` and `back_populates`

```python
class Order(Base):
    ...
    user: Mapped[User] = relationship(back_populates="orders")             # many-to-one
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan", passive_deletes=True
    )                                                                       # one-to-many

class OrderItem(Base):
    __tablename__ = "order_items"
    order_id: Mapped[int] = mapped_column(sa.ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True)
    product_id: Mapped[int] = mapped_column(sa.ForeignKey("products.id"), primary_key=True, index=True)
    quantity: Mapped[int]
    unit_price: Mapped[Decimal]
    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()
```

- The **direction** comes from the foreign keys: `Order.user` uses `orders.user_id → users.id`, so it's many-to-one and holds one object; `Order.items` uses `order_items.order_id → orders.id` from the other side, so it's one-to-many and holds a list. The annotation (`Mapped[User]` vs `Mapped[list[OrderItem]]`) must agree.
- `back_populates` links the two sides: appending an item to `order.items` sets `item.order = order` in memory, before any flush.
- `Mapped[Seller | None]` on `User.seller` makes it **one-to-one**: a scalar instead of a list on the side that would otherwise be a collection. `Product.inventory` is one-to-one the same way, and `Inventory.product_id` is both its primary key and its foreign key.

### 5.2 The association object

`order_items` is not a plain many-to-many link table: it carries `quantity` and `unit_price`. So it is mapped as a class of its own, an **association object**, with a composite primary key `(order_id, product_id)`. You navigate `order.items[i].product`, never directly `order.products`. ([`examples/02_relationships.py`](examples/02_relationships.py)):

```text
2. Many-to-one, one-to-one, and the association object
    2 x Basecamp Performance Yoga Mat  @ 32.99  (stock now 14)
    1 x The Hidden Company             @ 43.99  (stock now 210)
    1 x Casa Verde Nordic Planter - Wh @ 41.99  (stock now 166)
    buyer: Leon Wilson | shop: Little Workshop 2 | total: 151.96
```

(SQLAlchemy can also map a pure link table with `relationship(secondary=...)`, which hides the link rows; that only works when the link has no columns of its own. Add an `association_proxy` if you want `order.products` as a convenience view over the items.)

### 5.3 A self-referential tree

```python
class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(sa.Integer, sa.Identity(always=True), primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(sa.ForeignKey("categories.id"), index=True)
    ...
    parent: Mapped[Category | None] = relationship(back_populates="children", remote_side=[id])
    children: Mapped[list[Category]] = relationship(back_populates="parent", order_by="Category.name")
```

With both sides in the same table, SQLAlchemy can't tell from the foreign key alone which side is "one". `remote_side=[id]` says: in `parent`, the *remote* (other) row is identified by its `id`, so `parent` is many-to-one.

```text
1. A self-referential tree
    Electronics > Computers > Laptops | children of Computers: ['Keyboards & Mice', 'Laptops', 'Monitors', 'Tablets']
```

Walking up a tree with `.parent` is one query per level (or none, when the parents are already in the identity map). For whole subtrees use the recursive CTE from Module 24; you can select ORM objects from it: `select(Category).join(tree, Category.id == tree.c.id)`.

### 5.4 `foreign_keys`: when there are two paths

If a table has two foreign keys to the same target, the ORM refuses to guess. A hypothetical `transfers(from_user_id, to_user_id)` with `sender: Mapped[User] = relationship()`:

```text
AmbiguousForeignKeysError Could not determine join condition between parent/child tables on relationship T.sender - there are multiple foreign key paths linking the tables.  Specify the 'foreign_keys' argument, providing a list of those columns which should be counted as containing a foreign key reference to the parent table
```

The fix names the column: `sender: Mapped[User] = relationship(foreign_keys=[from_user_id])` and `recipient: Mapped[User] = relationship(foreign_keys=[to_user_id])`. For conditions that aren't plain foreign keys (a relationship to "the active promotion"), `primaryjoin=` takes an arbitrary expression; combine it with `viewonly=True` when the ORM must not write through it.

### 5.5 Cascades

A relationship's `cascade` says which session operations travel from parent to child:

| Cascade | Effect |
|---|---|
| `save-update` (default) | `session.add(parent)` also adds the children. |
| `merge` (default) | `session.merge(parent)` merges the children. |
| `delete` | Deleting the parent deletes the children through the ORM. |
| `delete-orphan` | A child removed from the parent's collection is deleted. |
| `all` | Everything except `delete-orphan`. |

Bazaar uses `cascade="all, delete-orphan"` where the child can't exist without the parent and belongs to it: `Order.items`, `Product.inventory`, `Product.reviews`, `Product.promotions`, `User.addresses`. Building a graph and flushing it lets the unit of work order the statements:

```text
3. Building a graph and flushing it: the unit of work orders the INSERTs
    SQL> INSERT INTO products (seller_id, category_id, sku, title, description, price) VALUES (%(seller_id)s::BIGINT, %
    SQL> INSERT INTO inventory (product_id, quantity) VALUES (%(product_id)s::BIGINT, %(quantity)s::INTEGER) RETURNING
    SQL> INSERT INTO reviews (product_id, user_id, rating, title, body) VALUES (%(product_id)s::BIGINT, %(user_id)s::BI

4. delete-orphan: removing a child from the collection deletes the row
    SQL> DELETE FROM reviews WHERE reviews.id = %(id)s::BIGINT
```

The product is inserted first, its generated id is copied into the inventory row and the review, then those are inserted. You never assigned `product_id` yourself.

### 5.6 `passive_deletes`: let `ON DELETE CASCADE` do it

With `cascade="all"`, deleting a product through the ORM must also delete its reviews. If the reviews aren't loaded, the ORM's default is to **load them first** (one `SELECT`), then delete them one by one, which for product 2's 167 reviews is a lot of work the database could do itself: `reviews.product_id` is declared `ON DELETE CASCADE` (Module 07). `passive_deletes=True` tells the ORM to trust the database for children it hasn't loaded:

```text
5. Deleting a parent: passive_deletes lets ON DELETE CASCADE do the work
    SQL> DELETE FROM products WHERE products.id = %(id)s::BIGINT AND products.xmin = %(xmin)s
    reviews of product 2 left: 0

6. ...unless the children are already loaded: then the ORM deletes them itself
    product 3 has 138 reviews loaded in the session
    SQL> DELETE FROM reviews WHERE reviews.id = %(id)s::BIGINT (executemany)
    SQL> DELETE FROM products WHERE products.id = %(id)s::BIGINT AND products.xmin = %(xmin)s
```

One `DELETE`, and PostgreSQL removed the 167 reviews (and the inventory row) through the foreign keys. Children that *are* in the session are still deleted by the ORM, so the session's state stays consistent. Use `passive_deletes=True` on every relationship whose foreign key has `ON DELETE CASCADE`. Where the foreign key has no cascade (`order_items.product_id`, `payments.order_id`), there is nothing to be passive about: the database refuses the delete, which is the point.

### 5.7 `lazy`: when a relationship is loaded

The `lazy` argument of `relationship()` sets the *default* loading strategy for that attribute:

| `lazy=` | Loads... |
|---|---|
| `"select"` (default) | on first access, with its own `SELECT` |
| `"selectin"` / `"joined"` / `"subquery"` | together with the parent, always (section 9) |
| `"raise"` / `"raise_on_sql"` | never: access raises unless the query loaded it |
| `"noload"` / `"write_only"` / `"dynamic"` | special cases (huge collections) |

Bazaar keeps the default `"select"` in the mapping and decides **per query** with loader options (section 9). Section 9.4 discusses that choice.

---

## 6. The N+1 problem

### 6.1 What it looks like

Here is an innocent piece of code, the shape of every "list page" ([`examples/03_loading_strategies.py`](examples/03_loading_strategies.py)): seller 42's 100 most recent orders, with buyer names and item SKUs.

```python
orders = session.scalars(
    select(Order).where(Order.seller_id == 42).order_by(Order.created_at.desc()).limit(100)
)
lines = [
    f"{o.id} {o.user.full_name}: " + ", ".join(f"{i.quantity}x{i.product.sku}" for i in o.items)
    for o in orders
]
```

```text
lazy (the default)                      417 statements    328.5 ms
```

**417 statements** for one page: 1 for the orders, then for each order one for its buyer (`o.user`) and one for its items (`o.items`), and one per distinct product (`i.product`). "1 query to get N things, then N queries for their relations": the N+1 problem. Each statement is fast (well under a millisecond on the server), and that's what makes it insidious: in development, with 5 orders and the database on localhost, the page renders instantly. In production, with 100 orders and a database 1 ms away, 417 round trips are half a second of pure waiting, and they multiply under load.

### 6.2 Seeing it

Nothing in the code above *looks* like a query. Ways to see it:

- `echo=True` and a terminal that scrolls for a long time.
- `bazaar.sa.engine.count_statements(engine)` around a block, which is what the numbers above come from, and what the milestone's **N+1 guard test** uses:

  ```python
  def test_order_history_has_no_n_plus_1(session):
      engine = session.get_bind().engine
      session.connection()                  # begin first, so the fixture's SAVEPOINT isn't counted
      with count_statements(engine) as stmts:
          orders = services.order_history(session, 8, limit=20)
          lines = [(o.seller.shop_name, i.product.title, i.quantity) for o in orders for i in o.items]
      assert len(orders) == 20 and len(lines) > 20
      assert len(stmts) == 2                # orders+sellers, then items+products: independent of `limit`
  ```

  A test like this fails the day someone adds `o.payments` to a template without adding a loader option.
- `pg_stat_statements` (Module 12) in production: a cheap primary-key query with a call count in the millions is the classic fingerprint.

---

## 7. Loading strategies

### 7.1 The options

Loader options tell one query how to load relationships. The same page, five ways, statement counts and best-of-five times:

```text
lazy (the default)                      417 statements    328.5 ms
joinedload everything                     1 statements     19.6 ms
selectinload everything                   4 statements     20.6 ms
joined to-one, selectin collections       2 statements     16.1 ms
subqueryload collections                  2 statements     17.9 ms
```

**`joinedload(rel)`** adds a `LEFT OUTER JOIN` to the main query and reads the related columns from the same rows:

```sql
SELECT ..., order_items_1.unit_price
FROM (SELECT ... FROM orders WHERE orders.seller_id = %(seller_id_2)s::BIGINT
      ORDER BY orders.created_at DESC LIMIT %(param_1)s::INTEGER) AS anon_1
LEFT OUTER JOIN users AS users_1 ON users_1.id = anon_1.user_id
LEFT OUTER JOIN order_items AS order_items_1 ON anon_1.id = order_items_1.order_id
LEFT OUTER JOIN products AS products_1 ON products_1.id = order_items_1.product_id
ORDER BY anon_1.created_at DESC
```

One round trip. Note the subquery: because joining a collection multiplies parent rows, the ORM wraps the `LIMIT` query so that it limits *orders*, not joined rows. For many-to-one relationships (`Order.user`, `OrderItem.product`) joining is ideal: one row per parent either way. For collections, every parent column is repeated once per child (an order with 9 items sends its order and buyer columns 9 times), nested collections multiply (items × payments), and the result needs `.unique()`.

**`selectinload(rel)`** runs one extra query per relationship, after the parents are loaded, with their primary keys in an `IN` list:

```sql
SELECT ..., products_1.xmin
FROM order_items LEFT OUTER JOIN products AS products_1 ON products_1.id = order_items.product_id
WHERE order_items.order_id IN (%(primary_keys_1)s::BIGINT, %(primary_keys_2)s::BIGINT, ... %(primary_keys_100)s::BIGINT)
```

No row multiplication, no `.unique()`, works with `LIMIT` naturally, and the `IN` list is served by the foreign-key index (Module 11: this is why foreign keys are indexed). Large parent sets are split into batches of 500 keys. For many-to-one it costs an extra query that a join would have avoided; hence "selectinload everything" took 4 statements.

**`subqueryload(rel)`** also runs one extra query per relationship, but re-runs the original query as a subquery instead of passing ids:

```sql
SELECT ... FROM (SELECT orders.id AS orders_id FROM orders WHERE orders.seller_id = %(seller_id_1)s::BIGINT
                 ORDER BY orders.created_at DESC LIMIT %(param_1)s::INTEGER) AS anon_1
JOIN order_items ON anon_1.orders_id = order_items.order_id LEFT OUTER JOIN products AS products_1 ...
```

It predates `selectinload` and is rarely the better choice: the original query runs twice, and with `LIMIT` but an incomplete `ORDER BY` the two runs can disagree. Prefer `selectinload`.

**Options chain along paths**: `selectinload(Order.items).joinedload(OrderItem.product)` means "items by IN-query, and each item's product joined into that query". The best plan for this page combines the two rules into 2 statements:

```python
.options(
    joinedload(Order.user),                                    # to-one: join
    selectinload(Order.items).joinedload(OrderItem.product),   # collection: selectin; its to-one: join
)
```

### 7.2 `raiseload`: make the N+1 impossible

`raiseload(rel)` makes an attribute **raise** instead of loading. `raiseload("*")` applies to every relationship not otherwise mentioned in the query's options:

```python
orders = session.scalars(
    select(Order).where(Order.id == 501).options(selectinload(Order.items), raiseload("*"))
).all()
```

```text
raiseload('*') as a guard:
   items were loaded: 3
   accessing .user: 'Order.user' is not available due to lazy='raise'
   with lazyload(Order.user): Leon Wilson
```

This is the guard every query in `bazaar.orm.services` ends with: the loader options are the function's contract ("I return orders with items, products and sellers"), and anything outside it fails loudly in tests instead of quietly costing a query per object in production. `lazyload(rel)` re-enables lazy loading for one path when you really want it.

### 7.3 Choosing

- **Many-to-one / one-to-one** (`Order.user`, `OrderItem.product`, `Product.inventory`): `joinedload`.
- **One-to-many / many-to-many** (`Order.items`, `Product.reviews`): `selectinload`.
- **Big collections you only need counts or a subset of**: don't load them at all; query an aggregate (`column_property` in section 11, or a separate grouped query).
- **Always**: `raiseload("*")` in request and job code.

### 7.4 The right default in the mapping

There are two camps. One sets `lazy="raise"` (or `"raise_on_sql"`) on every relationship, so nothing is ever loaded implicitly anywhere; scripts and the shell then need explicit options too. The other keeps `lazy="select"` and adds `raiseload("*")` to the queries that matter. Bazaar takes the second approach: interactive exploration, admin scripts and the exercises benefit from lazy loading, and every service query declares its loads and the guard, which the N+1 tests enforce. Either way, the decision is made *per query*: the mapping's default only decides what happens when you forget.

---

## 8. Writing: the unit of work and bulk operations

### 8.1 What the unit of work is good at

Adding, changing and deleting individual objects is what the unit of work is for: you change a handful of objects in a request, and the flush sends exactly the statements needed, in dependency order, batching same-shaped `INSERT`s with insertmanyvalues (Module 24, section 7.4).

```text
1. Unit of work: session.add_all() of 5,000 new objects
   1.09 s, 5 statements: INSERT INTO products (seller_id, category_id, sku, title, description, price, status) SELE...
```

([`examples/04_bulk.py`](examples/04_bulk.py), rolled back.) 5,000 `Product` objects became 5 batched `INSERT ... SELECT ... FROM (VALUES ...) RETURNING` statements of 1,000 rows each; each object received its id. Most of the 1.1 s is Python: building 5,000 objects with their instrumentation, and the identity-map bookkeeping.

### 8.2 Bulk INSERT with `insert(Model)`

When you have dictionaries, not objects, skip the objects:

```python
session.execute(sa.insert(Product), rows)                       # list of dicts
new = session.scalars(
    sa.insert(Product).returning(Product, sort_by_parameter_order=True), rows
).all()                                                          # ...and get objects back
```

```text
2. ORM bulk INSERT: session.execute(insert(Product), list_of_dicts)
   0.71 s, 1 statement(s)
   ... with RETURNING: get Product objects back
   0.84 s, 5 statements, first: Product(id=274172, sku='M25-BULK-00000-R', price=Decimal('9.99')), in session: True
```

The ORM translates attribute names to columns and applies the mapping's types, then runs the same Core paths as Module 24 (executemany without `RETURNING`, insertmanyvalues with it). `returning(Product)` builds objects from the returned rows and puts them in the session. For thousands of rows and more, Module 24's advice stands: COPY into a staging table.

### 8.3 Bulk UPDATE by primary key

A list of dicts that each contain the primary key updates those rows, with one `executemany`:

```python
session.execute(sa.update(Inventory), [{"product_id": i.product_id, "quantity": i.quantity + 10} for i in stock])
```

```text
3. Bulk UPDATE by primary key: a list of dicts that include the pk
   3458 rows, 1 statement(s): UPDATE inventory SET quantity=%(quantity)s::INTEGER WHERE inventory.product_id = %(inventory_product
   first row: object says 116 (was 106), database says 116
   versioned Product: cannot assign to system column "xmin"
```

Objects already in the session were updated in memory too. The last line is a real limitation you'll meet with Bazaar's versioned `Product` (section 10): bulk update by primary key on a versioned mapper wants the version in each dict, and then tries to *set* the version column, which is impossible for `xmin`. Use an ORM-enabled `UPDATE ... WHERE` (next section) or Core for bulk changes to products.

### 8.4 ORM-enabled `UPDATE` and `DELETE` with `WHERE`

`update(Model).where(...).values(...)` executed by the session is a single set-based statement, like Core's, plus one extra service: **synchronizing** objects already in the session.

```python
session.execute(
    sa.update(Product)
    .where(Product.sku.like("M25-BULK-%-R"), Product.seller_id == 7)
    .values(status=ProductStatus.ARCHIVED, price=Product.price * 2),
    execution_options={"synchronize_session": "auto"},     # the default
)
```

```text
4. ORM-enabled UPDATE with WHERE, and synchronize_session
   before: 9.99 draft
   after, synchronize_session='auto': 19.98 archived
   after, synchronize_session=False: title = 'Bulk 0' (stale)
   after refresh(): 'changed'
```

| `synchronize_session` | How objects in the session are updated |
|---|---|
| `"auto"` (default) | `"evaluate"` if possible, else `"fetch"` |
| `"evaluate"` | evaluates the `WHERE` and `SET` in Python against objects in memory; no extra SQL, fails for expressions it can't evaluate |
| `"fetch"` | finds the affected primary keys (on PostgreSQL with `RETURNING`, no extra query) and updates or expires those objects |
| `False` | does nothing: objects in the session keep stale values until refreshed or expired |

`synchronize_session=False` is fine (and fastest) when the session holds none of the affected objects, typically a job that only runs bulk statements. `delete(Model).where(...)` works the same way and supports `.returning(...)`:

```text
5. ORM-enabled DELETE ... RETURNING
   deleted cart lines for products [16856, 18257, 39415]
```

Bulk statements bypass the unit of work: no cascades, no relationship handling, no Python-side defaults or events per object. They are SQL with conveniences.

---

## 9. Optimistic concurrency with `version_id_col`

Module 10 described the **lost update**: two sessions read the same row, both change it, the second write silently overwrites the first. Pessimistic locking (`SELECT ... FOR UPDATE`) prevents it by blocking; optimistic concurrency detects it at write time instead, which suits edits that span user think time (an edit form open for two minutes can't hold a row lock).

The ORM implements optimistic concurrency with a **version column**: every `UPDATE` and `DELETE` includes `WHERE version = <the version I loaded>`, and if no row matches, someone else changed it: `StaleDataError`. Usually you add an integer column the ORM increments. PostgreSQL offers one for free: the system column **`xmin`**, the id of the transaction that created the current row version (Module 16), which changes on every update by anyone (psql, triggers, other services included):

```python
class Product(Base):
    ...
    xmin: Mapped[int] = mapped_column(XID(), system=True, server_default=sa.FetchedValue())
    __mapper_args__ = {"version_id_col": xmin, "version_id_generator": False}
```

- `system=True`: a column that exists without being declared in DDL; never created, never inserted.
- `version_id_generator=False`: the database produces new versions; the ORM reads them back (with `RETURNING`).
- `XID` is a tiny `UserDefinedType` in `models.py` for PostgreSQL's `xid` type (sent as text, read as `int`). Binding the version as `BIGINT` fails with `operator does not exist: xid = bigint`, because psycopg's bind casts are explicit (Module 24, section 8.1).

[`examples/05_versions_and_rls.py`](examples/05_versions_and_rls.py):

```text
1. Two sessions edit the same product
   both loaded xmin = 2511 2511
    SQL> UPDATE products SET price=%(price)s WHERE products.id = %(products_id)s::BIGINT AND products.xmin = %(products_xmin)s RE
   Bob committed; the row's xmin is now 2512
    SQL> UPDATE products SET title=%(title)s::VARCHAR WHERE products.id = %(products_id)s::BIGINT AND products.xmin = %(products_
   Alice: StaleDataError - UPDATE statement on table 'products' expected to update 1 row(s); 0 were matched.
   after rollback Alice sees 12.00 and can retry
```

What to do with a `StaleDataError` depends on the operation. For a user's edit, show "this product was changed meanwhile" with the new values (the `update_product()` service turns it into `bazaar.errors.ConcurrentUpdate`, and also accepts an `expected_version` the client sends back from the form). For an automatic operation, re-read and retry. Exercise 6 runs 8 threads × 25 increments three ways: naive read-modify-write loses most updates (`expected 200, got 38`), the versioned version gets 200 with ~700 retries, and an atomic `UPDATE ... SET quantity = quantity + 1` gets 200 with no retries at all, which is the better answer for counters.

> **Pitfall:** `xmin` is a 32-bit transaction id. It changes on *every* update of the row, by anyone (psql, other services, a migration's backfill), and transaction ids wrap around after about 4 billion transactions (Module 16), so a value can, in theory, come back. That makes `xmin` a good version for short periods, like the time between loading an edit form and saving it, and a poor one for long-lived versions (offline clients, sync protocols). For those, add an explicit `version integer` column that the ORM increments (adding it is a schema migration, the subject of Module 26).

---

## 10. Values the database computes

### 10.1 Identity, defaults, generated columns, triggers

The ORM must know which values the database will produce, so it doesn't send them and does read them back:

| Database feature | Mapping | Behavior |
|---|---|---|
| `GENERATED ALWAYS AS IDENTITY` | `sa.Identity(always=True)`, `primary_key=True` | not sent; read with `RETURNING` |
| `DEFAULT now()` | `server_default=sa.func.now()` | not sent if unset; read with `RETURNING` on insert |
| `GENERATED ALWAYS AS (...) STORED` | `sa.Computed("...", persisted=True)` | never sent; read back |
| set by a trigger on `UPDATE` | `server_onupdate=sa.FetchedValue()` | expired after each `UPDATE`, reloaded on access |
| set by a trigger on *another* table | nothing helps | `session.refresh(obj, ["col"])` |

[`examples/08_computed_values.py`](examples/08_computed_values.py) shows the last two rows:

```text
3. A trigger-maintained column (orders.total) goes stale in memory
   total before: 151.96
   after adding an item and flushing: 151.96 (the trigger ran, Python doesn't know)
   after session.refresh(order, ['total']): 163.95

4. server_onupdate=FetchedValue(): updated_at is expired after an UPDATE
   updated_at expired? True
   reloaded on access: 2022-02-10 19:59:52+00:00 -> 2026-10-14 10:17:44.439703+00:00
```

`orders.total` is maintained by a statement-level trigger on **`order_items`** (Module 09). Adding an item updates the order row behind the ORM's back; nothing in the `Order` mapping can know. The checkout service avoids the problem by loading orders *after* `place_order()` has run. When you change items yourself, refresh the order.

### 10.2 Hybrid properties

A **hybrid property** is a Python property that also works as a SQL expression on the class:

```python
class Inventory(Base):
    ...
    @hybrid_property
    def available(self) -> int:
        return self.quantity - self.reserved
```

```text
1. A hybrid property: Python on instances, SQL on the class
   instance: 115 - 3 = 112
   class:    SELECT count(*) AS count_1  FROM inventory  WHERE inventory.quantity - inventory.reserved = %(param_1)s::INTEGER
   sold out: 27535
```

On an instance, `inv.available` runs the Python body. On the class, `Inventory.available` runs the same body with *column expressions*, producing SQL. When Python and SQL need different code (a subquery in SQL, a loop over loaded objects in Python), give the hybrid an explicit `.expression`, or use `hybrid_method` for a parameterized one (Exercise 9's `Product.price_at(ts)` applies the promotion active at `ts`, in both worlds, with identical rounding).

### 10.3 `column_property`

A `column_property` maps a SQL expression as a read-only attribute, loaded with the object:

```python
class Order(Base):
    ...
    item_count: Mapped[int] = column_property(
        sa.select(sa.func.count()).where(OrderItem.order_id == id).correlate_except(OrderItem).scalar_subquery(),
        deferred=True,
    )
```

```text
2. column_property: a correlated subquery, deferred until asked for
   order 501 item_count = 3
   SQL: SELECT ... (SELECT count(*) AS count_1 FROM order_items WHERE order_items.order_id = orders.id) AS anon_1, orders.id, orders.user_id, orders.
```

Without `deferred=True`, every `select(Order)` would run that subquery for every row, whether you need the count or not. Deferred, it costs nothing until a query asks: `.options(undefer(Order.item_count))`. This is how you show "3 items" on an order list without loading the items.

---

## 11. Views and database functions

### 11.1 A view, read-only

`active_products` (Module 09) is a join of four tables. The ORM can map it like a table, with Module 24's `Table` from `view_metadata`:

```python
class ActiveProduct(Base):
    __table__ = core.active_products      # declares id as the "primary key" for the identity map
```

The view isn't updatable (it joins several tables), and the ORM doesn't know that:

```text
5. A view mapped read-only
    ActiveProduct(id=15, title='Zentro Slim 2-in-1 Laptop 456') Rustic Emporium 5 162
   plain Session, flush: cannot update view "active_products"
   BazaarSession, flush: ActiveProduct is read-only (a view)
```

A plain session sends the `UPDATE` and PostgreSQL refuses. `bazaar.orm.session.BazaarSession` has a `before_flush` listener that rejects any pending change to a read-only class before any SQL is sent, with a clear message.

### 11.2 Calling `place_order()`

Business logic that lives in the database is called like any SQL function. The checkout service from [`bazaar/orm/services.py`](../../project/python/src/bazaar/orm/services.py) (as of this module; Module 27 extends it with an idempotency key):

```python
def checkout(session, user_id, items, shipping_address_id=None) -> list[Order]:
    payload = [{"product_id": pid, "quantity": qty} for pid, qty in items]
    call = sa.select(
        sa.func.place_order(
            sa.bindparam("user_id", user_id, type_=sa.BigInteger),
            sa.bindparam("items", payload, type_=JSONB),
            sa.bindparam("address_id", shipping_address_id, type_=sa.BigInteger),
        )
    )
    try:
        with session.begin_nested():                    # SAVEPOINT: a failure undoes only this
            order_ids = list(session.scalars(call))
    except DBAPIError as e:
        _reraise_translated(e)                          # BZ001 -> InsufficientStock, ...
    return list(session.scalars(
        sa.select(Order).where(Order.id.in_(order_ids)).order_by(Order.id)
        .options(selectinload(Order.items), raiseload("*"))
    ))
```

```text
6. Calling place_order() through the checkout service
   InsufficientStock: product 16, requested 2, available 1
   placed: Order(id=500057, status=<OrderStatus.PENDING: 'pending'>, total=Decimal('10.99')) [(16, 1)]
```

The ORM didn't create the orders, so it loads them afterwards, by id, with the trigger-computed totals already in place. Error translation reuses Module 22's `bazaar.errors.translate()` on `e.orig`. The savepoint means a failed checkout leaves the caller's session usable, which the milestone test checks by placing a successful order right after an `InsufficientStock`.

---

## 12. Row-level security with the ORM

Module 15's policies show a seller only their own products and orders when the session variable `app.seller_id` is set, and must be set with `SET LOCAL` (transaction-scoped), so it can never leak to the next user of a pooled connection. With the ORM, the natural hook is the moment a session starts a transaction on a connection: the **`after_begin`** event.

```python
class BazaarSession(Session):
    ...

@event.listens_for(BazaarSession, "after_begin")
def _set_seller_scope(session, transaction, conn):
    seller_id = session.info.get("seller_id")
    if seller_id is not None:
        conn.execute(sa.text("SELECT set_config('app.seller_id', :sid, true)"), {"sid": str(seller_id)})
```

- `session.info` is a dict for your own data. A seller request creates its session with `info={"seller_id": 42}` (`bazaar.orm.session.seller_session()` does that and opens a transaction).
- `set_config(..., true)` is `SET LOCAL`, with a bound parameter (`SET` itself can't take parameters, Module 22).
- The event fires for **every** transaction the session begins, including the next one after a commit.

Connected as `bazaar_seller` (RLS doesn't apply to the table owner or superusers), from the same example:

```text
2. RLS: the session sets app.seller_id at the start of every transaction
    SQL> SELECT set_config('app.seller_id', %(sid)s, true) {'sid': '42'}
    SQL> SELECT count(*) AS count_1 FROM products {}
   seller 42 sees 603 products
    SQL> SELECT set_config('app.seller_id', %(sid)s, true) {'sid': '42'}
    SQL> SELECT products.id, products.seller_id, products.category_id, products.sku, products.title, products.description, produc {'pk_1': 1}
   product 1 (another seller's): None
   without a seller id: 0
```

`session.get(Product, 1)` returns `None`: for this session the row doesn't exist. The services add explicit `seller_id` filters too (defense in depth, and the planner can use the index), but the tests prove that RLS holds even when code forgets: inserting a product for seller 43 from seller 42's session fails with `new row violates row-level security policy`. Exercise 12 shows what goes wrong with a session-level `SET` instead: in 200 concurrent async requests over a 3-connection pool, 44 anonymous requests saw another seller's products.

---

## 13. The async ORM

### 13.1 `AsyncSession`

```python
from sqlalchemy.ext.asyncio import async_sessionmaker

engine = create_async_engine()                                  # asyncpg (module 24)
Session = async_sessionmaker(engine, expire_on_commit=False)

async with Session() as session:
    order = await session.get(Order, 501)
    orders = (await session.scalars(select(Order).where(...).options(selectinload(Order.items)))).all()
    session.add(Review(...))
    await session.commit()
```

Everything that may do IO is awaited: `execute`, `scalars`, `scalar`, `get`, `flush`, `commit`, `rollback`, `refresh`, `delete`, `close`. `session.add()` doesn't do IO, so it isn't. `bazaar.orm.session.make_async_sessionmaker()` uses `BazaarSession` as the `sync_session_class`, so the RLS and read-only hooks work unchanged in async code.

### 13.2 The greenlet, and why lazy loading raises

The ORM's internals (unit of work, loaders, identity map) are synchronous code, shared by the sync and async APIs. `AsyncSession` runs them inside a **greenlet**: when the sync code reaches the point of talking to the database, it switches back to the event loop, which awaits the async driver, then resumes the sync code with the result (Module 24, section 10). That only works when the ORM was *entered through an awaited call*, which set up the greenlet.

A lazy load is triggered by plain attribute access, `order.items`, which is not awaited and therefore runs outside any greenlet. SQLAlchemy can't block the event loop to run the query, so it raises ([`examples/06_async_orm.py`](examples/06_async_orm.py)):

```text
1. A lazy load in async code
   MissingGreenlet: greenlet_spawn has not been called; can't call await_() here. Was IO attempted i ...
2. The fixes
   awaitable_attrs.items: 3 items
   order 495378: ['S196-198986']
   order 481208: ['S4-047195', 'S4-081818']
   order 480393: ['S984-119862']
   run_sync: 98 orders of user 8
```

### 13.3 Designing for it

- **Eager-load what you will touch.** `selectinload` / `joinedload` in the query, as in every Bazaar service. `bazaar.orm.services.order_history_async()` runs the *same statement* as the sync `order_history()`: the loader options are part of the statement, so one function builds it for both.
- **`await obj.awaitable_attrs.items`** (from the `AsyncAttrs` mixin) when you genuinely want an on-demand load.
- **`await session.run_sync(fn)`** runs a plain sync function (taking a sync `Session`) inside the greenlet, where lazy loading works: a bridge for reusing sync code.
- **`expire_on_commit=False`.** With the default, every attribute is expired by `commit()`, and reading one afterwards is an implicit load:

  ```text
  3. expire_on_commit=True in async code
     reading u.full_name after commit -> MissingGreenlet (it was expired)
     after await session.refresh(u): Leon Wilson
  ```

- **One `AsyncSession` per task.** A session (and its connection) runs one statement at a time; concurrent tasks need their own sessions:

  ```text
  4. Fan-out: one session per task, never a shared one
     20 users, 3605 order lines: sequential 0.60 s, gather 0.21 s
     two tasks on one AsyncSession: InvalidRequestError - This session is provisioning a new connection; concurrent operations a...
  ```

The `raiseload("*")` discipline of section 7 is what makes async ORM code pleasant: the code that would raise `MissingGreenlet` in production raises a clear "not available due to lazy='raise'" in your sync tests already.

---

## 14. Testing with the ORM

Module 22's tests wrap each test in a transaction that is rolled back. With the ORM there's a complication: code under test may call `session.commit()` (or `rollback()`), which would end the test's transaction. The fix is to bind the session to a connection that already has a transaction and tell it to **join with savepoints**:

```python
@pytest.fixture
def sa_conn(sa_engine):
    with sa_engine.connect() as c:
        c.begin()                 # the outer transaction: always rolled back
        yield c
        c.rollback()

@pytest.fixture
def session(sa_conn):
    with BazaarSession(bind=sa_conn, join_transaction_mode="create_savepoint",
                       expire_on_commit=False) as s:
        yield s
```

With `join_transaction_mode="create_savepoint"` the session never touches the outer transaction: it starts a `SAVEPOINT` for its own work, `session.commit()` *releases* the savepoint, `session.rollback()` rolls back *to* it, and the next unit of work starts a new savepoint. Code that commits, rolls back and retries behaves as in production, and everything disappears at the end. Exercise 11 tests functions that commit and roll back with this fixture, and checks that the row counts of the database are unchanged afterwards.

Three more patterns the Bazaar tests use:

- **Count statements** to guard against N+1 (section 6.2), after `session.connection()` so the fixture's `SAVEPOINT` isn't counted.
- **Committed fixtures** for anything involving several connections (optimistic locking needs two sessions that see each other's commits): create rows, commit, and delete them at the end (`tests/factories.py`'s `cleanup()`).
- **A real role** for RLS: a connection as `bazaar_seller`, in a rolled-back transaction, so the policies are actually enforced.

Module 27 returns to test isolation at scale: template databases and parallel test runs.

---

## 15. When to drop to Core or raw SQL

The ORM is a tool for **object graphs that a unit of work reads and changes**. It is not a tool for **sets**. [`examples/07_orm_vs_core.py`](examples/07_orm_vs_core.py) computes seller 1's revenue per month of 2025 four ways:

```text
ORM entities (select(Order))             28,187 rows      575 ms  peak  52.2 MB
ORM entities, load_only(2 columns)       28,187 rows      429 ms  peak  50.9 MB
ORM columns (select(Order.a, Order.b))   28,187 rows      129 ms  peak   8.7 MB
GROUP BY in SQL                              12 rows       32 ms  peak   0.0 MB
December 2025: 2656641.98
```

Loading entities costs 20 µs and nearly 2 KB of Python memory per row: an object, its state, its identity-map entry and a copy of its loaded values for change detection. `load_only()` saves on columns but not on objects. Selecting *columns* through the ORM (`select(Order.created_at, Order.total)`) returns plain rows and skips the identity map entirely: 4.5 times faster. Letting PostgreSQL do the aggregation is another 4 times faster (18 times faster than loading entities), because 12 rows cross the wire instead of 28,187.

An honest rule set:

- **Reports and analytics** (anything with `GROUP BY`, window functions, roll-ups): Core, as in `bazaar.sa.reports`. The ORM adds nothing but overhead. You can still run the Core statement on the ORM session: `session.execute(stmt)`.
- **Bulk changes** (reprice 10,000 products, archive stale drafts): ORM-enabled `UPDATE`/`DELETE` (section 8.4) or Core; for imports, COPY through the driver connection.
- **Complex CTEs, `LATERAL`, `DISTINCT ON`, recursive queries**: write them in Core. You can select ORM entities *from* them when you want objects: `select(Category).join(tree, Category.id == tree.c.id)`.
- **Hot paths with fixed SQL**: `text()` or plain psycopg (Module 22) are fine. `bazaar.repo` still serves the storefront's product page.
- **Per-object business operations** (create a product, ship an order, submit a review, check out): the ORM shines. Validation in Python, relationships, change tracking, optimistic locking, one transaction.

Exercise 2 makes the point with a real question: checking 96,670 December order lines against the price history takes 18 s through ORM objects (346 statements, more than 130,000 objects) and 0.84 s as one SQL statement with a `LATERAL` lookup.

---

## 16. The Bazaar ORM layer

The milestone (see [project.md](project.md)) adds `bazaar/orm/` to the package:

```text
src/bazaar/orm/
├── models.py     Base (naming convention, type_annotation_map, AsyncAttrs) and the mapped classes:
│                 User, Seller, Category, Product, Inventory, Order, OrderItem, Payment, Review,
│                 Address, CartItem, Promotion, ActiveProduct (read-only view); XID for xmin
├── session.py    BazaarSession (RLS after_begin hook, read-only guard), make_sessionmaker(),
│                 seller_session(), make_async_sessionmaker()
└── services.py   checkout(), order_history() / order_history_async(), create_product(),
                  get_own_product(), update_product() (optimistic), set_stock(), list_products(),
                  submit_review()
```

`submit_review()` shows the service style in full: validate input in Python (`InvalidReview`), check a business rule with one `EXISTS` query (only a buyer with a delivered order containing the product may review it: `ReviewNotAllowed`), insert inside a savepoint, and translate the unique-constraint violation `reviews_product_id_user_id_key` into `DuplicateReview`, leaving the session usable.

The tests in `tests/test_orm.py` cover the mapping against the database, types and relationships, hybrids and `column_property` in SQL, `MutableDict` tracking, the read-only view, checkout (success, `InsufficientStock` with a usable session afterwards), the N+1 guard (2 statements) and its counterpart showing the lazy version takes more than 40, `raiseload`, optimistic concurrency across two sessions, RLS as `bazaar_seller`, reviews (success, duplicate, validation, not allowed), and the async ORM (eager loading, `MissingGreenlet`, `awaitable_attrs`).

```bash
cd project/python && pytest tests/test_orm.py -v
```

---

## Summary

- The ORM maps classes to tables and adds a **Session**: an **identity map** (one object per row per session) and a **unit of work** (change objects; flush sends the minimal, correctly ordered SQL). A session is a short-lived, transaction-scoped workspace: one per request or job, never shared between threads or tasks.
- 2.0 mapping: `DeclarativeBase`, `Mapped[T]` (nullability from `T | None`), `mapped_column()`, `type_annotation_map`. Existing enums with `create_type=False`; JSONB with `MutableDict` (top-level changes only); arrays with `MutableList`; `Range` for range types; `deferred` for large columns.
- Relationships: direction from foreign keys, `back_populates`, `remote_side` for self-reference, `foreign_keys` when ambiguous, association objects for link tables with payload. `cascade="all, delete-orphan"` for owned children, and `passive_deletes=True` wherever the foreign key has `ON DELETE CASCADE`.
- Object states: transient, pending, persistent, deleted, detached, expired. **Flush** sends SQL; **commit** flushes and commits. `expire_on_commit=True` reloads after commit (and breaks detached and async use); Bazaar uses `False` and refreshes explicitly. **Autoflush** makes queries see pending changes and can surface errors in unrelated queries; `no_autoflush` suspends it.
- Query with `select(Model)` and `session.scalars()`/`execute()`/`get()`; join along relationships; `any()`/`has()` for `EXISTS`; `contains_eager()` fills a collection from your join (and only with the rows your filter let through).
- **N+1**: lazy loading in a loop turned one page into 417 statements. Fix it per query: `joinedload` for to-one, `selectinload` for collections (here 2 statements, 20× faster), avoid `subqueryload`, and end service queries with **`raiseload("*")`**. Guard with statement-count tests.
- Bulk: `insert(Model)` with dicts (with `returning(Model)` for objects), bulk update by primary key, ORM-enabled `UPDATE`/`DELETE` with `synchronize_session` (`auto`, `evaluate`, `fetch`, `False`). They bypass the unit of work.
- **Optimistic concurrency** with `version_id_col`; PostgreSQL's `xmin` works without a schema change (with an `xid` type), raising `StaleDataError` on lost updates. Atomic SQL updates beat retries for counters.
- Database-computed values: `Identity`, `server_default`, `Computed` (fetched with `RETURNING`), `FetchedValue` for trigger-set columns, `refresh()` for values changed through other tables. **Hybrids** work in Python and SQL; **`column_property`** maps a subquery (deferred).
- Map views read-only (and guard them in `before_flush`); call `place_order()` with `func` and typed bind parameters, inside `begin_nested()`, and translate SQLSTATEs.
- **RLS**: set `app.seller_id` with `set_config(..., true)` in an `after_begin` session event, from `session.info`; never a session-level `SET`.
- **Async ORM**: `AsyncSession`/`async_sessionmaker`, internals run in a greenlet; implicit IO (lazy loads, expired attributes) raises `MissingGreenlet`. Eager-load, use `awaitable_attrs` or `run_sync`, set `expire_on_commit=False`, one session per task.
- Test with a session joined to a rolled-back outer transaction using `join_transaction_mode="create_savepoint"`.
- Drop to **Core** for reports, aggregates, bulk work and complex SQL: entities cost ~20 µs and ~2 KB each; an aggregate in SQL was 4× faster than loading columns and 18× faster than loading entities.

## What's next

So far the schema has been owned by numbered SQL files and `reset.sh`. Module 26 hands it to **Alembic**: migrations generated from the metadata you wrote in Modules 24 and 25 (and the limits of autogenerate), data migrations, and the zero-downtime patterns that keep Bazaar online while its schema changes.
