"""Session factories, the RLS hook, and the read-only guard.

    from bazaar.orm.session import make_sessionmaker

    Session = make_sessionmaker()            # once per process (owns an engine)
    with Session.begin() as session:         # BEGIN ... COMMIT / ROLLBACK
        session.add(...)

    with seller_session(Session, 42) as session:   # module-15 RLS as seller 42
        ...

Every function in bazaar.orm.services takes a Session first and never commits:
the caller (a request handler, a script, a test) owns the transaction.

expire_on_commit=False: objects stay readable after commit without a new
query. That is what a web handler wants (serialize after commit) and what
async code *needs* (an expired attribute would require lazy IO). The price:
after commit, values are as of the end of the transaction, not "live".
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

from bazaar.orm.models import READ_ONLY
from bazaar.sa.engine import create_async_engine, create_engine

SELLER_KEY = "seller_id"


class BazaarSession(Session):
    """A Session with two Bazaar-specific hooks (registered below):

    * after_begin: if session.info["seller_id"] is set, every transaction the
      session starts runs set_config('app.seller_id', ..., true) first, i.e.
      SET LOCAL, so the module-15 RLS policies see the seller.
    * before_flush: refuses to write mapped views (ActiveProduct).
    """


@event.listens_for(BazaarSession, "after_begin")
def _set_seller_scope(session: Session, transaction: SessionTransaction, conn: Connection) -> None:
    seller_id = session.info.get(SELLER_KEY)
    if seller_id is not None:
        conn.execute(
            sa.text("SELECT set_config('app.seller_id', :sid, true)"), {"sid": str(seller_id)}
        )


@event.listens_for(BazaarSession, "before_flush")
def _refuse_view_writes(session: Session, flush_context: Any, instances: Any) -> None:
    for obj in (*session.new, *session.dirty, *session.deleted):
        if isinstance(obj, READ_ONLY):
            raise sa.exc.InvalidRequestError(f"{type(obj).__name__} is read-only (a view)")


def make_sessionmaker(engine: Engine | None = None, **kw: Any) -> sessionmaker[BazaarSession]:
    params: dict[str, Any] = {"expire_on_commit": False, "class_": BazaarSession}
    params.update(kw)
    return sessionmaker(engine or create_engine(), **params)


@contextmanager
def seller_session(
    factory: sessionmaker[BazaarSession], seller_id: int, **kw: Any
) -> Iterator[BazaarSession]:
    """A session whose transactions run as seller `seller_id` (RLS), committed on success.

    RLS only restricts roles it applies to: connect as bazaar_seller (or any
    non-owner role with the seller policies) for it to have an effect.
    """
    with factory(info={SELLER_KEY: seller_id}, **kw) as session, session.begin():
        yield session


# --- async ---------------------------------------------------------------------------------


def make_async_sessionmaker(
    engine: AsyncEngine | None = None, **kw: Any
) -> async_sessionmaker[AsyncSession]:
    """async_sessionmaker whose sync sessions are BazaarSessions (same hooks).

    The hooks are plain sync functions: AsyncSession runs the ORM in a
    greenlet, and the events fire on its underlying sync Session.
    """
    params: dict[str, Any] = {"expire_on_commit": False, "sync_session_class": BazaarSession}
    params.update(kw)
    return async_sessionmaker(engine or create_async_engine(), **params)
