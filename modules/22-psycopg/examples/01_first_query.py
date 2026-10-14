"""Module 22, section 2: the smallest complete psycopg program."""

import os

import psycopg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")

with psycopg.connect(DSN, application_name="m22-first-query") as conn:
    print("server version:", conn.info.server_version)
    print("backend pid:   ", conn.info.backend_pid)
    print("status before: ", conn.info.transaction_status.name)
    cur = conn.execute(
        "SELECT id, shop_name, country_code FROM sellers WHERE id <= %s ORDER BY id", (3,)
    )
    print("status after:  ", conn.info.transaction_status.name)
    print("columns:       ", [c.name for c in cur.description])
    for row in cur:
        print(row)
    who = conn.execute(
        "SELECT application_name, state, backend_xid IS NOT NULL OR backend_xmin IS NOT NULL"
        " FROM pg_stat_activity WHERE pid = pg_backend_pid()"
    ).fetchone()
    print("pg_stat_activity:", who)
print("closed:", conn.closed)
