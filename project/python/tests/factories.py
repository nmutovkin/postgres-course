"""Tiny helpers that insert test rows. They work on any connection/transaction."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

import psycopg


def _tag() -> str:
    return uuid.uuid4().hex[:10]


def make_user(conn: psycopg.Connection, *, active: bool = True) -> int:
    t = _tag()
    row = conn.execute(
        "INSERT INTO users (email, full_name, is_active) VALUES (%s, %s, %s) RETURNING id",
        (f"test-{t}@example.com", f"Test User {t}", active),
    ).fetchone()
    assert row
    return row[0]


def make_address(conn: psycopg.Connection, user_id: int) -> int:
    row = conn.execute(
        """
        INSERT INTO addresses (user_id, recipient, line1, city, postal_code, country_code)
        VALUES (%s, 'Test Recipient', '1 Test Street', 'Testville', '12345', 'DE')
        RETURNING id
        """,
        (user_id,),
    ).fetchone()
    assert row
    return row[0]


def make_seller(conn: psycopg.Connection) -> int:
    owner = make_user(conn)
    row = conn.execute(
        "INSERT INTO sellers (user_id, shop_name, country_code) VALUES (%s, %s, 'DE') RETURNING id",
        (owner, f"Test Shop {_tag()}"),
    ).fetchone()
    assert row
    return row[0]


def make_product(
    conn: psycopg.Connection,
    seller_id: int,
    *,
    title: str | None = None,
    price: Decimal = Decimal("10.00"),
    stock: int = 10,
    status: str = "active",
    category_id: int = 3,
) -> int:
    row = conn.execute(
        """
        INSERT INTO products (seller_id, category_id, sku, title, price, status)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (seller_id, category_id, f"T-{_tag()}", title or f"Test product {_tag()}", price, status),
    ).fetchone()
    assert row
    conn.execute("INSERT INTO inventory (product_id, quantity) VALUES (%s, %s)", (row[0], stock))
    return row[0]


def cleanup(conn: psycopg.Connection, user_ids: list[int], seller_ids: list[int]) -> None:
    """Delete committed test rows (orders first, then products, sellers, users)."""
    with conn.transaction():
        orders = conn.execute(
            "DELETE FROM orders WHERE user_id = ANY(%s) OR seller_id = ANY(%s) RETURNING id",
            (user_ids, seller_ids),
        ).fetchall()
        if conn.execute("SELECT to_regclass('outbox')").fetchone()[0]:  # module 27+
            conn.execute(  # outbox events of those orders
                "DELETE FROM outbox WHERE aggregate_type = 'order' AND aggregate_id = ANY(%s)",
                ([str(o[0]) for o in orders],),
            )
        conn.execute("DELETE FROM products WHERE seller_id = ANY(%s)", (seller_ids,))
        owners = conn.execute(
            "DELETE FROM sellers WHERE id = ANY(%s) RETURNING user_id", (seller_ids,)
        ).fetchall()
        conn.execute(
            "DELETE FROM addresses WHERE user_id = ANY(%s)", (user_ids + [o[0] for o in owners],)
        )
        conn.execute("DELETE FROM users WHERE id = ANY(%s)", (user_ids + [o[0] for o in owners],))


def make_category(conn: psycopg.Connection) -> int:
    t = _tag()
    row = conn.execute(
        "INSERT INTO categories (name, slug) VALUES (%s, %s) RETURNING id",
        (f"Test category {t}", f"test-{t}"),
    ).fetchone()
    assert row
    return row[0]


@dataclass(frozen=True)
class Shop:
    """A buyer with an address, a seller, and products with stock: enough to check out."""

    buyer: int
    address: int
    seller: int
    products: list[int]


def make_shop(
    conn: psycopg.Connection,
    *,
    products: int = 2,
    stock: int = 10,
    price: Decimal = Decimal("10.00"),
) -> Shop:
    """Works on an EMPTY database (fresh_db) as well as on the reference one."""
    category = make_category(conn)
    buyer = make_user(conn)
    seller = make_seller(conn)
    pids = [
        make_product(conn, seller, stock=stock, price=price, category_id=category)
        for _ in range(products)
    ]
    return Shop(buyer, make_address(conn, buyer), seller, pids)
