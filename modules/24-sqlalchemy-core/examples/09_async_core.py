"""Module 24, section 10: Core on asyncio (asyncpg and psycopg underneath)."""

import asyncio
import time

import sqlalchemy as sa

from bazaar.sa.engine import create_async_engine
from bazaar.sa.tables import orders

o = orders


def revenue_of(seller_id: int) -> sa.Select:
    return sa.select(sa.func.sum(o.c.total)).where(
        o.c.seller_id == seller_id, o.c.status.in_(["paid", "shipped", "delivered"])
    )


async def main(driver: str) -> None:
    engine = create_async_engine(driver=driver, pool_size=5)
    try:
        async with engine.connect() as conn:  # AsyncConnection
            n = await conn.scalar(sa.select(sa.func.count()).select_from(o))
            result = await conn.execute(
                sa.select(o.c.id, o.c.status).where(o.c.user_id == 8).order_by(o.c.id).limit(2)
            )
            print(f"[{driver}] orders={n}  first two of user 8: {result.all()}")

            # Streaming a big result: server-side cursor, rows arrive in batches
            stream = await conn.stream(sa.select(o.c.total).where(o.c.seller_id == 1))
            total = sum([row.total async for row in stream])
            print(f"[{driver}] seller 1, streamed sum of totals: {total}")

        async with engine.begin() as conn:  # BEGIN ... COMMIT, like the sync engine
            await conn.execute(sa.text("SELECT 1"))

        async def one(seller_id: int):
            async with engine.connect() as conn:  # one connection per concurrent task
                return await conn.scalar(revenue_of(seller_id))

        t = time.perf_counter()
        sequential = [await one(s) for s in range(1, 21)]
        t_seq = time.perf_counter() - t
        t = time.perf_counter()
        concurrent = await asyncio.gather(*(one(s) for s in range(1, 21)))
        t_con = time.perf_counter() - t
        assert sequential == concurrent
        print(f"[{driver}] 20 revenue queries: sequential {t_seq:.2f} s, gather {t_con:.2f} s")
    finally:
        await engine.dispose()


asyncio.run(main("asyncpg"))
asyncio.run(main("psycopg"))
