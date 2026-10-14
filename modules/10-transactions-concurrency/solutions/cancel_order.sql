-- =============================================================================
-- cancel_order.sql  --  Module 10 milestone extension (reference solution)
-- -----------------------------------------------------------------------------
-- place_order() reserves stock, but nothing gives it back when an order is
-- cancelled. cancel_order() closes that gap:
--
--   * Only orders that still hold a reservation can be cancelled: 'pending'
--     and 'paid' (the reserved column counts exactly the units of those orders).
--     A paid order would also need a refund payment; that is out of scope here.
--   * Shipped, delivered and refunded orders raise BZ005.
--   * A missing order raises P0002 (no_data_found).
--   * Cancelling an already cancelled order is a no-op that returns false, so a
--     client that retries after a lost network reply gets a harmless answer
--     (the call is idempotent).
--
-- Concurrency design:
--   1. The order row is locked first (FOR NO KEY UPDATE: we change the status,
--      not the key, so FK checks from concurrent inserts are not blocked).
--      Two concurrent cancels of the same order serialize here; the second
--      one then sees status 'cancelled' (READ COMMITTED re-reads the locked
--      row's newest version) and returns false. Without this lock, both could
--      release the reservation: a double release.
--   2. The inventory rows are locked in product_id order, the same global
--      order place_order() uses, so a cancel and a checkout touching the same
--      products can never wait for each other in a cycle.
-- Works under READ COMMITTED (the default). Under REPEATABLE READ a concurrent
-- cancel makes the second caller fail with 40001 instead, which a retry turns
-- into the same "false" answer.
-- =============================================================================

SET search_path = bazaar, public;

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
    'Cancels a pending/paid order and releases its stock reservation. Returns false if already cancelled. Errors: BZ005 wrong status, P0002 missing order.';
