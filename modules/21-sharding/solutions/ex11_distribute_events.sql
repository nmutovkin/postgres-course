-- Exercise 11: distribute the monthly-partitioned events table by session_id.
-- State: citus-build.sh small (events is a local table on the coordinator).
SET search_path = bazaar, public;
\set ON_ERROR_STOP off

-- 1. As it is, it can't be distributed: the PK (id, occurred_at) lacks session_id.
SELECT create_distributed_table('events', 'session_id');

\set ON_ERROR_STOP on
-- 2. Make the PK shard-key-aware. It must also keep occurred_at, the
--    PARTITION key (module 14's rule), so it needs all three columns.
BEGIN;
ALTER TABLE events DROP CONSTRAINT events_pkey;
ALTER TABLE events ADD PRIMARY KEY (session_id, id, occurred_at);
COMMIT;

-- 3. Distribute. Every partition becomes a distributed table of its own,
--    co-located with the parent.
SELECT create_distributed_table('events', 'session_id', colocate_with => 'none');

SELECT count(*) AS shard_tables FROM citus_shards WHERE table_name::text LIKE 'events%';
SELECT count(*) AS relations FROM citus_tables WHERE table_name::text LIKE 'events%';

-- 4. The partition-maintenance function from module 14 keeps working: a new
--    partition of a distributed partitioned table is distributed automatically.
SELECT create_events_partition('2027-01-01');
SELECT count(*) AS shard_tables_after FROM citus_shards WHERE table_name::text LIKE 'events%';

-- 5. One session's events are in one shard group: router queries.
INSERT INTO events (occurred_at, user_id, session_id, event_type, product_id)
VALUES ('2025-06-30 10:00+00', 8, '6f0e8d7c-1b2a-4c3d-9e8f-0a1b2c3d4e5f', 'product_view', 24),
       ('2025-06-30 10:01+00', 8, '6f0e8d7c-1b2a-4c3d-9e8f-0a1b2c3d4e5f', 'add_to_cart', 24),
       ('2025-06-30 10:05+00', NULL, '0b7e2f1a-9c8d-4e6f-a5b4-c3d2e1f0a9b8', 'search', NULL);

SELECT get_shard_id_for_distribution_column('events_y2025m06', '6f0e8d7c-1b2a-4c3d-9e8f-0a1b2c3d4e5f'::uuid) AS shardid;

-- Without a time filter this is still ONE task, but the worker scans the
-- session's shard of all 26 partitions. With the partition key as well, both
-- levels prune: one node, one shard of one partition.
EXPLAIN (COSTS OFF)
SELECT event_type, occurred_at
FROM events
WHERE session_id = '6f0e8d7c-1b2a-4c3d-9e8f-0a1b2c3d4e5f'
  AND occurred_at >= '2025-06-30' AND occurred_at < '2025-07-01'
ORDER BY occurred_at;
