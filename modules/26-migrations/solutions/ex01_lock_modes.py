"""Exercise 1: which table locks do the statements of revisions 2602-2604 take?

Runs each DDL statement of the offline SQL (`alembic upgrade 2601:2604 --sql`)
in its own transaction on an empty scratch database at 2601, looks at
pg_locks before COMMIT, and prints the strongest lock per table.

    cd project/python && python ../../modules/26-migrations/solutions/ex01_lock_modes.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scratch_alembic import scratch_db  # noqa: E402

from bazaar import migrate  # noqa: E402

STRENGTH = [
    "AccessShareLock",
    "RowShareLock",
    "RowExclusiveLock",
    "ShareUpdateExclusiveLock",
    "ShareLock",
    "ShareRowExclusiveLock",
    "ExclusiveLock",
    "AccessExclusiveLock",
]

STATEMENTS = [
    ("2602", "ALTER TABLE products ADD COLUMN IF NOT EXISTS slug text"),
    (
        "2602",
        "CREATE OR REPLACE FUNCTION products_set_slug() RETURNS trigger LANGUAGE plpgsql"
        " AS $$ BEGIN RETURN NEW; END $$",
    ),
    (
        "2602",
        "CREATE OR REPLACE TRIGGER products_set_slug BEFORE INSERT ON products"
        " FOR EACH ROW EXECUTE FUNCTION products_set_slug()",
    ),
    (
        "2603",
        "ALTER TABLE products ADD CONSTRAINT products_slug_not_null"
        " CHECK (slug IS NOT NULL) NOT VALID",
    ),
    ("2603", "ALTER TABLE products VALIDATE CONSTRAINT products_slug_not_null"),
    ("2603", "ALTER TABLE products ALTER COLUMN slug SET NOT NULL"),
    ("2603", "ALTER TABLE products DROP CONSTRAINT IF EXISTS products_slug_not_null"),
    ("2604", "ALTER TABLE orders ADD COLUMN IF NOT EXISTS idempotency_key text"),
    (
        "2604",
        "ALTER TABLE orders ADD CONSTRAINT orders_idempotency_key_length"
        " CHECK (length(idempotency_key) BETWEEN 8 AND 200) NOT VALID",
    ),
    ("2604", "ALTER TABLE orders VALIDATE CONSTRAINT orders_idempotency_key_length"),
]

LOCKS = """
SELECT c.relname, l.mode
FROM pg_locks l JOIN pg_class c ON c.oid = l.relation
WHERE l.pid = pg_backend_pid() AND c.relkind IN ('r', 'p')
  AND c.relnamespace <> 'pg_catalog'::regnamespace
"""


def main() -> None:
    with scratch_db() as dsn:
        migrate.upgrade(dsn, "2601")
        with psycopg.connect(dsn) as c:
            for rev, stmt in STATEMENTS:
                c.execute(stmt)
                strongest: dict[str, str] = {}
                for rel, mode in c.execute(LOCKS).fetchall():
                    if STRENGTH.index(mode) > STRENGTH.index(strongest.get(rel, STRENGTH[0])):
                        strongest[rel] = mode
                    strongest.setdefault(rel, mode)
                c.commit()
                locks = ", ".join(f"{r}: {m}" for r, m in sorted(strongest.items()))
                print(f"{rev}  {stmt[:66]:<66}  {locks}")


if __name__ == "__main__":
    main()
