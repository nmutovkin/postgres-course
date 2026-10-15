# Module 28: Exercises

<!-- nav:start -->
> [Theory](README.md) · **Exercises** · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 27 Production Python and Postgres](../27-python-production/README.md) · [Course home](../../README.md)
<!-- nav:end -->

**Setup.** `./project/bin/reset.sh 28`, then in `project/python`: `alembic stamp 2600 && alembic upgrade head` (to 2801), and `pytest -n 4` passes. Every exercise extends the service **without editing the package**: `create_app(extra_routers=[...])` adds endpoints, `app.add_middleware(...)` adds middleware, and new schema objects come as Alembic revisions (2802, 2803, ...) that you copy into `migrations/versions/`. Write a test for each exercise with the helpers of `tests/test_api.py` (`running()`, `settings_for()`, `fill_cart()`, `key()`, `user()`) and the `fresh_db` fixture. Tests that commit must use a fresh database, never the reference one.

Solutions are in [`solutions/`](solutions/), with tests: from `project/python`, run `python -m pytest -c pyproject.toml ../../modules/28-capstone/solutions`.

---

<!-- toc:start -->
**Contents**

- [Exercise 1 ★: `/admin/slow-queries`](#exercise-1--adminslow-queries)
- [Exercise 2 ★: `/metrics` for Prometheus](#exercise-2--metrics-for-prometheus)
- [Exercise 3 ★: Cancel an order](#exercise-3--cancel-an-order)
- [Exercise 4 ★★: Reviews, only from buyers](#exercise-4--reviews-only-from-buyers)
- [Exercise 5 ★★: Rate limiting with a token bucket in PostgreSQL](#exercise-5--rate-limiting-with-a-token-bucket-in-postgresql)
- [Exercise 6 ★★: Stream a CSV export](#exercise-6--stream-a-csv-export)
- [Exercise 7 ★★: Read your writes from a replica](#exercise-7--read-your-writes-from-a-replica)
- [Exercise 8 ★★★: Tracing, from HTTP to SQL](#exercise-8--tracing-from-http-to-sql)
- [Exercise 9 ★★★: Chaos: kill the connection mid-request](#exercise-9--chaos-kill-the-connection-mid-request)

<!-- toc:end -->

## Exercise 1 ★: `/admin/slow-queries`

Add `GET /admin/slow-queries` (behind `X-Admin-Token`, like `/admin/refresh-stats`) that returns the top statements of this database from `pg_stat_statements`: calls, total and mean execution time in ms, rows, the shared-buffer cache hit percentage, the sqlcommenter tags (the `/*...*/` part) as a separate field, and the query text truncated to a requested width. Parameters: `order_by` = `total` (default) or `mean`, `min_calls` (default 5), `limit` (default 20). Include when the statistics were last reset.

**Self-check:** after 3 calls each of `/search?q=wireless headphones`, `chef knife` and `lamp`, `?order_by=mean&min_calls=9` lists `search_products` with at least 9 calls and tags containing `route='GET%20%2Fsearch'`. Without the token: `403`. Make sure `order_by=total; DROP TABLE users` can't do anything.

<details><summary>Hint</summary>

`ORDER BY` can't take a bind parameter, so map the enum to a column name in code. `pg_stat_statements` holds every database's statements: filter on `dbid`. As `bazaar_app`, other roles' query texts show as `<insufficient privilege>` unless the role is a member of `pg_read_all_stats`. Where does `stats_reset` live in PostgreSQL 14+?
</details>

---

## Exercise 2 ★: `/metrics` for Prometheus

Add `GET /metrics` in the Prometheus text exposition format with, per engine (`rw`, `read`, `seller`): the pool size, connections checked out and idle, and the statement-duration histogram, count, sum, errors and slow statements that `instrument_engine()` has been counting in `Database.stats`. Label every series with the engine and the process id.

**Self-check:** after one `GET /products`, the output contains `bazaar_pool_size{engine="rw",...}`, a `bazaar_sql_duration_ms_count{engine="read",...}` of at least 1, and buckets that end with `le="+Inf"` and are cumulative (each bucket's value at least the previous one's). The endpoint makes no database call: it still answers when PostgreSQL is stopped.

<details><summary>Hint</summary>

`QueryStats.histogram` counts per bucket; Prometheus buckets are cumulative ("≤ 10 ms" includes everything ≤ 5 ms). Why label by pid, and what does a scraper see with `--workers 4`?
</details>

---

## Exercise 3 ★: Cancel an order

Module 10's solutions wrote `cancel_order(order_id)` (`modules/10-transactions-concurrency/solutions/cancel_order.sql`): it releases the reservation of a pending or paid order, returns `false` for an already cancelled one, and raises `BZ005` for other statuses and `P0002` for a missing order. Bring it into the schema as revision **2802**, and add `POST /orders/{order_id}/cancel` for the buyer (`X-User-Id`): `200 {"order_id": ..., "cancelled": true|false}`, `404` for a missing order *or someone else's*, `409` (problem type `order-not-cancellable`) for a shipped one.

**Self-check:** on a fresh database, a checkout of 2 units of a 5-unit product leaves `(quantity, reserved) = (5, 2)`; cancelling gives `cancelled: true` and `(5, 0)`; cancelling again gives `cancelled: false` and still `(5, 0)`; another user gets `404`; an order set to `shipped` gets `409`. `alembic upgrade head` then `alembic downgrade 2801` both work.

<details><summary>Hint</summary>

The revision must contain the function's text: a revision never reads other files. Map the two SQLSTATEs yourself (`bazaar.resilience.sqlstate(e)` works on SQLAlchemy-wrapped errors). Is a separate ownership `SELECT` before the call a race? What can change about an order's owner?
</details>

---

## Exercise 4 ★★: Reviews, only from buyers

Add `POST /products/{product_id}/reviews` (`X-User-Id`, body `{rating: 1-5, title?, body?}`) and `GET /products/{product_id}/reviews` (newest first, keyset pages, plus `{count, average}` for the product). Only a user with a **delivered** order containing the product may review it, once. Reuse Module 25's `services.submit_review()` (synchronous) rather than rewriting its rules.

**Self-check:** on a fresh database: before delivery `403` (`review-not-allowed`); after setting the order to `delivered`, `201` with the rating; the same user again `409` (`duplicate-review`); a stranger `403`; `rating: 6` is `422`. On the reference data, product 24 has at least 3 reviews, and two pages of 2 by cursor contain no duplicates.

<details><summary>Hint</summary>

`await session.run_sync(lambda s: services.submit_review(s, ...))` runs the sync service on the async session's connection, in the same transaction. The error table in `bazaar/api/errors.py` already knows the review exceptions. `created_at` is a server default: how do you get it back after the insert?
</details>

---

## Exercise 5 ★★: Rate limiting with a token bucket in PostgreSQL

Limit `POST /checkout` per user: a burst of 5, then one every 5 seconds. Store the buckets in PostgreSQL (revision **2803**): a table `rate_limits (key, tokens, updated_at)` and a function `take_token(key, rate, burst, cost DEFAULT 1)` that refills the bucket for the elapsed time, takes a token if there is one and returns 0, or returns the seconds until there will be one. Apply it with an ASGI middleware that answers `429` (problem type `rate-limited`) with `Retry-After`. Concurrent requests must never spend the same token twice. Decide what the middleware does when the limiter itself fails, and justify it.

**Self-check:** with `rate=0.5, burst=3`: five checkouts with an empty cart give `[422, 422, 422, 429, 429]`; ten concurrent requests of another user give exactly three `422` and seven `429`; after 2.1 s one more request passes. `GET /cart` is never limited.

<details><summary>Hint</summary>

`INSERT ... ON CONFLICT DO NOTHING`, then `SELECT ... FOR UPDATE` serializes requests of one key without blocking other keys. Read `clock_timestamp()` *after* the lock. Think about `UNLOGGED` and `fillfactor` for a table that is updated on every request (Module 16), and about who deletes old buckets.
</details>

---

## Exercise 6 ★★: Stream a CSV export

Add `GET /seller/orders.csv?since=YYYY-MM-DD` to the seller portal: all the seller's orders (id, created_at, status, user_id, items, total) as CSV, ordered by time, **streamed**. Memory use must not grow with the number of orders: fetch rows through a server-side cursor in batches and send each batch as it is ready. RLS must still be what restricts the rows.

**Self-check:** on a fresh database with 4,500 orders for seller A and 1 for seller B, A's export has a header and 4,500 rows, all with A's buyer, totals summing to 45000.00, delivered in exactly 3 body chunks with a batch size of 2,000. B's order never appears.

<details><summary>Hint</summary>

`await session.stream(stmt)` and `result.partitions(n)` (Module 24, section 10; Module 22, section 10 for what the server does). The portal's session dependency has `scope="function"`: it closes the session when the handler returns, before the body has streamed. Which scope keeps it open? httpx's ASGI transport collects the whole body, so to count chunks, call the ASGI app directly.
</details>

---

## Exercise 7 ★★: Read your writes from a replica

`GET /users/{id}/orders` reads the primary so that a buyer always sees the order they just placed. Make a variant, `GET /me/orders`, that reads the read engine (a replica in production) **whenever that is safe**. After every successful write request, the service tells the client the primary's current WAL position in a cookie. On a read with the cookie, it asks the read engine whether it has replayed that far, and falls back to the primary if not. Report where it read from in an `X-Read-From: read | primary` header.

**Self-check:** without a replica (the read engine is the primary) every read says `read` and a just-placed order is visible. The freshness check returns `true` on a primary for any LSN, including `FFFFFFFF/FFFFFFFF`. With Module 19's lab (`BAZAAR_REPLICA_DSN` pointing at the standby, `recovery_min_apply_delay = '5s'` on it), an order placed less than 5 s ago is read from the primary, and an older one from the replica.

<details><summary>Hint</summary>

`pg_current_wal_lsn()` on the primary after the commit; `pg_last_wal_replay_lsn()` on the standby (NULL on a primary, so test `pg_is_in_recovery()` first). asyncpg binds `pg_lsn` as an integer: cast the text explicitly. Where can a middleware run a query after the handler's transaction committed but before the response headers go out?
</details>

---

## Exercise 8 ★★★: Tracing, from HTTP to SQL

Make every request produce one OpenTelemetry trace: a SERVER span named by method and route template (`GET /products/{product_id}`), with a CLIENT span for every SQL statement underneath. Each statement sent to PostgreSQL should carry a `traceparent` comment, so a line in the server's slow-query log can be traced back to its request. Instrument all three engines. Test with an in-memory exporter.

**Self-check:** one `GET /products/{id}` produces exactly one SERVER span, with no parent and `http.response.status_code` 200; the page query and the breadcrumb query are spans in the same trace whose root is that SERVER span; every statement the read engine sent contains `traceparent='00-`.

<details><summary>Hint</summary>

Check what your FastAPI version already does: recent versions have OpenTelemetry built in (`FastAPI(telemetry={"tracer_provider": ...})`), and `create_app()` forwards keyword arguments to `FastAPI()`. The engines exist only after startup. `SQLAlchemyInstrumentor` is a process-wide singleton, so instrument all engines in one call (`engines=[...]`) and `uninstrument()` at shutdown.
</details>

---

## Exercise 9 ★★★: Chaos: kill the connection mid-request

Prove the resilience claims of section 3.7 with tests that call `pg_terminate_backend()` on the connection serving a request, at a chosen statement:

1. a checkout whose connection dies right after `place_order()` reserved the stock, before the outbox insert and the commit: the client still gets `201`, there is exactly one order, the stock is reserved once, and there is one outbox event;
2. a product page whose connection dies after its first query: `503` with `Retry-After: 1` and problem type `database-unavailable` (not `500`), and the next request succeeds, so the dead connection didn't go back into the pool.

**Self-check:** both tests pass, the hook demonstrably fired (record the killed pids), and the log shows `retrying after connection (attempt 1, sqlstate 08003)` for the checkout.

<details><summary>Hint</summary>

An `after_cursor_execute` listener on `engine.sync_engine` sees each statement after it ran. `conn.connection.dbapi_connection.driver_connection.get_server_pid()` gives the backend pid under asyncpg. Kill it from a separate connection. Why is retrying the checkout safe even though the failure might, in general, hit during `COMMIT`?
</details>
