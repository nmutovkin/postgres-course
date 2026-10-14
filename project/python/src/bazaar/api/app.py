"""The FastAPI application.

    uvicorn bazaar.api.app:app --workers 4 --timeout-graceful-shutdown 20
    uvicorn --factory bazaar.api.app:create_app       # the same, built per process

Lifespan (runs once per worker process, inside its event loop, after the fork):

startup   1. the Database: three async engines (rw, read, seller), instrumented
          2. the schema check: the database must be at the Alembic head this
             code was written for (module 26), else the process refuses to start
          3. ready: /ready answers 200 from now on
shutdown  uvicorn stops accepting connections and waits for in-flight requests
          (up to --timeout-graceful-shutdown), then this code runs: /ready says
          503 and the pools are closed, so PostgreSQL sees clean disconnects.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from bazaar import migrate
from bazaar import observability as obs
from bazaar.api import errors
from bazaar.api.db import Database
from bazaar.api.middleware import RequestContext
from bazaar.api.routes import admin, catalog, health, seller, shop
from bazaar.config import Settings, get_settings

log = logging.getLogger("bazaar.api")


def create_app(
    settings: Settings | None = None, *, extra_routers: Sequence[APIRouter] = ()
) -> FastAPI:
    """Build the app. Settings default to the environment (read at startup, not import).

    extra_routers: more endpoints (the module 28 exercises plug in this way).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        s = settings or get_settings()
        if os.environ.get("BAZAAR_LOG_FORMAT") == "json":
            obs.configure_logging(json_output=True)
        db = Database.create(s)
        try:
            if s.check_schema:
                async with db.rw.connect() as conn:
                    await conn.run_sync(migrate.check_schema)
            app.state.db = db
            app.state.shutting_down = False
            log.info("bazaar api ready (pid %d, pool %d+%d+%d)", os.getpid(),
                     s.pool_max_size, s.pool_max_size, s.seller_pool_size)  # fmt: skip
            yield
        finally:
            app.state.shutting_down = True
            await db.dispose()
            log.info("bazaar api stopped (pid %d)", os.getpid())

    app = FastAPI(
        title="Bazaar",
        version="0.28.0",
        summary="The Bazaar marketplace service (PostgreSQL course, module 28)",
        lifespan=lifespan,
    )
    errors.install(app)
    timeout = (settings or get_settings()).request_timeout_ms
    app.add_middleware(RequestContext, timeout_ms=timeout)
    for router in (health.router, catalog.router, shop.router, seller.router, admin.router):
        app.include_router(router)
    for router in extra_routers:
        app.include_router(router)
    return app


app = create_app()
