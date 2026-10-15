"""order_status gains 'on_hold' (payment under manual review), with a partial index

Revision ID: 2605
Revises: 2801
Create Date: 2026-10-14

Module 26, exercise 2. ALTER TYPE ... ADD VALUE may run in a transaction
(PG 12+), but the new value can't be USED until that transaction commits.
The partial index uses it, so the ADD VALUE runs in an autocommit block
(committed at once); the index is built concurrently afterwards.

Deploy order: application code that knows OrderStatus.ON_HOLD first, then
this revision, then code that writes it.
"""

from collections.abc import Sequence

from alembic import op

from bazaar.migrate import create_index_concurrently, drop_index_concurrently

revision: str = "2605"
down_revision: str | Sequence[str] | None = "2801"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'on_hold' AFTER 'paid'")
    # A new transaction: the value is committed, so it may be used.
    create_index_concurrently(
        "orders_on_hold_created_at_idx", "orders", "created_at", where="status = 'on_hold'"
    )


def downgrade() -> None:
    # The index can go; the enum value cannot (no ALTER TYPE ... DROP VALUE).
    # Recreating the type would rewrite orders under ACCESS EXCLUSIVE and fail
    # anyway while views use the column. Be honest instead.
    drop_index_concurrently("orders_on_hold_created_at_idx")
    raise NotImplementedError(
        "order_status 'on_hold' cannot be removed; roll forward instead"
        " (the index orders_on_hold_created_at_idx was dropped)"
    )
