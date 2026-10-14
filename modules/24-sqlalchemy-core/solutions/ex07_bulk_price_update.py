"""Exercise 7: 10,000 price changes, three ways (everything is rolled back)."""

import time
from decimal import ROUND_HALF_UP, Decimal

import sqlalchemy as sa

from bazaar.sa.engine import create_engine
from bazaar.sa.tables import products

p = products
engine = create_engine()

with engine.connect() as conn:
    current = conn.execute(
        sa.select(p.c.id, p.c.price).where(p.c.seller_id == 1).order_by(p.c.id).limit(10_000)
    ).all()
CHANGES = [
    {"pid": r.id, "new_price": (r.price * Decimal("1.05")).quantize(Decimal("0.01"), ROUND_HALF_UP)}
    for r in current
]
IDS = [c["pid"] for c in CHANGES]
EXPECTED = sum(c["new_price"] for c in CHANGES)


def executemany(conn):
    stmt = (
        sa.update(p)
        .where(p.c.id == sa.bindparam("pid"))
        .values(price=sa.bindparam("new_price"))
    )
    conn.execute(stmt, CHANGES)  # one statement, executemany over 10,000 parameter sets


def update_from_values(conn):
    for i in range(0, len(CHANGES), 2000):  # 2,000 rows (4,000 parameters) per statement
        batch = CHANGES[i : i + 2000]
        v = sa.values(
            sa.column("pid", sa.BigInteger), sa.column("new_price", sa.Numeric(12, 2)), name="v"
        ).data([(c["pid"], c["new_price"]) for c in batch])
        conn.execute(sa.update(p).where(p.c.id == v.c.pid).values(price=v.c.new_price))


def copy_then_update(conn):
    conn.execute(sa.text("CREATE TEMP TABLE price_changes (pid bigint, new_price numeric(12,2))"))
    with conn.connection.driver_connection.cursor() as cur:
        with cur.copy("COPY price_changes (pid, new_price) FROM STDIN") as cp:
            for c in CHANGES:
                cp.write_row((c["pid"], c["new_price"]))
    conn.execute(
        sa.text("UPDATE products p SET price = c.new_price FROM price_changes c WHERE p.id = c.pid")
    )
    conn.execute(sa.text("DROP TABLE price_changes"))


for label, fn in (
    ("executemany UPDATE ... WHERE id = :pid", executemany),
    ("UPDATE ... FROM (VALUES ...), 2,000 per stmt", update_from_values),
    ("COPY into temp table + UPDATE ... FROM", copy_then_update),
):
    with engine.connect() as conn:
        t = time.perf_counter()
        fn(conn)
        elapsed = time.perf_counter() - t
        total = conn.scalar(sa.select(sa.func.sum(p.c.price)).where(p.c.id.in_(IDS)))
        conn.rollback()
    print(f"{label:<46} {elapsed:6.2f} s   sum={total}  ok={total == EXPECTED}")
