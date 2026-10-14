"""Exercise 8: old and new code during the rename, as bazaar_app.

cd project/python && python ../../modules/26-migrations/solutions/ex08_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg
from sqlalchemy.engine import make_url

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from scratch_alembic import scratch_db, scratch_migrations  # noqa: E402

OLD = (
    "INSERT INTO cart_items (user_id, product_id, quantity) VALUES (1, 1, 1)"
    " ON CONFLICT (user_id, product_id) DO UPDATE SET quantity = cart_items.quantity + 1"
)
NEW = (
    "INSERT INTO basket_items (user_id, product_id, quantity) VALUES (1, 1, 1)"
    " ON CONFLICT (user_id, product_id) DO UPDATE SET quantity = basket_items.quantity + 1"
)


def as_app(dsn: str) -> str:
    return (
        make_url(dsn)
        .set(username="bazaar_app", password="app")
        .render_as_string(hide_password=False)
    )


def run(dsn: str, label: str, sql: str) -> None:
    with psycopg.connect(as_app(dsn), autocommit=True) as c:
        try:
            c.execute(sql)
            n = c.execute("SELECT quantity FROM basket_items").fetchone()
            print(f"  {label}: ok, quantity now {n[0]}")
        except psycopg.Error as e:
            print(f"  {label}: {type(e).__name__}: {str(e).splitlines()[0]}")


def main() -> None:
    files = [HERE / "ex08_2605_rename_cart_expand.py", HERE / "ex08_2606_rename_cart_contract.py"]
    with scratch_migrations(*files) as mig, scratch_db() as dsn:
        mig.upgrade(dsn, "2701")
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute("INSERT INTO users (email, full_name) VALUES ('c@example.com', 'C')")
            c.execute(
                "INSERT INTO sellers (user_id, shop_name, country_code) VALUES (1, 'S', 'DE')"
            )
            c.execute("INSERT INTO categories (name, slug) VALUES ('C', 'c')")
            c.execute(
                "INSERT INTO products (seller_id, category_id, sku, title, price)"
                " VALUES (1, 1, 'x', 'T', 1)"
            )
        mig.upgrade(dsn, "2605")
        print("at 2605 (expand):")
        run(dsn, "old code (cart_items)  ", OLD)
        run(dsn, "new code (basket_items)", NEW)
        mig.upgrade(dsn, "2606")
        print("at 2606 (contract):")
        run(dsn, "old code (cart_items)  ", OLD)
        run(dsn, "new code (basket_items)", NEW)
        mig.downgrade(dsn, "2701")
        with psycopg.connect(dsn) as c:
            print(
                "after downgrade:",
                c.execute(
                    "SELECT relname FROM pg_class WHERE relname LIKE '%cart%' OR relname"
                    " LIKE 'basket%' ORDER BY 1"
                ).fetchall(),
            )


if __name__ == "__main__":
    main()
