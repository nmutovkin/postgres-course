"""Run Bazaar's migrations plus extra revisions, without touching project/python/migrations.

The exercises of module 26 write new revisions (2605, 2606, ...). To try one,
you'd normally drop it into project/python/migrations/versions/. These
solutions instead copy alembic.ini and migrations/ to a temporary directory,
add the exercise's revision files there, and point Alembic at the copy:

    from scratch_alembic import scratch_migrations, scratch_db

    with scratch_migrations(HERE / "ex02_2605_order_on_hold.py") as mig, scratch_db() as dsn:
        mig.upgrade(dsn, "head")
        mig.downgrade(dsn, "2701")

scratch_db() is an empty database (dropped afterwards). Use the reference
database (get_settings().dsn) only for exercises that need the large dataset,
and always downgrade back to 2701 afterwards: the project's migrations don't
know the exercise revisions, so the test suite would refuse to run.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from alembic import command

from bazaar import migrate
from bazaar.config import get_settings
from bazaar.testing import scratch_database


@dataclass
class ScratchMigrations:
    root: Path

    def config(self, dsn: str | None = None, **x: str):  # noqa: ANN201
        return migrate.alembic_config(dsn, ini=self.root / "alembic.ini", **x)

    def upgrade(self, dsn: str, revision: str = "head", **x: str) -> None:
        command.upgrade(self.config(dsn, **x), revision)

    def downgrade(self, dsn: str, revision: str, **x: str) -> None:
        command.downgrade(self.config(dsn, **x), revision)

    def sql(self, revision_range: str) -> str:
        import io

        buf = io.StringIO()
        cfg = migrate.alembic_config(ini=self.root / "alembic.ini", output_buffer=buf)
        command.upgrade(cfg, revision_range, sql=True)
        return buf.getvalue()


@contextmanager
def scratch_migrations(*revision_files: Path) -> Iterator[ScratchMigrations]:
    tmp = Path(tempfile.mkdtemp(prefix="bazaar-migrations-"))
    try:
        shutil.copy(migrate.PROJECT_DIR / "alembic.ini", tmp / "alembic.ini")
        shutil.copytree(
            migrate.PROJECT_DIR / "migrations",
            tmp / "migrations",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        for f in revision_files:
            shutil.copy(f, tmp / "migrations" / "versions" / f.name)
        yield ScratchMigrations(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@contextmanager
def scratch_db(prefix: str = "bazaar_ex") -> Iterator[str]:
    with scratch_database(get_settings().migration_dsn, f"{prefix}_{uuid.uuid4().hex[:8]}") as dsn:
        yield dsn
