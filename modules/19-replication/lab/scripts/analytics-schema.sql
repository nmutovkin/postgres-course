-- Module 19 milestone, SUBSCRIBER side. Run on m19-analytics:
--   docker exec -i m19-analytics psql -U postgres -v ON_ERROR_STOP=1 < scripts/analytics-schema.sql
--
-- Logical replication matches tables by schema-qualified NAME and columns by
-- NAME, so the subscriber needs bazaar.products / bazaar.orders /
-- bazaar.order_items with (at least) the published columns. Everything else
-- is up to us: types may differ as long as the text form converts (we use
-- text instead of the publisher's enums), there are no foreign keys (the
-- analytics copy only has 3 of Bazaar's tables), and we add our own indexes
-- and views.
CREATE DATABASE analytics;
\connect analytics
CREATE SCHEMA bazaar;
SET search_path = bazaar, public;

CREATE TABLE products (
    id           bigint        PRIMARY KEY,
    seller_id    bigint        NOT NULL,
    category_id  int           NOT NULL,
    sku          text          NOT NULL,
    title        text          NOT NULL,
    price        numeric(12,2) NOT NULL,
    status       text          NOT NULL,          -- enum product_status on the publisher
    created_at   timestamptz   NOT NULL,
    updated_at   timestamptz   NOT NULL
);

CREATE TABLE orders (
    id                   bigint        PRIMARY KEY,
    user_id              bigint        NOT NULL,
    seller_id            bigint        NOT NULL,
    status               text          NOT NULL,  -- enum order_status on the publisher
    total                numeric(12,2) NOT NULL,
    created_at           timestamptz   NOT NULL,
    paid_at              timestamptz,
    shipped_at           timestamptz,
    delivered_at         timestamptz,
    shipping_address_id  bigint,
    updated_at           timestamptz   NOT NULL,
    -- Subscriber-only column: filled by its DEFAULT when the apply worker
    -- inserts a row (the publisher does not send it).
    replicated_at        timestamptz   NOT NULL DEFAULT now()
);

CREATE TABLE order_items (
    order_id    bigint        NOT NULL,
    product_id  bigint        NOT NULL,
    quantity    int           NOT NULL,
    unit_price  numeric(12,2) NOT NULL,
    PRIMARY KEY (order_id, product_id)
);

-- Indexes for the analysts' queries (the publisher does not have these).
CREATE INDEX orders_paid_at_idx ON orders (paid_at);
CREATE INDEX orders_seller_id_paid_at_idx ON orders (seller_id, paid_at);
CREATE INDEX order_items_product_id_idx ON order_items (product_id);

-- A reporting view that only exists on the analytics server.
CREATE VIEW daily_revenue AS
SELECT
    date_trunc('day', o.paid_at)::date AS day,
    count(*)                           AS paid_orders,
    sum(o.total)                       AS revenue
FROM orders AS o
WHERE o.status NOT IN ('cancelled', 'refunded')
GROUP BY 1;

-- The subscription. Creating it (1) creates a logical replication slot named
-- after it on the publisher, (2) copies the current contents of each
-- published table (the initial sync), then (3) streams changes from there on.
CREATE SUBSCRIPTION bazaar_analytics
    CONNECTION 'host=m19-primary dbname=bazaar user=analytics_repl password=analytics_repl application_name=analytics'
    PUBLICATION bazaar_analytics;
