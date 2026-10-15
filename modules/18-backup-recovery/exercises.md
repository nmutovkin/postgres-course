# Module 18 exercises

<!-- nav:start -->
> [Theory](README.md) · **Exercises** · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 17 Configuration, Maintenance and Monitoring](../17-operations/README.md) · [Course home](../../README.md) · [19 Replication →](../19-replication/README.md)
<!-- nav:end -->

Exercises 1, 4, 5 and 8 use the **course database** (`course-pg`) at the Module 18 state with the large dataset (`./project/bin/reset.sh 18`). All the others use the **lab server** `m18-primary` with the small dataset (`COURSE_PG_CONTAINER=m18-primary ../../../project/bin/reset.sh 11` from `modules/18-backup-recovery/lab`), with at least one base backup taken by `scripts/nightly-backup.sh`. Run shell commands from the lab directory unless stated otherwise. Inside the course container, put dump files under `/tmp/dumps`.

Solutions are in [`solutions/README.md`](solutions/README.md).

---

### Exercise 1 ★: Read a dump's table of contents

Take a custom-format dump of `bazaar` from `course-pg` and answer from its table of contents alone (`pg_restore -l`), without restoring anything:

1. How many `TABLE DATA` entries does it contain? The database has 41 tables in schema `bazaar` (`\dt bazaar.*`). Why is the number of `TABLE DATA` entries different?
2. List every entry that concerns the `reviews` table (the table itself, its sequence, data, constraints, indexes, foreign keys). Which of them belong to the *post-data* section?

**Self-check:** 40 `TABLE DATA` entries; the reviews list has 10 entries.

<details><summary>Hint</summary>

`grep -c " TABLE DATA "` counts entries. For question 1, think about which kind of table never holds rows itself (Module 14). For question 2, an entry's type (the word after the OIDs) tells you its section: tables and sequences are pre-data, `TABLE DATA` and `SEQUENCE SET` are data, everything that needs the data first is post-data.
</details>

---

### Exercise 2 ★: An archiver health query

Write **one** query that returns a single row with: the number of WAL segments waiting to be archived, the total size of `pg_wal`, the last archived file, the number of seconds since it was archived, and a boolean `is_failing` that is true when the most recent archive attempt failed.

**Self-check:** on a healthy lab server `segments_waiting = 0` and `is_failing = f`. Make `/archive/wal` unwritable (`docker exec m18-primary chmod 500 /archive/wal`), write something and call `pg_switch_wal()`: within a few seconds `is_failing` becomes `t` and `segments_waiting` becomes at least 1. Restore the permissions (`chmod 750`) afterwards.

<details><summary>Hint</summary>

`pg_stat_archiver` has the timestamps; `pg_ls_archive_statusdir()` lists the `.ready`/`.done` files; `pg_ls_waldir()` lists the segments with their sizes. Be careful with NULLs: on a fresh server `last_failed_time` is NULL.
</details>

---

### Exercise 3 ★: Anatomy of a nightly backup

For the newest backup under `/backups/base` in the lab, determine without starting it:

1. the first WAL segment it needs, read from its `backup_label`, and check that it matches the `start_wal` column of the last line of `/backups/base/catalog.tsv`;
2. how many files its `backup_manifest` lists, and verify that this equals the number of files on disk, excluding the manifest itself and `pg_wal/`;
3. the WAL range (`Start-LSN`, `End-LSN`) the manifest records, and which file in the backup's `pg_wal/` contains it.

**Self-check:** the two file counts are equal; the `Start-LSN` equals the `START WAL LOCATION` in `backup_label`.

<details><summary>Hint</summary>

`grep -c '"Path"' backup_manifest` counts manifest entries; `find DIR -type f ! -name backup_manifest ! -path '*/pg_wal/*' | wc -l` counts files. Why are files in `pg_wal/` not in the manifest's file list?
</details>

---

### Exercise 4 ★★: A lean developer copy

Developers want a copy of Bazaar on their laptops, with every table, function, index and policy, but **without** the rows of the clickstream (`events` and all its partitions) and of `audit_log`, which are large and not needed for development. Produce a single dump file for this, restore it into a new database `bazaar_dev` on `course-pg`, and report the size of the dump and of the restored database.

**Self-check:** in `bazaar_dev`, `events` has 0 rows but still has 25 partitions; `orders` has 500,000 rows. The dump is less than half the size of a full custom-format dump made with the same compression (with `-Z zstd`: about 65 MB versus 152 MB). Drop `bazaar_dev` afterwards.

<details><summary>Hint</summary>

`--exclude-table-data` takes a pattern and can be repeated. Excluding the *data* of a partitioned table's parent does not exclude its partitions' data; a wildcard pattern does. Restore with `-j` for speed.
</details>

---

### Exercise 5 ★★: Undo a bad bulk update from last night's dump

On `course-pg`, first take a custom dump of `bazaar` (this is "last night's dump"). Then simulate the incident: a seller-tools script meant to raise seller 4's prices by 10% ran `UPDATE bazaar.products SET price = price * 10 WHERE seller_id = 4`.

Restore seller 4's prices from the dump **without** restoring over production, and without touching any product whose price was changed legitimately after the dump (treat a product as "damaged" only if its current price is exactly 10 times the dumped price).

**Self-check:** before the incident, seller 4 has 5,981 products whose prices sum to 1,079,730.19; after your repair, the sum is the same again and a comparison against the dumped prices finds 0 differences.

<details><summary>Hint</summary>

Restore the dump's pre-data section and only the `products` data into a scratch database (section 3.5). Production and scratch are different databases, so you need a way to move `(id, price)` pairs across: `\copy ... TO` a CSV file and `\copy ... FROM` it into a temporary table in production is enough. Then one `UPDATE ... FROM`.
</details>

---

### Exercise 6 ★★: How far back can we go?

Your manager asks: "If someone deletes data right now, which points in time can we restore to?" Write a query for the lab server that answers with one row: `restorable_from` (the earliest moment a kept base backup lets you recover to), `restorable_until` (the latest moment covered by archived WAL), the window length, and the number of base backups currently kept.

Use `/backups/base/catalog.tsv` (written by `nightly-backup.sh`) and the backup directories that still exist (retention deletes old ones; the catalog keeps every line).

**Self-check:** run `nightly-backup.sh` a few times: `base_backups_kept` never exceeds 3 (the script's `KEEP`), and `restorable_from` equals the `finished_at` of the oldest backup directory still on disk.

<details><summary>Hint</summary>

`pg_read_file()` reads the catalog, `regexp_split_to_table(..., E'\n')` turns it into lines, `split_part(line, E'\t', n)` picks a column, and `pg_ls_dir('/backups/base')` lists existing backups. Why is the earliest restorable point the backup's *end*, not its start? Can the window span a timeline switch?
</details>

---

### Exercise 7 ★★: Test a pgBackRest backup without touching production

Set up the `bazaar` stanza and take a full pgBackRest backup (section 8). Then restore that backup into a **separate directory** on a new Docker volume, start a second PostgreSQL server on it, and check that it contains the same number of orders as production had at backup time. The restored server must not archive anything, and production must keep running throughout.

**Self-check:** the restored server reports `pg_is_in_recovery() = f`, `SHOW archive_mode` returns `off`, and its `orders` count equals production's count at the time of the backup.

<details><summary>Hint</summary>

Useful restore options: `--pg1-path` (where to restore), `--archive-mode=off`, `--type=immediate` (stop as soon as the backup is consistent) and `--target-action=promote`. Use `docker compose run` with an extra `-v` for the new volume, and start the second server with `-e PGDATA=...`. The restored server's `restore_command` calls `pgbackrest archive-get`, so its container needs the repository volume and the configuration file too.
</details>

---

### Exercise 8 ★★: How much does `-j` help?

On `course-pg`, restore a directory-format dump of `bazaar` into fresh databases with `pg_restore -j 1`, `-j 2`, `-j 4` and `-j 8`, timing each. Then split one restore into two steps: pre-data plus data with no parallelism, then post-data with `-j 4`, and time both steps. Explain the shape of the results.

**Self-check:** a property, not a number: `-j 2` and `-j 4` are clearly faster than `-j 1`, and `-j 8` is not faster than `-j 4` on a 4-core machine (`nproc` in the container). The post-data step takes a large share of a serial restore.

<details><summary>Hint</summary>

Drop each database right after timing it: every copy is a full 1.5 GB `bazaar` database. Think about which section contains the CPU-heavy work, what limits parallelism (the largest table, the number of cores) and what extra workers cost.
</details>

---

### Exercise 9 ★★: Break an incremental chain

On the lab server, take a PostgreSQL 17 chain: a full backup, an incremental relative to it, and a second incremental relative to the first (change some data between backups, for example `UPDATE inventory SET quantity = quantity + 1`). Then try to combine the chain **wrongly** in three ways and record the error each time:

1. leave out the middle backup;
2. put the full backup in the middle;
3. combine the last incremental on its own.

Finally combine it correctly and verify the result.

**Self-check:** three different `pg_combinebackup` errors, then `pg_verifybackup` prints `backup successfully verified` for the correct combination.

<details><summary>Hint</summary>

`pg_basebackup -D i1 --incremental=full/backup_manifest`. `pg_combinebackup` takes the backups oldest first and `-o` for the output directory. Work in `/backups/ex9` and delete it afterwards.
</details>

---

### Exercise 10 ★★★: Compressed archiving

WAL segments compress very well, especially the mostly empty ones produced by `archive_timeout`. Write `archive-wal-gzip.sh`, a variant of `scripts/archive-wal.sh` that stores gzip-compressed segments in `/archive/wal-gz`, while keeping the three rules (durable, never overwrite a different file, idempotent). Switch the lab server to it, take a new base backup, and prove that point-in-time recovery still works by running `scripts/pitr-drill.sh` with a matching restore command. (The drill script reads `RESTORE_COMMAND` and `ARCHIVED_SEGMENT_PATH` from the environment; read its header.)

**Self-check:** the drill prints `PASS`; files in `/archive/wal-gz` end in `.gz` and a segment is around 100 KB instead of 16 MB. Switch `archive_command` back to `archive-wal.sh` afterwards.

<details><summary>Hint</summary>

Put your script in `lab/scripts/` so the container sees it as `/lab/scripts/archive-wal-gzip.sh`. For idempotency, compare *uncompressed* content: `gunzip -c "$dest" | cmp -s - "$src"`. The restore command is a shell command too: `gunzip -c /archive/wal-gz/%f.gz > %p`. Why must the new base backup be taken *after* the switch?
</details>

---

### Exercise 11 ★★★: Detect gaps in the archive

A single missing WAL segment makes every point-in-time recovery past it impossible, and you would only find out during a restore. Write a SQL query (run as a superuser on the lab server) that lists every segment missing from `/archive/wal` between the start WAL of the oldest base backup still kept and the newest archived segment.

**Self-check:** on a healthy archive it returns 0 rows. Move one segment out of the way (`docker exec m18-primary mv /archive/wal/<segment> /tmp/`) and it returns exactly that segment. Put it back afterwards.

<details><summary>Hint</summary>

A segment name is `TTTTTTTTLLLLLLLLSSSSSSSS` in hex: timeline, then a "log" number and a segment number within it. With 16 MB segments there are 256 segments per log, so `log * 256 + seg` numbers segments consecutively. `('x' || hexstring)::bit(32)::bigint` converts hex to a number; `to_hex()` and `lpad()` go back. Generate the expected names with `generate_series` and anti-join (Module 04) against `pg_ls_dir('/archive/wal')`.
</details>

---

### Exercise 12 ★★★: Recover a dropped table by LSN

On the lab server (with a nightly backup taken), drop the `promotions` table, then place one more order through `place_order()` (the shop keeps running). Without knowing the time of the `DROP`, recover the table:

1. use `pg_waldump` to find the commit record of the transaction that dropped `promotions` (hint: a commit record lists the relation files it removes, and you can find out `promotions`' file name beforehand or from the `DROP`'s neighbourhood);
2. restore the base backup into a throw-away server with `recovery_target_lsn` so that recovery stops **just before** that commit;
3. copy the table (definition, data, constraints) from the throw-away server back into production, without losing the new order.

**Self-check:** production has `promotions` again with 9 rows, its exclusion constraint `promotions_no_overlap` and its foreign key, and the order placed after the `DROP` still exists.

<details><summary>Hint</summary>

`pg_waldump --rmgr=Transaction` prints commit records; a `DROP TABLE` commit contains `rels: base/<db>/<relfilenode> ...`. Set `recovery_target_inclusive = off` with the commit record's LSN. To copy one table with everything attached, `pg_dump -Fc -t bazaar.promotions` on the restored server piped into `pg_restore -d bazaar` on production works, because production still has everything `promotions` depends on.
</details>
