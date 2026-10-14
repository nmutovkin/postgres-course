"""products.slug, expand phase: nullable column, slug trigger, batched backfill, unique index

Revision ID: 2602
Revises: 2601
Create Date: 2026-10-14

Product pages get URLs like /p/voltix-wireless-headphones-527-123456. This
revision is the EXPAND half of an expand/contract change; 2603 is the
contract half (NOT NULL). Every step keeps the old application working and
takes no lock that blocks traffic for more than a moment:

1. slugify(text): an IMMUTABLE SQL function (unaccent with an explicit
   dictionary, lower, non-alphanumerics to '-').
2. ADD COLUMN slug text: nullable, no default, so a catalog-only change. Its
   ACCESS EXCLUSIVE lock is held for milliseconds, *if* it is granted quickly:
   lock_timeout (env.py) makes it give up instead of queueing every query on
   products behind a long-running transaction.
3. A BEFORE INSERT trigger fills slug for new rows, so code that knows nothing
   about slugs (every deployed version) produces complete rows from now on.
   Steps 1-3 commit together.
4. Backfill the existing ~200,000 rows in batches of 10,000, one short
   transaction each, without firing the audit/updated_at triggers
   (session_replication_role = replica: a backfill is not a business change).
5. CREATE UNIQUE INDEX CONCURRENTLY products_slug_key: reads and writes go on
   while it builds.

Steps 4 and 5 run in autocommit mode, so the revision is not atomic: if the
index build fails, steps 1-4 stay committed and the next `alembic upgrade`
runs the whole revision again. Hence OR REPLACE / IF NOT EXISTS everywhere,
and a backfill whose WHERE clause skips rows that are already done.
"""

from collections.abc import Sequence

from alembic import op

from bazaar.migrate import backfill, create_index_concurrently, drop_index_concurrently

revision: str = "2602"
down_revision: str | Sequence[str] | None = "2601"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SLUGIFY = """
CREATE OR REPLACE FUNCTION slugify(p_text text)
RETURNS text
LANGUAGE sql
IMMUTABLE STRICT PARALLEL SAFE
RETURN trim(BOTH '-' FROM
            regexp_replace(lower(public.unaccent('public.unaccent'::regdictionary, p_text)),
                           '[^a-z0-9]+', '-', 'g'))
"""

SET_SLUG = """
CREATE OR REPLACE FUNCTION products_set_slug()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- The identity id is assigned before BEFORE triggers run, so it can make
    -- the slug unique without a lookup.
    IF NEW.slug IS NULL THEN
        NEW.slug := slugify(NEW.title) || '-' || NEW.id;
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    op.execute(SLUGIFY)
    op.execute(
        "COMMENT ON FUNCTION slugify(text) IS 'URL slug: unaccented, lower case, a-z0-9 and -'"
    )
    op.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS slug text")
    op.execute(SET_SLUG)
    op.execute(
        "CREATE OR REPLACE TRIGGER products_set_slug BEFORE INSERT ON products"
        " FOR EACH ROW EXECUTE FUNCTION products_set_slug()"
    )
    # session_replication_role is a superuser setting; since PostgreSQL 15 it
    # can be granted to the owner role instead (cluster-wide, idempotent).
    op.execute("RESET ROLE")
    op.execute("GRANT SET ON PARAMETER session_replication_role TO bazaar_owner")
    op.execute("SET ROLE bazaar_owner")

    backfill(
        "products",
        "slug = slugify(title) || '-' || id",
        "slug IS NULL",
        batch_size=10_000,
        skip_triggers=True,
    )
    create_index_concurrently("products_slug_key", "products", "slug", unique=True)


def downgrade() -> None:
    drop_index_concurrently("products_slug_key")
    op.execute("DROP TRIGGER products_set_slug ON products")
    op.execute("DROP FUNCTION products_set_slug()")
    op.execute("ALTER TABLE products DROP COLUMN slug")
    op.execute("DROP FUNCTION slugify(text)")
