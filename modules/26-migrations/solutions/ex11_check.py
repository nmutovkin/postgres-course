"""Exercise 11: old and new code side by side during the expand phase, then the
contract phase; and the backfill on the 100,000 users of the reference database.

    cd project/python && python ../../modules/26-migrations/solutions/ex11_check.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from scratch_alembic import scratch_db, scratch_migrations  # noqa: E402

from bazaar.config import get_settings  # noqa: E402

FILES = [HERE / "ex11_2605_split_name_expand.py", HERE / "ex11_2606_split_name_contract.py"]


def show(c: psycopg.Connection, label: str) -> None:
    rows = c.execute(
        "SELECT id, full_name, first_name, last_name FROM users ORDER BY id"
    ).fetchall()
    print(f"  {label}")
    for r in rows:
        print(f"    {r}")


def main() -> None:
    with scratch_migrations(*FILES) as mig, scratch_db() as dsn:
        mig.upgrade(dsn, "2801")
        with psycopg.connect(dsn, autocommit=True) as c:  # rows written before the change
            c.execute(
                "INSERT INTO users (email, full_name) VALUES"
                " ('a@example.com', 'Liam O''Connor'), ('b@example.com', '  Zoé   Martin '),"
                " ('c@example.com', 'Cher')"
            )
        mig.upgrade(dsn, "2605")
        with psycopg.connect(dsn, autocommit=True) as c:
            show(c, "after expand + backfill:")
            c.execute(
                "INSERT INTO users (email, full_name) VALUES ('d@example.com', 'Ana María de la Cruz')"
            )
            c.execute(
                "INSERT INTO users (email, first_name, last_name)"
                " VALUES ('e@example.com', 'Ana María', 'de la Cruz')"
            )
            c.execute("UPDATE users SET full_name = 'Liam O''Brien' WHERE id = 1")  # old code
            c.execute("UPDATE users SET last_name = 'Martin-Weiss' WHERE id = 2")  # new code
            show(c, "old and new code writing side by side:")
        mig.upgrade(dsn, "2606")
        with psycopg.connect(dsn, autocommit=True) as c:
            try:
                c.execute(
                    "INSERT INTO users (email, full_name) VALUES ('f@example.com', 'Old Code')"
                )
            except psycopg.Error as e:
                print("  after contract, old code:", type(e).__name__, str(e).splitlines()[0])
        mig.downgrade(dsn, "2605")
        with psycopg.connect(dsn) as c:
            show(c, "after downgrading the contract (full_name rebuilt from the parts):")
        mig.downgrade(dsn, "2801")

    # The backfill at scale, on the reference database (then undone).
    ref = get_settings().dsn
    with scratch_migrations(*FILES) as mig:
        t0 = time.perf_counter()
        mig.upgrade(ref, "2605")
        took = time.perf_counter() - t0
        with psycopg.connect(ref) as c:
            missing, sample = c.execute(
                "SELECT count(*) FILTER (WHERE first_name IS NULL),"
                " (SELECT array_agg(format('%s | %s | %s', full_name, first_name, last_name))"
                "  FROM (SELECT * FROM users ORDER BY id LIMIT 3) u)"
                " FROM users"
            ).fetchone()
        print(f"reference database: 2605 in {took:.1f}s, users without first_name: {missing}")
        for s in sample:
            print("   ", s)
        mig.downgrade(ref, "2801")


if __name__ == "__main__":
    main()
