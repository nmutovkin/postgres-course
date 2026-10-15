-- =============================================================================
-- 1501_schema_security.sql  --  Module 15: Security and Row-Level Security
-- -----------------------------------------------------------------------------
-- Roles (cluster-wide objects!):
--   bazaar_owner     NOLOGIN  owns the schema and every object in it. Nobody
--                             logs in as the owner; migrations run as a
--                             superuser/admin that does  SET ROLE bazaar_owner.
--   bazaar_app       LOGIN    the application: DML on all tables, EXECUTE functions
--   bazaar_readonly  NOLOGIN  a privilege bundle: SELECT on everything
--   bazaar_analyst   LOGIN    a person; gets read access by membership in bazaar_readonly
--   bazaar_seller    LOGIN    the seller portal: sees and edits ONLY the rows of
--                             the seller named in the setting app.seller_id
--
-- Passwords ('app', 'analyst', 'seller') are for the course only.
--
-- Roles live in the cluster, not in a database: reset.sh drops and recreates
-- the bazaar DATABASE, but the roles survive. Hence every CREATE ROLE is
-- guarded (DO block + IF NOT EXISTS) and passwords/attributes are re-applied
-- with ALTER ROLE, so this file can be applied any number of times.
--
-- Notes:
--   * Since PostgreSQL 15, PUBLIC no longer has CREATE on schema public
--     (CVE-2018-1058 hardening), so ordinary roles cannot plant objects there.
--   * A non-public schema such as bazaar grants nothing to PUBLIC; access is
--     exactly what is granted below.
--   * Functions are EXECUTE-able by PUBLIC by default; the grants below are
--     explicit for documentation. Table privileges still apply inside
--     SECURITY INVOKER functions (the default), so e.g. bazaar_readonly cannot
--     place orders through place_order().
-- =============================================================================

SET search_path = bazaar, public;
SET client_min_messages = warning;   -- hide "role is already a member" notices on re-runs

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Roles (idempotent)
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bazaar_owner') THEN
        CREATE ROLE bazaar_owner NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bazaar_app') THEN
        CREATE ROLE bazaar_app LOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bazaar_readonly') THEN
        CREATE ROLE bazaar_readonly NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bazaar_analyst') THEN
        CREATE ROLE bazaar_analyst LOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bazaar_seller') THEN
        CREATE ROLE bazaar_seller LOGIN;
    END IF;
END
$$;

-- Re-assert attributes every time (a previous run or a learner may have changed them).
-- With password_encryption = scram-sha-256 (the default) the password is stored hashed.
ALTER ROLE bazaar_owner    NOLOGIN;
ALTER ROLE bazaar_app      LOGIN PASSWORD 'app'     CONNECTION LIMIT 50;
ALTER ROLE bazaar_readonly NOLOGIN;
ALTER ROLE bazaar_analyst  LOGIN PASSWORD 'analyst';
ALTER ROLE bazaar_seller   LOGIN PASSWORD 'seller';

-- Membership: the analyst inherits bazaar_readonly's privileges.
GRANT bazaar_readonly TO bazaar_analyst;

-- Allow the login roles to connect to this database (PUBLIC has CONNECT by
-- default; explicit is better, and survives a REVOKE CONNECT ... FROM PUBLIC).
GRANT CONNECT ON DATABASE bazaar TO bazaar_app, bazaar_analyst, bazaar_seller;

-- -----------------------------------------------------------------------------
-- 2. Ownership: hand the schema and everything in it to bazaar_owner.
--    (Ownership implies all privileges plus the right to ALTER/DROP, and owners
--    are NOT subject to row-level security unless FORCE ROW LEVEL SECURITY.)
-- -----------------------------------------------------------------------------
ALTER SCHEMA bazaar OWNER TO bazaar_owner;

DO $$
DECLARE
    r record;
BEGIN
    -- tables (incl. partitions), views, materialized views. Identity sequences
    -- and indexes follow their table automatically.
    FOR r IN
        SELECT c.relname, c.relkind
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'bazaar' AND c.relkind IN ('r', 'p', 'v', 'm')
    LOOP
        EXECUTE format('ALTER %s bazaar.%I OWNER TO bazaar_owner',
                       CASE r.relkind WHEN 'v' THEN 'VIEW'
                                      WHEN 'm' THEN 'MATERIALIZED VIEW'
                                      ELSE 'TABLE' END,
                       r.relname);
    END LOOP;

    -- functions and procedures
    FOR r IN
        SELECT p.oid::regprocedure AS sig, p.prokind
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'bazaar'
    LOOP
        EXECUTE format('ALTER %s %s OWNER TO bazaar_owner',
                       CASE r.prokind WHEN 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END, r.sig);
    END LOOP;

    -- enum types and domains (row types of tables follow their table)
    FOR r IN
        SELECT t.typname, t.typtype
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'bazaar' AND t.typtype IN ('e', 'd')
    LOOP
        EXECUTE format('ALTER %s bazaar.%I OWNER TO bazaar_owner',
                       CASE r.typtype WHEN 'd' THEN 'DOMAIN' ELSE 'TYPE' END, r.typname);
    END LOOP;
END
$$;

-- -----------------------------------------------------------------------------
-- 3. Privileges on existing objects
-- -----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA bazaar TO bazaar_app, bazaar_readonly, bazaar_seller;

-- The application: full DML, but no DDL (it does not own anything).
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA bazaar TO bazaar_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA bazaar TO bazaar_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA bazaar TO bazaar_app;

-- Read-only bundle (analysts, BI tools, ...).
GRANT SELECT ON ALL TABLES IN SCHEMA bazaar TO bazaar_readonly;

-- The seller portal: least privilege, partly column-level.
GRANT SELECT ON categories, sellers TO bazaar_seller;                 -- catalog context
GRANT SELECT, INSERT, UPDATE ON products TO bazaar_seller;            -- no DELETE: archive instead
GRANT SELECT, INSERT, UPDATE ON inventory TO bazaar_seller;
GRANT SELECT ON orders, order_items TO bazaar_seller;
GRANT UPDATE (status, shipped_at, delivered_at) ON orders TO bazaar_seller;  -- fulfilment only
GRANT SELECT ON reviews TO bazaar_seller;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA bazaar TO bazaar_seller;

-- -----------------------------------------------------------------------------
-- 4. Default privileges: objects that bazaar_owner creates LATER (new tables in
--    a migration, new event partitions, ...) get the same grants automatically.
--    Default privileges are tied to the CREATING role, hence: run migrations
--    as bazaar_owner (SET ROLE bazaar_owner), not as postgres.
-- -----------------------------------------------------------------------------
ALTER DEFAULT PRIVILEGES FOR ROLE bazaar_owner IN SCHEMA bazaar
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO bazaar_app;
ALTER DEFAULT PRIVILEGES FOR ROLE bazaar_owner IN SCHEMA bazaar
    GRANT USAGE, SELECT ON SEQUENCES TO bazaar_app;
ALTER DEFAULT PRIVILEGES FOR ROLE bazaar_owner IN SCHEMA bazaar
    GRANT EXECUTE ON FUNCTIONS TO bazaar_app;
ALTER DEFAULT PRIVILEGES FOR ROLE bazaar_owner IN SCHEMA bazaar
    GRANT SELECT ON TABLES TO bazaar_readonly;

-- -----------------------------------------------------------------------------
-- 5. SECURITY DEFINER where a function must do more than its caller may.
--    A SECURITY DEFINER function runs with its OWNER's privileges, so it must
--    pin search_path (otherwise a caller could put a malicious table/function
--    earlier on the path) and be granted narrowly.
-- -----------------------------------------------------------------------------

-- Audit rows must be written whoever changes a product/order, so the trigger
-- runs as the owner. Note: section 3's blanket grant still gives bazaar_app
-- DML on audit_log; module 15's milestone (requirement 6) revokes it so that
-- only the owner, via this trigger, can write history.
ALTER FUNCTION audit_trigger() SECURITY DEFINER SET search_path = bazaar, pg_temp;

-- Creating a partition requires owning the parent table; let the app (or a
-- cron job running as bazaar_app) prepare next month's partition.
ALTER FUNCTION create_events_partition(date) SECURITY DEFINER SET search_path = bazaar, pg_temp;
REVOKE EXECUTE ON FUNCTION create_events_partition(date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION create_events_partition(date) TO bazaar_app;

-- REFRESH MATERIALIZED VIEW also requires ownership; wrap it for the app.
CREATE FUNCTION refresh_seller_sales_daily()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = bazaar, pg_temp
AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY seller_sales_daily;
END;
$$;
ALTER FUNCTION refresh_seller_sales_daily() OWNER TO bazaar_owner;
REVOKE EXECUTE ON FUNCTION refresh_seller_sales_daily() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION refresh_seller_sales_daily() TO bazaar_app;

-- -----------------------------------------------------------------------------
-- 6. Row-level security for the seller portal
--
-- The portal connects as bazaar_seller and, per request/transaction, runs
--     SET LOCAL app.seller_id = '42';     -- or: SELECT set_config('app.seller_id', '42', true)
-- Policies compare rows with that setting. If it is not set, current_seller_id()
-- is NULL, every comparison is NULL, and the seller sees nothing: fail closed.
--
-- Once RLS is ENABLED on a table, any role that is not the owner (and not a
-- superuser / BYPASSRLS role) sees NO rows unless some policy grants them.
-- So bazaar_app and bazaar_readonly need an explicit "everything" policy;
-- the alternative would be ALTER ROLE bazaar_app BYPASSRLS.
--
-- We do NOT use FORCE ROW LEVEL SECURITY. With FORCE, the table owner would be
-- subject to the policies too; as no policy targets bazaar_owner, SECURITY
-- DEFINER functions owned by it (and migrations run as it) would suddenly see
-- empty tables. (Foreign-key checks are never subject to RLS.)
--
-- Views run with their owner's privileges: a seller querying active_products
-- sees all active products (the public storefront), not just their own. For
-- a view that should respect the caller's RLS, use
-- CREATE VIEW ... WITH (security_invoker = true).
-- -----------------------------------------------------------------------------

-- '' (what current_setting returns after SET ... then RESET) is treated like
-- "not set" instead of failing with "invalid input syntax for type bigint".
CREATE FUNCTION current_seller_id()
RETURNS bigint
LANGUAGE sql
STABLE PARALLEL SAFE
RETURN nullif(current_setting('app.seller_id', true), '')::bigint;
ALTER FUNCTION current_seller_id() OWNER TO bazaar_owner;

ALTER TABLE products    ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory   ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders      ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_items ENABLE ROW LEVEL SECURITY;

-- The application and read-only roles see and (app) change everything.
CREATE POLICY products_all_access    ON products    TO bazaar_app, bazaar_readonly USING (true) WITH CHECK (true);
CREATE POLICY inventory_all_access   ON inventory   TO bazaar_app, bazaar_readonly USING (true) WITH CHECK (true);
CREATE POLICY orders_all_access      ON orders      TO bazaar_app, bazaar_readonly USING (true) WITH CHECK (true);
CREATE POLICY order_items_all_access ON order_items TO bazaar_app, bazaar_readonly USING (true) WITH CHECK (true);

-- Sellers: their own products. USING filters the rows they can see/update;
-- WITH CHECK stops them from inserting a product for, or moving a product to,
-- another seller.
CREATE POLICY products_seller ON products
    FOR ALL
    TO bazaar_seller
    USING (seller_id = current_seller_id())
    WITH CHECK (seller_id = current_seller_id());

-- Inventory has no seller_id: decide via the product. The subquery on products
-- is itself filtered by products' policy for bazaar_seller.
CREATE POLICY inventory_seller ON inventory
    FOR ALL
    TO bazaar_seller
    USING (EXISTS (SELECT 1 FROM products p
                   WHERE p.id = inventory.product_id AND p.seller_id = current_seller_id()))
    WITH CHECK (EXISTS (SELECT 1 FROM products p
                        WHERE p.id = inventory.product_id AND p.seller_id = current_seller_id()));

-- Orders: read and fulfil their own orders (column privileges above limit
-- WHICH columns an UPDATE may touch; the policy limits WHICH rows).
CREATE POLICY orders_seller_select ON orders
    FOR SELECT
    TO bazaar_seller
    USING (seller_id = current_seller_id());

CREATE POLICY orders_seller_update ON orders
    FOR UPDATE
    TO bazaar_seller
    USING (seller_id = current_seller_id())
    WITH CHECK (seller_id = current_seller_id());

CREATE POLICY order_items_seller_select ON order_items
    FOR SELECT
    TO bazaar_seller
    USING (EXISTS (SELECT 1 FROM orders o
                   WHERE o.id = order_items.order_id AND o.seller_id = current_seller_id()));

COMMIT;
