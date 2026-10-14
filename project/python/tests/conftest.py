"""Shared fixtures.

The tests need the Bazaar database (`./project/bin/reset.sh 26`) brought to the
newest Alembic revision (`alembic stamp 2600 && alembic upgrade head`, module 26),
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

import psycopg
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import Session

from bazaar import db, migrate
from bazaar.aio import db as adb
from bazaar.config import get_settings
from bazaar.orm.session import BazaarSession
from bazaar.sa.engine import NullPool, create_async_engine, create_engine
from bazaar.testing import TemplateDatabase, migrations_fingerprint, scratch_database


def pytest_report_header(config: pytest.Config) -> str:
    return f"BAZAAR_DSN={os.environ.get('BAZAAR_DSN', '(default) localhost:5432')}"


@pytest.fixture(scope="session", autouse=True)
def schema_at_head() -> None:
    """The database must be at the newest Alembic revision (module 26).

    Until module 25 this fixture applied project/python/sql/*.sql. Now the
    schema belongs to the migrations, and the tests refuse to run against a
    database whose schema is older (or newer) than the code:
        alembic stamp 2600 && alembic upgrade head      # once, after reset.sh
    """
    engine = create_engine(poolclass=NullPool)
    try:
        with engine.connect() as c:
            migrate.check_schema(c)
    except migrate.SchemaOutOfDate as e:
        pytest.exit(f"schema not at head: {e}", returncode=2)
    finally:
        engine.dispose()


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


# --- scratch and template databases (modules 26-27) ---------------------------------------
#
# Tests that need a database of their own (migrations, committed data at scale)
# get a copy of a template that `alembic upgrade head` built once. Names carry
# the pytest-xdist worker id, so parallel workers never share a scratch database.

WORKER = os.environ.get("PYTEST_XDIST_WORKER", "gw")  # "gw0", "gw1", ... under -n


@pytest.fixture(scope="session")
def admin_dsn() -> str:
    """A DSN of a role that may CREATE DATABASE (the migration DSN)."""
    return get_settings().migration_dsn


@pytest.fixture(scope="session")
def head_template(admin_dsn: str) -> TemplateDatabase:
    """An empty Bazaar schema at head, rebuilt only when a revision file changes."""
    tpl = TemplateDatabase(
        admin_dsn,
        "bazaar_test_template",
        build=lambda dsn: migrate.upgrade(dsn),
        fingerprint=migrations_fingerprint(migrate.PROJECT_DIR / "migrations" / "versions"),
    )
    tpl.ensure()
    return tpl


@pytest.fixture
def fresh_db(head_template: TemplateDatabase, request: pytest.FixtureRequest) -> Iterator[str]:
    """DSN of a brand-new database at head (empty tables), dropped after the test."""
    name = f"bazaar_test_{WORKER}_{abs(hash(request.node.nodeid)) % 10**8}"
    with head_template.clone(name) as dsn:
        yield dsn


@pytest.fixture
def empty_db(admin_dsn: str, request: pytest.FixtureRequest) -> Iterator[str]:
    """DSN of an empty database (no schema at all), dropped after the test."""
    name = f"bazaar_empty_{WORKER}_{abs(hash(request.node.nodeid)) % 10**8}"
    with scratch_database(admin_dsn, name) as dsn:
        yield dsn
