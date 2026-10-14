"""Exercise 3: objects that outlive their session."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import selectinload, sessionmaker

from bazaar.orm.models import Order
from bazaar.sa.engine import create_engine

engine = create_engine()
Session = sessionmaker(engine)  # the ORM defaults: expire_on_commit=True


def latest_orders_broken(user_id: int) -> list[Order]:
    """The starting point: returns ORM objects from a closed session."""
    with Session.begin() as session:  # commits (and therefore expires) at the end
        return list(
            session.scalars(
                sa.select(Order).where(Order.user_id == user_id).order_by(Order.id.desc()).limit(3)
            )
        )


def show(label: str, fn) -> None:
    try:
        orders = fn(8)
        print(f"{label:<34} OK:", [(o.id, o.total, len(o.items)) for o in orders])
    except sa.exc.SQLAlchemyError as e:
        print(f"{label:<34} {type(e).__name__}: {str(e)[:70]}...")


# Fix 1: keep loaded state after commit, and load everything the caller will touch.
NoExpire = sessionmaker(engine, expire_on_commit=False)


def latest_orders_eager(user_id: int) -> list[Order]:
    with NoExpire.begin() as session:
        return list(
            session.scalars(
                sa.select(Order)
                .where(Order.user_id == user_id)
                .order_by(Order.id.desc())
                .limit(3)
                .options(selectinload(Order.items))
            )
        )


# Fix 2: don't let ORM objects escape at all: return plain data.
@dataclass(frozen=True, slots=True)
class OrderView:
    id: int
    total: Decimal
    items: tuple[int, ...]  # product ids


def latest_orders_dto(user_id: int) -> list[OrderView]:
    with Session.begin() as session:
        orders = session.scalars(
            sa.select(Order)
            .where(Order.user_id == user_id)
            .order_by(Order.id.desc())
            .limit(3)
            .options(selectinload(Order.items))
        )
        return [OrderView(o.id, o.total, tuple(i.product_id for i in o.items)) for o in orders]


# Fix 3: the caller owns the session (the convention of bazaar.orm.services).
def latest_orders_in(session, user_id: int) -> list[Order]:
    return list(
        session.scalars(
            sa.select(Order).where(Order.user_id == user_id).order_by(Order.id.desc()).limit(3)
        )
    )


if __name__ == "__main__":
    show("broken (expired + detached)", latest_orders_broken)
    NoExpireLazy = sessionmaker(engine, expire_on_commit=False)

    def half_fixed(user_id):
        with NoExpireLazy.begin() as session:
            return latest_orders_in(session, user_id)

    show("expire_on_commit=False, lazy items", half_fixed)
    show("fix 1: no expiry + selectinload", latest_orders_eager)
    print(f"{'fix 2: DTOs':<34} OK:", [(v.id, v.total, len(v.items)) for v in latest_orders_dto(8)])
    with Session() as session:
        orders = latest_orders_in(session, 8)
        print(f"{'fix 3: caller owns the session':<34} OK:",
              [(o.id, o.total, len(o.items)) for o in orders])
