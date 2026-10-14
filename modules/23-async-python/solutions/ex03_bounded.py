"""Exercise 3: 500 queries, never more than 8 at a time."""

import asyncio
import time

import asyncpg

from bazaar.aio.db import create_asyncpg_pool

REVENUE = """
    SELECT coalesce(sum(total), 0) FROM orders
    WHERE seller_id = $1 AND status IN ('paid', 'shipped', 'delivered')
"""
LIMIT = 8


async def main() -> None:
    pool = await create_asyncpg_pool(min_size=1, max_size=20)  # the pool is NOT the limiter
    sem = asyncio.Semaphore(LIMIT)
    in_flight = peak = 0

    async def revenue(seller_id: int):
        nonlocal in_flight, peak
        async with sem:
            in_flight += 1
            peak = max(peak, in_flight)  # no lock needed: no await between read and write
            try:
                return seller_id, await pool.fetchval(REVENUE, seller_id)
            finally:
                in_flight -= 1

    t = time.perf_counter()
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(revenue(s)) for s in range(1, 501)]
    dt = time.perf_counter() - t
    per_seller = dict(t.result() for t in tasks)
    check = await pool.fetchval(
        "SELECT sum(total) FROM orders WHERE seller_id <= 500"
        " AND status IN ('paid', 'shipped', 'delivered')"
    )
    connections = await pool.fetchval(
        "SELECT count(*) FROM pg_stat_activity WHERE application_name = 'bazaar'"
    )
    await pool.close()
    print(f"500 queries in {dt:.2f}s, peak in flight: {peak}, pool connections opened: {connections}")
    print(f"sum of per-seller revenue: {sum(per_seller.values())}  check: {check}")
    assert peak <= LIMIT and sum(per_seller.values()) == check


if __name__ == "__main__":
    asyncio.run(main())
