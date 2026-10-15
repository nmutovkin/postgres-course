# Module 19: Project milestone — the Bazaar analytics replica

<!-- nav:start -->
> [Theory](README.md) · [Exercises](exercises.md) · **Project** · [Solutions](solutions/README.md)  
> [← 18 Backup and Recovery](../18-backup-recovery/README.md) · [Course home](../../README.md) · [20 High Availability →](../20-high-availability/README.md)
<!-- nav:end -->

<!-- toc:start -->
**Contents**

- [Goal](#goal)
- [Requirements](#requirements)
  - [Publisher (`m19-primary`)](#publisher-m19-primary)
  - [Subscriber (`m19-analytics`)](#subscriber-m19-analytics)
  - [Monitoring](#monitoring)
- [Reference solution](#reference-solution)
- [Acceptance checks](#acceptance-checks)

<!-- toc:end -->

## Goal

The finance and seller-success teams run heavy reports on orders: revenue per day, per seller, per category, three years back. Running them on the primary slows down checkout; running them on a physical standby gets them cancelled by replication conflicts (or, with `hot_standby_feedback`, bloats the primary). They also want their own indexes and views, which a read-only physical replica cannot have.

Give them a separate PostgreSQL server, `m19-analytics`, that receives **only what analytics needs**, continuously, through logical replication, plus a monitoring query that tells the on-call engineer at a glance whether any replica (physical or logical) is falling behind or endangering the primary's disk.

This milestone does not change the Bazaar schema except for one table-level setting (`REPLICA IDENTITY`), explained below. It runs on the module's lab (`modules/19-replication/lab`).

## Requirements

### Publisher (`m19-primary`)

1. A dedicated role for the subscription, with the `REPLICATION` attribute and `SELECT` on exactly the published tables, and nothing else.
2. A publication `bazaar_analytics` containing:
   - `products` **without** the large free-text and JSON columns (`description`, `attributes`, `tags`): a column list;
   - `orders`, but **only orders that have been paid** (`paid_at IS NOT NULL`): a row filter. Unpaid (pending, or cancelled before payment) orders are of no interest to finance;
   - `order_items`, all rows.
3. Whatever is needed so that `UPDATE` and `DELETE` on `orders` keep working on the primary with that row filter (section 10.3), with a comment explaining the cost.

### Subscriber (`m19-analytics`)

4. A database `analytics` with schema `bazaar` and the three tables, compatible with the publication: same table and column names; the publisher's enum columns may be `text`; **no foreign keys** (only three of Bazaar's tables are replicated).
5. At least two indexes that serve typical analytics queries (by payment date; by seller and payment date) that do **not** exist on the publisher.
6. A view `daily_revenue (day, paid_orders, revenue)` that excludes cancelled and refunded orders.
7. A subscriber-only column `orders.replicated_at` that records when a row first arrived.
8. A subscription `bazaar_analytics` that performs the initial copy and then streams changes.

### Monitoring

9. A query for the primary that returns **one row per replication consumer** (physical standbys, logical subscriptions, and any other slot), with: name, kind (physical/logical), connection state (including a disconnected slot), sync state, lag in bytes, time-based replay lag, WAL retained on the primary because of the consumer, the slot's `wal_status`, and an alert level `OK`/`WARN`/`CRIT` based on thresholds defined at the top of the query.

## Reference solution

| File | Requirements |
|---|---|
| [`lab/scripts/setup-analytics.sql`](lab/scripts/setup-analytics.sql) | 1–3 (run on `m19-primary`, database `bazaar`) |
| [`lab/scripts/analytics-schema.sql`](lab/scripts/analytics-schema.sql) | 4–8 (run on `m19-analytics`) |
| [`lab/scripts/lag.sql`](lab/scripts/lag.sql) | 9 (run on `m19-primary`) |

```bash
cd modules/19-replication/lab
docker exec -i m19-primary   psql -U postgres -d bazaar -v ON_ERROR_STOP=1 < scripts/setup-analytics.sql
docker exec -i m19-analytics psql -U postgres -v ON_ERROR_STOP=1 < scripts/analytics-schema.sql
docker exec -i m19-primary   psql -U postgres -d bazaar < scripts/lag.sql
```

Section 10.6 of the module walks through the reference solution.

## Acceptance checks

Start from a fresh lab (`docker compose --profile extras down -v && docker compose up -d`, then `COURSE_PG_CONTAINER=m19-primary ../../../project/bin/reset.sh 11`), apply your milestone, wait a few seconds, then run:

1. **Initial copy is complete and filtered.**

   ```bash
   docker exec m19-analytics psql -U postgres -d analytics -c "
     SELECT (SELECT count(*) FROM bazaar.orders)      AS orders,
            (SELECT count(*) FROM bazaar.order_items) AS order_items,
            (SELECT count(*) FROM bazaar.products)    AS products"
   docker exec m19-analytics psql -U postgres -d analytics -c "SELECT srrelid::regclass, srsubstate FROM pg_subscription_rel"
   ```

   Expected at state 11: `105 | 235 | 62`, and all three tables in state `r`. (105 = the primary's `SELECT count(*) FROM orders WHERE paid_at IS NOT NULL`.)

2. **Column list.** On the analytics server, `bazaar.products` has no `description` column, and on the primary, `SELECT attnames FROM pg_publication_tables WHERE pubname = 'bazaar_analytics' AND tablename = 'products'` does not list `description`, `attributes` or `tags`.

3. **The row filter transforms updates.** On the primary, pay one of the pending orders (115–119):

   ```sql
   UPDATE orders SET status = 'paid', paid_at = now() WHERE id = 115;
   ```

   It appears on the analytics server with a `replicated_at` close to now; then `UPDATE orders SET status = 'shipped', shipped_at = now() WHERE id = 115;` is reflected there and `replicated_at` does not change.

4. **Writes on the primary still work.** `BEGIN; DELETE FROM orders WHERE id = 116; ROLLBACK;` on the primary succeeds (no "not part of the replica identity" error). (`id = 116` is a pending order without payments, so the delete itself is allowed.)

5. **Subscriber-side objects.** On `m19-analytics`: `\d bazaar.orders` shows your two extra indexes and no foreign keys; `SELECT * FROM bazaar.daily_revenue ORDER BY day DESC LIMIT 3` returns rows.

6. **The privileges are minimal.** On the primary:

   ```sql
   SELECT rolreplication, rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = 'analytics_repl';
   SELECT table_name, privilege_type FROM information_schema.role_table_grants
   WHERE grantee = 'analytics_repl' ORDER BY 1;
   ```

   Expected: `t | f | f | f`, and exactly three rows, all `SELECT`.

7. **The lag query works for every kind of consumer.** Run `scripts/lag.sql` (or yours) on the primary: one row each for `bazaar_analytics` (logical), `replica1` (physical, sync) and `replica2` (physical, async), all `OK`. Then stop `m19-replica2` and run it again: `replica2` shows as disconnected with `CRIT`. Start it again: back to `OK` within seconds.

8. **Lag is detected on the logical side too.** On the primary, `ALTER TABLE orders ADD COLUMN gift_note text; UPDATE orders SET gift_note = 'x' WHERE id = 120;`. The subscription stops (the new column is missing on the subscriber), and within seconds the lag query shows `bazaar_analytics` as `DISCONNECTED`/`CRIT`. Add the column on the analytics server; the row returns to `OK` and order 120 has `gift_note = 'x'` there.
