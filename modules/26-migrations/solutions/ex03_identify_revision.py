"""Exercise 3: which revision is this database at, when alembic_version is gone?

Builds the mystery database described in the exercise (at 2604, version table
dropped, one index created by hand), then compares its catalog snapshot with a
reference database at every revision and reports the closest one.

    cd project/python && python ../../modules/26-migrations/solutions/ex03_identify_revision.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scratch_alembic import scratch_db  # noqa: E402

from bazaar import migrate  # noqa: E402
from bazaar.catalog import diff, snapshot  # noqa: E402

REVISIONS = ["2600", "2601", "2602", "2603", "2604", "2701"]


def snap(dsn: str) -> dict[str, str]:
    engine = sa.create_engine(dsn.replace("postgresql://", "postgresql+psycopg://"))
    try:
        with engine.connect() as c:
            return snapshot(c)
    finally:
        engine.dispose()


def main() -> None:
    with scratch_db("bazaar_mystery") as mystery, scratch_db("bazaar_ladder") as ladder:
        # --- the mystery database (the exercise's setup) ---
        migrate.upgrade(mystery, "2604")
        with psycopg.connect(mystery, autocommit=True) as c:
            c.execute("DROP TABLE bazaar.alembic_version")
            c.execute("CREATE INDEX orders_status_idx ON orders (status)")  # a hand-made "hotfix"
        target = snap(mystery)

        # --- climb the ladder: one snapshot per revision ---
        results = []
        for rev in REVISIONS:
            migrate.upgrade(ladder, rev)
            d = diff(snap(ladder), target, labels=(rev, "mystery"))
            results.append((len(d), rev, d))
            print(f"{rev}: {len(d)} differences")
        n, best, differences = min(results)
        print(f"closest revision: {best} ({n} differences)")
        for line in differences:
            print("   ", line.splitlines()[0])

        # --- repair: stamp, keep the hotfix as a real revision later, upgrade ---
        migrate.stamp(mystery, best)
        migrate.upgrade(mystery, "head")
        engine = sa.create_engine(mystery.replace("postgresql://", "postgresql+psycopg://"))
        with engine.connect() as c:
            print("after stamp + upgrade:", migrate.current_revisions(c))
        engine.dispose()


if __name__ == "__main__":
    main()
