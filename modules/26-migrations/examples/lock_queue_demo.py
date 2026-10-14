"""The lock queue: why a "fast" ALTER TABLE can take the site down (module 26, section 8.1).

Three sessions:
  A  a long transaction that has read products (holds ACCESS SHARE; think: a
     report, or an idle-in-transaction connection)
  B  the migration: ALTER TABLE products ADD COLUMN ... (needs ACCESS EXCLUSIVE,
     a catalog-only change that would take a millisecond)
  C  ordinary traffic: SELECT one product by id

B cannot get its lock while A holds one, so it queues. C's lock request is
compatible with A's but NOT with B's queued request, and PostgreSQL grants
locks in queue order: C waits behind B. Every new query on products now waits
for A to finish. With lock_timeout, B gives up instead and C runs.

    cd project/python && python ../../modules/26-migrations/examples/lock_queue_demo.py
"""

from __future__ import annotations

import threading
import time

import psycopg

from bazaar.config import get_settings

DSN = get_settings().dsn
HOLD = 3.0  # seconds session A keeps its transaction open


def run(lock_timeout: str) -> None:
    print(f"--- migration with lock_timeout = {lock_timeout}")
    a = psycopg.connect(DSN, application_name="A-report")
    a.execute("SELECT count(*) FROM products WHERE id < 100")  # transaction stays open

    result: dict[str, str] = {}

    def migration() -> None:
        with psycopg.connect(DSN, autocommit=True, application_name="B-migration") as b:
            b.execute("SELECT set_config('lock_timeout', %s, false)", (lock_timeout,))
            t0 = time.perf_counter()
            try:
                b.execute("ALTER TABLE products ADD COLUMN demo_note text")
                b.execute("ALTER TABLE products DROP COLUMN demo_note")
                result["B"] = f"done after {time.perf_counter() - t0:.2f}s"
            except psycopg.errors.LockNotAvailable as e:
                result["B"] = f"gave up after {time.perf_counter() - t0:.2f}s: {e}".strip()

    def traffic() -> None:
        with psycopg.connect(DSN, autocommit=True, application_name="C-traffic") as c:
            t0 = time.perf_counter()
            c.execute("SELECT title FROM products WHERE id = 42").fetchone()
            result["C"] = f"product page query took {time.perf_counter() - t0:.2f}s"

    tb = threading.Thread(target=migration)
    tb.start()
    time.sleep(0.3)
    tc = threading.Thread(target=traffic)
    tc.start()
    time.sleep(0.3)
    with psycopg.connect(DSN, autocommit=True) as mon:
        rows = mon.execute(
            """
            SELECT application_name, state, wait_event_type, wait_event,
                   pg_blocking_pids(pid) AS blocked_by, left(query, 45) AS query
            FROM pg_stat_activity
            WHERE application_name IN ('A-report', 'B-migration', 'C-traffic')
            ORDER BY application_name
            """
        ).fetchall()
        pids = {
            r[0]: r[1]
            for r in mon.execute(
                "SELECT pid, application_name FROM pg_stat_activity"
                " WHERE application_name IN ('A-report', 'B-migration', 'C-traffic')"
            )
        }
    for app, state, wtype, wevent, blocked_by, query in rows:
        names = [pids.get(p, str(p)) for p in blocked_by]
        print(f"  {app:<12} {state:<20} {wtype or '':<5} {wevent or '':<9} blocked_by={names} {query}")
    time.sleep(HOLD)
    a.commit()  # A finishes; the queue drains
    a.close()
    tb.join()
    tc.join()
    print(f"  B (migration): {result['B']}")
    print(f"  C (traffic):   {result['C']}")


if __name__ == "__main__":
    run("0")  # 0 = wait forever (the default)
    run("500ms")
