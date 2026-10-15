"""Exercise 7 on the reference database: a duplicate payment, the failed build,
the INVALID index, and the fixed revision.

    cd project/python && python ../../modules/26-migrations/solutions/ex07_check.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
import sqlalchemy as sa

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from scratch_alembic import scratch_migrations  # noqa: E402

from bazaar.config import get_settings  # noqa: E402

DSN = get_settings().dsn
INDEX_STATE = """
SELECT indexrelid::regclass::text, indisvalid FROM pg_index
WHERE indrelid = 'payments'::regclass AND indexrelid::regclass::text LIKE '%provider_ref%'
"""


def main() -> None:
    with psycopg.connect(DSN, autocommit=True) as c:
        dup = c.execute(
            "INSERT INTO payments (order_id, amount, provider, provider_ref, status)"
            " SELECT order_id, amount, provider, provider_ref, status FROM payments WHERE id = 1"
            " RETURNING id, provider, provider_ref"
        ).fetchone()
        print("duplicate inserted:", dup)
    with scratch_migrations(HERE / "ex07_2605_payment_ref_unique.py") as mig:
        os.environ["EX07_SKIP_CLEANUP"] = "1"
        try:
            mig.upgrade(DSN, "2605")
        except sa.exc.IntegrityError as e:
            print("without cleanup:", str(e.orig).splitlines()[0])
            print("               ", str(e.orig).splitlines()[1])
        with psycopg.connect(DSN) as c:
            print("left behind:", c.execute(INDEX_STATE).fetchall())
            print("version:", c.execute("TABLE alembic_version").fetchall())
        os.environ["EX07_SKIP_CLEANUP"] = "0"
        mig.upgrade(DSN, "2605")
        with psycopg.connect(DSN) as c:
            print("after the fixed run:", c.execute(INDEX_STATE).fetchall())
            print(
                "constraint:",
                c.execute(
                    "SELECT conname, contype FROM pg_constraint"
                    " WHERE conname = 'payments_provider_provider_ref_key'"
                ).fetchall(),
            )
            print(
                "quarantined:",
                c.execute("SELECT id, provider, provider_ref FROM payments_quarantine").fetchall(),
            )
        mig.downgrade(DSN, "2801")
    with psycopg.connect(DSN, autocommit=True) as c:  # leave the reference database as it was
        c.execute("DROP TABLE payments_quarantine")


if __name__ == "__main__":
    main()
