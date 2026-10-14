-- Module 19 milestone, PUBLISHER side. Run on m19-primary:
--   docker exec -i m19-primary psql -U postgres -d bazaar -v ON_ERROR_STOP=1 < scripts/setup-analytics.sql
--
-- Publishes what the analytics team needs, and nothing else:
--   * products     without the free-text/JSON columns (column list)
--   * orders       only once they have been paid (row filter)
--   * order_items  all rows
SET search_path = bazaar, public;

-- 1. A role for the subscription to connect as. REPLICATION allows it to
--    open a logical replication connection; SELECT is needed for the
--    initial copy of each table. It cannot write anything.
CREATE ROLE analytics_repl WITH LOGIN REPLICATION PASSWORD 'analytics_repl';
GRANT USAGE ON SCHEMA bazaar TO analytics_repl;
GRANT SELECT ON products, orders, order_items TO analytics_repl;

-- 2. The row filter below uses paid_at, which is not part of the primary key.
--    For UPDATE and DELETE, PostgreSQL evaluates the filter on the OLD row
--    too, and it only has the old values of replica-identity columns in WAL.
--    Without this, every UPDATE/DELETE on orders would fail with
--    "Column used in the publication WHERE expression is not part of the replica identity".
--    FULL logs the whole old row for every UPDATE/DELETE (more WAL; orders rows are small).
ALTER TABLE orders REPLICA IDENTITY FULL;

-- 3. The publication.
CREATE PUBLICATION bazaar_analytics FOR TABLE
    products (id, seller_id, category_id, sku, title, price, status, created_at, updated_at),
    orders WHERE (paid_at IS NOT NULL),
    order_items;
