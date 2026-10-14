"""Module 22, section 7: what Postgres values become in Python, and back."""

import os
from datetime import UTC, datetime

import psycopg
from psycopg.types.composite import CompositeInfo, register_composite
from psycopg.types.enum import EnumInfo, register_enum
from psycopg.types.json import Jsonb
from psycopg.types.range import Range

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")

with psycopg.connect(DSN) as conn:
    row = conn.execute(
        """
        SELECT p.price, p.created_at, p.created_at::date AS created_on,
               p.attributes, p.tags, p.status,
               pr.active_during, pr.discount_pct,
               e.session_id, now() - p.created_at AS age,
               '10.0.0.1'::inet AS ip, '\\xdeadbeef'::bytea AS raw,
               1.5::float8 AS f, 42::int2 AS small, NULL::text AS nothing
        FROM products p
        JOIN promotions pr ON pr.product_id = p.id
        JOIN events e ON e.product_id = p.id
        WHERE p.id = 20
        LIMIT 1
        """
    ).fetchone()
    for value, col in zip(row, [
        "price numeric", "created_at timestamptz", "created_on date", "attributes jsonb",
        "tags text[]", "status (enum)", "active_during tstzrange", "discount_pct numeric(5,2)",
        "session_id uuid", "age interval", "ip inet", "raw bytea", "f float8", "small int2",
        "nothing (NULL)"], strict=True):  # fmt: skip
        print(f"{col:26s} {type(value).__module__ + '.' + type(value).__qualname__:28s} {value!r}")

    print("\n-- the session time zone decides the tzinfo of timestamptz values")
    conn.execute("SET TimeZone = 'Asia/Tokyo'")
    print(conn.execute("SELECT created_at FROM products WHERE id = 20").fetchone()[0])
    conn.execute("RESET TimeZone")

    print("\n-- Python -> Postgres")
    for value in [Jsonb({"a": [1, 2]}), [1, 2, 3], ["a", None], datetime(2025, 1, 1, tzinfo=UTC),
                  datetime(2025, 1, 1), Range(1, 10), 2**40, 3.5, True]:  # fmt: skip
        try:
            pg_type = conn.execute("SELECT pg_typeof(%s)::text", (value,)).fetchone()[0]
        except psycopg.Error as e:
            conn.rollback()
            pg_type = f"{type(e).__name__}: {e}"
        print(f"{value!r:45.45s} -> {pg_type}")
    print("['a', None] with a cast ->", conn.execute("SELECT %s::text[]", (["a", None],)).fetchone()[0])
    try:
        conn.execute("SELECT %s", ({"a": 1},))
    except psycopg.ProgrammingError as e:
        print("plain dict ->", type(e).__name__ + ":", e)
    conn.rollback()
    print("[] ->", conn.execute("SELECT %s::int[]", ([],)).fetchone()[0])

    print("\n-- enums: str by default, a Python Enum once registered")
    print(repr(conn.execute("SELECT 'paid'::order_status").fetchone()[0]))
    info = EnumInfo.fetch(conn, "order_status")
    register_enum(info, conn)  # without a Python class: one is generated from the labels
    print(repr(conn.execute("SELECT 'paid'::order_status").fetchone()[0]))

    print("\n-- composite types: every table has a row type")
    print(repr(conn.execute("SELECT s FROM sellers s WHERE id = 1").fetchone()[0]))
    info = CompositeInfo.fetch(conn, "sellers")
    register_composite(info, conn)
    seller = conn.execute("SELECT s FROM sellers s WHERE id = 1").fetchone()[0]
    print(repr(seller))
    print(seller.shop_name, seller.country_code)
