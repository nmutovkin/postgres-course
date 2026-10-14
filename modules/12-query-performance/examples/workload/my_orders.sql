-- Customer: "my orders", newest first.
\set uid random(1, 100000)
SELECT id, status, total, created_at
FROM bazaar.orders
WHERE user_id = :uid
ORDER BY created_at DESC
LIMIT 20;
