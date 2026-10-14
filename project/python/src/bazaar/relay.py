"""The outbox relay as a process (module 28): python -m bazaar.relay

Moves committed outbox events (module 27, revision 2701) into the job queue
(module 10), where `python -m bazaar.worker` picks them up:

    POST /checkout ── orders + outbox rows (one transaction)
                          │  NOTIFY outbox (wake-up call)
    bazaar.relay ─────────┘  SELECT ... FOR UPDATE SKIP LOCKED; INSERT INTO jobs;
                             UPDATE outbox SET published_at   (one transaction)
                          │  NOTIFY jobs_ready (revision 2601's trigger)
    bazaar.worker ────────┘  dequeue_jobs() -> handler -> complete_job()

Publishing into the jobs table happens in the relay's own transaction, so the
job and the "published" mark commit together: exactly once (module 27,
section 6.4). Several relays may run side by side (SKIP LOCKED); one is enough
for Bazaar's volume.

SIGINT/SIGTERM stop the loop after the batch in hand; the connections are
closed cleanly. Routes (topic -> job kinds) are below; a topic without a route
is marked published and dropped.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from bazaar import outbox
from bazaar.config import get_settings

log = logging.getLogger("bazaar.relay")

ROUTES: dict[str, list[str]] = {
    "order.placed": ["order_confirmation"],  # bazaar.worker has the handler
}


async def run(poll_interval: float = 5.0, batch_size: int = 100) -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    log.info("relay started: routes=%s", ROUTES)
    total = await outbox.run_relay(
        None,
        stop,
        poll_interval=poll_interval,
        batch_size=batch_size,
        make_publish=lambda conn: outbox.ajobs_publisher(conn, ROUTES),
    )
    log.info("relay stopped: %d message(s) published", total)
    return total


def main(argv: list[str] | None = None) -> None:
    get_settings()  # fail early on a bad environment
    p = argparse.ArgumentParser(prog="python -m bazaar.relay", description=__doc__.split("\n")[0])
    p.add_argument("--poll-interval", type=float, default=5.0)
    p.add_argument("--batch-size", type=int, default=100)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run(args.poll_interval, args.batch_size))


if __name__ == "__main__":
    main()
