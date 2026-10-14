"""Exercise 5: archived products disappear from every ORM query, unless asked for."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session, selectinload, with_loader_criteria

from bazaar.models import ProductStatus
from bazaar.orm.models import Category, OrderItem, Product, Seller
from bazaar.sa.engine import create_engine


class CatalogSession(Session):
    """A Session in which archived products are invisible by default."""


@event.listens_for(CatalogSession, "do_orm_execute")
def _hide_archived(state: ORMExecuteState) -> None:
    if (
        state.is_select
        and not state.is_column_load  # refreshing an object we already have
        and not state.execution_options.get("include_archived", False)
    ):
        state.statement = state.statement.options(
            with_loader_criteria(
                Product,
                lambda cls: cls.status != ProductStatus.ARCHIVED,
                include_aliases=True,
            )
        )


def count(session, stmt, **opts) -> int:
    return session.scalar(stmt, execution_options=opts)


if __name__ == "__main__":
    engine = create_engine()
    with CatalogSession(engine) as s:
        all_42 = sa.select(sa.func.count(Product.id)).where(Product.seller_id == 42)
        print("1. count of seller 42's products:", count(s, all_42),
              "| include_archived:", count(s, all_42, include_archived=True))

        seller = s.get(Seller, 42)
        print("2. lazy Seller.products:", len(seller.products))
        s.expire(seller, ["products"])
        print("   with selectinload in another query:",
              len(s.scalars(sa.select(Seller).where(Seller.id == 42)
                            .options(selectinload(Seller.products))).one().products))

        cat = s.scalars(sa.select(Category).where(Category.slug == "laptops")).one()
        joined = count(
            s,
            sa.select(sa.func.count()).select_from(Category)
            .join(Category.products).where(Category.id == cat.id),
        )
        print("3. laptops via a join:", joined, "| via the relationship:", len(cat.products))

        archived_id = s.scalar(
            sa.select(Product.id).where(Product.status == ProductStatus.ARCHIVED).order_by(Product.id).limit(1),
            execution_options={"include_archived": True},
        )
        s.expunge_all()
        print("4. session.get() of archived product", archived_id, "->", s.get(Product, archived_id))
        item = s.scalars(
            sa.select(OrderItem).where(OrderItem.product_id == archived_id).limit(1)
        ).first()
        print("   an order line of it: product_id =", item.product_id if item else None,
              "| item.product =", item.product if item else None)
        old = s.get(Product, archived_id, execution_options={"include_archived": True})
        print("   with include_archived:", old)
