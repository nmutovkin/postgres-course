"""Module 24, section 1.3: what does the abstraction cost per statement?

Runs the same primary-key lookup N times through plain psycopg, Core text(),
a Core select() (with SQLAlchemy's compiled-statement cache), and the same
select() with the cache disabled. Everything runs in one transaction on one
connection, so only client-side overhead differs.
"""

import os
import random
import time

import psycopg
import sqlalchemy as sa

from bazaar.sa.tables import products

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
N = int(os.environ.get("N", 5000))
random.seed(24)
IDS = [random.randint(1, 200_000) for _ in range(N)]
SQL = "SELECT id, title, price FROM products WHERE id = %s"


def bench(label: str, fn) -> None:
    fn(IDS[:200])  # warm up
    t = time.perf_counter()
    fn(IDS)
    per = (time.perf_counter() - t) / N * 1e6
    print(f"{label:<44} {per:7.1f} µs/query")


with psycopg.connect(DSN) as pconn:

    def raw(ids):
        for i in ids:
            pconn.execute(SQL, (i,)).fetchone()

    bench("psycopg, conn.execute()", raw)
    pconn.prepare_threshold = None  # never prepare: like most SQLAlchemy setups

    bench("psycopg, conn.execute(), no prepared stmts", raw)

engine = sa.create_engine(sa.make_url(DSN).set(drivername="postgresql+psycopg"))
stmt_text = sa.text("SELECT id, title, price FROM products WHERE id = :id")
with engine.connect() as conn:

    def core_text(ids):
        for i in ids:
            conn.execute(stmt_text, {"id": i}).one()

    def core_select(ids):
        for i in ids:
            conn.execute(
                sa.select(products.c.id, products.c.title, products.c.price).where(
                    products.c.id == i
                )
            ).one()

    def core_select_nocache(ids):
        c = conn.execution_options(compiled_cache=None)
        for i in ids:
            c.execute(
                sa.select(products.c.id, products.c.title, products.c.price).where(
                    products.c.id == i
                )
            ).one()

    bench("Core text()", core_text)
    bench("Core select(), built per call, cached", core_select)
    bench("Core select(), compiled_cache=None", core_select_nocache)

with engine.connect() as conn:
    t = time.perf_counter()
    conn.execute(sa.text("SELECT 1")).scalar()
    for _ in range(1000):
        conn.execute(sa.text("SELECT 1")).scalar()
    print(f"{'round trip (SELECT 1 through Core)':<44} {(time.perf_counter() - t) * 1000:7.1f} µs")
