"""Exercise 2: the shops with the most distinct buyers in 2025, and how many came back."""

from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa

from bazaar.sa.engine import create_engine
from bazaar.sa.tables import orders, sellers

o, s = orders, sellers
Y2025 = (datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))

# Orders per (seller, buyer) first, then count buyers and repeat buyers per seller.
per_buyer = (
    sa.select(o.c.seller_id, o.c.user_id, sa.func.count().label("n"))
    .where(
        o.c.created_at >= Y2025[0],
        o.c.created_at < Y2025[1],
        o.c.status.in_(["paid", "shipped", "delivered"]),
    )
    .group_by(o.c.seller_id, o.c.user_id)
    .subquery("per_buyer")
)
buyers = sa.func.count().label("buyers")
repeat = sa.func.count().filter(per_buyer.c.n >= 2).label("repeat_buyers")
stmt = (
    sa.select(
        s.c.id,
        s.c.shop_name,
        buyers,
        repeat,
        # Decimal, not 100.0: a Python float is bound as double precision, and
        # round(double precision, integer) does not exist in PostgreSQL.
        sa.func.round(Decimal(100) * repeat / buyers, 1).label("repeat_pct"),
    )
    .join(per_buyer, per_buyer.c.seller_id == s.c.id)
    .group_by(s.c.id, s.c.shop_name)
    .order_by(buyers.desc(), s.c.id)
    .limit(5)
)

if __name__ == "__main__":
    engine = create_engine()
    print(stmt.compile(engine), "\n")
    with engine.connect() as conn:
        for row in conn.execute(stmt):
            print(f"{row.id:>3} {row.shop_name:<20} {row.buyers:>6,} {row.repeat_buyers:>6,}"
                  f" {row.repeat_pct:>5}%")
