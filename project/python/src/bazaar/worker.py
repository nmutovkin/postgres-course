"""An asyncio job worker for the module-10 queue (asyncpg).

    python -m bazaar.worker --queue default --concurrency 4

How it works:

* `concurrency` loops run as tasks in one TaskGroup. Each loop claims one job
  at a time with dequeue_jobs() (FOR UPDATE SKIP LOCKED, so loops and other
  worker processes never get the same job), runs its handler, and then calls
  complete_job() or fail_job() (which schedules a retry with backoff).
* Claiming is a short autocommitted statement: no transaction stays open
  while a handler runs. A lease (locked_at) protects the job instead; if the
  process dies, the lease expires and another worker picks the job up again.
  Delivery is therefore *at least once*: handlers must be idempotent.
* When the queue is empty, a loop sleeps until either a NOTIFY on the
  'jobs_ready' channel arrives (see project/python/sql/2301_notify.sql) or
  `poll_interval` passes. Polling is the safety net: notifications sent while
  the listener was disconnected are lost, rows in the jobs table are not.
* SIGINT/SIGTERM trigger a graceful shutdown: loops finish the job in hand and
  stop claiming new ones. If that takes longer than `grace` seconds, the
  remaining handlers are cancelled; their jobs are retried after the lease.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import socket
from collections.abc import Awaitable, Callable
from datetime import timedelta

import asyncpg

from bazaar.aio.db import asyncpg_connect, create_asyncpg_pool
from bazaar.config import get_settings
from bazaar.models import Job

log = logging.getLogger("bazaar.worker")

Handler = Callable[[Job, asyncpg.Pool], Awaitable[None]]
HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    """Register a coroutine function as the handler for jobs of `kind`."""

    def register(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return register


@handler("noop")
async def noop(job: Job, pool: asyncpg.Pool) -> None:
    """Does nothing; useful for tests and benchmarks."""


@handler("sleep")
async def sleep(job: Job, pool: asyncpg.Pool) -> None:
    """Simulates I/O-bound work (an HTTP call, an e-mail) of payload.seconds."""
    await asyncio.sleep(float(job.payload.get("seconds", 1)))


@handler("order_confirmation")
async def order_confirmation(job: Job, pool: asyncpg.Pool) -> None:
    """Would e-mail the buyer a summary of their order; here it only logs it."""
    row = await pool.fetchrow(
        """
        SELECT o.id, o.total, u.email, count(oi.*) AS items
        FROM orders o
        JOIN users u ON u.id = o.user_id
        LEFT JOIN order_items oi ON oi.order_id = o.id
        WHERE o.id = $1
        GROUP BY o.id, u.email
        """,
        int(job.payload["order_id"]),
    )
    if row is None:
        raise LookupError(f"order {job.payload['order_id']} not found")
    log.info("to %s: order %s, %s item(s), total %s", row["email"], row["id"], row["items"],
             row["total"])  # fmt: skip


class Worker:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        queue: str = "default",
        concurrency: int = 4,
        poll_interval: float = 5.0,
        job_timeout: float = 60.0,
        handlers: dict[str, Handler] | None = None,
        name: str | None = None,
    ) -> None:
        self.pool = pool
        self.queue = queue
        self.concurrency = concurrency
        self.poll_interval = poll_interval
        self.job_timeout = job_timeout
        self.handlers = HANDLERS if handlers is None else handlers
        self.name = name or f"{socket.gethostname()}:{os.getpid()}"
        # The lease must outlive the longest legitimate run of a handler.
        self.lease = timedelta(seconds=job_timeout + 30)
        self.processed = 0
        self.failed = 0
        self._stopping = False
        self._wakeups = [asyncio.Event() for _ in range(concurrency)]

    # -- lifecycle -------------------------------------------------------------

    @property
    def stopping(self) -> bool:
        return self._stopping

    def stop(self) -> None:
        """Ask all loops to finish their current job and exit."""
        self._stopping = True
        for ev in self._wakeups:
            ev.set()

    async def run(self) -> None:
        # LISTEN needs a dedicated connection that stays open: a pooled
        # connection goes back to the pool (and is reset) between uses.
        listener = await asyncpg_connect()
        await listener.add_listener("jobs_ready", self._on_notify)
        log.info("worker %s: queue=%s concurrency=%d", self.name, self.queue, self.concurrency)
        try:
            async with asyncio.TaskGroup() as tg:
                for n in range(self.concurrency):
                    tg.create_task(self._loop(n), name=f"worker-loop-{n}")
        finally:
            await listener.close()
            log.info("worker %s stopped: %d done, %d failed", self.name, self.processed,
                     self.failed)  # fmt: skip

    def _on_notify(self, conn: object, pid: int, channel: str, payload: str) -> None:
        if payload == self.queue:
            for ev in self._wakeups:
                ev.set()

    # -- the loop ----------------------------------------------------------------

    async def _loop(self, n: int) -> None:
        worker_id = f"{self.name}/{n}"
        wakeup = self._wakeups[n]
        while not self._stopping:
            wakeup.clear()  # clear BEFORE looking, so a NOTIFY that arrives meanwhile counts
            job = await self._claim(worker_id)
            if job is None:
                try:
                    await asyncio.wait_for(wakeup.wait(), self.poll_interval)
                except TimeoutError:
                    pass
                continue
            await self._process(job)

    async def _claim(self, worker_id: str) -> Job | None:
        row = await self.pool.fetchrow(
            "SELECT id, queue, kind, payload, attempts, max_attempts"
            " FROM dequeue_jobs($1, $2, 1, $3)",
            self.queue,
            worker_id,
            self.lease,
        )
        return Job(**dict(row)) if row is not None else None

    async def _process(self, job: Job) -> None:
        fn = self.handlers.get(job.kind)
        try:
            if fn is None:
                raise LookupError(f"no handler for job kind {job.kind!r}")
            async with asyncio.timeout(self.job_timeout):
                await fn(job, self.pool)
        except Exception as e:  # noqa: BLE001 - any handler error fails the job
            self.failed += 1
            log.warning("job %s (%s) failed on attempt %d: %r", job.id, job.kind, job.attempts, e)
            await self.pool.execute("SELECT fail_job($1, $2)", job.id, repr(e))
        else:
            self.processed += 1
            await self.pool.execute("SELECT complete_job($1)", job.id)


async def run_worker(queue: str, concurrency: int, poll_interval: float, grace: float) -> None:
    pool = await create_asyncpg_pool(min_size=1, max_size=concurrency + 1)
    worker = Worker(pool, queue=queue, concurrency=concurrency, poll_interval=poll_interval)
    run = asyncio.create_task(worker.run())
    loop = asyncio.get_running_loop()

    def on_signal() -> None:
        if worker.stopping:  # second Ctrl-C: do not wait any longer
            run.cancel()
            return
        log.info("shutting down: finishing jobs in progress (up to %.0fs)", grace)
        worker.stop()
        loop.call_later(grace, run.cancel)  # no-op if run has finished by then

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, on_signal)
    try:
        await run
    except asyncio.CancelledError:
        log.warning("in-flight jobs were cancelled; they are retried when their lease expires")
    finally:
        await pool.close()


def main(argv: list[str] | None = None) -> None:
    get_settings()  # fail early on a bad environment
    p = argparse.ArgumentParser(prog="python -m bazaar.worker", description=__doc__.split("\n")[0])
    p.add_argument("--queue", default="default")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--poll-interval", type=float, default=5.0)
    p.add_argument("--grace", type=float, default=30.0, help="seconds to finish jobs on shutdown")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run_worker(args.queue, args.concurrency, args.poll_interval, args.grace))


if __name__ == "__main__":
    main()
