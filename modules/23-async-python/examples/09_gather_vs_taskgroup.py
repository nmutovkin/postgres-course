"""Module 23, section 2.4: when one of several concurrent queries fails."""

import asyncio
import os
import time

import asyncpg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
T0 = 0.0


async def query(pool: asyncpg.Pool, name: str, sql: str):
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(sql)
    except asyncio.CancelledError:
        print(f"   {time.perf_counter() - T0:4.2f}s {name}: cancelled")
        raise
    finally:
        print(f"   {time.perf_counter() - T0:4.2f}s {name}: finished")


def jobs(pool):
    return [
        query(pool, "slow_a", "SELECT pg_sleep(1)::text || 'a'"),
        query(pool, "broken", "SELECT 1 / 0"),
        query(pool, "slow_b", "SELECT pg_sleep(1)::text || 'b'"),
    ]


async def main() -> None:
    global T0
    async with asyncpg.create_pool(DSN, min_size=3, max_size=3) as pool:
        print("gather():")
        T0 = time.perf_counter()
        try:
            await asyncio.gather(*jobs(pool))
        except asyncpg.DivisionByZeroError as e:
            print(f"   {time.perf_counter() - T0:4.2f}s gather raised {type(e).__name__}")
        await asyncio.sleep(1.2)  # the other two are still running in the background

        print("gather(return_exceptions=True):")
        T0 = time.perf_counter()
        results = await asyncio.gather(*jobs(pool), return_exceptions=True)
        print(f"   {time.perf_counter() - T0:4.2f}s results: {results}")

        print("TaskGroup:")
        T0 = time.perf_counter()
        try:
            async with asyncio.TaskGroup() as tg:
                for j in jobs(pool):
                    tg.create_task(j)
        except* asyncpg.DivisionByZeroError as eg:
            print(f"   {time.perf_counter() - T0:4.2f}s TaskGroup raised {eg!r}")


asyncio.run(main())
