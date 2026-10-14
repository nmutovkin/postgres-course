"""baseline: the schema built by project/db up to module 25

Revision ID: 2600
Revises:
Create Date: 2026-10-14

Two ways a database reaches this revision:

* An EXISTING database (built by project/bin/reset.sh, or production) already
  has this schema. It is *stamped*, never upgraded through here:

      alembic stamp 2600          # writes '2600' into bazaar.alembic_version, runs no DDL

* A NEW, EMPTY database (a test database, a new environment) runs upgrade():
  it executes baseline/schema.sql, a cleaned `pg_dump --schema-only` of the
  reference state, so both paths end with the same schema. Tests prove it
  (tests/test_migrations.py compares the two catalog by catalog).

The baseline needs superuser rights (CREATE EXTENSION, CREATE ROLE, ALTER ...
OWNER TO), so it steps out of the SET ROLE bazaar_owner that env.py applies.
Roles are cluster-wide: they are created here only if missing, without LOGIN
or passwords. Passwords are provisioning (secrets), not schema.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

from bazaar.migrate import execute_script

revision: str = "2600"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("bazaar",)
depends_on: str | Sequence[str] | None = None

SCHEMA_SQL = Path(__file__).resolve().parent.parent / "baseline" / "schema.sql"

ROLES = ("bazaar_owner", "bazaar_app", "bazaar_readonly", "bazaar_analyst", "bazaar_seller")


def upgrade() -> None:
    op.execute("RESET ROLE")  # superuser for this revision
    op.execute("SET LOCAL check_function_bodies = false")  # as pg_restore does
    roles = "\n".join(
        f"    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{r}') THEN"
        f" CREATE ROLE {r} NOLOGIN; END IF;"
        for r in ROLES
    )
    execute_script(f"DO $$\nBEGIN\n{roles}\nEND\n$$;\nGRANT bazaar_readonly TO bazaar_analyst;\n")
    execute_script(SCHEMA_SQL.read_text())
    # Database-level settings are not part of a schema dump.
    execute_script(
        """DO $$
BEGIN
    EXECUTE format('ALTER DATABASE %I SET search_path = bazaar, public', current_database());
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO bazaar_app, bazaar_analyst, bazaar_seller',
                   current_database());
END
$$;
SET search_path = bazaar, public;
REFRESH MATERIALIZED VIEW bazaar.seller_sales_daily;
"""
    )
    # alembic_version was created before the roles existed (as the connecting
    # role); give it to the owner like everything else.
    op.execute("ALTER TABLE bazaar.alembic_version OWNER TO bazaar_owner")
    op.execute("SET ROLE bazaar_owner")


def downgrade() -> None:
    raise NotImplementedError(
        "the baseline cannot be downgraded: drop the database instead (DROP DATABASE ...)"
    )
