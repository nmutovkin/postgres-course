"""Exercise 8: a Black Friday price cut as ORM bulk operations (rolled back)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from bazaar.models import ProductStatus
from bazaar.orm.models import Product
from bazaar.sa.engine import count_statements, create_engine
from bazaar.sa.tables import product_price_history

BLACK_FRIDAY = datetime(2025, 11, 28, tzinfo=UTC)
CUT = Decimal("0.80")

if __name__ == "__main__":
    engine = create_engine()
    with Session(engine) as session:
        session.connection()
        which = (
            Product.seller_id == 42,
            Product.status == ProductStatus.ACTIVE,
            Product.tags.contains(["gift"]),
        )
        # Some of these products are already in the session (e.g. a page being edited):
        loaded = session.scalars(sa.select(Product).where(*which).order_by(Product.id).limit(3)).all()
        before = {p.id: p.price for p in loaded}

        with count_statements(engine) as stmts:
            # 1. one UPDATE for all of them, prices rounded in SQL, new prices returned
            changed = session.execute(
                sa.update(Product)
                .where(*which)
                .values(price=sa.func.round(Product.price * CUT, 2))
                .returning(Product.id, Product.price),
                execution_options={"synchronize_session": "fetch"},
            ).all()
            # 2. one INSERT for the history rows (a Core table: no ORM objects needed)
            session.execute(
                sa.insert(product_price_history),
                [{"product_id": pid, "price": price, "valid_from": BLACK_FRIDAY}
                 for pid, price in changed],
            )  # fmt: skip
        print(f"{len(changed)} products repriced in {len(stmts)} statements")
        for p in loaded:
            print(f"   product {p.id}: {before[p.id]} -> {p.price} (in-memory object, no reload)")
        total = session.scalar(sa.select(sa.func.sum(Product.price)).where(*which))
        hist = session.scalar(
            sa.select(sa.func.count())
            .select_from(product_price_history)
            .where(product_price_history.c.valid_from == BLACK_FRIDAY)
        )
        print("sum of new prices:", total, "| history rows dated 2025-11-28:", hist)

        # 3. the same with synchronize_session=False: the session keeps stale values
        p = loaded[0]
        session.execute(
            sa.update(Product).where(Product.id == p.id).values(price=Decimal("1.00")),
            execution_options={"synchronize_session": False},
        )
        print("synchronize_session=False -> in memory:", p.price, "| in the database:",
              session.scalar(sa.select(Product.price).where(Product.id == p.id)))
        session.rollback()
