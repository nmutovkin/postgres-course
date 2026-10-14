# Module 24: Solutions

Every solution is a runnable script in this directory, run against `./project/bin/reset.sh 24` plus `project/python/sql/2301_notify.sql`, with Python 3.13, SQLAlchemy 2.1.4, psycopg 3.3.6, asyncpg 0.32.0 and PostgreSQL 17.11. The outputs below come from those runs. Run them from `project/python` with the venv active:

```bash
cd project/python && source .venv/bin/activate
python ../../modules/24-sqlalchemy-core/solutions/ex04_translate_reports.py
```

---

## Exercise 1 ★: An engine that says who it is

[`ex01_engine_settings.py`](ex01_engine_settings.py)

```text
default connection: {'application_name': 'm24-ex1', 'statement_timeout': '2s', 'lock_timeout': '500ms', 'transaction_isolation': 'read committed'}
snapshot connection: repeatable read
same snapshot: 0 == 0: True
after return: read committed
timeout after 2.0 s: QueryCanceled
Pool size: 2  Connections in pool: 2 Current Overflow: 0 Current Checked out connections: 0
```

**Reasoning.** Everything that must hold "from the first moment" goes into `connect_args`, which SQLAlchemy passes to `psycopg.connect()`: `application_name` as a libpq parameter, and the two timeouts as `options="-c statement_timeout=2000 -c lock_timeout=500"`, startup options that become session defaults before the first query (Module 22, section 2.2). Because they are session *defaults*, not `SET` commands, the pool's reset-on-return (a `ROLLBACK`) keeps them.

The snapshot test needs two connections: one checked out with `.execution_options(isolation_level="REPEATABLE READ")`, which takes its snapshot at its first query, and a second `engine.begin()` block that commits a new job meanwhile. The first connection still counts 0 (the queue was empty), because a repeatable-read transaction sees the database as of its first statement (Module 10). When that connection is returned, SQLAlchemy restores the engine's default isolation level, so the next checkout reports `read committed` again. That is why per-connection `execution_options` are safe in a pooled application, while a raw `SET SESSION CHARACTERISTICS` would leak.

The timeout surfaces as `sqlalchemy.exc.OperationalError`, SQLAlchemy's wrapper class for the DBAPI's `OperationalError`; the psycopg exception is in `e.orig` (`psycopg.errors.QueryCanceled`, SQLSTATE 57014). After it, the transaction is aborted, so the script calls `conn.rollback()` before using the connection again.

**Alternative.** `create_engine(..., isolation_level="REPEATABLE READ")` would set it for every connection of the engine, which suits a dedicated reporting engine. `pool_pre_ping` was required by the exercise but isn't observable here; `examples/12_pooling.py` demonstrates it.

---

## Exercise 2 ★: Loyal buyers

[`ex02_loyal_buyers.py`](ex02_loyal_buyers.py)

```text
  1 Happy Outlet 1       23,529  3,940  16.7%
  2 Little Workshop 2    16,202  1,816  11.2%
  5 Rustic Emporium 5    14,346  1,536  10.7%
  3 Happy Emporium 3      8,684    519   6.0%
  4 Cozy Store 4          7,001    344   4.9%
```

**Reasoning.** Two levels of aggregation: first orders per (seller, buyer), then buyers per seller. The inner query is a `.subquery("per_buyer")`; the outer one counts its rows (`buyers`) and the rows with `n >= 2` (`count(*) FILTER (WHERE per_buyer.n >= 2)`, spelled `sa.func.count().filter(per_buyer.c.n >= 2)`). The labeled `buyers` expression is reused in `order_by(buyers.desc())`.

The rate is the interesting line. The first attempt, `sa.func.round(100.0 * repeat / buyers, 1)`, failed with `function round(double precision, integer) does not exist`: `100.0` is a Python float, bound as `double precision`, which makes the whole expression a double, and PostgreSQL's two-argument `round()` only exists for `numeric`. `Decimal(100)` is bound as numeric and the expression stays exact. (SQLAlchemy also renders `/` between integers as `x / CAST(y AS NUMERIC)`, true division, unlike SQL's integer division of `count(*) / count(*)`.)

**Alternative.** `count(DISTINCT user_id)` in a single `GROUP BY seller_id` gives the buyers, but not the repeat buyers without a second pass; the subquery does both in one scan.

---

## Exercise 3 ★: Five queries that lie

[`ex03_traps_start.py`](ex03_traps_start.py) (the starting point), [`ex03_traps_fixed.py`](ex03_traps_fixed.py)

```text
titles_with_percent_sign             1137          <- start
tagged_gift                          0
seller7_catalog_value_plus_10pct     601126.26
SAWarning: SELECT statement has a cartesian product between FROM element(s) "products" and FROM element "sellers". ...
first_two_products_with_shop         4000 rows
   (raised TypeError('Boolean value of this clause is not defined') )
expensive_active                     None

titles_with_percent_sign             0             <- fixed
tagged_gift                          30093
seller7_catalog_value_plus_10pct     601126.262
first_two_products_with_shop         2 rows
expensive_active                     64019
```

**Reasoning, one trap each.**

1. `contains("50%")` renders `LIKE '%' || :x || '%'` and passes `%` in the value through as a wildcard: "50, then anything" matched 1,137 titles. `contains("50%", autoescape=True)` escapes `%` and `_` (no product title contains a literal `50%`). The same applies to `startswith`, `endswith`, `like`.
2. `tags == ["gift"]` is array equality. Containment is `tags.contains(["gift"])` (`@>`), the operator the GIN index serves.
3. `price * 1.1` binds `1.1` as `double precision`; the sum is a double, which SQLAlchemy then processes with the *left operand's* type, `Numeric(12, 2)`, quantizing it to two decimals. The rounding is silent. `Decimal("1.1")` keeps the computation in `numeric` and returns `601126.262` exactly.
4. Two tables in the SELECT list without a join condition make a cartesian product (2 products × 2,000 sellers = 4,000 rows). SQLAlchemy warns but executes. The fix is an explicit `.join(sellers, sellers.c.id == p.c.seller_id)`.
5. `a and b` calls `bool(a)`, which SQL expressions refuse. Pass both conditions to `where()` (or use `&` with parentheses, or `sa.and_`).

**Takeaway.** Four of the five run without error. The defense is the habit from section 9: look at the compiled SQL of every new query once, and test reports against known numbers.

---

## Exercise 4 ★★: Four reports, translated and proven

[`ex04_translate_reports.py`](ex04_translate_reports.py)

```text
(a)   12 rows identical; first: (datetime.datetime(2025, 1, 1, 0, 0), Decimal('8033914.73'), None)
(b)   33 rows identical; first: (16, 2, 'The Burning Protocol', 416)
(c)   90 rows identical; first: (45303, 8, 8)
(d)  299 rows identical; first: (726, 'Nordic Store 726', Decimal('21152.53'), Decimal('1937.89'))
```

**Reasoning.** The method matters more than the queries: keep the SQL as the specification, run both, compare `[tuple(r) for r in ...]`. `conn.exec_driver_sql()` runs a SQL string on the same SQLAlchemy connection without `text()` parsing (psycopg's `%s` style, no `:name`), which is handy for exactly this.

Translation notes per report:

- **(a)** `created_at AT TIME ZONE 'UTC'` is `func.timezone("UTC", o.c.created_at)` (`AT TIME ZONE` *is* the `timezone()` function). The month is a timestamp *without* time zone, which is why the first value is a naive `datetime`. `func.lag(m.c.revenue).over(order_by=m.c.month)` is the previous month; `Decimal(100) * (revenue / prev - 1)` keeps numeric arithmetic (Exercise 2's float trap again). January's lag is `NULL`, so its growth is `None`.
- **(b)** The subtree is a recursive CTE used in `in_(select(sub.c.id))`. The window function is computed over the *aggregate*: `row_number().over(partition_by=p.c.category_id, order_by=(units.desc(), p.c.id))` where `units = func.sum(oi.c.quantity)`, legal in the same SELECT as the `GROUP BY` because windows run after grouping. A second CTE (`sold`) holds the ranked rows so the outer query can filter `rn <= 3`.
- **(c)** `func.count(sa.distinct(o.c.seller_id))` renders `count(DISTINCT orders.seller_id)`; the anti-join is `~sa.exists().where(reviews.c.user_id == o.c.user_id)`, correlated automatically to the outer `orders`.
- **(d)** Two filtered sums over November and December in one pass (`sum(total) FILTER (WHERE created_at < :dec_start)`), wrapped in `coalesce(..., 0)` for sellers without December sales, then `dec < nov / 2`.

**Alternatives.** For (a), `func.date_trunc("month", o.c.created_at)` without `AT TIME ZONE` would bucket by the *session* time zone; it happens to be UTC here, but a report must not depend on that (Module 09's rule for `seller_sales_daily`). For (d), a `GROUP BY seller_id, month` plus `lag()` generalizes to any number of months.

---

## Exercise 5 ★★: A product listing with filters, sorts and cursors

[`ex05_product_listing.py`](ex05_product_listing.py)

```text
  784 rows,  32 pages, identical; first id=15468 price=18.99  {'category': 'audio', 'max_price': Decimal('30'), 'in_stock': True, 'sort': 'price'}
 2163 rows,  87 pages, identical; first id=194299 price=2949.99  {'category': 'computers', 'tags_any': ['bestseller', 'new'], 'sort': '-price'}
  705 rows,  29 pages, identical; first id=199945 price=1929.99  {'category': 'laptops', 'attrs': {'ram_gb': 64, 'storage': {'type': 'ssd'}}, 'sort': 'newest'}
  387 rows,  16 pages, identical; first id=110124 price=389.99  {'q': 'wireless headphones', 'sort': 'relevance', 'min_price': Decimal('50')}
  180 rows,   8 pages, identical; first id=22181 price=8.99  {'tags_all': ['gift', 'eco'], 'category': 'kitchen', 'sort': 'price'}
rejected: unknown sort 'price; DROP TABLE products'; use one of ['-price', 'newest', 'price', 'relevance']
```

**Reasoning.**

- **Filters** are conditional `.where()` calls on an immutable statement, each one a few lines: the subtree is a recursive CTE (`category_id IN (SELECT id FROM tree)`), `tags_all`/`tags_any` are `@>`/`&&`, `attrs` is JSONB `@>`, `in_stock` is an `EXISTS` on inventory, `q` is `search_vector @@ websearch_to_tsquery(...)`.
- **Sorts** are a dictionary from a public name to a list of `(key, direction)` pairs. The caller only ever chooses a *key of the dictionary*; column objects come from our code, so there is no identifier injection to defend against (compare with Module 22's whitelist of `sql.Identifier`s).
- **Keyset condition.** `after_clause()` builds `k1 > v1 OR (k1 = v1 AND k2 > v2)` with `>` or `<` per key direction. For uniform directions PostgreSQL would also accept a row comparison (`tuple_(...) > tuple_(...)`), which is index-friendlier; the expanded form is needed for `relevance` (rank descending, id ascending).
- **Opaque cursors** are base64 of a JSON list of the last row's key values. JSON loses types: `Decimal` and `datetime` come back as strings, so `revive()` converts each value with the key column's `python_type` before binding. Without that, the first attempt failed with `operator does not exist: numeric > character varying`: the string was bound as `::VARCHAR`.
- **The float4 trap.** With `relevance`, paged and unpaged results first differed. `ts_rank()` returns `real`; psycopg receives its *text* form (`0.9991941`), which is a rounded decimal, so the rank stored in the cursor is not exactly the rank PostgreSQL compares it with on the next page, and the equality branch of the keyset condition never matches. Casting the rank to `double precision` fixes it, because PostgreSQL prints float8 with the shortest representation that round-trips exactly (since PostgreSQL 12).

**Alternative.** Sorting by relevance is often better served by fetching the top N (say 500) once and paginating in memory or by `OFFSET` within that bounded set: ranks of the long tail are nearly equal and rarely paged through.

---

## Exercise 6 ★★: A cart that merges

[`ex06_cart_merge.py`](ex06_cart_merge.py)

```text
add 16856 to user 1: (4, False)
add 20 to user 1:    (1, True)
merge 7 -> 1: {'inserted': 2, 'updated': 1}
user 1 now: [(20, 1), (5179, 1), (16856, 4), (18257, 2), (21089, 3), (39415, 1)]
user 7 lines left: 0
```

**Reasoning.** `add_to_cart` is `INSERT ... ON CONFLICT (user_id, product_id) DO UPDATE SET quantity = cart_items.quantity + excluded.quantity, added_at = now() RETURNING quantity, xmax = 0`. User 1 already had 2 of product 16856, so adding 2 gives 4 and `inserted` is false.

`merge_carts` uses `pg.insert(ci).from_select(["user_id", "product_id", "quantity"], src)`, where `src` selects the guest's lines with the target user id as a bound literal. The same `on_conflict_do_update` adds quantities for products in both carts (18257 went from 1 to 2). `RETURNING xmax = 0` per row gives the inserted/updated split. The delete of the guest's lines runs in the same `begin_nested()` block, so a failure between the two statements leaves both carts untouched without committing the caller's transaction.

**Alternative.** `MERGE` (PostgreSQL 15+, Module 06) can express insert-or-update-and-delete in one statement, but SQLAlchemy has no `merge` construct; you'd write it with `text()`. `ON CONFLICT` is also safer under concurrency: `MERGE` can fail with a unique violation when two sessions insert the same key at the same time.

---

## Exercise 7 ★★: Ten thousand price changes

[`ex07_bulk_price_update.py`](ex07_bulk_price_update.py)

```text
executemany UPDATE ... WHERE id = :pid           3.07 s   sum=1865555.75  ok=True
UPDATE ... FROM (VALUES ...), 2,000 per stmt     2.46 s   sum=1865555.75  ok=True
COPY into temp table + UPDATE ... FROM           1.85 s   sum=1865555.75  ok=True
```

**Reasoning.** The three methods send the data differently: 10,000 pipelined statements; 5 statements with 4,000 parameters each; one COPY stream plus one statement. Yet the spread is only 1.7×, against more than 10× for the inserts of section 7.4. The work is on the server: every updated `products` row fires the `set_updated_at()` BEFORE trigger and the `audit_trigger()` AFTER trigger (Module 09), which writes a JSONB copy of the old and new row into `audit_log`, and every update creates a new row version. Because `price` is part of the partial index `products_active_category_id_price_idx`, these updates can't be HOT (Module 16): each new version gets entries in all eight indexes of `products`, the four GIN indexes included. With roughly 0.2 ms of server work per row, the client-side method stops mattering.

Details: bind parameter names must differ from column names (`pid`, `new_price`), because SQLAlchemy reserves the column names for `.values()` parameters. `sa.values(...).data([...])` renders `(VALUES (%(param_1)s, ...), ...) AS v (pid, new_price)` and joins like a table.

**In production:** for a price import, compute the diff first and update only rows whose price actually changes. And remember `product_price_history` (Module 07): a real price change also appends a history row, which the COPY version can do with one more `INSERT ... SELECT` from the staging table.

---

## Exercise 8 ★★: A data dictionary from the Inspector

[`ex08_data_dictionary.py`](ex08_data_dictionary.py) (pass a file name to write the full document)

```text
16 tables: addresses, audit_log, cart_items, categories, events, inventory, jobs, order_items, orders, payments, product_price_history, products, promotions, reviews, sellers, users
## orders

One order per (buyer, seller) checkout split.

| column | type | null | default | key | comment |
|---|---|---|---|---|---|
| id | bigint | no | identity (always) | PK |  |
| user_id | bigint | no |  | users.id |  |
| seller_id | bigint | no |  | sellers.id |  |
| status | order_status | no | 'pending'::order_status |  |  |
| total | numeric(12, 2) | no | 0 |  | Denormalised: sum(quantity * unit_price) of the order's items. |
...
```

**Reasoning.** The Inspector answers each question with one call per table: `get_columns()` (with `identity`, `computed`, `default` and `comment` keys), `get_pk_constraint()`, `get_foreign_keys()` (whose `options` carry `ondelete`), `get_table_comment()`. Types are rendered with `type.compile(dialect)`, the same normalization the drift test uses. Partitions are excluded with one catalog query, because `get_table_names()` lists them like any table.

**Alternative.** `COMMENT ON` texts are the cheapest documentation a schema can carry, and they travel with it (`pg_dump`, `\d+`). A generator like this one, run in CI, keeps a rendered copy next to the code.

---

## Exercise 9 ★★: The seller statement on asyncio

[`ex09_async_statements.py`](ex09_async_statements.py)

```text
20 statements: async (5 at a time) 0.30 s, sync one by one 0.40 s
identical: True
total sales: 9337683.15  orders: 15942
largest: Happy Outlet 1
```

**Reasoning.** `reports.seller_statement_query()` returns a `Select`: it doesn't know or care how it will be executed. The async function awaits the two executions (`await conn.scalar(...)`, `await conn.execute(...)`) and builds the same dataclasses, so the results compare equal to the sync ones field by field. Each task checks out its own `AsyncConnection` under a semaphore of 5 (the pool size), as Module 23 required.

The async time includes opening the pool's five connections; the gain is modest because each statement is fast (seller 1's statement, 3,705 orders, takes about 60 ms; the others a few ms). The point is that the query code was written once.

**A real bug this exercise found.** The first version of `seller_statement_query()` aggregated items and payments in plain `GROUP BY` subqueries joined to the orders. Run 20 times, the sync loop took 12 s: every statement aggregated all 1.27 million order lines. The milestone now aggregates in `LATERAL` subqueries per order (section 6.8 of the theory), and the loop takes 0.4 s. Same SQL semantics, a different plan.

---

## Exercise 10 ★★★: Commission to the cent

[`ex10_commission_rounding.py`](ex10_commission_rounding.py)

```text
27,144 orders; SQL total commission 2281187.55
Python ROUND_HALF_EVEN disagrees on 1,173 orders, e.g. 89.96 -> SQL 11.25, Python 11.24
Python ROUND_HALF_UP disagrees on 0 orders
sum of differences (HALF_EVEN): 11.73
```

**Reasoning.** PostgreSQL's `round(numeric, 2)` rounds halves **away from zero**; Python's `quantize(..., ROUND_HALF_EVEN)` rounds halves to the even neighbor (banker's rounding). They differ exactly when the unrounded commission is a half cent with an even cent below it: 89.96 × 0.125 = 11.245, which PostgreSQL makes 11.25 and banker's rounding 11.24. A total of `c` cents gives a commission of `c/8` cents, a half cent exactly when `c ≡ 4 (mod 8)`: roughly one order in eight is a tie, and half of the ties disagree (1,173 of 27,144). For positive amounts `ROUND_HALF_UP` equals "away from zero", so it agrees everywhere. With an 8 % rate there are no ties at all: `c × 8 / 100` is a half cent only if `8c` ends in 50, which is impossible for an integer `c`.

The `Cents` decorator wraps the SQL expression with `sa.type_coerce(func.round(...), Cents)`, which changes only the Python-side type. Its result check (`value != value.quantize(CENT)`) would catch a forgotten `round()`, since `total * 0.125` has three decimals.

**In production:** decide where money is rounded (one place, one rule) and write the rule down. Mixing SQL and Python rounding is how ledgers end up a few cents off per thousand orders, here 11.73 a month.

---

## Exercise 11 ★★★: A drift check that can't be fooled

[`ex11_materialized_drift.py`](ex11_materialized_drift.py)

```text
0 differences (101 constraints and indexes in the schema)
compare() (names only): []
materialized_drift():
  only in metadata: ('constraint', 'reviews', 'reviews_rating_range', 'CHECK (((rating >= 1) AND (rating <= 5)))')
  only in database: ('constraint', 'reviews', 'reviews_rating_range', 'CHECK (((rating >= 1) AND (rating <= 10)))')
```

**Reasoning.** Comparing our expression text (`rating BETWEEN 1 AND 5`) with reflected text is hopeless: PostgreSQL stores a parsed expression and prints it back in canonical form (`(rating >= 1) AND (rating <= 5)`). So let PostgreSQL produce the canonical form of *both* sides: inside a savepoint, create the metadata in a scratch schema (`drift_tmp`), then read `pg_get_constraintdef()` and `pg_get_indexdef()` for both schemas and compare sets of `(kind, table, name, definition)`.

Three details made it work:

- `SET LOCAL search_path = drift_tmp, bazaar, public`: new tables are created in `drift_tmp`, while `order_status`, `email_address`, `tags_to_text()` and the `pg_trgm`/`btree_gist` operator classes still resolve. Foreign keys point to the `drift_tmp` copies, because they're created first and found first.
- `create_all(checkfirst=False)`: by default `create_all()` checks whether each table exists, and with `bazaar` on the search path it finds every table and creates nothing.
- `pg_get_indexdef()` includes the schema (`ON bazaar.products`, `ON ONLY bazaar.events` for the partitioned table); a regular expression strips it.

Rolling back the savepoint drops the scratch schema and restores the search path. On the reference schema the result is **zero differences across 101 constraints and indexes**, which validates every `postgresql_where`, `postgresql_ops`, `ExcludeConstraint` and naming-convention detail in `tables.py`.

**Alternative.** Alembic's `compare_metadata()` (Module 26) compares reflected structures and can be extended with custom comparators; it is the tool for *generating* migrations. This materialized comparison is the stricter *test*. It needs DDL rights, so it runs in CI, not in production.

---

## Exercise 12 ★★★: Export a Core query, two ways

[`ex12_export.py`](ex12_export.py)

```text
stream    78,160 rows   1.39 s  4.6 MB  sha256=83072a39452feece
copy      78,160 rows   0.30 s  4.6 MB  sha256=83072a39452feece
first lines: ['order_id,created_at,status,sku,quantity,unit_price', '261918,2025-01-01 02:35:39+00,delivered,S1-012492,1,19.99']
identical files: True
```

**Reasoning.**

- **Streaming**: `conn.execution_options(stream_results=True, yield_per=5000)` makes psycopg use a server-side cursor and SQLAlchemy fetch 5,000 rows at a time, so memory stays bounded however large the result. Each row then passes through SQLAlchemy's result processing and Python's `csv` module, which is where the 1.4 s go.
- **COPY**: the Core statement is compiled for the connection's dialect with `render_postcompile=True` (the status IN-list placeholder becomes three real placeholders). The result is psycopg-style SQL (`%(status_1_1)s::VARCHAR`...) and `compiled.params` the matching dict. `COPY ... TO STDOUT` does not accept server-side parameters, but psycopg's `cursor.copy(sql, params)` merges them on the client with proper quoting: safe, unlike `literal_binds`, which only knows how to render simple types and doubles `%` for this dialect (section 9.1). PostgreSQL then formats the CSV itself.
- **Identical bytes** required formatting timestamps the way PostgreSQL does (`2025-01-01 02:35:39+00` with the session in UTC) and writing `Decimal` with `str()`. Comparing hashes is a strong test: any difference in rounding, ordering or quoting would show.

COPY is 4–5 times faster here and puts no Python code on the per-row path. Use streaming when rows need Python logic on the way out; use COPY for straight exports.
