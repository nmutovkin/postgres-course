"""Runtime configuration, read from the environment.

Every entry point (scripts, tests, the worker, later the FastAPI app) gets its
settings from here, so there is exactly one place that knows the defaults.

    BAZAAR_DSN               libpq connection string or URI
                             default: postgresql://postgres:postgres@localhost:5432/bazaar
    BAZAAR_APP_NAME          application_name reported to the server (pg_stat_activity)
    BAZAAR_POOL_MIN_SIZE     connection pool lower bound   (default 2)
    BAZAAR_POOL_MAX_SIZE     connection pool upper bound   (default 10)
    BAZAAR_STATEMENT_TIMEOUT per-connection statement_timeout in ms (default 5000; 0 = none)
    BAZAAR_LOCK_TIMEOUT      per-connection lock_timeout in ms (default 0 = none; module 27)
    BAZAAR_IDLE_TX_TIMEOUT   idle_in_transaction_session_timeout in ms (default 30000; module 27)
    BAZAAR_POOL_TIMEOUT      seconds to wait for a pooled connection (default 10; module 27)
    BAZAAR_REPLICA_DSN       a read replica for read-only work (default: none = the primary;
                             module 27, sa.engine.create_read_engine)
    BAZAAR_MIGRATION_DSN     DSN Alembic uses (default: BAZAAR_DSN). Migrations need a
                             role that may create extensions and SET ROLE bazaar_owner
                             (module 26); the application itself should not have one.

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
    lock_timeout_ms: int = field(default_factory=lambda: _int_env("BAZAAR_LOCK_TIMEOUT", 0))
    idle_in_transaction_timeout_ms: int = field(
        default_factory=lambda: _int_env("BAZAAR_IDLE_TX_TIMEOUT", 30000)
    )
    pool_timeout_s: float = field(
        default_factory=lambda: float(_int_env("BAZAAR_POOL_TIMEOUT", 10))
    )
    replica_dsn: str | None = field(
        default_factory=lambda: os.environ.get("BAZAAR_REPLICA_DSN") or None
    )
    migration_dsn: str = field(
        default_factory=lambda: (
            os.environ.get("BAZAAR_MIGRATION_DSN") or os.environ.get("BAZAAR_DSN", DEFAULT_DSN)
        )
    )

    def session_settings(self) -> dict[str, str]:
        """Server settings every application connection starts with (all drivers).

        Sent at connect time (libpq `options=-c ...`, asyncpg `server_settings`),
        so they cost no extra round trip and survive pooling. A value of 0 means
        "not set": the server default (no limit) applies.
        """
        out = {"application_name": self.app_name}
        for name, value in (
            ("statement_timeout", self.statement_timeout_ms),
            ("lock_timeout", self.lock_timeout_ms),
            ("idle_in_transaction_session_timeout", self.idle_in_transaction_timeout_ms),
        ):
            if value:
                out[name] = str(value)
        return out

    def libpq_options(self) -> str | None:
        """session_settings() minus application_name, as a libpq `options` string."""
        settings = self.session_settings()
        settings.pop("application_name")
        return " ".join(f"-c {k}={v}" for k, v in settings.items()) or None


def get_settings() -> Settings:
    """Read the settings now (not at import time), so tests can tweak os.environ."""
    return Settings()
