"""Module 26 milestone: the Alembic migrations.

* the revision graph is one straight line with one head;
* the models (bazaar.sa.tables) match the migrated schema, by Alembic's own
  comparison (what autogenerate would emit must be nothing);
* a database built from nothing by `alembic upgrade head` is identical, catalog
  by catalog, to the reference database (project/db + stamp 2600 + upgrade);
* every revision downgrades and upgrades again to the same schema (stairway);
* the products.slug data migration fills existing rows without touching
  updated_at or audit_log;
* offline mode renders reviewable SQL;
* env.py retries a revision that ran into lock_timeout.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from decimal import Decimal

import psycopg
import pytest
import sqlalchemy as sa

from bazaar import migrate
from bazaar.catalog import diff, snapshot
from bazaar.sa.engine import NullPool

# Modules 26-27: these tests need the database at Alembic head (tests/conftest.py).
pytestmark = pytest.mark.migrated

REVISIONS = ["2600", "2601", "2602", "2603", "2604", "2701", "2801"]


def _engine(dsn: str) -> sa.Engine:
    return sa.create_engine(
        dsn.replace("postgresql://", "postgresql+psycopg://"), poolclass=NullPool
    )


def _snapshot(dsn: str) -> dict[str, str]:
    engine = _engine(dsn)
    try:
        with engine.connect() as c:
            return snapshot(c)
    finally:
        engine.dispose()


def _current(dsn: str) -> tuple[str, ...]:
    engine = _engine(dsn)
    try:
        with engine.connect() as c:
            return migrate.current_revisions(c)
    finally:
        engine.dispose()


# --- the revision graph ------------------------------------------------------------------


def test_one_head_and_a_straight_history() -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(migrate.alembic_config())
    assert script.get_heads() == [REVISIONS[-1]]
    walked = [rev.revision for rev in script.walk_revisions("base", "heads")]
    assert walked[::-1] == REVISIONS  # walk_revisions goes from head down
    assert all(not rev.is_merge_point for rev in script.walk_revisions())


# --- models vs. schema --------------------------------------------------------------------


def test_reference_database_is_at_head(sa_conn: sa.Connection) -> None:
    migrate.check_schema(sa_conn)  # raises SchemaOutOfDate otherwise


def test_models_match_migrated_schema(sa_conn: sa.Connection) -> None:
    assert migrate.compare_models(sa_conn) == []


def test_model_drift_is_detected(sa_conn: sa.Connection) -> None:
    """compare_models() reports drift the way autogenerate would (rolled back)."""
    sa_conn.execute(sa.text("ALTER TABLE reviews ADD COLUMN helpful_votes int"))
    sa_conn.execute(sa.text("DROP INDEX products_slug_key"))
    sa_conn.execute(sa.text("ALTER TABLE orders DROP CONSTRAINT orders_idempotency_key_length"))
    ops = {(d[0], getattr(d[-1], "name", None)) for d in migrate.compare_models(sa_conn)}
    assert ("remove_column", "helpful_votes") in ops
    assert ("add_index", "products_slug_key") in ops
    assert ("add_constraint", "orders_idempotency_key_length") in ops


# --- fresh database == reference database -------------------------------------------------


def test_fresh_database_equals_reference(fresh_db: str) -> None:
    from bazaar.config import get_settings

    reference = _snapshot(get_settings().dsn)
    fresh = _snapshot(fresh_db)
    assert len(fresh) > 600  # tables, columns, constraints, functions, policies...
    assert diff(reference, fresh, labels=("reference", "fresh")) == []


def test_fresh_database_models_match(fresh_db: str) -> None:
    engine = _engine(fresh_db)
    try:
        with engine.connect() as c:
            assert migrate.compare_models(c) == []
    finally:
        engine.dispose()


def test_check_schema_rejects_an_old_database(empty_db: str) -> None:
    migrate.upgrade(empty_db, "2603")
    engine = _engine(empty_db)
    try:
        with engine.connect() as c, pytest.raises(migrate.SchemaOutOfDate, match="2603"):
            migrate.check_schema(c)
    finally:
        engine.dispose()


# --- every revision can go down and up again -----------------------------------------------


@pytest.mark.slow
def test_stairway(empty_db: str) -> None:
    """Upgrade one revision at a time; after each, downgrade it and upgrade it
    again, and require the schema to be exactly what it was."""
    migrate.upgrade(empty_db, "2600")
    before = _snapshot(empty_db)
    for rev in REVISIONS[1:]:
        migrate.upgrade(empty_db, rev)
        after = _snapshot(empty_db)
        migrate.downgrade(empty_db, "-1")
        assert diff(before, _snapshot(empty_db), labels=("before", "downgraded")) == [], rev
        migrate.upgrade(empty_db, rev)
        assert diff(after, _snapshot(empty_db), labels=("upgraded", "re-upgraded")) == [], rev
        before = after
    assert _current(empty_db) == (REVISIONS[-1],)


def test_baseline_refuses_to_downgrade(empty_db: str) -> None:
    migrate.upgrade(empty_db, "2600")
    with pytest.raises(NotImplementedError):
        migrate.downgrade(empty_db, "base")


# --- the data migration ----------------------------------------------------------------------


def test_slug_backfill(empty_db: str) -> None:
    """Rows that exist before 2602 get slugs; the backfill is not a business
    change: no audit rows, updated_at untouched. New rows get slugs from the
    trigger, explicit slugs are kept."""
    migrate.upgrade(empty_db, "2601")
    with psycopg.connect(empty_db, autocommit=True) as c:
        uid = c.execute(
            "INSERT INTO users (email, full_name) VALUES ('mig@example.com', 'Mig') RETURNING id"
        ).fetchone()[0]
        sid = c.execute(
            "INSERT INTO sellers (user_id, shop_name, country_code)"
            " VALUES (%s, 'Mig Shop', 'FR') RETURNING id",
            (uid,),
        ).fetchone()[0]
        c.execute("INSERT INTO categories (name, slug) VALUES ('Mugs', 'mugs')")
        cat = c.execute("SELECT id FROM categories").fetchone()[0]
        titles = ["Crème de Lys Café Mug", "Chef's Knife 20cm", "  ---  "]
        ids = [
            c.execute(
                "INSERT INTO products (seller_id, category_id, sku, title, price)"
                " VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (sid, cat, f"MIG-{i}", t, Decimal("9.99")),
            ).fetchone()[0]
            for i, t in enumerate(titles)
        ]
        with c.transaction():  # backdate, bypassing the set_updated_at() trigger
            c.execute("SET LOCAL session_replication_role = replica")
            c.execute("UPDATE products SET updated_at = '2025-01-01 00:00+00'")
        audit_before = c.execute("SELECT count(*) FROM audit_log").fetchone()[0]

    migrate.upgrade(empty_db, "head")

    with psycopg.connect(empty_db, autocommit=True) as c:
        rows = c.execute(
            "SELECT id, slug, updated_at::date::text FROM products ORDER BY id"
        ).fetchall()
        assert [r[1] for r in rows] == [
            f"creme-de-lys-cafe-mug-{ids[0]}",
            f"chef-s-knife-20cm-{ids[1]}",
            f"-{ids[2]}",  # nothing slug-worthy in the title: still unique, still valid
        ]
        assert {r[2] for r in rows} == {"2025-01-01"}
        assert c.execute("SELECT count(*) FROM audit_log").fetchone()[0] == audit_before
        attnotnull, indisvalid = c.execute(
            "SELECT a.attnotnull, i.indisvalid FROM pg_attribute a, pg_index i"
            " WHERE a.attrelid = 'products'::regclass AND a.attname = 'slug'"
            " AND i.indexrelid = 'products_slug_key'::regclass"
        ).fetchone()
        assert attnotnull and indisvalid
        new = c.execute(
            "INSERT INTO products (seller_id, category_id, sku, title, price)"
            " VALUES (%s, %s, 'MIG-new', 'Lumière Lamp', 5) RETURNING id, slug",
            (sid, cat),
        ).fetchone()
        assert new[1] == f"lumiere-lamp-{new[0]}"
        own = c.execute(
            "INSERT INTO products (seller_id, category_id, sku, title, price, slug)"
            " VALUES (%s, %s, 'MIG-own', 'Lamp', 5, 'my-lamp') RETURNING slug",
            (sid, cat),
        ).fetchone()
        assert own[0] == "my-lamp"
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute(
                "INSERT INTO products (seller_id, category_id, sku, title, price, slug)"
                " VALUES (%s, %s, 'MIG-dup', 'Lamp', 5, 'my-lamp')",
                (sid, cat),
            )


# --- offline mode -----------------------------------------------------------------------------


def test_offline_sql_for_review() -> None:
    out = migrate.upgrade_sql("2601:2604")
    # Online-safe DDL, outside transactions where it must be:
    assert "COMMIT;\n\nCREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS products_slug_key" in out
    assert "CHECK (slug IS NOT NULL) NOT VALID" in out
    assert "VALIDATE CONSTRAINT products_slug_not_null" in out
    # The backfill becomes a DO block that commits per batch.
    assert "DO $backfill$" in out and "COMMIT;\n        lo := lo + 10000;" in out
    assert out.count("UPDATE bazaar.alembic_version") == 3


# --- lock_timeout and retries ---------------------------------------------------------------


@pytest.mark.slow
def test_lock_timeout_retry(fresh_db: str, caplog: pytest.LogCaptureFixture) -> None:
    """A transaction holds a lock on products for 1.5 s; the migration's
    ALTER TABLE times out after 200 ms, is retried, and eventually succeeds."""
    migrate.downgrade(fresh_db, "2602")  # back to before "slug SET NOT NULL"
    ready = threading.Event()

    def blocker() -> None:
        with psycopg.connect(fresh_db) as c:
            c.execute("SELECT count(*) FROM products")  # ACCESS SHARE until commit
            ready.set()
            time.sleep(1.5)

    t = threading.Thread(target=blocker)
    t.start()
    ready.wait()
    caplog.set_level(logging.WARNING, logger="alembic.env")
    started = time.monotonic()
    migrate.upgrade(fresh_db, "head", lock_timeout="200ms", lock_retries="10")
    t.join()
    assert time.monotonic() - started >= 1.0
    assert any("lock_timeout (200ms)" in r.getMessage() for r in caplog.records)
    assert _current(fresh_db) == (REVISIONS[-1],)


def test_concurrent_upgrades_are_serialized(empty_db: str) -> None:
    """Two deploy jobs run `alembic upgrade head` at the same moment (two
    processes: Alembic's `context` is process-global, not thread-safe).
    env.py's advisory lock makes the second wait; it then finds nothing to do."""
    alembic = [sys.executable, "-m", "alembic", "-x", f"dsn={empty_db}", "upgrade", "head"]
    procs = [
        subprocess.Popen(alembic, cwd=migrate.PROJECT_DIR, stderr=subprocess.PIPE, text=True)
        for _ in range(2)
    ]
    outputs = [p.communicate(timeout=120)[1] for p in procs]
    assert [p.returncode for p in procs] == [0, 0], outputs
    ran = [o.count("Running upgrade") for o in outputs]
    assert sorted(ran) == [0, len(REVISIONS)]  # one did everything, the other nothing
    assert _current(empty_db) == (REVISIONS[-1],)
