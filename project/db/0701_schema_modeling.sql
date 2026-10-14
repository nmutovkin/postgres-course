-- =============================================================================
-- 0701_schema_modeling.sql  --  Module 07: Data Modeling
-- -----------------------------------------------------------------------------
-- Bazaar schema v2: the tables a real marketplace needs around the core.
--
--   addresses              1:N from users; "at most one default" via a partial
--                          unique index (a constraint a plain UNIQUE can't express)
--   orders.shipping_address_id
--                          nullable FK: orders placed before we captured
--                          addresses have none
--   payments               1:N from orders; a refund is a NEGATIVE payment row
--                          (an append-only ledger instead of updating amounts)
--   reviews                M:N between users and products with payload;
--                          one review per (product, user)
--   cart_items             M:N with a natural composite key (user_id, product_id)
--   product_price_history  a history table: products.price is the current
--                          price, this table records every price and the moment
--                          it became valid (valid_from). The price valid at time
--                          t is the row with the greatest valid_from <= t.
--
-- ON DELETE behaviour is chosen per relationship:
--   * CASCADE where the child is meaningless without the parent (a user's
--     addresses or cart, a product's reviews / price history);
--   * the default (NO ACTION) where deleting the parent would destroy business
--     records (payments of an order, the address an order was shipped to).
-- =============================================================================

SET search_path = bazaar, public;

-- -----------------------------------------------------------------------------
-- addresses
-- -----------------------------------------------------------------------------
CREATE TABLE addresses (
    id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    label         text,                         -- 'Home', 'Work', ... (optional)
    recipient     text        NOT NULL,
    line1         text        NOT NULL,
    line2         text,
    city          text        NOT NULL,
    postal_code   text        NOT NULL,         -- text, not int: 'SW1A 1AA', '02116'
    country_code  char(2)     NOT NULL,
    is_default    boolean     NOT NULL DEFAULT false,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- "At most one default address per user": a UNIQUE index that only contains the
-- rows WHERE is_default. Non-default rows are not in the index at all, so a user
-- may have any number of them.
CREATE UNIQUE INDEX addresses_one_default_per_user
    ON addresses (user_id)
    WHERE is_default;

-- -----------------------------------------------------------------------------
-- orders.shipping_address_id
-- Note the modelling trade-off: an address row can later be edited, which would
-- silently change where a historical order "was shipped". Real systems often
-- copy the address into the order (a deliberate denormalisation / snapshot).
-- Bazaar keeps the reference and treats addresses as append-only.
-- -----------------------------------------------------------------------------
ALTER TABLE orders
    ADD COLUMN shipping_address_id bigint REFERENCES addresses (id);

-- -----------------------------------------------------------------------------
-- payments: one row per payment attempt or refund.
-- -----------------------------------------------------------------------------
CREATE TABLE payments (
    id            bigint        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id      bigint        NOT NULL REFERENCES orders (id),
    amount        numeric(12,2) NOT NULL,       -- negative = refund
    provider      text          NOT NULL,       -- 'stripe', 'paypal', ...
    provider_ref  text,                         -- the provider's transaction id
    status        text          NOT NULL,
    created_at    timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT payments_amount_nonzero CHECK (amount <> 0),
    CONSTRAINT payments_status_check CHECK (status IN ('pending', 'succeeded', 'failed'))
);

-- -----------------------------------------------------------------------------
-- reviews
-- -----------------------------------------------------------------------------
CREATE TABLE reviews (
    id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id  bigint      NOT NULL REFERENCES products (id) ON DELETE CASCADE,
    user_id     bigint      NOT NULL REFERENCES users (id),
    rating      smallint    NOT NULL,
    title       text,
    body        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT reviews_rating_range CHECK (rating BETWEEN 1 AND 5),
    CONSTRAINT reviews_product_id_user_id_key UNIQUE (product_id, user_id)
);

-- -----------------------------------------------------------------------------
-- cart_items: the composite PK makes "add the same product twice" an upsert
-- (INSERT ... ON CONFLICT (user_id, product_id) DO UPDATE SET quantity = ...).
-- -----------------------------------------------------------------------------
CREATE TABLE cart_items (
    user_id     bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    product_id  bigint      NOT NULL REFERENCES products (id) ON DELETE CASCADE,
    quantity    int         NOT NULL,
    added_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cart_items_quantity_positive CHECK (quantity > 0),
    PRIMARY KEY (user_id, product_id)
);

-- -----------------------------------------------------------------------------
-- product_price_history: one row per price "version" of a product.
-- The PK (product_id, valid_from) also serves the typical lookup
--   "price of product X at time T":
--     SELECT price FROM product_price_history
--     WHERE product_id = X AND valid_from <= T
--     ORDER BY valid_from DESC LIMIT 1;
-- -----------------------------------------------------------------------------
CREATE TABLE product_price_history (
    product_id  bigint        NOT NULL REFERENCES products (id) ON DELETE CASCADE,
    price       numeric(12,2) NOT NULL,
    valid_from  timestamptz   NOT NULL,
    CONSTRAINT product_price_history_price_nonnegative CHECK (price >= 0),
    PRIMARY KEY (product_id, valid_from)
);

COMMENT ON TABLE addresses IS 'Postal addresses of users; at most one default per user.';
COMMENT ON TABLE payments IS 'Payment attempts and refunds (negative amounts) per order.';
COMMENT ON TABLE reviews IS 'Product reviews; one per (product, user).';
COMMENT ON TABLE cart_items IS 'Open shopping carts: products a user intends to buy.';
COMMENT ON TABLE product_price_history IS 'Every price a product has had, from valid_from on.';
