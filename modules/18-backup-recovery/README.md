# Module 18: Backup and Recovery

<!-- nav:start -->
> **Theory** · [Exercises](exercises.md) · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 17 Configuration, Maintenance and Monitoring](../17-operations/README.md) · [Course home](../../README.md) · [19 Replication →](../19-replication/README.md)
<!-- nav:end -->

It is 14:07 on a Tuesday. A support engineer, trying to clean up a few test orders in production, runs `DELETE FROM orders WHERE created_at > '2025-01-01'` in the wrong psql tab. The statement finishes in 300 ms. Thousands of real orders and their items are gone, the checkout keeps taking new orders on top of the hole, and every replica you built for high availability has faithfully deleted them too. What happens next depends entirely on decisions made months earlier: what you back up, how often, where you keep it, and whether anybody has ever practised a restore. This module is about making those decisions well, and about the PostgreSQL machinery (dumps, base backups, WAL archiving and point-in-time recovery) that lets you answer "bring Bazaar back to 14:06:59" with a calm "give me twenty minutes".

<!-- toc:start -->
**Contents**

- [What you'll learn](#what-youll-learn)
- [Before you start](#before-you-start)
- [1. What can go wrong, and what a backup must achieve](#1-what-can-go-wrong-and-what-a-backup-must-achieve)
  - [1.1 Failure modes](#11-failure-modes)
  - [1.2 RPO and RTO](#12-rpo-and-rto)
  - [1.3 The three families of backup](#13-the-three-families-of-backup)
- [2. Logical backups with `pg_dump`](#2-logical-backups-with-pg_dump)
  - [2.1 What `pg_dump` does](#21-what-pg_dump-does)
  - [2.2 The four formats](#22-the-four-formats)
  - [2.3 What a dump contains, and what it does not](#23-what-a-dump-contains-and-what-it-does-not)
  - [2.4 Version compatibility](#24-version-compatibility)
- [3. Restoring logical backups](#3-restoring-logical-backups)
  - [3.1 Plain dumps: `psql`](#31-plain-dumps-psql)
  - [3.2 Archive formats: `pg_restore`](#32-archive-formats-pg_restore)
  - [3.3 Sections, and why restores are ordered the way they are](#33-sections-and-why-restores-are-ordered-the-way-they-are)
  - [3.4 Restoring into a different database or a different server](#34-restoring-into-a-different-database-or-a-different-server)
  - [3.5 Selective restores: getting one table back](#35-selective-restores-getting-one-table-back)
- [4. Physical backups with `pg_basebackup`](#4-physical-backups-with-pg_basebackup)
  - [4.1 The idea: copy the files, then replay WAL](#41-the-idea-copy-the-files-then-replay-wal)
  - [4.2 Taking one](#42-taking-one)
  - [4.3 What is inside](#43-what-is-inside)
  - [4.4 Verifying a backup: `pg_verifybackup`](#44-verifying-a-backup-pg_verifybackup)
  - [4.5 Formats and compression, measured](#45-formats-and-compression-measured)
  - [4.6 Tablespaces](#46-tablespaces)
- [5. Continuous archiving](#5-continuous-archiving)
  - [5.1 From "a backup" to "any moment"](#51-from-a-backup-to-any-moment)
  - [5.2 `archive_mode` and `archive_command`](#52-archive_mode-and-archive_command)
  - [5.3 Monitoring the archiver, and what happens when it fails](#53-monitoring-the-archiver-and-what-happens-when-it-fails)
  - [5.4 `pg_receivewal`: streaming instead of shipping](#54-pg_receivewal-streaming-instead-of-shipping)
  - [5.5 `restore_command`: the way back](#55-restore_command-the-way-back)
- [6. Point-in-time recovery](#6-point-in-time-recovery)
  - [6.1 How a server knows it should recover](#61-how-a-server-knows-it-should-recover)
  - [6.2 Choosing the target](#62-choosing-the-target)
  - [6.3 Restoring: the procedure](#63-restoring-the-procedure)
  - [6.4 Timelines](#64-timelines)
- [7. Lab: recovering from an accidental DELETE](#7-lab-recovering-from-an-accidental-delete)
  - [7.1 Base backup, activity, the mistake](#71-base-backup-activity-the-mistake)
  - [7.2 Finding the exact moment with `pg_waldump`](#72-finding-the-exact-moment-with-pg_waldump)
  - [7.3 Restore into a separate server](#73-restore-into-a-separate-server)
  - [7.4 Verify, then promote](#74-verify-then-promote)
  - [7.5 Getting the data back into production](#75-getting-the-data-back-into-production)
- [8. pgBackRest](#8-pgbackrest)
  - [8.1 Why a dedicated tool](#81-why-a-dedicated-tool)
  - [8.2 Concepts: repository and stanza](#82-concepts-repository-and-stanza)
  - [8.3 Setting up: stanza-create and check](#83-setting-up-stanza-create-and-check)
  - [8.4 Full, differential and incremental backups](#84-full-differential-and-incremental-backups)
  - [8.5 Retention and expire](#85-retention-and-expire)
  - [8.6 `verify`](#86-verify)
  - [8.7 Point-in-time restore with `--delta`](#87-point-in-time-restore-with---delta)
- [9. Incremental backups in PostgreSQL 17](#9-incremental-backups-in-postgresql-17)
  - [9.1 WAL summaries](#91-wal-summaries)
  - [9.2 Taking an incremental chain](#92-taking-an-incremental-chain)
  - [9.3 Restoring: `pg_combinebackup`](#93-restoring-pg_combinebackup)
- [10. Testing restores and monitoring backups](#10-testing-restores-and-monitoring-backups)
  - [10.1 A backup you have not restored is a hope](#101-a-backup-you-have-not-restored-is-a-hope)
  - [10.2 Monitoring freshness](#102-monitoring-freshness)
- [11. A backup strategy for Bazaar, and the checklist](#11-a-backup-strategy-for-bazaar-and-the-checklist)
- [Summary](#summary)
- [What's next](#whats-next)

<!-- toc:end -->

## What you'll learn

- How to reason about backups in terms of **RPO** and **RTO**, and how to choose between logical backups, physical backups and continuous archiving.
- `pg_dump` in depth: the four output formats, compression, parallel dump and restore with `-j`, how a dump stays consistent, selective restores with `pg_restore -l`/`-L`, `--schema`/`--table`, restoring into a different database, and what `pg_dumpall --globals-only` adds.
- Version compatibility rules for dumps and restores.
- Physical backups with `pg_basebackup`: what happens on the server, what the backup contains (`backup_label`, `backup_manifest`, WAL), formats, compression, tablespaces, and verification with `pg_verifybackup`.
- Continuous archiving: `archive_mode`, writing a correct `archive_command`, `archive_library`, `archive_timeout`, `restore_command`, and monitoring the archiver.
- **Point-in-time recovery (PITR)**: recovery targets (`time`, `lsn`, `xid`, `name`, `immediate`), `recovery_target_action`, finding the exact moment of a mistake with `pg_waldump`, timelines and `.history` files.
- **pgBackRest**: repository, stanza, full/differential/incremental backups, retention, `verify`, PITR restore and delta restore.
- PostgreSQL 17's built-in **incremental backups**: `summarize_wal`, `pg_basebackup --incremental` and `pg_combinebackup`.
- How to test restores automatically, monitor backup freshness, and the backup checklist you should be able to tick for every production database.

## Before you start

The module uses two servers.

**1. The course database (`course-pg`) with the large dataset**, for the logical-backup sections (2 and 3), where realistic sizes and timings matter. If it is not already at the Module 18 state (the same data as the Module 16 and 17 states, so it is if you followed those modules), reset it; this takes a few minutes:

```bash
./project/bin/reset.sh 18
```

**2. The module's lab server (`m18-primary`)**, for everything physical: WAL archiving is switched on and pgBackRest is installed. It uses the small dataset, which keeps base backups and the WAL archive small (the large dataset would put several gigabytes of WAL into the archive just by loading it). From the repository root:

```bash
cd modules/18-backup-recovery/lab
docker compose up -d --build                     # builds postgres:17 + pgBackRest, starts m18-primary
COURSE_PG_CONTAINER=m18-primary ../../../project/bin/reset.sh 11
```

Open a psql session on the lab server with:

```bash
docker exec -it m18-primary psql -U postgres -d bazaar
```

> **If the image build fails** because your network blocks the Debian/PGDG package mirrors (the `Dockerfile` runs `apt-get install pgbackrest`), build from `Dockerfile.prebuilt` instead, which copies the pgBackRest binary from a public Docker Hub image: `M18_DOCKERFILE=Dockerfile.prebuilt docker compose up -d --build`. Everything else in the module is identical.

The lab contains:

| Path | What it is |
|---|---|
| `lab/docker-compose.yml` | The `m18-primary` service and four named volumes: `pgdata`, `archive` (the WAL archive), `backups` (base backups), `pgbackrest` (the pgBackRest repository). |
| `lab/conf/init-archiving.sql` | `ALTER SYSTEM` settings applied when the server is first created: `archive_mode`, `archive_command`, `summarize_wal`, ... |
| `lab/conf/pgbackrest.conf` | pgBackRest configuration (section 8). |
| `lab/scripts/archive-wal.sh` | The `archive_command` (section 5). |
| `lab/scripts/nightly-backup.sh` | Nightly base backup with verification and retention (section 10 and the milestone). |
| `lab/scripts/pitr-drill.sh` | An automated point-in-time-recovery drill that verifies row counts (section 10 and the milestone). |
| `lab/scripts/check-backup-freshness.sh` | A monitoring check for backup and archive freshness. |
| `lab/scripts/bazaar-activity.sql` | A burst of 200 checkouts, some of which are then marked paid and shipped, to generate realistic WAL. |

Module 16 explained the write-ahead log, LSNs, segments, checkpoints and crash recovery from the inside. This module puts that knowledge to work: **every physical backup technique is "copy the files, then replay WAL", which is crash recovery on purpose.** If any WAL term below feels hazy, reread Module 16's WAL sections first.

When you are done with the module, tear the lab down with `docker compose down -v` (the `-v` removes the volumes too).

## 1. What can go wrong, and what a backup must achieve

### 1.1 Failure modes

It helps to list concretely what a backup protects against, because each failure mode rules out some "solutions":

| Failure | Example | Does a replica help? | Does a nightly dump help? |
|---|---|---|---|
| Disk or server dies | NVMe failure, the cloud VM is retired | Yes, fail over to it (Module 20) | Yes, but you lose everything since the dump |
| Operator error | `DELETE` without `WHERE`, `DROP TABLE` on the wrong server, a bad migration | **No**: the replica replays the mistake within milliseconds | Partly: you can recover the data as of last night |
| Application bug | A release writes wrong prices for six hours before anyone notices | **No** | Partly |
| Silent corruption | Bad RAM or a firmware bug flips bits in a data page; nobody reads that page for weeks | **No**, physical replicas copy the damaged page | Maybe: the dump fails or contains the bad value |
| Malice | Ransomware encrypts the data directory, or an attacker with credentials drops the schema | **No** | Only if the attacker could not reach the backups |
| Site or region loss | Fire, flood, a cloud region outage | Only if the replica is elsewhere | Only if the dump is stored elsewhere |

Two lessons fall out of this table. First, **replication is not a backup.** Replicas (Module 19) protect against *hardware* failure; they make *logical* failures (human error, bugs, malice) worse, because they spread them instantly. Second, **a backup stored next to the database is not a backup either**: anything that destroys the server or the account (fire, ransomware, a compromised admin credential) destroys it too.

The classic rule of thumb is **3-2-1**: keep **3** copies of the data, on **2** different kinds of storage, with **1** copy off-site. A modern version adds "and at least one copy that the database server's credentials cannot delete", for example an object-storage bucket with versioning and object lock (immutability) enabled.

### 1.2 RPO and RTO

Two numbers turn "we need backups" into engineering requirements:

- **RPO, Recovery Point Objective**: how much data, measured in time, you can afford to lose. "RPO = 24 h" means losing everything since last night is acceptable. "RPO = 1 min" means at most the last minute of orders may vanish.
- **RTO, Recovery Time Objective**: how long the service may be down while you restore. "RTO = 4 h" versus "RTO = 15 min" leads to completely different designs.

```text
          last good backup        disaster             service back
                 │                    │                     │
 ────────────────●────────────────────✖─────────────────────●────────▶ time
                 │◀──── data lost ───▶│◀──── downtime ─────▶│
                 │        (RPO)       │        (RTO)        │
```

For Bazaar, a reasonable business answer is: losing a whole day of orders is unacceptable (orders are money, and customers have already been charged), so **RPO ≤ 1 minute**; and the shop can survive being down for a while in a disaster, but not a whole day, so **RTO ≤ 1 hour** for a full restore. Those two numbers immediately rule out "nightly `pg_dump` only" (RPO 24 h) and push you towards **physical backups plus continuous WAL archiving**, which is the core of this module.

> **In production:** RTO is dominated by boring things: how fast you can copy N gigabytes from the backup storage, how long WAL replay takes, and how long it takes a stressed human to find the runbook. You only learn those numbers by **timing real restores**. Section 10 automates that.

### 1.3 The three families of backup

PostgreSQL offers three fundamentally different approaches. You will usually combine them.

| | Logical backup (`pg_dump`) | Physical backup (`pg_basebackup`, pgBackRest) | Continuous archiving (WAL) |
|---|---|---|---|
| What is copied | SQL-level content: `CREATE` statements and table rows | The data directory files, byte for byte | Every WAL segment as it is completed |
| Granularity | One database, schema or table | The whole cluster (all databases) | The whole cluster |
| Restores to | The moment the dump started | The end of the backup | **Any moment** after a base backup (PITR) |
| Portable across major versions and platforms | **Yes** | No, same major version and architecture | No |
| Restore speed for large databases | Slow: rows are re-inserted and every index is rebuilt | Fast: copy files, replay a little WAL | Depends on how much WAL must be replayed |
| Typical use | Small databases, migrations, copying one table, long-term archives, upgrades | The foundation of disaster recovery | Combined with physical backups for a small RPO |

The rest of the module walks through them in that order, then puts them together into a strategy and a runbook for Bazaar.

## 2. Logical backups with `pg_dump`

### 2.1 What `pg_dump` does

`pg_dump` is an ordinary client. It connects to one database like any application, reads the system catalogs to learn which objects exist, and writes out the SQL needed to recreate them plus the contents of every table (as `COPY` data). Because it works at the SQL level, its output is independent of the server's on-disk format: a dump from PostgreSQL 17 on Linux can be restored into PostgreSQL 18 on macOS. That portability is its superpower; its weakness is that restoring means re-inserting every row and **rebuilding every index** from scratch.

**Consistency.** A dump of a live, busy database must not mix "before" and "after" states (an order without its items, say). `pg_dump` therefore runs inside a single `REPEATABLE READ` transaction (Module 10): every table is read through the **same MVCC snapshot**, taken when the dump starts. Writes continue normally while the dump runs and are simply invisible to it. The result is exactly the database as it was at the moment the dump began, even if the dump takes an hour.

To stop someone from dropping or altering a table halfway through, `pg_dump` also takes an `ACCESS SHARE` lock on every table it dumps, at the start. Those locks do not block reads or writes, but they do block DDL that needs an `ACCESS EXCLUSIVE` lock. Watch it on the course database while a dump runs (start `docker exec -u postgres course-pg pg_dump -d bazaar -Fc -f /tmp/during.dump` in one terminal, and query from another):

```sql
SELECT pid, application_name, backend_xmin, state, left(query, 60) AS query
FROM pg_stat_activity
WHERE application_name = 'pg_dump';

SELECT mode, count(*)
FROM pg_locks AS l
JOIN pg_stat_activity AS a USING (pid)
WHERE a.application_name = 'pg_dump' AND l.locktype = 'relation'
GROUP BY mode;

SET lock_timeout = '2s';
ALTER TABLE orders ADD COLUMN gift_note text;
```

```text
 pid | application_name | backend_xmin | state  |                            query
-----+------------------+--------------+--------+--------------------------------------------------------------
 536 | pg_dump          |         2261 | active | COPY bazaar.events_y2025m02 (id, occurred_at, user_id, sessi

      mode       | count
-----------------+-------
 AccessShareLock |   214

ERROR:  canceling statement due to lock timeout
```

Two consequences matter in production. The `ALTER TABLE` waits for the whole dump (and, as Module 10 showed, everything queued behind the `ALTER TABLE` waits too), so schedule migrations and dumps apart. And the dump's open snapshot (`backend_xmin`) holds back VACUUM for the entire duration of the dump, exactly like any long transaction (Module 16), so a multi-hour dump of a busy database causes bloat.

> **Under the hood:** with `--serializable-deferrable`, `pg_dump` waits for a moment when its snapshot is guaranteed free of serialization anomalies (Module 10) before starting. Only use it if the database relies on `SERIALIZABLE` transactions for correctness; it can delay the start of the dump.

### 2.2 The four formats

`pg_dump -F` chooses the output format:

| Format | Flag | Output | Compressed by default | Restore with | Parallel dump | Parallel restore | Selective restore |
|---|---|---|---|---|---|---|---|
| **plain** | `-Fp` (default) | One SQL script | No | `psql` | No | No | No (edit the file) |
| **custom** | `-Fc` | One binary archive file | Yes (gzip) | `pg_restore` | No | **Yes** | **Yes** |
| **directory** | `-Fd` | A directory: one file per table plus a table of contents | Yes (gzip) | `pg_restore` | **Yes** (`-j`) | **Yes** | **Yes** |
| **tar** | `-Ft` | A tar archive of the directory format | No | `pg_restore` | No | No | Yes |

All four contain the same information; they differ in packaging. Measured on the course database with the large Bazaar dataset (the `bazaar` database is 1.5 GB at the Module 18 state, roughly half of it indexes; later modules' data takes it to about 1.9 GB), writing inside the container:

```bash
docker exec -u postgres course-pg bash -c 'mkdir -p /tmp/dumps && cd /tmp/dumps &&
    time pg_dump -d bazaar -Fc -f bazaar.dump'      # and so on for each format
```

| Command | Time | Size |
|---|---:|---:|
| `pg_dump -Fp -f bazaar.sql` | 8.5 s | 779 MB |
| `pg_dump -Fc -f bazaar.dump` | 24.6 s | 144 MB |
| `pg_dump -Fc -Z zstd -f bazaar-zstd.dump` | 8.6 s | 152 MB |
| `pg_dump -Ft -f bazaar.tar` | 9.6 s | 780 MB |
| `pg_dump -Fd -f bazaar.dir` | 23.0 s | 144 MB |
| `pg_dump -Fd -j 4 -f bazaar-j4.dir` | 10.9 s | 144 MB |

What the numbers teach:

- **A dump contains no indexes**, only `CREATE INDEX` statements. The 1.5 GB database dumps to 779 MB of plain SQL, and to 144 MB once compressed: text data compresses very well.
- **Compression dominates the cost.** The custom format spends most of its 25 seconds in single-threaded gzip. `-Z zstd` (PostgreSQL 16+; also `lz4`, and levels like `-Z zstd:9`) is almost three times faster here for a similar size.
- **`-j 4` parallelizes across tables**, and only works with the directory format (each worker writes its own file). It halved the time; it cannot split one huge table, so the largest table bounds the total time. Each worker is a separate connection, and they all share the leader's snapshot (`pg_dump` *exports* its snapshot with `pg_export_snapshot()` and the workers import it), so the dump stays consistent.

Which to use? **Custom (`-Fc`) is the right default**: one file, compressed, restorable selectively and in parallel. Use **directory (`-Fd -j N`)** for large databases where dump time matters. Use **plain** for small schemas you want to read, diff or edit, and when the target is not PostgreSQL. Tar has no real advantage over the others.

> **Pitfall:** a plain-format dump made by `pg_dump` 17.6 or later starts with a line such as `\restrict ufkSLYcIIL3x...` and ends with the matching `\unrestrict`. These psql meta-commands (added in the August 2025 security releases) stop a malicious dump from running other psql meta-commands during the restore. Older `psql` versions do not know them and fail, so restore plain dumps with a `psql` at least as new as the `pg_dump` that wrote them.

### 2.3 What a dump contains, and what it does not

Restricting what is dumped:

```bash
pg_dump -d bazaar -Fc -n bazaar -f bazaar_schema_only.dump        # only schema "bazaar"
pg_dump -d bazaar -Fc -t 'bazaar.orders*' -f orders.dump          # tables matching a pattern
pg_dump -d bazaar -Fc -T 'bazaar.events_*' -f no_events.dump      # exclude tables
pg_dump -d bazaar -Fc --exclude-table-data='bazaar.events*' -f lean.dump  # keep definitions, skip rows
pg_dump -d bazaar --schema-only -f schema.sql                     # DDL only
pg_dump -d bazaar --data-only -Fc -f data.dump                    # rows only
```

`--exclude-table-data` is particularly useful for Bazaar: the clickstream `events` partitions are large and rebuildable, so a "developer copy" dump can keep their definitions but skip their rows.

A dump of one database does **not** contain:

- **Roles** (users and groups) and their passwords, and **tablespaces**. These are cluster-wide objects, not part of any database. Section 3.4 shows what happens when you forget them.
- Database-level settings such as `ALTER DATABASE bazaar SET search_path = ...` and the database's own privileges, **unless** you dump with `--create` (and then restore with `pg_restore --create`), which emits `CREATE DATABASE` plus those settings.
- Anything outside the database: `postgresql.conf`, `pg_hba.conf`, SSL certificates.

`pg_dumpall` dumps the whole cluster as one plain SQL script (every database plus the globals). It is rarely used for complete backups because it only produces plain format, but **`pg_dumpall --globals-only`** is the standard companion to per-database `pg_dump`s:

```bash
docker exec -u postgres course-pg pg_dumpall --globals-only > globals.sql
```

```text
CREATE ROLE bazaar_analyst;
ALTER ROLE bazaar_analyst WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB LOGIN NOREPLICATION NOBYPASSRLS PASSWORD 'SCRAM-SHA-256$4096:3XcMZTeD+8z0eiDLBO4bHg==$NUmC...';
CREATE ROLE bazaar_app;
ALTER ROLE bazaar_app WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB LOGIN NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 50 PASSWORD 'SCRAM-SHA-256$4096:bbYL...';
CREATE ROLE bazaar_owner;
ALTER ROLE bazaar_owner WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB NOLOGIN NOREPLICATION NOBYPASSRLS;
...
GRANT bazaar_readonly TO bazaar_analyst WITH INHERIT TRUE GRANTED BY postgres;
```

These are the roles from Module 15, with their SCRAM password hashes. Treat a globals file (and every dump) as a secret: anyone who has it has your data and can attempt offline attacks on the hashes. `--no-role-passwords` leaves the hashes out, for copies that go to less trusted places.

### 2.4 Version compatibility

Two rules cover almost every situation:

1. **Use the `pg_dump` of the newest version involved.** `pg_dump` can read servers back to PostgreSQL 9.2, and it knows how to emit SQL that the *new* version accepts. To move data from 15 to 17, run `pg_dump` 17 against the 15 server.
2. **Restore into the same or a newer major version.** Restoring into an *older* server is not supported: the SQL may use newer syntax, and an old `pg_restore` cannot read a newer archive format at all; it stops with an "unsupported version ... in file header" error. The archive header records the format version; for the lab's dumps, `pg_restore -l` reports `Dump Version: 1.16-0`.

This is why `pg_dump`/`pg_restore` is the classic (if slow) way to do major-version upgrades, and why it is also the escape hatch from PostgreSQL to anything else.

## 3. Restoring logical backups

### 3.1 Plain dumps: `psql`

A plain dump is a script, so you restore it by running it:

```bash
createdb bazaar_copy
psql -d bazaar_copy -v ON_ERROR_STOP=1 --single-transaction -f bazaar.sql
```

`ON_ERROR_STOP=1` aborts at the first error instead of ploughing on and leaving a half-restored database; `--single-transaction` makes the whole restore atomic (all or nothing). Without them, psql prints errors and keeps going, which is how people end up with databases that are "restored" except for three tables.

### 3.2 Archive formats: `pg_restore`

`pg_restore` reads custom, directory and tar archives. It can restore into a database, or (with `-f`) turn the archive back into a plain SQL script, which is handy for inspecting what it would do.

```bash
createdb bazaar_r1
pg_restore -d bazaar_r1 bazaar.dump                 # serial
createdb bazaar_r4
pg_restore -d bazaar_r4 -j 4 bazaar-j4.dir          # 4 parallel jobs
```

(run inside the container, in `/tmp/dumps`, as in 2.2). On the large dataset:

```text
pg_restore -d bazaar_r1 bazaar.dump                       52.5 s
pg_restore -d bazaar_r4 -j 4 bazaar-j4.dir                30.8 s
```

Drop the two copies afterwards (`dropdb bazaar_r1; dropdb bazaar_r4`): each is a full copy of the 1.5 GB `bazaar` database.

Restoring takes longer than dumping, because the server must rebuild every index and validate every foreign key. Parallel restore (`-j`, custom or directory format) runs table loads, index builds and constraint validation concurrently. The usual starting point for `-j` is the number of CPU cores of the server.

Useful options:

| Option | Effect |
|---|---|
| `--clean --if-exists` | Drop each object before recreating it (restore *over* an existing database). |
| `--create` | Create the database itself (connect with `-d postgres`); restores database-level settings. |
| `--no-owner` | Do not `ALTER ... OWNER TO` the original owners; everything is owned by the restoring user. |
| `--no-privileges` (`-x`) | Skip `GRANT`/`REVOKE`. |
| `--role=name` | `SET ROLE` before restoring, so objects are owned by that role. |
| `--single-transaction` | All or nothing (incompatible with `-j`). |
| `--exit-on-error` (`-e`) | Stop at the first error. By default `pg_restore` continues and reports `errors ignored on restore: N` at the end. |
| `--section=pre-data\|data\|post-data` | Restore only part of the archive (next subsection). |
| `--disable-triggers` | During a data-only restore, disable triggers (and FK checks) on the tables being loaded. Superuser only. |

### 3.3 Sections, and why restores are ordered the way they are

`pg_restore` (and the plain script) always restore in three **sections**:

1. **pre-data**: schemas, types, functions, tables (without constraints that need data), sequences.
2. **data**: the table contents (`COPY`), plus sequence values.
3. **post-data**: indexes, primary keys and unique constraints, foreign keys, triggers, rules, and policies.

Loading rows *before* creating indexes and constraints is much faster than maintaining them row by row, and it means triggers do not fire during the load. You can run the sections separately, which is the trick behind many selective restores.

### 3.4 Restoring into a different database or a different server

Restoring the dump into a brand new server shows the most common restore failure. Start an empty PostgreSQL 17 container and copy the dump and the globals file into it:

```bash
docker run -d --rm --name m18-other -e POSTGRES_PASSWORD=postgres postgres:17
docker exec course-pg cat /tmp/dumps/bazaar-zstd.dump | docker exec -i m18-other bash -c 'cat > /tmp/bazaar-zstd.dump'
docker exec course-pg cat /tmp/dumps/globals.sql     | docker exec -i m18-other bash -c 'cat > /tmp/globals.sql'
```

(`/tmp/dumps/globals.sql` is the globals file from section 2.3, written inside the container with `pg_dumpall --globals-only -f /tmp/dumps/globals.sql`.) Then, as `postgres` inside `m18-other`:

```bash
createdb bazaar
pg_restore -d bazaar -j 4 /tmp/bazaar-zstd.dump
```

```text
pg_restore: error: could not execute query: ERROR:  role "bazaar_owner" does not exist
Command was: ALTER SCHEMA bazaar OWNER TO bazaar_owner;
...
pg_restore: warning: errors ignored on restore: 221
```

The tables and data are there, but 221 statements failed: every ownership change and every `GRANT`. Adding `--no-owner --no-privileges` makes everything owned by the restoring user and skips the grants, and yet:

```text
pg_restore: error: could not execute query: ERROR:  role "bazaar_app" does not exist
Command was: CREATE POLICY orders_all_access ON bazaar.orders TO bazaar_app, bazaar_readonly USING (true) WITH CHECK (true);
...
pg_restore: warning: errors ignored on restore: 18
```

Row-level security **policies** name roles in their definition, so they cannot be created without them, and those are exactly the policies that keep sellers from seeing each other's orders (Module 15). And one more surprise:

```sql
SELECT count(*) FROM orders;
```

```text
ERROR:  relation "orders" does not exist
```

The data is in `bazaar.orders`, but the database-level `search_path` setting was not part of the dump. The complete, correct procedure for a new server is: restore the globals first, then restore the database **with `--create`**:

```bash
dropdb bazaar
psql -d postgres -f /tmp/globals.sql     # "role "postgres" already exists" is the one expected error
pg_restore -d postgres --create -j 4 /tmp/bazaar-zstd.dump
psql -d bazaar -c "SELECT count(*) FROM orders"
```

```text
 count
--------
 500000
```

with every table owned by `bazaar_owner`, all grants, all policies, and the `search_path` in place. Remove the container with `docker rm -f m18-other`.

### 3.5 Selective restores: getting one table back

The everyday use of a logical backup is not restoring the whole database; it is "an engineer broke one table, give me yesterday's version of it". Restore into a **scratch database** and copy what you need, never directly over production.

The obvious attempt, `-t`, restores *only* the table:

```bash
createdb scratch
pg_restore -d scratch -t orders bazaar.dump
```

```text
pg_restore: error: could not execute query: ERROR:  schema "bazaar" does not exist
LINE 1: CREATE TABLE bazaar.orders (
```

`-t` (and `-n`) select objects, not their dependencies: no schema, no `order_status` enum type, nothing. Two ways to get it right:

**A. Edit the table of contents.** `pg_restore -l` lists every entry of the archive; `pg_restore -L file` restores only the entries in the file, in the file's order:

```bash
pg_restore -l bazaar.dump | head -20
```

```text
;
; Archive created at 2026-10-14 08:19:10 UTC
;     dbname: bazaar
;     TOC Entries: 494
;     Compression: gzip
;     Dump Version: 1.16-0
;     Format: CUSTOM
...
;     Dumped from database version: 17.11 (Debian 17.11-1.pgdg13+2)
;     Dumped by pg_dump version: 17.11 (Debian 17.11-1.pgdg13+2)
;
; Selected TOC Entries:
;
14; 2615 16385 SCHEMA - bazaar bazaar_owner
4430; 0 0 ACL - SCHEMA bazaar bazaar_owner
2; 3079 16672 EXTENSION - btree_gist
...
1302; 1247 16609 TYPE bazaar order_status bazaar_owner
```

and every entry concerning `orders`:

```text
236; 1259 16475 TABLE bazaar orders bazaar_owner
4504; 0 0 COMMENT bazaar TABLE orders bazaar_owner
4506; 0 0 ACL bazaar TABLE orders bazaar_owner
4379; 0 16475 TABLE DATA bazaar orders bazaar_owner
3967; 2606 16485 CONSTRAINT bazaar orders orders_pkey bazaar_owner
4202; 2620 17359 TRIGGER bazaar orders orders_audit bazaar_owner
4203; 2620 17345 TRIGGER bazaar orders orders_set_updated_at bazaar_owner
4186; 2606 16491 FK CONSTRAINT bazaar orders orders_seller_id_fkey bazaar_owner
...
4362; 3256 18081 POLICY bazaar orders orders_all_access bazaar_owner
```

Keep the lines you need (you can also comment lines out with `;`):

```bash
pg_restore -l bazaar.dump |
  grep -E "SCHEMA - bazaar|TYPE bazaar order_status|TABLE bazaar orders |TABLE DATA bazaar orders |CONSTRAINT bazaar orders orders_pkey" \
  > orders.list
pg_restore -d scratch -L orders.list bazaar.dump
psql -d scratch -c "SELECT count(*), max(id) FROM bazaar.orders"
```

```text
 count  |  max
--------+--------
 500000 | 500000
```

Precise, but you must know every dependency. Here the identity default was lost (the sequence entry `orders_id_seq` was not in the list), which is fine for a scratch copy you only read from.

**B. Restore the whole schema, then only the data you want.** Simpler and more robust:

```bash
dropdb scratch && createdb scratch
pg_restore -d scratch --section=pre-data bazaar.dump                     # 0.3 s: all definitions, no rows
pg_restore -d scratch --data-only -t orders -t order_items bazaar.dump   # 3.0 s: just these rows
```

```text
 orders |  items  | audit_rows | products
--------+---------+------------+----------
 500000 | 1266928 |          0 |        0
```

Notice `audit_rows = 0`: the audit trigger on `orders` did not fire during the load, because triggers belong to post-data, which was never restored. The same goes for foreign keys, so `order_items` loaded although `products` is empty. In a scratch database that is exactly what you want. (When loading data-only into a database that *does* have triggers, use `--disable-triggers` as a superuser, or the triggers will fire for every restored row.)

From the scratch database you then copy the rows back to production with `INSERT ... SELECT` over `postgres_fdw`, or a `\copy` pipe; section 7.5 does exactly that after a point-in-time recovery, including a trap you should know about.

## 4. Physical backups with `pg_basebackup`

From here on, everything runs on the lab server `m18-primary` (small dataset), from the `modules/18-backup-recovery/lab` directory.

### 4.1 The idea: copy the files, then replay WAL

A **physical backup** is a copy of the data directory: the files you toured in Module 16. Copying the files of a *running* server cannot produce a consistent snapshot by itself: the copy takes time, and the server keeps changing pages while you copy them, so file A is copied as of 10:00:01 and file B as of 10:00:09. Some pages may even be copied half-written.

PostgreSQL makes this work with the same machinery as crash recovery (Module 16, section 9.6):

1. **Start**: the server performs a checkpoint and remembers its redo location, the *backup start LSN*. It also forces `full_page_writes` on for the duration of the backup.
2. **Copy** every file, in any order, while the server keeps running. Pages may be inconsistent; it does not matter.
3. **Stop**: the server records the *backup end LSN* and makes sure the WAL between start and end is available.
4. **Restore** = put the files back and **replay the WAL from the start LSN to (at least) the end LSN**. Every page change during the copy is in that WAL (with a full-page image for the first change of each page after the checkpoint), so replay repairs every torn or stale page. At the end LSN the copy is **consistent**.

A physical backup is therefore *useless without the WAL from its own start to its end*. Remember that sentence; most physical-backup mistakes violate it.

### 4.2 Taking one

`pg_basebackup` does all of this over a replication connection (the same protocol replicas use, Module 19), so it needs a role with the `REPLICATION` attribute (or a superuser), a `replication` line in `pg_hba.conf`, and a free `max_wal_senders` slot. On the lab server:

```bash
docker exec -u postgres m18-primary pg_basebackup \
    -D /backups/first -Fp -X stream -c fast -l "first backup" -v -P
```

```text
pg_basebackup: initiating base backup, waiting for checkpoint to complete
pg_basebackup: checkpoint completed
pg_basebackup: write-ahead log start point: 0/2000028 on timeline 1
pg_basebackup: starting background WAL receiver
pg_basebackup: created temporary replication slot "pg_basebackup_222"
32246/32246 kB (100%), 1/1 tablespace
pg_basebackup: write-ahead log end point: 0/2000120
pg_basebackup: waiting for background process to finish streaming ...
pg_basebackup: syncing data to disk ...
pg_basebackup: renaming backup_manifest.tmp to backup_manifest
pg_basebackup: base backup completed
```

The options:

| Option | Meaning |
|---|---|
| `-D dir` | Target directory (must be empty or not exist). |
| `-F p` / `-F t` | **Plain** (a ready-to-use data directory) or **tar** (one `.tar` per tablespace, easy to ship and compress). |
| `-X stream` | Stream the WAL generated during the backup **in parallel** over a second connection and include it in the backup (`pg_wal/` or `pg_wal.tar`). The backup is then self-contained. `-X fetch` fetches the WAL at the end instead (it can fail if the server recycled it meanwhile); `-X none` includes no WAL and relies on the archive. **Use `stream`.** |
| `-c fast` | Request an immediate checkpoint at the start. The default (`spread`) waits for a gentle, throttled checkpoint, which can take minutes. |
| `-l label` | A label stored in `backup_label`. |
| `-z`, `--compress=...` | Compression: `-z` is gzip on the client; `--compress=server-zstd` compresses on the server (less network traffic), `client-lz4`, `zstd:level=9` and so on. Tar format only for most methods. |
| `-r rate` | Throttle the transfer, e.g. `-r 100M`, to protect production I/O. |
| `-T old=new` | Relocate a tablespace (plain format). |
| `-P`, `-v` | Progress and verbose messages. |

The temporary replication slot in the output makes the server keep the WAL that the backup still needs until it is done (Module 19 explains slots).

### 4.3 What is inside

```bash
docker exec m18-primary ls /backups/first
docker exec m18-primary cat /backups/first/backup_label
```

```text
backup_label
backup_manifest
base
global
pg_commit_ts
...
pg_wal
pg_xact
postgresql.auto.conf
postgresql.conf

START WAL LOCATION: 0/2000028 (file 000000010000000000000002)
CHECKPOINT LOCATION: 0/2000080
BACKUP METHOD: streamed
BACKUP FROM: primary
START TIME: 2026-10-14 08:01:33 UTC
LABEL: first backup
START TIMELINE: 1
```

It looks like a data directory, because it is one, with two extra files:

- **`backup_label`** says "I am a backup; recovery must start from the checkpoint at `0/2000080`, replaying from `0/2000028`". Without it, a restored copy would trust `pg_control`, which may point to a *later* checkpoint that happened during the copy, and silently skip WAL it needed: corruption. **Never delete `backup_label` from a backup**; PostgreSQL renames it to `backup_label.old` itself once recovery has used it.
- **`backup_manifest`** (PostgreSQL 13+) is a JSON list of every file with its size and checksum, plus the WAL range the backup needs:

```text
{ "PostgreSQL-Backup-Manifest-Version": 2,
"System-Identifier": 7696430540803334183,
"Files": [
{ "Path": "backup_label", "Size": 212, "Last-Modified": "2026-10-14 08:01:33 GMT", "Checksum-Algorithm": "CRC32C", "Checksum": "0a149077" },
{ "Path": "PG_VERSION", "Size": 3, "Last-Modified": "2026-10-14 08:01:16 GMT", "Checksum-Algorithm": "CRC32C", "Checksum": "64440205" },
...
],
"WAL-Ranges": [
{ "Timeline": 1, "Start-LSN": "0/2000028", "End-LSN": "0/2000120" }
],
"Manifest-Checksum": "b59ffc418302b34af1b95d0c0131714ab40d8e1a5f4391ee231cb97388ad2eb8"}
```

Things deliberately **not** copied: the contents of `pg_wal/` from before the backup, `pg_replslot/` (slots), temporary files, `postmaster.pid`, and unlogged tables' data (they are empty after a restore, just as after a crash).

### 4.4 Verifying a backup: `pg_verifybackup`

The manifest makes backups verifiable *without restoring them*:

```bash
docker exec -u postgres m18-primary pg_verifybackup /backups/first
```

```text
backup successfully verified
```

It checks that every file in the manifest exists with the right size and checksum, that no unexpected files appeared, and (by running `pg_waldump` on the included WAL) that the WAL needed for consistency is present and parseable. To see it catch problems, flip one byte in the `orders` table's file and delete the backup's WAL:

```bash
docker exec -u postgres m18-primary bash -c '
  f=$(psql -d bazaar -Atc "SELECT pg_relation_filepath('"'"'bazaar.orders'"'"')")   # base/16384/16637
  printf "X" | dd of=/backups/first/$f bs=1 seek=100 conv=notrunc 2>/dev/null
  rm /backups/first/pg_wal/0000000100000000000000*
  pg_verifybackup /backups/first'
```

```text
pg_verifybackup: error: checksum mismatch for file "base/16384/16637"
pg_waldump: error: could not find any WAL file
pg_verifybackup: error: WAL parsing failed for timeline 1
```

> **Pitfall:** in PostgreSQL 17, `pg_verifybackup` only understands **plain**-format backups; for tar backups you must extract them first. (PostgreSQL 18 can verify tar-format backups directly.) Also, verification proves the backup is *what the server sent*; it cannot prove the server's data was not already corrupt. Only a test restore plus checks such as `pg_amcheck` (section 10) do that.

### 4.5 Formats and compression, measured

For comparison, the same measurements on `course-pg` with the large dataset, not on the lab server (the whole cluster, all databases plus shared catalogs, is 1.66 GB at the Module 18 state, a little more than the 1.5 GB `bazaar` database):

```text
pg_basebackup -D /backups/bb-plain -Fp -X stream -c fast                       4.0 s    1.7 GB
pg_basebackup -D /backups/bb-tgz -Ft -z -X stream -c fast                     69.7 s    421 MB (base.tar.gz)
pg_basebackup -D /backups/bb-zstd -Ft --compress=server-zstd -X stream -c fast  10.5 s    408 MB (base.tar.zst)
```

The uncompressed copy runs at disk speed. Gzip, single-threaded on the client, is 17 times slower. Zstandard on the server costs little and compresses better. Compare with `pg_dump`'s 144 MB: a physical backup is bigger because it contains every **index** and all the free space inside pages, but it restores without rebuilding anything.

### 4.6 Tablespaces

Tables in other tablespaces live outside the data directory, at the path given in `CREATE TABLESPACE`; the data directory only holds a symlink to them in `pg_tblspc/` (the directory you saw in Module 16's tour of the data directory). A plain-format backup recreates tablespaces **at their original absolute paths**, which collides with the live server when you back up to the same machine:

```sql
CREATE TABLESPACE cold LOCATION '/var/lib/postgresql/ts_cold';
CREATE TABLE bazaar.audit_archive (LIKE bazaar.audit_log) TABLESPACE cold;
```

```text
$ pg_basebackup -D /backups/ts-plain -Fp -X stream -c fast
pg_basebackup: error: directory "/var/lib/postgresql/ts_cold" exists but is not empty
```

Map it somewhere else with `-T`:

```bash
pg_basebackup -D /backups/ts-plain -Fp -X stream -c fast \
    -T /var/lib/postgresql/ts_cold=/backups/ts-plain-cold
ls -l /backups/ts-plain/pg_tblspc/
```

```text
lrwxrwxrwx 1 postgres postgres 22 Oct 14 08:01 17404 -> /backups/ts-plain-cold
```

In tar format each tablespace simply becomes its own archive named after its OID (`17404.tar`, next to `base.tar` and `pg_wal.tar`); at restore time you extract it wherever the tablespace should live and fix the symlink. Drop the demo objects afterwards (`DROP TABLE bazaar.audit_archive; DROP TABLESPACE cold;`).

> **Under the hood:** `pg_basebackup` is a client of the server's *base backup protocol*. Backup tools such as pgBackRest use the **low-level API** instead: `SELECT pg_backup_start('label', fast => true)`, copy the files themselves (in parallel, compressed, to S3...), then `SELECT * FROM pg_backup_stop()`, which returns the contents of `backup_label` for the tool to store with the copy. The session that called `pg_backup_start` must stay connected until `pg_backup_stop`. You rarely call these yourself, but now you know what your backup tool does.

## 5. Continuous archiving

### 5.1 From "a backup" to "any moment"

A base backup restores the database to the moment the backup ended. To get an RPO of minutes or seconds you would need base backups every few minutes, which is absurd. Instead, keep **every WAL segment** produced after the base backup. Then you can restore the base backup and replay WAL as far as you like: to the end of the archive (lose almost nothing), or to *any chosen moment* in between (point-in-time recovery, section 6).

```text
  base backup (Sunday 02:00)        WAL archive: every 16 MB segment, continuously
  ┌──────────────────────┐   ┌────┬────┬────┬────┬────┬────┬────┬────┬────┬────┐
  │ files + backup_label │ + │ 02 │ 03 │ 04 │ 05 │ .. │ .. │ .. │ .. │ C4 │ C5 │ ─▶ now
  └──────────────────────┘   └────┴────┴────┴────┴────┴────┴────┴────┴────┴────┘
                                    replay up to any point ──────────▶ ▲
                                                               "Tuesday 14:06:59"
```

### 5.2 `archive_mode` and `archive_command`

With **`archive_mode = on`** (restart required) the server runs an **archiver** process. Whenever a WAL segment is complete, the server creates `pg_wal/archive_status/<segment>.ready`; the archiver calls your **`archive_command`** for it, and on success renames the status file to `.done`. Only `.done` segments may be recycled at a checkpoint. `archive_mode = always` also archives on standbys (Module 19); `off` is the default.

`archive_command` is a shell command with two placeholders: `%p` (the path of the file to archive, relative to the data directory) and `%f` (its file name). The contract is simple and strict:

- Exit status **0** means "this file is safely archived". PostgreSQL may delete it right after.
- Any other exit status means "not archived". PostgreSQL keeps the file and retries later, forever.

The lab's `conf/init-archiving.sql` sets it up with `ALTER SYSTEM`:

```sql
ALTER SYSTEM SET wal_level = 'replica';
ALTER SYSTEM SET archive_mode = 'on';
ALTER SYSTEM SET archive_command = '/lab/scripts/archive-wal.sh %p %f';
ALTER SYSTEM SET archive_timeout = '60s';
ALTER SYSTEM SET summarize_wal = 'on';
```

(The lab deliberately uses `ALTER SYSTEM` instead of `postgres -c ...` flags in the compose file: **command-line settings take precedence over `postgresql.auto.conf`**, so a setting passed with `-c` could never be changed later with `ALTER SYSTEM`. Section 8 needs to change `archive_command` at runtime.)

The documentation's classic example is `test ! -f /archive/%f && cp %p /archive/%f`. It refuses to overwrite, but it is not good enough for production: `cp` does not `fsync`, so after a power loss on the archive host the "archived" file may be empty or truncated while PostgreSQL has already deleted its own copy. The lab's `scripts/archive-wal.sh` implements the three rules a correct archive command must follow:

```bash
if [[ -f "$dest" ]]; then
    if cmp -s "$src" "$dest"; then
        exit 0                      # already archived with identical contents: success (idempotent)
    fi
    echo "archive-wal: $dest exists with different contents, refusing to overwrite" >&2
    exit 1                          # never overwrite a different file
fi
cp "$src" "$dest.tmp"               # copy under a temporary name...
sync "$dest.tmp"                    # ...flush it to disk...
mv "$dest.tmp" "$dest"              # ...and atomically rename
sync "$dest_dir"                    # make the rename durable too
```

1. **Durable before success**: fsync before returning 0.
2. **Never overwrite** a different file: overwriting could destroy WAL from another timeline or another server that mistakenly uses the same archive.
3. **Idempotent**: if the server crashes after the copy but before recording success, it offers the same file again, and that must succeed.

Real archives are not on the same machine: they are another server (`rsync`/`scp` in the command, or `pg_receivewal`, below) or object storage. That is what tools like pgBackRest (section 8) and WAL-G give you, with compression, encryption and parallelism.

**`archive_timeout`** forces a segment switch after that many seconds if anything was written, so that a quiet database still archives its last changes promptly. Without it, a database that writes 1 MB per hour would keep its last 16 MB segment (and up to 16 hours of transactions!) unarchived. It directly bounds your RPO. Each forced switch archives a full 16 MB file, mostly empty but compressible, so do not set it to a few seconds.

**`archive_library`** (PostgreSQL 15+) is the alternative to a shell command: a loadable module that the archiver calls directly, avoiding a process fork per segment. PostgreSQL ships one example, `basic_archive`, which copies segments to `basic_archive.archive_directory` with the same safety rules as above. The two are mutually exclusive; setting both makes the archiver fail:

```text
FATAL:  both "archive_command" and "archive_library" set
DETAIL:  Only one of "archive_command", "archive_library" may be set.
```

> **Pitfall:** switching archive methods (or archive destinations) on a running system can leave a gap: segments archived by the old method are not in the new archive. After any change, take a new base backup into the new archive.

### 5.3 Monitoring the archiver, and what happens when it fails

`pg_stat_archiver` is the archiver's dashboard:

```sql
SELECT archived_count, last_archived_wal, failed_count, last_failed_wal, last_failed_time
FROM pg_stat_archiver;
```

A failing archive command does not stop the database. That is the problem: everything looks fine, while every unarchived segment **stays in `pg_wal`**. Simulate an archive destination that has become unwritable:

```bash
docker exec m18-primary chmod 500 /archive/wal       # postgres can no longer write there
```

After a few segment switches:

```text
 archived_count |            last_archived_wal             | failed_count |     last_failed_wal      |       last_failed_time
----------------+------------------------------------------+--------------+--------------------------+-------------------------------
             14 | 000000010000000000000009.00000028.backup |            6 | 00000001000000000000000A | 2026-10-14 08:02:13.145454+00

 ready_to_archive
------------------
                3
```

(`ready_to_archive` counts `.ready` files: `SELECT count(*) FROM pg_ls_archive_statusdir() WHERE name LIKE '%.ready'`.) The server log:

```text
cp: cannot create regular file '/archive/wal/00000001000000000000000A.tmp': Permission denied
LOG:  archive command failed with exit code 1
DETAIL:  The failed archive command was: /lab/scripts/archive-wal.sh pg_wal/00000001000000000000000A 00000001000000000000000A
WARNING:  archiving write-ahead log file "00000001000000000000000A" failed too many times, will try again later
```

The archiver tries each file three times, then pauses for a minute and starts over. Fix the permissions (`chmod 750`), and within a minute the backlog drains in order:

```text
 archived_count |    last_archived_wal     | failed_count | recovered
----------------+--------------------------+--------------+-----------
             18 | 00000001000000000000000D |            6 | t
```

If nobody notices, though, `pg_wal` grows by every segment written, until the disk is full and the server **stops with a PANIC**. A broken archive command is one of the classic ways to take down a production database. Two alerts are mandatory: `last_failed_time > last_archived_time` (failing right now), and `now() - last_archived_time` greater than a few times `archive_timeout` (stuck). Section 10's freshness check does both.

### 5.4 `pg_receivewal`: streaming instead of shipping

An archive command only sees *completed* segments, so the current segment (up to `archive_timeout` seconds of commits) is always at risk. **`pg_receivewal`** is the alternative: a client program, typically running on the backup server, that connects with the replication protocol and writes WAL to disk **as it is generated**, exactly like a standby's WAL receiver (Module 19). With a replication slot (`--slot`) the primary keeps WAL until `pg_receivewal` has it; with `--synchronous` it fsyncs after every write, and it can even act as a synchronous standby for an RPO of zero. Many backup setups use both: `archive_command` for completeness, `pg_receivewal` for the last few seconds.

### 5.5 `restore_command`: the way back

Recovery needs the inverse operation: a **`restore_command`** that copies an archived file back. `%f` is the file the server wants, `%p` is where to put it:

```text
restore_command = 'cp /archive/wal/%f %p'
```

It must return non-zero when the file does not exist, which is normal: during recovery the server asks for files that may or may not exist, for example `00000002.history` to discover timelines (section 6.4), and for the segment after the last one archived to find out where the archive ends. That is why recovery logs are full of harmless lines like `cp: cannot stat '/archive/wal/00000001.history': No such file or directory`.

## 6. Point-in-time recovery

### 6.1 How a server knows it should recover

When PostgreSQL starts, it looks for two signal files in the data directory:

| File | Meaning |
|---|---|
| `recovery.signal` | **Targeted recovery** (archive recovery): replay WAL from the archive, stop at the recovery target (or at the end of the available WAL), then finish according to `recovery_target_action`. |
| `standby.signal` | **Standby mode**: replay WAL forever, following a primary (Module 19). Takes precedence if both exist. |

With neither file, the server only performs crash recovery from `pg_wal` (Module 16). The recovery settings themselves are ordinary configuration parameters, normally appended to `postgresql.auto.conf` (until PostgreSQL 12 they lived in a separate `recovery.conf`, which you will still see in old blog posts; it no longer exists):

| Setting | Meaning |
|---|---|
| `restore_command` | How to fetch archived WAL (section 5.5). Required for archive recovery. |
| `recovery_target_time` | Stop at this timestamp (`'2026-10-14 08:04:14.5+00'`). Always include the time zone. |
| `recovery_target_xid` | Stop at the commit of this transaction ID. |
| `recovery_target_lsn` | Stop at this WAL position. |
| `recovery_target_name` | Stop at a named restore point created earlier with `SELECT pg_create_restore_point('before-migration-42')`. |
| `recovery_target = 'immediate'` | Stop as soon as the backup is consistent: restore exactly the base backup, nothing more. |
| `recovery_target_inclusive` | For `time`, `xid` and `lsn`: stop just **after** (default `on`) or just **before** (`off`) the target. |
| `recovery_target_timeline` | Which timeline to follow: `latest` (default), `current`, or a number (section 6.4). |
| `recovery_target_action` | What to do at the target: `pause` (default), `promote`, or `shutdown`. |

At most one `recovery_target*` may be set. With none, recovery replays everything in the archive and then promotes.

**Targets are evaluated at commit records.** Recovery checks the target condition on each transaction's commit (or abort) record: transactions that committed before the target are applied, and the first one at or after it is not (or is, with `inclusive`). Every transaction is applied entirely or not at all. A `recovery_target_time` therefore means "the database as of that instant", including transactions that were in progress at the time (they will be rolled back, since their commit comes later).

**`recovery_target_action`**:

- `pause` (the default) stops at the target with the server open for **read-only** queries. You can check whether you have the right moment, and either accept it with `SELECT pg_wal_replay_resume()` (which ends recovery and promotes) or shut down, adjust the target, and start again. If you went one transaction too far, you cannot go backwards: start again from the base backup.
- `promote` ends recovery and opens the database read/write immediately.
- `shutdown` stops the server at the target; useful in scripts that restore, then reconfigure, then start.

> **Pitfall:** if the WAL runs out before the target is reached (a gap in the archive, or a target in the future), PostgreSQL 13 and later refuse to continue: `FATAL: recovery ended before configured recovery target was reached`. Older versions silently promoted at whatever point they had reached, which is far worse.

### 6.2 Choosing the target

- **Time** is what humans know ("the DELETE happened around 14:07"), but it is imprecise: many transactions can commit in the same millisecond, and clocks differ between client and server.
- **`xid` or `lsn`** are exact, once you know them. `pg_waldump` finds them (section 7.2).
- **`name`** requires foresight: create restore points before risky operations (migrations, bulk deletes, data fixes). It costs one tiny WAL record. Bazaar's deploy pipeline should do it before every migration.
- **`immediate`** gives you the base backup itself, the fastest restore when you do not need anything newer.

### 6.3 Restoring: the procedure

1. **Do not overwrite the damaged cluster.** Restore to a new location (another directory, container, or machine). You may need the damaged cluster for forensics, or to salvage writes made after the mistake.
2. Copy the base backup into an empty data directory (owned by the server's OS user, mode 0700).
3. If the backup's `pg_wal/` contains WAL that is also in the archive, you may delete it; recovery fetches from the archive. If the damaged cluster's `pg_wal/` has *unarchived* segments (the archiver was behind), copy those too, or you lose their transactions.
4. Add the recovery settings to `postgresql.auto.conf` and **set `archive_mode = off`** (or point archiving elsewhere). Otherwise the restored server, once promoted, archives its new timeline into the production archive.
5. Create `recovery.signal`, start the server, and watch the log.
6. Verify (counts, the rows in question, `pg_amcheck`), then promote.

### 6.4 Timelines

When recovery ends and the server is promoted, it does not continue writing WAL with the same names. It starts a **new timeline**: the timeline number is the first 8 hex digits of every WAL file name (Module 16, section 9.2). It also writes a small **history file**, `0000000N.history`, recording where each timeline branched from its parent.

```text
timeline 1:  ──0001..02──0001..03──0001..04──0001..05──▶ (original history: the DELETE, later orders ...)
                                │
                                │ recovery stopped before xid 957, at 0/30B4750
                                ▼
timeline 2:                     └──0002..03──0002..04──▶ (the restored server's new history)

00000002.history:   1   0/30B4750   before transaction 957
```

Why not just continue timeline 1? Because the archive still holds the *original* timeline 1 segments after the branch point, containing the DELETE and everything after it. If the restored server wrote new WAL with the same names, it would collide with (or overwrite) the old history. With timelines, both histories coexist in the archive, and you can even recover again later along either one: `recovery_target_timeline = '1'` follows the original history, `'latest'` (the default) follows the newest timeline it finds history files for. It is the same mechanism Module 19 relies on when a standby is promoted.

## 7. Lab: recovering from an accidental DELETE

Time to do it for real, on the lab server with the small dataset.

### 7.1 Base backup, activity, the mistake

Take a base backup with the lab's nightly script (section 10 looks at it in detail), then generate some business activity:

```bash
docker exec -u postgres m18-primary /lab/scripts/nightly-backup.sh
docker exec -i m18-primary psql -U postgres -d bazaar < scripts/bazaar-activity.sql
```

```text
2026-10-14T08:04:01Z nightly-backup: starting base-20261014T080401Z
2026-10-14T08:04:01Z nightly-backup: verifying
2026-10-14T08:04:01Z nightly-backup: done: base-20261014T080401Z, 47 MB in 0s, starts at WAL 000000010000000000000002
2026-10-14T08:04:01Z nightly-backup: WAL cleanup: removing archived segments older than 000000010000000000000002
NOTICE:  placed 180 orders
```

```sql
SELECT count(*) AS orders, max(id) AS max_id, (SELECT count(*) FROM order_items) AS order_items FROM orders;
```

```text
 orders | max_id | order_items
--------+--------+-------------
    300 |    300 |         415
```

(The script places up to 200 orders through `place_order()`, skipping attempts that find no product with enough stock, so your count will differ slightly.) Now the mistake. Someone wanted to delete the test orders created in the last *minute*, and typed *hour*:

```sql
SELECT now() AS just_before_the_mistake;
DELETE FROM orders WHERE created_at > now() - interval '1 hour';
```

```text
    just_before_the_mistake
-------------------------------
 2026-10-14 08:04:14.501194+00

DELETE 180
```

All of today's orders are gone, and `ON DELETE CASCADE` took their `order_items` with them. The shop does not stop, so a customer places another order a few seconds later:

```sql
SELECT place_order(5, '[{"product_id": 2, "quantity": 1}]') AS new_order;
SELECT count(*) AS orders, max(id) AS max_id, (SELECT count(*) FROM order_items) AS order_items FROM orders;
```

```text
 new_order
-----------
       301

 orders | max_id | order_items
--------+--------+-------------
    121 |    301 |         236
```

Order 301 is a real order made *after* the mistake. Whatever we do must not lose it.

### 7.2 Finding the exact moment with `pg_waldump`

We know the mistake happened "around 08:04:14". To recover precisely, find the transaction. First make sure the WAL containing it is archived (`SELECT pg_switch_wal();`), then look for `DELETE` records on the `orders` table's file. `pg_relation_filepath('bazaar.orders')` returns `base/16384/16637`, so its relation is `1663/16384/16637` (tablespace OID `pg_default` = 1663, database OID, relfilenode):

```bash
docker exec -u postgres m18-primary pg_waldump --path=/archive/wal \
    --relation=1663/16384/16637 --rmgr=Heap 000000010000000000000003 | grep DELETE | head -3
```

```text
rmgr: Heap        len (rec/tot):     54/    54, tx:        957, lsn: 0/03099078, prev 0/03099040, desc: DELETE xmax: 957, off: 20, infobits: [KEYS_UPDATED], flags: 0x00, blkref #0: rel 1663/16384/16637 blk 3
rmgr: Heap        len (rec/tot):     54/    54, tx:        957, lsn: 0/030990B0, prev 0/03099078, desc: DELETE xmax: 957, off: 24, infobits: [KEYS_UPDATED], flags: 0x00, blkref #0: rel 1663/16384/16637 blk 3
rmgr: Heap        len (rec/tot):     54/    54, tx:        957, lsn: 0/030990E8, prev 0/030990B0, desc: DELETE xmax: 957, off: 28, infobits: [KEYS_UPDATED], flags: 0x00, blkref #0: rel 1663/16384/16637 blk 3
```

Transaction **957** deleted the rows. Its commit record, and its neighbours:

```bash
docker exec -u postgres m18-primary pg_waldump --path=/archive/wal --rmgr=Transaction \
    000000010000000000000003 | grep -E "tx: +95[5-8],"
```

```text
rmgr: Transaction len (rec/tot):     34/    34, tx:        955, lsn: 0/03094170, prev 0/03094130, desc: COMMIT 2026-10-14 08:04:02.248026 UTC
rmgr: Transaction len (rec/tot):     34/    34, tx:        956, lsn: 0/03098F68, prev 0/03098F28, desc: COMMIT 2026-10-14 08:04:02.249588 UTC
rmgr: Transaction len (rec/tot):     34/    34, tx:        957, lsn: 0/030B4750, prev 0/030B4710, desc: COMMIT 2026-10-14 08:04:14.519494 UTC
rmgr: Transaction len (rec/tot):     34/    34, tx:        958, lsn: 0/030B4FC0, prev 0/030B4F80, desc: COMMIT 2026-10-14 08:04:19.668883 UTC
```

Transaction 957 committed at 08:04:14.519, at LSN `0/030B4750`. Transaction 958 (five seconds later) is the new order 301. The precise target is "**stop before transaction 957 commits**": `recovery_target_xid = '957'` with `recovery_target_inclusive = off`.

> **In production:** the `orders` audit trigger (Module 09) gives you a second way to find the moment: `audit_log` rows with `operation = 'DELETE'`, their `changed_at` and the transaction that wrote them. Application logs and `log_statement = 'ddl'` or `'mod'` (Module 17) help too. `pg_waldump` is the tool of last resort that always works.

### 7.3 Restore into a separate server

Restore the base backup into a new volume, add recovery settings, and start a throw-away server `m18-restore` that can read the archive:

```bash
docker volume create m18-restore-pgdata
docker run --rm -v m18-lab_backups:/backups:ro -v m18-restore-pgdata:/var/lib/postgresql/data \
    m18-postgres-pgbackrest:17 bash -c '
  set -e
  cd /var/lib/postgresql/data
  cp -a /backups/base/base-20261014T080401Z/. .
  cat >> postgresql.auto.conf <<EOF
archive_mode = off
restore_command = '"'"'cp /archive/wal/%f %p'"'"'
recovery_target_xid = '"'"'957'"'"'
recovery_target_inclusive = off
recovery_target_action = '"'"'pause'"'"'
EOF
  touch recovery.signal
  chown -R postgres:postgres . && chmod 700 .'

docker run -d --name m18-restore \
    -v m18-restore-pgdata:/var/lib/postgresql/data -v m18-lab_archive:/archive:ro \
    m18-postgres-pgbackrest:17
docker logs m18-restore
```

(Replace the backup directory name with yours; the odd `'"'"'` sequences are just single quotes inside a single-quoted shell string.) The log tells the whole story:

```text
LOG:  database system was interrupted; last known up at 2026-10-14 08:04:01 UTC
LOG:  starting backup recovery with redo LSN 0/2000028, checkpoint LSN 0/2000080, on timeline ID 1
LOG:  restored log file "000000010000000000000002" from archive
LOG:  starting point-in-time recovery to XID 957
LOG:  redo starts at 0/2000028
LOG:  restored log file "000000010000000000000003" from archive
LOG:  completed backup recovery with redo LSN 0/2000028 and end LSN 0/2000120
LOG:  consistent recovery state reached at 0/2000120
LOG:  database system is ready to accept read-only connections
LOG:  recovery stopping before commit of transaction 957, time 2026-10-14 08:04:14.519494+00
LOG:  pausing at the end of recovery
HINT:  Execute pg_wal_replay_resume() to promote.
```

Read it line by line: recovery starts at the backup's redo point (from `backup_label`), fetches segments from the archive, reaches consistency at the backup's end LSN (from then on read-only queries are allowed), and stops right before the DELETE's commit.

### 7.4 Verify, then promote

The server is paused and read-only. Check that the orders are back:

```sql
-- on m18-restore
SELECT pg_is_in_recovery(), pg_get_wal_replay_pause_state();
SELECT count(*) AS orders, max(id) AS max_id, (SELECT count(*) FROM order_items) AS order_items FROM orders;
```

```text
 pg_is_in_recovery | pg_get_wal_replay_pause_state
-------------------+-------------------------------
 t                 | paused

 orders | max_id | order_items
--------+--------+-------------
    300 |    300 |         415
```

Exactly the state before the mistake: 300 orders and 415 items (and, correctly, no order 301, which came later). Promote:

```sql
SELECT pg_wal_replay_resume();
SELECT pg_is_in_recovery(), pg_walfile_name(pg_current_wal_lsn());
```

```text
 pg_is_in_recovery |     pg_walfile_name
-------------------+--------------------------
 f                 | 000000020000000000000003
```

The server is on **timeline 2**. Its `pg_wal/` now holds `00000002.history`:

```text
1	0/30B4750	before transaction 957
```

and `backup_label` has become `backup_label.old`, while `recovery.signal` is gone.

### 7.5 Getting the data back into production

Now there are two servers: production (with order 301 but without the 180 deleted orders) and the restored one (with the 180 orders but without 301). Which one becomes production?

- **Switch to the restored server** if nothing important happened after the mistake, or the mistake corrupted so much that salvaging is hopeless. Every write after the target is lost (order 301 here), unless you copy it over.
- **Copy the lost rows back** into production when production kept working. This is the usual answer for a localized mistake like a bad `DELETE`.

Here we copy the 180 orders and their items back. Load them into a staging schema on production first, so you can inspect them before touching the real tables:

```bash
docker exec m18-primary psql -U postgres -d bazaar -c "CREATE SCHEMA recovery" \
    -c "CREATE TABLE recovery.orders (LIKE bazaar.orders)" \
    -c "CREATE TABLE recovery.order_items (LIKE bazaar.order_items)"

docker exec m18-restore psql -U postgres -d bazaar -c "\copy (
        SELECT * FROM bazaar.orders
        WHERE created_at > '2026-10-14 08:04:14.501194+00'::timestamptz - interval '1 hour') TO STDOUT" |
  docker exec -i m18-primary psql -U postgres -d bazaar -c "\copy recovery.orders FROM STDIN"

docker exec m18-restore psql -U postgres -d bazaar -c "\copy (
        SELECT oi.* FROM bazaar.order_items AS oi JOIN bazaar.orders AS o ON o.id = oi.order_id
        WHERE o.created_at > '2026-10-14 08:04:14.501194+00'::timestamptz - interval '1 hour') TO STDOUT" |
  docker exec -i m18-primary psql -U postgres -d bazaar -c "\copy recovery.order_items FROM STDIN"
```

```text
COPY 180
COPY 180
```

(The `WHERE` clause repeats the mistaken `DELETE`'s condition, evaluated at the moment of the mistake. Passed with `psql -c`, a `\copy` may span lines as shown; typed interactively in psql, it must be on one line.)

Beware the obvious `INSERT ... SELECT` into `orders` and then into `order_items`: in this lab it produces 180 orders whose `total` is **doubled** for 176 of them. Remember Module 09: `orders.total` is maintained by a statement-level trigger on `order_items` that *adds* each inserted item to its order's total. The restored orders already carried their correct totals, and inserting their items added them again. Restoring rows into a live schema runs its triggers; think about each one. The correct version resets the totals before inserting the items, inside one transaction:

```sql
BEGIN;
INSERT INTO bazaar.orders OVERRIDING SYSTEM VALUE
SELECT r.* FROM recovery.orders AS r
WHERE NOT EXISTS (SELECT 1 FROM bazaar.orders AS o WHERE o.id = r.id);

-- order_items' trigger adds each item to orders.total, so start from 0
UPDATE bazaar.orders AS o SET total = 0
FROM recovery.orders AS r WHERE o.id = r.id;

INSERT INTO bazaar.order_items
SELECT r.* FROM recovery.order_items AS r
WHERE NOT EXISTS (SELECT 1 FROM bazaar.order_items AS oi
                  WHERE oi.order_id = r.order_id AND oi.product_id = r.product_id);
COMMIT;
```

```text
INSERT 0 180
UPDATE 180
INSERT 0 180
```

`OVERRIDING SYSTEM VALUE` is needed because `orders.id` is `GENERATED ALWAYS AS IDENTITY` (Module 02), and we want the original ids back. Verify, then clean up:

```sql
SELECT count(*) AS orders, max(id) AS max_id, (SELECT count(*) FROM order_items) AS order_items FROM orders;
SELECT count(*) AS totals_that_differ
FROM bazaar.orders AS o JOIN recovery.orders AS r USING (id)
WHERE o.total <> r.total;
DROP SCHEMA recovery CASCADE;
```

```text
 orders | max_id | order_items
--------+--------+-------------
    301 |    301 |         416

 totals_that_differ
--------------------
                  0
```

All 300 original orders, plus order 301, with correct totals. (The audit trigger also logged the repair in `audit_log`: the 180 `INSERT`s, plus the `UPDATE`s of those orders' totals, both the reset to 0 and the `order_items` trigger adding the items back. That is an honest record of the repair.) Remove the restore server with `docker rm -f m18-restore && docker volume rm m18-restore-pgdata`.

> **In production:** for larger repairs, connect the restored server to production with `postgres_fdw` (Module 21 introduces it) and run the `INSERT ... SELECT` directly across servers instead of piping `\copy`. And write the repair down in the incident report: what was restored, from which point, and how it was verified.

## 8. pgBackRest

### 8.1 Why a dedicated tool

Everything so far used PostgreSQL's built-in tools plus a few shell scripts, and it works. But look at what the scripts *don't* do: compress or encrypt WAL; copy files in parallel; ship to S3 or another data center; take backups that only copy what changed; keep track of which WAL belongs to which backup when expiring old ones; restore by changing only the files that differ; verify the repository end to end. Production teams almost always use a dedicated tool. The established ones are **pgBackRest**, **Barman** and **WAL-G**; they use the same low-level mechanisms you just learned (`pg_backup_start`/`stop`, archiving, `restore_command`), so everything in sections 4 to 7 still applies. This section uses pgBackRest, which is the most widely used.

### 8.2 Concepts: repository and stanza

- A **repository** (`repo1`, `repo2`, ...) is where backups and archived WAL are stored: a directory on a backup server, or an S3/GCS/Azure bucket. pgBackRest can write to up to four repositories at once (for example, a local one for fast restores and an S3 one for disaster recovery).
- A **stanza** is the configuration for one PostgreSQL cluster: where its data directory is and how to reach it. The name is arbitrary; the lab uses `bazaar`.
- Backup types: **full** (all files), **differential** (`diff`: files changed since the last *full*), **incremental** (`incr`: files changed since the last backup of any type). A diff needs its full; an incr needs its whole chain.

The lab's `conf/pgbackrest.conf` (mounted at `/etc/pgbackrest/pgbackrest.conf`):

```ini
[global]
repo1-path=/var/lib/pgbackrest
repo1-retention-full=2
compress-type=zst
compress-level=3
start-fast=y
process-max=2
log-level-console=info
log-level-file=detail

[global:archive-push]
compress-level=3

[bazaar]
pg1-path=/var/lib/postgresql/data
pg1-socket-path=/var/run/postgresql
```

In the lab the repository lives on the same machine (on the `pgbackrest` volume). In production, pgBackRest runs on a dedicated backup host (it reaches the database server over SSH or its own TLS protocol, configured with `pg1-host`) or writes to object storage (`repo1-type=s3`, with `repo1-s3-bucket`, `repo1-cipher-type=aes-256-cbc` for encryption, and so on).

### 8.3 Setting up: stanza-create and check

All pgBackRest commands run as the `postgres` OS user:

```bash
docker exec -u postgres m18-primary pgbackrest --stanza=bazaar stanza-create
docker exec -u postgres m18-primary pgbackrest --stanza=bazaar check
```

```text
P00   INFO: stanza-create for stanza 'bazaar' on repo1
P00   INFO: stanza-create command end: completed successfully (19ms)
P00   INFO: check command begin 2.54.2: ...
P00  ERROR: [068]: archive_command '/lab/scripts/archive-wal.sh %p %f' must contain pgbackrest
```

`check` verifies the whole pipeline, and it refuses our archive command: pgBackRest needs WAL to arrive in **its** repository, so `archive_command` must call `pgbackrest archive-push`. In the lab we keep the existing archive too (the drill script uses it) and run both. This "dual archiving" is also how you migrate between backup tools without a gap:

```sql
ALTER SYSTEM SET archive_command = '/lab/scripts/archive-wal.sh %p %f && pgbackrest --stanza=bazaar archive-push %p';
SELECT pg_reload_conf();
```

```text
P00   INFO: check repo1 configuration (primary)
P00   INFO: check repo1 archive for WAL (primary)
P00   INFO: WAL segment 000000010000000000000007 successfully archived to '/var/lib/pgbackrest/archive/bazaar/17-1/0000000100000000/000000010000000000000007-e64f35ebb6c2a6e4c4815ba5bc62e2d012103149.zst' on repo1
P00   INFO: check command end: completed successfully (134ms)
```

`check` forced a WAL switch and waited until the segment appeared in the repository, compressed, with its SHA-1 checksum in the name. (A production setup would use just `pgbackrest --stanza=bazaar archive-push %p`, typically with `archive-async=y`, which archives in parallel batches.)

### 8.4 Full, differential and incremental backups

```bash
docker exec -u postgres m18-primary pgbackrest --stanza=bazaar --type=full backup
```

```text
P00   INFO: execute non-exclusive backup start: backup begins after the requested immediate checkpoint completes
P00   INFO: backup start archive = 000000010000000000000008, lsn = 0/8000028
P00   INFO: check archive for segment 000000010000000000000008
P00   INFO: execute non-exclusive backup stop and wait for all WAL segments to archive
P00   INFO: backup stop archive = 000000010000000000000009, lsn = 0/9000050
P00   INFO: check archive for segment(s) 000000010000000000000008:000000010000000000000009
P00   INFO: new backup label = 20261014-080631F
P00   INFO: full backup size = 32.9MB, file total = 1356
P00   INFO: backup command end: completed successfully (2247ms)
P00   INFO: expire command begin 2.54.2: ...
P00   INFO: expire command end: completed successfully (1ms)
```

Recognize the steps from section 4: a "non-exclusive backup start" (that is `pg_backup_start`), copying, "backup stop" (`pg_backup_stop`), and then a check that **all WAL from start to stop is in the archive**, so the backup is guaranteed restorable. After every backup, `expire` applies the retention policy.

Generate some activity, then a differential, more activity, then an incremental:

```bash
docker exec -i m18-primary psql -q -U postgres -d bazaar < scripts/bazaar-activity.sql
docker exec -u postgres m18-primary pgbackrest --stanza=bazaar --type=diff backup
docker exec -i m18-primary psql -q -U postgres -d bazaar < scripts/bazaar-activity.sql
docker exec -u postgres m18-primary pgbackrest --stanza=bazaar --type=incr backup
docker exec -u postgres m18-primary pgbackrest --stanza=bazaar info
```

```text
stanza: bazaar
    status: ok
    cipher: none

    db (current)
        wal archive min/max (17): 000000010000000000000007/00000001000000000000000D

        full backup: 20261014-080631F
            timestamp start/stop: 2026-10-14 08:06:31+00 / 2026-10-14 08:06:33+00
            wal start/stop: 000000010000000000000008 / 000000010000000000000009
            database size: 32.9MB, database backup size: 32.9MB
            repo1: backup set size: 3.8MB, backup size: 3.8MB

        diff backup: 20261014-080631F_20261014-080639D
            timestamp start/stop: 2026-10-14 08:06:39+00 / 2026-10-14 08:06:41+00
            wal start/stop: 00000001000000000000000B / 00000001000000000000000B
            database size: 33MB, database backup size: 2.6MB
            repo1: backup set size: 3.8MB, backup size: 239.2KB
            backup reference total: 1 full

        incr backup: 20261014-080631F_20261014-080641I
            timestamp start/stop: 2026-10-14 08:06:41+00 / 2026-10-14 08:06:43+00
            wal start/stop: 00000001000000000000000D / 00000001000000000000000D
            database size: 33.2MB, database backup size: 2.4MB
            repo1: backup set size: 3.8MB, backup size: 167.5KB
            backup reference total: 1 full, 1 diff
```

How to read it: the 32.9 MB database became a 3.8 MB full backup (zstd). The diff copied only the 2.6 MB of files that changed since the full (239 KB compressed); the incremental only what changed since the diff. Labels encode the chain: `...F` is a full, `F_...D` a diff of that full, `F_...I` an incremental. Note that changed files are copied **whole**: a single changed row in a 1 GB table file means copying that 1 GB segment (pgBackRest's `repo1-bundle` and `repo1-block` options, block-level incremental, reduce this for big files).

A typical schedule: a full backup weekly, a diff daily, optionally incrementals every few hours; WAL archived continuously in between.

### 8.5 Retention and expire

`repo1-retention-full=2` keeps the two newest full backups, plus everything that depends on them, plus the WAL needed to restore any of them to any point after it. Two more full backups later:

```text
P00   INFO: new backup label = 20261014-080652F
P00   INFO: expire command begin 2.54.2: ...
P00   INFO: repo1: expire full backup set 20261014-080631F, 20261014-080631F_20261014-080639D, 20261014-080631F_20261014-080641I
P00   INFO: repo1: remove expired backup 20261014-080631F_20261014-080641I
P00   INFO: repo1: remove expired backup 20261014-080631F_20261014-080639D
P00   INFO: repo1: remove expired backup 20261014-080631F
P00   INFO: repo1: 17-1 remove archive, start = 000000010000000000000008, stop = 00000001000000000000000D
P00   INFO: expire command end: completed successfully (176ms)
```

The oldest full expired **together with its diff and incr** (they are useless without it), and so did the WAL that only those backups needed. This bookkeeping is exactly what is error-prone to script by hand (compare the `pg_archivecleanup` call in `nightly-backup.sh`). Retention can also be time-based (`repo1-retention-full-type=time` with `repo1-retention-full=14` keeps 14 days), which maps directly to "we can restore to any point in the last 14 days".

### 8.6 `verify`

```bash
docker exec -u postgres m18-primary pgbackrest --stanza=bazaar verify
```

```text
P00   INFO: verify command begin 2.54.2: ...
P00   INFO: verify command end: completed successfully (367ms)
```

`verify` reads every file in the repository (backups and WAL) and checks it against the checksums recorded at backup time, and checks that the WAL archive has no gaps. It catches bit rot and truncated uploads in the repository. Run it regularly (weekly), in addition to real test restores.

### 8.7 Point-in-time restore with `--delta`

Another accident: someone drops the `reviews` table. (The output below comes from a fresh run of the lab: a new full backup `20261014-082853F`, one burst of activity, then the drop.)

```sql
SELECT count(*) AS reviews FROM reviews;   -- 78
SELECT now() AS before_drop;               -- 2026-10-14 08:28:55.612545+00
DROP TABLE reviews;
```

pgBackRest restores into a **stopped** cluster. In the lab, stopping PostgreSQL stops the container, so run the restore in a one-off container of the same service (same volumes, same configuration):

```bash
docker exec m18-primary psql -U postgres -c "SELECT pg_switch_wal()"   # archive the DROP's segment
docker stop m18-primary
docker compose run --rm --no-deps -u postgres --entrypoint pgbackrest primary \
    --stanza=bazaar --delta --type=time "--target=2026-10-14 08:28:55.612545+00" \
    --target-action=promote restore
docker start m18-primary
```

```text
P00   INFO: restore command begin 2.54.2: --delta ... --target="2026-10-14 08:28:55.612545+00" --target-action=promote --type=time
P00   INFO: repo1: restore backup set 20261014-082853F, recovery will start at 2026-10-14 08:28:53
P00   INFO: remove invalid files/links/paths from '/var/lib/postgresql/data'
P00   INFO: write updated /var/lib/postgresql/data/postgresql.auto.conf
P00   INFO: restore global/pg_control (performed last to ensure aborted restores cannot be started)
P00   INFO: restore size = 31.5MB, file total = 1353
P00   INFO: restore command end: completed successfully (319ms)
```

pgBackRest picked the newest backup that ends **before** the target time on its own, wrote the recovery settings, and created `recovery.signal`. **`--delta`** means "the data directory already exists; compare each file's checksum with the backup and only copy files that differ". On a large database where the damage is one table, a delta restore copies a tiny fraction of the data, which can turn a one-hour restore into a few minutes. (Without `--delta`, pgBackRest requires an empty data directory.)

After the start:

```text
LOG:  recovery stopping before commit of transaction 1290, time 2026-10-14 08:28:55.614566+00
LOG:  selected new timeline ID: 2
LOG:  archive recovery complete
```

```sql
SELECT pg_is_in_recovery();              -- f
SELECT count(*) AS reviews FROM reviews; -- 78
```

The `reviews` table is back. `postgresql.auto.conf` now ends with:

```text
# Recovery settings generated by pgBackRest restore on 2026-10-14 08:28:58
restore_command = 'pgbackrest --stanza=bazaar archive-get %f "%p"'
recovery_target_time = '2026-10-14 08:28:55.612545+00'
recovery_target_action = 'promote'
```

`archive-get` is pgBackRest's `restore_command`. These leftover lines are harmless once recovery has finished (they are only read when a signal file exists), but remove them after the incident so that nobody is confused later.

Note what this restore did to production: **everything after the target, 08:28:55.612545, is gone**, on the server itself. That was acceptable here only because nothing else happened in between. With real traffic, restore to a separate location (`--pg1-path=/some/other/dir`, or another host) and copy the table back, as in section 7.5. Other useful restore options: `--set=<label>` to restore a specific backup, `--type=immediate` for the backup's consistent point, `--type=lsn`/`xid`/`name`, `--target-timeline`, and `--db-include=bazaar` to restore only one database of a cluster.

> **In production:** after any point-in-time restore, take a new **full backup** promptly. The server is on a new timeline, and while restoring along timelines works, a fresh full backup is the simplest guarantee that the next restore starts from the current history.

## 9. Incremental backups in PostgreSQL 17

### 9.1 WAL summaries

PostgreSQL 17 brought incremental backups into core. The challenge is knowing **which blocks changed** since the last backup without reading every file. The answer is the **WAL summarizer**: with `summarize_wal = on`, a background process (`walsummarizer`, Module 16) reads the WAL as it is written and records, per range of WAL, which blocks of which relations were modified. Those **WAL summaries** live in `pg_wal/summaries/` and are kept for `wal_summary_keep_time` (default 10 days):

```sql
SELECT count(*) AS summaries, min(start_lsn), max(end_lsn) FROM pg_available_wal_summaries();
SHOW wal_summary_keep_time;
```

```text
 summaries |    min    |    max
-----------+-----------+------------
        17 | 0/1000028 | 0/18000028

 wal_summary_keep_time
-----------------------
 10d
```

`pg_wal_summary_contents()` and the `pg_walsummary` command-line tool show what a summary contains. The lab enables `summarize_wal` from the start (it is a reload-only setting, so it can be turned on at any time; it only covers WAL written after that).

### 9.2 Taking an incremental chain

An incremental backup is taken *relative to the manifest of a previous backup*. The server looks up the summaries covering the WAL between that backup and now, and sends only the changed blocks:

```bash
docker exec -u postgres m18-primary bash -c 'mkdir -p /backups/incr && cd /backups/incr &&
    pg_basebackup -D full -c fast'
docker exec -i m18-primary psql -q -U postgres -d bazaar < scripts/bazaar-activity.sql
docker exec -u postgres m18-primary bash -c 'cd /backups/incr &&
    pg_basebackup -D incr1 -c fast --incremental=full/backup_manifest'
docker exec -i m18-primary psql -q -U postgres -d bazaar < scripts/bazaar-activity.sql
docker exec -u postgres m18-primary bash -c 'cd /backups/incr &&
    pg_basebackup -D incr2 -c fast --incremental=incr1/backup_manifest'
```

```text
full      total   50M   without pg_wal   34M
incr1     total   25M   without pg_wal  8.4M
incr2     total   25M   without pg_wal  8.2M
```

Every backup includes at least one 16 MB WAL segment (`-X stream`), which dominates the small lab's numbers; without it, each incremental is a quarter of the full. On a real database where a small fraction of blocks change per day, incrementals are a few percent of the full size. Inside, unchanged small files are copied whole, and changed relation files are stored as `INCREMENTAL.<relfilenode>` files holding only the modified blocks:

```text
files in incr2/base/16384:  382
of which INCREMENTAL.*:     273

-rw------- 1 postgres postgres 147456 Oct 14 08:07 full/base/16384/16637
-rw------- 1 postgres postgres  73728 Oct 14 08:07 incr2/base/16384/INCREMENTAL.16637
```

The `backup_label` of an incremental records what it is relative to (timeline 2, because this lab server had just been through the pgBackRest restore of section 8.7):

```text
INCREMENTAL FROM LSN: 0/16000028
INCREMENTAL FROM TLI: 2
START WAL LOCATION: 0/18000028 (file 000000020000000000000018)
...
```

### 9.3 Restoring: `pg_combinebackup`

An incremental backup cannot be started directly. `pg_combinebackup` merges a chain (oldest first) into a full, ordinary data directory:

```bash
pg_combinebackup full incr1 incr2 -o combined
pg_verifybackup combined
```

```text
backup successfully verified
```

Started on its own, the combined backup comes up with all the orders as of `incr2` (1327 in the lab run, the same count the primary had when `incr2` was taken). From there you can also do point-in-time recovery with archived WAL exactly as in section 7: the combined directory is a normal base backup.

Things to know:

- The chain is fragile: losing `incr1` makes `incr2` useless. Keep chains short, and verify the combined result, not just the pieces.
- `pg_combinebackup` needs plain-format inputs (tar backups must be extracted first) and writes a full copy, so you need disk space for the whole database at restore time. `--link` (PostgreSQL 18) and `--copy-file-range`/`--clone` reduce the copying cost.
- If the summaries for the needed WAL range have been removed (older than `wal_summary_keep_time`, or `summarize_wal` was off), the incremental backup fails, and you take a new full one.

Built-in incrementals make a solid backup scheme possible with core tools only. pgBackRest still adds repository management, retention, encryption, object storage, parallelism and delta restore; most teams use a tool and let it decide how to do incrementals.

## 10. Testing restores and monitoring backups

### 10.1 A backup you have not restored is a hope

Every experienced DBA has a story about the backup that turned out to be empty, encrypted with a lost key, missing one tablespace, or missing a week of WAL because the archive bucket's lifecycle policy deleted it. Verification tools check that files are intact; only a **restore** proves that the whole chain (backup, WAL, configuration, credentials, runbook, and the people) works. And only a timed restore tells you your real RTO.

The lab's `scripts/pitr-drill.sh` automates a complete point-in-time recovery drill. Run it from the host, in the lab directory:

```bash
./scripts/pitr-drill.sh
```

```text
08:36:55 pitr-drill: using base backup /backups/base/base-20261014T083654Z
08:36:56 pitr-drill: restore point 'drill-20261014T083656Z' created; 15 tables snapshotted
NOTICE:  placed 175 orders
08:36:56 pitr-drill: post-snapshot activity generated
08:36:56 pitr-drill: WAL archived up to 000000010000000000000003
08:36:58 pitr-drill: drill server m18-drill started, replaying WAL
    2026-10-14 08:36:58.380 UTC [29] LOG:  recovery stopping at restore point "drill-20261014T083656Z", time 2026-10-14 08:36:56.126368+00
    2026-10-14 08:36:58.386 UTC [29] LOG:  selected new timeline ID: 2
    2026-10-14 08:36:58.430 UTC [29] LOG:  archive recovery complete
table                      expected    current   restored
addresses                        32         32         32
audit_log                       441        889        441
cart_items                       14         14         14
categories                       23         23         23
inventory                        62         62         62
jobs                              0          0          0
order_items                     406        581        406
orders                          291        466        291
payments                        140        140        140
product_price_history            71         71         71
products                         62         62         62
promotions                        9          9          9
reviews                          78         78         78
sellers                           6          6          6
users                            30         30         30
08:36:59 pitr-drill: PASS: restored counts match the snapshot at 'drill-20261014T083656Z' (restore took 3s)
08:36:59 pitr-drill: removing drill container and volume
```

What it does, step by step:

1. Counts the rows of every table in schema `bazaar` in **one** `SELECT ... UNION ALL ...` statement (one statement, one snapshot, so the counts are mutually consistent), and immediately creates a named restore point.
2. Lets the database move on: in the lab it runs `bazaar-activity.sql` (on a production-like server with real traffic, set `GENERATE_ACTIVITY=0`; the drill itself never changes data).
3. Switches WAL and waits until the segment is archived.
4. Copies the newest base backup into a fresh volume, appends `restore_command`, `recovery_target_name`, `recovery_target_action = 'promote'` and **`archive_mode = 'off'`** (a drill must never write into the production archive), and starts it as `m18-drill`, with the archive mounted read-only.
5. Waits for promotion, then counts again on the restored server.
6. Compares: the `restored` column must equal `expected` (the moment of the restore point), *not* `current`. Tables that changed afterwards (`orders`, `order_items`, `audit_log`) prove that recovery stopped at the right place, not at the end of the WAL.

Row counts are a coarse check. A thorough drill adds: `pg_amcheck` on the restored server to verify table and index structure; a few application-level queries ("the latest paid order has a payment", "every order's total equals the sum of its items"); and recording the elapsed time. The command below runs `pg_amcheck` on `m18-primary` to show what it reports; in a drill you run the same command on the restored server (`m18-drill`).

```bash
docker exec -u postgres m18-primary pg_amcheck -d bazaar --install-missing --heapallindexed --progress
```

```text
334/334 relations (100%), 1158/1158 pages (100%)
```

`pg_amcheck` (using the `amcheck` extension) reads every heap page and index and verifies their internal invariants; `--heapallindexed` also checks that every heap tuple is present in each index. It exits non-zero on any problem.

> **In production:** schedule the drill (weekly is common) on a separate machine, restore from the **off-site** copy at least sometimes, alert when it fails or when it takes longer than your RTO allows, and once a quarter do it by hand, from the runbook, with someone who did not write the runbook.

### 10.2 Monitoring freshness

Backups fail silently: a cron job that stopped running after a server migration produces no errors at all. Monitor the *outcome*, not the job:

- **Age of the newest successful backup.** `nightly-backup.sh` appends every successful backup to `/backups/base/catalog.tsv`; `pgbackrest info --output=json` gives the same for pgBackRest (its `backup[].timestamp.stop`).
- **Archive freshness**: `pg_stat_archiver.last_archived_time` must be recent, and the archiver must not be failing (section 5.3).
- **Archive continuity**: no gaps between the oldest backup's start WAL and now (pgBackRest's `check` and `verify` do this).
- **Drill results** (previous subsection).

The lab's `scripts/check-backup-freshness.sh` implements the first two in the style of a Nagios/Icinga check (one line of output; exit 0 = OK, 1 = WARNING, 2 = CRITICAL), which almost every monitoring system can run:

```bash
docker exec -u postgres m18-primary /lab/scripts/check-backup-freshness.sh
```

```text
BACKUP OK: last backup base-20261014T080401Z 234s ago; last WAL 000000020000000000000018.00000028.backup archived 24s ago
```

(These lines were captured in a separate run of the lab, not in sequence with the sections above: the `00000002...` WAL name shows that this server was already on timeline 2 after a point-in-time restore, so the backup ages do not match the timestamps earlier in the module.)

With a tighter threshold (to simulate a missed nightly run) and with the archive made unwritable again:

```text
BACKUP WARNING: last backup base-20261014T080401Z is 234s old; last WAL 000000020000000000000018.00000028.backup archived 24s ago
BACKUP CRITICAL: last backup base-20261014T080401Z 239s ago; archiver is FAILING (last success: 000000020000000000000018.00000028.backup)
```

The thresholds are environment variables in seconds; the defaults are `BACKUP_WARN=93600` (26 h), `BACKUP_CRIT=180000` (50 h), `ARCHIVE_WARN=300` (5 min) and `ARCHIVE_CRIT=900` (15 min). They express the RPO: if archiving is stuck for 15 minutes, the RPO promise is already broken.

## 11. A backup strategy for Bazaar, and the checklist

Putting it all together for Bazaar's requirements (RPO ≤ 1 minute, RTO ≤ 1 hour):

| Layer | What | Why |
|---|---|---|
| Continuous WAL archiving | `archive_command` to a repository on another host / object storage, `archive_timeout = 60s` | RPO of seconds to one minute |
| Physical backups | Weekly full, daily differential (or nightly full for small databases), verified after each run | Restore base for PITR; restore time proportional to one backup plus a day of WAL |
| Retention | 14 days of PITR window; plus monthly backups kept 1 year in cold storage | Mistakes are often noticed days later; auditors ask for more |
| Logical dumps | Nightly `pg_dump -Fc` of `bazaar` + `pg_dumpall --globals-only`, kept 30 days | Single-table restores without PITR, portability, a second independent mechanism |
| Restore points | `pg_create_restore_point()` before every migration and data fix | Exact targets for the most common accidents |
| Delayed standby (Module 19) | Optional: a replica applying WAL 1 hour late | Fastest recovery from mistakes noticed within the hour |
| Drills | Weekly automated PITR drill; quarterly manual drill from the runbook | Proves RTO and the runbook |
| Monitoring | Freshness checks, archiver failures, drill results, repository `verify` | Backups fail silently |

**The backup checklist.** You should be able to tick every item for every production database:

- [ ] RPO and RTO are written down and agreed with the business.
- [ ] WAL archiving is on, its command is durable and idempotent, and archiver failures alert within minutes.
- [ ] Base backups run on a schedule and are **verified** after each run.
- [ ] Backups and WAL are stored **off the database host**, in at least two locations, one of them immutable or unreachable with the database server's credentials.
- [ ] Backups are **encrypted**, and the keys are stored somewhere that survives the loss of the database server (and of the backup server).
- [ ] Roles and configuration (`pg_dumpall --globals-only`, `postgresql.conf`, `pg_hba.conf`, certificates) are backed up too.
- [ ] Retention covers the longest time a mistake might go unnoticed.
- [ ] A restore is tested automatically at least weekly, and the measured restore time fits the RTO.
- [ ] There is a written runbook (the milestone below), and someone other than its author has followed it.
- [ ] Monitoring alerts on stale backups, stale archives, failed drills and repository errors.
- [ ] After every point-in-time recovery or failover, a new full backup is taken.

## Summary

- Define **RPO** (data you may lose) and **RTO** (time you may be down) first; they choose the technology. Replication is not a backup, and a backup on the same host is not a backup.
- **`pg_dump`** produces a consistent logical snapshot (one `REPEATABLE READ` snapshot, `ACCESS SHARE` locks). Prefer the custom format (`-Fc`), or directory format with `-j` for speed; consider `-Z zstd`. Dumps contain no indexes and no roles: pair them with `pg_dumpall --globals-only`.
- **`pg_restore`** restores in pre-data/data/post-data sections, in parallel with `-j`, selectively with `-t`/`-n`/`-L`, into other databases with `--create`, `--no-owner`, `--no-privileges`. Restore into a scratch database and copy rows back; mind triggers.
- **`pg_basebackup`** copies the data directory of a running server; `backup_label` plus the WAL from start to end make it consistent. Use `-X stream`, verify with `pg_verifybackup`, and remember tablespaces.
- **Continuous archiving** (`archive_mode`, a durable and idempotent `archive_command` or an `archive_library`, `archive_timeout`) plus a base backup gives **point-in-time recovery**: `recovery.signal`, `restore_command`, a `recovery_target_*`, and `recovery_target_action`. A failing archiver silently fills `pg_wal`.
- Find exact targets with **`pg_waldump`** (or restore points); after recovery the server starts a new **timeline**, recorded in a `.history` file.
- **pgBackRest** manages repositories, full/diff/incr backups, retention, verification, encryption and object storage, and restores by time with `--delta`.
- PostgreSQL 17's **`summarize_wal`**, `pg_basebackup --incremental` and **`pg_combinebackup`** provide built-in incremental backups.
- **Test restores** automatically, measure them against the RTO, and **monitor** backup and archive freshness.

## What's next

A restore takes minutes to hours. Module 19 builds replicas that are always running and seconds behind the primary, the basis for read scaling and for high availability.
