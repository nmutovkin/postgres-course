-- =============================================================================
-- drop_1102_indexes.sql  --  Module 11
-- -----------------------------------------------------------------------------
-- Takes a database at state 12 (large dataset + project/db/1102_schema_indexes.sql)
-- back to "large dataset, no module-11 indexes", the state the theory of
-- Module 11 runs in. Re-apply 1102_schema_indexes.sql to get state 12 back.
--
--   ./project/bin/psql.sh bazaar < modules/11-indexes/examples/drop_1102_indexes.sql
-- =============================================================================
SET search_path = bazaar, public;

DROP INDEX IF EXISTS
    categories_parent_id_idx,
    products_category_id_idx,
    order_items_product_id_idx,
    orders_shipping_address_id_idx,
    addresses_user_id_idx,
    payments_order_id_idx,
    reviews_user_id_idx,
    cart_items_product_id_idx,
    orders_user_id_created_at_idx,
    orders_seller_id_created_at_idx,
    products_active_category_id_price_idx,
    products_attributes_idx,
    products_tags_idx;
