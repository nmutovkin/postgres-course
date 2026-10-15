# Module 20: Project milestone — Bazaar survives a failover

<!-- nav:start -->
> [Theory](README.md) · [Exercises](exercises.md) · **Project** · [Solutions](solutions/README.md)  
> [← 19 Replication](../19-replication/README.md) · [Course home](../../README.md) · [21 Sharding →](../21-sharding/README.md)
<!-- nav:end -->

<!-- toc:start -->
**Contents**

- [The goal](#the-goal)
- [Requirements](#requirements)
  - [1. Bazaar on the HA cluster](#1-bazaar-on-the-ha-cluster)
  - [2. Synchronous mode](#2-synchronous-mode)
  - [3. A checkout client that survives failover](#3-a-checkout-client-that-survives-failover)
- [Acceptance checks](#acceptance-checks)
- [Reference solution](#reference-solution)

<!-- toc:end -->

## The goal

Bazaar's checkout must keep working when the primary database disappears, and it must **never lose an order the customer was told about, or place one twice**. At the end of this milestone Bazaar runs on the three-node Patroni cluster from the lab, with synchronous replication. A checkout client rides through a switchover and a crash of the primary, and the numbers prove that nothing was lost or duplicated.

The Bazaar schema does not change in this module. The only new object is a bookkeeping table in a separate `ha_lab` schema.

## Requirements

### 1. Bazaar on the HA cluster

- Start the lab and load Bazaar into the current leader:

  ```bash
  cd modules/20-high-availability/lab
  ./scripts/up.sh
  ./scripts/load-bazaar.sh        # reset state 20 (needs ~10 GB of disk for three copies)
  ./scripts/load-bazaar.sh 11     # or: the small dataset, which is enough for this milestone
  ```

- Every node must have the same data (the replicas got it by streaming, not by loading it themselves).

### 2. Synchronous mode

- Turn on Patroni's synchronous mode (`synchronous_mode: true`, **not** strict) with `patronictl edit-config`. `patronictl list` must show one `Sync Standby`.
- Write down in one or two sentences what RPO you now have, and in which situation it silently becomes > 0 (Section 10).

### 3. A checkout client that survives failover

Write a client (Python with psycopg 3 is recommended; Bash + `psql` is possible but much harder to get right) and put it in `lab/client/`, which the `m20-client` container sees as `/lab/client/`. It must:

1. **Connect without a proxy**, with a libpq multi-host connection string: all three nodes, `target_session_attrs=read-write`, a short `connect_timeout`, TCP keepalives, and an `application_name`.
2. Place orders in a loop for `--duration` seconds. Each checkout is **one transaction** that calls `bazaar.place_order(user_id, items)` (Module 10) for a random active user and one to three active products. Make sure the products have stock: it's fine for the client to top up `bazaar.inventory` for the products it uses before it starts.
3. Give every logical checkout a **request id** (a UUID generated once per checkout, before the first attempt) and record it in

   ```sql
   CREATE TABLE ha_lab.checkout_requests (
       request_id  uuid        PRIMARY KEY,
       order_ids   bigint[]    NOT NULL,
       created_at  timestamptz NOT NULL DEFAULT now()
   );
   ```

   **in the same transaction** as the `place_order()` call. Before placing the order, check whether the request id is already there. If it is, the earlier attempt committed, so return its order ids instead of ordering again.
4. **Retry** a failed checkout, with the *same* request id, on a fresh connection, with exponential backoff and jitter, up to a deadline of at least 60 s. Retry only for connection errors and SQLSTATEs `57P01`, `57P02`, `57P03`, `25006`, `40001`, `40P01`, and class `08`. Don't retry `BZ001` (out of stock) or other business errors.
5. At the end, **verify** on the current primary that:
   - every acknowledged request id is in `ha_lab.checkout_requests` with the same order ids (nothing lost),
   - every order id it was given exists in `bazaar.orders`,
   - `bazaar.orders` contains no more orders created since the run started than the client accounts for (nothing duplicated),

   and exit with status 0 only if all three hold.

## Acceptance checks

**A. Same data everywhere.** All three numbers must be equal:

```bash
for n in 1 2 3; do docker exec m20-pg$n psql -U postgres -d bazaar -XAtc "SELECT count(*) FROM bazaar.orders"; done
```

**B. Zero lost or duplicated checkouts across a switchover in synchronous mode.** The provided script turns on synchronous mode, runs your client for 45 s, and performs a `patronictl switchover` after 15 s:

```bash
CLIENT=/lab/client/my_checkout_client.py ../solutions/milestone_check.sh     # from lab/
```

It must end with `ACCEPTED: no lost or duplicated checkouts`. With the reference client it printed:

```text
08:28:05.084  request 061cbde0: AdminShutdown (57P01), retrying
08:28:07.600  request 061cbde0 committed after 6 attempts on m20-pg3
...
checkouts acknowledged: 735  retried: 1  replayed (dedup by request_id): 0  out of stock: 0  gave up: 0
verification on m20-pg3:
  acknowledged checkouts:           735
  ... found in checkout_requests:   735
  ... LOST:                         0
  ... different order ids:          0
  orders created / found in orders: 1264 / 1264
  orders in bazaar.orders since the run started: 1264 (anything above 1264 = duplicates)
ACCEPTED: no lost or duplicated checkouts
```

**C. Zero lost checkouts across a hard crash of the primary.** Run your client for 70 s and kill the leader container 12 s in:

```bash
docker exec m20-client python3 -u /lab/client/my_checkout_client.py --duration 70 &
sleep 12; ./scripts/kill-leader.sh; wait
./scripts/rejoin.sh m20-pgN          # the node you killed
```

Your client must report one checkout that needed many attempts (it waits for the leader key to expire, about 30 s), and **0** lost. The reference client logged `request a078ac8b committed after 22 attempts on m20-pg3` and `LOST: 0`.

**D. No orphan orders** (run on the leader, in database `bazaar`). It must return `0`:

```sql
SELECT count(*)
FROM bazaar.orders o
WHERE o.created_at >= now() - interval '1 hour'      -- the window of your test runs
  AND NOT EXISTS (
        SELECT 1 FROM ha_lab.checkout_requests r WHERE o.id = ANY (r.order_ids)
  );
```

(If you placed orders by hand in that window, narrow the interval.)

**E. (Stretch) Unknown outcomes are deduplicated.** Run your client with `--interval 0` for 80 s while you `kill -9` the leader's postmaster every 8 s (`docker exec <leader> sh -c 'kill -9 $(head -1 /var/lib/postgresql/data/pg17/postmaster.pid)'`). Sooner or later a kill lands between the commit and its acknowledgement. Your client must then find its own request id on the retry, and still verify with 0 lost and 0 duplicated. The reference client reported `replayed (dedup by request_id): 1` in an 80-second run with six kills.

## Reference solution

- [`solutions/bazaar_ha_client.py`](solutions/bazaar_ha_client.py): the reference client (psycopg 3, multi-host DSN, idempotency key, retries with backoff, verification).
- [`solutions/milestone_check.sh`](solutions/milestone_check.sh): acceptance check B.
- There is no new file in `project/db/`: Modules 16–20 don't change the Bazaar schema. The `ha_lab` schema is lab bookkeeping, created by the client itself.
