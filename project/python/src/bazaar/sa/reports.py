"""Reports written with the SQLAlchemy Core expression language.

Reports are where Core shines: the statements are built from reusable parts
(a "sales" filter, a category-tree CTE), stay fully parameterized, and run as
one SQL statement each. Every function takes a Connection first and never
commits, like bazaar.repo.

    with engine.connect() as conn:
        st = seller_statement(conn, 42, date(2025, 11, 1), date(2025, 12, 1))
        rows = category_revenue_rollup(conn, date(2025, 1, 1), date(2026, 1, 1))
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from bazaar.models import OrderStatus
from bazaar.sa.tables import categories, order_items, orders, payments, products, sellers

UTC = ZoneInfo("UTC")

#: Orders whose money was taken and not returned (same rule as seller_sales_daily).
SALE_STATUSES = (OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DELIVERED)


def utc_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """[start, end) as UTC timestamps: report days are UTC days (module 09)."""
    return datetime.combine(start, time(), UTC), datetime.combine(end, time(), UTC)


def is_sale() -> sa.ColumnElement[bool]:
    return orders.c.status.in_(SALE_STATUSES)


# --- seller statement -----------------------------------------------------------------


@dataclass(slots=True)
class StatementLine:
    order_id: int
    created_at: datetime
    status: OrderStatus
    items: int  # units
    total: Decimal
    paid: Decimal  # succeeded payments, positive amounts
    refunded: Decimal  # succeeded refunds, as a positive number


@dataclass(slots=True)
class SellerStatement:
    seller_id: int
    shop_name: str
    start: date
    end: date  # exclusive
    lines: list[StatementLine]
    orders: int
    sales: Decimal  # sum(total) of paid/shipped/delivered orders
    paid: Decimal
    refunded: Decimal

    @property
    def net_cash(self) -> Decimal:
        return self.paid - self.refunded


def seller_statement_query(seller_id: int, start: date, end: date) -> sa.Select:
    """One row per order of the seller created in [start, end), oldest first.

    Items and payments are aggregated per order in LATERAL subqueries: each
    runs for one order at a time (an index lookup), and aggregating before
    joining keeps the two one-to-many joins from multiplying each other's
    rows. (A plain GROUP BY subquery over all of order_items would aggregate
    1.3 million rows to report on a few hundred orders: Postgres does not push
    the join condition into an aggregated subquery.)
    """
    lo, hi = utc_bounds(start, end)
    items = (
        sa.select(sa.func.coalesce(sa.func.sum(order_items.c.quantity), 0).label("units"))
        .where(order_items.c.order_id == orders.c.id)
        .lateral("items")
    )
    ok = payments.c.status == "succeeded"
    pays = (
        sa.select(
            sa.func.coalesce(
                sa.func.sum(payments.c.amount).filter(ok, payments.c.amount > 0), 0
            ).label("paid"),
            sa.func.coalesce(
                -sa.func.sum(payments.c.amount).filter(ok, payments.c.amount < 0), 0
            ).label("refunded"),
        )
        .where(payments.c.order_id == orders.c.id)
        .lateral("pays")
    )
    return (
        sa.select(
            orders.c.id.label("order_id"),
            orders.c.created_at,
            orders.c.status,
            items.c.units.cast(sa.Integer).label("items"),
            orders.c.total,
            pays.c.paid,
            pays.c.refunded,
        )
        .select_from(orders)
        .join(items, sa.true())
        .join(pays, sa.true())
        .where(
            orders.c.seller_id == seller_id,
            orders.c.created_at >= lo,
            orders.c.created_at < hi,
        )
        .order_by(orders.c.created_at, orders.c.id)
    )


def seller_statement(
    conn: Connection, seller_id: int, start: date, end: date
) -> SellerStatement | None:
    """The statement of one seller for [start, end); None if the seller doesn't exist."""
    shop = conn.execute(
        sa.select(sellers.c.shop_name).where(sellers.c.id == seller_id)
    ).scalar_one_or_none()
    if shop is None:
        return None
    lines = [
        StatementLine(**row._asdict())
        for row in conn.execute(seller_statement_query(seller_id, start, end))
    ]
    zero = Decimal("0.00")
    return SellerStatement(
        seller_id=seller_id,
        shop_name=shop,
        start=start,
        end=end,
        lines=lines,
        orders=len(lines),
        sales=sum((ln.total for ln in lines if ln.status in SALE_STATUSES), zero),
        paid=sum((ln.paid for ln in lines), zero),
        refunded=sum((ln.refunded for ln in lines), zero),
    )


# --- category revenue rollup ------------------------------------------------------------


@dataclass(slots=True)
class CategoryRevenue:
    category_id: int
    name: str
    depth: int  # 0 = root
    path: str  # 'Electronics > Computers > Laptops'
    own_revenue: Decimal  # products directly in this category
    revenue: Decimal  # this category and every descendant
    units: int  # units sold in the subtree


def category_tree_cte() -> sa.CTE:
    """Recursive CTE: (id, name, depth, path) for every category, roots first."""
    c = categories
    roots = sa.select(
        c.c.id,
        c.c.name,
        sa.literal(0).label("depth"),
        sa.cast(c.c.name, sa.Text).label("path"),
    ).where(c.c.parent_id.is_(None))
    tree = roots.cte("tree", recursive=True)
    child = c.alias("child")
    return tree.union_all(
        sa.select(
            child.c.id,
            child.c.name,
            tree.c.depth + 1,
            tree.c.path + " > " + child.c.name,
        ).join_from(tree, child, child.c.parent_id == tree.c.id)
    )


def category_closure_cte() -> sa.CTE:
    """Recursive CTE of (ancestor_id, descendant_id) pairs, including (id, id)."""
    c = categories
    closure = sa.select(c.c.id.label("ancestor_id"), c.c.id.label("descendant_id")).cte(
        "closure", recursive=True
    )
    child = c.alias("child")
    return closure.union_all(
        sa.select(closure.c.ancestor_id, child.c.id).join_from(
            closure, child, child.c.parent_id == closure.c.descendant_id
        )
    )


def category_revenue_query(start: date, end: date) -> sa.Select:
    lo, hi = utc_bounds(start, end)
    line_value = order_items.c.quantity * order_items.c.unit_price
    # Revenue per category of the products sold (only categories with sales).
    sold = (
        sa.select(
            products.c.category_id,
            sa.func.sum(line_value).label("revenue"),
            sa.func.sum(order_items.c.quantity).label("units"),
        )
        .select_from(order_items)
        .join(orders, orders.c.id == order_items.c.order_id)
        .join(products, products.c.id == order_items.c.product_id)
        .where(is_sale(), orders.c.created_at >= lo, orders.c.created_at < hi)
        .group_by(products.c.category_id)
        .cte("sold")
    )
    tree = category_tree_cte()
    closure = category_closure_cte()
    zero = sa.literal(0, sa.Numeric(12, 2))
    own = sa.func.sum(sold.c.revenue).filter(closure.c.descendant_id == tree.c.id)
    return (
        sa.select(
            tree.c.id.label("category_id"),
            tree.c.name,
            tree.c.depth,
            tree.c.path,
            sa.func.coalesce(own, zero).label("own_revenue"),
            sa.func.coalesce(sa.func.sum(sold.c.revenue), zero).label("revenue"),
            sa.func.coalesce(sa.func.sum(sold.c.units), 0).cast(sa.Integer).label("units"),
        )
        .select_from(tree)
        .join(closure, closure.c.ancestor_id == tree.c.id)
        .outerjoin(sold, sold.c.category_id == closure.c.descendant_id)
        .group_by(tree.c.id, tree.c.name, tree.c.depth, tree.c.path)
        .order_by(tree.c.path)
    )


def category_revenue_rollup(conn: Connection, start: date, end: date) -> list[CategoryRevenue]:
    """Revenue per category for orders created in [start, end), rolled up the tree.

    Every category appears (with 0 if nothing sold), ordered by path so the
    result reads like an indented tree.
    """
    return [
        CategoryRevenue(**row._asdict()) for row in conn.execute(category_revenue_query(start, end))
    ]
