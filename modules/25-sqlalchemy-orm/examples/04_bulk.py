"""Module 25, section 10: bulk INSERT/UPDATE/DELETE through the ORM (rolled back)."""

import time
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from bazaar.models import ProductStatus
from bazaar.orm.models import CartItem, Inventory, Product
from bazaar.sa.engine import count_statements, create_engine

engine = create_engine()
N = 5_000
rows = [
    {"seller_id": 7, "category_id": 3, "sku": f"M25-BULK-{i:05d}", "title": f"Bulk {i}",
     "price": Decimal("9.99"), "status": ProductStatus.DRAFT}
    for i in range(N)
]  # fmt: skip

with Session(engine) as session:
    session.connection()

    print("1. Unit of work: session.add_all() of 5,000 new objects")
    with count_statements(engine) as stmts:
        t = time.perf_counter()
        session.add_all([Product(**r) for r in rows])
        session.flush()
        t_uow = time.perf_counter() - t
    print(f"   {t_uow:.2f} s, {len(stmts)} statements: {' '.join(stmts[0].split())[:90]}...")
    session.rollback()
    session.expunge_all()

    print("2. ORM bulk INSERT: session.execute(insert(Product), list_of_dicts)")
    with count_statements(engine) as stmts:
        t = time.perf_counter()
        session.execute(sa.insert(Product), rows)
        t_bulk = time.perf_counter() - t
    print(f"   {t_bulk:.2f} s, {len(stmts)} statement(s)")

    print("   ... with RETURNING: get Product objects back")
    with count_statements(engine) as stmts:
        t = time.perf_counter()
        new = session.scalars(
            sa.insert(Product).returning(Product, sort_by_parameter_order=True),
            [{**r, "sku": r["sku"] + "-R"} for r in rows],
        ).all()
        t_ret = time.perf_counter() - t
    print(f"   {t_ret:.2f} s, {len(stmts)} statements, first: {new[0]!r}, in session: {new[0] in session}")

    print("3. Bulk UPDATE by primary key: a list of dicts that include the pk")
    stock = session.scalars(
        sa.select(Inventory).join(Product).where(Product.seller_id == 7).order_by(Inventory.product_id)
    ).all()
    before = stock[0].quantity
    with count_statements(engine) as stmts:
        session.execute(
            sa.update(Inventory),
            [{"product_id": i.product_id, "quantity": i.quantity + 10} for i in stock],
        )
    print(f"   {len(stock)} rows, {len(stmts)} statement(s): {' '.join(stmts[0].split())[:100]}")
    in_db = session.scalar(
        sa.select(Inventory.quantity).where(Inventory.product_id == stock[0].product_id)
    )
    print(f"   first row: object says {stock[0].quantity} (was {before}), database says {in_db}")
    # On a versioned mapper the dicts must carry the version too, and with a
    # server-side version (xmin) the ORM then tries to SET it:
    try:
        with session.begin_nested():
            session.execute(
                sa.update(Product), [{"id": p.id, "xmin": p.xmin, "price": 1} for p in new[:2]]
            )
    except sa.exc.NotSupportedError as e:
        print("   versioned Product:", str(e.orig).splitlines()[0])

    print("4. ORM-enabled UPDATE with WHERE, and synchronize_session")
    p = new[0]
    print("   before:", p.price, p.status)
    session.execute(
        sa.update(Product)
        .where(Product.sku.like("M25-BULK-%-R"), Product.seller_id == 7)
        .values(status=ProductStatus.ARCHIVED, price=Product.price * 2),
        execution_options={"synchronize_session": "auto"},  # the default
    )
    print("   after, synchronize_session='auto':", p.price, p.status)
    session.execute(
        sa.update(Product).where(Product.id == p.id).values(title="changed"),
        execution_options={"synchronize_session": False},
    )
    print("   after, synchronize_session=False: title =", repr(p.title), "(stale)")
    session.refresh(p)
    print("   after refresh():", repr(p.title))

    print("5. ORM-enabled DELETE ... RETURNING")
    gone = session.scalars(
        sa.delete(CartItem).where(CartItem.user_id == 1).returning(CartItem.product_id)
    ).all()
    print("   deleted cart lines for products", gone)
    session.rollback()
_ = datetime.now(UTC)
