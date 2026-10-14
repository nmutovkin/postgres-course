# Module 22: Exercises

**Starting state.** `./project/bin/reset.sh 22` (the large dataset), plus the virtual environment from the module's *Before you start* section. Write each solution as a Python script, run it from `project/python` with the venv active, and point it at the database with `BAZAAR_DSN` (default `postgresql://postgres:postgres@localhost:5432/bazaar`):

```bash
cd project/python && source .venv/bin/activate
export BAZAAR_DSN=postgresql://postgres:postgres@localhost:5432/bazaar
python my_exercise.py
```

Exercises that change data say so, and their solutions roll back or undo everything, so the self-check numbers below stay valid however often you run them. Exercise 10 imports the `bazaar` package (installed in the venv); everything else needs only `psycopg` and `psycopg_pool`.

Solutions: [solutions/README.md](solutions/README.md), with runnable files in [solutions/](solutions/).

---

## Exercise 1 ★: Connection report

Write a script that connects with `application_name='ex01-report'`, a 5-second `connect_timeout`, and `statement_timeout = 5s` set **at connection time** (not with a separate `SET`). It then prints the server version as `17.x`, the backend PID, user, database, `application_name`, `TimeZone`, `search_path`, `statement_timeout`, and whether the connection uses SSL. Also print the transaction status before the first query, after the queries, and after `rollback()`.

**Self-check:** `statement_timeout` shows `5s`, `search_path` shows `bazaar, public`, and the three statuses are `IDLE`, `INTRANS`, `IDLE`.

<details><summary>Hint</summary>

`conn.info` has `server_version`, `backend_pid`, `user`, `dbname` and `parameter_status(name)` (for the settings the server reports automatically, like `TimeZone` and `application_name`). Session settings at connect time go into the libpq `options` parameter: `options="-c statement_timeout=5s"`. `pg_stat_ssl` tells you about SSL.

</details>

---

## Exercise 2 ★: The biggest shops, as objects

Define a frozen dataclass `ShopStats(seller_id, shop_name, country_code, active_products, avg_price)` with a `label` property returning `"Shop Name (CC)"`, and a function `top_shops(conn, n=5) -> list[ShopStats]` that returns the sellers with the most **active** products, with the average price of those products rounded to cents. Use a row factory; no manual tuple unpacking.

**Self-check:** the first row is `ShopStats(seller_id=1, shop_name='Happy Outlet 1', country_code='BR', active_products=15771, avg_price=Decimal('177.76'))`, and `avg_price` is a `Decimal`, not a `float`.

<details><summary>Hint</summary>

`class_row(ShopStats)` builds each row with `ShopStats(**columns)`, so the column aliases in your `SELECT` must match the field names exactly. `count(*)` is a `bigint`, which loads as a Python `int` anyway.

</details>

---

## Exercise 3 ★: Fix the calls

Each call below fails. Explain why in one sentence, fix it, and run it.

```python
conn.execute("SELECT count(*) FROM orders WHERE status IN %s", (("paid", "shipped"),))       # a
conn.execute("SET statement_timeout = %s", ("2s",))                                           # b
conn.execute("SELECT full_name FROM users WHERE lower(email) = lower('%s')",
             ("Lukas.Meyer10462@example.org",))                                               # c
conn.execute("SELECT title FROM products WHERE id = %s", 16)                                  # d
conn.execute("SELECT count(*) FROM products WHERE title LIKE '%Headphones%' AND seller_id = %s", (1,))  # e
conn.execute("SELECT count(*) FROM %s", ("reviews",))                                         # f
```

**Self-check:** a) 6538, b) `'2s'`, c) `'Lukas Meyer'`, d) `'The Life of the North'`, e) 146, f) 292355.

<details><summary>Hint</summary>

Section 4 of the module has a table with one row per failure. For (f), remember the whitelist.

</details>

---

## Exercise 4 ★★: A safe dynamic filter builder

The storefront's product list accepts any combination of filters from the query string. Write `build_product_query(filters: dict, *, sort="id", desc=False, limit=50)` that returns `(query, params)` for `SELECT p.id, p.title, p.price FROM products p WHERE ... ORDER BY ... LIMIT ...`, supporting:

| filter | meaning |
|---|---|
| `seller_id`, `status` | equality |
| `category_id` | equality, or membership if given a list |
| `min_price`, `max_price` | inclusive bounds |
| `tags` | the product has **all** of these tags |
| `title_contains` | case-insensitive substring; `%` and `_` typed by the user match **literally** |
| `attributes` | JSON containment, e.g. `{"ram_gb": 16}` |

Unknown filter names, a sort column outside `id`, `price`, `created_at`, `title`, and limits outside 1–500 raise `ValueError`. Ties in the sort are broken by `id`. Every value must be a parameter, and every identifier must be composed with `psycopg.sql`. Write at least three tests.

**Self-check:** `{"category_id": 3, "status": "active", "max_price": 500, "attributes": {"ram_gb": 16}}` matches **329** products. Sorted by price descending, the first is product 14120 at 499.99. `{"title_contains": "%"}` returns no rows (without escaping, it would return everything).

<details><summary>Hint</summary>

A `match` statement over the filter names keeps the whitelist explicit. In `LIKE`, backslash is the default escape character: escape `\` first, then `%` and `_`. Wrap the JSON value in `Jsonb()`. `sql.SQL(" AND ").join(clauses)` builds the `WHERE` list, and `sql.Identifier("p", "price")` renders `"p"."price"`.

</details>

---

## Exercise 5 ★★: CSV price importer with COPY and validation

Seller 42 sends price updates as a CSV file: [`data/price_updates_s42.csv`](data/price_updates_s42.csv) (columns `seller_id,sku,new_price`, 608 data lines). Write `import_prices(conn, path) -> Report` that:

1. validates in Python: `seller_id` is an integer, `new_price` matches a non-negative amount with at most two decimals (no exponents, no empty strings), and a `(seller_id, sku)` pair that already appeared earlier in the file is rejected as a duplicate;
2. streams the remaining rows into a temporary staging table with `COPY`;
3. rejects, in SQL, rows that match no product of that seller;
4. updates the prices that actually changed, and inserts one `product_price_history` row (`valid_from = now()`) per changed product;
5. does all of this atomically, and returns the number of accepted and changed rows plus a list of `(line_number, reason)` rejections, sorted by line number.

Run it as a dry run that rolls back, so you can repeat it.

**Self-check:** accepted 597, changed 597, rejected 11: six bad prices (lines 52, 123, 203, 263, 334, 404), three unknown products (102, 302, and 502, which uses seller 43), and two duplicates (608, 609).

<details><summary>Hint</summary>

Count line numbers from 2 (line 1 is the header) and carry the line number through COPY, so SQL-side rejections can name it too. `UPDATE ... FROM staging ... WHERE p.price IS DISTINCT FROM s.new_price RETURNING p.id, p.price` inside a CTE feeds the history `INSERT`. Watch out for `1e3`: `Decimal` accepts it.

</details>

---

## Exercise 6 ★★: Retry on serialization failure, proven

Write a decorator `retry_transaction(max_attempts=10, base_delay=0.005)` for functions `fn(conn, ...)` that run one complete transaction. When the transaction fails with `SerializationFailure` (40001) or `DeadlockDetected` (40P01), it waits for a random time with exponential backoff and runs the function again; after `max_attempts` it re-raises. It refuses to run on a connection that is already inside a transaction (explain why in a comment).

Prove it: create a table `ex06_counter(id int PRIMARY KEY, n int)` with one row `n = 0`. Eight threads, each with its own connection at `SERIALIZABLE`, call a read-modify-write `increment()` (a `SELECT n` then an `UPDATE ... SET n = <read value + 1>`) 25 times each. Run once without and once with the decorator, and drop the table at the end.

**Self-check:** without the decorator, the final `n` is well below 200 and the number of failed increments is exactly `200 - n`. With it, `n = 200` and no increment fails. (Retry counts vary from run to run; the reference run had 72.)

<details><summary>Hint</summary>

Give every connection `autocommit=True` and use `with conn.transaction():` inside `increment()`, so each call is exactly one transaction. A `ConnectionPool` with a `configure` callback that sets `conn.isolation_level` is a tidy way to get eight serializable connections. Full jitter: `random.uniform(0, base * 2 ** (attempt - 1))`.

</details>

---

## Exercise 7 ★★: Export 1.27 million rows with constant memory

Export every order line, `order_id, created_at, status, product_id, quantity, unit_price, line_total`, ordered by `order_id, product_id`, to a CSV file with a header. Implement it twice: once with `COPY ... TO STDOUT` and once with a server-side cursor and Python's `csv` module. Print the elapsed time, the peak RSS of the process (`resource.getrusage(resource.RUSAGE_SELF).ru_maxrss`, in KiB on Linux), and the number of lines.

**Self-check:** both files have 1,266,929 lines, and peak RSS stays below 60 MiB for both (the reference: 38 MiB for COPY, 52 MiB for the cursor). Compare the `created_at` format in the two files and explain the difference.

<details><summary>Hint</summary>

For COPY, iterate the `copy` object and write the raw `bytes` chunks. For the cursor, `conn.cursor(name=...)` must run inside a transaction. Set `itersize`, and consider `SET LOCAL cursor_tuple_fraction = 1`.

</details>

---

## Exercise 8 ★★: Turn constraint violations into domain errors

Write `add_review(conn, user_id, product_id, rating, title=None, body=None, *, verified=True) -> int` that inserts a review and returns its id. It raises `DuplicateReview`, `InvalidRating`, `UnknownProduct` or `UnknownUser` (subclasses of `ReviewError`), chosen by the **name of the violated constraint**, never by parsing message text. When `verified` is true, the review is only inserted if the user has a delivered order containing the product, otherwise `NotABuyer` is raised. Do this check in the same `INSERT` statement, not with a separate `SELECT`. A failed call must leave the caller's transaction usable. Test it inside a transaction that you roll back.

**Self-check:** with the first (by order id) delivered order line whose buyer hasn't reviewed the product (user 2875, product 9371), the first call succeeds. The second raises `DuplicateReview`, rating 6 raises `InvalidRating`, user 7 raises `NotABuyer`, product 999999999 with `verified=False` raises `UnknownProduct`, and user 999999999 with `verified=False` raises `UnknownUser`.

<details><summary>Hint</summary>

`INSERT ... SELECT <values> WHERE <condition> RETURNING id` returns no row when the condition is false. `e.diag.constraint_name` is set for unique, check and foreign key violations, and all of them are subclasses of `IntegrityError`. Run the `INSERT` in a `conn.transaction()` block so a failure only rolls back a savepoint.

</details>

---

## Exercise 9 ★★: Where does the time go?

Raise the price of every product whose id is divisible by 40 (5,000 products) by 5%, in four ways: an `execute()` loop, the same loop in pipeline mode, `executemany()`, and one `UPDATE ... FROM unnest(%s::numeric[], %s::bigint[])`. Run each inside `conn.transaction(force_rollback=True)`, time the second of two runs, and count the `audit_log` rows each wrote. Then explain why the gaps between the methods are much smaller than in the module's 100,000-row `INSERT` benchmark.

**Self-check:** each method writes exactly 5,000 audit rows. The single `unnest` statement is the fastest and the plain loop the slowest, but by a factor of about 3–4, not 50.

<details><summary>Hint</summary>

Remember the triggers on `products` (Module 09). `changed_at = now()` finds the rows written by the current transaction, because `now()` is the transaction's start time.

</details>

---

## Exercise 10 ★★★: A pooled seller portal under RLS, without leaks

The seller portal connects as `bazaar_seller` (password `seller`, Module 15) and relies on `app.seller_id` for row-level security. Build a `ConnectionPool` for that role, derived from `BAZAAR_DSN` by replacing the user and password (`psycopg.conninfo`), with `max_size=1` so every request reuses the same backend. Write:

- `dashboard(pool, seller_id)`, which returns the counts of visible products and orders and the number of distinct sellers among the visible orders, using `bazaar.db.seller_scope`;
- `anonymous(pool)`, the same counts with no seller set;
- `leaky_dashboard(pool, seller_id)`, which sets the seller with a session-level `set_config(..., false)`.

Call `dashboard` for sellers 1, 2, 1, then `anonymous`, then `leaky_dashboard(2)`, then `anonymous` again.

**Self-check:** seller 1 sees `(18500, 69805, 1)`, seller 2 sees `(10592, 46196, 1)`. The first anonymous call sees `(0, 0, 0)` (RLS fails closed), and the anonymous call after the leaky one sees seller 2's `(10592, 46196, 1)`. Explain why that last result is a security bug and which two mechanisms prevent it.

<details><summary>Hint</summary>

`make_conninfo(**{**conninfo_to_dict(DSN), "user": "bazaar_seller", "password": "seller"})`. Superusers bypass RLS, which is why you need the real portal role here.

</details>

---

## Exercise 11 ★★: One query, nested objects

Write `order_detail(conn, order_id) -> OrderDetail | None`, where `OrderDetail(id, status, total, address: Address | None, items: list[Item])`, `Address` mirrors all columns of `addresses`, and `Item` mirrors `order_items`. Fetch everything in **one query** that selects the whole `addresses` row and an `array_agg` of `order_items` rows, and let psycopg build the nested dataclasses by registering the two composite types with your classes as factories. Make the registration also able to *dump* your dataclasses back to the database.

**Self-check:** order 500000 is `pending`, total `1147.96`, ships to Lukas Meyer's Home address in Frankfurt (address 10462), and has two items: product 39858 (2 × 23.99) and product 134834 (2 × 549.99), in that order. `conn.execute("SELECT (%s::addresses).city", (detail.address,))` returns `Frankfurt`.

<details><summary>Hint</summary>

`register_composite(CompositeInfo.fetch(conn, "addresses"), conn, factory=Address, make_sequence=...)`. The factory receives the fields positionally, in table column order, so the dataclass fields must be declared in that order. For dumping, `make_sequence(obj, info)` must return the field values; `dataclasses.astuple` does it. Without `make_sequence`, psycopg warns.

</details>

---

## Exercise 12 ★★★: A price cache invalidated by NOTIFY

Product pages read prices constantly, and prices rarely change. Build a `PriceCache` that keeps prices in a dict, with a background thread holding its own connection that runs `LISTEN product_price` and evicts a product from the cache when a notification for it arrives. Create (and at the end drop) a trigger that sends `pg_notify('product_price', id::text)` only when a product's price actually changes. The thread must stop cleanly within a fraction of a second when asked to.

Demonstrate with product 16: cache its price; update it in another connection **without committing** and check the cache; roll back and check again; update and commit, and measure how long the eviction takes; read the new price; finally restore the original price.

**Self-check:** the cache keeps the old price (10.99) after the uncommitted update and after the rollback. After the commit it is evicted within a few milliseconds (5 ms in the reference run), and the next read returns 11.99. After restoring, the cache has seen exactly 2 invalidations. The price is 10.99 again at the end.

<details><summary>Hint</summary>

`conn.notifies(timeout=0.2)` in a `while not stop.is_set()` loop lets the thread notice a stop request. Use `CREATE OR REPLACE TRIGGER ... AFTER UPDATE OF price ON products FOR EACH ROW WHEN (OLD.price IS DISTINCT FROM NEW.price)`. Think about the window between starting the thread and `LISTEN` taking effect: what must the cache do with entries loaded before that?

</details>

---

## Exercise 13 ★★★: Upsert with honest counts

Load 10,000 rows for seller 2 into a staging table with `COPY`: the first 8,000 of the seller's existing products by id (with the price raised by 1.00 for those whose position `i` in that list has `i % 8 < 3`, which is 3,000 rows, and the other 5,000 unchanged), plus 2,000 new SKUs `NEW-00000`...`NEW-01999` in category 3 at 9.99. Upsert them into `products` keyed by `(seller_id, sku)` and report how many rows were **inserted, updated, and left unchanged**. Rows whose category, title and price are identical must not be written at all. Implement it twice: with `INSERT ... ON CONFLICT DO UPDATE` and with PostgreSQL 17's `MERGE ... RETURNING merge_action()`. Run both inside rolled-back transactions and compare them, including what happens to each under concurrent inserts of the same new SKU.

**Self-check:** both report `{'inserted': 2000, 'updated': 3000, 'unchanged': 5000}`.

<details><summary>Hint</summary>

`ON CONFLICT ... DO UPDATE SET ... WHERE (p.a, p.b) IS DISTINCT FROM (EXCLUDED.a, EXCLUDED.b)` skips identical rows, and skipped rows are not returned by `RETURNING`. `RETURNING (xmax = 0)` distinguishes inserts from updates (section 16 of the module). In `MERGE`, a condition on `WHEN MATCHED AND ...` does the same job.

</details>
