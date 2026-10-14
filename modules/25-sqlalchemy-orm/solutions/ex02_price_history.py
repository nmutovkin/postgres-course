"""Exercise 2: map product_price_history and check what customers paid."""

from __future__ import annotations

import time
from bisect import bisect_right
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship, selectinload

from bazaar.orm.models import Base, Order, OrderItem, Product
from bazaar.sa.drift import compare
from bazaar.sa.engine import count_statements, create_engine


class PriceHistory(Base):
    __tablename__ = "product_price_history"

    product_id: Mapped[int] = mapped_column(
        sa.ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    valid_from: Mapped[datetime] = mapped_column(primary_key=True)
    price: Mapped[Decimal]

    def __repr__(self) -> str:
        return f"PriceHistory({self.product_id}, {self.valid_from:%Y-%m-%d}, {self.price})"


# Adding a relationship to an already-mapped class: declarative accepts new
# attributes after the class body, as long as mappers are not configured yet
# or are re-configured (this happens automatically on first use).
Product.price_history = relationship(
    PriceHistory,
    order_by=PriceHistory.valid_from,
    viewonly=True,  # history is written by price changes, never through this collection
)


def price_at(product: Product, when: datetime) -> Decimal | None:
    """The price valid at `when`: the last history row with valid_from <= when."""
    history = product.price_history  # sorted by valid_from (order_by above)
    i = bisect_right([h.valid_from for h in history], when)
    return history[i - 1].price if i else None


if __name__ == "__main__":
    engine = create_engine()
    with engine.connect() as conn:
        print("mapping drift:", compare(conn, Base.metadata, mapping_only=True))
    with Session(engine) as session:
        p3 = session.get(Product, 3)
        print("product 3 history:", p3.price_history)
        print("price of product 3 on 2024-06-01:", price_at(p3, datetime(2024, 6, 1, tzinfo=UTC)))
        print("current price:", p3.price)

        day = (datetime(2025, 12, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))
        t = time.perf_counter()
        with count_statements(engine) as stmts:
            orders = session.scalars(
                sa.select(Order)
                .where(Order.created_at >= day[0], Order.created_at < day[1])
                .options(
                    selectinload(Order.items)
                    .selectinload(OrderItem.product)
                    .selectinload(Product.price_history)
                )
            ).all()
            lines = [(o, i) for o in orders for i in o.items]
            wrong = [
                (o.id, i.product_id)
                for o, i in lines
                if i.unit_price != price_at(i.product, o.created_at)
            ]
            changed = [i for o, i in lines if i.unit_price != i.product.price]
        elapsed = time.perf_counter() - t
        print(f"{len(orders)} orders, {len(lines)} lines in December 2025 in {len(stmts)} statements:"
              f" {len(wrong)} paid a price that wasn't valid, {len(changed)} differ from today's price"
              f" ({elapsed:.1f} s)")

        # The same check as one statement (section 16): a LATERAL lookup per line.
        h = (
            sa.select(PriceHistory.price)
            .where(
                PriceHistory.product_id == OrderItem.product_id,
                PriceHistory.valid_from <= Order.created_at,
            )
            .order_by(PriceHistory.valid_from.desc())
            .limit(1)
            .lateral("h")
        )
        stmt = (
            sa.select(
                sa.func.count().filter(OrderItem.unit_price != h.c.price),
                sa.func.count().filter(OrderItem.unit_price != Product.price),
            )
            .select_from(Order)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .join(Product, Product.id == OrderItem.product_id)
            .join(h, sa.true())
            .where(Order.created_at >= day[0], Order.created_at < day[1])
        )
        t = time.perf_counter()
        wrong_sql, changed_sql = session.execute(stmt).one()
        print(f"one SQL statement: {wrong_sql} wrong, {changed_sql} changed"
              f" ({time.perf_counter() - t:.2f} s)")
