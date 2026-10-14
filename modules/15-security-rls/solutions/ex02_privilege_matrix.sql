-- Exercise 2: what can each login role do to each relation of schema bazaar?
-- Effective privileges (direct, through PUBLIC, or inherited through membership).
-- Partitions of events are left out: queries go through the parent.
WITH roles AS (
    SELECT rolname FROM pg_roles WHERE rolname IN ('bazaar_app', 'bazaar_analyst', 'bazaar_seller')
), rels AS (
    SELECT c.oid, c.relname, c.relkind, c.relrowsecurity
    FROM pg_class AS c
    WHERE c.relnamespace = 'bazaar'::regnamespace
      AND c.relkind IN ('r', 'p', 'v', 'm')
      AND NOT c.relispartition
)
SELECT rels.relname,
       rels.relrowsecurity AS rls,
       max(CASE r.rolname WHEN 'bazaar_app' THEN p.flags END)     AS app,
       max(CASE r.rolname WHEN 'bazaar_analyst' THEN p.flags END) AS analyst,
       max(CASE r.rolname WHEN 'bazaar_seller' THEN p.flags END)  AS seller
FROM rels
CROSS JOIN roles AS r
CROSS JOIN LATERAL (
    SELECT concat(
        CASE WHEN has_table_privilege(r.rolname, rels.oid, 'SELECT')   THEN 'r'
             WHEN has_any_column_privilege(r.rolname, rels.oid, 'SELECT') THEN 'r(cols)' END,
        CASE WHEN has_table_privilege(r.rolname, rels.oid, 'INSERT')   THEN 'a' END,
        CASE WHEN has_table_privilege(r.rolname, rels.oid, 'UPDATE')   THEN 'w'
             WHEN has_any_column_privilege(r.rolname, rels.oid, 'UPDATE') THEN 'w(cols)' END,
        CASE WHEN has_table_privilege(r.rolname, rels.oid, 'DELETE')   THEN 'd' END,
        CASE WHEN has_table_privilege(r.rolname, rels.oid, 'TRUNCATE') THEN 'D' END) AS flags
) AS p
GROUP BY rels.relname, rels.relrowsecurity
ORDER BY rels.relname;
