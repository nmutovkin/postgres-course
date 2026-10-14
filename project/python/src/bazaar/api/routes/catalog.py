"""The storefront catalog: browse, product page, search. Read-only, on the read engine.

GET /products          filters (category subtree, price range, JSONB attributes,
                       in stock), sorts (newest, price_asc, price_desc), keyset pages
GET /products/{id}     product page: seller, category breadcrumb, stock, rating
GET /search?q=         module 13's search_products(): full text, fuzzy fallback

Why Core and not the ORM here: the listing is a *dynamic* query (filters and
sort chosen per request) that returns flat rows for JSON; the ORM would add
identity-map bookkeeping for objects nobody modifies. The product page is a
fixed statement with a LATERAL aggregate, and search is a database function:
plain text() for both.
"""

from __future__ import annotations

import base64
import json
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Query

from bazaar.api.db import ReadSession
from bazaar.api.errors import ApiError, InvalidCursor
from bazaar.api.middleware import TaggedRoute
from bazaar.api.schemas import (
    CategoryRef,
    Page,
    ProductDetail,
    ProductSummary,
    SearchHit,
    SearchResult,
)
from bazaar.errors import NotFound
from bazaar.sa.tables import categories, inventory, products

router = APIRouter(route_class=TaggedRoute, tags=["catalog"])


class Sort(StrEnum):
    newest = "newest"  # id DESC: identity ids follow insertion order, and the pkey serves it
    price_asc = "price_asc"
    price_desc = "price_desc"


# --- keyset cursors -------------------------------------------------------------------------
# The cursor is the sort key of the last row of the page, opaque to the client
# (base64 of JSON). It names its sort, so a cursor cannot be replayed with
# another ?sort= (the keys would mean something else).


def _encode(sort: Sort, row: Any) -> str:
    key = [sort.value, row.id] if sort is Sort.newest else [sort.value, str(row.price), row.id]
    return base64.urlsafe_b64encode(json.dumps(key).encode()).decode().rstrip("=")


def _decode(token: str, sort: Sort) -> tuple[Any, ...]:
    try:
        raw = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        if raw[0] != sort.value:
            raise InvalidCursor(f"cursor was made for sort={raw[0]}, not sort={sort.value}")
        if sort is Sort.newest:
            return (int(raw[1]),)
        return Decimal(raw[1]), int(raw[2])
    except InvalidCursor:
        raise
    except (ValueError, TypeError, IndexError, InvalidOperation, json.JSONDecodeError):
        raise InvalidCursor("malformed cursor") from None


def _parse_attributes(pairs: list[str]) -> dict[str, Any]:
    """?attr=ram_gb:16&attr=cpu:M3 -> {"ram_gb": 16, "cpu": "M3"} (JSON values when they
    parse as JSON numbers/booleans, strings otherwise)."""
    out: dict[str, Any] = {}
    for pair in pairs:
        key, sep, value = pair.partition(":")
        if not sep or not key:
            raise ApiError(f"attr must look like key:value, got {pair!r}")
        try:
            parsed = json.loads(value)
            out[key] = parsed if isinstance(parsed, int | float | bool) else value
        except json.JSONDecodeError:
            out[key] = value
    return out


def category_subtree(category_id: int) -> sa.CTE:
    """WITH RECURSIVE subtree: the category and all its descendants (module 06)."""
    base = sa.select(categories.c.id).where(categories.c.id == category_id)
    tree = base.cte("subtree", recursive=True)
    child = sa.select(categories.c.id).join(tree, categories.c.parent_id == tree.c.id)
    return tree.union_all(child)


def _sort_keys(cols: Any, sort: Sort) -> tuple[list[Any], Any]:
    """(ORDER BY list, keyset column tuple) for a sort, on `cols` (a table's or a subquery's)."""
    if sort is Sort.newest:
        return [cols.id.desc()], (cols.id,)
    if sort is Sort.price_asc:
        return [cols.price, cols.id], (cols.price, cols.id)
    return [cols.price.desc(), cols.id.desc()], (cols.price, cols.id)


def products_query(
    *,
    category_id: int | None = None,
    min_price: Decimal | None = None,
    max_price: Decimal | None = None,
    attributes: dict[str, Any] | None = None,
    in_stock: bool = False,
    sort: Sort = Sort.newest,
    after: tuple[Any, ...] | None = None,
    limit: int = 20,
) -> sa.Select:
    """The listing statement. Two shapes, chosen by which index can drive it:

    * merge of per-category top-N lists (the usual case): for each category of
      the subtree (or of the whole catalog), the first limit+1 rows straight
      from an index in sort order, (category_id, price) or (category_id, id);
      then the best limit+1 of those. Cost: a few index probes per category,
      whatever the size of the categories.
    * a plain filtered query, when another access path is better: an attribute
      filter (the GIN index on attributes is selective) or "newest first" over
      the whole catalog (the primary key, backwards).
    """
    p, i = products, inventory
    available = (i.c.quantity - i.c.reserved).label("available")
    stmt = (
        sa.select(
            p.c.id,
            p.c.title,
            p.c.slug,
            p.c.price,
            p.c.category_id,
            p.c.seller_id,
            available,
            p.c.created_at,
        )
        .select_from(p.join(i, i.c.product_id == p.c.id))
        # A literal, not a bound parameter: the partial indexes say WHERE status =
        # 'active', and a generic plan for "status = $1" could not use them.
        .where(sa.text("products.status = 'active'"))
    )
    if min_price is not None:
        stmt = stmt.where(p.c.price >= min_price)
    if max_price is not None:
        stmt = stmt.where(p.c.price <= max_price)
    if attributes:
        stmt = stmt.where(p.c.attributes.contains(attributes))  # @>: the GIN index
    if in_stock:
        stmt = stmt.where(i.c.quantity - i.c.reserved > 0)
    order, key = _sort_keys(p.c, sort)
    if after is not None:
        descending = sort is not Sort.price_asc
        cmp = (
            sa.tuple_(*key) < sa.tuple_(*after)
            if descending
            else sa.tuple_(*key) > sa.tuple_(*after)
        )
        stmt = stmt.where(cmp)

    if attributes or (category_id is None and sort is Sort.newest):
        if category_id is not None:
            subtree = category_subtree(category_id)
            stmt = stmt.where(p.c.category_id.in_(sa.select(subtree.c.id)))
        return stmt.order_by(*order).limit(limit + 1)  # one extra row: is there a next page?

    if category_id is not None:
        cats = category_subtree(category_id)
    else:
        cats = sa.select(categories.c.id).cte("cats")
    top = stmt.where(p.c.category_id == cats.c.id).order_by(*order).limit(limit + 1).lateral("top")
    outer_order, _ = _sort_keys(top.c, sort)
    return (
        sa.select(*top.c)
        .select_from(cats.join(top, sa.true()))
        .order_by(*outer_order)
        .limit(limit + 1)
    )


@router.get("/products", response_model=Page[ProductSummary])
async def list_products(
    session: ReadSession,
    category_id: Annotated[int | None, Query(gt=0, description="includes subcategories")] = None,
    min_price: Annotated[Decimal | None, Query(ge=0)] = None,
    max_price: Annotated[Decimal | None, Query(ge=0)] = None,
    attr: Annotated[list[str], Query(description="key:value, repeatable (JSONB @>)")] = [],  # noqa: B006
    in_stock: bool = False,
    sort: Sort = Sort.newest,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[ProductSummary]:
    stmt = products_query(
        category_id=category_id,
        min_price=min_price,
        max_price=max_price,
        attributes=_parse_attributes(attr),
        in_stock=in_stock,
        sort=sort,
        after=_decode(cursor, sort) if cursor else None,
        limit=limit,
    )
    rows = (await session.execute(stmt)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return Page[ProductSummary](
        items=[ProductSummary.model_validate(r._asdict()) for r in rows],
        next_cursor=_encode(sort, rows[-1]) if more else None,
    )


PRODUCT_PAGE = sa.text("""
    SELECT p.id, p.title, p.slug, p.description, p.price, p.status, p.seller_id,
           s.shop_name, p.attributes, p.tags, p.category_id,
           coalesce(i.quantity - i.reserved, 0) AS available,
           r.rating_avg, r.rating_count
    FROM products p
    JOIN sellers s ON s.id = p.seller_id
    LEFT JOIN inventory i ON i.product_id = p.id
    CROSS JOIN LATERAL (
        SELECT round(avg(rv.rating), 2) AS rating_avg, count(*) AS rating_count
        FROM reviews rv
        WHERE rv.product_id = p.id
    ) r
    WHERE p.id = :id
      AND p.status <> 'draft'
""")

BREADCRUMB = sa.text("""
    WITH RECURSIVE up AS (
        SELECT id, parent_id, name, slug, 0 AS depth FROM categories WHERE id = :cid
        UNION ALL
        SELECT c.id, c.parent_id, c.name, c.slug, up.depth + 1
        FROM categories c JOIN up ON c.id = up.parent_id
    )
    SELECT id, name, slug FROM up ORDER BY depth DESC
""")


@router.get("/products/{product_id}", response_model=ProductDetail)
async def get_product(product_id: int, session: ReadSession) -> ProductDetail:
    row = (await session.execute(PRODUCT_PAGE, {"id": product_id})).mappings().one_or_none()
    if row is None:
        raise NotFound(f"product {product_id}")
    path = (await session.execute(BREADCRUMB, {"cid": row["category_id"]})).mappings().all()
    return ProductDetail(**row, category_path=[CategoryRef(**c) for c in path])


SEARCH = sa.text("SELECT * FROM search_products(:q, :category_id, :limit)")


@router.get("/search", response_model=SearchResult)
async def search(
    session: ReadSession,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    category_id: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> SearchResult:
    # Users choose the input, so they choose the cost: search gets a tighter
    # statement_timeout than the connection default, for this transaction only.
    await session.execute(sa.text("SET LOCAL statement_timeout = '2s'"))
    rows = await session.execute(SEARCH, {"q": q, "category_id": category_id, "limit": limit})
    return SearchResult(query=q, items=[SearchHit(**r) for r in rows.mappings()])
