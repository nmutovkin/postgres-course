"""orders.total must not be negative (naive)

Revision ID: 2605
Revises: 2801
"""

from alembic import op

revision = "2605"
down_revision = "2801"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint("total_nonnegative", "orders", "total >= 0")


def downgrade() -> None:
    op.drop_constraint(op.f("orders_total_nonnegative"), "orders", type_="check")
