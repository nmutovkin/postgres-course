"""Exercise 6: a connection pool across fork(), without and with dispose_after_fork().

    cd project/python && python ../../modules/27-python-production/solutions/ex06_fork.py

The parent uses its engine (one pooled connection stays open), then forks
three workers that use the same engine object, like gunicorn --preload or a
multiprocessing pool would.
"""

from __future__ import annotations

import multiprocessing as mp

import sqlalchemy as sa

from bazaar.sa.engine import create_engine, dispose_after_fork

ENGINE: sa.Engine | None = None


def work(i: int) -> str:
    assert ENGINE is not None
    try:
        with ENGINE.connect() as c:
            pid = c.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
            for _ in range(200):
                c.execute(sa.text("SELECT count(*) FROM products WHERE id < 1000")).scalar_one()
            return f"worker {i}: ok, backend {pid}"
    except Exception as e:  # noqa: BLE001
        return f"worker {i}: {type(e).__name__}: {str(e).splitlines()[0][:80]}"


def run(label: str, fix: bool) -> None:
    global ENGINE
    ENGINE = create_engine(pool_size=1)
    if fix:
        dispose_after_fork(ENGINE)
    with ENGINE.connect() as c:
        parent = c.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
    print(f"--- {label} (parent's pooled connection: backend {parent})")
    with mp.get_context("fork").Pool(3) as pool:
        for line in pool.map(work, range(3)):
            print("  " + line)
    try:
        with ENGINE.connect() as c:
            c.execute(sa.text("SELECT 1"))
        print("  parent afterwards: ok")
    except Exception as e:  # noqa: BLE001
        print(f"  parent afterwards: {type(e).__name__}: {str(e).splitlines()[0][:80]}")
    ENGINE.dispose()


if __name__ == "__main__":
    run("engine created before fork, used as is", fix=False)
    run("engine created before fork, dispose_after_fork()", fix=True)
