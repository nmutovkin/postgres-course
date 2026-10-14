-- =============================================================================
-- fixed_queries.sql  --  Module 12 milestone: reference fixes for the six
-- slow queries in slow_queries.sql. See project.md and solutions/README.md
-- ("Milestone") for the reasoning and the measurements.
-- =============================================================================
SET search_path = bazaar, public;

-- -----------------------------------------------------------------------------
-- Schema and configuration changes
-- -----------------------------------------------------------------------------

-- S3: teach the planner that city (almost) determines country_code. This is
-- exactly project/db/1201_schema_stats.sql.
CREATE STATISTICS IF NOT EXISTS addresses_country_city_stats (dependencies, ndistinct, mcv)
    ON country_code, city
    FROM addresses;
ANALYZE addresses;

-- S3 (and many other plans): the data lives in RAM or on SSD, so a random page
-- read costs about as much as a sequential one. The default (4.0) dates from
-- spinning disks. For the measurement we set it per session; on a real server:
--     ALTER SYSTEM SET random_page_cost = 1.1;  SELECT pg_reload_conf();
-- (The course's reference files keep the default, so that plans shown in later
-- modules stay the same.)
SET random_page_cost = 1.1;

-- S5: a 24 kB BRIN index for time-range reports on orders (append-only,
-- created_at follows the physical order). Not part of the reference schema in
-- project/db; adopt it in your own migration if you keep this report.
CREATE INDEX IF NOT EXISTS orders_created_at_brin ON orders USING brin (created_at);

-- -----------------------------------------------------------------------------
-- S1. Keyset pagination: remember the last row of the previous page,
--     (created_at, id) = ('2023-10-07 08:23:06+00', 59156) for page 3001.
-- -----------------------------------------------------------------------------
SELECT id, user_id, status, total, created_at
FROM orders
WHERE seller_id = 1
  AND (created_at, id) < ('2023-10-07 08:23:06+00', 59156)
ORDER BY created_at DESC, id DESC
LIMIT 20;

-- -----------------------------------------------------------------------------
-- S2. Compute each seller's average once, then join.
-- -----------------------------------------------------------------------------
SELECT o.id, o.seller_id, o.total
FROM orders AS o
JOIN (
    SELECT seller_id, avg(total) AS avg_total
    FROM orders
    GROUP BY seller_id
) AS sa ON sa.seller_id = o.seller_id
WHERE o.created_at >= '2025-12-24'
  AND o.total > 3 * sa.avg_total
ORDER BY o.total DESC;

-- -----------------------------------------------------------------------------
-- S3. Unchanged SQL; the fix is the statistics object and random_page_cost.
-- -----------------------------------------------------------------------------
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

-- -----------------------------------------------------------------------------
-- S4. NOT EXISTS: an anti-join, and correct even if user_id could be NULL.
-- -----------------------------------------------------------------------------
SELECT count(*)
FROM users AS u
WHERE NOT EXISTS (SELECT 1 FROM orders AS o WHERE o.user_id = u.id);

-- -----------------------------------------------------------------------------
-- S5. A range on the bare column (UTC days), served by the BRIN index.
-- -----------------------------------------------------------------------------
SELECT date_trunc('day', created_at, 'UTC') AS day, count(*) AS orders, sum(total) AS revenue
FROM orders
WHERE created_at >= timestamptz '2025-12-25 00:00+00'
GROUP BY 1
ORDER BY 1;

-- -----------------------------------------------------------------------------
-- S6. Give this one report enough memory for a hash aggregate, and only it.
-- -----------------------------------------------------------------------------
BEGIN;
SET LOCAL work_mem = '64MB';
SELECT product_id, sum(quantity) AS units
FROM order_items
GROUP BY product_id
ORDER BY units DESC
LIMIT 10;
COMMIT;

RESET random_page_cost;
