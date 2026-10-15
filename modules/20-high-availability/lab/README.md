# Module 20 lab: Patroni + etcd + HAProxy

A three-node PostgreSQL 17 cluster managed by Patroni, with a three-member etcd cluster as the DCS, HAProxy in front, and a client container for the measuring scripts. The theory and the measured results are in the [module README](../README.md).

```text
                  m20-net (docker network)
  m20-etcd1  m20-etcd2  m20-etcd3          etcd v3.5.17 (Raft, 3 members)
       \         |         /
  m20-pg1    m20-pg2    m20-pg3            Patroni 4.1.5 + PostgreSQL 17, REST API :8008
       \         |         /
           m20-haproxy                     :5000 read-write, :5001 read-only, :7000 stats
                 |                         (published on 127.0.0.1 of your machine)
           m20-client                      psql, Python 3, psycopg 3: runs the client scripts
```

<!-- toc:start -->
**Contents**

- [Quick start](#quick-start)
- [Files](#files)
- [Scripts](#scripts)
- [Why three etcd members?](#why-three-etcd-members)

<!-- toc:end -->

## Quick start

```bash
./scripts/up.sh                 # build m20-patroni:17 (first time) and start everything
./scripts/load-bazaar.sh        # Bazaar at reset state 20, loaded into the current leader
./scripts/load-bazaar.sh 11     # ...or the small dataset (seconds, ~2 GB instead of ~10 GB of disk in total)
./scripts/cluster.sh            # patronictl list
./scripts/failover-drill.sh kill
./scripts/down.sh               # remove containers, network, and volumes (--image: also the image)
```

From your machine you can also connect through HAProxy: `psql "host=127.0.0.1 port=5000 user=postgres password=postgres dbname=bazaar"`. If a port is taken (on macOS, AirPlay Receiver uses 5000), change it with `M20_RW_PORT`, `M20_RO_PORT`, or `M20_STATS_PORT`, for example `M20_RW_PORT=15000 ./scripts/up.sh`. HAProxy's stats page is at <http://127.0.0.1:7000/>.

**If `apt-get` fails while building** (some corporate networks block the Debian mirrors), build the fallback image, which copies Python from the official `python` image instead: `M20_DOCKERFILE=Dockerfile.prebuilt ./scripts/up.sh`. It has no `curl`/`jq`; use `./scripts/rest.sh` for the REST API.

**Resources:** about 2 GB of RAM. Disk: the image is about 0.8 GB; with the small dataset (reset state 11) the volumes use about 2 GB in total (mostly WAL that Patroni-managed Postgres keeps up to `max_wal_size`). The full reset state 20 is about 2 GB of data per node, about 3 GB with WAL, *times three*: plan for about 10 GB. The scripts assume Bash and GNU `date` (Linux, WSL, or macOS with coreutils).

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | `FROM postgres:17` + `python3-venv` + `pip install patroni[etcd3] psycopg[binary]` in `/opt/patroni`. |
| `Dockerfile.prebuilt` | The same, without apt (Python copied from `python:3.13-slim-trixie`). |
| `compose.yml` | All services, the `m20-net` network, and the `m20-*-data` volumes. |
| `patroni/patroni.yml` | Shared Patroni configuration, mounted as `/etc/patroni/00-common.yml`. |
| `patroni/pg1.yml` ... `pg3.yml` | Per-node configuration (name, addresses, tags), mounted as `/etc/patroni/10-node.yml`. Edit, then `docker restart m20-pgN`. |
| `haproxy/haproxy.cfg` | Read-write and read-only listeners with Patroni REST health checks. |
| `client/writeloop.py` | Writes a row every 100 ms, reports outages, verifies lost and unknown-outcome writes. |
| `client/oneconn.py` | One connection that never reconnects (Exercise 12). |

## Scripts

| Script | What it does |
|---|---|
| `up.sh` / `down.sh` | Start and wait for a healthy cluster / remove everything. |
| `cluster.sh [-W]` | `patronictl list` from any running node (`-W`: refresh every 2 s). |
| `etcd.sh [health]` | Dump Patroni's keys in etcd / show etcd members and their status. |
| `rest.sh <node\|all> <path>` | Call the Patroni REST API, e.g. `./scripts/rest.sh all /primary`. |
| `load-bazaar.sh [NN]` | Run `project/bin/reset.sh NN` against the current leader. |
| `writeloop.sh [args]` | Run `client/writeloop.py` in `m20-client` (`--duration`, `--interval`, `--dsn`). |
| `failover-drill.sh kill\|stop\|switchover\|partition [secs]` | Write loop + one failure + timing of the new leader + the client's report. |
| `kill-leader.sh [stop]` | `docker kill` (or `docker stop`) the current leader. |
| `rejoin.sh m20-pgN` | Start a node again and show how it rejoined (pg_rewind, basebackup, ...). |
| `switchover.sh [pgN]` | Planned switchover, optionally to a chosen candidate. |
| `partition.sh isolate\|heal <container>` | Disconnect/reconnect a container from `m20-net`. |

## Why three etcd members?

One etcd member would work, and saves a little memory, but then etcd itself is a single point of failure for the HA machinery. With `failsafe_mode: false` (the lab's default), losing that member demotes the primary within about 20 seconds (Exercise 10). Three members tolerate the loss of any one. The lab runs them in separate containers, as you would on separate machines.
