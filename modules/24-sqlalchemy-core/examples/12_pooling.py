"""Module 24, section 2.4-2.5: QueuePool behavior, pre-ping, and NullPool."""

import os
import threading
import time

import psycopg
import sqlalchemy as sa
from sqlalchemy.pool import NullPool

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
URL = sa.make_url(DSN).set(drivername="postgresql+psycopg")


def kill_backends(app: str) -> int:
    with psycopg.connect(DSN, autocommit=True) as c:
        return c.execute(
            "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity"
            " WHERE application_name = %s",
            (app,),
        ).fetchone()[0]


# 1. pool_size + max_overflow, and what happens when all are busy
engine = sa.create_engine(URL, pool_size=2, max_overflow=1, pool_timeout=1)


def hold(seconds: float) -> None:
    with engine.connect() as conn:
        conn.execute(sa.text("SELECT pg_sleep(:s)"), {"s": seconds})


threads = [threading.Thread(target=hold, args=(1.5,)) for _ in range(3)]
for th in threads:
    th.start()
time.sleep(0.3)
print("1.", engine.pool.status())
t = time.perf_counter()
try:
    with engine.connect() as conn:
        pass
except sa.exc.TimeoutError as e:
    print(f"   4th checkout gave up after {time.perf_counter() - t:.2f} s: {str(e)[:60]}...")
for th in threads:
    th.join()
print("   after:", engine.pool.status(), "(the overflow connection was closed on return)")
engine.dispose()

# 2. A server restart / failover / idle timeout kills pooled connections
for pre_ping in (False, True):
    app = f"m24-preping-{pre_ping}"
    engine = sa.create_engine(
        URL, pool_size=1, pool_pre_ping=pre_ping, connect_args={"application_name": app}
    )
    with engine.connect() as conn:
        conn.execute(sa.text("SELECT 1"))
    killed = kill_backends(app)
    try:
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        print(f"2. pre_ping={pre_ping}: killed {killed}, next query OK")
    except sa.exc.OperationalError as e:
        print(f"2. pre_ping={pre_ping}: killed {killed}, next query FAILED: {type(e.orig).__name__}")
        print(f"   connection_invalidated={e.connection_invalidated}; the pool was emptied:")
        with engine.connect() as conn:  # the pool replaced the dead connection
            conn.execute(sa.text("SELECT 1"))
        print("   ...and the query after that works again")
    engine.dispose()

# 3. NullPool: a real connect and close per checkout (for PgBouncer or short scripts)
engine = sa.create_engine(URL, poolclass=NullPool)
t = time.perf_counter()
for _ in range(50):
    with engine.connect() as conn:
        conn.execute(sa.text("SELECT 1"))
null_ms = (time.perf_counter() - t) / 50 * 1000
engine.dispose()
engine = sa.create_engine(URL, pool_size=1)
t = time.perf_counter()
for _ in range(50):
    with engine.connect() as conn:
        conn.execute(sa.text("SELECT 1"))
queue_ms = (time.perf_counter() - t) / 50 * 1000
engine.dispose()
print(f"3. per checkout+query: NullPool {null_ms:.2f} ms, QueuePool {queue_ms:.2f} ms")
