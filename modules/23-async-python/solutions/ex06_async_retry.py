"""Exercise 6: an async retry decorator for asyncpg transactions, proven under contention."""

import asyncio
import functools
import random
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

import asyncpg

from bazaar.aio.db import create_asyncpg_pool

P = ParamSpec("P")
R = TypeVar("R")
RETRYABLE = (
    asyncpg.SerializationError,          # 40001
    asyncpg.DeadlockDetectedError,       # 40P01
    asyncpg.InvalidCachedStatementError,  # schema changed under a cached statement (in a txn)
)  # fmt: skip
stats = {"retries": 0}


def retry_transaction(max_attempts: int = 10, base_delay: float = 0.005):
    """Re-run an async function that performs one whole transaction on a pool.

    The function takes the pool (not a connection): every attempt acquires a
    connection and starts a fresh transaction, so nothing from a failed
    attempt can leak into the next.
    """

    def decorate(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            for attempt in range(1, max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except RETRYABLE:
                    if attempt == max_attempts:
                        raise
                    stats["retries"] += 1
                    await asyncio.sleep(random.uniform(0, base_delay * 2 ** (attempt - 1)))
            raise AssertionError("unreachable")

        return wrapper

    return decorate


async def increment(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn, conn.transaction(isolation="serializable"):
        n = await conn.fetchval("SELECT n FROM ex06_counter WHERE id = 1")
        await asyncio.sleep(0)  # yield, so that other tasks interleave like real requests
        await conn.execute("UPDATE ex06_counter SET n = $1 WHERE id = 1", n + 1)


increment_with_retry = retry_transaction()(increment)


async def run(pool: asyncpg.Pool, fn, tasks: int = 20, each: int = 10) -> tuple[int, int]:
    failures = 0

    async def worker() -> None:
        nonlocal failures
        for _ in range(each):
            try:
                await fn(pool)
            except asyncpg.SerializationError:
                failures += 1

    async with asyncio.TaskGroup() as tg:
        for _ in range(tasks):
            tg.create_task(worker())
    return await pool.fetchval("SELECT n FROM ex06_counter WHERE id = 1"), failures


async def main() -> None:
    pool = await create_asyncpg_pool(min_size=10, max_size=10)
    await pool.execute("DROP TABLE IF EXISTS ex06_counter; CREATE TABLE ex06_counter (id int PRIMARY KEY, n int)")
    try:
        for label, fn in (("without retry", increment), ("with retry", increment_with_retry)):
            await pool.execute("TRUNCATE ex06_counter; INSERT INTO ex06_counter VALUES (1, 0)")
            stats["retries"] = 0
            n, failures = await run(pool, fn)
            print(f"{label:14s} n = {n:3d} (expected 200), failed = {failures}, retries = {stats['retries']}")
        assert n == 200 and failures == 0
    finally:
        await pool.execute("DROP TABLE ex06_counter")
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
