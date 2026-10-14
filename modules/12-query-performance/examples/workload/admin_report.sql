-- Back office: revenue report per seller with correlated subqueries (runs rarely).
\set country random(1, 3)
SELECT s.id, s.shop_name,
       (SELECT count(*) FROM bazaar.orders o WHERE o.seller_id = s.id AND o.created_at >= '2025-10-01') AS orders_q4,
       (SELECT sum(total) FROM bazaar.orders o WHERE o.seller_id = s.id AND o.created_at >= '2025-10-01') AS revenue_q4
FROM bazaar.sellers s
WHERE s.country_code = (ARRAY['DE', 'GB', 'FR'])[:country]
ORDER BY revenue_q4 DESC NULLS LAST
LIMIT 10;
