"""Module 23, section 9: psycopg async vs asyncpg vs sync psycopg + threads.

Three workloads, same SQL and same concurrency for every driver; each
measurement is repeated and the median reported. Creates and drops its own
table `bench_insert`.

    python 07_driver_benchmark.py [small|fetch|insert]...
"""

import asyncio
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import psycopg
from psycopg_pool import AsyncConnectionPool, ConnectionPool

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
CONCURRENCY = 16
N_SMALL = 20_000
IDS = [(i * 7919) % 200_000 + 1 for i in range(N_SMALL)]  # deterministic spread of products
FETCH = "SELECT order_id, product_id, quantity, unit_price FROM order_items WHERE order_id <= 200000"
N_INSERT = 100_000
T0 = datetime(2025, 12, 1, tzinfo=UTC)
ROWS = [(f"SKU-{i:07d}", i % 2000 + 1, Decimal(i % 50_000) / 100, T0 + timedelta(seconds=i))
        for i in range(N_INSERT)]  # fmt: skip
REPEAT = 3


def median_time(fn, repeat: int = REPEAT) -> float:
    times = []
    for _ in range(repeat):
        t = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t)
    return statistics.median(times)


async def amedian_time(fn, repeat: int = REPEAT) -> float:
    times = []
    for _ in range(repeat):
        t = time.perf_counter()
        await fn()
        times.append(time.perf_counter() - t)
    return statistics.median(times)


def chunks(seq, n):
    k = len(seq) // n
    return [seq[i * k : (i + 1) * k] for i in range(n)]


# --- (a) many small queries ----------------------------------------------------------

LOOKUP_PG = "SELECT quantity - reserved FROM inventory WHERE product_id = %s"
LOOKUP_APG = "SELECT quantity - reserved FROM inventory WHERE product_id = $1"


async def small_queries() -> None:
    print(f"(a) {N_SMALL:,} point lookups, {CONCURRENCY} concurrent workers")
    apool = await asyncpg.create_pool(DSN, min_size=CONCURRENCY, max_size=CONCURRENCY)

    async def apg_worker(ids):
        async with apool.acquire() as c:  # each worker keeps its connection
            for pid in ids:
                await c.fetchval(LOOKUP_APG, pid)

    async def apg():
        await asyncio.gather(*(apg_worker(c) for c in chunks(IDS, CONCURRENCY)))

    ppool = AsyncConnectionPool(DSN, min_size=CONCURRENCY, max_size=CONCURRENCY,
                                kwargs={"autocommit": True}, open=False)  # fmt: skip
    await ppool.open(wait=True)

    async def pg_worker(ids):
        async with ppool.connection() as c:
            for pid in ids:
                cur = await c.execute(LOOKUP_PG, (pid,))
                await cur.fetchone()

    async def pg_async():
        await asyncio.gather(*(pg_worker(c) for c in chunks(IDS, CONCURRENCY)))

    spool = ConnectionPool(DSN, min_size=CONCURRENCY, max_size=CONCURRENCY,
                           kwargs={"autocommit": True}, open=True)  # fmt: skip
    spool.wait()

    def sync_worker(ids):
        with spool.connection() as c:
            for pid in ids:
                c.execute(LOOKUP_PG, (pid,)).fetchone()

    def pg_threads():
        with ThreadPoolExecutor(CONCURRENCY) as ex:
            list(ex.map(sync_worker, chunks(IDS, CONCURRENCY)))

    def pg_single():
        with spool.connection() as c:
            for pid in IDS:
                c.execute(LOOKUP_PG, (pid,)).fetchone()

    await apg()  # warm-up (caches, prepared statements)
    await pg_async()
    pg_threads()
    results = {
        "asyncpg, pool": await amedian_time(apg),
        "psycopg async, pool": await amedian_time(pg_async),
        "psycopg sync, pool + 16 threads": median_time(pg_threads),
        "psycopg sync, 1 connection, sequential": median_time(pg_single, 1),
    }
    for label, dt in results.items():
        print(f"    {label:40s} {dt:6.2f}s {N_SMALL / dt:>9,.0f} queries/s")
    await apool.close()
    await ppool.close()
    spool.close()


# --- (b) one large result ------------------------------------------------------------


async def large_fetch() -> None:
    aconn = await asyncpg.connect(DSN)
    n = await aconn.fetchval("SELECT count(*) FROM order_items WHERE order_id <= 200000")
    print(f"(b) fetch {n:,} rows x 4 columns into Python, one connection")

    async def apg():
        return await aconn.fetch(FETCH)

    pconn = await psycopg.AsyncConnection.connect(DSN, autocommit=True)

    async def pg_async():
        cur = await pconn.execute(FETCH)
        return await cur.fetchall()

    async def pg_async_binary():
        cur = await pconn.execute(FETCH, binary=True)
        return await cur.fetchall()

    sconn = psycopg.connect(DSN, autocommit=True)

    def pg_sync():
        return sconn.execute(FETCH).fetchall()

    results = {
        "asyncpg fetch()": await amedian_time(apg),
        "psycopg async fetchall()": await amedian_time(pg_async),
        "psycopg async fetchall(), binary=True": await amedian_time(pg_async_binary),
        "psycopg sync fetchall()": median_time(pg_sync),
    }
    for label, dt in results.items():
        print(f"    {label:40s} {dt:6.2f}s {n / dt:>9,.0f} rows/s")
    await aconn.close()
    await pconn.close()
    sconn.close()


# --- (c) bulk insert -----------------------------------------------------------------

CREATE = """
    DROP TABLE IF EXISTS bench_insert;
    CREATE TABLE bench_insert (sku text, seller_id bigint, price numeric(12,2), seen_at timestamptz)
"""
COLS = ["sku", "seller_id", "price", "seen_at"]


async def bulk_insert() -> None:
    print(f"(c) insert {N_INSERT:,} rows, one connection")
    admin = psycopg.connect(DSN, autocommit=True)
    aconn = await asyncpg.connect(DSN)
    pconn = await psycopg.AsyncConnection.connect(DSN)
    sconn = psycopg.connect(DSN)

    async def apg_copy():
        await aconn.copy_records_to_table("bench_insert", records=ROWS, columns=COLS)

    async def apg_many():
        async with aconn.transaction():
            await aconn.executemany(
                "INSERT INTO bench_insert VALUES ($1, $2, $3, $4)", ROWS
            )

    async def pg_async_copy():
        async with pconn.transaction(), pconn.cursor() as cur:
            async with cur.copy("COPY bench_insert FROM STDIN") as cp:
                for r in ROWS:
                    await cp.write_row(r)

    async def pg_async_many():
        async with pconn.transaction(), pconn.cursor() as cur:
            await cur.executemany("INSERT INTO bench_insert VALUES (%s, %s, %s, %s)", ROWS)

    def pg_sync_many():
        with sconn.transaction(), sconn.cursor() as cur:
            cur.executemany("INSERT INTO bench_insert VALUES (%s, %s, %s, %s)", ROWS)

    def pg_sync_copy():
        with sconn.transaction(), sconn.cursor() as cur:
            with cur.copy("COPY bench_insert FROM STDIN") as cp:
                for r in ROWS:
                    cp.write_row(r)

    async def measure(fn, is_async=True):
        times = []
        for _ in range(REPEAT):
            admin.execute(CREATE)
            t = time.perf_counter()
            if is_async:
                await fn()
            else:
                fn()
            times.append(time.perf_counter() - t)
            assert admin.execute("SELECT count(*) FROM bench_insert").fetchone()[0] == N_INSERT
        return statistics.median(times)

    results = {
        "asyncpg copy_records_to_table()": await measure(apg_copy),
        "psycopg async COPY write_row()": await measure(pg_async_copy),
        "psycopg sync COPY write_row()": await measure(pg_sync_copy, False),
        "asyncpg executemany()": await measure(apg_many),
        "psycopg async executemany()": await measure(pg_async_many),
        "psycopg sync executemany()": await measure(pg_sync_many, False),
    }
    for label, dt in results.items():
        print(f"    {label:40s} {dt:6.2f}s {N_INSERT / dt:>9,.0f} rows/s")
    admin.execute("DROP TABLE bench_insert")
    for c in (admin, sconn):
        c.close()
    await aconn.close()
    await pconn.close()


async def main(which: list[str]) -> None:
    print("asyncpg", asyncpg.__version__, "| psycopg", psycopg.__version__, psycopg.pq.__impl__,
          f"| Python {sys.version.split()[0]} | median of {REPEAT}")  # fmt: skip
    for name in which:
        await {"small": small_queries, "fetch": large_fetch, "insert": bulk_insert}[name]()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["small", "fetch", "insert"]))
