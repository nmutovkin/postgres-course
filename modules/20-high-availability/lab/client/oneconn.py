#!/usr/bin/env python3
"""oneconn.py -- keep ONE connection open and never reconnect.

Used in exercise 12 to compare what a single long-lived client connection
experiences during a switchover when it goes through HAProxy directly versus
through PgBouncer:

    docker exec m20-client python3 /lab/client/oneconn.py --dsn "host=m20-haproxy port=5000 dbname=postgres user=postgres"

Every --interval seconds it runs one autocommit INSERT on the same connection
and prints a line whenever the outcome changes (ok -> error, error -> ok).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

import psycopg


def now() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--duration", type=float, default=40)
    ap.add_argument("--interval", type=float, default=0.2)
    args = ap.parse_args()

    conn = psycopg.connect(args.dsn, autocommit=True, connect_timeout=3)
    conn.execute("CREATE SCHEMA IF NOT EXISTS ha_lab")
    conn.execute("CREATE TABLE IF NOT EXISTS ha_lab.oneconn (id bigint GENERATED ALWAYS AS IDENTITY, at timestamptz DEFAULT clock_timestamp())")
    print(f"{now()}  connected once; this script never reconnects")
    ok = err = ok_after_error = 0
    last = None
    t0 = time.monotonic()
    while time.monotonic() - t0 < args.duration:
        try:
            server = conn.execute("INSERT INTO ha_lab.oneconn DEFAULT VALUES RETURNING host(inet_server_addr())").fetchone()[0]
            ok += 1
            if err:
                ok_after_error += 1
            state = f"ok (server {server})"
        except psycopg.Error as exc:
            err += 1
            state = f"ERROR {type(exc).__name__}: {str(exc).strip().splitlines()[0] if str(exc).strip() else ''}"
        if state != last:
            print(f"{now()}  {state}")
            last = state
        time.sleep(args.interval)
    print(f"\nstatements ok: {ok}, failed: {err}, ok AFTER the first failure: {ok_after_error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
