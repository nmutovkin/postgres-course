"""Module 23, section 6: a product page assembled from independent queries."""

import asyncio
import os
import time

import asyncpg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
PRODUCT_ID = 2  # the most ordered (768 orders) and most reviewed (167) product

QUERIES = {
    "product": ("SELECT id, title, price, seller_id FROM products WHERE id = $1", "fetchrow"),
    "stock": ("SELECT quantity - reserved FROM inventory WHERE product_id = $1", "fetchval"),
    "rating": ("SELECT round(avg(rating), 2), count(*) FROM reviews WHERE product_id = $1", "fetchrow"),
    "recent_reviews": (
        "SELECT rating, title FROM reviews WHERE product_id = $1 ORDER BY created_at DESC LIMIT 3",
        "fetch",
    ),
    "bought_together": (
        """SELECT oi2.product_id, count(*) AS n
           FROM order_items oi1 JOIN order_items oi2
             ON oi2.order_id = oi1.order_id AND oi2.product_id <> oi1.product_id
           WHERE oi1.product_id = $1
           GROUP BY 1 ORDER BY n DESC, 1 LIMIT 3""",
        "fetch",
    ),
}


async def run_query(pool: asyncpg.Pool, name: str) -> tuple[str, object]:
    sql, method = QUERIES[name]
    async with pool.acquire() as conn:
        return name, await getattr(conn, method)(sql, PRODUCT_ID)


async def sequential(pool: asyncpg.Pool) -> dict:
    return dict([await run_query(pool, name) for name in QUERIES])


async def concurrent(pool: asyncpg.Pool) -> dict:
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(run_query(pool, name)) for name in QUERIES]
    return dict(t.result() for t in tasks)


async def main() -> None:
    async with asyncpg.create_pool(DSN, min_size=5, max_size=5) as pool:
        for fn in (sequential, concurrent):
            await fn(pool)  # warm-up: caches, prepared statements
            t = time.perf_counter()
            for _ in range(20):
                page = await fn(pool)
            print(f"{fn.__name__:10s} {(time.perf_counter() - t) / 20 * 1000:6.1f} ms per page")
        for name in QUERIES:  # how long does each query take on its own?
            t = time.perf_counter()
            for _ in range(20):
                await run_query(pool, name)
            ms = (time.perf_counter() - t) / 20 * 1000
            print(f"  {name:16s} {ms:5.1f} ms  {page[name]}")


asyncio.run(main())
