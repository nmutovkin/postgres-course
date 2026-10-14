"""Exercise 4 (solution): the same dashboard in 3 statements, guarded by raiseload."""

from __future__ import annotations

import time

import sqlalchemy as sa
from sqlalchemy.orm import Session, joinedload, raiseload, selectinload

from bazaar.models import PaymentStatus
from bazaar.orm.models import Order, OrderItem, Product
from bazaar.sa.engine import count_statements, create_engine

from ex04_dashboard_start import seller_dashboard as seller_dashboard_slow


def seller_dashboard(session: Session, seller_id: int, limit: int = 50) -> list[dict]:
    orders = session.scalars(
        sa.select(Order)
        .where(Order.seller_id == seller_id)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(limit)
        .options(
            # to-one: joined into the main query (one row per order either way)
            joinedload(Order.user),
            joinedload(Order.shipping_address),
            # collections: one extra SELECT ... WHERE order_id IN (...) each,
            # with the to-one relations of the items joined into it
            selectinload(Order.items)
            .joinedload(OrderItem.product)
            .joinedload(Product.category),
            selectinload(Order.payments),
            raiseload("*"),  # anything else is a bug, not a silent query
        )
    ).all()
    return [
        {
            "order": o.id,
            "status": str(o.status),
            "buyer": o.user.full_name,
            "city": o.shipping_address.city if o.shipping_address else None,
            "lines": [
                f"{i.quantity} x {i.product.title} ({i.product.category.name})" for i in o.items
            ],
            "paid": sum(p.amount for p in o.payments if p.status == PaymentStatus.SUCCEEDED),
        }
        for o in orders
    ]


if __name__ == "__main__":
    engine = create_engine()
    results = {}
    for label, fn in (("before", seller_dashboard_slow), ("after", seller_dashboard)):
        best = None
        for _ in range(3):
            with Session(engine) as session:
                session.connection()
                with count_statements(engine) as stmts:
                    t = time.perf_counter()
                    rows = fn(session, 42)
                    elapsed = time.perf_counter() - t
            best = min(best or elapsed, elapsed)
        results[label] = rows
        print(f"{label:<7} {len(rows)} orders, {len(stmts):>3} statements, {best * 1000:5.0f} ms")
        for s in stmts if label == "after" else []:
            print("        ", " ".join(s.split())[:120], "...")
    print("identical output:", results["before"] == results["after"])
    print(results["after"][0])
