-- Exercise 7: find objects in schema bazaar that are not owned by bazaar_owner,
-- hand them over, and give them the grants the default privileges would have given.

-- 1. The report.
SELECT 'relation' AS kind, c.oid::regclass::text AS object, c.relkind::text AS subtype,
       pg_get_userbyid(c.relowner) AS owner
FROM pg_class AS c
WHERE c.relnamespace = 'bazaar'::regnamespace
  AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
  AND c.relowner <> 'bazaar_owner'::regrole
UNION ALL
SELECT 'routine', p.oid::regprocedure::text, p.prokind::text, pg_get_userbyid(p.proowner)
FROM pg_proc AS p
WHERE p.pronamespace = 'bazaar'::regnamespace
  AND p.proowner <> 'bazaar_owner'::regrole
ORDER BY 1, 2;

-- 2. Generate and run the fix. Identity sequences and indexes follow their
--    table, so only tables, views, standalone sequences and routines are listed.
SELECT stmt
FROM (
    SELECT c.oid::regclass::text AS obj,
           unnest(CASE c.relkind
               WHEN 'S' THEN ARRAY[
                   format('ALTER SEQUENCE %s OWNER TO bazaar_owner', c.oid::regclass),
                   format('GRANT USAGE, SELECT ON SEQUENCE %s TO bazaar_app', c.oid::regclass)]
               ELSE ARRAY[
                   format('ALTER TABLE %s OWNER TO bazaar_owner', c.oid::regclass),
                   format('GRANT SELECT, INSERT, UPDATE, DELETE ON %s TO bazaar_app', c.oid::regclass),
                   format('GRANT SELECT ON %s TO bazaar_readonly', c.oid::regclass)]
           END) AS stmt
    FROM pg_class AS c
    WHERE c.relnamespace = 'bazaar'::regnamespace
      AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
      AND c.relowner <> 'bazaar_owner'::regrole
      AND NOT EXISTS (SELECT 1 FROM pg_depend AS d          -- skip identity/serial sequences
                      WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid
                        AND d.deptype IN ('a', 'i'))
    UNION ALL
    SELECT p.oid::regprocedure::text,
           unnest(ARRAY[
               format('ALTER ROUTINE %s OWNER TO bazaar_owner', p.oid::regprocedure),
               format('GRANT EXECUTE ON ROUTINE %s TO bazaar_app', p.oid::regprocedure)])
    FROM pg_proc AS p
    WHERE p.pronamespace = 'bazaar'::regnamespace
      AND p.proowner <> 'bazaar_owner'::regrole
) AS fixes
ORDER BY obj, stmt LIKE 'GRANT%'   -- ALTER ... OWNER first, then the grants
\gexec
