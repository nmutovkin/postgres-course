"""Exercise 7: load a day of clickstream (100,000 events) into the partitioned events table.

Compares asyncpg's copy_records_to_table() with executemany(), and removes the rows afterwards.
"""

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta

from bazaar.aio.db import create_asyncpg_pool

DAY = datetime(2026, 1, 15, tzinfo=UTC)
TYPES = ["page_view", "product_view", "product_view", "search", "add_to_cart"]
COUNT_DAY = """
    SELECT count(*), array_agg(DISTINCT tableoid::regclass::text)
    FROM events WHERE occurred_at >= $1 AND occurred_at < $1 + interval '1 day'
"""
DELETE_DAY = "DELETE FROM events WHERE occurred_at >= $1 AND occurred_at < $1 + interval '1 day'"
COLUMNS = ["occurred_at", "user_id", "session_id", "event_type", "product_id", "payload"]


def make_events(n: int = 100_000) -> list[tuple]:
    rows = []
    for i in range(n):
        kind = TYPES[i % len(TYPES)]
        rows.append((
            DAY + timedelta(seconds=i * 86_400 / n),
            None if i % 4 == 0 else i % 100_000 + 1,          # a quarter are anonymous
            uuid.UUID(int=i // 7 + 1),                         # 7 events per session
            kind,
            i % 200_000 + 1 if kind in ("product_view", "add_to_cart") else None,
            json.dumps({"q": f"query {i % 50}"}) if kind == "search" else "{}",
        ))  # fmt: skip
    return rows


async def main() -> None:
    rows = make_events()
    # No jsonb codec here: payload values are JSON *strings*, which is what asyncpg
    # expects for jsonb by default (pool init would turn them into dicts).
    pool = await create_asyncpg_pool(min_size=1, max_size=1, init=None)
    async with pool.acquire() as conn:
        for label in ("copy_records_to_table", "executemany"):
            async with conn.transaction():
                t = time.perf_counter()
                if label == "copy_records_to_table":
                    await conn.copy_records_to_table("events", records=rows, columns=COLUMNS)
                else:
                    await conn.executemany(
                        "INSERT INTO events (occurred_at, user_id, session_id, event_type,"
                        " product_id, payload) VALUES ($1, $2, $3, $4, $5, $6)",
                        rows,
                    )
                dt = time.perf_counter() - t
                n, parts = await conn.fetchrow(COUNT_DAY, DAY)
                print(f"{label:22s} {dt:5.2f}s {len(rows) / dt:>9,.0f} rows/s"
                      f"  -> {n:,} rows in {parts}")  # fmt: skip
                deleted = await conn.execute(DELETE_DAY, DAY)
                print(f"{'':22s} cleanup: {deleted}")
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
