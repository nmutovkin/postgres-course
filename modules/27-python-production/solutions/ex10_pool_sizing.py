"""Exercise 10: size a pool with Little's law, then check it under load.

    cd project/python && python ../../modules/27-python-production/solutions/ex10_pool_sizing.py

The request: a seller dashboard (orders and revenue per day since July 2025,
for one of the 50 biggest sellers). Step 1 measures W, the time a request holds a
connection. Step 2 picks an arrival rate and computes L = lambda * W. Step 3
runs an open-loop load test (Poisson arrivals, like real users: nobody waits
for the previous user to finish) against pools of several sizes.
"""

from __future__ import annotations

import asyncio
import math
import random
import statistics
import time

import sqlalchemy as sa

from bazaar.sa.engine import create_async_engine

DASHBOARD = sa.text(
    """
    SELECT date_trunc('day', created_at) AS day, count(*), sum(total)
    FROM orders
    WHERE seller_id = :s AND created_at >= timestamptz '2025-07-01'
    GROUP BY 1 ORDER BY 1
    """
)


async def request(engine, seller: int, waits: list[float], lat: list[float]) -> None:  # noqa: ANN001
    t0 = time.perf_counter()
    async with engine.connect() as c:
        waits.append(time.perf_counter() - t0)
        await c.execute(DASHBOARD, {"s": seller})
    lat.append(time.perf_counter() - t0)


async def measure_w(engine) -> float:  # noqa: ANN001
    holds = []
    for i in range(200):
        async with engine.connect() as c:
            t0 = time.perf_counter()
            await c.execute(DASHBOARD, {"s": 1 + i % 50})
            holds.append(time.perf_counter() - t0)
    return statistics.mean(holds)


async def load(pool_size: int, rate: float, seconds: float = 4.0) -> str:
    engine = create_async_engine(pool_size=pool_size, pool_timeout=10)
    # Open every connection before the clock starts (connection setup is not
    # what we measure).
    conns = [await engine.connect() for _ in range(pool_size)]
    for c in conns:
        await c.close()
    rng = random.Random(1)
    waits: list[float] = []
    lat: list[float] = []
    tasks = []
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        tasks.append(asyncio.create_task(request(engine, 1 + rng.randrange(50), waits, lat)))
        await asyncio.sleep(rng.expovariate(rate))
    await asyncio.gather(*tasks)
    await engine.dispose()
    ms = sorted(x * 1000 for x in lat)
    w = sorted(x * 1000 for x in waits)
    return (
        f"pool {pool_size:>2}: {len(ms)} requests, latency p50 {ms[len(ms) // 2]:6.1f} ms,"
        f" p99 {ms[int(len(ms) * 0.99)]:7.1f} ms; pool wait p99 {w[int(len(w) * 0.99)]:7.1f} ms"
    )


async def main() -> None:
    engine = create_async_engine(pool_size=1)
    w = await measure_w(engine)
    await engine.dispose()
    rate = 400.0
    busy = rate * w
    print(f"W (connection held per request) = {w * 1000:.1f} ms")
    print(
        f"lambda = {rate:.0f} requests/s  ->  L = lambda * W = {busy:.2f} connections busy on average"
    )
    print(f"a pool of ceil(L) = {math.ceil(busy)} is the bare minimum; bursts need headroom")
    for size in (1, 2, 4, 8, 16):
        print(await load(size, rate))


if __name__ == "__main__":
    asyncio.run(main())
