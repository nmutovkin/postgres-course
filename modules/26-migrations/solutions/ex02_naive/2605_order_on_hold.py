"""naive version of exercise 2: ADD VALUE and its use in one transaction

Revision ID: 2605
Revises: 2801
"""

from alembic import op

revision = "2605"
down_revision = "2801"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE order_status ADD VALUE 'on_hold' AFTER 'paid'")
    op.execute(
        "CREATE INDEX orders_on_hold_created_at_idx ON orders (created_at) WHERE status = 'on_hold'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX orders_on_hold_created_at_idx")
