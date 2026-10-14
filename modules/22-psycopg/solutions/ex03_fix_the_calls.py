"""Exercise 3: six broken calls, fixed."""

import os

import psycopg
from psycopg import sql

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")

with psycopg.connect(DSN) as conn:
    # a) IN %s  ->  = ANY(%s) with a list; the server infers order_status[] from the column
    a = conn.execute(
        "SELECT count(*) FROM orders WHERE status = ANY(%s)", (["paid", "shipped"],)
    ).fetchone()[0]
    # b) SET with a parameter  ->  set_config(); false = session, true = transaction (SET LOCAL)
    b = conn.execute("SELECT set_config('statement_timeout', %s, false)", ("2s",)).fetchone()[0]
    # c) quoted placeholder  ->  bare placeholder
    c = conn.execute(
        "SELECT full_name FROM users WHERE lower(email) = lower(%s)",
        ("Lukas.Meyer10462@example.org",),
    ).fetchone()[0]
    # d) a bare int is not a sequence  ->  1-tuple
    d = conn.execute("SELECT title FROM products WHERE id = %s", (16,)).fetchone()[0]
    # e) literal % next to placeholders  ->  pass the pattern as a parameter
    e = conn.execute(
        "SELECT count(*) FROM products WHERE title LIKE %s AND seller_id = %s", ("%Headphones%", 1)
    ).fetchone()[0]
    # f) table name as a parameter  ->  sql.Identifier (from a whitelist!)
    allowed = {"reviews", "orders", "products"}
    table = "reviews"
    assert table in allowed
    f = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]

for label, value in zip("abcdef", [a, b, c, d, e, f], strict=True):
    print(f"{label}) {value!r}")
