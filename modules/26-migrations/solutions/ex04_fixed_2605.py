"""orders: tracking token, status index, required shipping address, non-negative total

Revision ID: 2605
Revises: 2801
Create Date: 2026-10-14

Module 26, exercise 4: the reviewed version. No statement holds a lock that
blocks orders traffic for more than a few milliseconds, and the revision can
be run again after a failure half-way (its autocommit blocks are not atomic).
"""

from collections.abc import Sequence

from alembic import op

from bazaar.migrate import (
    add_check_constraint_online,
    backfill,
    create_index_concurrently,
    drop_index_concurrently,
    set_not_null_online,
)

revision: str = "2605"
down_revision: str | Sequence[str] | None = "2801"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Nullable, no default: catalog only. The default applies to NEW rows only.
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS tracking_token uuid")
    op.execute("ALTER TABLE orders ALTER COLUMN tracking_token SET DEFAULT gen_random_uuid()")
    # 2. Existing rows, in batches small enough to lock each row only briefly
    #    (2,000 rows = about 0.15 s), without audit rows or updated_at bumps.
    backfill(
        "orders",
        "tracking_token = gen_random_uuid()",
        "tracking_token IS NULL",
        batch_size=2_000,
        skip_triggers=True,
    )
    # 3. NOT NULL through a validated CHECK (PG 12+), for both columns.
    set_not_null_online("orders", "tracking_token")
    set_not_null_online("orders", "shipping_address_id")
    # 4. The index, concurrently (drops an INVALID leftover first).
    create_index_concurrently("orders_status_idx", "orders", "status")
    # 5. The CHECK: NOT VALID, then VALIDATE, in separate transactions.
    add_check_constraint_online("orders", "orders_total_nonnegative", "total >= 0")


def downgrade() -> None:
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_total_nonnegative")
    drop_index_concurrently("orders_status_idx")
    op.execute("ALTER TABLE orders ALTER COLUMN shipping_address_id DROP NOT NULL")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS tracking_token")
