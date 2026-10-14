"""Exercise 13: a bulk upsert that reports inserted / updated / unchanged rows.

Two implementations over the same COPY-loaded staging table, each run in a
transaction that is rolled back:
  * INSERT ... ON CONFLICT DO UPDATE ... WHERE <something changed>
  * MERGE ... RETURNING merge_action()   (PostgreSQL 17)
"""

import os
import time
from decimal import Decimal

import psycopg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
SELLER = 2

STAGING = """
    CREATE TEMP TABLE upsert_staging (
        sku text PRIMARY KEY, category_id int, title text, price numeric(12,2)
    ) ON COMMIT DROP
"""

ON_CONFLICT = """
    INSERT INTO products AS p (seller_id, category_id, sku, title, price)
    SELECT %(seller)s, category_id, sku, title, price FROM upsert_staging
    ON CONFLICT (seller_id, sku) DO UPDATE
    SET category_id = EXCLUDED.category_id, title = EXCLUDED.title, price = EXCLUDED.price
    WHERE (p.category_id, p.title, p.price)
          IS DISTINCT FROM (EXCLUDED.category_id, EXCLUDED.title, EXCLUDED.price)
    RETURNING (xmax = 0) AS inserted
"""

MERGE = """
    MERGE INTO products AS p
    USING upsert_staging AS s ON p.seller_id = %(seller)s AND p.sku = s.sku
    WHEN MATCHED AND (p.category_id, p.title, p.price)
                     IS DISTINCT FROM (s.category_id, s.title, s.price) THEN
        UPDATE SET category_id = s.category_id, title = s.title, price = s.price
    WHEN NOT MATCHED THEN
        INSERT (seller_id, category_id, sku, title, price)
        VALUES (%(seller)s, s.category_id, s.sku, s.title, s.price)
    RETURNING merge_action()
"""


def make_rows(conn: psycopg.Connection) -> list[tuple]:
    existing = conn.execute(
        "SELECT sku, category_id, title, price FROM products WHERE seller_id = %s ORDER BY id LIMIT 8000",
        (SELLER,),
    ).fetchall()
    rows = []
    for i, (sku, cat, title, price) in enumerate(existing):
        if i % 8 < 3:  # 3,000 of 8,000 get a new price
            price = price + Decimal("1.00")
        rows.append((sku, cat, title, price))
    rows += [(f"NEW-{i:05d}", 3, f"New product {i}", Decimal("9.99")) for i in range(2000)]
    return rows


def run(conn: psycopg.Connection, rows: list[tuple], stmt: str) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(STAGING)
        with cur.copy("COPY upsert_staging FROM STDIN") as cp:
            for r in rows:
                cp.write_row(r)
        cur.execute(stmt, {"seller": SELLER})
        actions = [r[0] for r in cur.fetchall()]
    if stmt is ON_CONFLICT:
        inserted = sum(actions)
        counts = {"inserted": inserted, "updated": len(actions) - inserted}
    else:
        counts = {"inserted": actions.count("INSERT"), "updated": actions.count("UPDATE")}
    counts["unchanged"] = len(rows) - counts["inserted"] - counts["updated"]
    return counts


if __name__ == "__main__":
    with psycopg.connect(DSN, autocommit=True) as conn:
        rows = make_rows(conn)
        for label, stmt in (("ON CONFLICT", ON_CONFLICT), ("MERGE", MERGE)):
            with conn.transaction(force_rollback=True):
                t = time.perf_counter()
                counts = run(conn, rows, stmt)
                dt = time.perf_counter() - t
            print(f"{label:12s} {counts}  {dt:.2f}s")
            assert counts == {"inserted": 2000, "updated": 3000, "unchanged": 5000}
