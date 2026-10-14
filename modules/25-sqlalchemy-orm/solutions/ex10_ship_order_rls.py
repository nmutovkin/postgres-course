"""Exercise 10: a seller ships an order through the ORM, as the bazaar_seller role (rolled back)."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from bazaar.errors import NotFound
from bazaar.models import OrderStatus
from bazaar.orm.models import Order
from bazaar.orm.services import scoped_seller_id
from bazaar.orm.session import SELLER_KEY, BazaarSession
from bazaar.sa.engine import count_statements, create_engine


def ship_order(session: Session, order_id: int) -> Order:
    """Mark one of the session seller's paid orders as shipped."""
    order = session.get(Order, order_id)  # RLS: other sellers' orders are invisible
    if order is None or order.seller_id != scoped_seller_id(session):
        raise NotFound(f"order {order_id}")
    if order.status is not OrderStatus.PAID:
        raise ValueError(f"order {order_id} is {order.status}, only paid orders can ship")
    order.status = OrderStatus.SHIPPED
    order.shipped_at = sa.func.now()  # a SQL expression as a value: evaluated by the server
    session.flush()
    return order


if __name__ == "__main__":
    admin = create_engine()
    url = admin.url.set(username="bazaar_seller", password="seller")
    seller_engine = sa.create_engine(url)
    with admin.connect() as c:
        paid_42 = c.scalar(sa.text(
            "SELECT min(id) FROM orders WHERE seller_id = 42 AND status = 'paid'"))
        pending_42 = c.scalar(sa.text(
            "SELECT min(id) FROM orders WHERE seller_id = 42 AND status = 'pending'"))
        other = c.scalar(sa.text("SELECT min(id) FROM orders WHERE seller_id = 43"))

    with seller_engine.connect() as conn, conn.begin() as tx:
        session = BazaarSession(bind=conn, join_transaction_mode="create_savepoint",
                                info={SELLER_KEY: 42})
        print("seller 42 sees", session.scalar(sa.select(sa.func.count()).select_from(Order)),
              "orders; current_user =", session.scalar(sa.text("SELECT current_user")))
        with count_statements(seller_engine) as stmts:
            order = ship_order(session, paid_42)
        print(f"shipped order {order.id}: {order.status}, shipped_at={order.shipped_at:%Y-%m-%d %H:%M}")
        print("   statements:", [" ".join(s.split())[:70] for s in stmts])
        for oid in (pending_42, other):
            try:
                ship_order(session, oid)
            except (NotFound, ValueError) as e:
                print(f"order {oid}: {type(e).__name__}: {e}")
        # Column privileges: bazaar_seller may only UPDATE status, shipped_at, delivered_at.
        order.total = 0
        try:
            with session.begin_nested():
                session.flush()
        except sa.exc.ProgrammingError as e:
            print("changing total:", type(e.orig).__name__, "-", str(e.orig).splitlines()[0])
        session.close()
        tx.rollback()
    seller_engine.dispose()
