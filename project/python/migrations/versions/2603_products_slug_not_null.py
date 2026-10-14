"""products.slug NOT NULL, contract phase, without a long ACCESS EXCLUSIVE scan

Revision ID: 2603
Revises: 2602
Create Date: 2026-10-14

Deploy after 2602 has finished (every row has a slug, the trigger fills new
ones). A plain `ALTER TABLE products ALTER COLUMN slug SET NOT NULL` would scan
all 200,000 rows while holding ACCESS EXCLUSIVE: no reads, no writes. Instead
(PostgreSQL 12+):

1. ADD CONSTRAINT products_slug_not_null CHECK (slug IS NOT NULL) NOT VALID
   -- ACCESS EXCLUSIVE for a moment, no scan; enforced for new rows. COMMIT.
2. VALIDATE CONSTRAINT -- scans under SHARE UPDATE EXCLUSIVE: reads and writes
   continue. COMMIT.
3. SET NOT NULL -- the validated CHECK proves there are no NULLs, so no scan.
4. DROP the now redundant CHECK.
"""

from collections.abc import Sequence

from alembic import op

from bazaar.migrate import set_not_null_online

revision: str = "2603"
down_revision: str | Sequence[str] | None = "2602"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    set_not_null_online("products", "slug")


def downgrade() -> None:
    op.execute("ALTER TABLE products ALTER COLUMN slug DROP NOT NULL")
