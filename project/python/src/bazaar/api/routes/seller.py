"""The seller portal: X-Seller-Id identifies the seller (a stand-in for real auth).

GET   /seller/orders              the seller's orders, newest first (RLS does the filtering)
GET   /seller/products            the seller's products, keyset by id
GET   /seller/products/{id}       one product, with its `version`
PATCH /seller/products/{id}       edit; `version` must match (optimistic locking) or 409
GET   /seller/stats               daily sales from the seller_sales_daily materialized view

Everything except /stats runs on the seller engine: connected as bazaar_seller,
the role module 15's policies apply to, with SET LOCAL app.seller_id = <id> at
the start of every transaction (bazaar.orm.session's after_begin hook). The
database then shows this seller's rows and nothing else, whatever the SQL says:
/seller/orders deliberately has no `WHERE seller_id = ...`, so the isolation
test proves the policy, not the query. (The ORM services also filter
explicitly: RLS is the safety net there.)

/stats reads a materialized view. Materialized views cannot have RLS policies,
and bazaar_seller has no privilege on it; it runs on the read engine (the
application role) with an explicit seller filter.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Query
from sqlalchemy.ext.asyncio import AsyncSession

from bazaar.api.db import ReadSession, SellerId, SellerSession
from bazaar.api.errors import ApiError, InvalidCursor
from bazaar.api.middleware import TaggedRoute
from bazaar.api.schemas import (
    DailyStats,
    Page,
    ProductPatch,
    SellerOrder,
    SellerProduct,
    SellerStats,
)
from bazaar.models import OrderStatus, ProductStatus, decode_cursor, encode_cursor
from bazaar.orm import services
from bazaar.orm.models import Product

router = APIRouter(prefix="/seller", route_class=TaggedRoute, tags=["seller"])

SELLER_ORDERS = """
    SELECT o.id, o.user_id, o.status, o.total, o.created_at,
           (SELECT count(*) FROM order_items oi WHERE oi.order_id = o.id) AS item_count
    FROM orders o
    WHERE true
"""


@router.get("/orders", response_model=Page[SellerOrder])
async def seller_orders(
    session: SellerSession,
    status: OrderStatus | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[SellerOrder]:
    sql = SELLER_ORDERS
    params: dict[str, object] = {"limit": limit + 1}
    if status is not None:
        sql += " AND o.status = CAST(:status AS order_status)"
        params["status"] = status.value
    if cursor:
        try:
            params["ts"], params["last_id"] = decode_cursor(cursor)
        except Exception:  # noqa: BLE001
            raise InvalidCursor("malformed cursor") from None
        sql += " AND (o.created_at, o.id) < (:ts, :last_id)"
    sql += " ORDER BY o.created_at DESC, o.id DESC LIMIT :limit"
    rows = (await session.execute(sa.text(sql), params)).mappings().all()
    more = len(rows) > limit
    rows = rows[:limit]
    return Page[SellerOrder](
        items=[SellerOrder(**r) for r in rows],
        next_cursor=encode_cursor(rows[-1]["created_at"], rows[-1]["id"]) if more else None,
    )


def _out(p: Product) -> SellerProduct:
    inv = p.inventory
    return SellerProduct(
        id=p.id,
        sku=p.sku,
        title=p.title,
        description=p.description,
        price=p.price,
        status=p.status,
        category_id=p.category_id,
        tags=list(p.tags),
        attributes=dict(p.attributes),
        quantity=inv.quantity if inv else None,
        reserved=inv.reserved if inv else None,
        version=p.xmin,
    )


@router.get("/products", response_model=Page[SellerProduct])
async def seller_products(
    session: SellerSession,
    status: ProductStatus | None = None,
    cursor: Annotated[str | None, Query(pattern=r"^[0-9]{1,19}$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[SellerProduct]:
    after_id = int(cursor) if cursor else None  # keyset by id: the cursor is the last id
    # The module 25 service is synchronous ORM code; run_sync runs it on the
    # AsyncSession's underlying Session, inside the greenlet, IO included.
    products = await session.run_sync(
        lambda s: services.list_products(s, status=status, after_id=after_id, limit=limit + 1)
    )
    more = len(products) > limit
    products = products[:limit]
    return Page[SellerProduct](
        items=[_out(p) for p in products],
        next_cursor=str(products[-1].id) if more else None,
    )


@router.get("/products/{product_id}", response_model=SellerProduct)
async def seller_product(product_id: int, session: SellerSession) -> SellerProduct:
    return _out(await session.run_sync(services.get_own_product, product_id))


@router.patch("/products/{product_id}", response_model=SellerProduct)
async def update_seller_product(
    product_id: int, patch: ProductPatch, session: SellerSession
) -> SellerProduct:
    changes = patch.model_dump(exclude_unset=True, exclude={"version"})
    if not changes:
        raise ApiError("nothing to change")

    def update(s: AsyncSession) -> Product:  # a sync Session, despite the annotation
        return services.update_product(s, product_id, expected_version=patch.version, **changes)

    product = await session.run_sync(update)
    return _out(product)


STATS = sa.text("""
    SELECT day, orders_count AS orders, items_sold, revenue
    FROM seller_sales_daily
    WHERE seller_id = :seller_id
      AND day BETWEEN :date_from AND :date_to
    ORDER BY day
""")


@router.get("/stats", response_model=SellerStats)
async def seller_stats(
    seller_id: SellerId,
    session: ReadSession,
    date_from: date | None = None,
    date_to: date | None = None,
) -> SellerStats:
    """Daily orders, items and revenue (paid, shipped, delivered orders), as of the
    last refresh of the materialized view (POST /admin/refresh-stats): not real time."""
    if date_to is None:  # default: the 30 days up to the seller's last day with sales
        date_to = await session.scalar(
            sa.text("SELECT max(day) FROM seller_sales_daily WHERE seller_id = :s"),
            {"s": seller_id},
        )
    date_to = date_to or date.today()
    date_from = date_from or date_to - timedelta(days=29)
    if date_from > date_to or (date_to - date_from).days > 366:
        raise ApiError("date range must be ascending and at most a year")
    rows = (
        (
            await session.execute(
                STATS, {"seller_id": seller_id, "date_from": date_from, "date_to": date_to}
            )
        )
        .mappings()
        .all()
    )
    days = [DailyStats(**r) for r in rows]
    return SellerStats(
        seller_id=seller_id,
        date_from=date_from,
        date_to=date_to,
        orders=sum(d.orders for d in days),
        items_sold=sum(d.items_sold for d in days),
        revenue=sum((d.revenue for d in days), start=0),
        days=days,
    )
