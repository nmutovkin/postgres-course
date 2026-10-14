"""Exercise 2: port two repository functions to asyncpg, with identical results."""

import asyncio
import json

import asyncpg

from bazaar import repo
from bazaar.aio.db import asyncpg_dsn
from bazaar.config import get_settings
from bazaar.db import connect
from bazaar.models import (
    OrderPage,
    OrderStatus,
    OrderSummary,
    Product,
    ProductStatus,
    decode_cursor,
    encode_cursor,
)

GET_PRODUCT = """
    SELECT p.id, p.seller_id, p.category_id, p.sku, p.title, p.description,
           p.price, p.status, p.attributes, p.tags, p.created_at,
           i.quantity - i.reserved AS available
    FROM products p
    LEFT JOIN inventory i ON i.product_id = p.id
    WHERE p.id = $1
"""

# Two statements instead of composing an optional clause: with $n parameters,
# fixed texts are simplest, and each is prepared and cached once.
ORDERS_FIRST = """
    SELECT o.id, o.seller_id, o.status, o.total, o.created_at,
           (SELECT count(*) FROM order_items oi WHERE oi.order_id = o.id)::int AS item_count
    FROM orders o
    WHERE o.user_id = $1
    ORDER BY o.created_at DESC, o.id DESC
    LIMIT $2
"""
ORDERS_AFTER = """
    SELECT o.id, o.seller_id, o.status, o.total, o.created_at,
           (SELECT count(*) FROM order_items oi WHERE oi.order_id = o.id)::int AS item_count
    FROM orders o
    WHERE o.user_id = $1 AND (o.created_at, o.id) < ($3, $4)
    ORDER BY o.created_at DESC, o.id DESC
    LIMIT $2
"""


async def init(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", schema="pg_catalog", encoder=json.dumps,
                              decoder=json.loads, format="text")  # fmt: skip


async def get_product(conn: asyncpg.Connection, product_id: int) -> Product | None:
    row = await conn.fetchrow(GET_PRODUCT, product_id)
    if row is None:
        return None
    data = dict(row)
    data["status"] = ProductStatus(data["status"])  # asyncpg returns enums as str
    return Product(**data)


async def list_orders_for_user(
    conn: asyncpg.Connection, user_id: int, *, limit: int = 20, after: str | None = None
) -> OrderPage:
    if after is None:
        rows = await conn.fetch(ORDERS_FIRST, user_id, limit + 1)
    else:
        ts, last_id = decode_cursor(after)
        rows = await conn.fetch(ORDERS_AFTER, user_id, limit + 1, ts, last_id)
    items = [OrderSummary(**{**dict(r), "status": OrderStatus(r["status"])}) for r in rows[:limit]]
    more = len(rows) > limit
    return OrderPage(items, encode_cursor(items[-1].created_at, items[-1].id) if more else None)


async def main() -> None:
    pool = await asyncpg.create_pool(asyncpg_dsn(get_settings().dsn), min_size=1, max_size=2,
                                     init=init)  # fmt: skip
    with connect() as sync_conn:
        async with pool.acquire() as conn:
            for pid in (1, 20, 24_001, -1):
                a, s = await get_product(conn, pid), repo.get_product(sync_conn, pid)
                assert a == s, (a, s)
            pages_async, pages_sync = [], []
            after_a = after_s = None
            while True:
                pa = await list_orders_for_user(conn, 8, limit=7, after=after_a)
                ps = repo.list_orders_for_user(sync_conn, 8, limit=7, after=after_s)
                pages_async.append(pa)
                pages_sync.append(ps)
                if pa.next_cursor is None:
                    break
                after_a, after_s = pa.next_cursor, ps.next_cursor
    await pool.close()
    assert pages_async == pages_sync
    p = pages_async[0].items[0]
    print("products 1, 20, 24001, -1: identical in both drivers")
    print(f"user 8: {sum(len(pg.items) for pg in pages_async)} orders in {len(pages_async)} pages;"
          f" newest {p.id} {p.status} {p.total} items={p.item_count}")  # fmt: skip


if __name__ == "__main__":
    asyncio.run(main())
