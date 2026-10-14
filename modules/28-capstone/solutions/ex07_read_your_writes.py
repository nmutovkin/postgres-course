"""Exercise 7: order history from the replica, with read-your-writes.

GET /users/{user_id}/orders reads the primary, because a replica may not have
the order the buyer placed a second ago. This variant reads the replica
whenever that is safe:

1. After every successful write request, the middleware asks the primary for
   its current WAL position (pg_current_wal_lsn(): at or after the commit of
   what this request wrote) and hands it to the client in a cookie.
2. GET /me/orders asks the read engine whether it has replayed that far:
       SELECT NOT pg_is_in_recovery() OR pg_last_wal_replay_lsn() >= :lsn
   (on a primary the answer is trivially yes). Caught up: read there. Not
   yet: read the primary. No cookie: the client wrote nothing, any replica will do.
The response says where it read from (X-Read-From), for tests and dashboards.

    app = create_app(extra_routers=[ex07_read_your_writes.router])
    app.add_middleware(RememberWritePosition)
"""

from __future__ import annotations

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Cookie, Query, Request, Response
from sqlalchemy.orm import raiseload, selectinload
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from bazaar.api.db import DB, UserId
from bazaar.api.schemas import OrderOut
from bazaar.orm.models import Order

COOKIE = "bazaar_lsn"
WRITES = {"POST", "PUT", "PATCH", "DELETE"}

router = APIRouter()


class RememberWritePosition:
    """Set-Cookie: bazaar_lsn=<primary WAL position> on successful write responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in WRITES:
            await self.app(scope, receive, send)
            return
        db = scope["app"].state.db

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] < 400:
                # The handler's transaction has committed (the session dependency
                # ends before the response starts), so this position covers it.
                async with db.rw.connect() as conn:
                    lsn = await conn.scalar(sa.text("SELECT pg_current_wal_lsn()::text"))
                cookie = f"{COOKIE}={lsn}; Path=/; Max-Age=60; HttpOnly; SameSite=Lax"
                message.setdefault("headers", []).append((b"set-cookie", cookie.encode()))
            await send(message)

        await self.app(scope, receive, send_wrapper)


FRESH_ENOUGH = sa.text(
    "SELECT NOT pg_is_in_recovery()"
    " OR coalesce(pg_last_wal_replay_lsn() >= CAST(CAST(:lsn AS text) AS pg_lsn), false)"
)


@router.get("/me/orders", response_model=list[OrderOut])
async def my_orders(
    request: Request,
    response: Response,
    user_id: UserId,
    db: DB,
    bazaar_lsn: Annotated[str | None, Cookie()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[OrderOut]:
    use_replica = True
    if bazaar_lsn:
        async with db.read.connect() as conn:
            use_replica = bool(await conn.scalar(FRESH_ENOUGH, {"lsn": bazaar_lsn}))
    factory = db.read_sessions if use_replica else db.sessions
    response.headers["X-Read-From"] = "read" if use_replica else "primary"
    stmt = (
        sa.select(Order)
        .where(Order.user_id == user_id)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(limit)
        .options(selectinload(Order.items), raiseload("*"))
    )
    async with factory() as session:
        orders = (await session.scalars(stmt)).all()
        return [OrderOut.model_validate(o) for o in orders]
