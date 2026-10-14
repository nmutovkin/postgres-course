#!/usr/bin/env python3
"""writeloop.py -- a deliberately simple client that writes as often as it can
and reports exactly when, and for how long, writes were failing.

It is the measuring instrument of the module 20 lab:

    docker exec m20-client python3 /lab/client/writeloop.py --duration 60

Every attempt inserts one row (run_id, seq) into ha_lab.writes, in autocommit
mode. The client remembers which seq numbers the server ACKNOWLEDGED (the
INSERT returned without error). At the end it reads the table back from the
current primary and reports:

  * lost writes:       acknowledged by the server but missing now  (RPO > 0!)
  * unknown outcomes:  the client saw an error, but the row IS there
                       (the commit happened; the acknowledgement was lost)

The client does NOT retry a failed write: it moves on to the next seq. That is
on purpose, so you can see the raw behaviour. The project milestone builds a
client that retries safely.
"""

from __future__ import annotations

import argparse
import signal
import socket
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import psycopg

DEFAULT_DSN = "host=m20-haproxy port=5000 dbname=postgres user=postgres connect_timeout=2"

DDL = """
CREATE SCHEMA IF NOT EXISTS ha_lab;
CREATE TABLE IF NOT EXISTS ha_lab.writes (
    run_id       text        NOT NULL,
    seq          bigint      NOT NULL,
    server_addr  inet        NOT NULL DEFAULT inet_server_addr(),
    committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (run_id, seq)
);
"""


def now() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


_names: dict[str, str] = {}


def host_name(addr: str | None) -> str:
    """Turn the server IP into a container name via Docker's reverse DNS."""
    if not addr:
        return "?"
    if addr not in _names:
        try:
            _names[addr] = socket.gethostbyaddr(addr)[0].split(".")[0]
        except OSError:
            _names[addr] = addr
    return _names[addr]


def first_line(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()
    return f"{type(exc).__name__}: {text[0] if text else ''}"


@dataclass
class Outage:
    first_error_at: float
    last_ok_before: float | None
    errors: int = 0
    recovered_at: float | None = None
    first_ok_after: float | None = None
    kinds: dict[str, int] = field(default_factory=dict)


notices: dict[str, int] = {}


def on_notice(diag: psycopg.errors.Diagnostic) -> None:
    """Print server WARNINGs (e.g. a cancelled synchronous-replication wait).
    Each distinct message is printed the first 3 times, then only counted."""
    msg = f"{diag.severity}: {diag.message_primary}"
    notices[msg] = notices.get(msg, 0) + 1
    if notices[msg] <= 3:
        print(f"{now()}  SERVER {msg}" + (f" ({diag.message_detail})" if diag.message_detail else ""))


def connect(dsn: str) -> psycopg.Connection:
    conn = psycopg.connect(dsn, autocommit=True)
    conn.add_notice_handler(on_notice)
    # Never hang forever on a dead server: give each statement 3 s.
    conn.execute("SET statement_timeout = '3s'")
    return conn


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", default=DEFAULT_DSN, help=f"libpq connection string (default: {DEFAULT_DSN!r})")
    ap.add_argument("--interval", type=float, default=0.1, help="pause between attempts, seconds (default 0.1)")
    ap.add_argument("--duration", type=float, default=60, help="stop after this many seconds (default 60)")
    ap.add_argument("--run-id", default=None, help="label for this run (default: random)")
    args = ap.parse_args()

    run_id = args.run_id or uuid.uuid4().hex[:8]
    stop = False

    def on_signal(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    # Create the table once, waiting for a primary if necessary.
    t_setup = time.monotonic()
    while True:
        try:
            with psycopg.connect(args.dsn, autocommit=True) as c:
                c.execute(DDL)
            break
        except psycopg.Error as exc:
            if time.monotonic() - t_setup > 60:
                print(f"{now()}  cannot create ha_lab.writes: {first_line(exc)}", file=sys.stderr)
                return 2
            time.sleep(1)

    print(f"{now()}  run {run_id}: writing every {args.interval}s for {args.duration}s via: {args.dsn}")
    conn: psycopg.Connection | None = None
    acked: list[int] = []
    unknown: list[int] = []  # statement was sent, then an error: did it commit?
    outages: list[Outage] = []
    current: Outage | None = None
    last_ok: float | None = None
    last_server = None
    seq = 0
    max_latency = 0.0
    t0 = time.monotonic()
    next_report = t0 + 5
    oks_since_report = 0

    while not stop and time.monotonic() - t0 < args.duration:
        seq += 1
        sent = False
        try:
            if conn is None or conn.closed:
                conn = connect(args.dsn)
            sent = True
            t_stmt = time.monotonic()
            row = conn.execute(
                "INSERT INTO ha_lab.writes (run_id, seq) VALUES (%s, %s) RETURNING host(inet_server_addr())",
                (run_id, seq),
            ).fetchone()
            t = time.monotonic()
            latency = t - t_stmt
            max_latency = max(max_latency, latency)
            server = host_name(row[0] if row else None)
            acked.append(seq)
            if latency > 1.0:
                print(f"{now()}  SLOW COMMIT seq={seq}: the INSERT took {latency:.2f}s")
            oks_since_report += 1
            if current is not None:
                current.recovered_at = t
                current.first_ok_after = t
                gap = t - (current.last_ok_before or current.first_error_at)
                print(
                    f"{now()}  RECOVERED seq={seq} on {server}: writes were failing for "
                    f"{t - current.first_error_at:.2f}s ({current.errors} failed attempts); "
                    f"gap between successful commits {gap:.2f}s"
                )
                current = None
            elif server != last_server:
                print(f"{now()}  writing to {server}")
            last_server = server
            last_ok = t
        except psycopg.Error as exc:
            t = time.monotonic()
            if sent:
                unknown.append(seq)
            kind = first_line(exc)
            if current is None:
                current = Outage(first_error_at=t, last_ok_before=last_ok)
                outages.append(current)
                print(f"{now()}  WRITE FAILED seq={seq}: {kind}")
            current.errors += 1
            current.kinds[kind] = current.kinds.get(kind, 0) + 1
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            conn = None
        if time.monotonic() >= next_report:
            state = f"FAILING ({current.errors} errors so far)" if current else f"ok: {oks_since_report} commits on {last_server}"
            print(f"{now()}  t={time.monotonic() - t0:5.1f}s  {state}")
            oks_since_report = 0
            next_report += 5
        time.sleep(args.interval)

    if conn is not None:
        conn.close()

    # ---------------------------------------------------------------- report
    print()
    print(f"=== run {run_id} summary ===")
    print(f"attempts: {seq}   acknowledged: {len(acked)}   failed: {seq - len(acked)}   "
          f"slowest successful INSERT: {max_latency:.2f}s")
    for i, o in enumerate(outages, 1):
        if o.recovered_at is None:
            print(f"outage {i}: NOT recovered by the end of the run ({o.errors} errors)")
        else:
            gap = o.recovered_at - (o.last_ok_before or o.first_error_at)
            print(
                f"outage {i}: {o.recovered_at - o.first_error_at:.2f}s of failing writes, "
                f"{gap:.2f}s between successful commits, {o.errors} failed attempts"
            )
        for k, n in sorted(o.kinds.items(), key=lambda kv: -kv[1])[:4]:
            print(f"    {n:4d} x {k}")

    for msg, n in notices.items():
        print(f"server notice x{n}: {msg}")

    # Verify against the current primary (wait for one, up to 60 s).
    t_v = time.monotonic()
    while True:
        try:
            with psycopg.connect(args.dsn, autocommit=True) as c:
                present = {r[0] for r in c.execute("SELECT seq FROM ha_lab.writes WHERE run_id = %s", (run_id,))}
                server = host_name(c.execute("SELECT host(inet_server_addr())").fetchone()[0])
            break
        except psycopg.Error as exc:
            if time.monotonic() - t_v > 60:
                print(f"verification impossible: {first_line(exc)}")
                return 1
            time.sleep(1)
    lost = sorted(set(acked) - present)
    committed_unacked = sorted(set(unknown) & present)
    print(f"verification on {server}: rows in table {len(present)}")
    print(f"  acknowledged but LOST:            {len(lost)} {lost[:10] if lost else ''}")
    print(f"  error reported but row COMMITTED: {len(committed_unacked)} {committed_unacked[:10] if committed_unacked else ''}")
    return 1 if lost else 0


if __name__ == "__main__":
    sys.exit(main())
