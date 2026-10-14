"""Exercise 5: 20 tests that each need a database of their own, two ways.

    cd project/python
    EX05_MODE=migrate  pytest -q -p no:cacheprovider ../../modules/27-python-production/solutions/ex05_test_template_speed.py
    EX05_MODE=template pytest -q -p no:cacheprovider ../../modules/27-python-production/solutions/ex05_test_template_speed.py
    (add -n 4 for pytest-xdist)

migrate:  CREATE DATABASE + `alembic upgrade head` per test
template: CREATE DATABASE ... TEMPLATE <built once per run> per test
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest

from bazaar import migrate
from bazaar.config import get_settings
from bazaar.testing import TemplateDatabase, migrations_fingerprint, scratch_database

MODE = os.environ.get("EX05_MODE", "template")
WORKER = os.environ.get("PYTEST_XDIST_WORKER", "gw")
ADMIN = get_settings().migration_dsn


@pytest.fixture(scope="session")
def template() -> TemplateDatabase:
    tpl = TemplateDatabase(
        ADMIN,
        "bazaar_ex05_template",
        build=lambda dsn: migrate.upgrade(dsn),
        fingerprint=migrations_fingerprint(migrate.PROJECT_DIR / "migrations" / "versions"),
    )
    tpl.ensure()
    return tpl


@pytest.fixture
def db(request: pytest.FixtureRequest) -> Iterator[str]:
    name = f"bazaar_ex05_{WORKER}_{uuid.uuid4().hex[:8]}"
    if MODE == "migrate":
        with scratch_database(ADMIN, name) as dsn:
            migrate.upgrade(dsn)
            yield dsn
    else:
        with request.getfixturevalue("template").clone(name) as dsn:
            yield dsn


@pytest.mark.parametrize("n", range(20))
def test_checkout_in_own_database(db: str, n: int) -> None:
    with psycopg.connect(db, autocommit=True) as c:
        c.execute("INSERT INTO users (email, full_name) VALUES (%s, 'T')", (f"t{n}@example.com",))
        c.execute("INSERT INTO sellers (user_id, shop_name, country_code) VALUES (1, 'S', 'DE')")
        c.execute("INSERT INTO categories (name, slug) VALUES ('C', 'c')")
        c.execute(
            "INSERT INTO products (seller_id, category_id, sku, title, price, status)"
            " VALUES (1, 1, 'x', 'T', 5, 'active')"
        )
        c.execute("INSERT INTO inventory (product_id, quantity) VALUES (1, 3)")
        [order_id] = c.execute(
            """SELECT place_order(1, '[{"product_id": 1, "quantity": 2}]', NULL)"""
        ).fetchone()
        assert c.execute("SELECT total FROM orders WHERE id = %s", (order_id,)).fetchone()[0] == 10
