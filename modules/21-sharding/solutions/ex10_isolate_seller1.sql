-- Exercise 10: give seller 1 a shard group of its own and a node of its own.
-- State: citus-build.sh small, then m21-worker3 started and registered:
--   docker compose --profile scale up -d worker3 && ./scripts/citus-init-nodes.sh m21-worker3
SET search_path = bazaar, public;

-- before: seller 1 shares shard 102077 with every seller whose hash falls in its range
SELECT get_shard_id_for_distribution_column('orders', 1) AS shard_before,
       (SELECT count(DISTINCT seller_id) FROM orders
        WHERE get_shard_id_for_distribution_column('orders', seller_id)
              = get_shard_id_for_distribution_column('orders', 1)) AS sellers_with_orders_in_it;

-- 1. split the shard group (all 8 co-located tables) so that seller 1 is alone
SELECT isolate_tenant_to_new_shard('orders', 1, 'CASCADE', shard_transfer_mode => 'block_writes')
       AS new_shard;

-- 2. check: the new shard's hash range is exactly hashint8(1)
SELECT s.shardid, s.shardminvalue, s.shardmaxvalue, hashint8(1) AS hash_of_1, cs.nodename
FROM pg_dist_shard s
JOIN citus_shards cs USING (shardid)
WHERE s.shardid = get_shard_id_for_distribution_column('orders', 1);

-- 3. move the whole group to the new worker (online move)
SELECT citus_move_shard_placement(
           get_shard_id_for_distribution_column('orders', 1),
           (SELECT nodename FROM citus_shards
            WHERE shardid = get_shard_id_for_distribution_column('orders', 1)), 5432,
           'm21-worker3', 5432,
           shard_transfer_mode => 'force_logical');

-- 4. verify: every seller-1 shard is on worker3, and nothing else is
SELECT nodename, count(*) AS shards, string_agg(DISTINCT table_name::text, ', ') AS tables
FROM citus_shards
WHERE nodename = 'm21-worker3' AND citus_table_type = 'distributed'
GROUP BY nodename;

SELECT count(*) AS shards_per_table FROM pg_dist_shard WHERE logicalrelid = 'orders'::regclass;
