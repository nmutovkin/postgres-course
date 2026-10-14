-- =============================================================================
-- fdw/coord.sql  --  run on m21-fdw-coord, which already holds the small
-- Bazaar dataset (reset.sh 11) in schema bazaar. Builds schema "dist": three
-- hash-partitioned tables whose partitions are FOREIGN tables on the shards,
-- then copies the data in through them.
-- =============================================================================
CREATE EXTENSION IF NOT EXISTS postgres_fdw;

DROP SCHEMA IF EXISTS dist CASCADE;
DROP SERVER IF EXISTS shard1 CASCADE;
DROP SERVER IF EXISTS shard2 CASCADE;

-- One foreign server per shard node.
--   async_capable:    scans of several shards run concurrently (Async Append)
--   fetch_size:       rows per round trip when reading (default 100)
--   batch_size:       rows per round trip when inserting (default 1)
--   use_remote_estimate: ask the shard for row estimates (costs a round trip
--                     per planning, but gives the planner real numbers)
CREATE SERVER shard1 FOREIGN DATA WRAPPER postgres_fdw
    OPTIONS (host 'm21-shard1', dbname 'bazaar', async_capable 'true',
             fetch_size '1000', batch_size '500');
CREATE SERVER shard2 FOREIGN DATA WRAPPER postgres_fdw
    OPTIONS (host 'm21-shard2', dbname 'bazaar', async_capable 'true',
             fetch_size '1000', batch_size '500');

-- Which remote role a local role becomes. (The lab trusts its network; in
-- production put a password here, readable only by superusers.)
CREATE USER MAPPING FOR postgres SERVER shard1 OPTIONS (user 'postgres');
CREATE USER MAPPING FOR postgres SERVER shard2 OPTIONS (user 'postgres');

CREATE SCHEMA dist;
SET search_path = dist;

-- The parents: ordinary partitioned tables, HASH-partitioned on seller_id.
-- They store nothing; each partition is a foreign table.
-- No PRIMARY KEY here: Postgres cannot build indexes on foreign tables, so
-- uniqueness is enforced only inside each shard.
CREATE TABLE products (
    seller_id    bigint        NOT NULL,
    id           bigint        NOT NULL,
    category_id  int           NOT NULL,
    title        text          NOT NULL,
    price        numeric(12,2) NOT NULL,
    status       text          NOT NULL
) PARTITION BY HASH (seller_id);

CREATE TABLE orders (
    seller_id    bigint        NOT NULL,
    id           bigint        NOT NULL,
    user_id      bigint        NOT NULL,
    status       text          NOT NULL,
    total        numeric(12,2) NOT NULL,
    created_at   timestamptz   NOT NULL
) PARTITION BY HASH (seller_id);

CREATE TABLE order_items (
    seller_id    bigint        NOT NULL,
    order_id     bigint        NOT NULL,
    product_id   bigint        NOT NULL,
    quantity     int           NOT NULL,
    unit_price   numeric(12,2) NOT NULL
) PARTITION BY HASH (seller_id);

-- remainder 0 -> m21-shard1, remainder 1 -> m21-shard2
CREATE FOREIGN TABLE products_p0 PARTITION OF products FOR VALUES WITH (MODULUS 2, REMAINDER 0)
    SERVER shard1 OPTIONS (schema_name 'shard', table_name 'products_p0');
CREATE FOREIGN TABLE products_p1 PARTITION OF products FOR VALUES WITH (MODULUS 2, REMAINDER 1)
    SERVER shard2 OPTIONS (schema_name 'shard', table_name 'products_p1');
CREATE FOREIGN TABLE orders_p0 PARTITION OF orders FOR VALUES WITH (MODULUS 2, REMAINDER 0)
    SERVER shard1 OPTIONS (schema_name 'shard', table_name 'orders_p0');
CREATE FOREIGN TABLE orders_p1 PARTITION OF orders FOR VALUES WITH (MODULUS 2, REMAINDER 1)
    SERVER shard2 OPTIONS (schema_name 'shard', table_name 'orders_p1');
CREATE FOREIGN TABLE order_items_p0 PARTITION OF order_items FOR VALUES WITH (MODULUS 2, REMAINDER 0)
    SERVER shard1 OPTIONS (schema_name 'shard', table_name 'order_items_p0');
CREATE FOREIGN TABLE order_items_p1 PARTITION OF order_items FOR VALUES WITH (MODULUS 2, REMAINDER 1)
    SERVER shard2 OPTIONS (schema_name 'shard', table_name 'order_items_p1');

-- Load: an INSERT into the parent is ROUTED by the partition key to the
-- right foreign table, which sends the rows to its shard (in batches of 500).
INSERT INTO products
SELECT seller_id, id, category_id, title, price, status::text FROM bazaar.products;
INSERT INTO orders
SELECT seller_id, id, user_id, status::text, total, created_at FROM bazaar.orders;
INSERT INTO order_items
SELECT o.seller_id, i.order_id, i.product_id, i.quantity, i.unit_price
FROM bazaar.order_items i
JOIN bazaar.orders o ON o.id = i.order_id;

-- Foreign tables have no local statistics until ANALYZE samples the shards.
ANALYZE products, orders, order_items;
