-- Exercise 13: 8 clients x 50 calls, all fighting over the same 5 keys
--   docker exec course-pg pgbench -U postgres -d bazaar -n -c 8 -j 8 -t 50 \
--       -f /course/modules/10-transactions-concurrency/solutions/exercises/ex13_idempotent_bench.sql
\set k random(1, 5)
SELECT count(*) FROM bazaar.place_order_idempotent('k' || :k, 8, '[{"product_id": 18, "quantity": 1}]');
