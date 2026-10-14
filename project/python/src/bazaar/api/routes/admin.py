"""Operations endpoints, behind X-Admin-Token (BAZAAR_ADMIN_TOKEN).

POST /admin/refresh-stats   REFRESH MATERIALIZED VIEW CONCURRENTLY seller_sales_daily,
                            through module 15's SECURITY DEFINER function
                            refresh_seller_sales_daily() (the app role does not own
                            the view, so it could not refresh it directly).

CONCURRENTLY keeps the view readable while it is rebuilt (it needs the unique
index on (seller_id, day)), at the price of a slower refresh: a diff against
the old contents. On the large dataset it takes a few seconds, longer than the
5-second statement_timeout every connection starts with, so this transaction
raises its own limit with SET LOCAL. In a bigger system this would be a job
(module 10's queue), scheduled, not an HTTP request.
"""

from __future__ import annotations

import time

import sqlalchemy as sa
from fastapi import APIRouter, Depends

from bazaar.api.db import RwSession, require_admin
from bazaar.api.middleware import TaggedRoute

router = APIRouter(
    prefix="/admin", route_class=TaggedRoute, tags=["admin"], dependencies=[Depends(require_admin)]
)


@router.post("/refresh-stats")
async def refresh_stats(session: RwSession) -> dict[str, float | int]:
    await session.execute(sa.text("SET LOCAL statement_timeout = '120s'"))
    t0 = time.perf_counter()
    await session.execute(sa.text("SELECT refresh_seller_sales_daily()"))
    elapsed = time.perf_counter() - t0
    rows = await session.scalar(sa.text("SELECT count(*) FROM seller_sales_daily"))
    return {"refreshed_rows": rows or 0, "duration_ms": round(elapsed * 1000, 1)}
