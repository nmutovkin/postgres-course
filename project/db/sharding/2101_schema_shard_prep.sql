-- =============================================================================
-- 2101_schema_shard_prep.sql  --  Module 21: Sharding
-- -----------------------------------------------------------------------------
-- Makes the Bazaar schema "shard-ready" with seller_id as the shard key
-- (distribution column), as required for co-location in Citus.
--
-- Why seller_id? One order belongs to exactly one seller (design decision #1),
-- so a seller's products, stock, orders, items, payments, reviews, price
-- history and promotions can all live on the same shard. Checkout, fulfilment
-- and the seller dashboard then become single-shard operations, and joins
-- between these tables on (seller_id, ...) never leave a node.
--
-- The rules a distributed (Citus) schema must follow, and what this file does:
--
--   1. Every PRIMARY KEY / UNIQUE / EXCLUDE constraint of a distributed table
--      must contain the distribution column (a shard can only check
--      uniqueness among its own rows).
--        products               PK (id)                 -> (seller_id, id)
--        orders                 PK (id)                 -> (seller_id, id)
--        inventory              PK (product_id)         -> (seller_id, product_id)
--        order_items            PK (order_id, product_id) -> (seller_id, order_id, product_id)
--        payments, reviews, promotions  PK (id)         -> (seller_id, id)
--        product_price_history  PK (product_id, valid_from) -> (seller_id, product_id, valid_from)
--        reviews                UNIQUE (product_id, user_id) -> (seller_id, product_id, user_id)
--        promotions             EXCLUDE (product_id =, active_during &&)
--                                 -> EXCLUDE (seller_id =, product_id =, active_during &&)
--      The ids stay identity columns, so they remain unique in practice; the
--      database just no longer *enforces* uniqueness of id alone.
--
--   2. A foreign key between two distributed tables must include the
--      distribution column on both sides (so the check is shard-local).
--      Hence seller_id is ADDED (and backfilled) to inventory, order_items,
--      payments, reviews, product_price_history and promotions, and their FKs
--      become (seller_id, product_id) -> products (seller_id, id) and
--      (seller_id, order_id) -> orders (seller_id, id).
--
--   3. Foreign keys from a distributed table to a REFERENCE table (a small
--      table replicated to every node) are fine. Planned reference tables:
--      users, sellers, categories, addresses. FKs such as orders.user_id ->
--      users stay unchanged.
--
--   4. Tables that do not fit the seller shard key:
--        cart_items  belongs to a USER and spans sellers; it would be
--                    distributed by user_id (not co-located with products),
--                    and Citus does not support FKs between non-co-located
--                    distributed tables, so cart_items_product_id_fkey is
--                    dropped. (Checkout reads the cart once and calls
--                    place_order(), which validates every product.)
--        events      append-only analytics, would be distributed by session_id
--                    or user_id; it already has no FKs.
--        audit_log, jobs  stay local to the coordinator.
--
-- Compatibility: code written before this migration inserts child rows
-- without seller_id (e.g. place_order() inserts order_items). BEFORE INSERT
-- triggers fill seller_id from the parent row when it is NULL, so that code
-- keeps working. On a real Citus cluster you would instead make the
-- application pass seller_id explicitly (it always knows it) and drop these
-- triggers: a trigger that looks up another table is a cross-shard query.
--
-- This migration rewrites large tables (backfill UPDATEs) and takes ACCESS
-- EXCLUSIVE locks: it is an offline migration. Module 26 shows how to do such
-- changes online (expand/contract, NOT VALID constraints, batches).
-- =============================================================================

SET search_path = bazaar, public;
SET maintenance_work_mem = '512MB';

BEGIN;

-- Skip user triggers (audit, updated_at, totals) during the backfill: we are
-- only copying an existing value into a new column. Requires superuser, so it
-- is set before switching role.
SET LOCAL session_replication_role = replica;
-- Run the DDL as the schema owner (module 15): new objects are owned by
-- bazaar_owner and pick up its default privileges.
SET LOCAL ROLE bazaar_owner;

-- -----------------------------------------------------------------------------
-- 1. Drop the foreign keys that point at the old single-column keys.
-- -----------------------------------------------------------------------------
ALTER TABLE inventory             DROP CONSTRAINT inventory_product_id_fkey;
ALTER TABLE order_items           DROP CONSTRAINT order_items_order_id_fkey;
ALTER TABLE order_items           DROP CONSTRAINT order_items_product_id_fkey;
ALTER TABLE payments              DROP CONSTRAINT payments_order_id_fkey;
ALTER TABLE reviews               DROP CONSTRAINT reviews_product_id_fkey;
ALTER TABLE product_price_history DROP CONSTRAINT product_price_history_product_id_fkey;
ALTER TABLE promotions            DROP CONSTRAINT promotions_product_id_fkey;
ALTER TABLE cart_items            DROP CONSTRAINT cart_items_product_id_fkey;   -- see note 4 above

-- -----------------------------------------------------------------------------
-- 2. Add seller_id to the child tables and backfill it from the parent.
-- -----------------------------------------------------------------------------
ALTER TABLE inventory             ADD COLUMN seller_id bigint;
ALTER TABLE order_items           ADD COLUMN seller_id bigint;
ALTER TABLE payments              ADD COLUMN seller_id bigint;
ALTER TABLE reviews               ADD COLUMN seller_id bigint;
ALTER TABLE product_price_history ADD COLUMN seller_id bigint;
ALTER TABLE promotions            ADD COLUMN seller_id bigint;

UPDATE inventory t             SET seller_id = p.seller_id FROM products p WHERE p.id = t.product_id;
UPDATE order_items t           SET seller_id = o.seller_id FROM orders o   WHERE o.id = t.order_id;
UPDATE payments t              SET seller_id = o.seller_id FROM orders o   WHERE o.id = t.order_id;
UPDATE reviews t               SET seller_id = p.seller_id FROM products p WHERE p.id = t.product_id;
UPDATE product_price_history t SET seller_id = p.seller_id FROM products p WHERE p.id = t.product_id;
UPDATE promotions t            SET seller_id = p.seller_id FROM products p WHERE p.id = t.product_id;

-- SET NOT NULL scans the table to verify; it fails loudly if a backfill missed rows.
ALTER TABLE inventory             ALTER COLUMN seller_id SET NOT NULL;
ALTER TABLE order_items           ALTER COLUMN seller_id SET NOT NULL;
ALTER TABLE payments              ALTER COLUMN seller_id SET NOT NULL;
ALTER TABLE reviews               ALTER COLUMN seller_id SET NOT NULL;
ALTER TABLE product_price_history ALTER COLUMN seller_id SET NOT NULL;
ALTER TABLE promotions            ALTER COLUMN seller_id SET NOT NULL;

-- -----------------------------------------------------------------------------
-- 3. Shard-key-aware primary keys, unique and exclusion constraints.
-- -----------------------------------------------------------------------------
ALTER TABLE products DROP CONSTRAINT products_pkey,
                     ADD CONSTRAINT products_pkey PRIMARY KEY (seller_id, id);
ALTER TABLE orders   DROP CONSTRAINT orders_pkey,
                     ADD CONSTRAINT orders_pkey PRIMARY KEY (seller_id, id);
ALTER TABLE inventory DROP CONSTRAINT inventory_pkey,
                      ADD CONSTRAINT inventory_pkey PRIMARY KEY (seller_id, product_id);
ALTER TABLE order_items DROP CONSTRAINT order_items_pkey,
                        ADD CONSTRAINT order_items_pkey PRIMARY KEY (seller_id, order_id, product_id);
ALTER TABLE payments DROP CONSTRAINT payments_pkey,
                     ADD CONSTRAINT payments_pkey PRIMARY KEY (seller_id, id);
ALTER TABLE reviews  DROP CONSTRAINT reviews_pkey,
                     ADD CONSTRAINT reviews_pkey PRIMARY KEY (seller_id, id),
                     DROP CONSTRAINT reviews_product_id_user_id_key,
                     ADD CONSTRAINT reviews_seller_id_product_id_user_id_key UNIQUE (seller_id, product_id, user_id);
ALTER TABLE product_price_history DROP CONSTRAINT product_price_history_pkey,
                                  ADD CONSTRAINT product_price_history_pkey PRIMARY KEY (seller_id, product_id, valid_from);
ALTER TABLE promotions DROP CONSTRAINT promotions_pkey,
                       ADD CONSTRAINT promotions_pkey PRIMARY KEY (seller_id, id),
                       DROP CONSTRAINT promotions_no_overlap,
                       ADD CONSTRAINT promotions_no_overlap
                           EXCLUDE USING gist (seller_id WITH =, product_id WITH =, active_during WITH &&);

-- -----------------------------------------------------------------------------
-- 4. Composite foreign keys (shard-local checks).
-- -----------------------------------------------------------------------------
ALTER TABLE inventory ADD CONSTRAINT inventory_product_fkey
    FOREIGN KEY (seller_id, product_id) REFERENCES products (seller_id, id) ON DELETE CASCADE;
ALTER TABLE order_items ADD CONSTRAINT order_items_order_fkey
    FOREIGN KEY (seller_id, order_id) REFERENCES orders (seller_id, id) ON DELETE CASCADE;
ALTER TABLE order_items ADD CONSTRAINT order_items_product_fkey
    FOREIGN KEY (seller_id, product_id) REFERENCES products (seller_id, id);
ALTER TABLE payments ADD CONSTRAINT payments_order_fkey
    FOREIGN KEY (seller_id, order_id) REFERENCES orders (seller_id, id);
ALTER TABLE reviews ADD CONSTRAINT reviews_product_fkey
    FOREIGN KEY (seller_id, product_id) REFERENCES products (seller_id, id) ON DELETE CASCADE;
ALTER TABLE product_price_history ADD CONSTRAINT product_price_history_product_fkey
    FOREIGN KEY (seller_id, product_id) REFERENCES products (seller_id, id) ON DELETE CASCADE;
ALTER TABLE promotions ADD CONSTRAINT promotions_product_fkey
    FOREIGN KEY (seller_id, product_id) REFERENCES products (seller_id, id) ON DELETE CASCADE;

-- Indexes for the new FKs where no index starts with the FK columns
-- (replacing the single-column FK indexes from module 11).
DROP INDEX order_items_product_id_idx;
CREATE INDEX order_items_seller_id_product_id_idx ON order_items (seller_id, product_id);
DROP INDEX payments_order_id_idx;
CREATE INDEX payments_seller_id_order_id_idx ON payments (seller_id, order_id);
-- reviews (seller_id, product_id): covered by the new UNIQUE constraint
-- inventory, product_price_history: covered by their primary keys
-- promotions: covered by the GiST index of the exclusion constraint

-- -----------------------------------------------------------------------------
-- 5. Keep existing code working: fill seller_id from the parent when omitted.
-- -----------------------------------------------------------------------------
CREATE FUNCTION fill_seller_id_from_order()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    SELECT o.seller_id INTO NEW.seller_id FROM orders o WHERE o.id = NEW.order_id;
    RETURN NEW;   -- if the order does not exist, seller_id stays NULL and NOT NULL fails
END;
$$;

CREATE FUNCTION fill_seller_id_from_product()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    SELECT p.seller_id INTO NEW.seller_id FROM products p WHERE p.id = NEW.product_id;
    RETURN NEW;
END;
$$;

CREATE TRIGGER order_items_fill_seller_id BEFORE INSERT ON order_items
    FOR EACH ROW WHEN (NEW.seller_id IS NULL) EXECUTE FUNCTION fill_seller_id_from_order();
CREATE TRIGGER payments_fill_seller_id BEFORE INSERT ON payments
    FOR EACH ROW WHEN (NEW.seller_id IS NULL) EXECUTE FUNCTION fill_seller_id_from_order();
CREATE TRIGGER inventory_fill_seller_id BEFORE INSERT ON inventory
    FOR EACH ROW WHEN (NEW.seller_id IS NULL) EXECUTE FUNCTION fill_seller_id_from_product();
CREATE TRIGGER reviews_fill_seller_id BEFORE INSERT ON reviews
    FOR EACH ROW WHEN (NEW.seller_id IS NULL) EXECUTE FUNCTION fill_seller_id_from_product();
CREATE TRIGGER product_price_history_fill_seller_id BEFORE INSERT ON product_price_history
    FOR EACH ROW WHEN (NEW.seller_id IS NULL) EXECUTE FUNCTION fill_seller_id_from_product();
CREATE TRIGGER promotions_fill_seller_id BEFORE INSERT ON promotions
    FOR EACH ROW WHEN (NEW.seller_id IS NULL) EXECUTE FUNCTION fill_seller_id_from_product();

-- The orders.total trigger now addresses orders by the full key
-- (seller_id, id), which keeps the UPDATE on the order's own shard.
CREATE OR REPLACE FUNCTION order_items_maintain_total()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE orders o
        SET total = o.total + d.delta
        FROM (SELECT seller_id, order_id, sum(quantity * unit_price) AS delta
              FROM new_items GROUP BY seller_id, order_id) d
        WHERE o.seller_id = d.seller_id AND o.id = d.order_id AND d.delta <> 0;
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE orders o
        SET total = o.total - d.delta
        FROM (SELECT seller_id, order_id, sum(quantity * unit_price) AS delta
              FROM old_items GROUP BY seller_id, order_id) d
        WHERE o.seller_id = d.seller_id AND o.id = d.order_id AND d.delta <> 0;
    ELSE
        UPDATE orders o
        SET total = o.total + d.delta
        FROM (SELECT seller_id, order_id, sum(delta) AS delta
              FROM (SELECT seller_id, order_id, quantity * unit_price AS delta FROM new_items
                    UNION ALL
                    SELECT seller_id, order_id, -(quantity * unit_price) FROM old_items) c
              GROUP BY seller_id, order_id) d
        WHERE o.seller_id = d.seller_id AND o.id = d.order_id AND d.delta <> 0;
    END IF;
    RETURN NULL;
END;
$$;

-- -----------------------------------------------------------------------------
-- 6. RLS policies can now use the column directly instead of a subquery.
-- -----------------------------------------------------------------------------
DROP POLICY inventory_seller ON inventory;
CREATE POLICY inventory_seller ON inventory
    FOR ALL
    TO bazaar_seller
    USING (seller_id = current_seller_id())
    WITH CHECK (seller_id = current_seller_id());

DROP POLICY order_items_seller_select ON order_items;
CREATE POLICY order_items_seller_select ON order_items
    FOR SELECT
    TO bazaar_seller
    USING (seller_id = current_seller_id());

COMMIT;

RESET maintenance_work_mem;

-- The backfills rewrote every row of these tables: refresh statistics and
-- the visibility map.
VACUUM (ANALYZE) inventory, order_items, payments, reviews, product_price_history, promotions;
