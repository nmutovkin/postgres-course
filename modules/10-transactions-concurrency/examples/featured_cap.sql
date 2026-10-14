-- featured_cap.sql  --  section 8.4: write skew at scale, and pgbench retries
-- Rule: a seller may feature at most 3 products. The check-then-insert below
-- is correct when run alone, but under REPEATABLE READ concurrent clients
-- break the rule; under SERIALIZABLE some of them fail with 40001 and
-- pgbench retries them (--max-tries).
--
-- One-time setup (in psql):
--   CREATE SCHEMA IF NOT EXISTS lab;
--   CREATE TABLE lab.featured_products (
--       seller_id  bigint NOT NULL,
--       product_id bigint NOT NULL,
--       created_at timestamptz NOT NULL DEFAULT clock_timestamp());
--
-- Each run:
--   ./project/bin/psql.sh -c "TRUNCATE lab.featured_products"
--   docker exec course-pg pgbench -U postgres -d bazaar -n -c 8 -j 8 -t 20 --max-tries=20 \
--       -D level='SERIALIZABLE' \
--       -f /course/modules/10-transactions-concurrency/examples/featured_cap.sql
--   ./project/bin/psql.sh -c "SELECT count(*) FROM lab.featured_products"
-- Use -D level='REPEATABLE READ' for the broken variant.
\set pid random(47, 56)
BEGIN ISOLATION LEVEL :level;
INSERT INTO lab.featured_products (seller_id, product_id)
SELECT 4, :pid
WHERE (SELECT count(*) FROM lab.featured_products WHERE seller_id = 4) < 3;
COMMIT;
