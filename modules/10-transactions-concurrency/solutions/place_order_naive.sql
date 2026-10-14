-- =============================================================================
-- place_order_naive.sql  --  a deliberately BROKEN place_order(), for testing the test
-- -----------------------------------------------------------------------------
-- It reserves stock line by line, in the order the client listed the items.
-- Each UPDATE is atomic and conditional, so it never oversells, but two
-- checkouts listing the same products in opposite orders can deadlock.
-- Load it on a throwaway database (it replaces place_order), run the
-- acceptance bench, and watch the "no deadlocks" check FAIL. Then restore:
--   ./project/bin/reset.sh 11      (or reload your own implementation)
-- =============================================================================
SET search_path = bazaar, public;

CREATE OR REPLACE FUNCTION place_order(
    p_user_id             bigint,
    p_items               jsonb,
    p_shipping_address_id bigint DEFAULT NULL
)
RETURNS SETOF bigint
LANGUAGE plpgsql
AS $$
DECLARE
    e         jsonb;
    v_seller  bigint;
    v_order   bigint;
BEGIN
    -- reserve, line by line, in request order (THE BUG: no global lock order)
    FOR e IN SELECT * FROM jsonb_array_elements(p_items) LOOP
        UPDATE inventory
        SET reserved = reserved + (e ->> 'quantity')::int
        WHERE product_id = (e ->> 'product_id')::bigint
          AND quantity - reserved >= (e ->> 'quantity')::int;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'insufficient stock for product %', e ->> 'product_id'
                USING ERRCODE = 'BZ001';
        END IF;
        PERFORM pg_sleep(0.001);   -- a little "work" between rows widens the window
    END LOOP;

    FOR v_seller IN
        SELECT DISTINCT p.seller_id
        FROM jsonb_array_elements(p_items) AS x
        JOIN products p ON p.id = (x ->> 'product_id')::bigint
        ORDER BY 1
    LOOP
        INSERT INTO orders (user_id, seller_id, shipping_address_id)
        VALUES (p_user_id, v_seller, p_shipping_address_id)
        RETURNING id INTO v_order;

        INSERT INTO order_items (order_id, product_id, quantity, unit_price)
        SELECT v_order, p.id, (x ->> 'quantity')::int, p.price
        FROM jsonb_array_elements(p_items) AS x
        JOIN products p ON p.id = (x ->> 'product_id')::bigint
        WHERE p.seller_id = v_seller;

        RETURN NEXT v_order;
    END LOOP;
END;
$$;
