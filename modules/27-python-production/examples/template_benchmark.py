"""How long does a fresh test database take? Migrations vs. a template copy (module 27, §2.3).

    cd project/python && python ../../modules/27-python-production/examples/template_benchmark.py

Three ways to get an empty Bazaar schema at head, 5 times each:
  1. CREATE DATABASE + `alembic upgrade head` (runs every revision)
  2. CREATE DATABASE ... TEMPLATE bazaar_bench_tpl (default strategy WAL_LOG)
  3. CREATE DATABASE ... TEMPLATE bazaar_bench_tpl STRATEGY FILE_COPY
"""

from __future__ import annotations

import statistics
import time

import psycopg

from bazaar import migrate
from bazaar.config import get_settings
from bazaar.testing import create_database, database_dsn, drop_database

ADMIN = get_settings().migration_dsn
TPL = "bazaar_bench_tpl"
RUNS = 5


def timed(fn) -> float:  # noqa: ANN001
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0


def via_migrations(i: int) -> float:
    name = f"bazaar_bench_m{i}"
    drop_database(ADMIN, name)

    def build() -> None:
        migrate.upgrade(create_database(ADMIN, name))

    t = timed(build)
    drop_database(ADMIN, name)
    return t


def via_template(i: int, strategy: str) -> float:
    name = f"bazaar_bench_t{i}"
    drop_database(ADMIN, name)

    def build() -> None:
        with psycopg.connect(database_dsn(ADMIN, "postgres"), autocommit=True) as c:
            c.execute(f"CREATE DATABASE {name} TEMPLATE {TPL} STRATEGY {strategy}")

    t = timed(build)
    drop_database(ADMIN, name)
    return t


def main() -> None:
    drop_database(ADMIN, TPL)
    migrate.upgrade(create_database(ADMIN, TPL))
    results = {
        "alembic upgrade head": [via_migrations(i) for i in range(RUNS)],
        "TEMPLATE (WAL_LOG)": [via_template(i, "WAL_LOG") for i in range(RUNS)],
        "TEMPLATE (FILE_COPY)": [via_template(i, "FILE_COPY") for i in range(RUNS)],
    }
    drop_database(ADMIN, TPL)
    base = statistics.median(results["alembic upgrade head"])
    for label, times in results.items():
        med = statistics.median(times)
        print(f"{label:<22} median {med * 1000:7.0f} ms   ({base / med:4.1f}x)")


if __name__ == "__main__":
    main()
