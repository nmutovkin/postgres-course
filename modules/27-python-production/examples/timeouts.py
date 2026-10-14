"""Every timeout layer, triggered on purpose (module 27, section 4).

    cd project/python && python ../../modules/27-python-production/examples/timeouts.py

For each one: how long it took to fire, the exception, its SQLSTATE, and how
bazaar.resilience classifies it. Uses only throwaway sessions; changes nothing.
"""

from __future__ import annotations

import asyncio
import time

import psycopg
import psycopg_pool

from bazaar.config import get_settings
from bazaar.resilience import classify

DSN = get_settings().dsn


def report(name: str, fn) -> None:  # noqa: ANN001
    t0 = time.perf_counter()
    try:
        fn()
        outcome = "no error"
    except Exception as e:
        first = str(e).strip().splitlines()[0] if str(e).strip() else ""
        outcome = (
            f"{type(e).__name__} sqlstate={getattr(e, 'sqlstate', None)}"
            f" kind={classify(e)}: {first[:70]}"
        )
    print(f"{name:<34} after {time.perf_counter() - t0:5.2f}s  {outcome}")


def statement_timeout() -> None:
    with psycopg.connect(DSN, options="-c statement_timeout=200") as c:
        c.execute("SELECT pg_sleep(5)")


def lock_timeout() -> None:
    holder = psycopg.connect(DSN)
    holder.execute("SELECT 1 FROM inventory WHERE product_id = 2 FOR UPDATE")
    try:
        with psycopg.connect(DSN, options="-c lock_timeout=300") as c:
            c.execute("UPDATE inventory SET quantity = quantity WHERE product_id = 2")
    finally:
        holder.rollback()
        holder.close()


def idle_in_transaction() -> None:
    with psycopg.connect(DSN, options="-c idle_in_transaction_session_timeout=300") as c:
        c.execute("SELECT 1")  # BEGIN ... and then the application "forgets" the transaction
        time.sleep(0.6)
        c.execute("SELECT 2")


def transaction_timeout() -> None:  # PostgreSQL 17
    with psycopg.connect(DSN, options="-c transaction_timeout=500") as c:
        for _ in range(10):  # many short statements: statement_timeout never fires
            c.execute("SELECT pg_sleep(0.1)")


def client_side_asyncio_timeout() -> None:
    async def main() -> None:
        async with await psycopg.AsyncConnection.connect(DSN) as c:
            try:
                async with asyncio.timeout(0.3):
                    await c.execute("SELECT pg_sleep(5)")
            finally:
                # Did the server stop working? psycopg sent a cancel request.
                await c.rollback()
                cur = await c.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE query = 'SELECT pg_sleep(5)'"
                    " AND state = 'active'"
                )
                print(
                    f"{'':<34}       (server still running the query: {(await cur.fetchone())[0]})"
                )

    asyncio.run(main())


def pool_timeout() -> None:
    with psycopg_pool.ConnectionPool(DSN, min_size=1, max_size=1, timeout=0.5) as pool:
        with pool.connection(), pool.connection():  # the second checkout finds nothing free
            pass


def connect_timeout() -> None:
    # 10.255.255.1 drops packets (nothing answers): without connect_timeout this
    # waits for the operating system's TCP timeout, often more than a minute.
    psycopg.connect("postgresql://u:p@10.255.255.1:5432/x", connect_timeout=2)


if __name__ == "__main__":
    report("statement_timeout = 200ms", statement_timeout)
    report("lock_timeout = 300ms", lock_timeout)
    report("idle_in_transaction = 300ms", idle_in_transaction)
    report("transaction_timeout = 500ms (PG17)", transaction_timeout)
    report("asyncio.timeout(0.3)", client_side_asyncio_timeout)
    report("pool timeout = 0.5s", pool_timeout)
    report("connect_timeout = 2s", connect_timeout)
