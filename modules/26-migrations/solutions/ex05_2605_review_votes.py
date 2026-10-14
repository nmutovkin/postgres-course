"""reviews.helpful_votes, and an index for "most helpful first"

Revision ID: 2605
Revises: 2701
Create Date: 2026-10-14

Module 26, exercise 5: autogenerate's draft, reviewed.
* add_column with a constant default: kept (catalog-only since PG 11).
* create_index: made CONCURRENT (reviews has 292,355 rows; a plain build
  blocks writes for its whole duration).
* create_check_constraint: split into NOT VALID + VALIDATE.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from bazaar.migrate import (
    add_check_constraint_online,
    create_index_concurrently,
    drop_index_concurrently,
)

revision: str = "2605"
down_revision: str | Sequence[str] | None = "2701"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reviews", sa.Column("helpful_votes", sa.Integer(), server_default="0", nullable=False)
    )
    add_check_constraint_online(
        "reviews", "reviews_helpful_votes_nonnegative", "helpful_votes >= 0"
    )
    create_index_concurrently(
        "reviews_product_id_helpful_votes_idx", "reviews", "product_id, helpful_votes DESC"
    )


def downgrade() -> None:
    drop_index_concurrently("reviews_product_id_helpful_votes_idx")
    op.drop_column("reviews", "helpful_votes")  # drops the CHECK with it
