"""Exercise 12: RLS with the async ORM and a pool: SET LOCAL versus a leaking SET."""

from __future__ import annotations

import asyncio
import dataclasses
import random
from collections import Counter

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import Session

from bazaar.config import get_settings
from bazaar.orm.models import Product
from bazaar.orm.session import SELLER_KEY, BazaarSession, make_async_sessionmaker
from bazaar.sa.engine import create_async_engine, create_engine

SELLERS = [41, 42, 43, 44, None]  # None = an anonymous request: must see nothing


class LeakySession(Session):
    """The bug: a session-level setting (is_local=false) survives COMMIT and goes
    back to the pool with the connection."""


@event.listens_for(LeakySession, "after_begin")
def _leaky(session, transaction, conn) -> None:
    sid = session.info.get(SELLER_KEY)
    if sid is not None:
        conn.execute(sa.text("SELECT set_config('app.seller_id', :sid, false)"), {"sid": str(sid)})


async def visible(factory, seller_id) -> tuple[int | None, int]:
    info = {SELLER_KEY: seller_id} if seller_id is not None else {}
    async with factory(info=info) as session:
        await asyncio.sleep(random.random() / 100)  # interleave the tasks
        n = await session.scalar(sa.select(sa.func.count()).select_from(Product))
        await session.commit()
        return seller_id, n


async def trial(engine: AsyncEngine, session_class, expected: dict) -> Counter:
    factory = make_async_sessionmaker(engine, sync_session_class=session_class)
    random.seed(12)
    jobs = [random.choice(SELLERS) for _ in range(200)]
    results = await asyncio.gather(*(visible(factory, s) for s in jobs))
    return Counter("ok" if n == expected[s] else f"wrong for {s}" for s, n in results)


async def main() -> None:
    with create_engine().connect() as c:  # the truth, as the owner sees it
        expected = {
            s: c.scalar(sa.select(sa.func.count()).where(Product.seller_id == s)) for s in SELLERS[:-1]
        }
    expected[None] = 0
    print("expected:", expected)
    # The application connects as bazaar_seller: RLS applies to it (not to the owner).
    dsn = sa.make_url(get_settings().dsn).set(username="bazaar_seller", password="seller")
    settings = dataclasses.replace(get_settings(), dsn=dsn.render_as_string(hide_password=False))
    for label, cls in (("SET LOCAL (BazaarSession)", BazaarSession), ("session-level SET", LeakySession)):
        engine = create_async_engine(settings, pool_size=3)  # asyncpg
        try:
            print(f"{label:<28}", dict(await trial(engine, cls, expected)))
        finally:
            await engine.dispose()


asyncio.run(main())
