"""Exercise 6: GET /seller/orders.csv, streamed through a server-side cursor.

A seller with 70,000 orders downloads them all. Loading them into a list
first would cost memory proportional to the export (and time before the first
byte); this endpoint keeps at most one batch of rows in memory:

* AsyncSession.stream() runs the query through a server-side cursor
  (asyncpg: a portal inside the transaction; module 22, section 10), and
  .partitions(n) fetches n rows per round trip;
* StreamingResponse sends each batch as soon as it is formatted.

The session must stay open while the body streams, i.e. AFTER the handler
returned. The portal's normal session dependency ends with the handler
(scope="function"); this one uses scope="request", which ends after the
response is sent. RLS applies as everywhere in the portal: the query has no
seller filter, the policies supply it.
"""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncIterator
from datetime import date
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from bazaar.api.db import DB, SellerId
from bazaar.api.middleware import TaggedRoute
from bazaar.orm.session import SELLER_KEY

router = APIRouter(prefix="/seller", route_class=TaggedRoute, tags=["seller"])

BATCH = 2000
COLUMNS = ["order_id", "created_at", "status", "user_id", "items", "total"]
EXPORT = sa.text("""
    SELECT o.id, o.created_at, o.status, o.user_id,
           (SELECT sum(quantity) FROM order_items oi WHERE oi.order_id = o.id) AS items,
           o.total
    FROM orders o
    WHERE o.created_at >= :since
    ORDER BY o.created_at, o.id
""")


async def _streaming_session(db: DB, seller_id: SellerId) -> AsyncIterator[AsyncSession]:
    async with db.seller_sessions(info={SELLER_KEY: seller_id}) as session, session.begin():
        # A long export must not be killed by the 5 s default, nor run forever.
        await session.execute(sa.text("SET LOCAL statement_timeout = '120s'"))
        yield session


StreamingSession = Annotated[AsyncSession, Depends(_streaming_session, scope="request")]


@router.get("/orders.csv")
async def export_orders(session: StreamingSession, since: date = date(2000, 1, 1)):  # noqa: ANN201
    result = await session.stream(EXPORT, {"since": since})

    async def body() -> AsyncIterator[str]:
        buf = io.StringIO()
        out = csv.writer(buf)
        out.writerow(COLUMNS)
        async for rows in result.partitions(BATCH):
            out.writerows(rows)
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()
        if buf.tell():
            yield buf.getvalue()

    return StreamingResponse(
        body(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="orders.csv"'},
    )
