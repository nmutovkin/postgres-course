"""Exercise 11: what prepared statements are worth, per driver."""

import asyncio
import os
import statistics
import time

import asyncpg
import psycopg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
BODY = """
    SELECT o.id, o.status, o.total, s.shop_name, count(oi.*) AS items
    FROM orders o
    JOIN sellers s ON s.id = o.seller_id
    LEFT JOIN order_items oi ON oi.order_id = o.id
    WHERE o.user_id = {p}
    GROUP BY o.id, s.shop_name
    ORDER BY o.created_at DESC
    LIMIT 5
"""
N = 3000


async def bench_asyncpg(**kw) -> tuple[float, int]:
    conn = await asyncpg.connect(DSN, **kw)
    sql = BODY.format(p="$1")
    runs = []
    for _ in range(3):
        t = time.perf_counter()
        for uid in range(1, N + 1):
            await conn.fetch(sql, uid)
        runs.append(time.perf_counter() - t)
    prepared = await conn.fetchval("SELECT count(*) FROM pg_prepared_statements")
    await conn.close()
    return statistics.median(runs), prepared


async def bench_psycopg(**kw) -> tuple[float, int]:
    conn = await psycopg.AsyncConnection.connect(DSN, autocommit=True, **kw)
    sql = BODY.format(p="%s")
    runs = []
    for _ in range(3):
        t = time.perf_counter()
        for uid in range(1, N + 1):
            cur = await conn.execute(sql, (uid,))
            await cur.fetchall()
        runs.append(time.perf_counter() - t)
    cur = await conn.execute("SELECT count(*) FROM pg_prepared_statements", prepare=False)
    prepared = (await cur.fetchone())[0]
    await conn.close()
    return statistics.median(runs), prepared


async def main() -> None:
    cases = [
        ("asyncpg, statement cache (default)", bench_asyncpg()),
        ("asyncpg, statement_cache_size=0", bench_asyncpg(statement_cache_size=0)),
        ("psycopg async, prepare_threshold=0", bench_psycopg(prepare_threshold=0)),
        ("psycopg async, prepare_threshold=5", bench_psycopg()),
        ("psycopg async, prepare_threshold=None", bench_psycopg(prepare_threshold=None)),
    ]
    print(f"{N} executions of a 3-join query, one connection, median of 3")
    for label, coro in cases:
        dt, prepared = await coro
        print(f"  {label:40s} {dt / N * 1e6:6.0f} µs/query   prepared statements: {prepared}")


if __name__ == "__main__":
    asyncio.run(main())
