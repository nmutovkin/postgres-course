"""users.full_name -> first_name + last_name, expand: new columns, sync trigger, backfill

Revision ID: 2605
Revises: 2801
Create Date: 2026-10-14

Module 26, exercise 11. During the transition old code writes full_name and
new code writes first_name/last_name; a BEFORE trigger keeps both in sync,
whichever side wrote. Rule: last_name is the last word, first_name the rest
("Liam O'Connor" -> "Liam" / "O'Connor"; "Ana María de la Cruz" ->
"Ana María de la" / "Cruz": imperfect, which is why the new UI asks for both).
"""

from collections.abc import Sequence

from alembic import op

from bazaar.migrate import backfill

revision: str = "2605"
down_revision: str | Sequence[str] | None = "2801"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SPLIT = r"""
CREATE OR REPLACE FUNCTION split_full_name(p_full text, OUT first_name text, OUT last_name text)
LANGUAGE sql
IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT CASE WHEN strpos(n, ' ') = 0 THEN n ELSE regexp_replace(n, '\s+\S+$', '') END,
           CASE WHEN strpos(n, ' ') = 0 THEN '' ELSE substring(n FROM '\S+$') END
    FROM (SELECT regexp_replace(btrim(p_full), '\s+', ' ', 'g') AS n) AS t
$$
"""

SYNC = """
CREATE OR REPLACE FUNCTION users_sync_names()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.first_name IS NULL AND NEW.full_name IS NOT NULL THEN      -- old code
            SELECT s.first_name, s.last_name INTO NEW.first_name, NEW.last_name
            FROM split_full_name(NEW.full_name) AS s;
        ELSIF NEW.full_name IS NULL THEN                                  -- new code
            NEW.full_name := btrim(NEW.first_name || ' ' || coalesce(NEW.last_name, ''));
        END IF;
    ELSIF NEW.full_name IS DISTINCT FROM OLD.full_name THEN               -- old code
        SELECT s.first_name, s.last_name INTO NEW.first_name, NEW.last_name
        FROM split_full_name(NEW.full_name) AS s;
    ELSIF NEW.first_name IS DISTINCT FROM OLD.first_name
       OR NEW.last_name IS DISTINCT FROM OLD.last_name THEN               -- new code
        NEW.full_name := btrim(NEW.first_name || ' ' || coalesce(NEW.last_name, ''));
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name text")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name text")
    op.execute(SPLIT)
    op.execute(SYNC)
    # full_name stays NOT NULL for now: the BEFORE trigger fills it for new
    # code before the constraint is checked.
    op.execute(
        "CREATE OR REPLACE TRIGGER users_sync_names BEFORE INSERT OR UPDATE ON users"
        " FOR EACH ROW EXECUTE FUNCTION users_sync_names()"
    )
    # Existing rows. Triggers off: the backfill must not run the sync logic
    # (which would rewrite full_name from the parts).
    backfill(
        "users",
        "(first_name, last_name) = (SELECT s.first_name, s.last_name"
        " FROM split_full_name(full_name) AS s)",
        "first_name IS NULL",
        batch_size=5_000,
        skip_triggers=True,
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS users_sync_names ON users")
    op.execute("DROP FUNCTION IF EXISTS users_sync_names()")
    op.execute("DROP FUNCTION IF EXISTS split_full_name(text)")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS last_name")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS first_name")
