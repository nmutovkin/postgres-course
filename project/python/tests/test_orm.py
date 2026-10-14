"""Module 25 milestone: ORM models, sessions and services."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import MissingGreenlet, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, joinedload, selectinload

from bazaar import db
from bazaar.config import get_settings
from bazaar.errors import (
    ConcurrentUpdate,
    DuplicateReview,
    InsufficientStock,
    InvalidReview,
    NotFound,
    ReviewNotAllowed,
)
from bazaar.models import OrderStatus, ProductStatus
from bazaar.orm import services
from bazaar.orm.models import ActiveProduct, Base, Category, Inventory, Order, Product
from bazaar.orm.session import SELLER_KEY, BazaarSession, make_sessionmaker
from bazaar.sa.drift import compare
from bazaar.sa.engine import count_statements, create_engine

from .factories import cleanup, make_product, make_seller, make_user

# The reference mapping (orm/models.py) describes the schema at Alembic head:
# Product.slug (revision 2602) and Order.idempotency_key (2604) are mapped, and
# checkout writes an outbox event (2701). Tests that load products or orders
# therefore need the migrated schema; on `reset.sh 22` they are skipped
# (tests/conftest.py). A module-25 learner's own mapping has no such columns.
needs_head = pytest.mark.migrated(reason="maps products.slug / orders.idempotency_key (26+)")

# --- mapping ---------------------------------------------------------------------------


@needs_head
def test_mapping_matches_database(sa_conn: sa.Connection) -> None:
    assert compare(sa_conn, Base.metadata, mapping_only=True) == []


@needs_head
def test_types_and_relationships(session: Session) -> None:
    laptops = session.get(Category, 3)
    assert laptops is not None and laptops.parent is not None
    assert laptops.parent.parent is not None and laptops.parent.parent.slug == "electronics"
    product = session.scalars(sa.select(Product).where(Product.category_id == 3).limit(1)).one()
    assert isinstance(product.price, Decimal) and isinstance(product.status, ProductStatus)
    assert isinstance(product.attributes, dict) and "ram_gb" in product.attributes
    assert product.inventory is not None
    assert product.inventory.available == product.inventory.quantity - product.inventory.reserved


@needs_head
def test_hybrid_and_column_property_in_sql(session: Session) -> None:
    # Inventory.available used as a SQL expression...
    n = session.scalar(
        sa.select(sa.func.count()).select_from(Inventory).where(Inventory.available == 0)
    )
    raw = session.scalar(sa.text("SELECT count(*) FROM inventory WHERE quantity = reserved"))
    assert n == raw
    # ... and Order.item_count, a deferred correlated subquery, loaded on request.
    order = session.scalars(
        sa.select(Order).where(Order.id == 1).options(sa.orm.undefer(Order.item_count))
    ).one()
    assert order.item_count == len(order.items)


@needs_head
def test_mutable_jsonb_is_tracked(session: Session) -> None:
    product = session.get(Product, 1)
    assert product is not None
    product.attributes["gift_wrap"] = True  # in-place change, no reassignment
    assert product in session.dirty
    session.flush()
    session.expire(product)
    assert product.attributes["gift_wrap"] is True


def test_view_is_read_only(session: Session) -> None:
    ap = session.scalars(sa.select(ActiveProduct).limit(1)).one()
    ap.title = "nope"
    with pytest.raises(sa.exc.InvalidRequestError, match="read-only"):
        session.flush()


# --- checkout ------------------------------------------------------------------------


def _scarce_product(session: Session) -> int:
    return session.scalars(
        sa.select(Inventory.product_id)
        .join(Product)
        .where(Product.status == ProductStatus.ACTIVE, Inventory.available == 1)
        .order_by(Inventory.product_id)
        .limit(1)
    ).one()


@needs_head
def test_checkout(session: Session) -> None:
    pid = _scarce_product(session)
    [order] = services.checkout(session, 8, [(pid, 1)])
    assert order.status is OrderStatus.PENDING
    assert [(i.product_id, i.quantity) for i in order.items] == [(pid, 1)]
    assert order.total == order.items[0].unit_price  # computed by the trigger
    inv = session.get(Inventory, pid, populate_existing=True)
    assert inv is not None and inv.available == 0


@needs_head
def test_checkout_insufficient_stock_leaves_session_usable(session: Session) -> None:
    pid = _scarce_product(session)
    with pytest.raises(InsufficientStock) as exc:
        services.checkout(session, 8, [(pid, 2)])
    assert (exc.value.requested, exc.value.available) == (2, 1)
    assert len(services.checkout(session, 8, [(pid, 1)])) == 1  # still usable


# --- loading strategies ----------------------------------------------------------------


@needs_head
def test_order_history_has_no_n_plus_1(session: Session) -> None:
    engine = session.get_bind().engine
    session.connection()  # begin now (the fixture's SAVEPOINT), so we count only queries
    with count_statements(engine) as stmts:
        orders = services.order_history(session, 8, limit=20)
        lines = [(o.seller.shop_name, i.product.title, i.quantity) for o in orders for i in o.items]
    assert len(orders) == 20 and len(lines) > 20
    assert len(stmts) == 2  # orders+sellers, then items+products: independent of `limit`


@needs_head
def test_lazy_loading_is_n_plus_1(session: Session) -> None:
    """The same loop without loader options: one query per order and per product."""
    engine = session.get_bind().engine
    session.connection()
    with count_statements(engine) as stmts:
        orders = session.scalars(
            sa.select(Order).where(Order.user_id == 8).order_by(Order.id.desc()).limit(20)
        ).all()
        for o in orders:
            for i in o.items:
                _ = (o.seller.shop_name, i.product.title)
    assert len(stmts) > 40


@needs_head
def test_raiseload_guards_unplanned_access(session: Session) -> None:
    [order] = services.order_history(session, 8, limit=1)
    with pytest.raises(sa.exc.InvalidRequestError, match="lazy=.raise"):
        _ = order.payments


# --- optimistic concurrency (xmin) -------------------------------------------------------------


@pytest.fixture
def committed_product() -> Iterator[tuple[int, int]]:
    with db.connect(autocommit=True) as conn:
        with conn.transaction():
            seller = make_seller(conn)
            pid = make_product(conn, seller, price=Decimal("10.00"))
        yield seller, pid
        cleanup(conn, [], [seller])


@needs_head
def test_concurrent_edit_is_detected(sa_engine: sa.Engine, committed_product) -> None:
    seller, pid = committed_product
    factory = make_sessionmaker(sa_engine)
    with factory(info={SELLER_KEY: seller}) as alice, factory(info={SELLER_KEY: seller}) as bob:
        a = services.get_own_product(alice, pid)
        b = services.get_own_product(bob, pid)
        seen = b.xmin
        b.price = Decimal("12.00")
        bob.commit()  # Bob saves first
        a.price = Decimal("11.00")
        with pytest.raises(ConcurrentUpdate):
            services.update_product(alice, pid, title="Alice's title")  # flush fails
        alice.rollback()
        # A client that sends the version it saw gets the same answer, before any UPDATE.
        with pytest.raises(ConcurrentUpdate):
            services.update_product(alice, pid, expected_version=seen, price=Decimal("9.00"))
    with factory() as check:
        product = check.get(Product, pid)
        assert product is not None and product.price == Decimal("12.00")


# --- seller services under RLS --------------------------------------------------------------


@pytest.fixture
def seller_conn() -> Iterator[sa.Connection]:
    """A connection as the bazaar_seller role (module 15), rolled back afterwards."""
    url = sa.make_url(get_settings().dsn).set(username="bazaar_seller", password="seller")
    settings = dataclasses.replace(get_settings(), dsn=url.render_as_string(hide_password=False))
    engine = create_engine(settings, pool_size=1)
    with engine.connect() as c:
        c.begin()
        yield c
        c.rollback()
    engine.dispose()


@needs_head
def test_rls_seller_sees_only_own_rows(seller_conn: sa.Connection, sa_conn: sa.Connection) -> None:
    own = sa_conn.scalar(sa.text("SELECT count(*) FROM products WHERE seller_id = 42"))
    other = sa_conn.scalar(sa.text("SELECT min(id) FROM products WHERE seller_id = 43"))
    with BazaarSession(
        bind=seller_conn, join_transaction_mode="create_savepoint", info={SELLER_KEY: 42}
    ) as s:
        assert s.scalar(sa.select(sa.func.count()).select_from(Product)) == own
        assert s.get(Product, other) is None  # invisible, not just filtered by our code
        with pytest.raises(NotFound):
            services.update_product(s, other, price=Decimal("1.00"))

        p = services.create_product(
            s, sku="RLS-1", title="Seller-made lamp", price=Decimal("20.00"), category_id=12,
            quantity=3,
        )  # fmt: skip
        assert p.id and p.inventory is not None and p.inventory.quantity == 3
        assert [x.id for x in services.list_products(s, after_id=p.id - 1)] == [p.id]

        # Bypassing the service does not help: the policy's WITH CHECK refuses the row.
        s.add(Product(seller_id=43, sku="X", title="x", price=Decimal(1), category_id=12))
        with pytest.raises(ProgrammingError, match="row-level security"):
            s.flush()


def test_seller_services_need_a_seller(session: Session) -> None:
    with pytest.raises(PermissionError):
        services.list_products(session)


# --- reviews -----------------------------------------------------------------------------------


def _unreviewed_purchase(session: Session) -> tuple[int, int]:
    row = session.execute(
        sa.text(
            """
            SELECT o.user_id, oi.product_id
            FROM orders o JOIN order_items oi ON oi.order_id = o.id
            WHERE o.status = 'delivered'
              AND NOT EXISTS (SELECT 1 FROM reviews r
                              WHERE r.user_id = o.user_id AND r.product_id = oi.product_id)
            ORDER BY o.id LIMIT 1
            """
        )
    ).one()
    return row.user_id, row.product_id


def test_submit_review(session: Session) -> None:
    user_id, product_id = _unreviewed_purchase(session)
    review = services.submit_review(session, user_id, product_id, 5, "  Great  ", "Works.")
    assert review.id is not None and review.title == "Great"
    with pytest.raises(DuplicateReview):
        services.submit_review(session, user_id, product_id, 4)
    # the failed attempt was rolled back to its savepoint; the first review survives
    assert session.get(type(review), review.id) is review


def test_review_validation(session: Session) -> None:
    user_id, product_id = _unreviewed_purchase(session)
    with pytest.raises(InvalidReview):
        services.submit_review(session, user_id, product_id, 6)
    with pytest.raises(InvalidReview):
        services.submit_review(session, user_id, product_id, 3, body="x" * 5001)
    stranger = make_user(session.connection().connection.driver_connection)  # type: ignore[arg-type]
    with pytest.raises(ReviewNotAllowed):
        services.submit_review(session, stranger, product_id, 3)


# --- async ORM ---------------------------------------------------------------------------------


@needs_head
async def test_async_order_history(asession: AsyncSession) -> None:
    orders = await services.order_history_async(asession, 8, limit=5)
    assert len(orders) == 5
    assert all(o.items and o.items[0].product.title for o in orders)  # all preloaded


@needs_head
async def test_async_lazy_load_raises_missing_greenlet(asession: AsyncSession) -> None:
    order = (await asession.scalars(sa.select(Order).where(Order.id == 1))).one()
    with pytest.raises(MissingGreenlet):
        _ = order.items  # would need IO inside a plain attribute access
    items = await order.awaitable_attrs.items  # the explicit async way
    assert len(items) >= 1
    again = (
        await asession.scalars(
            sa.select(Order)
            .where(Order.id == 1)
            .options(selectinload(Order.items), joinedload(Order.user))
        )
    ).one()
    assert again is order and again.user.id == order.user_id
