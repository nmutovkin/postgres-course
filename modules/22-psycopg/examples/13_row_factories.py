"""Module 22, section 6: row factories."""

import os
from dataclasses import dataclass
from decimal import Decimal

import psycopg
from psycopg.rows import class_row, dict_row, namedtuple_row
from pydantic import BaseModel, Field  # optional dependency: pip install pydantic

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
Q = "SELECT id, shop_name, country_code FROM sellers WHERE id = %s"


@dataclass
class Seller:
    id: int
    shop_name: str
    country_code: str


class SellerIn(BaseModel):
    id: int
    shop_name: str = Field(min_length=3)
    country_code: str = Field(pattern=r"^[A-Z]{2}$")


with psycopg.connect(DSN) as conn:
    print("default:   ", conn.execute(Q, (1,)).fetchone())
    with conn.cursor(row_factory=dict_row) as cur:
        print("dict_row:  ", cur.execute(Q, (1,)).fetchone())
    with conn.cursor(row_factory=namedtuple_row) as cur:
        r = cur.execute(Q, (1,)).fetchone()
        print("namedtuple:", r, "->", r.shop_name)
    with conn.cursor(row_factory=class_row(Seller)) as cur:
        print("dataclass: ", cur.execute(Q, (1,)).fetchone())
    with conn.cursor(row_factory=class_row(SellerIn)) as cur:
        print("pydantic:  ", cur.execute(Q, (1,)).fetchone())
        try:
            cur.execute("SELECT 7 AS id, 'Ok' AS shop_name, 'de' AS country_code").fetchone()
        except Exception as e:  # pydantic.ValidationError
            print("pydantic rejects:", str(e).splitlines()[0], "...")
    try:
        with conn.cursor(row_factory=class_row(Seller)) as cur:
            cur.execute("SELECT id, shop_name FROM sellers WHERE id = 1").fetchone()
    except TypeError as e:
        print("dataclass, missing column:", e)
    # A connection-wide default; cursors inherit it.
    conn.row_factory = dict_row
    total = conn.execute("SELECT sum(total) AS revenue FROM orders WHERE seller_id = 1"
                         " AND status IN ('paid', 'shipped', 'delivered')").fetchone()  # fmt: skip
    print("connection default:", total, isinstance(total["revenue"], Decimal))
