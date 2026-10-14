-- Exercise 10: warehouse pickers claim paid orders with SKIP LOCKED and consume stock
SET search_path = bazaar, public;

CREATE OR REPLACE FUNCTION ship_next_order(p_picker text)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_order_id bigint;
BEGIN
    -- 1. Claim the oldest paid order nobody else is shipping right now.
    UPDATE orders
    SET status     = 'shipped',
        shipped_at = now()
    WHERE id = (
        SELECT o.id
        FROM orders o
        WHERE o.status = 'paid'
        ORDER BY o.created_at, o.id
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING id INTO v_order_id;

    IF v_order_id IS NULL THEN
        RETURN NULL;                           -- nothing to ship
    END IF;

    -- 2. Lock its inventory rows in the global order (product_id) ...
    PERFORM 1
    FROM inventory i
    WHERE i.product_id IN (SELECT oi.product_id FROM order_items oi WHERE oi.order_id = v_order_id)
    ORDER BY i.product_id
    FOR NO KEY UPDATE;

    -- 3. ... and turn the reservation into a real stock movement.
    UPDATE inventory i
    SET quantity = i.quantity - oi.quantity,
        reserved = i.reserved - oi.quantity
    FROM order_items oi
    WHERE oi.order_id = v_order_id
      AND i.product_id = oi.product_id;

    RAISE NOTICE 'picker % ships order %', p_picker, v_order_id;
    RETURN v_order_id;
END;
$$;
