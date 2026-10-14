"""Request-level concerns: request id, deadline, access log, SQL route tags.

Written as a pure ASGI middleware rather than @app.middleware("http"): it wraps
the whole request (including exception handlers and streaming bodies) and adds
no extra task per request.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from fastapi import Request
from fastapi.routing import APIRoute
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from bazaar import observability as obs
from bazaar.api.errors import problem

log = logging.getLogger("bazaar.api.access")


class RequestContext:
    """Per request: X-Request-ID (taken from the client/proxy or generated),
    a deadline (asyncio.timeout -> 504), and one access-log line with the status
    and duration. The request id is returned in the X-Request-ID response header
    and in problem bodies, but deliberately NOT put into the SQL comments: a value
    that differs per request would make every statement text unique, and asyncpg
    caches prepared statements by text (module 27, section 8.4)."""

    def __init__(self, app: ASGIApp, *, timeout_ms: int) -> None:
        self.app = app
        self.timeout = timeout_ms / 1000 if timeout_ms else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        rid = headers.get(b"x-request-id", b"").decode()[:64] or uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = rid
        status: dict[str, Any] = {"code": None}
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            await send(message)

        try:
            async with asyncio.timeout(self.timeout):
                await self.app(scope, receive, send_wrapper)
        except TimeoutError:
            if status["code"] is not None:  # response already started: nothing to fix
                raise
            # The handler was cancelled at its current await. An in-flight query
            # is cancelled too, and its connection is discarded, not reused.
            response = problem(
                Request(scope), 504, "deadline", "Request took too long", headers={}
            )
            await response(scope, receive, send_wrapper)
        finally:
            log.info(
                "%s %s %s",
                scope["method"],
                scope["path"],
                status["code"],
                extra={
                    "status": status["code"],
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "request_id": rid,
                },
            )


class TaggedRoute(APIRoute):
    """Runs every handler inside sql_context(route="GET /products/{product_id}"):
    the route *template*, not the URL, so pg_stat_activity and the slow-query
    log can be grouped by endpoint (module 27, section 8.4)."""

    def get_route_handler(self):  # noqa: ANN201
        handler = super().get_route_handler()
        tag = f"{','.join(sorted(self.methods))} {self.path_format}"

        async def tagged(request: Request):  # noqa: ANN202
            with obs.sql_context(route=tag):
                return await handler(request)

        return tagged
