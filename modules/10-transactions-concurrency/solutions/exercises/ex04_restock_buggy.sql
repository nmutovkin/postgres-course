-- Exercise 4: the buggy restock() function (given to the learner)
SET search_path = bazaar, public;

CREATE OR REPLACE FUNCTION restock(p_product_id bigint, p_units int)
RETURNS int
LANGUAGE plpgsql
AS $$
DECLARE
    v_qty int;
BEGIN
    SELECT quantity INTO v_qty
    FROM inventory
    WHERE product_id = p_product_id;

    -- (imagine a call to the warehouse system here)
    PERFORM pg_sleep(0.001);

    UPDATE inventory
    SET quantity = v_qty + p_units
    WHERE product_id = p_product_id;

    RETURN v_qty + p_units;
END;
$$;
