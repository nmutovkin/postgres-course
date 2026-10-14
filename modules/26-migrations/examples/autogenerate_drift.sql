-- Module 26, section 6: drift for the autogenerate demonstration.
-- Apply to a SCRATCH database at head (never to bazaar):
--   createdb + alembic -x dsn=.../bazaar_scratch upgrade head, then
--   psql -d bazaar_scratch < autogenerate_drift.sql
--   alembic -x dsn=.../bazaar_scratch revision --autogenerate -m "autogenerate demo" --rev-id 9999
-- The output is in autogenerate_demo_output.py. Autogenerate describes what to do to the
-- DATABASE to match the METADATA, so every change below shows up reversed, or not at all.
ALTER TABLE reviews RENAME COLUMN body TO content;                 -- seen as drop + add
ALTER TYPE order_status ADD VALUE 'on_hold';                        -- not seen
ALTER TABLE products ALTER COLUMN status SET DEFAULT 'active';     -- seen (compare_server_default)
ALTER TABLE reviews DROP CONSTRAINT reviews_rating_range;           -- same name, new expression:
ALTER TABLE reviews ADD CONSTRAINT reviews_rating_range CHECK (rating BETWEEN 1 AND 10);  -- not seen
ALTER TABLE inventory DROP CONSTRAINT inventory_quantity_nonnegative;  -- seen (checkconstraint_byname)
ALTER TABLE jobs ALTER COLUMN kind TYPE varchar(40);                -- seen (compare_type)
DROP INDEX products_tags_idx;                                       -- seen (but re-created non-concurrently)
CREATE TABLE wishlist (user_id bigint, product_id bigint, PRIMARY KEY (user_id, product_id));  -- seen: DROP TABLE!
DROP POLICY products_seller ON products;                            -- not seen
DROP TRIGGER orders_notify_new ON orders;                           -- not seen
DROP VIEW active_products;                                       -- not seen
