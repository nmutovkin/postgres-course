-- Exercise 6: the naive version, for comparison (deadlocks under load)
SET search_path = bazaar, public;

CREATE OR REPLACE FUNCTION transfer_stock(p_from bigint, p_to bigint, p_units int)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE inventory SET quantity = quantity - p_units
    WHERE product_id = p_from AND quantity - reserved >= p_units;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'insufficient stock for product %', p_from USING ERRCODE = 'BZ001';
    END IF;
    PERFORM pg_sleep(0.001);   -- any work between the two updates widens the window
    UPDATE inventory SET quantity = quantity + p_units WHERE product_id = p_to;
END;
$$;
