"""Test support: scratch databases and template databases (modules 26-27).

    from bazaar.testing import scratch_database, TemplateDatabase

    with scratch_database(admin_dsn, "bazaar_t1") as dsn:     # CREATE ... DROP
        migrate.upgrade(dsn)

    tpl = TemplateDatabase(admin_dsn, "bazaar_tpl", build=lambda dsn: migrate.upgrade(dsn))
    tpl.ensure()                                   # built once, reused while migrations match
    with tpl.clone("bazaar_t2") as dsn:            # CREATE DATABASE ... TEMPLATE: a copy
        ...

`admin_dsn` is any DSN of a role that may CREATE DATABASE (the course's
postgres superuser); the database part of it is ignored and `postgres` is used
for the CREATE/DROP commands, which cannot run inside the target database.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def database_dsn(dsn: str, dbname: str) -> str:
    """The same server and credentials, another database."""
    return make_url(dsn).set(database=dbname).render_as_string(hide_password=False)


def _check(name: str) -> str:
    if not _NAME.match(name):
        raise ValueError(f"not a safe database name: {name!r}")
    return name


def _admin(dsn: str) -> psycopg.Connection:
    # CREATE/DROP DATABASE cannot run in a transaction block: autocommit.
    return psycopg.connect(database_dsn(dsn, "postgres"), autocommit=True)


def drop_database(admin_dsn: str, name: str) -> None:
    with _admin(admin_dsn) as c:
        # WITH (FORCE) (PG 13+) terminates leftover sessions, e.g. of a crashed test.
        c.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(_check(name)))
        )


def create_database(
    admin_dsn: str, name: str, *, template: str | None = None, strategy: str = "WAL_LOG"
) -> str:
    """CREATE DATABASE name [TEMPLATE template]; returns its DSN.

    PostgreSQL 15+ copies a template block by block through the WAL by default
    (STRATEGY WAL_LOG). STRATEGY FILE_COPY copies files instead, but needs two
    checkpoints: on a small test template it was slower (217 vs 138 ms on the
    course machine), on the 2 GB course database faster (4.0 vs 8.2 s). Measure yours
    (modules/27-python-production/examples/template_benchmark.py).

    A template copy gets the template's *contents* only. Database-level
    settings (ALTER DATABASE ... SET search_path) and database privileges
    (GRANT CONNECT) belong to the database object, not to its contents, so
    they are copied here explicitly.
    """
    stmt = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(_check(name)))
    if template:
        if strategy.upper() not in ("WAL_LOG", "FILE_COPY"):
            raise ValueError(f"unknown strategy {strategy!r}")
        stmt += sql.SQL(" TEMPLATE {} STRATEGY {}").format(
            sql.Identifier(_check(template)), sql.SQL(strategy.upper())
        )
    with _admin(admin_dsn) as c:
        c.execute(stmt)
        if template:
            _copy_database_attributes(c, template, name)
    return database_dsn(admin_dsn, name)


def _copy_database_attributes(c: psycopg.Connection, src: str, dst: str) -> None:
    settings = c.execute(
        """
        SELECT split_part(cfg, '=', 1), substr(cfg, strpos(cfg, '=') + 1)
        FROM pg_db_role_setting s
        JOIN pg_database d ON d.oid = s.setdatabase
        CROSS JOIN unnest(s.setconfig) AS cfg
        WHERE d.datname = %s AND s.setrole = 0
        """,
        (src,),
    ).fetchall()
    for key, value in settings:
        # The value comes from the catalog in SET syntax ("bazaar, public").
        c.execute(
            sql.SQL("ALTER DATABASE {} SET {} = {}").format(
                sql.Identifier(dst), sql.Identifier(key), sql.SQL(value)
            )
        )
    grants = c.execute(
        """
        SELECT a.privilege_type, a.grantee::regrole::text
        FROM pg_database d, aclexplode(d.datacl) AS a
        WHERE d.datname = %s AND a.grantee <> 0 AND a.grantee <> d.datdba
        """,
        (src,),
    ).fetchall()
    for privilege, grantee in grants:
        c.execute(
            sql.SQL("GRANT {} ON DATABASE {} TO {}").format(
                sql.SQL(privilege), sql.Identifier(dst), sql.Identifier(grantee)
            )
        )


@contextmanager
def scratch_database(
    admin_dsn: str, name: str, *, template: str | None = None, keep: bool = False
) -> Iterator[str]:
    """A fresh database for the duration of the block (dropped afterwards)."""
    drop_database(admin_dsn, name)
    dsn = create_database(admin_dsn, name, template=template)
    try:
        yield dsn
    finally:
        if not keep:
            drop_database(admin_dsn, name)


def migrations_fingerprint(versions_dir: Path) -> str:
    """A hash of every revision file: the template must be rebuilt when it changes."""
    h = hashlib.sha256()
    for f in sorted(versions_dir.glob("*.py")):
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


class TemplateDatabase:
    """A database built once (e.g. `alembic upgrade head` + seed rows) and then
    cloned with CREATE DATABASE ... TEMPLATE for every test (or xdist worker).

    The fingerprint is stored as the template's COMMENT; ensure() rebuilds the
    template only when it is missing or the fingerprint changed. A database
    used as a template must have no other connections while it is copied, so
    nothing may stay connected to it: build() connects, works, disconnects.
    """

    def __init__(
        self,
        admin_dsn: str,
        name: str,
        build: Callable[[str], None],
        fingerprint: str = "",
    ) -> None:
        self.admin_dsn = admin_dsn
        self.name = _check(name)
        self.build = build
        self.fingerprint = fingerprint

    def _current_fingerprint(self) -> str | None:
        with _admin(self.admin_dsn) as c:
            row = c.execute(
                "SELECT coalesce(shobj_description(oid, 'pg_database'), '')"
                " FROM pg_database WHERE datname = %s",
                (self.name,),
            ).fetchone()
        return None if row is None else row[0]

    def ensure(self) -> bool:
        """Build the template if needed. Returns True if it was (re)built.

        Several pytest-xdist workers call this at the same time: a session
        advisory lock (module 10) makes exactly one of them build while the
        others wait, then find the fingerprint in place and reuse the result.
        """
        with _admin(self.admin_dsn) as lock:
            lock.execute("SELECT pg_advisory_lock(hashtext(%s))", (self.name,))
            try:
                if self.fingerprint and self._current_fingerprint() == self.fingerprint:
                    return False
                drop_database(self.admin_dsn, self.name)
                dsn = create_database(self.admin_dsn, self.name)
                try:
                    self.build(dsn)
                except BaseException:
                    drop_database(self.admin_dsn, self.name)
                    raise
                with _admin(self.admin_dsn) as c:
                    c.execute(
                        sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                            sql.Identifier(self.name), sql.Literal(self.fingerprint)
                        )
                    )
                return True
            finally:
                lock.execute("SELECT pg_advisory_unlock(hashtext(%s))", (self.name,))

    @contextmanager
    def clone(self, name: str, *, keep: bool = False) -> Iterator[str]:
        with scratch_database(self.admin_dsn, name, template=self.name, keep=keep) as dsn:
            yield dsn
