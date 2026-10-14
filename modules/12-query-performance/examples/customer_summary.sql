-- =============================================================================
-- customer_summary.sql  --  Module 12, exercise 13
-- -----------------------------------------------------------------------------
-- A CRM helper function with a performance problem hidden inside it.
-- EXPLAIN on a call shows only "Function Scan"; find the slow statement.
--   ./project/bin/psql.sh bazaar < modules/12-query-performance/examples/customer_summary.sql
-- =============================================================================
SET search_path = bazaar, public;

CREATE OR REPLACE FUNCTION customer_summary(p_user bigint)
RETURNS TABLE (orders_count bigint, total_spent numeric, favourite_category text, last_order_at timestamptz)
LANGUAGE plpgsql
STABLE
AS $$
BEGIN
    -- How many orders, and how much money?
    SELECT count(*), coalesce(sum(o.total), 0)
    INTO orders_count, total_spent
    FROM orders AS o
    WHERE o.user_id = p_user
      AND o.status IN ('paid', 'shipped', 'delivered');

    -- Which category does the customer buy most often?
    SELECT c.name
    INTO favourite_category
    FROM order_items AS oi
    JOIN orders AS o ON o.id = oi.order_id
    JOIN products AS p ON p.id = oi.product_id
    JOIN categories AS c ON c.id = p.category_id
    WHERE o.user_id::text = p_user::text
    GROUP BY c.name
    ORDER BY count(*) DESC, c.name
    LIMIT 1;

    -- When did they last order?
    SELECT max(o.created_at)
    INTO last_order_at
    FROM orders AS o
    WHERE o.user_id = p_user;

    RETURN NEXT;
END;
$$;
