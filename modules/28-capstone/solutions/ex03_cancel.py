"""Exercise 3: POST /orders/{order_id}/cancel, on module 10's cancel_order().

Needs revision 2802 (2802_cancel_order.py). The buyer cancels their own
pending or paid order; the reservation is released in the same transaction.

    200 {"order_id": 7, "cancelled": true}     released now
    200 {"order_id": 7, "cancelled": false}    was already cancelled (idempotent)
    404                                        no such order, or not yours
    409 order-not-cancellable                  shipped, delivered, refunded (BZ005)
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter
from sqlalchemy.exc import DBAPIError

from bazaar.api.db import RwSession, UserId
from bazaar.api.errors import ApiError
from bazaar.api.middleware import TaggedRoute
from bazaar.errors import NotFound
from bazaar.resilience import sqlstate

router = APIRouter(route_class=TaggedRoute, tags=["shop"])


class NotCancellable(ApiError):
    status, slug, title = 409, "order-not-cancellable", "The order can no longer be cancelled"


@router.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: int, user_id: UserId, session: RwSession) -> dict:
    # Ownership first. Not FOR UPDATE: cancel_order() locks the row itself, and
    # an order never changes owner, so this read cannot go stale.
    owner = await session.scalar(
        sa.text("SELECT user_id FROM orders WHERE id = :id"), {"id": order_id}
    )
    if owner != user_id:  # None (missing) or someone else's: the same answer
        raise NotFound(f"order {order_id}")
    try:
        cancelled = await session.scalar(sa.text("SELECT cancel_order(:id)"), {"id": order_id})
    except DBAPIError as e:
        if sqlstate(e) == "BZ005":
            raise NotCancellable(f"order {order_id} is no longer pending or paid") from e
        if sqlstate(e) == "P0002":  # deleted between our read and the call
            raise NotFound(f"order {order_id}") from e
        raise
    return {"order_id": order_id, "cancelled": cancelled}
