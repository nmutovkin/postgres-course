"""Exercise 12: export a Core query to CSV, streamed by SQLAlchemy and by COPY."""

import csv
import hashlib
import io
import time
from datetime import UTC, datetime

import sqlalchemy as sa

from bazaar.sa.engine import create_engine
from bazaar.sa.tables import order_items, orders, products

o, oi, p = orders, order_items, products
stmt = (
    sa.select(
        o.c.id.label("order_id"),
        o.c.created_at,
        o.c.status,
        p.c.sku,
        oi.c.quantity,
        oi.c.unit_price,
    )
    .join(oi, oi.c.order_id == o.c.id)
    .join(p, p.c.id == oi.c.product_id)
    .where(
        o.c.seller_id == 1,
        o.c.status.in_(["paid", "shipped", "delivered"]),
        o.c.created_at >= datetime(2025, 1, 1, tzinfo=UTC),
        o.c.created_at < datetime(2026, 1, 1, tzinfo=UTC),
    )
    .order_by(o.c.id, p.c.sku)
)


def pg_text(value) -> str:
    """Format like PostgreSQL's text output, so both files are byte-identical."""
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S+00")
    return str(value)


def export_streamed(conn: sa.Connection, out: io.StringIO) -> int:
    w = csv.writer(out, lineterminator="\n")
    w.writerow(stmt.selected_columns.keys())
    n = 0
    # stream_results: a server-side cursor; yield_per: fetch 5,000 rows at a time
    result = conn.execution_options(stream_results=True, yield_per=5000).execute(stmt)
    for row in result:
        w.writerow([pg_text(v) for v in row])
        n += 1
    return n


def export_copy(conn: sa.Connection, out: io.StringIO) -> int:
    # COPY cannot take server-side parameters. psycopg's copy() binds them on the
    # client, safely quoted, so compile the Core statement for *this* dialect
    # (with IN-lists expanded) and pass its parameters along.
    compiled = stmt.compile(conn, compile_kwargs={"render_postcompile": True})
    sql = f"COPY ({compiled}) TO STDOUT WITH (FORMAT csv, HEADER)"
    raw = conn.connection.driver_connection
    with raw.cursor() as cur, cur.copy(sql, compiled.params) as copy:
        for chunk in copy:
            out.write(bytes(chunk).decode())
    return out.getvalue().count("\n") - 1


if __name__ == "__main__":
    engine = create_engine()
    with engine.connect() as conn:
        conn.exec_driver_sql("SET TIME ZONE 'UTC'")
        results = {}
        for name, fn in (("stream", export_streamed), ("copy", export_copy)):
            buf = io.StringIO()
            t = time.perf_counter()
            n = fn(conn, buf)
            elapsed = time.perf_counter() - t
            digest = hashlib.sha256(buf.getvalue().encode()).hexdigest()[:16]
            results[name] = digest
            print(f"{name:<7} {n:>8,} rows  {elapsed:5.2f} s  {len(buf.getvalue()) / 1e6:.1f} MB"
                  f"  sha256={digest}")
            if name == "copy":
                print("first lines:", buf.getvalue().splitlines()[:2])
        print("identical files:", results["stream"] == results["copy"])
