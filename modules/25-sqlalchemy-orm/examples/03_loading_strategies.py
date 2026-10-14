"""Module 25, sections 8-9: the N+1 problem and the loading strategies, measured.

"Page" = the 100 most recent orders of seller 42, each with its buyer, its
items and each item's product, rendered into a list of strings.
"""

import time

import sqlalchemy as sa
from sqlalchemy.orm import (
    Session,
    joinedload,
    lazyload,
    raiseload,
    selectinload,
    subqueryload,
)

from bazaar.orm.models import Order, OrderItem
from bazaar.sa.engine import count_statements, create_engine

engine = create_engine()


def page(session: Session, *options) -> list[str]:
    orders = session.scalars(
        sa.select(Order)
        .where(Order.seller_id == 42)
        .order_by(Order.created_at.desc())
        .limit(100)
        .options(*options)
    ).unique()  # unique(): needed when a collection is joined-eager-loaded
    return [
        f"{o.id} {o.user.full_name}: " + ", ".join(f"{i.quantity}x{i.product.sku}" for i in o.items)
        for o in orders
    ]


strategies = {
    "lazy (the default)": [],
    "joinedload everything": [
        joinedload(Order.user),
        joinedload(Order.items).joinedload(OrderItem.product),
    ],
    "selectinload everything": [
        selectinload(Order.user),
        selectinload(Order.items).selectinload(OrderItem.product),
    ],
    "joined to-one, selectin collections": [
        joinedload(Order.user),
        selectinload(Order.items).joinedload(OrderItem.product),
    ],
    "subqueryload collections": [
        joinedload(Order.user),
        subqueryload(Order.items).joinedload(OrderItem.product),
    ],
}

reference = None
for label, options in strategies.items():
    timings = []
    for _ in range(5):
        with Session(engine) as session:
            session.connection()  # start the transaction outside the measurement
            with count_statements(engine) as stmts:
                t = time.perf_counter()
                lines = page(session, *options)
                timings.append(time.perf_counter() - t)
    reference = reference or lines
    assert lines == reference
    print(f"{label:<38} {len(stmts):>4} statements  {min(timings) * 1000:7.1f} ms")
    if label in ("joinedload everything", "subqueryload collections"):
        for s in stmts:
            print("      ", " ".join(s.split())[:150], "...")

print("\nraiseload('*') as a guard:")
with Session(engine) as session:
    orders = session.scalars(
        sa.select(Order).where(Order.id == 501).options(selectinload(Order.items), raiseload("*"))
    ).all()
    print("   items were loaded:", len(orders[0].items))
    try:
        _ = orders[0].user
    except sa.exc.InvalidRequestError as e:
        print("   accessing .user:", e)
    # lazyload() re-enables lazy loading for one path, e.g. in a script
    o = session.scalars(sa.select(Order).where(Order.id == 971).options(lazyload(Order.user))).one()
    print("   with lazyload(Order.user):", o.user.full_name)
