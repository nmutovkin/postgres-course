"""Shared fixtures.

The tests need a Bazaar database at state 22 or later (`./project/bin/reset.sh 22`)
reachable through BAZAAR_DSN (default: localhost:5432, the course container).

Isolation strategy:
* `conn` / `aconn` wrap each test in a transaction that is ROLLED BACK at the
  end (`force_rollback=True`), so tests can insert whatever they need and leave
  nothing behind. Repository functions use `conn.transaction()` internally,
  which becomes a SAVEPOINT inside the test's transaction.
* Tests that need *committed* data (several connections, LISTEN/NOTIFY, the
  worker) use `committed` fixtures that create their own rows and delete them.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import psycopg
import pytest

from bazaar import db
from bazaar.aio import db as adb

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
