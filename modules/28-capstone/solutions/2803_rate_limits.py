"""rate_limits + take_token(): a token bucket in PostgreSQL

Revision ID: 2803
Revises: 2802
Create Date: 2026-10-14

Module 28, exercise 5. One row per bucket ("checkout:user:42"): the tokens
left and when they were last counted. take_token() refills the bucket for the
time that passed (rate tokens per second, at most burst), takes `cost` tokens
if there are enough and returns 0, or returns the seconds until there will be.
The row lock (FOR UPDATE) makes concurrent requests of one key take turns, so
two of them can never spend the same token; different keys never wait for
each other.

UNLOGGED: no WAL, so cheaper writes, but the table is emptied after a crash
and is not replicated. For a rate limiter that is the right trade: forgetting
the counts after a crash is harmless. fillfactor 50 leaves room on each page so
that the constant UPDATEs stay HOT (module 16): no index maintenance, less bloat.
Old buckets are deleted by purge_rate_limits() (run it from a job, hourly).
"""

from collections.abc import Sequence

from alembic import op

from bazaar.migrate import execute_script

revision: str = "2803"
down_revision: str | Sequence[str] | None = "2802"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = """
CREATE UNLOGGED TABLE IF NOT EXISTS rate_limits (
    key         text PRIMARY KEY,
    tokens      double precision NOT NULL,
    updated_at  timestamptz NOT NULL
) WITH (fillfactor = 50);

CREATE OR REPLACE FUNCTION take_token(
    p_key   text,
    p_rate  double precision,     -- tokens added per second
    p_burst integer,              -- bucket size
    p_cost  double precision DEFAULT 1
)
RETURNS double precision          -- 0: allowed; > 0: seconds to wait
LANGUAGE plpgsql
AS $$
DECLARE
    v_tokens  double precision;
    v_updated timestamptz;
    v_now     timestamptz;
BEGIN
    INSERT INTO rate_limits (key, tokens, updated_at)
    VALUES (p_key, p_burst, clock_timestamp())
    ON CONFLICT (key) DO NOTHING;

    SELECT tokens, updated_at INTO v_tokens, v_updated
    FROM rate_limits
    WHERE key = p_key
    FOR UPDATE;

    -- Read the clock AFTER the lock: time spent waiting for it counts as refill.
    v_now := clock_timestamp();
    v_tokens := least(p_burst, v_tokens + p_rate * extract(epoch FROM v_now - v_updated));

    IF v_tokens >= p_cost THEN
        UPDATE rate_limits SET tokens = v_tokens - p_cost, updated_at = v_now WHERE key = p_key;
        RETURN 0;
    END IF;
    UPDATE rate_limits SET tokens = v_tokens, updated_at = v_now WHERE key = p_key;
    RETURN (p_cost - v_tokens) / p_rate;
END;
$$;

CREATE OR REPLACE FUNCTION purge_rate_limits(p_idle interval DEFAULT '1 hour')
RETURNS bigint
LANGUAGE sql
AS $$
    WITH gone AS (DELETE FROM rate_limits WHERE updated_at < now() - p_idle RETURNING 1)
    SELECT count(*) FROM gone;
$$;
"""


def upgrade() -> None:
    execute_script(SCHEMA)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS purge_rate_limits(interval)")
    op.execute(
        "DROP FUNCTION IF EXISTS take_token(text, double precision, integer, double precision)"
    )
    op.execute("DROP TABLE IF EXISTS rate_limits")
