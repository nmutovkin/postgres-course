"""The async data-access layer: bazaar.repo, but with psycopg's AsyncConnection.

Same SQL (imported from bazaar.repo), same models, same error translation,
same transaction conventions: functions take a connection, never commit, and
use `conn.transaction()` (a savepoint inside an outer transaction) where they
must be atomic on their own.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from bazaar.errors import translate
from bazaar.models import ImportResult, NewProduct, OrderPage, OrderSummary, Product, ProductHit
from bazaar.repo import (
    ENQUEUE_JOB,
    GET_PRODUCT,
    IMPORT_COPY,
    IMPORT_COPY_TYPES,
    IMPORT_INVENTORY,
    IMPORT_STAGING,
    IMPORT_UPSERT,
    PLACE_ORDER,
    import_row,
    make_page,
    order_items_param,
    orders_query,
    search_query,
)


async def get_product(conn: psycopg.AsyncConnection, product_id: int) -> Product | None:
    async with conn.cursor(row_factory=class_row(Product)) as cur:
        await cur.execute(GET_PRODUCT, {"id": product_id})
        return await cur.fetchone()


async def search_products(
    conn: psycopg.AsyncConnection,
    query: str,
    *,
    limit: int = 20,
    category_id: int | None = None,
    max_price: Decimal | None = None,
) -> list[ProductHit]:
    stmt, params = search_query(category_id, max_price)
    async with conn.cursor(row_factory=class_row(ProductHit)) as cur:
        await cur.execute(stmt, {"q": query, "limit": limit, **params})
        return await cur.fetchall()


async def list_orders_for_user(
    conn: psycopg.AsyncConnection, user_id: int, *, limit: int = 20, after: str | None = None
) -> OrderPage:
    stmt, params = orders_query(after)
    async with conn.cursor(row_factory=class_row(OrderSummary)) as cur:
        await cur.execute(stmt, {"user_id": user_id, "limit_plus_one": limit + 1, **params})
        return make_page(await cur.fetchall(), limit)


async def place_order(
    conn: psycopg.AsyncConnection,
    user_id: int,
    items: Sequence[tuple[int, int]],
    shipping_address_id: int | None = None,
) -> list[int]:
    params = {
        "user_id": user_id,
        "items": order_items_param(items),
        "address_id": shipping_address_id,
    }
    try:
        async with conn.transaction():
            cur = await conn.execute(PLACE_ORDER, params)
            rows = await cur.fetchall()
    except psycopg.Error as e:
        raise translate(e) from e
    return [r[0] for r in rows]


async def import_products(
    conn: psycopg.AsyncConnection, products: Iterable[NewProduct]
) -> ImportResult:
    async with conn.transaction(), conn.cursor() as cur:
        await cur.execute(IMPORT_STAGING)
        async with cur.copy(IMPORT_COPY) as copy:
            copy.set_types(IMPORT_COPY_TYPES)
            for p in products:
                await copy.write_row(import_row(p))
        await cur.execute(IMPORT_UPSERT)
        flags = [r[0] for r in await cur.fetchall()]
        await cur.execute(IMPORT_INVENTORY)
        await cur.execute("DROP TABLE import_products")
    inserted = sum(flags)
    return ImportResult(inserted=inserted, updated=len(flags) - inserted)


async def enqueue_job(
    conn: psycopg.AsyncConnection,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    queue: str = "default",
) -> int:
    """Add a job; it becomes visible to workers when the caller commits."""
    cur = await conn.execute(
        ENQUEUE_JOB, {"queue": queue, "kind": kind, "payload": Jsonb(payload or {})}
    )
    row = await cur.fetchone()
    assert row is not None
    return row[0]
