"""Module 22, section 8: errors, SQLSTATEs, and the connection afterwards."""

import os

import psycopg
from psycopg import errors

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")

print("UniqueViolation MRO:", " -> ".join(c.__name__ for c in errors.UniqueViolation.__mro__[:5]))
print("lookup('40001'):", errors.lookup("40001").__name__)
print("lookup('BZ001'):", end=" ")
try:
    errors.lookup("BZ001")
except KeyError as e:
    print("KeyError", e)

with psycopg.connect(DSN) as conn:
    # 1. A unique violation, and everything diag tells us about it
    try:
        conn.execute(
            "INSERT INTO reviews (product_id, user_id, rating) VALUES (%s, %s, %s)", (17063, 7068, 5)
        )
    except errors.UniqueViolation as e:
        d = e.diag
        print("\n1.", type(e).__name__, e.sqlstate)
        print("   message_primary:", d.message_primary)
        print("   message_detail: ", d.message_detail)
        print("   constraint_name:", d.constraint_name, "| table:", d.schema_name + "." + d.table_name)
        print("   status:", conn.info.transaction_status.name)

    # 2. The transaction is now aborted: everything fails until rollback
    try:
        conn.execute("SELECT 1")
    except errors.InFailedSqlTransaction as e:
        print("\n2.", type(e).__name__ + ":", e)
    conn.rollback()
    print("   after rollback:", conn.execute("SELECT 'usable again'").fetchone()[0])
    conn.rollback()

    # 3. A custom SQLSTATE from place_order()
    try:
        conn.execute("SELECT place_order(8, '[{\"product_id\": 47, \"quantity\": 3}]')")
    except psycopg.Error as e:
        print("\n3.", type(e).__module__ + "." + type(e).__qualname__, e.sqlstate)
        print("   message_primary:", e.diag.message_primary)
        print("   message_detail: ", e.diag.message_detail)
        print("   context:        ", e.diag.context.splitlines()[0])
    conn.rollback()

    # 4. Notices: RAISE NOTICE / WARNING arrive as notices, not errors
    conn.add_notice_handler(lambda n: print(f"   notice [{n.severity}] {n.message_primary}"))
    print("\n4.")
    conn.execute("DO $$ BEGIN RAISE NOTICE 'reindexing % products', 200000; END $$")
    conn.execute("DROP TABLE IF EXISTS no_such_table")
    conn.rollback()

# 5. The server goes away under us
with psycopg.connect(DSN, autocommit=True) as victim, psycopg.connect(DSN, autocommit=True) as admin:
    admin.execute("SELECT pg_terminate_backend(%s)", (victim.info.backend_pid,))
    try:
        victim.execute("SELECT 1")
    except psycopg.OperationalError as e:
        print("\n5.", type(e).__name__ + ":", str(e).strip().splitlines()[0])
        print("   closed:", victim.closed, "| broken:", victim.broken)
