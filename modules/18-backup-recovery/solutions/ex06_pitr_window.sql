-- Exercise 6: the point-in-time recovery window of the lab's nightly backups.
-- Earliest point: the moment the oldest *kept* base backup became consistent
-- (its end; approximated by finished_at in catalog.tsv). Latest point: the
-- last archived WAL segment (anything after it is only in pg_wal).
-- Superuser only (pg_read_file/pg_ls_dir outside the data directory).
WITH catalog AS (
    SELECT split_part(line, E'\t', 1)                  AS label,
           split_part(line, E'\t', 3)::timestamptz     AS finished_at
    FROM regexp_split_to_table(pg_read_file('/backups/base/catalog.tsv'), E'\n') AS line
    WHERE line LIKE 'base-%'
),
kept AS (
    SELECT c.*
    FROM catalog AS c
    WHERE c.label IN (SELECT d FROM pg_ls_dir('/backups/base') AS d)
)
SELECT
    (SELECT min(finished_at) FROM kept)                     AS restorable_from,
    a.last_archived_time                                    AS restorable_until,
    a.last_archived_time - (SELECT min(finished_at) FROM kept) AS window_length,
    (SELECT count(*) FROM kept)                             AS base_backups_kept
FROM pg_stat_archiver AS a;
