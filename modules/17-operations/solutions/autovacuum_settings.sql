-- =============================================================================
-- autovacuum_settings.sql  --  Module 17 project milestone (reference solution)
-- -----------------------------------------------------------------------------
-- Per-table autovacuum settings for Bazaar's two hottest tables, plus per-role
-- guardrails. Storage parameters take only a SHARE UPDATE EXCLUSIVE lock, so
-- this is safe to run on a live system. Re-runnable.
--
-- Apply:   docker exec -i course-pg psql -U postgres -d bazaar -v ON_ERROR_STOP=1 \
--              < modules/17-operations/solutions/autovacuum_settings.sql
-- =============================================================================

SET search_path = bazaar, public;

-- -----------------------------------------------------------------------------
-- jobs: the background job queue (module 10).
-- Every job is inserted once and updated two or three times (dequeue_jobs sets
-- the lease, complete_job/fail_job finish it), so the table produces dead
-- tuples at the rate of the job throughput, while the set of *ready* jobs that
-- workers scan stays small. The default trigger (20% of the table) would let
-- tens of thousands of dead versions pile up in a table of 100k finished jobs,
-- and dequeue_jobs() would wade through them on every call.
--   * fixed thresholds (scale factor 0): vacuum after 1,000 dead tuples,
--     whatever the table size;
--   * no cost delay: the table is small and vacuuming it is cheap; finishing
--     quickly matters more than throttling;
--   * analyze after 1,000 changes, so the planner's idea of "ready jobs" stays
--     current;
--   * an insert trigger as well, so freshly inserted, never-updated jobs get
--     their visibility-map bits set;
--   * fillfactor 80: leaves room on each page so that lease updates (which do
--     not touch indexed columns) can be HOT.
-- -----------------------------------------------------------------------------
ALTER TABLE jobs SET (
    fillfactor                             = 80,
    autovacuum_vacuum_scale_factor         = 0,
    autovacuum_vacuum_threshold            = 1000,
    autovacuum_vacuum_insert_scale_factor  = 0,
    autovacuum_vacuum_insert_threshold     = 5000,
    autovacuum_analyze_scale_factor        = 0,
    autovacuum_analyze_threshold           = 1000,
    autovacuum_vacuum_cost_delay           = 0
);

-- -----------------------------------------------------------------------------
-- inventory: 200,000 rows, updated by every checkout (reserved) and every
-- shipment (quantity, reserved). Small rows, constant churn.
--   * 1% + 1,000 rows (= 3,000 dead tuples today) instead of 20% + 50
--     (= 40,050): vacuum early and often, so freed space is reused instead of
--     the table growing;
--   * analyze at 2%;
--   * a higher cost limit for this table's vacuums (default 200 for all workers
--     together), so each run is short;
--   * fillfactor 90 for HOT updates (module 16's milestone). It only affects
--     pages written from now on; module 16 shows how to rewrite the table so
--     existing pages get the free space too.
-- -----------------------------------------------------------------------------
ALTER TABLE inventory SET (
    fillfactor                       = 90,
    autovacuum_vacuum_scale_factor   = 0.01,
    autovacuum_vacuum_threshold      = 1000,
    autovacuum_analyze_scale_factor  = 0.02,
    autovacuum_analyze_threshold     = 1000,
    autovacuum_vacuum_cost_limit     = 1000
);

-- -----------------------------------------------------------------------------
-- Guardrails per role (section 10). IN DATABASE bazaar keeps them inside this
-- database, so ./project/bin/reset.sh removes them together with the database.
-- -----------------------------------------------------------------------------
ALTER ROLE bazaar_app     IN DATABASE bazaar SET statement_timeout = '5s';
ALTER ROLE bazaar_app     IN DATABASE bazaar SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE bazaar_app     IN DATABASE bazaar SET lock_timeout = '3s';
ALTER ROLE bazaar_analyst IN DATABASE bazaar SET statement_timeout = '5min';
ALTER ROLE bazaar_analyst IN DATABASE bazaar SET work_mem = '64MB';
