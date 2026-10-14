-- Storefront: category page with OFFSET pagination (pages 1-50).
\set cat random(1, 69)
\set page random(0, 49)
SELECT id, title, price
FROM bazaar.products
WHERE status = 'active' AND category_id = :cat
ORDER BY price, id
LIMIT 24 OFFSET :page * 24;
