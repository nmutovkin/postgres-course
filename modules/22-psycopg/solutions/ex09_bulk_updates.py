"""Exercise 9: 5,000 price changes, four ways (each rolled back)."""

import os
import time
from decimal import Decimal

import psycopg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
UPDATE = "UPDATE products SET price = %s WHERE id = %s"


def loop(conn, changes):
    for price, pid in changes:
        conn.execute(UPDATE, (price, pid))


def pipeline(conn, changes):
    with conn.pipeline():
        for price, pid in changes:
            conn.execute(UPDATE, (price, pid))


def executemany(conn, changes):
    with conn.cursor() as cur:
        cur.executemany(UPDATE, changes)


def unnest(conn, changes):
    prices, ids = zip(*changes, strict=True)
    conn.execute(
        """UPDATE products p SET price = c.price
           FROM unnest(%s::numeric[], %s::bigint[]) AS c (price, id)
           WHERE p.id = c.id""",
        (list(prices), list(ids)),
    )


if __name__ == "__main__":
    with psycopg.connect(DSN, autocommit=True) as conn:
        changes = [
            (price * Decimal("1.05"), pid)
            for pid, price in conn.execute(
                "SELECT id, price FROM products WHERE id % 40 = 0 ORDER BY id"
            ).fetchall()
        ]
        print(f"{len(changes)} updates")
        for fn in (loop, pipeline, executemany, unnest):
            for _ in range(2):  # keep the second (warm) run
                t = time.perf_counter()
                with conn.transaction(force_rollback=True):
                    fn(conn, changes)
                    n = conn.execute(
                        "SELECT count(*) FROM audit_log WHERE changed_at = now()"
                    ).fetchone()[0]
                dt = time.perf_counter() - t
            print(f"{fn.__name__:12s} {dt:6.2f}s  audit rows written: {n}")
