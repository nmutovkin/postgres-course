# Module 18 solutions

<!-- nav:start -->
> [Theory](../README.md) · [Exercises](../exercises.md) · [Project](../project.md) · **Solutions**  
> [← 17 Configuration, Maintenance and Monitoring](../../17-operations/README.md) · [Course home](../../../README.md) · [19 Replication →](../../19-replication/README.md)
<!-- nav:end -->

All results below come from runs on PostgreSQL 17.11: exercises 1, 4, 5 and 8 against the large dataset (state 16), the others against the lab server `m18-primary` with the small dataset (state 11). Times and LSNs will differ on your machine; counts should not, except where the lab's random activity script is involved.

Runnable files in this directory:

| File | Exercise |
|---|---|
| `ex02_archiver_health.sql` | 2 |
| `ex06_pitr_window.sql` | 6 |
| `archive-wal-gzip.sh` | 10 |
| `ex11_archive_gaps.sql` | 11 |

The milestone's reference solution (runbook and scripts) is in [`../lab/`](../lab/): see [`../project.md`](../project.md).

---

<!-- toc:start -->
**Contents**

- [Exercise 1: Read a dump's table of contents](#exercise-1-read-a-dumps-table-of-contents)
- [Exercise 2: An archiver health query](#exercise-2-an-archiver-health-query)
- [Exercise 3: Anatomy of a nightly backup](#exercise-3-anatomy-of-a-nightly-backup)
- [Exercise 4: A lean developer copy](#exercise-4-a-lean-developer-copy)
- [Exercise 5: Undo a bad bulk update from last night's dump](#exercise-5-undo-a-bad-bulk-update-from-last-nights-dump)
- [Exercise 6: How far back can we go?](#exercise-6-how-far-back-can-we-go)
- [Exercise 7: Test a pgBackRest backup without touching production](#exercise-7-test-a-pgbackrest-backup-without-touching-production)
- [Exercise 8: How much does `-j` help?](#exercise-8-how-much-does--j-help)
- [Exercise 9: Break an incremental chain](#exercise-9-break-an-incremental-chain)
- [Exercise 10: Compressed archiving](#exercise-10-compressed-archiving)
- [Exercise 11: Detect gaps in the archive](#exercise-11-detect-gaps-in-the-archive)
- [Exercise 12: Recover a dropped table by LSN](#exercise-12-recover-a-dropped-table-by-lsn)

<!-- toc:end -->

## Exercise 1: Read a dump's table of contents

```bash
docker exec -u postgres course-pg bash -c '
  mkdir -p /tmp/dumps && cd /tmp/dumps
  pg_dump -d bazaar -Fc -f bazaar.dump
  pg_restore -l bazaar.dump | grep -c " TABLE DATA "
  pg_restore -l bazaar.dump | grep -E " bazaar reviews | reviews_"'
```

```text
40
243; 1259 16556 TABLE bazaar reviews bazaar_owner
242; 1259 16555 SEQUENCE bazaar reviews_id_seq bazaar_owner
4522; 0 0 ACL bazaar SEQUENCE reviews_id_seq bazaar_owner
4386; 0 16556 TABLE DATA bazaar reviews bazaar_owner
4538; 0 0 SEQUENCE SET bazaar reviews_id_seq bazaar_owner
3982; 2606 16564 CONSTRAINT bazaar reviews reviews_pkey bazaar_owner
3984; 2606 16566 CONSTRAINT bazaar reviews reviews_product_id_user_id_key bazaar_owner
3985; 1259 17599 INDEX bazaar reviews_user_id_idx bazaar_owner
4193; 2606 16567 FK CONSTRAINT bazaar reviews reviews_product_id_fkey bazaar_owner
4194; 2606 16572 FK CONSTRAINT bazaar reviews reviews_user_id_fkey bazaar_owner
```

1. **40 data entries for 41 tables**, because `events` is a *partitioned* table: the parent never stores rows (Module 14), so there is nothing to dump for it. Its 25 partitions each have their own `TABLE DATA` entry.
2. The ten `reviews` entries split into sections like this: `TABLE`, `SEQUENCE` and the `ACL` on the sequence are **pre-data** (ACLs are applied at the end in practice, but they need no data); `TABLE DATA` and `SEQUENCE SET` are **data**; the two `CONSTRAINT`s (primary key and unique), the `INDEX` and the two `FK CONSTRAINT`s are **post-data**. That is the expensive part of a restore: building three B-tree indexes (the PK and unique constraints are indexes too) and validating two foreign keys over 290,000 rows.

The pattern `" bazaar reviews | reviews_"` misses entries whose name is `TABLE reviews`, such as the table's `ACL` and `COMMENT` lines (`... ACL bazaar TABLE reviews ...`). A more robust way to see everything about one object is `pg_restore -l bazaar.dump | grep -w reviews`.

---

## Exercise 2: An archiver health query

[`ex02_archiver_health.sql`](ex02_archiver_health.sql):

```sql
SELECT
    (SELECT count(*) FROM pg_ls_archive_statusdir() WHERE name LIKE '%.ready')  AS segments_waiting,
    (SELECT pg_size_pretty(sum(size)) FROM pg_ls_waldir())                       AS pg_wal_size,
    a.last_archived_wal,
    extract(epoch FROM now() - a.last_archived_time)::int                        AS seconds_since_last_archive,
    coalesce(a.last_failed_time > a.last_archived_time, false)                   AS is_failing
FROM pg_stat_archiver AS a;
```

```text
 segments_waiting | pg_wal_size | last_archived_wal | seconds_since_last_archive | is_failing
------------------+-------------+-------------------+----------------------------+------------
                0 | 48 MB       | 00000002.history  |                         45 | f
```

(The last archived file was a timeline history file because the lab had just been through a pgBackRest point-in-time restore; history files are archived like segments.)

Reasoning:

- `.ready` files are exactly the segments the archiver has not yet succeeded on, so their count is the backlog. `pg_ls_archive_statusdir()` and `pg_ls_waldir()` need superuser or membership in `pg_monitor`.
- `is_failing` compares the two timestamps: if the most recent event was a failure, archiving is currently broken. `coalesce` handles a server that never failed (`last_failed_time IS NULL`, so the comparison is NULL). Note the asymmetric case of a server that never *succeeded*: `last_archived_time` NULL and `last_failed_time` set also yields NULL, hence `false`. A stricter version would be `a.last_failed_time IS NOT NULL AND a.last_failed_time > coalesce(a.last_archived_time, '-infinity')`, which is what `check-backup-freshness.sh` uses.
- `seconds_since_last_archive` should stay below a small multiple of `archive_timeout` on a server with any write activity.

With `chmod 500 /archive/wal` and a segment switch, the same query shows `is_failing = t` and the waiting count grows with each switch, as in section 5.3.

---

## Exercise 3: Anatomy of a nightly backup

```bash
docker exec m18-primary bash -c '
  b=$(ls -d /backups/base/base-* | tail -1); echo $b
  grep "START WAL" $b/backup_label
  tail -1 /backups/base/catalog.tsv | cut -f6
  grep -c "\"Path\"" $b/backup_manifest
  find $b -type f ! -name backup_manifest ! -path "*/pg_wal/*" | wc -l
  grep -A2 "WAL-Ranges" $b/backup_manifest
  ls $b/pg_wal'
```

```text
/backups/base/base-20261014T082949Z
START WAL LOCATION: 0/8000028 (file 000000020000000000000008)
000000020000000000000008
1346
1346
"WAL-Ranges": [
{ "Timeline": 2, "Start-LSN": "0/8000028", "End-LSN": "0/8000120" }
],
000000020000000000000008
00000002.history
archive_status
summaries
```

1. The first segment needed is `000000020000000000000008` (timeline 2, because this lab server had been through a point-in-time restore); the catalog agrees, since `nightly-backup.sh` extracts it from the same `backup_label`.
2. 1,346 files in the manifest and 1,346 on disk. The manifest includes `backup_label` itself but not `backup_manifest` (a file cannot contain its own checksum) and not `pg_wal/`. WAL is described differently: as an LSN range, because which *files* hold that range depends on segment size and timeline, and because `pg_verifybackup` checks WAL by parsing it with `pg_waldump` rather than by checksums.
3. The range `0/8000028`–`0/8000120` lies inside segment `...0008`, the one file in `pg_wal/`. A plain backup taken with `-X stream` also carries the timeline's history file, so it can be restored on timeline 2 without the archive.

---

## Exercise 4: A lean developer copy

```bash
docker exec -u postgres course-pg bash -c '
  cd /tmp/dumps
  pg_dump -d bazaar -Fc -Z zstd \
      --exclude-table-data="bazaar.events*" --exclude-table-data=bazaar.audit_log -f dev.dump
  ls -la dev.dump
  createdb bazaar_dev
  pg_restore -d bazaar_dev -j 4 --no-owner dev.dump
  psql -d bazaar_dev -Atc "SELECT (SELECT count(*) FROM bazaar.events),
      (SELECT count(*) FROM pg_inherits WHERE inhparent = '"'"'bazaar.events'"'"'::regclass),
      (SELECT count(*) FROM bazaar.orders), pg_size_pretty(pg_database_size(current_database()))"
  dropdb bazaar_dev'
```

```text
-rw-rw-rw- 1 postgres postgres 68332902 Oct 14 08:24 dev.dump
0|25|500000|643 MB
```

The dump took 3.4 s and is 65 MB, against 152 MB for a full zstd dump. The restored database is 643 MB instead of about 1.5 GB: the events partitions are there (25 of them, empty), with all their indexes.

Points to note:

- `--exclude-table-data='bazaar.events*'` matches the parent **and** every partition (`events_y2025m01`, ..., `events_default`). Excluding only `bazaar.events` would still dump all partition data, because in a dump each partition is a table of its own. (`--exclude-table-and-children`, PostgreSQL 16+, also exists, but for the *definitions*: it would drop the tables entirely.)
- `--exclude-table-data` keeps the `CREATE TABLE`, indexes, grants and policies, which developers need for realistic testing; `-T` would have removed the tables altogether.
- `--no-owner` makes sense for a laptop restore, where the `bazaar_owner` role may not exist. On a developer laptop you would usually restore `pg_dumpall --globals-only --no-role-passwords` first, so the RLS policies (which name roles) can be created; see section 3.4.
- Alternative: dump with `--data-only` selectively plus `--schema-only` for everything. Two files, more moving parts, same result.

---

## Exercise 5: Undo a bad bulk update from last night's dump

```bash
docker exec -u postgres course-pg bash -c '
  cd /tmp/dumps
  pg_dump -d bazaar -Fc -f bazaar.dump                                    # last night
  psql -d bazaar -c "SELECT count(*), sum(price) FROM bazaar.products WHERE seller_id = 4"
  psql -d bazaar -c "UPDATE bazaar.products SET price = price * 10 WHERE seller_id = 4"   # the incident

  createdb scratch
  pg_restore -d scratch --section=pre-data bazaar.dump
  pg_restore -d scratch --data-only -t products bazaar.dump
  psql -d scratch -c "\copy (SELECT id, price FROM bazaar.products WHERE seller_id = 4) TO /tmp/dumps/seller4_prices.csv CSV"'
```

Then, in production (`psql -d bazaar`):

```sql
BEGIN;
CREATE TEMP TABLE old_prices (id bigint PRIMARY KEY, price numeric(12,2));
\copy old_prices FROM /tmp/dumps/seller4_prices.csv CSV

UPDATE bazaar.products AS p
SET price = o.price
FROM old_prices AS o
WHERE p.id = o.id
  AND p.seller_id = 4
  AND p.price = o.price * 10;          -- only rows that still carry the damage

SELECT count(*) AS still_wrong
FROM bazaar.products AS p JOIN old_prices AS o USING (id)
WHERE p.price <> o.price;
COMMIT;
```

```text
 count |    sum
-------+------------
  5981 | 1079730.19        <- before the incident
UPDATE 5981                <- the incident
COPY 5981
UPDATE 5981                <- the repair
 still_wrong
-------------
           0
 count |    sum
-------+------------
  5981 | 1079730.19        <- after the repair
```

Reasoning:

- Restoring `products` over production is out of the question: it would also roll back every *legitimate* change to every seller's products since the dump (new products, other price changes, status changes), and the `products` table has foreign keys pointing at it from `order_items`, `inventory`, `reviews` and others.
- The condition `p.price = o.price * 10` makes the repair **precise and idempotent**: a product whose price was legitimately changed after the incident is left alone, and running the repair twice changes nothing the second time. With `numeric`, multiplying by 10 is exact, so equality is safe (it would not be with `float`; Module 02).
- Doing it in one transaction with a check before `COMMIT` lets you `ROLLBACK` if `still_wrong` is not what you expect.
- The repair fires the `products` triggers: `set_updated_at` and the audit trigger (5,981 audit rows). That is desirable: the audit log now records the repair.
- Alternatives: with the `dblink` or `postgres_fdw` extension you can query the scratch database directly from production and skip the CSV file. If a point-in-time restore is available (sections 6 and 7), you can restore to just before the `UPDATE` instead of last night, which also recovers price changes made between the dump and the incident.

Drop the scratch database afterwards (`dropdb scratch`).

---

## Exercise 6: How far back can we go?

[`ex06_pitr_window.sql`](ex06_pitr_window.sql):

```sql
WITH catalog AS (
    SELECT split_part(line, E'\t', 1)                  AS label,
           split_part(line, E'\t', 3)::timestamptz     AS finished_at
    FROM regexp_split_to_table(pg_read_file('/backups/base/catalog.tsv'), E'\n') AS line
    WHERE line LIKE 'base-%'
),
kept AS (
    SELECT c.*
    FROM catalog AS c
    WHERE c.label IN (SELECT d FROM pg_ls_dir('/backups/base') AS d)
)
SELECT
    (SELECT min(finished_at) FROM kept)                        AS restorable_from,
    a.last_archived_time                                       AS restorable_until,
    a.last_archived_time - (SELECT min(finished_at) FROM kept) AS window_length,
    (SELECT count(*) FROM kept)                                AS base_backups_kept
FROM pg_stat_archiver AS a;
```

After two more runs of `nightly-backup.sh`:

```text
    restorable_from     |       restorable_until        |  window_length  | base_backups_kept
------------------------+-------------------------------+-----------------+-------------------
 2026-10-14 08:28:39+00 | 2026-10-14 08:29:50.382143+00 | 00:01:11.382143 |                 3
```

```text
label                  started_at            finished_at           seconds  bytes     start_wal
base-20261014T082838Z  2026-10-14T08:28:38Z  2026-10-14T08:28:39Z  1        49355714  000000010000000000000002
base-20261014T082946Z  2026-10-14T08:29:46Z  2026-10-14T08:29:47Z  1        50405498  000000020000000000000007
base-20261014T082949Z  2026-10-14T08:29:49Z  2026-10-14T08:29:50Z  1        50405498  000000020000000000000008
```

Reasoning:

- The earliest restorable point is the **end** of the oldest backup, not its start: a backup is only consistent once WAL has been replayed up to its end LSN (section 4.1). You cannot recover to a moment in the middle of the backup's copy.
- The latest point is the last *archived* WAL. Anything after it lives only in the server's `pg_wal`; if the server's disk is lost, so is that. That gap is your effective RPO at this moment.
- The window above spans a **timeline switch**: the oldest backup is on timeline 1 (`start_wal` `00000001...`), the others on timeline 2, created by the pgBackRest restore in section 8.7. Restoring the timeline 1 backup to a point after the switch still works, because recovery follows the `00000002.history` file in the archive (`recovery_target_timeline = 'latest'`), but a point between the old branch point and "now" on the *abandoned* timeline 1 history is also reachable with `recovery_target_timeline = '1'`. A complete answer to "which points can we restore to" is therefore per timeline.
- `nightly-backup.sh` keeps 3 backups; `base_backups_kept` confirms retention works. In production, the catalog line could also be written into a monitoring table or metrics system instead of a TSV file.

---

## Exercise 7: Test a pgBackRest backup without touching production

```bash
# production keeps running; take a full backup
docker exec m18-primary psql -U postgres -c \
  "ALTER SYSTEM SET archive_command = '/lab/scripts/archive-wal.sh %p %f && pgbackrest --stanza=bazaar archive-push %p'" \
  -c "SELECT pg_reload_conf()"
docker exec -u postgres m18-primary pgbackrest --stanza=bazaar stanza-create
docker exec -u postgres m18-primary pgbackrest --stanza=bazaar --type=full backup
docker exec m18-primary psql -U postgres -d bazaar -c "SELECT count(*) AS orders_now FROM orders"

# a new volume with an empty, correctly owned directory
docker volume create m18-ex7
docker run --rm -v m18-ex7:/restore m18-postgres-pgbackrest:17 \
  bash -c 'mkdir -p /restore/data && chown -R postgres /restore && chmod 700 /restore/data'

# restore into it
docker compose run --rm --no-deps -u postgres -v m18-ex7:/restore --entrypoint pgbackrest primary \
  --stanza=bazaar --pg1-path=/restore/data --archive-mode=off \
  --type=immediate --target-action=promote restore

# a second server on the restored directory, with read access to the repository
docker run -d --name m18-ex7 -e PGDATA=/restore/data -v m18-ex7:/restore \
  -v m18-lab_pgbackrest:/var/lib/pgbackrest:ro \
  -v "$PWD/conf/pgbackrest.conf:/etc/pgbackrest/pgbackrest.conf:ro" \
  m18-postgres-pgbackrest:17
docker exec m18-ex7 psql -U postgres -d bazaar -c "SELECT pg_is_in_recovery(), count(*) AS orders FROM orders" -c "SHOW archive_mode"
```

```text
P00   INFO: new backup label = 20261014-081811F
 orders_now
------------
        486
P00   INFO: repo1: restore backup set 20261014-081811F, recovery will start at 2026-10-14 08:18:11
P00   INFO: restore command end: completed successfully (674ms)
LOG:  starting point-in-time recovery to earliest consistent point
LOG:  consistent recovery state reached at 0/11000120
LOG:  recovery stopping after reaching consistency
LOG:  archive recovery complete
 pg_is_in_recovery | orders
-------------------+--------
 f                 |    486
 archive_mode
--------------
 off
```

Reasoning:

- `--pg1-path` overrides the stanza's data directory for this one command, so production's files are never touched. The restore needs no running server and no lock on production.
- `--archive-mode=off` writes `archive_mode = off` into the restored server's configuration. Without it, the restored server would inherit production's `archive_command` and, once promoted, push its own new timeline into the **production repository**, polluting it.
- `--type=immediate` stops recovery as soon as the backup is consistent, so the restored server has exactly the backup's content: 486 orders, the count at backup time. Without it, pgBackRest replays all archived WAL, which tests the WAL archive too (also worth doing).
- The second container needs the repository (read-only is enough) and `pgbackrest.conf`, because its `restore_command` is `pgbackrest ... archive-get`.

Clean up with `docker rm -f m18-ex7 && docker volume rm m18-ex7`. This is the core of an automated pgBackRest restore test; put it in a scheduled job with a count or `pg_amcheck` check, as `pitr-drill.sh` does for the plain setup.

---

## Exercise 8: How much does `-j` help?

```bash
docker exec -u postgres course-pg bash -c '
  cd /tmp/dumps && pg_dump -d bazaar -Fd -j 4 -f bazaar-j4.dir
  for j in 1 2 4 8; do createdb r$j; time pg_restore -d r$j -j $j bazaar-j4.dir; dropdb r$j; done
  createdb rs
  time pg_restore -d rs --section=pre-data --section=data bazaar-j4.dir
  time pg_restore -d rs -j 4 --section=post-data bazaar-j4.dir
  dropdb rs; nproc'
```

```text
pg_restore -d r1 -j 1 bazaar-j4.dir                              44.8 s
pg_restore -d r2 -j 2 bazaar-j4.dir                              26.6 s
pg_restore -d r4 -j 4 bazaar-j4.dir                              20.8 s
pg_restore -d r8 -j 8 bazaar-j4.dir                              24.7 s
pg_restore -d rs --section=pre-data --section=data bazaar-j4.dir   23.4 s
pg_restore -d rs -j 4 --section=post-data bazaar-j4.dir           9.3 s
4
```

(The machine was shared with other workloads; repeat runs varied by up to 30%, which is why section 3.2 shows 30.8 s for `-j 4` from another run. The *shape* is stable.)

Interpretation:

- **Post-data is about half the serial work.** Building 146 indexes and validating 18 foreign keys is CPU-bound and parallelizes well: with `-j 4` it took 9.3 s. Loading data is more I/O-bound and limited by the biggest tables (`events` partitions, `order_items`, `products`), so it parallelizes less.
- **`-j 2` gives most of the gain, `-j 4` a little more, `-j 8` is slower** on 4 cores: the extra workers compete for the same CPUs and disk, and each one is a separate connection with its own memory. Rule of thumb: start with `-j` = number of server cores and measure.
- Parallelism is also bounded by dependencies: an index can only be built after its table's data is loaded, and a foreign key only after both tables' unique indexes exist. `pg_restore` schedules items as their dependencies complete.
- Server settings matter too. For a restore you can temporarily raise `maintenance_work_mem` (faster index builds) and `max_wal_size` (fewer checkpoints) and, on a throw-away target only, turn `fsync` off. These are Module 17's settings applied to one job.

---

## Exercise 9: Break an incremental chain

```bash
docker exec -u postgres m18-primary bash -c '
  mkdir -p /backups/ex9 && cd /backups/ex9
  pg_basebackup -D full -c fast
  psql -d bazaar -qc "UPDATE inventory SET quantity = quantity + 1"
  pg_basebackup -D i1 -c fast --incremental=full/backup_manifest
  psql -d bazaar -qc "UPDATE inventory SET quantity = quantity + 1"
  pg_basebackup -D i2 -c fast --incremental=i1/backup_manifest
  pg_combinebackup full i2 -o out1
  pg_combinebackup i1 full i2 -o out2
  pg_combinebackup i2 -o out3
  pg_combinebackup full i1 i2 -o out4 && pg_verifybackup -q out4 && echo verified
  cd / && rm -rf /backups/ex9'
```

```text
pg_combinebackup: error: backup at "full" starts at LSN 0/2000028, but expected 0/4000028
pg_combinebackup: error: backup at "full" is a full backup, but only the first backup should be a full backup
pg_combinebackup: error: backup at "i2" is an incremental backup, but the first backup should be a full backup
verified
```

What the errors show about how the chain is checked:

1. **Missing middle.** Each incremental's `backup_label` records `INCREMENTAL FROM LSN` (the start of the backup it was taken against). `i2` was taken against `i1`, which started at `0/4000028`; the backup offered before it (`full`) starts at `0/2000028`, so the chain is broken. The message names the backup that does not fit.
2. **Wrong order.** Only the first backup in the list may be a full backup; `pg_combinebackup` does not sort its arguments for you.
3. **Incremental alone.** An incremental only contains changed blocks of relation files (`INCREMENTAL.*` files); without a full backup underneath there is nothing to apply them to.

`pg_combinebackup` refuses rather than producing a silently corrupt data directory, and it checks the system identifier and timelines too. Still, run `pg_verifybackup` on the combined output, and keep the chain short: every link is a single point of failure.

---

## Exercise 10: Compressed archiving

[`archive-wal-gzip.sh`](archive-wal-gzip.sh) keeps the structure of `archive-wal.sh`:

```bash
dest="/archive/wal-gz/$name.gz"
mkdir -p /archive/wal-gz
if [[ -f "$dest" ]]; then
    if gunzip -c "$dest" | cmp -s - "$src"; then exit 0; fi          # idempotent
    echo "... refusing to overwrite" >&2; exit 1                      # never overwrite
fi
gzip -c "$src" > "$dest.tmp"; sync "$dest.tmp"; mv "$dest.tmp" "$dest"; sync /archive/wal-gz   # durable
```

Install and test it:

```bash
cp ../solutions/archive-wal-gzip.sh scripts/
docker exec m18-primary psql -U postgres -c "ALTER SYSTEM SET archive_command = '/lab/scripts/archive-wal-gzip.sh %p %f'" -c "SELECT pg_reload_conf()"
docker exec -u postgres m18-primary /lab/scripts/nightly-backup.sh
RESTORE_COMMAND='gunzip -c /archive/wal-gz/%f.gz > %p' \
ARCHIVED_SEGMENT_PATH='/archive/wal-gz/%s.gz' ./scripts/pitr-drill.sh
docker exec m18-primary ls -la /archive/wal-gz
```

```text
08:17:51 pitr-drill: using base backup /backups/base/base-20261014T081750Z
...
    2026-10-14 08:17:55.677 UTC [29] LOG:  recovery stopping at restore point "drill-20261014T081751Z", time 2026-10-14 08:17:52.102276+00
...
08:17:56 pitr-drill: PASS: restored counts match the snapshot at 'drill-20261014T081751Z' (restore took 2s)

-rw------- 1 postgres postgres 110502 Oct 14 08:17 00000001000000000000000D.gz
-rw------- 1 postgres postgres    239 Oct 14 08:17 00000001000000000000000E.00000028.backup.gz
```

A 16 MB segment became 108 KB. Points to note:

- **The new base backup must come after the switch.** The drill restores the newest base backup and then needs *every* segment after it from the new archive. Segments archived before the switch are in `/archive/wal`, not `/archive/wal-gz`: an older base backup would hit a gap (the pitfall in section 5.2).
- `gzip -c` reads the source and writes to a temporary file, so a crash leaves at most a `.tmp` file that the next attempt overwrites.
- The restore command must fail when the file does not exist. `gunzip -c missing.gz > %p` does return non-zero, but it also creates an empty `%p` file; PostgreSQL ignores the file when the command fails, so this is harmless, though a more careful version would write to a temporary name and rename.
- `nightly-backup.sh` still cleans up `/archive/wal`; for the new archive use `pg_archivecleanup -x .gz /archive/wal-gz <start_wal>` (`-x` strips an extension before comparing names).
- Faster alternatives: `zstd` or `lz4` instead of gzip; or set `wal_compression = zstd` (Module 17) to compress full-page images inside WAL records, which reduces WAL volume at the source. pgBackRest compresses archived WAL by default.

Switch back with `ALTER SYSTEM SET archive_command = '/lab/scripts/archive-wal.sh %p %f'` and remove `scripts/archive-wal-gzip.sh`.

---

## Exercise 11: Detect gaps in the archive

[`ex11_archive_gaps.sql`](ex11_archive_gaps.sql):

```sql
WITH archived AS (
    SELECT f
    FROM pg_ls_dir('/archive/wal') AS f
    WHERE f ~ '^[0-9A-F]{24}$'                       -- segments only (no .history/.backup/.partial)
),
oldest_backup AS (
    SELECT min(split_part(line, E'\t', 6)) AS start_wal
    FROM regexp_split_to_table(pg_read_file('/backups/base/catalog.tsv'), E'\n') AS line
    WHERE split_part(line, E'\t', 1) IN (SELECT d FROM pg_ls_dir('/backups/base') AS d)
),
bounds AS (
    SELECT substr(b.start_wal, 1, 8) AS tli,
           ('x' || substr(b.start_wal, 9, 8))::bit(32)::bigint * 256
         + ('x' || substr(b.start_wal, 17, 8))::bit(32)::bigint AS first_n,
           (SELECT ('x' || substr(max(f), 9, 8))::bit(32)::bigint * 256
                 + ('x' || substr(max(f), 17, 8))::bit(32)::bigint
            FROM archived
            WHERE substr(f, 1, 8) = substr(b.start_wal, 1, 8)) AS last_n
    FROM oldest_backup AS b
),
expected AS (
    SELECT b.tli
        || lpad(upper(to_hex(n / 256)), 8, '0')
        || lpad(upper(to_hex(n % 256)), 8, '0') AS f
    FROM bounds AS b, generate_series(b.first_n, b.last_n) AS n
)
SELECT e.f AS missing_segment
FROM expected AS e
WHERE NOT EXISTS (SELECT 1 FROM archived AS a WHERE a.f = e.f)
ORDER BY 1;
```

On the healthy lab archive it returned `(0 rows)`. After `mv /archive/wal/00000001000000000000000A /tmp/`:

```text
     missing_segment
--------------------------
 00000001000000000000000A
```

Reasoning and limits:

- The hex-to-number trick: `('x' || 'FF')::bit(8)` reads a hex literal as a bit string, and `::bigint` converts it. `n = log * 256 + seg` turns the two-part segment address into one counter, so `generate_series` can enumerate the expected names; `n / 256` and `n % 256` go back.
- `NOT EXISTS` is the anti-join from Module 04; `EXCEPT` would work as well.
- **Only one timeline is checked**: the one the oldest backup starts on. After a timeline switch, the check should continue on the new timeline from the switch point recorded in the `.history` file. A full checker follows the history files; pgBackRest's `check` and `verify` commands do this for you.
- The 256-segments-per-log rule assumes the default 16 MB segment size (`SHOW wal_segment_size`). With 64 MB segments there are 64 per log.
- The newest segment(s) that are still being written or waiting in `pg_wal` are, correctly, not reported: the check only looks up to the newest archived segment. Pair it with exercise 2's backlog check.

---

## Exercise 12: Recover a dropped table by LSN

The incident:

```sql
SELECT pg_relation_filepath('promotions'), count(*) FROM promotions GROUP BY 1;
DROP TABLE promotions;
SELECT place_order(8, '[{"product_id": 1, "quantity": 1}]');      -- the shop keeps running
SELECT pg_walfile_name(pg_current_wal_lsn()), pg_switch_wal();
```

```text
 pg_relation_filepath | count
----------------------+-------
 base/16384/17323     |     9

 place_order
-------------
         307
```

**1. Find the commit.** A `DROP TABLE` commit record lists the files it unlinks:

```bash
docker exec -u postgres m18-primary bash -c '
  for f in $(ls /archive/wal | grep -E "^[0-9A-F]{24}$" | tail -2); do
    pg_waldump --path=/archive/wal --rmgr=Transaction $f; done | grep "rels:"'
```

```text
rmgr: Transaction len (rec/tot):   2365/  2365, tx:        995, lsn: 0/0C03A4F0, prev 0/0C039CE0, desc: COMMIT 2026-10-14 08:17:11.419217 UTC; rels: base/16384/17323 base/16384/17340 base/16384/17329 base/16384/17330 base/16384/17331 base/16384/17333 ...
```

`base/16384/17323` is `promotions` (the other files are its indexes and TOAST table); the commit record is at `0/0C03A4F0`. If you had not looked up the file name before the drop, `rels:` lines are rare enough that the one with the right time is easy to spot, or you could match the relfilenode from an older backup's catalog.

**2. Restore just before it** into a throw-away server:

```bash
docker volume create m18-ex12
docker run --rm -v m18-lab_backups:/backups:ro -v m18-ex12:/var/lib/postgresql/data \
  m18-postgres-pgbackrest:17 bash -c '
  cp -a /backups/base/base-20261014T081647Z/. /var/lib/postgresql/data/ && cd /var/lib/postgresql/data
  cat >> postgresql.auto.conf <<EOF
archive_mode = off
restore_command = '"'"'cp /archive/wal/%f %p'"'"'
recovery_target_lsn = '"'"'0/0C03A4F0'"'"'
recovery_target_inclusive = off
recovery_target_action = '"'"'promote'"'"'
EOF
  touch recovery.signal && chown -R postgres:postgres . && chmod 700 .'
docker run -d --name m18-ex12 -v m18-ex12:/var/lib/postgresql/data \
  -v m18-lab_archive:/archive:ro m18-postgres-pgbackrest:17
docker logs m18-ex12 2>&1 | grep -E "recovery stopping|starting point-in-time"
```

```text
LOG:  starting point-in-time recovery to WAL location (LSN) "0/C03A4F0"
LOG:  recovery stopping before WAL location (LSN) "0/C03A4F0"
```

**3. Copy the table back** with everything attached:

```bash
docker exec m18-ex12 pg_dump -U postgres -d bazaar -Fc -t bazaar.promotions |
  docker exec -i m18-primary pg_restore -U postgres -d bazaar
docker exec m18-primary psql -U postgres -d bazaar -c "SELECT count(*) FROM promotions" -c "\d promotions"
```

```text
 count
-------
     9
...
Indexes:
    "promotions_pkey" PRIMARY KEY, btree (id)
    "promotions_no_overlap" EXCLUDE USING gist (product_id WITH =, active_during WITH &&)
Check constraints:
    "promotions_discount_pct_range" CHECK (discount_pct > 0::numeric AND discount_pct < 100::numeric)
    "promotions_range_not_empty" CHECK (NOT isempty(active_during))
Foreign-key constraints:
    "promotions_product_id_fkey" FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
```

Order 307 was never at risk, because production was never restored over.

Reasoning:

- A **time** target would also have worked, but the `DROP` and the next order were seconds apart; the **LSN** of the commit record is exact. `inclusive = off` stops *before* the record at that LSN, that is, before the `DROP` committed.
- `pg_dump -t` on the restored server dumps the table with its sequence (identity), indexes, constraints, comments, grants and policies, but not its *dependencies* (`btree_gist`, `products`), which is fine because production still has them. This is the opposite situation from section 3.5, where the scratch database had nothing.
- If other tables had foreign keys pointing *to* the dropped table, `DROP TABLE` would have needed `CASCADE`, dropping those constraints too; you would recreate them from the restored server (`pg_dump --schema-only -t` the referencing tables and pick the `FK CONSTRAINT` entries).
- Clean up: `docker rm -f m18-ex12 && docker volume rm m18-ex12`.
