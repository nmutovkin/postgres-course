# Module 26 exercises: Migrations with Alembic

**Setup.** `./project/bin/reset.sh 26`, then in `project/python`: `alembic stamp 2600 && alembic upgrade head` (the module's milestone). The database must be back at `2701` after every exercise: the test suite refuses to run otherwise.

Exercises that add a revision number it `2605` (and `2606`) on top of `2701`. You can put your file into `migrations/versions/` while you work on it and remove it (after `alembic downgrade 2701`) when you're done. The reference solutions use [`solutions/scratch_alembic.py`](solutions/scratch_alembic.py) instead: it copies `alembic.ini` and `migrations/` to a temporary directory, adds the exercise's revision there, and runs Alembic on that copy, against an empty scratch database or the reference one. Feel free to use it too.

Measure, don't guess: [`examples/lock_probe.py`](examples/lock_probe.py) runs DDL statements against `orders` (or another table) under concurrent traffic and reports locks and the worst latency.

---

## Exercise 1 ★: Review the locks of a release

A DBA asks for the list of statements in revisions 2602–2604 that take an `ACCESS EXCLUSIVE` lock on a table with data, and what every other statement locks.

1. Print the SQL of the release with Alembic's offline mode.
2. For each `ALTER TABLE` / `CREATE TRIGGER` statement, find the strongest lock it takes on its table, **by running it**: on an empty scratch database at 2601, execute each statement in a transaction, look at `pg_locks` for your own backend before committing, then commit and go on with the next statement.
3. Which statements would hold their `ACCESS EXCLUSIVE` lock for more than a moment on a big table?

**Self-check:** 6 statements take `ACCESS EXCLUSIVE` (1 in 2602, 3 in 2603, 2 in 2604), `CREATE OR REPLACE TRIGGER` takes `SHARE ROW EXCLUSIVE`, and both `VALIDATE CONSTRAINT`s take `SHARE UPDATE EXCLUSIVE`. None of the six scans the table.

<details><summary>Hint</summary>

`alembic upgrade 2601:2604 --sql`. A query for your own locks: `SELECT c.relname, l.mode FROM pg_locks l JOIN pg_class c ON c.oid = l.relation WHERE l.pid = pg_backend_pid()`; filter out `pg_catalog` (your query itself locks `pg_class`). The statements inside autocommit blocks can't run in your test transaction as they are (`CONCURRENTLY`, the `DO` block): you know their locks from section 8.
</details>

## Exercise 2 ★: A new order status

Payments flagged by fraud screening go **on hold** before they're shipped. Write revision 2605 that adds `'on_hold'` to `order_status` (after `'paid'`) and creates a partial index `orders_on_hold_created_at_idx` on `orders (created_at) WHERE status = 'on_hold'`, so the review queue is cheap to list.

1. Write it first the obvious way: `op.execute("ALTER TYPE ...")` followed by `op.create_index(..., postgresql_where=...)`. Run it on an empty scratch database. What happens, and what is in `alembic_version` afterwards?
2. Fix it. The index must be built without blocking writes.
3. What should `downgrade()` do?
4. On the upgraded database, insert an order with status `on_hold` and read it with the module-22 code (`bazaar.db.connect()`, which registers the `OrderStatus` enum). What does that teach about deployment order?

**Self-check:** the naive version fails with `unsafe use of new value "on_hold" of enum type order_status`, and the database is still at 2701 with the old labels. The fixed version leaves `enum_range(NULL::order_status)` = `{pending,paid,on_hold,shipped,delivered,cancelled,refunded}` and a valid partial index.

<details><summary>Hint</summary>

The value must be committed before the index can mention it: `op.get_context().autocommit_block()`, or a separate revision. `IF NOT EXISTS` makes the `ADD VALUE` rerunnable. There's no `DROP VALUE`.
</details>

## Exercise 3 ★: Which revision is this database at?

A database was restored from a dump that left out `alembic_version` (a "data only for the important tables" dump script). In the meantime someone added an index by hand. Reproduce it:

```bash
createdb bazaar_mystery        # or CREATE DATABASE in psql
alembic -x dsn=postgresql://postgres:postgres@localhost:5432/bazaar_mystery upgrade 2604
psql -d bazaar_mystery -c "DROP TABLE bazaar.alembic_version" \
                       -c "CREATE INDEX orders_status_idx ON orders (status)"
```

Write a script that finds the revision whose schema is closest to the mystery database, prints the remaining differences, stamps the database, and upgrades it to head.

**Self-check:** the closest revision is 2604, with exactly one difference (`only in mystery: index:orders_status_idx`); revisions 2603 and 2701 are 4 and 26 differences away. After `stamp 2604` and `upgrade head`, `alembic current` says `2701 (head)`.

<details><summary>Hint</summary>

`bazaar.catalog.snapshot()` and `diff()`. Build a "ladder" database: upgrade it one revision at a time and compare after each step. Decide what to do about the extra index: drop it, or adopt it in a new revision (with `IF NOT EXISTS`).
</details>

## Exercise 4 ★★: Review this migration

A colleague submits this revision for review:

```python
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from bazaar.orm.models import Order  # noqa: F401  (for "type hints")

revision = "2605"
down_revision = "2701"


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("tracking_token", pg.UUID(), nullable=False,
                  server_default=sa.text("gen_random_uuid()")),
    )
    op.create_index("orders_status_idx", "orders", ["status"])
    op.alter_column("orders", "shipping_address_id", nullable=False)
    op.create_check_constraint("total_nonnegative", "orders", "total >= 0")


def downgrade() -> None:
    pass
```

1. List every problem, with the lock it takes and for how long, measured on the reference database with `lock_probe.py` (one `--ddl` per statement, `--undo` to restore).
2. Rewrite it so that no step blocks writes to `orders` for more than a few milliseconds, and so that it can be run again after failing half-way. Measure your version's steps the same way, including one batch of the backfill. What batch size would you pick, and why?
3. Run your revision on an empty database: upgrade, downgrade, upgrade again.

**Self-check:** in the bad version, the `ADD COLUMN` alone stopped traffic for about 3 s (and in the revision, all four statements share one transaction, so the `ACCESS EXCLUSIVE` lock is held for all of them). In your version, every DDL step's worst traffic latency stays in single-digit milliseconds; a backfill batch of 2,000 rows (with triggers off) takes about 0.15 s.

<details><summary>Hint</summary>

Six problems, not four. Look at the import, at `downgrade()`, and at what happens to the backfill's `UPDATE`s with the audit trigger on `orders`. `bazaar.migrate` has a helper for almost every step.
</details>

## Exercise 5 ★★: From autogenerate's draft to a revision

Reviews get "helpful" votes. Add to the metadata (`bazaar/sa/tables.py`, `reviews`):

- `helpful_votes integer NOT NULL DEFAULT 0` with `CHECK (helpful_votes >= 0)` (name `helpful_votes_nonnegative`, so the convention makes it `reviews_helpful_votes_nonnegative`),
- an index `reviews_product_id_helpful_votes_idx` on `(product_id, helpful_votes DESC)`.

Let autogenerate draft revision 2605 against a database at head, then turn the draft into a revision you'd deploy. Which of the draft's operations are fine as they are?

**Self-check:** before the revision, `bazaar.migrate.compare_models()` reports an added column, index and constraint; after it (and after a downgrade and upgrade again), it reports `[]`.

<details><summary>Hint</summary>

The draft is three lines. One of them is perfectly online since PostgreSQL 11. The reference solution patches the metadata in memory (`solutions/ex05_metadata.py`), so the project's `tables.py` stays as it is; you can simply edit the file and revert it afterwards.
</details>

## Exercise 6 ★★: A denormalized counter, backfilled under load

Product listings want "123 reviews" without counting each time. Write revision 2605: `products.review_count integer NOT NULL DEFAULT 0`, kept up to date by a trigger on `reviews` (insert and delete), and a backfill of the existing 292,355 reviews in batches of product ids.

The backfill must be correct **while new reviews are being written**. Run it on the reference database with a thread that keeps inserting reviews, for batch sizes 1,000, 10,000 and 50,000, and report the time, the insert latencies, and whether `sum(review_count) = count(*)` afterwards. Clean up (delete the inserted reviews, downgrade).

**Self-check:** `sum(review_count)` equals `count(*) FROM reviews` after every run, whatever the batch size. Larger batches make inserts wait longer (seconds at 50,000).

<details><summary>Hint</summary>

The trap: a batch counts reviews as of its snapshot, then writes the count; a review committed in between was already added by the trigger to the *old* value, and the batch's absolute count overwrites that increment. Lock the batch's product rows *before* counting. And put the `ADD COLUMN` (which locks `products`) and the `CREATE TRIGGER` (which locks `reviews`) in different transactions; find out why by running them together while reviews are inserted.
</details>

## Exercise 7 ★★: A unique constraint on dirty data

Payment providers retry their webhooks, and the handler wants `INSERT ... ON CONFLICT (provider, provider_ref) DO NOTHING`. Write revision 2605 that makes `(provider, provider_ref)` unique on `payments` (536,816 rows) online, as a real **constraint** named `payments_provider_provider_ref_key`.

Production has a duplicate. Simulate it before you start:

```sql
INSERT INTO payments (order_id, amount, provider, provider_ref, status)
SELECT order_id, amount, provider, provider_ref, status FROM payments WHERE id = 1;
```

1. Run your revision without handling duplicates. What fails, and what does it leave behind in `pg_index`?
2. Handle duplicates (they're payments: don't just delete them) and run it again. Does the leftover from step 1 cause trouble?
3. Downgrade and remove the duplicate when you're done.

**Self-check:** step 1 fails with `could not create unique index "payments_provider_provider_ref_key"` and leaves that index with `indisvalid = false`; step 2 succeeds with a valid index backing a `u` constraint, and the duplicate row is preserved somewhere for a human to review (not deleted).

<details><summary>Hint</summary>

`CREATE UNIQUE INDEX CONCURRENTLY`, then `ALTER TABLE ... ADD CONSTRAINT ... UNIQUE USING INDEX ...`. `bazaar.migrate.create_index_concurrently()` already knows what to do about an invalid index of the same name. `DELETE ... USING ... RETURNING` inside a CTE can move rows in one statement.
</details>

## Exercise 8 ★★: Rename a table without breaking running code

The product team now says "basket", not "cart". Rename `cart_items` to `basket_items` in two revisions:

- 2605 (expand): rename the table, its constraints and its index to the new name (Module 24's naming convention), and keep `cart_items` working for old code;
- 2606 (contract): remove the compatibility layer.

Prove, connected as `bazaar_app`, that at 2605 both the old code's upsert (`INSERT INTO cart_items ... ON CONFLICT (user_id, product_id) DO UPDATE ...`) and the new code's (on `basket_items`) work, and that at 2606 only the new one does.

**Self-check:** at 2605 both upserts succeed and change the same row; at 2606 the old one fails with `relation "cart_items" does not exist`; downgrading to 2701 restores `cart_items`, `cart_items_pkey` and `cart_items_product_id_idx`.

<details><summary>Hint</summary>

A simple view (`SELECT * FROM basket_items`) is automatically updatable, `ON CONFLICT` included. Does `bazaar_app` need a `GRANT` on the view? Think about who creates it (Module 15's default privileges).
</details>

## Exercise 9 ★★: Two heads

Two developers branch off at head 2701. One writes revision 2702 ("add wishlist"), the other 2703 ("add gift wrap"); both say `down_revision = "2701"`. Both branches get merged.

1. In a scratch copy of the migrations, create both revisions (as Alembic would on each branch). What do `alembic heads` and `alembic upgrade head` say?
2. Write the CI check that fails in this situation, with a message that tells the developer what to do.
3. Resolve it twice: by **rebasing** the second revision, and by a **merge revision**. Print the history for both.
4. When is a merge revision the right answer, and what does it *not* guarantee about the order of 2702 and 2703?

**Self-check:** with two heads, `alembic upgrade head` fails with "Multiple head revisions are present"; after either fix there's exactly one head (2703 or 2704), and the merge revision has `down_revision = ('2702', '2703')`.

<details><summary>Hint</summary>

`command.revision(cfg, ..., head="2701", splice=True)` creates a revision on a non-head parent (that's what the second branch did). `ScriptDirectory.from_config(cfg).get_heads()` for the check. If you rewrite revision files in a script, beware of Python's cached `.pyc` files.
</details>

## Exercise 10 ★★★: Prove that writers were never blocked

"The migration was online" is a claim. Build the proof: a script that runs `alembic upgrade 2605` on the reference database while

- a traffic thread reads and updates random orders by primary key, recording latencies, and
- a sampler thread polls `pg_locks` every couple of milliseconds for the **migration's** backend (identify it by `application_name`) and its locks on `orders`,

and then reports the longest time the migration held a lock that conflicts with `ROW EXCLUSIVE` (what writers need), plus traffic latency percentiles. It must exit non-zero when that time exceeds a threshold (default 50 ms), and downgrade back to 2701 afterwards.

Use it on two versions of a revision that adds `CHECK (total >= 0)` to `orders`: `op.create_check_constraint(...)`, and the online version.

**Self-check:** the naive revision fails the check (about 80 ms of `ACCESS EXCLUSIVE` on the cached 500,000-row table, and a traffic round trip of about the same length); the online one passes with only `SHARE UPDATE EXCLUSIVE` visible to the sampler.

<details><summary>Hint</summary>

Write-blocking modes: `SHARE`, `SHARE ROW EXCLUSIVE`, `EXCLUSIVE`, `ACCESS EXCLUSIVE`. A 2 ms sampler misses locks held for less than 2 ms: that's fine, and your report should say what it can and cannot see. Watch the naming convention in the naive `downgrade()`: `op.drop_constraint("orders_total_nonnegative", ...)` doesn't drop what you think.
</details>

## Exercise 11 ★★★: Split `users.full_name`, with zero downtime

New registration forms ask for first and last names separately. Replace `users.full_name` by `first_name` and `last_name`, while old code (which reads and writes `full_name`) and new code (which uses the parts) run side by side:

- 2605 (expand): the new columns, a split rule (the last word is the last name; collapse whitespace), a trigger that keeps both representations in sync whichever side wrote, and a backfill of the 100,000 users;
- 2606 (contract): the parts become `NOT NULL` online, the trigger and `full_name` go.

Show, on a scratch database: rows written before the change, an insert by old code (`full_name` only), an insert by new code (parts only, although `full_name` is still `NOT NULL`), an update by each; then what old code gets after the contract. Then time 2605 on the reference database.

**Self-check:** `'  Zoé   Martin '` becomes `Zoé` / `Martin`, `Cher` becomes `Cher` / `''`; an old-code update of `full_name` re-splits, a new-code update of `last_name` rebuilds `full_name`; after 2606, old code gets `column "full_name" of relation "users" does not exist`. On the reference database no user is left without a `first_name`.

<details><summary>Hint</summary>

A `BEFORE` row trigger may set `NEW.full_name`, and `NOT NULL` is checked after `BEFORE` triggers. In the trigger, decide which side wrote by comparing `NEW` with `OLD`. The backfill must not fire your trigger (why?).
</details>

## Exercise 12 ★★★: Squash the history

Years later, the team wants new databases to start from a recent snapshot instead of replaying every revision. In a scratch copy of the migrations, replace revisions 2600–2604 by a single new baseline `2700`, and make `2701` follow it.

1. Produce the new snapshot from a database at 2604 (what must the dump leave out that `make_baseline.sh` didn't need to?).
2. Prove that a fresh database built from the squashed chain equals one built from the original chain.
3. What happens to an existing database at 2701? At 2603?

**Self-check:** the squashed history is `<base> -> 2700 -> 2701 (head)`; the two fresh databases have no catalog differences; a database at 2701 is unaffected; one at 2603 fails with `Can't locate revision identified by '2603'`.

<details><summary>Hint</summary>

A database at 2604 has an `alembic_version` table, and a baseline that creates one would collide with the one Alembic creates first (`--exclude-table`). The new baseline revision can reuse almost all of `2600_baseline.py`.
</details>
