"""Module 27: logging, query timing, sqlcommenter, pool metrics, health, tracing,
the read-only engine."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest
import sqlalchemy as sa

from bazaar import observability as obs
from bazaar.config import get_settings
from bazaar.sa.engine import NullPool, create_engine, create_read_engine


@pytest.fixture
def engine() -> Iterator[sa.Engine]:
    e = create_engine(pool_size=2)
    yield e
    e.dispose()


def test_sqlcomment_format() -> None:
    assert obs.sqlcomment({}) == ""
    assert obs.sqlcomment({"route": "POST /checkout", "app": "bazaar"}) == (
        "/*app='bazaar',route='POST%20%2Fcheckout'*/"
    )
    # A hostile value cannot close the comment and append SQL.
    evil = obs.sqlcomment({"route": "x*/; DROP TABLE users; --"})
    assert "*/" not in evil[2:-2] and ";" not in evil


def test_sql_context_nests_and_resets() -> None:
    with obs.sql_context(request_id="r1", route="GET /a"):
        with obs.sql_context(route="GET /b"):
            assert obs.current_context() == {"request_id": "r1", "route": "GET /b"}
        assert obs.current_context()["route"] == "GET /a"
    assert obs.current_context() == {}


def test_sqlcommenter_reaches_pg_stat_activity(engine: sa.Engine) -> None:
    obs.install_sqlcommenter(engine, static={"application": "bazaar"})
    with engine.connect() as c, obs.sql_context(route="GET /products/{id}"):
        query = c.execute(
            sa.text("SELECT query FROM pg_stat_activity WHERE pid = pg_backend_pid()")
        ).scalar_one()
    assert query.endswith("/*application='bazaar',route='GET%20%2Fproducts%2F%7Bid%7D'*/")


def test_slow_and_failed_queries_are_logged(
    engine: sa.Engine, caplog: pytest.LogCaptureFixture
) -> None:
    stats = obs.instrument_engine(engine, slow_ms=50)
    caplog.set_level(logging.WARNING, logger="bazaar.sql")
    with engine.connect() as c:
        c.execute(sa.text("SELECT 1"))
        c.execute(sa.text("SELECT pg_sleep(0.08)"))
        with pytest.raises(sa.exc.DBAPIError):
            c.execute(sa.text("SELECT * FROM no_such_table"))
    slow = [r for r in caplog.records if r.getMessage() == "slow query"]
    failed = [r for r in caplog.records if r.getMessage() == "query failed"]
    assert len(slow) == 1 and slow[0].duration_ms >= 80 and "pg_sleep" in slow[0].statement
    assert len(failed) == 1 and failed[0].sqlstate == "42P01"
    assert failed[0].error_kind == "permanent"
    assert stats.slow == 1 and stats.errors == 1 and stats.count >= 2
    assert sum(stats.histogram.values()) == stats.count


def test_json_log_lines_carry_context(capsys: pytest.CaptureFixture) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(obs.JsonFormatter())
    logger = logging.getLogger("bazaar.test.json")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        with obs.sql_context(request_id="req-42"):
            logger.warning("slow query", extra={"duration_ms": 812.5})
    finally:
        logger.removeHandler(handler)
    line = json.loads(capsys.readouterr().err.strip())
    assert line["msg"] == "slow query" and line["level"] == "WARNING"
    assert line["duration_ms"] == 812.5 and line["request_id"] == "req-42"


def test_pool_metrics(engine: sa.Engine) -> None:
    from bazaar import db

    with engine.connect():
        m = obs.pool_metrics(engine)
    assert m["kind"] == "QueuePool" and m["checked_out"] == 1 and m["size"] == 2
    assert obs.pool_metrics(create_engine(poolclass=NullPool)) == {"kind": "NullPool"}
    pool = db.create_pool(min_size=1, max_size=2)
    try:
        with pool.connection():
            m = obs.pool_metrics(pool)
        assert m["checked_out"] == 1 and m["waiting"] == 0
    finally:
        pool.close()


def test_health_check(sa_conn: sa.Connection, fresh_db: str) -> None:
    h = obs.check_health(sa_conn)
    assert h["ok"] and not h["in_recovery"] and h["schema"] == ["2701"]
    stale = sa.create_engine(fresh_db.replace("postgresql://", "postgresql+psycopg://"))
    with stale.connect() as c:
        c.execute(sa.text("UPDATE alembic_version SET version_num = '2604'"))
        h = obs.check_health(c)
    stale.dispose()
    assert not h["ok"] and "code expects ['2701']" in h["problems"][0]


def test_read_engine_refuses_writes() -> None:
    engine = create_read_engine()
    try:
        with engine.connect() as c:
            assert c.execute(sa.text("SHOW default_transaction_read_only")).scalar_one() == "on"
            assert c.execute(sa.text("SELECT count(*) FROM users WHERE id < 10")).scalar_one()
            with pytest.raises(sa.exc.DBAPIError) as info:
                c.execute(sa.text("UPDATE users SET full_name = full_name WHERE id = 1"))
            assert info.value.orig.sqlstate == "25006"  # read_only_sql_transaction
        assert engine.url.database == "bazaar"  # no BAZAAR_REPLICA_DSN: the primary
        assert get_settings().replica_dsn is None
    finally:
        engine.dispose()


def test_opentelemetry_spans_and_traceparent(engine: sa.Engine) -> None:
    pytest.importorskip("opentelemetry.instrumentation.sqlalchemy")
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = obs.setup_tracing("bazaar-test", engine=engine, exporter=exporter)
    try:
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("GET /products/42"), engine.connect() as c:
            c.execute(sa.text("SELECT title FROM products WHERE id = 42")).scalar_one()
            seen = c.execute(
                sa.text("SELECT query FROM pg_stat_activity WHERE pid = pg_backend_pid()")
            ).scalar_one()
    finally:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().uninstrument()
        provider.shutdown()
    spans = exporter.get_finished_spans()
    db_spans = [s for s in spans if s.attributes.get("db.system") == "postgresql"]
    root = next(s for s in spans if s.name == "GET /products/42")
    assert len(db_spans) >= 2
    assert all(s.parent.span_id == root.context.span_id for s in db_spans)
    assert "traceparent=" in seen and format(root.context.trace_id, "032x") in seen
