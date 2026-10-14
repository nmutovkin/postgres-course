"""Module 24, section 6 (continued): CTEs, windows, LATERAL, set operations, CASE, casts."""

import sys
from datetime import UTC, datetime

import sqlalchemy as sa

from bazaar.sa.engine import create_engine
from bazaar.sa.tables import categories, order_items, orders, products, reviews

engine = create_engine()
WANTED = set(sys.argv[1:])


def show(label: str, stmt, rows: int = 5) -> None:
    if WANTED and label.split()[0].rstrip(".") not in WANTED:
        return
    print(f"--- {label}")
    print(stmt.compile(engine))
    with engine.connect() as conn:
        for r in conn.execute(stmt).fetchmany(rows):
            print("   ", tuple(r))
    print()


p, o, c = products, orders, categories

# G. a recursive CTE: Electronics and everything below it
tree = (
    sa.select(c.c.id, c.c.name, sa.literal(0).label("depth"))
    .where(c.c.slug == "electronics")
    .cte("tree", recursive=True)
)
child = c.alias("child")
tree = tree.union_all(
    sa.select(child.c.id, child.c.name, tree.c.depth + 1).join(
        child, child.c.parent_id == tree.c.id
    )
)
show(
    "G. recursive CTE",
    sa.select(tree.c.depth, sa.func.count().label("categories"))
    .group_by(tree.c.depth)
    .order_by(tree.c.depth),
)

# G2. the CTE reused: active products anywhere under Electronics
show(
    "G2. using the CTE",
    sa.select(sa.func.count())
    .select_from(p)
    .join(tree, tree.c.id == p.c.category_id)
    .where(p.c.status == "active"),
)

# H. window functions
rank = (
    sa.func.row_number()
    .over(partition_by=p.c.category_id, order_by=(p.c.price.desc(), p.c.id))
    .label("rank")
)
ranked = (
    sa.select(p.c.category_id, p.c.id, p.c.title, p.c.price, rank)
    .where(p.c.status == "active", p.c.category_id.in_([3, 4]))
    .subquery()
)
show(
    "H. row_number() OVER (PARTITION BY ...), top 2 per category",
    sa.select(ranked).where(ranked.c.rank <= 2).order_by(ranked.c.category_id, ranked.c.rank),
)

day = sa.func.date_trunc("day", o.c.created_at).label("day")
daily = (
    sa.select(day, sa.func.sum(o.c.total).label("revenue"))
    .where(
        o.c.status.in_(["paid", "shipped", "delivered"]),
        o.c.created_at >= datetime(2025, 12, 1, tzinfo=UTC),
    )
    .group_by(day)
    .subquery()
)
show(
    "H2. running total with a frame",
    sa.select(
        daily.c.day,
        daily.c.revenue,
        sa.func.sum(daily.c.revenue).over(order_by=daily.c.day, rows=(None, 0)).label("to_date"),
        sa.func.avg(daily.c.revenue)
        .over(order_by=daily.c.day, rows=(-6, 0))
        .cast(sa.Numeric(12, 2))
        .label("avg_7d"),
    ).order_by(daily.c.day),
    rows=3,
)

# I. LATERAL: the 2 most recent orders of each of three users
recent = (
    sa.select(o.c.id, o.c.created_at, o.c.total)
    .where(o.c.user_id == sa.literal_column("u.id"))
    .order_by(o.c.created_at.desc())
    .limit(2)
    .lateral("recent")
)
u = sa.select(sa.func.unnest(sa.literal([8, 13, 11])).label("id")).subquery("u")
show(
    "I. LATERAL",
    sa.select(u.c.id.label("user_id"), recent.c.id, recent.c.total)
    .select_from(u)
    .join(recent, sa.true())
    .order_by(u.c.id, recent.c.created_at.desc()),
    rows=6,
)

# J. set operations, CASE, casts, literal_column
expensive = sa.select(p.c.id, sa.literal("expensive").label("why")).where(p.c.price > 2980)
popular = (
    sa.select(reviews.c.product_id, sa.literal("popular"))
    .group_by(reviews.c.product_id)
    .having(sa.func.count() > 120)
)
show("J. UNION ALL", sa.union_all(expensive, popular).order_by("why", "id"), rows=4)

band = sa.case(
    (p.c.price < 20, "budget"),
    (p.c.price < 200, "mid"),
    else_="premium",
).label("band")
show(
    "J2. CASE + cast + literal_column",
    sa.select(
        band,
        sa.func.count().label("products"),
        sa.cast(sa.func.avg(p.c.price), sa.Numeric(10, 2)).label("avg_price"),
        sa.literal_column("round(100.0 * count(*) / sum(count(*)) over (), 1)").label("pct"),
    )
    .where(p.c.status == "active")
    .group_by(band)
    .order_by(sa.func.min(p.c.price)),
)

# K. a dynamic query: optional filters, then keyset pagination with a row comparison
def product_page(category_id=None, max_price=None, after=None, limit=3):
    stmt = sa.select(p.c.id, p.c.title, p.c.price, p.c.created_at).where(p.c.status == "active")
    if category_id is not None:
        stmt = stmt.where(p.c.category_id == category_id)
    if max_price is not None:
        stmt = stmt.where(p.c.price <= max_price)
    if after is not None:
        stmt = stmt.where(sa.tuple_(p.c.price, p.c.id) > sa.tuple_(*after))
    return stmt.order_by(p.c.price, p.c.id).limit(limit)


page1 = product_page(category_id=6, max_price=100)
show("K. keyset page 1", page1)
with engine.connect() as conn:
    last = conn.execute(page1).all()[-1]
show("K2. keyset page 2", product_page(category_id=6, max_price=100, after=(last.price, last.id)))

items_per_order = (
    sa.select(order_items.c.order_id, sa.func.count().label("n"))
    .group_by(order_items.c.order_id)
    .cte("items_per_order")
)
show(
    "L. non-recursive CTE",
    sa.select(items_per_order.c.n, sa.func.count().label("orders"))
    .group_by(items_per_order.c.n)
    .order_by(items_per_order.c.n),
    rows=6,
)
