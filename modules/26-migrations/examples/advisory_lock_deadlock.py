"""Why env.py polls pg_try_advisory_lock() instead of waiting in pg_advisory_lock() (section 10).

Run 1 holds the migration lock and builds an index CONCURRENTLY. Run 2 waits
for the migration lock *inside* SELECT pg_advisory_lock(): a statement with a
snapshot. CREATE INDEX CONCURRENTLY waits for all older snapshots to finish;
run 2's snapshot never finishes, because run 2 waits for run 1. PostgreSQL's
deadlock detector breaks the cycle after deadlock_timeout (1 s).

    cd project/python && python ../../modules/26-migrations/examples/advisory_lock_deadlock.py

Uses a scratch table in the bazaar database and drops it afterwards.
"""

from __future__ import annotations

import threading
import time

import psycopg

from bazaar.config import get_settings

DSN = get_settings().dsn
KEY = "demo.migrations"


def main() -> None:
    with psycopg.connect(DSN, autocommit=True) as setup:
        setup.execute("DROP TABLE IF EXISTS demo_cic")
        setup.execute("CREATE TABLE demo_cic AS SELECT g AS n FROM generate_series(1, 100000) g")

    run1 = psycopg.connect(DSN, autocommit=True)
    run1.execute("SELECT pg_advisory_lock(hashtext(%s))", (KEY,))
    outcome: dict[str, str] = {}

    def run2() -> None:
        with psycopg.connect(DSN, autocommit=True) as c:
            try:
                c.execute("SELECT pg_advisory_lock(hashtext(%s))", (KEY,))  # waits...
                outcome["run 2"] = "got the lock"
            except psycopg.Error as e:
                outcome["run 2"] = f"{type(e).__name__}: {e}"

    t = threading.Thread(target=run2)
    t.start()
    time.sleep(0.5)
    try:
        run1.execute("CREATE INDEX CONCURRENTLY demo_cic_n_idx ON demo_cic (n)")
        outcome["run 1"] = "index built"
    except psycopg.Error as e:
        outcome["run 1"] = f"{type(e).__name__}: {e}"
    run1.execute("SELECT pg_advisory_unlock_all()")
    t.join()
    run1.execute("DROP TABLE demo_cic")
    run1.close()
    for k, v in sorted(outcome.items()):
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
