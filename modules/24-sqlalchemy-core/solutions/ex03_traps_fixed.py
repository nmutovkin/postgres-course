"""Exercise 3 (solution): the five queries, fixed."""

from decimal import Decimal

import sqlalchemy as sa

from bazaar.sa.engine import create_engine
from bazaar.sa.tables import products, sellers

p = products


def titles_with_percent_sign():
    """Products whose title contains the literal text '50%'."""
    # contains() builds LIKE '%' || :x || '%' and does NOT escape wildcards in :x,
    # so '50%' meant "50 followed by anything". autoescape=True escapes % and _.
    return sa.select(sa.func.count()).where(p.c.title.contains("50%", autoescape=True))


def tagged_gift():
    """Products tagged 'gift' (among other tags)."""
    # == compares whole arrays: only tags = '{gift}' exactly. @> is "contains".
    return sa.select(sa.func.count()).where(p.c.tags.contains(["gift"]))


def seller7_catalog_value_plus_10pct():
    """Sum of seller 7's prices plus 10%, exact to the cent fraction (no rounding)."""
    # 1.1 is a Python float: bound as double precision, so the product is a
    # double, and the result is then coerced back to the column's Numeric(12,2)
    # (silently rounded to cents). Decimal keeps everything in numeric.
    return sa.select(sa.func.sum(p.c.price * Decimal("1.1"))).where(p.c.seller_id == 7)


def first_two_products_with_shop():
    """Products 1 and 2 with the name of the shop that sells them: 2 rows."""
    # Two FROM elements without a join condition = a cartesian product
    # (SQLAlchemy warns: "SELECT statement has a cartesian product").
    return (
        sa.select(p.c.title, sellers.c.shop_name)
        .join(sellers, sellers.c.id == p.c.seller_id)
        .where(p.c.id < 3)
    )


def expensive_active():
    """Active products over 100: count."""
    # Python's `and` asks the first expression for its truth value, which a SQL
    # expression refuses to give. Use & (with parentheses), sa.and_(), or
    # simply several arguments to where(), which are ANDed.
    return sa.select(sa.func.count()).where(p.c.price > 100, p.c.status == "active")


if __name__ == "__main__":
    engine = create_engine()
    with engine.connect() as conn:
        for fn in (
            titles_with_percent_sign,
            tagged_gift,
            seller7_catalog_value_plus_10pct,
            first_two_products_with_shop,
            expensive_active,
        ):
            rows = conn.execute(fn()).all()
            shown = rows[0][0] if len(rows) == 1 else f"{len(rows)} rows"
            print(f"{fn.__name__:<36} {shown}")
