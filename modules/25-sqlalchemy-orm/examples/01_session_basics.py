"""Module 25, sections 2-4: identity map, object states, flush vs commit, expiry, autoflush.

Everything runs in transactions that are rolled back (or deletes what it created).
"""

from contextlib import contextmanager
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from bazaar.orm.models import Product, Seller, User
from bazaar.sa.engine import create_engine

engine = create_engine()


@contextmanager
def show_sql():
    """Print each statement sent inside the block (one line each)."""

    def show(conn, cursor, statement, parameters, context, executemany):
        print("    SQL>", " ".join(statement.split())[:110])

    event.listen(engine, "before_cursor_execute", show)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", show)


def state(obj) -> str:
    i = inspect(obj)
    for name in ("transient", "pending", "persistent", "deleted", "detached"):
        if getattr(i, name):
            return name + (" (expired: " + ", ".join(sorted(i.expired_attributes)[:3]) + ", ...)"
                           if i.expired_attributes else "")
    return "?"


print("1. The identity map: one object per row per session")
with Session(engine) as session:
    a = session.get(Product, 20)
    b = session.scalars(sa.select(Product).where(Product.sku == a.sku)).one()
    print("   same object:", a is b, "| session.get again issues SQL? ", end="")
    with show_sql():
        c = session.get(Product, 20)
    print("no" if c is a else "yes")

print("\n2. Object states, flush and commit")
with Session(engine) as session:
    u = User(email="m25-demo@example.com", full_name="Demo Buyer")
    print("   new object:        ", state(u), "| id =", u.id)
    session.add(u)
    print("   after add():       ", state(u), "| id =", u.id)
    with show_sql():
        session.flush()
    print("   after flush():     ", state(u), "| id =", u.id, "| is_active =", u.is_active)
    session.commit()
    print("   after commit():    ", state(u))
    with show_sql():
        print("   reading u.full_name ->", u.full_name)
    session.delete(u)
    session.flush()
    print("   after delete+flush:", state(u))
    session.commit()
print("   after close():     ", state(u))

print("\n3. Autoflush: a query sees pending changes, because they are flushed first")
with Session(engine) as session:
    p = session.get(Product, 20)
    p.price = Decimal("1.00")
    with show_sql():
        cheap = session.scalar(
            sa.select(sa.func.count())
            .select_from(Product)
            .where(Product.price <= 1, Product.seller_id == p.seller_id)
        )
    print("   products <= 1.00 of that seller:", cheap)
    session.rollback()
    print("   after rollback, p.price =", p.price, "(expired and reloaded)")

print("\n4. ...and an autoflush can fail in a place that has nothing to do with the bug")
with Session(engine) as session:
    s = session.get(Seller, 7)
    session.add(Product(seller_id=7, category_id=3, sku="M25-X", title=None, price=Decimal("5")))
    try:
        session.scalars(sa.select(Product).where(Product.seller_id == s.id).limit(1)).first()
    except sa.exc.IntegrityError as e:
        print("   the SELECT raised:", type(e.orig).__name__, "-", str(e.orig).splitlines()[0])
    session.rollback()
    with session.no_autoflush:
        session.add(Product(seller_id=7, category_id=3, sku="M25-Y", title=None, price=Decimal("5")))
        n = session.scalar(sa.select(sa.func.count()).select_from(Product).where(Product.seller_id == 7))
        print("   inside no_autoflush the same kind of query works:", n)
    session.rollback()
