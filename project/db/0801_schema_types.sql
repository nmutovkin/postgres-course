-- =============================================================================
-- 0801_schema_types.sql  --  Module 08: Beyond Scalars
-- -----------------------------------------------------------------------------
-- Replaces "text + CHECK" status columns with ENUM types, introduces an
-- email_address DOMAIN, adds semi-structured product data (jsonb attributes and
-- a text[] of tags), and a promotions table whose "no overlapping promotions
-- per product" rule is enforced by an EXCLUSION constraint over tstzrange.
--
-- Everything runs in one transaction: DDL is transactional in PostgreSQL, so a
-- failure half-way leaves the schema untouched.
-- =============================================================================

SET search_path = bazaar, public;

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Enum types
--    An enum value takes 4 bytes, sorts in declaration order (pending < paid <
--    shipped ...), and rejects typos at the type level. Trade-off: adding a value
--    is easy (ALTER TYPE ... ADD VALUE), removing or renaming one is not.
-- -----------------------------------------------------------------------------
CREATE TYPE order_status   AS ENUM ('pending', 'paid', 'shipped', 'delivered', 'cancelled', 'refunded');
CREATE TYPE product_status AS ENUM ('draft', 'active', 'archived');
CREATE TYPE payment_status AS ENUM ('pending', 'succeeded', 'failed');

-- Converting a column: the old CHECK and the text DEFAULT must go first (the
-- default 'pending'::text cannot be cast automatically), then ALTER ... TYPE
-- with a USING expression rewrites the table, then the default comes back as
-- an enum literal.
ALTER TABLE orders DROP CONSTRAINT orders_status_check;
ALTER TABLE orders ALTER COLUMN status DROP DEFAULT;
ALTER TABLE orders ALTER COLUMN status TYPE order_status USING status::order_status;
ALTER TABLE orders ALTER COLUMN status SET DEFAULT 'pending';

ALTER TABLE products DROP CONSTRAINT products_status_check;
ALTER TABLE products ALTER COLUMN status DROP DEFAULT;
ALTER TABLE products ALTER COLUMN status TYPE product_status USING status::product_status;
ALTER TABLE products ALTER COLUMN status SET DEFAULT 'draft';

ALTER TABLE payments DROP CONSTRAINT payments_status_check;
ALTER TABLE payments ALTER COLUMN status TYPE payment_status USING status::payment_status;

-- -----------------------------------------------------------------------------
-- 2. A domain for e-mail addresses
--    A domain is a named base type plus constraints, reusable across tables.
--    The regex is deliberately simple ("something@something.tld"); real
--    validation means sending a confirmation e-mail.
-- -----------------------------------------------------------------------------
CREATE DOMAIN email_address AS text
    CONSTRAINT email_address_format CHECK (VALUE ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$');

-- Converting to the domain re-checks every existing row.
ALTER TABLE users ALTER COLUMN email TYPE email_address;

-- E-mail addresses are case-insensitive in practice: 'Zoe.Martin@Example.COM'
-- and 'zoe.martin@example.com' must not be two accounts. Replace the
-- case-sensitive UNIQUE constraint with a unique EXPRESSION index on lower(email).
-- (Consequences: look users up with WHERE lower(email) = lower($1) so the index
-- is usable, and upserts must say ON CONFLICT (lower(email)).)
ALTER TABLE users DROP CONSTRAINT users_email_key;
CREATE UNIQUE INDEX users_email_lower_key ON users (lower(email));

-- -----------------------------------------------------------------------------
-- 3. Semi-structured product data
--    attributes: category-specific properties ({"ram_gb": 16} for laptops,
--                {"author": ...} for books). jsonb is parsed, binary, indexable.
--    tags:       a small list of labels; text[] supports @>, && and GIN indexes.
--    Both are NOT NULL with "empty" defaults so queries need no COALESCE.
-- -----------------------------------------------------------------------------
ALTER TABLE products
    ADD COLUMN attributes jsonb  NOT NULL DEFAULT '{}',
    ADD COLUMN tags       text[] NOT NULL DEFAULT '{}',
    ADD CONSTRAINT products_attributes_is_object CHECK (jsonb_typeof(attributes) = 'object');

-- -----------------------------------------------------------------------------
-- 4. Promotions with a range type and an exclusion constraint
--    active_during is a half-open interval [start, end); an empty upper bound
--    means "until further notice".
--    EXCLUDE USING gist (product_id WITH =, active_during WITH &&) means: no two
--    rows may have equal product_id AND overlapping ranges. GiST indexes do not
--    support "=" on bigint out of the box; the btree_gist extension adds that.
-- -----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA public;

CREATE TABLE promotions (
    id             bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id     bigint       NOT NULL REFERENCES products (id) ON DELETE CASCADE,
    discount_pct   numeric(5,2) NOT NULL,
    active_during  tstzrange    NOT NULL,
    created_at     timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT promotions_discount_pct_range CHECK (discount_pct > 0 AND discount_pct < 100),
    CONSTRAINT promotions_range_not_empty CHECK (NOT isempty(active_during)),
    CONSTRAINT promotions_no_overlap
        EXCLUDE USING gist (product_id WITH =, active_during WITH &&)
);

COMMENT ON TABLE promotions IS 'Time-boxed percentage discounts; never overlapping per product.';
COMMENT ON COLUMN products.attributes IS 'Category-specific properties as a JSON object.';

COMMIT;
