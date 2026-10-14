"""The outbox end to end, inside one rolled-back transaction (module 27, section 6).

    cd project/python && python ../../modules/27-python-production/examples/outbox_demo.py

1. an idempotent checkout (orders + outbox events in one transaction), twice;
2. the relay turns the events into jobs (jobs_publisher);
3. a consumer handles a message twice; the second time is skipped.

Everything happens on one connection inside a transaction that is rolled back
at the end (the relay's and consumer's own transactions become savepoints),
so the reference database is left as it was.
"""

from __future__ import annotations

from bazaar import outbox
from bazaar.orm import services
from bazaar.orm.session import make_sessionmaker
from bazaar.sa.engine import create_engine


def main() -> None:
    engine = create_engine()
    with engine.connect() as sa_conn:
        sa_conn.begin()
        Session = make_sessionmaker(sa_conn, join_transaction_mode="create_savepoint")
        with Session() as session:
            cart = [(2, 1), (368, 2)]  # seller 5 and seller 2: two orders
            first = services.checkout(session, 8, cart, idempotency_key="demo-checkout-0001")
            again = services.checkout(session, 8, cart, idempotency_key="demo-checkout-0001")
            print("orders:", [(o.id, o.seller_id, str(o.total)) for o in first])
            same = [o.id for o in again] == [o.id for o in first]
            print("replay returned the same orders:", same)
            session.commit()  # releases the session's savepoint; the outer transaction goes on

        conn = sa_conn.connection.driver_connection  # the psycopg connection underneath
        rows = conn.execute(
            "SELECT id, topic, aggregate_id, payload FROM outbox WHERE published_at IS NULL"
            " ORDER BY id"
        ).fetchall()
        for r in rows:
            print("outbox:", r)

        routes = {"order.placed": ["send_order_confirmation", "notify_seller"]}
        n = outbox.relay_batch(conn, outbox.jobs_publisher(conn, routes))
        print(f"relay published {n} messages; pending now: {outbox.pending_count(conn)}")
        jobs = conn.execute(
            "SELECT kind, payload->>'order_id' FROM jobs WHERE payload ? 'message_id'"
            " ORDER BY id DESC LIMIT 4"
        ).fetchall()
        print("jobs:", sorted(jobs))

        handled: list[int] = []
        mid = rows[0][0]
        for delivery in (1, 2):  # the same message, delivered twice
            ran = outbox.consume_once(conn, "email", mid, lambda c: handled.append(mid))
            print(f"delivery {delivery} of message {mid}: handler ran = {ran}")
        sa_conn.rollback()
    engine.dispose()


if __name__ == "__main__":
    main()
