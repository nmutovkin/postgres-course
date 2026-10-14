"""Exercise 4: a safe dynamic filter builder with psycopg.sql.

Run directly (prints results) or with pytest (runs the test_* functions).
"""

import os
from decimal import Decimal
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")

SORTABLE = {"id", "price", "created_at", "title"}
MAX_LIMIT = 500


def _escape_like(s: str) -> str:
    """Make user text match literally inside LIKE/ILIKE (default escape char is backslash)."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_product_query(
    filters: dict[str, Any], *, sort: str = "id", desc: bool = False, limit: int = 50
) -> tuple[sql.Composed, dict[str, Any]]:
    clauses: list[sql.Composable] = []
    params: dict[str, Any] = {}
    for key, value in filters.items():
        match key:
            case "seller_id" | "status":
                clauses.append(sql.SQL("{} = {}").format(sql.Identifier("p", key), sql.Placeholder(key)))
                params[key] = value
            case "category_id":
                if isinstance(value, list | tuple | set):
                    clauses.append(sql.SQL("p.category_id = ANY(%(category_id)s)"))
                    params[key] = list(value)
                else:
                    clauses.append(sql.SQL("p.category_id = %(category_id)s"))
                    params[key] = value
            case "min_price":
                clauses.append(sql.SQL("p.price >= %(min_price)s"))
                params[key] = Decimal(str(value))
            case "max_price":
                clauses.append(sql.SQL("p.price <= %(max_price)s"))
                params[key] = Decimal(str(value))
            case "tags":  # all of these tags
                clauses.append(sql.SQL("p.tags @> %(tags)s::text[]"))
                params[key] = list(value)
            case "title_contains":
                clauses.append(sql.SQL("p.title ILIKE %(title_contains)s"))
                params[key] = f"%{_escape_like(value)}%"
            case "attributes":  # JSON containment, uses the GIN index from module 11
                clauses.append(sql.SQL("p.attributes @> %(attributes)s"))
                params[key] = Jsonb(value)
            case _:
                raise ValueError(f"unknown filter {key!r}")
    if sort not in SORTABLE:
        raise ValueError(f"cannot sort by {sort!r}")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    where = sql.SQL(" AND ").join(clauses) if clauses else sql.SQL("true")
    query = sql.SQL(
        "SELECT p.id, p.title, p.price FROM products p WHERE {where}"
        " ORDER BY {sort} {dir}, p.id LIMIT {limit}"
    ).format(
        where=where,
        sort=sql.Identifier("p", sort),
        dir=sql.SQL("DESC" if desc else "ASC"),
        limit=sql.Literal(limit),
    )
    return query, params


def search(conn: psycopg.Connection, filters: dict[str, Any], **kw: Any) -> list[tuple]:
    q, params = build_product_query(filters, **kw)
    return conn.execute(q, params).fetchall()


# --- tests ---------------------------------------------------------------------------------

LAPTOPS = {"category_id": 3, "status": "active", "max_price": 500, "attributes": {"ram_gb": 16}}


def test_laptops() -> None:
    with psycopg.connect(DSN) as conn:
        rows = search(conn, LAPTOPS, sort="price", desc=True, limit=500)
        assert rows and all(r[2] <= 500 for r in rows)
        assert [r[2] for r in rows] == sorted((r[2] for r in rows), reverse=True)


def test_rejects_unknown_things() -> None:
    for bad in [lambda: build_product_query({"id; DROP TABLE x": 1}),
                lambda: build_product_query({}, sort="price; --"),
                lambda: build_product_query({}, limit=10_000)]:  # fmt: skip
        try:
            bad()
        except ValueError:
            continue
        raise AssertionError("should have raised")


def test_like_wildcards_are_literal() -> None:
    with psycopg.connect(DSN) as conn:
        assert search(conn, {"title_contains": "%"}) == []  # no title contains a literal %
        assert len(search(conn, {"title_contains": "_"}, limit=5)) == 0


if __name__ == "__main__":
    with psycopg.connect(DSN) as conn:
        q, params = build_product_query(LAPTOPS, sort="price", desc=True, limit=3)
        print(q.as_string(conn))
        print(params)
        for row in conn.execute(q, params):
            print(row)
        n = conn.execute(
            sql.SQL("SELECT count(*) FROM ({}) t").format(
                build_product_query(LAPTOPS, limit=500)[0]
            ),
            build_product_query(LAPTOPS)[1],
        ).fetchone()[0]
        print("matching laptops (up to the 500 cap):", n)
    test_laptops()
    test_rejects_unknown_things()
    test_like_wildcards_are_literal()
    print("tests passed")
