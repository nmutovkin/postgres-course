"""Exercise 7: prove that cancelling an order releases its stock exactly once,
even when ten cancels of the same order arrive at the same moment.

    cd project/python && pytest -q -p no:cacheprovider ../../modules/27-python-production/solutions/ex07_test_cancel.py

Two implementations are tested with the same test:
* naive_cancel(): Python, read the status, then release and update (no lock);
* cancel_order(): Module 10's SQL function (locks the order row first).
The test must catch the first and pass the second.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import psycopg
import pytest

from bazaar import migrate
from bazaar.config import get_settings
from bazaar.testing import TemplateDatabase, migrations_fingerprint

CANCEL_SQL = (
    Path(__file__).resolve().parents[2]
    / "10-transactions-concurrency"
    / "solutions"
    / "cancel_order.sql"
)
N = 10


@pytest.fixture(scope="session")
def template() -> TemplateDatabase:
    def build(dsn: str) -> None:
        migrate.upgrade(dsn)
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute("SET ROLE bazaar_owner")
            c.execute(CANCEL_SQL.read_text().encode())

    fp = migrations_fingerprint(migrate.PROJECT_DIR / "migrations" / "versions")
    tpl = TemplateDatabase(
        get_settings().migration_dsn, "bazaar_ex07_template", build, fingerprint=fp + "-cancel"
    )
    tpl.ensure()
    return tpl


@pytest.fixture
def shop(template: TemplateDatabase) -> Iterator[tuple[str, int, int]]:
    """A fresh database: one product with 10 units, two pending orders (2 and 6 units).
    The order to cancel is the first; the other keeps 6 units reserved."""
    with template.clone(f"bazaar_ex07_{uuid.uuid4().hex[:8]}") as dsn:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute("INSERT INTO users (email, full_name) VALUES ('b@example.com', 'B')")
            c.execute(
                "INSERT INTO sellers (user_id, shop_name, country_code) VALUES (1, 'S', 'DE')"
            )
            c.execute("INSERT INTO categories (name, slug) VALUES ('C', 'c')")
            c.execute(
                "INSERT INTO products (seller_id, category_id, sku, title, price, status)"
                " VALUES (1, 1, 'x', 'T', 5, 'active')"
            )
            c.execute("INSERT INTO inventory (product_id, quantity) VALUES (1, 10)")
            [order_id] = c.execute(
                """SELECT place_order(1, '[{"product_id": 1, "quantity": 2}]', NULL)"""
            ).fetchone()
            c.execute("""SELECT place_order(1, '[{"product_id": 1, "quantity": 6}]', NULL)""")
        yield dsn, order_id, 1


def naive_cancel(conn: psycopg.Connection, order_id: int, barrier: threading.Barrier) -> bool:
    status = conn.execute("SELECT status FROM orders WHERE id = %s", (order_id,)).fetchone()[0]
    barrier.wait()  # every caller has read 'pending'
    if status == "cancelled":
        return False
    conn.execute(
        "UPDATE inventory i SET reserved = i.reserved - oi.quantity FROM order_items oi"
        " WHERE oi.order_id = %s AND i.product_id = oi.product_id",
        (order_id,),
    )
    conn.execute("UPDATE orders SET status = 'cancelled' WHERE id = %s", (order_id,))
    return True


def sql_cancel(conn: psycopg.Connection, order_id: int, barrier: threading.Barrier) -> bool:
    barrier.wait()  # every caller is about to call the function
    return conn.execute("SELECT cancel_order(%s)", (order_id,)).fetchone()[0]


def cancel_concurrently(
    dsn: str, order_id: int, impl: Callable[[psycopg.Connection, int, threading.Barrier], bool]
) -> list:
    barrier = threading.Barrier(N)
    results: list = []

    def one() -> None:
        with psycopg.connect(dsn) as c:
            try:
                results.append(impl(c, order_id, barrier))
                c.commit()
            except psycopg.Error as e:
                results.append(type(e).__name__)

    threads = [threading.Thread(target=one) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


@pytest.mark.parametrize("impl", [naive_cancel, sql_cancel], ids=["naive", "cancel_order"])
def test_ten_concurrent_cancels_release_stock_once(shop, impl) -> None:  # noqa: ANN001
    dsn, order_id, product_id = shop
    results = cancel_concurrently(dsn, order_id, impl)
    with psycopg.connect(dsn) as c:
        reserved = c.execute(
            "SELECT reserved FROM inventory WHERE product_id = %s", (product_id,)
        ).fetchone()[0]
    print(f"{impl.__name__}: results {sorted(map(str, results))}, reserved afterwards {reserved}")
    if impl is naive_cancel:
        # The test must be able to see the bug: the 2 units were released several
        # times, eating into the other order's reservation of 6 (until the CHECK
        # reserved >= 0 stopped further releases).
        assert results.count(True) > 1 and reserved < 6
    else:
        assert results.count(True) == 1 and results.count(False) == N - 1
        assert reserved == 6
