"""Module 25, sections 5-6: relationships, the association object, cascades, passive_deletes.

Runs in one transaction that is rolled back.
"""

from contextlib import contextmanager
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.orm import Session

from bazaar.orm.models import Category, Inventory, Order, OrderItem, Product, Review
from bazaar.sa.engine import create_engine

engine = create_engine()


@contextmanager
def show_sql(width: int = 110):
    def show(conn, cursor, statement, parameters, context, executemany):
        print("    SQL>", " ".join(statement.split())[:width], "(executemany)" if executemany else "")

    event.listen(engine, "before_cursor_execute", show)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", show)


with Session(engine) as session:
    print("1. A self-referential tree")
    laptops = session.get(Category, 3)
    path = []
    node = laptops
    while node is not None:  # each .parent is a lazy load (or an identity-map hit)
        path.append(node.name)
        node = node.parent
    print("   ", " > ".join(reversed(path)), "| children of Computers:",
          [c.name for c in laptops.parent.children])

    print("\n2. Many-to-one, one-to-one, and the association object")
    order = session.get(Order, 501)
    for item in order.items:  # OrderItem rows: quantity and unit_price live here
        print(f"    {item.quantity} x {item.product.title[:30]:<30} @ {item.unit_price}"
              f"  (stock now {item.product.inventory.available})")
    print("    buyer:", order.user.full_name, "| shop:", order.seller.shop_name, "| total:", order.total)

    print("\n3. Building a graph and flushing it: the unit of work orders the INSERTs")
    p = Product(
        seller_id=7, category_id=3, sku="M25-REL", title="Relationship demo", price=Decimal("10"),
        inventory=Inventory(quantity=5),
    )  # fmt: skip
    p.reviews.append(Review(user_id=8, rating=5, title="ok"))
    session.add(p)  # cascade="save-update" adds the inventory and the review too
    with show_sql():
        session.flush()
    print("    product", p.id, "inventory", p.inventory.product_id, "review", p.reviews[0].id)

    print("\n4. delete-orphan: removing a child from the collection deletes the row")
    with show_sql():
        p.reviews.clear()
        session.flush()

    print("\n5. Deleting a parent: passive_deletes lets ON DELETE CASCADE do the work")
    p2 = session.get(Product, 2)  # 167 reviews, never loaded into the session
    session.execute(sa.delete(OrderItem).where(OrderItem.product_id == 2))  # FK without cascade
    with show_sql():
        session.delete(p2)
        session.flush()
    left = session.scalar(sa.select(sa.func.count()).select_from(Review).where(Review.product_id == 2))
    print("    reviews of product 2 left:", left)

    print("\n6. ...unless the children are already loaded: then the ORM deletes them itself")
    p3 = session.get(Product, 3)
    print("    product 3 has", len(p3.reviews), "reviews loaded in the session")
    session.execute(sa.delete(OrderItem).where(OrderItem.product_id == 3))
    with show_sql():
        session.delete(p3)
        session.flush()
    session.rollback()
