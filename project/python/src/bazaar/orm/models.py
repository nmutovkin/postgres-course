"""The Bazaar entities as SQLAlchemy ORM classes (2.0-style typed declarative mapping).

Design rules (module 25 explains each):

* The database owns the schema. These classes describe what the ORM needs
  to know (columns, types, keys); CHECK constraints and most indexes live in
  the SQL files and in the Core tables of module 24. The drift test
  (tests/test_orm.py) checks the mapping against the live database.
* Relationships default to lazy="select" (convenient in scripts and the
  shell), but request-handling code states what it loads (selectinload /
  joinedload) and adds raiseload("*") so that a forgotten relationship is an
  error instead of a silent extra query. In async code lazy loading is
  impossible anyway.
* Child rows that the database deletes with ON DELETE CASCADE use
  passive_deletes=True, so the ORM lets Postgres do it instead of loading
  every child first.
* Columns the database computes (identity, defaults, triggers, generated
  columns) are marked so the ORM knows to fetch them, not to send them.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    column_property,
    mapped_column,
    relationship,
)

from bazaar.models import OrderStatus, PaymentStatus, ProductStatus
from bazaar.sa import tables as core


class XID(sa.types.UserDefinedType):
    """PostgreSQL's 32-bit transaction id type (system column xmin), as a Python int."""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "xid"

    def bind_processor(self, dialect: Any) -> Any:
        # psycopg sends a str (text, cast by the server to xid); asyncpg prepares
        # the statement, learns the parameter is an xid, and wants an int.
        if dialect.driver == "asyncpg":
            return lambda value: None if value is None else int(value)
        return lambda value: None if value is None else str(value)

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        return lambda value: None if value is None else int(value)


class Base(AsyncAttrs, DeclarativeBase):
    """Declarative base: one MetaData, the same naming convention as the Core tables."""

    metadata = sa.MetaData(naming_convention=core.NAMING_CONVENTION)
    type_annotation_map = {
        Decimal: sa.Numeric(12, 2),
        datetime: sa.DateTime(timezone=True),
        dict[str, Any]: pg.JSONB,
        list[str]: pg.ARRAY(sa.Text),
        OrderStatus: core.order_status,
        ProductStatus: core.product_status,
        PaymentStatus: core.payment_status,
        str: sa.Text,
    }


def created_at_column() -> Mapped[datetime]:
    return mapped_column(server_default=sa.func.now())


def updated_at_column() -> Mapped[datetime]:
    # A BEFORE UPDATE trigger (set_updated_at, module 09) sets it: FetchedValue
    # tells the ORM that an UPDATE changes it server-side, so it is expired and
    # reloaded on next access (or fetched with RETURNING when eager_defaults).
    return mapped_column(server_default=sa.func.now(), server_onupdate=sa.FetchedValue())


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(always=True), primary_key=True)
    email: Mapped[str] = mapped_column(core.email_address)
    full_name: Mapped[str]
    is_active: Mapped[bool] = mapped_column(server_default=sa.true())
    created_at: Mapped[datetime] = created_at_column()

    seller: Mapped[Seller | None] = relationship(back_populates="user")  # one-to-one
    addresses: Mapped[list[Address]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    orders: Mapped[list[Order]] = relationship(back_populates="user")
    reviews: Mapped[list[Review]] = relationship(back_populates="user")

    def __repr__(self) -> str:
        return f"User(id={self.id!r}, email={self.email!r})"


class Seller(Base):
    __tablename__ = "sellers"

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id"), unique=True)
    shop_name: Mapped[str] = mapped_column(unique=True)
    country_code: Mapped[str] = mapped_column(sa.CHAR(2))
    created_at: Mapped[datetime] = created_at_column()

    user: Mapped[User] = relationship(back_populates="seller")
    products: Mapped[list[Product]] = relationship(back_populates="seller")
    orders: Mapped[list[Order]] = relationship(back_populates="seller")

    def __repr__(self) -> str:
        return f"Seller(id={self.id!r}, shop_name={self.shop_name!r})"


class Category(Base):
    """A self-referential adjacency list: parent_id points at another category."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(sa.Integer, sa.Identity(always=True), primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(sa.ForeignKey("categories.id"), index=True)
    name: Mapped[str]
    slug: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[datetime] = created_at_column()

    # remote_side says which side of the self-join is the "one" side: the
    # parent's id. Without it SQLAlchemy cannot tell parent from children.
    parent: Mapped[Category | None] = relationship(back_populates="children", remote_side=[id])
    children: Mapped[list[Category]] = relationship(
        back_populates="parent", order_by="Category.name"
    )
    products: Mapped[list[Product]] = relationship(back_populates="category")

    def __repr__(self) -> str:
        return f"Category(id={self.id!r}, slug={self.slug!r})"


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (sa.UniqueConstraint("seller_id", "sku"),)

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(always=True), primary_key=True)
    seller_id: Mapped[int] = mapped_column(sa.ForeignKey("sellers.id"))
    category_id: Mapped[int] = mapped_column(sa.ForeignKey("categories.id"), index=True)
    sku: Mapped[str]
    title: Mapped[str]
    description: Mapped[str | None]
    price: Mapped[Decimal]
    status: Mapped[ProductStatus] = mapped_column(server_default="draft")
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
    # In-place changes (p.attributes["color"] = "red") are only noticed because
    # of MutableDict; a plain JSONB column only tracks reassignment.
    attributes: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(pg.JSONB), server_default=sa.text("'{}'::jsonb")
    )
    tags: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(pg.ARRAY(sa.Text)), server_default=sa.text("'{}'::text[]")
    )
    # Generated by the database (module 13); deferred: rarely needed in Python.
    search_vector: Mapped[str | None] = mapped_column(
        pg.TSVECTOR,
        sa.Computed(
            "setweight(to_tsvector('english', coalesce(title, '')), 'A')"
            " || setweight(to_tsvector('english', coalesce(description, '')), 'B')"
            " || setweight(to_tsvector('english', tags_to_text(tags)), 'C')",
            persisted=True,
        ),
        deferred=True,
    )
    # Module 26 (revisions 2602-2603): the products_set_slug() trigger fills it
    # when an INSERT leaves it NULL; FetchedValue makes the ORM fetch it back
    # (INSERT ... RETURNING) instead of sending NULL explicitly.
    slug: Mapped[str] = mapped_column(server_default=sa.FetchedValue())
    # Optimistic concurrency without a schema change: PostgreSQL's system
    # column xmin changes with every new row version (module 16). Every ORM
    # UPDATE says "... WHERE id = :id AND xmin = :seen" and raises
    # StaleDataError when another transaction changed the row meanwhile.
    xmin: Mapped[int] = mapped_column(XID(), system=True, server_default=sa.FetchedValue())

    seller: Mapped[Seller] = relationship(back_populates="products")
    category: Mapped[Category] = relationship(back_populates="products")
    inventory: Mapped[Inventory | None] = relationship(
        back_populates="product", cascade="all, delete-orphan", passive_deletes=True
    )
    reviews: Mapped[list[Review]] = relationship(
        back_populates="product", cascade="all, delete-orphan", passive_deletes=True
    )
    promotions: Mapped[list[Promotion]] = relationship(
        back_populates="product", cascade="all, delete-orphan", passive_deletes=True
    )

    __mapper_args__ = {"version_id_col": xmin, "version_id_generator": False}

    def __repr__(self) -> str:
        return f"Product(id={self.id!r}, sku={self.sku!r}, price={self.price!r})"


class Inventory(Base):
    """One-to-one with Product: the primary key is also the foreign key."""

    __tablename__ = "inventory"

    product_id: Mapped[int] = mapped_column(
        sa.ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    quantity: Mapped[int] = mapped_column(server_default="0")
    reserved: Mapped[int] = mapped_column(server_default="0")
    updated_at: Mapped[datetime] = updated_at_column()

    product: Mapped[Product] = relationship(back_populates="inventory")

    @hybrid_property
    def available(self) -> int:
        """Units that can still be sold. Works on instances *and* in queries:
        select(Inventory).where(Inventory.available > 0)."""
        return self.quantity - self.reserved

    def __repr__(self) -> str:
        return f"Inventory(product_id={self.product_id!r}, {self.quantity}-{self.reserved})"


class OrderItem(Base):
    """Association object between Order and Product, with a composite primary key."""

    __tablename__ = "order_items"

    order_id: Mapped[int] = mapped_column(
        sa.ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[int] = mapped_column(
        sa.ForeignKey("products.id"), primary_key=True, index=True
    )
    quantity: Mapped[int]
    unit_price: Mapped[Decimal]

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()

    @hybrid_property
    def line_total(self) -> Decimal:
        return self.quantity * self.unit_price

    def __repr__(self) -> str:
        return f"OrderItem({self.order_id!r}, {self.product_id!r}, qty={self.quantity!r})"


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id"))
    seller_id: Mapped[int] = mapped_column(sa.ForeignKey("sellers.id"))
    status: Mapped[OrderStatus] = mapped_column(server_default="pending")
    # Maintained by a trigger on order_items (module 09): after items change,
    # the in-memory value is stale until the object is refreshed/expired.
    total: Mapped[Decimal] = mapped_column(server_default="0", server_onupdate=sa.FetchedValue())
    created_at: Mapped[datetime] = created_at_column()
    paid_at: Mapped[datetime | None]
    shipped_at: Mapped[datetime | None]
    delivered_at: Mapped[datetime | None]
    shipping_address_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("addresses.id"), index=True
    )
    updated_at: Mapped[datetime] = updated_at_column()
    # Module 26 (revision 2604): set by an idempotent checkout, NULL otherwise.
    idempotency_key: Mapped[str | None]

    user: Mapped[User] = relationship(back_populates="orders")
    seller: Mapped[Seller] = relationship(back_populates="orders")
    shipping_address: Mapped[Address | None] = relationship()
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan", passive_deletes=True
    )
    payments: Mapped[list[Payment]] = relationship(back_populates="order")

    # A correlated subquery loaded with the object (deferred: only on request,
    # e.g. .options(undefer(Order.item_count))).
    item_count: Mapped[int] = column_property(
        sa.select(sa.func.count())
        .where(OrderItem.order_id == id)
        .correlate_except(OrderItem)
        .scalar_subquery(),
        deferred=True,
    )

    def __repr__(self) -> str:
        return f"Order(id={self.id!r}, status={self.status!r}, total={self.total!r})"


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(always=True), primary_key=True)
    order_id: Mapped[int] = mapped_column(sa.ForeignKey("orders.id"), index=True)
    amount: Mapped[Decimal]  # negative = refund
    provider: Mapped[str]
    provider_ref: Mapped[str | None]
    status: Mapped[PaymentStatus]
    created_at: Mapped[datetime] = created_at_column()

    order: Mapped[Order] = relationship(back_populates="payments")


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (sa.UniqueConstraint("product_id", "user_id"),)

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(always=True), primary_key=True)
    product_id: Mapped[int] = mapped_column(sa.ForeignKey("products.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id"), index=True)
    rating: Mapped[int] = mapped_column(sa.SmallInteger)
    title: Mapped[str | None]
    body: Mapped[str | None]
    created_at: Mapped[datetime] = created_at_column()

    product: Mapped[Product] = relationship(back_populates="reviews")
    user: Mapped[User] = relationship(back_populates="reviews")


class Address(Base):
    __tablename__ = "addresses"

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str | None]
    recipient: Mapped[str]
    line1: Mapped[str]
    line2: Mapped[str | None]
    city: Mapped[str]
    postal_code: Mapped[str]
    country_code: Mapped[str] = mapped_column(sa.CHAR(2))
    is_default: Mapped[bool] = mapped_column(server_default=sa.false())
    created_at: Mapped[datetime] = created_at_column()

    user: Mapped[User] = relationship(back_populates="addresses")


class CartItem(Base):
    __tablename__ = "cart_items"

    user_id: Mapped[int] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[int] = mapped_column(
        sa.ForeignKey("products.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    quantity: Mapped[int]
    added_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())

    product: Mapped[Product] = relationship()


class Promotion(Base):
    __tablename__ = "promotions"

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(always=True), primary_key=True)
    product_id: Mapped[int] = mapped_column(sa.ForeignKey("products.id", ondelete="CASCADE"))
    discount_pct: Mapped[Decimal] = mapped_column(sa.Numeric(5, 2))
    active_during: Mapped[Range[datetime]] = mapped_column(pg.TSTZRANGE)
    created_at: Mapped[datetime] = created_at_column()

    product: Mapped[Product] = relationship(back_populates="promotions")


class ActiveProduct(Base):
    """The active_products view (module 09), mapped read-only.

    It reuses the Core Table from bazaar.sa.tables (which lives in a separate
    MetaData, so Base.metadata.create_all() never creates a table for it).
    bazaar.orm.session refuses to flush changes to it.
    """

    __table__ = core.active_products

    def __repr__(self) -> str:
        return f"ActiveProduct(id={self.id!r}, title={self.title!r})"


READ_ONLY = (ActiveProduct,)
