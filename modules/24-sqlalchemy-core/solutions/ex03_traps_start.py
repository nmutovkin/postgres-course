"""Exercise 3 (starting point): five queries that don't do what their docstring says.

Run it, read the numbers and warnings, then fix each function without
changing its docstring. The fixed version is ex03_traps_fixed.py.
"""

import warnings

import sqlalchemy as sa

from bazaar.sa.engine import create_engine
from bazaar.sa.tables import products, sellers

p = products
warnings.simplefilter("always")  # show SAWarnings every time


def titles_with_percent_sign():
    """Products whose title contains the literal text '50%'."""
    return sa.select(sa.func.count()).where(p.c.title.contains("50%"))


def tagged_gift():
    """Products tagged 'gift' (among other tags)."""
    return sa.select(sa.func.count()).where(p.c.tags == ["gift"])


def seller7_catalog_value_plus_10pct():
    """Sum of seller 7's prices plus 10%, exact to the cent fraction (no rounding)."""
    return sa.select(sa.func.sum(p.c.price * 1.1)).where(p.c.seller_id == 7)


def first_two_products_with_shop():
    """Products 1 and 2 with the name of the shop that sells them: 2 rows."""
    return sa.select(p.c.title, sellers.c.shop_name).where(p.c.id < 3)


def expensive_active():
    """Active products over 100: count."""
    try:
        return sa.select(sa.func.count()).where(p.c.price > 100 and p.c.status == "active")
    except TypeError as e:
        print("   (raised", repr(e), ")")
        return sa.select(sa.literal(None))


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
