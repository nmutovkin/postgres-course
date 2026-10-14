"""Runtime configuration, read from the environment.

Every entry point (scripts, tests, the worker, later the FastAPI app) gets its
settings from here, so there is exactly one place that knows the defaults.

    BAZAAR_DSN               libpq connection string or URI
                             default: postgresql://postgres:postgres@localhost:5432/bazaar
    BAZAAR_APP_NAME          application_name reported to the server (pg_stat_activity)
    BAZAAR_POOL_MIN_SIZE     connection pool lower bound   (default 2)
    BAZAAR_POOL_MAX_SIZE     connection pool upper bound   (default 10)
    BAZAAR_STATEMENT_TIMEOUT per-connection statement_timeout in ms (default 5000; 0 = none)

Standard libpq variables (PGHOST, PGPORT, PGUSER, PGPASSWORD, PGSSLMODE, ...)
still apply to anything the DSN leaves out.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5432/bazaar"


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else int(raw)


@dataclass(frozen=True, slots=True)
class Settings:
    dsn: str = field(default_factory=lambda: os.environ.get("BAZAAR_DSN", DEFAULT_DSN))
    app_name: str = field(default_factory=lambda: os.environ.get("BAZAAR_APP_NAME", "bazaar"))
    pool_min_size: int = field(default_factory=lambda: _int_env("BAZAAR_POOL_MIN_SIZE", 2))
    pool_max_size: int = field(default_factory=lambda: _int_env("BAZAAR_POOL_MAX_SIZE", 10))
    statement_timeout_ms: int = field(
        default_factory=lambda: _int_env("BAZAAR_STATEMENT_TIMEOUT", 5000)
    )


def get_settings() -> Settings:
    """Read the settings now (not at import time), so tests can tweak os.environ."""
    return Settings()
