"""Compare SQLAlchemy metadata with the live database ("schema drift").

    from bazaar.sa.drift import compare
    from bazaar.sa.tables import metadata, view_metadata

    with engine.connect() as conn:
        problems = compare(conn, metadata, view_metadata)   # [] when they agree

The comparison uses the Inspector (reflection) and checks what matters to
code that builds SQL from the metadata: which tables and columns exist, their
types and nullability, identity and generated columns, primary keys, foreign
keys (with ON DELETE), unique and check constraint names, and index names
with their uniqueness. It deliberately does not compare CHECK expressions or
server default texts character by character: Postgres normalizes them
('price >= 0' comes back as 'price >= 0::numeric'), and comparing normalized
SQL text is a job for a migration tool (module 26).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Dialect


def _type_str(t: sa.types.TypeEngine, dialect: Dialect) -> str:
    return t.compile(dialect=dialect).lower()


def _partitions(conn: Connection) -> set[str]:
    rows = conn.execute(
        sa.text(
            "SELECT c.relname FROM pg_class c"
            " WHERE c.relispartition AND c.relnamespace = current_schema()::regnamespace"
        )
    )
    return set(rows.scalars())


def _compare_columns(
    name: str, table: sa.Table, reflected: list, dialect: Dialect, *, check_nullable: bool = True
) -> list[str]:
    out: list[str] = []
    db_cols = {c["name"]: c for c in reflected}
    for col in table.columns:
        if col.system:  # xmin & co. exist on every table but are never reflected
            continue
        db = db_cols.pop(col.name, None)
        if db is None:
            out.append(f"{name}.{col.name}: column missing in database")
            continue
        mine, theirs = _type_str(col.type, dialect), _type_str(db["type"], dialect)
        if mine != theirs:
            out.append(f"{name}.{col.name}: type {mine} in metadata, {theirs} in database")
        if check_nullable and col.nullable != db["nullable"]:
            out.append(
                f"{name}.{col.name}: nullable={col.nullable} in metadata,"
                f" {db['nullable']} in database"
            )
        if check_nullable:
            if (col.identity is not None) != bool(db.get("identity")):
                out.append(f"{name}.{col.name}: identity differs")
            if (col.computed is not None) != bool(db.get("computed")):
                out.append(f"{name}.{col.name}: generated (computed) column differs")
            has_default = col.server_default is not None and col.identity is None
            if not col.computed and has_default != (db["default"] is not None):
                out.append(f"{name}.{col.name}: server default present in only one of them")
    out += [f"{name}.{c}: column not in metadata" for c in db_cols]
    return out


def compare(
    conn: Connection,
    metadata: sa.MetaData,
    view_metadata: sa.MetaData | None = None,
    *,
    mapping_only: bool = False,
) -> list[str]:
    """Return a list of human-readable differences (empty = no drift).

    mapping_only=True checks only what an ORM mapping relies on: the mapped
    tables exist, with the same columns, types, nullability, primary and
    foreign keys. Unmapped tables, indexes and CHECK constraints are ignored.
    """
    insp = sa.inspect(conn)
    dialect = conn.dialect
    out: list[str] = []

    db_tables = set(insp.get_table_names()) - _partitions(conn)
    meta_tables = set(metadata.tables)
    out += [f"{t}: table missing in database" for t in sorted(meta_tables - db_tables)]
    if not mapping_only:
        out += [f"{t}: table not in metadata" for t in sorted(db_tables - meta_tables)]

    for name in sorted(meta_tables & db_tables):
        table = metadata.tables[name]
        out += _compare_columns(name, table, insp.get_columns(name), dialect)

        pk = insp.get_pk_constraint(name)
        mine_pk = [c.name for c in table.primary_key.columns]
        if mine_pk != pk["constrained_columns"] or table.primary_key.name != pk["name"]:
            out.append(
                f"{name}: primary key {table.primary_key.name}{mine_pk} in metadata,"
                f" {pk['name']}{pk['constrained_columns']} in database"
            )

        mine_fks = {
            (
                fk.name,
                tuple(fk.column_keys),
                fk.referred_table.name,
                tuple(e.column.name for e in fk.elements),
                (fk.ondelete or "NO ACTION").upper(),
            )
            for fk in table.foreign_key_constraints
        }
        db_fks = {
            (
                fk["name"],
                tuple(fk["constrained_columns"]),
                fk["referred_table"],
                tuple(fk["referred_columns"]),
                fk["options"].get("ondelete", "NO ACTION").upper(),
            )
            for fk in insp.get_foreign_keys(name)
        }
        out += [f"{name}: foreign key {fk} only in metadata" for fk in sorted(mine_fks - db_fks)]
        out += [f"{name}: foreign key {fk} only in database" for fk in sorted(db_fks - mine_fks)]

        if mapping_only:
            continue

        mine_uq = {
            (c.name, tuple(col.name for col in c.columns))
            for c in table.constraints
            if isinstance(c, sa.UniqueConstraint)
        }
        db_uq = {(u["name"], tuple(u["column_names"])) for u in insp.get_unique_constraints(name)}
        out += [f"{name}: unique constraint {u} only in metadata" for u in sorted(mine_uq - db_uq)]
        out += [f"{name}: unique constraint {u} only in database" for u in sorted(db_uq - mine_uq)]

        mine_ck = {c.name for c in table.constraints if isinstance(c, sa.CheckConstraint)}
        db_ck = {c["name"] for c in insp.get_check_constraints(name)}
        out += [f"{name}: check {c} only in metadata" for c in sorted(mine_ck - db_ck)]
        out += [f"{name}: check {c} only in database" for c in sorted(db_ck - mine_ck)]

        # Indexes, including the ones backing UNIQUE and EXCLUDE constraints
        # (reflection reports those with `duplicates_constraint`).
        mine_ix = {(ix.name, bool(ix.unique)) for ix in table.indexes}
        mine_ix |= {
            (c.name, False)
            for c in table.constraints
            if c.__class__.__name__ == "ExcludeConstraint"
        }
        db_ix = {
            (ix["name"], bool(ix["unique"]))
            for ix in insp.get_indexes(name)
            if not (ix.get("duplicates_constraint") and ix["unique"])
        }
        out += [f"{name}: index {ix} only in metadata" for ix in sorted(mine_ix - db_ix)]
        out += [f"{name}: index {ix} only in database" for ix in sorted(db_ix - mine_ix)]

    if view_metadata is not None:
        views = set(insp.get_view_names()) | set(insp.get_materialized_view_names())
        for name, table in sorted(view_metadata.tables.items()):
            if name not in views:
                out.append(f"{name}: view missing in database")
                continue
            out += _compare_columns(
                name, table, insp.get_columns(name), dialect, check_nullable=False
            )
    return out
