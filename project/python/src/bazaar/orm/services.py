"""Business operations on top of the ORM.

Conventions (the same as bazaar.repo):

* Every function takes a Session first and **never commits**. Operations that
  must be atomic on their own run inside session.begin_nested(): a SAVEPOINT,
  so a failure undoes only that operation and the caller's session stays usable.
* Queries say what they load (selectinload / joinedload) and end with
  raiseload("*"), so a relationship nobody asked for raises instead of
  quietly costing one query per object (the N+1 problem).
* Database errors with business meaning become bazaar.errors exceptions.

Seller operations rely on the session's seller scope (bazaar.orm.session:
session.info["seller_id"], applied as SET LOCAL app.seller_id). They also
filter by seller explicitly: RLS is the safety net, not the only check.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, joinedload, raiseload, selectinload
from sqlalchemy.orm.exc import StaleDataError

from bazaar.errors import (
    ConcurrentUpdate,
    DuplicateReview,
    IdempotencyConflict,
    InvalidReview,
    NotFound,
    ReviewNotAllowed,
    constraint_name,
    translate,
)
from bazaar.models import OrderStatus, ProductStatus
from bazaar.orm.models import Inventory, Order, OrderItem, Product, Review
from bazaar.orm.session import SELLER_KEY
from bazaar.sa.tables import outbox as outbox_table


def _reraise_translated(e: DBAPIError) -> None:
    err = translate(e.orig) if e.orig is not None else e
    if err is e.orig:
        raise e
    raise err from e


# --- checkout ----------------------------------------------------------------------------

IDEMPOTENCY_INDEX = "orders_idempotency_key_idx"  # unique (user_id, idempotency_key, seller_id)


def _place_order_call(
    user_id: int, items: Sequence[tuple[int, int]], shipping_address_id: int | None
) -> sa.Select:
    payload = [{"product_id": pid, "quantity": qty} for pid, qty in items]
    return sa.select(
        sa.func.place_order(
            sa.bindparam("user_id", user_id, type_=sa.BigInteger),
            sa.bindparam("items", payload, type_=JSONB),
            sa.bindparam("address_id", shipping_address_id, type_=sa.BigInteger),
        )
    )


def _orders_query(*where: Any) -> sa.Select[tuple[Order]]:
    return (
        sa.select(Order)
        .where(*where)
        .order_by(Order.id)
        .options(selectinload(Order.items), raiseload("*"))
    )


def _keyed_orders(user_id: int, key: str) -> sa.Select[tuple[Order]]:
    return _orders_query(Order.user_id == user_id, Order.idempotency_key == key)


def _tag_and_record(order_ids: list[int], idempotency_key: str | None, events: bool) -> list:
    """Statements that run in the checkout's savepoint after place_order()."""
    stmts: list = []
    if idempotency_key is not None:
        stmts.append(
            sa.update(Order.__table__)
            .where(Order.__table__.c.id.in_(order_ids))
            .values(idempotency_key=idempotency_key)
        )
    if events:
        o = Order.__table__
        stmts.append(
            sa.insert(outbox_table).from_select(
                ["topic", "aggregate_type", "aggregate_id", "payload"],
                sa.select(
                    sa.literal("order.placed"),
                    sa.literal("order"),
                    sa.cast(o.c.id, sa.Text),
                    sa.func.jsonb_build_object(
                        "order_id",
                        o.c.id,
                        "user_id",
                        o.c.user_id,
                        "seller_id",
                        o.c.seller_id,
                        "total",
                        sa.cast(o.c.total, sa.Text),
                    ),
                )
                .where(o.c.id.in_(order_ids))
                .order_by(o.c.id),
            )
        )
    return stmts


def _same_items(orders: Sequence[Order], items: Sequence[tuple[int, int]]) -> bool:
    wanted: dict[int, int] = {}
    for pid, qty in items:  # place_order() sums duplicate lines
        wanted[pid] = wanted.get(pid, 0) + qty
    have = {i.product_id: i.quantity for o in orders for i in o.items}
    return have == wanted


def _is_key_conflict(e: IntegrityError) -> bool:
    return constraint_name(e) == IDEMPOTENCY_INDEX  # psycopg and asyncpg alike


def _replay(orders: list[Order], items: Sequence[tuple[int, int]], key: str) -> list[Order]:
    if not _same_items(orders, items):
        raise IdempotencyConflict(f"idempotency key {key!r} was used for a different cart")
    return orders


def checkout(
    session: Session,
    user_id: int,
    items: Sequence[tuple[int, int]],
    shipping_address_id: int | None = None,
    *,
    idempotency_key: str | None = None,
    emit_events: bool = True,
) -> list[Order]:
    """Place an order through the database function place_order() (module 10).

    The function splits the cart per seller, locks and reserves stock, and
    returns the new order ids; we then load the orders with their items.
    Raises InsufficientStock, ProductUnavailable, UserInactive, AddressMismatch
    or InvalidOrder; on error nothing is changed.

    Module 27 additions, both in the same savepoint as the orders:
    * idempotency_key (8-200 characters, chosen by the client per checkout):
      a repeated call with the same key returns the orders of the first call
      instead of ordering again, also when the two calls race (the unique
      index orders_idempotency_key_idx decides; the loser re-reads). Reusing
      a key for a different cart raises IdempotencyConflict.
    * emit_events: one "order.placed" message per order in the outbox, so the
      events exist if and only if the orders do (bazaar.outbox relays them).
    """
    if idempotency_key is not None:
        existing = list(session.scalars(_keyed_orders(user_id, idempotency_key)))
        if existing:
            return _replay(existing, items, idempotency_key)
    try:
        with session.begin_nested():
            order_ids = list(
                session.scalars(_place_order_call(user_id, items, shipping_address_id))
            )
            for stmt in _tag_and_record(order_ids, idempotency_key, emit_events):
                session.execute(stmt)
    except IntegrityError as e:
        if idempotency_key is None or not _is_key_conflict(e):
            raise
        # A concurrent request with the same key committed first.
        existing = list(session.scalars(_keyed_orders(user_id, idempotency_key)))
        return _replay(existing, items, idempotency_key)
    except DBAPIError as e:
        _reraise_translated(e)
    return list(session.scalars(_orders_query(Order.id.in_(order_ids))))


async def checkout_async(
    session: AsyncSession,
    user_id: int,
    items: Sequence[tuple[int, int]],
    shipping_address_id: int | None = None,
    *,
    idempotency_key: str | None = None,
    emit_events: bool = True,
) -> list[Order]:
    """checkout() for an AsyncSession (the module 28 service): same statements."""
    if idempotency_key is not None:
        existing = list(await session.scalars(_keyed_orders(user_id, idempotency_key)))
        if existing:
            return _replay(existing, items, idempotency_key)
    try:
        async with session.begin_nested():
            order_ids = list(
                await session.scalars(_place_order_call(user_id, items, shipping_address_id))
            )
            for stmt in _tag_and_record(order_ids, idempotency_key, emit_events):
                await session.execute(stmt)
    except IntegrityError as e:
        if idempotency_key is None or not _is_key_conflict(e):
            raise
        existing = list(await session.scalars(_keyed_orders(user_id, idempotency_key)))
        return _replay(existing, items, idempotency_key)
    except DBAPIError as e:
        _reraise_translated(e)
    return list(await session.scalars(_orders_query(Order.id.in_(order_ids))))


# --- reading orders without N+1 -----------------------------------------------------------


def order_history_query(user_id: int, limit: int = 20) -> sa.Select[tuple[Order]]:
    """A user's newest orders with seller, items and the items' products.

    Two statements whatever `limit` is: orders JOIN sellers, then one
    SELECT ... WHERE order_id IN (...) for all items, joined to products.
    """
    return (
        sa.select(Order)
        .where(Order.user_id == user_id)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(limit)
        .options(
            joinedload(Order.seller),
            selectinload(Order.items).joinedload(OrderItem.product),
            raiseload("*"),
        )
    )


def order_history(session: Session, user_id: int, limit: int = 20) -> list[Order]:
    return list(session.scalars(order_history_query(user_id, limit)))


async def order_history_async(session: AsyncSession, user_id: int, limit: int = 20) -> list[Order]:
    """The same statement on an AsyncSession: eager loading is mandatory there anyway."""
    return list(await session.scalars(order_history_query(user_id, limit)))


# --- seller product management (RLS) -------------------------------------------------------

EDITABLE = frozenset(
    {"title", "description", "price", "status", "category_id", "attributes", "tags"}
)


def scoped_seller_id(session: Session) -> int:
    seller_id = session.info.get(SELLER_KEY)
    if seller_id is None:
        raise PermissionError("this operation needs a seller session (session.info['seller_id'])")
    return int(seller_id)


def create_product(
    session: Session,
    *,
    sku: str,
    title: str,
    price: Decimal,
    category_id: int,
    quantity: int = 0,
    description: str | None = None,
    status: ProductStatus = ProductStatus.DRAFT,
    attributes: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Product:
    """Create a product (and its inventory row) for the session's seller."""
    product = Product(
        seller_id=scoped_seller_id(session),
        sku=sku,
        title=title,
        price=price,
        category_id=category_id,
        description=description,
        status=status,
        attributes=attributes or {},
        tags=tags or [],
        inventory=Inventory(quantity=quantity),
    )
    session.add(product)
    session.flush()  # surface constraint errors here, and get the id
    return product


def get_own_product(session: Session, product_id: int) -> Product:
    product = session.get(Product, product_id, options=[selectinload(Product.inventory)])
    if product is None or product.seller_id != scoped_seller_id(session):
        raise NotFound(f"product {product_id}")
    return product


def update_product(
    session: Session, product_id: int, *, expected_version: int | None = None, **changes: Any
) -> Product:
    """Change editable fields of one of the seller's products.

    expected_version is the `xmin` the client saw when it loaded the product
    (an edit form would carry it). If the row changed since, ConcurrentUpdate
    is raised instead of silently overwriting someone else's edit.
    """
    unknown = set(changes) - EDITABLE
    if unknown:
        raise ValueError(f"not editable: {sorted(unknown)}")
    product = get_own_product(session, product_id)
    if expected_version is not None and product.xmin != expected_version:
        raise ConcurrentUpdate(f"product {product_id} changed since version {expected_version}")
    for key, value in changes.items():
        setattr(product, key, value)
    try:
        session.flush()
    except StaleDataError as e:  # changed between our SELECT and our UPDATE
        raise ConcurrentUpdate(f"product {product_id} was changed concurrently") from e
    return product


def set_stock(session: Session, product_id: int, quantity: int) -> Inventory:
    product = get_own_product(session, product_id)
    if product.inventory is None:
        product.inventory = Inventory(quantity=quantity)
    else:
        product.inventory.quantity = quantity
    session.flush()
    return product.inventory


def list_products(
    session: Session,
    *,
    status: ProductStatus | None = None,
    after_id: int | None = None,
    limit: int = 50,
) -> list[Product]:
    """The seller's products by id, keyset-paginated (pass the last id as after_id)."""
    stmt = (
        sa.select(Product)
        .where(Product.seller_id == scoped_seller_id(session))
        .order_by(Product.id)
        .limit(limit)
        .options(selectinload(Product.inventory), raiseload("*"))
    )
    if status is not None:
        stmt = stmt.where(Product.status == status)
    if after_id is not None:
        stmt = stmt.where(Product.id > after_id)
    return list(session.scalars(stmt))


# --- reviews ----------------------------------------------------------------------------------

MAX_TITLE = 200
MAX_BODY = 5000


def submit_review(
    session: Session,
    user_id: int,
    product_id: int,
    rating: int,
    title: str | None = None,
    body: str | None = None,
) -> Review:
    """Add a verified review: the user must have a delivered order with the product."""
    if not isinstance(rating, int) or not 1 <= rating <= 5:
        raise InvalidReview("rating must be an integer from 1 to 5")
    title = (title or "").strip() or None
    body = (body or "").strip() or None
    if title is not None and len(title) > MAX_TITLE:
        raise InvalidReview(f"title longer than {MAX_TITLE} characters")
    if body is not None and len(body) > MAX_BODY:
        raise InvalidReview(f"body longer than {MAX_BODY} characters")

    bought = sa.select(
        sa.exists().where(
            OrderItem.product_id == product_id,
            OrderItem.order_id == Order.id,
            Order.user_id == user_id,
            Order.status == OrderStatus.DELIVERED,
        )
    )
    if not session.scalar(bought):
        raise ReviewNotAllowed(f"user {user_id} has no delivered order with product {product_id}")

    review = Review(user_id=user_id, product_id=product_id, rating=rating, title=title, body=body)
    try:
        with session.begin_nested():
            session.add(review)
    except IntegrityError as e:
        if constraint_name(e) == "reviews_product_id_user_id_key":
            raise DuplicateReview(f"user {user_id} already reviewed product {product_id}") from e
        raise
    return review
