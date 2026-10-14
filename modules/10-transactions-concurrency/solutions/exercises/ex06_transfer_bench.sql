-- Exercise 6: transfers in both directions between products 1 and 2 (seller 1)
-- Before: UPDATE bazaar.inventory SET quantity = 1000 WHERE product_id IN (1, 2);
--   docker exec course-pg pgbench -U postgres -d bazaar -n -c 8 -j 8 -t 200 \
--       -f /course/modules/10-transactions-concurrency/solutions/exercises/ex06_transfer_bench.sql
\set dir random(0, 1)
\if :dir
SELECT bazaar.transfer_stock(1, 2, 1);
\else
SELECT bazaar.transfer_stock(2, 1, 1);
\endif
