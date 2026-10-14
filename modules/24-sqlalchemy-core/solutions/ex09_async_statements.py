"""Exercise 9: the seller statement on asyncio, reusing the sync report's statements."""

import asyncio
import time
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from bazaar.sa import reports
from bazaar.sa.engine import create_async_engine, create_engine
from bazaar.sa.tables import sellers

NOV = (date(2025, 11, 1), date(2025, 12, 1))


async def seller_statement_async(
    conn: AsyncConnection, seller_id: int, start: date, end: date
) -> reports.SellerStatement | None:
    """Same statements as reports.seller_statement; only the execution is awaited."""
    shop = await conn.scalar(sa.select(sellers.c.shop_name).where(sellers.c.id == seller_id))
    if shop is None:
        return None
    result = await conn.execute(reports.seller_statement_query(seller_id, start, end))
    lines = [reports.StatementLine(**row._asdict()) for row in result]
    zero = Decimal("0.00")
    return reports.SellerStatement(
        seller_id=seller_id,
        shop_name=shop,
        start=start,
        end=end,
        lines=lines,
        orders=len(lines),
        sales=sum((ln.total for ln in lines if ln.status in reports.SALE_STATUSES), zero),
        paid=sum((ln.paid for ln in lines), zero),
        refunded=sum((ln.refunded for ln in lines), zero),
    )


async def many(engine: AsyncEngine, ids: list[int], limit: int = 5):
    sem = asyncio.Semaphore(limit)

    async def one(sid: int):
        async with sem, engine.connect() as conn:
            return await seller_statement_async(conn, sid, *NOV)

    return await asyncio.gather(*(one(s) for s in ids))


async def main() -> None:
    ids = list(range(1, 21))
    engine = create_async_engine(pool_size=5)
    try:
        t = time.perf_counter()
        results = await many(engine, ids)
        t_async = time.perf_counter() - t
    finally:
        await engine.dispose()

    sync_engine = create_engine()
    t = time.perf_counter()
    with sync_engine.connect() as conn:
        expected = [reports.seller_statement(conn, s, *NOV) for s in ids]
    t_sync = time.perf_counter() - t

    assert results == expected  # dataclasses compare field by field
    print(f"20 statements: async (5 at a time) {t_async:.2f} s, sync one by one {t_sync:.2f} s")
    print("identical:", results == expected)
    print("total sales:", sum(r.sales for r in results), " orders:", sum(r.orders for r in results))
    print("largest:", max(results, key=lambda r: r.sales).shop_name)


asyncio.run(main())
