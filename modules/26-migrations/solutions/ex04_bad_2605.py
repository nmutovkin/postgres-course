"""orders: tracking token, status index, required shipping address, non-negative total

Revision ID: 2605
Revises: 2801

THE MIGRATION OF EXERCISE 4, AS SUBMITTED FOR REVIEW. Do not use: every
statement in it blocks orders traffic (see solutions/README.md).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from bazaar.orm.models import Order  # noqa: F401  (for "type hints")

revision = "2605"
down_revision = "2801"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "tracking_token",
            pg.UUID(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
    )
    op.create_index("orders_status_idx", "orders", ["status"])
    op.alter_column("orders", "shipping_address_id", nullable=False)
    op.create_check_constraint("total_nonnegative", "orders", "total >= 0")


def downgrade() -> None:
    pass
