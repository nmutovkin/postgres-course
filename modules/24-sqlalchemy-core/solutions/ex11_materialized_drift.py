"""Exercise 11: a stricter drift check: build the metadata for real, let Postgres compare.

Reflection normalizes some things and loses others (CHECK expressions come back
rewritten, index expressions and WHERE clauses as text, EXCLUDE constraints
only partly). Instead of comparing our Python description with reflected
text, create the metadata in a scratch schema inside a transaction, and ask
Postgres for the canonical definition of every constraint and index in both
schemas: pg_get_constraintdef() and pg_get_indexdef() print both the same way.
Everything is rolled back.
"""

import sqlalchemy as sa

from bazaar.sa.drift import compare
from bazaar.sa.engine import create_engine
from bazaar.sa.tables import metadata

DEFS = sa.text(
    """
    SELECT 'constraint' AS kind, c.relname AS tbl, k.conname AS name,
           pg_get_constraintdef(k.oid) AS def
    FROM pg_constraint k
    JOIN pg_class c ON c.oid = k.conrelid
    WHERE c.relnamespace = CAST(:schema AS regnamespace) AND NOT c.relispartition
    UNION ALL
    SELECT 'index', c.relname, i.relname,
           regexp_replace(pg_get_indexdef(i.oid), ' ON (ONLY )?\\S+\\.', ' ON \\1')
    FROM pg_index x
    JOIN pg_class i ON i.oid = x.indexrelid
    JOIN pg_class c ON c.oid = x.indrelid
    WHERE c.relnamespace = CAST(:schema AS regnamespace) AND NOT c.relispartition
      AND i.relkind IN ('i', 'I')
    """
)


def materialized_drift(conn: sa.Connection, md: sa.MetaData) -> list[str]:
    with conn.begin_nested() as sp:
        conn.execute(sa.text("CREATE SCHEMA drift_tmp"))
        # New tables land in drift_tmp; enum types, the domain and functions
        # (tags_to_text) still resolve from bazaar and public.
        conn.execute(sa.text("SET LOCAL search_path = drift_tmp, bazaar, public"))
        md.create_all(conn, checkfirst=False)  # checkfirst would find bazaar's tables
        mine = {tuple(r) for r in conn.execute(DEFS, {"schema": "drift_tmp"})}
        conn.execute(sa.text("SET LOCAL search_path = bazaar, public"))
        real = {tuple(r) for r in conn.execute(DEFS, {"schema": "bazaar"})}
        sp.rollback()
    tables = set(md.tables)
    real = {r for r in real if r[1] in tables}
    out = [f"only in metadata: {r}" for r in sorted(mine - real)]
    out += [f"only in database: {r}" for r in sorted(real - mine)]
    return out


if __name__ == "__main__":
    engine = create_engine()
    with engine.connect() as conn:
        problems = materialized_drift(conn, metadata)
        n = conn.execute(DEFS, {"schema": "bazaar"}).all()
        print(f"{len(problems)} differences ({len(n)} constraints and indexes in the schema)")
        for p in problems:
            print(" ", p)

        # Now introduce a subtle drift that reflection-based comparison misses:
        # same constraint name, different rule.
        conn.execute(sa.text("ALTER TABLE reviews DROP CONSTRAINT reviews_rating_range"))
        conn.execute(
            sa.text(
                "ALTER TABLE reviews ADD CONSTRAINT reviews_rating_range"
                " CHECK (rating BETWEEN 1 AND 10)"
            )
        )
        print("compare() (names only):", compare(conn, metadata))
        print("materialized_drift():")
        for p in materialized_drift(conn, metadata):
            print(" ", p)
        conn.rollback()
