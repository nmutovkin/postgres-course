-- =============================================================================
-- inventory_fillfactor.sql  --  Module 16 project milestone (reference solution)
-- -----------------------------------------------------------------------------
-- Make inventory updates HOT.
--
-- Arithmetic (see project.md):
--   row = 24 B header + 8 (product_id) + 4 (quantity) + 4 (reserved) + 8 (updated_at)
--       = 48 B, plus a 4 B line pointer = 52 B per row version
--   fillfactor 100: (8192 - 24) / 52 = 157 rows per page, ~0 bytes free
--   The heaviest burst per page between two prunes is the restock, which
--   updates every 10th product in ONE transaction: ~16 of the 157 rows of a
--   page, i.e. 16 x 52 = 832 B of new versions that must fit on the page,
--   because none of the old versions can be pruned before that transaction ends.
--   fillfactor 90 reserves 819 B on the page, and pages hold only ~141 rows
--   (14 restocked, 14 x 52 = 728 B): just enough. 85 would leave a safety margin
--   at the cost of ~6% more disk.
--
-- fillfactor applies only to pages written from now on, so the table is
-- rewritten. CLUSTER (rather than VACUUM FULL) rewrites it in primary-key order,
-- which also undoes any reshuffling caused by earlier non-HOT updates (rows
-- that moved to the end of the table). Both take an ACCESS EXCLUSIVE lock for
-- the duration: fine for a 10 MB table, a maintenance-window job for a big one
-- (module 17 covers online alternatives).
--
-- Run:  docker exec -i course-pg psql -U postgres -d bazaar < modules/16-internals/solutions/inventory_fillfactor.sql
-- =============================================================================

SET search_path = bazaar, public;

ALTER TABLE inventory SET (fillfactor = 90);
CLUSTER inventory USING inventory_pkey;
ANALYZE inventory;

SELECT c.reloptions,
       pg_size_pretty(pg_relation_size(c.oid)) AS size,
       c.relpages,
       round(c.reltuples / c.relpages)        AS rows_per_page
FROM pg_class c
WHERE c.oid = 'inventory'::regclass;
