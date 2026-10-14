-- =============================================================================
-- redundant_indexes.sql  --  Module 11, exercise 12
-- Non-unique, non-partial, non-expression indexes whose key columns (with the
-- same operator classes and sort options) are a prefix of another index of the
-- same type on the same table. Expect 0 rows at state 12.
-- =============================================================================
SET search_path = bazaar, public;

WITH idx AS (
    SELECT ix.indexrelid,
           ix.indrelid,
           ix.indisunique,
           ix.indpred IS NOT NULL AS is_partial,
           ix.indexprs IS NOT NULL AS has_expressions,
           c.relam,
           string_to_array(ix.indkey::text, ' ')::int2[]   AS cols,
           string_to_array(ix.indclass::text, ' ')::oid[]  AS opclasses,
           string_to_array(ix.indoption::text, ' ')::int2[] AS options,
           ix.indnkeyatts
    FROM pg_index AS ix
    JOIN pg_class AS c ON c.oid = ix.indexrelid
    WHERE c.relnamespace = 'bazaar'::regnamespace
)
SELECT a.indrelid::regclass AS table_name,
       a.indexrelid::regclass AS redundant_index,
       b.indexrelid::regclass AS covered_by,
       pg_size_pretty(pg_relation_size(a.indexrelid)) AS wasted
FROM idx AS a
JOIN idx AS b
  ON  b.indrelid = a.indrelid
  AND b.indexrelid <> a.indexrelid
  AND b.relam = a.relam
WHERE NOT a.indisunique                    -- a unique index enforces a rule: never "redundant"
  AND NOT a.is_partial AND NOT b.is_partial
  AND NOT a.has_expressions AND NOT b.has_expressions
  AND a.indnkeyatts <= b.indnkeyatts
  AND a.cols[1:a.indnkeyatts]      = b.cols[1:a.indnkeyatts]       -- same leading key columns,
  AND a.opclasses[1:a.indnkeyatts] = b.opclasses[1:a.indnkeyatts]  -- same operator classes
  AND a.options[1:a.indnkeyatts]   = b.options[1:a.indnkeyatts]    -- same ASC/DESC, NULLS
  AND (a.indnkeyatts < b.indnkeyatts OR a.indexrelid > b.indexrelid)  -- exact duplicates: report one
ORDER BY pg_relation_size(a.indexrelid) DESC;
