"""Exercise 8: a live per-seller order counter fed by NOTIFY (needs revision 2601).

Places 30 real orders (plus one that is rolled back) and removes them at the end.
"""

import asyncio
import json
from collections import Counter

import asyncpg

from bazaar.aio import repo
from bazaar.aio.db import asyncpg_connect, create_pool

PRODUCTS = {1: 822, 2: 368, 3: 5485, 4: 4241}  # seller -> an active product with stock
BUYERS = [8, 13, 11, 17, 20]


async def counter(queue: asyncio.Queue, counts: Counter, seen: list[int]) -> None:
    while True:
        payload = await queue.get()
        if payload is None:
            return
        event = json.loads(payload)
        counts[event["seller_id"]] += 1
        seen.append(event["order_id"])


async def shop(pool) -> list[int]:
    created: list[int] = []
    sellers = list(PRODUCTS)
    for i in range(30):
        seller = sellers[i % 3 if i < 24 else 3]  # sellers 1-3 get 8 each, seller 4 gets 6
        async with pool.connection() as conn:
            created += await repo.place_order(conn, BUYERS[i % len(BUYERS)], [(PRODUCTS[seller], 1)])
        await asyncio.sleep(0.01)
    async with pool.connection() as conn:  # this one never commits
        await conn.execute("SELECT 1")  # outer transaction: place_order becomes a savepoint
        await repo.place_order(conn, 8, [(PRODUCTS[4], 1)])
        await conn.rollback()
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
    queue: asyncio.Queue = asyncio.Queue()
    counts: Counter = Counter()
    seen: list[int] = []
    listener: asyncpg.Connection = await asyncpg_connect()
    # The callback runs on the event loop and must be quick: hand the payload over.
    await listener.add_listener("new_order", lambda c, pid, ch, payload: queue.put_nowait(payload))
    pool = await create_pool()
    try:
        consumer = asyncio.create_task(counter(queue, counts, seen))
        created = await shop(pool)
        await asyncio.sleep(0.2)  # let the last notifications arrive
        queue.put_nowait(None)
        await consumer
        print("orders per seller from notifications:", dict(sorted(counts.items())))
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT seller_id, count(*) FROM orders WHERE id = ANY(%s) GROUP BY 1 ORDER BY 1",
                (created,),
            )
            from_db = dict(await cur.fetchall())
        print("orders per seller in the database:   ", from_db)
        assert dict(counts) == from_db and sorted(seen) == sorted(created)
        await cleanup(pool, created)
    finally:
        await listener.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
