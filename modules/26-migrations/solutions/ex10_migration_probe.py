"""Exercise 10: run a revision against the large reference database under traffic,
and prove from pg_locks how long writers could have been blocked.

    cd project/python
    python ../../modules/26-migrations/solutions/ex10_migration_probe.py ex10_naive_2605.py
    python ../../modules/26-migrations/solutions/ex10_migration_probe.py ex10_online_2605.py

While `alembic upgrade 2605` runs (on a scratch copy of the migrations plus the
given revision file), two threads watch:

* traffic: SELECT + UPDATE of a random order by primary key, in a loop,
  recording each round trip's latency;
* sampler: every 2 ms, the locks of the migration's backend (found through
  application_name = 'bazaar-migrations') on `orders`. Lock modes that conflict
  with ROW EXCLUSIVE (what INSERT/UPDATE/DELETE need) are "write-blocking";
  the longest uninterrupted run of samples in which the migration held one is
  the bound on how long any writer could have waited for it.

Afterwards the revision is downgraded again (back to 2701). Exit status 1 if
the write-blocking time exceeded --max-ms.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
import threading
import time
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from scratch_alembic import scratch_migrations  # noqa: E402

from bazaar.config import get_settings  # noqa: E402

WRITE_BLOCKING = {"ShareLock", "ShareRowExclusiveLock", "ExclusiveLock", "AccessExclusiveLock"}

SAMPLE = """
SELECT l.mode, l.granted
FROM pg_locks l
JOIN pg_stat_activity a ON a.pid = l.pid
WHERE a.application_name = 'bazaar-migrations'
  AND l.locktype = 'relation' AND l.relation = 'bazaar.orders'::regclass
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("revision_file")
    ap.add_argument("--max-ms", type=float, default=50.0)
    args = ap.parse_args()
    dsn = get_settings().dsn
    stop = threading.Event()
    latencies: list[float] = []
    samples: list[tuple[float, bool, set[str]]] = []  # (time, migration waiting?, modes held)

    with psycopg.connect(dsn, autocommit=True) as c:
        lo, hi = c.execute("SELECT min(id), max(id) FROM orders").fetchone()

    def traffic() -> None:
        with psycopg.connect(dsn, autocommit=True) as c:
            while not stop.is_set():
                k = random.randint(lo, hi)
                t0 = time.perf_counter()
                c.execute("SELECT status FROM orders WHERE id = %s", (k,))
                c.execute("UPDATE orders SET updated_at = updated_at WHERE id = %s", (k,))
                latencies.append(time.perf_counter() - t0)
                time.sleep(0.002)

    def sampler() -> None:
        with psycopg.connect(dsn, autocommit=True) as c:
            while not stop.is_set():
                rows = c.execute(SAMPLE).fetchall()
                held = {m for m, g in rows if g}
                waiting = any(not g for _, g in rows)
                samples.append((time.perf_counter(), waiting, held))
                time.sleep(0.002)

    threads = [threading.Thread(target=traffic), threading.Thread(target=sampler)]
    with scratch_migrations(HERE / args.revision_file) as mig:
        for t in threads:
            t.start()
        time.sleep(0.3)
        t0 = time.perf_counter()
        try:
            mig.upgrade(dsn, "2605")
            took = time.perf_counter() - t0
        finally:
            time.sleep(0.1)
            stop.set()
            for t in threads:
                t.join()
            mig.downgrade(dsn, "2701")

    # Longest run of consecutive samples with a write-blocking lock held.
    longest = current = 0.0
    prev_t = None
    for ts, _, held in samples:
        if held & WRITE_BLOCKING:
            current += (ts - prev_t) if prev_t is not None else 0.0
            longest = max(longest, current)
        else:
            current = 0.0
        prev_t = ts
    modes = sorted({m for _, _, h in samples for m in h})
    lat_ms = sorted(x * 1000 for x in latencies)
    print(f"revision {args.revision_file}: upgrade took {took:.1f}s")
    print(f"  lock modes the migration held on orders: {modes}")
    print(f"  longest write-blocking lock hold (sampled every ~2 ms): {longest * 1000:.0f} ms")
    print(
        f"  traffic: {len(lat_ms)} round trips, median {statistics.median(lat_ms):.1f} ms,"
        f" p99 {lat_ms[int(len(lat_ms) * 0.99)]:.1f} ms, worst {lat_ms[-1]:.0f} ms"
    )
    ok = longest * 1000 <= args.max_ms
    print(f"  {'PASS' if ok else 'FAIL'}: writers blocked at most {args.max_ms:.0f} ms")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
