"""The bazaar package behind PgBouncer in transaction mode (module 27, section 5.3).

Start the lab first (2 server connections, transaction pooling):

    cd modules/27-python-production/lab && ./run_pgbouncer.sh          # strict
    cd project/python && python ../../modules/27-python-production/examples/pgbouncer_gotchas.py
    cd modules/27-python-production/lab && ./run_pgbouncer.sh ignore   # then run it again

Set PGBOUNCER_PORT if the lab does not listen on 6432.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace

import psycopg
import sqlalchemy as sa

from bazaar import db
from bazaar.config import get_settings
from bazaar.sa.engine import NullPool, create_async_engine, create_engine

PORT = os.environ.get("PGBOUNCER_PORT", "6432")
DIRECT = get_settings().dsn
BOUNCER = f"postgresql://bazaar_app:app@localhost:{PORT}/bazaar"


def step(title: str) -> None:
    print(f"\n--- {title}")


def timeouts_through_bouncer() -> None:
    step("1. our timeouts travel as a startup parameter: options='-c statement_timeout=5000 ...'")
    settings = replace(get_settings(), dsn=BOUNCER)
    try:
        with db.connect(settings) as c:
            print("connected; SHOW statement_timeout =", c.execute("SHOW statement_timeout").fetchone()[0])
            print("          SHOW idle_in_transaction_session_timeout =",
                  c.execute("SHOW idle_in_transaction_session_timeout").fetchone()[0])
    except psycopg.OperationalError as e:
        print("refused:", str(e).strip().splitlines()[-1])


def role_defaults() -> None:
    step("2. the fix: defaults on the role, applied when PgBouncer opens a server connection")
    with psycopg.connect(DIRECT, autocommit=True) as admin:
        admin.execute("ALTER ROLE bazaar_app SET statement_timeout = '5s'")
        admin.execute("ALTER ROLE bazaar_app SET idle_in_transaction_session_timeout = '30s'")
        # PgBouncer keeps its server connections: make it open new ones.
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = 'bazaar_app'"
        )
    settings = replace(get_settings(), dsn=BOUNCER, statement_timeout_ms=0,
                       idle_in_transaction_timeout_ms=0)  # no startup options at all
    try:
        with db.connect(settings) as c:
            print("SHOW statement_timeout =", c.execute("SHOW statement_timeout").fetchone()[0])
    finally:
        with psycopg.connect(DIRECT, autocommit=True) as admin:
            admin.execute("ALTER ROLE bazaar_app RESET statement_timeout")
            admin.execute("ALTER ROLE bazaar_app RESET idle_in_transaction_session_timeout")


def session_state_leaks() -> None:
    step("3. session state: SET vs SET LOCAL, through 2 shared server connections")
    s = replace(get_settings(), dsn=BOUNCER, statement_timeout_ms=0,
                idle_in_transaction_timeout_ms=0)
    engine = create_engine(s, poolclass=NullPool)  # PgBouncer is the pool
    try:
        with engine.begin() as c:  # "request 1": seller 42, session-level SET
            c.execute(sa.text("SET app.seller_id = '42'"))
        seen = []
        for _ in range(4):  # "requests 2-5", other users, fresh client connections
            with engine.begin() as c:
                seen.append(c.execute(sa.text("SELECT current_setting('app.seller_id', true)"))
                            .scalar_one())
        print("SET:       later requests see app.seller_id =", seen)
        with engine.begin() as c:
            c.execute(sa.text("RESET app.seller_id"))
            c.execute(sa.text("SELECT set_config('app.seller_id', '42', true)"))  # SET LOCAL
        seen = []
        for _ in range(4):
            with engine.begin() as c:
                seen.append(c.execute(sa.text("SELECT current_setting('app.seller_id', true)"))
                            .scalar_one())
        print("SET LOCAL: later requests see app.seller_id =", seen)
    finally:
        with engine.begin() as c:  # clean up both server connections
            c.execute(sa.text("RESET ALL"))
        with engine.begin() as c:
            c.execute(sa.text("RESET ALL"))
        engine.dispose()


async def asyncpg_prepared() -> None:
    step("4. SQLAlchemy + asyncpg (prepares every statement) with max_prepared_statements=200")
    s = replace(get_settings(), dsn=BOUNCER, statement_timeout_ms=0,
                idle_in_transaction_timeout_ms=0)
    engine = create_async_engine(s, poolclass=NullPool)
    errors = 0

    async def worker() -> None:
        nonlocal errors
        for i in range(20):
            try:
                async with engine.connect() as c:
                    await c.execute(sa.text("SELECT title FROM products WHERE id = :id"),
                                    {"id": i + 1})
            except Exception:
                errors += 1

    try:
        await asyncio.gather(*(worker() for _ in range(10)))
    finally:
        await engine.dispose()
    print(f"200 queries from 10 clients over 2 server connections: {errors} errors")


if __name__ == "__main__":
    timeouts_through_bouncer()
    role_defaults()
    session_state_leaks()
    asyncio.run(asyncpg_prepared())
