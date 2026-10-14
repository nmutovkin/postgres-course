"""Exercise 12: checkouts during repeated "failovers", with and without
idempotency keys.

    cd project/python && python ../../modules/27-python-production/solutions/ex12_failover_checkout.py

In a fresh database, a client places 150 checkouts while a "failover" thread
terminates every application connection every 50-150 ms. In addition, 10% of
the checkouts lose their reply after a successful COMMIT (the client sees a
connection error although the order exists): the unknown-outcome case.
The client retries connection errors as if the work were idempotent:
  run 1: without an idempotency key   (the retry is NOT actually idempotent)
  run 2: with an idempotency key per checkout
"""

from __future__ import annotations

import random
import threading
import time
import uuid
from dataclasses import replace

import psycopg

from bazaar import migrate
from bazaar.config import get_settings
from bazaar.orm import services
from bazaar.orm.session import make_sessionmaker
from bazaar.resilience import RetryPolicy, call_with_retry
from bazaar.sa.engine import create_engine
from bazaar.testing import TemplateDatabase, migrations_fingerprint

POLICY = RetryPolicy(attempts=10, base_delay=0.02, max_delay=0.5, deadline=30)


def failovers(dsn: str, stop: threading.Event, count: list[int]) -> None:
    rng = random.Random(5)
    with psycopg.connect(dsn, autocommit=True) as admin:
        while not stop.is_set():
            time.sleep(rng.uniform(0.05, 0.15))
            n = admin.execute(
                "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity"
                " WHERE datname = current_database() AND application_name = 'bazaar'"
            ).fetchone()[0]
            count[0] += n


def run(dsn: str, with_keys: bool) -> None:
    engine = create_engine(replace(get_settings(), dsn=dsn), pool_size=2)
    Session = make_sessionmaker(engine)
    rng = random.Random(11)
    retries = 0

    def count_retry(attempt: int, exc: BaseException, delay: float) -> None:
        nonlocal retries
        retries += 1

    stop, killed = threading.Event(), [0]
    killer = threading.Thread(target=failovers, args=(dsn, stop, killed))
    killer.start()
    acked: dict[str, list[int]] = {}
    try:
        for i in range(150):
            key = f"checkout-{i:04d}-{uuid.uuid4().hex[:8]}" if with_keys else None

            def attempt(key: str | None = key) -> list[int]:
                with Session.begin() as s:
                    ids = [
                        o.id
                        for o in services.checkout(
                            s, 1, [(1, 1), (2, 1)], idempotency_key=key, emit_events=False
                        )
                    ]
                if rng.random() < 0.10:  # the COMMIT happened, the reply got lost
                    raise psycopg.OperationalError("server closed the connection unexpectedly")
                return ids

            acked[f"request-{i}"] = call_with_retry(
                attempt, POLICY, idempotent=True, on_retry=count_retry
            )
    finally:
        stop.set()
        killer.join()
        engine.dispose()
    with psycopg.connect(dsn) as c:
        orders = c.execute("SELECT count(*) FROM orders").fetchone()[0]
        reserved = c.execute("SELECT sum(reserved) FROM inventory").fetchone()[0]
        c.execute("TRUNCATE orders CASCADE")
        c.execute("UPDATE inventory SET reserved = 0")
        c.commit()
    label = "with keys   " if with_keys else "without keys"
    print(
        f"{label}: 150 checkouts acknowledged, {retries} retries, {killed[0]} connections killed;"
        f" orders in the database: {orders} (expected 300), units reserved: {reserved}"
    )


def main() -> None:
    admin = get_settings().migration_dsn
    tpl = TemplateDatabase(
        admin,
        "bazaar_test_template",
        lambda dsn: migrate.upgrade(dsn),
        migrations_fingerprint(migrate.PROJECT_DIR / "migrations" / "versions"),
    )
    tpl.ensure()
    with tpl.clone(f"bazaar_ex12_{uuid.uuid4().hex[:8]}") as dsn:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute("INSERT INTO categories (name, slug) VALUES ('C', 'c')")
            for u in (1, 2, 3):  # user 1 buys; users 2 and 3 own the two shops
                c.execute("INSERT INTO users (email, full_name) VALUES (%s, 'U')", (f"u{u}@x.org",))
            for s in (1, 2):  # two sellers: each checkout creates two orders
                c.execute(
                    "INSERT INTO sellers (user_id, shop_name, country_code) VALUES (%s, %s, 'DE')",
                    (s + 1, f"S{s}"),
                )
                c.execute(
                    "INSERT INTO products (seller_id, category_id, sku, title, price, status)"
                    " VALUES (%s, 1, 'x', 'T', 5, 'active')",
                    (s,),
                )
                c.execute("INSERT INTO inventory (product_id, quantity) VALUES (%s, 100000)", (s,))
        run(dsn, with_keys=False)
        run(dsn, with_keys=True)


if __name__ == "__main__":
    main()
