"""Shared fixtures.

The tests need a Bazaar database at state 22 or later (`./project/bin/reset.sh 22`)
reachable through BAZAAR_DSN (default: localhost:5432, the course container).

Isolation strategy:
* `conn` / `aconn` wrap each test in a transaction that is ROLLED BACK at the
  end (`force_rollback=True`), so tests can insert whatever they need and leave
  nothing behind. Repository functions use `conn.transaction()` internally,
  which becomes a SAVEPOINT inside the test's transaction.
* SQLAlchemy (modules 24-25): `sa_conn` is a Core Connection inside a
  transaction that is rolled back. `session` is an ORM Session bound to such a
  connection with join_transaction_mode="create_savepoint": whatever the code
  under test does (flush, begin_nested, even commit) stays inside the outer
  transaction, which the fixture rolls back.
* Tests that need *committed* data (several connections, LISTEN/NOTIFY, the
  worker) use `committed` fixtures that create their own rows and delete them.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import psycopg
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import Session

from bazaar import db
from bazaar.aio import db as adb
from bazaar.orm.session import BazaarSession
from bazaar.sa.engine import create_async_engine, create_engine

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def pytest_report_header(config: pytest.Config) -> str:
    return f"BAZAAR_DSN={os.environ.get('BAZAAR_DSN', '(default) localhost:5432')}"


@pytest.fixture(scope="session", autouse=True)
def schema_extras() -> None:
    """Apply project/python/sql/*.sql (idempotent) once per test session."""
    with db.connect(autocommit=True) as conn:
        for f in sorted(SQL_DIR.glob("*.sql")):
            conn.execute(f.read_text().encode())


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    with db.connect(autocommit=True) as c, c.transaction(force_rollback=True):
        yield c


@pytest.fixture
async def aconn() -> AsyncIterator[psycopg.AsyncConnection]:
    c = await adb.connect(autocommit=True)
    try:
        async with c.transaction(force_rollback=True):
            yield c
    finally:
        await c.close()


# --- SQLAlchemy ----------------------------------------------------------------------


@pytest.fixture(scope="session")
def sa_engine() -> Iterator[sa.Engine]:
    engine = create_engine(pool_size=3)
    yield engine
    engine.dispose()


@pytest.fixture
def sa_conn(sa_engine: sa.Engine) -> Iterator[sa.Connection]:
    with sa_engine.connect() as c:
        c.begin()
        yield c
        c.rollback()


@pytest.fixture
def session(sa_conn: sa.Connection) -> Iterator[Session]:
    with BazaarSession(
        bind=sa_conn, join_transaction_mode="create_savepoint", expire_on_commit=False
    ) as s:
        yield s


@pytest.fixture
async def async_engine() -> AsyncIterator[AsyncEngine]:
    # Function-scoped: an async engine's connections belong to one event loop.
    engine = create_async_engine(pool_size=2)
    yield engine
    await engine.dispose()


@pytest.fixture
async def asession(async_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with async_engine.connect() as c:
        await c.begin()
        s = AsyncSession(
            bind=c,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
            sync_session_class=BazaarSession,
        )
        try:
            yield s
        finally:
            await s.close()
            await c.rollback()
