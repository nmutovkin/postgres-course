-- Exercise 9: four new tables on the Citus cluster (state: citus-build.sh small).
--   docker exec -i m21-coord psql -U postgres -d bazaar -v ON_ERROR_STOP=1 < solutions/ex09_new_tables.sql
SET search_path = bazaar, public;
SET citus.multi_shard_modify_mode = 'sequential';   -- FKs to reference tables (section 13.3)

-- 1. shipping_carriers: ~20 rows, read by every order -> reference table.
CREATE TABLE shipping_carriers (
    id           int  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name         text NOT NULL UNIQUE,
    tracking_url text NOT NULL
);
SELECT create_reference_table('shipping_carriers');
INSERT INTO shipping_carriers (name, tracking_url)
VALUES ('DHL', 'https://dhl.example/t/{code}'), ('UPS', 'https://ups.example/t/{code}');
-- distributed -> reference FK: allowed. Name the constraint yourself: with the
-- inline form (ADD COLUMN ... REFERENCES) Citus 14.2 named it "orders__fkey".
ALTER TABLE orders ADD COLUMN carrier_id int;
ALTER TABLE orders ADD CONSTRAINT orders_carrier_id_fkey
    FOREIGN KEY (carrier_id) REFERENCES shipping_carriers (id);

-- 2. coupons: platform-wide codes, unique across the platform, read at every
--    checkout on every shard, few writes -> reference table (UNIQUE (code) is
--    fine on a reference table: it has one shard).
CREATE TABLE coupons (
    code        text          PRIMARY KEY,
    percent_off numeric(5,2)  NOT NULL CHECK (percent_off > 0 AND percent_off <= 100),
    valid       tstzrange     NOT NULL
);
SELECT create_reference_table('coupons');
INSERT INTO coupons VALUES ('WELCOME10', 10, tstzrange('2025-01-01', '2026-01-01'));

-- New tables on Citus: create them WITHOUT foreign keys, distribute them, THEN
-- add the FKs. A CREATE TABLE ... REFERENCES <reference table> turns the new
-- table into a "Citus managed local table" at once, and converting that to a
-- distributed table fails for identity columns (section 13.3, item 1).

-- 3. seller_payouts: per seller, monthly -> distributed by seller_id, co-located.
CREATE TABLE seller_payouts (
    seller_id  bigint        NOT NULL,
    id         bigint        GENERATED ALWAYS AS IDENTITY,
    period     daterange     NOT NULL,
    amount     numeric(12,2) NOT NULL,
    paid_at    timestamptz,
    PRIMARY KEY (seller_id, id),
    -- no overlapping payout periods per seller: EXCLUDE must use seller_id WITH =
    EXCLUDE USING gist (seller_id WITH =, period WITH &&)
);
SELECT create_distributed_table('seller_payouts', 'seller_id', colocate_with => 'products');
ALTER TABLE seller_payouts ADD FOREIGN KEY (seller_id) REFERENCES sellers (id);

-- 4. product_questions: Q&A on a product page -> belongs to the product's
--    seller: distributed by seller_id, co-located, composite FK to products.
CREATE TABLE product_questions (
    seller_id  bigint      NOT NULL,
    id         bigint      GENERATED ALWAYS AS IDENTITY,
    product_id bigint      NOT NULL,
    user_id    bigint      NOT NULL,
    question   text        NOT NULL,
    answer     text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (seller_id, id)
);
CREATE INDEX ON product_questions (seller_id, product_id);
SELECT create_distributed_table('product_questions', 'seller_id', colocate_with => 'products');
-- Citus wants one ADD CONSTRAINT per ALTER TABLE on distributed tables
ALTER TABLE product_questions ADD FOREIGN KEY (user_id) REFERENCES users (id);
ALTER TABLE product_questions
    ADD FOREIGN KEY (seller_id, product_id) REFERENCES products (seller_id, id) ON DELETE CASCADE;

INSERT INTO product_questions (seller_id, product_id, user_id, question)
VALUES (2, 24, 8, 'Is this the hardcover edition?');
INSERT INTO seller_payouts (seller_id, period, amount)
VALUES (2, daterange('2025-05-01', '2025-06-01'), 812.40);

-- Checks
SELECT table_name, citus_table_type, distribution_column, colocation_id
FROM citus_tables
WHERE table_name::text IN ('shipping_carriers', 'coupons', 'seller_payouts', 'product_questions')
ORDER BY 1;

EXPLAIN (COSTS OFF)
SELECT q.question, p.title, u.full_name
FROM product_questions q
JOIN products p ON p.seller_id = q.seller_id AND p.id = q.product_id
JOIN users u    ON u.id = q.user_id
WHERE q.seller_id = 2 AND q.product_id = 24;

SELECT q.question, p.title, u.full_name
FROM product_questions q
JOIN products p ON p.seller_id = q.seller_id AND p.id = q.product_id
JOIN users u    ON u.id = q.user_id
WHERE q.seller_id = 2 AND q.product_id = 24;

-- the overlap check runs on the shard:
INSERT INTO seller_payouts (seller_id, period, amount)
VALUES (2, daterange('2025-05-15', '2025-06-15'), 1.00);
