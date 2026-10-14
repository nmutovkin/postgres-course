-- Exercise 4: two correct versions of restock()
SET search_path = bazaar, public;

-- Fix 1: let the database do the arithmetic (atomic UPDATE).
CREATE OR REPLACE FUNCTION restock(p_product_id bigint, p_units int)
RETURNS int
LANGUAGE plpgsql
AS $$
DECLARE
    v_new int;
BEGIN
    PERFORM pg_sleep(0.001);                 -- the "warehouse call", now outside any lock
    UPDATE inventory
    SET quantity = quantity + p_units
    WHERE product_id = p_product_id
    RETURNING quantity INTO v_new;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'product % has no inventory row', p_product_id
            USING ERRCODE = 'BZ002';
    END IF;
    RETURN v_new;
END;
$$;

-- Fix 2 (pessimistic): lock the row before reading it.
CREATE OR REPLACE FUNCTION restock_locking(p_product_id bigint, p_units int)
RETURNS int
LANGUAGE plpgsql
AS $$
DECLARE
    v_qty int;
BEGIN
    SELECT quantity INTO v_qty
    FROM inventory
    WHERE product_id = p_product_id
    FOR NO KEY UPDATE;                       -- concurrent callers queue here

    PERFORM pg_sleep(0.001);                 -- the lock is held during this "call"

    UPDATE inventory
    SET quantity = v_qty + p_units
    WHERE product_id = p_product_id;

    RETURN v_qty + p_units;
END;
$$;
