"""Exercise 1: GET /admin/slow-queries, the top statements from pg_stat_statements.

from bazaar.api.app import create_app
app = create_app(extra_routers=[ex01_slow_queries.router])
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query

from bazaar.api.db import ReadSession, require_admin
from bazaar.api.middleware import TaggedRoute


class OrderBy(StrEnum):
    total = "total"  # where the database spends its time: fix these first
    mean = "mean"  # the slowest per call: what users wait for


router = APIRouter(prefix="/admin", route_class=TaggedRoute, dependencies=[Depends(require_admin)])

# The ORDER BY column comes from a closed enum, never from the request text:
# identifiers cannot be bound parameters, so they are chosen, not interpolated.
_SORT = {OrderBy.total: "total_exec_time", OrderBy.mean: "mean_exec_time"}

SQL = """
    SELECT queryid::text AS queryid,
           calls,
           round(total_exec_time::numeric, 1)  AS total_ms,
           round(mean_exec_time::numeric, 2)   AS mean_ms,
           round(max_exec_time::numeric, 1)    AS max_ms,
           rows,
           round(100 * shared_blks_hit::numeric
                 / nullif(shared_blks_hit + shared_blks_read, 0), 1) AS cache_hit_pct,
           substring(query FROM '/\\*(.*?)\\*/')                     AS tags,
           left(regexp_replace(query, '\\s+', ' ', 'g'), :width)     AS query
    FROM pg_stat_statements
    WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
      AND toplevel
      AND calls >= :min_calls
    ORDER BY {sort} DESC
    LIMIT :limit
"""


@router.get("/slow-queries")
async def slow_queries(
    session: ReadSession,
    order_by: OrderBy = OrderBy.total,
    min_calls: Annotated[int, Query(ge=1)] = 5,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    width: Annotated[int, Query(ge=40, le=2000)] = 300,
) -> dict:
    stmt = sa.text(SQL.format(sort=_SORT[order_by]))
    rows = await session.execute(stmt, {"min_calls": min_calls, "limit": limit, "width": width})
    since = await session.scalar(sa.text("SELECT stats_reset FROM pg_stat_statements_info"))
    return {"since": since, "order_by": order_by, "items": [dict(r) for r in rows.mappings()]}
