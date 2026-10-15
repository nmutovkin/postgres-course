# Module 25: Solutions

<!-- nav:start -->
> [Theory](../README.md) · [Exercises](../exercises.md) · [Project](../project.md) · **Solutions**  
> [← 24 SQLAlchemy Core](../../24-sqlalchemy-core/README.md) · [Course home](../../../README.md) · [26 Migrations with Alembic →](../../26-migrations/README.md)
<!-- nav:end -->

Every solution is a runnable script in this directory, run against `./project/bin/reset.sh 25` brought to the package's schema (`alembic stamp 2600 && alembic upgrade head`), with Python 3.13, SQLAlchemy 2.1.4, psycopg 3.3.6, asyncpg 0.32.0 and PostgreSQL 17.11. The outputs below come from those runs. Run them from `project/python` with the venv active:

```bash
cd project/python && source .venv/bin/activate
python ../../modules/25-sqlalchemy-orm/solutions/ex04_dashboard_fixed.py
pytest ../../modules/25-sqlalchemy-orm/solutions/test_ex11_isolation.py -v
```

---

<!-- toc:start -->
**Contents**

- [Exercise 1 ★: Trace a session](#exercise-1--trace-a-session)
- [Exercise 2 ★: Map the price history](#exercise-2--map-the-price-history)
- [Exercise 3 ★: Objects that outlive their session](#exercise-3--objects-that-outlive-their-session)
- [Exercise 4 ★★: Find and fix the N+1](#exercise-4--find-and-fix-the-n1)
- [Exercise 5 ★★: Soft delete with a global filter](#exercise-5--soft-delete-with-a-global-filter)
- [Exercise 6 ★★: Lost updates, measured](#exercise-6--lost-updates-measured)
- [Exercise 7 ★★: Async fan-out without lazy loads](#exercise-7--async-fan-out-without-lazy-loads)
- [Exercise 8 ★★: Black Friday repricing](#exercise-8--black-friday-repricing)
- [Exercise 9 ★★: Discounts as hybrids](#exercise-9--discounts-as-hybrids)
- [Exercise 10 ★★★: Ship an order as the seller](#exercise-10--ship-an-order-as-the-seller)
- [Exercise 11 ★★★: Test code that commits](#exercise-11--test-code-that-commits)
- [Exercise 12 ★★★: RLS, async, and a leaking pool](#exercise-12--rls-async-and-a-leaking-pool)

<!-- toc:end -->

## Exercise 1 ★: Trace a session

[`ex01_session_trace.py`](ex01_session_trace.py)

```text
1. session.get(Product, 20)                          1 statement(s) ['SELECT']
2. session.get(Product, 20) again                    0 statement(s) []
3. select(Product).where(sku == ...)                 1 statement(s) ['SELECT']
   a is b is c: True
   after a.price = 1.00: dirty = [Product(id=20, sku='S5-000020', price=Decimal('1.00'))] | modified: [Decimal('1.00')]
4. count that seller's products priced <= 1.00       2 statement(s) ['UPDATE', 'SELECT']
   count = 1 (our unflushed change was flushed first: autoflush)
5. session.rollback()                                0 statement(s) []
   expired after rollback: ['attributes', 'category', 'category_id', 'created_at'] ...
6. read a.price                                      1 statement(s) ['SELECT']
   a.price = 24.99
7. read a.title (same row, already reloaded)         0 statement(s) []
   after expunge: detached | a in session: False
```

**Reasoning, step by step.**

1. The identity map is empty: one `SELECT`.
2. `get()` checks the identity map first and finds `(Product, 20)`: no SQL.
3. A `select()` always goes to the database, but the row's identity is already mapped, so the ORM returns the existing object (and does not overwrite its attributes).
4. The assignment only marks the object dirty (`inspect(a).attrs.price.history` shows the pending value). The count query triggers an **autoflush**: the `UPDATE` (with `AND products.xmin = ...`, because `Product` is versioned), then the `SELECT`, which therefore counts our own change.
5. `rollback()` sends `ROLLBACK`, which psycopg issues on the connection, not through a cursor, so `count_statements()` (a cursor event) doesn't see it. The rollback **expires** every object: their values may no longer be true.
6. Reading an expired attribute reloads the *whole row* (all non-deferred columns) in a new transaction: one `SELECT`, and the price is back to 24.99.
7. ...so `title` is already there.

`expunge()` removes the object from the session: it is now detached, and a lazy load or refresh through it would raise.

---

## Exercise 2 ★: Map the price history

[`ex02_price_history.py`](ex02_price_history.py)

```text
mapping drift: []
product 3 history: [PriceHistory(3, 2022-02-10, 20.99), PriceHistory(3, 2024-09-05, 19.99)]
price of product 3 on 2024-06-01: 20.99
current price: 19.99
34488 orders, 96670 lines in December 2025 in 346 statements: 0 paid a price that wasn't valid, 73 differ from today's price (18.1 s)
one SQL statement: 0 wrong, 73 changed (0.84 s)
```

**Reasoning.** The class declares the composite primary key by marking both columns `primary_key=True`; the identity map key is then `(PriceHistory, (product_id, valid_from))`. The relationship is added after the fact, `Product.price_history = relationship(...)`: declarative classes accept new mapped attributes until the mappers are configured (on first use), so a separate module can extend `Product` without editing `models.py`. It's `viewonly=True`, because history rows are written by price-change code, never by appending to this collection, and the drift check (`mapping_only=True`) confirms the new class matches the table.

`price_at()` relies on `order_by=PriceHistory.valid_from`: the collection is sorted, so `bisect_right` finds the last row with `valid_from <= when`.

The December check is correct in both versions, and the comparison is the lesson. The ORM version loads 34,488 orders, 96,670 items, their products and price histories: 346 statements (selectin batches of 500 keys) and more than 130,000 objects, 18 s. The single statement lets PostgreSQL do the lookup per line with a `LATERAL` subquery on the `(product_id, valid_from)` primary key: 0.84 s, and no objects at all. Use the ORM version for one order's detail page; use SQL for a month's audit (section 15 of the theory).

**Alternative.** A `relationship` with `primaryjoin` can express "the history row valid at the order's time" only awkwardly (it depends on two tables); a `column_property` on `OrderItem` with a correlated subquery could expose the historical price per line, at the cost of a subquery in every `OrderItem` load unless deferred.

---

## Exercise 3 ★: Objects that outlive their session

[`ex03_detached.py`](ex03_detached.py)

```text
broken (expired + detached)        DetachedInstanceError: Instance <Order at 0x7f6c30baba10> is not bound to a Session; attribut...
expire_on_commit=False, lazy items DetachedInstanceError: Parent instance <Order at 0x7f6c3032c640> is not bound to a Session; l...
fix 1: no expiry + selectinload    OK: [(495378, Decimal('187.99'), 1), (481208, Decimal('622.98'), 2), (480393, Decimal('90.99'), 1)]
fix 2: DTOs                        OK: [(495378, Decimal('187.99'), 1), (481208, Decimal('622.98'), 2), (480393, Decimal('90.99'), 1)]
fix 3: caller owns the session     OK: [(495378, Decimal('187.99'), 1), (481208, Decimal('622.98'), 2), (480393, Decimal('90.99'), 1)]
```

**Reasoning.** Two problems hide behind one error class:

1. `Session.begin()` **commits** at the end of the block, and with the default `expire_on_commit=True` every loaded attribute is discarded. Then the block **closes** the session. Reading `o.total` needs a refresh, and there's no session to refresh with: "attribute refresh operation cannot proceed".
2. With `expire_on_commit=False`, `o.total` is still there, but `o.items` was never loaded: "lazy load operation of attribute 'items' cannot proceed".

The three fixes are three architectural choices:

- **Load everything, don't expire** (`expire_on_commit=False` + `selectinload`): ORM objects leave the function as snapshots. Simple; the caller must not touch anything that wasn't loaded (add `raiseload("*")` to make that explicit).
- **Return plain data** (frozen dataclasses built inside the session): the cleanest boundary for an API layer; Module 28's FastAPI responses will be built this way (pydantic models).
- **Let the caller own the session**: Bazaar's service convention. The function never closes or commits anything, and the caller's session is still open when the data is used.

---

## Exercise 4 ★★: Find and fix the N+1

[`ex04_dashboard_start.py`](ex04_dashboard_start.py), [`ex04_dashboard_fixed.py`](ex04_dashboard_fixed.py)

```text
before  50 orders, 352 statements,   262 ms
after   50 orders,   3 statements,    17 ms
         SELECT orders.id, orders.user_id, orders.seller_id, orders.status, orders.total, orders.created_at, orders.paid_at, orde ...
         SELECT order_items.order_id, order_items.product_id, order_items.quantity, order_items.unit_price, categories_1.id, cate ...
         SELECT payments.order_id, payments.id, payments.amount, payments.provider, payments.provider_ref, payments.status, payme ...
identical output: True
{'order': 499613, 'status': 'pending', 'buyer': 'Arjun Lewis', 'city': 'Los Angeles', 'lines': ['1 x Threadline Recycled Slim Jeans - Black (Jeans)', '1 x Indigo & Rye Recycled Parka - Red (Jackets)'], 'paid': 0}
```

**Reasoning.** Count what the loop touches: per order `o.user`, `o.shipping_address`, `o.items`, `o.payments` (4 lazy loads), and per item `i.product` and `i.product.category` (identity-map hits when already loaded, queries otherwise). That's how 50 orders turn into 352 statements.

The fix classifies each relationship:

- **to-one** (`user`, `shipping_address`): `joinedload` into the main query; one row per order either way;
- **collections** (`items`, `payments`): `selectinload`, one `IN` query each;
- the items' **to-one chain** (`product`, `product.category`): `joinedload` *into the items query*: `selectinload(Order.items).joinedload(OrderItem.product).joinedload(Product.category)`.

Three statements whatever the number of orders, 15× faster here. `raiseload("*")` turns any relationship added to the output later without a loader into an immediate error. One more detail: the fixed version compares `p.status == PaymentStatus.SUCCEEDED`; the original's `== "succeeded"` works too (a `StrEnum` equals its value), but the enum makes typos impossible.

**Alternative.** `paid` is a sum, so the payments don't need to be loaded as objects at all: a `column_property` (deferred) or a correlated subquery in the select (`select(Order, paid_subquery)`) would deliver the number directly. Worth it when orders have many payments.

---

## Exercise 5 ★★: Soft delete with a global filter

[`ex05_soft_delete.py`](ex05_soft_delete.py)

```text
1. count of seller 42's products: 554 | include_archived: 603
2. lazy Seller.products: 554
   with selectinload in another query: 554
3. laptops via a join: 3918 | via the relationship: 3918
4. session.get() of archived product 14 -> None
   an order line of it: product_id = 14 | item.product = None
   with include_archived: Product(id=14, sku='S5-000014', price=Decimal('499.99'))
```

**Reasoning.** `do_orm_execute` fires for every ORM statement the session executes, including the internal ones for lazy loads and `get()`. The hook adds `with_loader_criteria(Product, lambda cls: cls.status != ARCHIVED, include_aliases=True)`, which SQLAlchemy applies wherever `Product` appears: the main FROM, joins (`Category.products` joined in a count), aliases, and relationship loads of objects the query produced. The lambda is analyzed once and cached, so the criterion costs nothing per execution. The hook skips `is_column_load` (refreshing attributes of an object already loaded), where adding criteria could make a known object "disappear" mid-session. The opt-out is an execution option, so it can be set per statement (`session.scalar(stmt, execution_options={"include_archived": True})`) or per `get()`.

The last check is the important one: an order line of an archived product now has `item.product = None`. Order history must show what was bought even after the product is archived. In a real application, either the order-history code passes `include_archived=True`, or the filter applies only to catalog sessions (that's why it lives on `CatalogSession`, not on every session).

**Alternative.** A database view of non-archived products mapped as the "catalog product" class (section 11.1) gives the same effect without event hooks, but needs a second class. Row-level security could enforce it in the database for a storefront role.

---

## Exercise 6 ★★: Lost updates, measured

[`ex06_optimistic_retry.py`](ex06_optimistic_retry.py)

```text
naive read-modify-write      expected 200, got  38  conflicts/retries:   0  0.48 s
versioned (xmin) + retry     expected 200, got 200  conflicts/retries: 708  2.47 s
atomic UPDATE quantity + 1   expected 200, got 200  conflicts/retries:   0  0.36 s
```

**Reasoning.** The naive version is Module 10's lost update in ORM clothing: each thread reads `quantity`, computes `quantity + 1` in Python, and writes the result with `UPDATE inventory SET quantity = 7 WHERE product_id = ...`. Under `READ COMMITTED`, eight threads that read 6 all write 7. 162 of 200 increments vanished, and no error was raised anywhere.

The versioned mapping (`VersionedStock`, mapped onto the same `inventory` table in a *separate* `DeclarativeBase`, with `xmin` as `version_id_col`) turns every write into `UPDATE ... WHERE product_id = :id AND xmin = :seen`. A thread that read a stale row matches 0 rows, the ORM raises `StaleDataError`, and the loop re-reads and re-applies. Correct, but with 8 threads hammering one row, 708 retries: optimistic concurrency is for **low** contention, like two people editing the same product.

The atomic version doesn't read at all: `UPDATE inventory SET quantity = inventory.quantity + 1` lets PostgreSQL serialize the increments with row locks. For counters and stock, that (or `place_order()`'s `SELECT ... FOR UPDATE`, Module 10) is the right tool. Use version checks where the new value genuinely depends on a human looking at the old one.

---

## Exercise 7 ★★: Async fan-out without lazy loads

[`ex07_async_fanout.py`](ex07_async_fanout.py)

```text
naive: MissingGreenlet - greenlet_spawn has not been called; can't call await_() here ...
Profile(user_id=1, orders=234, units=674, shops=85, top_category='Cycling')
Profile(user_id=2, orders=138, units=505, shops=64, top_category='Weights')
Profile(user_id=4, orders=132, units=442, shops=57, top_category='Chairs')
10 profiles: sequential 0.31 s, gather (pool of 5) 0.21 s; total orders 1219
```

**Reasoning.** `profile()` is ordinary Python over ORM objects: it touches `o.items`, `i.product`, `i.product.category`. Given lazily loaded orders on an `AsyncSession`, the first `o.items` is an implicit load outside the greenlet: `MissingGreenlet`. The eager statement (`history_stmt()`) loads the whole graph in two statements per user (orders; items joined to products and categories), and ends with `raiseload("*")`, so the same mistake in sync tests reads "not available due to lazy='raise'" instead.

Each task opens its own `AsyncSession` (and so its own pooled connection); `gather` runs 10 of them over a pool of 5. The speed-up is modest because each profile is only two fast queries; the structure is what matters.

---

## Exercise 8 ★★: Black Friday repricing

[`ex08_black_friday.py`](ex08_black_friday.py)

```text
90 products repriced in 2 statements
   product 17778: 70.99 -> 56.79 (in-memory object, no reload)
   product 18160: 124.99 -> 99.99 (in-memory object, no reload)
   product 18663: 16.99 -> 13.59 (in-memory object, no reload)
sum of new prices: 7439.10 | history rows dated 2025-11-28: 90
synchronize_session=False -> in memory: 56.79 | in the database: 1.00
```

**Reasoning.** The ORM-enabled `UPDATE` is one statement: `UPDATE products SET price = round(products.price * 0.80, 2) WHERE ... RETURNING products.id, products.price`. The returned rows feed the history `INSERT` directly (executemany on the Core table: no objects needed). With `synchronize_session="fetch"`, the ORM uses the affected primary keys from `RETURNING` (no extra query on PostgreSQL) to update the three products that were already in the identity map. `"evaluate"` would have to compute `round(price * 0.80, 2)` in Python and can't evaluate `func.round`; `"auto"` would fall back to `"fetch"` anyway.

With `synchronize_session=False`, the session isn't told: the loaded object keeps saying 56.79 while the row says 1.00. That's fine for a job that holds no objects; it's a bug in a request that renders the objects afterwards.

**In production:** a price change in Bazaar also needs the history row, and possibly an audit trail; doing both in one transaction with two set-based statements is what keeps them consistent. Note that the ORM bulk update *by primary key* wouldn't work here at all: `Product` is versioned with `xmin` (section 8.3 of the theory).

---

## Exercise 9 ★★: Discounts as hybrids

[`ex09_promotions_hybrid.py`](ex09_promotions_hybrid.py)

```text
38 discounted laptops under 500 on Black Friday (2 statements)
   89039 Lumio Wireless Laptop 189          309.99 -> 155.00
   31366 Sonora Mini Laptop 528             309.99 -> 247.99
   46367 Brightline Max Ultrabook 366       359.99 -> 251.99
Python == SQL for all: True
```

**Reasoning.** `hybrid_method(python_fn, sql_fn)` attaches two implementations to one name. On instances, `p.price_at(ts)` looks through the loaded `promotions` (`Range.contains()` works in Python) and applies the discount with `Decimal.quantize(..., ROUND_HALF_UP)`; on the class, `Product.price_at(ts)` is `round(price * (1 - coalesce(<correlated subquery>, 0) / 100), 2)`. Rounding is where the two can silently disagree: PostgreSQL's `round(numeric)` rounds halves away from zero, which for positive prices is `ROUND_HALF_UP`, not Python's banker's rounding (Module 24, Exercise 10). The final check compares both for all 38 products.

The query uses the hybrid three times: in `WHERE` (`< 500`), in `ORDER BY`, and the relationship filter `Product.promotions.any(Promotion.is_active_at(noon))`, an `EXISTS`. `selectinload(Product.promotions)` makes the Python side available without lazy loads: 2 statements.

The result was checked independently with SQL in psql: 38 rows, minimum discounted price 155.00.

---

## Exercise 10 ★★★: Ship an order as the seller

[`ex10_ship_order_rls.py`](ex10_ship_order_rls.py)

```text
seller 42 sees 1402 orders; current_user = bazaar_seller
shipped order 497018: shipped, shipped_at=2026-10-14 10:22
   statements: ['SELECT orders.id, orders.user_id, orders.seller_id, orders.status, ord', 'UPDATE orders SET status=%(status)s, shipped_at=now() WHERE orders.id ']
order 498512: ValueError: order 498512 is pending, only paid orders can ship
order 372: NotFound: order 372
changing total: InsufficientPrivilege - permission denied for table orders
```

**Reasoning.** Every outcome comes from Module 15's design, reached through the ORM:

- **1,402 orders**: the `orders_seller_select` policy filters by `current_seller_id()`, which reads `app.seller_id`, set by `BazaarSession`'s `after_begin` hook with `SET LOCAL` semantics.
- **Shipping works** although `bazaar_seller` may only update three columns: the ORM's `UPDATE` lists only *changed* attributes (`status`, `shipped_at`), and the `orders_seller_update` policy allows the row. `shipped_at = sa.func.now()` sends the expression, so the timestamp is the server's transaction time; the ORM expires the attribute and reloads it when printed.
- **Order 372** belongs to seller 43: `session.get()` returns `None` because the row is invisible, and the service raises `NotFound`. (The explicit `seller_id` check is defense in depth.)
- **Changing `total`** fails with SQLSTATE 42501: `bazaar_seller` has no `UPDATE` privilege on that column, so PostgreSQL reports "permission denied for table orders" (the message names the table even for column privileges). The flush ran inside `begin_nested()`, so only the savepoint is rolled back.

**Alternative.** The status transition could be enforced in the database too (a trigger allowing only `paid → shipped → delivered` for the seller role), so that a different client can't skip the rule.

---

## Exercise 11 ★★★: Test code that commits

[`test_ex11_isolation.py`](test_ex11_isolation.py)

```text
../../modules/25-sqlalchemy-orm/solutions/test_ex11_isolation.py::test_register_commits_and_is_visible PASSED [ 25%]
../../modules/25-sqlalchemy-orm/solutions/test_ex11_isolation.py::test_duplicate_rolls_back_only_the_savepoint PASSED [ 50%]
../../modules/25-sqlalchemy-orm/solutions/test_ex11_isolation.py::test_review_with_nested_savepoint PASSED [ 75%]
../../modules/25-sqlalchemy-orm/solutions/test_ex11_isolation.py::test_nothing_from_other_tests_is_visible PASSED [100%]
============================== 4 passed in 0.59s ===============================
```

**Reasoning.** The fixture opens a connection, begins the outer transaction, and binds a `Session` to that connection with `join_transaction_mode="create_savepoint"`. From then on:

- the session's transaction is a `SAVEPOINT` inside the outer transaction;
- `session.commit()` in `register_user` releases the savepoint and the session starts a new one for its next work; the user is visible inside the test because the outer transaction is still the same;
- the duplicate e-mail raises `IntegrityError` (the unique index on `lower(email)`); `session.rollback()` rolls back *to the savepoint*, the outer transaction survives, and the function can query the existing user;
- `leave_review`'s own `begin_nested()` is a savepoint inside the session's savepoint;
- the fixture's teardown rolls back the outer transaction: nothing was ever committed.

The module-scoped `baseline` fixture counts users and reviews (with a different connection) before the first test and after the last; the assertion passing proves no row leaked. The other join modes are worth knowing: `"rollback_only"` ignores the session's `commit()` but lets its `rollback()` roll back the *outer* transaction, which breaks the rest of the test after the duplicate e-mail; `"control_fully"` hands the outer transaction to the session entirely; and the default, `"conditional_savepoint"`, behaves like `"rollback_only"` on a connection in a plain transaction and like `"create_savepoint"` on one that is already inside a savepoint. Say what you mean: `"create_savepoint"`.

---

## Exercise 12 ★★★: RLS, async, and a leaking pool

[`ex12_async_rls.py`](ex12_async_rls.py)

```text
expected: {41: 690, 42: 603, 43: 613, 44: 584, None: 0}
SET LOCAL (BazaarSession)    {'ok': 200}
session-level SET            {'ok': 156, 'wrong for None': 44}
```

**Reasoning.** 200 tasks share 3 pooled connections, so each connection serves dozens of sessions in turn. `BazaarSession` sets `app.seller_id` with `set_config(..., true)` (local to the transaction): at commit the setting disappears, and the next session on that connection starts clean. All 200 counts are right, including the anonymous ones (0 products: `current_seller_id()` is `NULL`, and the policy matches nothing).

`LeakySession` uses `set_config(..., false)`: a **session-level** setting that survives the commit and goes back to the pool with the connection. The pool's reset-on-return only rolls back. A seller's request overwrites whatever the connection had, so seller requests are always right; but an **anonymous** request that gets a connection last used by seller 43 runs as seller 43: 44 of the anonymous requests saw another shop's products. In a storefront, that's a data leak that no unit test with one connection would ever show.

This is Module 22's rule (section 14.4) again, reached through a different stack: session state must be transaction-scoped, or reset explicitly on checkout/return. With PgBouncer in transaction mode (Module 17), even the "reset on return" option disappears, and `SET LOCAL` is the only safe form.
