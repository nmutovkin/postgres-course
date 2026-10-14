"""Module 23, section 2: what the event loop can and cannot overlap."""

import asyncio
import os
import time

import psycopg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")


async def nap_async(i: int) -> int:
    await asyncio.sleep(1)  # yields to the loop while waiting
    return i


async def nap_blocking(i: int) -> int:
    time.sleep(1)  # blocks the whole loop: nothing else can run
    return i


def query_sync(i: int) -> int:
    with psycopg.connect(DSN) as conn:  # a blocking driver call
        return conn.execute("SELECT pg_sleep(1), %s", (i,)).fetchone()[1]


async def query_sync_in_coroutine(i: int) -> int:
    return query_sync(i)  # an `async def` around blocking code is still blocking


async def query_async(i: int) -> int:
    async with await psycopg.AsyncConnection.connect(DSN) as conn:
        cur = await conn.execute("SELECT pg_sleep(1), %s", (i,))
        return (await cur.fetchone())[1]


async def timed(label: str, coro) -> None:
    t = time.perf_counter()
    result = await coro
    print(f"{label:48s} {time.perf_counter() - t:5.2f}s  {result}")


async def main() -> None:
    await timed("3 x asyncio.sleep(1), gathered", asyncio.gather(*(nap_async(i) for i in range(3))))
    await timed("3 x time.sleep(1) in coroutines, gathered", asyncio.gather(*(nap_blocking(i) for i in range(3))))
    await timed("3 x sync psycopg pg_sleep(1) in coroutines",
                asyncio.gather(*(query_sync_in_coroutine(i) for i in range(3))))  # fmt: skip
    await timed("3 x sync psycopg via asyncio.to_thread",
                asyncio.gather(*(asyncio.to_thread(query_sync, i) for i in range(3))))  # fmt: skip
    await timed("3 x async psycopg pg_sleep(1), gathered", asyncio.gather(*(query_async(i) for i in range(3))))


asyncio.run(main())
