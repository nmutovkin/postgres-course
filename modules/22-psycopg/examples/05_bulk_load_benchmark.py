"""Module 22, section 9: loading 100,000 rows six ways.

Creates and drops its own table (bench_load). Run it twice and keep the second
run's numbers (the first run warms caches). Numbers depend heavily on the
round-trip time between client and server: see the RTT line first.
"""

import os
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
N = int(os.environ.get("N", 100_000))
T0 = datetime(2025, 12, 1, tzinfo=UTC)
ROWS = [
    (f"SKU-{i:07d}", i % 2000 + 1, Decimal(i % 50_000) / 100, f"Product title {i}",
     T0 + timedelta(seconds=i))
    for i in range(N)
]  # fmt: skip
INSERT = "INSERT INTO bench_load (sku, seller_id, price, title, observed_at) VALUES (%s, %s, %s, %s, %s)"


def reset(conn: psycopg.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS bench_load")
    conn.execute(
        """CREATE TABLE bench_load (
               id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
               sku text NOT NULL, seller_id bigint NOT NULL, price numeric(12,2) NOT NULL,
               title text NOT NULL, observed_at timestamptz NOT NULL)"""
    )
    conn.commit()


def execute_loop(conn, rows):
    with conn.transaction():
        for r in rows:
            conn.execute(INSERT, r)


def execute_loop_autocommit(conn, rows):
    conn.autocommit = True
    try:
        for r in rows:
            conn.execute(INSERT, r)
    finally:
        conn.autocommit = False


def execute_loop_pipeline(conn, rows):
    with conn.transaction(), conn.pipeline():
        for r in rows:
            conn.execute(INSERT, r)


def executemany(conn, rows):
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(INSERT, rows)


def unnest_arrays(conn, rows):
    cols = list(zip(*rows, strict=True))
    with conn.transaction():
        conn.execute(
            """INSERT INTO bench_load (sku, seller_id, price, title, observed_at)
               SELECT * FROM unnest(%s::text[], %s::bigint[], %s::numeric[], %s::text[],
                                    %s::timestamptz[])""",
            [list(c) for c in cols],
        )


def copy_text(conn, rows):
    with conn.transaction(), conn.cursor() as cur:
        with cur.copy("COPY bench_load (sku, seller_id, price, title, observed_at) FROM STDIN") as cp:
            for r in rows:
                cp.write_row(r)


def copy_binary(conn, rows):
    with conn.transaction(), conn.cursor() as cur:
        with cur.copy(
            "COPY bench_load (sku, seller_id, price, title, observed_at) FROM STDIN (FORMAT BINARY)"
        ) as cp:
            cp.set_types(["text", "int8", "numeric", "text", "timestamptz"])
            for r in rows:
                cp.write_row(r)


def main() -> None:
    with psycopg.connect(DSN) as conn:
        conn.autocommit = True
        t = time.perf_counter()
        for _ in range(2000):
            conn.execute("SELECT 1")
        rtt = (time.perf_counter() - t) / 2000
        conn.autocommit = False
        print(f"round trip (SELECT 1): {rtt * 1e6:.0f} µs   rows: {N:,}")
        methods = [
            ("execute() per row, autocommit (1 txn/row)", execute_loop_autocommit, N // 10),
            ("execute() per row, one transaction", execute_loop, N),
            ("execute() per row, pipeline mode", execute_loop_pipeline, N),
            ("executemany()", executemany, N),
            ("one INSERT ... SELECT FROM unnest(arrays)", unnest_arrays, N),
            ("COPY, text format", copy_text, N),
            ("COPY, binary format", copy_binary, N),
        ]
        for label, fn, n in methods:
            reset(conn)
            t = time.perf_counter()
            fn(conn, ROWS[:n])
            dt = time.perf_counter() - t
            count = conn.execute("SELECT count(*) FROM bench_load").fetchone()[0]
            conn.commit()
            assert count == n, (label, count)
            note = f"  ({n:,} rows, scaled x{N // n})" if n != N else ""
            print(f"{label:45s} {dt * N / n:7.2f} s {N / (dt * N / n):>10,.0f} rows/s{note}")
        conn.execute("DROP TABLE bench_load")


if __name__ == "__main__":
    main()
