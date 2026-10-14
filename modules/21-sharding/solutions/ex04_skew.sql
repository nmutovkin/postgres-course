-- Exercise 4: skew of seller_id across 32 hash buckets, plain SQL.
-- State: any single-node Bazaar with the large dataset (reset.sh 21; with or without 2101).
SET search_path = bazaar, public;

-- 1. orders per bucket
WITH per_shard AS (
    SELECT ((hashint8(seller_id)::bigint + 2147483648) * 32 / 4294967296) AS shard,
           count(*) AS orders
    FROM orders
    GROUP BY 1
)
SELECT count(*) AS shards, min(orders), round(avg(orders)) AS avg, max(orders),
       round(max(orders) / avg(orders), 2) AS max_over_avg
FROM per_shard;

-- 2. the three biggest buckets
SELECT ((hashint8(seller_id)::bigint + 2147483648) * 32 / 4294967296) AS shard,
       count(*)                  AS orders,
       count(DISTINCT seller_id) AS sellers,
       min(seller_id)            AS smallest_seller_id
FROM orders
GROUP BY 1
ORDER BY orders DESC
LIMIT 3;

-- 3. without seller 1
WITH per_shard AS (
    SELECT ((hashint8(seller_id)::bigint + 2147483648) * 32 / 4294967296) AS shard,
           count(*) AS orders
    FROM orders
    WHERE seller_id <> 1
    GROUP BY 1
)
SELECT max(orders), round(avg(orders)) AS avg, round(max(orders) / avg(orders), 2) AS max_over_avg
FROM per_shard;

-- 4. even buckets -> worker1, odd buckets -> worker2
SELECT CASE ((hashint8(seller_id)::bigint + 2147483648) * 32 / 4294967296) % 2
            WHEN 0 THEN 'm21-worker1' ELSE 'm21-worker2' END AS worker,
       count(*) AS orders,
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
FROM orders
GROUP BY 1
ORDER BY 1;
