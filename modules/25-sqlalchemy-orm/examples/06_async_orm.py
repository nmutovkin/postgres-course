"""Module 25, section 13: the async ORM, and what lazy loading becomes there."""

import asyncio
import time

import sqlalchemy as sa
from sqlalchemy.exc import MissingGreenlet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from bazaar.orm.models import Order, OrderItem, User
from bazaar.sa.engine import create_async_engine


async def main() -> None:
    engine = create_async_engine(pool_size=5)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as session:
            print("1. A lazy load in async code")
            order = await session.get(Order, 501)
            try:
                print(order.items)
            except MissingGreenlet as e:
                print("   MissingGreenlet:", str(e)[:80], "...")

            print("2. The fixes")
            items = await order.awaitable_attrs.items  # explicit, awaitable lazy load
            print("   awaitable_attrs.items:", len(items), "items")
            stmt = (
                sa.select(Order)
                .where(Order.user_id == 8)
                .order_by(Order.id.desc())
                .limit(3)
                .options(selectinload(Order.items).selectinload(OrderItem.product))
            )
            for o in await session.scalars(stmt):  # eager: everything loaded before return
                print(f"   order {o.id}: {[i.product.sku for i in o.items]}")

            def sync_part(sync_session):  # plain sync ORM code, run inside the greenlet
                u = sync_session.get(User, 8)
                return len(u.orders)  # a lazy load is fine here

            print("   run_sync:", await session.run_sync(sync_part), "orders of user 8")

        print("3. expire_on_commit=True in async code")
        async with AsyncSession(engine) as session:  # the default: expire_on_commit=True
            u = await session.get(User, 8)
            await session.commit()
            try:
                print(u.full_name)
            except MissingGreenlet:
                print("   reading u.full_name after commit -> MissingGreenlet (it was expired)")
            await session.refresh(u)
            print("   after await session.refresh(u):", u.full_name)

        print("4. Fan-out: one session per task, never a shared one")

        async def history(user_id: int) -> int:
            async with Session() as s:
                rows = await s.scalars(
                    sa.select(Order).where(Order.user_id == user_id).options(selectinload(Order.items))
                )
                return sum(len(o.items) for o in rows)

        users = list(range(8, 28))
        t = time.perf_counter()
        seq = [await history(u) for u in users]
        t_seq = time.perf_counter() - t
        t = time.perf_counter()
        con = await asyncio.gather(*(history(u) for u in users))
        t_con = time.perf_counter() - t
        assert seq == con
        print(f"   20 users, {sum(con)} order lines: sequential {t_seq:.2f} s, gather {t_con:.2f} s")

        async with Session() as shared:

            async def bad(user_id: int):
                return (await shared.scalars(sa.select(Order.id).where(Order.user_id == user_id))).all()

            # return_exceptions=True lets the other task finish before the session closes
            results = await asyncio.gather(bad(8), bad(9), return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    print("   two tasks on one AsyncSession:", type(r).__name__, "-", str(r)[:70])
    finally:
        await engine.dispose()


asyncio.run(main())
