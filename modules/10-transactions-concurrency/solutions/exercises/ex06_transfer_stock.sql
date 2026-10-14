-- Exercise 6: move stock between two products of the same seller, safely
SET search_path = bazaar, public;

CREATE OR REPLACE FUNCTION transfer_stock(p_from bigint, p_to bigint, p_units int)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    r           record;
    v_found     int := 0;
    v_sellers   int;
BEGIN
    IF p_units IS NULL OR p_units <= 0 OR p_from IS NULL OR p_to IS NULL OR p_from = p_to THEN
        RAISE EXCEPTION 'need two different products and a positive number of units'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Lock both inventory rows in ascending product_id order, whatever the
    -- direction of the transfer: transfer(1,2) and transfer(2,1) both lock 1 first.
    FOR r IN
        SELECT i.product_id, i.quantity, i.reserved
        FROM inventory i
        WHERE i.product_id IN (p_from, p_to)
        ORDER BY i.product_id
        FOR NO KEY UPDATE
    LOOP
        v_found := v_found + 1;
        IF r.product_id = p_from AND r.quantity - r.reserved < p_units THEN
            RAISE EXCEPTION 'insufficient stock for product %', p_from
                USING ERRCODE = 'BZ001',
                      DETAIL  = format('requested %s, available %s', p_units, r.quantity - r.reserved);
        END IF;
    END LOOP;

    IF v_found < 2 THEN
        RAISE EXCEPTION 'product % or % has no inventory', p_from, p_to
            USING ERRCODE = 'BZ002';
    END IF;

    SELECT count(DISTINCT seller_id) INTO v_sellers
    FROM products
    WHERE id IN (p_from, p_to);
    IF v_sellers <> 1 THEN
        RAISE EXCEPTION 'products % and % belong to different sellers', p_from, p_to
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Both rows are locked by us: these updates never wait.
    UPDATE inventory SET quantity = quantity - p_units WHERE product_id = p_from;
    UPDATE inventory SET quantity = quantity + p_units WHERE product_id = p_to;
END;
$$;
