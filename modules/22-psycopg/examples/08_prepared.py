"""Module 22, section 12: automatic prepared statements."""

import os
import time

import psycopg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
QUERY = """
    SELECT o.id, o.status, o.total, s.shop_name, count(oi.*) AS items
    FROM orders o
    JOIN sellers s ON s.id = o.seller_id
    LEFT JOIN order_items oi ON oi.order_id = o.id
    WHERE o.user_id = %s
    GROUP BY o.id, s.shop_name
    ORDER BY o.created_at DESC
    LIMIT 5
"""
PREPARED = "SELECT name, statement IS NOT NULL, generic_plans, custom_plans FROM pg_prepared_statements"

with psycopg.connect(DSN, autocommit=True) as conn:
    print("prepare_threshold =", conn.prepare_threshold)
    for i in range(1, 8):
        conn.execute(QUERY, (i,)).fetchall()
        print(f"after execution {i}: {conn.execute(PREPARED).fetchall()}")

for threshold, label in [(None, "never prepare"), (0, "prepare at once")]:
    with psycopg.connect(DSN, autocommit=True, prepare_threshold=threshold) as conn:
        for _ in range(2):  # second round counts
            t = time.perf_counter()
            for uid in range(1, 3001):
                conn.execute(QUERY, (uid,)).fetchall()
            dt = time.perf_counter() - t
        print(f"{label:16s} 3000 executions: {dt:.2f} s ({dt / 3000 * 1e6:.0f} µs each)")
