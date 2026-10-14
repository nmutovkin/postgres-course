"""Synchronous connections and the connection pool (psycopg 3).

    from bazaar.db import connect, create_pool

    with connect() as conn:                 # one-off scripts
        ...

    pool = create_pool()                    # long-running processes: ONE pool per process
    with pool.connection() as conn:         # borrow; commit on success, rollback on error
        ...

Every connection made here goes through configure(), which registers the
Bazaar enum types so that order/product statuses load as Python enums.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.types.enum import EnumInfo, register_enum
from psycopg_pool import ConnectionPool

from bazaar.config import Settings, get_settings
from bazaar.models import OrderStatus, ProductStatus

_ENUMS = {
    "bazaar.order_status": OrderStatus,
    "bazaar.product_status": ProductStatus,
}


def connect_kwargs(settings: Settings | None = None) -> dict[str, Any]:
    """Connection parameters shared by sync and async psycopg connections."""
    s = settings or get_settings()
    kwargs: dict[str, Any] = {
        "conninfo": s.dsn,
        "application_name": s.app_name,
        "connect_timeout": 5,
    }
    if s.statement_timeout_ms:
        # Startup option: applies to the whole session, set once at connect time.
        kwargs["options"] = f"-c statement_timeout={s.statement_timeout_ms}"
    return kwargs


def configure(conn: psycopg.Connection) -> None:
    """Per-connection setup: register enum adapters.

    Used directly by connect() and as the pool's `configure` callback. A pool
    requires the connection to be idle when configure returns, so we commit
    the (read-only) transaction that EnumInfo.fetch() opened.
    """
    for name, py_enum in _ENUMS.items():
        info = EnumInfo.fetch(conn, name)
        if info is None:  # older reset state without enums: statuses stay str
            continue
        register_enum(info, conn, py_enum, mapping={m: m.value for m in py_enum})
    if not conn.autocommit:
        conn.commit()


def connect(settings: Settings | None = None, **overrides: Any) -> psycopg.Connection:
    """Open a configured connection. `overrides` are passed to psycopg.connect."""
    conn = psycopg.connect(**{**connect_kwargs(settings), **overrides})
    configure(conn)
    return conn


def create_pool(
    settings: Settings | None = None, *, open: bool = True, **overrides: Any
) -> ConnectionPool:
    """Create the process-wide connection pool."""
    s = settings or get_settings()
    kw = connect_kwargs(s)
    conninfo = kw.pop("conninfo")
    pool = ConnectionPool(
        conninfo,
        kwargs=kw,
        min_size=s.pool_min_size,
        max_size=s.pool_max_size,
        configure=configure,
        check=ConnectionPool.check_connection,  # cheap liveness check on checkout
        name="bazaar",
        open=False,
        **overrides,
    )
    if open:
        pool.open(wait=True, timeout=10)
    return pool


@contextmanager
def seller_scope(conn: psycopg.Connection, seller_id: int) -> Iterator[psycopg.Connection]:
    """Run a block as seller `seller_id` under the module-15 RLS policies.

    The setting is transaction-local (set_config(..., true) == SET LOCAL), so it
    disappears at COMMIT/ROLLBACK and can never leak to the next user of a
    pooled connection, or to another client behind PgBouncer.
    """
    with conn.transaction():
        conn.execute("SELECT set_config('app.seller_id', %s, true)", (str(seller_id),))
        yield conn
