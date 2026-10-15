# Module 24: Exercises

<!-- nav:start -->
> [Theory](README.md) · **Exercises** · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 23 Async Python: psycopg async and asyncpg](../23-async-python/README.md) · [Course home](../../README.md) · [25 SQLAlchemy ORM →](../25-sqlalchemy-orm/README.md)
<!-- nav:end -->

**Starting state.** `./project/bin/reset.sh 24`, then `alembic stamp 2600 && alembic upgrade head` in `project/python` (the package's schema, see the README's setup), and the `bazaar` package installed in `project/python/.venv` with SQLAlchemy (`uv sync`). Run solutions from `project/python`:

```bash
cd project/python && source .venv/bin/activate
export BAZAAR_DSN=postgresql://postgres:postgres@localhost:5432/bazaar
python my_exercise.py
```

You may use the milestone's helpers: `bazaar.sa.engine.create_engine()` / `create_async_engine()` / `count_statements()`, the tables in `bazaar.sa.tables`, and `bazaar.sa.reports`. Exercises that write data roll back, so the self-check numbers stay valid however often you run them. Timings vary by machine; counts and properties don't.

Solutions: [solutions/README.md](solutions/README.md), with runnable files in [solutions/](solutions/).

---

<!-- toc:start -->
**Contents**

- [Exercise 1 ★: An engine that says who it is](#exercise-1--an-engine-that-says-who-it-is)
- [Exercise 2 ★: Loyal buyers](#exercise-2--loyal-buyers)
- [Exercise 3 ★: Five queries that lie](#exercise-3--five-queries-that-lie)
- [Exercise 4 ★★: Four reports, translated and proven](#exercise-4--four-reports-translated-and-proven)
- [Exercise 5 ★★: A product listing with filters, sorts and cursors](#exercise-5--a-product-listing-with-filters-sorts-and-cursors)
- [Exercise 6 ★★: A cart that merges](#exercise-6--a-cart-that-merges)
- [Exercise 7 ★★: Ten thousand price changes](#exercise-7--ten-thousand-price-changes)
- [Exercise 8 ★★: A data dictionary from the Inspector](#exercise-8--a-data-dictionary-from-the-inspector)
- [Exercise 9 ★★: The seller statement on asyncio](#exercise-9--the-seller-statement-on-asyncio)
- [Exercise 10 ★★★: Commission to the cent](#exercise-10--commission-to-the-cent)
- [Exercise 11 ★★★: A drift check that can't be fooled](#exercise-11--a-drift-check-that-cant-be-fooled)
- [Exercise 12 ★★★: Export a Core query, two ways](#exercise-12--export-a-core-query-two-ways)

<!-- toc:end -->

## Exercise 1 ★: An engine that says who it is

Create an engine (directly with `sa.create_engine`, not the package helper) with a pool of 2 and no overflow, whose connections report `application_name = 'm24-ex1'`, have `statement_timeout = 2s` and `lock_timeout = 500ms` from the first moment, and pre-ping. Then prove three things:

1. A connection checked out with `isolation_level="REPEATABLE READ"` keeps its snapshot: count `jobs`, commit a new job from *another* connection, count again.
2. After that connection goes back to the pool, the next checkout is `read committed` again.
3. `SELECT pg_sleep(3)` is cancelled by the server after 2 seconds. Which exception class do you catch, and what is `e.orig`?

Clean up the job you inserted.

**Self-check:** the four `SHOW` values are `m24-ex1`, `2s`, `500ms`, `read committed`; the two counts are equal; the timeout fires after 2.0 s with `OperationalError` wrapping psycopg's `QueryCanceled`.

<details><summary>Hint</summary>

`connect_args={"application_name": ..., "options": "-c statement_timeout=2000 -c lock_timeout=500"}`. Isolation: `engine.connect().execution_options(isolation_level=...)`. The other connection can come from the same engine: that's why the pool needs 2.

</details>

---

## Exercise 2 ★: Loyal buyers

Using Core only (no `text()`), list the 5 shops with the most **distinct buyers** in 2025 (orders created in 2025 with status paid, shipped or delivered), with the number of **repeat buyers** (2 or more such orders at that shop) and the repeat rate in percent, rounded to one decimal. Print the generated SQL and the rows.

**Self-check:** the first row is seller 1, `Happy Outlet 1`, 23,529 buyers, 3,940 repeat buyers, 16.7 %. The fifth is `Cozy Store 4` with 4.9 %.

<details><summary>Hint</summary>

Group by (seller, buyer) in a subquery first, then count per seller with `func.count().filter(...)`. If PostgreSQL complains that `round(double precision, integer)` does not exist, look at the type of `100.0` (section 6.9).

</details>

---

## Exercise 3 ★: Five queries that lie

[`solutions/ex03_traps_start.py`](solutions/ex03_traps_start.py) contains five small Core queries, each with a docstring saying what it should return. Run it: some return the wrong number, one returns the wrong number of rows (and warns), and one cannot even be built. Fix each function without changing its docstring, and explain in one sentence per function what went wrong.

**Self-check:** after fixing: `titles_with_percent_sign` 0, `tagged_gift` 30,093, `seller7_catalog_value_plus_10pct` `601126.262` (three decimals, not `601126.26`), `first_two_products_with_shop` 2 rows, `expensive_active` 64,019.

<details><summary>Hint</summary>

Look for: wildcards inside `contains()`, array equality versus containment, the type of a Python `1.1`, a FROM list without a join condition, and Python's `and`.

</details>

---

## Exercise 4 ★★: Four reports, translated and proven

Translate these four reports to Core and prove, row for row, that each returns exactly what the SQL returns (run the SQL with `conn.exec_driver_sql()` and compare lists of tuples):

- **(a)** Revenue per month of 2025 (UTC months; paid/shipped/delivered) with the month-over-month growth in percent (`round(100 * (revenue / previous - 1), 1)`, `NULL` for January).
- **(b)** For every category under *Books* (the whole subtree), the top 3 products by units sold in 2025 (sales statuses), ties broken by product id.
- **(c)** Buyers who ordered from at least 5 different shops in 2025 (any status) and have never written a review, with their number of shops and orders, most orders first.
- **(d)** Sellers whose revenue fell by more than half from November to December 2025, among those with at least 1,000 in November; biggest drop first.

Write the SQL yourself first (it's Module 05/06 material), then the Core version.

**Self-check:** (a) 12 rows, January revenue `8033914.73`; (b) 33 rows, the first is category 16, product 2 *The Burning Protocol*, 416 units; (c) 90 rows, the first is user 45303 with 8 shops and 8 orders; (d) 299 rows, the first is seller 726 *Nordic Store 726* (21,152.53 → 1,937.89). All four comparisons are equal.

<details><summary>Hint</summary>

`func.lag(x).over(order_by=...)`; `func.timezone("UTC", col)` is `col AT TIME ZONE 'UTC'`; a recursive CTE for the subtree, used as `p.c.category_id.in_(select(sub.c.id))`; `func.count(sa.distinct(col))`; `~sa.exists().where(...)`. A window over an aggregate (`row_number() OVER (ORDER BY sum(...))`) is allowed in the same SELECT as the `GROUP BY`.

</details>

---

## Exercise 5 ★★: A product listing with filters, sorts and cursors

Write `list_products(conn, *, category=None, min_price=None, max_price=None, tags_all=None, tags_any=None, attrs=None, in_stock=False, q=None, sort="price", limit=20, cursor=None)` returning a page of active products and an **opaque** `next_cursor`:

- `category` is a slug and includes the whole subtree; `attrs` is a dict that `attributes` must contain; `in_stock` means available > 0; `q` is a web-search query.
- `sort` is one of `price`, `-price`, `newest`, `relevance` (only with `q`); anything else raises `ValueError` (no identifier ever comes from the caller).
- Keyset pagination, with `id` as the tie-breaker. `relevance` sorts by rank descending but id *ascending*.

Prove it: for each case below, walking all pages with `limit=25` returns exactly the ids of one unpaged call.

```python
dict(category="audio", max_price=Decimal("30"), in_stock=True, sort="price"),
dict(category="computers", tags_any=["bestseller", "new"], sort="-price"),
dict(category="laptops", attrs={"ram_gb": 64, "storage": {"type": "ssd"}}, sort="newest"),
dict(q="wireless headphones", sort="relevance", min_price=Decimal("50")),
dict(tags_all=["gift", "eco"], category="kitchen", sort="price"),
```

**Self-check:** 784, 2,163, 705, 387 and 180 rows; first ids 15468, 194299, 199945, 110124, 22181. Paged equals unpaged in all five cases.

<details><summary>Hint</summary>

A row comparison `(a, b) > (x, y)` only works when all keys sort in the same direction; for mixed directions write `a < x OR (a = x AND b > y)`. The cursor round-trips through JSON: what type comes back for a `Decimal`, a `datetime`, a `ts_rank`? If `relevance` pages don't match, print the rank you stored and the rank PostgreSQL compares it with: `ts_rank()` returns `real`.

</details>

---

## Exercise 6 ★★: A cart that merges

Implement with Core:

- `add_to_cart(conn, user_id, product_id, qty)`: one statement that adds a new line or increases the quantity of an existing one, refreshes `added_at`, and returns `(new_quantity, inserted: bool)`.
- `merge_carts(conn, from_user, to_user)`: moves a (guest) cart into a user's cart **in one INSERT statement** plus one DELETE, atomically, adding quantities where both carts contain the product; returns `{"inserted": n, "updated": m}`.

Test it (and roll back): add 2 × product 16856 to user 1, then product 20; give user 7 a cart with products 18257 (×1), 21089 (×3), 5179 (×1); merge 7 into 1.

**Self-check:** `(4, False)`, then `(1, True)`; the merge reports 2 inserted and 1 updated; user 1's cart is `[(20, 1), (5179, 1), (16856, 4), (18257, 2), (21089, 3), (39415, 1)]` and user 7 has 0 lines.

<details><summary>Hint</summary>

`postgresql.insert(t).from_select(columns, select)` plus `.on_conflict_do_update(...)`. `RETURNING xmax = 0` tells inserted from updated. `conn.begin_nested()` makes the pair atomic without committing.

</details>

---

## Exercise 7 ★★: Ten thousand price changes

Take the first 10,000 products of seller 1 (by id) and raise each price by 5 %, rounded half-up to the cent. Apply the changes three ways, each in its own rolled-back transaction, and time them:

1. one `update()` with bind parameters, executed with the list of 10,000 parameter sets;
2. `UPDATE ... FROM (VALUES ...)` with SQLAlchemy's `values()` construct, 2,000 rows per statement;
3. `COPY` into a temporary table through the driver connection, then one `UPDATE ... FROM`.

Check that each leaves the same sum of prices over those products.

**Self-check:** all three give `sum=1865555.75`. On the reference machine: about 3.1 s, 2.5 s and 1.8 s. Explain why the ratios are much smaller than the bulk-insert ratios of section 7.4.

<details><summary>Hint</summary>

`sa.update(p).where(p.c.id == sa.bindparam("pid")).values(price=sa.bindparam("new_price"))`: bind parameter names must not collide with column names. `sa.values(sa.column("pid", ...), sa.column("new_price", ...), name="v").data([...])`. For the explanation, look at the triggers on `products` (Module 09).

</details>

---

## Exercise 8 ★★: A data dictionary from the Inspector

Write `data_dictionary(conn) -> str` that produces Markdown documentation of every Bazaar table (not the partitions of `events`): the table comment, then one row per column with type, nullability, default (show `identity (always)` and `generated` instead of the raw text), primary key and foreign-key target (with `ON DELETE` when set), and column comment. Use only the Inspector (plus one query to find partitions).

**Self-check:** 16 tables, starting `addresses, audit_log, cart_items, categories, events, ...`. The `orders` section starts with the comment *One order per (buyer, seller) checkout split.*, has 11 columns, `id` shows `identity (always)` and `PK`, `user_id` shows `users.id`, and `total` carries the column comment *Denormalised: sum(quantity * unit_price) of the order's items.*

<details><summary>Hint</summary>

`get_table_names()` includes partitions; `pg_class.relispartition` tells them apart. `get_columns()` returns `identity` and `computed` keys when they apply; `get_table_comment()` returns a dict.

</details>

---

## Exercise 9 ★★: The seller statement on asyncio

Write `seller_statement_async(conn: AsyncConnection, seller_id, start, end)` returning the same `SellerStatement` as `bazaar.sa.reports.seller_statement()`, **without copying its SQL**: reuse the statement-building functions of the module. Then produce the November 2025 statements of sellers 1–20 concurrently, at most 5 at a time, on an async engine, and compare them with the sync function's results.

**Self-check:** the 20 async statements are equal to the sync ones (dataclass equality); total sales 9,337,683.15 over 15,942 orders; the largest is *Happy Outlet 1*.

<details><summary>Hint</summary>

`reports.seller_statement_query()` returns a plain `Select`. One connection per task (`async with sem, engine.connect() as conn:`), never a shared one.

</details>

---

## Exercise 10 ★★★: Commission to the cent

Bazaar takes a 12.5 % commission on each sold order. Compute it per order for November 2025 (sales statuses) **in SQL** as `round(total * 0.125, 2)`, and read it through a `TypeDecorator` over `Numeric(12, 2)` that rejects Python floats on the way in and raises if a result has more than two decimals. Then compute the same commission in Python with `Decimal.quantize()` and count the orders where Python and PostgreSQL disagree, first with `ROUND_HALF_EVEN` (Python's default for `quantize` in many codebases), then with `ROUND_HALF_UP`. Explain the difference, and why the same experiment with an 8 % rate would show no disagreement at all.

**Self-check:** 27,144 orders, SQL total commission 2,281,187.55. `ROUND_HALF_EVEN` disagrees on 1,173 orders (the first is a total of 89.96: SQL 11.25, Python 11.24), 11.73 in total; `ROUND_HALF_UP` disagrees on 0.

<details><summary>Hint</summary>

PostgreSQL's `round(numeric)` rounds halves away from zero. When is `total × 0.125` exactly halfway between two cents? Write the total in cents and think modulo 8. For 8 %, `cents × 8` would have to end in 50.

</details>

---

## Exercise 11 ★★★: A drift check that can't be fooled

The drift test of the milestone compares *names* of constraints and indexes. Show that it misses a real drift: inside a transaction, replace `reviews_rating_range` with a constraint of the same name that allows ratings up to 10, and run `bazaar.sa.drift.compare()`. Then write `materialized_drift(conn, metadata)` that catches it: create the metadata for real in a scratch schema inside a savepoint, and compare `pg_get_constraintdef()` / `pg_get_indexdef()` of both schemas. Roll everything back.

**Self-check:** on the untouched database, 0 differences (across all constraints and indexes of the 16 tables); with the altered constraint, exactly two lines: `CHECK (((rating >= 1) AND (rating <= 5)))` only in the metadata, and `... <= 10` only in the database. `compare()` reports nothing in both cases.

<details><summary>Hint</summary>

`SET LOCAL search_path = drift_tmp, bazaar, public` makes `create_all()` create tables in `drift_tmp` while enum types and functions still resolve. Why does a plain `create_all()` then create nothing? (Look at `checkfirst`.) Index definitions contain the schema name; strip it, including the `ON ONLY` form used for partitioned tables.

</details>

---

## Exercise 12 ★★★: Export a Core query, two ways

Export seller 1's 2025 order lines (order id, created_at, status, SKU, quantity, unit price; sales statuses; ordered by order id and SKU) to CSV, from a Core `select()`, in two ways:

1. **streaming** through SQLAlchemy with a server-side cursor, writing with the `csv` module;
2. **COPY**: `COPY (<the compiled select>) TO STDOUT WITH (FORMAT csv, HEADER)` through the driver connection, with the statement's parameters bound safely (no `literal_binds`).

Make the two files **byte-identical** and compare their SHA-256, row counts and run times.

**Self-check:** 78,160 rows each, identical hashes; the first data line is `261918,2025-01-01 02:35:39+00,delivered,S1-012492,1,19.99`. On the reference machine, COPY is about 4–5 times faster (0.3 s vs 1.4 s).

<details><summary>Hint</summary>

`conn.execution_options(stream_results=True, yield_per=5000)`. COPY can't take server-side parameters, but psycopg's `cursor.copy(sql, params)` binds them on the client; compile with `compile_kwargs={"render_postcompile": True}` so the IN-list placeholder is expanded, and pass `compiled.params`. For identical bytes, format timestamps as PostgreSQL does in UTC.

</details>
