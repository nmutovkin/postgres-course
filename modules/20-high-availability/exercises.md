# Module 20 Exercises: High Availability

All exercises use the lab in [`lab/`](lab/). Start it and load Bazaar first:

```bash
cd modules/20-high-availability/lab
./scripts/up.sh
./scripts/load-bazaar.sh 11      # the small dataset is enough for every exercise (use 20 if you have ~10 GB of disk)
```

Useful helpers (all in `lab/scripts/`): `cluster.sh` (`patronictl list`), `etcd.sh` (DCS keys; `etcd.sh health`), `rest.sh` (call the REST API: `./scripts/rest.sh all /primary`), `writeloop.sh` (the measuring client), `failover-drill.sh`, `kill-leader.sh`, `rejoin.sh`, `switchover.sh`, `partition.sh`.

Several exercises change the cluster configuration. Each one says how to put it back. If the cluster gets into a state you don't understand, `./scripts/down.sh && ./scripts/up.sh` gives you a fresh one in about 15 seconds (reload Bazaar afterwards).

Exact timings vary from machine to machine. The self-checks give the ranges we measured.

---

## Exercise 1 ★: The cluster's identity card

Using only `./scripts/etcd.sh`, `patronictl`, and `pg_controldata` (run it inside a node: `docker exec m20-pg1 pg_controldata /var/lib/postgresql/data/pg17`), answer:

1. What is the value of the `initialize` key, and where else can you find the same number on each node?
2. How many entries does the `history` key hold, and how does that number relate to the `TL` column of `patronictl list`?
3. Which etcd key would you look at to see **which node** is primary right now, and why can't you just check `pg_is_in_recovery()` on every node and take the one that says `false`?

**Self-check:** the `initialize` value equals the `Database system identifier` printed by `pg_controldata` on **all three** nodes, and it is the number in the header of `patronictl list` (`+ Cluster: bazaar-ha (7696...)`). In a cluster that has never been reset, `len(history) = TL − 1`.

<details><summary>Hint</summary>

`./scripts/etcd.sh | grep -A1 initialize`. To count history entries, pipe the value through `python3 -c "import json,sys; print(len(json.load(sys.stdin)))"`. For question 3, think about Section 11.3: what is an isolated old primary's `pg_is_in_recovery()` in the seconds before it demotes itself?
</details>

---

## Exercise 2 ★: Keep a replica out of the read pool

Bazaar's analysts want `m20-pg2` for themselves: it should stay a replica and a failover candidate, but application read traffic on HAProxy's port 5001 must no longer go there.

1. Before changing anything, **predict** the HTTP status of `/replica`, `/read-only`, and `/health` on all three nodes after the change.
2. Make the change, restart only `m20-pg2`, and verify your prediction with `./scripts/rest.sh all <path>`.
3. Open 10 connections through port 5001 (`docker exec m20-client psql "host=m20-haproxy port=5001 dbname=postgres user=postgres" -XAtc "select host(inet_server_addr())"`) and count where they land.
4. Undo the change.

**Self-check:** on `m20-pg2`, `/replica` and `/read-only` return 503 while `/health` returns 200. All 10 connections land on the one remaining replica. `patronictl list` shows a `Tags` column with `noloadbalance: true`.

<details><summary>Hint</summary>

Tags are local configuration: edit [`lab/patroni/pg2.yml`](lab/patroni/pg2.yml) (`tags: {noloadbalance: true}`) and `docker restart m20-pg2`. HAProxy's read-only pool uses `GET /replica` as its health check.
</details>

---

## Exercise 3 ★: What maintenance mode really means

1. With the cluster **not** paused, stop Postgres on a replica behind Patroni's back: `docker exec m20-pg1 pg_ctl stop -D /var/lib/postgresql/data/pg17 -m fast` (pick a replica). Time how long until it accepts connections again (`docker exec m20-pg1 pg_isready -h localhost`).
2. Now `patronictl pause --wait`, and do the same. Wait 30 seconds. What does `patronictl list` show, and what does `/health` return on that node?
3. `patronictl resume --wait`. How long until the replica is streaming again?
4. In one sentence: why would you ever *want* the behaviour from step 2?

**Self-check:** in step 1, Patroni restarts Postgres by itself, within about one or two `loop_wait` intervals (we measured 6.5–20 s). In step 2 the node stays `stopped`, the list ends with ` Maintenance mode: on`, and `/health` is 503. After `resume`, it was streaming within about 3 s.

<details><summary>Hint</summary>

The node's Patroni log (`docker logs m20-pg1`) explains each decision. Look for `PAUSE: postgres is not running`.
</details>

---

## Exercise 4 ★★: Diagnose the incident (paper exercise)

For each scenario, say **what is going on**, **whether Patroni will fail over by itself**, and **what you would do**. No lab needed, but you can reproduce most of them.

**(a)** 03:10, `patronictl list`:

```text
| pg1    | m20-pg1 | Replica | stopped   |    |     unknown |     |    unknown |     |
| pg2    | m20-pg2 | Replica | streaming |  7 |   0/9000060 |   0 |  0/9000060 |   0 |
| pg3    | m20-pg3 | Replica | streaming |  7 |   0/9000060 |   0 |  0/9000060 |   0 |
 Maintenance mode: on
```

**(b)** The leader's Patroni log, repeating every 10 seconds, while every node is up:

```text
ERROR: Error communicating with DCS
INFO: demoting self because DCS is not accessible and I was a leader
```

**(c)** Two replicas, after the primary's host died, each logging every 10 s for minutes:

```text
INFO: My wal position exceeds maximum replication lag
INFO: following a different leader because i am not the healthiest node
```

and `patronictl list` shows **no leader**, with replica lags of 26 MB and 10 MB.

**(d)** After a switchover attempt:

```text
Switchover failed, details: 412, candidate name does not match with sync_standby
```

**(e)** The application logs show bursts of `cannot execute INSERT in a read-only transaction` for about 20 seconds after every failover, then everything recovers. Clients connect with `host=pg1,pg2,pg3 target_session_attrs=read-write` and use a connection pool.

**(f)** A rejoining old primary logs `pg_rewind: servers diverged at WAL location 0/4AF2A2B0 on timeline 25`, and the next morning finance reports 49 orders that customers have emails for but that are not in the database.

**Self-check:** your answers should name: (a) maintenance mode left on, no leader, no automatic failover; (b) DCS quorum loss and the demotion rule, plus the setting that avoids it; (c) `maximum_lag_on_failover` refusing a lossy failover, plus the manual command that accepts the loss; (d) synchronous mode's candidate rule; (e) pooled connections opened before the failover, now attached to a demoted node; (f) asynchronous RPO plus `pg_rewind` discarding acknowledged commits.

<details><summary>Hint</summary>

Sections 9 (pause), 10 (sync), 11.1 (candidate rules), 11.4 (DCS failure), 11.5 (rewind), and 13.2/14.4 (connection lifetime) cover one scenario each.
</details>

---

## Exercise 5 ★★: Tune the failover timings, and measure

1. With the default timings, run `./scripts/failover-drill.sh kill 60` at least twice (run `./scripts/rejoin.sh <old leader>` in between). Record "new leader running after" and the client's "gap between successful commits".
2. Change the timings to `ttl=20`, `loop_wait=2`, `retry_timeout=5` with `patronictl edit-config -s ...`. Repeat the measurement.
3. Try `ttl=20 loop_wait=10 retry_timeout=10`. What does Patroni log, and what values does it actually use?
4. Explain why the hard-crash failover time follows `ttl` and not `loop_wait`, and what you risk by making `ttl` small on a real network.
5. Put the defaults back: `patronictl edit-config -s ttl=30 -s loop_wait=10 -s retry_timeout=10 --force`.

**Self-check:** with defaults we measured 27–31 s per crash. With 20/2/5 we measured 19–20 s. Step 3 logs `Violated the rule "loop_wait + 2*retry_timeout <= ttl"` and Patroni lowers `loop_wait`. A `ttl` below 20 is raised to 20 (`ttl=10 can't be smaller than 20, adjusting...`).

<details><summary>Hint</summary>

`docker logs --since 1m m20-pgN | grep -i -E "violated|adjust"` on the leader shows the adjustments. For question 4, think about what has to happen before anyone may take the leader key after a `docker kill`, and about the false positives of Section 4.1.
</details>

---

## Exercise 6 ★★: Network partitions

Use `./scripts/partition.sh isolate|heal <container>` with a write loop running in another terminal (`./scripts/writeloop.sh --duration 90`).

1. **Isolate the leader.** From its log, note when it stopped being primary. From `patronictl list` on another node, note when a new leader appeared. Compute the margin between the two. Heal it and watch it rejoin.
2. **Isolate a replica.** What happens to writes? What does `patronictl list` show after about 30 s? What would a client that is connected *directly* to the isolated replica experience?
3. **Isolate one etcd member** (say `m20-etcd2`). What happens? Check `./scripts/etcd.sh health`.
4. Explain why the margin in step 1 must always be positive, and which Patroni rule guarantees it.

**Self-check:** (1) The isolated leader logs `demoting self because DCS is not accessible and I was a leader` about 10–20 s after the cut. The new leader appears about 25–30 s after the cut, and the margin was about 14 s in our run. (2) No write errors at all. The isolated replica disappears from the list after the member key's TTL and reappears, streaming, after healing. (3) No effect on Postgres at all. etcd keeps quorum with 2 of 3.

<details><summary>Hint</summary>

`docker logs --since 2m <leader> | grep -E "demot|DCS"`, and `watch -n1 ./scripts/cluster.sh` (or `docker exec -it m20-pg1 patronictl list -W`) on a node on the majority side. Rule: Section 5.4.
</details>

---

## Exercise 7 ★★: Synchronous mode under fire

1. Enable synchronous mode: `docker exec m20-pg1 patronictl edit-config -s synchronous_mode=true --force`. Which node became `Sync Standby`? What is `synchronous_standby_names` on the leader?
2. Start `./scripts/writeloop.sh --duration 60` and, after 10 s, `docker kill` the **sync standby**. Watch `synchronous_standby_names` change (`docker exec <leader> psql -U postgres -Atc "show synchronous_standby_names"` every couple of seconds). Did any write fail? Did any write become slow?
3. Kill the **other** replica too. What happens to writes, and to `synchronous_standby_names`?
4. Restart both replicas, set `synchronous_mode_strict=true`, and repeat step 3. What happens now? Start one replica again and note when writes resume.
5. Write down the RPO and the availability you get in steps 2, 3, and 4. Then put things back (`synchronous_mode_strict=false`, `synchronous_mode=false`).

**Self-check:** (2) No failed writes, but a stall of a few seconds (we saw about 6 s) until Patroni names the other replica. (3) Writes continue after one slow commit (5.7 s in our run), and `synchronous_standby_names` becomes empty. (4) `synchronous_standby_names = '*'`, and every commit blocks until a replica is back (writes resumed about 2 s after we started one).

<details><summary>Hint</summary>

The write loop prints `SLOW COMMIT` for any INSERT that took more than 1 s. `./scripts/etcd.sh | grep -A1 /sync` shows Patroni's `sync` key.
</details>

---

## Exercise 8 ★★: Connection strings that find the primary

Inside `m20-client` (`docker exec -it m20-client bash`), and without HAProxy:

1. Write a libpq connection string for Bazaar's **write** path that always lands on the current primary, gives up on a dead host after 2 seconds, and identifies itself as `bazaar-checkout` in `pg_stat_activity`.
2. Write one for **reporting reads** that uses a replica if there is one, falls back to the primary otherwise, and spreads connections across replicas.
3. Prove both: run each 20 times with `psql "<string>" -XAt -c '\echo :HOST'` and count the hosts with `sort | uniq -c`. Then stop both replicas (`docker stop m20-pgX m20-pgY`) and re-run the read string.
4. Open an **interactive** `psql` with string 1, run a query, then do `./scripts/switchover.sh` from another terminal. Run a write such as `CREATE TEMP TABLE t (x int);`. What does psql print, and which host are you connected to afterwards (`\conninfo`)?

**Self-check:** (3) the write string always lands on the primary. The read string with replicas up lands only on replicas, roughly evenly (we saw 24/36 over 60 connections), and with both replicas stopped it lands on the primary. (4) psql prints `FATAL: terminating connection due to administrator command`, then `The connection to the server was lost. Attempting reset: Succeeded.`, and `\conninfo` shows the **new** primary.

<details><summary>Hint</summary>

The keywords are `target_session_attrs`, `load_balance_hosts`, `connect_timeout`, and `application_name`. Which `target_session_attrs` value means "standby if possible, else anything"?
</details>

---

## Exercise 9 ★★: A restart that nobody notices

Bazaar needs `max_connections` raised from 100 to 120. That parameter needs a restart, and it must be the same on all nodes.

1. Apply it with `patronictl edit-config` and confirm that all three nodes show a pending restart.
2. With `./scripts/writeloop.sh --duration 60` running, perform a **rolling restart** that causes at most one short write interruption.
3. Why does the order of restarts matter for this particular parameter? What order would you use to *lower* it?

**Self-check:** the write loop reports exactly **one** outage, of about the length of a switchover (2.3 s in our run), and `0` lost writes. Afterwards no node shows a pending restart, and `show max_connections` returns `120` everywhere.

<details><summary>Hint</summary>

`patronictl restart bazaar-ha <member>` restarts one member. Which members can you restart without touching writes? For question 3: a hot standby refuses to start if some settings are lower than on the primary (Module 19).
</details>

---

## Exercise 10 ★★★: Losing the DCS

1. With a write loop running, stop **one** etcd member (`docker stop m20-etcd3`). Anything?
2. Stop a **second** one. Record when the first write fails, what Patroni logs on the leader, and how long writes stay down. Start both again.
3. Enable `failsafe_mode` (`patronictl edit-config -s failsafe_mode=true`) and repeat step 2.
4. Explain why failsafe mode is safe: what exactly must be true before the leader keeps running without the DCS? Construct a situation in which failsafe mode would still demote the leader.
5. Turn failsafe mode off again (`-s failsafe_mode=false`).

**Self-check:** (1) nothing visible. (2) The leader logs `demoting self because DCS is not accessible and I was a leader` about 19 s after the second etcd member stopped, and writes fail until etcd is back (24.6 s of failures in our run). (3) The leader logs `continue to run as a leader because failsafe mode is enabled and all members are accessible`, and the write loop reports `failed: 0`.

<details><summary>Hint</summary>

For question 4: combine an etcd outage with something from Exercise 6.
</details>

---

## Exercise 11 ★★★: Make an asynchronous failover lose data, then make Patroni refuse

Synchronous mode must be **off** for this exercise.

1. Start `./scripts/writeloop.sh --duration 75`. After 5 s, isolate **both replicas** (`partition.sh isolate` on each), wait 5 s, `docker kill` the leader, and immediately heal both replicas. Read the write loop's verification lines when it finishes.
2. Rejoin the old leader. Find the `pg_rewind` line that shows where the timelines diverged.
3. Repeat the setup, but instead of the write loop, create about 10 MB of data on the leader while the replicas are isolated (for example `CREATE TABLE ha_lab.big AS SELECT g, repeat('x', 1000) AS pad FROM generate_series(1, 10000) g;`). Kill the leader and heal the replicas. Does a failover happen? What do the replicas log?
4. Force the failover anyway. Which command, and what happens to `ha_lab.big`?
5. Would synchronous mode have prevented the loss in step 1? What would the write loop have shown instead?

**Self-check:** (1) The verification shows `acknowledged but LOST:` greater than zero (49 in our run, the writes made while the replicas were cut off). (3) There is **no** failover: both replicas log `My wal position exceeds maximum replication lag`, and `patronictl list` shows no leader with lags in MB. (4) After `patronictl failover --candidate <node> --force`, the cluster has a leader again, and `ha_lab.big` does not exist on it (and is discarded from the old leader by `pg_rewind` when it rejoins).

<details><summary>Hint</summary>

Why not `docker pause` the replicas instead of disconnecting them? Try it: the paused containers' kernel TCP buffers still accept the WAL, so nothing is lost. Disconnecting the network really stops the WAL.
</details>

---

## Exercise 12 ★★★: Where the pooler helps, and where it hurts

Run PgBouncer (the image from Module 17) in transaction mode **in front of HAProxy's read-write port**:

```bash
docker run -d --name m20-pgbouncer --network m20-net \
  -e DB_HOST=m20-haproxy -e DB_PORT=5000 -e DB_USER=postgres -e DB_PASSWORD=postgres \
  -e POOL_MODE=transaction -e AUTH_TYPE=scram-sha-256 -e LISTEN_PORT=6432 \
  edoburu/pgbouncer:v1.26.0-p0
```

[`lab/client/oneconn.py`](lab/client/oneconn.py) opens **one** connection, runs an INSERT every 0.2 s on it, and **never reconnects**.

1. Run two copies at the same time, one through HAProxy (`--dsn "host=m20-haproxy port=5000 dbname=postgres user=postgres"`) and one through PgBouncer (`--dsn "host=m20-pgbouncer port=6432 dbname=postgres user=postgres"`), each with `--duration 25`. Six seconds in, do `./scripts/switchover.sh`. Compare the two outputs.
2. Repeat the PgBouncer run three times. How long did the INSERTs stall during the switchover each time? Look at `docker logs m20-pgbouncer` for the reason when it was long.
3. Recreate the PgBouncer container with `-e SERVER_CONNECT_TIMEOUT=3 -e SERVER_LOGIN_RETRY=1` and measure again.
4. Which application bugs does PgBouncer hide here, and which new problem does it introduce? Remove the container afterwards (`docker rm -f m20-pgbouncer`).

**Self-check:** through HAProxy, the connection dies (`AdminShutdown`, then `the connection is closed`) and **0** statements succeed after the first failure. Through PgBouncer there are **0** errors, but a stall. In our runs it was sometimes about 2–3 s and sometimes **15 s**, with `closing because: connect timeout (age=15s)` in PgBouncer's log. With `server_connect_timeout = 3`, three runs stalled for at most about 3 s.

<details><summary>Hint</summary>

In transaction pooling, a client is attached to a server connection only during a transaction. What happens to an *idle* client when the server connection behind it dies? And what does PgBouncer do while its new server connection is stuck in HAProxy during the moments when no node passes the `/primary` check?
</details>
