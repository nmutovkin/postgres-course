-- Module 08 exercise solutions.
-- Run on a fresh state:  ./project/bin/reset.sh 09
--   docker exec -i course-pg psql -X -U postgres -d bazaar < modules/08-beyond-scalars/solutions/exercises.sql
-- Exercises 3, 7 and 12 change the schema; reset afterwards.

\echo '== Exercise 1: laptops with >= 16 GB, three ways'
-- 1a. containment only: one equality test per qualifying value
SELECT id, title
FROM products
WHERE category_id = 3
  AND status = 'active'
  AND attributes @> ANY (ARRAY['{"ram_gb": 16}', '{"ram_gb": 32}']::jsonb[])
ORDER BY id;

-- 1b. jsonpath filter
SELECT id, title
FROM products
WHERE category_id = 3
  AND status = 'active'
  AND attributes @? '$.ram_gb ? (@ >= 16)'
ORDER BY id;

-- 1c. ->> with a cast
SELECT id, title
FROM products
WHERE category_id = 3
  AND status = 'active'
  AND (attributes ->> 'ram_gb')::int >= 16
ORDER BY id;

\echo '== Exercise 2: products sharing tags with product 10'
SELECT p.id,
       p.title,
       s.shared_tags
FROM products p
JOIN products r ON r.id = 10
CROSS JOIN LATERAL (
    SELECT ARRAY(SELECT unnest(p.tags) INTERSECT SELECT unnest(r.tags) ORDER BY 1) AS shared_tags
) s
WHERE p.id <> r.id
  AND p.status = 'active'
  AND p.tags && r.tags
ORDER BY cardinality(s.shared_tags) DESC, p.id;

\echo '== Exercise 3: on_hold status and the lifecycle report'
ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'on_hold' AFTER 'paid';

SELECT s.status,
       s.position,
       count(o.id) AS orders
FROM unnest(enum_range(NULL::order_status)) WITH ORDINALITY AS s(status, position)
LEFT JOIN orders o ON o.status = s.status
GROUP BY s.status, s.position
ORDER BY s.position;

\echo '== Exercise 4: attribute key inventory per category'
WITH per_category AS (
    SELECT category_id, count(*) AS products
    FROM products
    GROUP BY category_id
), key_counts AS (
    SELECT p.category_id, k.key, count(*) AS n
    FROM products p
    CROSS JOIN LATERAL jsonb_object_keys(p.attributes) AS k(key)
    GROUP BY p.category_id, k.key
)
SELECT c.name AS category,
       pc.products,
       coalesce(jsonb_object_agg(kc.key, kc.n) FILTER (WHERE kc.key IS NOT NULL), '{}') AS key_counts,
       coalesce(array_agg(kc.key ORDER BY kc.key) FILTER (WHERE kc.n = pc.products), '{}') AS universal_keys
FROM per_category pc
JOIN categories c        ON c.id = pc.category_id
LEFT JOIN key_counts kc  ON kc.category_id = pc.category_id
GROUP BY c.id, c.name, pc.products
ORDER BY c.id;

\echo '== Exercise 5: tag co-occurrence'
SELECT a.tag AS tag_a,
       b.tag AS tag_b,
       count(*) AS products
FROM products p
CROSS JOIN LATERAL unnest(p.tags) AS a(tag)
CROSS JOIN LATERAL unnest(p.tags) AS b(tag)
WHERE a.tag < b.tag
GROUP BY a.tag, b.tag
HAVING count(*) >= 3
ORDER BY products DESC, tag_a, tag_b;

\echo '== Exercise 6: monitor spec sheet'
SELECT p.id,
       jt.brand,
       jt.size_in,
       split_part(jt.resolution, 'x', 1)::int AS width_px,
       split_part(jt.resolution, 'x', 2)::int AS height_px,
       split_part(jt.resolution, 'x', 1)::int * split_part(jt.resolution, 'x', 2)::int AS pixels,
       jt.refresh_hz,
       jt.port_count,
       jt.has_usb_c
FROM products p,
     JSON_TABLE(p.attributes, '$'
         COLUMNS (
             brand       text          PATH '$.brand',
             size_in     numeric(4,1)  PATH '$.size_in',
             resolution  text          PATH '$.resolution',
             refresh_hz  int           PATH '$.refresh_hz'    DEFAULT 60 ON EMPTY,
             port_count  int           PATH '$.ports.size()'  DEFAULT 0 ON EMPTY,
             has_usb_c   boolean       EXISTS PATH '$.ports[*] ? (@ == "USB-C")'
         )) AS jt
WHERE p.category_id = 4
ORDER BY pixels DESC, p.id;

\echo '== Exercise 7: country_code domain'
BEGIN;
CREATE DOMAIN country_code AS char(2)
    CONSTRAINT country_code_format CHECK (VALUE ~ '^[A-Z]{2}$');
ALTER TABLE sellers DROP CONSTRAINT sellers_country_code_upper;
ALTER TABLE sellers   ALTER COLUMN country_code TYPE country_code;
ALTER TABLE addresses ALTER COLUMN country_code TYPE country_code;
COMMIT;

SELECT table_name, column_name, domain_name
FROM information_schema.columns
WHERE domain_name = 'country_code'
ORDER BY table_name;

UPDATE sellers SET country_code = 'it' WHERE id = 3;      -- fails
UPDATE addresses SET country_code = 'D' WHERE id = 1;     -- fails: char(2) pads to 'D '

\echo '== Exercise 8: conflicts in a proposed batch of promotions'
WITH proposed (ref, product_id, discount_pct, active_during) AS (
    VALUES ('a', 10, 10.00, tstzrange('2025-06-14 00:00+00', '2025-06-21 00:00+00')),
           ('b',  1, 15.00, tstzrange('2025-07-01 00:00+00', '2025-07-08 00:00+00')),
           ('c',  1,  5.00, tstzrange('2025-07-05 00:00+00', '2025-07-12 00:00+00')),
           ('d', 47, 10.00, tstzrange('2025-07-01 00:00+00', '2025-07-31 00:00+00')),
           ('e', 37, 20.00, tstzrange('2025-07-01 00:00+00', '2025-07-15 00:00+00')),
           ('f', 24, 10.00, tstzrange('2024-12-25 00:00+00', '2025-01-01 00:00+00'))
)
SELECT n.ref,
       n.product_id,
       'existing promotion ' || e.id      AS conflicts_with,
       n.active_during * e.active_during  AS overlap
FROM proposed n
JOIN promotions e
  ON e.product_id = n.product_id
 AND e.active_during && n.active_during
UNION ALL
SELECT n.ref,
       n.product_id,
       'proposal ' || o.ref,
       n.active_during * o.active_during
FROM proposed n
JOIN proposed o
  ON o.product_id = n.product_id
 AND o.ref < n.ref
 AND o.active_during && n.active_during
ORDER BY ref;

BEGIN;
INSERT INTO promotions (product_id, discount_pct, active_during)
VALUES (10, 10.00, tstzrange('2025-06-14 00:00+00', '2025-06-21 00:00+00')),
       ( 1, 15.00, tstzrange('2025-07-01 00:00+00', '2025-07-08 00:00+00')),
       ( 1,  5.00, tstzrange('2025-07-05 00:00+00', '2025-07-12 00:00+00')),
       (47, 10.00, tstzrange('2025-07-01 00:00+00', '2025-07-31 00:00+00')),
       (37, 20.00, tstzrange('2025-07-01 00:00+00', '2025-07-15 00:00+00')),
       (24, 10.00, tstzrange('2024-12-25 00:00+00', '2025-01-01 00:00+00'))
ON CONFLICT DO NOTHING
RETURNING product_id, discount_pct, active_during;
ROLLBACK;

\echo '== Exercise 9: promotion calendar for June 2025'
SELECT d::date                                                   AS day,
       count(pr.id)                                              AS products_on_promotion,
       coalesce(string_agg(pr.product_id::text, ',' ORDER BY pr.product_id), '') AS products
FROM generate_series(timestamptz '2025-06-01 00:00+00',
                     timestamptz '2025-06-30 00:00+00',
                     interval '1 day') AS d
LEFT JOIN promotions pr
       ON pr.active_during && tstzrange(d, d + interval '1 day')
GROUP BY d
ORDER BY d;

\echo '== Exercise 10: effective prices at 2025-06-10 12:00 UTC'
SELECT p.id,
       p.title,
       p.price,
       pr.discount_pct,
       round(p.price * (1 - coalesce(pr.discount_pct, 0) / 100), 2) AS effective_price
FROM products p
LEFT JOIN promotions pr
       ON pr.product_id = p.id
      AND pr.active_during @> timestamptz '2025-06-10 12:00+00'
WHERE p.status = 'active'
ORDER BY pr.discount_pct DESC NULLS LAST, p.id
LIMIT 5;

SELECT count(*)                                                          AS products,
       sum(round(p.price * (1 - coalesce(pr.discount_pct, 0) / 100), 2)) AS total_effective,
       sum(p.price)                                                      AS total_list
FROM products p
LEFT JOIN promotions pr
       ON pr.product_id = p.id
      AND pr.active_during @> timestamptz '2025-06-10 12:00+00'
WHERE p.status = 'active';

\echo '== Exercise 11: order lines that missed their discount'
WITH priced AS (
    SELECT o.id AS order_id,
           oi.product_id,
           oi.quantity,
           oi.unit_price,
           h.price AS list_price,
           pr.discount_pct,
           round(h.price * (1 - coalesce(pr.discount_pct, 0) / 100), 2) AS effective_price
    FROM orders o
    JOIN order_items oi ON oi.order_id = o.id
    CROSS JOIN LATERAL (
        SELECT price
        FROM product_price_history h
        WHERE h.product_id = oi.product_id
          AND h.valid_from <= o.created_at
        ORDER BY h.valid_from DESC
        LIMIT 1
    ) h
    LEFT JOIN promotions pr
           ON pr.product_id = oi.product_id
          AND pr.active_during @> o.created_at
)
SELECT order_id, product_id, quantity, list_price, discount_pct, effective_price, unit_price,
       quantity * (unit_price - effective_price) AS overcharged,
       sum(quantity * (unit_price - effective_price)) OVER () AS total_overcharged
FROM priced
WHERE unit_price <> effective_price
ORDER BY order_id, product_id;

-- spot checks of the price logic
SELECT x.product_id, x.at, h.price AS list_price, pr.discount_pct,
       round(h.price * (1 - coalesce(pr.discount_pct, 0) / 100), 2) AS effective_price
FROM (VALUES (1::bigint, timestamptz '2024-11-26 12:00+00'),
             (34::bigint, timestamptz '2024-07-03 12:00+00')) AS x(product_id, at)
CROSS JOIN LATERAL (
    SELECT price FROM product_price_history h
    WHERE h.product_id = x.product_id AND h.valid_from <= x.at
    ORDER BY h.valid_from DESC LIMIT 1
) h
LEFT JOIN promotions pr ON pr.product_id = x.product_id AND pr.active_during @> x.at;

\echo '== Exercise 12: removing on_hold again'
UPDATE orders SET status = 'on_hold' WHERE id = 120;

BEGIN;
ALTER TYPE order_status RENAME TO order_status_old;
CREATE TYPE order_status AS ENUM ('pending', 'paid', 'shipped', 'delivered', 'cancelled', 'refunded');
ALTER TABLE orders ALTER COLUMN status DROP DEFAULT;
ALTER TABLE orders ALTER COLUMN status TYPE order_status
    USING (CASE status::text WHEN 'on_hold' THEN 'paid' ELSE status::text END)::order_status;
ALTER TABLE orders ALTER COLUMN status SET DEFAULT 'pending';
DROP TYPE order_status_old;
COMMIT;

SELECT enum_range(NULL::order_status);
SELECT id, status FROM orders WHERE id = 120;
SELECT column_default FROM information_schema.columns
WHERE table_name = 'orders' AND column_name = 'status';
SELECT count(*) AS old_type_left FROM pg_type WHERE typname = 'order_status_old';

\echo '== Exercise 13: gaps between promotions'
WITH win AS (
    SELECT tstzrange('2024-07-01 00:00+00', '2025-07-01 00:00+00') AS w
), promoted AS (
    SELECT pr.product_id,
           range_agg(pr.active_during * win.w) AS promoted
    FROM promotions pr, win
    WHERE pr.active_during && win.w
    GROUP BY pr.product_id
)
SELECT p.product_id,
       (SELECT sum(upper(r) - lower(r)) FROM unnest(p.promoted) AS r)                       AS promoted_time,
       (SELECT max(upper(g) - lower(g)) FROM unnest(win.w::tstzmultirange - p.promoted) AS g) AS longest_gap,
       win.w::tstzmultirange - p.promoted                                                     AS not_promoted
FROM promoted p, win
ORDER BY p.product_id;

WITH win AS (
    SELECT tstzrange('2024-07-01 00:00+00', '2025-07-01 00:00+00') AS w
)
SELECT g                   AS no_promotion_at_all,
       upper(g) - lower(g) AS length
FROM win,
     unnest(win.w::tstzmultirange - (SELECT range_agg(active_during) FROM promotions)) AS g
ORDER BY g;
