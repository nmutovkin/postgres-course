"""Exercise 6: lost updates, and optimistic locking with retries, under real concurrency.

Creates a committed product with stock 0, runs 8 threads x 25 "+1" adjustments
three ways, and deletes the product at the end.
"""

from __future__ import annotations

import threading
import time
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from bazaar.orm.models import XID, Inventory, Product
from bazaar.sa.engine import create_engine


class Base(DeclarativeBase):  # a separate registry: the same table, mapped differently
    pass


class VersionedStock(Base):
    __tablename__ = "inventory"
    product_id: Mapped[int] = mapped_column(primary_key=True)
    quantity: Mapped[int]
    xmin: Mapped[int] = mapped_column(XID(), system=True, server_default=sa.FetchedValue())
    __mapper_args__ = {"version_id_col": xmin, "version_id_generator": False}


THREADS, ROUNDS = 8, 25
engine = create_engine(pool_size=THREADS)
Session = sessionmaker(engine)


def naive(pid: int) -> int:
    with Session.begin() as s:
        inv = s.get(Inventory, pid)
        time.sleep(0.001)  # "business logic" between read and write
        inv.quantity = inv.quantity + 1
    return 0


def versioned(pid: int) -> int:
    conflicts = 0
    while True:
        try:
            with Session.begin() as s:
                inv = s.get(VersionedStock, pid)
                time.sleep(0.001)
                inv.quantity = inv.quantity + 1
            return conflicts
        except StaleDataError:
            conflicts += 1  # someone else won: re-read and re-apply


def atomic(pid: int) -> int:
    with Session.begin() as s:  # no read-modify-write in Python at all
        s.execute(
            sa.update(Inventory)
            .where(Inventory.product_id == pid)
            .values(quantity=Inventory.quantity + 1)
        )
    return 0


def run(fn, pid: int) -> tuple[int, int, float]:
    with Session.begin() as s:
        s.get(Inventory, pid).quantity = 0
    conflicts = []

    def worker():
        conflicts.append(sum(fn(pid) for _ in range(ROUNDS)))

    t = time.perf_counter()
    threads = [threading.Thread(target=worker) for _ in range(THREADS)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    with Session() as s:
        final = s.get(Inventory, pid).quantity
    return final, sum(conflicts), time.perf_counter() - t


if __name__ == "__main__":
    with Session.begin() as s:
        p = Product(seller_id=7, category_id=3, sku="M25-EX6", title="Contended", price=Decimal(1),
                    inventory=Inventory(quantity=0))  # fmt: skip
        s.add(p)
        s.flush()
        pid = p.id
    try:
        for label, fn in (("naive read-modify-write", naive),
                          ("versioned (xmin) + retry", versioned),
                          ("atomic UPDATE quantity + 1", atomic)):  # fmt: skip
            final, conflicts, elapsed = run(fn, pid)
            print(f"{label:<28} expected {THREADS * ROUNDS}, got {final:>3}"
                  f"  conflicts/retries: {conflicts:>3}  {elapsed:.2f} s")
    finally:
        with Session.begin() as s:
            s.execute(sa.delete(Product).where(Product.id == pid))
