-- pgbench_cancel_orders.sql  --  milestone extension: cancel random bench orders
-- concurrently while other clients keep placing new ones.
-- Run AFTER pgbench_place_order.sql, passing the range of bench order ids:
--   LO=$(docker exec course-pg psql -U postgres -d bazaar -Atc "SELECT max_order_id + 1 FROM bench.order_watermark")
--   HI=$(docker exec course-pg psql -U postgres -d bazaar -Atc "SELECT max(id) FROM bazaar.orders")
--   docker exec course-pg pgbench -U postgres -d bazaar -n -c 8 -j 4 -t 100 -D lo=$LO -D hi=$HI \
--       -f /course/modules/10-transactions-concurrency/solutions/pgbench_cancel_orders.sql@3 \
--       -f /course/modules/10-transactions-concurrency/solutions/pgbench_place_order.sql@1
-- The same order is often cancelled by two clients at once: exactly one of
-- them must get 'true'.
\set oid random(:lo, :hi)
SELECT bench.try_cancel(:oid);
