# Module 18 milestone: Bazaar can be restored, and you can prove it

## Goal

Bazaar takes money. The business has agreed on two numbers: at most **one minute** of committed orders may be lost in a disaster (RPO), and the shop must be back within **one hour** (RTO). Today nothing guarantees either. In this milestone you put backups in place that meet those numbers, write the runbook that the on-call engineer will follow at 3 a.m., and automate a restore drill that proves, every week, that the backups actually restore.

This milestone changes no schema. It produces scripts and a document, and it runs against the module's lab server `m18-primary` (`modules/18-backup-recovery/lab`).

## Requirements

### 1. Continuous WAL archiving

- `archive_mode = on`, an `archive_command` (or `archive_library`) that is **durable** (fsync before success), **never overwrites** a different file, and is **idempotent**.
- `archive_timeout` chosen so that a quiet database still meets the 1-minute RPO.
- Configured with `ALTER SYSTEM` (or the configuration file), not with command-line flags, so it can be changed without editing the container definition.

### 2. Nightly base backup script

A script that can run unattended from cron and:

1. takes a full base backup with `pg_basebackup` that includes the WAL needed for consistency;
2. **verifies** it (`pg_verifybackup`), and only then marks it as complete (a failed or interrupted run must never look like a good backup);
3. records each successful backup (label, start and finish time, size, start WAL segment) in a catalog that monitoring can read;
4. keeps the newest N backups (N configurable) and deletes older ones;
5. deletes archived WAL that no kept backup needs, and nothing more;
6. exits non-zero on any failure.

### 3. Freshness check

A check, runnable by any monitoring system, that reports OK / WARNING / CRITICAL (exit codes 0/1/2) when:

- the newest successful base backup is too old;
- the archiver is currently failing;
- nothing has been archived for longer than your RPO allows.

### 4. Scripted PITR drill

A script that, without modifying production data:

1. records the row count of **every** table in schema `bazaar` at one consistent moment and marks that moment so recovery can target it exactly;
2. lets the database move on (in the lab: generate activity);
3. restores the newest base backup plus archived WAL into a throw-away server, stopping exactly at the recorded moment, **with archiving disabled** on the restored server;
4. compares the restored counts with the recorded ones and prints PASS or FAIL plus the time the restore took;
5. cleans up after itself.

### 5. Runbook

A document an engineer who has never seen this setup can follow under stress. It must cover:

- what is backed up, where, how often, and for how long (including roles and configuration);
- how the backups are monitored, and what to do when the archiver fails (and what never to do);
- how to decide between salvaging data from a restored copy and a full restore;
- how to find the exact recovery target (restore point, transaction id or LSN with `pg_waldump`, or time);
- step-by-step commands for: restoring a copy for salvage, a full restore, a pgBackRest restore;
- what to do after any restore (new full backup, cleanup, incident report);
- how drills are run.

## Reference solution

| File | Requirement |
|---|---|
| [`lab/conf/init-archiving.sql`](lab/conf/init-archiving.sql), [`lab/scripts/archive-wal.sh`](lab/scripts/archive-wal.sh) | 1 |
| [`lab/scripts/nightly-backup.sh`](lab/scripts/nightly-backup.sh) | 2 |
| [`lab/scripts/check-backup-freshness.sh`](lab/scripts/check-backup-freshness.sh) | 3 |
| [`lab/scripts/pitr-drill.sh`](lab/scripts/pitr-drill.sh) (with [`lab/scripts/bazaar-activity.sql`](lab/scripts/bazaar-activity.sql)) | 4 |
| [`lab/RUNBOOK.md`](lab/RUNBOOK.md) | 5 |

Try writing your own first. The module's sections 5, 7 and 10 explain every design decision in the reference scripts.

## Acceptance checks

Run from `modules/18-backup-recovery/lab` on a fresh lab (`docker compose down -v && docker compose up -d --build`, then `COURSE_PG_CONTAINER=m18-primary ../../../project/bin/reset.sh 11`). Replace the script paths with yours if you wrote your own.

1. **Archiving works and is configured for the RPO.**

   ```bash
   docker exec m18-primary psql -U postgres -c "SELECT name, setting, source FROM pg_settings
       WHERE name IN ('archive_mode', 'archive_command', 'archive_timeout')"
   docker exec m18-primary psql -U postgres -c "SELECT pg_switch_wal()"
   docker exec m18-primary psql -U postgres -c "SELECT archived_count > 0 AS archiving, failed_count FROM pg_stat_archiver"
   ```

   Expected: `archive_mode = on`, `archive_timeout` ≤ 60 (seconds), source `configuration file`; `archiving = t`, `failed_count = 0`.

2. **The archive command refuses to overwrite.** Pick any archived segment and run the command against a different file under that name:

   ```bash
   docker exec -u postgres m18-primary bash -c 'f=$(ls /archive/wal | grep -E "^[0-9A-F]{24}$" | head -1);
       cd /var/lib/postgresql/data && /lab/scripts/archive-wal.sh PG_VERSION "$f"; echo "exit=$?"'
   ```

   Expected: an error message and `exit=1`. Running it again with the real segment (`pg_wal/$f`, if still present) must give `exit=0`.

3. **Nightly backup, verification, retention.** Run the backup script four times:

   ```bash
   for i in 1 2 3 4; do docker exec -u postgres m18-primary /lab/scripts/nightly-backup.sh; sleep 1; done
   docker exec m18-primary ls /backups/base
   ```

   Expected: every run reports success; exactly 3 `base-*` directories remain (with `KEEP=3`); `catalog.tsv` has 4 data lines; no `.partial` directory exists. Running `pg_verifybackup` on each remaining directory prints `backup successfully verified`.

4. **A failed backup does not look like a success.** Make the backup directory unwritable and run the script:

   ```bash
   docker exec m18-primary chmod 500 /backups/base
   docker exec -u postgres m18-primary /lab/scripts/nightly-backup.sh; echo "exit=$?"
   docker exec m18-primary chmod 750 /backups/base
   ```

   Expected: non-zero exit, and no new line in `catalog.tsv`.

5. **Freshness check.** With a recent backup: exit 0 and a line starting with `BACKUP OK`. With `BACKUP_WARN=1` in the environment (`docker exec -u postgres -e BACKUP_WARN=1 ...`): exit 1. With `/archive/wal` unwritable (`chmod 500`) after a `pg_switch_wal()` and a few seconds' wait: exit 2 and the word `FAILING`. Restore the permissions afterwards.

6. **The drill passes.**

   ```bash
   ./scripts/pitr-drill.sh; echo "exit=$?"
   ```

   Expected: a table with one row per Bazaar table (15 at state 11) where `restored` equals `expected` in every row and differs from `current` for `orders`; the line `PASS: restored counts match the snapshot ...`; `exit=0`; and afterwards no `m18-drill` container (`docker ps -a --filter name=m18-drill` is empty).

7. **The drill can fail.** Delete an archived segment the drill needs and run it again; it must not report PASS. For example, run the drill once to see which segment it archives up to, then:

   ```bash
   docker exec -u postgres m18-primary /lab/scripts/nightly-backup.sh
   docker exec -i m18-primary psql -U postgres -d bazaar < scripts/bazaar-activity.sql
   docker exec m18-primary psql -U postgres -c "SELECT pg_switch_wal()"
   docker exec m18-primary bash -c 'ls /archive/wal | grep -E "^[0-9A-F]{24}$" | tail -1 | xargs -I{} mv /archive/wal/{} /tmp/'
   ./scripts/pitr-drill.sh; echo "exit=$?"
   ```

   Expected: non-zero exit (recovery cannot reach the restore point: `recovery ended before configured recovery target was reached`). Put the segment back afterwards.

8. **The runbook is usable.** Ask someone else (or yourself, a week later, without looking at the module) to follow the runbook's salvage procedure on the lab after `DELETE FROM reviews WHERE rating = 1;` and get the deleted reviews back into production. Fix every step they stumble on.
