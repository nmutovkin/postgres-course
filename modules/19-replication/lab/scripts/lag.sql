-- Replication lag for every consumer of m19-primary's WAL, physical and logical.
-- Run on the PRIMARY:
--   docker exec -i m19-primary psql -U postgres -d bazaar < scripts/lag.sql
--
-- One row per replication slot or walsender:
--   kind              physical / logical
--   lag_bytes         WAL the consumer has not yet confirmed/replayed
--   replay_lag        time-based lag reported by the walsender (NULL when idle)
--   retained_wal      WAL kept on the primary's disk for this slot
--   wal_status        reserved / extended / unreserved / lost (slot health)
--   alert             OK / WARN / CRIT using the thresholds below
WITH thresholds AS (
    SELECT pg_size_bytes('64MB')  AS warn_bytes,
           pg_size_bytes('512MB') AS crit_bytes,
           interval '30 seconds'  AS warn_time,
           interval '5 minutes'   AS crit_time
),
consumers AS (
    SELECT
        coalesce(s.slot_name, r.application_name)    AS name,
        coalesce(s.slot_type, 'physical')            AS kind,
        r.state,
        r.sync_state,
        s.active,
        s.wal_status,
        -- How far behind the consumer is, in bytes:
        --  * physical: from what the standby has replayed;
        --  * logical:  from what the subscriber has confirmed applying.
        pg_wal_lsn_diff(pg_current_wal_lsn(),
                        coalesce(r.replay_lsn, s.confirmed_flush_lsn, s.restart_lsn)) AS lag_bytes,
        r.replay_lag,
        pg_wal_lsn_diff(pg_current_wal_lsn(), s.restart_lsn) AS retained_bytes
    FROM pg_replication_slots AS s
    FULL JOIN pg_stat_replication AS r ON r.pid = s.active_pid
)
SELECT
    c.name,
    c.kind,
    coalesce(c.state, 'DISCONNECTED')        AS state,
    c.sync_state,
    pg_size_pretty(c.lag_bytes)              AS lag,
    c.replay_lag,
    pg_size_pretty(c.retained_bytes)         AS retained_wal,
    c.wal_status,
    CASE
        WHEN c.wal_status = 'lost'
          OR c.state IS NULL
          OR c.lag_bytes  > t.crit_bytes
          OR c.replay_lag > t.crit_time       THEN 'CRIT'
        WHEN c.wal_status = 'unreserved'
          OR c.lag_bytes  > t.warn_bytes
          OR c.replay_lag > t.warn_time       THEN 'WARN'
        ELSE 'OK'
    END                                       AS alert
FROM consumers AS c
CROSS JOIN thresholds AS t
ORDER BY c.kind, c.name;
