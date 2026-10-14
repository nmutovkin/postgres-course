"""Exercise 1 (solution): the same server without blocking the event loop."""

import asyncio
import os
import time

from psycopg_pool import AsyncConnectionPool

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
pool: AsyncConnectionPool  # created in main(), inside the running loop


async def handle_request(i: int) -> str:
    await asyncio.sleep(0.5)  # an async HTTP client would await here
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT title FROM products, pg_sleep(0.5) WHERE id = %s", (i + 1,)
        )
        return (await cur.fetchone())[0]


async def main() -> None:
    global pool
    async with AsyncConnectionPool(DSN, min_size=10, max_size=10) as pool:
        t = time.perf_counter()
        titles = await asyncio.gather(*(handle_request(i) for i in range(10)))
        print(f"{len(titles)} requests in {time.perf_counter() - t:.2f}s; first: {titles[0]!r}")


if __name__ == "__main__":
    asyncio.run(main())
