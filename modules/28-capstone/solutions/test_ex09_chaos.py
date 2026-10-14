"""Exercise 9: chaos tests. Kill the database connection in the middle of a request.

pg_terminate_backend() is what a failover, a pooler restart or an over-eager
DBA does to a connection. The service must:
* retry a checkout whose connection died mid-transaction (it carries an
  idempotency key, so the retry is safe), and order exactly once;
* answer a read whose connection died with 503 + Retry-After, not 500;
* not keep the dead connection: the next request works.

Run (from project/python, the database at head):
    python -m pytest -c pyproject.toml ../../modules/28-capstone/solutions/test_ex09_chaos.py
"""

from __future__ import annotations

import psycopg
import pytest
from sqlalchemy import event

from tests.factories import make_shop
from tests.test_api import fill_cart, key, running, settings_for, stock, user

pytestmark = pytest.mark.migrated


def kill_on(engine, admin_dsn: str, marker: str, times: int = 1) -> list[int]:  # noqa: ANN001
    """After the first `times` statements containing `marker`, terminate the backend
    that ran them. Returns the list of killed pids (filled as it happens)."""
    killed: list[int] = []

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def _kill(conn, cursor, statement, params, context, executemany) -> None:  # noqa: ANN001
        if marker in statement and len(killed) < times:
            pid = conn.connection.dbapi_connection.driver_connection.get_server_pid()
            with psycopg.connect(admin_dsn, autocommit=True) as admin:
                admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
            killed.append(pid)

    return killed


async def test_checkout_survives_a_killed_connection(fresh_db: str) -> None:
    with psycopg.connect(fresh_db, autocommit=True) as c:
        shop = make_shop(c, products=1, stock=5)
    pid = shop.products[0]
    app_settings = settings_for(fresh_db)
    async with running(app_settings) as client:
        db = client._transport.app.state.db  # noqa: SLF001 - test-only access
        # Die right after the stock was reserved, before the outbox INSERT and COMMIT:
        killed = kill_on(db.rw, fresh_db, "UPDATE orders SET idempotency_key")
        await fill_cart(client, shop.buyer, [(pid, 2)])
        r = await client.post("/checkout", headers={**user(shop.buyer), **key()})
        assert killed, "the hook never fired"
        assert r.status_code == 201, r.text  # the retry succeeded
        with psycopg.connect(fresh_db, autocommit=True) as c:
            assert stock(c, pid) == (5, 2)  # reserved once: the first attempt rolled back
            n = c.execute("SELECT count(*) FROM orders WHERE user_id = %s", (shop.buyer,))
            assert n.fetchone() == (1,)
            events = c.execute("SELECT count(*) FROM outbox").fetchone()
            assert events == (1,)


async def test_read_with_a_killed_connection_is_503_then_recovers(fresh_db: str) -> None:
    with psycopg.connect(fresh_db, autocommit=True) as c:
        shop = make_shop(c, products=1)
    async with running(settings_for(fresh_db)) as client:
        db = client._transport.app.state.db  # noqa: SLF001
        killed = kill_on(db.read, fresh_db, "FROM products p")
        r = await client.get(f"/products/{shop.products[0]}")
        # The SELECT itself completed; the connection died after it. The next
        # statement of the request (the breadcrumb) fails: 503, no retry for reads.
        assert killed
        assert r.status_code == 503 and r.headers["retry-after"] == "1"
        assert r.json()["type"].endswith("/database-unavailable")
        r = await client.get(f"/products/{shop.products[0]}")
        assert r.status_code == 200  # a fresh connection; the dead one was discarded
