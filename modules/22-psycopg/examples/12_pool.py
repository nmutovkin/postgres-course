"""Module 22, section 14: why pools, and how they behave."""

import logging
import os
import threading
import time

import psycopg
from psycopg_pool import ConnectionPool, PoolTimeout

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")

# 1. What does a connection cost?
N = 200
t = time.perf_counter()
for _ in range(N):
    with psycopg.connect(DSN) as conn:
        conn.execute("SELECT 1")
connect_each = (time.perf_counter() - t) / N
with ConnectionPool(DSN, min_size=1, max_size=1) as pool:
    t = time.perf_counter()
    for _ in range(N):
        with pool.connection() as conn:
            conn.execute("SELECT 1")
    pooled = (time.perf_counter() - t) / N
print(f"1. new connection per query: {connect_each * 1000:.2f} ms   pooled: {pooled * 1000:.2f} ms")

# 2. Waiting for a connection, and giving up
logging.basicConfig(level=logging.WARNING)
with ConnectionPool(DSN, min_size=2, max_size=2, timeout=1.0) as pool:

    def hold(seconds: float) -> None:
        with pool.connection() as conn:
            conn.execute("SELECT pg_sleep(%s)", (seconds,))

    threads = [threading.Thread(target=hold, args=(2,)) for _ in range(2)]
    for th in threads:
        th.start()
    time.sleep(0.2)
    t = time.perf_counter()
    try:
        with pool.connection() as conn:
            pass
    except PoolTimeout as e:
        print(f"2. after {time.perf_counter() - t:.2f}s: PoolTimeout: {e}")
    print("   stats:", {k: v for k, v in pool.get_stats().items()
                         if k in ("pool_size", "pool_available", "requests_num",
                                  "requests_errors")})  # fmt: skip
    for th in threads:
        th.join()

# 3. Session state leaks from one borrower to the next
with ConnectionPool(DSN, min_size=1, max_size=1) as pool:
    with pool.connection() as conn:  # request 1: seller 42's portal page
        conn.execute("SET app.seller_id = '42'")
    with pool.connection() as conn:  # request 2: someone else entirely
        print("3. leaked:  app.seller_id =",
              conn.execute("SELECT current_setting('app.seller_id', true)").fetchone()[0])  # fmt: skip

    with pool.connection() as conn:
        conn.execute("RESET app.seller_id")
        with conn.transaction():
            conn.execute("SELECT set_config('app.seller_id', '42', true)")  # = SET LOCAL
            inside = conn.execute("SELECT current_setting('app.seller_id', true)").fetchone()[0]
    with pool.connection() as conn:
        after = conn.execute("SELECT current_setting('app.seller_id', true)").fetchone()[0]
        print(f"   SET LOCAL: inside the transaction {inside!r}, next borrower {after!r}")




def naive_reset(conn: psycopg.Connection) -> None:
    conn.execute("RESET ALL")  # BUG: opens a transaction, the pool must be handed an idle conn


def reset(conn: psycopg.Connection) -> None:
    with conn.transaction():  # BEGIN; RESET ALL; COMMIT -> idle again
        conn.execute("RESET ALL")


for fn in (naive_reset, reset):
    with ConnectionPool(DSN, min_size=1, max_size=1, reset=fn) as pool:
        with pool.connection() as conn:
            pid = conn.info.backend_pid
            conn.execute("SET app.seller_id = '42'")
        with pool.connection() as conn:
            value = conn.execute("SELECT current_setting('app.seller_id', true)").fetchone()[0]
            print(f"   reset={fn.__name__}: next borrower sees {value!r},"
                  f" same backend: {conn.info.backend_pid == pid}")  # fmt: skip
