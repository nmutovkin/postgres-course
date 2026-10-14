"""cancel_order(): module 10's reference solution, now part of the schema

Revision ID: 2802
Revises: 2801
Create Date: 2026-10-14

Module 28, exercise 3. place_order() reserves stock; cancel_order() gives it
back (pending/paid orders only), idempotently: cancelling a cancelled order
returns false. Errors: BZ005 (wrong status), P0002 (no such order).
The body is copied verbatim from modules/10-transactions-concurrency/
solutions/cancel_order.sql: a revision must not import or read other files.

Copy this file to project/python/migrations/versions/ and run
`alembic upgrade head`.
"""

from collections.abc import Sequence

from alembic import op

from bazaar.migrate import execute_script

revision: str = "2802"
down_revision: str | Sequence[str] | None = "2801"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CANCEL_ORDER = """
CREATE OR REPLACE FUNCTION cancel_order(p_order_id bigint)
RETURNS boolean
LANGUAGE plpgsql
AS $$
DECLARE
    v_status order_status;
BEGIN
    SELECT status INTO v_status
    FROM orders
    WHERE id = p_order_id
    FOR NO KEY UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'order % does not exist', p_order_id
            USING ERRCODE = 'no_data_found';
    END IF;

    IF v_status = 'cancelled' THEN
        RETURN false;                       -- already done: idempotent no-op
    END IF;

    IF v_status NOT IN ('pending', 'paid') THEN
        RAISE EXCEPTION 'order % cannot be cancelled in status %', p_order_id, v_status
            USING ERRCODE = 'BZ005';
    END IF;

    -- Lock the inventory rows in the global order (product_id), then release.
    PERFORM 1
    FROM inventory i
    WHERE i.product_id IN (SELECT oi.product_id FROM order_items oi WHERE oi.order_id = p_order_id)
    ORDER BY i.product_id
    FOR NO KEY UPDATE;

    UPDATE inventory i
    SET reserved = i.reserved - oi.quantity
    FROM order_items oi
    WHERE oi.order_id = p_order_id
      AND i.product_id = oi.product_id;

    UPDATE orders
    SET status = 'cancelled'
    WHERE id = p_order_id;

    RETURN true;
END;
$$;

COMMENT ON FUNCTION cancel_order(bigint) IS
    'Cancels a pending/paid order and releases its stock reservation. '
    'Returns false if already cancelled. Errors: BZ005 wrong status, P0002 missing order.';
"""


def upgrade() -> None:
    execute_script(CANCEL_ORDER)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS cancel_order(bigint)")
