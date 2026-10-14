"""Alembic environment for the Bazaar database (module 26).

Alembic runs this file for every command that touches the database (upgrade,
downgrade, stamp, current, check, revision --autogenerate). It decides:

* WHERE to connect: BAZAAR_MIGRATION_DSN (falling back to BAZAAR_DSN) via
  bazaar.config, never a URL written in alembic.ini. `alembic -x dsn=...`
  overrides it (the migration tests use that to build scratch databases).
* AS WHOM objects are created: the connection role (a superuser or an admin
  role in the course) does SET ROLE bazaar_owner, so new tables and functions
  belong to the owner and pick up its default privileges (module 15).
* HOW LONG to wait for locks: lock_timeout (default 2s, `-x lock_timeout=5s`),
  and retries of the failed migration when a lock was not granted in time.
* WHAT autogenerate compares: bazaar.sa.tables.metadata against the bazaar
  schema, minus the objects Alembic cannot model (bazaar.migrate.context_options:
  include_object/include_name filters, comparison plugins).
"""

from __future__ import annotations

import logging
import random
import time
import warnings
from dataclasses import replace
from logging.config import fileConfig

import sqlalchemy as sa
from alembic import context
from sqlalchemy.exc import OperationalError

from bazaar.config import get_settings
from bazaar.migrate import COMPUTED_WARNING, context_options
from bazaar.sa.engine import NullPool, create_engine

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

log = logging.getLogger("alembic.env")
warnings.filterwarnings("ignore", message=COMPUTED_WARNING)  # see bazaar.migrate

SCHEMA = "bazaar"
OWNER_ROLE = "bazaar_owner"
MIGRATION_LOCK = "bazaar.migrations"
x_args = context.get_x_argument(as_dictionary=True)


def _common_options() -> dict:
    # Shared with the tests (alembic's compare_metadata), so that "the models
    # match the database" is checked with exactly the rules autogenerate uses.
    return context_options()


# --- offline: emit SQL for review --------------------------------------------------------


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: print the SQL instead of running it."""
    context.configure(
        url="postgresql+psycopg://",  # only the dialect matters offline
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_common_options(),
    )
    with context.begin_transaction():
        context.execute(f"SET lock_timeout = '{_lock_timeout()}'")
        context.execute(f"SET ROLE {OWNER_ROLE}")
        context.run_migrations()


# --- online -------------------------------------------------------------------------------


def _lock_timeout() -> str:
    return x_args.get("lock_timeout", "2s")


def _engine() -> sa.Engine:
    connection = config.attributes.get("connection")
    if connection is not None:  # a caller (tests, bazaar.migrate) passed one in
        return connection
    s = get_settings()
    s = replace(
        s,
        dsn=x_args.get("dsn") or s.migration_dsn,
        app_name="bazaar-migrations",  # visible in pg_stat_activity and the logs
        statement_timeout_ms=0,  # backfills and index builds may take minutes
        lock_timeout_ms=0,  # set below, per session
        idle_in_transaction_timeout_ms=0,
    )
    return create_engine(s, poolclass=NullPool)


def _acquire_migration_lock(connection: sa.Connection) -> None:
    """One migration run per database at a time.

    Two deploy jobs starting together would otherwise both read "at 2603" and
    both run 2604. A session advisory lock (module 10) is held until released
    or until the connection closes; the second run waits, then finds nothing
    left to do.

    It must NOT wait inside pg_advisory_lock(): a waiting statement holds a
    snapshot, and CREATE INDEX CONCURRENTLY in the first run waits for every
    snapshot older than its own to go away, while the second run waits for the
    first: a deadlock (we hit it in tests/test_migrations.py). So: try, and
    sleep in Python between tries, with no transaction open.
    """
    dbapi = connection.connection.dbapi_connection
    dbapi.autocommit = True
    try:
        waited = 0.0
        while not dbapi.execute(
            "SELECT pg_try_advisory_lock(hashtext(%s))", (MIGRATION_LOCK,)
        ).fetchone()[0]:
            if waited == 0.0:
                log.info("another migration run holds the lock; waiting")
            time.sleep(0.5)
            waited += 0.5
    finally:
        dbapi.autocommit = False


def _prepare_session(connection: sa.Connection) -> None:
    """Session settings for the migration connection (they survive COMMITs)."""
    _acquire_migration_lock(connection)
    connection.execute(
        sa.text("SELECT set_config('lock_timeout', :t, false)"), {"t": _lock_timeout()}
    )
    has_owner = connection.execute(
        sa.text("SELECT EXISTS (SELECT FROM pg_roles WHERE rolname = :r)"), {"r": OWNER_ROLE}
    ).scalar_one()
    # The schema must exist before Alembic creates bazaar.alembic_version in it.
    owner = f" AUTHORIZATION {OWNER_ROLE}" if has_owner else ""
    connection.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}{owner}"))
    if has_owner:  # absent only in a brand-new cluster, before the baseline creates it
        connection.execute(sa.text(f"SET ROLE {OWNER_ROLE}"))
    connection.commit()  # end the implicit transaction; SET (not SET LOCAL) persists


def _is_lock_timeout(exc: BaseException) -> bool:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", None) == "55P03"  # lock_not_available


def run_migrations_online() -> None:
    """Run the migrations, retrying a revision that hit lock_timeout.

    With transaction_per_migration, the revision that failed was rolled back
    as a whole, and the revisions before it are committed and recorded in
    alembic_version. Running again therefore resumes at the failed revision.
    """
    attempts = int(x_args.get("lock_retries", "5"))
    engine_or_conn = _engine()
    for attempt in range(1, attempts + 1):
        try:
            if isinstance(engine_or_conn, sa.Connection):
                _run(engine_or_conn)
            else:
                with engine_or_conn.connect() as connection:
                    _run(connection)
            return
        except OperationalError as exc:
            if not _is_lock_timeout(exc) or attempt == attempts:
                raise
            delay = min(30.0, 0.5 * 2**attempt) * random.uniform(0.5, 1.0)
            log.warning(
                "lock_timeout (%s) on attempt %d/%d: %s; retrying in %.1fs",
                _lock_timeout(),
                attempt,
                attempts,
                str(exc.orig).splitlines()[0],
                delay,
            )
            if isinstance(engine_or_conn, sa.Connection):
                engine_or_conn.rollback()
            time.sleep(delay)


def _run(connection: sa.Connection) -> None:
    _prepare_session(connection)
    try:
        context.configure(connection=connection, **_common_options())
        with context.begin_transaction():
            context.run_migrations()
    finally:
        if connection.in_transaction():
            connection.rollback()
        connection.execute(sa.text("SELECT pg_advisory_unlock_all()"))
        connection.execute(sa.text("RESET ROLE"))
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
