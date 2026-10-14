# Module 22: Solutions

Every solution is a runnable script in this directory. All were run against `./project/bin/reset.sh 22` with psycopg 3.3.6 on PostgreSQL 17.11; the outputs below are from those runs. Run them from `project/python` with the venv active:

```bash
cd project/python && source .venv/bin/activate
python ../../modules/22-psycopg/solutions/ex01_connection_report.py
```

Timings depend on your machine and on the round-trip time to the database. Counts don't.

---

## Exercise 1 ★: Connection report

[`ex01_connection_report.py`](ex01_connection_report.py)

```python
with psycopg.connect(
    DSN, application_name="ex01-report", options="-c statement_timeout=5s", connect_timeout=5
) as conn:
```

```text
status at start: IDLE
server_version     17.11
backend_pid        914
user               postgres
database           bazaar
application_name   ex01-report
TimeZone           Etc/UTC
search_path        bazaar, public
statement_timeout  5s
ssl                False
status after queries: INTRANS
status after rollback: IDLE
```

**Reasoning.** Three sources of information are used on purpose:

- `conn.info` reads what libpq already knows, with no round trip: the PID, user, database, and the **parameter status** values the server pushes to the client at connect time and whenever they change (`TimeZone`, `application_name`, `server_version`, `DateStyle`, `client_encoding`, `standard_conforming_strings`, and a few more).
- `search_path` and `statement_timeout` are not among the parameters PostgreSQL 17 reports automatically, so they need `SHOW`.
- `pg_stat_ssl` is the server's view of this backend's encryption. With the course's default `sslmode=prefer` and a server without a certificate, the answer is `False`.

`options="-c statement_timeout=5s"` sets the timeout in the startup packet, so it is in force before your first statement and costs no extra round trip. A `SET` after connecting would work too, but would be lost if a pool's `reset` ran `RESET ALL`, while a startup option becomes the session's *default* and survives `RESET`.

The status line shows the DB-API implicit transaction: `SHOW` is a statement like any other, so it opened a transaction. A read-only report like this one is a good candidate for `autocommit=True`.

---

## Exercise 2 ★: The biggest shops, as objects

[`ex02_top_sellers.py`](ex02_top_sellers.py)

```python
@dataclass(frozen=True, slots=True)
class ShopStats:
    seller_id: int
    shop_name: str
    country_code: str
    active_products: int
    avg_price: Decimal

    @property
    def label(self) -> str:
        return f"{self.shop_name} ({self.country_code})"

TOP_SHOPS = """
    SELECT s.id AS seller_id, s.shop_name, s.country_code,
           count(*)::int AS active_products,
           round(avg(p.price), 2) AS avg_price
    FROM sellers s
    JOIN products p ON p.seller_id = s.id
    WHERE p.status = 'active'
    GROUP BY s.id
    ORDER BY active_products DESC, s.id
    LIMIT %(n)s
"""

def top_shops(conn, n=5):
    with conn.cursor(row_factory=class_row(ShopStats)) as cur:
        return cur.execute(TOP_SHOPS, {"n": n}).fetchall()
```

```text
   1 Happy Outlet 1 (BR)          15,771   177.76
   2 Little Workshop 2 (ES)        8,998   177.85
   3 Happy Emporium 3 (IN)         6,513   171.30
   4 Cozy Store 4 (DE)             5,135   182.43
   5 Rustic Emporium 5 (US)        4,008   185.14
```

**Reasoning.** The column aliases *are* the contract with the dataclass: `s.id AS seller_id` is what makes `class_row` work. `GROUP BY s.id` is enough to select `shop_name` and `country_code` because they are functionally dependent on the primary key (Module 05). `round(avg(p.price), 2)` keeps the result `numeric`, so it arrives as `Decimal`. Rounding in Python with `float` would reintroduce the representation problems Module 02 warned about. `ORDER BY ..., s.id` makes ties deterministic.

**Alternatives.** `namedtuple_row` would give attribute access without defining a class, but no `label` property and no type information. A pydantic model would validate the rows, which is unnecessary for data that comes from our own constraints.

---

## Exercise 3 ★: Fix the calls

[`ex03_fix_the_calls.py`](ex03_fix_the_calls.py)

```text
a) 6538
b) '2s'
c) 'Lukas Meyer'
d) 'The Life of the North'
e) 146
f) 292355
```

| | Why it fails | Fix |
|---|---|---|
| a | With server-side binding, a parameter is one *value*; `IN (...)` needs a list of expressions, so `IN $1` is a syntax error. | `status = ANY(%s)` with a **list**. The list is sent as an array of unknown type, and the server infers `order_status[]` from the column. |
| b | `SET` is a utility statement and can't take parameters. | `SELECT set_config('statement_timeout', %s, false)`. The third argument `true` would make it `SET LOCAL`. |
| c | `'%s'` is a string literal; psycopg turns it into the literal text `'$1'` and the parameter you passed is unused, so the server can't determine its type. | Remove the quotes: `lower(%s)`. |
| d | The parameters must be a sequence or mapping; `16` is neither. | `(16,)`. |
| e | With parameters present, every `%` is a placeholder marker, and `%H` is not a valid placeholder. | Pass the pattern as a parameter: `title LIKE %s` with `"%Headphones%"`. Doubling (`'%%Headphones%%'`) also works but is easier to get wrong. |
| f | A table name is an identifier, not a value. | `sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))`, after checking `table` against a whitelist. |

---

## Exercise 4 ★★: A safe dynamic filter builder

[`ex04_filter_builder.py`](ex04_filter_builder.py) (runs its tests when executed directly; `pytest ex04_filter_builder.py` also works)

```text
SELECT p.id, p.title, p.price FROM products p WHERE p.category_id = %(category_id)s AND "p"."status" = %(status)s AND p.price <= %(max_price)s AND p.attributes @> %(attributes)s ORDER BY "p"."price" DESC, p.id LIMIT 3
{'category_id': 3, 'status': 'active', 'max_price': Decimal('500'), 'attributes': Jsonb({'ram_gb': 16})}
(14120, 'Quanta Wireless Ultrabook 512', Decimal('499.99'))
(19596, 'Brightline Pro Gaming Laptop 149', Decimal('499.99'))
(21745, 'Arcadia Pro Laptop 439', Decimal('499.99'))
matching laptops (up to the 500 cap): 329
tests passed
```

**Reasoning.** The design separates three kinds of input, and each gets its own defense:

1. **Filter names** select which SQL fragment is used. They never reach the SQL text themselves: a `match` statement maps each known name to a fragment written by us, and anything else is a `ValueError`. This whitelist is the real protection. `sql.Identifier` only guarantees correct quoting.
2. **Values** always become named placeholders (`%(max_price)s`). `max_price` is converted with `Decimal(str(value))`, so a float from JSON doesn't sneak binary rounding into a money comparison.
3. **Sort column and direction** come from small whitelists; the column is rendered with `sql.Identifier("p", sort)` and the direction with `sql.SQL("DESC")`/`sql.SQL("ASC")`, which are our own constant strings. The limit is validated and rendered with `sql.Literal` (a placeholder would work as well).

Two details are easy to miss. **LIKE wildcards in user input**: binding `title_contains` as a parameter prevents injection, but a user typing `%` would still match everything, because `%` is meaningful *inside* a LIKE pattern. `_escape_like` escapes the backslash first (otherwise the escapes we add would themselves be escaped), then `%` and `_`. The test `title_contains="%"` returns nothing, as it should, since no title contains a literal `%`. **Tie-breaking**: `ORDER BY price DESC` alone doesn't define an order among the many products at 499.99, so pagination would be unstable; the trailing `p.id` fixes that.

**Alternatives.** The query could be written as one static statement with `(%(x)s IS NULL OR col = %(x)s)` for every filter. That avoids composition, but the planner has to produce one plan for all combinations, and as a prepared statement with a generic plan it often can't use the right index (Module 12). Composed SQL produces a few distinct statement texts (one per combination of filters actually used), each planned well and each visible separately in `pg_stat_statements`. A query builder such as SQLAlchemy Core (Module 24) does the same composition with less code.

---

## Exercise 5 ★★: CSV price importer with COPY and validation

[`ex05_price_importer.py`](ex05_price_importer.py)

```bash
python ../../modules/22-psycopg/solutions/ex05_price_importer.py ../../modules/22-psycopg/data/price_updates_s42.csv
```

```text
accepted 597, changed 597, rejected 11
  line 52: bad price '-5.00'
  line 102: unknown product S42-999901 for seller 42
  line 123: bad price '-0.01'
  line 203: bad price 'abc'
  line 263: bad price ''
  line 302: unknown product S42-999902 for seller 42
  line 334: bad price '12.345'
  line 404: bad price '1e3'
  line 502: unknown product S42-018157 for seller 43
  line 608: duplicate of line 7
  line 609: duplicate of line 8
dry run: rolled back
```

**Reasoning.** Validation is split by what each side is good at:

- **Python** checks what needs no database: syntax (a strict regular expression for prices, because `Decimal("1e3")` would happily accept an exponent, and `Decimal("-0.01")` a negative), and duplicates within the file. Duplicates have to be caught before the staging table anyway: two staging rows for the same product would make the `UPDATE ... FROM` pick one of them arbitrarily.
- **SQL** checks what needs the data: whether `(seller_id, sku)` exists. One anti-join over the staging table finds all unknown products in one statement, instead of one lookup per row. The wrong-seller row (line 502) is caught by the same check, because the key includes the seller.

COPY carries the line number into the staging table, so rejections from both sides are reported in the same terms. The update and the history insert are one statement:

```sql
WITH changed AS (
    UPDATE products p
    SET price = s.new_price
    FROM price_staging s
    WHERE p.seller_id = s.seller_id AND p.sku = s.sku
      AND p.price IS DISTINCT FROM s.new_price
    RETURNING p.id, p.price
)
INSERT INTO product_price_history (product_id, price, valid_from)
SELECT id, price, now() FROM changed
```

so history rows exist exactly for the rows that changed, with no window in between. `cur.rowcount` after it is the number of history rows inserted. The whole import is one `conn.transaction()` block: either everything is applied or nothing. The `__main__` block opens a transaction first (`SELECT 1`), so that block is a savepoint and the final `rollback()` undoes it (the pitfall from section 5.2).

**Alternatives and trade-offs.** Rejecting the whole file on the first error (just letting COPY fail) is simpler and sometimes right: a supplier's file should perhaps be all-or-nothing. Partial acceptance with a report, as here, is friendlier for manual uploads. For files of millions of rows, do the syntax validation in SQL too: COPY everything as `text` into staging and check with `new_price ~ '^\d{1,10}(\.\d{1,2})?$'`. That's faster than Python and keeps one code path. PostgreSQL 17 also has `COPY ... (ON_ERROR ignore)`, which skips rows with malformed values during COPY itself, but it doesn't report *which* rows were skipped (only a count, and with `LOG_VERBOSITY verbose` a notice per row in PG 17), so it doesn't fit the reporting requirement as well.

---

## Exercise 6 ★★: Retry on serialization failure, proven

[`ex06_retry.py`](ex06_retry.py)

```text
without retry  final n =  40 (expected 200), failed increments = 160, retries = 0
with retry     final n = 200 (expected 200), failed increments = 0, retries = 72
```

**Reasoning.** At `SERIALIZABLE`, when two transactions read `n` and both try to write `n + 1`, PostgreSQL can't serialize them and aborts one with 40001 (here: `could not serialize access due to concurrent update`). Without a retry, 160 of 200 increments were lost as errors. That's the point of serializable isolation: an error instead of a silently wrong count. The decorator turns those errors into retries, and all 200 increments land.

The decorator's important properties:

- **It retries the whole transaction**, by calling the function again. The function must start from scratch (re-reading `n`), which is why it has to own the complete transaction and have no side effects outside the database. Calling an external API or sending an e-mail inside would repeat it.
- **It refuses to run on a connection that is already in a transaction.** Inside an outer transaction, the function's `transaction()` block would only be a savepoint. After a serialization failure the *outer* transaction's snapshot is the problem, so rolling back to a savepoint and retrying can't succeed, and the conflict has to propagate to whoever owns the outer transaction.
- **Backoff with full jitter**: `random.uniform(0, base * 2**(attempt-1))`. Without randomness, the transactions that just collided would retry in lockstep and collide again.
- **Only 40001 and 40P01 are retried.** A unique violation or a check failure will fail again the same way; retrying it only hides a bug.

**Alternatives.** For this particular counter, the right fix is not retrying at all but writing `UPDATE ex06_counter SET n = n + 1`, which is atomic at any isolation level, or `SELECT ... FOR UPDATE` before the write (Module 10). The retry decorator is for the cases where the read-then-write logic is genuinely complex. Module 27 builds the production version with logging, metrics, and a total time budget instead of an attempt count.

---

## Exercise 7 ★★: Export 1.27 million rows with constant memory

[`ex07_export_csv.py`](ex07_export_csv.py)

```text
copy     2.5s  peak RSS  38.1 MiB  1,266,929 lines  73 MiB file
cursor  10.1s  peak RSS  52.3 MiB  1,266,929 lines  78 MiB file
```

```text
order_id,created_at,status,product_id,quantity,unit_price,line_total
1,2023-01-01 00:28:19+00,refunded,4321,1,21.99,21.99        <- COPY
1,2023-01-01 00:28:19+00:00,refunded,4321,1,21.99,21.99     <- cursor + csv
```

**Reasoning.** Both stay far below the 460+ MiB that `fetchall()` needed for just four columns in the module's section 10, because neither ever holds more than a small batch.

- **COPY** lets the server produce CSV. Python receives `bytes` chunks and writes them to the file, so no Python object exists per row. It's 4x faster.
- **The named cursor** fetches 10,000 rows per round trip, turns each row into Python objects, and `csv.writer` formats them back into text. That conversion is what costs the time. It's the right choice when you have to *transform* rows in Python (redacting, enriching, writing to a format Postgres can't produce).

The timestamp difference is the type adaptation of section 7 made visible: COPY writes PostgreSQL's text output for `timestamptz` (`+00`), while the cursor version loads a Python `datetime` and `csv` writes `str(datetime)`, which is ISO 8601 with `+00:00`. Pick one deliberately when other systems parse the file. Setting the session `TimeZone` to `UTC` makes both independent of server configuration.

`SET LOCAL cursor_tuple_fraction = 1` tells the planner the cursor will be read to the end, so it plans for total time. With a 10% fraction, a query with `ORDER BY` can be planned as an index scan that returns the first rows quickly but reads the whole table slowly; with 1, the planner optimizes for total time. The `ORDER BY` costs a sort of 1.27 M rows on the server. If the consumer doesn't need ordered output, dropping it makes both versions faster and lets the server start sending immediately.

**Alternative.** `cursor.stream(query, size=10_000)` (section 10) also bounds memory without needing a transaction, but keeps the connection busy until the end of the export.

---

## Exercise 8 ★★: Turn constraint violations into domain errors

[`ex08_review_errors.py`](ex08_review_errors.py)

```text
buyer 2875 product 9371
ok -> 292360
same review again  -> DuplicateReview: Key (product_id, user_id)=(9371, 2875) already exists.
rating 6           -> InvalidRating: Failing row contains (292362, 9371, 2875, 6, null, null, 2026-10-14 09:06:29.558296+00).
not a buyer        -> NotABuyer: user 7 has no delivered order containing product 9371
unknown product    -> UnknownProduct: Key (product_id)=(999999999) is not present in table "products".
unknown user       -> UnknownUser: Key (user_id)=(999999999) is not present in table "users".
```

(Review ids grow every time you run it, because sequences are not rolled back.)

**Reasoning.** The mapping is a dictionary from **constraint name** to exception class:

```python
CONSTRAINTS = {
    "reviews_product_id_user_id_key": DuplicateReview,
    "reviews_rating_range": InvalidRating,
    "reviews_product_id_fkey": UnknownProduct,
    "reviews_user_id_fkey": UnknownUser,
}
```

`e.diag.constraint_name` is part of the protocol's error fields, so it doesn't depend on the server's language (`lc_messages`) or on message wording changing between versions. This only works because constraints have predictable names (Module 02's conventions). An unnamed `CHECK` would get a generated name that could change. Unknown constraint names are re-raised unchanged: a violation we didn't anticipate is a bug and should look like one.

The "verified buyer" rule is a condition on the `INSERT ... SELECT`, so the check and the insert are one statement, with no window in which an order could change between a separate check and the insert. When the condition is false, the statement inserts nothing and `RETURNING` returns no row, which is how `NotABuyer` is detected. The `INSERT` runs in a `conn.transaction()` block, so a failure rolls back to a savepoint, and the caller's transaction stays usable (the script makes six calls in one transaction and rolls back once at the end).

Note the order in which the server checks things: the `CHECK` on rating is evaluated before the unique index is consulted, so rating 6 on an already-reviewed product reports `InvalidRating`, not `DuplicateReview`.

**Alternatives.** Checking everything with `SELECT`s first ("does the product exist? did they already review it?") costs extra round trips and is still racy: two concurrent submissions both pass the check, and one then hits the unique constraint anyway. Let the constraints decide, and translate their verdicts.

---

## Exercise 9 ★★: Where does the time go?

[`ex09_bulk_updates.py`](ex09_bulk_updates.py)

```text
5000 updates
loop           4.27s  audit rows written: 5000
pipeline       1.78s  audit rows written: 5000
executemany    1.75s  audit rows written: 5000
unnest         1.20s  audit rows written: 5000
```

**Reasoning.** In the module's `INSERT` benchmark, the target table had no triggers, so per-statement client overhead and round trips were almost all of the cost, and a single bulk statement was 10–50 times faster than a loop. Here every updated row of `products` fires two row triggers: `set_updated_at()` and the audit trigger, which serializes the old and new row to JSONB, compares them, and inserts into `audit_log`. That server-side work is the same for all four methods and takes about 1.2 seconds for 5,000 rows, so it puts a floor under every method:

- loop: about 1.2 s of server work plus 5,000 round trips (≈ 3 s);
- pipeline and `executemany()`: the round trips mostly disappear;
- one `UPDATE ... FROM unnest(...)`: also one statement to parse and plan instead of 5,000 executions.

The lesson: batching fixes *client-side* costs. When the server-side cost per row dominates (triggers, indexes, foreign keys), measure before rewriting code. Here it's worth knowing that half the time of a bulk price change is spent writing an audit trail. `force_rollback=True` made every run start from the same state. The `audit_log` count uses `changed_at = now()`, which works because `now()` is fixed for the whole transaction.

---

## Exercise 10 ★★★: A pooled seller portal under RLS, without leaks

[`ex10_seller_portal_pool.py`](ex10_seller_portal_pool.py)

```text
seller 1: (products, orders, sellers seen) = (18500, 69805, 1)
seller 2: (products, orders, sellers seen) = (10592, 46196, 1)
seller 1: (products, orders, sellers seen) = (18500, 69805, 1)
no seller set: (0, 0, 0)
leaky seller 2: (10592, 46196, 1)
no seller set after the leaky call: (10592, 46196, 1)
pool stats: {'requests_num': 6, 'connections_num': 1}
```

**Reasoning.** All six requests ran on the same backend (`connections_num: 1`), which is exactly the situation of a busy production pool. `dashboard()` uses `seller_scope`, which calls `set_config('app.seller_id', ..., true)` inside a transaction block. The setting is transaction-local, so it is gone when the block commits, before the connection goes back to the pool. The anonymous request that follows sees nothing, because `current_seller_id()` is `NULL` and the policies compare against it (Module 15's fail-closed design).

`leaky_dashboard()` sets the same value with `false` (session scope). The pool's `with pool.connection()` commits and returns the connection, but session settings survive commits. The next anonymous request, perhaps a different seller's or a logged-out visitor's, sees seller 2's 10,592 products and 46,196 orders. In a real portal that means one shop reading another shop's orders.

Two mechanisms prevent it, and you want both:

1. **Scope the setting to the transaction** (`SET LOCAL` / `set_config(..., true)`), as `seller_scope` does. That also works behind PgBouncer in transaction mode, where session settings are unreliable anyway.
2. **A pool `reset` callback** that clears session state when a connection is returned (`RESET ALL`, or `DISCARD ALL`), wrapped so it leaves the connection idle (section 14.4). It is a safety net for code that gets (1) wrong.

The pool connects as `bazaar_seller`. As `postgres` (a superuser) the RLS policies would not apply at all and every call would see everything, which is why the DSN is rewritten with `conninfo_to_dict`/`make_conninfo` rather than by string manipulation, which breaks on passwords with special characters.

---

## Exercise 11 ★★: One query, nested objects

[`ex11_nested_composites.py`](ex11_nested_composites.py)

```text
500000 pending 1147.96
Address(id=10462, user_id=10462, label='Home', recipient='Lukas Meyer', line1='49 Mill Lane', line2=None, city='Frankfurt', postal_code='42966', country_code='DE', is_default=True, created_at=datetime.datetime(2023, 1, 12, 23, 31, 18, tzinfo=zoneinfo.ZoneInfo(key='Etc/UTC')))
   Item(order_id=500000, product_id=39858, quantity=2, unit_price=Decimal('23.99'))
   Item(order_id=500000, product_id=134834, quantity=2, unit_price=Decimal('549.99'))
```

```sql
SELECT o.id, o.status, o.total,
       a AS address,                                   -- the whole row, as a composite
       array_agg(oi ORDER BY oi.product_id) AS items   -- an array of composites
FROM orders o
LEFT JOIN addresses a ON a.id = o.shipping_address_id
JOIN order_items oi ON oi.order_id = o.id
WHERE o.id = %s
GROUP BY o.id, a.*
```

**Reasoning.** Selecting a table alias (`a`) yields the whole row as a value of the table's composite type. `array_agg(oi ...)` yields an array of them. After `register_composite(..., factory=Address)`, psycopg's loader builds an `Address` from each composite, converting every field with its own loader (note the aware `datetime` and the `Decimal`s), and the array loader builds a list of `Item`s. `class_row(OrderDetail)` then receives already-built objects for `address` and `items`. When there is no address (`LEFT JOIN` without a match), `a` is `NULL` and arrives as `None`.

`make_sequence=lambda obj, _info: astuple(obj)` teaches the dumper how to turn the dataclass back into fields, which is why `SELECT (%s::addresses).city` with an `Address` works. Without it, psycopg warns at registration that dumping would fail.

**Trade-offs.** One round trip instead of three, and no N+1 queries when you fetch many orders (`WHERE o.id = ANY(%s)`, `GROUP BY` per order). The cost is coupling: the dataclass field order must match the table's column order, so adding a column to `addresses` breaks the factory until the class is updated. (Positional construction makes it fail loudly with a `TypeError` instead of silently mixing fields up.) For looser coupling, build the nested value explicitly with `jsonb_build_object(...)` or `json_agg(...)` and load it as JSON, at the cost of losing types (`Decimal` becomes `float` and timestamps become strings). Module 25 shows how an ORM does the same with eager loading.

---

## Exercise 12 ★★★: A price cache invalidated by NOTIFY

[`ex12_price_cache.py`](ex12_price_cache.py)

```text
cached: 10.99
uncommitted update -> still cached: 10.99
rolled back       -> still cached: 10.99
committed         -> invalidated after 5 ms
fresh read: 11.99
invalidations: 2
```

**Reasoning.** The trigger sends one notification per product whose price actually changed (`WHEN (OLD.price IS DISTINCT FROM NEW.price)` in the trigger definition, so the function doesn't even run for other updates). Because `NOTIFY` is transactional, the uncommitted update and the rolled-back one produced nothing: the cache is never invalidated for a change that didn't happen, and it's invalidated within milliseconds after one that did.

The listener thread's design points:

- **Its own connection, in autocommit mode.** Notifications are only delivered between transactions, and the listening connection must not be shared with request code.
- **`notifies(timeout=0.2)` in a loop** returns control every 200 ms, so `stop()` takes effect quickly without closing the connection from another thread.
- **Clear the cache after `LISTEN`.** A price cached before `LISTEN` took effect could have changed before we started listening, and that notification is lost. Clearing once after subscribing closes that window. The same reasoning applies after a reconnect: every notification sent while disconnected is lost, so a production version must clear (or revalidate) the cache whenever its listener reconnects.
- **Invalidate, don't update.** The payload carries only the id. The next `get()` reads the committed price from the table, so the cache never trusts message contents and never applies notifications out of order.

The test changes product 16's price and restores it, which leaves two `audit_log` rows and a new `updated_at` behind; the price itself ends where it started.

**Alternatives.** A time-to-live cache (entries expire after N seconds) needs no trigger but serves stale prices for up to N seconds. Redis with pub/sub is the usual choice when many processes share one cache. Within Postgres, `LISTEN`/`NOTIFY` is the lightest way to get push-based invalidation. Remember it doesn't pass through PgBouncer in transaction mode (section 13), so the listener needs a direct connection.

---

## Exercise 13 ★★★: Upsert with honest counts

[`ex13_upsert_counts.py`](ex13_upsert_counts.py)

```text
ON CONFLICT  {'inserted': 2000, 'updated': 3000, 'unchanged': 5000}  1.39s
MERGE        {'inserted': 2000, 'updated': 3000, 'unchanged': 5000}  1.13s
```

**Reasoning.** Both statements read the same COPY-loaded staging table, and both skip identical rows with a row comparison:

```sql
-- ON CONFLICT
ON CONFLICT (seller_id, sku) DO UPDATE
SET category_id = EXCLUDED.category_id, title = EXCLUDED.title, price = EXCLUDED.price
WHERE (p.category_id, p.title, p.price)
      IS DISTINCT FROM (EXCLUDED.category_id, EXCLUDED.title, EXCLUDED.price)
RETURNING (xmax = 0) AS inserted

-- MERGE
WHEN MATCHED AND (p.category_id, p.title, p.price)
                 IS DISTINCT FROM (s.category_id, s.title, s.price) THEN UPDATE ...
WHEN NOT MATCHED THEN INSERT ...
RETURNING merge_action()
```

Skipping unchanged rows matters more than the count: an `UPDATE` that writes identical values still creates a new row version, a dead tuple, WAL, and (here) runs the triggers. 5,000 of the 10,000 rows would have been written for nothing. `IS DISTINCT FROM` treats `NULL` as a value, so a `NULL` description changing to `NULL` is "unchanged" and `NULL` to text is "changed".

Counting: rows skipped by the `WHERE` of `DO UPDATE` or by the `MERGE` condition are not returned, so `unchanged = total − returned`. `ON CONFLICT` needs the `xmax = 0` trick to tell inserts from updates (section 16 of the module). PostgreSQL 17's `merge_action()` reports `'INSERT'`/`'UPDATE'`/`'DELETE'` officially.

**Concurrency is the deciding difference.** `INSERT ... ON CONFLICT` is designed for concurrent upserts: if another transaction inserts the same `(seller_id, sku)` at the same moment, one of them waits and then takes the update path. `MERGE` decides "matched or not" from its snapshot. If a concurrent transaction inserts the same new SKU after our `MERGE` started, our `WHEN NOT MATCHED THEN INSERT` hits the unique index and fails with a unique violation. So: `ON CONFLICT` for upserts that may race (imports running in parallel, APIs), and `MERGE` when you need its extra power (`WHEN MATCHED ... THEN DELETE`, several conditional branches, `WHEN NOT MATCHED BY SOURCE` to delete rows missing from the feed) and can guarantee a single writer, for example with an advisory lock per seller.
