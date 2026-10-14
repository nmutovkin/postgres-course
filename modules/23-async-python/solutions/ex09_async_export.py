"""Exercise 9: export 1.27M order lines as NDJSON from async code, in constant memory.

    python ex09_async_export.py asyncpg  out.ndjson
    python ex09_async_export.py psycopg  out.ndjson
    python ex09_async_export.py fetch    out.ndjson     # the naive way, for comparison
"""

import asyncio
import json
import os
import resource
import sys
import time

import asyncpg
import psycopg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
QUERY = "SELECT order_id, product_id, quantity, unit_price FROM order_items"
BATCH = 5_000


def line(order_id, product_id, quantity, unit_price) -> str:
    # Decimal as a string: JSON numbers would go through float on the reader's side.
    return json.dumps({"order_id": order_id, "product_id": product_id,
                       "quantity": quantity, "unit_price": str(unit_price)}) + "\n"  # fmt: skip


async def with_asyncpg(path: str) -> int:
    conn = await asyncpg.connect(DSN)
    n = 0
    with open(path, "w") as f:
        async with conn.transaction():  # asyncpg cursors live in a transaction
            async for r in conn.cursor(QUERY, prefetch=BATCH):
                f.write(line(*r))
                n += 1
    await conn.close()
    return n


async def with_psycopg(path: str) -> int:
    conn = await psycopg.AsyncConnection.connect(DSN)
    n = 0
    with open(path, "w") as f:
        async with conn.transaction(), conn.cursor(name="export") as cur:
            cur.itersize = BATCH
            await cur.execute(QUERY)
            async for r in cur:
                f.write(line(*r))
                n += 1
    await conn.close()
    return n


async def naive_fetch(path: str) -> int:
    conn = await asyncpg.connect(DSN)
    rows = await conn.fetch(QUERY)  # everything in memory at once
    with open(path, "w") as f:
        f.writelines(line(*r) for r in rows)
    await conn.close()
    return len(rows)


if __name__ == "__main__":
    how, path = sys.argv[1], sys.argv[2]
    fn = {"asyncpg": with_asyncpg, "psycopg": with_psycopg, "fetch": naive_fetch}[how]
    t = time.perf_counter()
    n = asyncio.run(fn(path))
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"{how:8s} {n:,} lines in {time.perf_counter() - t:5.1f}s, peak RSS {peak:6.1f} MiB")
