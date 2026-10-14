"""The Bazaar schema as SQLAlchemy Core metadata (state of the database from module 24 on).

This file *describes* tables that the SQL files in project/db/ create; it does
not own them (until module 26, where Alembic takes over). Two consequences:

* The Postgres enum types and the email_address domain already exist, so their
  SQLAlchemy types say create_type=False: metadata.create_all() (used only for
  throwaway test schemas) must never try to create them.
* The description can drift from the real schema. tests/test_sa_tables.py
  compares this metadata with the live database through reflection
  (bazaar.sa.drift) and fails on any difference.

Tables are not schema-qualified: the database's search_path (bazaar, public)
resolves them, exactly as in the plain-SQL repository of module 22.

The naming convention reproduces PostgreSQL's own default names
(products_pkey, products_seller_id_fkey, products_seller_id_sku_key, ...), so
constraints declared here get the same names the SQL files gave them.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from bazaar.models import OrderStatus, PaymentStatus, ProductStatus

NAMING_CONVENTION = {
    "pk": "%(table_name)s_pkey",
    "fk": "%(table_name)s_%(column_0_name)s_fkey",
    "uq": "%(table_name)s_%(column_0_N_name)s_key",
    "ix": "%(table_name)s_%(column_0_N_name)s_idx",
    "ck": "%(table_name)s_%(constraint_name)s",
}

metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)


def _values(enum_cls: type) -> list[str]:
    return [m.value for m in enum_cls]  # type: ignore[attr-defined]


# Existing database types: never CREATE/DROP them from Python.
order_status = pg.ENUM(
    OrderStatus, name="order_status", create_type=False, values_callable=_values
)
product_status = pg.ENUM(
    ProductStatus, name="product_status", create_type=False, values_callable=_values
)
payment_status = pg.ENUM(
    PaymentStatus, name="payment_status", create_type=False, values_callable=_values
)
email_address = pg.DOMAIN("email_address", sa.Text(), create_type=False)

Money = sa.Numeric(12, 2)  # numeric(12,2) <-> decimal.Decimal
TSTZ = sa.DateTime(timezone=True)  # timestamptz <-> aware datetime


def _id(type_: sa.types.TypeEngine = sa.BigInteger()) -> sa.Column:
    return sa.Column("id", type_, sa.Identity(always=True), primary_key=True)


def _created_at() -> sa.Column:
    return sa.Column("created_at", TSTZ, nullable=False, server_default=sa.func.now())


# --- module 02: core -----------------------------------------------------------------

users = sa.Table(
    "users",
    metadata,
    _id(),
    sa.Column("email", email_address, nullable=False),
    sa.Column("full_name", sa.Text, nullable=False),
    sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
    _created_at(),
    # module 08: case-insensitive uniqueness through an expression index
    sa.Index("users_email_lower_key", sa.func.lower(sa.text("email")), unique=True),
)

sellers = sa.Table(
    "sellers",
    metadata,
    _id(),
    sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id"), nullable=False, unique=True),
    sa.Column("shop_name", sa.Text, nullable=False, unique=True),
    sa.Column("country_code", sa.CHAR(2), nullable=False),
    _created_at(),
    sa.CheckConstraint("country_code ~ '^[A-Z]{2}$'", name="country_code_upper"),
)

categories = sa.Table(
    "categories",
    metadata,
    _id(sa.Integer()),
    sa.Column("parent_id", sa.Integer, sa.ForeignKey("categories.id"), index=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("slug", sa.Text, nullable=False, unique=True),
    _created_at(),
    sa.CheckConstraint("parent_id <> id", name="not_own_parent"),
)

products = sa.Table(
    "products",
    metadata,
    _id(),
    sa.Column("seller_id", sa.BigInteger, sa.ForeignKey("sellers.id"), nullable=False),
    sa.Column(
        "category_id", sa.Integer, sa.ForeignKey("categories.id"), nullable=False, index=True
    ),
    sa.Column("sku", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("description", sa.Text),
    sa.Column("price", Money, nullable=False),
    sa.Column("status", product_status, nullable=False, server_default="draft"),
    _created_at(),
    sa.Column("updated_at", TSTZ, nullable=False, server_default=sa.func.now()),
    sa.Column("attributes", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column(
        "tags", pg.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'::text[]")
    ),
    # module 13: a stored generated column; never written by the application
    sa.Column(
        "search_vector",
        pg.TSVECTOR,
        sa.Computed(
            "setweight(to_tsvector('english', coalesce(title, '')), 'A')"
            " || setweight(to_tsvector('english', coalesce(description, '')), 'B')"
            " || setweight(to_tsvector('english', tags_to_text(tags)), 'C')",
            persisted=True,
        ),
    ),
    sa.UniqueConstraint("seller_id", "sku"),
    sa.CheckConstraint("price >= 0", name="price_nonnegative"),
    sa.CheckConstraint("jsonb_typeof(attributes) = 'object'", name="attributes_is_object"),
    # module 11 / 13 indexes
    sa.Index(
        "products_active_category_id_price_idx",
        "category_id",
        "price",
        postgresql_where=sa.text("status = 'active'"),
    ),
    sa.Index(
        "products_attributes_idx",
        "attributes",
        postgresql_using="gin",
        postgresql_ops={"attributes": "jsonb_path_ops"},
    ),
    sa.Index("products_tags_idx", "tags", postgresql_using="gin"),
    sa.Index("products_search_vector_idx", "search_vector", postgresql_using="gin"),
    sa.Index(
        "products_title_trgm_idx",
        "title",
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    ),
)

inventory = sa.Table(
    "inventory",
    metadata,
    sa.Column(
        "product_id",
        sa.BigInteger,
        sa.ForeignKey("products.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("quantity", sa.Integer, nullable=False, server_default="0"),
    sa.Column("reserved", sa.Integer, nullable=False, server_default="0"),
    sa.Column("updated_at", TSTZ, nullable=False, server_default=sa.func.now()),
    sa.CheckConstraint("quantity >= 0", name="quantity_nonnegative"),
    sa.CheckConstraint("reserved >= 0 AND reserved <= quantity", name="reserved_within_quantity"),
)

orders = sa.Table(
    "orders",
    metadata,
    _id(),
    sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id"), nullable=False),
    sa.Column("seller_id", sa.BigInteger, sa.ForeignKey("sellers.id"), nullable=False),
    sa.Column("status", order_status, nullable=False, server_default="pending"),
    # maintained by the order_items_maintain_total() trigger (module 09)
    sa.Column("total", Money, nullable=False, server_default="0"),
    _created_at(),
    sa.Column("paid_at", TSTZ),
    sa.Column("shipped_at", TSTZ),
    sa.Column("delivered_at", TSTZ),
    sa.Column("shipping_address_id", sa.BigInteger, sa.ForeignKey("addresses.id"), index=True),
    sa.Column("updated_at", TSTZ, nullable=False, server_default=sa.func.now()),
    sa.Index("orders_user_id_created_at_idx", "user_id", sa.text("created_at DESC")),
    sa.Index("orders_seller_id_created_at_idx", "seller_id", sa.text("created_at DESC")),
)

order_items = sa.Table(
    "order_items",
    metadata,
    sa.Column(
        "order_id",
        sa.BigInteger,
        sa.ForeignKey("orders.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column(
        "product_id", sa.BigInteger, sa.ForeignKey("products.id"), primary_key=True, index=True
    ),
    sa.Column("quantity", sa.Integer, nullable=False),
    sa.Column("unit_price", Money, nullable=False),
    sa.CheckConstraint("quantity > 0", name="quantity_positive"),
    sa.CheckConstraint("unit_price >= 0", name="unit_price_nonnegative"),
)

# --- module 07: modeling ---------------------------------------------------------------

addresses = sa.Table(
    "addresses",
    metadata,
    _id(),
    sa.Column(
        "user_id",
        sa.BigInteger,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    sa.Column("label", sa.Text),
    sa.Column("recipient", sa.Text, nullable=False),
    sa.Column("line1", sa.Text, nullable=False),
    sa.Column("line2", sa.Text),
    sa.Column("city", sa.Text, nullable=False),
    sa.Column("postal_code", sa.Text, nullable=False),
    sa.Column("country_code", sa.CHAR(2), nullable=False),
    sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.false()),
    _created_at(),
    sa.Index(
        "addresses_one_default_per_user",
        "user_id",
        unique=True,
        postgresql_where=sa.text("is_default"),
    ),
)

payments = sa.Table(
    "payments",
    metadata,
    _id(),
    sa.Column("order_id", sa.BigInteger, sa.ForeignKey("orders.id"), nullable=False, index=True),
    sa.Column("amount", Money, nullable=False),  # negative = refund
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("provider_ref", sa.Text),
    sa.Column("status", payment_status, nullable=False),
    _created_at(),
    sa.CheckConstraint("amount <> 0", name="amount_nonzero"),
)

reviews = sa.Table(
    "reviews",
    metadata,
    _id(),
    sa.Column(
        "product_id",
        sa.BigInteger,
        sa.ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id"), nullable=False, index=True),
    sa.Column("rating", sa.SmallInteger, nullable=False),
    sa.Column("title", sa.Text),
    sa.Column("body", sa.Text),
    _created_at(),
    sa.UniqueConstraint("product_id", "user_id"),
    sa.CheckConstraint("rating BETWEEN 1 AND 5", name="rating_range"),
)

cart_items = sa.Table(
    "cart_items",
    metadata,
    sa.Column(
        "user_id",
        sa.BigInteger,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column(
        "product_id",
        sa.BigInteger,
        sa.ForeignKey("products.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
    sa.Column("quantity", sa.Integer, nullable=False),
    sa.Column("added_at", TSTZ, nullable=False, server_default=sa.func.now()),
    sa.CheckConstraint("quantity > 0", name="quantity_positive"),
)

product_price_history = sa.Table(
    "product_price_history",
    metadata,
    sa.Column(
        "product_id",
        sa.BigInteger,
        sa.ForeignKey("products.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("price", Money, nullable=False),
    sa.Column("valid_from", TSTZ, primary_key=True),
    sa.CheckConstraint("price >= 0", name="price_nonnegative"),
)

# --- module 08: promotions ---------------------------------------------------------------

promotions = sa.Table(
    "promotions",
    metadata,
    _id(),
    sa.Column(
        "product_id",
        sa.BigInteger,
        sa.ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("discount_pct", sa.Numeric(5, 2), nullable=False),
    sa.Column("active_during", pg.TSTZRANGE, nullable=False),  # <-> sqlalchemy Range
    _created_at(),
    sa.CheckConstraint("discount_pct > 0 AND discount_pct < 100", name="discount_pct_range"),
    sa.CheckConstraint("NOT isempty(active_during)", name="range_not_empty"),
    pg.ExcludeConstraint(
        ("product_id", "="),
        ("active_during", "&&"),
        name="promotions_no_overlap",
        using="gist",
    ),
)

# --- modules 09-14: logic, jobs, events --------------------------------------------------

audit_log = sa.Table(
    "audit_log",
    metadata,
    _id(),
    sa.Column("table_name", sa.Text, nullable=False),
    sa.Column("operation", sa.Text, nullable=False),
    sa.Column("row_pk", pg.JSONB),
    sa.Column("old_row", pg.JSONB),
    sa.Column("new_row", pg.JSONB),
    sa.Column("changed_by", sa.Text, nullable=False, server_default=sa.text("CURRENT_USER")),
    sa.Column("changed_at", TSTZ, nullable=False, server_default=sa.func.now()),
    sa.CheckConstraint("operation IN ('INSERT', 'UPDATE', 'DELETE')", name="operation_check"),
)

jobs = sa.Table(
    "jobs",
    metadata,
    _id(),
    sa.Column("queue", sa.Text, nullable=False, server_default="default"),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("payload", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("run_at", TSTZ, nullable=False, server_default=sa.func.now()),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("max_attempts", sa.Integer, nullable=False, server_default="5"),
    sa.Column("locked_at", TSTZ),
    sa.Column("locked_by", sa.Text),
    sa.Column("last_error", sa.Text),
    sa.Column("done_at", TSTZ),
    _created_at(),
    sa.CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
    sa.CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
    sa.Index("jobs_ready_idx", "queue", "run_at", postgresql_where=sa.text("done_at IS NULL")),
)

EVENT_TYPES = (
    "page_view",
    "product_view",
    "add_to_cart",
    "remove_from_cart",
    "checkout_started",
    "order_placed",
    "search",
)

events = sa.Table(
    "events",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("occurred_at", TSTZ, primary_key=True),
    sa.Column("user_id", sa.BigInteger),  # no FK: anonymous visitors, high volume
    sa.Column("session_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("product_id", sa.BigInteger),
    sa.Column("payload", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.CheckConstraint(
        "event_type IN (" + ", ".join(f"'{t}'" for t in EVENT_TYPES) + ")",
        name="event_type_check",
    ),
    sa.Index("events_occurred_at_idx", "occurred_at"),
    sa.Index("events_product_id_idx", "product_id"),
    postgresql_partition_by="RANGE (occurred_at)",  # partitions are managed in SQL (module 14)
)

# --- views: a separate MetaData, so create_all() never turns them into tables -----------

view_metadata = sa.MetaData()

active_products = sa.Table(
    "active_products",
    view_metadata,
    sa.Column("id", sa.BigInteger, primary_key=True),  # "primary key" for SQLAlchemy only
    sa.Column("seller_id", sa.BigInteger),
    sa.Column("shop_name", sa.Text),
    sa.Column("category_id", sa.Integer),
    sa.Column("category_name", sa.Text),
    sa.Column("sku", sa.Text),
    sa.Column("title", sa.Text),
    sa.Column("description", sa.Text),
    sa.Column("price", Money),
    sa.Column("attributes", pg.JSONB),
    sa.Column("tags", pg.ARRAY(sa.Text)),
    sa.Column("available", sa.Integer),
    sa.Column("created_at", TSTZ),
    sa.Column("updated_at", TSTZ),
    info={"kind": "view"},
)

seller_sales_daily = sa.Table(
    "seller_sales_daily",
    view_metadata,
    sa.Column("seller_id", sa.BigInteger, primary_key=True),
    sa.Column("day", sa.Date, primary_key=True),
    sa.Column("orders_count", sa.BigInteger),
    sa.Column("items_sold", sa.Numeric),
    sa.Column("revenue", sa.Numeric),
    info={"kind": "materialized view"},
)
