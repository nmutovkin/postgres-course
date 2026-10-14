"""Exercise 9: database time and statement count per request, in the request's
log line, and a "top routes by database time" report built from the logs.

    cd project/python && python ../../modules/27-python-production/solutions/ex09_request_db_time.py
"""

from __future__ import annotations

import io
import json
import logging
import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.orm import selectinload

from bazaar import observability as obs
from bazaar.orm import services
from bazaar.orm.models import Order
from bazaar.orm.session import make_sessionmaker
from bazaar.sa.engine import create_engine


@dataclass
class RequestStats:
    statements: int = 0
    db_ms: float = 0.0


_current: ContextVar[RequestStats | None] = ContextVar("request_stats", default=None)


def install(engine: sa.Engine) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def _start(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        conn.info.setdefault("req_t0", []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def _end(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        elapsed = (time.perf_counter() - conn.info["req_t0"].pop()) * 1000
        stats = _current.get()
        if stats is not None:  # outside a request (startup, a script): not counted
            stats.statements += 1
            stats.db_ms += elapsed


@contextmanager
def request(route: str, log: logging.Logger) -> Iterator[None]:
    stats = RequestStats()
    token = _current.set(stats)
    t0 = time.perf_counter()
    try:
        with obs.sql_context(route=route):
            yield
            log.info(
                "request done",
                extra={
                    "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "db_ms": round(stats.db_ms, 1),
                    "db_statements": stats.statements,
                },
            )
    finally:
        _current.reset(token)


def main() -> None:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(obs.JsonFormatter())
    log = logging.getLogger("bazaar.api")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False

    engine = create_engine()
    install(engine)
    Session = make_sessionmaker(engine)
    users = [8, 13, 11, 4021, 777]
    for uid in users:
        with request("GET /me/orders", log), Session() as s:  # the service: 2 statements
            for o in services.order_history(s, uid, limit=20):
                _ = [(i.product.title, i.quantity) for i in o.items]
        with request("GET /me/orders/v0", log), Session() as s:  # an older handler: lazy loads
            orders = s.scalars(
                sa.select(Order).where(Order.user_id == uid).order_by(Order.id.desc()).limit(20)
            ).all()
            for o in orders:
                _ = [(i.product.title, i.quantity) for i in o.items]
        with request("GET /products/{id}", log), Session() as s:
            s.scalars(
                sa.select(Order).options(selectinload(Order.items)).where(Order.id == uid * 10)
            ).all()
    engine.dispose()

    # The report: what an aggregation over the JSON logs (Loki, Elastic, ...) would do.
    per_route: dict[str, list[dict]] = defaultdict(list)
    for line in buf.getvalue().splitlines():
        rec = json.loads(line)
        per_route[rec["route"]].append(rec)
    print(f"{'route':<22} {'requests':>8} {'db ms total':>12} {'avg stmts':>10} {'max stmts':>10}")
    for route, recs in sorted(per_route.items(), key=lambda kv: -sum(r["db_ms"] for r in kv[1])):
        total = sum(r["db_ms"] for r in recs)
        stmts = [r["db_statements"] for r in recs]
        print(
            f"{route:<22} {len(recs):>8} {total:>12.1f} {sum(stmts) / len(stmts):>10.1f}"
            f" {max(stmts):>10}"
        )
    print("one log line:", buf.getvalue().splitlines()[1])


if __name__ == "__main__":
    main()
