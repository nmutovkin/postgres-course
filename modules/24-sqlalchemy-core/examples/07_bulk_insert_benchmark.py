"""Module 24, section 7.2: loading 100,000 rows through SQLAlchemy, versus COPY.

Same rows and same table shape as module 22's 05_bulk_load_benchmark.py, so the
numbers can be compared. Creates and drops its own table (bench_load_sa).
Run it twice and keep the second run (the first warms caches).
"""

import os
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import sqlalchemy as sa

from bazaar.sa.engine import create_engine

N = int(os.environ.get("N", 100_000))
T0 = datetime(2025, 12, 1, tzinfo=UTC)
ROWS = [
    {
        "sku": f"SKU-{i:07d}",
        "seller_id": i % 2000 + 1,
        "price": Decimal(i % 50_000) / 100,
        "title": f"Product title {i}",
        "observed_at": T0 + timedelta(seconds=i),
    }
    for i in range(N)
]
COLS = ["sku", "seller_id", "price", "title", "observed_at"]

md = sa.MetaData()
bench = sa.Table(
    "bench_load_sa",
    md,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("sku", sa.Text, nullable=False),
    sa.Column("seller_id", sa.BigInteger, nullable=False),
    sa.Column("price", sa.Numeric(12, 2), nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
)
engine = create_engine()


def reset() -> None:
    with engine.begin() as conn:
        md.drop_all(conn)
        md.create_all(conn)


def insertmanyvalues(conn):
    # No RETURNING: SQLAlchemy hands the whole list to the driver's executemany()
    # (psycopg pipelines it, module 22).
    conn.execute(sa.insert(bench), ROWS)


def insertmanyvalues_returning(conn):
    # With RETURNING: "insertmanyvalues" rewrites the batch into multi-row
    # INSERT ... SELECT FROM (VALUES ...) statements of 1,000 rows each.
    ids = conn.execute(sa.insert(bench).returning(bench.c.id, sort_by_parameter_order=True), ROWS)
    assert len(ids.all()) == N


def returning_small_pages(conn):
    c = conn.execution_options(insertmanyvalues_page_size=100)  # default is 1000 rows per batch
    ids = c.execute(sa.insert(bench).returning(bench.c.id, sort_by_parameter_order=True), ROWS)
    assert len(ids.all()) == N


def one_statement_values(conn):
    for i in range(0, N, 5000):  # one INSERT ... VALUES (...), (...) per 5,000 rows
        conn.execute(sa.insert(bench).values(ROWS[i : i + 5000]))


def copy_via_driver(conn):
    raw = conn.connection.driver_connection  # the psycopg.Connection under SQLAlchemy
    with raw.cursor() as cur:
        with cur.copy(f"COPY bench_load_sa ({', '.join(COLS)}) FROM STDIN") as cp:
            for r in ROWS:
                cp.write_row([r[c] for c in COLS])


def run(label, fn, n=N):
    reset()
    t = time.perf_counter()
    with engine.begin() as conn:
        fn(conn)
    elapsed = time.perf_counter() - t
    with engine.connect() as conn:
        count = conn.execute(sa.select(sa.func.count()).select_from(bench)).scalar_one()
    assert count == n, count
    print(f"{label:<52} {elapsed:6.2f} s  {n / elapsed:>9,.0f} rows/s")


run("insert(table), list of dicts (executemany)", insertmanyvalues)
run("... with RETURNING id (insertmanyvalues)", insertmanyvalues_returning)
run("... with RETURNING, insertmanyvalues_page_size=100", returning_small_pages)
run("insert().values(list) per 5,000 rows", one_statement_values)
run("COPY through conn.connection.driver_connection", copy_via_driver)
with engine.begin() as conn:
    md.drop_all(conn)
