-- =============================================================================
-- fdw/shard.sql  --  run on EACH shard node (m21-shard1 with -v n=0,
-- m21-shard2 with -v n=1). Creates the shard's share of three Bazaar tables:
-- plain local tables with their own primary keys and indexes.
-- =============================================================================
DROP SCHEMA IF EXISTS shard CASCADE;
CREATE SCHEMA shard;
SET search_path = shard;

CREATE TABLE products_p:n (
    seller_id    bigint        NOT NULL,
    id           bigint        NOT NULL,
    category_id  int           NOT NULL,
    title        text          NOT NULL,
    price        numeric(12,2) NOT NULL,
    status       text          NOT NULL,
    PRIMARY KEY (seller_id, id)
);

CREATE TABLE orders_p:n (
    seller_id    bigint        NOT NULL,
    id           bigint        NOT NULL,
    user_id      bigint        NOT NULL,
    status       text          NOT NULL,
    total        numeric(12,2) NOT NULL,
    created_at   timestamptz   NOT NULL,
    PRIMARY KEY (seller_id, id)
);
CREATE INDEX ON orders_p:n (seller_id, created_at);

CREATE TABLE order_items_p:n (
    seller_id    bigint        NOT NULL,
    order_id     bigint        NOT NULL,
    product_id   bigint        NOT NULL,
    quantity     int           NOT NULL,
    unit_price   numeric(12,2) NOT NULL,
    PRIMARY KEY (seller_id, order_id, product_id),
    -- shard-local FK: possible because both rows always live on this node
    FOREIGN KEY (seller_id, order_id) REFERENCES orders_p:n (seller_id, id)
);
