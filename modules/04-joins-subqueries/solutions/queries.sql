-- =============================================================================
-- queries.sql  --  Module 04 milestone (reference solution)
-- -----------------------------------------------------------------------------
-- Saved "business questions" for the Bazaar admin. Each query is documented
-- with the question it answers and its expected answer on the small dataset
-- (./project/bin/reset.sh 04). Read-only: nothing here changes data.
--
-- Run it:
--   docker exec -i course-pg psql -X -U postgres -d bazaar < modules/04-joins-subqueries/solutions/queries.sql
-- Parameters (psql variables, override with -v name=value):
--   order_id   the order for Q1             (default 102)
--   today      "today" for Q4, Q9 and Q10   (default 2025-06-30, the dataset's last day)
-- =============================================================================

\set ON_ERROR_STOP on
\set QUIET on
\pset null '∅'
\set QUIET off
\if :{?order_id}
\else
    \set order_id 102
\endif
\if :{?today}
\else
    \set today 2025-06-30
\endif

-- -----------------------------------------------------------------------------
-- Q1. Order sheet: every line of one order with product, category, the price
--     paid, today's price, and the line total.
-- Expected (order 102): 4 lines, line totals summing to 2657.98 (= orders.total);
--     buyer Liam O'Connor, shop TechTrove; first line Titan G17 Gaming Laptop 2199.99.
-- -----------------------------------------------------------------------------
\echo '== Q1 order sheet'
SELECT o.id                      AS order_id,
       o.created_at::date        AS ordered,
       o.status,
       buyer.full_name           AS buyer,
       s.shop_name,
       p.title,
       c.name                    AS category,
       i.quantity,
       i.unit_price,
       p.price                   AS price_today,
       i.quantity * i.unit_price AS line_total
FROM orders AS o
JOIN users       AS buyer ON buyer.id  = o.user_id
JOIN sellers     AS s     ON s.id      = o.seller_id
JOIN order_items AS i     ON i.order_id = o.id
JOIN products    AS p     ON p.id      = i.product_id
JOIN categories  AS c     ON c.id      = p.category_id
WHERE o.id = :order_id
ORDER BY line_total DESC, p.id;

-- -----------------------------------------------------------------------------
-- Q2. Customer directory: every user, the shop they own (if any), and their
--     most recent order (if any).
-- Expected: 30 rows; 6 have a shop (users 1-6); 9 have no order
--     (6, 7, 15, 16, 21, 24, 28, 29, 30). User 6 has a shop and no order.
-- -----------------------------------------------------------------------------
\echo '== Q2 customer directory'
SELECT u.id,
       u.full_name,
       u.is_active,
       s.shop_name               AS owns_shop,
       last.id                   AS last_order_id,
       last.created_at::date     AS last_ordered,
       last.status               AS last_status
FROM users AS u
LEFT JOIN sellers AS s ON s.user_id = u.id
LEFT JOIN LATERAL (
    SELECT o.id, o.created_at, o.status
    FROM orders AS o
    WHERE o.user_id = u.id
    ORDER BY o.created_at DESC, o.id DESC
    LIMIT 1
) AS last ON true
ORDER BY u.id;

-- -----------------------------------------------------------------------------
-- Q3. Stock alert: active products with nothing left to sell
--     (quantity - reserved = 0), with the shop to contact.
-- Expected: 3 rows: 3 (TechTrove, fully reserved), 13 (TechTrove, fully
--     reserved), 25 (Bookworm Corner, quantity 0).
-- -----------------------------------------------------------------------------
\echo '== Q3 stock alert'
SELECT p.id, p.title, s.shop_name, inv.quantity, inv.reserved
FROM products AS p
JOIN inventory AS inv ON inv.product_id = p.id
JOIN sellers   AS s   ON s.id = p.seller_id
WHERE p.status = 'active'
  AND inv.quantity - inv.reserved = 0
ORDER BY p.id;

-- -----------------------------------------------------------------------------
-- Q4. Dormant buyers: users who ordered in the previous calendar year but not
--     yet in the current one (relative to :today), with their last order date.
-- Expected: 3 rows: 2 (2024-04-04), 9 (2024-02-01), 17 (2024-11-28).
-- -----------------------------------------------------------------------------
\echo '== Q4 dormant buyers'
SELECT u.id,
       u.full_name,
       u.is_active,
       (SELECT max(o.created_at)::date FROM orders AS o WHERE o.user_id = u.id) AS last_ordered
FROM users AS u
WHERE EXISTS (
        SELECT 1 FROM orders AS o
        WHERE o.user_id = u.id
          AND o.created_at >= date_trunc('year', :'today'::date) - interval '1 year'
          AND o.created_at <  date_trunc('year', :'today'::date)
      )
  AND NOT EXISTS (
        SELECT 1 FROM orders AS o
        WHERE o.user_id = u.id
          AND o.created_at >= date_trunc('year', :'today'::date)
      )
ORDER BY u.id;

-- -----------------------------------------------------------------------------
-- Q5. Orders placed by users whose account is now inactive.
-- Expected: 4 rows: order 6 (user 9), orders 28, 48, 77 (user 22).
-- -----------------------------------------------------------------------------
\echo '== Q5 orders of inactive users'
SELECT o.id AS order_id, o.created_at::date AS ordered, o.status, o.total,
       u.id AS user_id, u.full_name
FROM orders AS o
JOIN users AS u ON u.id = o.user_id
WHERE NOT u.is_active
ORDER BY o.id;

-- -----------------------------------------------------------------------------
-- Q6. Breadcrumbs: every active product with its full category path.
-- Expected: 54 rows. Product 1 -> 'Electronics > Computers > Laptops',
--     17 -> 'Electronics > Computers', 33 -> 'Books > Fiction',
--     62 -> 'Clothing'.
-- -----------------------------------------------------------------------------
\echo '== Q6 breadcrumbs'
SELECT p.id,
       p.title,
       concat_ws(' > ', gp.name, parent.name, c.name) AS category_path
FROM products AS p
JOIN categories      AS c      ON c.id      = p.category_id
LEFT JOIN categories AS parent ON parent.id = c.parent_id
LEFT JOIN categories AS gp     ON gp.id     = parent.parent_id
WHERE p.status = 'active'
ORDER BY category_path, p.id;

-- -----------------------------------------------------------------------------
-- Q7. Shop owners as customers: which shops' owners bought from which other
--     shops (non-cancelled orders), one row per pair.
-- Expected: 9 rows; no owner ever bought from their own shop.
--     (With cancelled orders included there are 10 pairs: Maple & Pine's
--     owner's only Bookworm Corner order, 31, was cancelled.)
-- -----------------------------------------------------------------------------
\echo '== Q7 owners buying from other shops'
SELECT DISTINCT
       own.shop_name  AS buyer_owns,
       sold.shop_name AS bought_from,
       own.id = sold.id AS own_shop
FROM orders AS o
JOIN sellers AS own  ON own.user_id = o.user_id
JOIN sellers AS sold ON sold.id     = o.seller_id
WHERE o.status <> 'cancelled'
ORDER BY buyer_owns, bought_from;

-- -----------------------------------------------------------------------------
-- Q8. Sticker-pack companions: orders containing the free sticker pack
--     (product 62) and what else was in them.
-- Expected: orders 10, 46, 82, 111, 115; 9 companion lines in total
--     (order 10: 51, 52; 46: 52; 82: 48, 56; 111: 47, 48, 54; 115: 49).
-- -----------------------------------------------------------------------------
\echo '== Q8 sticker-pack companions'
SELECT sticker.order_id, other.product_id, p.title, other.quantity
FROM order_items AS sticker
JOIN order_items AS other ON other.order_id = sticker.order_id
                         AND other.product_id <> sticker.product_id
JOIN products    AS p     ON p.id = other.product_id
WHERE sticker.product_id = 62
ORDER BY sticker.order_id, other.product_id;

-- -----------------------------------------------------------------------------
-- Q9. Stale stock: active products with units available that nobody has
--     ordered this year (relative to :today). Candidates for a promotion.
-- Expected: 9 rows: 15, 20, 21, 35, 44, 52, 57, 58, 60.
-- -----------------------------------------------------------------------------
\echo '== Q9 stale stock'
SELECT p.id, p.title, s.shop_name, inv.quantity - inv.reserved AS available,
       (SELECT max(o.created_at)::date
          FROM order_items AS i JOIN orders AS o ON o.id = i.order_id
         WHERE i.product_id = p.id) AS last_ordered
FROM products AS p
JOIN inventory AS inv ON inv.product_id = p.id
JOIN sellers   AS s   ON s.id = p.seller_id
WHERE p.status = 'active'
  AND inv.quantity > inv.reserved
  AND NOT EXISTS (
        SELECT 1
        FROM order_items AS i
        JOIN orders AS o ON o.id = i.order_id
        WHERE i.product_id = p.id
          AND o.created_at >= date_trunc('year', :'today'::date)
      )
ORDER BY p.id;

-- -----------------------------------------------------------------------------
-- Q10. Activity feed for the last 7 days up to :today (inclusive): new orders,
--      new users and new shops in one list, newest first.
-- Expected: 13 rows: 12 orders (109-120) and 1 signup (user 30, Samuel Okafor).
-- -----------------------------------------------------------------------------
\echo '== Q10 activity feed'
SELECT 'order' AS kind, o.id, o.created_at, s.shop_name || ' <- ' || u.full_name AS detail
FROM orders AS o
JOIN sellers AS s ON s.id = o.seller_id
JOIN users   AS u ON u.id = o.user_id
WHERE o.created_at >= :'today'::date - 6 AND o.created_at < :'today'::date + 1
UNION ALL
SELECT 'signup', u.id, u.created_at, u.full_name
FROM users AS u
WHERE u.created_at >= :'today'::date - 6 AND u.created_at < :'today'::date + 1
UNION ALL
SELECT 'new shop', s.id, s.created_at, s.shop_name
FROM sellers AS s
WHERE s.created_at >= :'today'::date - 6 AND s.created_at < :'today'::date + 1
ORDER BY created_at DESC;
