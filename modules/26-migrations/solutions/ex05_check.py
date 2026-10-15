"""Exercise 5: after the reviewed revision, the patched metadata matches the database.

cd project/python && python ../../modules/26-migrations/solutions/ex05_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import sqlalchemy as sa

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ex05_metadata import patch_metadata  # noqa: E402
from scratch_alembic import scratch_db, scratch_migrations  # noqa: E402

from bazaar import migrate  # noqa: E402


def compare(dsn: str) -> list:
    engine = sa.create_engine(dsn.replace("postgresql://", "postgresql+psycopg://"))
    with engine.connect() as c:
        out = migrate.compare_models(c)
    engine.dispose()
    return out


def main() -> None:
    patch_metadata()
    with scratch_migrations(HERE / "ex05_2605_review_votes.py") as mig, scratch_db() as dsn:
        mig.upgrade(dsn, "2801")
        print("at 2801, autogenerate would emit:", [op[0] for op in compare(dsn)])
        mig.upgrade(dsn, "2605")
        print("at 2605:", compare(dsn))
        mig.downgrade(dsn, "2801")
        mig.upgrade(dsn, "2605")
        print("after down/up:", compare(dsn))


if __name__ == "__main__":
    main()
