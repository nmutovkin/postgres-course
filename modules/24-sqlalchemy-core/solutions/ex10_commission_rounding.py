"""Exercise 10: a 12.5 % commission per order, computed in SQL and in Python, to the cent."""

from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

import sqlalchemy as sa

from bazaar.sa.engine import create_engine
from bazaar.sa.tables import orders

RATE = Decimal("0.125")
CENT = Decimal("0.01")
o = orders


class Cents(sa.TypeDecorator):
    """numeric(12,2) that refuses floats on the way in and checks scale on the way out."""

    impl = sa.Numeric(12, 2)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if isinstance(value, float):
            raise TypeError("float is not money")
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value != value.quantize(CENT):
            raise ValueError(f"{value} has more than 2 decimals")
        return value


commission_sql = sa.type_coerce(sa.func.round(o.c.total * RATE, 2), Cents).label("commission")
stmt = (
    sa.select(o.c.id, o.c.seller_id, o.c.total, commission_sql)
    .where(
        o.c.status.in_(["paid", "shipped", "delivered"]),
        o.c.created_at >= datetime(2025, 11, 1, tzinfo=UTC),
        o.c.created_at < datetime(2025, 12, 1, tzinfo=UTC),
    )
    .order_by(o.c.id)
)

if __name__ == "__main__":
    engine = create_engine()
    with engine.connect() as conn:
        rows = conn.execute(stmt).all()

    def py(total: Decimal, rounding) -> Decimal:
        return (total * RATE).quantize(CENT, rounding=rounding)

    even = [r for r in rows if py(r.total, ROUND_HALF_EVEN) != r.commission]
    up = [r for r in rows if py(r.total, ROUND_HALF_UP) != r.commission]
    print(f"{len(rows):,} orders; SQL total commission {sum(r.commission for r in rows)}")
    print(f"Python ROUND_HALF_EVEN disagrees on {len(even):,} orders, e.g. {even[0].total}"
          f" -> SQL {even[0].commission}, Python {py(even[0].total, ROUND_HALF_EVEN)}")
    print(f"Python ROUND_HALF_UP disagrees on {len(up)} orders")
    print("sum of differences (HALF_EVEN):",
          sum(r.commission - py(r.total, ROUND_HALF_EVEN) for r in even))
