"""payments: (provider, provider_ref) unique, so provider webhooks can upsert

Revision ID: 2605
Revises: 2801
Create Date: 2026-10-14

Module 26, exercise 7.

1. Duplicates first. A unique index build fails on the first duplicate, and a
   failed CONCURRENT build leaves an INVALID index behind (maintained on every
   write, used by no query). Payments are money: duplicates are not deleted
   but moved to payments_quarantine for a human to look at.
2. CREATE UNIQUE INDEX CONCURRENTLY (bazaar.migrate drops an INVALID leftover
   of an earlier attempt first).
3. ADD CONSTRAINT ... UNIQUE USING INDEX: a constraint backed by the index,
   with a brief ACCESS EXCLUSIVE lock and no scan.

EX07_SKIP_CLEANUP=1 skips step 1 (to see the failure in the exercise).
"""

import os
from collections.abc import Sequence

from alembic import op

from bazaar.migrate import create_index_concurrently

revision: str = "2605"
down_revision: str | Sequence[str] | None = "2801"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if os.environ.get("EX07_SKIP_CLEANUP") != "1":
        op.execute(
            "CREATE TABLE IF NOT EXISTS payments_quarantine"
            " (LIKE payments INCLUDING DEFAULTS, quarantined_at timestamptz NOT NULL DEFAULT now())"
        )
        # Keep the oldest payment of each (provider, provider_ref); move the rest.
        op.execute(
            """
            WITH dup AS (
                DELETE FROM payments p
                USING payments keep
                WHERE keep.provider = p.provider
                  AND keep.provider_ref = p.provider_ref
                  AND keep.id < p.id
                RETURNING p.*
            )
            INSERT INTO payments_quarantine
            SELECT dup.*, now() FROM dup
            """
        )
    create_index_concurrently(
        "payments_provider_provider_ref_key", "payments", "provider, provider_ref", unique=True
    )
    op.execute(
        "ALTER TABLE payments ADD CONSTRAINT payments_provider_provider_ref_key"
        " UNIQUE USING INDEX payments_provider_provider_ref_key"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE payments DROP CONSTRAINT payments_provider_provider_ref_key")
    # payments_quarantine stays: it holds data (drop it by hand once reviewed).
