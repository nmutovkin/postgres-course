-- Exercise 5: rewrites that run on Citus without repartition joins
-- (state: citus-build.sh small).
SET search_path = bazaar, public;

-- a: add seller_id to the join -> co-located join, 32 independent tasks
SELECT p.seller_id, count(*) AS in_stock
FROM products p
JOIN inventory i ON i.seller_id = p.seller_id AND i.product_id = p.id
WHERE p.status = 'active' AND i.quantity - i.reserved > 0
GROUP BY p.seller_id
ORDER BY 1;

-- b: the same for payments -> orders
SELECT pay.status, count(*), sum(pay.amount)
FROM payments pay
JOIN orders o ON o.seller_id = pay.seller_id AND o.id = pay.order_id
WHERE o.status = 'refunded'
GROUP BY pay.status
ORDER BY 1;

-- c: correlated subquery under a reference table -> outer join + GROUP BY
SELECT s.shop_name, count(p.id) AS active
FROM sellers s
LEFT JOIN products p ON p.seller_id = s.id AND p.status = 'active'
GROUP BY s.id, s.shop_name
ORDER BY s.id;

-- d: correlated EXISTS -> join to a DISTINCT subquery (or a non-correlated IN)
SELECT u.id, u.full_name
FROM users u
JOIN (SELECT DISTINCT user_id FROM orders WHERE status = 'pending') o ON o.user_id = u.id
ORDER BY u.id;

SELECT u.id, u.full_name
FROM users u
WHERE u.id IN (SELECT user_id FROM orders WHERE status = 'pending')
ORDER BY u.id;

-- e1: fixed depth (the tree has 3 levels)
SELECT coalesce(c3.id, c2.id, c1.id) AS root, count(*)
FROM products p
JOIN categories c1      ON c1.id = p.category_id
LEFT JOIN categories c2 ON c2.id = c1.parent_id
LEFT JOIN categories c3 ON c3.id = c2.parent_id
GROUP BY 1
ORDER BY 1;

-- e2: keep the recursion, but in a MATERIALIZED CTE that Citus plans and runs
--     first (on reference data only); its result is then joined on the shards
WITH roots AS MATERIALIZED (
    WITH RECURSIVE tree AS (
        SELECT id, id AS root FROM categories WHERE parent_id IS NULL
        UNION ALL
        SELECT c.id, t.root FROM categories c JOIN tree t ON c.parent_id = t.id
    )
    SELECT * FROM tree
)
SELECT r.root, count(*)
FROM products p
JOIN roots r ON r.id = p.category_id
GROUP BY r.root
ORDER BY 1;
