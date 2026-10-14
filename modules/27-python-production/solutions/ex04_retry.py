"""Exercise 4: a transaction-retry decorator with exponential backoff and full
jitter, written from scratch, and proven with injected and real 40001 errors.

    cd project/python && python ../../modules/27-python-production/solutions/ex04_retry.py
"""

from __future__ import annotations

import functools
import random
import threading
import time
from collections.abc import Callable
from typing import Any

import psycopg
from psycopg import errors as pgerr

from bazaar.config import get_settings
from bazaar.testing import scratch_database

RETRYABLE = (pgerr.SerializationFailure, pgerr.DeadlockDetected)  # 40001, 40P01


def retry_transaction(
    attempts: int = 5,
    base: float = 0.05,
    cap: float = 2.0,
    *,
    sleep: Callable[[float], None] = time.sleep,
    rand: Callable[[], float] = random.random,
    log: Callable[[str], None] | None = print,
) -> Callable:
    """Retry the decorated function (which must own its transaction) on 40001/40P01.

    Delay before retry n (n = 1, 2, ...): rand() * min(cap, base * 2**(n-1)),
    i.e. "full jitter". sleep and rand are parameters so tests can check the
    schedule without waiting.
    """

    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except RETRYABLE as e:
                    if attempt == attempts:
                        raise
                    delay = rand() * min(cap, base * 2 ** (attempt - 1))
                    if log:
                        log(f"  attempt {attempt}: {e.sqlstate} -> sleep {delay * 1000:.1f} ms")
                    sleep(delay)
            raise AssertionError("unreachable")

        return wrapper

    return deco


def injected() -> None:
    print("1. injected 40001, deterministic schedule (rand() = 0.5, no real sleeping):")
    slept: list[float] = []
    calls = 0

    @retry_transaction(attempts=5, base=0.05, sleep=slept.append, rand=lambda: 0.5)
    def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls <= 3:
            raise pgerr.SerializationFailure("could not serialize access")
        return "committed"

    print("  result:", flaky(), f"after {calls} calls; slept {[round(s, 3) for s in slept]}")

    @retry_transaction(attempts=3, base=0.001, log=None)
    def always() -> None:
        raise pgerr.DeadlockDetected("deadlock detected")

    try:
        always()
    except pgerr.DeadlockDetected:
        print("  a permanent conflict is re-raised after 3 attempts")


def real() -> None:
    """Write skew under SERIALIZABLE, in a scratch database: a seller's shop needs at
    least one staff member on duty. Two staff members go off duty at the same time;
    each checks "is someone else still on duty?" (yes) and updates their own row.
    Different rows, so no lock conflict: only SERIALIZABLE notices."""
    print("2. real 40001 (SERIALIZABLE write skew, two threads held together by a barrier):")
    admin = get_settings().migration_dsn
    with scratch_database(admin, "bazaar_ex04_skew") as dsn:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute("CREATE TABLE staff (name text PRIMARY KEY, on_duty boolean NOT NULL)")
            c.execute("INSERT INTO staff VALUES ('ana', true), ('ben', true)")
        barrier = threading.Barrier(2)
        calls: list[str] = []
        results: dict[str, str] = {}

        @retry_transaction(attempts=5, base=0.02)
        def go_off_duty(name: str) -> str:
            calls.append(name)
            with psycopg.connect(dsn) as c:
                c.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                others = c.execute(
                    "SELECT count(*) FROM staff WHERE on_duty AND name <> %s", (name,)
                ).fetchone()[0]
                if len(calls) <= 2:
                    barrier.wait()  # both have checked before either writes
                if others == 0:
                    return "refused: last one on duty"
                c.execute("UPDATE staff SET on_duty = false WHERE name = %s", (name,))
                c.commit()
                return "off duty"

        threads = [
            threading.Thread(target=lambda n=n: results.__setitem__(n, go_off_duty(n)))
            for n in ("ana", "ben")
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with psycopg.connect(dsn) as c:
            on_duty = c.execute("SELECT count(*) FROM staff WHERE on_duty").fetchone()[0]
        print(f"  results {results}, {len(calls)} calls, still on duty: {on_duty}")


def collisions() -> None:
    """Why jitter: 50 clients fail at the same moment; a retry 'slot' succeeds only
    if exactly one client retries in it. Count rounds until everyone is done."""
    print("3. 50 clients after a conflict, retries in 1 ms slots:")
    rng = random.Random(42)
    for label, jitter in (("no jitter (sleep = base * 2**n)", False), ("full jitter", True)):
        pending, attempt = 50, 0
        while pending and attempt < 12:
            attempt += 1
            ceiling = min(512, 2 ** (attempt - 1))  # in 1 ms slots
            slots = [rng.randrange(ceiling) if jitter else ceiling - 1 for _ in range(pending)]
            pending -= sum(1 for s in set(slots) if slots.count(s) == 1)
        print(f"  {label:<34} after {attempt} retry rounds: {pending} of 50 still failing")


if __name__ == "__main__":
    injected()
    real()
    collisions()
