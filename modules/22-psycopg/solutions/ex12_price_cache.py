"""Exercise 12: an in-process price cache invalidated by LISTEN/NOTIFY."""

import os
import threading
import time
from decimal import Decimal

import psycopg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")

SETUP = """
CREATE OR REPLACE FUNCTION notify_price_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('product_price', NEW.id::text);
    RETURN NULL;
END $$;
CREATE OR REPLACE TRIGGER products_notify_price
    AFTER UPDATE OF price ON products
    FOR EACH ROW WHEN (OLD.price IS DISTINCT FROM NEW.price)
    EXECUTE FUNCTION notify_price_change();
"""
TEARDOWN = "DROP TRIGGER products_notify_price ON products; DROP FUNCTION notify_price_change();"


class PriceCache:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._prices: dict[int, Decimal] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self.invalidations = 0
        self._thread = threading.Thread(target=self._listen, daemon=True)

    def start(self) -> "PriceCache":
        self._thread.start()
        self._ready.wait(5)
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def get(self, conn: psycopg.Connection, product_id: int) -> Decimal:
        with self._lock:
            if product_id in self._prices:
                return self._prices[product_id]
        price = conn.execute("SELECT price FROM products WHERE id = %s", (product_id,)).fetchone()[0]
        with self._lock:
            self._prices[product_id] = price
        return price

    def cached(self, product_id: int) -> Decimal | None:
        with self._lock:
            return self._prices.get(product_id)

    def _listen(self) -> None:
        with psycopg.connect(self._dsn, autocommit=True) as conn:
            conn.execute("LISTEN product_price")
            # Anything cached before LISTEN took effect could already be stale.
            with self._lock:
                self._prices.clear()
            self._ready.set()
            while not self._stop.is_set():
                for n in conn.notifies(timeout=0.2):  # wake up regularly to check _stop
                    with self._lock:
                        if self._prices.pop(int(n.payload), None) is not None:
                            self.invalidations += 1


def wait_for(predicate, timeout: float = 2.0) -> float:
    t = time.perf_counter()
    while not predicate():
        if time.perf_counter() - t > timeout:
            raise TimeoutError
        time.sleep(0.005)
    return time.perf_counter() - t


if __name__ == "__main__":
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(SETUP)
        try:
            cache = PriceCache(DSN).start()
            with psycopg.connect(DSN, autocommit=True) as app:
                original = cache.get(app, 16)
                print("cached:", original)

                with psycopg.connect(DSN) as writer:  # change, then roll back
                    writer.execute("UPDATE products SET price = price + 1 WHERE id = 16")
                    time.sleep(0.3)
                    print("uncommitted update -> still cached:", cache.cached(16))
                    writer.rollback()
                    time.sleep(0.3)
                    print("rolled back       -> still cached:", cache.cached(16))

                    writer.execute("UPDATE products SET price = price + 1 WHERE id = 16")
                    writer.commit()
                    dt = wait_for(lambda: cache.cached(16) is None)
                    print(f"committed         -> invalidated after {dt * 1000:.0f} ms")
                    print("fresh read:", cache.get(app, 16))

                    writer.execute("UPDATE products SET price = %s WHERE id = 16", (original,))
                    writer.commit()
            wait_for(lambda: cache.cached(16) is None)
            print("invalidations:", cache.invalidations)
            cache.stop()
        finally:
            admin.execute(TEARDOWN)
