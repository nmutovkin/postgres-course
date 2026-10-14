"""orders.idempotency_key: a client-chosen key that makes checkout safe to retry

Revision ID: 2604
Revises: 2603
Create Date: 2026-10-14

A checkout request may be sent twice (a timeout and a retry, a double click).
The client sends an Idempotency-Key with it; the orders created by the first
request carry it, and a repeat finds them instead of ordering again (the
module 28 capstone's POST /checkout; bazaar.repo.place_order_idempotent).

One checkout creates one order per seller, so the key is unique per
(user_id, idempotency_key, seller_id), only among rows that have a key: a
partial unique index, built CONCURRENTLY. The length CHECK is added NOT VALID
and validated separately: the column is new and empty, so validation is
instant here, but the pattern is the one to use on any big table.
"""

from collections.abc import Sequence

from alembic import op

from bazaar.migrate import (
    add_check_constraint_online,
    create_index_concurrently,
    drop_index_concurrently,
)

revision: str = "2604"
down_revision: str | Sequence[str] | None = "2603"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS idempotency_key text")  # re-runnable
    add_check_constraint_online(
        "orders",
        "orders_idempotency_key_length",
        "length(idempotency_key) BETWEEN 8 AND 200",
    )
    create_index_concurrently(
        "orders_idempotency_key_idx",
        "orders",
        "user_id, idempotency_key, seller_id",
        unique=True,
        where="idempotency_key IS NOT NULL",
    )


def downgrade() -> None:
    drop_index_concurrently("orders_idempotency_key_idx")
    op.execute("ALTER TABLE orders DROP COLUMN idempotency_key")  # drops the CHECK too
