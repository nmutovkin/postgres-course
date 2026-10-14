"""Exercise 9: discounted prices as hybrids that work in Python and in SQL."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

import sqlalchemy as sa
from sqlalchemy.ext.hybrid import hybrid_method
from sqlalchemy.orm import Session, selectinload

from bazaar.models import ProductStatus
from bazaar.orm.models import Product, Promotion
from bazaar.sa.engine import count_statements, create_engine

CENT = Decimal("0.01")


# --- Promotion.is_active_at(ts) ---------------------------------------------------------
def _promo_active_py(self: Promotion, ts: datetime) -> bool:
    return self.active_during.contains(ts)  # sqlalchemy Range implements contains()


def _promo_active_sql(cls, ts: datetime):
    return cls.active_during.contains(ts)  # tstzrange @> timestamptz


Promotion.is_active_at = hybrid_method(_promo_active_py, _promo_active_sql)


# --- Product.price_at(ts): price after the promotion active at ts, if any ----------------
def _price_py(self: Product, ts: datetime) -> Decimal:
    active = [p for p in self.promotions if p.is_active_at(ts)]  # at most one (EXCLUDE)
    if not active:
        return self.price
    factor = 1 - active[0].discount_pct / 100
    return (self.price * factor).quantize(CENT, rounding=ROUND_HALF_UP)  # = SQL round()


def _price_sql(cls, ts: datetime):
    pct = (
        sa.select(Promotion.discount_pct)
        .where(Promotion.product_id == cls.id, Promotion.is_active_at(ts))
        .scalar_subquery()
    )
    return sa.func.round(cls.price * (1 - sa.func.coalesce(pct, 0) / 100), 2)


Product.price_at = hybrid_method(_price_py, _price_sql)


if __name__ == "__main__":
    engine = create_engine()
    noon = datetime(2025, 11, 28, 12, tzinfo=UTC)
    with Session(engine) as session:
        stmt = (
            sa.select(Product)
            .where(
                Product.category_id == 3,
                Product.status == ProductStatus.ACTIVE,
                Product.promotions.any(Promotion.is_active_at(noon)),  # EXISTS (...)
                Product.price_at(noon) < 500,
            )
            .order_by(Product.price_at(noon), Product.id)
            .options(selectinload(Product.promotions))
        )
        with count_statements(engine) as stmts:
            products = session.scalars(stmt).all()
        print(f"{len(products)} discounted laptops under 500 on Black Friday ({len(stmts)} statements)")
        for p in products[:3]:
            print(f"   {p.id} {p.title[:32]:<32} {p.price:>8} -> {p.price_at(noon)}")
        # Python and SQL agree for every product:
        sql_prices = dict(
            session.execute(
                sa.select(Product.id, Product.price_at(noon)).where(
                    Product.id.in_([p.id for p in products])
                )
            ).all()
        )
        print("Python == SQL for all:", all(p.price_at(noon) == sql_prices[p.id] for p in products))
        print("WHERE clause:", str(Product.price_at(noon).compile(engine)).replace("\n", " ")[:160])
