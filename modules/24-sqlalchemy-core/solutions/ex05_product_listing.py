"""Exercise 5: a dynamic product listing: optional filters, whitelisted sorts, keyset pages."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from bazaar.sa.engine import create_engine
from bazaar.sa.tables import categories, inventory, products

p, c = products, categories

# sort name -> list of (key name, direction); the last key is always the unique id
SORTS: dict[str, list[tuple[str, str]]] = {
    "price": [("price", "asc"), ("id", "asc")],
    "-price": [("price", "desc"), ("id", "desc")],
    "newest": [("created_at", "desc"), ("id", "desc")],
    "relevance": [("rank", "desc"), ("id", "asc")],  # mixed directions
}


@dataclass
class Page:
    rows: list[Any]
    next_cursor: str | None


def encode(values: list[Any]) -> str:
    raw = json.dumps([str(v) if isinstance(v, Decimal) else v for v in values], default=str)
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode(token: str) -> list[Any]:
    return json.loads(base64.urlsafe_b64decode(token.encode()))


def revive(col: sa.ColumnElement, value: Any) -> Any:
    """JSON gave us str/float/int back; turn them into what the column's type binds."""
    py = col.type.python_type
    if py is datetime:
        return datetime.fromisoformat(value)
    return py(value)


def subtree(slug: str) -> sa.CTE:
    tree = sa.select(c.c.id).where(c.c.slug == slug).cte(recursive=True)
    ch = c.alias()
    return tree.union_all(sa.select(ch.c.id).join(tree, ch.c.parent_id == tree.c.id))


def after_clause(keys: list[tuple[sa.ColumnElement, str]], values: list[Any]):
    """Rows strictly after `values` in the given ordering, for any mix of directions:
    k1 > v1 OR (k1 = v1 AND k2 > v2) OR ... with > or < per direction."""
    ors = []
    for i, (col, direction) in enumerate(keys):
        cmp = col > values[i] if direction == "asc" else col < values[i]
        ors.append(sa.and_(*[k == v for (k, _), v in zip(keys[:i], values[:i], strict=True)], cmp))
    return sa.or_(*ors)


def list_products(
    conn: sa.Connection,
    *,
    category: str | None = None,
    min_price: Decimal | None = None,
    max_price: Decimal | None = None,
    tags_all: list[str] | None = None,
    tags_any: list[str] | None = None,
    attrs: dict[str, Any] | None = None,
    in_stock: bool = False,
    q: str | None = None,
    sort: str = "price",
    limit: int = 20,
    cursor: str | None = None,
) -> Page:
    if sort not in SORTS:
        raise ValueError(f"unknown sort {sort!r}; use one of {sorted(SORTS)}")
    if sort == "relevance" and not q:
        raise ValueError("sort=relevance needs q")
    tsq = sa.func.websearch_to_tsquery("english", q) if q else None
    # ts_rank() returns real (float4). Its text form is rounded ("0.9991941"), so
    # the value we put in the cursor would not compare equal to the next query's
    # rank. float8 prints its shortest exact representation: round-trips safely.
    rank = (
        sa.cast(sa.func.ts_rank(p.c.search_vector, tsq), pg.DOUBLE_PRECISION)
        if tsq is not None
        else sa.null()
    )
    cols = {"price": p.c.price, "id": p.c.id, "created_at": p.c.created_at, "rank": rank}

    stmt = sa.select(p.c.id, p.c.title, p.c.price, p.c.created_at, rank.label("rank")).where(
        p.c.status == "active"
    )
    if category:
        stmt = stmt.where(p.c.category_id.in_(sa.select(subtree(category).c.id)))
    if min_price is not None:
        stmt = stmt.where(p.c.price >= min_price)
    if max_price is not None:
        stmt = stmt.where(p.c.price <= max_price)
    if tags_all:
        stmt = stmt.where(p.c.tags.contains(sa.cast(tags_all, pg.ARRAY(sa.Text))))
    if tags_any:
        stmt = stmt.where(p.c.tags.overlap(sa.cast(tags_any, pg.ARRAY(sa.Text))))
    if attrs:
        stmt = stmt.where(p.c.attributes.contains(attrs))
    if in_stock:
        stmt = stmt.where(
            sa.exists().where(
                inventory.c.product_id == p.c.id, inventory.c.quantity > inventory.c.reserved
            )
        )
    if tsq is not None:
        stmt = stmt.where(p.c.search_vector.bool_op("@@")(tsq))

    keys = [(cols[name], direction) for name, direction in SORTS[sort]]
    if cursor:
        values = [revive(col, v) for (col, _), v in zip(keys, decode(cursor), strict=True)]
        stmt = stmt.where(after_clause(keys, values))
    stmt = stmt.order_by(*[col.asc() if d == "asc" else col.desc() for col, d in keys])
    rows = conn.execute(stmt.limit(limit + 1)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    token = None
    if more:
        last = rows[-1]
        token = encode([getattr(last, name) for name, _ in SORTS[sort]])
    return Page(rows, token)


def all_pages(conn, **kw) -> list[int]:
    ids, cursor, pages = [], None, 0
    while True:
        page = list_products(conn, cursor=cursor, **kw)
        ids += [r.id for r in page.rows]
        pages += 1
        if page.next_cursor is None:
            return ids
        cursor = page.next_cursor


if __name__ == "__main__":
    engine = create_engine()
    cases = [
        dict(category="audio", max_price=Decimal("30"), in_stock=True, sort="price"),
        dict(category="computers", tags_any=["bestseller", "new"], sort="-price"),
        dict(category="laptops", attrs={"ram_gb": 64, "storage": {"type": "ssd"}}, sort="newest"),
        dict(q="wireless headphones", sort="relevance", min_price=Decimal("50")),
        dict(tags_all=["gift", "eco"], category="kitchen", sort="price"),
    ]
    with engine.connect() as conn:
        for kw in cases:
            everything = list_products(conn, limit=100_000, **kw).rows
            paged = all_pages(conn, limit=25, **kw)
            assert paged == [r.id for r in everything], kw
            first = everything[0]
            print(f"{len(everything):>5} rows, {-(-len(everything) // 25):>3} pages, identical;"
                  f" first id={first.id} price={first.price}  {kw}")
        try:
            list_products(conn, sort="price; DROP TABLE products")
        except ValueError as e:
            print("rejected:", e)
