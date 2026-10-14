# Module 20 Solutions: High Availability

<!-- nav:start -->
> [Theory](../README.md) · [Exercises](../exercises.md) · [Project](../project.md) · **Solutions**  
> [← 19 Replication](../../19-replication/README.md) · [Course home](../../../README.md) · [21 Sharding →](../../21-sharding/README.md)
<!-- nav:end -->

All outputs below come from the module's lab (3 etcd 3.5.17 + 3 Patroni 4.1.5 / PostgreSQL 17.11 + HAProxy 3.0) on a 4-CPU Linux machine, with Bazaar loaded at reset state 11. Node names, timelines, LSNs, and IP addresses will differ in your run. Timings will be similar, but not identical.

Files in this directory:

- [`bazaar_ha_client.py`](bazaar_ha_client.py): reference client for the project milestone.
- [`milestone_check.sh`](milestone_check.sh): the milestone's acceptance check B.

---

<!-- toc:start -->
**Contents**

- [Exercise 1 ★: The cluster's identity card](#exercise-1--the-clusters-identity-card)
- [Exercise 2 ★: Keep a replica out of the read pool](#exercise-2--keep-a-replica-out-of-the-read-pool)
- [Exercise 3 ★: What maintenance mode really means](#exercise-3--what-maintenance-mode-really-means)
- [Exercise 4 ★★: Diagnose the incident](#exercise-4--diagnose-the-incident)
- [Exercise 5 ★★: Tune the failover timings, and measure](#exercise-5--tune-the-failover-timings-and-measure)
- [Exercise 6 ★★: Network partitions](#exercise-6--network-partitions)
- [Exercise 7 ★★: Synchronous mode under fire](#exercise-7--synchronous-mode-under-fire)
- [Exercise 8 ★★: Connection strings that find the primary](#exercise-8--connection-strings-that-find-the-primary)
- [Exercise 9 ★★: A restart that nobody notices](#exercise-9--a-restart-that-nobody-notices)
- [Exercise 10 ★★★: Losing the DCS](#exercise-10--losing-the-dcs)
- [Exercise 11 ★★★: Make an asynchronous failover lose data, then make Patroni refuse](#exercise-11--make-an-asynchronous-failover-lose-data-then-make-patroni-refuse)
- [Exercise 12 ★★★: Where the pooler helps, and where it hurts](#exercise-12--where-the-pooler-helps-and-where-it-hurts)

<!-- toc:end -->

## Exercise 1 ★: The cluster's identity card

```bash
./scripts/etcd.sh | grep -A1 initialize
for n in 1 2 3; do docker exec m20-pg$n pg_controldata /var/lib/postgresql/data/pg17 | grep identifier; done
./scripts/etcd.sh | grep -A1 '^/service/bazaar-ha/history' | tail -1 \
  | python3 -c "import json,sys; h=json.load(sys.stdin); print(len(h), h[-1])"
./scripts/cluster.sh
```

```text
/service/bazaar-ha/initialize
7696419998835892256
Database system identifier:           7696419998835892256
Database system identifier:           7696419998835892256
Database system identifier:           7696419998835892256
33 [33, 1257805336, 'no recovery target specified', '2026-10-14T08:18:34.845616+00:00', 'pg3']
+ Cluster: bazaar-ha (7696419998835892256) ---+-------------+-----+------------+-----+
| pg1    | m20-pg1 | Replica | streaming | 34 | ...
| pg2    | m20-pg2 | Replica | streaming | 34 | ...
| pg3    | m20-pg3 | Leader  | running   | 34 | ...
```

1. `initialize` holds the **database system identifier**: the random 64-bit number `initdb` writes into `pg_control`. Every physical copy of the cluster (each replica is a `pg_basebackup` of the original) has the same identifier, which is why it appears on all three nodes and in the header of `patronictl list`. Patroni checks it before letting a node join: a data directory from *another* cluster has a different identifier and is refused, which prevents accidentally streaming WAL into an unrelated database.
2. `history` had 33 entries, and the current timeline is 34. Each promotion creates a new timeline and appends one entry recording the switch LSN, the time, and the new leader. Starting from timeline 1, after *k* promotions you are on timeline *k*+1. (`pg_controldata` on a replica may show a smaller `Latest checkpoint's TimeLineID`: that is the timeline of its last restartpoint, not where it is replaying now. Patroni's `TL` column is the live value.)
3. The `leader` key. `pg_is_in_recovery() = false` tells you that a node *is running as a primary*, not that it is *entitled* to be one. During a partition (Section 11.3), the isolated old primary keeps answering `false` for ten or more seconds after it has lost its lease and while a replacement is being elected. During that window two nodes could say `false`. Only the leader key, guarded by etcd's consensus, is authoritative. That is also why HAProxy checks `/primary` (which requires the leader key) and not a SQL query.

---

## Exercise 2 ★: Keep a replica out of the read pool

The tag is `noloadbalance`. In `lab/patroni/pg2.yml`:

```yaml
tags: {noloadbalance: true}
```

```bash
docker restart m20-pg2
for p in /replica /read-only /health; do ./scripts/rest.sh all $p; done
docker exec m20-client sh -c 'for i in $(seq 1 10); do
  psql "host=m20-haproxy port=5001 dbname=postgres user=postgres" -XAtc "select host(inet_server_addr())"
done | sort | uniq -c'
```

```text
m20-pg1 GET /replica -> 200
m20-pg2 GET /replica -> 503
m20-pg3 GET /replica -> 503
m20-pg1 GET /read-only -> 200
m20-pg2 GET /read-only -> 503
m20-pg3 GET /read-only -> 200
m20-pg1 GET /health -> 200
m20-pg2 GET /health -> 200
m20-pg3 GET /health -> 200
     10 172.22.0.6          <- m20-pg1 (docker exec m20-client getent hosts 172.22.0.6)
```

`patronictl list` gained a `Tags` column showing `noloadbalance: true` for pg2. (pg3 was the leader, so `/replica` is 503 there for a different reason, while `/read-only` accepts the primary too.)

Prediction reasoning: `noloadbalance` affects only the *load-balancing* endpoints (`/replica`, `/read-only`, `/async`, ...). `/health` just asks "is Postgres running?", and the node is still a perfectly good failover candidate, because `noloadbalance` says nothing about `nofailover`. HAProxy's `replicas` section checks `/replica`, so it marked pg2 DOWN within two seconds, and all ten connections went to the only remaining replica.

Undo: set `tags: {}` again and `docker restart m20-pg2`.

Alternatives: you could keep pg2 in HAProxy's config but mark it `backup`, or give the analysts their own HAProxy port that checks `/replica?tag_role=analytics` (custom tags can be matched with `tag_` query parameters).

---

## Exercise 3 ★: What maintenance mode really means

**Step 1** (not paused): Patroni notices on its next HA loop that Postgres is not running and starts it again, as a replica of the current leader:

```text
WARNING: Postgresql is not running.
INFO: Lock owner: pg3; I am pg1
INFO: pg_controldata:
INFO: Local timeline=34 lsn=0/4AF96840
INFO: primary_timeline=34
```

`pg_isready` succeeded again after **6.5 s** and **20.3 s** in two attempts. The delay depends on where in its 10-second `loop_wait` the agent was when Postgres stopped, plus the start-up time.

**Steps 2–3** (paused):

```text
| pg1    | m20-pg1 | Replica | stopped   |    |     unknown |     |    unknown |     |
| pg2    | m20-pg2 | Replica | streaming | 34 |  0/4AF96840 |   0 | 0/4AF96840 |   0 |
| pg3    | m20-pg3 | Leader  | running   | 34 |             |     |            |     |
 Maintenance mode: on
m20-pg1 GET /health -> 503
```

The log said only `PAUSE: postgres is not running`, every loop, for as long as we waited. After `patronictl resume --wait`, Patroni started Postgres and the node was **streaming again 3 s later** (`no action. I am (pg1), a secondary, and following a leader (pg3)`).

**Step 4:** you pause when you need Postgres to stay in a state that Patroni would otherwise "repair". Examples: a planned stop of Postgres for manual surgery on the data directory, upgrading or restarting the Patroni agents themselves without touching Postgres (a stopped agent in paused mode leaves Postgres running), or a maintenance on the DCS during which you don't want any automatic action. The price is that **no failover happens while paused**. In Section 9 the cluster sat without a primary until it was resumed.

---

## Exercise 4 ★★: Diagnose the incident

**(a) Maintenance mode left on, and the primary has died.** There is no `Leader` row. pg1 (presumably the old primary, since it is the one stopped) is down, and because the cluster is paused, Patroni will **not** fail over. It only removed the leader lock (`PAUSE: removed leader lock because postgres is not running`). Bazaar is down for writes until a human acts. Decide whether pg1 can be restarted quickly. If not, `patronictl resume`: a healthy replica takes the free leader key within a loop or two (we saw pg2 take it within a second of `resume`), and pg1 rejoins later. Afterwards, add an alert on `patroni_is_paused == 1` for longer than a maintenance window.

**(b) The DCS is unreachable from the leader, and Patroni demotes it on purpose.** The leader can't renew its key, and it can't tell "I'm partitioned" from "etcd is down", so it fences itself. With all nodes up, the likely cause is an etcd problem: quorum lost (two members down), etcd overloaded or slow on disk, or a network problem between the database hosts and etcd. Patroni will **not** pick another primary while the DCS is unusable, so the cluster is read-only. Fix etcd first (`etcdctl endpoint status`). To survive this in the future, enable `failsafe_mode: true` (Exercise 10), which keeps the primary running as long as it can reach all other members over the REST API.

**(c) `maximum_lag_on_failover` is blocking a lossy failover.** Both remaining replicas are more than 1 MiB (the default) behind the last WAL position the old leader published in the DCS. Promoting either would silently drop 10–26 MB of committed transactions, so Patroni refuses, and the cluster has no primary. Your choices: bring the old primary back if at all possible (its WAL is the only complete copy); otherwise accept the loss **explicitly** with `patronictl failover --candidate <the less-lagging node> --force`. That is what Exercise 11 reproduces. Then investigate why the replicas lagged that much (network, slow disk, long queries holding replay back).

**(d) Synchronous mode's candidate rule.** In synchronous mode only the node in the `sync` key is guaranteed to have every acknowledged commit, so Patroni accepts a switchover only to that node (HTTP 412 otherwise). Either switch over without `--candidate` (Patroni picks the sync standby), or choose the sync standby. If you really need another node to be primary, switch over to the sync standby first and let Patroni pick a new sync standby, or use `failover`, which skips the check (Section 9).

**(e) Pooled connections that outlived the failover.** libpq checks `target_session_attrs=read-write` **only when a connection is opened**. The pool still holds connections opened to the old primary. When the old primary rejoins as a replica (or was demoted in place), those connections are now attached to a read-only node, and every write fails with SQLSTATE `25006` until the pool happens to recycle them. Fixes: treat `25006` (and the `57P0x`/`08xxx` classes) as "discard this connection and retry the transaction on a new one"; give pooled connections a maximum lifetime; validate connections on checkout; and route writes through HAProxy with `on-marked-down shutdown-sessions`, which closes such connections actively instead of leaving them open.

**(f) Asynchronous replication lost acknowledged commits, and `pg_rewind` threw them away.** The old primary had committed (and confirmed to customers) transactions that never reached the replica that was promoted. When it rejoined, `pg_rewind` rewound it to the divergence point `0/4AF2A2B0`, discarding those transactions on that node too. They are not in the database any more. Recovery: if you still have the old primary's WAL (from the WAL archive, Module 18, or a copy of its data directory taken before the rewind), you can decode the lost transactions (for example with `pg_waldump`, or by restoring a copy to a point in time and diffing) and re-enter them by hand. Prevention: synchronous mode for this kind of data (RPO 0), plus alerting on every unplanned failover, so that someone *looks* for lost transactions instead of finding out from finance.

---

## Exercise 5 ★★: Tune the failover timings, and measure

**Defaults** (`ttl 30, loop_wait 10, retry_timeout 10`), six `docker kill` drills:

```text
new leader running after: 30.9 s, 27.3 s, 27.8 s, 29.3 s, 29.3 s, 30.8 s
client gap:               30.3 s, 28.0 s, 28.1 s, 28.3 s, 29.2 s, 29.7 s
```

**`ttl 20, loop_wait 2, retry_timeout 5`**, three drills:

```bash
docker exec m20-pg1 patronictl edit-config -s ttl=20 -s loop_wait=2 -s retry_timeout=5 --force
```

```text
new leader running after: 19.8 s, 18.8 s, 20.1 s
client gap:               19.3 s, 19.6 s, 19.7 s
```

**`ttl 20, loop_wait 10, retry_timeout 10`** breaks the rule (10 + 2 × 10 = 30 > 20):

```text
WARNING: Violated the rule "loop_wait + 2*retry_timeout <= ttl", where ttl=20. Adjusting loop_wait from 10 to 1 and retry_timeout from 10 to 9
```

Patroni runs with the adjusted values, but `patronictl show-config` still shows what you stored (10/10/20), which is confusing. Always store a consistent set.

**Why `ttl` dominates.** After `docker kill`, nobody deletes the leader key. The replicas *watch* the key and react within moments of its disappearance, but it disappears only when its lease **expires**, which is `ttl` seconds after the last successful renewal. With `loop_wait 10`, the last renewal happened 0–10 s before the kill, so expiry comes 20–30 s after the kill. We measured mostly 27–31 s. With `loop_wait 2`, the last renewal was at most 2 s before the kill, so expiry is very consistently about `ttl` = 20 s after it. Then the promotion itself takes about a second.

**Risks of a small `ttl` on a real network.** The leader must renew within `ttl`, and must give up (demote) after `retry_timeout`. Short values make **false positives** likely: an etcd leader election (which itself takes a second or two), a GC pause, a slow fsync on the etcd disk, a congested link, or a VM migration can all delay a renewal by a few seconds. Each false positive is a real failover: an outage of the same length as the one you are trying to avoid, a timeline change, rewinds, and with async replication possibly lost transactions. Patroni's floor of 20 s reflects that. Lowering `loop_wait` also multiplies the load on etcd (every node writes every `loop_wait`), which matters with hundreds of clusters sharing one etcd. Most production clusters keep the defaults, or go to about 20/5/5 at most.

Restore: `patronictl edit-config -s ttl=30 -s loop_wait=10 -s retry_timeout=10 --force`.

---

## Exercise 6 ★★: Network partitions

**1. Isolating the leader** (`./scripts/failover-drill.sh partition`, or `partition.sh isolate <leader>` by hand). Leader's log, trimmed:

```text
07:24:46,989 INFO: Lock owner: pg3; I am pg3          <- last renewal before the cut
   07:24:44.887  docker network disconnect m20-net m20-pg3
07:24:55,945 ERROR: Error communicating with DCS
07:24:55,945 INFO: demoting self because DCS is not accessible and I was a leader
07:24:55,977 LOG:  received fast shutdown request
```

Majority side: new leader pg2 running at 07:25:10.1, **25.3 s** after the cut. The old leader stopped being a primary at 07:24:55.9, so the **margin was about 14 s**. The write loop saw 21 s of failing writes (25 s between successful commits), and 0 lost. After `partition.sh heal m20-pg3`, the node logged `Local timeline=4 ... primary_timeline=5`, took the new leader's history, and was streaming on timeline 5 within 15 s.

**2. Isolating a replica:** the write loop reported `failed: 0`. After about 30 s (the member key's TTL), the replica disappeared from `patronictl list`. After healing, it reappeared as `streaming` within a few seconds (`no action. I am (pg3), a secondary, and following a leader (pg1)`). A client connected *directly* to the isolated replica, from its side of the partition, would still be served, but with **stale data**: its Postgres stays up as a read-only hot standby and nobody fences replicas. That is one more reason to route reads through something that checks lag, such as HAProxy with `/replica?lag=...`, or an application-level staleness check using `pg_last_xact_replay_timestamp()` (Module 19).

**3. Isolating one etcd member:** nothing at all happened to Postgres or to writes. `./scripts/etcd.sh health` listed the two remaining members, one of them `IS LEADER: true`, and reported `Failed to get the status of endpoint http://m20-etcd2:2379`. Two of three is still a majority. Patroni's etcd client simply uses another member.

**4. Why the margin must be positive.** Split-brain needs two nodes accepting writes at the same time. The new leader can start only after the old key has **expired**: at least `ttl` after the old leader's last successful renewal. The old leader demotes itself when a renewal attempt fails: at most `loop_wait + retry_timeout` after its last success (the next loop starts within `loop_wait`, and the renewal is retried for up to `retry_timeout`). The rule `loop_wait + 2 × retry_timeout ≤ ttl` makes the demotion deadline smaller than the expiry with a whole `retry_timeout` to spare, for the demotion itself (stopping Postgres) and for clock-rate differences. The watchdog (Section 11.6) covers the remaining case where the old leader is too frozen to demote itself at all.

---

## Exercise 7 ★★: Synchronous mode under fire

```bash
docker exec m20-pg1 patronictl edit-config -s synchronous_mode=true --force
```

```text
| pg1    | m20-pg1 | Leader       | running   | 14 | ...
| pg2    | m20-pg2 | Replica      | streaming | 14 | ...
| pg3    | m20-pg3 | Sync Standby | streaming | 14 | ...
$ docker exec m20-pg1 psql -U postgres -Atc "show synchronous_standby_names"
pg3
$ ./scripts/etcd.sh | grep -A1 /sync
/service/bazaar-ha/sync
{"leader":"pg1","quorum":0,"sync_standby":"pg3"}
```

**2. Kill the sync standby:**

```text
07:40:54.370 kill pg3
07:40:56.575 pg3
07:40:58.699 pg3
07:41:00.833 pg2          <- Patroni chose the other replica
```

The write loop reported `failed: 0`, but only 26 commits in a 7-second window where it usually does about 70: commits **stalled** for about 6 s, waiting for a standby that would never answer, until Patroni (on its next loop) replaced it in `synchronous_standby_names`. Afterwards pg2 showed as `Sync Standby`.

**3. Kill the other replica as well** (non-strict):

```text
07:42:12.968 kill pg2 pg3
07:42:16.571 ssn='pg2'
07:42:18.770 ssn=''
07:42:18.877  SLOW COMMIT seq=73: the INSERT took 5.74s
attempts: 364   acknowledged: 364   failed: 0
```

With no candidate left, Patroni emptied `synchronous_standby_names`, and the primary carried on **asynchronously**. Writes were available, but every commit from then on existed on one machine only.

**4. Strict mode** (`-s synchronous_mode_strict=true`, both replicas killed):

```text
ssn='*'
562|IPC|SyncRep|active|INSERT INTO ha_lab.writes (run_id, seq) VALUES ($1, $2) RETU
...
07:44:38.811 start pg2
07:44:40.686  SLOW COMMIT seq=77: the INSERT took 36.07s
```

Patroni set `synchronous_standby_names = '*'` ("any standby"). With no standby connected, every commit waited (`wait_event = SyncRep` in `pg_stat_activity`) until we started pg2. The INSERT then completed 1.9 s later. (During the test we also cancelled two of the waiting INSERTs with `pg_cancel_backend()`: each one returned **success** with `WARNING: canceling wait for synchronous replication due to user request`. See the pitfall in Section 10.)

**5. Summary:**

| Step | RPO | Write availability |
|---|---|---|
| 2: sync standby dies, another replica available | 0 | yes, after a stall of a few seconds |
| 3: all replicas die, non-strict | **> 0** from then on (only the primary has new commits) | yes |
| 4: all replicas die, strict | 0 | **no**, writes block until a replica returns |

That is the CAP-style choice in its plainest form. For Bazaar's payments, many teams pick non-strict with **two** potential sync standbys (three nodes), plus an alert when the cluster runs without a sync standby. Losing both replicas at once is rare, and when it happens they would rather keep selling with an alert ringing than stop. Strict mode suits ledgers where an acknowledged write must never exist on a single machine.

Restore: `-s synchronous_mode_strict=false`, then `-s synchronous_mode=false` (or keep it on for the milestone).

---

## Exercise 8 ★★: Connection strings that find the primary

```text
# 1. writes
host=m20-pg1,m20-pg2,m20-pg3 dbname=bazaar user=postgres target_session_attrs=read-write connect_timeout=2 application_name=bazaar-checkout

# 2. reporting reads
host=m20-pg1,m20-pg2,m20-pg3 dbname=bazaar user=postgres target_session_attrs=prefer-standby load_balance_hosts=random connect_timeout=2 application_name=bazaar-reports
```

Test (with pg1 as leader):

```bash
docker exec m20-client sh -c 'for i in $(seq 1 20); do psql "<string 1>" -XAt -c "\echo :HOST"; done | sort | uniq -c'
docker exec m20-client sh -c 'for i in $(seq 1 20); do psql "<string 2>" -XAt -c "\echo :HOST"; done | sort | uniq -c'
docker stop m20-pg2 m20-pg3
docker exec m20-client sh -c 'for i in $(seq 1 10); do psql "<string 2>" -XAt -c "\echo :HOST"; done | sort | uniq -c'
```

```text
     20 m20-pg1                     <- writes: always the primary
      8 m20-pg2                     <- reads: only replicas, spread
     12 m20-pg3
     10 m20-pg1                     <- reads with both replicas stopped: falls back to the primary
```

`application_name` showed up as expected: `select application_name from pg_stat_activity where pid = pg_backend_pid()` returned `bazaar-checkout`.

Notes:

- `read-write` accepts a server whose sessions are writable (not in hot standby, and `default_transaction_read_only` off). `primary` only requires "not in hot standby". On a Patroni cluster both pick the leader, but `read-write` also rejects a primary that has been made read-only with `default_transaction_read_only`, which some teams use as a "maintenance switch".
- `prefer-standby` is what gives "replica if possible, else primary". `standby` would fail outright with both replicas down.
- Without `load_balance_hosts=random`, every reporting client would pick the **first** standby in the list, so pg2 would get all the read traffic while pg3 sat idle.

**4. Switchover under an interactive psql:**

```text
postgres=# INSERT ...;          -- or any statement after the switchover
FATAL:  terminating connection due to administrator command
server closed the connection unexpectedly
	This probably means the server terminated abnormally
	before or while processing the request.
The connection to the server was lost. Attempting reset: Succeeded.
```

The switchover shut the old primary down cleanly, which terminated our session (`57P01`). psql's automatic reset reconnects **with the same connection string**, so `target_session_attrs=read-write` sent it to the new primary, and `\conninfo` showed the new host. The statement that hit the dead connection was **not** retried. psql never re-runs it, and you would have to run it again yourself. Applications need the same logic explicitly (Section 14.3). In a non-interactive `psql -f script.sql` there is no reset: the script stops with `error: connection to server was lost`.

---

## Exercise 9 ★★: A restart that nobody notices

```bash
docker exec m20-pg1 patronictl edit-config -p max_connections=120 --force
docker exec m20-pg1 patronictl list -e        # all three: Pending restart * max_connections: 100->120

# with ./scripts/writeloop.sh --duration 60 running; the leader was pg3:
docker exec m20-pg1 patronictl restart bazaar-ha pg1 --force     # replica
docker exec m20-pg1 patronictl restart bazaar-ha pg2 --force     # replica
./scripts/switchover.sh                                          # pg3 -> a restarted replica
docker exec m20-pg1 patronictl restart bazaar-ha pg3 --force     # old leader, now a replica
```

```text
Success: restart on member pg1
Success: restart on member pg2
Success: restart on member pg3
08:22:55.613  WRITE FAILED seq=151: OperationalError: consuming input failed: server closed the connection unexpectedly
08:22:57.852  RECOVERED seq=153 on m20-pg1: writes were failing for 2.24s (2 failed attempts); gap between successful commits 2.34s
attempts: 528   acknowledged: 526   failed: 2
  acknowledged but LOST:            0
```

One outage of 2.3 s: the switchover. Restarting replicas never touched writes. Afterwards `patronictl list -e` showed no pending restarts, and `show max_connections` returned `120` on every node.

**Why the order matters.** A hot standby tracks locks, transactions, and prepared transactions of the primary in shared memory sized by `max_connections`, `max_prepared_transactions`, `max_locks_per_transaction`, `max_worker_processes`, and `max_wal_senders`. PostgreSQL therefore won't run a hot standby with any of these **lower** than on the primary: it refuses to start, or pauses recovery when it replays the primary's parameter change. When **raising**, restart the replicas first (they temporarily have more than the primary, which is fine), then switch over, then restart the old primary. When **lowering**, do the reverse: the primary must get the smaller value first (restart it via a switchover-and-restart), and only then the replicas. Patroni helps by applying these parameters from the shared dynamic configuration, but the restart order is up to you.

`patronictl restart bazaar-ha --pending` (all at once) would also have worked, but it restarts the leader too, which costs a short outage, and Patroni doesn't promise the replica-first order you need when lowering values.

---

## Exercise 10 ★★★: Losing the DCS

**1. One etcd member down:** nothing visible. The write loop reported 0 failures, and `etcd.sh health` showed 2 of 3 members.

**2. Two members down (failsafe off):**

```text
07:45:33.478 stop etcd2 etcd3
pg1 (leader):
07:45:52,385 ERROR: Error communicating with DCS
07:45:52,387 INFO: demoting self because DCS is not accessible and I was a leader
07:45:52,387 INFO: Demoting self (offline)
write loop:
07:45:52.460  WRITE FAILED seq=257: AdminShutdown: terminating connection due to administrator command
07:46:14.706 start etcd2 etcd3
07:46:17.058  RECOVERED seq=477 on m20-pg1: writes were failing for 24.60s (220 failed attempts)
```

With quorum gone, etcd rejects writes and consistent reads. The leader kept trying for about 19 s (one `loop_wait` plus `retry_timeout` of retries against the remaining member), then demoted itself. The replicas logged errors but couldn't do anything either: no election is possible without the DCS. When etcd came back, pg1 was the healthiest node and took the leader key again (timeline 14 → 15). The write loop also saw two `ReadOnlySqlTransaction` errors: HAProxy's connections still pointed at pg1 for a moment after it restarted as a read-only replica.

**3. Failsafe mode on:**

```text
07:47:21,721 ERROR: Error communicating with DCS
07:47:21,731 INFO: Got response from pg3 http://m20-pg3:8008/failsafe: Accepted
07:47:21,738 INFO: Got response from pg2 http://m20-pg2:8008/failsafe: Accepted
07:47:21,739 INFO: continue to run as a leader because failsafe mode is enabled and all members are accessible
attempts: 530   acknowledged: 530   failed: 0
```

**4. Why it is safe.** In failsafe mode, the leader keeps running without the DCS only if **every** member in the last known member list (which Patroni stores in the DCS while it is healthy) answers its `POST /failsafe` call and accepts it as leader. If all members can hear the leader, none of them can be on the other side of a partition, building a new majority and electing another leader. So the main risk of DCS-based HA, two leaders, is excluded by direct communication instead of by the DCS. And if the DCS is down, nobody could win an election through it anyway.

A situation where it still demotes: etcd quorum is lost **and** one replica is unreachable at the same time. For example, stop `m20-etcd2` and `m20-etcd3`, then `partition.sh isolate m20-pg3`. The leader's `POST /failsafe` to pg3 fails, so it can't prove that nobody else is acting, and it demotes, exactly as without failsafe mode. That is the correct choice: pg3's view of the world is unknown.

Restore: `-s failsafe_mode=false`. (In production, consider leaving it **on**.)

---

## Exercise 11 ★★★: Make an asynchronous failover lose data, then make Patroni refuse

**1. Losing acknowledged writes:**

```bash
./scripts/writeloop.sh --duration 75 &
sleep 5
for r in m20-pgA m20-pgB; do ./scripts/partition.sh isolate $r; done   # the two replicas
sleep 5
docker kill <leader>
for r in m20-pgA m20-pgB; do ./scripts/partition.sh heal $r; done
wait
```

```text
08:10:13.965  WRITE FAILED seq=96: OperationalError: consuming input failed: server closed the connection unexpectedly
08:10:37.798  RECOVERED seq=308 on m20-pg3: writes were failing for 23.83s (212 failed attempts)
attempts: 699   acknowledged: 487   failed: 212
verification on m20-pg3: rows in table 438
  acknowledged but LOST:            49 [47, 48, 49, 50, 51, 52, 53, 54, 55, 56]
```

While the replicas were cut off, the primary still held the leader key (it could reach etcd), so it kept accepting and **acknowledging** writes: asynchronous replication doesn't wait for anyone. Rows 47–95 existed only on the primary, which we then killed. When the replicas reconnected, the leader key expired, and the most advanced replica was promoted, without those 49 rows. They were lost even though every one of them had been confirmed to the client. The lag was only a few kilobytes, well under `maximum_lag_on_failover`, so Patroni had no reason to hesitate.

**2. Rejoining the old leader:**

```text
INFO: running pg_rewind from pg3
INFO: pg_rewind exit code=0
INFO:  stderr=pg_rewind: servers diverged at WAL location 0/4AF2A2B0 on timeline 25
pg_rewind: rewinding from last common checkpoint at 0/4900D6D0 on timeline 25
```

`0/4AF2A2B0` is where the old primary's history and the new timeline part ways. Everything the old primary wrote after it (our 49 rows) was rewound away, which completes the loss.

**3. Too much lag:** while the replicas were isolated, we created a 12 MB table on the leader, waited 11 s (so the leader published the new WAL position in the DCS `status` key), killed it, and healed the replicas:

```text
| pg1    | m20-pg1 | Replica | running | 26 |  0/4AF4AFF8 |  10 | 0/4AF4AFF8 |  10 |
| pg2    | m20-pg2 | Replica | running | 26 |  0/4A000000 |  26 | 0/4AF4AFF8 |  10 |
INFO: My wal position exceeds maximum replication lag
INFO: following a different leader because i am not the healthiest node
```

No leader, for as long as we waited. Both replicas knew from the `status` key that the leader had reached a WAL position 10 MB (and 26 MB) beyond theirs, which is far more than the default `maximum_lag_on_failover` of 1 MiB. They refused to promote rather than silently discard 10 MB of committed work.

**4. Forcing it:**

```bash
docker exec m20-pg1 patronictl failover --candidate pg1 --force
```

pg1 became leader on a new timeline. `SELECT to_regclass('ha_lab.big')` returned NULL: the table never reached it. When the old leader rejoined, `pg_rewind` reported `servers diverged at WAL location 0/4AF4AFF8 on timeline 26` and discarded the table there too. `patronictl failover` is the operator's explicit "I accept losing this data".

**5. With synchronous mode,** step 1 could not have lost anything. While the replicas were isolated, the primary could not get any commit confirmed, so the write loop's INSERTs would have **hung** (non-strict: until Patroni dropped the cut-off sync standby on its next loop and continued asynchronously, which opens the same hole a few seconds later; strict: until a replica returned). In strict mode, no acknowledged write could ever exist only on the primary, so `LOST` would be 0. In non-strict mode, the guarantee lasts only until Patroni removes the unreachable sync standby. Patroni then also refuses to fail over to a node that is not the recorded sync standby, which narrows but doesn't close the window. This is precisely the "RPO = 0, except while degraded" line in Section 10.

Why not `docker pause`? We tried it first. Paused containers don't run any process, but their **kernel** still accepts TCP data into socket buffers. The primary kept streaming WAL into the paused replicas' buffers, and after `docker unpause` the walreceivers read it all: `acknowledged but LOST: 0`. Disconnecting the network stops the data for real.

---

## Exercise 12 ★★★: Where the pooler helps, and where it hurts

**1. One connection each, switchover after 6 s:**

```text
== via HAProxy
08:13:38.061  ok (server 172.22.0.6)
08:13:45.541  ERROR AdminShutdown: terminating connection due to administrator command
08:13:45.741  ERROR OperationalError: the connection is closed
statements ok: 37, failed: 88, ok AFTER the first failure: 0

== via PgBouncer
08:13:38.085  ok (server 172.22.0.6)
08:14:00.973  ok (server 172.22.0.8)
statements ok: 48, failed: 0, ok AFTER the first failure: 0
```

Through HAProxy, the client's TCP connection is spliced through to one Postgres backend. When that backend is terminated, the client's connection is gone for good, and a client that doesn't reconnect is dead. Through PgBouncer in **transaction mode**, the client is attached to a server connection only during a transaction. Between our autocommit INSERTs it held no server connection, so when PgBouncer's server connection died, the client didn't notice. Its next INSERT waited for PgBouncer to open a new server connection through HAProxy, which landed on the new primary. Zero errors.

**2. But look at the timestamps:** no INSERT completed between 08:13:45 and 08:14:00, a **15-second stall** (48 statements in 25 s instead of about 120). Repeating the run gave stalls of 15 s and about 2 s. PgBouncer's log for a slow run:

```text
S-0x7f8d04cfa3d0: postgres/postgres@172.22.0.9:5000 closing because: server conn crashed? (age=7s)
S-0x7f8d04cfa3d0: postgres/postgres@172.22.0.9:5000 new connection to server (from 172.22.0.10:33236)
S-0x7f8d04cfa3d0: postgres/postgres@172.22.0.9:5000 closing because: connect timeout (age=15s)
S-0x7f8d04cfa6b8: postgres/postgres@172.22.0.9:5000 new connection to server (from 172.22.0.10:55064)
```

PgBouncer reconnected *during* the switchover, at a moment when HAProxy had no healthy primary (the old one was shutting down, and the new one wasn't promoted or hadn't passed its `/primary` check yet). That connection attempt never got a server greeting, and PgBouncer waited its default `server_connect_timeout` of **15 s** before trying again. Whether you hit this window is a race, which is why the stall varied between runs.

**3. With `server_connect_timeout = 3` and `server_login_retry = 1`:** three runs completed 97, 94, and 98 statements of about 110 possible, so the stalls were roughly 2–3 s each, and still 0 errors.

**4. What PgBouncer hides, and what it adds.**

- *It hides* the need to reconnect for **idle** clients in transaction mode, and it absorbs the reconnection storm. Hundreds of app connections don't all hit the new primary at once, only PgBouncer's pool does. That makes failovers gentler on the database.
- *It doesn't hide* the failure of a transaction that was **in flight** when the server died. That client still gets an error, and it still needs retries and idempotency (Section 14). Seeing "zero errors" in a test with tiny autocommit statements can lull you into skipping that work.
- *It adds* another timeout layer that you must tune for HA (`server_connect_timeout`, `server_login_retry`, `query_wait_timeout`), plus a component that must itself be redundant. It also slightly hides failovers from monitoring: the application sees latency spikes instead of errors.
- In **session** pooling mode, the client is bound to one server connection for its whole session, and it would have failed just like the HAProxy client.

Clean up: `docker rm -f m20-pgbouncer`.
