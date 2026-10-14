# Module 19 exercises

All exercises use the module's lab (`modules/19-replication/lab`), freshly built and loaded, unless an exercise says otherwise:

```bash
cd modules/19-replication/lab
docker compose --profile extras down -v          # start from scratch
docker compose up -d
COURSE_PG_CONTAINER=m19-primary ../../../project/bin/reset.sh 11
```

Exercises 9, 11 and 12 also need the milestone's analytics replica (`scripts/setup-analytics.sql` and `scripts/analytics-schema.sql`, see [`project.md`](project.md)). Exercises 6 and 10 need the optional nodes (`docker compose --profile extras up -d`). Section 12 of the module breaks the topology; rebuild the lab before doing the exercises.

Solutions are in [`solutions/README.md`](solutions/README.md).

---

### Exercise 1 ★: Draw the topology from SQL alone

Without looking at `docker-compose.yml`, determine for every running node: whether it is a primary or a standby, which server it streams from, which servers stream from it, and which of its standbys are synchronous. Use only SQL (`pg_is_in_recovery()`, `pg_stat_replication`, `pg_stat_wal_receiver`, `current_setting('cluster_name')`) and draw the result as a small diagram. Do it once with the default nodes and once with the `extras` profile running.

**Self-check:** with the extras running, `m19-primary` serves three standbys (`replica1` sync, `replica2` and `delayed` async), `m19-replica2` serves one (`replica3`), and `m19-replica3`'s WAL receiver points at `m19-replica2`.

<details><summary>Hint</summary>

Run the same query on each container in a shell loop. A standby that feeds another standby also has rows in `pg_stat_replication`.
</details>

---

### Exercise 2 ★: What did `pg_basebackup` copy?

`pg_basebackup -R` copied the primary's configuration into each standby (section 3.2). Using `pg_file_settings` on `m19-replica1`, list every setting that comes from its `postgresql.auto.conf`, and say for each one whether it came from the primary or was written by `pg_basebackup -R`. Which of them would cause trouble if `replica1` were promoted?

**Self-check:** 10 settings, 2 of them written by `-R` (`primary_conninfo`, `primary_slot_name`); `synchronous_standby_names = 'FIRST 1 (replica1)'` is among the rest.

<details><summary>Hint</summary>

`pg_file_settings` has `sourcefile`, `name`, `setting` and `applied`. Compare with the same view on the primary.
</details>

---

### Exercise 3 ★: Rename a standby

Make `m19-replica2` identify itself to the primary as `reporting` instead of `replica2`, without restarting it and without changing `cluster_name`. Then explain what would happen to synchronous replication if you renamed `replica1` the same way, and check your prediction.

**Self-check:** `pg_stat_replication` on the primary shows `reporting` (async). Renaming `replica1` to anything not listed in `synchronous_standby_names` makes the primary's commits wait (`wait_event = 'SyncRep'`) until you rename it back or change `synchronous_standby_names`. Undo both changes at the end.

<details><summary>Hint</summary>

`application_name` inside `primary_conninfo` takes precedence over `cluster_name`. `primary_conninfo` can be changed with `ALTER SYSTEM` and a reload on the standby. Test the commit with a session-level `SET statement_timeout` and remember from section 7.4 why that does not help; cancel from another session instead.
</details>

---

### Exercise 4 ★★: An honest lag measurement

Section 6.2 promised a heartbeat. Build one:

1. On the primary, a table `public.heartbeat (id int PRIMARY KEY, ts timestamptz NOT NULL)` in database `bazaar` with one row.
2. A loop that updates the row every second (a shell `while` loop running `docker exec ... psql -c "UPDATE ..."` is fine for the lab).
3. A query to run on any standby that returns its staleness in seconds.

Then pause replay on `replica2` for about 15 seconds and watch both your staleness value and `replay_lag` from `pg_stat_replication`.

**Self-check:** normally the staleness on each standby is below 2 seconds. While `replica2` is paused it grows by about one second per second, then drops back below 2 seconds within a second of `pg_wal_replay_resume()`.

<details><summary>Hint</summary>

`now() - ts` on the standby compares the standby's clock with the primary's, which is fine when clocks are synchronized (NTP). Why does a heartbeat give a meaningful number even when the shop has no traffic at all?
</details>

---

### Exercise 5 ★★: Slot early warning

Write a monitoring query for the primary that returns one row per replication slot with: name, type, active, `wal_status`, retained WAL (pretty-printed), `safe_wal_size` (pretty-printed), and the percentage of `max_slot_wal_keep_size` already used by the slot (NULL if unlimited). Order it so the most dangerous slot comes first.

Test it: stop `replica2`, generate about 200 MB of WAL on the primary (`CREATE TABLE t AS SELECT g, repeat('x', 200) FROM generate_series(1, 800000) g`), and run your query. Then start `replica2` again, drop the test table, and run it once more.

**Self-check:** with `replica2` stopped, its slot is first in the list, inactive, retaining about 200 MB, at roughly 10% of the lab's 2 GB limit; after the restart it catches up and its retained WAL drops to a few kilobytes.

<details><summary>Hint</summary>

`current_setting('max_slot_wal_keep_size')` returns text such as `2GB` or `-1`; `pg_size_bytes()` converts it. Retained WAL is `pg_current_wal_lsn() - restart_lsn`.
</details>

---

### Exercise 6 ★★: Quorums and the delayed standby

The `extras` profile adds `m19-delayed`, which *flushes* WAL immediately but *applies* it five minutes late. Set `synchronous_standby_names = 'ANY 1 (delayed)'` on the primary. Before measuring, predict how long a single-row `INSERT ... COMMIT` takes with `synchronous_commit` set to `on`, `remote_write` and `remote_apply`. Then measure (for `remote_apply`, give up after 10 seconds by cancelling from another session) and explain. Restore `'FIRST 1 (replica1)'` afterwards.

**Self-check:** `on` and `remote_write` commit in a few milliseconds; `remote_apply` does not return within 10 seconds, and cancelling prints `canceling wait for synchronous replication` with the "already committed locally" detail.

<details><summary>Hint</summary>

Which of the four positions (section 2.2) does `recovery_min_apply_delay` hold back? Use `\timing on` in psql.
</details>

---

### Exercise 7 ★★: Read-your-writes in one psql script

Write a psql script, run from inside the `m19-analytics` container (which can reach every node), that:

1. connects to the primary, places an order with `place_order(2, '[{"product_id": 1, "quantity": 1}]')`, and saves the new order id and the current WAL LSN in psql variables;
2. connects to `m19-replica2`, waits until the replica has replayed that LSN (at most 5 seconds; fail with a clear error otherwise);
3. reads the order back from the replica.

Run it twice: normally, and with replay paused on `replica2`.

**Self-check:** normally the script prints the new order with status `pending`. With replay paused it fails after about 5 seconds with your error message, and the order is not read.

<details><summary>Hint</summary>

`\gset` stores query results in variables; `\connect` switches servers inside one psql session. A `DO` block runs on a standby (it is read-only work) and can loop with `pg_sleep(0.1)`. Pass the LSN into the `DO` block with `format()` and `\gexec`, or set it as a custom setting (`SET my.lsn = :'lsn'`) and read it with `current_setting('my.lsn')`.
</details>

---

### Exercise 8 ★★: Which tables are not ready for logical replication?

Before running `CREATE PUBLICATION ... FOR ALL TABLES` (for an upgrade, section 13), you must know which tables would start rejecting `UPDATE` and `DELETE`. Write a query against the catalogs that lists every table in schema `bazaar` whose replica identity would not identify rows: replica identity `DEFAULT` without a primary key, or `NOTHING`. Also list tables with `REPLICA IDENTITY FULL`, with a note.

**Self-check:** on the state-11 database every table has a primary key, so the first list is empty. Create a test table without a primary key (`CREATE TABLE bazaar.import_staging (sku text, qty int)`): it must appear. Drop it afterwards.

<details><summary>Hint</summary>

`pg_class.relreplident` is `d` (default), `n` (nothing), `f` (full) or `i` (index). `pg_index.indisprimary` tells you whether a table has a primary key.
</details>

---

### Exercise 9 ★★: Complaints for the analytics team

The customer-care team wants the **negative reviews** (rating 1 or 2) on the analytics server, without the review text (`body`). Extend the milestone's publication and subscription accordingly, without recreating the subscription and without re-copying the tables that are already there.

**Self-check:** on `m19-analytics`, `SELECT count(*) FROM bazaar.reviews` equals the primary's `SELECT count(*) FROM reviews WHERE rating <= 2`; inserting a 1-star review on the primary makes it appear on the analytics server; changing that review's rating to 5 makes it disappear there.

<details><summary>Hint</summary>

`ALTER PUBLICATION ... ADD TABLE` with a column list and a row filter, then `ALTER SUBSCRIPTION ... REFRESH PUBLICATION` on the subscriber (which copies only the *new* table). Check rule 2 of section 10.2: is `rating` part of the replica identity?
</details>

---

### Exercise 10 ★★: Undo a `DROP TABLE` with the delayed standby

With the `extras` profile running, drop the `promotions` table on the primary. Use the delayed standby to put the table back on the primary, **with** its exclusion constraint, check constraints and foreign key, before the delayed standby replays the drop.

**Self-check:** the primary has `promotions` again with 9 rows, `\d promotions` shows `promotions_no_overlap`, and `m19-delayed` still shows the table while paused. Resume replay at the end: five minutes later the delayed standby applies the drop and then the re-creation.

<details><summary>Hint</summary>

`pg_dump` works against a standby (it only reads). Pause replay first. Pipe `pg_dump -Fc -t bazaar.promotions` from the delayed standby into `pg_restore` on the primary.
</details>

---

### Exercise 11 ★★★: Rehearse a zero-downtime migration

Use logical replication to move the whole Bazaar database from `m19-primary` to a new database `bazaar_v2` on `m19-analytics` (a separate cluster, standing in for a server running a newer major version):

1. copy the schema (roles first: `pg_dumpall --globals-only`; then `pg_dump --schema-only`);
2. publish every table and subscribe from `bazaar_v2`;
3. while replication is running, place a few orders on the old primary and check that they arrive;
4. switchover: stop writes on the old primary, wait for zero lag, **synchronize the sequences**, and prove that the new database accepts new orders through `place_order()`.

**Self-check:** after the switchover, every table in schema `bazaar` has the same row count on both sides, and `SELECT place_order(...)` on `bazaar_v2` succeeds and returns an id larger than any id that existed on the old primary.

<details><summary>Hint</summary>

The schema dump includes triggers; on the subscriber they do not fire for replicated changes, which is what you want (the publisher already computed `orders.total`). For the sequences, generate `setval()` calls from `pg_sequences` on the old primary with `format()` and run them on the new database. To stop writes, `ALTER DATABASE bazaar SET default_transaction_read_only = on` plus terminating existing sessions is a lab-friendly method. Mind `pg_hba.conf` and the replication privilege of the role in the subscription's connection string.
</details>

---

### Exercise 12 ★★★: Skip a transaction, then repair

`ALTER SUBSCRIPTION ... SKIP` throws away a whole remote transaction. Practise it, and the repair it requires, on the analytics replica:

1. On `m19-analytics`, insert a junk row into `bazaar.orders` with the id the *next* order on the primary will get.
2. On the primary, in **one** transaction: pay the pending order 115 (`status = 'paid', paid_at = now()`), place a new order with `place_order()`, and pay that new order too.
3. The subscription now fails on a duplicate key. Resolve it with `SKIP`, not by deleting the junk row.
4. Bring the analytics server back in line with the publisher for **everything** the skipped transaction touched.

**Self-check:** after the repair, for orders 115 and the new order, `SELECT id, status, total, paid_at FROM bazaar.orders` returns the same rows on both servers, the new order's `order_items` match too, the junk row is gone, and `pg_stat_subscription_stats.apply_error_count` stops increasing.

<details><summary>Hint</summary>

The `finished at` LSN in the subscriber's error context is the value for `SKIP (lsn = ...)`. What did the skipped transaction contain, as seen by the subscriber? Remember the row filter: paying a pending order arrives as an `INSERT`, and the new order's items are published regardless of payment. Copy the affected rows from the publisher with `\copy` pipes (or `INSERT ... VALUES` by hand for a few rows). Why would `TRUNCATE` plus a fresh `copy_data` be the safe but heavy alternative?
</details>
