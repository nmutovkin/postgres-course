"""A canonical description of a schema from the system catalogs, and a diff (module 26).

    from bazaar.catalog import snapshot, diff

    with engine_a.connect() as a, engine_b.connect() as b:
        problems = diff(snapshot(a), snapshot(b))      # [] when the schemas are equal

Used to prove that a database built by `alembic upgrade head` from nothing is
the same as one built by project/db + `alembic stamp` + `alembic upgrade head`.

Every object becomes one entry "kind:name" -> "definition", where the
definition comes from PostgreSQL's own deparsers (format_type, pg_get_expr,
pg_get_constraintdef, pg_get_indexdef, pg_get_functiondef, pg_get_triggerdef,
pg_get_viewdef, ...). Two databases whose catalogs agree on all of these
behave the same for every query; OIDs, physical column numbers, statistics and
data are deliberately left out. Unlike diffing two `pg_dump --schema-only`
outputs, this needs no pg_dump binary of the right version, and a difference
is reported per object rather than as a text diff.
"""

from __future__ import annotations

import sqlalchemy as sa

# (kind, query). Each query returns rows (name, definition) for schema :s.
_QUERIES: list[tuple[str, str]] = [
    (
        "relation",
        """
        SELECT c.relname,
               concat_ws(' | ', c.relkind, c.relpersistence,
                   'owner=' || pg_get_userbyid(c.relowner),
                   'acl=' || (SELECT string_agg(a::text, ',' ORDER BY a::text)
                              FROM unnest(c.relacl) AS a),
                   CASE WHEN c.relrowsecurity THEN 'rls' END,
                   CASE WHEN c.relforcerowsecurity THEN 'force_rls' END,
                   'partkey=' || pg_get_partkeydef(c.oid),
                   'bound=' || pg_get_expr(c.relpartbound, c.oid),
                   'parent=' || (SELECT i.inhparent::regclass::text FROM pg_inherits i
                                 WHERE i.inhrelid = c.oid),
                   'comment=' || obj_description(c.oid, 'pg_class'),
                   'options=' || array_to_string(c.reloptions, ','))
        FROM pg_class c
        WHERE c.relnamespace = CAST(:s AS regnamespace)
          AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f', 'c')
          AND c.relname <> 'alembic_version'
        """,
    ),
    (
        "column",
        """
        SELECT c.relname || '.' || a.attname,
               concat_ws(' | ',
                   'pos=' || row_number() OVER (PARTITION BY c.oid ORDER BY a.attnum),
                   format_type(a.atttypid, a.atttypmod),
                   CASE WHEN a.attnotnull THEN 'not null' END,
                   'default=' || pg_get_expr(d.adbin, d.adrelid),
                   'identity=' || nullif(a.attidentity::text, ''),
                   'generated=' || nullif(a.attgenerated::text, ''),
                   'collation=' || CASE WHEN a.attcollation <> t.typcollation
                                        THEN a.attcollation::regcollation::text END,
                   'acl=' || (SELECT string_agg(x::text, ',' ORDER BY x::text)
                              FROM unnest(a.attacl) AS x),
                   'comment=' || col_description(c.oid, a.attnum))
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_type t ON t.oid = a.atttypid
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE c.relnamespace = CAST(:s AS regnamespace)
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'c')
          AND c.relname <> 'alembic_version'
          AND a.attnum > 0 AND NOT a.attisdropped
        """,
    ),
    (
        "constraint",
        """
        SELECT coalesce(c.relname, t.typname) || '.' || con.conname,
               concat_ws(' | ', con.contype, pg_get_constraintdef(con.oid),
                   CASE WHEN NOT con.convalidated THEN 'NOT VALID' END)
        FROM pg_constraint con
        LEFT JOIN pg_class c ON c.oid = con.conrelid
        LEFT JOIN pg_type t ON t.oid = con.contypid
        WHERE con.connamespace = CAST(:s AS regnamespace)
          AND coalesce(c.relname, '') <> 'alembic_version'
        """,
    ),
    (
        "index",
        """
        SELECT ci.relname,
               concat_ws(' | ', pg_get_indexdef(i.indexrelid),
                   CASE WHEN NOT i.indisvalid THEN 'INVALID' END)
        FROM pg_index i
        JOIN pg_class ci ON ci.oid = i.indexrelid
        JOIN pg_class ct ON ct.oid = i.indrelid
        WHERE ci.relnamespace = CAST(:s AS regnamespace) AND ct.relname <> 'alembic_version'
        """,
    ),
    (
        "function",
        """
        SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
               concat_ws(' | ', pg_get_functiondef(p.oid),
                   'owner=' || pg_get_userbyid(p.proowner),
                   'acl=' || (SELECT string_agg(a::text, ',' ORDER BY a::text)
                              FROM unnest(p.proacl) AS a),
                   'comment=' || obj_description(p.oid, 'pg_proc'))
        FROM pg_proc p
        WHERE p.pronamespace = CAST(:s AS regnamespace) AND p.prokind <> 'a'
        """,
    ),
    (
        "trigger",
        """
        SELECT c.relname || '.' || t.tgname,
               concat_ws(' | ', pg_get_triggerdef(t.oid), 'enabled=' || t.tgenabled::text)
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        WHERE c.relnamespace = CAST(:s AS regnamespace) AND NOT t.tgisinternal
        """,
    ),
    (
        "view",
        """
        SELECT c.relname, pg_get_viewdef(c.oid, true)
        FROM pg_class c
        WHERE c.relnamespace = CAST(:s AS regnamespace) AND c.relkind IN ('v', 'm')
        """,
    ),
    (
        "policy",
        """
        SELECT tablename || '.' || policyname,
               concat_ws(' | ', permissive, cmd, roles::text, qual, with_check)
        FROM pg_policies
        WHERE schemaname = :s
        """,
    ),
    (
        "type",
        """
        SELECT t.typname,
               concat_ws(' | ', t.typtype,
                   'labels=' || (SELECT string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder)
                                 FROM pg_enum e WHERE e.enumtypid = t.oid),
                   'base=' || CASE WHEN t.typtype = 'd'
                                   THEN format_type(t.typbasetype, t.typtypmod) END,
                   CASE WHEN t.typnotnull THEN 'not null' END,
                   'owner=' || pg_get_userbyid(t.typowner))
        FROM pg_type t
        WHERE t.typnamespace = CAST(:s AS regnamespace)
          AND t.typtype IN ('e', 'd', 'r', 'c')
          AND (t.typrelid = 0 OR (SELECT relkind FROM pg_class WHERE oid = t.typrelid) = 'c')
        """,
    ),
    (
        "sequence",
        """
        SELECT c.relname,
               concat_ws(' | ', format_type(s.seqtypid, NULL), s.seqstart, s.seqincrement,
                   s.seqmin, s.seqmax, s.seqcache, s.seqcycle,
                   'owned_by=' || (SELECT d.refobjid::regclass || '.' || a.attname
                                   FROM pg_depend d
                                   JOIN pg_attribute a ON a.attrelid = d.refobjid
                                                      AND a.attnum = d.refobjsubid
                                   WHERE d.objid = c.oid AND d.deptype IN ('a', 'i')
                                     AND d.classid = 'pg_class'::regclass
                                   LIMIT 1))
        FROM pg_sequence s
        JOIN pg_class c ON c.oid = s.seqrelid
        WHERE c.relnamespace = CAST(:s AS regnamespace)
        """,
    ),
    (
        "statistics",
        """
        SELECT s.stxname, pg_get_statisticsobjdef(s.oid)
        FROM pg_statistic_ext s
        WHERE s.stxnamespace = CAST(:s AS regnamespace)
        """,
    ),
    (
        "schema",
        """
        SELECT n.nspname,
               concat_ws(' | ', 'owner=' || pg_get_userbyid(n.nspowner),
                   'acl=' || (SELECT string_agg(a::text, ',' ORDER BY a::text)
                              FROM unnest(n.nspacl) AS a))
        FROM pg_namespace n
        WHERE n.nspname = :s
        """,
    ),
    (
        "default_acl",
        """
        SELECT pg_get_userbyid(d.defaclrole) || ':' || d.defaclobjtype::text,
               (SELECT string_agg(a::text, ',' ORDER BY a::text) FROM unnest(d.defaclacl) AS a)
        FROM pg_default_acl d
        WHERE d.defaclnamespace = CAST(:s AS regnamespace)
        """,
    ),
    (
        "extension",
        "SELECT extname, extversion FROM pg_extension WHERE extname <> 'plpgsql' AND :s <> ''",
    ),
]


def snapshot(conn: sa.Connection, schema: str = "bazaar") -> dict[str, str]:
    """Describe every object of `schema` (plus installed extensions) as text."""
    out: dict[str, str] = {}
    for kind, query in _QUERIES:
        for name, definition in conn.execute(sa.text(query), {"s": schema}):
            out[f"{kind}:{name}"] = definition or ""
    return out


def diff(
    a: dict[str, str], b: dict[str, str], *, labels: tuple[str, str] = ("a", "b")
) -> list[str]:
    """Human-readable differences between two snapshots ([] = identical)."""
    la, lb = labels
    out = [f"only in {la}: {k}" for k in sorted(a.keys() - b.keys())]
    out += [f"only in {lb}: {k}" for k in sorted(b.keys() - a.keys())]
    for k in sorted(a.keys() & b.keys()):
        if a[k] != b[k]:
            out.append(f"differs: {k}\n    {la}: {a[k]}\n    {lb}: {b[k]}")
    return out
