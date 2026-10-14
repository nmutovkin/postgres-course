"""Exercise 7: export every order line (1.27M rows) to CSV in constant memory.

    python ex07_export_csv.py copy   /tmp/lines.csv
    python ex07_export_csv.py cursor /tmp/lines.csv
"""

import csv
import os
import resource
import sys
import time

import psycopg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
QUERY = """
    SELECT oi.order_id, o.created_at, o.status, oi.product_id, oi.quantity, oi.unit_price,
           oi.quantity * oi.unit_price AS line_total
    FROM order_items oi
    JOIN orders o ON o.id = oi.order_id
    ORDER BY oi.order_id, oi.product_id
"""
HEADER = ["order_id", "created_at", "status", "product_id", "quantity", "unit_price", "line_total"]


def export_copy(conn: psycopg.Connection, path: str) -> None:
    """The server formats CSV; Python only moves bytes."""
    with conn.cursor() as cur, open(path, "wb") as f:
        with cur.copy(f"COPY ({QUERY}) TO STDOUT (FORMAT csv, HEADER)") as copy:
            for chunk in copy:
                f.write(chunk)


def export_cursor(conn: psycopg.Connection, path: str) -> None:
    """Rows become Python objects (so you could transform them) but never all at once."""
    with conn.transaction(), conn.cursor(name="export_lines") as cur, open(path, "w", newline="") as f:
        conn.execute("SET LOCAL cursor_tuple_fraction = 1")  # plan for reading everything
        cur.itersize = 10_000
        cur.execute(QUERY)
        w = csv.writer(f)
        w.writerow(HEADER)
        for row in cur:
            w.writerow(row)


if __name__ == "__main__":
    how, path = sys.argv[1], sys.argv[2]
    with psycopg.connect(DSN) as conn:
        conn.execute("SET TimeZone = 'UTC'")
        conn.commit()
        t = time.perf_counter()
        {"copy": export_copy, "cursor": export_cursor}[how](conn, path)
        dt = time.perf_counter() - t
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    with open(path) as f:
        lines = sum(1 for _ in f)
    print(f"{how:6s} {dt:5.1f}s  peak RSS {peak:5.1f} MiB  {lines:,} lines  "
          f"{os.path.getsize(path) / 2**20:.0f} MiB file")  # fmt: skip
    assert peak < 60 and lines == 1_266_929
