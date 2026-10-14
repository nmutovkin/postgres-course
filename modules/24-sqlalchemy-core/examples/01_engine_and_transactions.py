"""Module 24, sections 2-3: engines, the pool, and the transaction patterns of 2.0.

Prints the SQL the engine sends (the same log `echo=True` writes), so you can
see exactly where BEGIN, COMMIT, ROLLBACK and SAVEPOINT happen.
"""

import logging
import os

import sqlalchemy as sa

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
URL = sa.make_url(DSN).set(drivername="postgresql+psycopg")

log = logging.getLogger("sqlalchemy.engine.Engine")
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("    SQL> %(message)s"))
log.addHandler(handler)

engine = sa.create_engine(URL, pool_size=2, max_overflow=0, connect_args={"application_name": "m24"})
print("engine:", engine)  # the password is masked
print("pool:", engine.pool.status())

log.setLevel(logging.INFO)  # from here on, like echo=True

print("\n-- A. engine.connect(): 'commit as you go'")
with engine.connect() as conn:
    n = conn.execute(sa.text("SELECT count(*) FROM jobs WHERE queue = :q"), {"q": "m24"}).scalar()
    conn.execute(sa.text("INSERT INTO jobs (queue, kind) VALUES ('m24', 'demo')"))
    conn.commit()
    conn.execute(sa.text("INSERT INTO jobs (queue, kind) VALUES ('m24', 'not kept')"))
    # leaving the block without commit(): the second INSERT is rolled back

print("\n-- B. engine.begin(): one transaction, committed at the end of the block")
with engine.begin() as conn:
    conn.execute(sa.text("UPDATE jobs SET kind = 'demo2' WHERE queue = 'm24'"))

print("\n-- C. begin_nested(): a SAVEPOINT inside a transaction")
with engine.begin() as conn:
    conn.execute(sa.text("INSERT INTO jobs (queue, kind) VALUES ('m24', 'outer')"))
    try:
        with conn.begin_nested():
            conn.execute(sa.text("INSERT INTO jobs (queue, kind, attempts) VALUES ('m24', 'bad', -1)"))
    except sa.exc.IntegrityError as e:
        print(f"    caught: {type(e.orig).__name__}")
    kinds = conn.execute(sa.text("SELECT kind FROM jobs WHERE queue = 'm24' ORDER BY id")).scalars()
    print("    kinds now:", list(kinds))

print("\n-- D. AUTOCOMMIT: no BEGIN at all")
with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
    conn.execute(sa.text("DELETE FROM jobs WHERE queue = 'm24'"))

log.setLevel(logging.WARNING)
print("\npool:", engine.pool.status())
engine.dispose()
