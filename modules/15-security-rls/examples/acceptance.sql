-- Module 15 milestone: acceptance checks.
--
--   docker exec -i course-pg psql -U postgres -d bazaar -X < modules/15-security-rls/examples/acceptance.sql
--
-- Connects as each Bazaar role in turn (inside the container, local connections
-- need no password). Every check prints one line starting with PASS or FAIL.
-- Works on the small and the large dataset: expected values are computed first,
-- as postgres. Nothing is changed: writes run in transactions that roll back.
\set QUIET on
\pset tuples_only on
\pset format unaligned

-- Expected values, computed as the superuser.
SELECT count(*) AS exp_products_1 FROM bazaar.products WHERE seller_id = 1 \gset
SELECT count(*) AS exp_orders_1 FROM bazaar.orders WHERE seller_id = 1 \gset
SELECT min(id) AS other_product FROM bazaar.products WHERE seller_id <> 1 \gset

\echo '--- bazaar_seller'
\c - bazaar_seller
\set QUIET on
BEGIN;
SET LOCAL app.seller_id = '1';
SELECT CASE WHEN count(*) = :exp_products_1 AND count(*) FILTER (WHERE seller_id <> 1) = 0
            THEN 'PASS' ELSE 'FAIL' END || ' seller 1 sees exactly its ' || count(*) || ' products'
FROM bazaar.products;
SELECT CASE WHEN count(*) = :exp_orders_1 AND count(*) FILTER (WHERE seller_id <> 1) = 0
            THEN 'PASS' ELSE 'FAIL' END || ' seller 1 sees exactly its ' || count(*) || ' orders'
FROM bazaar.orders;
SELECT CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END
       || ' seller 1 sees no order_items of other sellers'
FROM bazaar.order_items AS oi
WHERE NOT EXISTS (SELECT 1 FROM bazaar.orders AS o WHERE o.id = oi.order_id);
WITH u AS (UPDATE bazaar.products SET price = price + 1 WHERE id = :other_product RETURNING 1)
SELECT CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END
       || ' seller 1 cannot update product ' || :other_product || ' of another seller (' || count(*) || ' rows)'
FROM u;
ROLLBACK;

BEGIN;
SET LOCAL app.seller_id = '1';
DO $$
BEGIN
    UPDATE bazaar.products SET seller_id = 2 WHERE seller_id = 1;
    RAISE NOTICE 'FAIL seller 1 moved its products to seller 2';
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'PASS seller 1 cannot hand its products to another seller (%)', SQLERRM;
END
$$;
ROLLBACK;

SELECT CASE WHEN (SELECT count(*) FROM bazaar.products) = 0
             AND (SELECT count(*) FROM bazaar.orders) = 0
            THEN 'PASS' ELSE 'FAIL' END || ' without app.seller_id the seller sees nothing';

\echo '--- bazaar_analyst'
\c - bazaar_analyst
\set QUIET on
DO $$
BEGIN
    PERFORM email FROM bazaar.users LIMIT 1;
    RAISE NOTICE 'FAIL the analyst can read users.email';
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'PASS the analyst cannot read users.email (%)', SQLERRM;
END
$$;
SELECT CASE WHEN count(*) > 0 THEN 'PASS' ELSE 'FAIL' END
       || ' the analyst can still count users (' || count(*) || ') and read their names'
FROM (SELECT id, full_name FROM bazaar.users) AS u;
SELECT CASE WHEN count(*) > 0 THEN 'PASS' ELSE 'FAIL' END
       || ' the analyst reads all orders (' || count(*) || ')'
FROM bazaar.orders;

\echo '--- bazaar_app'
\c - bazaar_app
\set QUIET on
BEGIN;
DO $$
BEGIN
    DROP TABLE bazaar.orders;
    RAISE NOTICE 'FAIL the app dropped a table';
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'PASS the app cannot DROP tables (%)', SQLERRM;
END
$$;
DO $$
BEGIN
    DELETE FROM bazaar.audit_log;
    RAISE NOTICE 'FAIL the app can delete audit history';
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'PASS the app cannot rewrite audit_log (%)', SQLERRM;
END
$$;
SELECT CASE WHEN count(*) > 0 THEN 'PASS' ELSE 'FAIL' END
       || ' the app sees all products (' || count(*) || ')'
FROM bazaar.products;
ROLLBACK;

\echo '--- ownership'
\c - postgres
\set QUIET on
SELECT CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END
       || ' every table, view and function in bazaar is owned by bazaar_owner ('
       || count(*) || ' exceptions)'
FROM (
    SELECT relowner AS owner FROM pg_class
    WHERE relnamespace = 'bazaar'::regnamespace AND relkind IN ('r', 'p', 'v', 'm', 'S')
    UNION ALL
    SELECT proowner FROM pg_proc WHERE pronamespace = 'bazaar'::regnamespace
) AS o
WHERE owner <> 'bazaar_owner'::regrole;
