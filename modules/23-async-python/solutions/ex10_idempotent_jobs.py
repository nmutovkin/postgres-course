"""Exercise 10: an idempotent job handler under at-least-once delivery.

Creates (and drops) a table receipts_sent and a queue 'ex10-receipts'.
"""

import asyncio
import uuid

import asyncpg

from bazaar.aio.db import create_asyncpg_pool
from bazaar.models import Job
from bazaar.worker import Worker

QUEUE = f"ex10-{uuid.uuid4().hex[:6]}"
outbox: list[tuple[int, str]] = []  # stands in for the e-mail provider
flaky = {"fail_next_send": True}


async def send_email(order_id: int, to: str) -> None:
    if flaky["fail_next_send"]:
        flaky["fail_next_send"] = False
        raise ConnectionError("mail server timeout")  # the first send attempt fails
    outbox.append((order_id, to))


async def send_receipt(job: Job, pool: asyncpg.Pool) -> None:
    order_id = int(job.payload["order_id"])
    async with pool.acquire() as conn, conn.transaction():
        # Claim the idempotency key. A second delivery of the same receipt (a
        # duplicate job, or a retry after a crash) finds the key and does nothing.
        claimed = await conn.fetchval(
            "INSERT INTO receipts_sent (order_id) VALUES ($1)"
            " ON CONFLICT (order_id) DO NOTHING RETURNING true",
            order_id,
        )
        if not claimed:
            return
        email = await conn.fetchval(
            "SELECT u.email FROM orders o JOIN users u ON u.id = o.user_id WHERE o.id = $1", order_id
        )
        # Send INSIDE the transaction: if sending fails, the key is rolled back
        # and the retry sends again. (If the COMMIT itself failed after a
        # successful send, a retry would send twice; passing order_id as the
        # provider's idempotency key closes that last gap.)
        await send_email(order_id, email)


async def drain(pool: asyncpg.Pool, worker: Worker) -> None:
    async with asyncio.timeout(10):
        while await pool.fetchval(
            "SELECT count(*) FROM jobs WHERE queue = $1 AND done_at IS NULL"
            " AND run_at <= now() AND attempts < max_attempts",
            QUEUE,
        ):
            await asyncio.sleep(0.05)


async def main() -> None:
    pool = await create_asyncpg_pool(min_size=2, max_size=6)
    await pool.execute(
        "CREATE TABLE receipts_sent (order_id bigint PRIMARY KEY, sent_at timestamptz NOT NULL DEFAULT now())"
    )
    try:
        # Order 500000 is enqueued three times (a double-click and a retried API call...).
        await pool.executemany(
            "INSERT INTO jobs (queue, kind, payload) VALUES ($1, 'send_receipt', $2)",
            [(QUEUE, {"order_id": oid}) for oid in (500000, 500000, 499990, 500000)],
        )
        worker = Worker(pool, queue=QUEUE, concurrency=4, poll_interval=0.1,
                        handlers={"send_receipt": send_receipt})  # fmt: skip
        run = asyncio.create_task(worker.run())
        await drain(pool, worker)
        # One job failed (the flaky send) and waits for its backoff; fast-forward it.
        retry = await pool.execute(
            "UPDATE jobs SET run_at = now() WHERE queue = $1 AND done_at IS NULL", QUEUE
        )
        print("jobs waiting for a retry:", retry.split()[-1])
        await drain(pool, worker)
        await asyncio.sleep(0.2)
        worker.stop()
        await run
        print(f"handler runs: {worker.processed} ok, {worker.failed} failed")
        print("e-mails sent:", sorted(outbox))
        assert sorted(o for o, _ in outbox) == [499990, 500000]
    finally:
        await pool.execute("DROP TABLE receipts_sent")
        await pool.execute("DELETE FROM jobs WHERE queue = $1", QUEUE)
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
