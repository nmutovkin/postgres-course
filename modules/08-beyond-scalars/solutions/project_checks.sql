-- Acceptance checks for the module 08 milestone (Bazaar schema v3).
-- Run after applying your migration and seed on top of ./project/bin/reset.sh 08:
--   docker exec -i course-pg psql -X -U postgres -d bazaar < modules/08-beyond-scalars/solutions/project_checks.sql
-- Every check that modifies data runs inside a transaction that is rolled back.

\echo '1. enum types and their order'
SELECT enum_range(NULL::order_status)   AS order_status,
       enum_range(NULL::product_status) AS product_status,
       enum_range(NULL::payment_status) AS payment_status;

\echo '2. column types'
SELECT table_name, column_name, coalesce(domain_name, udt_name) AS type, column_default
FROM information_schema.columns
WHERE table_schema = 'bazaar'
  AND (table_name, column_name) IN (('orders', 'status'), ('products', 'status'), ('payments', 'status'),
                                    ('users', 'email'), ('products', 'attributes'), ('products', 'tags'))
ORDER BY table_name, column_name;

\echo '3. no leftover status CHECK constraints (expect 0 rows)'
SELECT conrelid::regclass, conname
FROM pg_constraint
WHERE contype = 'c' AND pg_get_constraintdef(oid) LIKE '%status%';

\echo '4. data survived the conversion'
SELECT (SELECT count(*) FROM orders)                            AS orders,
       (SELECT count(*) FROM orders WHERE status = 'delivered') AS delivered,
       (SELECT count(*) FROM products WHERE status = 'active')  AS active_products,
       (SELECT count(*) FROM payments WHERE status = 'failed')  AS failed_payments;

\echo '5. e-mail domain and case-insensitive uniqueness (expect two errors)'
BEGIN;
UPDATE users SET email = 'not-an-email' WHERE id = 7;
ROLLBACK;
BEGIN;
INSERT INTO users (email, full_name) VALUES ('zoe.martin@example.com', 'Zoe Duplicate');
ROLLBACK;

\echo '6. attributes and tags'
SELECT count(*) FILTER (WHERE attributes <> '{}')                          AS with_attributes,
       count(*) FILTER (WHERE cardinality(tags) > 0)                       AS with_tags,
       count(*) FILTER (WHERE attributes @> '{"storage": {"type": "ssd"}}') AS ssd_laptops,
       count(*) FILTER (WHERE attributes @? '$.ram_gb ? (@ >= 16)')         AS ram_16_plus,
       count(*) FILTER (WHERE tags @> '{bestseller}')                      AS bestsellers
FROM products;

BEGIN;
UPDATE products SET attributes = '[]' WHERE id = 1;        -- expect: violates a CHECK constraint
ROLLBACK;

\echo '7. promotions and the exclusion constraint'
SELECT count(*) AS promotions,
       count(*) FILTER (WHERE upper_inf(active_during)) AS open_ended
FROM promotions;

BEGIN;
-- adjacent to promotion 3 of product 10: must succeed
INSERT INTO promotions (product_id, discount_pct, active_during)
VALUES (10, 10, '[2025-06-15 00:00+00, 2025-06-22 00:00+00)');
ROLLBACK;

BEGIN;
-- overlaps promotion 3: must fail with the exclusion constraint
INSERT INTO promotions (product_id, discount_pct, active_during)
VALUES (10, 10, '[2025-06-10 00:00+00, 2025-06-20 00:00+00)');
ROLLBACK;

BEGIN;
-- empty range: must fail
INSERT INTO promotions (product_id, discount_pct, active_during)
VALUES (11, 10, 'empty');
ROLLBACK;

BEGIN;
-- 0 % discount: must fail
INSERT INTO promotions (product_id, discount_pct, active_during)
VALUES (11, 0, '[2025-07-01 00:00+00, 2025-07-02 00:00+00)');
ROLLBACK;
