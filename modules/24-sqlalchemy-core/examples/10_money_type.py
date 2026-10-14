"""Module 24, section 11: a TypeDecorator that turns numeric(12,2) into a Money value object."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

import sqlalchemy as sa

from bazaar.sa.engine import create_engine
from bazaar.sa.tables import products

CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str = "EUR"

    def __post_init__(self) -> None:
        q = Decimal(self.amount).quantize(CENT, rounding=ROUND_HALF_EVEN)
        object.__setattr__(self, "amount", q)

    def __mul__(self, n: int) -> Money:
        return Money(self.amount * n, self.currency)

    def __str__(self) -> str:
        return f"{self.amount:,.2f} {self.currency}"


class MoneyType(sa.TypeDecorator):
    """numeric(12,2) in the database, Money in Python. The currency is not stored
    (Bazaar is single-currency); a real multi-currency model needs a second column."""

    impl = sa.Numeric(12, 2)
    cache_ok = True  # instances have no state that changes the SQL

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, Money):
            return value.amount
        if isinstance(value, float):
            raise TypeError("refusing float for money; use Decimal or Money")
        return Decimal(value)

    def process_result_value(self, value, dialect):
        return None if value is None else Money(value)


# A lightweight table object over the same products table, with the custom type.
priced = sa.table("products", sa.column("id", sa.BigInteger), sa.column("price", MoneyType()))

engine = create_engine()
with engine.connect() as conn:
    price = conn.execute(sa.select(priced.c.price).where(priced.c.id == 20)).scalar_one()
    print(repr(price), "->", price, "| x3 =", price * 3)
    # binding: the comparison value goes through process_bind_param too
    n = conn.execute(
        sa.select(sa.func.count()).select_from(priced).where(priced.c.price >= Money(Decimal(2000)))
    ).scalar_one()
    print("products costing at least 2,000.00 EUR:", n)
    try:
        conn.execute(sa.select(priced.c.id).where(priced.c.price == 19.99)).all()
    except sa.exc.StatementError as e:
        print("float rejected:", e.orig)
    # The ordinary Core table still returns Decimal: types are per column object.
    print(repr(conn.execute(sa.select(products.c.price).where(products.c.id == 20)).scalar_one()))
