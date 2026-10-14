-- Exercise 4: 8 clients x 100 restocks of one unit each
--   docker exec course-pg pgbench -U postgres -d bazaar -n -c 8 -j 8 -t 100 \
--       -f /course/modules/10-transactions-concurrency/solutions/exercises/ex04_restock_bench.sql
SELECT bazaar.restock(57, 1);
