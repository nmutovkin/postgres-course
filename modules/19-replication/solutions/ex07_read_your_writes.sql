-- Exercise 7: read-your-writes with an LSN wait, in one psql script.
-- Run from inside m19-analytics (it can reach every lab node):
--   docker exec -i -e PGPASSWORD=postgres m19-analytics \
--       psql -U postgres -X -q -f - < solutions/ex07_read_your_writes.sql
\set ON_ERROR_STOP on

-- 1. Write on the primary and remember where its WAL ended.
\connect 'host=m19-primary dbname=bazaar user=postgres'
SELECT place_order(2, '[{"product_id": 1, "quantity": 1}]') AS order_id \gset
SELECT pg_current_wal_lsn() AS lsn \gset
\echo placed order :order_id, commit LSN at most :lsn

-- 2. On the replica, wait (at most 5 s) until it has replayed that LSN.
\connect 'host=m19-replica2 dbname=bazaar user=postgres'
SET my.wait_lsn = :'lsn';
DO $$
DECLARE
    target   pg_lsn      := current_setting('my.wait_lsn')::pg_lsn;
    deadline timestamptz := clock_timestamp() + interval '5 seconds';
BEGIN
    WHILE pg_last_wal_replay_lsn() < target LOOP
        IF clock_timestamp() > deadline THEN
            RAISE EXCEPTION 'replica has not replayed % within 5 s (it is at %)',
                            target, pg_last_wal_replay_lsn();
        END IF;
        PERFORM pg_sleep(0.05);
    END LOOP;
END
$$;

-- 3. Now the read is guaranteed to see the write.
SELECT id, status FROM orders WHERE id = :order_id;
