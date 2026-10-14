"""Module 22, section 13: LISTEN/NOTIFY with a synchronous connection."""

import os
import threading
import time

import psycopg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")


def producer() -> None:
    time.sleep(0.5)
    with psycopg.connect(DSN) as conn:
        conn.execute("SELECT pg_notify('price_changed', '16')")
        conn.execute("SELECT pg_notify('price_changed', '16')")  # duplicate in the same txn
        conn.execute("SELECT pg_notify('price_changed', '47')")
        print(f"{time.perf_counter() - t0:5.2f}s producer: sent 3, committing")
    # the with block commits here: only now are the notifications delivered
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("NOTIFY price_changed, '68'")


with psycopg.connect(DSN, autocommit=True) as conn:  # autocommit: LISTEN takes effect now
    conn.execute("LISTEN price_changed")
    t0 = time.perf_counter()
    threading.Thread(target=producer).start()
    for n in conn.notifies(timeout=3.0):
        print(f"{time.perf_counter() - t0:5.2f}s got {n.channel!r} payload={n.payload!r} from pid {n.pid}")
    print(f"{time.perf_counter() - t0:5.2f}s timeout (3 s in total) reached, generator ended")
