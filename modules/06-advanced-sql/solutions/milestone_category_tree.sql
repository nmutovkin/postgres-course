-- =============================================================================
-- Module 06 milestone, part 1: the category-tree toolkit (reference solution)
-- State: ./project/bin/reset.sh 06
-- Run:   docker exec -i course-pg psql -X -U postgres -d bazaar \
--            -v root_id=14 -v product_id=25 < modules/06-advanced-sql/solutions/milestone_category_tree.sql
-- Without -v, the defaults below are used.
-- =============================================================================

\if :{?root_id}
\else
    \set root_id 1
\endif
\if :{?product_id}
\else
    \set product_id 3
\endif

-- -----------------------------------------------------------------------------
-- 1. Full subtree of :root_id, with depth, id path and readable path.
--    Depth-first order (children right under their parent, siblings by name).
-- -----------------------------------------------------------------------------
WITH RECURSIVE subtree AS (
    SELECT id, parent_id, name,
           0             AS depth,
           ARRAY[id]     AS id_path,
           ARRAY[name]   AS name_path
    FROM categories
    WHERE id = :root_id
    UNION ALL
    SELECT c.id, c.parent_id, c.name,
           s.depth + 1,
           s.id_path || c.id,
           s.name_path || c.name
    FROM categories AS c
    JOIN subtree AS s ON c.parent_id = s.id
) CYCLE id SET is_cycle USING cycle_path
SELECT id,
       repeat('    ', depth) || name           AS category,
       depth,
       id_path,
       array_to_string(name_path, ' > ')       AS path
FROM subtree
WHERE NOT is_cycle
ORDER BY name_path;

-- -----------------------------------------------------------------------------
-- 2. Breadcrumb for :product_id: root first, the product's own category last.
-- -----------------------------------------------------------------------------
WITH RECURSIVE ancestors AS (
    SELECT c.id, c.parent_id, c.name, 0 AS up
    FROM products AS p
    JOIN categories AS c ON c.id = p.category_id
    WHERE p.id = :product_id
    UNION ALL
    SELECT c.id, c.parent_id, c.name, a.up + 1
    FROM categories AS c
    JOIN ancestors AS a ON c.id = a.parent_id
    WHERE a.up < 50                      -- safety net against a corrupted tree
)
SELECT p.id    AS product_id,
       p.title,
       (SELECT string_agg(a.name, ' › ' ORDER BY a.up DESC) FROM ancestors AS a) AS breadcrumb
FROM products AS p
WHERE p.id = :product_id;

-- -----------------------------------------------------------------------------
-- 3. Category revenue rolled up through the tree.
--    own_revenue    = revenue of products placed directly in the category
--    total_revenue  = own_revenue of the category and all its descendants
--    Revenue = sum(quantity * unit_price) over items of paid/shipped/delivered orders.
-- -----------------------------------------------------------------------------
WITH RECURSIVE
closure AS (                      -- every (ancestor, descendant) pair, including (c, c)
    SELECT id AS ancestor_id, id AS descendant_id
    FROM categories
    UNION ALL
    SELECT cl.ancestor_id, c.id
    FROM closure AS cl
    JOIN categories AS c ON c.parent_id = cl.descendant_id
),
tree AS (                         -- depth and path for display
    SELECT id, name, 0 AS depth, ARRAY[name] AS name_path
    FROM categories
    WHERE parent_id IS NULL
    UNION ALL
    SELECT c.id, c.name, t.depth + 1, t.name_path || c.name
    FROM categories AS c
    JOIN tree AS t ON c.parent_id = t.id
),
own AS (                          -- revenue per category, products placed directly in it
    SELECT p.category_id, sum(oi.quantity * oi.unit_price) AS revenue
    FROM order_items AS oi
    JOIN orders   AS o ON o.id = oi.order_id
    JOIN products AS p ON p.id = oi.product_id
    WHERE o.status IN ('paid', 'shipped', 'delivered')
    GROUP BY p.category_id
)
SELECT t.id,
       repeat('    ', t.depth) || t.name               AS category,
       coalesce(max(own_here.revenue), 0)              AS own_revenue,
       coalesce(sum(own_below.revenue), 0)             AS total_revenue
FROM tree AS t
JOIN closure AS cl             ON cl.ancestor_id = t.id
LEFT JOIN own AS own_below     ON own_below.category_id = cl.descendant_id
LEFT JOIN own AS own_here      ON own_here.category_id = t.id
GROUP BY t.id, t.name, t.depth, t.name_path
ORDER BY t.name_path;
