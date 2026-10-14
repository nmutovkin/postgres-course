-- =============================================================================
-- 1102_schema_indexes.sql  --  Module 11: Indexes
-- -----------------------------------------------------------------------------
-- 1. Index every foreign-key column that is not already the LEADING column of
--    some index. PostgreSQL indexes the referenced side (it must be a PK or
--    UNIQUE) but never the referencing side. Without these indexes:
--      * joins from parent to children scan the whole child table, and
--      * every DELETE (or key UPDATE) on the parent scans the child table to
--        check for referencing rows (ON DELETE NO ACTION / CASCADE / SET NULL).
-- 2. Indexes for the hot queries of the storefront and the dashboards.
--
-- Already covered (no new index needed):
--   sellers.user_id            UNIQUE (user_id)
--   products.seller_id         UNIQUE (seller_id, sku)          -- leading column
--   inventory.product_id       PRIMARY KEY
--   order_items.order_id       PRIMARY KEY (order_id, product_id)
--   reviews.product_id         UNIQUE (product_id, user_id)
--   cart_items.user_id         PRIMARY KEY (user_id, product_id)
--   product_price_history      PRIMARY KEY (product_id, valid_from)
--   promotions.product_id      the GiST index behind the EXCLUDE constraint
--   orders.user_id/seller_id   the composite indexes in section 2
--
-- In production you would build these with CREATE INDEX CONCURRENTLY (no
-- write lock, but slower and not allowed inside a transaction block). This
-- file runs on a database nobody else is using, so plain CREATE INDEX is fine.
-- =============================================================================

SET search_path = bazaar, public;
SET maintenance_work_mem = '512MB';   -- faster sorts while building indexes

-- -----------------------------------------------------------------------------
-- 1. Foreign-key indexes
-- -----------------------------------------------------------------------------

-- Children of a category (recursive tree walks: WHERE parent_id = $1).
CREATE INDEX categories_parent_id_idx ON categories (parent_id);

-- "Products in category X" (all statuses; the storefront uses the partial
-- index below) and FK checks when deleting a category.
CREATE INDEX products_category_id_idx ON products (category_id);

-- "Which orders contain product X?", "best sellers", FK checks on products.
CREATE INDEX order_items_product_id_idx ON order_items (product_id);

-- Orders shipped to an address; FK checks when deleting an address.
CREATE INDEX orders_shipping_address_id_idx ON orders (shipping_address_id);

-- A user's address book (the partial unique index only holds default rows).
CREATE INDEX addresses_user_id_idx ON addresses (user_id);

-- Payments of an order: SELECT ... FROM payments WHERE order_id = $1.
CREATE INDEX payments_order_id_idx ON payments (order_id);

-- "All reviews written by user X".
CREATE INDEX reviews_user_id_idx ON reviews (user_id);

-- Cascade from products to cart_items; "how many carts contain product X".
CREATE INDEX cart_items_product_id_idx ON cart_items (product_id);

-- -----------------------------------------------------------------------------
-- 2. Hot queries
-- -----------------------------------------------------------------------------

-- "My orders, newest first":
--   SELECT ... FROM orders WHERE user_id = $1 ORDER BY created_at DESC LIMIT 20;
-- Equality column first, then the sort column: the index returns the rows
-- already in the requested order, so no Sort node and LIMIT stops early.
-- Also serves the FK on orders.user_id (leading column).
CREATE INDEX orders_user_id_created_at_idx ON orders (user_id, created_at DESC);

-- Seller dashboard: "my latest orders" / "orders this month":
--   SELECT ... FROM orders WHERE seller_id = $1 AND created_at >= $2 ORDER BY created_at DESC;
CREATE INDEX orders_seller_id_created_at_idx ON orders (seller_id, created_at DESC);

-- Storefront category pages:
--   SELECT ... FROM products WHERE status = 'active' AND category_id = $1
--   ORDER BY price LIMIT 24;   (or: AND price BETWEEN $2 AND $3)
-- Partial: only active products (~85%) are in the index, and the planner
-- uses it only for queries whose WHERE implies status = 'active'.
CREATE INDEX products_active_category_id_price_idx
    ON products (category_id, price)
    WHERE status = 'active';

-- Attribute filters:  WHERE attributes @> '{"brand": "Voltix", "ram_gb": 16}'
-- jsonb_path_ops indexes hashes of (path, value) pairs: smaller and faster than
-- the default jsonb_ops, but supports only @> (and jsonpath @? / @@), not the
-- key-existence operators ?, ?| and ?&.
CREATE INDEX products_attributes_idx ON products USING gin (attributes jsonb_path_ops);

-- Tag filters:  WHERE tags @> '{gift}'  or  WHERE tags && '{eco,handmade}'
CREATE INDEX products_tags_idx ON products USING gin (tags);

RESET maintenance_work_mem;

-- No ANALYZE needed: statistics are per column, not per index, and 1101
-- already analyzed every table. (Expression indexes are the exception: their
-- expression gets its own statistics at the next ANALYZE.)
