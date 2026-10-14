"""Module 22, section 5: isolation levels and read-only transactions from Python."""

import os

import psycopg
from psycopg import IsolationLevel, errors

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")

a = psycopg.connect(DSN)
b = psycopg.connect(DSN)
for c in (a, b):
    c.isolation_level = IsolationLevel.SERIALIZABLE

# Both read the stock of product 16, then both write based on what they read.
qa = a.execute("SELECT quantity FROM inventory WHERE product_id = 16").fetchone()[0]
qb = b.execute("SELECT quantity FROM inventory WHERE product_id = 16").fetchone()[0]
a.execute("UPDATE inventory SET quantity = %s WHERE product_id = 16", (qa + 1,))
a.commit()
print("A committed:", a.execute("SHOW transaction_isolation").fetchone()[0])
a.rollback()
try:
    b.execute("UPDATE inventory SET quantity = %s WHERE product_id = 16", (qb + 1,))
except errors.SerializationFailure as e:
    print(f"B: {type(e).__name__} ({e.sqlstate}): {e.diag.message_primary}")
    b.rollback()

# Undo A's change
a.isolation_level = None  # back to the server default (read committed)
with a.transaction():
    a.execute("UPDATE inventory SET quantity = quantity - 1 WHERE product_id = 16")

# Read-only transactions refuse writes
a.read_only = True
try:
    a.execute("DELETE FROM jobs")
except errors.ReadOnlySqlTransaction as e:
    print(f"read_only: {type(e).__name__}: {e}")
a.rollback()
a.close()
b.close()
