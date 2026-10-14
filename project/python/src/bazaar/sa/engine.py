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

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine
from sqlalchemy.pool import NullPool

from bazaar.config import Settings, get_settings

__all__ = [
    "NullPool",
    "count_statements",
    "create_async_engine",
    "create_engine",
    "create_read_engine",
    "dispose_after_fork",
    "sa_url",
]


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
        "pool_timeout": s.pool_timeout_s,  # wait for a free connection, then TimeoutError
        "pool_pre_ping": True,  # test a connection on checkout (one round trip)
        "pool_recycle": 1800,  # replace connections older than 30 minutes
    }


def create_engine(settings: Settings | None = None, **overrides: Any) -> Engine:
    """The process-wide sync engine (psycopg 3 driver)."""
    s = settings or get_settings()
    connect_args: dict[str, Any] = {"application_name": s.app_name, "connect_timeout": 5}
    if s.libpq_options():
        connect_args["options"] = s.libpq_options()
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
        connect_args: dict[str, Any] = {"server_settings": s.session_settings(), "timeout": 5}
    elif driver == "psycopg":
        connect_args = {"application_name": s.app_name, "connect_timeout": 5}
        if s.libpq_options():
            connect_args["options"] = s.libpq_options()
    else:
        raise ValueError(f"unknown async driver {driver!r}")
    params: dict[str, Any] = {"connect_args": connect_args, **_pool_args(s)}
    params.update(overrides)
    if params.get("poolclass") is NullPool:
        for k in ("pool_size", "max_overflow", "pool_timeout"):
            params.pop(k, None)
    return _create_async_engine(sa_url(s.dsn, driver), **params)


def create_read_engine(settings: Settings | None = None, **overrides: Any) -> Engine:
    """An engine for reads that may be slightly stale (module 19's "two pools").

    It connects to BAZAAR_REPLICA_DSN when set, else to the primary, and in
    both cases with default_transaction_read_only=on: code that accidentally
    writes through it fails at once (SQLSTATE 25006) instead of working in
    development (one database) and failing in production (a replica).
    Read-your-own-writes paths (show the order just placed) must use the
    primary engine: a replica may lag behind by some milliseconds or more.
    """
    s = settings or get_settings()
    s = replace(s, dsn=s.replica_dsn or s.dsn, app_name=f"{s.app_name}-ro")
    engine = create_engine(s, **overrides)

    @event.listens_for(engine, "connect")
    def _read_only(dbapi_conn, record) -> None:  # noqa: ANN001
        # A startup option would need a second `-c` in connect_args["options"]
        # (merged with the timeouts); a SET on connect is simpler and per engine.
        autocommit = dbapi_conn.autocommit
        dbapi_conn.autocommit = True
        dbapi_conn.execute("SET default_transaction_read_only = on")
        dbapi_conn.autocommit = autocommit

    return engine


def dispose_after_fork(engine: Engine | AsyncEngine) -> None:
    """Make a forked child start with an empty pool (gunicorn --preload, multiprocessing).

    A pooled connection is a socket; after fork() parent and child share it,
    and two processes talking on one PostgreSQL connection corrupt each other's
    protocol state. dispose(close=False) drops the child's references without
    closing the parent's sockets (closing would send a Terminate message on
    the parent's behalf). Better still: create engines after the fork (in the
    worker's startup hook), which is what the module 28 service does.
    """
    target = engine.sync_engine if isinstance(engine, AsyncEngine) else engine
    os.register_at_fork(after_in_child=lambda: target.dispose(close=False))


@contextmanager
def count_statements(engine: Engine | AsyncEngine) -> Iterator[list[str]]:
    """Record every SQL statement sent through `engine` inside the block.

        with count_statements(engine) as stmts:
            do_something(session)
        assert len(stmts) <= 3        # an N+1 guard

    It listens to before_cursor_execute, the last hook before the driver, so
    it sees exactly what goes to the server (lazy loads, autoflushes, ...).
    An executemany batch counts once.
    """
    target = engine.sync_engine if isinstance(engine, AsyncEngine) else engine
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        seen.append(statement)

    event.listen(target, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(target, "before_cursor_execute", record)
