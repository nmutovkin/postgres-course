"""Module 24, section 6: the SQL expression language, one construct at a time.

Each block prints the SQL that SQLAlchemy generates for PostgreSQL (with the
psycopg dialect's %(name)s placeholders) and the first rows of the result.
Run: python 04_select.py [block-letter ...]
"""

import sys
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa

from bazaar.sa.engine import create_engine
from bazaar.sa.tables import categories, orders, products, reviews, sellers, users

engine = create_engine()
WANTED = set(sys.argv[1:])


def show(label: str, stmt, rows: int = 5) -> None:
    if WANTED and label.split()[0].rstrip(".") not in WANTED:
        return
    print(f"--- {label}")
    print(stmt.compile(engine))
    with engine.connect() as conn:
        result = conn.execute(stmt)
        for r in result.fetchmany(rows):
            print("   ", tuple(r))
    print()


p, o, c = products, orders, categories

show(
    "A. select / where / order_by / limit",
    sa.select(p.c.id, p.c.title, p.c.price)
    .where(p.c.category_id == 3, p.c.status == "active", p.c.price < 500)
    .order_by(p.c.price.desc(), p.c.id)
    .limit(3),
)

show(
    "B. operators",
    sa.select(p.c.id, p.c.title)
    .where(
        p.c.seller_id.in_([7, 8, 9]),
        p.c.price.between(Decimal("10"), Decimal("20")),
        p.c.title.ilike("%lamp%") | p.c.title.ilike("%light%"),
        p.c.description.is_not(None),
        sa.not_(p.c.tags.any_() == "sale"),  # NOT (... = ANY(tags)), section 8
    )
    .order_by(p.c.id)
    .limit(3),
)

show(
    "C. join with explicit ON clauses",
    sa.select(o.c.id, users.c.full_name, sellers.c.shop_name, o.c.total)
    .join(users, users.c.id == o.c.user_id)
    .join(sellers, sellers.c.id == o.c.seller_id)
    .where(o.c.created_at >= datetime(2025, 12, 31, tzinfo=UTC))
    .order_by(o.c.total.desc())
    .limit(3),
)

show(
    "C1. join with an inferred ON clause: a trap",
    sa.select(o.c.id, users.c.full_name, sellers.c.shop_name, o.c.total)
    .join(users, users.c.id == o.c.user_id)
    .join(sellers)  # inferred from the most recent FROM that has a FK to sellers... users!
    .where(o.c.created_at >= datetime(2025, 12, 31, tzinfo=UTC))
    .order_by(o.c.total.desc())
    .limit(3),
)

never = (
    sa.select(sa.func.count())
    .select_from(users.outerjoin(o, o.c.user_id == users.c.id))
    .where(o.c.id.is_(None))
)
show("C2. users who never ordered (anti-join with LEFT JOIN)", never)

revenue = sa.func.sum(o.c.total).label("revenue")
show(
    "D. group_by / having / aggregate FILTER",
    sa.select(
        o.c.seller_id,
        sa.func.count().label("orders"),
        sa.func.count().filter(o.c.status == "refunded").label("refunded"),
        revenue,
    )
    .where(
        o.c.status.in_(["paid", "shipped", "delivered", "refunded"]),
        o.c.created_at >= datetime(2025, 1, 1, tzinfo=UTC),
    )
    .group_by(o.c.seller_id)
    .having(sa.func.sum(o.c.total) > 1_000_000)
    .order_by(revenue.desc())
    .limit(3),
)

parent = c.alias("parent")
show(
    "E. alias (self-join)",
    sa.select(c.c.name, parent.c.name.label("parent"))
    .join(parent, c.c.parent_id == parent.c.id)
    .where(parent.c.parent_id.is_(None))
    .order_by(parent.c.name, c.c.name)
    .limit(4),
)

review_count = (
    sa.select(sa.func.count())
    .where(reviews.c.product_id == p.c.id)  # refers to the outer query: correlated
    .scalar_subquery()
    .label("reviews")
)
show(
    "F. correlated scalar subquery",
    sa.select(p.c.id, p.c.title, review_count).where(p.c.id.in_([1, 2, 3])).order_by(p.c.id),
)

has_orders = sa.exists().where(o.c.user_id == users.c.id)
show("F2. NOT EXISTS", sa.select(sa.func.count()).select_from(users).where(~has_orders))

big = (
    sa.select(o.c.seller_id, sa.func.max(o.c.total).label("biggest"))
    .group_by(o.c.seller_id)
    .subquery("big")
)
show(
    "F3. subquery in FROM",
    sa.select(sellers.c.shop_name, big.c.biggest)
    .join(big, big.c.seller_id == sellers.c.id)
    .order_by(big.c.biggest.desc())
    .limit(3),
)
