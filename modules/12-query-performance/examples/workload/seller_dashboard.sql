-- Seller dashboard: latest orders with OFFSET pagination, for sellers drawn
-- with a Zipf-like skew (small seller ids, which are the big sellers, are picked more often).
\set sid random_zipfian(1, 2000, 1.1)
\set page random(0, 99)
SELECT id, user_id, status, total, created_at
FROM bazaar.orders
WHERE seller_id = :sid
ORDER BY created_at DESC
LIMIT 20 OFFSET :page * 20;
