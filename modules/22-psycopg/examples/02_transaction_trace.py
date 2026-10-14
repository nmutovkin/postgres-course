"""Module 22, section 3/5: which SQL does psycopg send for each transaction style?

Run it, then read the server log, e.g.:
    docker logs course-pg 2>&1 | grep 'm22-trace' -A0 | tail -40
Each connection sets log_statement=all for itself (allowed for superusers),
so the server logs every statement it receives from this script.
"""

import os

import psycopg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
OPTS = {"options": "-c log_statement=all"}


def scenario(name: str) -> psycopg.Connection:
    conn = psycopg.connect(DSN, **OPTS)
    conn.execute("SELECT %s::text AS scenario", (name,))  # marker in the log
    conn.commit()
    return conn


# A: default (autocommit off): implicit BEGIN, `with` commits and closes
with scenario("A: with-block, no error") as conn:
    conn.execute("UPDATE inventory SET quantity = quantity WHERE product_id = 16")

# B: exception inside the with-block: rollback, then close
try:
    with scenario("B: with-block, exception") as conn:
        conn.execute("UPDATE inventory SET quantity = quantity WHERE product_id = 16")
        raise RuntimeError("boom")
except RuntimeError:
    pass

# C: autocommit + transaction() blocks, nested
with scenario("C: autocommit + nested transaction()") as conn:
    conn.autocommit = True
    conn.execute("SELECT 1")  # its own transaction, no BEGIN needed
    with conn.transaction():
        conn.execute("UPDATE inventory SET quantity = quantity WHERE product_id = 16")
        try:
            with conn.transaction():
                conn.execute("UPDATE inventory SET quantity = -1 WHERE product_id = 16")
        except psycopg.errors.CheckViolation:
            pass  # only the inner block is undone
        conn.execute("UPDATE inventory SET quantity = quantity WHERE product_id = 47")

# D: transaction() on an idle, non-autocommit connection is the OUTER transaction
with scenario("D: no autocommit, transaction() first") as conn:
    with conn.transaction():
        conn.execute("SELECT 2")
    conn.execute("SELECT 3")  # implicit BEGIN again; committed when the with ends
print("done")
