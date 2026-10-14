"""Module 23, section 4.8: asyncpg (and psycopg) behind PgBouncer in transaction mode.

Start PgBouncer first:  ../lab/run_pgbouncer.sh 0     (or 200)
Then:                   python 08_pgbouncer_prepared.py
20 clients share PgBouncer's 2 server connections, each running 50 queries.
"""

import asyncio
import os
from collections import Counter

import asyncpg
import psycopg

BOUNCER = os.environ.get("PGBOUNCER_DSN", "postgresql://postgres:postgres@localhost:6432/bazaar")
Q_APG = "SELECT quantity FROM inventory WHERE product_id = $1"
Q_PG = "SELECT quantity FROM inventory WHERE product_id = %s"


async def asyncpg_client(i: int, errors: Counter, **kw) -> None:
    conn = await asyncpg.connect(BOUNCER, **kw)
    try:
        for n in range(50):
            try:
                await conn.fetchval(Q_APG, i * 50 + n + 1)
            except asyncpg.PostgresError as e:
                errors[f"{type(e).__name__}: {str(e).splitlines()[0]}"] += 1
    finally:
        await conn.close()


async def psycopg_client(i: int, errors: Counter, **kw) -> None:
    conn = await psycopg.AsyncConnection.connect(BOUNCER, autocommit=True, **kw)
    try:
        for n in range(50):
            try:
                await conn.execute(Q_PG, (i * 50 + n + 1,))
            except psycopg.Error as e:
                errors[f"{type(e).__name__}: {str(e).splitlines()[0]}"] += 1
    finally:
        await conn.close()


async def scenario(label: str, client, **kw) -> None:
    errors: Counter = Counter()
    await asyncio.gather(*(client(i, errors, **kw) for i in range(20)))
    print(f"{label:46s} failed queries: {sum(errors.values()):4d} / 1000")
    for msg, n in errors.most_common(3):
        print(f"{'':6s}{n:4d} x {msg}")


async def main() -> None:
    await scenario("asyncpg, default statement cache", asyncpg_client)
    await scenario("asyncpg, statement_cache_size=0", asyncpg_client, statement_cache_size=0)
    await scenario("psycopg, prepare_threshold=5 (default)", psycopg_client)
    await scenario("psycopg, prepare_threshold=None", psycopg_client, prepare_threshold=None)


asyncio.run(main())
