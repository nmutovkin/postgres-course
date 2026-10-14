-- Exercise 3: three ways to locate seller 42 (state: citus-build.sh small).
SET search_path = bazaar, public;

-- 1. Citus's helper
SELECT get_shard_id_for_distribution_column('orders', 42) AS shardid;

-- 2. hashint8 + the hash ranges in the metadata
SELECT hashint8(42) AS hash, s.shardid, s.shardminvalue, s.shardmaxvalue
FROM pg_dist_shard s
WHERE s.logicalrelid = 'orders'::regclass
  AND hashint8(42) BETWEEN s.shardminvalue::int AND s.shardmaxvalue::int;

-- 3. pure arithmetic: 32 equal slices of the int4 hash space
SELECT b AS bucket,
       (SELECT min(shardid) FROM pg_dist_shard WHERE logicalrelid = 'orders'::regclass) + b AS shardid
FROM (SELECT (hashint8(42)::bigint + 2147483648) * 32 / 4294967296 AS b) x;

-- the node, and the co-located products shard
SELECT table_name, shardid, nodename
FROM citus_shards
WHERE shardid IN (get_shard_id_for_distribution_column('orders', 42),
                  get_shard_id_for_distribution_column('products', 42))
ORDER BY table_name::text;
