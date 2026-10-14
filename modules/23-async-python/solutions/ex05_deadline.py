"""Exercise 5: a query deadline that covers the pool wait and is enforced by the server."""

import asyncio
import time

import asyncpg

from bazaar.aio.db import create_asyncpg_pool

GRACE = 0.5  # client-side backstop beyond the deadline, for a hung network


async def query_with_deadline(pool: asyncpg.Pool, seconds: float, sql: str, *args):
    """Run `sql` and return its rows, or raise TimeoutError once `seconds` have passed.

    The budget starts now: time spent waiting for a pooled connection counts.
    The rest of the budget becomes the statement_timeout of a transaction, so the
    server stops the query itself; asyncio.timeout is only the backstop.
    """
    deadline = time.monotonic() + seconds
    try:
        async with asyncio.timeout(seconds + GRACE):
            async with pool.acquire(timeout=seconds) as conn:
                remaining_ms = int((deadline - time.monotonic()) * 1000)
                if remaining_ms <= 0:
                    raise TimeoutError("deadline passed while waiting for a connection")
                async with conn.transaction(readonly=True):
                    await conn.execute(
                        "SELECT set_config('statement_timeout', $1, true)", str(remaining_ms)
                    )
                    return await conn.fetch(sql, *args)
    except asyncpg.QueryCanceledError as e:
        raise TimeoutError(f"statement timeout after {seconds}s") from e


async def attempt(label: str, coro) -> None:
    t = time.perf_counter()
    try:
        rows = await coro
        outcome = f"{len(rows)} row(s)"
    except TimeoutError as e:
        outcome = f"TimeoutError ({e})"
    print(f"{label:34s} {time.perf_counter() - t:5.2f}s  {outcome}")


async def main() -> None:
    pool = await create_asyncpg_pool(min_size=1, max_size=1)
    try:
        await attempt("fast query, 1 s budget",
                      query_with_deadline(pool, 1.0, "SELECT id FROM sellers LIMIT 5"))
        await attempt("pg_sleep(5), 1 s budget",
                      query_with_deadline(pool, 1.0, "SELECT pg_sleep(5)"))

        async def hog():  # holds the only connection for 0.8 s
            async with pool.acquire() as conn:
                await conn.execute("SELECT pg_sleep(0.8)")

        hog_task = asyncio.create_task(hog())
        await asyncio.sleep(0.05)
        await attempt("pool busy 0.8 s, query 0.5 s, 1 s",
                      query_with_deadline(pool, 1.0, "SELECT pg_sleep(0.5)"))
        await hog_task
        active = await pool.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE query LIKE 'SELECT pg_sleep%' AND state = 'active'"
        )
        print("queries still running on the server:", active)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
