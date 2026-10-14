"""Module 27: the transactional outbox, the relay, idempotent consumers,
and the idempotent checkout that writes to the outbox."""

from __future__ import annotations

import asyncio
import threading

import psycopg
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from bazaar import outbox
from bazaar.errors import IdempotencyConflict, InsufficientStock
from bazaar.orm import services
from tests.factories import make_product, make_seller

# Modules 26-27: these tests need the database at Alembic head (tests/conftest.py).
pytestmark = pytest.mark.migrated

# --- producing in the business transaction (rolled-back reference DB) ------------------------


def _events(session: Session, order_ids: list[int]) -> list[tuple]:
    return session.execute(
        sa.text(
            "SELECT topic, aggregate_id, payload->>'total', payload->>'seller_id', published_at"
            " FROM outbox WHERE aggregate_type = 'order' AND aggregate_id = ANY(:ids)"
            " ORDER BY id"
        ),
        {"ids": [str(i) for i in order_ids]},
    ).all()


def test_checkout_records_one_event_per_order(session: Session) -> None:
    conn = session.connection().connection.driver_connection
    seller_a, seller_b = make_seller(conn), make_seller(conn)
    pa = make_product(conn, seller_a, stock=3)
    pb = make_product(conn, seller_b, stock=3)
    orders = services.checkout(session, 8, [(pa, 2), (pb, 1)])
    assert len(orders) == 2  # one order per seller
    events = _events(session, [o.id for o in orders])
    assert [(e[0], e[1], e[2], e[3], e[4]) for e in events] == [
        (
            "order.placed",
            str(o.id),
            "20.00" if o.seller_id == seller_a else "10.00",
            str(o.seller_id),
            None,
        )
        for o in orders
    ]


def test_failed_checkout_records_nothing(session: Session) -> None:
    conn = session.connection().connection.driver_connection
    pid = make_product(conn, make_seller(conn), stock=1)
    before = session.scalar(sa.text("SELECT count(*) FROM outbox"))
    with pytest.raises(InsufficientStock):
        services.checkout(session, 8, [(pid, 2)])
    assert session.scalar(sa.text("SELECT count(*) FROM outbox")) == before


def test_idempotent_checkout_replays(session: Session) -> None:
    conn = session.connection().connection.driver_connection
    pid = make_product(conn, make_seller(conn), stock=5)
    first = services.checkout(session, 8, [(pid, 2)], idempotency_key="cart-8-0001")
    again = services.checkout(session, 8, [(pid, 2)], idempotency_key="cart-8-0001")
    assert [o.id for o in again] == [o.id for o in first]
    reserved = session.scalar(
        sa.text("SELECT reserved FROM inventory WHERE product_id = :p"), {"p": pid}
    )
    assert reserved == 2  # stock reserved once
    assert len(_events(session, [o.id for o in first])) == 1  # one event, not two
    with pytest.raises(IdempotencyConflict):
        services.checkout(session, 8, [(pid, 3)], idempotency_key="cart-8-0001")
    # Another user may use the same key: keys are per user.
    other = services.checkout(session, 11, [(pid, 1)], idempotency_key="cart-8-0001")
    assert other[0].id != first[0].id


def test_idempotency_key_length_is_checked(session: Session) -> None:
    conn = session.connection().connection.driver_connection
    pid = make_product(conn, make_seller(conn), stock=5)
    with pytest.raises(sa.exc.IntegrityError, match="orders_idempotency_key_length"):
        services.checkout(session, 8, [(pid, 1)], idempotency_key="short")


# --- the relay (committed data: a fresh database per test) -------------------------------------


@pytest.fixture
def fresh_conn(fresh_db: str):  # noqa: ANN201
    with psycopg.connect(fresh_db, autocommit=True) as c:
        yield c


def _add(conn: psycopg.Connection, n: int, topic: str = "order.placed") -> list[int]:
    with conn.transaction():
        return [outbox.record(conn, topic, "order", 1000 + i, {"n": i}) for i in range(n)]


def test_relay_publishes_in_order_once(fresh_conn: psycopg.Connection) -> None:
    ids = _add(fresh_conn, 5)
    seen: list[int] = []
    assert outbox.relay_batch(fresh_conn, lambda m: seen.append(m.id), limit=3) == 3
    assert outbox.relay_batch(fresh_conn, lambda m: seen.append(m.id), limit=3) == 2
    assert outbox.relay_batch(fresh_conn, lambda m: seen.append(m.id)) == 0
    assert seen == ids
    assert outbox.pending_count(fresh_conn) == 0


def test_relay_stops_at_first_failure(fresh_conn: psycopg.Connection) -> None:
    ids = _add(fresh_conn, 4)
    seen: list[int] = []

    def flaky(m: outbox.Message) -> None:
        if m.id == ids[2]:
            raise ConnectionError("broker unavailable")
        seen.append(m.id)

    assert outbox.relay_batch(fresh_conn, flaky) == 2
    row = fresh_conn.execute(
        "SELECT attempts, last_error, published_at FROM outbox WHERE id = %s", (ids[2],)
    ).fetchone()
    assert row == (1, "ConnectionError: broker unavailable", None)
    assert outbox.pending_count(fresh_conn) == 2  # the failed one and the one after it
    assert outbox.relay_batch(fresh_conn, lambda m: seen.append(m.id)) == 2
    assert seen == ids


def test_parallel_relays_never_publish_twice(fresh_db: str, fresh_conn: psycopg.Connection) -> None:
    _add(fresh_conn, 200)
    published: list[int] = []
    lock = threading.Lock()

    def relay() -> None:
        with psycopg.connect(fresh_db, autocommit=True) as c:
            while outbox.relay_batch(
                c, lambda m: (lock.acquire(), published.append(m.id), lock.release()), limit=7
            ):
                pass

    threads = [threading.Thread(target=relay) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(published) == 200 and len(set(published)) == 200


def test_jobs_publisher_is_exactly_once(fresh_conn: psycopg.Connection) -> None:
    """Publishing into the jobs table happens in the relay's transaction: the
    jobs and the published mark commit together, or neither does."""
    _add(fresh_conn, 3)
    routes = {"order.placed": ["send_confirmation", "update_stats"]}
    publish = outbox.jobs_publisher(fresh_conn, routes)
    assert outbox.relay_batch(fresh_conn, publish) == 3
    kinds = fresh_conn.execute(
        "SELECT kind, count(*) FROM jobs GROUP BY kind ORDER BY 1"
    ).fetchall()
    assert kinds == [("send_confirmation", 3), ("update_stats", 3)]

    # A publish that fails half-way (after inserting one job) leaves nothing behind.
    _add(fresh_conn, 1)
    inner = outbox.jobs_publisher(fresh_conn, routes)

    def broken(m: outbox.Message) -> None:
        inner(m)
        raise RuntimeError("crash after the first insert")

    assert outbox.relay_batch(fresh_conn, broken) == 0
    assert fresh_conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 6
    assert outbox.pending_count(fresh_conn) == 1


def test_at_least_once_delivery_and_idempotent_consumer(
    fresh_db: str, fresh_conn: psycopg.Connection
) -> None:
    """The relay publishes to a broker, then dies before its COMMIT: the broker
    has the message, the outbox still says unpublished, the next relay sends it
    again. The consumer's processed_messages row makes the duplicate harmless."""
    [mid] = _add(fresh_conn, 1)
    broker: list[outbox.Message] = []

    relay_conn = psycopg.connect(fresh_db, autocommit=True)

    def publish_then_die(m: outbox.Message) -> None:
        broker.append(m)
        fresh_conn.execute("SELECT pg_terminate_backend(%s)", (relay_conn.info.backend_pid,))

    with pytest.raises(psycopg.OperationalError):
        outbox.relay_batch(relay_conn, publish_then_die)
    relay_conn.close()
    assert outbox.pending_count(fresh_conn) == 1  # never marked published
    assert outbox.relay_batch(fresh_conn, broker.append) == 1
    assert [m.id for m in broker] == [mid, mid]  # delivered twice

    fresh_conn.execute("CREATE TABLE emails_sent (order_id text)")

    def send_email(c: psycopg.Connection, m: outbox.Message) -> None:
        c.execute("INSERT INTO emails_sent VALUES (%s)", (m.aggregate_id,))

    results = [
        outbox.consume_once(fresh_conn, "email", m.id, lambda c, m=m: send_email(c, m))
        for m in broker
    ]
    assert results == [True, False]
    assert fresh_conn.execute("SELECT count(*) FROM emails_sent").fetchone()[0] == 1


def test_consumer_failure_leaves_message_unprocessed(fresh_conn: psycopg.Connection) -> None:
    def boom(c: psycopg.Connection) -> None:
        raise RuntimeError("smtp down")

    with pytest.raises(RuntimeError):
        outbox.consume_once(fresh_conn, "email", 1, boom)
    assert outbox.consume_once(fresh_conn, "email", 1, lambda c: None) is True


def test_purge_published(fresh_conn: psycopg.Connection) -> None:
    _add(fresh_conn, 3)
    outbox.relay_batch(fresh_conn, lambda m: None)
    fresh_conn.execute("UPDATE outbox SET published_at = now() - interval '8 days'")
    _add(fresh_conn, 1)
    assert outbox.purge_published(fresh_conn, "7 days") == 3
    assert outbox.pending_count(fresh_conn) == 1


async def test_run_relay_wakes_up_on_notify(fresh_db: str) -> None:
    """With a 30 s poll interval, only the NOTIFY from the INSERT trigger can
    explain a delivery within a second."""
    published: list[int] = []
    got_one = asyncio.Event()
    stop = asyncio.Event()

    async def publish(m: outbox.Message) -> None:
        published.append(m.id)
        got_one.set()

    task = asyncio.create_task(outbox.run_relay(publish, stop, dsn=fresh_db, poll_interval=30))
    await asyncio.sleep(0.3)  # the relay is idle, waiting
    async with await psycopg.AsyncConnection.connect(fresh_db, autocommit=True) as c:
        mid = await outbox.arecord(c, "order.placed", "order", 7, {"x": 1})
    async with asyncio.timeout(2):
        await got_one.wait()
    stop.set()
    assert await asyncio.wait_for(task, 5) == 1
    assert published == [mid]
