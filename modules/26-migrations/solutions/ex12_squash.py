"""Exercise 12: squash revisions 2600-2604 into a new baseline 2700, and prove it.

    cd project/python && COURSE_PG_CONTAINER=course-pg \
        python ../../modules/26-migrations/solutions/ex12_squash.py

Works on a scratch copy of the migrations (the project is not changed).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.util import CommandError

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
from scratch_alembic import scratch_db, scratch_migrations  # noqa: E402

from bazaar import migrate  # noqa: E402
from bazaar.catalog import diff, snapshot  # noqa: E402

CONTAINER = os.environ.get("COURSE_PG_CONTAINER", "course-pg")


def dump_schema(dbname: str) -> str:
    """make_baseline.sh's pg_dump + cleaning, for another database and without alembic_version."""
    out = subprocess.run(
        ["docker", "exec", CONTAINER, "pg_dump", "-U", "postgres", "-d", dbname, "--schema-only",
         "--no-tablespaces", "--exclude-table=bazaar.alembic_version"],
        check=True, capture_output=True, text=True,
    ).stdout
    lines = []
    for line in out.splitlines():
        if re.match(r"^\\(un)?restrict ", line) or re.match(r"^SET [a-z_]+ = .*;$", line):
            continue
        if line == "SELECT pg_catalog.set_config('search_path', '', false);":
            continue
        lines.append("CREATE SCHEMA IF NOT EXISTS bazaar;" if line == "CREATE SCHEMA bazaar;" else line)
    return "-- Bazaar squashed baseline (revision 2604). Generated; do not edit.\n" + "\n".join(lines) + "\n"


def snap(dsn: str) -> dict[str, str]:
    e = sa.create_engine(dsn.replace("postgresql://", "postgresql+psycopg://"))
    with e.connect() as c:
        out = snapshot(c)
    e.dispose()
    return out


def main() -> None:
    with scratch_migrations() as mig, scratch_db() as at_2604, scratch_db() as new_db, \
            scratch_db() as old_db, scratch_db() as stale_db:
        versions = mig.root / "migrations" / "versions"
        # 1. A database at the revision we squash up to, and its schema.
        migrate.upgrade(at_2604, "2604")
        schema = dump_schema(at_2604.rsplit("/", 1)[1])
        (mig.root / "migrations" / "baseline" / "schema_2700.sql").write_text(schema)
        print(f"squashed schema: {len(schema.splitlines())} lines")
        # 2. The new baseline revision: 2600's code, pointed at the new snapshot.
        base = (versions / "2600_baseline.py").read_text()
        base = base.replace('revision: str = "2600"', 'revision: str = "2700"')
        base = base.replace('"schema.sql"', '"schema_2700.sql"')
        base = base.replace("baseline: the schema built by project/db up to module 25",
                            "squashed baseline: everything up to revision 2604")
        (versions / "2700_squashed_baseline.py").write_text(base)
        # 3. Drop the squashed revisions; re-point the first survivor.
        for f in versions.glob("260[0-4]_*.py"):
            f.unlink()
        f2701 = versions / "2701_outbox.py"
        f2701.write_text(f2701.read_text().replace('down_revision: str | Sequence[str] | None = "2604"',
                                                   'down_revision: str | Sequence[str] | None = "2700"'))
        cfg = mig.config()
        command.history(cfg)

        # 4. New databases: squashed chain == original chain.
        mig.upgrade(new_db, "head")
        migrate.upgrade(old_db, "head")
        print("fresh via squashed chain == fresh via original chain:",
              diff(snap(old_db), snap(new_db), labels=("original", "squashed")) == [])

        # 5. Existing databases: at 2701 nothing changes; below 2604 they are stuck.
        e = sa.create_engine(old_db.replace("postgresql://", "postgresql+psycopg://"))
        with e.connect() as c:
            print("database at 2701, squashed scripts: current =", migrate.current_revisions(c))
        e.dispose()
        migrate.upgrade(stale_db, "2603")
        try:
            mig.upgrade(stale_db, "head")
        except CommandError as err:
            print("database at 2603, squashed scripts:", err)


if __name__ == "__main__":
    main()
