-- Exercise 2: one-row archiver health check (run on m18-primary).
SELECT
    (SELECT count(*) FROM pg_ls_archive_statusdir() WHERE name LIKE '%.ready')  AS segments_waiting,
    (SELECT pg_size_pretty(sum(size)) FROM pg_ls_waldir())                       AS pg_wal_size,
    a.last_archived_wal,
    extract(epoch FROM now() - a.last_archived_time)::int                        AS seconds_since_last_archive,
    coalesce(a.last_failed_time > a.last_archived_time, false)                   AS is_failing
FROM pg_stat_archiver AS a;
