-- Exercise 11: search_facets(q) -- result counts per root category and per
-- second-level category, for the facet sidebar of the search page.
SET search_path = bazaar, public;

CREATE OR REPLACE FUNCTION search_facets(q text)
RETURNS TABLE (root_name text, category_name text, products bigint)
LANGUAGE sql
STABLE
AS $$
    WITH RECURSIVE tree AS (
        -- every category with its root and its level-2 ancestor
        SELECT c.id, c.id AS root_id, NULL::int AS level2_id
        FROM categories c
        WHERE c.parent_id IS NULL
        UNION ALL
        SELECT c.id, t.root_id, coalesce(t.level2_id, c.id)
        FROM categories c
        JOIN tree t ON c.parent_id = t.id
    ), hits AS (
        -- aggregate early: at most 69 groups leave this step
        SELECT p.category_id, count(*) AS n
        FROM products p
        WHERE p.search_vector @@ websearch_to_tsquery('english', q)
          AND p.status = 'active'
        GROUP BY p.category_id
    ), facets AS (
        SELECT r.name                AS root_name,
               l2.name               AS category_name,   -- NULL on the root's total row
               sum(h.n)::bigint      AS products,
               GROUPING(l2.name)     AS is_total
        FROM hits h
        JOIN tree t ON t.id = h.category_id
        JOIN categories r ON r.id = t.root_id
        LEFT JOIN categories l2 ON l2.id = t.level2_id
        GROUP BY GROUPING SETS ((r.name), (r.name, l2.name))
    )
    SELECT root_name, category_name, products
    FROM facets
    WHERE NOT (is_total = 0 AND category_name IS NULL)   -- products sitting directly in a root
    ORDER BY max(products) FILTER (WHERE is_total = 1) OVER (PARTITION BY root_name) DESC,
             root_name,
             is_total DESC,
             products DESC,
             category_name
$$;
