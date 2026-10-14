"""Measure what a DDL statement does to concurrent traffic (module 26, section 8).

While one connection runs the DDL steps you give it, a "traffic" thread runs a
small read + write against the same table in a tight loop, timing each
statement, and a "sampler" thread polls pg_locks for the DDL connection's
locks every 2 ms. For each DDL step you get its duration, the worst traffic
latency while it ran, and the locks it held or waited for (AccessShareLocks
on indexes are left out: every query takes those).

    cd project/python
    python ../../modules/26-migrations/examples/lock_probe.py \
        --table orders \
        --ddl "ALTER TABLE orders ADD COLUMN demo_flag boolean NOT NULL DEFAULT false" \
        --undo "ALTER TABLE orders DROP COLUMN demo_flag"

Each --ddl runs in its own transaction (autocommit), like the steps of an
online migration; --undo statements run afterwards to restore the database.
The traffic is "SELECT ... WHERE id = $1" and "UPDATE ... SET updated_at =
updated_at WHERE id = $1" (--set) on random ids: no business change (the
updated_at trigger bumps the timestamp, the audit trigger ignores that), but
an UPDATE needs ROW EXCLUSIVE on the table, like any write.
Use --no-write to measure readers only.
"""

from __future__ import annotations

import argparse
import random
import threading
import time

import psycopg

from bazaar.config import get_settings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default="orders")
    ap.add_argument("--key", default="id")
    ap.add_argument("--set", default="updated_at = updated_at")
    ap.add_argument("--ddl", action="append", required=True)
    ap.add_argument("--undo", action="append", default=[])
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--lock-timeout", default="0", help="lock_timeout for the DDL session")
    args = ap.parse_args()

    dsn = get_settings().dsn
    stop = threading.Event()
    ddl_running = threading.Event()
    latencies: list[tuple[float, float]] = []  # (start time, seconds) during the DDL
    locks: dict[tuple[str, str, bool], float] = {}

    with psycopg.connect(dsn, autocommit=True) as c:
        lo, hi = c.execute(f"SELECT min({args.key}), max({args.key}) FROM {args.table}").fetchone()

    def traffic() -> None:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute("SET application_name = 'probe-traffic'")
            while not stop.is_set():
                k = random.randint(lo, hi)
                t0 = time.perf_counter()
                c.execute(f"SELECT 1 FROM {args.table} WHERE {args.key} = %s", (k,))
                if not args.no_write:
                    c.execute(
                        f"UPDATE {args.table} SET {args.set} WHERE {args.key} = %s",
                        (k,),
                    )
                if ddl_running.is_set():
                    latencies.append((t0, time.perf_counter() - t0))
                time.sleep(0.005)

    def sampler(pid: int) -> None:
        with psycopg.connect(dsn, autocommit=True) as c:
            while not stop.is_set():
                rows = c.execute(
                    """
                    SELECT coalesce(relation::regclass::text, locktype), mode, granted
                    FROM pg_locks
                    WHERE pid = %s AND locktype IN ('relation', 'transactionid')
                      AND (relation IS NULL OR relation::regclass::text NOT LIKE 'pg_%%')
                    """,
                    (pid,),
                ).fetchall()
                for rel, mode, granted in rows:
                    locks.setdefault((rel, mode, granted), time.perf_counter())
                time.sleep(0.002)

    with psycopg.connect(dsn, autocommit=True) as ddl:
        ddl.execute("SET application_name = 'probe-ddl'")
        ddl.execute("SELECT set_config('lock_timeout', %s, false)", (args.lock_timeout,))
        threads = [
            threading.Thread(target=traffic, daemon=True),
            threading.Thread(target=sampler, args=(ddl.info.backend_pid,), daemon=True),
        ]
        for t in threads:
            t.start()
        time.sleep(0.5)  # warm up
        try:
            for stmt in args.ddl:
                ddl_running.set()
                t0 = time.perf_counter()
                try:
                    ddl.execute(stmt)
                    outcome = "ok"
                except psycopg.Error as e:
                    outcome = f"{type(e).__name__}: {str(e).splitlines()[0]}"
                took = time.perf_counter() - t0
                time.sleep(0.05)
                ddl_running.clear()
                during = [s for start, s in latencies if start >= t0 - 0.01]
                worst = max(during, default=0.0)
                print(f"DDL  {stmt}")
                print(f"     {outcome}, took {took * 1000:.0f} ms")
                print(
                    f"     traffic during it: {len(during)} round trips,"
                    f" worst {worst * 1000:.0f} ms"
                )
                latencies.clear()
                print("     locks of the DDL session (in the order first seen):")
                for (rel, mode, granted), _ in sorted(locks.items(), key=lambda kv: kv[1]):
                    if rel.isdigit():  # not named yet: a relation the DDL is creating
                        rel = f"(new relation {rel})"
                    if not rel.endswith(("_idx", "_pkey", "_key")) or mode != "AccessShareLock":
                        print(f"       {rel:<32} {mode:<26} {'granted' if granted else 'WAITING'}")
                locks.clear()
        finally:
            stop.set()
            for t in threads:
                t.join()
            for stmt in args.undo:
                ddl.execute(stmt)


if __name__ == "__main__":
    main()
