"""Exercise 5: rate limiting with the token bucket of revision 2803.

    app = create_app()
    app.add_middleware(RateLimit, rules={("POST", "/checkout"): Rule(rate=0.2, burst=5)})

A pure ASGI middleware: for a request that matches a rule, one autocommitted
`SELECT take_token(...)` on the primary (about 1 ms); 429 with Retry-After when
the bucket is empty. The bucket key is the user (X-User-Id), else the client
address. If the limiter itself fails (database trouble) the request goes
through: a rate limiter must not become the outage ("fail open"), and the
request will meet the same database anyway.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import sqlalchemy as sa
from fastapi import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from bazaar.api.errors import problem

log = logging.getLogger("bazaar.api.ratelimit")


@dataclass(frozen=True)
class Rule:
    rate: float  # tokens per second (0.2 = one every 5 s on average)
    burst: int  # bucket size: how many at once after a quiet period


class RateLimit:
    def __init__(self, app: ASGIApp, *, rules: dict[tuple[str, str], Rule]) -> None:
        self.app = app
        self.rules = rules

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        rule = self.rules.get((scope.get("method", ""), scope.get("path", "")))
        if scope["type"] != "http" or rule is None:
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        who = request.headers.get("x-user-id") or (request.client.host if request.client else "?")
        key = f"{scope['method']} {scope['path']}:{who}"
        try:
            db = request.app.state.db
            async with db.rw.begin() as conn:
                wait = await conn.scalar(
                    sa.text("SELECT take_token(:k, :r, :b)"),
                    {"k": key, "r": rule.rate, "b": rule.burst},
                )
        except Exception:  # noqa: BLE001 - fail open
            log.warning("rate limiter unavailable; letting %s through", key, exc_info=True)
            wait = 0
        if wait:
            response = problem(
                request,
                429,
                "rate-limited",
                "Too many requests",
                f"try again in {wait:.1f} s",
                headers={"Retry-After": str(math.ceil(wait))},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
