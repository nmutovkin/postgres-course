"""A small async load generator for the Bazaar service, with invariant checks.

    # terminal 1: the service (4 processes), the relay, the job worker
    BAZAAR_POOL_MAX_SIZE=8 uvicorn bazaar.api.app:app --workers 4 --log-level warning
    python -m bazaar.relay
    python -m bazaar.worker --concurrency 4

    # terminal 2
    python modules/28-capstone/examples/loadtest.py --duration 30 --shoppers 40 --browsers 40

What it does:

1. setup (direct SQL): a load-test seller with --products "hot" products of
   --stock units each, and --buyers fresh users with a default address (fresh,
   so that no pre-existing cart rows take part);
2. for --duration seconds, two crowds at once:
   * shoppers: pick a buyer, put 1-3 hot products in the cart (1-2 units
     each), POST /checkout with a fresh Idempotency-Key; on 409 (sold out) drop
     that product from the cart and try again, at most 3 times. 10% of
     checkouts are sent twice with the same key, like a client retrying after
     a timeout: the second answer must be a replay of the first;
   * browsers: GET /products (random root category, random sort, sometimes the
     next page), GET /products/{id}, GET /search?q=<random term>;
3. report: requests/s, p50/p95/p99 per endpoint, status codes;
4. invariants, checked in the database after the relay and worker had time to
   drain (--drain seconds):
   * no overselling: for every hot product reserved <= quantity, and
     reserved == the units in this run's orders;
   * order totals == sum(quantity * unit_price) of their items;
   * one order set per idempotency key; replays returned the same order ids;
   * buyers who checked out successfully have an empty cart... unless they
     added items after their last checkout (we check only those who did not);
   * the outbox is drained (no unpublished rows) and every order.placed job is done.
5. cleanup (unless --keep): the run's orders, jobs, outbox rows, products, users.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import statistics
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import httpx
import psycopg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
TERMS = [
    "wireless headphones", "headphnes", "chef knife", "lumiere", "creme", "gaming laptop",
    "silent witness", "running shoes", "coffee", "jeans", "lamp", "puzzle", "garden hose",
    "notebok", "\"power bank\"", "yoga -mat", "kettle", "board game",
]  # fmt: skip
ROOTS = [1, 8, 14, 21, 55, 63, 67]


@dataclass
class Stats:
    latencies: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    codes: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    orders: dict[str, list[int]] = field(default_factory=dict)  # key -> order ids (201)
    replays: list[tuple[list[int], list[int]]] = field(default_factory=list)
    checked_out: set[int] = field(default_factory=set)
    dirty_carts: set[int] = field(default_factory=set)  # added items after last checkout

    def record(self, name: str, started: float, r: httpx.Response) -> None:
        self.latencies[name].append((time.perf_counter() - started) * 1000)
        self.codes[name][r.status_code] += 1


async def call(
    stats: Stats, client: httpx.AsyncClient, name: str, method: str, url: str, **kw
) -> httpx.Response:
    t0 = time.perf_counter()
    r = await client.request(method, url, **kw)
    stats.record(name, t0, r)
    return r


# --- setup and cleanup --------------------------------------------------------------------


def setup(conn: psycopg.Connection, args: argparse.Namespace) -> tuple[int, list[int], list[int]]:
    tag = uuid.uuid4().hex[:8]
    with conn.transaction():
        owner = conn.execute(
            "INSERT INTO users (email, full_name) VALUES (%s, 'Load Test Seller') RETURNING id",
            (f"loadtest-seller-{tag}@example.com",),
        ).fetchone()[0]
        seller = conn.execute(
            "INSERT INTO sellers (user_id, shop_name, country_code) VALUES (%s, %s, 'DE')"
            " RETURNING id",
            (owner, f"Load Test {tag}"),
        ).fetchone()[0]
        products = [
            r[0]
            for r in conn.execute(
                """
                INSERT INTO products (seller_id, category_id, sku, title, price, status)
                SELECT %s, 3, 'LT-' || %s || '-' || g, 'Loadtest Laptop ' || g,
                       9.99 + g, 'active'
                FROM generate_series(1, %s) AS g
                RETURNING id
                """,
                (seller, tag, args.products),
            ).fetchall()
        ]
        conn.execute(
            "INSERT INTO inventory (product_id, quantity) SELECT unnest(%s::bigint[]), %s",
            (products, args.stock),
        )
        buyers = [
            r[0]
            for r in conn.execute(
                """
                INSERT INTO users (email, full_name)
                SELECT 'loadtest-' || %s || '-' || g || '@example.com', 'Load Buyer ' || g
                FROM generate_series(1, %s) AS g
                RETURNING id
                """,
                (tag, args.buyers),
            ).fetchall()
        ]
        conn.execute(
            """
            INSERT INTO addresses (user_id, recipient, line1, city, postal_code, country_code,
                                   is_default)
            SELECT u, 'Load Buyer', '1 Test Street', 'Berlin', '10115', 'DE', true
            FROM unnest(%s::bigint[]) AS u
            """,
            (buyers,),
        )
    return seller, products, buyers


def cleanup(conn: psycopg.Connection, seller: int, buyers: list[int]) -> None:
    with conn.transaction():
        orders = [
            r[0]
            for r in conn.execute(
                "DELETE FROM orders WHERE seller_id = %s RETURNING id", (seller,)
            ).fetchall()
        ]
        conn.execute(
            "DELETE FROM jobs WHERE kind = 'order_confirmation'"
            " AND (payload->>'order_id')::bigint = ANY(%s)",
            (orders,),
        )
        conn.execute(
            "DELETE FROM outbox WHERE aggregate_type = 'order' AND aggregate_id = ANY(%s)",
            ([str(o) for o in orders],),
        )
        conn.execute("DELETE FROM products WHERE seller_id = %s", (seller,))
        owner = conn.execute(
            "DELETE FROM sellers WHERE id = %s RETURNING user_id", (seller,)
        ).fetchone()[0]
        conn.execute("DELETE FROM users WHERE id = ANY(%s)", (buyers + [owner],))


# --- the two crowds -------------------------------------------------------------------------


async def shopper(
    stats: Stats, client: httpx.AsyncClient, buyers: list[int], products: list[int], stop: float
) -> None:
    while time.perf_counter() < stop:
        buyer = random.choice(buyers)
        h = {"X-User-Id": str(buyer)}
        for pid in random.sample(products, random.randint(1, 3)):
            r = await call(stats, client, "POST /cart/items", "POST", "/cart/items",
                           json={"product_id": pid, "quantity": random.randint(1, 2)}, headers=h)  # fmt: skip
            stats.dirty_carts.add(buyer)
        for _attempt in range(3):
            key = str(uuid.uuid4())
            hk = {**h, "Idempotency-Key": key}
            r = await call(stats, client, "POST /checkout", "POST", "/checkout", headers=hk)
            if r.status_code == 201:
                ids = [o["id"] for o in r.json()["orders"]]
                stats.orders[key] = ids
                stats.checked_out.add(buyer)
                stats.dirty_carts.discard(buyer)
                if random.random() < 0.10:  # the client "times out" and retries
                    again = await call(stats, client, "POST /checkout (retry)", "POST",
                                       "/checkout", headers=hk)  # fmt: skip
                    stats.replays.append((ids, [o["id"] for o in again.json()["orders"]]))
                break
            if r.status_code == 409:  # sold out: drop that product, try the rest
                pid = r.json()["product_id"]
                await call(stats, client, "DELETE /cart/items/{id}", "DELETE",
                           f"/cart/items/{pid}", headers=h)  # fmt: skip
                continue
            break  # 422 empty cart etc.


async def browser(stats: Stats, client: httpx.AsyncClient, stop: float) -> None:
    while time.perf_counter() < stop:
        roll = random.random()
        if roll < 0.45:
            params = {"category_id": random.choice(ROOTS),
                      "sort": random.choice(["newest", "price_asc", "price_desc"])}  # fmt: skip
            r = await call(stats, client, "GET /products", "GET", "/products", params=params)
            items = r.json().get("items", [])
            if r.json().get("next_cursor") and random.random() < 0.5:
                await call(stats, client, "GET /products (page 2)", "GET", "/products",
                           params={**params, "cursor": r.json()["next_cursor"]})  # fmt: skip
            if items:
                pid = random.choice(items)["id"]
                await call(stats, client, "GET /products/{id}", "GET", f"/products/{pid}")
        else:
            await call(stats, client, "GET /search", "GET", "/search",
                       params={"q": random.choice(TERMS), "limit": 20})  # fmt: skip


# --- report and invariants ---------------------------------------------------------------------


def pct(values: list[float], p: float) -> float:
    return statistics.quantiles(values, n=100)[int(p) - 1] if len(values) > 1 else values[0]


def report(stats: Stats, elapsed: float) -> None:
    total = sum(len(v) for v in stats.latencies.values())
    print(f"\n{total} requests in {elapsed:.1f} s = {total / elapsed:.0f} req/s")
    print(f"{'endpoint':<26}{'count':>7}{'req/s':>8}{'p50 ms':>9}{'p95 ms':>9}{'p99 ms':>9}  codes")
    for name in sorted(stats.latencies):
        lat = stats.latencies[name]
        codes = " ".join(f"{c}:{n}" for c, n in sorted(stats.codes[name].items()))
        print(f"{name:<26}{len(lat):>7}{len(lat) / elapsed:>8.1f}{pct(lat, 50):>9.1f}"
              f"{pct(lat, 95):>9.1f}{pct(lat, 99):>9.1f}  {codes}")  # fmt: skip


def check_invariants(
    conn: psycopg.Connection, stats: Stats, seller: int, products: list[int], drain: float
) -> bool:
    ok = True

    def check(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        print(f"  [{'ok' if passed else 'FAIL'}] {name}{': ' + detail if detail else ''}")

    print("\ninvariants")
    oversold = conn.execute(
        """
        SELECT count(*) FILTER (WHERE i.reserved > i.quantity),
               count(*) FILTER (WHERE i.reserved <> coalesce(s.units, 0)),
               sum(i.reserved), sum(i.quantity)
        FROM inventory i
        LEFT JOIN (SELECT oi.product_id, sum(oi.quantity) AS units
                   FROM order_items oi JOIN orders o ON o.id = oi.order_id
                   WHERE o.seller_id = %s GROUP BY oi.product_id) s
               ON s.product_id = i.product_id
        WHERE i.product_id = ANY(%s)
        """,
        (seller, products),
    ).fetchone()
    check("no oversold product (reserved <= quantity)", oversold[0] == 0)
    check("reserved == units in this run's orders", oversold[1] == 0,
          f"{oversold[2]} of {oversold[3]} units sold")  # fmt: skip
    bad_totals = conn.execute(
        """
        SELECT count(*) FROM orders o
        WHERE o.seller_id = %s
          AND o.total <> (SELECT sum(quantity * unit_price) FROM order_items WHERE order_id = o.id)
        """,
        (seller,),
    ).fetchone()[0]
    n_orders = conn.execute("SELECT count(*) FROM orders WHERE seller_id = %s", (seller,)).fetchone()
    check("order totals == sum of their items", bad_totals == 0, f"{n_orders[0]} orders")
    returned = sorted(i for ids in stats.orders.values() for i in ids)
    in_db = sorted(
        r[0] for r in conn.execute("SELECT id FROM orders WHERE seller_id = %s", (seller,))
    )
    check("orders in the database == orders returned by 201 responses", returned == in_db)
    dup = conn.execute(
        "SELECT count(*) FROM (SELECT user_id, idempotency_key FROM orders WHERE seller_id = %s"
        " GROUP BY 1, 2 HAVING count(*) > 1) d",
        (seller,),
    ).fetchone()[0]
    check("one order per (user, key) (single-seller carts)", dup == 0)
    check("retried checkouts replayed the same orders",
          all(a == b for a, b in stats.replays), f"{len(stats.replays)} retries")  # fmt: skip
    clean = sorted(stats.checked_out - stats.dirty_carts)
    leftover = conn.execute(
        "SELECT count(*) FROM cart_items WHERE user_id = ANY(%s)", (clean,)
    ).fetchone()[0]
    check("carts emptied by checkout", leftover == 0, f"{len(clean)} buyers")

    deadline = time.monotonic() + drain
    while True:
        pending, jobs_open, jobs = conn.execute(
            """
            SELECT (SELECT count(*) FROM outbox WHERE published_at IS NULL),
                   (SELECT count(*) FROM jobs WHERE kind = 'order_confirmation'
                      AND done_at IS NULL AND (payload->>'order_id')::bigint = ANY(%s)),
                   (SELECT count(*) FROM jobs WHERE kind = 'order_confirmation'
                      AND (payload->>'order_id')::bigint = ANY(%s))
            """,
            (in_db, in_db),
        ).fetchone()
        if (pending == 0 and jobs == len(in_db) and jobs_open == 0) or time.monotonic() > deadline:
            break
        time.sleep(0.2)
    check("outbox drained (relay)", pending == 0, f"{pending} unpublished")
    check("one order_confirmation job per order, all done (worker)",
          jobs == len(in_db) and jobs_open == 0, f"{jobs} jobs, {jobs_open} open")  # fmt: skip
    return ok


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--duration", type=float, default=30)
    p.add_argument("--shoppers", type=int, default=40)
    p.add_argument("--browsers", type=int, default=40)
    p.add_argument("--products", type=int, default=20)
    p.add_argument("--stock", type=int, default=100)
    p.add_argument("--buyers", type=int, default=500)
    p.add_argument("--drain", type=float, default=15, help="seconds to wait for relay/worker")
    p.add_argument("--keep", action="store_true", help="keep the run's rows")
    args = p.parse_args()

    conn = psycopg.connect(DSN, autocommit=True)
    seller, products, buyers = setup(conn, args)
    print(f"seller {seller}: {len(products)} products x {args.stock} units; {len(buyers)} buyers")
    stats = Stats()
    limits = httpx.Limits(max_connections=args.shoppers + args.browsers)
    try:
        async with httpx.AsyncClient(base_url=args.url, limits=limits, timeout=30) as client:
            started = time.perf_counter()
            stop = started + args.duration
            async with asyncio.TaskGroup() as tg:
                for n in range(args.shoppers):  # each shopper has buyers of its own
                    tg.create_task(shopper(stats, client, buyers[n :: args.shoppers], products, stop))
                for _ in range(args.browsers):
                    tg.create_task(browser(stats, client, stop))
            elapsed = time.perf_counter() - started
        report(stats, elapsed)
        ok = check_invariants(conn, stats, seller, products, args.drain)
    finally:
        if not args.keep:
            cleanup(conn, seller, buyers)
        conn.close()
    print("\nALL INVARIANTS HOLD" if ok else "\nINVARIANT VIOLATED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
