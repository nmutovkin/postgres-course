"""Exercise 4: the fixed revision on an empty database (up, down, up), plus its SQL.

    cd project/python && python ../../modules/26-migrations/solutions/ex04_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import sqlalchemy as sa

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from scratch_alembic import scratch_db, scratch_migrations  # noqa: E402

from bazaar.catalog import diff, snapshot  # noqa: E402


def snap(dsn: str) -> dict[str, str]:
    e = sa.create_engine(dsn.replace("postgresql://", "postgresql+psycopg://"))
    with e.connect() as c:
        out = snapshot(c)
    e.dispose()
    return out


def main() -> None:
    with scratch_migrations(HERE / "ex04_fixed_2605.py") as mig, scratch_db() as dsn:
        mig.upgrade(dsn, "2701")
        before = snap(dsn)
        mig.upgrade(dsn, "2605")
        after = snap(dsn)
        print("2605 adds:", [d.split(": ", 1)[1] for d in diff(before, after) if "only in" in d])
        mig.downgrade(dsn, "2701")
        print("downgrade restores 2701:", diff(before, snap(dsn)) == [])
        mig.upgrade(dsn, "2605")
        print("re-upgrade is identical:", diff(after, snap(dsn)) == [])
        sql = mig.sql("2701:2605")
        print("statements outside a transaction:", sql.count("CONCURRENTLY"), "concurrent,",
              sql.count("NOT VALID"), "NOT VALID,", sql.count("VALIDATE CONSTRAINT"), "VALIDATE")


if __name__ == "__main__":
    main()
