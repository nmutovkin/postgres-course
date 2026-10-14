"""Module 28: the HTTP service, tested through HTTP against the real database.

httpx.AsyncClient with ASGITransport calls the app in-process (no server, no
port), but everything below the app is real: the lifespan builds the three
engines, requests go through the middleware, dependencies, sessions, asyncpg,
RLS and place_order().

Two kinds of tests:
* read-only ones use the reference database (BAZAAR_DSN, the large dataset);
* tests that commit (checkout, the seller portal, concurrency) get a fresh
  database each, a copy of the migrated template (module 27's `fresh_db`), and
  build their own rows with tests.factories.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from decimal import Decimal

import httpx
import psycopg
import pytest
import sqlalchemy as sa
from fastapi import APIRouter

from bazaar import outbox
from bazaar.api.app import create_app
from bazaar.api.db import RwSession
from bazaar.config import Settings, get_settings
from bazaar.migrate import SchemaOutOfDate
from tests.factories import Shop, make_address, make_product, make_seller, make_shop, make_user

pytestmark = pytest.mark.migrated

ADMIN = {"X-Admin-Token": "test-admin-token"}


def settings_for(dsn: str, **kw: object) -> Settings:
    return replace(get_settings(), dsn=dsn, admin_token="test-admin-token", **kw)


@asynccontextmanager
async def running(settings: Settings, **app_kw: object) -> AsyncIterator[httpx.AsyncClient]:
    """The app with its lifespan (startup and shutdown), and a client talking to it."""
    app = create_app(settings, **app_kw)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://bazaar") as client:
            yield client


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """On the reference database: for tests that only read."""
    async with running(settings_for(get_settings().dsn)) as c:
        yield c


@pytest.fixture
def fresh_conn(fresh_db: str):  # noqa: ANN201
    with psycopg.connect(fresh_db, autocommit=True) as c:
        yield c


@pytest.fixture
async def fresh_client(fresh_db: str) -> AsyncIterator[httpx.AsyncClient]:
    async with running(settings_for(fresh_db)) as c:
        yield c


def user(uid: int) -> dict[str, str]:
    return {"X-User-Id": str(uid)}


def seller(sid: int) -> dict[str, str]:
    return {"X-Seller-Id": str(sid)}


def key() -> dict[str, str]:
    return {"Idempotency-Key": f"test-{uuid.uuid4()}"}


async def fill_cart(c: httpx.AsyncClient, uid: int, items: list[tuple[int, int]]) -> None:
    for pid, qty in items:
        r = await c.post(
            "/cart/items", json={"product_id": pid, "quantity": qty}, headers=user(uid)
        )
        assert r.status_code == 201, r.text


def any_category(conn: psycopg.Connection) -> int:
    return conn.execute("SELECT min(id) FROM categories").fetchone()[0]


def stock(conn: psycopg.Connection, pid: int) -> tuple[int, int]:
    row = conn.execute(
        "SELECT quantity, reserved FROM inventory WHERE product_id = %s", (pid,)
    ).fetchone()
    assert row is not None
    return row[0], row[1]


# --- health, readiness, startup -------------------------------------------------------------


async def test_health_and_ready(client: httpx.AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    r = await client.get("/ready", headers={"X-Request-ID": "probe-1"})
    body = r.json()
    assert r.status_code == 200 and body["ok"] and body["schema"] == ["2801"]
    assert set(body["pools"]) == {"rw", "read", "seller"}
    assert r.headers["x-request-id"] == "probe-1"


async def test_startup_refuses_an_old_schema(fresh_db: str, fresh_conn: psycopg.Connection) -> None:
    fresh_conn.execute("UPDATE alembic_version SET version_num = '2701'")
    with pytest.raises(SchemaOutOfDate, match="2701"):
        async with running(settings_for(fresh_db)):
            pass


async def test_ready_reports_schema_drift(fresh_db: str, fresh_conn: psycopg.Connection) -> None:
    async with running(settings_for(fresh_db)) as c:
        fresh_conn.execute("UPDATE alembic_version SET version_num = '2701'")  # after startup
        r = await c.get("/ready")
        assert r.status_code == 503
        assert "code expects ['2801']" in r.json()["problems"][0]


# --- catalog (reference database) ------------------------------------------------------------


async def test_products_category_subtree_and_price_filter(client: httpx.AsyncClient) -> None:
    r = await client.get(
        "/products", params={"category_id": 1, "min_price": 100, "max_price": 200, "limit": 100}
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 100
    engine = sa.create_engine(get_settings().dsn.replace("postgresql://", "postgresql+psycopg://"))
    with engine.connect() as c:
        subtree = set(
            c.scalars(
                sa.text(
                    "WITH RECURSIVE t AS (SELECT id FROM categories WHERE id = 1 UNION ALL"
                    " SELECT c.id FROM categories c JOIN t ON c.parent_id = t.id) SELECT id FROM t"
                )
            )
        )
    engine.dispose()
    assert len(subtree) > 5  # Electronics and its descendants
    assert {i["category_id"] for i in items} <= subtree
    assert all(Decimal("100") <= Decimal(i["price"]) <= Decimal("200") for i in items)
    ids = [i["id"] for i in items]
    assert ids == sorted(ids, reverse=True)  # default sort: newest (id DESC)


async def test_keyset_pages_match_offset_pages(client: httpx.AsyncClient) -> None:
    """Walking three pages by cursor gives exactly the first 60 rows in price order.
    (Books, category 14: other tests commit products into category 3 meanwhile.)"""
    params = {"category_id": 14, "sort": "price_asc", "limit": 20}
    seen: list[dict] = []
    cursor = None
    for _ in range(3):
        r = await client.get(
            "/products", params={**params, **({"cursor": cursor} if cursor else {})}
        )
        page = r.json()
        seen += page["items"]
        cursor = page["next_cursor"]
        assert cursor
    keys = [(Decimal(i["price"]), i["id"]) for i in seen]
    assert keys == sorted(keys) and len(set(keys)) == 60
    r = await client.get("/products", params={**params, "limit": 60})
    assert [i["id"] for i in r.json()["items"]] == [i["id"] for i in seen]
    # A cursor belongs to its sort order.
    r = await client.get("/products", params={"sort": "newest", "cursor": cursor})
    assert r.status_code == 400
    assert r.headers["content-type"] == "application/problem+json"


async def test_attribute_filter_uses_jsonb_containment(client: httpx.AsyncClient) -> None:
    r = await client.get(
        "/products", params=[("category_id", 3), ("attr", "ram_gb:32"), ("attr", "cpu:Ryzon 9")]
    )
    items = r.json()["items"]
    assert items
    engine = sa.create_engine(get_settings().dsn.replace("postgresql://", "postgresql+psycopg://"))
    with engine.connect() as c:
        attrs = c.execute(
            sa.text("SELECT attributes FROM products WHERE id = ANY(:ids)"),
            {"ids": [i["id"] for i in items]},
        ).scalars()
        assert all(a["ram_gb"] == 32 and a["cpu"] == "Ryzon 9" for a in attrs)
    engine.dispose()


async def test_product_page(client: httpx.AsyncClient) -> None:
    r = await client.get("/products/110124")
    assert r.status_code == 200
    p = r.json()
    assert p["title"] == "Orbitron Wireless Headphones 251"
    path = [c["name"] for c in p["category_path"]]
    assert path[0] == "Electronics" and path[-1] == "Headphones"
    assert p["rating_count"] >= 0 and isinstance(p["price"], str)
    r = await client.get("/products/999999999")
    assert r.status_code == 404 and r.json()["type"].endswith("/not-found")


async def test_search_relevance_smoke(client: httpx.AsyncClient) -> None:
    r = await client.get("/search", params={"q": "wireless headphones", "limit": 10})
    hits = r.json()["items"]
    assert len(hits) == 10 and all(h["matched_by"] == "fts" for h in hits)
    assert all("wireless" in h["title"].lower() and "headphones" in h["title"].lower()
               for h in hits)  # fmt: skip
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)
    assert "<mark>" in hits[0]["snippet"]
    # A typo finds nothing by full text, so the trigram fallback answers.
    r = await client.get("/search", params={"q": "headphnes", "limit": 5})
    hits = r.json()["items"]
    assert hits and all(h["matched_by"] == "fuzzy" for h in hits)
    assert all("headphones" in h["title"].lower() for h in hits)
    # Only stop words: nothing to search for (and no error).
    r = await client.get("/search", params={"q": "the"})
    assert r.status_code == 200 and r.json()["items"] == []


# --- cart and checkout (fresh database) ------------------------------------------------------


async def test_cart_and_checkout(
    fresh_client: httpx.AsyncClient, fresh_conn: psycopg.Connection
) -> None:
    shop = make_shop(fresh_conn, products=2, stock=10, price=Decimal("12.50"))
    other_seller = make_seller(fresh_conn)
    p3 = make_product(
        fresh_conn,
        other_seller,
        stock=3,
        price=Decimal("5.00"),
        category_id=any_category(fresh_conn),
    )
    fresh_conn.execute("UPDATE addresses SET is_default = true WHERE id = %s", (shop.address,))
    await fill_cart(
        fresh_client, shop.buyer, [(shop.products[0], 2), (p3, 1), (shop.products[0], 1)]
    )

    cart = (await fresh_client.get("/cart", headers=user(shop.buyer))).json()
    assert [(i["product_id"], i["quantity"]) for i in cart["items"]] == [
        (shop.products[0], 3),
        (p3, 1),
    ]
    assert cart["total"] == "42.50"

    r = await fresh_client.post("/checkout", headers={**user(shop.buyer), **key()})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["replayed"] is False
    orders = body["orders"]
    assert sorted(o["seller_id"] for o in orders) == sorted([shop.seller, other_seller])
    assert {o["total"] for o in orders} == {"37.50", "5.00"}
    assert all(o["shipping_address_id"] == shop.address for o in orders)  # the default
    assert stock(fresh_conn, shop.products[0]) == (10, 3)
    assert (await fresh_client.get("/cart", headers=user(shop.buyer))).json()["items"] == []
    events = fresh_conn.execute(
        "SELECT count(*) FROM outbox WHERE topic = 'order.placed' AND published_at IS NULL"
    ).fetchone()
    assert events == (2,)

    # The history shows them at once (primary, read-your-writes).
    r = await fresh_client.get(f"/users/{shop.buyer}/orders", headers=user(shop.buyer))
    assert {o["id"] for o in r.json()["items"]} == {o["id"] for o in orders}
    r = await fresh_client.get(f"/users/{shop.buyer}/orders", headers=user(shop.buyer + 1))
    assert r.status_code == 403


async def test_checkout_is_idempotent(
    fresh_client: httpx.AsyncClient, fresh_conn: psycopg.Connection
) -> None:
    shop = make_shop(fresh_conn, products=1, stock=10)
    pid = shop.products[0]
    await fill_cart(fresh_client, shop.buyer, [(pid, 2)])
    k = key()
    first = await fresh_client.post("/checkout", headers={**user(shop.buyer), **k})
    again = await fresh_client.post("/checkout", headers={**user(shop.buyer), **k})
    assert first.status_code == 201 and again.status_code == 200
    assert again.headers["idempotent-replayed"] == "true" and again.json()["replayed"] is True
    assert again.json()["orders"] == first.json()["orders"]
    assert stock(fresh_conn, pid) == (10, 2)  # reserved once

    # The key identifies the request, not the cart: a checkout with a used key
    # replays the first answer, even if the cart has changed since.
    await fill_cart(fresh_client, shop.buyer, [(pid, 1)])
    r = await fresh_client.post("/checkout", headers={**user(shop.buyer), **k})
    assert r.status_code == 200 and r.json()["orders"] == first.json()["orders"]
    assert stock(fresh_conn, pid) == (10, 2)
    # Keys are per user: the same key from another user is that user's first request.
    other = make_user(fresh_conn)
    await fill_cart(fresh_client, other, [(pid, 1)])
    r = await fresh_client.post("/checkout", headers={**user(other), **k})
    assert r.status_code == 201 and stock(fresh_conn, pid) == (10, 3)


async def test_checkout_insufficient_stock_is_409(
    fresh_client: httpx.AsyncClient, fresh_conn: psycopg.Connection
) -> None:
    shop = make_shop(fresh_conn, products=2, stock=2)
    a, b = shop.products
    await fill_cart(fresh_client, shop.buyer, [(a, 1), (b, 3)])
    r = await fresh_client.post("/checkout", headers={**user(shop.buyer), **key()})
    assert r.status_code == 409
    assert r.headers["content-type"] == "application/problem+json"
    problem = r.json()
    assert problem["type"].endswith("/insufficient-stock")
    assert (problem["product_id"], problem["requested"], problem["available"]) == (b, 3, 2)
    assert stock(fresh_conn, a) == (2, 0)  # all or nothing
    cart = (await fresh_client.get("/cart", headers=user(shop.buyer))).json()
    assert len(cart["items"]) == 2  # the cart is kept, to be fixed by the buyer


async def test_checkout_request_errors(
    fresh_client: httpx.AsyncClient, fresh_conn: psycopg.Connection
) -> None:
    buyer = make_user(fresh_conn)
    r = await fresh_client.post("/checkout", headers=user(buyer))
    assert r.status_code == 400 and "Idempotency-Key" in r.json()["detail"]
    r = await fresh_client.post("/checkout", headers={**user(buyer), **key()})
    assert r.status_code == 422 and r.json()["type"].endswith("/empty-cart")
    r = await fresh_client.post("/checkout", headers=key())
    assert r.status_code == 401
    r = await fresh_client.post(
        "/cart/items", json={"product_id": 1, "quantity": 0}, headers=user(buyer)
    )
    assert r.status_code == 422 and r.json()["errors"][0]["loc"] == ["body", "quantity"]
    shop = make_shop(fresh_conn, products=1)
    fresh_conn.execute("UPDATE products SET status = 'draft' WHERE id = %s", (shop.products[0],))
    r = await fresh_client.post(
        "/cart/items", json={"product_id": shop.products[0]}, headers=user(buyer)
    )
    assert r.status_code == 422 and r.json()["type"].endswith("/product-unavailable")
    other = make_address(fresh_conn, make_user(fresh_conn))
    extra = make_product(fresh_conn, shop.seller, category_id=any_category(fresh_conn))
    await fill_cart(fresh_client, shop.buyer, [(extra, 1)])
    r = await fresh_client.post(
        "/checkout",
        json={"shipping_address_id": other},
        headers={**user(shop.buyer), **key()},
    )
    assert r.status_code == 422 and r.json()["type"].endswith("/address-mismatch")


async def test_concurrent_checkouts_never_oversell(
    fresh_client: httpx.AsyncClient, fresh_conn: psycopg.Connection
) -> None:
    """20 buyers, 5 units, 20 checkouts in flight at once through the API."""
    shop = make_shop(fresh_conn, products=1, stock=5)
    pid = shop.products[0]
    buyers = [make_user(fresh_conn) for _ in range(20)]
    for b in buyers:
        await fill_cart(fresh_client, b, [(pid, 1)])
    responses = await asyncio.gather(
        *(fresh_client.post("/checkout", headers={**user(b), **key()}) for b in buyers)
    )
    codes = sorted(r.status_code for r in responses)
    assert codes == [201] * 5 + [409] * 15
    assert stock(fresh_conn, pid) == (5, 5)
    n = fresh_conn.execute(
        "SELECT count(*) FROM order_items WHERE product_id = %s", (pid,)
    ).fetchone()
    assert n == (5,)


async def test_concurrent_retries_with_one_key_order_once(
    fresh_client: httpx.AsyncClient, fresh_conn: psycopg.Connection
) -> None:
    """A client that fires the same checkout five times at once (retries after
    timeouts, a double click): one order, five identical answers."""
    shop = make_shop(fresh_conn, products=1, stock=10)
    await fill_cart(fresh_client, shop.buyer, [(shop.products[0], 2)])
    k = key()
    responses = await asyncio.gather(
        *(fresh_client.post("/checkout", headers={**user(shop.buyer), **k}) for _ in range(5))
    )
    assert sorted(r.status_code for r in responses) == [200, 200, 200, 200, 201]
    assert len({str(r.json()["orders"]) for r in responses}) == 1
    assert stock(fresh_conn, shop.products[0]) == (10, 2)


# --- seller portal ----------------------------------------------------------------------------


def _two_sellers_with_orders(conn: psycopg.Connection) -> tuple[Shop, Shop]:
    a, b = make_shop(conn, products=1), make_shop(conn, products=1)
    for s in (a, b):
        conn.execute(
            "SELECT place_order(%s, %s::jsonb, NULL)",
            (s.buyer, f'[{{"product_id": {s.products[0]}, "quantity": 1}}]'),
        )
    return a, b


async def test_rls_seller_sees_only_own_orders(
    fresh_client: httpx.AsyncClient, fresh_conn: psycopg.Connection
) -> None:
    a, b = _two_sellers_with_orders(fresh_conn)
    for me, them in ((a, b), (b, a)):
        r = await fresh_client.get("/seller/orders", headers=seller(me.seller))
        orders = r.json()["items"]
        assert [o["user_id"] for o in orders] == [me.buyer]
        r = await fresh_client.get(
            f"/seller/products/{them.products[0]}", headers=seller(me.seller)
        )
        assert r.status_code == 404  # invisible, not "forbidden": RLS hides the row
    r = await fresh_client.get("/seller/orders", headers=seller(999_999))
    assert r.json()["items"] == []
    # The policy, not the query: the seller engine's role really is bazaar_seller.
    r = await fresh_client.get("/seller/orders")
    assert r.status_code == 401


async def test_optimistic_locking_conflict(
    fresh_client: httpx.AsyncClient, fresh_conn: psycopg.Connection
) -> None:
    shop = make_shop(fresh_conn, products=1, price=Decimal("10.00"))
    pid, me = shop.products[0], seller(shop.seller)
    seen = (await fresh_client.get(f"/seller/products/{pid}", headers=me)).json()
    r = await fresh_client.patch(
        f"/seller/products/{pid}", json={"version": seen["version"], "price": "12.00"}, headers=me
    )
    assert r.status_code == 200 and r.json()["price"] == "12.00"
    assert r.json()["version"] != seen["version"]
    # A second editor still holds the old version: 409, and the first edit stands.
    r = await fresh_client.patch(
        f"/seller/products/{pid}", json={"version": seen["version"], "title": "Mine!"}, headers=me
    )
    assert r.status_code == 409 and r.json()["type"].endswith("/concurrent-update")
    now = (await fresh_client.get(f"/seller/products/{pid}", headers=me)).json()
    assert (now["price"], now["title"]) == ("12.00", seen["title"])
    r = await fresh_client.patch(
        f"/seller/products/{pid}", json={"version": now["version"], "sku": "X"}, headers=me
    )
    assert r.status_code == 422  # sku is not editable (extra="forbid")


async def test_seller_stats_and_refresh(
    fresh_client: httpx.AsyncClient, fresh_conn: psycopg.Connection
) -> None:
    a, _ = _two_sellers_with_orders(fresh_conn)
    fresh_conn.execute("UPDATE orders SET status = 'paid', paid_at = now()")
    r = await fresh_client.get("/seller/stats", headers=seller(a.seller))
    assert r.json()["orders"] == 0  # the materialized view is not refreshed yet
    r = await fresh_client.post("/admin/refresh-stats")
    assert r.status_code == 403
    r = await fresh_client.post("/admin/refresh-stats", headers=ADMIN)
    assert r.status_code == 200 and r.json()["refreshed_rows"] == 2
    stats = (await fresh_client.get("/seller/stats", headers=seller(a.seller))).json()
    assert (stats["orders"], stats["items_sold"], stats["revenue"]) == (1, 1, "10.00")


async def test_seller_stats_on_reference_data(client: httpx.AsyncClient) -> None:
    r = await client.get(
        "/seller/stats",
        params={"date_from": "2025-11-01", "date_to": "2025-11-30"},
        headers=seller(7),
    )
    stats = r.json()
    engine = sa.create_engine(get_settings().dsn.replace("postgresql://", "postgresql+psycopg://"))
    with engine.connect() as c:
        expected = c.execute(
            sa.text(
                "SELECT sum(orders_count), sum(revenue) FROM seller_sales_daily"
                " WHERE seller_id = 7 AND day BETWEEN '2025-11-01' AND '2025-11-30'"
            )
        ).one()
    engine.dispose()
    assert (stats["orders"], Decimal(stats["revenue"])) == (expected[0], expected[1])
    assert len(stats["days"]) <= 30


# --- the background path: outbox -> relay -> jobs ---------------------------------------------


async def test_checkout_events_reach_the_job_queue(
    fresh_db: str, fresh_client: httpx.AsyncClient, fresh_conn: psycopg.Connection
) -> None:
    shop = make_shop(fresh_conn, products=1)
    await fill_cart(fresh_client, shop.buyer, [(shop.products[0], 1)])
    order_id = (await fresh_client.post("/checkout", headers={**user(shop.buyer), **key()})).json()[
        "orders"
    ][0]["id"]
    stop = asyncio.Event()
    relay = asyncio.create_task(
        outbox.run_relay(
            None,
            stop,
            dsn=fresh_db,
            poll_interval=0.1,
            make_publish=lambda c: outbox.ajobs_publisher(
                c, {"order.placed": ["order_confirmation"]}
            ),
        )
    )
    async with asyncio.timeout(5):
        while not fresh_conn.execute("SELECT count(*) FROM jobs").fetchone()[0]:
            await asyncio.sleep(0.05)
    stop.set()
    assert await relay == 1
    job = fresh_conn.execute("SELECT kind, payload->>'order_id' FROM jobs").fetchone()
    assert job == ("order_confirmation", str(order_id))
    assert outbox.pending_count(fresh_conn) == 0


# --- timeouts and errors ----------------------------------------------------------------------

slow = APIRouter()


@slow.get("/test/sleep")
async def _sleep(session: RwSession, seconds: float, statement_timeout_ms: int = 0) -> dict:
    if statement_timeout_ms:
        await session.execute(sa.text(f"SET LOCAL statement_timeout = {statement_timeout_ms}"))
    await session.execute(sa.text("SELECT pg_sleep(:s)"), {"s": seconds})
    return {"slept": seconds}


async def test_statement_timeout_is_503_and_deadline_is_504(fresh_db: str) -> None:
    s = settings_for(fresh_db, request_timeout_ms=300)
    async with running(s, extra_routers=[slow]) as c:
        r = await c.get("/test/sleep", params={"seconds": 1, "statement_timeout_ms": 100})
        assert r.status_code == 503 and r.json()["type"].endswith("/timeout")
        assert r.headers["retry-after"] == "1"
        r = await c.get("/test/sleep", params={"seconds": 2})
        assert r.status_code == 504 and r.json()["type"].endswith("/deadline")
        # The pool is still healthy afterwards.
        r = await c.get("/test/sleep", params={"seconds": 0})
        assert r.status_code == 200
        r = await c.get("/ready")
        assert r.json()["pools"]["rw"]["checked_out"] == 0
