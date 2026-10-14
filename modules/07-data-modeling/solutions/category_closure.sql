-- =============================================================================
-- Module 07, exercise 12: a closure table for the category tree.
-- State: ./project/bin/reset.sh 08. Runs in a transaction and rolls back.
-- =============================================================================
SET search_path = bazaar, public;
BEGIN;

-- One row per (ancestor, descendant) pair, including each category with itself (depth 0).
CREATE TABLE category_closure (
    ancestor_id    int NOT NULL REFERENCES categories (id) ON DELETE CASCADE,
    descendant_id  int NOT NULL REFERENCES categories (id) ON DELETE CASCADE,
    depth          int NOT NULL,
    PRIMARY KEY (ancestor_id, descendant_id),
    CONSTRAINT category_closure_depth_nonnegative CHECK (depth >= 0),
    CONSTRAINT category_closure_self_is_depth_0 CHECK ((ancestor_id = descendant_id) = (depth = 0))
);
-- The PK serves "descendants of X"; this index serves "ancestors of Y".
CREATE INDEX category_closure_descendant_idx ON category_closure (descendant_id, depth);

COMMENT ON TABLE category_closure IS
    'Derived from categories.parent_id: every (ancestor, descendant, depth) pair. Rebuild or maintain on every tree change.';

-- Populate it from the adjacency list with a recursive CTE.
INSERT INTO category_closure (ancestor_id, descendant_id, depth)
WITH RECURSIVE pairs AS (
    SELECT id AS ancestor_id, id AS descendant_id, 0 AS depth
    FROM categories
    UNION ALL
    SELECT p.ancestor_id, c.id, p.depth + 1
    FROM pairs AS p
    JOIN categories AS c ON c.parent_id = p.descendant_id
)
SELECT ancestor_id, descendant_id, depth FROM pairs;

SELECT count(*) AS closure_rows FROM category_closure;

-- Subtree of Books, no recursion.
SELECT c.id, repeat('    ', cc.depth) || c.name AS category, cc.depth
FROM category_closure AS cc
JOIN categories AS c ON c.id = cc.descendant_id
WHERE cc.ancestor_id = 14
ORDER BY cc.depth, c.name;

-- Breadcrumb of product 25, no recursion.
SELECT string_agg(c.name, ' › ' ORDER BY cc.depth DESC) AS breadcrumb
FROM products AS p
JOIN category_closure AS cc ON cc.descendant_id = p.category_id
JOIN categories AS c ON c.id = cc.ancestor_id
WHERE p.id = 25;

-- Add a new leaf "Thrillers" under Fiction (15) and maintain the closure:
-- the new node's ancestors are its parent's ancestors (each one level deeper), plus itself.
WITH new_category AS (
    INSERT INTO categories (parent_id, name, slug)
    VALUES (15, 'Thrillers', 'thrillers')
    RETURNING id, parent_id
)
INSERT INTO category_closure (ancestor_id, descendant_id, depth)
SELECT cc.ancestor_id, n.id, cc.depth + 1
FROM new_category AS n
JOIN category_closure AS cc ON cc.descendant_id = n.parent_id
UNION ALL
SELECT n.id, n.id, 0
FROM new_category AS n
RETURNING *;

-- Check: the closure table equals what a recursive CTE computes now.
WITH RECURSIVE pairs AS (
    SELECT id AS ancestor_id, id AS descendant_id, 0 AS depth FROM categories
    UNION ALL
    SELECT p.ancestor_id, c.id, p.depth + 1
    FROM pairs AS p JOIN categories AS c ON c.parent_id = p.descendant_id
)
SELECT (SELECT count(*) FROM (SELECT * FROM pairs EXCEPT SELECT * FROM category_closure) AS a) AS missing,
       (SELECT count(*) FROM (SELECT * FROM category_closure EXCEPT SELECT * FROM pairs) AS b) AS extra;

ROLLBACK;
