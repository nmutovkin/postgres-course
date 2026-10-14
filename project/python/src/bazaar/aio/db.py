"""Async connections and pools, for both drivers.

psycopg (same API as bazaar.db, with async/await):

    conn = await connect()
    pool = await create_pool()              # AsyncConnectionPool, opened
    async with pool.connection() as conn:   # commit on success, rollback on error
        ...

asyncpg (its own API: $1 parameters, Record rows, prepared-statement cache):

    pool = await create_asyncpg_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT ... WHERE id = $1", 42)

One pool per process, created at startup and closed at shutdown. Never share a
single connection between concurrently running tasks.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

import asyncpg
import psycopg
from psycopg.types.enum import EnumInfo, register_enum
from psycopg_pool import AsyncConnectionPool

from bazaar.config import Settings, get_settings
from bazaar.db import _ENUMS, connect_kwargs

# --- psycopg async -------------------------------------------------------------


async def configure(conn: psycopg.AsyncConnection) -> None:
    """Async twin of bazaar.db.configure: register the enum adapters."""
    for name, py_enum in _ENUMS.items():
        info = await EnumInfo.fetch(conn, name)
        if info is None:
            continue
        register_enum(info, conn, py_enum, mapping={m: m.value for m in py_enum})
    if not conn.autocommit:
        await conn.commit()


async def connect(settings: Settings | None = None, **overrides: Any) -> psycopg.AsyncConnection:
    conn = await psycopg.AsyncConnection.connect(**{**connect_kwargs(settings), **overrides})
    await configure(conn)
    return conn


async def create_pool(
    settings: Settings | None = None, *, open: bool = True, **overrides: Any
) -> AsyncConnectionPool:
    s = settings or get_settings()
    kw = connect_kwargs(s)
    conninfo = kw.pop("conninfo")
    pool = AsyncConnectionPool(
        conninfo,
        kwargs=kw,
        min_size=s.pool_min_size,
        max_size=s.pool_max_size,
        configure=configure,
        check=AsyncConnectionPool.check_connection,
        name="bazaar-async",
        open=False,
        **overrides,
    )
    if open:
        await pool.open(wait=True, timeout=10)
    return pool


# --- asyncpg -------------------------------------------------------------------


def asyncpg_dsn(dsn: str) -> str:
    """asyncpg understands postgresql:// URIs but not libpq key=value strings."""
    if urlsplit(dsn).scheme in ("postgresql", "postgres"):
        return dsn
    raise ValueError("asyncpg needs a postgresql:// URI; got a key=value conninfo string")


async def init_asyncpg_connection(conn: asyncpg.Connection) -> None:
    """Run once per new asyncpg connection (the pool's `init` hook).

    By default asyncpg returns json/jsonb as *strings*. Decode them to Python
    objects in both directions. (format='text': the codec sees JSON text.)
    """
    for typename in ("json", "jsonb"):
        await conn.set_type_codec(
            typename,
            schema="pg_catalog",
            encoder=json.dumps,
            decoder=json.loads,
            format="text",
        )


def asyncpg_server_settings(s: Settings) -> dict[str, str]:
    settings = {"application_name": s.app_name}
    if s.statement_timeout_ms:
        settings["statement_timeout"] = str(s.statement_timeout_ms)
    return settings


async def asyncpg_connect(settings: Settings | None = None, **overrides: Any) -> asyncpg.Connection:
    s = settings or get_settings()
    conn = await asyncpg.connect(
        asyncpg_dsn(s.dsn),
        server_settings=asyncpg_server_settings(s),
        timeout=5,
        **overrides,
    )
    await init_asyncpg_connection(conn)
    return conn


async def create_asyncpg_pool(settings: Settings | None = None, **overrides: Any) -> asyncpg.Pool:
    s = settings or get_settings()
    kwargs: dict[str, Any] = {
        "min_size": s.pool_min_size,
        "max_size": s.pool_max_size,
        "server_settings": asyncpg_server_settings(s),
        "init": init_asyncpg_connection,
        "timeout": 5,
    }
    kwargs.update(overrides)
    return await asyncpg.create_pool(asyncpg_dsn(s.dsn), **kwargs)
