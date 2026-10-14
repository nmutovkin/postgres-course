-- =============================================================================
-- 0901_schema_logic.sql  --  Module 09: Views, Functions and Triggers
-- -----------------------------------------------------------------------------
-- Moves a few business rules into the database:
--   1. set_updated_at()      BEFORE UPDATE row trigger keeping updated_at honest
--                            (products, inventory, and orders, which gets an
--                            updated_at column here)
--   2. audit_log + audit_trigger()
--                            a generic AFTER row trigger that records every
--                            INSERT/UPDATE/DELETE on products and orders as JSONB
--   3. order_items_maintain_total()
--                            statement-level trigger with transition tables that
--                            keeps orders.total = sum(quantity * unit_price)
--   4. active_products       a view: what the storefront lists
--   5. seller_sales_daily    a materialized view for seller dashboards, with the
--                            unique index REFRESH ... CONCURRENTLY requires
-- =============================================================================

SET search_path = bazaar, public;

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. updated_at maintenance
-- -----------------------------------------------------------------------------

-- orders had no updated_at so far. Adding a column with a non-volatile default
-- is instant (no table rewrite; the default is stored in the catalog). now() is
-- STABLE, so every existing row gets this transaction's timestamp; we then
-- backfill something meaningful: the latest lifecycle timestamp.
ALTER TABLE orders ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
UPDATE orders
SET updated_at = greatest(created_at, paid_at, shipped_at, delivered_at);  -- greatest() ignores NULLs

CREATE FUNCTION set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- A BEFORE trigger may modify NEW; the modified row is what gets stored.
    -- now() is the transaction start time, so all rows touched by one
    -- transaction share the same updated_at.
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION set_updated_at() IS 'BEFORE UPDATE trigger: sets NEW.updated_at = now().';

CREATE TRIGGER products_set_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER inventory_set_updated_at
    BEFORE UPDATE ON inventory
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER orders_set_updated_at
    BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- -----------------------------------------------------------------------------
-- 2. Generic audit log
-- -----------------------------------------------------------------------------
CREATE TABLE audit_log (
    id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_name  text        NOT NULL,
    operation   text        NOT NULL,      -- 'INSERT' | 'UPDATE' | 'DELETE'
    row_pk      jsonb,                     -- e.g. {"id": 42}
    old_row     jsonb,                     -- NULL for INSERT
    new_row     jsonb,                     -- NULL for DELETE
    changed_by  text        NOT NULL DEFAULT current_user,
    changed_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT audit_log_operation_check CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE'))
);

COMMENT ON TABLE audit_log IS 'Row-level change history written by audit_trigger().';

-- One function serves every audited table:
--   * TG_TABLE_NAME / TG_OP tell it where and what happened;
--   * to_jsonb(OLD) / to_jsonb(NEW) serialise whole rows whatever their shape;
--   * the primary-key column names come in as trigger arguments (TG_ARGV), so
--     the function needs no per-table code:  EXECUTE FUNCTION audit_trigger('id')
CREATE FUNCTION audit_trigger()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_old  jsonb;
    v_new  jsonb;
    v_pk   jsonb;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        v_old := to_jsonb(OLD);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        v_new := to_jsonb(NEW);
    END IF;

    -- Skip no-op updates (UPDATE t SET x = x): nothing worth auditing.
    -- updated_at is ignored in the comparison because set_updated_at() changes
    -- it on every UPDATE.
    IF TG_OP = 'UPDATE' AND (v_old - 'updated_at') = (v_new - 'updated_at') THEN
        RETURN NULL;
    END IF;

    -- Build {"pk_col": value, ...} from the trigger arguments.
    SELECT jsonb_object_agg(col, coalesce(v_new, v_old) -> col)
    INTO v_pk
    FROM unnest(TG_ARGV) AS col;

    INSERT INTO audit_log (table_name, operation, row_pk, old_row, new_row)
    VALUES (TG_TABLE_NAME, TG_OP, v_pk, v_old, v_new);

    RETURN NULL;  -- the return value of an AFTER row trigger is ignored
END;
$$;

COMMENT ON FUNCTION audit_trigger() IS
    'Generic AFTER row trigger writing to audit_log; trigger args = primary key column names.';

CREATE TRIGGER products_audit
    AFTER INSERT OR UPDATE OR DELETE ON products
    FOR EACH ROW EXECUTE FUNCTION audit_trigger('id');

CREATE TRIGGER orders_audit
    AFTER INSERT OR UPDATE OR DELETE ON orders
    FOR EACH ROW EXECUTE FUNCTION audit_trigger('id');

-- -----------------------------------------------------------------------------
-- 3. orders.total maintained from order_items
--
-- Choice: a STATEMENT-level trigger with transition tables, applying DELTAS.
--   * Statement-level: `INSERT INTO order_items ... VALUES (..),(..),(..)` for
--     one order fires the trigger once and updates the order row once, instead
--     of three row-level firings each creating a new version of the same
--     orders row.
--   * Transition tables (REFERENCING NEW TABLE / OLD TABLE) expose all rows the
--     statement inserted / deleted / updated, so one set-based UPDATE handles
--     any number of orders.
--   * Deltas (total = total + change) rather than recomputing sum(): under READ
--     COMMITTED, if two transactions add items to the same order concurrently,
--     the second UPDATE waits for the first, then re-reads the *latest* orders
--     row and adds its own delta, so nothing is lost. A recomputing
--     `SET total = (SELECT sum(...) FROM order_items ...)` would evaluate the
--     subquery with the statement's older snapshot and silently drop the other
--     transaction's item. (Module 10 explores this anomaly.)
--   * Deltas assume total was correct to begin with; seeds compute it exactly
--     and tools/check.sql verifies it.
-- -----------------------------------------------------------------------------
CREATE FUNCTION order_items_maintain_total()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- A transition table only exists for the events that define it, so each
    -- branch references only the tables available for TG_OP.
    IF TG_OP = 'INSERT' THEN
        UPDATE orders o
        SET total = o.total + d.delta
        FROM (
            SELECT order_id, sum(quantity * unit_price) AS delta
            FROM new_items
            GROUP BY order_id
        ) d
        WHERE o.id = d.order_id
          AND d.delta <> 0;
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE orders o
        SET total = o.total - d.delta
        FROM (
            SELECT order_id, sum(quantity * unit_price) AS delta
            FROM old_items
            GROUP BY order_id
        ) d
        WHERE o.id = d.order_id
          AND d.delta <> 0;
    ELSE  -- UPDATE (may even move an item to another order)
        UPDATE orders o
        SET total = o.total + d.delta
        FROM (
            SELECT order_id, sum(delta) AS delta
            FROM (
                SELECT order_id, quantity * unit_price AS delta FROM new_items
                UNION ALL
                SELECT order_id, -(quantity * unit_price) FROM old_items
            ) changes
            GROUP BY order_id
        ) d
        WHERE o.id = d.order_id
          AND d.delta <> 0;
    END IF;
    RETURN NULL;
END;
$$;

COMMENT ON FUNCTION order_items_maintain_total() IS
    'Statement-level trigger: applies the change in sum(quantity*unit_price) to orders.total.';

-- A trigger that uses transition tables must fire on exactly ONE event
-- ("transition tables cannot be specified for triggers with more than one
-- event"), so we create one trigger per event, all calling the same function.
CREATE TRIGGER order_items_total_ins
    AFTER INSERT ON order_items
    REFERENCING NEW TABLE AS new_items
    FOR EACH STATEMENT EXECUTE FUNCTION order_items_maintain_total();

CREATE TRIGGER order_items_total_upd
    AFTER UPDATE ON order_items
    REFERENCING OLD TABLE AS old_items NEW TABLE AS new_items
    FOR EACH STATEMENT EXECUTE FUNCTION order_items_maintain_total();

CREATE TRIGGER order_items_total_del
    AFTER DELETE ON order_items
    REFERENCING OLD TABLE AS old_items
    FOR EACH STATEMENT EXECUTE FUNCTION order_items_maintain_total();

-- -----------------------------------------------------------------------------
-- 4. active_products: what the storefront shows.
-- A view is a stored query; it is expanded into every query that uses it, so
-- it costs nothing until used and is always current.
-- LEFT JOIN inventory: a product without an inventory row is shown as 0 available.
-- -----------------------------------------------------------------------------
CREATE VIEW active_products AS
SELECT
    p.id,
    p.seller_id,
    s.shop_name,
    p.category_id,
    c.name                                         AS category_name,
    p.sku,
    p.title,
    p.description,
    p.price,
    p.attributes,
    p.tags,
    coalesce(i.quantity - i.reserved, 0)           AS available,
    p.created_at,
    p.updated_at
FROM products p
JOIN sellers s         ON s.id = p.seller_id
JOIN categories c      ON c.id = p.category_id
LEFT JOIN inventory i  ON i.product_id = p.id
WHERE p.status = 'active';

COMMENT ON VIEW active_products IS 'Active products with shop, category and available stock.';

-- -----------------------------------------------------------------------------
-- 5. seller_sales_daily: precomputed daily sales per seller.
-- Counts orders whose money was taken and not returned: paid, shipped, delivered.
-- The day is the order's creation day *in UTC* (AT TIME ZONE 'UTC'), so the
-- result does not depend on the TimeZone setting of whoever refreshes it.
-- items_sold is aggregated per order first so the join does not multiply orders.
-- -----------------------------------------------------------------------------
CREATE MATERIALIZED VIEW seller_sales_daily AS
SELECT
    o.seller_id,
    (o.created_at AT TIME ZONE 'UTC')::date  AS day,
    count(*)                                 AS orders_count,
    sum(i.items)                             AS items_sold,
    sum(o.total)                             AS revenue
FROM orders o
JOIN (
    SELECT order_id, sum(quantity) AS items
    FROM order_items
    GROUP BY order_id
) i ON i.order_id = o.id
WHERE o.status IN ('paid', 'shipped', 'delivered')
GROUP BY o.seller_id, (o.created_at AT TIME ZONE 'UTC')::date;

-- REFRESH MATERIALIZED VIEW CONCURRENTLY (which does not block readers) needs a
-- UNIQUE index on plain columns covering all rows to match old and new rows.
CREATE UNIQUE INDEX seller_sales_daily_seller_id_day_key
    ON seller_sales_daily (seller_id, day);

COMMENT ON MATERIALIZED VIEW seller_sales_daily IS
    'Daily orders/items/revenue per seller (paid+shipped+delivered). Refresh: REFRESH MATERIALIZED VIEW CONCURRENTLY seller_sales_daily;';

COMMIT;
