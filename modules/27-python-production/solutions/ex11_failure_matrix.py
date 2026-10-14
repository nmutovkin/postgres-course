"""Exercise 11: producer -> outbox -> relay -> broker -> consumer, with a crash
injected at every step, and an exactly-once effect at the end.

    cd project/python && python ../../modules/27-python-production/solutions/ex11_failure_matrix.py

The "broker" is in memory, with at-least-once semantics: a message stays in it
until the consumer acknowledges it, and is delivered again otherwise. The
consumer's effect is a row in emails_sent (no unique constraint on purpose:
duplicates would be visible). Crash points (each fires for some messages):

  P  producer: the business transaction fails after writing the event
  R1 relay: publish raises (broker unavailable)
  R2 relay: dies after publishing, before its COMMIT (pg_terminate_backend)
  C1 consumer: the handler fails after writing its effect, before COMMIT
  C2 consumer: commits, then dies before acknowledging the message
"""

from __future__ import annotations

import random
import uuid
from collections import Counter
from dataclasses import dataclass, field

import psycopg

from bazaar import migrate, outbox
from bazaar.config import get_settings
from bazaar.testing import TemplateDatabase, migrations_fingerprint

rng = random.Random(3)
crashes: Counter[str] = Counter()


def maybe(point: str, probability: float) -> bool:
    if rng.random() < probability:
        crashes[point] += 1
        return True
    return False


@dataclass
class Broker:
    unacked: dict[int, outbox.Message] = field(default_factory=dict)
    deliveries: int = 0

    def publish(self, msg: outbox.Message) -> None:
        if maybe("R1 publish raises", 0.15):
            raise ConnectionError("broker unavailable")
        self.unacked[msg.id] = msg  # a republish of the same id overwrites: still one copy

    def deliver(self) -> list[outbox.Message]:
        self.deliveries += len(self.unacked)
        return list(self.unacked.values())

    def ack(self, msg_id: int) -> None:
        self.unacked.pop(msg_id, None)


def produce(dsn: str, n: int) -> int:
    placed = 0
    with psycopg.connect(dsn, autocommit=True) as c:
        for _ in range(n):
            try:
                with c.transaction():
                    [order_id] = c.execute(
                        """SELECT place_order(1, '[{"product_id": 1, "quantity": 1}]', NULL)"""
                    ).fetchone()
                    outbox.record(c, "order.placed", "order", order_id, {"order_id": order_id})
                    if maybe("P  producer fails", 0.2):
                        raise RuntimeError("payment declined after the event was written")
                placed += 1
            except RuntimeError:
                pass
    return placed


def relay_round(dsn: str, broker: Broker) -> None:
    conn = psycopg.connect(dsn, autocommit=True)
    killer = psycopg.connect(dsn, autocommit=True)

    def publish(msg: outbox.Message) -> None:
        broker.publish(msg)
        if maybe("R2 relay dies before COMMIT", 0.1):
            killer.execute("SELECT pg_terminate_backend(%s)", (conn.info.backend_pid,))

    try:
        while outbox.relay_batch(conn, publish, limit=5):
            pass
    except psycopg.OperationalError:
        pass  # the relay process "crashed"; the next round is a new process
    finally:
        conn.close()
        killer.close()


def consume_round(dsn: str, broker: Broker) -> None:
    with psycopg.connect(dsn, autocommit=True) as c:
        for msg in broker.deliver():

            def send_email(conn: psycopg.Connection, m: outbox.Message = msg) -> None:
                conn.execute("INSERT INTO emails_sent (order_id) VALUES (%s)", (m.aggregate_id,))
                if maybe("C1 consumer fails before COMMIT", 0.15):
                    raise RuntimeError("SMTP timeout")

            try:
                outbox.consume_once(c, "email", msg.id, send_email)
            except RuntimeError:
                continue  # not acknowledged: the broker delivers it again
            if maybe("C2 consumer dies before ack", 0.15):
                continue
            broker.ack(msg.id)


def main() -> None:
    admin = get_settings().migration_dsn
    tpl = TemplateDatabase(
        admin,
        "bazaar_test_template",
        lambda dsn: migrate.upgrade(dsn),
        migrations_fingerprint(migrate.PROJECT_DIR / "migrations" / "versions"),
    )
    tpl.ensure()
    with tpl.clone(f"bazaar_ex11_{uuid.uuid4().hex[:8]}") as dsn:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute("CREATE TABLE emails_sent (order_id text NOT NULL)")
            c.execute("INSERT INTO users (email, full_name) VALUES ('b@x.org', 'B')")
            c.execute(
                "INSERT INTO sellers (user_id, shop_name, country_code) VALUES (1, 'S', 'DE')"
            )
            c.execute("INSERT INTO categories (name, slug) VALUES ('C', 'c')")
            c.execute(
                "INSERT INTO products (seller_id, category_id, sku, title, price, status)"
                " VALUES (1, 1, 'x', 'T', 5, 'active')"
            )
            c.execute("INSERT INTO inventory (product_id, quantity) VALUES (1, 10000)")
        broker = Broker()
        placed = produce(dsn, 200)
        rounds = 0
        with psycopg.connect(dsn, autocommit=True) as c:
            while outbox.pending_count(c) or broker.unacked:
                rounds += 1
                relay_round(dsn, broker)
                consume_round(dsn, broker)
            orders = c.execute("SELECT count(*) FROM orders").fetchone()[0]
            events = c.execute("SELECT count(*) FROM outbox").fetchone()[0]
            effects = c.execute(
                "SELECT count(*), count(DISTINCT order_id) FROM emails_sent"
            ).fetchone()
            missing = c.execute(
                "SELECT count(*) FROM orders o WHERE NOT EXISTS"
                " (SELECT FROM emails_sent e WHERE e.order_id = o.id::text)"
            ).fetchone()[0]
        print("crashes injected:", dict(sorted(crashes.items())))
        print(
            f"orders committed: {placed} (in the database: {orders}), events: {events},"
            f" broker deliveries: {broker.deliveries}, rounds: {rounds}"
        )
        print(
            f"emails: {effects[0]} for {effects[1]} distinct orders; orders without email: {missing}"
        )


if __name__ == "__main__":
    main()
