"""Module 25, sections 11 and 15: optimistic concurrency with xmin, and RLS per transaction.

Creates a committed test product for the first part and deletes it at the end.
"""

from contextlib import contextmanager
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.orm.exc import StaleDataError

from bazaar.orm.models import Product
from bazaar.orm.session import SELLER_KEY, make_sessionmaker
from bazaar.sa.engine import create_engine

engine = create_engine()
Session = make_sessionmaker(engine)


@contextmanager
def show_sql(eng):
    def show(conn, cursor, statement, parameters, context, executemany):
        print("    SQL>", " ".join(statement.split())[:120], parameters if len(str(parameters)) < 60 else "")

    event.listen(eng, "before_cursor_execute", show)
    try:
        yield
    finally:
        event.remove(eng, "before_cursor_execute", show)


with Session.begin() as s:
    p = Product(seller_id=7, category_id=3, sku="M25-VER", title="Versioned", price=Decimal("10"))
    s.add(p)
    s.flush()
    pid = p.id

print("1. Two sessions edit the same product")
with Session() as alice, Session() as bob:
    a = alice.get(Product, pid)
    b = bob.get(Product, pid)
    print("   both loaded xmin =", a.xmin, b.xmin)
    b.price = Decimal("12.00")
    with show_sql(engine):
        bob.commit()
    print("   Bob committed; the row's xmin is now", bob.get(Product, pid).xmin)
    a.title = "Alice's title"
    try:
        with show_sql(engine):
            alice.flush()
    except StaleDataError as e:
        print("   Alice:", type(e).__name__, "-", str(e)[:95])
    alice.rollback()
    print("   after rollback Alice sees", alice.get(Product, pid).price, "and can retry")

with Session.begin() as s:
    s.execute(sa.delete(Product).where(Product.id == pid))

print("\n2. RLS: the session sets app.seller_id at the start of every transaction")
url = engine.url.set(username="bazaar_seller", password="seller")
seller_engine = sa.create_engine(url)
SellerSession = make_sessionmaker(seller_engine)
with SellerSession(info={SELLER_KEY: 42}) as s:
    with show_sql(seller_engine):
        n = s.scalar(sa.select(sa.func.count()).select_from(Product))
    print("   seller 42 sees", n, "products")
    s.commit()
    with show_sql(seller_engine):
        other = s.get(Product, 1)  # seller 5's product
    print("   product 1 (another seller's):", other)
with SellerSession() as s:  # no seller in info: the policy sees current_seller_id() IS NULL
    print("   without a seller id:", s.scalar(sa.select(sa.func.count()).select_from(Product)))
seller_engine.dispose()
