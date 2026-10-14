"""Module 24 milestone: Core tables (and their drift test), engines, Core reports."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import psycopg
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from bazaar.models import OrderStatus
from bazaar.sa import reports
from bazaar.sa.drift import compare
from bazaar.sa.engine import count_statements, sa_url
from bazaar.sa.tables import metadata, order_items, orders, products, view_metadata

# --- metadata vs. database -------------------------------------------------------------


def test_tables_match_database(sa_conn: sa.Connection) -> None:
    assert compare(sa_conn, metadata, view_metadata) == []


def test_drift_is_detected(sa_conn: sa.Connection) -> None:
    """The comparison is only worth something if it fails: change the schema
    inside the (rolled-back) test transaction and expect precise complaints."""
    sa_conn.execute(sa.text("ALTER TABLE reviews ADD COLUMN helpful_votes int"))
    sa_conn.execute(sa.text("ALTER TABLE products ALTER COLUMN sku DROP NOT NULL"))
    sa_conn.execute(sa.text("ALTER TABLE jobs ALTER COLUMN kind TYPE varchar(40)"))
    sa_conn.execute(sa.text("DROP INDEX products_tags_idx"))
    sa_conn.execute(sa.text("CREATE TABLE wishlist (id int PRIMARY KEY)"))
    problems = compare(sa_conn, metadata, view_metadata)
    assert sorted(problems) == [
        "jobs.kind: type text in metadata, varchar(40) in database",
        "products.sku: nullable=False in metadata, True in database",
        "products: index ('products_tags_idx', False) only in metadata",
        "reviews.helpful_votes: column not in metadata",
        "wishlist: table not in metadata",
    ]


# --- engines ----------------------------------------------------------------------------


def test_sa_url_conversion() -> None:
    url = sa_url("postgresql://u:p%40ss@db.example:6432/bazaar?sslmode=require", "asyncpg")
    assert url.drivername == "postgresql+asyncpg"
    assert url.password == "p@ss" and url.port == 6432 and url.query["sslmode"] == "require"
    with pytest.raises(ValueError):
        sa_url("host=localhost dbname=bazaar")


def test_engine_session_settings(sa_conn: sa.Connection) -> None:
    app = sa_conn.execute(sa.text("SHOW application_name")).scalar_one()
    timeout = sa_conn.execute(sa.text("SHOW statement_timeout")).scalar_one()
    assert app == "bazaar" and timeout == "5s"
    assert sa_conn.engine.pool.size() == 3  # the fixture's pool_size


async def test_async_engine(async_engine: AsyncEngine) -> None:
    async with async_engine.connect() as conn:
        n = await conn.scalar(sa.select(sa.func.count()).select_from(products))
        status = await conn.scalar(sa.select(orders.c.status).where(orders.c.id == 1))
    assert n is not None and n > 0
    assert isinstance(status, OrderStatus)  # the ENUM type maps to the Python enum


# --- reports ------------------------------------------------------------------------------

NOV = (date(2025, 11, 1), date(2025, 12, 1))


def test_seller_statement(sa_conn: sa.Connection, conn: psycopg.Connection) -> None:
    st = reports.seller_statement(sa_conn, 42, *NOV)
    assert st is not None and st.shop_name
    # Cross-check with independent plain SQL (module 22 style) ...
    n_orders, refunded = conn.execute(
        """
        SELECT count(DISTINCT o.id),
               coalesce(-sum(p.amount) FILTER (WHERE p.amount < 0 AND p.status = 'succeeded'), 0)
        FROM orders o LEFT JOIN payments p ON p.order_id = o.id
        WHERE o.seller_id = 42 AND o.created_at >= '2025-11-01' AND o.created_at < '2025-12-01'
        """
    ).fetchone()  # type: ignore[misc]
    # ... and with the materialized view of module 09, which uses the same "sale" rule.
    (mv_revenue,) = conn.execute(
        "SELECT sum(revenue) FROM seller_sales_daily"
        " WHERE seller_id = 42 AND day >= '2025-11-01' AND day < '2025-12-01'"
    ).fetchone()  # type: ignore[misc]
    assert st.orders == n_orders == len(st.lines)
    assert st.refunded == refunded
    assert st.sales == mv_revenue
    assert all(isinstance(ln.total, Decimal) for ln in st.lines)
    assert [ln.created_at for ln in st.lines] == sorted(ln.created_at for ln in st.lines)


def test_seller_statement_unknown_seller(sa_conn: sa.Connection) -> None:
    assert reports.seller_statement(sa_conn, -1, *NOV) is None


def test_category_rollup(sa_conn: sa.Connection, conn: psycopg.Connection) -> None:
    rows = reports.category_revenue_rollup(sa_conn, date(2025, 1, 1), date(2026, 1, 1))
    by_id = {r.category_id: r for r in rows}
    assert len(rows) == conn.execute("SELECT count(*) FROM categories").fetchone()[0]  # type: ignore[index]

    # Every node = its own revenue + the revenue of its children.
    parents = dict(conn.execute("SELECT id, parent_id FROM categories").fetchall())
    for r in rows:
        children = [c for c in rows if parents[c.category_id] == r.category_id]
        assert r.revenue == r.own_revenue + sum((c.revenue for c in children), Decimal(0))

    # The roots add up to the grand total.
    (total,) = conn.execute(
        """
        SELECT sum(oi.quantity * oi.unit_price)
        FROM order_items oi JOIN orders o ON o.id = oi.order_id
        WHERE o.status IN ('paid', 'shipped', 'delivered')
          AND o.created_at >= '2025-01-01' AND o.created_at < '2026-01-01'
        """
    ).fetchone()  # type: ignore[misc]
    roots = [r for r in rows if r.depth == 0]
    assert sum((r.revenue for r in roots), Decimal(0)) == total
    assert by_id[3].path == "Electronics > Computers > Laptops"


def test_reports_are_single_statements(sa_conn: sa.Connection) -> None:
    with count_statements(sa_conn.engine) as stmts:
        reports.category_revenue_rollup(sa_conn, date(2025, 12, 1), date(2026, 1, 1))
        reports.seller_statement(sa_conn, 7, *NOV)
    assert len(stmts) == 3  # rollup + (shop name, statement lines)


def test_line_value_expression_compiles_to_sql() -> None:
    expr = sa.func.sum(order_items.c.quantity * order_items.c.unit_price)
    assert str(expr) == "sum(order_items.quantity * order_items.unit_price)"
