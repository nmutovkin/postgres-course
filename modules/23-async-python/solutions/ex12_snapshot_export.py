"""Exercise 12: a parallel export where all workers see the same snapshot.

Four connections read disjoint slices of `orders` concurrently while another
task keeps inserting orders. Thanks to pg_export_snapshot()/SET TRANSACTION
SNAPSHOT, the slices add up to exactly the state at the moment of the snapshot.
Inserted test orders are deleted at the end.
"""

import asyncio
import re

from bazaar.aio.db import asyncpg_connect

WORKERS = 4
SLICE = "SELECT count(*), coalesce(sum(total), 0) FROM orders WHERE id % $1 = $2"


async def writer(stop: asyncio.Event, inserted: list[int]) -> None:
    conn = await asyncpg_connect()
    while not stop.is_set():
        inserted.append(await conn.fetchval(
            "INSERT INTO orders (user_id, seller_id, total) VALUES (8, 1, 0) RETURNING id"
        ))  # fmt: skip
        await asyncio.sleep(0.005)
    await conn.close()


async def read_slice(snapshot: str, k: int, consistent: bool) -> tuple[int, object]:
    conn = await asyncpg_connect()
    try:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            if consistent:
                # Must be the first statement of the transaction; it can't take parameters.
                await conn.execute(f"SET TRANSACTION SNAPSHOT '{snapshot}'")
            await asyncio.sleep(0.05 * k)  # workers start at different moments
            return tuple(await conn.fetchrow(SLICE, WORKERS, k))
    finally:
        await conn.close()


async def export(consistent: bool) -> None:
    stop = asyncio.Event()
    inserted: list[int] = []
    coordinator = await asyncpg_connect()
    w = asyncio.create_task(writer(stop, inserted))
    await asyncio.sleep(0.05)
    try:
        async with coordinator.transaction(isolation="repeatable_read", readonly=True):
            snapshot = await coordinator.fetchval("SELECT pg_export_snapshot()")
            assert re.fullmatch(r"[0-9A-F-]+", snapshot), snapshot  # safe to embed
            expected = tuple(await coordinator.fetchrow(
                "SELECT count(*), coalesce(sum(total), 0) FROM orders"
            ))  # fmt: skip
            # The snapshot stays importable only while this transaction is open.
            slices = await asyncio.gather(
                *(read_slice(snapshot, k, consistent) for k in range(WORKERS))
            )
        stop.set()
        await w
        total = (sum(s[0] for s in slices), sum(s[1] for s in slices))
        label = "with SET TRANSACTION SNAPSHOT" if consistent else "independent snapshots"
        print(f"{label:30s} slices sum to {total[0]:,} orders; snapshot had {expected[0]:,};"
              f" match: {total == expected}; {len(inserted)} orders inserted meanwhile")  # fmt: skip
    finally:
        stop.set()
        await coordinator.execute("DELETE FROM orders WHERE id = ANY($1::bigint[])", inserted)
        await coordinator.close()


async def main() -> None:
    await export(consistent=False)
    await export(consistent=True)


if __name__ == "__main__":
    asyncio.run(main())
