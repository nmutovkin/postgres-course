-- Exercise 2: settings that come from postgresql.auto.conf on a standby.
-- Run on m19-replica1 and on m19-primary and compare.
SELECT name, setting, applied
FROM pg_file_settings
WHERE sourcefile LIKE '%postgresql.auto.conf'
ORDER BY seqno;
