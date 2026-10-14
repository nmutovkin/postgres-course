"""rename cart_items to basket_items, contract: drop the compatibility view

Revision ID: 2606
Revises: 2605
Create Date: 2026-10-14

Deploy only when no code uses cart_items any more (check pg_stat_statements
for queries that still mention it, Module 12).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "2606"
down_revision: str | Sequence[str] | None = "2605"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP VIEW cart_items")


def downgrade() -> None:
    op.execute("CREATE VIEW cart_items AS SELECT * FROM basket_items")
