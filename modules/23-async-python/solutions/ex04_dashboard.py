"""Exercise 4: an operations dashboard, six panels queried concurrently with a deadline."""

import asyncio
import time
from typing import Any

from psycopg_pool import AsyncConnectionPool

from bazaar.aio.db import create_pool

TODAY = "2025-12-31"  # "today" in the large dataset
PANELS: dict[str, str] = {
    "orders_today": "SELECT count(*) FROM orders WHERE created_at >= %(today)s::date",
    "revenue_7d": """
        SELECT sum(total) FROM orders
        WHERE created_at >= %(today)s::date - 6 AND status IN ('paid', 'shipped', 'delivered')""",
    "open_orders": """
        SELECT status::text, count(*) FROM orders
        WHERE status IN ('pending', 'paid', 'shipped') GROUP BY 1 ORDER BY 1""",
    "top_sellers_30d": """
        SELECT s.shop_name, sum(o.total) AS revenue
        FROM orders o JOIN sellers s ON s.id = o.seller_id
        WHERE o.created_at >= %(today)s::date - 29 AND o.status IN ('paid', 'shipped', 'delivered')
        GROUP BY s.shop_name ORDER BY revenue DESC LIMIT 5""",
    "low_stock": """
        SELECT count(*) FROM products p JOIN inventory i ON i.product_id = p.id
        WHERE p.status = 'active' AND i.quantity - i.reserved <= 2""",
    "jobs_ready": "SELECT count(*) FROM jobs WHERE done_at IS NULL AND run_at <= now()",
}
SINGLE_VALUE = {"orders_today", "revenue_7d", "low_stock", "jobs_ready"}


async def panel(pool: AsyncConnectionPool, name: str, slow: bool = False) -> Any:
    sql = PANELS[name]
    async with pool.connection() as conn:
        if slow:
            await conn.execute("SELECT pg_sleep(3)")  # simulate a struggling query
        cur = await conn.execute(sql, {"today": TODAY})
        rows = await cur.fetchall()
    return rows[0][0] if name in SINGLE_VALUE else rows


async def dashboard(pool: AsyncConnectionPool, *, deadline: float = 2.0,
                    slow: str | None = None) -> dict[str, Any]:  # fmt: skip
    """All panels concurrently. A panel that misses the deadline is None; the others still show."""
    tasks = {name: asyncio.create_task(panel(pool, name, name == slow)) for name in PANELS}
    done, pending = await asyncio.wait(tasks.values(), timeout=deadline)
    for t in pending:
        t.cancel()  # the driver cancels the server-side query too
    await asyncio.gather(*pending, return_exceptions=True)  # wait until they are really gone
    result = {}
    for name, t in tasks.items():
        if t in done and t.exception() is None:
            result[name] = t.result()
        else:
            result[name] = None
    return result


async def sequential(pool: AsyncConnectionPool) -> dict[str, Any]:
    return {name: await panel(pool, name) for name in PANELS}


async def main() -> None:
    pool = await create_pool(min_size=len(PANELS), max_size=len(PANELS))
    try:
        await dashboard(pool)  # warm-up
        for label, fn in (("sequential", sequential), ("concurrent", dashboard)):
            t = time.perf_counter()
            data = await fn(pool)
            print(f"{label:10s} {(time.perf_counter() - t) * 1000:6.0f} ms")
        for k, v in data.items():
            print(f"  {k:16s} {v}")
        t = time.perf_counter()
        partial = await dashboard(pool, slow="top_sellers_30d")
        print(f"with a slow panel: {(time.perf_counter() - t) * 1000:.0f} ms, "
              f"missing: {[k for k, v in partial.items() if v is None]}")  # fmt: skip
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE query LIKE 'SELECT pg_sleep(3)%%'"
                " AND state = 'active'"
            )
            print("pg_sleep still running on the server:", (await cur.fetchone())[0])
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
