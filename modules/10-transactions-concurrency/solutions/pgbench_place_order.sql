-- pgbench_place_order.sql  --  hammer place_order() from many clients at once.
-- Needs bench_setup.sql first. Run (8 clients x 50 checkouts each):
--   docker exec course-pg pgbench -U postgres -d bazaar -n -c 8 -j 4 -t 50 \
--       -f /course/modules/10-transactions-concurrency/solutions/pgbench_place_order.sql
--
-- Every checkout asks for 1-2 units of two or three of the contested products
-- 1 (seller 1), 24 (seller 2) and 47 (seller 4), listed in DIFFERENT orders,
-- so a function that locked rows in request order would deadlock.
\set uid random(10, 19)
\set q1 random(1, 2)
\set q2 random(1, 2)
\set q3 random(1, 2)
\set shape random(1, 5)
\if :shape = 1
SELECT bench.try_place_order(:uid, jsonb_build_array(
    jsonb_build_object('product_id', 1,  'quantity', :q1),
    jsonb_build_object('product_id', 24, 'quantity', :q2)));
\elif :shape = 2
SELECT bench.try_place_order(:uid, jsonb_build_array(
    jsonb_build_object('product_id', 24, 'quantity', :q2),
    jsonb_build_object('product_id', 1,  'quantity', :q1)));
\elif :shape = 3
SELECT bench.try_place_order(:uid, jsonb_build_array(
    jsonb_build_object('product_id', 47, 'quantity', :q3),
    jsonb_build_object('product_id', 24, 'quantity', :q2),
    jsonb_build_object('product_id', 1,  'quantity', :q1)));
\elif :shape = 4
SELECT bench.try_place_order(:uid, jsonb_build_array(
    jsonb_build_object('product_id', 1,  'quantity', :q1),
    jsonb_build_object('product_id', 47, 'quantity', :q3)));
\else
SELECT bench.try_place_order(:uid, jsonb_build_array(
    jsonb_build_object('product_id', 47, 'quantity', :q3),
    jsonb_build_object('product_id', 1,  'quantity', :q1)));
\endif
