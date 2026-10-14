-- Exercise 11: generate setval() calls for every sequence of the OLD primary.
-- Run on the old primary with -At; pipe the output into the new database:
--   docker exec -i m19-primary psql -U postgres -d bazaar -Atq < solutions/ex11_sequences.sql |
--     docker exec -i m19-analytics psql -U postgres -d bazaar_v2
--
-- pg_sequences.last_value is NULL when is_called = false (for example after the
-- seeds' setval(seq, max(id) + 1, false)), so read each sequence relation itself:
-- the outer query builds one SELECT per sequence, \gexec runs them, and each of
-- those prints a ready-to-run setval() with the exact (last_value, is_called).
SELECT format('SELECT format(%L, %L, last_value, is_called) FROM %s',
              'SELECT setval(%L, %s, %L);', q, q)
FROM (SELECT format('%I.%I', schemaname, sequencename) AS q
      FROM pg_sequences
      ORDER BY 1) AS s
\gexec
