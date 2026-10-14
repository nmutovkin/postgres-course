"""One traced "request" with OpenTelemetry's console exporter (module 27, section 7.5).

cd project/python && pip install -e '.[otel]'
python ../../modules/27-python-production/examples/otel_console.py
"""

from __future__ import annotations

import sqlalchemy as sa

from bazaar import observability as obs
from bazaar.sa.engine import create_engine

engine = create_engine()
provider = obs.setup_tracing("bazaar-api", engine=engine)  # ConsoleSpanExporter
tracer = provider.get_tracer("bazaar.api")

with tracer.start_as_current_span("GET /products/{id}"), engine.connect() as c:
    c.execute(sa.text("SELECT title FROM products WHERE id = :id"), {"id": 42}).scalar_one()

provider.shutdown()
engine.dispose()
