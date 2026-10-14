-- =============================================================================
-- Module 06 milestone, part 2: seller leaderboard with month-over-month rank
-- change (reference solution).
-- State: ./project/bin/reset.sh 06
--
-- Rules
--   * revenue = sum(orders.total) of paid/shipped/delivered orders, by the
--     calendar month (UTC) of orders.created_at
--   * only sellers with revenue in a month are ranked in that month
--   * rank = dense_rank by revenue, highest first
--   * rank_change = previous calendar month's rank - this month's rank
--     (positive = moved up); NULL when the seller was not ranked in the
--     previous calendar month (shown as 'entered')
-- =============================================================================

WITH monthly AS (
    SELECT seller_id,
           date_trunc('month', created_at)::date AS month,
           count(*)                              AS orders,
           sum(total)                            AS revenue
    FROM orders
    WHERE status IN ('paid', 'shipped', 'delivered')
    GROUP BY seller_id, date_trunc('month', created_at)
),
ranked AS (
    SELECT seller_id, month, orders, revenue,
           dense_rank() OVER (PARTITION BY month ORDER BY revenue DESC) AS rank
    FROM monthly
)
SELECT cur.month,
       cur.rank,
       s.shop_name,
       cur.orders,
       cur.revenue,
       prev.rank                         AS prev_rank,
       CASE
           WHEN prev.rank IS NULL        THEN 'entered'
           WHEN prev.rank > cur.rank     THEN '▲ ' || (prev.rank - cur.rank)
           WHEN prev.rank < cur.rank     THEN '▼ ' || (cur.rank - prev.rank)
           ELSE '='
       END                               AS rank_change
FROM ranked AS cur
JOIN sellers AS s ON s.id = cur.seller_id
LEFT JOIN ranked AS prev
       ON prev.seller_id = cur.seller_id
      AND prev.month = (cur.month - interval '1 month')::date
WHERE cur.month >= date '2025-01-01'
ORDER BY cur.month, cur.rank, s.shop_name;
