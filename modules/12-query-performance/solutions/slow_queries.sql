-- =============================================================================
-- slow_queries.sql  --  Module 12 milestone: the six slow queries, as reported
-- -----------------------------------------------------------------------------
-- These are the queries exactly as the application team sent them. Each one
-- returns correct results. Your job (project.md) is to measure, explain and fix
-- them. Do not run S4 without a statement_timeout: it runs for many minutes.
-- =============================================================================
SET search_path = bazaar, public;

-- S1. Seller dashboard, page 3001 of seller 1's order list (20 per page).
SELECT id, user_id, status, total, created_at
FROM orders
WHERE seller_id = 1
ORDER BY created_at DESC, id DESC
LIMIT 20 OFFSET 60000;

-- S2. Fraud review: orders of the last week of 2025 worth more than three
--     times their seller's average order value.
SELECT o.id, o.seller_id, o.total
FROM orders AS o
WHERE o.created_at >= '2025-12-24'
  AND o.total > 3 * (SELECT avg(o2.total)
                     FROM orders AS o2
                     WHERE o2.seller_id = o.seller_id)
ORDER BY o.total DESC;

-- S3. Marketing: which shops do Berlin customers buy from? (2025)
SELECT s.shop_name, count(*) AS orders, sum(o.total) AS revenue
FROM addresses AS a
JOIN orders AS o ON o.shipping_address_id = a.id
JOIN sellers AS s ON s.id = o.seller_id
WHERE a.country_code = 'DE'
  AND a.city = 'Berlin'
  AND o.created_at >= '2025-01-01'
GROUP BY s.shop_name
ORDER BY revenue DESC
LIMIT 10;

-- S4. CRM: how many registered users have never placed an order?
SET statement_timeout = '30s';
SELECT count(*)
FROM users
WHERE id NOT IN (SELECT user_id FROM orders);
RESET statement_timeout;

-- S5. Finance: daily revenue for the last seven days of 2025 (UTC days).
SELECT created_at::date AS day, count(*) AS orders, sum(total) AS revenue
FROM orders
WHERE created_at::date >= date '2025-12-25'
GROUP BY 1
ORDER BY 1;

-- S6. Merchandising: the ten best-selling products of all time, by units.
SELECT product_id, sum(quantity) AS units
FROM order_items
GROUP BY product_id
ORDER BY units DESC
LIMIT 10;
