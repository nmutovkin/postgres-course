"""Module 23, section 5: one connection runs one query at a time."""

import asyncio
import os
import time

import asyncpg
import psycopg
from psycopg_pool import AsyncConnectionPool

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")


async def main() -> None:
    # asyncpg: a second query on a busy connection is an error
    conn = await asyncpg.connect(DSN)
    try:
        await asyncio.gather(conn.fetchval("SELECT pg_sleep(0.5)"), conn.fetchval("SELECT 1"))
    except asyncpg.InterfaceError as e:
        print("asyncpg, shared connection:", type(e).__name__ + ":", e)
    await asyncio.sleep(0.6)
    await conn.close()

    # psycopg: queries on a shared connection wait for each other (a lock), no error
    aconn = await psycopg.AsyncConnection.connect(DSN, autocommit=True)
    t = time.perf_counter()
    await asyncio.gather(*(aconn.execute("SELECT pg_sleep(0.5)") for _ in range(4)))
    print(f"psycopg, shared connection: 4 x 0.5 s took {time.perf_counter() - t:.2f}s")
    await aconn.close()

    # a pool: each task borrows its own connection, the waits overlap
    async with AsyncConnectionPool(DSN, min_size=4, max_size=4) as pool:

        async def one() -> None:
            async with pool.connection() as c:
                await c.execute("SELECT pg_sleep(0.5)")

        t = time.perf_counter()
        await asyncio.gather(*(one() for _ in range(4)))
        print(f"psycopg, pool of 4:         4 x 0.5 s took {time.perf_counter() - t:.2f}s")
        t = time.perf_counter()
        await asyncio.gather(*(one() for _ in range(8)))
        print(f"psycopg, pool of 4:         8 x 0.5 s took {time.perf_counter() - t:.2f}s")


asyncio.run(main())
