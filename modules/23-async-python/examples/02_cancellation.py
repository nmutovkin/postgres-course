"""Module 23, section 3.4: what happens to a running query when its task is cancelled."""

import asyncio
import os
import time

import asyncpg
import psycopg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
WATCH = "SELECT state, left(query, 30) FROM pg_stat_activity WHERE pid = $1"


async def show(admin: asyncpg.Connection, label: str, pid: int) -> None:
    row = await admin.fetchrow(WATCH, pid)
    print(f"   {label:28s} server says: {tuple(row) if row else 'no such backend'}")


async def with_psycopg(admin: asyncpg.Connection) -> None:
    print("psycopg AsyncConnection (autocommit):")
    conn = await psycopg.AsyncConnection.connect(DSN, autocommit=True)
    pid = conn.info.backend_pid
    t = time.perf_counter()
    try:
        async with asyncio.timeout(0.5):
            await conn.execute("SELECT pg_sleep(10)")
    except TimeoutError:
        print(f"   TimeoutError after {time.perf_counter() - t:.2f}s")
    await show(admin, "right after the timeout:", pid)
    print("   connection status:", conn.info.transaction_status.name, "| closed:", conn.closed)
    cur = await conn.execute("SELECT 'still usable'")
    print("  ", (await cur.fetchone())[0])
    await conn.close()


async def with_asyncpg(admin: asyncpg.Connection) -> None:
    print("asyncpg:")
    conn = await asyncpg.connect(DSN)
    pid = conn.get_server_pid()
    t = time.perf_counter()
    try:
        async with asyncio.timeout(0.5):
            await conn.execute("SELECT pg_sleep(10)")
    except TimeoutError:
        print(f"   TimeoutError after {time.perf_counter() - t:.2f}s")
    await show(admin, "right after the timeout:", pid)
    await asyncio.sleep(0.05)
    await show(admin, "50 ms later:", pid)
    print("   is_closed:", conn.is_closed())
    t = time.perf_counter()
    print("  ", await conn.fetchval("SELECT 'still usable'"), f"({(time.perf_counter() - t) * 1000:.1f} ms)")
    await conn.close()


async def server_side_timeout(admin: asyncpg.Connection) -> None:
    print("statement_timeout instead (asyncpg, inside a transaction):")
    conn = await asyncpg.connect(DSN)
    t = time.perf_counter()
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL statement_timeout = '500ms'")
            await conn.execute("SELECT pg_sleep(10)")
    except asyncpg.QueryCanceledError as e:
        print(f"   {type(e).__name__} after {time.perf_counter() - t:.2f}s: {e}")
    print("  ", await conn.fetchval("SELECT 'still usable'"))
    await conn.close()


async def main() -> None:
    admin = await asyncpg.connect(DSN)
    await with_psycopg(admin)
    await with_asyncpg(admin)
    await server_side_timeout(admin)
    await admin.close()


asyncio.run(main())
