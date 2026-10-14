-- =============================================================================
-- fk_audit.sql  --  Module 11, exercise 7
-- Foreign keys whose columns are not the leading columns of any non-partial
-- index on the referencing table (in any order). Expect 0 rows at state 12.
-- =============================================================================
SET search_path = bazaar, public;

SELECT c.conrelid::regclass AS table_name,
       c.conname AS fk_name,
       array_agg(a.attname ORDER BY k.ord) AS fk_columns,
       pg_size_pretty(pg_relation_size(c.conrelid)) AS table_size
FROM pg_constraint AS c
CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
JOIN pg_attribute AS a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
WHERE c.contype = 'f'
  AND c.connamespace = 'bazaar'::regnamespace
  AND NOT EXISTS (
        SELECT 1
        FROM pg_index AS ix
        WHERE ix.indrelid = c.conrelid
          AND ix.indpred IS NULL                       -- a partial index cannot serve every lookup
          AND (string_to_array(ix.indkey::text, ' ')::int2[])[1:cardinality(c.conkey)]
              @> c.conkey                              -- the FK columns are the leading columns,
          AND (string_to_array(ix.indkey::text, ' ')::int2[])[1:cardinality(c.conkey)]
              <@ c.conkey                              -- in any order
      )
GROUP BY c.conrelid, c.conname
ORDER BY pg_relation_size(c.conrelid) DESC, fk_name;
