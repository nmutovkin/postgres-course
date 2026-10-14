-- =============================================================================
-- reservation_workload.sql  --  Module 16 project milestone: the measurement
-- -----------------------------------------------------------------------------
-- Simulates a busy day for inventory:
--   1. 20,000 single-row reservations (reserved + 1), in transactions of 10
--      updates each (one "checkout"), spread over the whole table by a hash of
--      the loop counter (deterministic), as place_order() does;
--   2. a restock: one transaction adds 5 units to every 10th product
--      (20,000 rows), like a nightly supplier feed;
--   3. the same 20,000 reservations released (reserved - 1);
--   4. the restock undone (one transaction).
-- The net effect on the data is zero, but every update writes a new row version.
--
-- Before the workload it resets inventory's cumulative statistics counters, so
-- the report at the end shows exactly this run.
--
-- Run:  docker exec -i course-pg psql -U postgres -d bazaar < modules/16-internals/solutions/reservation_workload.sql
-- (outside an explicit transaction: the DO block commits as it goes)
-- =============================================================================

SET search_path = bazaar, public;

SELECT pg_stat_reset_single_table_counters('inventory'::regclass);
SELECT pg_relation_size('inventory') AS size_before,
       pg_relation_size('inventory_pkey') AS index_before \gset

DO $$
DECLARE
    pid bigint;
BEGIN
    FOR pass IN 1..2 LOOP                         -- 1: reserve, 2: release
        FOR i IN 1..20000 LOOP
            pid := 1 + (hashint4(i) & 2147483647) % 200000;
            IF pass = 1 THEN
                UPDATE inventory SET reserved = reserved + 1
                WHERE product_id = pid AND reserved < quantity;
            ELSE
                UPDATE inventory SET reserved = reserved - 1
                WHERE product_id = pid AND reserved > 0;
            END IF;
            IF i % 10 = 0 THEN
                COMMIT;                           -- a "checkout" = 10 rows
            END IF;
        END LOOP;
        COMMIT;
        -- the restock (pass 1) and its reversal (pass 2): one big transaction each
        UPDATE inventory SET quantity = quantity + CASE pass WHEN 1 THEN 5 ELSE -5 END
        WHERE product_id % 10 = 0;
        COMMIT;
    END LOOP;
END
$$;

SELECT pg_stat_force_next_flush();
SELECT pg_sleep(1);

SELECT s.n_tup_upd,
       s.n_tup_hot_upd,
       round(100.0 * s.n_tup_hot_upd / nullif(s.n_tup_upd, 0), 1)    AS hot_pct,
       pg_size_pretty(:size_before::bigint)                         AS size_before,
       pg_size_pretty(pg_relation_size('inventory'))                AS size_after,
       pg_size_pretty(:index_before::bigint)                        AS pkey_before,
       pg_size_pretty(pg_relation_size('inventory_pkey'))           AS pkey_after,
       coalesce((SELECT opt FROM unnest(c.reloptions) AS opt WHERE opt LIKE 'fillfactor=%'),
                'fillfactor=100 (default)')                         AS fillfactor
FROM pg_stat_user_tables s
JOIN pg_class c ON c.oid = s.relid
WHERE s.relname = 'inventory';
