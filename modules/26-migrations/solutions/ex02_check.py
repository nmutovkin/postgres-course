"""Exercise 2: run the naive and the fixed revision 2605 on scratch databases.

cd project/python && python ../../modules/26-migrations/solutions/ex02_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import sqlalchemy as sa

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from scratch_alembic import scratch_db, scratch_migrations  # noqa: E402

from bazaar import db  # noqa: E402
from bazaar.config import get_settings  # noqa: E402


def main() -> None:
    print("--- naive revision")
    with (
        scratch_migrations(HERE / "ex02_naive" / "2605_order_on_hold.py") as mig,
        scratch_db() as dsn,
    ):
        try:
            mig.upgrade(dsn)
        except sa.exc.DBAPIError as e:
            print(type(e.orig).__name__, "-", str(e.orig).splitlines()[0])
        with psycopg.connect(dsn) as c:
            print("alembic_version:", c.execute("TABLE alembic_version").fetchall())
            print("labels:", c.execute("SELECT enum_range(NULL::order_status)").fetchone()[0])

    print("--- fixed revision")
    with scratch_migrations(HERE / "ex02_2605_order_on_hold.py") as mig, scratch_db() as dsn:
        mig.upgrade(dsn)
        with psycopg.connect(dsn, autocommit=True) as c:
            print("alembic_version:", c.execute("TABLE alembic_version").fetchall())
            print("labels:", c.execute("SELECT enum_range(NULL::order_status)").fetchone()[0])
            print(
                "index:",
                c.execute(
                    "SELECT pg_get_indexdef('orders_on_hold_created_at_idx'::regclass)"
                ).fetchone()[0],
            )
            c.execute("INSERT INTO users (email, full_name) VALUES ('h@example.com', 'H')")
            c.execute(
                "INSERT INTO sellers (user_id, shop_name, country_code) VALUES (1, 'S', 'DE')"
            )
            c.execute("INSERT INTO orders (user_id, seller_id, status) VALUES (1, 1, 'on_hold')")
        # Old application code: bazaar.models.OrderStatus has no ON_HOLD.
        from dataclasses import replace

        settings = replace(get_settings(), dsn=dsn)
        with db.connect(settings) as c:
            try:
                c.execute("SELECT status FROM orders").fetchone()
            except Exception as e:  # noqa: BLE001
                print("old code reading the row:", type(e).__name__, "-", e)
        try:
            mig.downgrade(dsn, "2801")
        except NotImplementedError as e:
            print("downgrade:", e)


if __name__ == "__main__":
    main()
