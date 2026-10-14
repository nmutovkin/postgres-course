"""transactional outbox: outbox + processed_messages (module 27)

Revision ID: 2701
Revises: 2604
Create Date: 2026-10-14

outbox
    One row per domain event ("order.placed"), INSERTed in the SAME transaction
    as the change it describes, so the event exists if and only if the change
    committed. A relay (bazaar.outbox) reads unpublished rows in id order with
    FOR UPDATE SKIP LOCKED, publishes them, and sets published_at.
    A statement-level trigger sends NOTIFY outbox (no payload: a wake-up call)
    so the relay does not have to poll aggressively.

processed_messages
    The consumer side ("inbox"): a consumer records (consumer, message_id) in
    the same transaction as its effect. A redelivered message hits the primary
    key and is skipped: at-least-once delivery + idempotent consumer =
    an exactly-once effect.

New, empty tables: plain CREATE TABLE / CREATE INDEX are fine here (nobody can
be blocked by locks on a table nobody uses yet).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "2701"
down_revision: str | Sequence[str] | None = "2604"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOTIFY_OUTBOX = """
CREATE OR REPLACE FUNCTION notify_outbox()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_notify('outbox', '');
    RETURN NULL;
END;
$$
"""


def upgrade() -> None:
    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("topic", sa.Text, nullable=False),
        sa.Column("aggregate_type", sa.Text, nullable=False),
        sa.Column("aggregate_id", sa.Text, nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("headers", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text),
        sa.CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
    )
    # The relay's query: WHERE published_at IS NULL ORDER BY id. A partial index
    # stays tiny however many published rows the table keeps.
    op.create_index(
        "outbox_unpublished_idx",
        "outbox",
        ["id"],
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.execute(NOTIFY_OUTBOX)
    op.execute(
        "CREATE TRIGGER outbox_notify AFTER INSERT ON outbox"
        " FOR EACH STATEMENT EXECUTE FUNCTION notify_outbox()"
    )
    op.create_table(
        "processed_messages",
        sa.Column("consumer", sa.Text, primary_key=True),
        sa.Column("message_id", sa.BigInteger, primary_key=True),
        sa.Column(
            "processed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("processed_messages")
    op.execute("DROP TRIGGER outbox_notify ON outbox")
    op.execute("DROP FUNCTION notify_outbox()")
    op.drop_table("outbox")
