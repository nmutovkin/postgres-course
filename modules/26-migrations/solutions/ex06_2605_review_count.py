"""products.review_count, maintained by a trigger, backfilled in locked batches

Revision ID: 2605
Revises: 2801
Create Date: 2026-10-14

Module 26, exercise 6.

1. ADD COLUMN review_count integer NOT NULL DEFAULT 0: catalog-only (PG 11+),
   committed on its own (see the deadlock note in upgrade()).
2. AFTER INSERT OR DELETE trigger on reviews: +1 / -1 (a delta, so concurrent
   reviews don't overwrite each other's counts).
3. Backfill per id range, each batch in its own transaction:
       SELECT ... FROM products WHERE id in range FOR UPDATE   -- lock first
       UPDATE products SET review_count = (SELECT count(*) ...)  -- then count
   Without the lock, a review committed between the batch's snapshot and its
   UPDATE would be counted by the trigger (+1 on the old value) and then
   overwritten by the batch's stale absolute count: a lost increment. With
   the lock, new reviews of those products wait (their trigger's UPDATE
   blocks), and the UPDATE statement's fresh snapshot (READ COMMITTED takes
   one per statement) sees every review committed before the lock.

Batch size: EX06_BATCH (default 2000), see the exercise's measurements.
"""

import os
import time
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2605"
down_revision: str | Sequence[str] | None = "2801"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COUNT_TRIGGER = """
CREATE OR REPLACE FUNCTION reviews_maintain_count()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE products SET review_count = review_count + 1 WHERE id = NEW.product_id;
    ELSE
        UPDATE products SET review_count = review_count - 1 WHERE id = OLD.product_id;
    END IF;
    RETURN NULL;
END;
$$
"""


def upgrade() -> None:
    # Its own transaction: holding ACCESS EXCLUSIVE on products while waiting
    # for SHARE ROW EXCLUSIVE on reviews (CREATE TRIGGER) deadlocks with any
    # INSERT INTO reviews, whose foreign key check needs a lock on products.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS review_count integer NOT NULL DEFAULT 0"
        )
    op.execute(COUNT_TRIGGER)
    op.execute(
        "CREATE OR REPLACE TRIGGER reviews_maintain_count AFTER INSERT OR DELETE ON reviews"
        " FOR EACH ROW EXECUTE FUNCTION reviews_maintain_count()"
    )
    if op.get_context().as_sql:
        return  # offline: the backfill below needs a live connection (write a DO block)
    batch = int(os.environ.get("EX06_BATCH", "2000"))
    conn = op.get_bind()
    with op.get_context().autocommit_block():
        lo, hi = conn.execute(sa.text("SELECT min(id), max(id) FROM products")).one()
        started, batches = time.monotonic(), 0
        conn.execute(sa.text("SET session_replication_role = replica"))  # no audit rows
        try:
            while lo is not None and lo <= hi:
                conn.execute(sa.text("BEGIN"))
                conn.execute(
                    sa.text("SELECT 1 FROM products WHERE id >= :lo AND id < :hi FOR UPDATE"),
                    {"lo": lo, "hi": lo + batch},
                )
                conn.execute(
                    sa.text(
                        "UPDATE products p SET review_count = c.n"
                        " FROM (SELECT product_id, count(*) AS n FROM reviews"
                        "       WHERE product_id >= :lo AND product_id < :hi"
                        "       GROUP BY product_id) AS c"
                        " WHERE p.id = c.product_id AND p.review_count IS DISTINCT FROM c.n"
                    ),
                    {"lo": lo, "hi": lo + batch},
                )
                conn.execute(sa.text("COMMIT"))
                lo += batch
                batches += 1
        finally:
            conn.execute(sa.text("RESET session_replication_role"))
        print(f"backfill: {batches} batches of {batch} in {time.monotonic() - started:.1f}s")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS reviews_maintain_count ON reviews")
    op.execute("DROP FUNCTION IF EXISTS reviews_maintain_count()")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS review_count")
