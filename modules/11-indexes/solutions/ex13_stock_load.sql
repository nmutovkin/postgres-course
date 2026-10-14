-- pgbench script for exercise 13: a stream of small stock updates on inventory.
-- Each transaction adjusts the stock of one random product.
\set pid random(1, 200000)
UPDATE bazaar.inventory
SET quantity = quantity + 1
WHERE product_id = :pid;
