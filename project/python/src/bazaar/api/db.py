"""Engines, session factories, and the dependencies that hand sessions to handlers.

One `Database` per process, created in the app's lifespan (inside the running
event loop, after uvicorn forked its workers) and disposed at shutdown:

    rw       the primary, as the application role: writes, and reads that must
             see the request's own writes (checkout, a user's orders, the cart)
    read     BAZAAR_REPLICA_DSN or the primary, read-only (default_transaction_
             read_only=on): catalog browsing, search, seller statistics
    seller   the primary as bazaar_seller, a role that row-level security applies
             to (module 15): the seller portal. Every transaction starts with
             SET LOCAL app.seller_id (BazaarSession's after_begin hook).

Transaction boundaries are explicit. Dependencies give a handler a session
that is *inside* a transaction (`async with factory.begin()`), committed when
the handler returns and rolled back when it raises, and the dependency has
scope="function", so the commit happens BEFORE the response is sent: a client
never sees 201 for a transaction that then fails to commit. Handlers that
retry (checkout) take the factory instead and run `arun_in_session()`, which
opens a fresh session and transaction per attempt.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from bazaar import observability as obs
from bazaar.api.errors import Forbidden, Unauthorized
from bazaar.config import Settings
from bazaar.orm.session import SELLER_KEY, make_async_sessionmaker
from bazaar.sa.engine import create_async_engine, create_async_read_engine


@dataclass
class Database:
    settings: Settings
    rw: AsyncEngine
    read: AsyncEngine
    seller: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    read_sessions: async_sessionmaker[AsyncSession]
    seller_sessions: async_sessionmaker[AsyncSession]
    stats: dict[str, obs.QueryStats] = field(default_factory=dict)

    @classmethod
    def create(cls, settings: Settings) -> Database:
        rw = create_async_engine(settings)
        read = create_async_read_engine(settings)
        seller = create_async_engine(
            _seller_settings(settings), pool_size=settings.seller_pool_size
        )
        stats = {
            name: obs.instrument_engine(engine, slow_ms=200)
            for name, engine in (("rw", rw), ("read", read), ("seller", seller))
        }
        for engine in (rw, read, seller):
            # Route tags only (low cardinality): asyncpg caches prepared
            # statements by text, and a request id would make every text unique.
            obs.install_sqlcommenter(engine, static={"app": "bazaar-api"})
        return cls(
            settings=settings,
            rw=rw,
            read=read,
            seller=seller,
            sessions=make_async_sessionmaker(rw),
            read_sessions=make_async_sessionmaker(read),
            seller_sessions=make_async_sessionmaker(seller),
            stats=stats,
        )

    async def dispose(self) -> None:
        for engine in (self.rw, self.read, self.seller):
            await engine.dispose()

    def pools(self) -> dict[str, dict]:
        return {
            "rw": obs.pool_metrics(self.rw),
            "read": obs.pool_metrics(self.read),
            "seller": obs.pool_metrics(self.seller),
        }


def _seller_settings(settings: Settings) -> Settings:
    from dataclasses import replace

    return replace(
        settings, dsn=settings.seller_dsn_or_default(), app_name=f"{settings.app_name}-seller"
    )


# --- dependencies ----------------------------------------------------------------------------


def get_db(request: Request) -> Database:
    return request.app.state.db


DB = Annotated[Database, Depends(get_db)]


async def _rw_session(db: DB) -> AsyncIterator[AsyncSession]:
    async with db.sessions.begin() as session:  # COMMIT on success, ROLLBACK on error
        yield session


async def _read_session(db: DB) -> AsyncIterator[AsyncSession]:
    async with db.read_sessions.begin() as session:
        yield session


# scope="function": the code after `yield` (COMMIT) runs before the response is sent.
RwSession = Annotated[AsyncSession, Depends(_rw_session, scope="function")]
ReadSession = Annotated[AsyncSession, Depends(_read_session, scope="function")]


# --- identity (a stand-in for real authentication) -----------------------------------------
#
# A real service would verify a session cookie or a bearer token (OAuth2/OIDC)
# and get the user id from it. These headers are trusted as is: fine for a
# course, for internal tools behind an authenticating gateway, never on the
# open internet.


def _positive_int_header(request: Request, name: str) -> int:
    raw = request.headers.get(name)
    if raw is None:
        raise Unauthorized(f"missing {name} header")
    try:
        value = int(raw)
    except ValueError:
        raise Unauthorized(f"{name} must be a positive integer") from None
    if value <= 0:
        raise Unauthorized(f"{name} must be a positive integer")
    return value


def current_user_id(request: Request) -> int:
    return _positive_int_header(request, "X-User-Id")


def current_seller_id(request: Request) -> int:
    return _positive_int_header(request, "X-Seller-Id")


def require_admin(request: Request) -> None:
    token = request.app.state.db.settings.admin_token
    if token is None:
        raise Forbidden("admin endpoints are disabled (set BAZAAR_ADMIN_TOKEN)")
    if request.headers.get("X-Admin-Token") != token:
        raise Forbidden("bad or missing X-Admin-Token")


UserId = Annotated[int, Depends(current_user_id)]
SellerId = Annotated[int, Depends(current_seller_id)]


async def _seller_session(db: DB, seller_id: SellerId) -> AsyncIterator[AsyncSession]:
    # info["seller_id"] makes BazaarSession run set_config('app.seller_id', ..., true)
    # (= SET LOCAL) at the start of the transaction; it ends with the transaction,
    # so a pooled connection never carries one seller's id into another request.
    async with db.seller_sessions(info={SELLER_KEY: seller_id}) as session, session.begin():
        yield session


SellerSession = Annotated[AsyncSession, Depends(_seller_session, scope="function")]
