"""Module 23 milestone: the async repository layer, asyncpg setup, notifications."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from decimal import Decimal

import psycopg
import pytest

from bazaar import db
from bazaar.aio import db as adb
from bazaar.aio import repo as arepo
from bazaar.errors import InsufficientStock
from bazaar.models import ProductStatus

from .factories import cleanup, make_product, make_seller, make_user

# --- the async repo mirrors the sync one --------------------------------------------


async def test_async_get_and_search(aconn: psycopg.AsyncConnection) -> None:
    cur = await aconn.execute(
        "INSERT INTO sellers (user_id, shop_name, country_code)"
        " SELECT id, 'Async Shop ' || id, 'FR' FROM users WHERE id = 1 RETURNING id"
    )
    row = await cur.fetchone()
    assert row
    seller = row[0]
    cur = await aconn.execute(
        "INSERT INTO products (seller_id, category_id, sku, title, price, status)"
        " VALUES (%s, 3, 'ASYNC-1', 'Quixplorer Async Gadget', 12.34, 'active') RETURNING id",
        (seller,),
    )
    row = await cur.fetchone()
    assert row
    pid = row[0]
    p = await arepo.get_product(aconn, pid)
    assert p is not None and p.price == Decimal("12.34") and p.status is ProductStatus.ACTIVE
    assert p.available is None  # no inventory row
    hits = await arepo.search_products(aconn, "quixplorer")
    assert [h.id for h in hits] == [pid]


async def test_async_place_order_errors_and_pagination(aconn: psycopg.AsyncConnection) -> None:
    cur = await aconn.execute(
        "SELECT p.id FROM products p JOIN inventory i ON i.product_id = p.id"
        " WHERE p.status = 'active' AND i.quantity - i.reserved = 1 ORDER BY p.id LIMIT 1"
    )
    row = await cur.fetchone()
    assert row
    pid = row[0]
    with pytest.raises(InsufficientStock) as exc:
        await arepo.place_order(aconn, 8, [(pid, 2)])
    assert (exc.value.requested, exc.value.available) == (2, 1)
    [order_id] = await arepo.place_order(aconn, 8, [(pid, 1)])
    page = await arepo.list_orders_for_user(aconn, 8, limit=1)
    assert page.items[0].id == order_id and page.next_cursor is not None
    page2 = await arepo.list_orders_for_user(aconn, 8, limit=1, after=page.next_cursor)
    assert page2.items[0].id != order_id


# --- asyncpg setup --------------------------------------------------------------------


async def test_asyncpg_pool_decodes_jsonb() -> None:
    pool = await adb.create_asyncpg_pool(min_size=1, max_size=2)
    try:
        attrs = await pool.fetchval("SELECT attributes FROM products WHERE id = 1")
        assert isinstance(attrs, dict) and attrs["color"] == "Black"
        name = await pool.fetchval("SELECT current_setting('application_name')")
        assert name == "bazaar"
    finally:
        await pool.close()


# --- committed data: concurrency and notifications -----------------------------------


@pytest.fixture
def scarce_product() -> Iterator[tuple[int, int]]:
    """A committed buyer and a product with exactly 5 units; removed afterwards."""
    with db.connect(autocommit=True) as conn:
        with conn.transaction():
            buyer = make_user(conn)
            seller = make_seller(conn)
            pid = make_product(conn, seller, stock=5)
        yield buyer, pid
        cleanup(conn, [buyer], [seller])


async def test_concurrent_checkouts_never_oversell(scarce_product: tuple[int, int]) -> None:
    buyer, pid = scarce_product
    pool = await adb.create_pool(open=True)
    outcomes: list[str] = []

    async def checkout() -> None:
        async with pool.connection() as conn:
            try:
                await arepo.place_order(conn, buyer, [(pid, 1)])
                outcomes.append("ok")
            except InsufficientStock:
                outcomes.append("sold out")

    try:
        async with asyncio.TaskGroup() as tg:
            for _ in range(20):
                tg.create_task(checkout())
        async with pool.connection() as conn:
            p = await arepo.get_product(conn, pid)
    finally:
        await pool.close()
    assert outcomes.count("ok") == 5 and outcomes.count("sold out") == 15
    assert p is not None and p.available == 0


async def test_new_order_notification(scarce_product: tuple[int, int]) -> None:
    buyer, pid = scarce_product
    listener = await adb.connect(autocommit=True)
    try:
        await listener.execute("LISTEN new_order")
        async with await adb.connect() as conn:
            # Start the outer transaction first, so place_order's transaction()
            # block becomes a savepoint instead of committing on its own.
            await conn.execute("SELECT 1")
            [order_id] = await arepo.place_order(conn, buyer, [(pid, 2)])
            # not committed yet: nothing may arrive
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.3):
                    async for _ in listener.notifies():
                        pass
            await conn.commit()
        async with asyncio.timeout(5):
            gen = listener.notifies()
            note = await anext(gen)
            await gen.aclose()
        assert note.channel == "new_order"
        assert json.loads(note.payload)["order_id"] == order_id
    finally:
        await listener.close()
