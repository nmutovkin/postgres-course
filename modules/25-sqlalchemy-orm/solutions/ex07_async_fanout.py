"""Exercise 7: per-buyer profiles for the 10 biggest buyers, concurrently, on the async ORM."""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.exc import MissingGreenlet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import raiseload, selectinload

from bazaar.orm.models import Order, OrderItem, Product
from bazaar.sa.engine import create_async_engine


@dataclass(frozen=True)
class Profile:
    user_id: int
    orders: int
    units: int
    shops: int
    top_category: str


def history_stmt(user_id: int) -> sa.Select:
    return (
        sa.select(Order)
        .where(Order.user_id == user_id)
        .options(
            selectinload(Order.items)
            .joinedload(OrderItem.product)
            .joinedload(Product.category),
            raiseload("*"),
        )
    )


def profile(user_id: int, orders: list[Order]) -> Profile:
    cats = Counter(i.product.category.name for o in orders for i in o.items)
    return Profile(
        user_id=user_id,
        orders=len(orders),
        units=sum(i.quantity for o in orders for i in o.items),
        shops=len({o.seller_id for o in orders}),
        top_category=min(cats.items(), key=lambda kv: (-kv[1], kv[0]))[0],
    )


async def one(Session: async_sessionmaker[AsyncSession], user_id: int) -> Profile:
    async with Session() as session:  # one session (and connection) per task
        orders = list(await session.scalars(history_stmt(user_id)))
        return profile(user_id, orders)


async def main() -> None:
    engine = create_async_engine(pool_size=5)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as session:
            top = list(
                await session.scalars(
                    sa.select(Order.user_id)
                    .group_by(Order.user_id)
                    .order_by(sa.func.count().desc(), Order.user_id)
                    .limit(10)
                )
            )
            # The naive version: lazy loading inside async code
            orders = list(await session.scalars(sa.select(Order).where(Order.user_id == top[0])))
            try:
                profile(top[0], orders)
            except MissingGreenlet as e:
                print("naive:", type(e).__name__, "-", str(e)[:60], "...")

        t = time.perf_counter()
        sequential = [await one(Session, u) for u in top]
        t_seq = time.perf_counter() - t
        t = time.perf_counter()
        concurrent = await asyncio.gather(*(one(Session, u) for u in top))
        t_con = time.perf_counter() - t
        assert sequential == concurrent
        for p in concurrent[:3]:
            print(p)
        print(f"10 profiles: sequential {t_seq:.2f} s, gather (pool of 5) {t_con:.2f} s;"
              f" total orders {sum(p.orders for p in concurrent)}")
    finally:
        await engine.dispose()


asyncio.run(main())
