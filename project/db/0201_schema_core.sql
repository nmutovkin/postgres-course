-- =============================================================================
-- 0201_schema_core.sql  --  Module 02: Data Types and Tables
-- -----------------------------------------------------------------------------
-- The Bazaar core schema (v1): users, sellers, categories, products, inventory,
-- orders, order_items.
--
-- Design notes for learners:
--   * Surrogate keys are `GENERATED ALWAYS AS IDENTITY` (the SQL-standard
--     successor of `serial`). ALWAYS means you cannot accidentally insert your
--     own id; seeds that need fixed ids use `OVERRIDING SYSTEM VALUE`.
--   * Money is numeric(12,2): exact decimal arithmetic, no float rounding.
--   * Status columns are `text` + CHECK here. Module 08 converts them to enums.
--   * Every table has created_at; tables whose rows change also get updated_at.
--   * CHECK constraints are given explicit names so error messages are readable
--     ("violates check constraint products_price_nonnegative").
--   * On purpose there are NO secondary indexes yet (other than those created
--     implicitly by PRIMARY KEY and UNIQUE). Postgres does not index foreign
--     key columns automatically; Module 11 adds them as a lesson.
-- =============================================================================

SET search_path = bazaar, public;

-- -----------------------------------------------------------------------------
-- users: everybody who has an account (shoppers and sellers alike).
-- -----------------------------------------------------------------------------
CREATE TABLE users (
    id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email       text        NOT NULL UNIQUE,        -- case-sensitive for now; see module 08
    full_name   text        NOT NULL,
    is_active   boolean     NOT NULL DEFAULT true,  -- soft "disabled" flag
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- sellers: a user who has opened a shop. 1:1 with users (UNIQUE user_id).
-- -----------------------------------------------------------------------------
CREATE TABLE sellers (
    id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       bigint      NOT NULL UNIQUE REFERENCES users (id),
    shop_name     text        NOT NULL UNIQUE,
    country_code  char(2)     NOT NULL,             -- ISO 3166-1 alpha-2, e.g. 'DE'
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT sellers_country_code_upper CHECK (country_code ~ '^[A-Z]{2}$')
);

-- -----------------------------------------------------------------------------
-- categories: a self-referencing tree (adjacency list). Roots have parent_id NULL.
-- Module 06 walks it with recursive CTEs.
-- -----------------------------------------------------------------------------
CREATE TABLE categories (
    id          int         GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    parent_id   int         REFERENCES categories (id),
    name        text        NOT NULL,
    slug        text        NOT NULL UNIQUE,        -- URL-friendly, e.g. 'laptops'
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT categories_not_own_parent CHECK (parent_id <> id)
);

-- -----------------------------------------------------------------------------
-- products: listed by exactly one seller, in exactly one (usually leaf) category.
-- A seller's SKU is unique within that seller only.
-- -----------------------------------------------------------------------------
CREATE TABLE products (
    id           bigint        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seller_id    bigint        NOT NULL REFERENCES sellers (id),
    category_id  int           NOT NULL REFERENCES categories (id),
    sku          text          NOT NULL,
    title        text          NOT NULL,
    description  text,                              -- optional: NULL means "not written yet"
    price        numeric(12,2) NOT NULL,
    status       text          NOT NULL DEFAULT 'draft',
    created_at   timestamptz   NOT NULL DEFAULT now(),
    updated_at   timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT products_price_nonnegative CHECK (price >= 0),
    CONSTRAINT products_status_check CHECK (status IN ('draft', 'active', 'archived')),
    CONSTRAINT products_seller_id_sku_key UNIQUE (seller_id, sku)
);

-- -----------------------------------------------------------------------------
-- inventory: one row per product (1:1, product_id is both PK and FK).
-- `reserved` = units promised to placed-but-not-shipped orders;
-- available to sell = quantity - reserved.
-- -----------------------------------------------------------------------------
CREATE TABLE inventory (
    product_id  bigint      PRIMARY KEY REFERENCES products (id) ON DELETE CASCADE,
    quantity    int         NOT NULL DEFAULT 0,
    reserved    int         NOT NULL DEFAULT 0,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT inventory_quantity_nonnegative CHECK (quantity >= 0),
    -- A table-level CHECK can compare two columns of the same row.
    CONSTRAINT inventory_reserved_within_quantity CHECK (reserved >= 0 AND reserved <= quantity)
);

-- -----------------------------------------------------------------------------
-- orders: one order = one buyer + one seller (checkout splits carts per seller).
-- `total` is denormalised: it must equal sum(quantity * unit_price) of its items
-- (module 09 keeps it in sync with a trigger).
-- Lifecycle timestamps: paid_at / shipped_at / delivered_at are filled as the
-- order moves through the status values.
-- -----------------------------------------------------------------------------
CREATE TABLE orders (
    id            bigint        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       bigint        NOT NULL REFERENCES users (id),
    seller_id     bigint        NOT NULL REFERENCES sellers (id),
    status        text          NOT NULL DEFAULT 'pending',
    total         numeric(12,2) NOT NULL DEFAULT 0,
    created_at    timestamptz   NOT NULL DEFAULT now(),
    paid_at       timestamptz,
    shipped_at    timestamptz,
    delivered_at  timestamptz,
    CONSTRAINT orders_status_check CHECK (
        status IN ('pending', 'paid', 'shipped', 'delivered', 'cancelled', 'refunded')
    )
);

-- -----------------------------------------------------------------------------
-- order_items: the lines of an order. Composite natural key (order_id, product_id):
-- a product appears at most once per order (with a quantity).
-- unit_price is copied from products.price at checkout time, so later price
-- changes do not rewrite history.
-- -----------------------------------------------------------------------------
CREATE TABLE order_items (
    order_id    bigint        NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    product_id  bigint        NOT NULL REFERENCES products (id),
    quantity    int           NOT NULL,
    unit_price  numeric(12,2) NOT NULL,
    CONSTRAINT order_items_quantity_positive CHECK (quantity > 0),
    CONSTRAINT order_items_unit_price_nonnegative CHECK (unit_price >= 0),
    PRIMARY KEY (order_id, product_id)
);

COMMENT ON TABLE users       IS 'Bazaar accounts (shoppers and sellers).';
COMMENT ON TABLE sellers     IS 'Shops; each is owned by exactly one user.';
COMMENT ON TABLE categories  IS 'Product category tree (adjacency list).';
COMMENT ON TABLE products    IS 'Listings; each belongs to one seller and one category.';
COMMENT ON TABLE inventory   IS 'Stock per product; available = quantity - reserved.';
COMMENT ON TABLE orders      IS 'One order per (buyer, seller) checkout split.';
COMMENT ON TABLE order_items IS 'Order lines; unit_price frozen at checkout.';
COMMENT ON COLUMN orders.total IS 'Denormalised: sum(quantity * unit_price) of the order''s items.';
