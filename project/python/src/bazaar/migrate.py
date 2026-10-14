"""Alembic from Python, and helpers for writing online-safe migrations (module 26).

Two audiences:

1. Code that needs to know about migrations: tests, deployment scripts, and
   the service's startup check.

       from bazaar import migrate
       migrate.upgrade(dsn)                  # == `alembic upgrade head`
       migrate.check_schema(conn)            # raises SchemaOutOfDate unless at head

2. Revision files. They may import these helpers (stable, schema-agnostic
   building blocks), but never the ORM models or bazaar.sa.tables: a revision
   must do the same thing in a year, when the models describe a different
   schema.

       from bazaar.migrate import create_index_concurrently, backfill

Every helper works online (executes) and offline (`alembic upgrade --sql`:
prints SQL for review). Helpers that must run outside a transaction open an
autocommit block themselves, which COMMITs the revision's work so far. That
makes the revision non-atomic: if it fails later, what was committed stays,
alembic_version still says the previous revision, and the next run starts the
revision again. So the helpers are re-runnable (they skip what is done), and
the DDL before them in a revision must be too (IF NOT EXISTS, OR REPLACE).
"""

from __future__ import annotations

import logging
import os
import time
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import command, op
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection

log = logging.getLogger("bazaar.migrate")

SCHEMA = "bazaar"
VERSION_TABLE_SCHEMA = SCHEMA

# project/python/alembic.ini (this file is project/python/src/bazaar/migrate.py)
PROJECT_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI = Path(os.environ.get("BAZAAR_ALEMBIC_INI", PROJECT_DIR / "alembic.ini"))


# --- what Alembic compares and where it keeps its version (used by env.py and tests) ---------

# products.search_vector is a generated column; Postgres returns its expression
# normalized ('A'::"char", ::regconfig casts), so the texts never match and
# Alembic warns on every run. Changing a generation expression needs a new
# column anyway (or PG 17's ALTER COLUMN ... SET EXPRESSION), written by hand.
COMPUTED_WARNING = r"Computed default on .* cannot be modified"

# Tables that exist in the database but are deliberately not described by the
# metadata (none at the moment: every Bazaar table is in bazaar.sa.tables).
IGNORED_TABLES: set[str] = set()


def _is_partition_name(name: str | None) -> bool:
    # Monthly partitions of events (events_2025_01 ..., events_default) are
    # created at run time by create_events_partition() (module 14).
    return bool(name) and name.startswith("events_")  # type: ignore[union-attr]


def include_name(name, type_, parent_names) -> bool:  # noqa: ANN001
    """Pre-filter by name, before reflection: only our schema, no partitions."""
    if type_ == "schema":
        return name in (None, SCHEMA)
    if type_ == "table":
        return not _is_partition_name(name)
    return True


def include_object(obj, name, type_, reflected, compare_to) -> bool:  # noqa: ANN001
    """Filter compared objects. `reflected and compare_to is None` means "in the
    database, not in the metadata": autogenerate would emit a DROP for it."""
    if type_ == "table" and (name == "alembic_version" or name in IGNORED_TABLES):
        return False
    return True


def context_options() -> dict:
    """MigrationContext options for env.py (and for compare_metadata in tests)."""
    from bazaar.sa.tables import metadata  # the models, for autogenerate only

    return {
        "target_metadata": metadata,
        # alembic_version lives in the bazaar schema, next to the tables it versions.
        "version_table_schema": VERSION_TABLE_SCHEMA,
        # The metadata's tables are not schema-qualified: they live in the
        # connection's default schema, which is `bazaar` (search_path). So
        # autogenerate compares the default schema only.
        "include_schemas": False,
        "include_object": include_object,
        "include_name": include_name,
        "compare_type": True,  # column type changes (the default since Alembic 1.12)
        "compare_server_default": True,  # DEFAULT changes (off by default: noisy)
        # Comparison plugins (Alembic 1.18+): the defaults, minus comments (the
        # SQL files commented tables; the metadata does not carry those texts),
        # plus the opt-in plugin that adds/drops CHECK constraints by name.
        "autogenerate_plugins": [
            "alembic.autogenerate.*",
            "~alembic.autogenerate.comments",
            "alembic.ext.checkconstraint_byname",
        ],
        # Each revision commits on its own: a failure in revision 3 leaves 1 and 2
        # applied and recorded, and autocommit blocks (CREATE INDEX CONCURRENTLY)
        # only commit their own revision's work.
        "transaction_per_migration": True,
    }


def compare_models(conn: Connection) -> list:
    """What autogenerate would emit to make the database match the metadata
    ([] when they agree). The test "the models match the migrations"."""
    from alembic.autogenerate import compare_metadata

    opts = context_options()
    ctx = MigrationContext.configure(conn, opts=opts)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=COMPUTED_WARNING)
        return compare_metadata(ctx, opts["target_metadata"])


# --- running Alembic programmatically ---------------------------------------------------


def alembic_config(
    dsn: str | None = None,
    *,
    connection: sa.Connection | None = None,
    output_buffer: Any = None,
    ini: str | Path | None = None,
    **x: str,
) -> Config:
    """An Alembic Config for project/python/alembic.ini.

    dsn: database to migrate (default: BAZAAR_MIGRATION_DSN / BAZAAR_DSN).
    connection: run on this SQLAlchemy connection instead (env.py uses it as is).
    output_buffer: where offline (--sql) output goes (default: stdout).
    ini: another alembic.ini (e.g. a scratch copy with extra revisions).
    x: extra `-x key=value` arguments, e.g. lock_timeout="5s".
    """
    cfg = Config(str(ini or ALEMBIC_INI), output_buffer=output_buffer)
    cfg.attributes["configure_logging"] = False  # leave the caller's logging alone
    xs = [f"{k}={v}" for k, v in x.items()]
    if dsn:
        xs.append(f"dsn={dsn}")
    cfg.cmd_opts = type("Opts", (), {"x": xs})()  # what `alembic -x ...` would set
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def upgrade(dsn: str | None = None, revision: str = "head", **x: str) -> None:
    command.upgrade(alembic_config(dsn, **x), revision)


def upgrade_sql(revision_range: str = "2600:head") -> str:
    """The SQL `alembic upgrade <range> --sql` prints (offline mode), as a string."""
    import io

    buf = io.StringIO()
    command.upgrade(alembic_config(output_buffer=buf), revision_range, sql=True)
    return buf.getvalue()


def downgrade(dsn: str | None = None, revision: str = "-1", **x: str) -> None:
    command.downgrade(alembic_config(dsn, **x), revision)


def stamp(dsn: str | None = None, revision: str = "head") -> None:
    command.stamp(alembic_config(dsn), revision)


def head_revisions() -> tuple[str, ...]:
    """The head revision(s) of the scripts on disk (normally exactly one)."""
    return tuple(ScriptDirectory.from_config(alembic_config()).get_heads())


def current_revisions(conn: sa.Connection) -> tuple[str, ...]:
    """What alembic_version says about this database (empty if not under Alembic)."""
    ctx = MigrationContext.configure(conn, opts={"version_table_schema": VERSION_TABLE_SCHEMA})
    return tuple(ctx.get_current_heads())


class SchemaOutOfDate(RuntimeError):
    """The database is not at the revision this code was written for."""


def check_schema(conn: sa.Connection) -> None:
    """Refuse to run against a database whose schema is not at head.

    Meant for service startup and the test session: failing loudly at boot is
    better than failing on the first request that touches a new column. (A
    deployment where new code may start *before* the migration ran must relax
    this to "at least revision X"; see module 26, section 9.)
    """
    current, heads = set(current_revisions(conn)), set(head_revisions())
    if current != heads:
        raise SchemaOutOfDate(
            f"database is at {sorted(current) or 'no revision'}, code expects {sorted(heads)}:"
            " run `alembic upgrade head` (or `alembic stamp 2600` first on a database"
            " built by project/bin/reset.sh)"
        )


# --- helpers for revision files ---------------------------------------------------------


def _offline() -> bool:
    return op.get_context().as_sql


def _bind() -> sa.Connection:
    return op.get_bind()


def execute_script(sql: str) -> None:
    """Execute a multi-statement SQL script verbatim.

    op.execute() would turn the text into a SQLAlchemy text() construct, where
    ':name' means a bind parameter, and even exec_driver_sql() hands psycopg a
    (empty) parameter list, so every '%' is parsed as a placeholder: fatal for
    a pg_dump file full of format('%I', ...), JSON and PL/pgSQL. Here the text
    goes to the driver untouched (online), or to the output as is (offline).
    """
    if _offline():
        op.get_context().impl.static_output(sql)
    else:
        # The DBAPI cursor, without parameters: psycopg then sends the text as
        # is (no placeholder parsing, so '%I' in format() strings is fine), in
        # the simple query protocol that allows many statements in one call.
        with _bind().connection.dbapi_connection.cursor() as cur:
            cur.execute(sql)


def _autocommit():
    return op.get_context().autocommit_block()


def create_index_concurrently(
    name: str,
    table: str,
    columns: str,
    *,
    unique: bool = False,
    where: str | None = None,
    using: str | None = None,
) -> None:
    """CREATE [UNIQUE] INDEX CONCURRENTLY, safely re-runnable.

    columns/where are SQL snippets, e.g. columns="user_id, created_at DESC".
    A failed concurrent build leaves an INVALID index behind (it still slows
    down writes, and IF NOT EXISTS would happily keep it), so an invalid
    leftover of the same name is dropped first.
    """
    stmt = (
        f"CREATE {'UNIQUE ' if unique else ''}INDEX CONCURRENTLY IF NOT EXISTS {name}"
        f" ON {table}{f' USING {using}' if using else ''} ({columns})"
        f"{f' WHERE {where}' if where else ''}"
    )
    with _autocommit():
        if not _offline():
            valid = (
                _bind()
                .execute(
                    sa.text("SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass(:n)"),
                    {"n": name},
                )
                .scalar()
            )
            if valid is False:
                log.warning("dropping INVALID index %s left by an earlier attempt", name)
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
        op.execute(stmt)


def drop_index_concurrently(name: str) -> None:
    with _autocommit():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")


def add_check_constraint_online(table: str, name: str, condition: str) -> None:
    """ADD CONSTRAINT ... CHECK NOT VALID, COMMIT, then VALIDATE CONSTRAINT.

    The two steps must be separate transactions: ADD takes an ACCESS EXCLUSIVE
    lock (briefly, no scan), VALIDATE scans the table under SHARE UPDATE
    EXCLUSIVE, which does not block reads or writes. In one transaction the
    first lock would be held for the whole scan.
    """
    with _autocommit():
        state = None if _offline() else _constraint_state(table, name)
        if state is None:
            op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({condition}) NOT VALID")
        if state is not True:
            op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")


def _constraint_state(table: str, name: str) -> bool | None:
    """None: no such constraint; else whether it is validated. (Re-runs after a
    failure must skip what an earlier attempt already committed.)"""
    return (
        _bind()
        .execute(
            sa.text(
                "SELECT convalidated FROM pg_constraint"
                " WHERE conrelid = to_regclass(:t) AND conname = :n"
            ),
            {"t": table, "n": name},
        )
        .scalar()
    )


def set_not_null_online(table: str, column: str) -> None:
    """SET NOT NULL without a long ACCESS EXCLUSIVE scan (PostgreSQL 12+).

    A validated CHECK (column IS NOT NULL) proves there are no NULLs, so SET
    NOT NULL skips its full-table scan; afterwards the CHECK is redundant.
    """
    check = f"{table}_{column}_not_null"
    add_check_constraint_online(table, check, f"{column} IS NOT NULL")
    with _autocommit():
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET NOT NULL")
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {check}")


@contextmanager
def _replica_role(enabled: bool) -> Iterator[None]:
    """session_replication_role = replica: ordinary triggers do not fire.

    Needs superuser or, since PostgreSQL 15, GRANT SET ON PARAMETER
    session_replication_role TO <role>. It also disables FK triggers, so only
    use it for UPDATEs of columns that no foreign key involves.
    """
    if not enabled:
        yield
        return
    op.execute("SET session_replication_role = replica")
    try:
        yield
    finally:
        op.execute("RESET session_replication_role")


def backfill(
    table: str,
    set_clause: str,
    where: str,
    *,
    key: str = "id",
    batch_size: int = 5000,
    pause: float = 0.0,
    skip_triggers: bool = False,
) -> int:
    """UPDATE a big table in key-range batches, one short transaction per batch.

        backfill("products", "slug = slugify(title) || '-' || id", "slug IS NULL")

    Each batch locks at most `batch_size` rows for a moment, produces a bounded
    amount of WAL, and leaves dead tuples that autovacuum can clean while the
    backfill goes on. `pause` seconds between batches throttles the load
    (replication lag, I/O). `where` must make the update idempotent (only rows
    that still need it), so an interrupted backfill can simply run again.

    Returns the number of updated rows (online), 0 offline. Offline, it prints
    an equivalent DO block that COMMITs per batch (PostgreSQL 11+; it must run
    outside a transaction block, which the autocommit block ensures).
    """
    with _autocommit():
        if _offline():
            op.execute(
                f"""DO $backfill$
DECLARE
    lo bigint := (SELECT min({key}) FROM {table} WHERE {where});
    hi bigint := (SELECT max({key}) FROM {table} WHERE {where});
BEGIN
    {"SET session_replication_role = replica;" if skip_triggers else ""}
    WHILE lo <= hi LOOP
        UPDATE {table} SET {set_clause}
        WHERE {key} >= lo AND {key} < lo + {batch_size} AND ({where});
        COMMIT;
        lo := lo + {batch_size};
        PERFORM pg_sleep({pause});
    END LOOP;
END
$backfill$"""
            )
            return 0

        conn = _bind()
        lo, hi = conn.execute(
            sa.text(f"SELECT min({key}), max({key}) FROM {table} WHERE {where}")
        ).one()
        if lo is None:
            return 0
        total, started = 0, time.monotonic()
        update = sa.text(
            f"UPDATE {table} SET {set_clause} WHERE {key} >= :lo AND {key} < :hi AND ({where})"
        )
        with _replica_role(skip_triggers):
            while lo <= hi:
                n = conn.execute(update, {"lo": lo, "hi": lo + batch_size}).rowcount
                total += n
                lo += batch_size
                if pause:
                    time.sleep(pause)
        log.info(
            "backfill %s: %d rows in %.1fs (batches of %d)",
            table,
            total,
            time.monotonic() - started,
            batch_size,
        )
        return total
