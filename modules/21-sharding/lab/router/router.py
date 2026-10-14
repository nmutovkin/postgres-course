"""Application-level sharding for Bazaar: a minimal seller_id -> shard router.

Module 21, section 10. Run it inside the lab's Python container:

    docker exec m21-app python router.py init           # create the logical shards
    docker exec m21-app python router.py load           # copy orders from m21-fdw-coord
    docker exec m21-app python router.py where 1 2 3    # which shard/node holds a seller?
    docker exec m21-app python router.py dashboard 3    # single-shard query
    docker exec m21-app python router.py top-sellers    # scatter-gather over all shards

The design, in three layers:
  seller_id --hash--> logical shard (0..15) --SHARD_MAP--> node (a DSN)
plus a DIRECTORY of exceptions that pins chosen sellers to a logical shard.
Each logical shard is a SCHEMA (s00 .. s15) on its node, so moving a shard to
another node later means moving one schema and editing SHARD_MAP.
(psycopg itself is taught in module 22; only connect/execute/fetch are used here.)
"""

import hashlib
import heapq
import os
import sys

import psycopg

N_LOGICAL = 16  # fixed forever: changing it re-hashes every seller

NODES = {
    "shard1": os.environ.get("SHARD1_DSN", "host=m21-shard1 user=postgres dbname=approuter"),
    "shard2": os.environ.get("SHARD2_DSN", "host=m21-shard2 user=postgres dbname=approuter"),
}
SOURCE_DSN = os.environ.get("SOURCE_DSN", "host=m21-fdw-coord user=postgres dbname=bazaar")

# Logical shard -> node. Rebalancing = changing entries here (after moving data).
SHARD_MAP = {s: ("shard1" if s < 8 else "shard2") for s in range(N_LOGICAL)}
# Moved shards, e.g. SHARD_MOVES="5:shard2" after copying schema s05 to shard2.
# (A real system keeps this map in a small config database or service.)
for move in filter(None, os.environ.get("SHARD_MOVES", "").split(",")):
    shard, node = move.split(":")
    SHARD_MAP[int(shard)] = node

# Directory overrides: sellers placed explicitly (e.g. a huge seller isolated
# on a logical shard of its own). Empty in the lab; exercise 12 uses it.
DIRECTORY: dict[int, int] = {}


def logical_shard(seller_id: int) -> int:
    """Stable hash of the shard key. Never use Python's hash(): it is salted per process."""
    if seller_id in DIRECTORY:
        return DIRECTORY[seller_id]
    digest = hashlib.blake2b(seller_id.to_bytes(8, "big", signed=True), digest_size=8).digest()
    return int.from_bytes(digest, "big") % N_LOGICAL


def route(seller_id: int) -> tuple[str, str]:
    """Return (node name, schema name) for a seller."""
    shard = logical_shard(seller_id)
    return SHARD_MAP[shard], f"s{shard:02d}"


def connect(node: str) -> psycopg.Connection:
    return psycopg.connect(NODES[node], autocommit=True)


def init() -> None:
    for node, dsn in NODES.items():
        admin = dsn.replace("dbname=approuter", "dbname=postgres")
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute("DROP DATABASE IF EXISTS approuter WITH (FORCE)")
            conn.execute("CREATE DATABASE approuter")
    for shard, node in SHARD_MAP.items():
        with connect(node) as conn:
            conn.execute(f"CREATE SCHEMA s{shard:02d}")
            conn.execute(f"""
                CREATE TABLE s{shard:02d}.orders (
                    seller_id  bigint        NOT NULL,
                    id         bigint        NOT NULL,
                    user_id    bigint        NOT NULL,
                    status     text          NOT NULL,
                    total      numeric(12,2) NOT NULL,
                    created_at timestamptz   NOT NULL,
                    PRIMARY KEY (seller_id, id))""")
    print(f"created {N_LOGICAL} logical shards on {len(NODES)} nodes")


def load() -> None:
    with psycopg.connect(SOURCE_DSN) as src:
        rows = src.execute(
            "SELECT seller_id, id, user_id, status::text, total, created_at FROM bazaar.orders"
        ).fetchall()
    conns = {node: connect(node) for node in NODES}
    for row in rows:
        node, schema = route(row[0])
        conns[node].execute(
            f"INSERT INTO {schema}.orders VALUES (%s, %s, %s, %s, %s, %s)", row
        )
    for conn in conns.values():
        conn.close()
    print(f"loaded {len(rows)} orders")


def where(seller_ids: list[int]) -> None:
    for seller_id in seller_ids:
        node, schema = route(seller_id)
        print(f"seller {seller_id:>4} -> logical shard {schema} on {node}")


def dashboard(seller_id: int) -> None:
    # Single-shard query: the router picks ONE node, the SQL names ONE schema.
    node, schema = route(seller_id)
    with connect(node) as conn:
        rows = conn.execute(
            f"""SELECT status, count(*), sum(total)
                FROM {schema}.orders
                WHERE seller_id = %s
                GROUP BY status ORDER BY status""",
            (seller_id,),
        ).fetchall()
    print(f"seller {seller_id} ({schema} on {node})")
    for status, n, total in rows:
        print(f"  {status:<10} {n:>4} {total:>12}")


def top_sellers(limit: int = 3) -> None:
    # Scatter-gather: the same query on every logical shard, merged here.
    # Each shard returns its own per-seller revenue (sellers never span
    # shards, so the per-shard sums are already final); we merge and re-sort.
    partials = []
    for shard, node in SHARD_MAP.items():
        with connect(node) as conn:
            partials.append(
                conn.execute(
                    f"""SELECT seller_id, sum(total) AS revenue
                        FROM s{shard:02d}.orders
                        WHERE status IN ('paid', 'shipped', 'delivered')
                        GROUP BY seller_id""",
                ).fetchall()
            )
    merged = heapq.nlargest(limit, (r for part in partials for r in part), key=lambda r: r[1])
    print(f"queried {len(partials)} logical shards")
    for seller_id, revenue in merged:
        print(f"  seller {seller_id:>4} revenue {revenue:>12}")


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "init":
        init()
    elif cmd == "load":
        load()
    elif cmd == "where":
        where([int(a) for a in args])
    elif cmd == "dashboard":
        dashboard(int(args[0]))
    elif cmd == "top-sellers":
        top_sellers()
    else:
        sys.exit(f"unknown command {cmd}")
