"""Module 23, section 7: consuming 'new_order' notifications, two ways.

Needs project/python/sql/2301_notify.sql applied. Places three real orders
(committed, so that notifications are sent) and removes them again at the end.
"""

import asyncio
import json
import os
import time

import asyncpg
import psycopg

from bazaar.aio import repo
from bazaar.aio.db import create_pool

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
T0 = time.perf_counter()


def stamp() -> str:
    return f"{time.perf_counter() - T0:5.2f}s"


async def asyncpg_listener(stop: asyncio.Event) -> None:
    conn = await asyncpg.connect(DSN)

    def on_new_order(conn, pid, channel, payload) -> None:  # a plain callback, not a coroutine
        print(f"{stamp()} asyncpg  got {channel}: {json.loads(payload)}")

    await conn.add_listener("new_order", on_new_order)
    await stop.wait()
    await conn.close()


async def psycopg_listener(stop: asyncio.Event) -> None:
    conn = await psycopg.AsyncConnection.connect(DSN, autocommit=True)
    await conn.execute("LISTEN new_order")
    while not stop.is_set():
        # timeout= ends the generator every 0.2 s so we can check `stop`.
        # (Don't wrap anext() in asyncio.timeout(): cancelling it closes the generator.)
        async for n in conn.notifies(timeout=0.2):
            print(f"{stamp()} psycopg  got {n.channel}: {json.loads(n.payload)}")
    await conn.close()


async def shop(pool) -> list[int]:
    created: list[int] = []
    await asyncio.sleep(0.3)  # let the listeners subscribe
    async with pool.connection() as conn, conn.transaction():  # the outer transaction
        created += await repo.place_order(conn, 8, [(3, 1), (4, 1)])  # one seller -> one order
        print(f"{stamp()} shop     placed {created[-1]} (not yet committed)")
        await asyncio.sleep(0.5)
    print(f"{stamp()} shop     committed")
    async with pool.connection() as conn:
        async with conn.transaction():
            created += await repo.place_order(conn, 13, [(4, 2)])
            created += await repo.place_order(conn, 21, [(5, 1)])
    print(f"{stamp()} shop     placed and committed {created[1:]}")
    await asyncio.sleep(0.3)
    return created


async def cleanup(pool, order_ids: list[int]) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            """WITH items AS (SELECT product_id, sum(quantity) AS q FROM order_items
                              WHERE order_id = ANY(%s) GROUP BY 1)
               UPDATE inventory i SET reserved = i.reserved - items.q
               FROM items WHERE i.product_id = items.product_id""",
            (order_ids,),
        )
        await conn.execute("DELETE FROM orders WHERE id = ANY(%s)", (order_ids,))


async def main() -> None:
    stop = asyncio.Event()
    pool = await create_pool()
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(asyncpg_listener(stop))
            tg.create_task(psycopg_listener(stop))
            created = await shop(pool)
            stop.set()
        await cleanup(pool, created)
    finally:
        await pool.close()


asyncio.run(main())
