"""Module 27: concurrency tests. Many connections, started together on purpose.

A concurrency bug needs an interleaving to show up. Tests that just "run 20
tasks" often run them one after the other (each finishes before the next
starts). asyncio.Barrier / threading.Barrier make every task reach the
critical point before any of them proceeds, so the interleaving we want to
test actually happens. Every test runs in its own fresh database (committed
data, nothing to clean up).
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import asyncpg
import psycopg
import pytest
import sqlalchemy as sa

from bazaar.config import get_settings
from bazaar.errors import InsufficientStock, translate
from bazaar.orm import services
from bazaar.orm.session import make_async_sessionmaker
from bazaar.sa.engine import create_async_engine
from tests.factories import make_shop

N = 20


async def _asyncpg_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, min_size=N, max_size=N)


async def test_place_order_never_oversells(fresh_db: str) -> None:
    """20 buyers, 5 units, all calling place_order() at the same moment."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        shop = make_shop(c, products=1, stock=5)
    pid = shop.products[0]
    barrier = asyncio.Barrier(N)
    pool = await _asyncpg_pool(fresh_db)
    outcomes: list[str] = []

    async def buy() -> None:
        async with pool.acquire() as conn:
            await barrier.wait()  # everyone holds a connection; now go
            try:
                await conn.fetchval(
                    "SELECT place_order($1, $2::jsonb, NULL)",
                    shop.buyer,
                    f'[{{"product_id": {pid}, "quantity": 1}}]',
                )
                outcomes.append("ok")
            except asyncpg.PostgresError as e:
                assert isinstance(translate(e), InsufficientStock), e
                outcomes.append("sold out")

    try:
        async with asyncio.TaskGroup() as tg:
            for _ in range(N):
                tg.create_task(buy())
        qty, reserved = await pool.fetchrow(
            "SELECT quantity, reserved FROM inventory WHERE product_id = $1", pid
        )
    finally:
        await pool.close()
    assert outcomes.count("ok") == 5 and outcomes.count("sold out") == N - 5
    assert (qty, reserved) == (5, 5)


async def test_opposite_lock_orders_do_not_deadlock(fresh_db: str) -> None:
    """Carts [A, B] and [B, A] at the same time: place_order() locks inventory
    rows in product_id order, whatever the cart order, so no deadlock."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        shop = make_shop(c, products=2, stock=1000)
    a, b = shop.products
    barrier = asyncio.Barrier(N)
    pool = await _asyncpg_pool(fresh_db)
    errors: list[BaseException] = []

    async def buy(first: int, second: int) -> None:
        items = (
            f'[{{"product_id": {first}, "quantity": 1}}, {{"product_id": {second}, "quantity": 1}}]'
        )
        async with pool.acquire() as conn:
            await barrier.wait()
            try:
                await conn.fetchval("SELECT place_order($1, $2::jsonb, NULL)", shop.buyer, items)
            except asyncpg.PostgresError as e:  # a 40P01 would land here
                errors.append(e)

    try:
        async with asyncio.TaskGroup() as tg:
            for i in range(N):
                tg.create_task(buy(a, b) if i % 2 else buy(b, a))
        reserved = await pool.fetchval("SELECT sum(reserved) FROM inventory")
    finally:
        await pool.close()
    assert errors == []
    assert reserved == 2 * N


async def test_concurrent_retries_of_one_checkout_create_one_order(fresh_db: str) -> None:
    """A client times out and retries its checkout 8 times, all at once, with
    the same Idempotency-Key, through the async ORM service. Exactly one set of
    orders exists afterwards, stock is reserved once, one event per order is in
    the outbox, and every caller got the same orders back."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        shop = make_shop(c, products=2, stock=10)
    cart = [(shop.products[0], 2), (shop.products[1], 1)]
    engine = create_async_engine(replace(get_settings(), dsn=fresh_db), pool_size=8)
    Session = make_async_sessionmaker(engine)
    barrier = asyncio.Barrier(8)

    async def attempt() -> list[int]:
        async with Session() as session, session.begin():
            await session.execute(sa.text("SELECT 1"))  # connection checked out
            await barrier.wait()
            orders = await services.checkout_async(
                session, shop.buyer, cart, shop.address, idempotency_key="retry-me-0001"
            )
            return [o.id for o in orders]

    try:
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(attempt()) for _ in range(8)]
        results = [t.result() for t in tasks]
        async with engine.connect() as c:
            n_orders = (await c.execute(sa.text("SELECT count(*) FROM orders"))).scalar_one()
            reserved = (
                await c.execute(sa.text("SELECT sum(reserved) FROM inventory"))
            ).scalar_one()
            events = (await c.execute(sa.text("SELECT count(*) FROM outbox"))).scalar_one()
    finally:
        await engine.dispose()
    assert n_orders == 1  # both products belong to the same seller: one order
    assert len({tuple(r) for r in results}) == 1
    assert reserved == 3 and events == 1


@pytest.mark.slow
async def test_pool_exhaustion_is_overload_not_retried(fresh_db: str) -> None:
    """More concurrent requests than connections: the pool's timeout turns the
    wait into an error (shed load) instead of an ever-growing queue."""
    from bazaar.resilience import ErrorKind, classify

    engine = create_async_engine(
        replace(get_settings(), dsn=fresh_db), pool_size=2, pool_timeout=0.2
    )
    errors: list[BaseException] = []

    async def hold() -> None:
        try:
            async with engine.connect() as c:
                await c.execute(sa.text("SELECT pg_sleep(0.5)"))
        except Exception as e:
            errors.append(e)

    try:
        async with asyncio.TaskGroup() as tg:
            for _ in range(4):
                tg.create_task(hold())
    finally:
        await engine.dispose()
    assert len(errors) == 2
    assert all(classify(e) == ErrorKind.OVERLOADED for e in errors)
