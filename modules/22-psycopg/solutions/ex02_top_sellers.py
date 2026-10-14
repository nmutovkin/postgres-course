"""Exercise 2: the biggest shops, as dataclasses."""

import os
from dataclasses import dataclass
from decimal import Decimal

import psycopg
from psycopg.rows import class_row

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")


@dataclass(frozen=True, slots=True)
class ShopStats:
    seller_id: int
    shop_name: str
    country_code: str
    active_products: int
    avg_price: Decimal

    @property
    def label(self) -> str:
        return f"{self.shop_name} ({self.country_code})"


TOP_SHOPS = """
    SELECT s.id AS seller_id, s.shop_name, s.country_code,
           count(*)::int AS active_products,
           round(avg(p.price), 2) AS avg_price
    FROM sellers s
    JOIN products p ON p.seller_id = s.id
    WHERE p.status = 'active'
    GROUP BY s.id
    ORDER BY active_products DESC, s.id
    LIMIT %(n)s
"""


def top_shops(conn: psycopg.Connection, n: int = 5) -> list[ShopStats]:
    with conn.cursor(row_factory=class_row(ShopStats)) as cur:
        return cur.execute(TOP_SHOPS, {"n": n}).fetchall()


if __name__ == "__main__":
    with psycopg.connect(DSN) as conn:
        shops = top_shops(conn)
    for s in shops:
        print(f"{s.seller_id:>4} {s.label:28s} {s.active_products:>6,} {s.avg_price:>8}")
    assert isinstance(shops[0].avg_price, Decimal)
