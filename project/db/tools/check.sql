-- =============================================================================
-- tools/check.sql  --  data sanity checks for the Bazaar reference database
-- -----------------------------------------------------------------------------
-- Run with psql against any reset state from 03 on, e.g.
--     ./project/bin/reset.sh 07
--     docker exec -i course-pg psql -U postgres -d bazaar < project/db/tools/check.sql
--
-- Each check prints the number of violating rows. Every row should show 0,
-- except the ones marked "(info)", which count deliberate anomalies:
--   * reviews_without_delivered_purchase: the small dataset has exactly 1
--     (user 7 reviewed product 1 without buying it); the large one ~6,000.
--
-- Checks for tables that do not exist yet in the current state are skipped
-- (psql \if), so the same script works at every state. Status columns are
-- compared as ::text so the script works both before (text + CHECK) and after
-- (enum) module 08. All checks are set-based (joins + GROUP BY) so they run in
-- seconds on the large dataset even before module 11 adds indexes.
-- =============================================================================

SET search_path = bazaar, public;

SELECT to_regclass('bazaar.payments')            IS NOT NULL AS has_v2,
       to_regclass('bazaar.promotions')          IS NOT NULL AS has_types,
       to_regclass('bazaar.seller_sales_daily')  IS NOT NULL AS has_logic
\gset

\echo '== core (module 02/03 tables) =='
SELECT check_name, violations
FROM (
    SELECT 1 AS n, 'orders_total_equals_sum_of_items' AS check_name, count(*) AS violations
    FROM orders o
    LEFT JOIN (
        SELECT order_id, sum(quantity * unit_price) AS s
        FROM order_items
        GROUP BY order_id
    ) i ON i.order_id = o.id
    WHERE o.total <> coalesce(i.s, 0)

    UNION ALL
    SELECT 2, 'orders_without_items', count(*)
    FROM orders o
    WHERE NOT EXISTS (SELECT 1 FROM order_items i WHERE i.order_id = o.id)

    UNION ALL
    -- paid_at set iff status in (paid, shipped, delivered, refunded);
    -- shipped_at set for shipped/delivered (optional for refunded);
    -- delivered_at set for delivered (optional for refunded, requires shipped_at).
    SELECT 3, 'status_timestamp_consistency', count(*)
    FROM orders
    WHERE (paid_at IS NOT NULL) <> (status::text IN ('paid', 'shipped', 'delivered', 'refunded'))
       OR (status::text IN ('shipped', 'delivered') AND shipped_at IS NULL)
       OR (status::text IN ('pending', 'paid', 'cancelled') AND shipped_at IS NOT NULL)
       OR (status::text = 'delivered' AND delivered_at IS NULL)
       OR (status::text NOT IN ('delivered', 'refunded') AND delivered_at IS NOT NULL)
       OR (delivered_at IS NOT NULL AND shipped_at IS NULL)

    UNION ALL
    SELECT 4, 'timestamps_in_order', count(*)
    FROM orders
    WHERE paid_at < created_at OR shipped_at < paid_at OR delivered_at < shipped_at

    UNION ALL
    SELECT 5, 'item_product_belongs_to_order_seller', count(*)
    FROM order_items i
    JOIN orders o ON o.id = i.order_id
    JOIN products p ON p.id = i.product_id
    WHERE p.seller_id <> o.seller_id

    UNION ALL
    SELECT 6, 'buyer_is_not_shop_owner', count(*)
    FROM orders o
    JOIN sellers s ON s.id = o.seller_id
    WHERE s.user_id = o.user_id

    UNION ALL
    SELECT 7, 'order_after_user_signup', count(*)
    FROM orders o
    JOIN users u ON u.id = o.user_id
    WHERE o.created_at < u.created_at

    UNION ALL
    SELECT 8, 'ordered_product_existed', count(*)
    FROM order_items i
    JOIN orders o ON o.id = i.order_id
    JOIN products p ON p.id = i.product_id
    WHERE p.created_at > o.created_at

    UNION ALL
    SELECT 9, 'draft_products_never_ordered', count(*)
    FROM order_items i
    JOIN products p ON p.id = i.product_id
    WHERE p.status::text = 'draft'

    UNION ALL
    SELECT 10, 'every_product_has_inventory', count(*)
    FROM products p
    WHERE NOT EXISTS (SELECT 1 FROM inventory v WHERE v.product_id = p.id)

    UNION ALL
    -- Stock is reserved when an order is placed and consumed when it ships, so
    -- reserved must equal the units sitting in pending/paid orders.
    -- (Holds right after a reset; place_order() experiments change it legitimately.)
    SELECT 11, 'inventory_reserved_matches_open_orders', count(*)
    FROM inventory v
    LEFT JOIN (
        SELECT i.product_id, sum(i.quantity) AS units
        FROM order_items i
        JOIN orders o ON o.id = i.order_id
        WHERE o.status::text IN ('pending', 'paid')
        GROUP BY i.product_id
    ) r ON r.product_id = v.product_id
    WHERE v.reserved <> coalesce(r.units, 0)

    UNION ALL
    SELECT 12, 'category_tree_has_no_cycles', count(*)
    FROM (
        WITH RECURSIVE walk (id, parent_id, depth) AS (
            SELECT id, parent_id, 1 FROM categories
            UNION ALL
            SELECT w.id, c.parent_id, w.depth + 1
            FROM walk w
            JOIN categories c ON c.id = w.parent_id
            WHERE w.depth < 20
        )
        SELECT id FROM walk WHERE depth >= 20
    ) cyc
) checks
ORDER BY n;

\if :has_v2
\echo '== schema v2 (module 07 tables) =='
WITH order_payments AS (
    SELECT
        o.id, o.status::text AS status, o.total, o.paid_at,
        count(p.id) FILTER (WHERE p.status::text = 'succeeded' AND p.amount = o.total)  AS n_paid_total,
        count(p.id) FILTER (WHERE p.status::text = 'succeeded')                         AS n_succeeded,
        count(p.id) FILTER (WHERE p.status::text = 'succeeded' AND p.amount = -o.total) AS n_refund_total,
        count(p.id) FILTER (WHERE p.amount < 0)                                         AS n_negative
    FROM orders o
    LEFT JOIN payments p ON p.order_id = o.id
    GROUP BY o.id, o.status, o.total, o.paid_at   -- (not just o.id: after 2101 id alone is not the PK)
),
first_delivery AS (
    SELECT o.user_id, i.product_id, min(o.delivered_at) AS delivered_at
    FROM orders o
    JOIN order_items i ON i.order_id = o.id
    WHERE o.status::text = 'delivered'
    GROUP BY o.user_id, i.product_id
)
SELECT check_name, violations
FROM (
    SELECT 1 AS n, 'paid_orders_have_one_succeeded_payment_equal_total' AS check_name, count(*) AS violations
    FROM order_payments
    WHERE paid_at IS NOT NULL AND total > 0 AND n_paid_total <> 1

    UNION ALL
    SELECT 2, 'unpaid_orders_have_no_succeeded_payment', count(*)
    FROM order_payments
    WHERE paid_at IS NULL AND n_succeeded > 0

    UNION ALL
    SELECT 3, 'refunded_orders_have_refund_payment', count(*)
    FROM order_payments
    WHERE status = 'refunded' AND total > 0 AND n_refund_total <> 1

    UNION ALL
    SELECT 4, 'refunds_only_on_refunded_orders', count(*)
    FROM order_payments
    WHERE status <> 'refunded' AND n_negative > 0

    UNION ALL
    SELECT 5, 'shipping_address_belongs_to_buyer', count(*)
    FROM orders o
    JOIN addresses a ON a.id = o.shipping_address_id
    WHERE a.user_id <> o.user_id

    UNION ALL
    SELECT 6, 'reviews_without_delivered_purchase (info)', count(*)
    FROM reviews r
    LEFT JOIN first_delivery f ON f.user_id = r.user_id AND f.product_id = r.product_id
    WHERE f.user_id IS NULL

    UNION ALL
    SELECT 7, 'verified_reviews_written_after_delivery', count(*)
    FROM reviews r
    JOIN first_delivery f ON f.user_id = r.user_id AND f.product_id = r.product_id
    WHERE r.created_at < f.delivered_at

    UNION ALL
    -- The price valid at order time is the history row with the latest
    -- valid_from <= created_at. Set-based version: turn the history into
    -- [valid_from, valid_to) intervals and join on them.
    SELECT 8, 'unit_price_equals_price_history_at_order_time', count(*)
    FROM order_items i
    JOIN orders o ON o.id = i.order_id
    JOIN (
        SELECT product_id, price, valid_from,
               lead(valid_from, 1, 'infinity') OVER (PARTITION BY product_id ORDER BY valid_from) AS valid_to
        FROM product_price_history
    ) h ON h.product_id = i.product_id
       AND o.created_at >= h.valid_from
       AND o.created_at <  h.valid_to
    WHERE h.price <> i.unit_price

    UNION ALL
    SELECT 9, 'order_items_without_price_history', count(*)
    FROM order_items i
    JOIN orders o ON o.id = i.order_id
    LEFT JOIN (
        SELECT product_id, min(valid_from) AS first_valid_from
        FROM product_price_history
        GROUP BY product_id
    ) h ON h.product_id = i.product_id
    WHERE h.first_valid_from IS NULL OR h.first_valid_from > o.created_at

    UNION ALL
    SELECT 10, 'latest_price_history_equals_current_price', count(*)
    FROM products p
    LEFT JOIN (
        SELECT DISTINCT ON (product_id) product_id, price
        FROM product_price_history
        ORDER BY product_id, valid_from DESC
    ) h ON h.product_id = p.id
    WHERE p.price IS DISTINCT FROM h.price

    UNION ALL
    SELECT 11, 'cart_items_reference_active_products', count(*)
    FROM cart_items c
    JOIN products p ON p.id = c.product_id
    WHERE p.status::text <> 'active'
) checks
ORDER BY n;
\endif

\if :has_types
\echo '== module 08 types =='
SELECT check_name, violations
FROM (
    SELECT 1 AS n, 'attributes_are_objects' AS check_name, count(*) AS violations
    FROM products WHERE jsonb_typeof(attributes) <> 'object'

    UNION ALL
    SELECT 2, 'emails_unique_case_insensitively', count(*)
    FROM (SELECT lower(email) FROM users GROUP BY 1 HAVING count(*) > 1) d

    UNION ALL
    SELECT 3, 'promotions_on_existing_products', count(*)
    FROM promotions pr
    WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.id = pr.product_id)
) checks
ORDER BY n;
\endif

\if :has_logic
\echo '== module 09 materialized view is fresh =='
SELECT 'seller_sales_daily_matches_base_tables' AS check_name, count(*) AS violations
FROM (
    (SELECT seller_id, day, orders_count, items_sold, revenue FROM seller_sales_daily
     EXCEPT ALL
     SELECT o.seller_id, (o.created_at AT TIME ZONE 'UTC')::date, count(DISTINCT o.id),
            sum(i.quantity), sum(i.quantity * i.unit_price)
     FROM orders o JOIN order_items i ON i.order_id = o.id
     WHERE o.status::text IN ('paid', 'shipped', 'delivered')
     GROUP BY 1, 2)
    UNION ALL
    (SELECT o.seller_id, (o.created_at AT TIME ZONE 'UTC')::date, count(DISTINCT o.id),
            sum(i.quantity), sum(i.quantity * i.unit_price)
     FROM orders o JOIN order_items i ON i.order_id = o.id
     WHERE o.status::text IN ('paid', 'shipped', 'delivered')
     GROUP BY 1, 2
     EXCEPT ALL
     SELECT seller_id, day, orders_count, items_sold, revenue FROM seller_sales_daily)
) diff;
\endif

\echo '== exact row counts =='
-- query_to_xml() runs a dynamic count(*) per table inside a single SELECT.
SELECT c.relname AS table_name,
       (xpath('/row/n/text()',
              query_to_xml(format('SELECT count(*) AS n FROM bazaar.%I', c.relname), false, true, '')
       ))[1]::text::bigint AS row_count
FROM pg_class c
JOIN pg_namespace ns ON ns.oid = c.relnamespace
WHERE ns.nspname = 'bazaar'
  AND c.relkind IN ('r', 'p')        -- tables and partitioned tables
  AND NOT c.relispartition           -- count partitions through their parent
ORDER BY c.relname;
