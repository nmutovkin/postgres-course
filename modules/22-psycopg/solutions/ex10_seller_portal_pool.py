"""Exercise 10: a pooled seller portal under row-level security, without leaks."""

import os

from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import ConnectionPool

from bazaar.db import seller_scope

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
# Same server and database, but the portal's least-privilege role from module 15.
SELLER_DSN = make_conninfo(
    **{**conninfo_to_dict(DSN), "user": "bazaar_seller", "password": "seller"}
)
COUNTS = """
    SELECT (SELECT count(*) FROM products) AS products,
           (SELECT count(*) FROM orders)   AS orders,
           (SELECT count(DISTINCT seller_id) FROM orders) AS sellers_seen
"""


def dashboard(pool: ConnectionPool, seller_id: int) -> tuple[int, int, int]:
    with pool.connection() as conn, seller_scope(conn, seller_id):
        return conn.execute(COUNTS).fetchone()


def leaky_dashboard(pool: ConnectionPool, seller_id: int) -> tuple[int, int, int]:
    with pool.connection() as conn:
        conn.execute("SELECT set_config('app.seller_id', %s, false)", (str(seller_id),))  # BUG
        return conn.execute(COUNTS).fetchone()


def anonymous(pool: ConnectionPool) -> tuple[int, int, int]:
    with pool.connection() as conn:
        return conn.execute(COUNTS).fetchone()


if __name__ == "__main__":
    with ConnectionPool(SELLER_DSN, min_size=1, max_size=1, open=True) as pool:
        for sid in (1, 2, 1):
            print(f"seller {sid}: (products, orders, sellers seen) = {dashboard(pool, sid)}")
        print("no seller set:", anonymous(pool))
        print("leaky seller 2:", leaky_dashboard(pool, 2))
        print("no seller set after the leaky call:", anonymous(pool))
        print("pool stats:", {k: v for k, v in pool.get_stats().items()
                              if k in ("connections_num", "requests_num")})  # fmt: skip
