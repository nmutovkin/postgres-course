-- =============================================================================
-- acceptance_cancel.sql  --  milestone extension: single-session checks for cancel_order()
--   ./project/bin/psql.sh < modules/10-transactions-concurrency/solutions/acceptance_cancel.sql
-- Runs in a transaction that is rolled back. Every line must start with PASS.
-- =============================================================================
SET search_path = bazaar, public;
\set ON_ERROR_STOP on
BEGIN;

CREATE FUNCTION pg_temp.sqlstate_of(p_sql text) RETURNS text LANGUAGE plpgsql AS $$
BEGIN
    EXECUTE p_sql;
    RETURN '00000';
EXCEPTION WHEN OTHERS THEN
    RETURN SQLSTATE;
END;
$$;
CREATE FUNCTION pg_temp.check(p_name text, p_ok boolean) RETURNS text LANGUAGE sql AS
$$ SELECT CASE WHEN p_ok THEN 'PASS  ' ELSE 'FAIL  ' END || p_name $$;

\pset tuples_only on
\pset format unaligned

-- Order 115 is pending. Remember its items and the stock before cancelling.
CREATE TEMP TABLE before AS
SELECT oi.product_id, oi.quantity, i.reserved
FROM order_items oi JOIN inventory i USING (product_id)
WHERE oi.order_id = 115;

CREATE TEMP TABLE first_call AS SELECT cancel_order(115) AS result;

SELECT pg_temp.check('cancelling a pending order returns true',
    (SELECT result FROM first_call));
SELECT pg_temp.check('the order is now cancelled',
    (SELECT status = 'cancelled' FROM orders WHERE id = 115));
SELECT pg_temp.check('its reserved units were released',
    NOT EXISTS (SELECT 1 FROM before b JOIN inventory i USING (product_id)
                WHERE i.reserved <> b.reserved - b.quantity));
SELECT pg_temp.check('cancelling it again returns false and changes nothing',
    cancel_order(115) = false);
SELECT pg_temp.check('a paid order (110) can be cancelled',
    cancel_order(110));
SELECT pg_temp.check('a delivered order (1) raises BZ005',
    pg_temp.sqlstate_of('SELECT cancel_order(1)') = 'BZ005');
SELECT pg_temp.check('a shipped order (105) raises BZ005',
    pg_temp.sqlstate_of('SELECT cancel_order(105)') = 'BZ005');
SELECT pg_temp.check('a missing order raises P0002',
    pg_temp.sqlstate_of('SELECT cancel_order(999999)') = 'P0002');
SELECT pg_temp.check('reserved equals the units of pending and paid orders, for every product',
    NOT EXISTS (
        SELECT 1
        FROM inventory i
        LEFT JOIN (SELECT oi.product_id, sum(oi.quantity) AS units
                   FROM order_items oi JOIN orders o ON o.id = oi.order_id
                   WHERE o.status IN ('pending', 'paid')
                   GROUP BY oi.product_id) u USING (product_id)
        WHERE i.reserved <> coalesce(u.units, 0)));

\pset tuples_only off
\pset format aligned
ROLLBACK;
