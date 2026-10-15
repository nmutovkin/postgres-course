# Module 25: Exercises

<!-- nav:start -->
> [Theory](README.md) · **Exercises** · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 24 SQLAlchemy Core](../24-sqlalchemy-core/README.md) · [Course home](../../README.md) · [26 Migrations with Alembic →](../26-migrations/README.md)
<!-- nav:end -->

**Starting state.** `./project/bin/reset.sh 25`, then `alembic stamp 2600 && alembic upgrade head` in `project/python` (the package's schema, see the README's setup), and the `bazaar` package with Modules 24 and 25 (`bazaar.sa`, `bazaar.orm`) in `project/python/.venv`. Run solutions from `project/python`:

```bash
cd project/python && source .venv/bin/activate
export BAZAAR_DSN=postgresql://postgres:postgres@localhost:5432/bazaar
python my_exercise.py
```

Use the mapped classes of `bazaar.orm.models`, `bazaar.orm.session`, and `bazaar.sa.engine.count_statements()` to count statements. Exercises that write data roll back, or create their own committed rows and delete them, so the self-check numbers stay valid. Statement counts and row counts are exact; timings vary.

Solutions: [solutions/README.md](solutions/README.md), with runnable files in [solutions/](solutions/).

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

Without running anything, predict how many SQL statements each step sends, then check with `count_statements()`:

1. `session.get(Product, 20)`
2. `session.get(Product, 20)` again
3. `session.scalars(select(Product).where(Product.sku == p.sku)).one()`: is the result the same Python object?
4. set `p.price = Decimal("1.00")`, then count that seller's products priced at most 1.00
5. `session.rollback()`
6. read `p.price`
7. read `p.title`

Also print `session.dirty` after step 4's assignment, and the object's state after `session.expunge(p)`.

**Self-check:** 1, 0, 1, 2 (`UPDATE`, `SELECT`), 0, 1, 0 statements; the three loads return the same object; the count in step 4 is 1; after the rollback `p.price` is `24.99` again; after `expunge` the object is detached.

<details><summary>Hint</summary>

Identity map, autoflush, and expiry on rollback. `count_statements()` sees cursor executions only: does `ROLLBACK` go through a cursor?

</details>

---

## Exercise 2 ★: Map the price history

Map `product_price_history` as a class `PriceHistory` (composite primary key `(product_id, valid_from)`), and add a relationship `Product.price_history`, ordered by `valid_from`, **without editing `models.py`** (add it from your own module). Write `price_at(product, when)` that returns the price valid at `when` from the loaded history. Then check every order line of December 2025 (orders created that month): how many lines were sold at a price that wasn't valid at the order's `created_at`, and how many differ from today's price? Load everything with eager loading, and count statements and time. Finally, answer the same two questions with one SQL statement and compare.

**Self-check:** the mapping-only drift check still reports nothing. Product 3's history is `20.99` from 2022-02-10 and `19.99` from 2024-09-05, so on 2024-06-01 its price was 20.99. December 2025: 34,488 orders, 96,670 lines, **0** sold at an invalid price, **73** differ from today's price. The ORM version takes hundreds of statements and well over 10 s; the single statement under a second.

<details><summary>Hint</summary>

`Product.price_history = relationship(PriceHistory, order_by=..., viewonly=True)` after both classes exist. `bisect_right` on the `valid_from` list. For the SQL version, a `LATERAL` subquery picks the latest history row with `valid_from <= o.created_at` for each line (Module 24, section 6.8).

</details>

---

## Exercise 3 ★: Objects that outlive their session

[`solutions/ex03_detached.py`](solutions/ex03_detached.py) starts with `latest_orders_broken(user_id)`, which returns a user's three latest orders from a `with Session.begin()` block, using a plain `sessionmaker(engine)`. The caller then reads `o.total` and `len(o.items)`. Run it, explain the error, then show what happens with `expire_on_commit=False` alone. Fix it three different ways, each returning the same data for user 8.

**Self-check:** the broken version raises `DetachedInstanceError` (*attribute refresh operation cannot proceed*); with only `expire_on_commit=False` it raises `DetachedInstanceError` again (*lazy load operation of attribute 'items' cannot proceed*); all three fixes print `[(495378, Decimal('187.99'), 1), (481208, Decimal('622.98'), 2), (480393, Decimal('90.99'), 1)]`.

<details><summary>Hint</summary>

Two separate problems: expiry at commit, and a lazy relationship with no session to load through. Fixes: eager loading plus no expiry; returning plain dataclasses built inside the session; or letting the caller own the session (Bazaar's convention).

</details>

---

## Exercise 4 ★★: Find and fix the N+1

[`solutions/ex04_dashboard_start.py`](solutions/ex04_dashboard_start.py) contains `seller_dashboard(session, seller_id)`, an endpoint-like function returning seller 42's 50 latest orders with buyer name, shipping city, item lines ("2 x Title (Category)") and the sum of succeeded payments. Measure its statement count. Then rewrite it so that it returns **exactly the same data** with a fixed number of statements that doesn't depend on the number of orders, and so that any relationship you forgot raises instead of loading.

**Self-check:** before: 352 statements; after: 3 statements; the outputs are equal (`==`); the first entry is order 499613, `pending`, buyer *Arjun Lewis* in *Los Angeles*, two lines, paid 0.

<details><summary>Hint</summary>

Five relationships are touched: `user`, `shipping_address`, `items` → `product` → `category`, `payments`. Which are to-one, which are collections? Loader options chain: `selectinload(A.b).joinedload(B.c).joinedload(C.d)`.

</details>

---

## Exercise 5 ★★: Soft delete with a global filter

Bazaar never deletes products (sellers have no `DELETE` privilege, Module 15); it **archives** them. Make archived products invisible to every ORM query of a session class `CatalogSession`, including relationship loads, unless the query passes the execution option `include_archived=True`. Use the `do_orm_execute` event and `with_loader_criteria()`. Check: seller 42's product count, the lazy `Seller.products` collection, a `selectinload` of it, laptops counted through a join and through `Category.products`, `session.get()` of an archived product, and an order line that refers to an archived product. What does that last check tell you about using such a filter on order history?

**Self-check:** seller 42: 554 products (603 with `include_archived`); `Seller.products` has 554 entries both ways; 3,918 laptops both ways; `session.get(Product, 14)` returns `None` (product 14 is archived) and so does `item.product` for an order line of product 14, while `include_archived=True` returns it.

<details><summary>Hint</summary>

Skip column loads (`state.is_column_load`), which refresh objects you already have. `with_loader_criteria(Product, lambda cls: cls.status != ..., include_aliases=True)`. Relationship lazy loads inherit the criteria from the query that loaded the parent.

</details>

---

## Exercise 6 ★★: Lost updates, measured

Create a committed product with stock 0 (and delete it at the end). Then let 8 threads each add 1 to its `inventory.quantity` 25 times, three ways, resetting to 0 in between:

1. naive: load the `Inventory` object, wait 1 ms ("business logic"), assign `quantity + 1`, commit;
2. versioned: the same with a mapping of `inventory` that uses `xmin` as `version_id_col` (in a separate `DeclarativeBase`), retrying on `StaleDataError`;
3. atomic: an ORM-enabled `UPDATE ... SET quantity = quantity + 1`.

Report the final quantity, the number of retries, and the time.

**Self-check:** expected 200 each time. Naive ends far below (38 and 35 in two reference runs); versioned reaches exactly 200 with about 700 retries; atomic reaches 200 with no retries and is the fastest.

<details><summary>Hint</summary>

A separate `class Base(DeclarativeBase)` lets you map the same table again with different options. `XID` from `bazaar.orm.models` maps the `xid` type. Each thread needs its own session; the engine's pool needs 8 connections.

</details>

---

## Exercise 7 ★★: Async fan-out without lazy loads

With `AsyncSession` on the asyncpg engine, build a profile for each of the 10 users with the most orders: number of orders, units bought, distinct shops, and the most frequent category of the products they bought (ties broken alphabetically). Run the 10 profiles concurrently, one session per task, on a pool of 5. First show what happens with a lazy-loading version, then write the eager version with `raiseload("*")`, and check that concurrent and sequential runs agree.

**Self-check:** the lazy version raises `MissingGreenlet`. The top profile is `Profile(user_id=1, orders=234, units=674, shops=85, top_category='Cycling')`, then user 2 (138 orders, *Weights*) and user 4 (132 orders, *Chairs*); 1,219 orders in total.

<details><summary>Hint</summary>

Build the statement in a plain function so that sync and async code can share it; `selectinload(Order.items).joinedload(OrderItem.product).joinedload(Product.category)`.

</details>

---

## Exercise 8 ★★: Black Friday repricing

For seller 42's active products tagged `gift`, cut the price by 20 % (rounded to the cent in SQL) with **one** ORM-enabled `UPDATE ... RETURNING`, and record each new price in `product_price_history` (valid from 2025-11-28 00:00 UTC) with **one** bulk `INSERT`. Some of those products are already loaded in the session before the update: show that their in-memory prices are updated without a reload. Then show what `synchronize_session=False` does. Roll everything back.

**Self-check:** 90 products repriced in 2 statements; product 17778 goes from 70.99 to 56.79 in memory; the sum of the new prices is 7,439.10; 90 history rows; with `synchronize_session=False`, the object still says 56.79 while the database says 1.00.

<details><summary>Hint</summary>

`execution_options={"synchronize_session": "fetch"}` uses `RETURNING` on PostgreSQL. The history table needs no mapped class: `sa.insert(bazaar.sa.tables.product_price_history)` runs fine on an ORM session.

</details>

---

## Exercise 9 ★★: Discounts as hybrids

Add, from your own module, a `hybrid_method` `Promotion.is_active_at(ts)` and a `hybrid_method` `Product.price_at(ts)`: the price after the promotion active at `ts` (at most one, thanks to the exclusion constraint), rounded half-up to the cent, or the plain price. Both must work on instances (Python) and in queries (SQL). Then list the active laptops (category 3) that have a promotion active on 2025-11-28 at 12:00 UTC and cost less than 500 after the discount, cheapest first, in at most two statements, and verify that the Python and SQL prices agree for every product.

**Self-check:** 38 laptops; the cheapest is product 89039, 309.99 → 155.00; Python equals SQL for all 38.

<details><summary>Hint</summary>

`hybrid_method(python_fn, sql_fn)`. In SQL, a correlated scalar subquery on promotions with `coalesce(pct, 0)`; `Product.promotions.any(...)` for the "has a promotion" filter. PostgreSQL's `round()` rounds halves away from zero; which `Decimal` rounding mode matches it for positive prices?

</details>

---

## Exercise 10 ★★★: Ship an order as the seller

Write `ship_order(session, order_id)` for the seller portal: the session belongs to a seller (`session.info["seller_id"]`) and connects as **`bazaar_seller`**. Only paid orders of that seller can ship; shipping sets the status to `shipped` and `shipped_at` to the server's `now()`. Demonstrate, in a rolled-back transaction: seller 42 ships its oldest paid order; a pending order is refused; seller 43's order doesn't exist for seller 42; and changing `order.total` through the ORM fails. Explain each outcome in terms of Module 15's policies and grants.

**Self-check:** seller 42 sees 1,402 orders as `bazaar_seller`; order 497018 is shipped with exactly two statements (a `SELECT`, then `UPDATE orders SET status=..., shipped_at=now() ...`); order 498512 is refused (pending); order 372 raises `NotFound`; the `total` change fails with `InsufficientPrivilege: permission denied for table orders`.

<details><summary>Hint</summary>

Assigning `sa.func.now()` to an attribute sends the SQL expression. The ORM's `UPDATE` only includes changed columns, which is why the column-level `GRANT UPDATE (status, shipped_at, delivered_at)` works with it at all.

</details>

---

## Exercise 11 ★★★: Test code that commits

Write a pytest module (it can live anywhere; run it with `pytest path/to/test_file.py` from `project/python`) for two functions that manage their own transactions: `register_user(session, email, name)`, which commits, and on a duplicate e-mail (case-insensitively) rolls back and returns the existing user; and `leave_review(session, user_id, product_id, rating)`, which uses a savepoint and commits. Use a fixture that joins the session to an outer, always-rolled-back transaction. Prove four things: a registered user is visible in the same test; a duplicate leaves the session usable and returns the first user; the review works; and nothing from one test is visible in the next. Add a module-scoped check that the database's row counts are unchanged after all tests.

**Self-check:** 4 tests pass; the module-level row-count assertion passes, so no committed rows leaked.

<details><summary>Hint</summary>

`Session(bind=conn, join_transaction_mode="create_savepoint")` on a connection with an open transaction. A module-scoped fixture can compare counts in its teardown.

</details>

---

## Exercise 12 ★★★: RLS, async, and a leaking pool

Connect the async ORM as `bazaar_seller` with a pool of 3 connections, and run 200 concurrent "requests", each with its own `AsyncSession`, randomly as seller 41, 42, 43, 44 or anonymous (no seller id), each counting the products it can see and committing. Compare two session classes: `BazaarSession` (its `after_begin` hook uses `set_config(..., true)`), and a `LeakySession` whose hook uses `set_config(..., false)`. Count the requests that saw the wrong number.

**Self-check:** expected counts 690, 603, 613, 584 and 0 (anonymous). `BazaarSession`: all 200 correct. `LeakySession`: anonymous requests see another seller's products (44 of 200 wrong with `random.seed(12)`).

<details><summary>Hint</summary>

`make_async_sessionmaker(engine, sync_session_class=...)`: the hook is a sync event on the sync session class. Why can only anonymous requests be wrong in the leaky version?

</details>
