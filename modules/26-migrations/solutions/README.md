# Module 26: Solutions

<!-- nav:start -->
> [Theory](../README.md) · [Exercises](../exercises.md) · [Project](../project.md) · **Solutions**  
> [← 25 SQLAlchemy ORM](../../25-sqlalchemy-orm/README.md) · [Course home](../../../README.md) · [27 Production Python and Postgres →](../../27-python-production/README.md)
<!-- nav:end -->

All solutions ran against PostgreSQL 17.11 with the large dataset (`reset.sh 26`, stamped at 2600 and upgraded to the head, 2801), Alembic 1.20.0, SQLAlchemy 2.1.4, psycopg 3.3.6. Run them from `project/python` with the venv active, for example `python ../../modules/26-migrations/solutions/ex02_check.py`.

The exercise revisions build on 2801, the head of the finished package (2701 was the head at the end of Module 27; Module 28 adds 2801). Most solutions use [`scratch_alembic.py`](scratch_alembic.py): `scratch_migrations(*files)` copies `alembic.ini` and `migrations/` to a temporary directory and adds the exercise's revision files; `scratch_db()` creates an empty database and drops it afterwards. Solutions that need the large dataset run against the reference database and downgrade back to 2801 at the end. Timings depend on the machine and on what's cached; the ratios are what matter.

---

<!-- toc:start -->
**Contents**

- [Exercise 1: Review the locks of a release](#exercise-1-review-the-locks-of-a-release)
- [Exercise 2: A new order status](#exercise-2-a-new-order-status)
- [Exercise 3: Which revision is this database at?](#exercise-3-which-revision-is-this-database-at)
- [Exercise 4: Review this migration](#exercise-4-review-this-migration)
- [Exercise 5: From autogenerate's draft to a revision](#exercise-5-from-autogenerates-draft-to-a-revision)
- [Exercise 6: A denormalized counter, backfilled under load](#exercise-6-a-denormalized-counter-backfilled-under-load)
- [Exercise 7: A unique constraint on dirty data](#exercise-7-a-unique-constraint-on-dirty-data)
- [Exercise 8: Rename a table without breaking running code](#exercise-8-rename-a-table-without-breaking-running-code)
- [Exercise 9: Two heads](#exercise-9-two-heads)
- [Exercise 10: Prove that writers were never blocked](#exercise-10-prove-that-writers-were-never-blocked)
- [Exercise 11: Split `users.full_name`, with zero downtime](#exercise-11-split-usersfull_name-with-zero-downtime)
- [Exercise 12: Squash the history](#exercise-12-squash-the-history)

<!-- toc:end -->

## Exercise 1 ★: Review the locks of a release

`alembic upgrade 2601:2604 --sql` prints the release. [`ex01_lock_modes.py`](ex01_lock_modes.py) runs each table-level statement in its own transaction on an empty database at 2601 and reads `pg_locks` for its own backend before committing:

```text
2602  ALTER TABLE products ADD COLUMN IF NOT EXISTS slug text             products: AccessExclusiveLock
2602  CREATE OR REPLACE FUNCTION products_set_slug() RETURNS trigger LAN
2602  CREATE OR REPLACE TRIGGER products_set_slug BEFORE INSERT ON produ  products: ShareRowExclusiveLock
2603  ALTER TABLE products ADD CONSTRAINT products_slug_not_null CHECK (  products: AccessExclusiveLock
2603  ALTER TABLE products VALIDATE CONSTRAINT products_slug_not_null     products: ShareUpdateExclusiveLock
2603  ALTER TABLE products ALTER COLUMN slug SET NOT NULL                 products: AccessExclusiveLock
2603  ALTER TABLE products DROP CONSTRAINT IF EXISTS products_slug_not_n  products: AccessExclusiveLock
2604  ALTER TABLE orders ADD COLUMN IF NOT EXISTS idempotency_key text    orders: AccessExclusiveLock
2604  ALTER TABLE orders ADD CONSTRAINT orders_idempotency_key_length CH  orders: AccessExclusiveLock
2604  ALTER TABLE orders VALIDATE CONSTRAINT orders_idempotency_key_leng  orders: ShareUpdateExclusiveLock
```

(The script's first version also listed `pg_class: AccessShareLock` on every line: the `pg_locks` query itself reads the catalog. Filter `pg_catalog` out.)

Six statements take `ACCESS EXCLUSIVE`, and none of them scans the table: `ADD COLUMN` without a default and `ADD CONSTRAINT ... NOT VALID` are catalog changes; `SET NOT NULL` skips its scan because a validated `CHECK (slug IS NOT NULL)` exists; `DROP CONSTRAINT` is a catalog change. So all six are held for milliseconds, *once granted*. The real risk is the wait for them (section 8.1), which `lock_timeout` and the retries in `env.py` cover. The trigger takes `SHARE ROW EXCLUSIVE`, which blocks writes to `products` but only for the instant of the `CREATE`. The two `VALIDATE`s and the two concurrent index builds (`SHARE UPDATE EXCLUSIVE`) are the steps that take time, and they don't block reads or writes. The backfill's `UPDATE`s take `ROW EXCLUSIVE` and row locks, 10,000 rows at a time.

**Alternative.** Without running anything, the lock table in the README's section 8.1 gives the same answer. Running the statements is better evidence, especially for less common DDL (does `ALTER TABLE ... SET (fillfactor = 90)` take `ACCESS EXCLUSIVE`? It takes `SHARE UPDATE EXCLUSIVE`; try it).

## Exercise 2 ★: A new order status

[`ex02_2605_order_on_hold.py`](ex02_2605_order_on_hold.py), checked by [`ex02_check.py`](ex02_check.py):

```text
--- naive revision
UnsafeNewEnumValueUsage - unsafe use of new value "on_hold" of enum type order_status
alembic_version: [('2801',)]
labels: {pending,paid,shipped,delivered,cancelled,refunded}
--- fixed revision
alembic_version: [('2605',)]
labels: {pending,paid,on_hold,shipped,delivered,cancelled,refunded}
index: CREATE INDEX orders_on_hold_created_at_idx ON bazaar.orders USING btree (created_at) WHERE (status = 'on_hold'::order_status)
old code reading the row: DataError - bad member for enum OrderStatus: 'on_hold'
downgrade: order_status 'on_hold' cannot be removed; roll forward instead (the index orders_on_hold_created_at_idx was dropped)
```

1. The naive revision runs `ADD VALUE` and the index's `WHERE status = 'on_hold'` in one transaction. The new value isn't committed, so using it is an error, and the whole revision rolls back: still at 2801, still six labels (with `transaction_per_migration`, the revision's transaction is all-or-nothing).
2. The fix commits the value first:

   ```python
   def upgrade() -> None:
       with op.get_context().autocommit_block():
           op.execute("ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'on_hold' AFTER 'paid'")
       create_index_concurrently(
           "orders_on_hold_created_at_idx", "orders", "created_at", where="status = 'on_hold'"
       )
   ```

   `IF NOT EXISTS` matters because the revision is now non-atomic: if the index build fails, the value is already committed and the rerun must not fail on it.
3. `downgrade()` drops the index and raises: PostgreSQL has no `DROP VALUE`, and recreating the type would rewrite `orders` under `ACCESS EXCLUSIVE` (and fail while `seller_sales_daily` uses the column). Note what the failed downgrade leaves: the index is gone (dropped in an autocommit block), the version still says 2605. An honest downgrade should raise *before* changing anything if it can't finish; here, dropping the index first is harmless.
4. The module-22 code maps statuses to `OrderStatus`, which doesn't know `on_hold`: reading the row raises `bad member for enum OrderStatus`. So: deploy code that knows `ON_HOLD` (and handles it in every `match`), then the revision, then code that writes the value.

**Alternative.** Two revisions: 2605 adds the value (its transaction commits at the end of the revision), 2606 builds the index. That's more explicit and works with migration tools that have no autocommit escape.

## Exercise 3 ★: Which revision is this database at?

[`ex03_identify_revision.py`](ex03_identify_revision.py) builds the mystery database, then climbs a "ladder" database one revision at a time, comparing catalog snapshots:

```text
2600: 13 differences
2601: 9 differences
2602: 5 differences
2603: 4 differences
2604: 1 differences
2701: 26 differences
2801: 30 differences
closest revision: 2604 (1 differences)
    only in mystery: index:orders_status_idx
after stamp + upgrade: ('2801',)
```

The distance falls steadily up to 2604 and jumps at 2701 (the outbox tables, their columns, constraints, indexes, trigger and function) and again at 2801 (`search_products()` and its indexes). One difference remains: the hand-made index. Options: drop it (if nobody needs it), or keep it and write a revision `CREATE INDEX CONCURRENTLY IF NOT EXISTS orders_status_idx ...` so that every environment has it and fresh databases match. Either way, `stamp 2604` is now a fact, not a guess, and `upgrade head` brings the database to 2801.

**Alternative.** `pg_dump --schema-only` of both databases and `diff -u` works too, with a `pg_dump` of the server's version and some patience for 4,000-line diffs. Comparing only "does table X / column Y exist" (what most "detect the version" scripts do) can't tell 2602 from 2603 (`NOT NULL` is the only difference).

## Exercise 4 ★★: Review this migration

The problems, measured with `lock_probe.py` on the reference database (each statement on its own; `transactionid` lines and per-step lock lists omitted):

```text
DDL  ALTER TABLE orders ADD COLUMN tracking_token uuid NOT NULL DEFAULT gen_random_uuid()
     ok, took 3360 ms
     traffic during it: 15 round trips, worst 3209 ms
DDL  CREATE INDEX orders_status_idx ON orders (status)
     ok, took 265 ms
     traffic during it: 8 round trips, worst 264 ms
DDL  ALTER TABLE orders ALTER COLUMN shipping_address_id SET NOT NULL
     ok, took 85 ms
     traffic during it: 8 round trips, worst 84 ms
DDL  ALTER TABLE orders ADD CONSTRAINT orders_total_nonnegative CHECK (total >= 0)
     ok, took 69 ms
     traffic during it: 7 round trips, worst 69 ms
```

1. **Volatile default**: `gen_random_uuid()` is evaluated per row, so the table is rewritten under `ACCESS EXCLUSIVE`: 3.4 s of no reads and no writes on 55 MB.
2. **Index not concurrent**: `SHARE` lock, writes blocked for the build.
3. **`SET NOT NULL`**: full scan under `ACCESS EXCLUSIVE`.
4. **`CHECK` validated inline**: full scan under `ACCESS EXCLUSIVE`.
5. **All in one transaction**: Alembic runs the revision as one transaction, so the first `ACCESS EXCLUSIVE` lock is held until the end. Traffic stops for the *sum* (about 3.8 s here, minutes on a real table). Running the migration through exercise 10's probe confirmed it: `longest write-blocking lock hold: 3070 ms`.
6. **The ORM import** (`from bazaar.orm.models import Order`): the revision now depends on today's model; in a year, importing it may fail or do something else. And **`downgrade()` does nothing**, so the stairway test would fail, and a real rollback would leave the columns behind. (Also, the naming convention turns `total_nonnegative` into `orders_total_nonnegative`; a `downgrade()` that drops `"orders_total_nonnegative"` through `op.drop_constraint` would look for `orders_orders_total_nonnegative`. Use `op.f()` or plain SQL.)

[`ex04_fixed_2605.py`](ex04_fixed_2605.py):

```python
def upgrade() -> None:
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS tracking_token uuid")
    op.execute("ALTER TABLE orders ALTER COLUMN tracking_token SET DEFAULT gen_random_uuid()")
    backfill(
        "orders",
        "tracking_token = gen_random_uuid()",
        "tracking_token IS NULL",
        batch_size=2_000,
        skip_triggers=True,
    )
    set_not_null_online("orders", "tracking_token")
    set_not_null_online("orders", "shipping_address_id")
    create_index_concurrently("orders_status_idx", "orders", "status")
    add_check_constraint_online("orders", "orders_total_nonnegative", "total >= 0")
```

The same probe, on the online steps:

```text
DDL  ALTER TABLE orders ADD COLUMN tracking_token uuid
     ok, took 2 ms
     traffic during it: 8 round trips, worst 2 ms
DDL  ALTER TABLE orders ALTER COLUMN tracking_token SET DEFAULT gen_random_uuid()
     ok, took 2 ms
     traffic during it: 7 round trips, worst 3 ms
DDL  CREATE INDEX CONCURRENTLY orders_status_idx ON orders (status)
     ok, took 336 ms
     traffic during it: 54 round trips, worst 7 ms
DDL  ALTER TABLE orders ADD CONSTRAINT orders_shipping_address_id_not_null CHECK (shipping_address_id IS NOT NULL) NOT VALID
     ok, took 1 ms
     traffic during it: 8 round trips, worst 2 ms
DDL  ALTER TABLE orders VALIDATE CONSTRAINT orders_shipping_address_id_not_null
     ok, took 60 ms
     traffic during it: 17 round trips, worst 2 ms
DDL  ALTER TABLE orders ALTER COLUMN shipping_address_id SET NOT NULL
     ok, took 1 ms
     traffic during it: 7 round trips, worst 3 ms
DDL  ALTER TABLE orders ADD CONSTRAINT orders_total_nonnegative CHECK (total >= 0) NOT VALID
     ok, took 4 ms
     traffic during it: 8 round trips, worst 5 ms
DDL  ALTER TABLE orders VALIDATE CONSTRAINT orders_total_nonnegative
     ok, took 81 ms
     traffic during it: 18 round trips, worst 5 ms
```

**Batch size.** The probe's traffic updates random orders, so it sometimes hits a row that a backfill batch has locked:

```text
DDL  UPDATE orders SET tracking_token = gen_random_uuid() WHERE id >= 1 AND id < 20001 AND tracking_token IS NULL
     ok, took 2285 ms
     traffic during it: 73 round trips, worst 1814 ms
DDL  SET session_replication_role = replica
DDL  UPDATE orders SET tracking_token = gen_random_uuid() WHERE id >= 20001 AND id < 40001 AND tracking_token IS NULL
     ok, took 853 ms
     traffic during it: 72 round trips, worst 364 ms
DDL  UPDATE orders SET tracking_token = gen_random_uuid() WHERE id >= 40001 AND id < 42001 AND tracking_token IS NULL
     ok, took 145 ms
     traffic during it: 13 round trips, worst 89 ms
```

A batch holds its rows' locks until it commits, so the batch's duration is the worst-case wait for a writer of one of those rows. 20,000 rows with triggers firing took 2.3 s (and wrote 20,000 `audit_log` rows, which had to be deleted afterwards); without triggers, 0.85 s; 2,000 rows without triggers, 0.15 s. Pick the batch size from the *time* you're willing to make a writer wait (here ~100–150 ms), not from a row count. The whole revision ran in 12.3 s on 500,000 orders under the probe of exercise 10: `longest write-blocking lock hold (sampled every ~2 ms): 3 ms`, traffic p99 6.9 ms, worst 70 ms (a batch's row lock).

[`ex04_check.py`](ex04_check.py) on an empty database:

```text
2605 adds: ['column:orders.tracking_token', 'constraint:orders.orders_total_nonnegative', 'index:orders_status_idx']
downgrade restores 2801: True
re-upgrade is identical: True
statements outside a transaction: 1 concurrent, 3 NOT VALID, 3 VALIDATE
```

## Exercise 5 ★★: From autogenerate's draft to a revision

[`ex05_draft.py`](ex05_draft.py) runs autogenerate's comparison against a fresh database at head with the patched metadata ([`ex05_metadata.py`](ex05_metadata.py)) and renders the draft:

```text
# ### commands auto generated by Alembic - please adjust! ###
    op.add_column('reviews', sa.Column('helpful_votes', sa.Integer(), server_default='0', nullable=False))
    op.create_index('reviews_product_id_helpful_votes_idx', 'reviews', ['product_id', sa.literal_column('helpful_votes DESC')], unique=False)
    op.create_check_constraint(op.f('reviews_helpful_votes_nonnegative'), 'reviews', 'helpful_votes >= 0')
    # ### end Alembic commands ###
```

- `add_column` with a constant default and `NOT NULL`: fine as it is. Since PostgreSQL 11 it's a catalog change (the default is stored once, `attmissingval`).
- `create_index`: a plain build takes `SHARE` on 292,355 reviews, blocking new reviews. Made concurrent.
- `create_check_constraint`: validated inline under `ACCESS EXCLUSIVE`. Every row has `helpful_votes = 0`, so it can't fail, but it still scans. Split into `NOT VALID` + `VALIDATE`. (Also note `op.f()`: autogenerate marks the name as final, so the naming convention isn't applied twice.)

[`ex05_2605_review_votes.py`](ex05_2605_review_votes.py) and [`ex05_check.py`](ex05_check.py):

```text
at 2801, autogenerate would emit: ['add_column', 'add_index', 'add_constraint']
at 2605: []
after down/up: []
```

**Alternative.** Keep the inline `CHECK` but on a column that was just added with a constant default, PostgreSQL could in principle prove it without a scan. It doesn't: `ADD CONSTRAINT ... CHECK` always scans unless `NOT VALID`. Only `SET NOT NULL` has the "validated CHECK" shortcut.

## Exercise 6 ★★: A denormalized counter, backfilled under load

[`ex06_2605_review_count.py`](ex06_2605_review_count.py) and [`ex06_measure.py`](ex06_measure.py) (a thread inserts reviews of random products by random users during the migration; afterwards they're deleted, and the trigger decrements):

```text
backfill: 200 batches of 1000 in 14.4s
  batch   1000: 2531 concurrent inserts (2531 new reviews), p99 16 ms, worst 1002 ms; sum(review_count) = 294886, count(reviews) = 294886
backfill: 20 batches of 10000 in 16.3s
  batch  10000: 319 concurrent inserts (319 new reviews), p99 1096 ms, worst 1494 ms; sum(review_count) = 292674, count(reviews) = 292674
backfill: 4 batches of 50000 in 16.3s
  batch  50000: 63 concurrent inserts (63 new reviews), p99 6192 ms, worst 6192 ms; sum(review_count) = 292418, count(reviews) = 292418
```

**Correctness.** The counts are exact in all three runs. The backfill locks a batch's products *before* counting:

```sql
SELECT 1 FROM products WHERE id >= :lo AND id < :hi FOR UPDATE;
UPDATE products p SET review_count = c.n
FROM (SELECT product_id, count(*) AS n FROM reviews
      WHERE product_id >= :lo AND product_id < :hi GROUP BY product_id) AS c
WHERE p.id = c.product_id AND p.review_count IS DISTINCT FROM c.n;
```

Without the `FOR UPDATE`, a review committed after the `UPDATE` took its snapshot but before it reached that product's row would be counted by the trigger (`+1` on the old value), and then the batch would overwrite the row with its stale absolute count: a lost increment. With the lock taken first, new reviews of those products block in their trigger until the batch commits, and the `UPDATE` (a new statement, so a new snapshot in `READ COMMITTED`) sees every review committed before the lock.

**Throughput versus waiting.** The batch size barely changes the total time (14–16 s), but it sets how long a new review can wait: the batch's row locks last as long as the batch. p99 16 ms at 1,000 products per batch, about 1.1 s at 10,000, 6.2 s at 50,000 (and the thread managed only 63 inserts in that run, because it spent most of its time waiting).

The single 1-second outlier in the 1,000 run happened in the first 0.2 s of the migration, before any batch, and the server log explained it:

```text
ERROR:  canceling autovacuum task
CONTEXT:  while scanning block 67983 of relation "bazaar.products"
```

Autovacuum was working on `products` (after earlier exercises' updates) and holds `SHARE UPDATE EXCLUSIVE`. The `ADD COLUMN` queued for `ACCESS EXCLUSIVE`, inserts (whose foreign key check needs a lock on `products`) queued behind it, and PostgreSQL cancels an autovacuum that blocks someone only after `deadlock_timeout` (1 s). An anti-wraparound autovacuum is *not* cancelled, so the wait could be much longer: one more reason for `lock_timeout`.

**The deadlock.** The first version had `ADD COLUMN` (on `products`) and `CREATE TRIGGER` (on `reviews`) in one transaction. The concurrent inserts deadlocked with it:

```text
psycopg.errors.DeadlockDetected: deadlock detected
DETAIL:  Process 4304 waits for RowShareLock on relation 16435 of database 16384; blocked by process 4305.
Process 4305 waits for ShareRowExclusiveLock on relation 16556 of database 16384; blocked by process 4304.
```

The migration held `ACCESS EXCLUSIVE` on `products` and wanted `SHARE ROW EXCLUSIVE` on `reviews`; an insert held `ROW EXCLUSIVE` on `reviews` and wanted `ROW SHARE` on `products` for its foreign key check. DDL that locks two tables must either commit in between (the fix: `ADD COLUMN` in its own autocommit block) or take its locks in the same order as the application does.

**Design note.** Every review now updates its product's row, which recomputes `search_vector`, usually updates every index of `products` (Module 16's HOT conditions rarely hold for this wide row), bumps `updated_at` through `set_updated_at()`, and writes an `audit_log` row through `audit_trigger()`: after these runs, thousands of products had a fresh `updated_at` although nobody edited them. A separate `product_stats (product_id PRIMARY KEY, review_count)` table would keep that write amplification off the hot table; consider it before denormalizing into a big row.

## Exercise 7 ★★: A unique constraint on dirty data

[`ex07_2605_payment_ref_unique.py`](ex07_2605_payment_ref_unique.py) and [`ex07_check.py`](ex07_check.py) (which inserts the duplicate, runs the revision with and without its cleanup step, and restores the database):

```text
duplicate inserted: (536817, 'paypal', '48475942286b042f')
without cleanup: could not create unique index "payments_provider_provider_ref_key"
                DETAIL:  Key (provider, provider_ref)=(paypal, 48475942286b042f) is duplicated.
left behind: [('payments_provider_provider_ref_key', False)]
version: [('2801',)]
dropping INVALID index payments_provider_provider_ref_key left by an earlier attempt
after the fixed run: [('payments_provider_provider_ref_key', True)]
constraint: [('payments_provider_provider_ref_key', 'u')]
quarantined: [(536817, 'paypal', '48475942286b042f')]
```

1. The concurrent build failed on the duplicate. The revision rolled back (`alembic_version` still 2801), but the index didn't: a concurrent build commits its catalog entries in separate transactions, so the failure leaves an index marked invalid. It's ignored by queries but **maintained on every insert and update** of payments. Any rerun with `CREATE INDEX CONCURRENTLY IF NOT EXISTS` would skip it, and the `ADD CONSTRAINT ... USING INDEX` would then fail on an invalid index.
2. The fixed revision moves duplicates into `payments_quarantine` (keeping the oldest payment of each pair) in one statement:

   ```sql
   WITH dup AS (
       DELETE FROM payments p
       USING payments keep
       WHERE keep.provider = p.provider
         AND keep.provider_ref = p.provider_ref
         AND keep.id < p.id
       RETURNING p.*
   )
   INSERT INTO payments_quarantine
   SELECT dup.*, now() FROM dup
   ```

   then `create_index_concurrently()` noticed the invalid leftover and dropped it before building, and `ALTER TABLE payments ADD CONSTRAINT payments_provider_provider_ref_key UNIQUE USING INDEX payments_provider_provider_ref_key` turned the index into a constraint (a brief `ACCESS EXCLUSIVE`, no scan).

**Caveat.** Between the cleanup and the end of the index build, a new duplicate could still arrive (the cleanup and the build are different transactions). The build would then fail again, and the rerun would clean up again. For a table with frequent duplicates, first deploy code that stops creating them (an upsert on a non-unique column isn't possible, but a check before insert narrows the window), then migrate.

## Exercise 8 ★★: Rename a table without breaking running code

[`ex08_2605_rename_cart_expand.py`](ex08_2605_rename_cart_expand.py), [`ex08_2606_rename_cart_contract.py`](ex08_2606_rename_cart_contract.py), checked by [`ex08_check.py`](ex08_check.py) as `bazaar_app`:

```text
at 2605 (expand):
  old code (cart_items)  : ok, quantity now 1
  new code (basket_items): ok, quantity now 2
at 2606 (contract):
  old code (cart_items)  : UndefinedTable: relation "cart_items" does not exist
  new code (basket_items): ok, quantity now 3
after downgrade: [('cart_items',), ('cart_items_pkey',), ('cart_items_product_id_idx',)]
```

The expand revision renames the table, its four constraints and its index (all catalog changes, each taking `ACCESS EXCLUSIVE` briefly, together in one short transaction under `lock_timeout`), then creates `CREATE VIEW cart_items AS SELECT * FROM basket_items`. The view is simple (one table, no aggregates), so PostgreSQL makes it automatically updatable, and `INSERT ... ON CONFLICT` works through it. No `GRANT` was needed: the view was created by `bazaar_owner` (`env.py`'s `SET ROLE`), and Module 15's default privileges for tables also apply to views, so `bazaar_app` got its DML rights automatically. The view also runs with its owner's rights on `basket_items`.

`SELECT *` in a view is expanded when the view is created: a column added to `basket_items` later won't appear in `cart_items`. That's fine for a short-lived compatibility view. Before the contract revision, check that nothing uses the old name any more, for example in `pg_stat_statements` (`WHERE query ILIKE '%cart_items%'`).

**Alternative.** Keep the table name and change only the code's name for it (the ORM's `__tablename__` stays `cart_items`). Renames are often not worth a migration at all.

## Exercise 9 ★★: Two heads

[`ex09_heads.py`](ex09_heads.py):

```text
after merging both branches in git:
  CI: FAIL, 2 heads ['2802', '2803']: rebase or merge before merging
  alembic upgrade head: Multiple head revisions are present for given argument 'head ...
after rebasing 2803 onto 2802:
  CI: ok, one head 2803
    2802 -> 2803  add gift wrap
    2801 -> 2802  add wishlist
    2701 -> 2801  storefront: search_products() from module 13, its trigram index, a browse index
with a merge revision instead:
  CI: ok, one head 2804
    ('2802', '2803') -> 2804  merge wishlist and gift wrap
    2801 -> 2802  add wishlist
    2801 -> 2803  add gift wrap
    2701 -> 2801  storefront: search_products() from module 13, its trigram index, a browse index
```

The check is three lines (`ScriptDirectory.from_config(cfg).get_heads()`, fail if not exactly one); the project's version is `test_one_head_and_a_straight_history`. **Rebase** (change `down_revision` of the later revision, renumber if ids collide) gives a straight history, and it's the right fix while the revision hasn't run anywhere shared. A **merge revision** is right when both revisions have already run somewhere (a hotfix applied to production while `main` moved on): their files can't change any more. A merge says nothing about the order of its parents: Alembic may run 2802 or 2803 first. If one needs the other, declare it with `depends_on`.

The script first produced a nonsensical merge history, because it rewrote `2803_*.py` twice within one second and Python loaded the stale `.pyc` from `__pycache__` (same size, same mtime). `sys.dont_write_bytecode = True` fixed it; when you edit revision files by script, remember the bytecode cache.

## Exercise 10 ★★★: Prove that writers were never blocked

[`ex10_migration_probe.py`](ex10_migration_probe.py), with [`ex10_naive_2605.py`](ex10_naive_2605.py) and [`ex10_online_2605.py`](ex10_online_2605.py), two runs each:

```text
revision ex10_naive_2605.py: upgrade took 0.2s
  lock modes the migration held on orders: ['AccessExclusiveLock']
  longest write-blocking lock hold (sampled every ~2 ms): 80 ms
  traffic: 126 round trips, median 1.6 ms, p99 21.5 ms, worst 82 ms
  FAIL: writers blocked at most 50 ms
revision ex10_online_2605.py: upgrade took 0.2s
  lock modes the migration held on orders: ['ShareUpdateExclusiveLock']
  longest write-blocking lock hold (sampled every ~2 ms): 0 ms
  traffic: 145 round trips, median 1.6 ms, p99 11.7 ms, worst 16 ms
  PASS: writers blocked at most 50 ms
revision ex10_naive_2605.py: upgrade took 0.2s
  lock modes the migration held on orders: ['AccessExclusiveLock']
  longest write-blocking lock hold (sampled every ~2 ms): 78 ms
  traffic: 122 round trips, median 1.6 ms, p99 19.5 ms, worst 79 ms
  FAIL: writers blocked at most 50 ms
revision ex10_online_2605.py: upgrade took 0.3s
  lock modes the migration held on orders: ['ShareUpdateExclusiveLock']
  longest write-blocking lock hold (sampled every ~2 ms): 0 ms
  traffic: 145 round trips, median 1.7 ms, p99 16.4 ms, worst 23 ms
  PASS: writers blocked at most 50 ms
```

The two signals agree: the naive revision held `ACCESS EXCLUSIVE` for about 80 ms (the validation scan), and the worst traffic round trip was about the same. The online version's `ADD CONSTRAINT ... NOT VALID` held `ACCESS EXCLUSIVE` for less than the sampling interval (invisible here), and the validation ran under `SHARE UPDATE EXCLUSIVE`. The script identifies the migration's backend through `application_name = 'bazaar-migrations'`, which `env.py` sets: naming your connections pays off.

What the sampler can't see: locks held for less than its interval, and waits *for* the migration's lock request (the lock queue of section 8.1: the migration hasn't got the lock yet, but it already blocks others). The traffic latencies cover both, so report them together.

Two bugs met on the way, both worth knowing:

- The naive `downgrade()` first used `op.drop_constraint("orders_total_nonnegative", ...)`, and Alembic applied the naming convention to it: `constraint "orders_orders_total_nonnegative" of relation "orders" does not exist`. Names passed to `op.*` go through the convention unless wrapped in `op.f()`.
- After that failed downgrade, the database was still at 2605 with the naive constraint, and the next run of the *online* revision "succeeded" in 0.1 s: `add_check_constraint_online()` found a validated constraint of that name and skipped everything. Idempotent helpers make reruns safe, and they also make a half-cleaned-up environment look fine. Check `alembic current` before measuring.

## Exercise 11 ★★★: Split `users.full_name`, with zero downtime

[`ex11_2605_split_name_expand.py`](ex11_2605_split_name_expand.py), [`ex11_2606_split_name_contract.py`](ex11_2606_split_name_contract.py), [`ex11_check.py`](ex11_check.py):

```text
  after expand + backfill:
    (1, "Liam O'Connor", 'Liam', "O'Connor")
    (2, '  Zoé   Martin ', 'Zoé', 'Martin')
    (3, 'Cher', 'Cher', '')
  old and new code writing side by side:
    (1, "Liam O'Brien", 'Liam', "O'Brien")
    (2, 'Zoé Martin-Weiss', 'Zoé', 'Martin-Weiss')
    (3, 'Cher', 'Cher', '')
    (4, 'Ana María de la Cruz', 'Ana María de la', 'Cruz')
    (5, 'Ana María de la Cruz', 'Ana María', 'de la Cruz')
  after contract, old code: UndefinedColumn column "full_name" of relation "users" does not exist
  after downgrading the contract (full_name rebuilt from the parts):
    (1, "Liam O'Brien", 'Liam', "O'Brien")
    (2, 'Zoé Martin-Weiss', 'Zoé', 'Martin-Weiss')
    (3, 'Cher', 'Cher', '')
    (4, 'Ana María de la Cruz', 'Ana María de la', 'Cruz')
    (5, 'Ana María de la Cruz', 'Ana María', 'de la Cruz')
reference database: 2605 in 3.2s, users without first_name: 0
    Hugo Nelson | Hugo | Nelson
    Kevin Clark | Kevin | Clark
    Giulia Rossi | Giulia | Rossi
```

The trigger decides who wrote by comparing `NEW` with `OLD`: a changed `full_name` means old code (re-split), changed parts mean new code (rebuild `full_name`). On `INSERT`, a missing `first_name` means old code, a missing `full_name` new code. New code can insert without `full_name` although the column is still `NOT NULL`, because a `BEFORE` trigger runs before the constraint is checked. Rows 4 and 5 show why the new form asks for both parts: the automatic split of "Ana María de la Cruz" is wrong, and the person's own split is kept.

The backfill runs with triggers off: with the trigger on, setting the parts would look like "new code wrote the parts" and rewrite `full_name` from them, normalizing user 2's spaces (harmless here, but a backfill must not change data it doesn't own). It took 3.2 s for 100,000 users.

The contract's `downgrade()` is honest (the full name can be rebuilt from the parts), but not perfect: the re-added `full_name` is the *last* column of `users` now, and the rebuilt value is normalized. The stairway test's catalog comparison would flag the column position. Decide whether that matters; for a downgrade that exists for development, it usually doesn't.

## Exercise 12 ★★★: Squash the history

[`ex12_squash.py`](ex12_squash.py) (it needs `COURSE_PG_CONTAINER` for `docker exec ... pg_dump`):

```text
squashed schema: 4240 lines
2701 -> 2801 (bazaar) (head), storefront: search_products() from module 13, its trigram index, a browse index
2700 -> 2701 (bazaar), transactional outbox: outbox + processed_messages (module 27)
<base> -> 2700 (bazaar), squashed baseline: everything up to revision 2604
fresh via squashed chain == fresh via original chain: True
database at head, squashed scripts: current = ('2801',)
database at 2603, squashed scripts: Can't locate revision identified by '2603'
```

1. The snapshot is taken from a database built by migrations, so it contains `bazaar.alembic_version`. A baseline that creates that table would collide with the one Alembic creates before running the baseline: dump with `--exclude-table=bazaar.alembic_version`. (`make_baseline.sh` dumped a database that had never seen Alembic.) The new snapshot includes everything 2601–2604 added: the notify triggers, `slugify()`, `products.slug` with its trigger and index, `orders.idempotency_key`.
2. `2700_squashed_baseline.py` is `2600_baseline.py` with a new id and the new file. The catalog comparison of the two fresh databases is empty.
3. A database at the head (2801) needs nothing: its revision exists in the new graph, and `alembic_version` only records the current revision. A database at 2603 is stranded: its revision is gone. Before squashing, every environment must be at or past the squash point (2604). If one isn't, upgrade it with the old scripts first, or stamp it after verifying its schema (exercise 3).
