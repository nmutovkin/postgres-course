# Module 28: Solutions

<!-- nav:start -->
> [Theory](../README.md) · [Exercises](../exercises.md) · [Project](../project.md) · **Solutions**  
> [← 27 Production Python and Postgres](../../27-python-production/README.md) · [Course home](../../../README.md)
<!-- nav:end -->

Every solution is a module that plugs into the unchanged service: a router for `create_app(extra_routers=[...])`, a middleware for `app.add_middleware(...)`, or an Alembic revision to copy into `project/python/migrations/versions/`. They're tested in [`test_solutions.py`](test_solutions.py) and [`test_ex09_chaos.py`](test_ex09_chaos.py), which reuse the package's fixtures through [`conftest.py`](conftest.py). From `project/python`, with the reference database at revision 2801:

```bash
python -m pytest -c pyproject.toml ../../modules/28-capstone/solutions -q
```

```text
11 passed in 9.24s
```

The tests apply revisions 2802 and 2803 to their fresh databases by executing the revisions' SQL, so the package's migration history stays as shipped. Both revisions were also run for real: copied into `migrations/versions/`, `alembic upgrade head` on an empty database (2600 → 2803), `take_token()` called, then `alembic downgrade 2801` removed both functions and the table.

| Exercise | Files |
|---|---|
| 1 | [`ex01_slow_queries.py`](ex01_slow_queries.py) |
| 2 | [`ex02_metrics.py`](ex02_metrics.py) |
| 3 | [`2802_cancel_order.py`](2802_cancel_order.py), [`ex03_cancel.py`](ex03_cancel.py) |
| 4 | [`ex04_reviews.py`](ex04_reviews.py) |
| 5 | [`2803_rate_limits.py`](2803_rate_limits.py), [`ex05_rate_limit.py`](ex05_rate_limit.py) |
| 6 | [`ex06_export.py`](ex06_export.py) |
| 7 | [`ex07_read_your_writes.py`](ex07_read_your_writes.py) |
| 8 | [`ex08_tracing.py`](ex08_tracing.py) |
| 9 | [`test_ex09_chaos.py`](test_ex09_chaos.py) |

---

<!-- toc:start -->
**Contents**

- [Exercise 1: `/admin/slow-queries`](#exercise-1-adminslow-queries)
- [Exercise 2: `/metrics`](#exercise-2-metrics)
- [Exercise 3: Cancel an order](#exercise-3-cancel-an-order)
- [Exercise 4: Reviews, only from buyers](#exercise-4-reviews-only-from-buyers)
- [Exercise 5: Rate limiting with a token bucket](#exercise-5-rate-limiting-with-a-token-bucket)
- [Exercise 6: Stream a CSV export](#exercise-6-stream-a-csv-export)
- [Exercise 7: Read your writes from a replica](#exercise-7-read-your-writes-from-a-replica)
- [Exercise 8: Tracing, from HTTP to SQL](#exercise-8-tracing-from-http-to-sql)
- [Exercise 9: Chaos tests](#exercise-9-chaos-tests)

<!-- toc:end -->

## Exercise 1 ★: `/admin/slow-queries`

```python
_SORT = {OrderBy.total: "total_exec_time", OrderBy.mean: "mean_exec_time"}

SQL = """
    SELECT queryid::text AS queryid, calls,
           round(total_exec_time::numeric, 1)  AS total_ms,
           round(mean_exec_time::numeric, 2)   AS mean_ms,
           round(max_exec_time::numeric, 1)    AS max_ms,
           rows,
           round(100 * shared_blks_hit::numeric
                 / nullif(shared_blks_hit + shared_blks_read, 0), 1) AS cache_hit_pct,
           substring(query FROM '/\\*(.*?)\\*/')                     AS tags,
           left(regexp_replace(query, '\\s+', ' ', 'g'), :width)     AS query
    FROM pg_stat_statements
    WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
      AND toplevel
      AND calls >= :min_calls
    ORDER BY {sort} DESC
    LIMIT :limit
"""

@router.get("/slow-queries")
async def slow_queries(session: ReadSession, order_by: OrderBy = OrderBy.total, ...):
    stmt = sa.text(SQL.format(sort=_SORT[order_by]))
    ...
```

**Reasoning.** Everything the client controls is a bind parameter except the sort column, which can't be one (identifiers never can). So it's chosen from a closed `StrEnum` → column map, never interpolated from the request. FastAPI rejects `order_by=total; DROP TABLE users` with `422` before the handler runs. The other filters:

- `dbid` limits the result to this database (`pg_stat_statements` is cluster-wide);
- `toplevel` drops statements executed *inside* functions (`pg_stat_statements.track = all` in the course container records both levels), so `search_products()` appears once and not also as its inner `WITH candidates` queries;
- `min_calls` hides one-off statements, which dominate "mean" sorts with noise.

The tags column extracts the sqlcommenter comment. The route is the most useful thing to see next to a slow statement. Note that `pg_stat_statements` stores the text it saw *first* for a query id, and the id ignores comments: if two routes run the same statement, only one route shows up (Module 27, section 8.4). Since PostgreSQL 14 the reset time lives in `pg_stat_statements_info.stats_reset`.

It runs on the read engine (a read-only, possibly replica, connection), which is fine: a standby has its own `pg_stat_statements` for its own queries. Point it at the primary if you want the write side.

**Privileges.** The view shows other roles' query texts only to superusers and members of `pg_read_all_stats`. In production, `GRANT pg_read_all_stats TO bazaar_app` exposes every role's SQL text to the application. It's better to give the endpoint a separate monitoring DSN with that grant.

**Alternatives.** Expose `queryid` and let a dashboard (Grafana with the PostgreSQL data source, or `pg_stat_monitor`) do the history. `pg_stat_statements` is cumulative since the last reset, so "slow *now*" needs two snapshots and a difference, which is what monitoring systems store.

## Exercise 2 ★: `/metrics` for Prometheus

```python
for engine, s in db.stats.items():
    labels = {"engine": engine, "pid": pid}
    cumulative = 0
    for bound in BUCKETS_MS:
        cumulative += s.histogram[f"<={bound}"]
        out.append(_line("bazaar_sql_duration_ms_bucket", {**labels, "le": bound}, cumulative))
    out.append(_line("bazaar_sql_duration_ms_bucket", {**labels, "le": "+Inf"}, s.count))
    out.append(_line("bazaar_sql_duration_ms_sum", labels, round(s.total_ms, 3)))
    out.append(_line("bazaar_sql_duration_ms_count", labels, s.count))
```

**Reasoning.** `QueryStats` counts each statement in exactly one bucket. Prometheus histograms are cumulative (`le="10"` counts everything ≤ 10 ms), so the solution keeps a running sum, and `+Inf` equals the total count. Pool gauges come from `Database.pools()` (Module 27's `pool_metrics()`). Nothing touches the database: a metrics endpoint that needs a healthy database goes dark exactly when you need it.

**The pid label.** Each uvicorn worker has its own pools and counters, and a scrape reaches whichever worker accepts the connection. Labelling by pid at least makes series from different workers distinguishable instead of jumping around. The production answer is `prometheus_client`'s multiprocess mode (workers write to shared files, one worker serves the aggregate), or one worker per container, so a scrape target is one process.

**Alternatives.** `prometheus_client` with `Histogram` objects updated from the engine events directly, instead of converting `QueryStats`, or OpenTelemetry metrics (exercise 8's provider can export metrics too).

## Exercise 3 ★: Cancel an order

The revision embeds Module 10's function verbatim:

```python
revision = "2802"
down_revision = "2801"

CANCEL_ORDER = """
CREATE OR REPLACE FUNCTION cancel_order(p_order_id bigint)
RETURNS boolean
LANGUAGE plpgsql
AS $$
...
$$;
COMMENT ON FUNCTION cancel_order(bigint) IS ...;
"""


def upgrade() -> None:
    execute_script(CANCEL_ORDER)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS cancel_order(bigint)")
```

The endpoint:

```python
@router.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: int, user_id: UserId, session: RwSession) -> dict:
    owner = await session.scalar(
        sa.text("SELECT user_id FROM orders WHERE id = :id"), {"id": order_id}
    )
    if owner != user_id:  # None (missing) or someone else's: the same answer
        raise NotFound(f"order {order_id}")
    try:
        cancelled = await session.scalar(sa.text("SELECT cancel_order(:id)"), {"id": order_id})
    except DBAPIError as e:
        if sqlstate(e) == "BZ005":
            raise NotCancellable(f"order {order_id} is no longer pending or paid") from e
        if sqlstate(e) == "P0002":
            raise NotFound(f"order {order_id}") from e
        raise
    return {"order_id": order_id, "cancelled": cancelled}
```

**Reasoning.**

- **Copy, don't import.** `execute_script()` (Module 26) sends the text as is, so the `%`, `$$` and `:` in PL/pgSQL are safe. Module 26 explains why a revision must not read `cancel_order.sql`: if that file changes next year, replaying the history would build a different schema.
- **Ownership without a race.** The ownership `SELECT` isn't locked, and doesn't need to be: an order never changes its `user_id`, so the answer can't go stale. Everything that *can* change (the status, the reservation) is checked and changed under `cancel_order()`'s own `FOR NO KEY UPDATE`. A missing order and someone else's order get the same `404`, so order ids can't be probed.
- **Idempotent by design.** Cancelling twice returns `false` the second time with `200`, not an error: a client that retries after a lost response gets a harmless answer. The self-check shows the reservation released exactly once: `(5, 2)` → `(5, 0)` → `(5, 0)`.
- **Errors.** `BZ005` and `P0002` aren't in `bazaar.errors.translate()` (that covers `place_order()`), so the endpoint maps them. If more endpoints needed them, they would belong in `bazaar.errors` and the `DOMAIN_STATUS` table.

**Alternatives.** Put the ownership check into the function (`cancel_order(p_order_id, p_user_id)`) and raise `P0002` for "not yours", which is one round trip instead of two. Or let RLS for buyers do it (exercise idea: a `bazaar_buyer` role with `app.user_id`). A paid order's cancellation also needs a refund: write an `order.cancelled` event to the outbox in the same transaction and let a payments consumer refund it.

## Exercise 4 ★★: Reviews, only from buyers

```python
@router.post("/products/{product_id}/reviews", status_code=201, response_model=ReviewOut)
async def post_review(product_id: int, review: ReviewIn, user_id: UserId, session: RwSession):
    r = await session.run_sync(
        lambda s: services.submit_review(
            s, user_id, product_id, review.rating, review.title, review.body
        )
    )
    await session.flush()
    await session.refresh(r, ["created_at"])  # server default: fetch it
    return ReviewOut(
        id=r.id,
        user_id=r.user_id,
        rating=r.rating,
        title=r.title,
        body=r.body,
        created_at=r.created_at,
    )
```

**Reasoning.** The rule "a delivered order containing the product" and the uniqueness check already live in `services.submit_review()` (Module 25). `run_sync` runs that synchronous function on the `AsyncSession`'s underlying `Session`, in the same transaction, with IO through asyncpg. The domain exceptions it raises (`ReviewNotAllowed`, `DuplicateReview`, `InvalidReview`) are already in the error table, so the endpoint gets `403`/`409`/`422` for free. Validation happens twice on purpose: pydantic rejects `rating: 6` at the boundary with a precise `422`, and the service validates again because it has other callers.

`created_at` is a server default. After the insert the ORM has expired it, and reading an expired attribute in async code would need IO outside an `await`. `refresh(r, ["created_at"])` loads it explicitly. (The mapping could also set `eager_defaults=True` to have it returned by `INSERT ... RETURNING`.)

The list uses two fixed statements (first page, next page), not a `WHERE` string assembled at run time, and a keyset on `(created_at, id)`. Reviews per product are few, so the sort is cheap even though `reviews_product_id_user_id_key` (on `(product_id, user_id)`) doesn't provide the order. A product with 10,000 reviews would want an index on `(product_id, created_at DESC, id DESC)`.

**Alternatives.** Enforce "only buyers" in the database: a `BEFORE INSERT` trigger or a check in a `SECURITY DEFINER` function `submit_review()`, so that no client (an admin script, a future service) can bypass it. The cost is that the error is then a SQLSTATE to translate, which is the same pattern as `place_order()`'s.

## Exercise 5 ★★: Rate limiting with a token bucket in PostgreSQL

The core is one function (revision 2803):

```sql
CREATE UNLOGGED TABLE IF NOT EXISTS rate_limits (
    key         text PRIMARY KEY,
    tokens      double precision NOT NULL,
    updated_at  timestamptz NOT NULL
) WITH (fillfactor = 50);

CREATE OR REPLACE FUNCTION take_token(p_key text, p_rate double precision,
                                      p_burst integer, p_cost double precision DEFAULT 1)
RETURNS double precision
LANGUAGE plpgsql
AS $$
DECLARE
    v_tokens double precision; v_updated timestamptz; v_now timestamptz;
BEGIN
    INSERT INTO rate_limits (key, tokens, updated_at)
    VALUES (p_key, p_burst, clock_timestamp())
    ON CONFLICT (key) DO NOTHING;

    SELECT tokens, updated_at INTO v_tokens, v_updated
    FROM rate_limits WHERE key = p_key FOR UPDATE;

    v_now := clock_timestamp();       -- after the lock: waiting counts as refill
    v_tokens := least(p_burst, v_tokens + p_rate * extract(epoch FROM v_now - v_updated));

    IF v_tokens >= p_cost THEN
        UPDATE rate_limits SET tokens = v_tokens - p_cost, updated_at = v_now WHERE key = p_key;
        RETURN 0;
    END IF;
    UPDATE rate_limits SET tokens = v_tokens, updated_at = v_now WHERE key = p_key;
    RETURN (p_cost - v_tokens) / p_rate;
END;
$$;
```

The middleware calls it in its own short transaction before the request reaches the app:

```python
async with db.rw.begin() as conn:
    wait = await conn.scalar(
        sa.text("SELECT take_token(:k, :r, :b)"), {"k": key, "r": rule.rate, "b": rule.burst}
    )
...
if wait:
    response = problem(
        request,
        429,
        "rate-limited",
        "Too many requests",
        f"try again in {wait:.1f} s",
        headers={"Retry-After": str(math.ceil(wait))},
    )
```

**Reasoning.**

- **Correct under concurrency.** The row lock serializes requests of the same key: ten concurrent checkouts of one user get exactly `burst` passes (the test: three `422` past the limiter, seven `429`). Different keys never wait for each other. The `INSERT ... ON CONFLICT DO NOTHING` creates the bucket race-free on first use. `clock_timestamp()` instead of `now()`: `now()` is the transaction's start, which would be wrong after waiting for the lock.
- **The refill is computed, not scheduled.** No timer tops up buckets. The elapsed time since `updated_at` is turned into tokens on the next request, capped at `burst`.
- **`UNLOGGED`.** No WAL for the most frequently updated table in the system, so cheaper writes and no replication traffic. After a crash it's empty, which means everyone gets a full bucket: harmless for a rate limiter, unacceptable for anything else. It also isn't on replicas, which is fine because the limiter must run on the primary anyway.
- **`fillfactor = 50`.** Every request updates its row. With free space on the page, the update can be HOT (Module 16, section 7.2): no new index entry, and the old version pruned on the page. Old buckets are removed by `purge_rate_limits()` (run it hourly from the job queue).
- **Fail open.** If the limiter can't reach the database, the request passes and a warning is logged. A limiter protects the service from abuse; it must not become a second way for the service to go down. (A login endpoint protecting against password guessing would fail *closed*.)
- **Key.** `X-User-Id`, else the client address. Behind a proxy, the client address is the proxy's unless uvicorn runs with `--proxy-headers` and trusted forwarders.

**Cost and alternatives.** One extra round trip and one row lock per limited request, about a millisecond. That's fine for checkout, but too much for every `GET` of a busy API. Redis (`INCR` with expiry, or a Lua token bucket) is the usual home for high-volume limits. PostgreSQL is the right home when the limit must be consistent with the data (one coupon per account) or when you'd rather not run Redis. A fixed window (`count(*)` of the last minute's requests) is simpler but allows bursts of twice the limit at window edges.

After adopting 2803, add `rate_limits` to `bazaar/sa/tables.py` (or to `migrate.IGNORED_TABLES`). Otherwise `test_models_match_migrated_schema` reports it as a table autogenerate would drop.

## Exercise 6 ★★: Stream a CSV export

```python
async def _streaming_session(db: DB, seller_id: SellerId) -> AsyncIterator[AsyncSession]:
    async with db.seller_sessions(info={SELLER_KEY: seller_id}) as session, session.begin():
        await session.execute(sa.text("SET LOCAL statement_timeout = '120s'"))
        yield session


StreamingSession = Annotated[AsyncSession, Depends(_streaming_session, scope="request")]


@router.get("/orders.csv")
async def export_orders(session: StreamingSession, since: date = date(2000, 1, 1)):
    result = await session.stream(EXPORT, {"since": since})

    async def body() -> AsyncIterator[str]:
        buf = io.StringIO()
        out = csv.writer(buf)
        out.writerow(COLUMNS)
        async for rows in result.partitions(BATCH):  # BATCH = 2000
            out.writerows(rows)
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()
        ...

    return StreamingResponse(
        body(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="orders.csv"'},
    )
```

**Reasoning.**

- **Server-side cursor.** `session.stream()` executes through a cursor that stays open on the server (with asyncpg, a portal inside the transaction) and fetches rows in batches as they are consumed; `partitions(2000)` hands them over 2,000 at a time. Only about one batch is in Python's memory, however large the export.
- **The session's lifetime is the whole response.** The handler returns a `StreamingResponse` immediately, and the body is produced afterwards. With the portal's usual `scope="function"` dependency, the session would be committed and closed by then, and the cursor with it. `scope="request"` ends the dependency after the response is sent. This is the one endpoint where the commit-before-response rule of section 3.3 doesn't apply, and it doesn't need to: it writes nothing.
- **RLS still filters.** The export runs on the seller engine with `SET LOCAL app.seller_id`; the query has no seller condition. Seller B's order never appears in A's file.
- **A longer `statement_timeout`**, set locally: an export of 70,000 orders legitimately takes longer than 5 s to *stream*. The time between fetches counts as idle-in-transaction rather than statement time, and the 30 s `idle_in_transaction_session_timeout` limits how slowly a client may read.

**Testing the streaming.** httpx's `ASGITransport` collects the whole body before returning, so a chunk count through it is always 1. The test calls the ASGI app directly and counts `http.response.body` messages: 3 for 4,500 rows with batches of 2,000. The test's `receive()` must not keep returning `http.request`. `StreamingResponse` listens for `http.disconnect` while it streams and would spin forever. Returning `http.disconnect` only after the last body message models a real client.

**Alternatives.** `COPY (SELECT ...) TO STDOUT WITH (FORMAT csv, HEADER)` produces the CSV in the server, which is faster still. Through asyncpg, `copy_from_query()` writes to an output callback. That needs the raw driver connection and is the right choice for very large exports. For exports that take minutes, generate the file in a job and send a download link.

## Exercise 7 ★★: Read your writes from a replica

```python
class RememberWritePosition:
    async def __call__(self, scope, receive, send):
        ...

        async def send_wrapper(message):
            if message["type"] == "http.response.start" and message["status"] < 400:
                async with db.rw.connect() as conn:
                    lsn = await conn.scalar(sa.text("SELECT pg_current_wal_lsn()::text"))
                cookie = f"{COOKIE}={lsn}; Path=/; Max-Age=60; HttpOnly; SameSite=Lax"
                message.setdefault("headers", []).append((b"set-cookie", cookie.encode()))
            await send(message)


FRESH_ENOUGH = sa.text(
    "SELECT NOT pg_is_in_recovery()"
    " OR coalesce(pg_last_wal_replay_lsn() >= CAST(CAST(:lsn AS text) AS pg_lsn), false)"
)
```

**Reasoning.**

- **What position to remember.** Every committed write is at or before the primary's current WAL position once the commit has returned. The middleware asks for `pg_current_wal_lsn()` at `http.response.start`. The session dependencies end before the response starts (`scope="function"`), so the request's transaction has committed by then. That makes it an upper bound: the replica may need to replay a bit more than strictly necessary, never less.
- **Deciding.** A standby reports how far it has replayed (`pg_last_wal_replay_lsn()`). On a primary that function returns NULL, so the check starts with `NOT pg_is_in_recovery()`: a primary is always fresh. With no cookie, the client wrote nothing recently, and any replica will do. The cookie expires after 60 s; by then any healthy replica has caught up.
- **The cost** is one extra query per write (on the primary) and one per guarded read (on the replica), about a millisecond each, against a read that would otherwise have gone to the primary.
- **A driver detail.** asyncpg encodes `pg_lsn` parameters as integers and rejects the text `'2/64F4D20'`. Casting the parameter to `text` first lets PostgreSQL parse it.

The test runs without a replica, where every read says `read` and the freshness check is true for any LSN. **Not run here:** the lagging-replica case. With Module 19's lab (a standby with `recovery_min_apply_delay`), set `BAZAAR_REPLICA_DSN` to the standby and check that a read within the delay says `primary`.

**Alternatives.** Pin a user to the primary for N seconds after a write (simpler, no LSN, but it guesses). Use synchronous replication with `remote_apply` (Module 19, section 7) for the writes that need it, so the commit waits until the standby has applied it, at a latency cost on every such write.

## Exercise 8 ★★★: Tracing, from HTTP to SQL

```python
provider = make_provider(exporter)  # TracerProvider + SimpleSpanProcessor
app = create_app(settings, telemetry={"tracer_provider": provider})
instrument_engines(app, provider)


def instrument_engines(app, provider):
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a):
        async with inner(a):
            db = a.state.db
            SQLAlchemyInstrumentor().instrument(
                engines=[db.rw.sync_engine, db.read.sync_engine, db.seller.sync_engine],
                tracer_provider=provider,
                enable_commenter=True,
                skip_dep_check=True,
            )
            try:
                yield
            finally:
                SQLAlchemyInstrumentor().uninstrument()

    app.router.lifespan_context = lifespan
```

**Reasoning.** FastAPI 0.143 has OpenTelemetry built in. Given a tracer provider, it opens a SERVER span per request named by method and route template, with INTERNAL spans for dependency resolution, the endpoint and serialization, and continues an incoming `traceparent` header. `create_app()` forwards `telemetry=...` to `FastAPI()`. The first version of this solution wrote its own tracing middleware and got *two* SERVER spans per request: FastAPI's and its own. Check what your framework already does before adding instrumentation.

The SQL half is Module 27's instrumentation applied to engines that exist only after startup, hence the lifespan wrapper. `SQLAlchemyInstrumentor` is a process-wide singleton: a second `instrument()` call is ignored with a warning, so all three engines go into one call with `engines=[...]`. The test checks the structure, not just that spans exist: exactly one SERVER span with no parent, the page and breadcrumb statements in its trace, every SQL span's root being the SERVER span, and every statement that reached the read engine carrying `traceparent='00-...`. That comment makes `log_min_duration_statement` and `auto_explain` entries findable in the tracing backend.

**Production notes.** Use a `BatchSpanProcessor` and an OTLP exporter (exporting synchronously on the request path adds latency). Sample (`ParentBased(TraceIdRatioBased(0.1))`). `db.statement` contains the SQL text but never parameter values, which keeps personal data out of traces. With sqlcommenter on, asyncpg's statement cache sees a different text per request (the span id changes), so prepared statements are never reused. Measure the effect, and consider sampling or turning the commenter off for hot paths.

## Exercise 9 ★★★: Chaos: kill the connection mid-request

```python
def kill_on(engine, admin_dsn: str, marker: str, times: int = 1) -> list[int]:
    killed: list[int] = []

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def _kill(conn, cursor, statement, params, context, executemany) -> None:
        if marker in statement and len(killed) < times:
            pid = conn.connection.dbapi_connection.driver_connection.get_server_pid()
            with psycopg.connect(admin_dsn, autocommit=True) as admin:
                admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
            killed.append(pid)

    return killed
```

The checkout test kills the connection right after `UPDATE orders SET idempotency_key ...`, the statement after `place_order()`. The stock is reserved and the order rows exist, but nothing is committed. The next statement (the outbox insert) fails, the transaction is gone (the server rolled it back when the backend died), and:

```text
WARNING  bazaar.resilience:resilience.py:183 retrying after connection (attempt 1, sqlstate 08003): sleeping 0.048s
```

`arun_in_session()` classifies the error as `connection`. Checkout passes `idempotent=True`, so it retries with a new session and a new pooled connection (the dead one was invalidated and discarded). The second attempt finds no orders under the key (the first never committed) and the cart back in place (its `DELETE` was rolled back too), so it checks out normally. Result: `201`, one order, `(quantity, reserved) = (5, 2)`, one outbox event.

**Why the retry is safe** even though, in general, a connection can die *during* `COMMIT`, leaving the outcome unknown: the request carries an idempotency key. If the first attempt had committed, the retry would find its orders and replay them. Without the key, `idempotent=True` would be a lie and the retry could order twice (Module 27, section 3.2).

The read test kills the read engine's connection after the product page's first query. The breadcrumb query then fails, reads aren't retried in the service (section 3.7 of the module), and the error handler classifies it as `connection` and answers `503`, `Retry-After: 1`, `database-unavailable`. The next request succeeds on a fresh connection: SQLAlchemy invalidated the dead one instead of returning it to the pool.

**Alternatives and extensions.** `pool_pre_ping=True` (on in this package) catches connections that died *while idle in the pool*, at the cost of a round trip per checkout. It can't help with a death mid-request; only retries can. For broader chaos testing, put a TCP proxy that can drop or delay traffic (Toxiproxy) between the service and PostgreSQL. Or run the load test while switching over Module 20's Patroni cluster, and check that the invariants still hold.
