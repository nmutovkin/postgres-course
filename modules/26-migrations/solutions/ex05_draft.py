"""Exercise 5: what autogenerate drafts for the change (printed, not written to a file).

    cd project/python && python ../../modules/26-migrations/solutions/ex05_draft.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import sqlalchemy as sa
from alembic.autogenerate import produce_migrations, render_python_code
from alembic.runtime.migration import MigrationContext

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ex05_metadata import patch_metadata  # noqa: E402
from scratch_alembic import scratch_db  # noqa: E402

from bazaar import migrate  # noqa: E402


def main() -> None:
    patch_metadata()
    with scratch_db() as dsn:
        migrate.upgrade(dsn)
        engine = sa.create_engine(dsn.replace("postgresql://", "postgresql+psycopg://"))
        with engine.connect() as c:
            opts = migrate.context_options()
            ctx = MigrationContext.configure(c, opts=opts)
            script = produce_migrations(ctx, opts["target_metadata"])
            print(render_python_code(script.upgrade_ops))
        engine.dispose()


if __name__ == "__main__":
    main()
