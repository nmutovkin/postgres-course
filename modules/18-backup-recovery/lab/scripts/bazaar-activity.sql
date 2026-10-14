-- A burst of realistic Bazaar write activity for the Module 18 lab:
-- 200 checkouts through place_order() (module 10), then some of the new
-- orders are paid and shipped. Run it as often as you like:
--
--   docker exec -i m18-primary psql -U postgres -d bazaar < scripts/bazaar-activity.sql
--
-- Each checkout is its own transaction (COMMIT inside a DO block needs
-- PostgreSQL 11+), so the WAL contains many small commits spread over time,
-- like a real shop, rather than one giant transaction.
SET search_path = bazaar, public;

DO $$
DECLARE
    v_user    bigint;
    v_product bigint;
    v_placed  int := 0;
BEGIN
    FOR i IN 1..200 LOOP
        SELECT id INTO v_user
        FROM users
        WHERE is_active
        OFFSET floor(random() * 50) LIMIT 1;

        SELECT p.id INTO v_product
        FROM products AS p
        JOIN inventory AS inv ON inv.product_id = p.id
        WHERE p.status = 'active'
          AND inv.quantity - inv.reserved >= 1
        OFFSET floor(random() * 50) LIMIT 1;

        IF v_user IS NOT NULL AND v_product IS NOT NULL THEN
            PERFORM place_order(v_user, jsonb_build_array(
                jsonb_build_object('product_id', v_product, 'quantity', 1)));
            v_placed := v_placed + 1;
        END IF;
        COMMIT;
    END LOOP;
    RAISE NOTICE 'placed % orders', v_placed;
END
$$;

-- Half of today's pending orders get paid, a fifth of those ship.
UPDATE orders
SET status = 'paid', paid_at = now()
WHERE status = 'pending'
  AND created_at > now() - interval '1 hour'
  AND id % 2 = 0;

UPDATE orders
SET status = 'shipped', shipped_at = now()
WHERE status = 'paid'
  AND paid_at > now() - interval '1 hour'
  AND id % 5 = 0;
