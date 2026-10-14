# Bazaar backup and restore runbook

Reference solution for the Module 18 milestone. It is written for the lab (`m18-primary`, volumes `m18-lab_*`), but every step names the production equivalent. Keep a printed or offline copy: when you need this document, the systems that host it may be down.

| | |
|---|---|
| Database | `bazaar` on cluster `m18-primary` (PostgreSQL 17) |
| Owners | DBA on call (primary), backend lead (secondary) |
| RPO | ≤ 1 minute of committed transactions |
| RTO | ≤ 1 hour for a full restore; ≤ 15 minutes to have a restored copy available for salvage |
| Last drill | record date, result and measured restore time here after every drill |

---

<!-- toc:start -->
**Contents**

- [1. What is backed up, where, and how often](#1-what-is-backed-up-where-and-how-often)
- [2. Monitoring](#2-monitoring)
- [3. Before you restore: decide](#3-before-you-restore-decide)
- [4. Finding the recovery target](#4-finding-the-recovery-target)
- [5. Salvage: restore a copy and copy data back (production keeps running)](#5-salvage-restore-a-copy-and-copy-data-back-production-keeps-running)
- [6. Full restore (production is lost or must be rewound)](#6-full-restore-production-is-lost-or-must-be-rewound)
  - [6a. With the lab's base backups and WAL archive](#6a-with-the-labs-base-backups-and-wal-archive)
  - [6b. With pgBackRest (in place, delta)](#6b-with-pgbackrest-in-place-delta)
- [7. After every restore](#7-after-every-restore)
- [8. Drills](#8-drills)

<!-- toc:end -->

## 1. What is backed up, where, and how often

| What | How | Schedule | Where (lab) | Where (production) | Retention |
|---|---|---|---|---|---|
| WAL | `archive_command = '/lab/scripts/archive-wal.sh %p %f'`, `archive_timeout = 60s` | continuous | volume `m18-lab_archive`, `/archive/wal` | backup host or object storage, second region copy | until no kept base backup needs it |
| Base backup | `scripts/nightly-backup.sh` (`pg_basebackup -Fp -X stream`, `pg_verifybackup`) | nightly 02:15 | volume `m18-lab_backups`, `/backups/base/base-<UTC timestamp>` | backup host / object storage | newest 3 (`KEEP=3`); production: 14 days |
| Backup catalog | appended by `nightly-backup.sh` | every backup | `/backups/base/catalog.tsv` | same | forever (it is tiny) |
| pgBackRest (alternative/secondary) | `pgbackrest --stanza=bazaar backup` | weekly full, daily diff | volume `m18-lab_pgbackrest` | `repo1` S3 bucket, encrypted | `repo1-retention-full=2` |
| Logical dump | `pg_dump -Fc -Z zstd bazaar` + `pg_dumpall --globals-only` | nightly | (not in the lab) | object storage | 30 days |
| Configuration | `postgresql.conf`, `postgresql.auto.conf`, `pg_hba.conf`, `conf/pgbackrest.conf` | on change (in git) | `modules/18-backup-recovery/lab/conf/` | config repository | forever |

Cron (production backup host, as the `postgres` OS user):

```text
15 2 * * *   /opt/bazaar/nightly-backup.sh >> /var/log/bazaar/backup.log 2>&1
*/5 * * * *  /opt/bazaar/check-backup-freshness.sh || /opt/bazaar/page-oncall.sh
0 4 * * 0    /opt/bazaar/pitr-drill.sh >> /var/log/bazaar/drill.log 2>&1
```

In the lab, run the same scripts by hand:

```bash
cd modules/18-backup-recovery/lab
docker exec -u postgres m18-primary /lab/scripts/nightly-backup.sh
docker exec -u postgres m18-primary /lab/scripts/check-backup-freshness.sh
./scripts/pitr-drill.sh
```

## 2. Monitoring

`scripts/check-backup-freshness.sh` exits 0/1/2 (OK/WARNING/CRITICAL) and prints one line. It alerts when:

- the newest base backup is older than 26 h (warning) / 50 h (critical);
- the archiver is failing right now (`last_failed_time > last_archived_time`) (critical);
- nothing has been archived for 5 min (warning) / 15 min (critical).

Also watch: free space of the `pg_wal` volume (a failing archiver fills it), the weekly drill result, and `pgbackrest verify` (weekly).

If the archiver is failing: read the server log (`archive command failed ...`), fix the destination (space, permissions, credentials, network), and confirm `pg_stat_archiver.archived_count` increases again. **Never** "fix" it by setting `archive_command = '/bin/true'`: that silently discards WAL and breaks every point-in-time recovery after that moment. If `pg_wal` is about to fill the disk and the archive cannot be fixed quickly, grow the volume first.

## 3. Before you restore: decide

1. **What happened, and when?** Collect: the time the problem was noticed, application logs, `audit_log` (`operation`, `changed_at`), the server log (`log_statement`). Write it down in the incident channel.
2. **What kind of restore?**
   - A few rows or one table damaged, production otherwise healthy → **salvage**: restore a copy elsewhere, copy the data back (section 5). Production keeps running.
   - Database unusable (storage lost, widespread corruption) → **full restore**: restore to the newest possible point and switch the application to it (section 6).
   - Recent logical mistake on many tables, and losing the writes since then is acceptable → **restore in place to a point in time** (section 6 with a target), after a business decision.
3. **Stop the bleeding.** If something is still destroying data (a runaway job, a bad deploy), stop it first: disable the job, roll back the deploy, or revoke the role's write access (`REVOKE INSERT, UPDATE, DELETE ...`).
4. **Never restore over the only copy.** Keep the damaged cluster (stop it if necessary) until the incident is closed.

## 4. Finding the recovery target

Choose the most precise target available, in this order:

1. **A restore point** created before a migration or data fix: `recovery_target_name = '<name>'`. List them with `pg_waldump --rmgr=XLOG ... | grep RESTORE_POINT`.
2. **The transaction or LSN** of the mistake, from `pg_waldump` on the archived WAL:

   ```bash
   # the file of the damaged table (look it up on production, or in a restored copy)
   docker exec m18-primary psql -U postgres -d bazaar -Atc "SELECT pg_relation_filepath('bazaar.orders')"
   # data changes to that file, with their transaction ids
   docker exec -u postgres m18-primary pg_waldump --path=/archive/wal \
       --relation=1663/<db oid>/<relfilenode> --rmgr=Heap <first segment> | grep -E "DELETE|UPDATE" | head
   # the commit records, with times
   docker exec -u postgres m18-primary pg_waldump --path=/archive/wal --rmgr=Transaction <segment> | grep "tx: *<xid>,"
   ```

   Then `recovery_target_xid = '<xid>'` (or `recovery_target_lsn = '<commit lsn>'`) with `recovery_target_inclusive = off`. For a `DROP TABLE` or `TRUNCATE`, look for the commit record listing `rels: base/<db>/<relfilenode>`.
3. **A time**, slightly before the mistake: `recovery_target_time = '2026-10-14 08:04:14+00'` (always with a time zone).

Use `recovery_target_action = 'pause'` whenever you are not completely sure of the target: you can inspect the paused server read-only and restart with a different target if needed.

## 5. Salvage: restore a copy and copy data back (production keeps running)

Lab commands; production uses a separate host and the same steps.

1. Make sure the WAL containing the target is archived: `SELECT pg_switch_wal();` on production, then wait until `pg_stat_archiver.last_archived_wal` passes it.
2. Pick the newest base backup that **finished before the target** (`/backups/base/catalog.tsv`, column `finished_at`).
3. Restore it into a new volume and configure recovery:

   ```bash
   docker volume create m18-restore-pgdata
   docker run --rm -v m18-lab_backups:/backups:ro -v m18-restore-pgdata:/var/lib/postgresql/data \
       m18-postgres-pgbackrest:17 bash -c '
     set -e; cd /var/lib/postgresql/data
     cp -a /backups/base/<backup>/. .
     cat >> postgresql.auto.conf <<EOF
   archive_mode = off
   restore_command = '"'"'cp /archive/wal/%f %p'"'"'
   recovery_target_xid = '"'"'<xid>'"'"'
   recovery_target_inclusive = off
   recovery_target_action = '"'"'pause'"'"'
   EOF
     touch recovery.signal
     chown -R postgres:postgres . && chmod 700 .'
   docker run -d --name m18-restore -v m18-restore-pgdata:/var/lib/postgresql/data \
       -v m18-lab_archive:/archive:ro m18-postgres-pgbackrest:17
   docker logs -f m18-restore      # wait for "pausing at the end of recovery"
   ```

   `archive_mode = off` is mandatory: a restored copy must never write into the production archive.
4. Verify on the paused copy (read-only): the damaged rows are present, counts look right. If the target was wrong, remove the container and volume and go back to step 3.
5. Copy the data back into a staging schema on production, inspect it, then merge it in **one transaction**, thinking about every trigger on the target tables (the `orders.total` trigger adds item amounts: insert orders with their final total, set `total = 0`, then insert the items; see Module 18, section 7.5). For whole tables: `pg_dump -Fc -t <table>` on the copy, `pg_restore -d bazaar` on production.
6. Verify on production (counts, totals, a sample of rows), then remove the copy: `docker rm -f m18-restore && docker volume rm m18-restore-pgdata`.

## 6. Full restore (production is lost or must be rewound)

### 6a. With the lab's base backups and WAL archive

1. Stop the damaged server if it is still running: `docker stop m18-primary`. Do not delete its volume.
2. Restore the newest base backup into a **new** data volume as in section 5, step 3, but:
   - without a `recovery_target_*` setting to recover everything in the archive (minimal data loss), or with the agreed target;
   - with `recovery_target_action = 'promote'`;
   - with the archive settings of production (`archive_mode = on` and the production `archive_command`) **only if this server becomes the new production**; it will archive on a new timeline.
   - If the old server's `pg_wal` is readable and contains segments that were never archived, copy them into the new data directory's `pg_wal/` before starting: they contain the last committed transactions.
3. Start the server and watch the log until `database system is ready to accept connections`. Check `SELECT pg_is_in_recovery();` returns `f`.
4. Verify: row counts of the main tables, the newest orders, `pg_amcheck -d bazaar --install-missing`.
5. Point the application at the new server (connection string, DNS or proxy; Module 20).
6. **Take a new full base backup immediately** (`nightly-backup.sh`): the server is on a new timeline.
7. Re-create replicas from the new server (Module 19).

### 6b. With pgBackRest (in place, delta)

```bash
docker stop m18-primary
docker compose run --rm --no-deps -u postgres --entrypoint pgbackrest primary \
    --stanza=bazaar --delta --type=time "--target=<timestamp with zone>" \
    --target-action=promote restore              # or omit --type for "everything"
docker start m18-primary
docker exec -u postgres m18-primary pgbackrest --stanza=bazaar --type=full backup
```

`--delta` only rewrites files that differ from the backup. Use `--pg1-path=<other dir> --archive-mode=off` to restore a copy elsewhere instead (salvage).

## 7. After every restore

- [ ] New full base backup taken (and pgBackRest full backup, if used).
- [ ] `check-backup-freshness.sh` is OK again.
- [ ] Recovery settings (`recovery_target_*`, `restore_command`) removed from `postgresql.auto.conf` of the new production server.
- [ ] Old/damaged cluster kept until the incident is closed, then removed deliberately.
- [ ] Incident report: what was lost, the recovery target and why, how it was verified, measured restore time versus RTO, what to change.

## 8. Drills

`scripts/pitr-drill.sh` runs weekly: it creates a restore point, lets the database move on, restores the newest base backup plus archived WAL into a throw-away container up to the restore point, compares row counts of every Bazaar table, and reports PASS/FAIL and the restore time. Once a quarter, a person who did not write this runbook performs section 5 by hand and updates this document with whatever was unclear.
