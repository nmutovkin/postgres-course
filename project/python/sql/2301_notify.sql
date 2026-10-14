-- =============================================================================
-- 2301_notify.sql  --  Module 23: notifications for async consumers
-- -----------------------------------------------------------------------------
-- Two triggers that turn row changes into NOTIFY messages:
--
--   channel 'jobs_ready'  payload = queue name
--       sent once per INSERT statement on jobs and per distinct queue, so an
--       idle worker wakes up immediately instead of waiting for its next poll.
--   channel 'new_order'   payload = {"order_id":..,"seller_id":..,"user_id":..}
--       sent for every new order (place_order() inserts one per seller).
--
-- NOTIFY is transactional: listeners receive the message only when (and if)
-- the inserting transaction COMMITs, which is exactly when the row becomes
-- visible to them. Identical (channel, payload) pairs sent in the same
-- transaction are collapsed into one notification.
--
-- The 'new_order' payload carries ids only, not the total: the row trigger
-- fires before place_order() inserts the items (total is still 0 then). A
-- notification is a wake-up call; consumers read current data themselves.
--
-- Idempotent (CREATE OR REPLACE), applied on top of `reset.sh 22` / `reset.sh 23`:
--     ./project/bin/psql.sh < project/python/sql/2301_notify.sql
-- From module 26 on, Alembic owns schema changes and this file becomes a migration.
-- =============================================================================

SET search_path = bazaar, public;
SET ROLE bazaar_owner;   -- objects must belong to the owner role (module 15)

BEGIN;

CREATE OR REPLACE FUNCTION notify_jobs_ready()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_notify('jobs_ready', q.queue)
    FROM (SELECT DISTINCT queue FROM new_jobs) AS q;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE TRIGGER jobs_notify_ready
    AFTER INSERT ON jobs
    REFERENCING NEW TABLE AS new_jobs
    FOR EACH STATEMENT EXECUTE FUNCTION notify_jobs_ready();

CREATE OR REPLACE FUNCTION notify_new_order()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_notify('new_order', json_build_object(
        'order_id',  NEW.id,
        'seller_id', NEW.seller_id,
        'user_id',   NEW.user_id)::text);
    RETURN NULL;
END;
$$;

CREATE OR REPLACE TRIGGER orders_notify_new
    AFTER INSERT ON orders
    FOR EACH ROW EXECUTE FUNCTION notify_new_order();

COMMIT;

RESET ROLE;
