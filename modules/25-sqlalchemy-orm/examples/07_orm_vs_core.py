"""Module 25, section 16: what the ORM costs for read-heavy work, and when to drop to Core.

Task: revenue per month of seller 1 in 2025 (sales statuses), four ways.
"""

import time
import tracemalloc
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session, load_only

from bazaar.models import OrderStatus
from bazaar.orm.models import Order
from bazaar.sa.engine import create_engine

engine = create_engine()
SALE = [OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DELIVERED]
WHERE = (
    Order.seller_id == 1,
    Order.status.in_(SALE),
    Order.created_at >= datetime(2025, 1, 1, tzinfo=UTC),
    Order.created_at < datetime(2026, 1, 1, tzinfo=UTC),
)


def by_month(pairs) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(Decimal)
    for created_at, total in pairs:
        out[created_at.strftime("%Y-%m")] += total
    return dict(out)


def orm_objects(session):
    orders = session.scalars(sa.select(Order).where(*WHERE)).all()
    return by_month((o.created_at, o.total) for o in orders), len(orders)


def orm_load_only(session):
    orders = session.scalars(
        sa.select(Order).where(*WHERE).options(load_only(Order.created_at, Order.total))
    ).all()
    return by_month((o.created_at, o.total) for o in orders), len(orders)


def orm_columns(session):  # select columns, not entities: rows, no identity map
    rows = session.execute(sa.select(Order.created_at, Order.total).where(*WHERE)).all()
    return by_month(rows), len(rows)


def sql_aggregate(session):  # let the database aggregate
    month = sa.func.to_char(sa.func.timezone("UTC", Order.created_at), "YYYY-MM")
    rows = session.execute(
        sa.select(month, sa.func.sum(Order.total)).where(*WHERE).group_by(month)
    ).all()
    return dict(rows), len(rows)


reference = None
for label, fn in (
    ("ORM entities (select(Order))", orm_objects),
    ("ORM entities, load_only(2 columns)", orm_load_only),
    ("ORM columns (select(Order.a, Order.b))", orm_columns),
    ("GROUP BY in SQL", sql_aggregate),
):
    best = None
    for _ in range(3):  # time without tracemalloc (it slows Python down a lot)
        with Session(engine) as session:
            session.connection()
            t = time.perf_counter()
            result, n = fn(session)
            elapsed = time.perf_counter() - t
            best = min(best or elapsed, elapsed)
    with Session(engine) as session:  # then one run for peak Python memory
        session.connection()
        tracemalloc.start()
        fn(session)
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    reference = reference or result
    assert result == reference
    print(f"{label:<40} {n:>6,} rows  {best * 1000:7.0f} ms  peak {peak / 1e6:5.1f} MB")
print("December 2025:", reference["2025-12"])
