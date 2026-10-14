-- Exercise 8: tables that cannot publish UPDATE/DELETE, and tables with FULL identity.
SELECT c.oid::regclass AS table_name,
       CASE c.relreplident
           WHEN 'd' THEN 'default (no primary key!)'
           WHEN 'n' THEN 'nothing'
       END AS problem
FROM pg_class AS c
WHERE c.relnamespace = 'bazaar'::regnamespace
  AND c.relkind IN ('r', 'p')
  AND NOT c.relispartition
  AND (c.relreplident = 'n'
       OR (c.relreplident = 'd'
           AND NOT EXISTS (SELECT 1 FROM pg_index AS i
                           WHERE i.indrelid = c.oid AND i.indisprimary)))
UNION ALL
SELECT c.oid::regclass,
       'FULL: every UPDATE/DELETE logs the whole old row; subscriber needs an index to find rows'
FROM pg_class AS c
WHERE c.relnamespace = 'bazaar'::regnamespace
  AND c.relkind IN ('r', 'p')
  AND c.relreplident = 'f'
ORDER BY 1;
