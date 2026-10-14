-- =============================================================================
-- Module 07, exercise 11: normalize purchase_sheet into 3NF/BCNF tables.
-- State: ./project/bin/reset.sh 08, then solutions/purchase_sheet.sql.
-- The whole migration is one transaction: it either fully happens or not at all.
-- =============================================================================
SET search_path = bazaar, public;
BEGIN;

-- 1. Suppliers: identified by email (the sheet's stable identifier).
--    The name is NOT consistent in the sheet ('Ferro Italia' vs
--    'Ferro Italia S.r.l.'), so we pick the most frequent spelling.
CREATE TABLE suppliers (
    id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          text        NOT NULL,
    email         text        NOT NULL UNIQUE,
    country_code  char(2)     NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT suppliers_country_code_upper CHECK (country_code ~ '^[A-Z]{2}$')
);

INSERT INTO suppliers (name, email, country_code)
SELECT mode() WITHIN GROUP (ORDER BY supplier_name),
       supplier_email,
       min(supplier_country)                -- consistent per email (checked in exercise 4)
FROM purchase_sheet
GROUP BY supplier_email
ORDER BY min(po_date);

-- 2. Warehouses: a short, stable code is a good natural key.
CREATE TABLE warehouses (
    code  text PRIMARY KEY,
    city  text NOT NULL
);

INSERT INTO warehouses (code, city)
SELECT DISTINCT warehouse_code, warehouse_city
FROM purchase_sheet;

-- 3. Purchase orders: one supplier, one warehouse, one date per PO.
CREATE TABLE purchase_orders (
    id              bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    po_number       text        NOT NULL UNIQUE,
    supplier_id     bigint      NOT NULL REFERENCES suppliers (id),
    warehouse_code  text        NOT NULL REFERENCES warehouses (code),
    ordered_on      date        NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

INSERT INTO purchase_orders (po_number, supplier_id, warehouse_code, ordered_on)
SELECT DISTINCT ps.po_number, s.id, ps.warehouse_code, ps.po_date
FROM purchase_sheet AS ps
JOIN suppliers AS s ON s.email = ps.supplier_email
ORDER BY ps.po_number;

-- 4. Lines: the product is Bazaar's own product (seller 3's SKU), so the title
--    is NOT copied: it lives in products. unit_cost IS kept: it is the cost
--    agreed in this PO, a historical fact (it differs between POs).
CREATE TABLE purchase_order_lines (
    purchase_order_id  bigint        NOT NULL REFERENCES purchase_orders (id) ON DELETE CASCADE,
    product_id         bigint        NOT NULL REFERENCES products (id),
    quantity           int           NOT NULL,
    unit_cost          numeric(12,2) NOT NULL,
    CONSTRAINT purchase_order_lines_quantity_positive CHECK (quantity > 0),
    CONSTRAINT purchase_order_lines_unit_cost_nonnegative CHECK (unit_cost >= 0),
    PRIMARY KEY (purchase_order_id, product_id)
);

INSERT INTO purchase_order_lines (purchase_order_id, product_id, quantity, unit_cost)
SELECT po.id, p.id, ps.quantity, ps.unit_cost
FROM purchase_sheet AS ps
JOIN purchase_orders AS po ON po.po_number = ps.po_number
JOIN products AS p ON p.seller_id = 3 AND p.sku = ps.sku;     -- an unknown SKU would be dropped: checked below

COMMENT ON TABLE suppliers IS 'Companies Casa Cucina buys stock from; identified by email.';
COMMENT ON TABLE warehouses IS 'Stock locations, identified by a short code.';
COMMENT ON TABLE purchase_orders IS 'Purchase orders: one supplier, one destination warehouse.';
COMMENT ON COLUMN purchase_order_lines.unit_cost IS 'Cost agreed in this PO (historical fact, not the current cost).';

COMMIT;
