-- Exercise 8: a "support" role. Reads orders of every seller; changes an order's
-- status only through one function that enforces the allowed transitions.
BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bazaar_support') THEN
        CREATE ROLE bazaar_support LOGIN PASSWORD 'support';   -- course-only password
    END IF;
END
$$;
ALTER ROLE bazaar_support CONNECTION LIMIT 10;
ALTER ROLE bazaar_support IN DATABASE bazaar SET statement_timeout = '30s';

GRANT CONNECT ON DATABASE bazaar TO bazaar_support;
GRANT USAGE ON SCHEMA bazaar TO bazaar_support;

-- Read access: the order, its lines, payments, and who the customer is (name only).
GRANT SELECT ON bazaar.orders, bazaar.order_items, bazaar.payments, bazaar.sellers
    TO bazaar_support;
GRANT SELECT (id, full_name, is_active) ON bazaar.users TO bazaar_support;

-- orders and order_items have RLS enabled: without a policy for this role the
-- grants above would return zero rows.
CREATE POLICY orders_support_read ON bazaar.orders
    FOR SELECT TO bazaar_support USING (true);
CREATE POLICY order_items_support_read ON bazaar.order_items
    FOR SELECT TO bazaar_support USING (true);

-- The only way to write: a SECURITY DEFINER function owned by bazaar_owner.
SET LOCAL ROLE bazaar_owner;

CREATE FUNCTION bazaar.support_set_order_status(
    p_order_id   bigint,
    p_new_status bazaar.order_status,
    p_reason     text
)
RETURNS bazaar.order_status
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, bazaar, pg_temp
AS $$
DECLARE
    v_old bazaar.order_status;
BEGIN
    IF p_reason IS NULL OR length(trim(p_reason)) < 5 THEN
        RAISE EXCEPTION 'a reason of at least 5 characters is required'
            USING ERRCODE = '22023';
    END IF;

    SELECT status INTO v_old
    FROM bazaar.orders
    WHERE id = p_order_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'order % does not exist', p_order_id USING ERRCODE = 'P0002';
    END IF;

    -- The transitions support may make; everything else belongs to the seller or the app.
    IF NOT ((v_old, p_new_status) IN (
                ('pending'::bazaar.order_status,   'cancelled'::bazaar.order_status),
                ('paid'::bazaar.order_status,      'cancelled'::bazaar.order_status),
                ('delivered'::bazaar.order_status, 'refunded'::bazaar.order_status))) THEN
        RAISE EXCEPTION 'support may not change order % from % to %',
                        p_order_id, v_old, p_new_status
            USING ERRCODE = '42501';
    END IF;

    UPDATE bazaar.orders SET status = p_new_status WHERE id = p_order_id;

    -- The orders audit trigger records the change with changed_by = session_user
    -- (bazaar_support); keep the reason next to it.
    INSERT INTO bazaar.audit_log (table_name, operation, row_pk, new_row)
    VALUES ('orders', 'UPDATE', jsonb_build_object('id', p_order_id),
            jsonb_build_object('support_reason', p_reason));

    RETURN p_new_status;
END
$$;

REVOKE EXECUTE ON FUNCTION bazaar.support_set_order_status(bigint, bazaar.order_status, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bazaar.support_set_order_status(bigint, bazaar.order_status, text) TO bazaar_support;

COMMIT;
