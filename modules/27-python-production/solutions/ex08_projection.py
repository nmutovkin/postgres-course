"""Exercise 8: a revenue projection fed by outbox events, exactly once in effect.

    cd project/python && python ../../modules/27-python-production/solutions/ex08_projection.py

In a fresh database (a template copy): 40 checkouts across 3 sellers write
orders + "order.placed" events; a relay publishes to a flaky "broker" that
delivers every third message twice; a consumer maintains seller_revenue_daily
with consume_once(). The projection must equal the aggregate of the orders.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import replace

import psycopg

from bazaar import migrate, outbox
from bazaar.config import get_settings
from bazaar.orm import services
from bazaar.orm.session import make_sessionmaker
from bazaar.sa.engine import create_engine
from bazaar.testing import TemplateDatabase, migrations_fingerprint

PROJECTION = """
CREATE TABLE seller_revenue_daily (
    seller_id bigint NOT NULL,
    day date NOT NULL,
    orders integer NOT NULL,
    revenue numeric(12,2) NOT NULL,
    PRIMARY KEY (seller_id, day)
)
"""


def apply_event(conn: psycopg.Connection, msg: outbox.Message) -> None:
    conn.execute(
        """
        INSERT INTO seller_revenue_daily (seller_id, day, orders, revenue)
        VALUES (%s, (%s AT TIME ZONE 'UTC')::date, 1, %s)
        ON CONFLICT (seller_id, day) DO UPDATE
        SET orders = seller_revenue_daily.orders + 1,
            revenue = seller_revenue_daily.revenue + EXCLUDED.revenue
        """,
        (msg.payload["seller_id"], msg.created_at, msg.payload["total"]),
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
    rng = random.Random(7)
    with tpl.clone(f"bazaar_ex08_{uuid.uuid4().hex[:8]}") as dsn:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute(PROJECTION)
            c.execute("INSERT INTO categories (name, slug) VALUES ('C', 'c')")
            for i in range(1, 4):
                c.execute("INSERT INTO users (email, full_name) VALUES (%s, 'U')", (f"u{i}@x.org",))
                c.execute(
                    "INSERT INTO sellers (user_id, shop_name, country_code) VALUES (%s, %s, 'DE')",
                    (i, f"Shop {i}"),
                )
                c.execute(
                    "INSERT INTO products (seller_id, category_id, sku, title, price, status)"
                    " VALUES (%s, 1, 'p', 'P', %s, 'active')",
                    (i, 10 * i),
                )
                c.execute("INSERT INTO inventory (product_id, quantity) VALUES (%s, 1000)", (i,))
        # 40 checkouts through the ORM service (orders + events in one transaction each)
        engine = create_engine(replace(get_settings(), dsn=dsn))
        Session = make_sessionmaker(engine)
        for _ in range(40):
            cart = [(pid, rng.randint(1, 3)) for pid in rng.sample([1, 2, 3], rng.randint(1, 3))]
            with Session.begin() as s:
                services.checkout(s, rng.randint(1, 3), cart)
        engine.dispose()

        # Relay -> flaky broker (every third message delivered twice) -> consumer
        delivered: list[outbox.Message] = []

        def broker(msg: outbox.Message) -> None:
            delivered.append(msg)
            if msg.id % 3 == 0:
                delivered.append(msg)  # a redelivery

        with psycopg.connect(dsn, autocommit=True) as c:
            while outbox.relay_batch(c, broker, limit=10):
                pass
            applied = sum(
                outbox.consume_once(c, "revenue", m.id, lambda conn, m=m: apply_event(conn, m))
                for m in delivered
            )
            print(
                f"events: {len({m.id for m in delivered})}, deliveries: {len(delivered)},"
                f" applied: {applied}, skipped as duplicates: {len(delivered) - applied}"
            )
            mismatches = c.execute(
                """
                SELECT o.seller_id, o.day, o.n, o.revenue, p.orders, p.revenue
                FROM (SELECT seller_id, (created_at AT TIME ZONE 'UTC')::date AS day,
                             count(*) AS n, sum(total) AS revenue
                      FROM orders GROUP BY 1, 2) o
                FULL JOIN seller_revenue_daily p USING (seller_id, day)
                WHERE o.n IS DISTINCT FROM p.orders OR o.revenue IS DISTINCT FROM p.revenue
                """
            ).fetchall()
            print("projection vs. orders, mismatches:", mismatches)
            print(
                c.execute(
                    "SELECT seller_id, orders, revenue FROM seller_revenue_daily ORDER BY seller_id"
                ).fetchall()
            )


if __name__ == "__main__":
    main()
