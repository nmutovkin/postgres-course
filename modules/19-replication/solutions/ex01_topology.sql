-- Exercise 1: what is this node, where does it get WAL from, who streams from it?
-- Run on every node:
--   for n in m19-primary m19-replica1 m19-replica2 m19-delayed m19-replica3; do
--     docker exec -i $n psql -U postgres -X -q < solutions/ex01_topology.sql; done
SELECT current_setting('cluster_name')                          AS node,
       CASE WHEN pg_is_in_recovery() THEN 'standby' ELSE 'primary' END AS role,
       (SELECT sender_host FROM pg_stat_wal_receiver)           AS streams_from,
       (SELECT string_agg(application_name || ' (' || sync_state || ')', ', '
                          ORDER BY application_name)
        FROM pg_stat_replication)                               AS feeds;
