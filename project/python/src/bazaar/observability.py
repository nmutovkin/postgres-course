"""Seeing what the application does to the database (module 27).

    from bazaar import observability as obs

    obs.configure_logging(json_output=True)          # one JSON object per log line
    stats = obs.instrument_engine(engine, slow_ms=200)   # timings, slow-query log, counters
    obs.install_sqlcommenter(engine)                  # /*route='...',request_id='...'*/
    with obs.sql_context(route="POST /checkout", request_id=rid):
        ...                                           # every statement carries these tags
    obs.pool_metrics(engine)                          # {"size": 10, "checked_out": 3, ...}
    obs.check_health(conn)                            # readiness: reachable, primary, migrated
    obs.setup_tracing("bazaar", engine=engine)        # OpenTelemetry spans (optional extra)

Everything here hangs off SQLAlchemy's engine events (module 24, section 9.3),
so it sees every statement the Core, the ORM and Alembic send.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any
from urllib.parse import quote

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from bazaar.resilience import classify, sqlstate

log = logging.getLogger("bazaar.sql")

# --- structured logging ---------------------------------------------------------------------

_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: easy to ship, index and query (Loki, ELK, CloudWatch).

    Fields passed with `extra={...}` become top-level keys, so
    log.warning("slow query", extra={"duration_ms": 812}) is searchable by duration.
    """

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        out.update(_sql_context.get())  # request_id, route, ... of the current request
        for k, v in record.__dict__.items():
            if k not in _STANDARD and not k.startswith("_"):
                out[k] = v
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def configure_logging(level: str | int = "INFO", *, json_output: bool = True) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter() if json_output else logging.Formatter("%(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


# --- request context, and sqlcommenter-style comments ------------------------------------

_sql_context: ContextVar[Mapping[str, str]] = ContextVar(
    "bazaar_sql_context", default=MappingProxyType({})
)


@contextmanager
def sql_context(**tags: Any) -> Iterator[Mapping[str, str]]:
    """Tags for everything done in this block (this task / thread only).

    A ContextVar, so concurrent asyncio tasks (requests) each see their own
    tags. Nested blocks add to the outer tags.
    """
    merged = {**_sql_context.get(), **{k: str(v) for k, v in tags.items() if v is not None}}
    token = _sql_context.set(merged)
    try:
        yield merged
    finally:
        _sql_context.reset(token)


def current_context() -> dict[str, str]:
    return dict(_sql_context.get())


def sqlcomment(tags: Mapping[str, str]) -> str:
    """Render tags in the sqlcommenter format: /*key='value',key2='value2'*/

    Keys sorted; keys and values URL-encoded, so a value can never contain
    '*/' and close the comment early (that would be SQL injection through a
    request header).
    """
    if not tags:
        return ""
    parts = [f"{quote(k, safe='')}='{quote(str(v), safe='')}'" for k, v in sorted(tags.items())]
    return "/*" + ",".join(parts) + "*/"


def install_sqlcommenter(
    engine: Engine | AsyncEngine, static: dict[str, str] | None = None
) -> None:
    """Append the current sql_context() tags (plus `static` ones) to every statement.

    The comment shows up wherever the query text does: pg_stat_activity, the
    server log (log_min_duration_statement, auto_explain, lock waits). It does
    NOT split pg_stat_statements entries: the query id ignores comments, and the
    stored text is the first one seen. Cost: the statement text changes per
    request, so driver-side prepared-statement caches (asyncpg, psycopg's
    prepare_threshold) see "new" queries; keep the tags low-cardinality
    (route, not request_id) if you rely on those caches.
    """
    target = engine.sync_engine if isinstance(engine, AsyncEngine) else engine
    fixed = dict(static or {})

    # psycopg uses %s placeholders: a literal '%' (from the URL encoding) must be
    # doubled, or the driver reads '%2F' as a broken placeholder.
    escape = target.dialect.paramstyle in ("format", "pyformat")

    @event.listens_for(target, "before_cursor_execute", retval=True)
    def _comment(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        tags = {**fixed, **_sql_context.get()}
        if not tags:
            return statement, parameters
        comment = sqlcomment(tags)
        return f"{statement} {comment.replace('%', '%%') if escape else comment}", parameters


# --- timings, slow queries, counters ---------------------------------------------------------

BUCKETS_MS = (1, 5, 10, 50, 100, 500, 1000, 5000)


@dataclass
class QueryStats:
    """In-process counters, cheap enough to keep always on. Export them to your
    metrics system (Prometheus, StatsD) from a periodic task or a /metrics route."""

    count: int = 0
    errors: int = 0
    slow: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    histogram: dict[str, int] = field(default_factory=lambda: dict.fromkeys(_labels(), 0))

    def observe(self, ms: float, slow_ms: float) -> None:
        self.count += 1
        self.total_ms += ms
        self.max_ms = max(self.max_ms, ms)
        if ms >= slow_ms:
            self.slow += 1
        for bound, label in zip(BUCKETS_MS, _labels(), strict=False):
            if ms <= bound:
                self.histogram[label] += 1
                break
        else:
            self.histogram[f">{BUCKETS_MS[-1]}"] += 1


def _strip_comment(statement: str) -> str:
    if statement.endswith("*/") and " /*" in statement:
        return statement.rsplit(" /*", 1)[0]
    return statement


def _labels() -> list[str]:
    return [f"<={b}" for b in BUCKETS_MS] + [f">{BUCKETS_MS[-1]}"]


def instrument_engine(
    engine: Engine | AsyncEngine,
    *,
    slow_ms: float = 200.0,
    log_all: bool = False,
    stats: QueryStats | None = None,
    logger: logging.Logger = log,
) -> QueryStats:
    """Time every statement; log slow ones (WARNING) and failures (ERROR).

    log_all=True logs every statement at DEBUG (development only: volume, and
    parameter values may be personal data, so they are never logged; only
    their count is).
    """
    target = engine.sync_engine if isinstance(engine, AsyncEngine) else engine
    stats = stats if stats is not None else QueryStats()

    @event.listens_for(target, "before_cursor_execute")
    def _start(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        conn.info.setdefault("bazaar_t0", []).append(time.perf_counter())

    @event.listens_for(target, "after_cursor_execute")
    def _end(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        ms = (time.perf_counter() - conn.info["bazaar_t0"].pop()) * 1000
        stats.observe(ms, slow_ms)
        fields = {
            "duration_ms": round(ms, 2),
            "statement": _strip_comment(statement)[:500],  # the tags are fields already
            "rowcount": getattr(cursor, "rowcount", None),
            "params": len(parameters) if hasattr(parameters, "__len__") else None,
            "executemany": executemany,
        }
        if ms >= slow_ms:
            logger.warning("slow query", extra=fields)
        elif log_all:
            logger.debug("query", extra=fields)

    @event.listens_for(target, "handle_error")
    def _error(ctx) -> None:  # noqa: ANN001
        stack = ctx.connection.info.get("bazaar_t0") if ctx.connection is not None else None
        ms = (time.perf_counter() - stack.pop()) * 1000 if stack else None
        stats.errors += 1
        exc = ctx.original_exception
        logger.error(
            "query failed",
            extra={
                "duration_ms": round(ms, 2) if ms is not None else None,
                "statement": (ctx.statement or "")[:500],
                "sqlstate": sqlstate(exc),
                "error_kind": str(classify(exc)),
                "error": str(exc).splitlines()[0] if str(exc) else type(exc).__name__,
            },
        )

    return stats


# --- pool metrics --------------------------------------------------------------------------


def pool_metrics(pool_or_engine: Any) -> dict[str, Any]:
    """Current pool usage for SQLAlchemy engines, psycopg_pool and asyncpg pools.

    The number to alert on: checked_out (in use) close to size for long, or any
    requests waiting. A pool that is always exhausted means connections are
    held too long (slow queries, transactions spanning slow calls) more often
    than it means the pool is too small.
    """
    if isinstance(pool_or_engine, AsyncEngine):
        pool_or_engine = pool_or_engine.sync_engine
    if isinstance(pool_or_engine, Engine):
        pool = pool_or_engine.pool
        if not hasattr(pool, "checkedout"):  # NullPool
            return {"kind": type(pool).__name__}
        return {
            "kind": type(pool).__name__,
            "size": pool.size(),
            "checked_out": pool.checkedout(),
            "idle": pool.checkedin(),
            "overflow": pool.overflow(),
        }
    if hasattr(pool_or_engine, "get_stats"):  # psycopg_pool
        s = pool_or_engine.get_stats()
        return {
            "kind": type(pool_or_engine).__name__,
            "size": s.get("pool_size"),
            "idle": s.get("pool_available"),
            "checked_out": s.get("pool_size", 0) - s.get("pool_available", 0),
            "waiting": s.get("requests_waiting", 0),
            "errors": s.get("connections_errors", 0),
            "timeouts": s.get("requests_errors", 0),
        }
    if hasattr(pool_or_engine, "get_idle_size"):  # asyncpg
        size = pool_or_engine.get_size()
        idle = pool_or_engine.get_idle_size()
        return {"kind": "asyncpg.Pool", "size": size, "idle": idle, "checked_out": size - idle}
    raise TypeError(f"unknown pool type {type(pool_or_engine).__name__}")


# --- health checks ---------------------------------------------------------------------------


def check_health(
    conn: sa.Connection, *, require_primary: bool = True, check_migrations: bool = True
) -> dict[str, Any]:
    """Readiness of the database *for this service*, not just "it answers".

    * reachable: SELECT 1 round trip (latency reported);
    * primary: after a failover (module 20) a pool may still point at a node
      that is now a standby; writes would fail with 25006;
    * schema at head: the code and the migrations agree (module 26).

    For AsyncConnection: `await conn.run_sync(check_health)`.
    """
    from bazaar import migrate

    t0 = time.perf_counter()
    in_recovery = conn.execute(sa.text("SELECT pg_is_in_recovery()")).scalar_one()
    out: dict[str, Any] = {
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        "in_recovery": in_recovery,
    }
    problems = []
    if require_primary and in_recovery:
        problems.append("connected to a standby")
    if check_migrations:
        current, heads = migrate.current_revisions(conn), migrate.head_revisions()
        out["schema"] = list(current)
        if set(current) != set(heads):
            problems.append(f"schema at {list(current)}, code expects {list(heads)}")
    out["ok"] = not problems
    out["problems"] = problems
    return out


# --- OpenTelemetry (optional: pip install bazaar[otel]) -----------------------------------------


def setup_tracing(
    service_name: str = "bazaar",
    *,
    engine: Engine | AsyncEngine | None = None,
    exporter: Any = None,
    commenter: bool = True,
) -> Any:
    """Send spans for every statement to `exporter` (default: the console).

    With commenter=True the instrumentation also appends a traceparent comment
    (/*traceparent='00-<trace id>-<span id>-01'*/) to each statement, which
    links a slow-query log line on the server to the trace of the request
    that sent it. Returns the TracerProvider (call .shutdown() at exit).
    """
    from opentelemetry import trace
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(SimpleSpanProcessor(exporter or ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    if engine is not None:
        target = engine.sync_engine if isinstance(engine, AsyncEngine) else engine
        SQLAlchemyInstrumentor().instrument(
            engine=target,
            tracer_provider=provider,
            enable_commenter=commenter,
            # The instrumentation declares support for SQLAlchemy < 2.1 only; the
            # engine events it uses are unchanged in 2.1, so skip the version check.
            skip_dep_check=True,
        )
    return provider
