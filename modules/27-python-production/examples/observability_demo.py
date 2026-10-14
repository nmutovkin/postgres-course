"""Structured query logs, sqlcommenter tags, pool metrics and a trace (module 27, section 7).

    cd project/python && python ../../modules/27-python-production/examples/observability_demo.py

Simulates two "requests" against the reference database (read-only work).
"""

from __future__ import annotations

import logging
import uuid

import sqlalchemy as sa

from bazaar import observability as obs
from bazaar.sa.engine import create_engine


def main() -> None:
    obs.configure_logging("INFO", json_output=True)
    engine = create_engine(pool_size=3)
    stats = obs.instrument_engine(engine, slow_ms=25)
    obs.install_sqlcommenter(engine, static={"application": "bazaar"})
    log = logging.getLogger("bazaar.api")

    # Request 1: a product page (fast).
    with obs.sql_context(route="GET /products/{id}", request_id=uuid.uuid4().hex[:8]):
        with engine.connect() as c:
            c.execute(sa.text("SELECT title, price FROM products WHERE id = :id"), {"id": 42}).one()
        log.info("request done", extra={"status": 200})

    # Request 2: a seller report that is too slow.
    with obs.sql_context(route="GET /sellers/{id}/report", request_id=uuid.uuid4().hex[:8]):
        with engine.connect() as c:
            c.execute(
                sa.text(
                    "SELECT date_trunc('month', created_at) AS m, sum(total)"
                    " FROM orders WHERE seller_id = :s GROUP BY 1 ORDER BY 1"
                ),
                {"s": 1},
            ).all()
            # What the server sees while a statement runs (pg_stat_activity.query):
            seen = c.execute(
                sa.text("SELECT query FROM pg_stat_activity WHERE pid = pg_backend_pid()")
            ).scalar_one()
        log.info("request done", extra={"status": 200})

    print("\npg_stat_activity.query:", seen)
    print("pool:", obs.pool_metrics(engine))
    print(
        f"stats: {stats.count} statements, {stats.slow} slow, max {stats.max_ms:.0f} ms,"
        f" histogram {stats.histogram}"
    )
    engine.dispose()


if __name__ == "__main__":
    main()
