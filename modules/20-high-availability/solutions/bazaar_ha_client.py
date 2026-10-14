#!/usr/bin/env python3
"""bazaar_ha_client.py -- reference solution for the module 20 project milestone.

A Bazaar checkout client that survives a Patroni failover or switchover:

  * It connects with a libpq MULTI-HOST connection string and
    target_session_attrs=read-write, so it always lands on the current primary
    without any proxy in between.
  * Every checkout is ONE transaction that calls bazaar.place_order() and
    records an idempotency key (request_id) in ha_lab.checkout_requests.
  * If the transaction fails with a *retryable* error (connection lost, server
    shutting down, read-only node, serialization failure), the client
    reconnects and retries the SAME request_id with exponential backoff.
    Before placing the order again it checks whether that request_id already
    committed -- so an "unknown outcome" (commit sent, acknowledgement lost)
    is never turned into a duplicate order.

Run it from the lab's client container:

    docker exec m20-client python3 /lab/solutions/bazaar_ha_client.py --duration 60

At the end it verifies, on the current primary, that every acknowledged
checkout is present exactly once and that every order it created exists.
Exit status 0 = no lost and no duplicated checkouts.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
import uuid
from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb

DEFAULT_DSN = (
    "host=m20-pg1,m20-pg2,m20-pg3 port=5432 dbname=bazaar user=postgres password=postgres "
    "target_session_attrs=read-write connect_timeout=2 "
    # Detect a silently dead server (no RST, e.g. a partition) in seconds, not minutes.
    "keepalives=1 keepalives_idle=5 keepalives_interval=2 keepalives_count=2 tcp_user_timeout=5000 "
    "application_name=bazaar-ha-client"
)

SETUP = """
CREATE SCHEMA IF NOT EXISTS ha_lab;
CREATE TABLE IF NOT EXISTS ha_lab.checkout_requests (
    request_id  uuid        PRIMARY KEY,
    order_ids   bigint[]    NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
"""

# SQLSTATEs worth retrying after a reconnect.
RETRYABLE_SQLSTATES = {
    "57P01",  # admin_shutdown: server is shutting down / being demoted
    "57P02",  # crash_shutdown
    "57P03",  # cannot_connect_now: starting up / shutting down
    "25006",  # read_only_sql_transaction: we reached a node that is (now) a replica
    "40001",  # serialization_failure
    "40P01",  # deadlock_detected
}


def log(msg: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]}  {msg}", flush=True)


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, psycopg.OperationalError):  # connection refused / lost / timed out
        return True
    sqlstate = getattr(exc, "sqlstate", None)
    return bool(sqlstate) and (sqlstate in RETRYABLE_SQLSTATES or sqlstate.startswith("08"))


class Client:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.conn: psycopg.Connection | None = None
        self.server = "?"

    def connection(self) -> psycopg.Connection:
        if self.conn is None or self.conn.closed or self.conn.broken:
            self.conn = psycopg.connect(self.dsn)
            self.conn.execute("SET statement_timeout = '5s'")
            self.conn.commit()
            self.server = self.conn.info.host
        return self.conn

    def drop(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = None

    def checkout_once(self, request_id: uuid.UUID, user_id: int, items: list[dict]) -> tuple[list[int], bool]:
        """One attempt. Returns (order_ids, replayed). replayed=True means an
        earlier attempt of this request had already committed."""
        conn = self.connection()
        with conn.transaction():
            row = conn.execute(
                "SELECT order_ids FROM ha_lab.checkout_requests WHERE request_id = %s", (request_id,)
            ).fetchone()
            if row is not None:
                return list(row[0]), True
            order_ids = [
                r[0] for r in conn.execute("SELECT * FROM bazaar.place_order(%s, %s)", (user_id, Jsonb(items)))
            ]
            conn.execute(
                "INSERT INTO ha_lab.checkout_requests (request_id, order_ids) VALUES (%s, %s)",
                (request_id, order_ids),
            )
        return order_ids, False

    def checkout(self, request_id: uuid.UUID, user_id: int, items: list[dict], deadline_s: float) -> tuple[list[int], int, bool]:
        """Retry one checkout until it commits or the deadline passes.
        Returns (order_ids, attempts, replayed)."""
        start = time.monotonic()
        attempt = 0
        while True:
            attempt += 1
            try:
                ids, replayed = self.checkout_once(request_id, user_id, items)
                return ids, attempt, replayed
            except psycopg.Error as exc:
                if not is_retryable(exc):
                    self.rollback_quietly()
                    raise
                self.drop()
                elapsed = time.monotonic() - start
                if elapsed > deadline_s:
                    raise
                if attempt == 1:
                    log(f"request {str(request_id)[:8]}: {type(exc).__name__} ({getattr(exc, 'sqlstate', None)}), retrying")
                # Exponential backoff with jitter: 0.1, 0.2, 0.4 ... capped at 2 s.
                time.sleep(min(2.0, 0.1 * 2 ** (attempt - 1)) * random.uniform(0.5, 1.0))

    def rollback_quietly(self) -> None:
        try:
            if self.conn is not None and not self.conn.closed:
                self.conn.rollback()
        except Exception:
            self.drop()


def load_catalog(dsn: str, n_users: int, n_products: int) -> tuple[list[int], list[int]]:
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(SETUP)
        users = [r[0] for r in c.execute(
            "SELECT id FROM bazaar.users WHERE is_active ORDER BY id LIMIT %s", (n_users,))]
        products = [r[0] for r in c.execute(
            """SELECT p.id
               FROM bazaar.products p JOIN bazaar.inventory i ON i.product_id = p.id
               WHERE p.status = 'active' AND i.quantity - i.reserved >= 20
               ORDER BY i.quantity - i.reserved DESC, p.id
               LIMIT %s""", (n_products,))]
    if not users or not products:
        sys.exit("no active users or no products with stock: load Bazaar first (./scripts/load-bazaar.sh)")
    return users, products


def verify(dsn: str, acked: dict[uuid.UUID, list[int]]) -> int:
    for _ in range(60):
        try:
            with psycopg.connect(dsn, autocommit=True) as c:
                ids = list(acked)
                found = {r[0]: r[1] for r in c.execute(
                    "SELECT request_id, order_ids FROM ha_lab.checkout_requests WHERE request_id = ANY(%s)", (ids,))}
                all_orders = [o for v in acked.values() for o in v]
                n_orders = c.execute("SELECT count(*) FROM bazaar.orders WHERE id = ANY(%s)", (all_orders,)).fetchone()[0]
                host = c.info.host
            break
        except psycopg.OperationalError:
            time.sleep(1)
    else:
        print("verification failed: no primary reachable")
        return 2
    lost = [r for r in acked if r not in found]
    mismatched = [r for r in acked if r in found and list(found[r]) != acked[r]]
    print(f"verification on {host}:")
    print(f"  acknowledged checkouts:           {len(acked)}")
    print(f"  ... found in checkout_requests:   {len(acked) - len(lost)}")
    print(f"  ... LOST:                         {len(lost)}")
    print(f"  ... different order ids:          {len(mismatched)}")
    print(f"  orders created / found in orders: {len(all_orders)} / {n_orders}")
    return 0 if not lost and not mismatched and n_orders == len(all_orders) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--interval", type=float, default=0.05, help="pause between checkouts (s)")
    ap.add_argument("--retry-deadline", type=float, default=90.0, help="give up on one checkout after this long (s)")
    args = ap.parse_args()

    users, products = load_catalog(args.dsn, 500, 300)
    log(f"catalog: {len(users)} users, {len(products)} products; running {args.duration:.0f}s")

    client = Client(args.dsn)
    acked: dict[uuid.UUID, list[int]] = {}
    stats = {"retried": 0, "replayed": 0, "out_of_stock": 0, "gave_up": 0}
    last_server = None
    t0 = time.monotonic()
    while time.monotonic() - t0 < args.duration:
        request_id = uuid.uuid4()  # generated ONCE per logical checkout, reused by every retry
        user_id = random.choice(users)
        items = [{"product_id": p, "quantity": 1} for p in random.sample(products, k=random.randint(1, 3))]
        try:
            ids, attempts, replayed = client.checkout(request_id, user_id, items, args.retry_deadline)
        except psycopg.Error as exc:
            if getattr(exc, "sqlstate", None) == "BZ001":
                stats["out_of_stock"] += 1
                continue
            stats["gave_up"] += 1
            log(f"request {str(request_id)[:8]} FAILED for good: {exc}")
            continue
        acked[request_id] = ids
        if attempts > 1:
            stats["retried"] += 1
            log(f"request {str(request_id)[:8]} committed after {attempts} attempts on {client.server}"
                + (" (an earlier attempt had already committed: not placed twice)" if replayed else ""))
        if replayed:
            stats["replayed"] += 1
        if client.server != last_server:
            log(f"now writing to {client.server}")
            last_server = client.server
        time.sleep(args.interval)

    client.drop()
    print()
    print(f"checkouts acknowledged: {len(acked)}  retried: {stats['retried']}  "
          f"replayed (dedup by request_id): {stats['replayed']}  out of stock: {stats['out_of_stock']}  "
          f"gave up: {stats['gave_up']}")
    return verify(args.dsn, acked)


if __name__ == "__main__":
    sys.exit(main())
