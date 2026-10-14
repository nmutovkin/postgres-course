"""Module 22, section 4: what server-side binding allows, and what it does not."""

import os

import psycopg
from psycopg import sql

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")


def attempt(conn: psycopg.Connection, label: str, query, params=None) -> None:
    try:
        cur = conn.execute(query, params)
        print(f"OK    {label}: {cur.fetchall() if cur.description else cur.statusmessage}")
    except (psycopg.Error, TypeError) as e:
        print(f"FAIL  {label}: {type(e).__name__}: {str(e).splitlines()[0]}")


with psycopg.connect(DSN, autocommit=True) as conn:
    attempt(conn, "string instead of a sequence", "SELECT %s::int", "42")
    attempt(conn, "1-tuple", "SELECT %s::int", ("42",))
    attempt(conn, "IN %s with a tuple", "SELECT count(*) FROM sellers WHERE id IN %s", ((1, 2, 3),))
    attempt(conn, "= ANY(%s) with a list", "SELECT count(*) FROM sellers WHERE id = ANY(%s)",
            ([1, 2, 3],))
    attempt(conn, "quoted placeholder", "SELECT count(*) FROM users WHERE email = '%s'", ("x",))
    attempt(conn, "literal % without params", "SELECT count(*) FROM users WHERE email LIKE '%@example.org'")
    attempt(conn, "literal % with params", "SELECT count(*) FROM users WHERE email LIKE '%@example.org' AND id < %s", (10,))
    attempt(conn, "%% with params", "SELECT count(*) FROM users WHERE email LIKE '%%@example.org' AND id < %s", (10,))
    attempt(conn, "pattern as a parameter", "SELECT count(*) FROM users WHERE email LIKE %s", ("%@example.org",))
    attempt(conn, "SET with a parameter", "SET statement_timeout = %s", ("5s",))
    attempt(conn, "set_config() instead", "SELECT set_config('statement_timeout', %s, false)", ("5s",))
    attempt(conn, "table name as a parameter", "SELECT count(*) FROM %s", ("sellers",))
    attempt(conn, "sql.Identifier", sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("sellers")))
    attempt(conn, "untyped NULL comparison", "SELECT %s IS NULL", (None,))
    attempt(conn, "parameter needing a type", "SELECT %s + %s", (1, 2))
    attempt(conn, "mixed text arithmetic", "SELECT %s || 'x'", ("a",))
