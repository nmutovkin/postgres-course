"""Tests of the module 28 exercise solutions (ex01-ex08; ex09 is a test itself).

Run from project/python with the reference database at head:

    python -m pytest ../../modules/28-capstone/solutions -q

They reuse the package's fixtures (fresh_db, factories) and test helpers.
Revisions 2802/2803 are applied to the fresh database by executing their SQL,
so the package's own migration history stays untouched; copy the revision files
to migrations/versions/ to adopt them for real.
"""

from __future__ import annotations

import asyncio
import csv
import importlib.util
import io
from decimal import Decimal
from pathlib import Path

import ex01_slow_queries
import ex02_metrics
import ex03_cancel
import ex04_reviews
import ex05_rate_limit
import ex06_export
import ex07_read_your_writes
import ex08_tracing
import httpx
import psycopg
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from sqlalchemy import event

from bazaar.api.app import create_app
from bazaar.config import get_settings
from tests.factories import make_shop, make_user
from tests.test_api import ADMIN, fill_cart, key, running, settings_for, stock, user

pytestmark = pytest.mark.migrated

HERE = Path(__file__).parent


def revision_sql(filename: str, constant: str) -> str:
    spec = importlib.util.spec_from_file_location(filename[:-3], HERE / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return getattr(module, constant)


@pytest.fixture
def fresh_conn(fresh_db: str):  # noqa: ANN201
    with psycopg.connect(fresh_db, autocommit=True) as c:
        yield c


async def checkout(client, buyer: int, items: list[tuple[int, int]]) -> dict:  # noqa: ANN001
    await fill_cart(client, buyer, items)
    r = await client.post("/checkout", headers={**user(buyer), **key()})
    assert r.status_code == 201, r.text
    return r.json()["orders"][0]


# --- 1. slow queries ----------------------------------------------------------------------------


async def test_ex01_slow_queries() -> None:
    s = settings_for(get_settings().dsn)
    async with running(s, extra_routers=[ex01_slow_queries.router]) as c:
        for q in ("wireless headphones", "chef knife", "lamp"):
            for _ in range(3):
                await c.get("/search", params={"q": q})
        r = await c.get("/admin/slow-queries")
        assert r.status_code == 403
        r = await c.get(
            "/admin/slow-queries", params={"order_by": "mean", "min_calls": 9}, headers=ADMIN
        )
        assert r.status_code == 200
        items = r.json()["items"]
        search = [i for i in items if "search_products" in i["query"]]
        assert search and search[0]["calls"] >= 9
        assert "route='GET%20%2Fsearch'" in search[0]["tags"]


# --- 2. metrics ---------------------------------------------------------------------------------


async def test_ex02_metrics() -> None:
    async with running(settings_for(get_settings().dsn), extra_routers=[ex02_metrics.router]) as c:
        await c.get("/products", params={"limit": 5})
        text = (await c.get("/metrics")).text
    assert 'bazaar_pool_size{engine="rw"' in text
    read_count = [
        line for line in text.splitlines()
        if line.startswith('bazaar_sql_duration_ms_count{engine="read"')
    ]  # fmt: skip
    assert read_count and int(read_count[0].rsplit(" ", 1)[1]) >= 1
    assert 'le="+Inf"' in text


# --- 3. cancel ----------------------------------------------------------------------------------


async def test_ex03_cancel_order(fresh_db: str, fresh_conn: psycopg.Connection) -> None:
    fresh_conn.execute(revision_sql("2802_cancel_order.py", "CANCEL_ORDER"))
    shop = make_shop(fresh_conn, products=1, stock=5)
    pid = shop.products[0]
    async with running(settings_for(fresh_db), extra_routers=[ex03_cancel.router]) as c:
        order = await checkout(c, shop.buyer, [(pid, 2)])
        assert stock(fresh_conn, pid) == (5, 2)
        r = await c.post(f"/orders/{order['id']}/cancel", headers=user(shop.buyer + 1))
        assert r.status_code == 404  # not yours
        r = await c.post(f"/orders/{order['id']}/cancel", headers=user(shop.buyer))
        assert r.json() == {"order_id": order["id"], "cancelled": True}
        assert stock(fresh_conn, pid) == (5, 0)
        r = await c.post(f"/orders/{order['id']}/cancel", headers=user(shop.buyer))
        assert r.json()["cancelled"] is False  # idempotent
        assert stock(fresh_conn, pid) == (5, 0)
        shipped = await checkout(c, shop.buyer, [(pid, 1)])
        fresh_conn.execute("UPDATE orders SET status = 'shipped' WHERE id = %s", (shipped["id"],))
        r = await c.post(f"/orders/{shipped['id']}/cancel", headers=user(shop.buyer))
        assert r.status_code == 409 and r.json()["type"].endswith("/order-not-cancellable")


# --- 4. reviews ---------------------------------------------------------------------------------


async def test_ex04_reviews(fresh_db: str, fresh_conn: psycopg.Connection) -> None:
    shop = make_shop(fresh_conn, products=1)
    pid = shop.products[0]
    stranger = make_user(fresh_conn)
    async with running(settings_for(fresh_db), extra_routers=[ex04_reviews.router]) as c:
        order = await checkout(c, shop.buyer, [(pid, 1)])
        review = {"rating": 5, "title": "Great"}
        r = await c.post(f"/products/{pid}/reviews", json=review, headers=user(shop.buyer))
        assert r.status_code == 403  # bought, but not delivered yet
        fresh_conn.execute("UPDATE orders SET status = 'delivered' WHERE id = %s", (order["id"],))
        r = await c.post(f"/products/{pid}/reviews", json=review, headers=user(shop.buyer))
        assert r.status_code == 201 and r.json()["rating"] == 5
        r = await c.post(f"/products/{pid}/reviews", json=review, headers=user(shop.buyer))
        assert r.status_code == 409  # once per buyer
        r = await c.post(f"/products/{pid}/reviews", json=review, headers=user(stranger))
        assert r.status_code == 403
        r = await c.post(f"/products/{pid}/reviews", json={"rating": 6}, headers=user(shop.buyer))
        assert r.status_code == 422
        page = (await c.get(f"/products/{pid}/reviews")).json()
        assert page["summary"]["count"] == 1 and float(page["summary"]["average"]) == 5.0
        assert [i["title"] for i in page["items"]] == ["Great"]


async def test_ex04_reviews_keyset_on_reference_data() -> None:
    s = settings_for(get_settings().dsn)
    async with running(s, extra_routers=[ex04_reviews.router]) as c:
        first = (await c.get("/products/24/reviews", params={"limit": 2})).json()
        assert first["summary"]["count"] >= 3
        second = (
            await c.get("/products/24/reviews", params={"limit": 2, "cursor": first["next_cursor"]})
        ).json()
        ids = [i["id"] for i in first["items"] + second["items"]]
        assert len(ids) == len(set(ids)) >= 3


# --- 5. rate limiting ---------------------------------------------------------------------------


async def test_ex05_rate_limit(fresh_db: str, fresh_conn: psycopg.Connection) -> None:
    fresh_conn.execute(revision_sql("2803_rate_limits.py", "SCHEMA"))
    buyer, other = make_user(fresh_conn), make_user(fresh_conn)
    app = create_app(settings_for(fresh_db))
    app.add_middleware(
        ex05_rate_limit.RateLimit,
        rules={("POST", "/checkout"): ex05_rate_limit.Rule(rate=0.5, burst=3)},
    )

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            codes = [
                (await c.post("/checkout", headers={**user(buyer), **key()})).status_code
                for _ in range(5)
            ]
            assert codes == [422, 422, 422, 429, 429]  # empty cart x3, then limited
            r = await c.post("/checkout", headers={**user(buyer), **key()})
            assert r.status_code == 429 and int(r.headers["retry-after"]) in (1, 2)
            # Concurrent requests of another user never spend more than the burst.
            results = await asyncio.gather(
                *(c.post("/checkout", headers={**user(other), **key()}) for _ in range(10))
            )
            assert sorted(r.status_code for r in results) == [422] * 3 + [429] * 7
            await asyncio.sleep(2.1)  # 0.5 tokens/s: one token is back
            r = await c.post("/checkout", headers={**user(buyer), **key()})
            assert r.status_code == 422
            assert (await c.get("/cart", headers=user(buyer))).status_code == 200  # no rule


# --- 6. CSV export ------------------------------------------------------------------------------


async def test_ex06_export_streams_only_own_orders(
    fresh_db: str, fresh_conn: psycopg.Connection
) -> None:
    a, b = make_shop(fresh_conn, products=1, stock=10_000), make_shop(fresh_conn, products=1)
    fresh_conn.execute(  # 4,500 orders for seller a: more than two batches
        "SELECT place_order(%s, %s::jsonb, NULL) FROM generate_series(1, 4500)",
        (a.buyer, f'[{{"product_id": {a.products[0]}, "quantity": 1}}]'),
    )
    fresh_conn.execute(
        "SELECT place_order(%s, %s::jsonb, NULL)",
        (b.buyer, f'[{{"product_id": {b.products[0]}, "quantity": 1}}]'),
    )
    app = create_app(settings_for(fresh_db), extra_routers=[ex06_export.router])
    async with app.router.lifespan_context(app):
        # Raw ASGI, because httpx's ASGITransport collects the whole body before
        # returning: here every http.response.body message is one batch.
        messages: list[dict] = []
        scope = {
            "type": "http", "method": "GET", "path": "/seller/orders.csv",
            "raw_path": b"/seller/orders.csv", "query_string": b"", "root_path": "",
            "scheme": "http", "server": ("t", 80), "client": ("127.0.0.1", 1),
            "http_version": "1.1", "app": app,
            "headers": [(b"x-seller-id", str(a.seller).encode())],
        }  # fmt: skip

        done = asyncio.Event()
        requested = False

        async def receive() -> dict:
            nonlocal requested
            if not requested:
                requested = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await done.wait()  # the client "disconnects" once the body is complete
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            messages.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body"):
                done.set()

        await app(scope, receive, send)
    start = messages[0]
    assert start["status"] == 200
    assert (b"content-type", b"text/csv; charset=utf-8") in start["headers"]
    bodies = [m["body"] for m in messages if m["type"] == "http.response.body" and m["body"]]
    chunks = len(bodies)
    rows = list(csv.reader(io.StringIO(b"".join(bodies).decode())))
    assert rows[0] == ex06_export.COLUMNS
    assert len(rows) == 4501 and {r[3] for r in rows[1:]} == {str(a.buyer)}
    assert sum(Decimal(r[5]) for r in rows[1:]) == Decimal("45000.00")
    assert chunks == 3  # 4,500 rows in batches of 2,000: streamed, not built in memory


# --- 7. read-your-writes ------------------------------------------------------------------------


async def test_ex07_read_your_writes(fresh_db: str, fresh_conn: psycopg.Connection) -> None:
    shop = make_shop(fresh_conn, products=1)
    app = create_app(settings_for(fresh_db), extra_routers=[ex07_read_your_writes.router])
    app.add_middleware(ex07_read_your_writes.RememberWritePosition)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/me/orders", headers=user(shop.buyer))
            assert r.headers["x-read-from"] == "read" and r.json() == []
            await fill_cart(c, shop.buyer, [(shop.products[0], 1)])
            assert ex07_read_your_writes.COOKIE in c.cookies  # set by the write
            r = await c.post("/checkout", headers={**user(shop.buyer), **key()})
            lsn = c.cookies[ex07_read_your_writes.COOKIE]
            r = await c.get("/me/orders", headers=user(shop.buyer))
            # No replica here: the read engine is the primary, which is never behind.
            assert r.headers["x-read-from"] == "read" and len(r.json()) == 1
            # A position the read engine has "not reached" (as a lagging replica
            # would report): fall back to the primary.
            fresh_conn.execute("SELECT %s::pg_lsn", (lsn,))  # it is a valid LSN
            behind = await _fresh_enough_on(c, "FFFFFFFF/FFFFFFFF")
            assert behind is True  # on a primary: always fresh enough
    # On a standby, pg_last_wal_replay_lsn() < LSN gives false; see solutions/README.md.


async def _fresh_enough_on(c, lsn: str) -> bool:  # noqa: ANN001
    db = c._transport.app.state.db  # noqa: SLF001
    async with db.read.connect() as conn:
        return bool(await conn.scalar(ex07_read_your_writes.FRESH_ENOUGH, {"lsn": lsn}))


# --- 8. tracing ---------------------------------------------------------------------------------


async def test_ex08_tracing(fresh_db: str, fresh_conn: psycopg.Connection) -> None:
    shop = make_shop(fresh_conn, products=1)
    exporter = InMemorySpanExporter()
    provider = ex08_tracing.make_provider(exporter)
    app = create_app(settings_for(fresh_db), telemetry={"tracer_provider": provider})
    ex08_tracing.instrument_engines(app, provider)

    sent: list[str] = []  # the statements as they went to the server
    async with app.router.lifespan_context(app):
        event.listen(
            app.state.db.read.sync_engine,
            "after_cursor_execute",
            lambda conn, cur, statement, *a: sent.append(statement),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get(f"/products/{shop.products[0]}")
            assert r.status_code == 200
    assert sent and all("traceparent='00-" in st for st in sent)
    spans = exporter.get_finished_spans()
    [server] = [s for s in spans if s.kind is SpanKind.SERVER]
    assert server.name == "GET /products/{product_id}" and server.parent is None
    assert server.attributes["http.response.status_code"] == 200
    sql = [s for s in spans if s.attributes.get("db.system") == "postgresql"]
    statements = [s.attributes["db.statement"] for s in sql if "db.statement" in s.attributes]
    assert any("FROM products p" in st for st in statements)  # the page query
    assert any("WITH RECURSIVE up" in st for st in statements)  # the breadcrumb
    assert all(s.context.trace_id == server.context.trace_id for s in sql)
    by_id = {s.context.span_id: s for s in spans}

    def root(span):  # noqa: ANN001, ANN202
        while span.parent is not None:
            span = by_id[span.parent.span_id]
        return span

    assert all(root(s) is server for s in sql)  # every statement under the request
