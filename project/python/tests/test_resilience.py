"""Module 27: error classification and retries, with injected and with real failures."""

from __future__ import annotations

import asyncio
import threading
import time
from decimal import Decimal

import asyncpg
import psycopg
import psycopg_pool
import pytest
import sqlalchemy as sa
from psycopg import errors as pgerr

from bazaar import repo
from bazaar.errors import InsufficientStock
from bazaar.resilience import (
    ErrorKind,
    RetryPolicy,
    acall_with_retry,
    call_with_retry,
    classify,
    retry,
    run_transaction,
)
from tests.factories import make_shop

FAST = RetryPolicy(attempts=5, base_delay=0.001, max_delay=0.01, deadline=None)

# --- classification ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (pgerr.SerializationFailure(), ErrorKind.SERIALIZATION),
        (pgerr.DeadlockDetected(), ErrorKind.SERIALIZATION),
        (pgerr.LockNotAvailable(), ErrorKind.LOCK_TIMEOUT),
        (pgerr.AdminShutdown(), ErrorKind.CONNECTION),
        (pgerr.CannotConnectNow(), ErrorKind.CONNECTION),
        (pgerr.ReadOnlySqlTransaction(), ErrorKind.READ_ONLY),
        (pgerr.QueryCanceled(), ErrorKind.TIMEOUT),
        (pgerr.IdleInTransactionSessionTimeout(), ErrorKind.CONNECTION),
        (pgerr.TransactionTimeout(), ErrorKind.TIMEOUT),
        (pgerr.ConnectionTimeout("connection timeout expired"), ErrorKind.CONNECTION),
        (pgerr.TooManyConnections(), ErrorKind.OVERLOADED),
        (pgerr.UniqueViolation(), ErrorKind.PERMANENT),
        (pgerr.SyntaxError(), ErrorKind.PERMANENT),
        (
            psycopg.OperationalError("server closed the connection unexpectedly"),
            ErrorKind.CONNECTION,
        ),
        (
            psycopg.OperationalError(
                'connection failed: FATAL:  password authentication failed for user "bazaar_app"'
            ),
            ErrorKind.PERMANENT,
        ),
        (psycopg_pool.PoolTimeout("couldn't get a connection after 10 sec"), ErrorKind.OVERLOADED),
        (asyncpg.exceptions.SerializationError("x"), ErrorKind.SERIALIZATION),
        (asyncpg.exceptions.ConnectionDoesNotExistError("x"), ErrorKind.CONNECTION),
        (asyncpg.exceptions.UniqueViolationError("x"), ErrorKind.PERMANENT),
        (TimeoutError(), ErrorKind.TIMEOUT),
        (ConnectionResetError(), ErrorKind.CONNECTION),
        (ValueError("bug"), ErrorKind.PERMANENT),
    ],
    ids=lambda v: type(v).__name__ if isinstance(v, BaseException) else str(v),
)
def test_classify(exc: BaseException, kind: ErrorKind) -> None:
    assert classify(exc) == kind


def test_classify_sees_through_sqlalchemy(sa_conn: sa.Connection) -> None:
    with pytest.raises(sa.exc.DBAPIError) as info, sa_conn.begin_nested():
        sa_conn.execute(sa.text("SELECT 1/0"))
    assert classify(info.value) == ErrorKind.PERMANENT  # 22012 division_by_zero
    with pytest.raises(sa.exc.DBAPIError) as info, sa_conn.begin_nested():
        sa_conn.execute(sa.text("SET LOCAL statement_timeout = '10ms'"))
        sa_conn.execute(sa.text("SELECT pg_sleep(1)"))
    assert classify(info.value) == ErrorKind.TIMEOUT


def test_business_errors_are_permanent(conn: psycopg.Connection) -> None:
    with pytest.raises(psycopg.Error) as info, conn.transaction():
        conn.execute('SELECT place_order(8, \'[{"product_id": 999999999, "quantity": 1}]\', NULL)')
    assert info.value.sqlstate.startswith("BZ")
    assert classify(info.value) == ErrorKind.PERMANENT


# --- backoff ------------------------------------------------------------------------------


def test_full_jitter_backoff_is_bounded_and_spread() -> None:
    p = RetryPolicy(base_delay=0.1, max_delay=1.0)
    for attempt, ceiling in [(1, 0.1), (2, 0.2), (3, 0.4), (4, 0.8), (5, 1.0), (9, 1.0)]:
        delays = [p.backoff(attempt) for _ in range(2000)]
        assert 0 <= min(delays) and max(delays) <= ceiling
        assert max(delays) > 0.9 * ceiling and min(delays) < 0.1 * ceiling  # spread out


# --- injected failures ----------------------------------------------------------------------


def test_retry_after_injected_serialization_failures() -> None:
    calls: list[int] = []
    retries: list[tuple[int, float]] = []

    def work() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise pgerr.SerializationFailure("could not serialize access")
        return "done"

    out = call_with_retry(work, FAST, on_retry=lambda n, e, d: retries.append((n, d)))
    assert out == "done" and len(calls) == 3
    assert [n for n, _ in retries] == [1, 2]


def test_gives_up_after_attempts_and_reraises() -> None:
    calls: list[int] = []

    @retry(RetryPolicy(attempts=3, base_delay=0.001, deadline=None))
    def work() -> None:
        calls.append(1)
        raise pgerr.DeadlockDetected("deadlock detected")

    with pytest.raises(pgerr.DeadlockDetected):
        work()
    assert len(calls) == 3


def test_permanent_errors_are_not_retried() -> None:
    calls: list[int] = []

    def work() -> None:
        calls.append(1)
        raise pgerr.UniqueViolation("duplicate key")

    with pytest.raises(pgerr.UniqueViolation):
        call_with_retry(work, FAST)
    assert len(calls) == 1


class Flaky:
    """Fails once with `exc`, then succeeds."""

    def __init__(self, exc: BaseException) -> None:
        self.exc, self.calls = exc, 0

    def __call__(self) -> str:
        self.calls += 1
        if self.calls == 1:
            raise self.exc
        return "ok"


def test_connection_errors_retried_only_when_idempotent() -> None:
    lost = pgerr.AdminShutdown("terminating connection due to administrator command")
    work = Flaky(lost)
    with pytest.raises(pgerr.AdminShutdown):
        call_with_retry(work, FAST)  # the COMMIT may have happened: not ours to repeat
    assert work.calls == 1
    work = Flaky(lost)
    assert call_with_retry(work, FAST, idempotent=True) == "ok"
    assert work.calls == 2


def test_deadline_stops_retrying() -> None:
    calls: list[float] = []

    def work() -> None:
        calls.append(time.monotonic())
        raise pgerr.SerializationFailure()

    policy = RetryPolicy(attempts=100, base_delay=0.05, max_delay=0.05, deadline=0.3)
    started = time.monotonic()
    with pytest.raises(pgerr.SerializationFailure):
        call_with_retry(work, policy, on_retry=None)
    assert time.monotonic() - started < 0.4 and 2 <= len(calls) < 100


async def test_async_retry_and_cancellation() -> None:
    calls: list[int] = []

    async def work() -> int:
        calls.append(1)
        if len(calls) < 2:
            raise asyncpg.exceptions.SerializationError("could not serialize")
        return 42

    assert await acall_with_retry(work, FAST) == 42 and len(calls) == 2

    slow_calls: list[int] = []

    async def slow() -> None:
        slow_calls.append(1)
        await asyncio.sleep(10)

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await acall_with_retry(slow, FAST)
    assert len(slow_calls) == 1  # cancellation is not an error to retry


# --- real failures (committed data in a fresh database) --------------------------------------


def test_real_serialization_failure_is_retried(fresh_db: str) -> None:
    """Write skew under SERIALIZABLE: "a seller may run at most 2 promotions".

    Both transactions count 1 promotion, both insert one. Without SERIALIZABLE
    the seller would end up with 3. With it, one transaction fails with 40001;
    the retry re-reads (2 promotions now) and refuses.
    """
    with psycopg.connect(fresh_db, autocommit=True) as c:
        shop = make_shop(c, products=3)
        c.execute(
            "INSERT INTO promotions (product_id, discount_pct, active_during)"
            " VALUES (%s, 10, tstzrange(now(), NULL))",
            (shop.products[0],),
        )
    barrier = threading.Barrier(2)
    attempts: list[int] = []
    results: dict[int, str] = {}

    def add_promotion(product_id: int) -> str:
        attempts.append(product_id)
        with psycopg.connect(fresh_db) as c:
            c.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            n = c.execute(
                "SELECT count(*) FROM promotions JOIN products p ON p.id = product_id"
                " WHERE p.seller_id = %s",
                (shop.seller,),
            ).fetchone()[0]
            if len(attempts) <= 2:
                barrier.wait()  # first round: both have read the count
            if n >= 2:
                return "limit reached"
            c.execute(
                "INSERT INTO promotions (product_id, discount_pct, active_during)"
                " VALUES (%s, 15, tstzrange(now(), NULL))",
                (product_id,),
            )
            c.commit()
            return "added"

    def run(pid: int) -> None:
        results[pid] = call_with_retry(lambda: add_promotion(pid), FAST)

    threads = [threading.Thread(target=run, args=(p,)) for p in shop.products[1:]]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results.values()) == ["added", "limit reached"]
    assert len(attempts) == 3  # one transaction ran twice
    with psycopg.connect(fresh_db) as c:
        assert c.execute("SELECT count(*) FROM promotions").fetchone()[0] == 2


def test_real_deadlock_is_retried(fresh_db: str) -> None:
    """Two transactions lock two inventory rows in opposite orders."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        a, b = make_shop(c, products=2, stock=100).products
    barrier = threading.Barrier(2)
    attempts: list[int] = []

    def move(first: int, second: int) -> None:
        attempts.append(first)
        with psycopg.connect(fresh_db) as c:
            c.execute(
                "UPDATE inventory SET quantity = quantity - 1 WHERE product_id = %s", (first,)
            )
            if len(attempts) <= 2:
                barrier.wait()
            c.execute(
                "UPDATE inventory SET quantity = quantity + 1 WHERE product_id = %s", (second,)
            )
            c.commit()

    threads = [
        threading.Thread(target=call_with_retry, args=(lambda: move(a, b), FAST)),
        threading.Thread(target=call_with_retry, args=(lambda: move(b, a), FAST)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(attempts) == 3  # the deadlock victim ran again
    with psycopg.connect(fresh_db) as c:
        assert c.execute("SELECT sum(quantity) FROM inventory").fetchone()[0] == 200


def test_lock_timeout_is_retried(fresh_db: str) -> None:
    with psycopg.connect(fresh_db, autocommit=True) as c:
        pid = make_shop(c, products=1).products[0]
    holder = psycopg.connect(fresh_db)
    holder.execute("SELECT 1 FROM inventory WHERE product_id = %s FOR UPDATE", (pid,))
    threading.Timer(0.3, holder.commit).start()
    attempts: list[int] = []

    def work() -> int:
        attempts.append(1)
        with psycopg.connect(fresh_db) as c:
            c.execute("SET lock_timeout = '50ms'")
            c.execute("UPDATE inventory SET quantity = 7 WHERE product_id = %s", (pid,))
            c.commit()
            return len(attempts)

    policy = RetryPolicy(attempts=20, base_delay=0.05, max_delay=0.1, deadline=5)
    assert call_with_retry(work, policy, on_retry=None) >= 2
    holder.close()


def test_failover_style_disconnect(fresh_db: str, caplog: pytest.LogCaptureFixture) -> None:
    """pg_terminate_backend() is what a failover or a restart does to a session
    (SQLSTATE 57P01). With the pool's check on checkout the dead connection is
    replaced before anyone sees it; without, the work fails once, and an
    idempotent operation is retried on a fresh connection."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        pid = make_shop(c, products=1, stock=3).products[0]

    def kill_pool_connections(pool: psycopg_pool.ConnectionPool) -> None:
        with pool.connection() as conn:
            backend = conn.info.backend_pid
        with psycopg.connect(fresh_db, autocommit=True) as admin:
            admin.execute("SELECT pg_terminate_backend(%s)", (backend,))
        time.sleep(0.1)

    def read_stock(conn: psycopg.Connection) -> int:
        return conn.execute(
            "SELECT quantity FROM inventory WHERE product_id = %s", (pid,)
        ).fetchone()[0]

    for check in (True, False):
        caplog.clear()
        kw = {"check": psycopg_pool.ConnectionPool.check_connection} if check else {}
        with psycopg_pool.ConnectionPool(fresh_db, min_size=1, max_size=1, **kw) as pool:
            kill_pool_connections(pool)
            assert run_transaction(pool, read_stock, FAST, idempotent=True) == 3
        retried = [r for r in caplog.records if "retrying after connection" in r.getMessage()]
        assert len(retried) == (0 if check else 1), f"check={check}"


def test_insufficient_stock_is_not_retried(fresh_db: str) -> None:
    with psycopg.connect(fresh_db, autocommit=True) as c:
        shop = make_shop(c, products=1, stock=1, price=Decimal("5.00"))
    attempts: list[int] = []
    with psycopg_pool.ConnectionPool(fresh_db, min_size=1, max_size=2) as pool:

        def work(conn: psycopg.Connection) -> None:
            attempts.append(1)
            repo.place_order(conn, shop.buyer, [(shop.products[0], 2)])

        with pytest.raises(InsufficientStock):
            run_transaction(pool, work, FAST)
    assert len(attempts) == 1
