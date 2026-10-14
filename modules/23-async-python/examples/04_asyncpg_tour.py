"""Module 23, section 4: a tour of the asyncpg API on Bazaar."""

import asyncio
import json
import os
from decimal import Decimal

import asyncpg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")


async def main() -> None:
    conn = await asyncpg.connect(DSN, server_settings={"application_name": "m23-tour"})

    print("-- fetch / fetchrow / fetchval / execute")
    rows = await conn.fetch("SELECT id, shop_name FROM sellers WHERE id = ANY($1) ORDER BY id", [1, 2])
    print(rows)
    row = await conn.fetchrow("SELECT id, title, price, status, tags FROM products WHERE id = $1", 20)
    print(row["title"], row[2], row.get("status"), dict(row)["tags"])
    print(await conn.fetchval("SELECT count(*) FROM orders WHERE user_id = $1", 8))
    print(repr(await conn.execute("UPDATE inventory SET quantity = quantity WHERE product_id = $1", 16)))

    print("\n-- types are checked on the client, strictly")
    for value in ["8", 8.0, 8.5, Decimal(8)]:
        try:
            n = await conn.fetchval("SELECT count(*) FROM orders WHERE user_id = $1", value)
            print(f"{value!r:14} -> {n}")
        except (asyncpg.DataError, TypeError) as e:
            print(f"{value!r:14} -> {type(e).__name__}: {str(e).splitlines()[0]}")

    print("\n-- json/jsonb are str unless you install a codec")
    print(repr(await conn.fetchval("SELECT attributes FROM products WHERE id = 20")))
    await conn.set_type_codec("jsonb", schema="pg_catalog", encoder=json.dumps,
                              decoder=json.loads, format="text")  # fmt: skip
    print(repr(await conn.fetchval("SELECT attributes FROM products WHERE id = 20")))
    print(await conn.fetchval("SELECT $1::jsonb ->> 'a'", {"a": "dict in, jsonb out"}))

    print("\n-- transactions nest as savepoints")
    async with conn.transaction():
        await conn.execute("UPDATE inventory SET quantity = quantity + 1 WHERE product_id = 16")
        try:
            async with conn.transaction():
                await conn.execute("UPDATE inventory SET quantity = -1 WHERE product_id = 16")
        except asyncpg.CheckViolationError as e:
            print("inner block rolled back:", e.constraint_name, e.sqlstate)
        await conn.execute("UPDATE inventory SET quantity = quantity - 1 WHERE product_id = 16")

    print("\n-- prepared statements, explicit and implicit")
    stmt = await conn.prepare("SELECT title, price FROM products WHERE seller_id = $1 ORDER BY id LIMIT $2")
    print([a.name + ":" + a.type.name for a in stmt.get_attributes()],
          [t.name for t in stmt.get_parameters()])  # fmt: skip
    print(await stmt.fetchrow(42, 1))
    names = await conn.fetch("SELECT name FROM pg_prepared_statements ORDER BY prepare_time")
    print(len(names), "statements prepared on this connection, e.g.", names[0]["name"])

    print("\n-- the statement cache vs. schema changes")
    await conn.execute("CREATE TEMP TABLE t (id int, note text)")
    await conn.execute("INSERT INTO t VALUES (1, 'x')")
    print(await conn.fetch("SELECT * FROM t"))
    await conn.execute("ALTER TABLE t ADD COLUMN extra int")
    print(await conn.fetch("SELECT * FROM t"), "<- outside a transaction: re-prepared silently")
    await conn.execute("ALTER TABLE t DROP COLUMN extra")
    try:
        async with conn.transaction():
            print(await conn.fetch("SELECT * FROM t"))
    except asyncpg.InvalidCachedStatementError as e:
        print("inside a transaction:", type(e).__name__ + ":", str(e).splitlines()[0])

    print("\n-- COPY and cursors")
    await conn.execute("CREATE TEMP TABLE price_import (sku text, price numeric(12,2))")
    n = await conn.copy_records_to_table(
        "price_import", records=[(f"SKU-{i}", Decimal(i) / 4) for i in range(10_000)]
    )
    print(n)
    async with conn.transaction():  # cursors need a transaction
        total = 0
        async for r in conn.cursor("SELECT quantity FROM order_items WHERE order_id < $1", 1000,
                                   prefetch=500):  # fmt: skip
            total += r["quantity"]
    print("units in the first 999 orders:", total)
    await conn.close()


asyncio.run(main())
