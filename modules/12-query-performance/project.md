# Module 12: Project milestone — a performance review of Bazaar

<!-- nav:start -->
> [Theory](README.md) · [Exercises](exercises.md) · **Project** · [Solutions](solutions/README.md)  
> [← 11 Indexes](../11-indexes/README.md) · [Course home](../../README.md) · [13 Full-Text and Fuzzy Search →](../13-full-text-search/README.md)
<!-- nav:end -->

<!-- toc:start -->
**Contents**

- [The goal](#the-goal)
- [Starting state](#starting-state)
- [Requirements](#requirements)
  - [1. The migration](#1-the-migration)
  - [2. The six slow queries](#2-the-six-slow-queries)
  - [3. Targets](#3-targets)
  - [4. The write-up](#4-the-write-up)
- [Acceptance checks](#acceptance-checks)
- [Hints](#hints)
- [Reference solution](#reference-solution)

<!-- toc:end -->

## The goal

Bazaar's back office has filed six tickets about slow screens and reports. You are the database person on call. For each query you will **measure** it, **explain** from its plan why it is slow, **fix** it (with a rewrite, an index, statistics, or a setting), and **document** the before-and-after numbers, so the team can review your changes and learn from them.

Along the way, you install the two objects of this module's reference migration: `pg_stat_statements` and the extended statistics on `addresses`.

## Starting state

```bash
./project/bin/reset.sh 12          # large dataset + Module 11 indexes
```

or your own database after Module 11's milestone.

## Requirements

### 1. The migration

Write `workspace/12/1201_schema_stats.sql` that:

- creates the `pg_stat_statements` extension (in schema `public`);
- creates the extended statistics object `addresses_country_city_stats` on `addresses (country_code, city)` with the kinds that fix both the Berlin row estimate and the `GROUP BY country_code, city` estimate;
- runs `ANALYZE` on the table so the statistics exist immediately.

### 2. The six slow queries

They are in [`solutions/slow_queries.sql`](solutions/slow_queries.sql), exactly as the application sends them:

| # | Ticket | Query (abridged) |
|---|---|---|
| S1 | "Page 3001 of my orders takes forever" (seller 1) | `... WHERE seller_id = 1 ORDER BY created_at DESC, id DESC LIMIT 20 OFFSET 60000` |
| S2 | "The fraud report times out" | orders of the last week of 2025 worth more than 3× their seller's average, via a correlated sub-query |
| S3 | "The Berlin report is slow, and someone says its estimates are wrong" | shops that Berlin customers bought from in 2025 (addresses → orders → sellers) |
| S4 | "The CRM count never finishes" | `SELECT count(*) FROM users WHERE id NOT IN (SELECT user_id FROM orders)` |
| S5 | "Finance's daily revenue for the last week is slow" | `WHERE created_at::date >= date '2025-12-25'` grouped by day |
| S6 | "The all-time best sellers take over a second" | `GROUP BY product_id ORDER BY units DESC LIMIT 10` over all order lines |

**Never run S4 without a `statement_timeout`.** Its first version runs for many minutes.

For each query:

1. **Measure** it with `EXPLAIN (ANALYZE, BUFFERS)` (or the provided [`solutions/measure_slow.sql`](solutions/measure_slow.sql), which prints one line per query and stops S2 and S4 after 30 seconds). Run it twice and use the second (warm) run.
2. **Explain** the problem in two or three sentences, pointing at the plan node(s) responsible and at the estimated-versus-actual rows where relevant.
3. **Fix** it. Allowed tools: rewriting the SQL (results must not change), adding at most **one** new index in total for the whole milestone, statistics objects, `ANALYZE`, and settings, either per transaction (`SET LOCAL`) or as a documented server-level recommendation. Not allowed: `enable_*` settings, `pg_hint_plan`.
4. **Measure** again, and verify that the result is identical (`EXCEPT` both ways returns nothing, or compare counts and sums).

### 3. Targets

After your fixes, on a warm cache, with `max_parallel_workers_per_gather = 0`:

| # | Target |
|---|---|
| S1 | under 1 ms, under 50 buffers |
| S2 | under 1 s |
| S3 | the Berlin filter on `addresses` estimated within 10% of the actual 1,827 rows, **and** the query no slower than before your statistics change |
| S4 | under 1 s |
| S5 | under 20 ms and under 1,000 buffers |
| S6 | under 800 ms and under 20,000 buffers |

### 4. The write-up

In `workspace/12/REVIEW.md`, one section per query with: the ticket, the plan excerpt that shows the problem, the root cause in one sentence, the fix (SQL or command), and a before/after line with time, buffers and (where relevant) estimated versus actual rows. Add a short "recommendations" section for anything you would change at the server level and why.

## Acceptance checks

1. **Migration**:

   ```sql
   SELECT extname FROM pg_extension WHERE extname = 'pg_stat_statements';      -- 1 row
   SELECT statistics_name, kinds FROM pg_stats_ext
   WHERE statistics_name = 'addresses_country_city_stats';                     -- kinds {d,f,m}
   EXPLAIN SELECT * FROM addresses WHERE country_code = 'DE' AND city = 'Berlin';
   -- rows= between 1,645 and 2,010 (within 10% of 1,827)
   EXPLAIN SELECT country_code, city, count(*) FROM addresses GROUP BY country_code, city;
   -- rows=92 on the HashAggregate
   ```

2. **Results unchanged.** Your fixed queries return: S1 orders 59147, 59142, 59135, ... (20 rows, the same as the `OFFSET` version); S2 418 rows with `sum(total)` = 1,273,208.23; S3 10 rows; S4 **15,942**; S5 7 rows, 2025-12-25 to 2025-12-31, with 704 orders and 481,028.63 revenue on the 25th; S6 10 rows, from product 2 (1,005 units) down to product 16 (376 units).

3. **Targets met.** Put your fixed queries in a copy of [`solutions/measure_fixed.sql`](solutions/measure_fixed.sql) (it reads the helper `solutions/plan_check.sql`) and run it:

   ```bash
   ./project/bin/psql.sh bazaar < workspace/12/measure_fixed.sql
   ```

   On the course machine, the reference solution printed (second run):

   ```text
    S1 keyset page     |   0.2 |      15 |    0 | Limit     |   20 |   20
    S2 join seller avg | 189.1 |    7670 |    0 | Sort      | 1892 |  418
    S3 berlin shops    |  34.0 |   14405 |    0 | Limit     |   10 |   10
    S4 never ordered   | 151.1 |    2769 |    0 | Aggregate |    1 |    1
    S5 daily revenue   |   9.0 |     569 |    0 | Aggregate | 4705 |    7
    S6 best sellers    | 575.9 |    9316 |    0 | Limit     |   10 |   10
   ```

   (columns: label, ms, buffers, temp blocks written, root node, estimated rows, actual rows), against this baseline from `measure_slow.sql`:

   ```text
    S1 offset page     |   97.0 |    7373 |  429 | Limit     |     20 |  20
    S2 ...             ERROR:  canceling statement due to statement timeout
    S3 berlin shops    |   41.0 |   24595 |    0 | Limit     |     10 |  10
    S4 ...             ERROR:  canceling statement due to statement timeout
    S5 daily revenue   |   65.1 |    7098 |    0 | Aggregate | 166396 |   7
    S6 best sellers    | 1144.7 | 1267199 |    0 | Limit     |     10 |  10
   ```

4. **The S3 story.** Your write-up explains what happened to S3's plan and time when you added the statistics, and what else you had to change.

## Hints

- S1: section 12.2. You need the last row of page 3000; get it once with the slow query (`LIMIT 1 OFFSET 59999`).
- S2: section 12.9. How many distinct averages does the query need?
- S3: section 9.1 and section 7.3. Measure after *each* change. If the query gets slower after fixing the estimate, read the costs of the two plans.
- S4: section 12.7.
- S5: section 12.4 and Module 11, section 14. Keep the meaning "UTC calendar days".
- S6: section 6.1. Which strategy does the planner pick, and why? What does it need to pick the other one?

## Reference solution

- [`project/db/1201_schema_stats.sql`](../../project/db/1201_schema_stats.sql): the migration (extension + extended statistics).
- [`solutions/fixed_queries.sql`](solutions/fixed_queries.sql): the six fixes, with the BRIN index for S5 and the `random_page_cost` recommendation for S3.
- [`solutions/README.md`](solutions/README.md#milestone-the-performance-review): the reference review with before/after numbers and the S3 analysis.

The BRIN index and the `random_page_cost` change are not part of `project/db/`, so that later modules print the same plans for everyone. After this milestone, `./project/bin/reset.sh 13` gives you the reference state for Module 13.
