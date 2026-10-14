"""Exercise 2: GET /metrics in the Prometheus text format.

Pool usage (per engine) and the per-engine query counters that
bazaar.observability.instrument_engine() keeps. No database call: scraping
metrics must work (and be cheap) when the database is the thing in trouble.
Every uvicorn worker has its own pools and counters, so the scraper sees one
process per scrape; label by pid (or use prometheus_client's multiprocess mode).
"""

from __future__ import annotations

import os

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from bazaar.api.db import DB
from bazaar.observability import BUCKETS_MS

router = APIRouter()


def _line(name: str, labels: dict[str, object], value: float) -> str:
    rendered = ",".join(f'{k}="{v}"' for k, v in labels.items())
    return f"{name}{{{rendered}}} {value}"


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(db: DB) -> str:
    pid = os.getpid()
    out = [
        "# HELP bazaar_pool_connections Connections of the engine's pool by state.",
        "# TYPE bazaar_pool_connections gauge",
    ]
    for engine, m in db.pools().items():
        for state in ("checked_out", "idle"):
            labels = {"engine": engine, "state": state, "pid": pid}
            out.append(_line("bazaar_pool_connections", labels, m.get(state, 0)))
        out.append(_line("bazaar_pool_size", {"engine": engine, "pid": pid}, m.get("size", 0)))
    out += [
        "# HELP bazaar_sql_duration_ms Statement durations seen by the application.",
        "# TYPE bazaar_sql_duration_ms histogram",
    ]
    for engine, s in db.stats.items():
        labels = {"engine": engine, "pid": pid}
        cumulative = 0
        for bound in BUCKETS_MS:
            cumulative += s.histogram[f"<={bound}"]
            out.append(_line("bazaar_sql_duration_ms_bucket", {**labels, "le": bound}, cumulative))
        out.append(_line("bazaar_sql_duration_ms_bucket", {**labels, "le": "+Inf"}, s.count))
        out.append(_line("bazaar_sql_duration_ms_sum", labels, round(s.total_ms, 3)))
        out.append(_line("bazaar_sql_duration_ms_count", labels, s.count))
        out.append(_line("bazaar_sql_errors_total", labels, s.errors))
        out.append(_line("bazaar_sql_slow_total", labels, s.slow))
    return "\n".join(out) + "\n"
