"""The transactional outbox (module 27): events that exist if and only if the change did.

Producer side - in the SAME transaction as the business change:

    with conn.transaction():                       # psycopg
        order_ids = repo.place_order(conn, ...)
        outbox.record(conn, "order.placed", "order", order_ids[0], {"total": "12.50"})

    session.execute(outbox.insert_stmt(...))       # SQLAlchemy Session / Connection (sync or async)

Relay - a separate process (or task) that moves committed events onward:

    outbox.relay_batch(conn, publish)              # one batch, sync
    await outbox.run_relay(publish, stop)          # LISTEN outbox + polling, async

`publish(message)` delivers one message somewhere: a message broker, a
webhook, or the jobs table (jobs_publisher). The relay marks a message
published only after publish() returned, in the same transaction that locked
it; if the process dies in between, the message is published again later.
That is AT-LEAST-ONCE delivery, and the reason consumers must be idempotent:

Consumer side:

    outbox.consume_once(conn, "email", message_id, handler)   # skips duplicates

which records (consumer, message_id) in processed_messages in the same
transaction as the handler's effect: at-least-once delivery + an idempotent
consumer = an exactly-once *effect*.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
import sqlalchemy as sa
from psycopg.types.json import Jsonb

from bazaar.sa import tables

log = logging.getLogger("bazaar.outbox")

# NOTIFY payloads are limited to 8000 bytes; outbox payloads are not, but a
# message is meant to describe a change, not carry a document. Keep it small.
MAX_PAYLOAD_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class Message:
    id: int
    topic: str
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, Any]
    headers: dict[str, Any]
    created_at: datetime
    attempts: int


# --- producing ------------------------------------------------------------------------------

_INSERT = """
INSERT INTO outbox (topic, aggregate_type, aggregate_id, payload, headers)
VALUES (%s, %s, %s, %s, %s)
RETURNING id
"""


def _check(payload: dict[str, Any]) -> None:
    if len(json.dumps(payload, default=str)) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"outbox payload larger than {MAX_PAYLOAD_BYTES} bytes")


def record(
    conn: psycopg.Connection,
    topic: str,
    aggregate_type: str,
    aggregate_id: Any,
    payload: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
) -> int:
    """Add an event to the outbox in the caller's transaction; returns its id.

    Never commits: call it inside the transaction that makes the change.
    """
    payload = payload or {}
    _check(payload)
    row = conn.execute(
        _INSERT,
        (topic, aggregate_type, str(aggregate_id), Jsonb(payload), Jsonb(headers or {})),
    ).fetchone()
    assert row is not None
    return row[0]


async def arecord(
    conn: psycopg.AsyncConnection,
    topic: str,
    aggregate_type: str,
    aggregate_id: Any,
    payload: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
) -> int:
    payload = payload or {}
    _check(payload)
    cur = await conn.execute(
        _INSERT,
        (topic, aggregate_type, str(aggregate_id), Jsonb(payload), Jsonb(headers or {})),
    )
    row = await cur.fetchone()
    assert row is not None
    return row[0]


def insert_stmt(
    topic: str,
    aggregate_type: str,
    aggregate_id: Any,
    payload: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
) -> sa.Insert:
    """The same INSERT for SQLAlchemy: session.execute(insert_stmt(...)).scalar_one()."""
    payload = payload or {}
    _check(payload)
    return (
        sa.insert(tables.outbox)
        .values(
            topic=topic,
            aggregate_type=aggregate_type,
            aggregate_id=str(aggregate_id),
            payload=payload,
            headers=headers or {},
        )
        .returning(tables.outbox.c.id)
    )


# --- relaying ------------------------------------------------------------------------------

_CLAIM = """
SELECT id, topic, aggregate_type, aggregate_id, payload, headers, created_at, attempts
FROM outbox
WHERE published_at IS NULL
ORDER BY id
LIMIT %s
FOR UPDATE SKIP LOCKED
"""
_DONE = "UPDATE outbox SET published_at = now(), attempts = attempts + 1 WHERE id = ANY(%s)"
_FAILED = "UPDATE outbox SET attempts = attempts + 1, last_error = %s WHERE id = %s"

Publish = Callable[[Message], None]
APublish = Callable[[Message], Awaitable[None]]


def relay_batch(conn: psycopg.Connection, publish: Publish, limit: int = 100) -> int:
    """Publish up to `limit` unpublished messages in id order; returns how many.

    Runs in its own transaction on `conn` (which must not be in one). Rows are
    claimed with FOR UPDATE SKIP LOCKED, so several relays can run side by
    side without publishing the same message concurrently. On the first
    failure the batch stops (the rest keeps its order) and the error is
    recorded on the message.
    """
    done: list[int] = []
    with conn.transaction():
        for row in conn.execute(_CLAIM, (limit,)).fetchall():
            msg = Message(*row)
            try:
                with conn.transaction():  # a savepoint: a failing publish undoes only itself
                    publish(msg)
            except Exception as exc:
                log.warning("publishing outbox message %d failed: %s", msg.id, exc)
                if done:
                    conn.execute(_DONE, (done,))
                conn.execute(_FAILED, (f"{type(exc).__name__}: {exc}"[:1000], msg.id))
                return len(done)
            done.append(msg.id)
        if done:
            conn.execute(_DONE, (done,))
    return len(done)


async def arelay_batch(conn: psycopg.AsyncConnection, publish: APublish, limit: int = 100) -> int:
    """Async twin of relay_batch (psycopg AsyncConnection)."""
    done: list[int] = []
    async with conn.transaction():
        cur = await conn.execute(_CLAIM, (limit,))
        for row in await cur.fetchall():
            msg = Message(*row)
            try:
                async with conn.transaction():
                    await publish(msg)
            except Exception as exc:
                log.warning("publishing outbox message %d failed: %s", msg.id, exc)
                if done:
                    await conn.execute(_DONE, (done,))
                await conn.execute(_FAILED, (f"{type(exc).__name__}: {exc}"[:1000], msg.id))
                return len(done)
            done.append(msg.id)
        if done:
            await conn.execute(_DONE, (done,))
    return len(done)


async def run_relay(
    publish: APublish | None,
    stop: asyncio.Event,
    *,
    dsn: str | None = None,
    poll_interval: float = 5.0,
    batch_size: int = 100,
    make_publish: Callable[[psycopg.AsyncConnection], APublish] | None = None,
) -> int:
    """Relay until `stop` is set; returns the number of messages published.

    One connection LISTENs on 'outbox' (the INSERT trigger's wake-up call);
    another does the work. NOTIFY is only a hint: notifications sent while the
    listener was disconnected are lost, so the loop also polls every
    `poll_interval` seconds, and always drains the table completely.

    make_publish (module 28): build the publisher from the relay's working
    connection instead of passing one, e.g. ajobs_publisher, whose INSERTs
    must run in the relay's own transaction (`python -m bazaar.relay`).
    """
    from bazaar.aio import db as adb

    extra = {"conninfo": dsn} if dsn else {}
    total = 0
    wake = asyncio.Event()

    async def listen(conn: psycopg.AsyncConnection) -> None:
        async for _ in conn.notifies():
            wake.set()

    listener = await adb.connect(autocommit=True, **extra)
    worker = await adb.connect(autocommit=True, **extra)
    if make_publish is not None:
        publish = make_publish(worker)
    if publish is None:
        raise TypeError("run_relay needs publish or make_publish")
    await listener.execute("LISTEN outbox")
    listen_task = asyncio.create_task(listen(listener))
    try:
        while not stop.is_set():
            wake.clear()
            while not stop.is_set() and (n := await arelay_batch(worker, publish, batch_size)):
                total += n
            # Sleep until a NOTIFY arrives, the poll interval passes, or stop is set.
            waits = [asyncio.create_task(wake.wait()), asyncio.create_task(stop.wait())]
            await asyncio.wait(waits, timeout=poll_interval, return_when="FIRST_COMPLETED")
            for t in waits:
                t.cancel()
    finally:
        listen_task.cancel()
        await asyncio.gather(listen_task, return_exceptions=True)
        await listener.close()
        await worker.close()
    return total


def jobs_publisher(conn: psycopg.Connection, routes: dict[str, list[str]]) -> Publish:
    """A publisher that turns events into jobs (module 10's queue), in the relay's
    own transaction: the job and the "published" mark commit together, so in
    this one case delivery is exactly once. `routes` maps a topic to job kinds:
    {"order.placed": ["send_order_confirmation", "update_seller_stats"]}."""

    def publish(msg: Message) -> None:
        for kind in routes.get(msg.topic, []):
            conn.execute(
                "INSERT INTO jobs (queue, kind, payload) VALUES ('default', %s, %s)",
                (kind, Jsonb({"message_id": msg.id, **msg.payload})),
            )

    return publish


def ajobs_publisher(conn: psycopg.AsyncConnection, routes: dict[str, list[str]]) -> APublish:
    """Async twin of jobs_publisher (for run_relay(make_publish=...), module 28)."""

    async def publish(msg: Message) -> None:
        for kind in routes.get(msg.topic, []):
            await conn.execute(
                "INSERT INTO jobs (queue, kind, payload) VALUES ('default', %s, %s)",
                (kind, Jsonb({"message_id": msg.id, **msg.payload})),
            )

    return publish


def purge_published(conn: psycopg.Connection, older_than: str = "7 days") -> int:
    """Delete published messages older than `older_than` (an interval); returns the count."""
    cur = conn.execute(
        "DELETE FROM outbox WHERE published_at < now() - %s::interval", (older_than,)
    )
    return cur.rowcount


# --- consuming ------------------------------------------------------------------------------


def consume_once(
    conn: psycopg.Connection,
    consumer: str,
    message_id: int,
    handler: Callable[[psycopg.Connection], None],
) -> bool:
    """Run handler(conn) for this message unless `consumer` already did.

    The marker row and the handler's writes commit (or roll back) together.
    A duplicate delivery finds the marker and returns False without running
    the handler. Concurrent duplicates: the second INSERT waits for the first
    transaction's outcome on the primary key, then does nothing if it committed.
    """
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO processed_messages (consumer, message_id) VALUES (%s, %s)"
            " ON CONFLICT DO NOTHING RETURNING 1",
            (consumer, message_id),
        ).fetchone()
        if row is None:
            return False
        handler(conn)
        return True


def pending_count(conn: psycopg.Connection) -> int:
    """Unpublished messages: a metric to alert on (the relay is down or failing)."""
    row = conn.execute("SELECT count(*) FROM outbox WHERE published_at IS NULL").fetchone()
    return row[0] if row else 0
