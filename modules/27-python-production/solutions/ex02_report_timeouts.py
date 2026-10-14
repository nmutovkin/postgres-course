"""Exercise 2: per-transaction timeouts for a slow endpoint, PgBouncer-safe.

    cd project/python && python ../../modules/27-python-production/solutions/ex02_report_timeouts.py

The seller report may run for up to 2 s on the server and may wait at most
0.5 s for a lock. Everything else keeps the connection's defaults (5 s, no
lock timeout).
"""

from __future__ import annotations

import concurrent.futures
import time
from datetime import date

import psycopg
import sqlalchemy as sa

from bazaar.config import get_settings
from bazaar.resilience import classify
from bazaar.sa import reports
from bazaar.sa.engine import create_engine


def seller_report(
    engine: sa.Engine,
    seller_id: int,
    start: date,
    end: date,
    *,
    statement_timeout: str = "2s",
    lock_timeout: str = "500ms",
):  # noqa: ANN201
    with engine.begin() as conn:
        # SET LOCAL through set_config(..., true): ends with the transaction, so it
        # never leaks to the next user of the pooled (or PgBouncer) connection.
        conn.execute(
            sa.text(
                "SELECT set_config('statement_timeout', :st, true),"
                " set_config('lock_timeout', :lt, true)"
            ),
            {"st": statement_timeout, "lt": lock_timeout},
        )
        return reports.seller_statement(conn, seller_id, start, end)


def timed(label: str, fn) -> None:  # noqa: ANN001
    t0 = time.perf_counter()
    try:
        out = fn()
        print(f"{label:<44} ok after {time.perf_counter() - t0:.2f}s ({len(out.lines)} lines)")
    except Exception as e:  # noqa: BLE001
        orig = getattr(e, "orig", e)
        print(
            f"{label:<44} {type(orig).__name__} sqlstate={getattr(orig, 'sqlstate', None)}"
            f" kind={classify(e)} after {time.perf_counter() - t0:.2f}s"
        )


def main() -> None:
    engine = create_engine()
    month = (date(2025, 11, 1), date(2025, 12, 1))
    span = (date(2023, 1, 1), date(2026, 1, 1))
    timed("seller 1, November 2025", lambda: seller_report(engine, 1, *month))
    timed(
        "seller 1, three years, limit 0.5 s",
        lambda: seller_report(engine, 1, *span, statement_timeout="500ms"),
    )

    # A migration holds ACCESS EXCLUSIVE on orders (an ALTER TABLE in an open transaction).
    blocker = psycopg.connect(get_settings().dsn)
    blocker.execute("LOCK TABLE orders IN ACCESS EXCLUSIVE MODE")
    try:
        timed("seller 1, November, orders locked", lambda: seller_report(engine, 1, *month))
    finally:
        blocker.rollback()
        blocker.close()

    # The caller's own deadline (0.5 s here) on the three-year report (allowed
    # 10 s on the server). In sync code a deadline can only stop *waiting*: the
    # thread and the server keep working (compare asyncio + psycopg, which
    # sends a cancel request, module 27 section 4).
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        t0 = time.perf_counter()
        future = ex.submit(seller_report, engine, 1, *span, statement_timeout="10s")
        timed("caller deadline 0.5 s, three years", lambda: future.result(timeout=0.5))
        with psycopg.connect(get_settings().dsn) as mon:
            n = mon.execute(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE state = 'active' AND application_name = 'bazaar'"
                " AND backend_type = 'client backend'"
            ).fetchone()[0]
        print(f"{'':<44} still running on the server: {n}")
        future.result()
        print(
            f"{'':<44} ...and finished anyway after {time.perf_counter() - t0:.2f}s"
            " (result thrown away)"
        )

    with engine.connect() as conn:  # the defaults are back on this pooled connection
        print(
            "afterwards:",
            conn.execute(sa.text("SHOW statement_timeout")).scalar_one(),
            "/",
            conn.execute(sa.text("SHOW lock_timeout")).scalar_one(),
        )
    engine.dispose()


if __name__ == "__main__":
    main()
