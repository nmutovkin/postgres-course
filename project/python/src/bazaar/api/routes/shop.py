"""The buyer's side: cart, checkout, order history. All on the primary.

POST   /cart/items                add a product (quantities add up)
DELETE /cart/items/{product_id}   remove a line
GET    /cart                      the cart with current prices and stock
POST   /checkout                  Idempotency-Key header (required); the cart becomes
                                  one order per seller (place_order(), module 10)
GET    /users/{user_id}/orders    newest first, keyset pages

The cart and the order history read the primary, not the replica: right
after "add to cart" or "checkout" the buyer must see the change
(read-your-writes, module 19 section 9.2).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Body, Header, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import raiseload, selectinload

from bazaar.api.db import DB, RwSession, UserId
from bazaar.api.errors import ApiError, EmptyCart, Forbidden, InvalidCursor
from bazaar.api.middleware import TaggedRoute
from bazaar.api.schemas import Cart, CartItemIn, CartLine, CheckoutIn, CheckoutOut, OrderOut, Page
from bazaar.errors import ProductUnavailable, UserInactive
from bazaar.models import decode_cursor, encode_cursor
from bazaar.orm import services
from bazaar.orm.models import Order
from bazaar.resilience import RetryPolicy, arun_in_session

router = APIRouter(route_class=TaggedRoute, tags=["shop"])

# --- cart --------------------------------------------------------------------------------------

ADD_TO_CART = sa.text("""
    INSERT INTO cart_items AS c (user_id, product_id, quantity)
    SELECT :user_id, p.id, :quantity
    FROM products p
    WHERE p.id = :product_id
      AND p.status = 'active'
      AND EXISTS (SELECT 1 FROM users u WHERE u.id = :user_id AND u.is_active)
    ON CONFLICT (user_id, product_id)
        DO UPDATE SET quantity = c.quantity + EXCLUDED.quantity
    RETURNING c.quantity
""")

CART = sa.text("""
    SELECT c.product_id, p.title, p.price AS unit_price, c.quantity,
           p.price * c.quantity AS line_total,
           coalesce(i.quantity - i.reserved, 0) AS available,
           p.status = 'active' AND coalesce(i.quantity - i.reserved, 0) >= c.quantity
               AS purchasable
    FROM cart_items c
    JOIN products p ON p.id = c.product_id
    LEFT JOIN inventory i ON i.product_id = c.product_id
    WHERE c.user_id = :user_id
    ORDER BY c.added_at, c.product_id
""")


async def _cart(session: AsyncSession, user_id: int) -> Cart:
    lines = [CartLine(**r) for r in (await session.execute(CART, {"user_id": user_id})).mappings()]
    return Cart(items=lines, total=sum((line.line_total for line in lines), start=0))


@router.post("/cart/items", response_model=Cart, status_code=201)
async def add_to_cart(item: CartItemIn, user_id: UserId, session: RwSession) -> Cart:
    params = {"user_id": user_id, "product_id": item.product_id, "quantity": item.quantity}
    if (await session.execute(ADD_TO_CART, params)).first() is None:
        # One statement did the checks; only on failure do we ask which one failed.
        active = await session.scalar(
            sa.text("SELECT is_active FROM users WHERE id = :u"), {"u": user_id}
        )
        if not active:
            raise UserInactive(f"user {user_id} does not exist or is inactive")
        raise ProductUnavailable(f"product {item.product_id} is not available for sale")
    return await _cart(session, user_id)


@router.delete("/cart/items/{product_id}", status_code=204)
async def remove_from_cart(product_id: int, user_id: UserId, session: RwSession) -> None:
    await session.execute(
        sa.text("DELETE FROM cart_items WHERE user_id = :u AND product_id = :p"),
        {"u": user_id, "p": product_id},
    )


@router.get("/cart", response_model=Cart)
async def get_cart(user_id: UserId, session: RwSession) -> Cart:
    return await _cart(session, user_id)


# --- checkout ----------------------------------------------------------------------------------

# Whole-transaction retries for deadlocks/serialization failures, and (because
# every checkout carries an idempotency key) for a connection lost mid-commit:
# the retry finds the orders of the first attempt instead of ordering twice.
CHECKOUT_RETRY = RetryPolicy(attempts=3, base_delay=0.05, deadline=5.0)

LOCK_CART = sa.text("""
    SELECT product_id, quantity
    FROM cart_items
    WHERE user_id = :user_id
    ORDER BY product_id
    FOR UPDATE
""")

DEFAULT_ADDRESS = sa.text("SELECT id FROM addresses WHERE user_id = :user_id AND is_default")


def _orders_with_key(user_id: int, key: str) -> sa.Select[tuple[Order]]:
    return (
        sa.select(Order)
        .where(Order.user_id == user_id, Order.idempotency_key == key)
        .order_by(Order.id)
        .options(selectinload(Order.items), raiseload("*"))
    )


async def place_cart_order(
    session: AsyncSession, user_id: int, key: str, shipping_address_id: int | None
) -> tuple[Sequence[Order], bool]:
    """Check out the user's cart in the session's transaction; (orders, replayed).

    1. A key seen before: return its orders (a retried request, a double click).
    2. Lock the cart rows (FOR UPDATE): two checkouts of the same cart, whatever
       their keys, run one after the other; the second finds the cart empty.
    3. place_order() through services.checkout_async (one order per seller,
       stock reserved, the key stored, one outbox event per order), then empty
       the cart. Same transaction: all of it commits, or none of it.
    """
    existing = (await session.scalars(_orders_with_key(user_id, key))).all()
    if existing:
        return existing, True
    items = [
        (r.product_id, r.quantity) for r in await session.execute(LOCK_CART, {"user_id": user_id})
    ]
    if not items:
        # Empty: maybe because a concurrent request with this key just checked it out.
        existing = (await session.scalars(_orders_with_key(user_id, key))).all()
        if existing:
            return existing, True
        raise EmptyCart("add items to the cart before checking out")
    if shipping_address_id is None:
        shipping_address_id = await session.scalar(DEFAULT_ADDRESS, {"user_id": user_id})
    orders = await services.checkout_async(
        session, user_id, items, shipping_address_id, idempotency_key=key
    )
    await session.execute(
        sa.text("DELETE FROM cart_items WHERE user_id = :user_id"), {"user_id": user_id}
    )
    return orders, False


@router.post(
    "/checkout",
    response_model=CheckoutOut,
    status_code=201,
    responses={
        200: {"description": "Replay: this Idempotency-Key was used before"},
        409: {
            "description": "Insufficient stock (problem+json with product_id, requested, available)"
        },
        422: {"description": "Empty cart, unavailable product, key reused for another cart"},
    },
)
async def checkout(
    response: Response,
    user_id: UserId,
    db: DB,
    body: Annotated[CheckoutIn | None, Body()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CheckoutOut:
    if idempotency_key is None:
        raise ApiError("the Idempotency-Key header is required (8-200 characters, e.g. a UUID)")
    if not 8 <= len(idempotency_key) <= 200:
        raise ApiError("Idempotency-Key must be 8-200 characters long")
    address = body.shipping_address_id if body else None

    async def attempt(session: AsyncSession) -> tuple[Sequence[Order], bool]:
        return await place_cart_order(session, user_id, idempotency_key, address)

    orders, replayed = await arun_in_session(db.sessions, attempt, CHECKOUT_RETRY, idempotent=True)
    # Committed by now. expire_on_commit=False: the loaded orders are still readable.
    if replayed:
        response.status_code = 200
        response.headers["Idempotent-Replayed"] = "true"
    return CheckoutOut(orders=[OrderOut.model_validate(o) for o in orders], replayed=replayed)


# --- order history -----------------------------------------------------------------------------


@router.get("/users/{user_id}/orders", response_model=Page[OrderOut])
async def list_orders(
    user_id: int,
    caller: UserId,
    session: RwSession,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[OrderOut]:
    if user_id != caller:
        raise Forbidden("you can only list your own orders")
    stmt = (
        sa.select(Order)
        .where(Order.user_id == user_id)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(limit + 1)
        .options(selectinload(Order.items), raiseload("*"))
    )
    if cursor:
        try:
            created_at, last_id = decode_cursor(cursor)
        except Exception:  # noqa: BLE001 - any garbage is the client's mistake
            raise InvalidCursor("malformed cursor") from None
        stmt = stmt.where(sa.tuple_(Order.created_at, Order.id) < sa.tuple_(created_at, last_id))
    orders = (await session.scalars(stmt)).all()
    more = len(orders) > limit
    orders = orders[:limit]
    return Page[OrderOut](
        items=[OrderOut.model_validate(o) for o in orders],
        next_cursor=encode_cursor(orders[-1].created_at, orders[-1].id) if more else None,
    )
