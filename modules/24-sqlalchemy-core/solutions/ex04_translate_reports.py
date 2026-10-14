"""Exercise 4: four SQL reports translated to Core, checked row for row against the SQL."""

from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa

from bazaar.sa.engine import create_engine
from bazaar.sa.tables import categories, order_items, orders, products, reviews, sellers

o, oi, p, c = orders, order_items, products, categories
SALE = ["paid", "shipped", "delivered"]


def ts(y: int, m: int, d: int = 1) -> datetime:
    return datetime(y, m, d, tzinfo=UTC)


# --- (a) monthly revenue in 2025 with month-over-month growth --------------------------------
SQL_A = """
    SELECT month, revenue,
           round(100 * (revenue / lag(revenue) OVER (ORDER BY month) - 1), 1) AS growth_pct
    FROM (SELECT date_trunc('month', created_at AT TIME ZONE 'UTC') AS month,
                 sum(total) AS revenue
          FROM orders
          WHERE status IN ('paid', 'shipped', 'delivered')
            AND created_at >= '2025-01-01 00:00+00' AND created_at < '2026-01-01 00:00+00'
          GROUP BY 1) m
    ORDER BY month
"""


def core_a() -> sa.Select:
    month = sa.func.date_trunc("month", sa.func.timezone("UTC", o.c.created_at)).label("month")
    m = (
        sa.select(month, sa.func.sum(o.c.total).label("revenue"))
        .where(o.c.status.in_(SALE), o.c.created_at >= ts(2025, 1), o.c.created_at < ts(2026, 1))
        .group_by(month)
        .subquery("m")
    )
    prev = sa.func.lag(m.c.revenue).over(order_by=m.c.month)
    return sa.select(
        m.c.month,
        m.c.revenue,
        sa.func.round(Decimal(100) * (m.c.revenue / prev - 1), 1).label("growth_pct"),
    ).order_by(m.c.month)


# --- (b) top 3 products by units sold in 2025, per category, under Books ----------------------
SQL_B = """
    WITH RECURSIVE sub AS (
        SELECT id FROM categories WHERE slug = 'books'
        UNION ALL
        SELECT ch.id FROM categories ch JOIN sub ON ch.parent_id = sub.id
    ), sold AS (
        SELECT p.category_id, p.id, p.title, sum(oi.quantity) AS units,
               row_number() OVER (PARTITION BY p.category_id
                                  ORDER BY sum(oi.quantity) DESC, p.id) AS rn
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        JOIN products p ON p.id = oi.product_id
        WHERE o.status IN ('paid', 'shipped', 'delivered')
          AND o.created_at >= '2025-01-01 00:00+00' AND o.created_at < '2026-01-01 00:00+00'
          AND p.category_id IN (SELECT id FROM sub)
        GROUP BY p.category_id, p.id, p.title
    )
    SELECT category_id, id, title, units FROM sold WHERE rn <= 3 ORDER BY category_id, rn
"""


def core_b() -> sa.Select:
    sub = sa.select(c.c.id).where(c.c.slug == "books").cte("sub", recursive=True)
    ch = c.alias("ch")
    sub = sub.union_all(sa.select(ch.c.id).join(sub, ch.c.parent_id == sub.c.id))
    units = sa.func.sum(oi.c.quantity)
    sold = (
        sa.select(
            p.c.category_id,
            p.c.id,
            p.c.title,
            units.label("units"),
            sa.func.row_number()
            .over(partition_by=p.c.category_id, order_by=(units.desc(), p.c.id))
            .label("rn"),
        )
        .select_from(oi)
        .join(o, o.c.id == oi.c.order_id)
        .join(p, p.c.id == oi.c.product_id)
        .where(
            o.c.status.in_(SALE),
            o.c.created_at >= ts(2025, 1),
            o.c.created_at < ts(2026, 1),
            p.c.category_id.in_(sa.select(sub.c.id)),
        )
        .group_by(p.c.category_id, p.c.id, p.c.title)
        .cte("sold")
    )
    return (
        sa.select(sold.c.category_id, sold.c.id, sold.c.title, sold.c.units)
        .where(sold.c.rn <= 3)
        .order_by(sold.c.category_id, sold.c.rn)
    )


# --- (c) buyers with orders from >= 5 shops in 2025 who never wrote a review -------------------
SQL_C = """
    SELECT o.user_id, count(DISTINCT o.seller_id) AS shops, count(*) AS orders
    FROM orders o
    WHERE o.created_at >= '2025-01-01 00:00+00' AND o.created_at < '2026-01-01 00:00+00'
      AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.user_id = o.user_id)
    GROUP BY o.user_id
    HAVING count(DISTINCT o.seller_id) >= 5
    ORDER BY orders DESC, o.user_id
"""


def core_c() -> sa.Select:
    shops = sa.func.count(sa.distinct(o.c.seller_id))
    n = sa.func.count()
    return (
        sa.select(o.c.user_id, shops.label("shops"), n.label("orders"))
        .where(
            o.c.created_at >= ts(2025, 1),
            o.c.created_at < ts(2026, 1),
            ~sa.exists().where(reviews.c.user_id == o.c.user_id),
        )
        .group_by(o.c.user_id)
        .having(shops >= 5)
        .order_by(n.desc(), o.c.user_id)
    )


# --- (d) sellers whose revenue fell by more than half from November to December 2025 ----------
SQL_D = """
    SELECT s.id, s.shop_name, nov, dec
    FROM (SELECT seller_id,
                 sum(total) FILTER (WHERE created_at <  '2025-12-01 00:00+00') AS nov,
                 coalesce(sum(total) FILTER (WHERE created_at >= '2025-12-01 00:00+00'), 0) AS dec
          FROM orders
          WHERE status IN ('paid', 'shipped', 'delivered')
            AND created_at >= '2025-11-01 00:00+00' AND created_at < '2026-01-01 00:00+00'
          GROUP BY seller_id) r
    JOIN sellers s ON s.id = r.seller_id
    WHERE nov >= 1000 AND dec < nov / 2
    ORDER BY nov - dec DESC, s.id
"""


def core_d() -> sa.Select:
    dec_start = ts(2025, 12)
    r = (
        sa.select(
            o.c.seller_id,
            sa.func.sum(o.c.total).filter(o.c.created_at < dec_start).label("nov"),
            sa.func.coalesce(
                sa.func.sum(o.c.total).filter(o.c.created_at >= dec_start), 0
            ).label("dec"),
        )
        .where(o.c.status.in_(SALE), o.c.created_at >= ts(2025, 11), o.c.created_at < ts(2026, 1))
        .group_by(o.c.seller_id)
        .subquery("r")
    )
    return (
        sa.select(sellers.c.id, sellers.c.shop_name, r.c.nov, r.c.dec)
        .join(r, r.c.seller_id == sellers.c.id)
        .where(r.c.nov >= 1000, r.c.dec < r.c.nov / 2)
        .order_by((r.c.nov - r.c.dec).desc(), sellers.c.id)
    )


if __name__ == "__main__":
    engine = create_engine()
    with engine.connect() as conn:
        for name, raw, core in (
            ("a", SQL_A, core_a),
            ("b", SQL_B, core_b),
            ("c", SQL_C, core_c),
            ("d", SQL_D, core_d),
        ):
            expected = [tuple(r) for r in conn.exec_driver_sql(raw)]
            got = [tuple(r) for r in conn.execute(core())]
            assert got == expected, (name, got[:3], expected[:3])
            print(f"({name}) {len(got):>4} rows identical; first: {got[0]}")
