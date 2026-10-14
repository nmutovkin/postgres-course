-- =============================================================================
-- audit_log_seed.sql  --  Module 14 milestone, part 3 (input data)
-- -----------------------------------------------------------------------------
-- The large dataset leaves audit_log empty (1101_seed_large.sql loads its data
-- with the audit triggers disabled). To practise migrating a big live table, this
-- script fills it with 1,500,000 synthetic but realistic-looking audit rows:
-- price changes and status changes of products, status changes of orders,
-- spread over 2024-01-01 .. 2025-12-31 with growing volume, ids in time order.
-- Deterministic (no random()). Takes about 15 s; about 400 MB on disk.
--
--   docker exec -i course-pg psql -U postgres -d bazaar < modules/14-partitioning/solutions/audit_log_seed.sql
-- =============================================================================

SET search_path = bazaar, public;
SET synchronous_commit = off;

BEGIN;

-- Start from an empty table and identity 1, so that every run gives the same ids.
TRUNCATE audit_log RESTART IDENTITY;

WITH g AS (
    SELECT n,
           -- sqrt() makes later months busier, like a growing business
           timestamptz '2024-01-01 00:00+00'
             + (interval '731 days') * sqrt((n - 0.5) / 1500000) AS changed_at,
           ((hashint8extended(n, 1) >> 11) & 1048575)::float8 / 1048576 AS r1,
           ((hashint8extended(n, 2) >> 11) & 1048575)::float8 / 1048576 AS r2,
           ((hashint8extended(n, 3) >> 11) & 1048575)::float8 / 1048576 AS r3
    FROM generate_series(1, 1500000) AS n
)
INSERT INTO audit_log (table_name, operation, row_pk, old_row, new_row, changed_by, changed_at)
SELECT CASE WHEN r1 < 0.55 THEN 'orders' ELSE 'products' END,
       'UPDATE',
       jsonb_build_object('id', CASE WHEN r1 < 0.55 THEN 1 + floor(r2 * 500000)::bigint
                                     ELSE 1 + floor(r2 * 200000)::bigint END),
       CASE WHEN r1 < 0.55
            THEN jsonb_build_object('status', (ARRAY['pending', 'paid', 'shipped'])[1 + floor(r3 * 3)::int])
            WHEN r1 < 0.85
            THEN jsonb_build_object('price', round((5 + r3 * 300)::numeric, 2))
            ELSE jsonb_build_object('status', 'draft')
       END,
       CASE WHEN r1 < 0.55
            THEN jsonb_build_object('status', (ARRAY['paid', 'shipped', 'delivered'])[1 + floor(r3 * 3)::int])
            WHEN r1 < 0.85
            THEN jsonb_build_object('price', round((5 + r3 * 300)::numeric * 0.9, 2))
            ELSE jsonb_build_object('status', 'active')
       END,
       CASE WHEN r1 < 0.55 THEN 'bazaar_app' ELSE 'seller' END,
       changed_at
FROM g
ORDER BY n;

COMMIT;

VACUUM (ANALYZE) audit_log;
RESET synchronous_commit;
