"""users.full_name -> first_name + last_name, contract

Revision ID: 2606
Revises: 2605
Create Date: 2026-10-14

Only after every deployed version writes first_name/last_name (and nothing
reads full_name any more): the parts become NOT NULL (online), the sync
trigger goes, and full_name is dropped.
"""

from collections.abc import Sequence

from alembic import op

from bazaar.migrate import set_not_null_online

revision: str = "2606"
down_revision: str | Sequence[str] | None = "2605"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    set_not_null_online("users", "first_name")
    set_not_null_online("users", "last_name")
    op.execute("DROP TRIGGER users_sync_names ON users")
    op.execute("ALTER TABLE users DROP COLUMN full_name")


def downgrade() -> None:
    # The data can be rebuilt from the parts, so this downgrade is honest.
    op.execute("ALTER TABLE users ADD COLUMN full_name text")
    op.execute("UPDATE users SET full_name = btrim(first_name || ' ' || last_name)")
    op.execute("ALTER TABLE users ALTER COLUMN full_name SET NOT NULL")
    op.execute(
        "CREATE OR REPLACE TRIGGER users_sync_names BEFORE INSERT OR UPDATE ON users"
        " FOR EACH ROW EXECUTE FUNCTION users_sync_names()"
    )
    op.execute("ALTER TABLE users ALTER COLUMN first_name DROP NOT NULL")
    op.execute("ALTER TABLE users ALTER COLUMN last_name DROP NOT NULL")
