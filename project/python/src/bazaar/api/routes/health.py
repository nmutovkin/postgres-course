"""Liveness and readiness (module 27, section 5.5).

GET /health  liveness: the process answers. No database call: if PostgreSQL is
             down, restarting every API process would not help it come back.
GET /ready   readiness: should this process get traffic? check_health() on the
             primary (reachable, not a standby, schema at head), cached for two
             seconds so that load balancers polling every second do not become
             a load of their own. 503 while not ready or while shutting down.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from bazaar.api.db import DB
from bazaar.api.middleware import TaggedRoute
from bazaar.observability import check_health

router = APIRouter(route_class=TaggedRoute, tags=["health"])

CACHE_SECONDS = 2.0


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request, db: DB) -> JSONResponse:
    state = request.app.state
    if getattr(state, "shutting_down", False):
        return JSONResponse({"ok": False, "problems": ["shutting down"]}, status_code=503)
    cached: tuple[float, dict[str, Any]] | None = getattr(state, "ready_cache", None)
    if cached is None or time.monotonic() - cached[0] > CACHE_SECONDS:
        try:
            async with asyncio.timeout(1.0), db.rw.connect() as conn:
                result = await conn.run_sync(check_health)
        except Exception as e:  # noqa: BLE001 - any failure means "not ready"
            result = {"ok": False, "problems": [f"{type(e).__name__}: {e}"[:200]]}
        cached = (time.monotonic(), result)
        state.ready_cache = cached
    result = {**cached[1], "pools": db.pools()}
    return JSONResponse(result, status_code=200 if result["ok"] else 503)
