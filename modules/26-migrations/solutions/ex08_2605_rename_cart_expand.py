"""rename cart_items to basket_items, expand: keep the old name as a view

Revision ID: 2605
Revises: 2701
Create Date: 2026-10-14

Module 26, exercise 8. Old code keeps using cart_items (the view: a simple
view is automatically updatable, ON CONFLICT included); new code uses
basket_items. Renames are catalog-only, but each takes ACCESS EXCLUSIVE:
all in one short transaction, under env.py's lock_timeout.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "2605"
down_revision: str | Sequence[str] | None = "2701"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RENAMES = [
    ("CONSTRAINT", "cart_items_pkey", "basket_items_pkey"),
    ("CONSTRAINT", "cart_items_user_id_fkey", "basket_items_user_id_fkey"),
    ("CONSTRAINT", "cart_items_product_id_fkey", "basket_items_product_id_fkey"),
    ("CONSTRAINT", "cart_items_quantity_positive", "basket_items_quantity_positive"),
]


def upgrade() -> None:
    op.execute("ALTER TABLE cart_items RENAME TO basket_items")
    for _, old, new in RENAMES:  # keep the naming convention (module 24)
        op.execute(f"ALTER TABLE basket_items RENAME CONSTRAINT {old} TO {new}")
    op.execute("ALTER INDEX cart_items_product_id_idx RENAME TO basket_items_product_id_idx")
    # Created as bazaar_owner: the default privileges give bazaar_app DML on the view.
    op.execute("CREATE VIEW cart_items AS SELECT * FROM basket_items")


def downgrade() -> None:
    op.execute("DROP VIEW cart_items")
    op.execute("ALTER INDEX basket_items_product_id_idx RENAME TO cart_items_product_id_idx")
    for _, old, new in RENAMES:
        op.execute(f"ALTER TABLE basket_items RENAME CONSTRAINT {new} TO {old}")
    op.execute("ALTER TABLE basket_items RENAME TO cart_items")
