"""Module 25, section 12: hybrids, column_property, server-computed values, views, DB functions.

Runs in a transaction that is rolled back.
"""

from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session, undefer

from bazaar.errors import InsufficientStock
from bazaar.orm import services
from bazaar.orm.models import ActiveProduct, Inventory, Order, OrderItem, Product
from bazaar.orm.session import BazaarSession
from bazaar.sa.engine import create_engine

engine = create_engine()

with Session(engine) as session:
    print("1. A hybrid property: Python on instances, SQL on the class")
    inv = session.get(Inventory, 20)
    print("   instance:", inv.quantity, "-", inv.reserved, "=", inv.available)
    stmt = sa.select(sa.func.count()).select_from(Inventory).where(Inventory.available == 0)
    print("   class:   ", str(stmt.compile(engine)).replace("\n", " "))
    print("   sold out:", session.scalar(stmt))

    print("\n2. column_property: a correlated subquery, deferred until asked for")
    o = session.scalars(
        sa.select(Order).where(Order.id == 501).options(undefer(Order.item_count))
    ).one()
    print("   order 501 item_count =", o.item_count)
    sql = str(sa.select(Order).options(undefer(Order.item_count)).compile(engine))
    print("   SQL: SELECT ...", " ".join(sql[sql.index("(SELECT"):].split())[:130])

    print("\n3. A trigger-maintained column (orders.total) goes stale in memory")
    order = session.get(Order, 501)
    print("   total before:", order.total)
    p = session.get(Product, 1)
    order.items.append(OrderItem(product_id=p.id, quantity=1, unit_price=p.price))
    session.flush()
    print("   after adding an item and flushing:", order.total, "(the trigger ran, Python doesn't know)")
    session.refresh(order, ["total"])
    print("   after session.refresh(order, ['total']):", order.total)
    session.rollback()

    print("\n4. server_onupdate=FetchedValue(): updated_at is expired after an UPDATE")
    p = session.get(Product, 20)
    before = p.updated_at
    p.price = p.price + Decimal("1")
    session.flush()
    print("   updated_at expired?", "updated_at" in sa.inspect(p).expired_attributes)
    print("   reloaded on access:", before, "->", p.updated_at)
    session.rollback()

    print("\n5. A view mapped read-only")
    ap = session.scalars(
        sa.select(ActiveProduct).where(ActiveProduct.category_name == "Laptops").limit(1)
    ).one()
    print("   ", ap, ap.shop_name, ap.available)
    ap.price = Decimal("1")
    try:
        session.flush()  # a plain Session sends the UPDATE; PostgreSQL refuses it
    except sa.exc.OperationalError as e:
        print("   plain Session, flush:", str(e.orig).splitlines()[0])
    session.rollback()

with BazaarSession(engine) as session:  # bazaar.orm.session's guard stops it before SQL
    ap = session.get(ActiveProduct, 15)
    ap.price = Decimal("1")
    try:
        session.flush()
    except sa.exc.InvalidRequestError as e:
        print("   BazaarSession, flush:", e)
    session.rollback()

    print("\n6. Calling place_order() through the checkout service")
    pid = session.scalars(
        sa.select(Inventory.product_id)
        .join(Product)
        .where(Product.status == "active", Inventory.available == 1)
        .order_by(Inventory.product_id)
        .limit(1)
    ).one()
    try:
        services.checkout(session, 8, [(pid, 2)])
    except InsufficientStock as e:
        print(f"   InsufficientStock: product {e.product_id}, requested {e.requested},"
              f" available {e.available}")
    [order] = services.checkout(session, 8, [(pid, 1)])
    print("   placed:", order, [(i.product_id, i.quantity) for i in order.items])
    session.rollback()
