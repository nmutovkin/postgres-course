"""Error classification and retries for database work (module 27).

    from bazaar.resilience import classify, ErrorKind, RetryPolicy, retry, run_transaction

    @retry(RetryPolicy(attempts=5))                 # sync; `aretry` for coroutines
    def transfer(pool, ...):
        with pool.connection() as conn, conn.transaction():
            ...

    order_ids = run_transaction(pool, lambda conn: repo.place_order(conn, ...))
    orders = run_in_session(Session, lambda s: services.checkout(s, ...))

Rules this module encodes (module 27, section 3):

1. Retry WHOLE transactions, never single statements: after 40001 the
   transaction is aborted, and everything it read is stale. The helpers that
   own the transaction (run_transaction, run_in_session, and their async twins)
   open a fresh one for every attempt.
2. Retry only errors that say "try again": serialization failures and
   deadlocks (the server rolled you back), lock timeouts, and lost or refused
   connections. Never constraint violations, syntax errors, permission errors,
   or business errors (BZ001 ...).
3. A connection lost during COMMIT leaves the outcome UNKNOWN: the commit may
   have happened. Such errors are retried only for idempotent work (an
   idempotency key, an upsert, a read), i.e. when the caller says so.
4. Back off exponentially, with full jitter, within a total time budget, so
   that a crowd of clients that failed together does not retry together.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ParamSpec, TypeVar

log = logging.getLogger("bazaar.resilience")

P = ParamSpec("P")
T = TypeVar("T")


class ErrorKind(StrEnum):
    SERIALIZATION = "serialization"  # 40001 serialization_failure, 40P01 deadlock_detected
    LOCK_TIMEOUT = "lock_timeout"  # 55P03 lock_not_available (lock_timeout, NOWAIT)
    CONNECTION = "connection"  # lost/refused connection, server shutting down (failover)
    READ_ONLY = "read_only"  # 25006: wrote to a standby (stale DNS/pool after failover)
    OVERLOADED = "overloaded"  # too many connections, out of memory/disk, pool exhausted
    TIMEOUT = "timeout"  # 57014 statement_timeout / cancel, client-side timeouts
    PERMANENT = "permanent"  # everything else: retrying cannot help


# SQLSTATE -> kind. Classes (the first two characters) are handled below.
_SQLSTATES: dict[str, ErrorKind] = {
    "40001": ErrorKind.SERIALIZATION,
    "40P01": ErrorKind.SERIALIZATION,
    "55P03": ErrorKind.LOCK_TIMEOUT,
    "57P01": ErrorKind.CONNECTION,  # admin_shutdown: fast shutdown, failover, pg_terminate_backend
    "57P02": ErrorKind.CONNECTION,  # crash_shutdown
    "57P03": ErrorKind.CONNECTION,  # cannot_connect_now: starting up / in recovery
    "57P05": ErrorKind.CONNECTION,  # idle_session_timeout
    "25006": ErrorKind.READ_ONLY,
    "25P03": ErrorKind.CONNECTION,  # idle_in_transaction_session_timeout: session is gone
    "25P04": ErrorKind.TIMEOUT,  # transaction_timeout (PG 17): session is gone too
    "57014": ErrorKind.TIMEOUT,  # query_canceled
    "53300": ErrorKind.OVERLOADED,  # too_many_connections
    "53200": ErrorKind.OVERLOADED,  # out_of_memory
    "53100": ErrorKind.OVERLOADED,  # disk_full
}


def _unwrap(exc: BaseException) -> BaseException:
    """SQLAlchemy wraps driver errors (DBAPIError.orig); look at the driver's."""
    orig = getattr(exc, "orig", None)
    return orig if isinstance(orig, BaseException) else exc


def sqlstate(exc: BaseException) -> str | None:
    """The SQLSTATE of a psycopg, asyncpg or SQLAlchemy-wrapped error, if any."""
    return getattr(_unwrap(exc), "sqlstate", None)


def classify(exc: BaseException) -> ErrorKind:
    """What kind of failure `exc` is, for psycopg, asyncpg and SQLAlchemy errors."""
    inner = _unwrap(exc)
    code = getattr(inner, "sqlstate", None)
    if code:
        if code in _SQLSTATES:
            return _SQLSTATES[code]
        if code.startswith("08"):  # connection_exception class
            return ErrorKind.CONNECTION
        return ErrorKind.PERMANENT
    name = type(inner).__name__
    module = type(inner).__module__
    # Pool exhaustion: demand exceeds capacity. Retrying adds load; shed it.
    if name == "PoolTimeout" or (module.startswith("sqlalchemy") and name == "TimeoutError"):
        return ErrorKind.OVERLOADED
    # A dropped connection without a server message (psycopg OperationalError
    # "server closed the connection unexpectedly", asyncpg's
    # ConnectionDoesNotExistError, SQLAlchemy's invalidated connections).
    if getattr(exc, "connection_invalidated", False):
        return ErrorKind.CONNECTION
    mro = {c.__name__ for c in type(inner).__mro__}
    if mro & {
        "OperationalError",  # psycopg (incl. ConnectionTimeout), DB-API in general
        "InterfaceError",  # "connection is closed" and asyncpg's connection errors
        "ConnectionDoesNotExistError",
        "ConnectionFailureError",
    }:
        return ErrorKind.CONNECTION
    if isinstance(inner, TimeoutError):  # asyncio.timeout(); before OSError (a subclass)
        return ErrorKind.TIMEOUT
    if isinstance(inner, ConnectionError | OSError):
        return ErrorKind.CONNECTION
    return ErrorKind.PERMANENT


@dataclass(frozen=True)
class RetryPolicy:
    """How to retry.

    attempts:    total tries, including the first
    base_delay:  first backoff ceiling (seconds); doubles each attempt
    max_delay:   cap of a single backoff
    deadline:    total time budget in seconds (None: unlimited); no retry is
                 started that would end after it (e.g. the request's deadline)
    retry_on:    error kinds retried for any work
    idempotent_retry_on: kinds retried only when the work is idempotent
                 (the outcome of a commit interrupted by a lost connection is unknown)
    """

    attempts: int = 5
    base_delay: float = 0.05
    max_delay: float = 2.0
    deadline: float | None = 10.0
    retry_on: frozenset[ErrorKind] = field(
        default_factory=lambda: frozenset({ErrorKind.SERIALIZATION, ErrorKind.LOCK_TIMEOUT})
    )
    idempotent_retry_on: frozenset[ErrorKind] = field(
        default_factory=lambda: frozenset({ErrorKind.CONNECTION, ErrorKind.READ_ONLY})
    )

    def backoff(self, attempt: int) -> float:
        """Full jitter: uniform(0, min(max_delay, base_delay * 2**(attempt-1))).

        Attempt 1 failed -> wait up to base_delay; attempt 2 -> 2x; ... Random
        spreading matters more than the exact curve: clients that collided
        once must not collide again in lockstep.
        """
        return random.uniform(0, min(self.max_delay, self.base_delay * 2 ** (attempt - 1)))

    def should_retry(self, exc: BaseException, *, idempotent: bool) -> bool:
        kind = classify(exc)
        return kind in self.retry_on or (idempotent and kind in self.idempotent_retry_on)


DEFAULT_POLICY = RetryPolicy()

OnRetry = Callable[[int, BaseException, float], None]


def _log_retry(attempt: int, exc: BaseException, delay: float) -> None:
    log.warning(
        "retrying after %s (attempt %d, sqlstate %s): sleeping %.3fs",
        classify(exc),
        attempt,
        sqlstate(exc),
        delay,
        extra={"retry_attempt": attempt, "error_kind": str(classify(exc)), "delay_s": delay},
    )


def _next_delay(
    policy: RetryPolicy, attempt: int, exc: BaseException, idempotent: bool, started: float
) -> float | None:
    """Seconds to wait before the next attempt, or None to give up."""
    if attempt >= policy.attempts or not policy.should_retry(exc, idempotent=idempotent):
        return None
    delay = policy.backoff(attempt)
    if policy.deadline is not None and time.monotonic() - started + delay > policy.deadline:
        return None
    return delay


def call_with_retry[T](
    fn: Callable[[], T],
    policy: RetryPolicy = DEFAULT_POLICY,
    *,
    idempotent: bool = False,
    on_retry: OnRetry | None = _log_retry,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call fn() until it succeeds, or the policy says stop (then re-raise)."""
    started = time.monotonic()
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except Exception as exc:
            delay = _next_delay(policy, attempt, exc, idempotent, started)
            if delay is None:
                raise
            if on_retry:
                on_retry(attempt, exc, delay)
            sleep(delay)


async def acall_with_retry[T](
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy = DEFAULT_POLICY,
    *,
    idempotent: bool = False,
    on_retry: OnRetry | None = _log_retry,
) -> T:
    """Async twin of call_with_retry. Cancellation is never retried: CancelledError
    is a BaseException, not an Exception, and propagates at once."""
    started = time.monotonic()
    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn()
        except Exception as exc:
            delay = _next_delay(policy, attempt, exc, idempotent, started)
            if delay is None:
                raise
            if on_retry:
                on_retry(attempt, exc, delay)
            await asyncio.sleep(delay)


def retry(
    policy: RetryPolicy = DEFAULT_POLICY, *, idempotent: bool = False
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorator form. The decorated function must own its transaction (open
    and commit it itself): retrying a function that runs inside a caller's
    transaction would retry inside an aborted transaction."""

    def deco(fn: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            return call_with_retry(lambda: fn(*args, **kwargs), policy, idempotent=idempotent)

        return wrapper

    return deco


def aretry(
    policy: RetryPolicy = DEFAULT_POLICY, *, idempotent: bool = False
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    def deco(fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            return await acall_with_retry(
                lambda: fn(*args, **kwargs), policy, idempotent=idempotent
            )

        return wrapper

    return deco


# --- helpers that own the transaction --------------------------------------------------


def run_transaction[T](
    pool: Any,
    work: Callable[[Any], T],
    policy: RetryPolicy = DEFAULT_POLICY,
    *,
    idempotent: bool = False,
) -> T:
    """work(conn) in a transaction on a connection from a psycopg ConnectionPool,
    retried as a whole. A connection that failed is discarded by the pool (it
    checks connections on return), so a retry gets a healthy one."""

    def attempt() -> T:
        with pool.connection() as conn, conn.transaction():
            return work(conn)

    return call_with_retry(attempt, policy, idempotent=idempotent)


async def arun_transaction[T](
    pool: Any,
    work: Callable[[Any], Awaitable[T]],
    policy: RetryPolicy = DEFAULT_POLICY,
    *,
    idempotent: bool = False,
) -> T:
    """Async twin for psycopg's AsyncConnectionPool and asyncpg pools."""

    async def attempt() -> T:
        if hasattr(pool, "acquire"):  # asyncpg
            async with pool.acquire() as conn, conn.transaction():
                return await work(conn)
        async with pool.connection() as conn, conn.transaction():
            return await work(conn)

    return await acall_with_retry(attempt, policy, idempotent=idempotent)


def run_in_session[T](
    factory: Any,
    work: Callable[[Any], T],
    policy: RetryPolicy = DEFAULT_POLICY,
    *,
    idempotent: bool = False,
) -> T:
    """work(session) in `with factory.begin() as session:` (a SQLAlchemy
    sessionmaker), retried as a whole: every attempt gets a new Session, so no
    stale objects from a failed attempt leak into the next one."""

    def attempt() -> T:
        with factory.begin() as session:
            return work(session)

    return call_with_retry(attempt, policy, idempotent=idempotent)


async def arun_in_session[T](
    factory: Any,
    work: Callable[[Any], Awaitable[T]],
    policy: RetryPolicy = DEFAULT_POLICY,
    *,
    idempotent: bool = False,
) -> T:
    """Async twin for async_sessionmaker."""

    async def attempt() -> T:
        async with factory.begin() as session:
            return await work(session)

    return await acall_with_retry(attempt, policy, idempotent=idempotent)
