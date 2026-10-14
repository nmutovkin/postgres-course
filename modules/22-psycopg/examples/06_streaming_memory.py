"""Module 22, section 10: reading 1.27 million rows with bounded memory.

Each strategy runs in a fresh subprocess so its peak RSS is measured alone:
    python 06_streaming_memory.py            # runs all strategies
    python 06_streaming_memory.py named      # runs one
"""

import os
import resource
import subprocess
import sys
import time

import psycopg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
QUERY = "SELECT order_id, product_id, quantity, unit_price FROM order_items"


def consume(row) -> int:
    return row[2]


def client_fetchall(conn):
    rows = conn.execute(QUERY).fetchall()
    return len(rows), sum(consume(r) for r in rows)


def client_iterate(conn):
    n = total = 0
    for row in conn.execute(QUERY):  # the whole result is already in client memory
        n += 1
        total += consume(row)
    return n, total


def named(conn):
    n = total = 0
    with conn.transaction(), conn.cursor(name="order_items_export") as cur:
        cur.itersize = 5000  # rows per FETCH round trip (default 100)
        cur.execute(QUERY)
        for row in cur:
            n += 1
            total += consume(row)
    return n, total


def stream(conn):
    n = total = 0
    for row in conn.cursor().stream(QUERY, size=1000):  # chunked rows mode (libpq 17+)
        n += 1
        total += consume(row)
    return n, total


def copy_out(conn):
    n = total = 0
    with conn.cursor() as cur, cur.copy(f"COPY ({QUERY}) TO STDOUT") as cp:
        cp.set_types(["int8", "int8", "int4", "numeric"])
        for row in cp.rows():
            n += 1
            total += consume(row)
    return n, total


STRATEGIES = {f.__name__: f for f in (client_fetchall, client_iterate, named, stream, copy_out)}


def run_one(name: str) -> None:
    base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    with psycopg.connect(DSN) as conn:
        t = time.perf_counter()
        n, total = STRATEGIES[name](conn)
        dt = time.perf_counter() - t
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # KiB on Linux
    print(f"{name:16s} rows={n:,} units={total:,} time={dt:5.2f}s "
          f"peak RSS={peak / 1024:6.1f} MiB (+{(peak - base) / 1024:6.1f} MiB)")  # fmt: skip


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_one(sys.argv[1])
    else:
        for name in STRATEGIES:
            subprocess.run([sys.executable, __file__, name], check=True)
