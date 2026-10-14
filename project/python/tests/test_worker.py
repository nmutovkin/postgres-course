"""Module 23 milestone: the async job worker (bazaar.worker)."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator

import asyncpg
import pytest

from bazaar.aio.db import create_asyncpg_pool
from bazaar.models import Job
from bazaar.worker import HANDLERS, Worker


@pytest.fixture
async def pool() -> AsyncIterator[asyncpg.Pool]:
    p = await create_asyncpg_pool(min_size=1, max_size=6)
    yield p
    await p.close()


@pytest.fixture
async def queue(pool: asyncpg.Pool) -> AsyncIterator[str]:
    """A fresh queue name per test; its jobs are deleted afterwards."""
    name = f"test-{uuid.uuid4().hex[:8]}"
    yield name
    await pool.execute("DELETE FROM jobs WHERE queue = $1", name)


async def enqueue(pool: asyncpg.Pool, queue: str, kind: str, n: int, payload: dict) -> None:
    await pool.execute(
        "INSERT INTO jobs (queue, kind, payload) SELECT $1, $2, $3 FROM generate_series(1, $4)",
        queue,
        kind,
        payload,
        n,
    )


async def wait_until_drained(pool: asyncpg.Pool, queue: str, timeout: float = 10) -> None:
    async with asyncio.timeout(timeout):
        while await pool.fetchval(
            "SELECT count(*) FROM jobs WHERE queue = $1 AND done_at IS NULL AND attempts = 0",
            queue,
        ):
            await asyncio.sleep(0.05)
        while await pool.fetchval(
            "SELECT count(*) FROM jobs WHERE queue = $1 AND locked_at IS NOT NULL", queue
        ):
            await asyncio.sleep(0.05)


async def test_each_job_runs_exactly_once(pool: asyncpg.Pool, queue: str) -> None:
    seen: Counter[int] = Counter()

    async def record(job: Job, pool: asyncpg.Pool) -> None:
        seen[job.id] += 1
        await asyncio.sleep(0.005)

    await enqueue(pool, queue, "record", 200, {})
    await enqueue(pool, queue, "no-such-kind", 1, {})
    worker = Worker(pool, queue=queue, concurrency=4, poll_interval=0.2,
                    handlers={"record": record})  # fmt: skip
    run = asyncio.create_task(worker.run())
    await wait_until_drained(pool, queue)
    worker.stop()
    await run

    assert len(seen) == 200 and set(seen.values()) == {1}
    assert (worker.processed, worker.failed) == (200, 1)
    failed = await pool.fetchrow(
        "SELECT attempts, last_error, run_at > now() AS backoff FROM jobs"
        " WHERE queue = $1 AND kind = 'no-such-kind'",
        queue,
    )
    assert failed["attempts"] == 1 and "no handler" in failed["last_error"] and failed["backoff"]
    # every loop took part
    loops = await pool.fetchval(
        "SELECT count(DISTINCT locked_by) FROM jobs WHERE queue = $1 AND done_at IS NOT NULL",
        queue,
    )
    assert loops == 4


async def test_notify_wakes_idle_worker(pool: asyncpg.Pool, queue: str) -> None:
    worker = Worker(pool, queue=queue, concurrency=2, poll_interval=30, handlers=HANDLERS)
    run = asyncio.create_task(worker.run())
    await asyncio.sleep(0.3)  # both loops find nothing and go to sleep for 30 s
    t0 = time.perf_counter()
    await enqueue(pool, queue, "noop", 1, {})
    async with asyncio.timeout(5):
        while worker.processed < 1:
            await asyncio.sleep(0.01)
    latency = time.perf_counter() - t0
    worker.stop()
    await run
    assert latency < 1.0  # woken by NOTIFY, not by the 30 s poll


async def test_graceful_stop_finishes_job_in_hand(pool: asyncpg.Pool, queue: str) -> None:
    await enqueue(pool, queue, "sleep", 3, {"seconds": 0.5})
    worker = Worker(pool, queue=queue, concurrency=1, poll_interval=0.1, handlers=HANDLERS)
    run = asyncio.create_task(worker.run())
    await asyncio.sleep(0.2)  # first job is running
    worker.stop()
    await run
    done = await pool.fetchval(
        "SELECT count(*) FROM jobs WHERE queue = $1 AND done_at IS NOT NULL", queue
    )
    untouched = await pool.fetchval(
        "SELECT count(*) FROM jobs WHERE queue = $1 AND attempts = 0", queue
    )
    assert (done, untouched) == (1, 2)


async def test_cancelled_job_keeps_its_lease(pool: asyncpg.Pool, queue: str) -> None:
    await enqueue(pool, queue, "sleep", 1, {"seconds": 10})
    worker = Worker(pool, queue=queue, concurrency=1, poll_interval=0.1, handlers=HANDLERS)
    run = asyncio.create_task(worker.run())
    await asyncio.sleep(0.3)
    run.cancel()  # a hard shutdown: the handler never finishes
    with pytest.raises(asyncio.CancelledError):
        await run
    row = await pool.fetchrow(
        "SELECT attempts, locked_at IS NOT NULL AS leased, done_at FROM jobs WHERE queue = $1",
        queue,
    )
    # Not done, still leased: after the lease expires another worker retries it.
    assert (row["attempts"], row["leased"], row["done_at"]) == (1, True, None)
