"""Exercise 6: run the review_count revision on the reference database with
concurrent reviews being written, for several batch sizes; check the counts.

    cd project/python && python ../../modules/26-migrations/solutions/ex06_measure.py
"""

from __future__ import annotations

import os
import random
import sys
import threading
import time
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from scratch_alembic import scratch_migrations  # noqa: E402

from bazaar.config import get_settings  # noqa: E402

DSN = get_settings().dsn
MARK = "ex06-concurrent"


def writer(stop: threading.Event, latencies: list[float]) -> None:
    """New reviews of random products by random users, while the backfill runs."""
    with psycopg.connect(DSN, autocommit=True) as c:
        while not stop.is_set():
            pid, uid = random.randint(1, 200_000), random.randint(1, 100_000)
            t0 = time.perf_counter()
            c.execute(
                "INSERT INTO reviews (product_id, user_id, rating, title)"
                " VALUES (%s, %s, 5, %s) ON CONFLICT DO NOTHING",
                (pid, uid, MARK),
            )
            latencies.append(time.perf_counter() - t0)
            time.sleep(0.003)


def main() -> None:
    with scratch_migrations(HERE / "ex06_2605_review_count.py") as mig:
        for batch in (1_000, 10_000, 50_000):
            os.environ["EX06_BATCH"] = str(batch)
            stop, latencies = threading.Event(), []
            t = threading.Thread(target=writer, args=(stop, latencies))
            t.start()
            try:
                mig.upgrade(DSN, "2605")
            finally:
                stop.set()
                t.join()
            with psycopg.connect(DSN) as c:
                total, counted, written = c.execute(
                    "SELECT (SELECT sum(review_count) FROM products), (SELECT count(*) FROM reviews),"
                    " (SELECT count(*) FROM reviews WHERE title = %s)",
                    (MARK,),
                ).fetchone()
            lat = sorted(x * 1000 for x in latencies)
            print(
                f"  batch {batch:>6}: {len(lat)} concurrent inserts ({written} new reviews),"
                f" p99 {lat[int(len(lat) * 0.99)]:.0f} ms, worst {lat[-1]:.0f} ms;"
                f" sum(review_count) = {total}, count(reviews) = {counted}"
            )
            with psycopg.connect(DSN) as c:  # the trigger decrements on delete
                c.execute("DELETE FROM reviews WHERE title = %s", (MARK,))
                c.commit()
            mig.downgrade(DSN, "2701")


if __name__ == "__main__":
    main()
