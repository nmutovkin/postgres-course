"""Exercise 1 (starting point): a tiny "server" that handles 10 requests concurrently... or does it?

Do not change main(); fix handle_request() so the 10 requests overlap.
"""

import asyncio
import os
import time

import psycopg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")


async def handle_request(i: int) -> str:
    time.sleep(0.5)  # "call the recommendation service"
    with psycopg.connect(DSN) as conn:  # look up the product
        title = conn.execute(
            "SELECT title FROM products, pg_sleep(0.5) WHERE id = %s", (i + 1,)
        ).fetchone()[0]
    return title


async def main() -> None:
    t = time.perf_counter()
    titles = await asyncio.gather(*(handle_request(i) for i in range(10)))
    print(f"{len(titles)} requests in {time.perf_counter() - t:.2f}s; first: {titles[0]!r}")


if __name__ == "__main__":
    asyncio.run(main())
