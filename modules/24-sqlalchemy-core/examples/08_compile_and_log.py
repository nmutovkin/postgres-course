"""Module 24, section 9: looking at the SQL SQLAlchemy generates."""

import logging
import time
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.dialects import postgresql

from bazaar.sa.engine import create_engine
from bazaar.sa.tables import products

p = products
stmt = (
    sa.select(p.c.id, p.c.title)
    .where(p.c.seller_id == 42, p.c.price < Decimal("9.99"), p.c.title.ilike("%o'%"))
    .limit(2)
)

print("1. str(stmt) -- the generic dialect, named :params")
print(stmt, "\n")
print("2. compiled for the PostgreSQL dialect (its default driver: psycopg in 2.1)")
compiled = stmt.compile(dialect=postgresql.dialect())
print(compiled)
print("   params:", compiled.params, "\n")
print("3. literal_binds -- for reading/logging only, never for executing")
print(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}), "\n")

engine = create_engine()
print("4. compiled for this engine (psycopg: %(name)s placeholders with casts)")
print(stmt.compile(engine), "\n")

print("5. the engine log, and its statement cache")
log = logging.getLogger("sqlalchemy.engine.Engine")
h = logging.StreamHandler()
h.setFormatter(logging.Formatter("    SQL> %(message)s"))
log.addHandler(h)
log.setLevel(logging.INFO)
with engine.connect() as conn:
    for seller in (42, 43):
        conn.execute(
            sa.select(p.c.id).where(p.c.seller_id == seller, p.c.price < Decimal("9.99")).limit(1)
        ).all()
log.setLevel(logging.WARNING)

print("\n6. timing every statement with cursor events")


@event.listens_for(engine, "before_cursor_execute")
def _start(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault("t0", []).append(time.perf_counter())


@event.listens_for(engine, "after_cursor_execute")
def _stop(conn, cursor, statement, parameters, context, executemany):
    ms = (time.perf_counter() - conn.info["t0"].pop()) * 1000
    print(f"    {ms:7.2f} ms  {' '.join(statement.split())[:70]}")


with engine.connect() as conn:
    conn.execute(sa.select(sa.func.count()).select_from(p).where(p.c.seller_id == 1)).scalar()
    conn.execute(sa.select(sa.func.count()).select_from(p)).scalar()
