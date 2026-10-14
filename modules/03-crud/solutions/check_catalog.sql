-- =============================================================================
-- check_catalog.sql  --  Module 03 milestone acceptance check
-- -----------------------------------------------------------------------------
-- Prints seller 6's catalogue with stock, plus two guard rails: the other
-- sellers' products are untouched, and every product has an inventory row.
-- Compare the output with the expected results in project.md.
--
--   docker exec -i course-pg psql -X -U postgres -d bazaar \
--       < modules/03-crud/solutions/check_catalog.sql
-- (Joins appear here before module 04 teaches them; you only need to read
--  the output.)
-- =============================================================================
\set QUIET on
\pset footer off
\pset null '(null)'
SET search_path = bazaar, public;

SELECT p.sku,
       p.title,
       p.description IS NULL          AS no_description,
       c.slug                         AS category,
       p.price,
       p.status,
       i.quantity,
       p.updated_at > p.created_at    AS product_changed
FROM products AS p
JOIN categories AS c ON c.id = p.category_id
LEFT JOIN inventory AS i ON i.product_id = p.id
WHERE p.seller_id = 6
ORDER BY p.sku;

SELECT (SELECT count(*) FROM products WHERE seller_id <> 6)            AS other_products,
       (SELECT sum(price) FROM products WHERE seller_id <> 6)          AS other_price_sum,
       (SELECT count(*) FROM products AS p
         WHERE NOT EXISTS (SELECT 1 FROM inventory AS i WHERE i.product_id = p.id))
                                                                        AS products_without_stock;
