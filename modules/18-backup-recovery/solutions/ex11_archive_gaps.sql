-- Exercise 11: find gaps in the WAL archive.
-- Run on m18-primary as a superuser (pg_ls_dir/pg_read_file on paths outside
-- the data directory need superuser, or explicit GRANTs on those functions):
--   docker exec -i m18-primary psql -U postgres -d bazaar < solutions/ex11_archive_gaps.sql
--
-- A WAL file name is TTTTTTTTLLLLLLLLSSSSSSSS (hex): timeline, "log" and
-- segment within the log. With 16 MB segments there are 256 segments per
-- log, so segment number n = log * 256 + seg, and consecutive segments have
-- consecutive n. Every segment from the oldest kept base backup's START WAL
-- up to the newest archived segment must be present.
WITH archived AS (
    SELECT f
    FROM pg_ls_dir('/archive/wal') AS f
    WHERE f ~ '^[0-9A-F]{24}$'                       -- segments only (no .history/.backup/.partial)
),
oldest_backup AS (
    -- catalog.tsv lists every backup ever taken; keep those whose directory
    -- still exists (retention deletes old ones) and take the oldest start WAL.
    SELECT min(split_part(line, E'\t', 6)) AS start_wal
    FROM regexp_split_to_table(pg_read_file('/backups/base/catalog.tsv'), E'\n') AS line
    WHERE split_part(line, E'\t', 1) IN (SELECT d FROM pg_ls_dir('/backups/base') AS d)
),
bounds AS (
    SELECT substr(b.start_wal, 1, 8) AS tli,
           ('x' || substr(b.start_wal, 9, 8))::bit(32)::bigint * 256
         + ('x' || substr(b.start_wal, 17, 8))::bit(32)::bigint AS first_n,
           (SELECT ('x' || substr(max(f), 9, 8))::bit(32)::bigint * 256
                 + ('x' || substr(max(f), 17, 8))::bit(32)::bigint
            FROM archived
            WHERE substr(f, 1, 8) = substr(b.start_wal, 1, 8)) AS last_n
    FROM oldest_backup AS b
),
expected AS (
    SELECT b.tli
        || lpad(upper(to_hex(n / 256)), 8, '0')
        || lpad(upper(to_hex(n % 256)), 8, '0') AS f
    FROM bounds AS b, generate_series(b.first_n, b.last_n) AS n
)
SELECT e.f AS missing_segment
FROM expected AS e
WHERE NOT EXISTS (SELECT 1 FROM archived AS a WHERE a.f = e.f)
ORDER BY 1;
