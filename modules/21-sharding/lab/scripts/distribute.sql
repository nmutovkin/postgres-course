-- =============================================================================
-- distribute.sql  --  Module 21 lab: turn the 2101-shaped Bazaar into a Citus
-- cluster.  Run on the coordinator after load-bazaar.sh and citus-init-nodes.sh:
--
--   docker exec -i m21-coord psql -U postgres -d bazaar -v ON_ERROR_STOP=1 < scripts/distribute.sql
--
-- Decisions (README section 13 and project.md justify each one):
--   reference   users, sellers, categories, addresses   (small, read everywhere, joined to everything)
--   distributed by seller_id, all CO-LOCATED with products:
--               products, inventory, orders, order_items, payments, reviews,
--               product_price_history, promotions
--   distributed by user_id (its own co-location group): cart_items
--   local on the coordinator: events (+ partitions), audit_log, jobs
-- =============================================================================

SET search_path = bazaar, public;

-- A distributed query opens connections to the workers AS THE CURRENT ROLE.
-- Migrations run as  SET ROLE bazaar_owner  (module 15), and REFRESH
-- MATERIALIZED VIEW runs as the view's owner, also bazaar_owner; a NOLOGIN
-- role then fails with  FATAL: role "bazaar_owner" is not permitted to log in
-- on the worker. Citus propagates ALTER ROLE to every node. (The lab trusts
-- connections inside its network; in production this role would also need a
-- password in .pgpass or in pg_dist_authinfo.)
ALTER ROLE bazaar_owner LOGIN;

-- Module 15's seller policies read the setting app.seller_id (SET LOCAL by the
-- seller portal). Citus does not forward settings to the workers by default,
-- so on the shards the setting is empty and a seller sees NOTHING. 'local'
-- forwards every SET LOCAL to the worker connections of the transaction.
ALTER DATABASE bazaar SET citus.propagate_set_commands = 'local';

-- 32 shards per distributed table (the default, written out for clarity):
-- many more shards than nodes, so shards can later be MOVED to new nodes.
SET citus.shard_count = 32;

-- Distributing a table that has foreign keys to reference tables makes Citus
-- run all DDL over ONE connection per worker; in the default parallel mode it
-- refuses with "cannot execute parallel DDL ... because there is a foreign key".
SET citus.multi_shard_modify_mode = 'sequential';

-- By default, a foreign key from a plain local table to a new reference table
-- makes Citus silently convert the local table into a "Citus managed local
-- table". Converting such a table to a distributed table later fails for
-- tables with identity columns ("cannot complete operation on a table with
-- identity column"), so switch the automatic conversion off: every table will
-- get an explicit role below anyway.
SET citus.enable_local_reference_table_foreign_keys = off;

-- Citus refuses to distribute tables that have triggers unless told that we
-- have checked them: a trigger runs on the WORKER, against one shard, and
-- cannot safely touch other tables. We keep only the set_updated_at triggers
-- (pure BEFORE ROW, touch nothing else) and drop the rest:
SET citus.enable_unsafe_triggers = on;

-- (a) audit triggers write to audit_log, which lives only on the coordinator:
--     on a worker they fail with  relation "audit_log" does not exist.
DROP TRIGGER products_audit ON products;
DROP TRIGGER orders_audit   ON orders;

-- (b) the orders.total triggers are statement-level triggers with TRANSITION
--     TABLES (REFERENCING NEW TABLE AS new_items); Citus cannot run them on
--     shards:  executor could not find named tuplestore "new_items".
--     The sharded checkout (place_order_sharded) computes totals itself.
DROP TRIGGER order_items_total_ins ON order_items;
DROP TRIGGER order_items_total_upd ON order_items;
DROP TRIGGER order_items_total_del ON order_items;

-- (c) the 2101 compatibility triggers look up the PARENT row in another table
--     (orders / products) to fill seller_id: a query against a distributed
--     table from inside a shard. On Citus the application passes seller_id.
DROP TRIGGER order_items_fill_seller_id            ON order_items;
DROP TRIGGER payments_fill_seller_id               ON payments;
DROP TRIGGER inventory_fill_seller_id              ON inventory;
DROP TRIGGER reviews_fill_seller_id                ON reviews;
DROP TRIGGER product_price_history_fill_seller_id  ON product_price_history;
DROP TRIGGER promotions_fill_seller_id             ON promotions;

\timing on

-- -----------------------------------------------------------------------------
-- 1. Reference tables: one shard, a full copy on every node (coordinator too).
--    Order matters: a reference table may only reference other reference
--    tables, so users goes first (sellers and addresses point at it).
-- -----------------------------------------------------------------------------
SELECT create_reference_table('users');
SELECT create_reference_table('sellers');
SELECT create_reference_table('categories');
SELECT create_reference_table('addresses');

-- -----------------------------------------------------------------------------
-- 2. The seller co-location group. products creates the group; every other
--    table joins it, so shard N of each table holds the same seller_id hash
--    range and lives on the same node.
-- -----------------------------------------------------------------------------
SELECT create_distributed_table('products',              'seller_id');
SELECT create_distributed_table('inventory',             'seller_id', colocate_with => 'products');
SELECT create_distributed_table('orders',                'seller_id', colocate_with => 'products');
SELECT create_distributed_table('order_items',           'seller_id', colocate_with => 'products');
SELECT create_distributed_table('payments',              'seller_id', colocate_with => 'products');
SELECT create_distributed_table('reviews',               'seller_id', colocate_with => 'products');
SELECT create_distributed_table('product_price_history', 'seller_id', colocate_with => 'products');
SELECT create_distributed_table('promotions',            'seller_id', colocate_with => 'products');

-- -----------------------------------------------------------------------------
-- 3. cart_items belongs to a user and spans sellers: distribute it by user_id
--    in its own co-location group (2101 already dropped its FK to products).
-- -----------------------------------------------------------------------------
SELECT create_distributed_table('cart_items', 'user_id', colocate_with => 'none');

\timing off

-- The local copies of the data are now invisible but still on disk: remove them.
SET client_min_messages = warning;   -- (it lists every FK cascade: harmless, only the local copies go)
SELECT truncate_local_data_after_distributing_table(t)
FROM unnest(ARRAY['users', 'sellers', 'categories', 'addresses', 'products',
                  'inventory', 'orders', 'order_items', 'payments', 'reviews',
                  'product_price_history', 'promotions', 'cart_items']) AS t
\g /dev/null
RESET client_min_messages;

SELECT table_name, citus_table_type, distribution_column, colocation_id, shard_count
FROM citus_tables
ORDER BY colocation_id, table_name::text;
