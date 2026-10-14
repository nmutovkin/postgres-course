-- Exercise 1: one row per non-predefined role: attributes, limits, memberships with options.
SELECT r.rolname,
       array_remove(ARRAY[
           CASE WHEN r.rolsuper       THEN 'SUPERUSER'   END,
           CASE WHEN r.rolcanlogin    THEN 'LOGIN'       END,
           CASE WHEN r.rolcreaterole  THEN 'CREATEROLE'  END,
           CASE WHEN r.rolcreatedb    THEN 'CREATEDB'    END,
           CASE WHEN r.rolreplication THEN 'REPLICATION' END,
           CASE WHEN r.rolbypassrls   THEN 'BYPASSRLS'   END,
           CASE WHEN NOT r.rolinherit THEN 'NOINHERIT'   END
       ], NULL) AS attributes,
       nullif(r.rolconnlimit, -1) AS conn_limit,
       (SELECT string_agg(
                   g.rolname || ' (' || concat_ws(',',
                       CASE WHEN m.admin_option   THEN 'admin'   END,
                       CASE WHEN m.inherit_option THEN 'inherit' END,
                       CASE WHEN m.set_option     THEN 'set'     END) || ')',
                   ', ' ORDER BY g.rolname)
        FROM pg_auth_members AS m
        JOIN pg_roles AS g ON g.oid = m.roleid
        WHERE m.member = r.oid) AS member_of,
       (SELECT string_agg(cfg, ', ')
        FROM pg_db_role_setting AS s, unnest(s.setconfig) AS cfg
        WHERE s.setrole = r.oid) AS role_settings
FROM pg_roles AS r
WHERE r.rolname !~ '^pg_'
ORDER BY r.rolname;
