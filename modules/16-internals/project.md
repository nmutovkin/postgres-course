# Module 16 project: a storage report, and HOT inventory updates

<!-- nav:start -->
> [Theory](README.md) · [Exercises](exercises.md) · **Project** · [Solutions](solutions/README.md)  
> [← 15 Security and Row-Level Security](../15-security-rls/README.md) · [Course home](../../README.md) · [17 Configuration, Maintenance and Monitoring →](../17-operations/README.md)
<!-- nav:end -->

Bazaar's schema doesn't change in this module (modules 16 to 20 work *on* the database, not on its design). Instead, you take a close look at how its data is stored and make one targeted storage change that the internals justify: making the constant stream of `inventory` updates HOT.

**Starting state:** `./project/bin/reset.sh 16`, plus the contrib extensions from the [theory's "Before you start"](README.md#module-16-internals-deep-dive) (`pg_visibility` is needed by the report).

<!-- toc:start -->
**Contents**

- [Part 1: the storage report](#part-1-the-storage-report)
- [Part 2: make inventory updates HOT](#part-2-make-inventory-updates-hot)
- [Acceptance checks](#acceptance-checks)
- [Reference solution](#reference-solution)

<!-- toc:end -->

## Part 1: the storage report

Write a read-only SQL script, `storage_report.sql`, that a teammate can run at any time with `psql -f`. It must consist only of queries over catalogs and statistics views (no `pgstattuple`, no full table scans), and it has five sections, each introduced with `\echo`:

1. **Where the bytes are.** For the 12 largest tables of schema `bazaar` (partitions count as tables): heap size, TOAST size, FSM + VM size, index size, total size, and the percentage of the whole database.
2. **Row anatomy.** For the 12 largest tables: row count, pages, average row size *including* the 24-byte tuple header and the 4-byte line pointer (use `pg_stats.avg_width`), the number of rows per page you'd expect from that size and the table's fillfactor, and the actual `reltuples / relpages`.
3. **Update health.** For every table with updates or dead tuples: `n_tup_upd`, `n_tup_hot_upd`, the HOT percentage, live and dead tuples, the dead percentage, the table's fillfactor, and when it was last vacuumed.
4. **Freeze health.** The 8 tables with the oldest `relfrozenxid`: XID age, that age as a percentage of `autovacuum_freeze_max_age`, multixact age, and the percentage of pages marked all-frozen in the visibility map.
5. **TOAST usage.** How many tables have a TOAST table, and which of them actually store out-of-line values (with the TOAST table's size).

## Part 2: make inventory updates HOT

Every checkout updates `inventory.reserved` (Module 10's `place_order()`), and a nightly restock updates `quantity` for many products at once. Neither column is indexed, so all these updates *could* be HOT, but the bulk load filled every page to 100%.

1. **Measure the baseline.** Run the provided workload [`solutions/reservation_workload.sql`](solutions/reservation_workload.sql) on the freshly reset database. It resets `inventory`'s statistics counters, runs 20,000 small "checkout" transactions, a one-transaction restock of every 10th product, then the reverse of both (the data ends up unchanged), and reports the HOT percentage, the table size before and after, and the primary key size before and after.
2. **Choose a fillfactor by arithmetic, not by guessing.** Compute the size of one `inventory` row version (tuple header, columns with their alignment, line pointer), how many rows fit on a page at fillfactor 100, and how many bytes of new versions the restock needs per page *within one transaction* (old versions can't be pruned until that transaction ends). Pick the highest fillfactor that leaves room for that burst. Write the calculation down as a comment in your script.
3. **Apply it** so that it affects the existing pages too, and in a way that also restores the rows' physical order (think about where the rows that moved during the baseline run ended up).
4. **Measure again** with the same workload.

## Acceptance checks

```bash
docker exec -i course-pg psql -U postgres -d bazaar < storage_report.sql
```

- The report runs without errors and prints five sections. Section 1 lists `products` first (about 196 MB total). Section 2 shows `products` with about 11 rows per page and `orders` with about 70. Section 5 says that none of Bazaar's tables stores toasted values.

```bash
docker exec -i course-pg psql -U postgres -d bazaar < modules/16-internals/solutions/reservation_workload.sql
```

- **Baseline** (before your change): `hot_pct` is well below 100 (about 46%), and the table grew during the run (from about 10 MB to about 12 MB).
- **After your change:** `hot_pct` is **at least 99%**, and `size_before` equals `size_after`: the workload no longer grows the table.
- `SELECT reloptions FROM pg_class WHERE oid = 'inventory'::regclass;` shows your fillfactor.

## Reference solution

- [`solutions/storage_report.sql`](solutions/storage_report.sql): the report.
- [`solutions/inventory_fillfactor.sql`](solutions/inventory_fillfactor.sql): the arithmetic, `fillfactor = 90`, and the rewrite with `CLUSTER inventory USING inventory_pkey` (a `VACUUM FULL` would apply the fillfactor too, but it keeps the rows in their current physical order, and after the baseline run the restocked rows sit together at the end of the table, where the next restock can't be HOT).
- [`solutions/reservation_workload.sql`](solutions/reservation_workload.sql): the workload used for both measurements.

Measured on PostgreSQL 17 from a fresh `reset.sh 16`:

| fillfactor | HOT updates | size before → after |
|---|---|---|
| 100 (default) | 45.2% (33,659 of 74,432) | 10,192 kB → 12 MB |
| 90 (reference) | 99.5% (74,124 of 74,504) | 11 MB → 11 MB |

Module 17 builds on this: its milestone sets per-table autovacuum parameters for `inventory` (and `jobs`) and keeps `fillfactor = 90`.
