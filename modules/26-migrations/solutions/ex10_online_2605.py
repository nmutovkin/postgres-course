"""orders.total must not be negative (online)

Revision ID: 2605
Revises: 2701
Create Date: 2026-10-14

Module 26, exercise 10. ADD CONSTRAINT ... NOT VALID (brief ACCESS EXCLUSIVE,
no scan), COMMIT, VALIDATE CONSTRAINT (scan under SHARE UPDATE EXCLUSIVE).
"""

from collections.abc import Sequence

from alembic import op

from bazaar.migrate import add_check_constraint_online

revision: str = "2605"
down_revision: str | Sequence[str] | None = "2701"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    add_check_constraint_online("orders", "orders_total_nonnegative", "total >= 0")


def downgrade() -> None:
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_total_nonnegative")
