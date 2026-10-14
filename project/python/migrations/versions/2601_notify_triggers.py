"""notify triggers: jobs_ready and new_order (was project/python/sql/2301_notify.sql)

Revision ID: 2601
Revises: 2600
Create Date: 2026-10-14

Module 23 applied these by hand with psql. From module 26 on, every schema
change is a revision, so the file becomes this migration. CREATE OR REPLACE
makes it safe on databases where a learner already applied 2301_notify.sql
by hand: stamping at 2600 and upgrading converges on the same objects.

Functions and triggers are invisible to autogenerate, so this revision is
written by hand with op.execute(). The SQL is copied in, not read from the
.sql file: a revision must not change when someone edits a file later.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "2601"
down_revision: str | Sequence[str] | None = "2600"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NOTIFY_JOBS_READY = """
CREATE OR REPLACE FUNCTION notify_jobs_ready()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_notify('jobs_ready', q.queue)
    FROM (SELECT DISTINCT queue FROM new_jobs) AS q;
    RETURN NULL;
END;
$$
"""

NOTIFY_NEW_ORDER = """
CREATE OR REPLACE FUNCTION notify_new_order()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_notify('new_order', json_build_object(
        'order_id',  NEW.id,
        'seller_id', NEW.seller_id,
        'user_id',   NEW.user_id)::text);
    RETURN NULL;
END;
$$
"""


def upgrade() -> None:
    # Function bodies are stored verbatim (indentation included), so the SQL
    # sits flush left: the same text as 2301_notify.sql produced.
    op.execute(NOTIFY_JOBS_READY)
    op.execute(
        "CREATE OR REPLACE TRIGGER jobs_notify_ready"
        " AFTER INSERT ON jobs"
        " REFERENCING NEW TABLE AS new_jobs"
        " FOR EACH STATEMENT EXECUTE FUNCTION notify_jobs_ready()"
    )
    op.execute(NOTIFY_NEW_ORDER)
    op.execute(
        "CREATE OR REPLACE TRIGGER orders_notify_new"
        " AFTER INSERT ON orders"
        " FOR EACH ROW EXECUTE FUNCTION notify_new_order()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS orders_notify_new ON orders")
    op.execute("DROP FUNCTION IF EXISTS notify_new_order()")
    op.execute("DROP TRIGGER IF EXISTS jobs_notify_ready ON jobs")
    op.execute("DROP FUNCTION IF EXISTS notify_jobs_ready()")
