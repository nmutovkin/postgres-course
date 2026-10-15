# Module 14: Project milestone — partitioned events, their upkeep, and a live migration

<!-- nav:start -->
> [Theory](README.md) · [Exercises](exercises.md) · **Project** · [Solutions](solutions/README.md)  
> [← 13 Full-Text and Fuzzy Search](../13-full-text-search/README.md) · [Course home](../../README.md) · [15 Security and Row-Level Security →](../15-security-rls/README.md)
<!-- nav:end -->

Bazaar's analytics team is about to start collecting clickstream events: page views, product views, cart changes, checkouts, searches. That is millions of rows a month, queried by month, and kept for 12 months. This milestone builds the `events` table partitioned by month, the maintenance that keeps it healthy month after month, and then applies the same technique to a table that already exists and is being written to: `audit_log`.

**Starting state:** `./project/bin/reset.sh 14` (the large dataset, with full-text search; no `events` table yet).

<!-- toc:start -->
**Contents**

- [Part 1: the partitioned `events` table](#part-1-the-partitioned-events-table)
- [Part 2: the maintenance procedure](#part-2-the-maintenance-procedure)
- [Part 3: migrate `audit_log` to partitioning, live](#part-3-migrate-audit_log-to-partitioning-live)
- [Acceptance checks](#acceptance-checks)
  - [Part 1](#part-1)
  - [Part 2](#part-2)
  - [Part 3](#part-3)
- [Reference solution](#reference-solution)

<!-- toc:end -->

## Part 1: the partitioned `events` table

Write your own version of [`project/db/1401_schema_events.sql`](../../project/db/1401_schema_events.sql):

1. `events(id, occurred_at, user_id, session_id, event_type, product_id, payload)`:
   - `id bigint` identity, `occurred_at timestamptz NOT NULL`, `user_id bigint` (NULL for anonymous visitors), `session_id uuid NOT NULL`, `event_type text NOT NULL` limited by a `CHECK` to `page_view`, `product_view`, `add_to_cart`, `remove_from_cart`, `checkout_started`, `order_placed`, `search`, `product_id bigint`, `payload jsonb NOT NULL DEFAULT '{}'`;
   - **RANGE-partitioned by `occurred_at`, one partition per calendar month in UTC**, named `events_yYYYYmMM`;
   - a primary key that PostgreSQL accepts on a partitioned table (and a comment explaining why it isn't just `id`);
   - no foreign keys (write down why, in a comment).
2. A function `create_events_partition(p_month date) RETURNS text` that creates the partition for the month containing `p_month` if it doesn't exist yet, and returns its name. Bounds must not depend on the session's `TimeZone`. Use `format()` with `%I` and `%L` for the dynamic SQL.
3. Partitions for January 2025 to December 2026, and a `DEFAULT` partition.
4. Partitioned indexes on `occurred_at` and on `product_id`.

Then load the reference data with the reference seed, which only needs your table to have the right columns and partitions:

```bash
docker exec -i course-pg psql -U postgres -d bazaar < project/db/1402_seed_events.sql    # about a minute
```

## Part 2: the maintenance procedure

Write a procedure that a scheduler (cron, `pg_cron`, a Kubernetes CronJob) calls every day:

```sql
CALL maintain_monthly_partitions(
    p_parent         regclass,                          -- e.g. 'events'
    p_months_ahead   int      DEFAULT 3,                -- create partitions up to 3 months ahead
    p_retention      interval DEFAULT interval '12 months',
    p_today          date     DEFAULT current_date,     -- overridable, for tests and for this dataset
    p_archive_schema text     DEFAULT NULL,             -- NULL: drop expired partitions; else move them there
    p_max_remove     int      DEFAULT 2                 -- safety valve
);
```

Requirements:

1. It works for **any** table RANGE-partitioned monthly on a `timestamptz` column with partitions named `<table>_yYYYYmMM` (you will reuse it for `audit_log` in part 3). Write a generic `create_monthly_partition(parent regclass, month date)` for it, which **skips months already covered by another partition** (for example a big legacy partition) instead of failing.
2. It creates the partitions from the current month to `p_months_ahead` months ahead. Running it twice in a row changes nothing.
3. It finds expired partitions **from their bounds** (not from their names): a partition expires when its upper bound is not after "start of the current month minus `p_retention`". It detaches each one, then drops it or moves it to `p_archive_schema`.
4. It never removes more than `p_max_remove` partitions in one call: if more have expired, it raises an error and changes nothing. (In this course, "today" in the data is 2025-12-31 but `current_date` is your real clock: the defaults would expire most of 2025. The safety valve is what stops that.)
5. It never waits long for a lock: use a `lock_timeout`, and release the locks of each partition before handling the next one (a procedure can `COMMIT`).
6. It warns (`RAISE WARNING`) when the `DEFAULT` partition is not empty.

## Part 3: migrate `audit_log` to partitioning, live

`audit_log` (from Module 09) records every change to products and orders through `audit_trigger()`. In the large dataset it is empty, so first fill it with 1.5 million realistic rows from January 2024 to December 2025:

```bash
docker exec -i course-pg psql -U postgres -d bazaar < modules/14-partitioning/solutions/audit_log_seed.sql   # about 15 s
```

Convert it into a table RANGE-partitioned by month on `changed_at`, **while the application keeps writing to it**. Requirements:

1. Afterwards `audit_log` is a partitioned table with the same columns, defaults and `CHECK` constraint; `audit_trigger()` keeps working without any change; ids continue from where they were; no row is lost.
2. New rows go to monthly partitions named `audit_log_yYYYYmMM` from a cut-over month onwards; your `maintain_monthly_partitions('audit_log', ...)` works on it.
3. The existing rows are **not copied**. Nothing that scans the table runs under a lock that blocks writes. The switch itself holds the `ACCESS EXCLUSIVE` lock for milliseconds and gives up (rather than queueing) if it can't get the lock within a few seconds.
4. Prove it under load: run the migration while `pgbench` updates random products (each update writes an audit row), and get **zero failed transactions**.

The theory's section 10 describes the strategy. Expect to meet, and solve, each of these errors at least once: `child table is missing constraint`, `multiple primary keys for table ... are not allowed`, `table ... being attached contains an identity column`.

## Acceptance checks

### Part 1

```sql
\d events
--  Partition key: RANGE (occurred_at); the primary key includes occurred_at; 25 partitions

SELECT count(*), min(id), max(id) FROM events;
--  4983725 | 1 | 4983725

SELECT tableoid::regclass, occurred_at FROM events WHERE id = 1234567;
--  events_y2025m04 | 2025-04-21 14:55:00+00

SELECT count(*) FROM events_default;                                    -- 0

SELECT create_events_partition('2025-11-15'), create_events_partition('2025-11-15');
--  events_y2025m11 | events_y2025m11       (idempotent)

EXPLAIN (COSTS OFF)
SELECT count(*) FROM events WHERE occurred_at >= '2025-11-01' AND occurred_at < '2025-12-01';
--  reads events_y2025m11 only

SET TimeZone = 'America/Los_Angeles';
SELECT create_events_partition('2027-01-01');
SELECT pg_get_expr(relpartbound, oid) FROM pg_class WHERE relname = 'events_y2027m01';
--  FOR VALUES FROM ('2026-12-31 16:00:00-08') TO ('2027-01-31 16:00:00-08')
DROP TABLE events_y2027m01;
RESET TimeZone;
```

`pg_get_expr` prints the bounds in the session's time zone: 16:00 on December 31 in Los Angeles *is* midnight UTC on January 1. If yours prints `FROM ('2027-01-01 00:00:00-08')`, your bounds depend on the session's time zone, and a partition created from a session in another time zone would leave gaps or overlap its neighbours.

### Part 2

```sql
CREATE SCHEMA IF NOT EXISTS archive;

-- the defaults, with your real clock, must be refused
CALL maintain_monthly_partitions('events');
--  ERROR:  9 partitions of events are older than ... more than p_max_remove (2): nothing removed
--  (the number depends on your date; nothing is created or removed)

-- "today" of the dataset, 10 months of retention: January 2025 expires
CALL maintain_monthly_partitions('events', 3, interval '10 months', date '2025-12-31', 'archive');
--  NOTICE:  archived bazaar.events_y2025m01 to schema archive
SELECT count(*) FROM events;                          -- 4657594
SELECT count(*) FROM archive.events_y2025m01;         -- 326131

-- idempotent
CALL maintain_monthly_partitions('events', 3, interval '10 months', date '2025-12-31', 'archive');
--  no notices; still 24 partitions
```

Then put January back without a validation scan (theory section 9.3), and check that `events` has 4,983,725 rows again.

### Part 3

After the seed, and before the migration: `SELECT count(*) FROM audit_log` is 1,500,000.

Run the migration under load: start this `pgbench` (4 clients, 25 seconds) and run your migration script while it is running.

```bash
cat > /tmp/audit_load.sql <<'EOF'
\set id random(1, 200000)
UPDATE bazaar.products SET price = price + 0.01 WHERE id = :id;
EOF
docker cp /tmp/audit_load.sql course-pg:/tmp/audit_load.sql
docker exec course-pg pgbench -U postgres -n -c 4 -T 25 -f /tmp/audit_load.sql bazaar &
sleep 3
docker exec -i course-pg psql -U postgres -d bazaar < your_migration.sql
wait
```

- `pgbench` reports `number of failed transactions: 0 (0.000%)`. (In the reference run: 59,812 transactions, 0 failed.)
- `SELECT relkind FROM pg_class WHERE relname = 'audit_log'` is `p`.
- `SELECT count(*), max(id), count(DISTINCT id) FROM audit_log` gives three equal numbers: 1,500,000 plus the number of `pgbench` transactions.
- `\d+ audit_log` lists a legacy partition `FROM (MINVALUE) TO (<cut-over>)`, monthly partitions after it, and the primary key `(id, changed_at)`.
- `UPDATE products SET price = price + 1 WHERE id = 1;` adds one row to `audit_log` (check its `tableoid`); undo it with `price - 1`.
- `CALL maintain_monthly_partitions('audit_log', 3, interval '24 months');` runs without error.
- Your migration's switch transaction (from `BEGIN` to `COMMIT`) took less than 100 ms. (Reference: about 35 ms under load.)

Finally, restore the reference state for the next modules: `./project/bin/reset.sh 15`.

## Reference solution

- [`project/db/1401_schema_events.sql`](../../project/db/1401_schema_events.sql) and [`project/db/1402_seed_events.sql`](../../project/db/1402_seed_events.sql): Part 1 and the data.
- [`solutions/partition_maintenance.sql`](solutions/partition_maintenance.sql): Part 2, `partition_bounds()`, `create_monthly_partition()` and `maintain_monthly_partitions()`.
- [`solutions/audit_log_seed.sql`](solutions/audit_log_seed.sql): the 1.5 million audit rows.
- [`solutions/audit_log_migration.sql`](solutions/audit_log_migration.sql): Part 3 (requires `partition_maintenance.sql`).

The Part 2 and Part 3 objects are not in `project/db`, so `reset.sh 15` and later states don't contain them: `audit_log` stays unpartitioned in the reference project, and later modules don't depend on its shape.

Design notes on the reference:

- **Bounds from the catalog.** `partition_bounds()` parses `pg_get_expr(relpartbound, oid)` and turns `MINVALUE`/`MAXVALUE` into `-infinity`/`infinity`, so retention and overlap checks work for the legacy partition of part 3 too, and renaming a partition can't make it immortal.
- **One partition per transaction.** The procedure commits after each detach, so it never holds the locks of several partitions at once, and a failure half-way leaves a consistent state that the next run continues from.
- **Plain `DETACH`, not `CONCURRENTLY`.** `events` has a default partition (which forbids `CONCURRENTLY`), and `CONCURRENTLY` can't run inside a procedure anyway. The 5-second `lock_timeout` keeps the plain form from stalling the application.
- **The cut-over is two months ahead** in the migration: the old table gets `CHECK (changed_at < cut-over)`, so if the switch were delayed past that date, audit rows (and with them the audited updates) would start failing. Two months is a comfortable margin; the switch happens minutes later.
- **The legacy partition** holds all the history until the cut-over. It is dropped in one piece by the same retention procedure once its upper bound expires (with a 24-month retention, two years after the cut-over).
