"""Exercise 8: OpenTelemetry, from the HTTP request down to each SQL statement.

    exporter = OTLPSpanExporter(...)            # or InMemorySpanExporter() in a test
    provider = make_provider(exporter)
    app = create_app(telemetry={"tracer_provider": provider})
    instrument_engines(app, provider)           # before the app starts

Two halves:

* HTTP: FastAPI (0.143 here) has OpenTelemetry built in. Given a tracer
  provider (globally, or with telemetry={"tracer_provider": ...}) it opens a
  SERVER span per request, named by method and route template
  ("GET /products/{product_id}"), with INTERNAL child spans for dependency
  resolution, the endpoint and serialization. It also continues a trace whose
  `traceparent` header the caller sent. (With an older FastAPI, a 30-line ASGI
  middleware or opentelemetry-instrumentation-fastapi does the same.)
* SQL: the engines are created in the lifespan, so they are instrumented
  there: one CLIENT span per statement (opentelemetry-instrumentation-sqlalchemy,
  module 27), a child of whatever span is current, i.e. of the request. With
  enable_commenter=True each statement also carries traceparent='00-<trace>-
  <span>-01' in a comment, so a line in the server's slow-query log leads to
  the trace of the request that sent it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor


def make_provider(exporter: Any) -> TracerProvider:
    provider = TracerProvider(resource=Resource.create({"service.name": "bazaar-api"}))
    # BatchSpanProcessor in production (exports in the background); Simple is
    # synchronous, which a test wants.
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider


def instrument_engines(app: FastAPI, provider: TracerProvider) -> None:
    """Wrap the app's lifespan: after startup, instrument the three engines."""
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a: FastAPI) -> AsyncIterator[None]:
        async with inner(a):
            db = a.state.db
            # One instrument() call for all engines: the instrumentor is a
            # process-wide singleton, a second call would be ignored.
            SQLAlchemyInstrumentor().instrument(
                engines=[db.rw.sync_engine, db.read.sync_engine, db.seller.sync_engine],
                tracer_provider=provider,
                enable_commenter=True,
                skip_dep_check=True,  # declares SQLAlchemy < 2.1; the events it uses are unchanged
            )
            try:
                yield
            finally:
                SQLAlchemyInstrumentor().uninstrument()

    app.router.lifespan_context = lifespan
