-- Exercise 5: replication slots, most dangerous first. Run on the primary.
WITH cap AS (
    SELECT CASE WHEN current_setting('max_slot_wal_keep_size') = '-1' THEN NULL
                ELSE pg_size_bytes(current_setting('max_slot_wal_keep_size')) END AS bytes
)
SELECT s.slot_name,
       s.slot_type,
       s.active,
       s.wal_status,
       pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), s.restart_lsn)) AS retained,
       pg_size_pretty(s.safe_wal_size)                                       AS safe_wal_size,
       round(100.0 * pg_wal_lsn_diff(pg_current_wal_lsn(), s.restart_lsn) / cap.bytes, 1)
                                                                             AS pct_of_cap
FROM pg_replication_slots AS s
CROSS JOIN cap
ORDER BY
    CASE s.wal_status WHEN 'lost' THEN 0 WHEN 'unreserved' THEN 1 WHEN 'extended' THEN 2 ELSE 3 END,
    s.active,                                                          -- inactive (false) first
    pg_wal_lsn_diff(pg_current_wal_lsn(), s.restart_lsn) DESC NULLS FIRST;
