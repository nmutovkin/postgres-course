"""Module 22, section 7.5: custom loaders and dumpers."""

import os
import time
from dataclasses import dataclass

import psycopg
from psycopg.adapt import Dumper, Loader
from psycopg.types.numeric import FloatLoader

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
Q = "SELECT unit_price * quantity FROM order_items WHERE order_id <= 150000"


# 1. A loader: numeric -> float, for an analytics script that does not need exact cents.
with psycopg.connect(DSN) as conn:
    for label in ("numeric -> Decimal (default)", "numeric -> float (FloatLoader)"):
        if "float" in label:
            conn.adapters.register_loader("numeric", FloatLoader)  # only on this connection
        t = time.perf_counter()
        values = [r[0] for r in conn.execute(Q)]
        dt = time.perf_counter() - t
        print(f"{label:32s} {type(values[0]).__name__:8s} n={len(values):,} sum={sum(values)!r:22s}  {dt:.2f}s")


# 2. A dumper and a loader for a small value class of our own.
@dataclass(frozen=True)
class Sku:
    seller_id: int
    code: str

    def __str__(self) -> str:
        return f"S{self.seller_id}-{self.code}"


class SkuDumper(Dumper):
    oid = psycopg.adapters.types["text"].oid  # send it as text

    def dump(self, obj: Sku) -> bytes:
        return str(obj).encode()


class SkuLoader(Loader):
    def load(self, data) -> Sku:
        seller, code = bytes(data).decode().split("-", 1)
        return Sku(int(seller[1:]), code)


with psycopg.connect(DSN) as conn:
    conn.adapters.register_dumper(Sku, SkuDumper)
    row = conn.execute("SELECT id, title FROM products WHERE sku = %s", (Sku(5, "000001"),)).fetchone()
    print("dumper:", row)
    with conn.cursor() as cur:
        cur.adapters.register_loader("text", SkuLoader)  # cursor scope: every text column!
        print("loader:", cur.execute("SELECT sku FROM products WHERE id = 1").fetchone())
