"""Exercise 6: add-to-cart as an upsert, and merging a guest cart in one statement."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from bazaar.sa.engine import create_engine
from bazaar.sa.tables import cart_items

ci = cart_items
INSERTED = sa.literal_column("xmax = 0", sa.Boolean).label("inserted")


def add_to_cart(conn: sa.Connection, user_id: int, product_id: int, qty: int = 1):
    """Add qty units; returns (new quantity, True if the line is new)."""
    ins = pg.insert(ci).values(user_id=user_id, product_id=product_id, quantity=qty)
    stmt = ins.on_conflict_do_update(
        index_elements=[ci.c.user_id, ci.c.product_id],
        set_={"quantity": ci.c.quantity + ins.excluded.quantity, "added_at": sa.func.now()},
    ).returning(ci.c.quantity, INSERTED)
    return tuple(conn.execute(stmt).one())


def merge_carts(conn: sa.Connection, from_user: int, to_user: int) -> dict[str, int]:
    """Move every line of from_user's cart into to_user's, adding quantities on overlap."""
    src = sa.select(sa.literal(to_user, sa.BigInteger), ci.c.product_id, ci.c.quantity).where(
        ci.c.user_id == from_user
    )
    ins = pg.insert(ci).from_select(["user_id", "product_id", "quantity"], src)
    stmt = ins.on_conflict_do_update(
        index_elements=[ci.c.user_id, ci.c.product_id],
        set_={"quantity": ci.c.quantity + ins.excluded.quantity},
    ).returning(ci.c.product_id, INSERTED)
    with conn.begin_nested():  # both statements or neither
        flags = [r.inserted for r in conn.execute(stmt)]
        conn.execute(sa.delete(ci).where(ci.c.user_id == from_user))
    return {"inserted": sum(flags), "updated": len(flags) - sum(flags)}


if __name__ == "__main__":
    engine = create_engine()
    with engine.connect() as conn:
        print("add 16856 to user 1:", add_to_cart(conn, 1, 16856, 2))  # already in the cart
        print("add 20 to user 1:   ", add_to_cart(conn, 1, 20))  # new line
        # a "guest" cart (user 7 has none): one overlapping product, two new ones
        for pid, q in ((18257, 1), (21089, 3), (5179, 1)):
            add_to_cart(conn, 7, pid, q)
        print("merge 7 -> 1:", merge_carts(conn, 7, 1))
        lines = conn.execute(
            sa.select(ci.c.product_id, ci.c.quantity)
            .where(ci.c.user_id == 1)
            .order_by(ci.c.product_id)
        ).all()
        print("user 1 now:", [tuple(r) for r in lines])
        print("user 7 lines left:", conn.scalar(sa.select(sa.func.count()).where(ci.c.user_id == 7)))
        conn.rollback()
