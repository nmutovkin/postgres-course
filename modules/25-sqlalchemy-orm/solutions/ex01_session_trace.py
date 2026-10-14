"""Exercise 1: trace what a Session does, statement by statement."""

from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from bazaar.orm.models import Product
from bazaar.sa.engine import count_statements, create_engine

engine = create_engine()


def step(label, fn):
    with count_statements(engine) as stmts:
        result = fn()
    kinds = [s.split()[0] for s in stmts]
    print(f"{label:<52} {len(stmts)} statement(s) {kinds}")
    return result


with Session(engine) as session:
    a = step("1. session.get(Product, 20)", lambda: session.get(Product, 20))
    b = step("2. session.get(Product, 20) again", lambda: session.get(Product, 20))
    c = step(
        "3. select(Product).where(sku == ...)",
        lambda: session.scalars(sa.select(Product).where(Product.sku == a.sku)).one(),
    )
    print("   a is b is c:", a is b is c)

    a.price = Decimal("1.00")
    print("   after a.price = 1.00: dirty =", list(session.dirty), "| modified:",
          inspect(a).attrs.price.history.added)
    n = step(
        "4. count that seller's products priced <= 1.00",
        lambda: session.scalar(
            sa.select(sa.func.count()).where(Product.seller_id == a.seller_id, Product.price <= 1)
        ),
    )
    print("   count =", n, "(our unflushed change was flushed first: autoflush)")
    step("5. session.rollback()", session.rollback)
    print("   expired after rollback:", sorted(inspect(a).expired_attributes)[:4], "...")
    price = step("6. read a.price", lambda: a.price)
    print("   a.price =", price)
    step("7. read a.title (same row, already reloaded)", lambda: a.title)
    session.expunge(a)
    print("   after expunge:", "detached" if inspect(a).detached else "?", "| a in session:", a in session)
