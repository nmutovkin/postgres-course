"""Module 22, section 4: SQL injection, demonstrated (on a transaction we roll back).

NEVER build SQL like find_user_unsafe() does. This file exists to show why.
"""

import os

import psycopg
from psycopg import ClientCursor

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")


def find_user_unsafe(conn: psycopg.Connection, email: str) -> list[tuple]:
    query = f"SELECT id, full_name FROM users WHERE email = '{email}'"  # noqa: S608  <- the bug
    return conn.execute(query).fetchall()


def find_user_safe(conn: psycopg.Connection, email: str) -> list[tuple]:
    return conn.execute("SELECT id, full_name FROM users WHERE email = %s", (email,)).fetchall()


attacks = {
    "normal input": "lukas.meyer10462@example.org",
    "always true": "x' OR '1'='1",
    "stacked statement": "x'; UPDATE products SET price = 0.01 WHERE id = 16; --",
}

with psycopg.connect(DSN) as conn:
    for label, value in attacks.items():
        print(f"--- {label}: {value!r}")
        try:
            rows = find_user_unsafe(conn, value)
            print(f"unsafe -> {len(rows)} row(s); first: {rows[:1]}")
        except psycopg.Error as e:
            print(f"unsafe -> {type(e).__name__}: {e}")
            conn.rollback()
        price = conn.execute("SELECT price FROM products WHERE id = 16").fetchone()
        print(f"         (product 16 price is now {price[0]})")
        print(f"safe   -> {len(find_user_safe(conn, value))} row(s)")
    conn.rollback()  # undo the stacked UPDATE

    # What the safe version means, rendered client-side for display only:
    with ClientCursor(conn) as cur:
        print(cur.mogrify("SELECT id FROM users WHERE email = %s", (attacks["always true"],)))
