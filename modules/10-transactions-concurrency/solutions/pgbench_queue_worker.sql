-- pgbench_queue_worker.sql  --  N concurrent workers draining the job queue.
-- Needs bench_setup.sql first (it enqueues 2,000 jobs in queue 'bench').
-- Run (8 workers x 300 iterations = 2,400 dequeue attempts for 2,000 jobs):
--   docker exec course-pg pgbench -U postgres -d bazaar -n -c 8 -j 4 -t 300 \
--       -f /course/modules/10-transactions-concurrency/solutions/pgbench_queue_worker.sql
-- :client_id is pgbench's built-in client number (0..7), used as the worker name.
SELECT bench.work_one('worker-' || :client_id);
