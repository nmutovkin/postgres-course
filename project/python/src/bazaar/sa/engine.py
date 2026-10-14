"""SQLAlchemy engines built from the same settings as bazaar.db.

    from bazaar.sa.engine import create_engine

    engine = create_engine()                 # ONE engine per process, at startup
    with engine.begin() as conn:             # BEGIN ... COMMIT (ROLLBACK on error)
        conn.execute(...)

    aengine = create_async_engine()          # asyncpg underneath (driver="psycopg" works too)
    async with aengine.connect() as conn:
        ...

The engine owns the connection pool (SQLAlchemy's QueuePool). Do not combine
it with psycopg_pool: two pools stacked on each other hold idle connections
twice and make sizing guesswork. If something else already pools (PgBouncer
in transaction mode, Module 17), pass `poolclass=NullPool`.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine
from sqlalchemy.pool import NullPool

from bazaar.config import Settings, get_settings

__all__ = ["NullPool", "create_async_engine", "create_engine", "sa_url"]


def sa_url(dsn: str, driver: str = "psycopg") -> URL:
    """Turn a libpq URI (postgresql://...) into a SQLAlchemy URL for `driver`.

    driver: "psycopg" (psycopg 3, sync or async) or "asyncpg".
    """
    if "://" not in dsn:
        raise ValueError("SQLAlchemy needs a postgresql:// URI; got a key=value conninfo string")
    url = make_url(dsn)
    if url.get_backend_name() != "postgresql":
        raise ValueError(f"not a PostgreSQL URL: {url.get_backend_name()}")
    return url.set(drivername=f"postgresql+{driver}")


def _pool_args(s: Settings) -> dict[str, Any]:
    # A hard ceiling like psycopg_pool's max_size: pool_size connections are
    # kept, and no "overflow" connections beyond that are opened.
    return {
        "pool_size": s.pool_max_size,
        "max_overflow": 0,
        "pool_timeout": 10,  # seconds to wait for a free connection, then TimeoutError
        "pool_pre_ping": True,  # test a connection on checkout (one round trip)
        "pool_recycle": 1800,  # replace connections older than 30 minutes
    }


def create_engine(settings: Settings | None = None, **overrides: Any) -> Engine:
    """The process-wide sync engine (psycopg 3 driver)."""
    s = settings or get_settings()
    connect_args: dict[str, Any] = {"application_name": s.app_name, "connect_timeout": 5}
    if s.statement_timeout_ms:
        connect_args["options"] = f"-c statement_timeout={s.statement_timeout_ms}"
    params: dict[str, Any] = {"connect_args": connect_args, **_pool_args(s)}
    params.update(overrides)  # e.g. create_engine(echo=True) or poolclass=NullPool
    if params.get("poolclass") is NullPool:
        for k in ("pool_size", "max_overflow", "pool_timeout"):
            params.pop(k, None)
    return sa.create_engine(sa_url(s.dsn, "psycopg"), **params)


def create_async_engine(
    settings: Settings | None = None, *, driver: str = "asyncpg", **overrides: Any
) -> AsyncEngine:
    """The process-wide async engine. Create it inside the running event loop."""
    s = settings or get_settings()
    if driver == "asyncpg":
        server_settings = {"application_name": s.app_name}
        if s.statement_timeout_ms:
            server_settings["statement_timeout"] = str(s.statement_timeout_ms)
        connect_args: dict[str, Any] = {"server_settings": server_settings, "timeout": 5}
    elif driver == "psycopg":
        connect_args = {"application_name": s.app_name, "connect_timeout": 5}
        if s.statement_timeout_ms:
            connect_args["options"] = f"-c statement_timeout={s.statement_timeout_ms}"
    else:
        raise ValueError(f"unknown async driver {driver!r}")
    params: dict[str, Any] = {"connect_args": connect_args, **_pool_args(s)}
    params.update(overrides)
    if params.get("poolclass") is NullPool:
        for k in ("pool_size", "max_overflow", "pool_timeout"):
            params.pop(k, None)
    return _create_async_engine(sa_url(s.dsn, driver), **params)
