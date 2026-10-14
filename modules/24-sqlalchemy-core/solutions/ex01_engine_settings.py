"""Exercise 1: an engine that says who it is, gives up on slow queries, and reads a snapshot."""

import os
import time

import sqlalchemy as sa

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")

engine = sa.create_engine(
    sa.make_url(DSN).set(drivername="postgresql+psycopg"),
    pool_size=2,
    max_overflow=0,
    pool_pre_ping=True,
    connect_args={
        "application_name": "m24-ex1",
        "options": "-c statement_timeout=2000 -c lock_timeout=500",
    },
)

with engine.connect() as conn:
    shown = {
        k: conn.execute(sa.text(f"SHOW {k}")).scalar_one()
        for k in ("application_name", "statement_timeout", "lock_timeout", "transaction_isolation")
    }
    print("default connection:", shown)

# Isolation per connection (or per engine: create_engine(isolation_level=...))
with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
    print("snapshot connection:", conn.execute(sa.text("SHOW transaction_isolation")).scalar_one())
    before = conn.execute(sa.text("SELECT count(*) FROM jobs")).scalar_one()
    with engine.begin() as other:  # another transaction commits a new row meanwhile
        other.execute(sa.text("INSERT INTO jobs (queue, kind) VALUES ('m24-ex1', 'x')"))
    after = conn.execute(sa.text("SELECT count(*) FROM jobs")).scalar_one()
    print(f"same snapshot: {before} == {after}: {before == after}")

with engine.connect() as conn:  # the pool reset the isolation level on return
    print("after return:", conn.execute(sa.text("SHOW transaction_isolation")).scalar_one())
    t = time.perf_counter()
    try:
        conn.execute(sa.text("SELECT pg_sleep(3)"))
    except sa.exc.OperationalError as e:
        print(f"timeout after {time.perf_counter() - t:.1f} s: {type(e.orig).__name__}")
    conn.rollback()
    conn.execute(sa.text("DELETE FROM jobs WHERE queue = 'm24-ex1'"))
    conn.commit()

print(engine.pool.status())
