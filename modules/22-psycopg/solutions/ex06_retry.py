"""Exercise 6: retry transactions that fail with serialization failures or deadlocks."""

import functools
import os
import random
import threading
import time
from collections.abc import Callable
from typing import ParamSpec, TypeVar

import psycopg
from psycopg import IsolationLevel, errors
from psycopg_pool import ConnectionPool

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
RETRYABLE = (errors.SerializationFailure, errors.DeadlockDetected)

P = ParamSpec("P")
R = TypeVar("R")
stats = {"retries": 0}
stats_lock = threading.Lock()


def retry_transaction(max_attempts: int = 10, base_delay: float = 0.005):
    """Re-run the decorated function when its transaction fails with 40001 or 40P01.

    The function must take the connection as first argument, run ONE whole
    transaction (in a conn.transaction() block), and have no side effects outside
    the database: it may run several times.
    """

    def decorate(fn: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            conn = args[0]
            assert isinstance(conn, psycopg.Connection)
            if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                # A retry restarts the transaction from scratch; inside an outer
                # transaction we would only be retrying a savepoint, which can't help.
                raise RuntimeError("retry_transaction needs an idle connection")
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except RETRYABLE:
                    if attempt == max_attempts:
                        raise
                    with stats_lock:
                        stats["retries"] += 1
                    # exponential backoff with full jitter, so retries don't collide again
                    time.sleep(random.uniform(0, base_delay * 2 ** (attempt - 1)))
            raise AssertionError("unreachable")

        return wrapper

    return decorate


def increment(conn: psycopg.Connection) -> None:
    """Read-modify-write: safe only at SERIALIZABLE (or with SELECT ... FOR UPDATE)."""
    with conn.transaction():
        n = conn.execute("SELECT n FROM ex06_counter WHERE id = 1").fetchone()[0]
        conn.execute("UPDATE ex06_counter SET n = %s WHERE id = 1", (n + 1,))


increment_with_retry = retry_transaction()(increment)


def run(fn, threads: int = 8, per_thread: int = 25) -> tuple[int, int]:
    failures = 0
    lock = threading.Lock()
    pool = ConnectionPool(
        DSN, min_size=threads, max_size=threads, open=True,
        kwargs={"autocommit": True},
        configure=lambda c: setattr(c, "isolation_level", IsolationLevel.SERIALIZABLE),
    )  # fmt: skip

    def worker() -> None:
        nonlocal failures
        for _ in range(per_thread):
            with pool.connection() as conn:
                try:
                    fn(conn)
                except errors.SerializationFailure:
                    with lock:
                        failures += 1

    ts = [threading.Thread(target=worker) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    with pool.connection() as conn:
        final = conn.execute("SELECT n FROM ex06_counter WHERE id = 1").fetchone()[0]
    pool.close()
    return final, failures


if __name__ == "__main__":
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS ex06_counter")
        conn.execute("CREATE TABLE ex06_counter (id int PRIMARY KEY, n int NOT NULL)")
        try:
            for label, fn in [("without retry", increment), ("with retry", increment_with_retry)]:
                conn.execute("TRUNCATE ex06_counter")
                conn.execute("INSERT INTO ex06_counter VALUES (1, 0)")
                stats["retries"] = 0
                final, failures = run(fn)
                print(f"{label:14s} final n = {final:3d} (expected 200), "
                      f"failed increments = {failures}, retries = {stats['retries']}")  # fmt: skip
            assert final == 200 and failures == 0
        finally:
            conn.execute("DROP TABLE ex06_counter")
