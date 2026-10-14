-- =============================================================================
-- seller_statement.sql  --  Module 05 milestone (reference solution)
-- -----------------------------------------------------------------------------
-- The monthly seller statement: for every shop and every month of a period,
--   orders   orders that took money: paid, shipped, delivered or refunded
--   items    units in those orders
--   gross    sum of those orders' totals
--   refunds  sum of the totals of refunded orders
--   net      gross - refunds (= revenue: paid + shipped + delivered)
-- Every shop gets a row for every month, with zeros when nothing happened.
-- Part 1 is the plain statement; part 2 adds a subtotal per shop and a grand
-- total with GROUP BY ROLLUP.
--
-- Booking rule: an order (and its refund) is booked in the UTC month in which
-- it was created. The schema at this point records no refund date; module 07's
-- payments table makes "refunds in the month they happened" possible.
--
-- Run it (state: ./project/bin/reset.sh 05):
--   docker exec -i course-pg psql -X -U postgres -d bazaar < modules/05-aggregation/solutions/seller_statement.sql
-- Parameters (psql variables, override with -v name=value):
--   period_start  first month of the period, inclusive     (default 2025-01-01)
--   period_end    first month AFTER the period, exclusive  (default 2025-07-01)
-- =============================================================================

\set ON_ERROR_STOP on
\set QUIET on
\pset null '∅'
\set QUIET off
\if :{?period_start}
\else
    \set period_start 2025-01-01
\endif
\if :{?period_end}
\else
    \set period_end 2025-07-01
\endif

-- -----------------------------------------------------------------------------
-- Part 1: one row per shop and month.
--   * sellers CROSS JOIN the calendar = the full grid (no missing months/shops)
--   * the order lines are aggregated per order FIRST (one row per order), so
--     joining them to orders cannot fan out and double count orders.total
--   * the orders + per-order units join is parenthesized, so the LEFT JOIN
--     treats "an order together with its units" as the optional part, and all
--     order conditions sit in ON (a WHERE would drop the zero months)
-- -----------------------------------------------------------------------------
\echo '== Monthly seller statement'
SELECT s.id                                                          AS seller_id,
       s.shop_name,
       to_char(g.month, 'YYYY-MM')                                   AS month,
       count(o.id)                                                   AS orders,
       coalesce(sum(u.units), 0)                                     AS items,
       coalesce(sum(o.total), 0)                                     AS gross,
       coalesce(sum(o.total) FILTER (WHERE o.status = 'refunded'), 0) AS refunds,
       coalesce(sum(o.total), 0)
         - coalesce(sum(o.total) FILTER (WHERE o.status = 'refunded'), 0) AS net
FROM sellers AS s
CROSS JOIN generate_series(:'period_start'::timestamptz,
                           :'period_end'::timestamptz - interval '1 month',
                           interval '1 month') AS g (month)
LEFT JOIN (
        orders AS o
        JOIN (
            SELECT order_id, sum(quantity) AS units
            FROM order_items
            GROUP BY order_id
        ) AS u ON u.order_id = o.id
     )
       ON o.seller_id   = s.id
      AND o.created_at >= g.month
      AND o.created_at <  g.month + interval '1 month'
      AND o.status IN ('paid', 'shipped', 'delivered', 'refunded')
GROUP BY s.id, g.month
ORDER BY s.id, g.month;

-- -----------------------------------------------------------------------------
-- Part 2: the same statement with a subtotal per shop and a grand total.
--   * ROLLUP ((s.id, s.shop_name), g.month) treats id + name as one unit, so
--     the rollup levels are: shop+month, shop, everything
--   * GROUPING() labels the subtotal rows and sorts them after their details
-- -----------------------------------------------------------------------------
\echo '== Monthly seller statement with subtotals'
SELECT CASE WHEN GROUPING(s.id) = 1 THEN 'ALL SHOPS' ELSE s.shop_name END     AS shop,
       CASE WHEN GROUPING(s.id) = 1 THEN 'total'
            WHEN GROUPING(g.month) = 1 THEN 'subtotal'
            ELSE to_char(g.month, 'YYYY-MM') END                             AS month,
       count(o.id)                                                           AS orders,
       coalesce(sum(u.units), 0)                                             AS items,
       coalesce(sum(o.total), 0)                                             AS gross,
       coalesce(sum(o.total) FILTER (WHERE o.status = 'refunded'), 0)        AS refunds,
       coalesce(sum(o.total), 0)
         - coalesce(sum(o.total) FILTER (WHERE o.status = 'refunded'), 0)    AS net
FROM sellers AS s
CROSS JOIN generate_series(:'period_start'::timestamptz,
                           :'period_end'::timestamptz - interval '1 month',
                           interval '1 month') AS g (month)
LEFT JOIN (
        orders AS o
        JOIN (
            SELECT order_id, sum(quantity) AS units
            FROM order_items
            GROUP BY order_id
        ) AS u ON u.order_id = o.id
     )
       ON o.seller_id   = s.id
      AND o.created_at >= g.month
      AND o.created_at <  g.month + interval '1 month'
      AND o.status IN ('paid', 'shipped', 'delivered', 'refunded')
GROUP BY ROLLUP ((s.id, s.shop_name), g.month)
ORDER BY GROUPING(s.id), s.id, GROUPING(g.month), g.month;
