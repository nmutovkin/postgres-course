"""Exercise 11: an order with its address and items, in one query, as nested objects."""

import os
from dataclasses import astuple, dataclass
from datetime import datetime
from decimal import Decimal

import psycopg
from psycopg.rows import class_row
from psycopg.types.composite import CompositeInfo, register_composite

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")


@dataclass
class Address:  # field order = column order of the addresses table
    id: int
    user_id: int
    label: str | None
    recipient: str
    line1: str
    line2: str | None
    city: str
    postal_code: str
    country_code: str
    is_default: bool
    created_at: datetime


@dataclass
class Item:
    order_id: int
    product_id: int
    quantity: int
    unit_price: Decimal


@dataclass
class OrderDetail:
    id: int
    status: str
    total: Decimal
    address: Address | None
    items: list[Item]


ORDER_DETAIL = """
    SELECT o.id, o.status, o.total,
           a AS address,                                   -- the whole row, as a composite
           array_agg(oi ORDER BY oi.product_id) AS items   -- an array of composites
    FROM orders o
    LEFT JOIN addresses a ON a.id = o.shipping_address_id
    JOIN order_items oi ON oi.order_id = o.id
    WHERE o.id = %s
    GROUP BY o.id, a.*
"""


def register(conn: psycopg.Connection) -> None:
    for name, cls in (("addresses", Address), ("order_items", Item)):
        info = CompositeInfo.fetch(conn, name)
        # factory receives the fields positionally, in table column order
        # make_sequence turns our objects back into field tuples, so they can be dumped too
        register_composite(info, conn, factory=cls, make_sequence=lambda obj, _info: astuple(obj))


def order_detail(conn: psycopg.Connection, order_id: int) -> OrderDetail | None:
    with conn.cursor(row_factory=class_row(OrderDetail)) as cur:
        return cur.execute(ORDER_DETAIL, (order_id,)).fetchone()


if __name__ == "__main__":
    with psycopg.connect(DSN) as conn:
        register(conn)
        d = order_detail(conn, 500000)
        assert d is not None
        print(d.id, d.status, d.total)
        print(d.address)
        for item in d.items:
            print("  ", item)
        assert d.total == sum(i.quantity * i.unit_price for i in d.items)
