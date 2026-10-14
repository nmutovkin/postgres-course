"""Exercise 4 (starting point): a seller dashboard endpoint with an N+1 problem.

Don't change what `seller_dashboard()` returns; make it cheaper. The fixed
version is ex04_dashboard_fixed.py.
"""

from __future__ import annotations

import time

import sqlalchemy as sa
from sqlalchemy.orm import Session

from bazaar.orm.models import Order
from bazaar.sa.engine import count_statements, create_engine


def seller_dashboard(session: Session, seller_id: int, limit: int = 50) -> list[dict]:
    orders = session.scalars(
        sa.select(Order)
        .where(Order.seller_id == seller_id)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(limit)
    ).all()
    out = []
    for o in orders:
        out.append(
            {
                "order": o.id,
                "status": str(o.status),
                "buyer": o.user.full_name,
                "city": o.shipping_address.city if o.shipping_address else None,
                "lines": [
                    f"{i.quantity} x {i.product.title} ({i.product.category.name})" for i in o.items
                ],
                "paid": sum(p.amount for p in o.payments if p.status == "succeeded"),
            }
        )
    return out


if __name__ == "__main__":
    engine = create_engine()
    with Session(engine) as session:
        session.connection()
        with count_statements(engine) as stmts:
            t = time.perf_counter()
            rows = seller_dashboard(session, 42)
            elapsed = time.perf_counter() - t
    print(f"{len(rows)} orders, {len(stmts)} statements, {elapsed * 1000:.0f} ms")
    print(rows[0])
