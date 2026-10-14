"""Module 22, section 11: pipeline mode.

1) Benchmark: 2,000 independent lookups, one by one vs pipelined.
2) A libpq protocol trace of three statements in a pipeline (see trace file).
"""

import os
import tempfile
import time

import psycopg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
LOOKUP = "SELECT quantity - reserved FROM inventory WHERE product_id = %s"
IDS = list(range(1, 200_001, 100))  # 2,000 products

with psycopg.connect(DSN, autocommit=True) as conn:
    for attempt in range(2):  # second round = warm cache
        t = time.perf_counter()
        one_by_one = [conn.execute(LOOKUP, (pid,)).fetchone()[0] for pid in IDS]
        t1 = time.perf_counter() - t

        t = time.perf_counter()
        with conn.pipeline():
            curs = [conn.execute(LOOKUP, (pid,)) for pid in IDS]  # sent, not yet awaited
        pipelined = [c.fetchone()[0] for c in curs]  # results arrived at pipeline exit
        t2 = time.perf_counter() - t
    assert one_by_one == pipelined
    print(f"{len(IDS)} lookups  one by one: {t1 * 1000:6.0f} ms   pipelined: {t2 * 1000:6.0f} ms"
          f"   speed-up x{t1 / t2:.1f}")  # fmt: skip

    # --- what goes over the wire --------------------------------------------------
    with tempfile.NamedTemporaryFile("w+", suffix=".trace") as f:
        conn.pgconn.trace(f.fileno())
        conn.pgconn.set_trace_flags(psycopg.pq.Trace.SUPPRESS_TIMESTAMPS)
        with conn.pipeline():
            a = conn.execute(LOOKUP, (16,))
            b = conn.execute(LOOKUP, (47,))
            c = conn.execute("SELECT count(*) FROM sellers")
        conn.pgconn.untrace()
        f.seek(0)
        print(f.read())
    print(a.fetchone(), b.fetchone(), c.fetchone())
