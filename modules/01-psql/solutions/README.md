# Module 01: Solutions

<!-- nav:start -->
> [Theory](../README.md) · [Exercises](../exercises.md) · [Project](../project.md) · **Solutions**  
> [← 00 Setup](../../00-setup/README.md) · [Course home](../../../README.md) · [02 Data Types and Tables →](../../02-types-and-ddl/README.md)
<!-- nav:end -->

Every solution was run on PostgreSQL 17 against `./project/bin/reset.sh 01` plus [`scratch_setup.sql`](scratch_setup.sql), except exercise 11, which uses `reset.sh 03`. Process ids, OIDs and timings will differ on your machine. Everything else should match.

Files in this directory:

| File | What |
|---|---|
| [`scratch_setup.sql`](scratch_setup.sql) | The scratch objects used by the theory and the exercises. |
| [`setup_bazaar.sql`](setup_bazaar.sql) | Project milestone, part 1: create the database, schema and search_path. |
| [`psqlrc`](psqlrc) | Project milestone, part 2: a recommended `~/.psqlrc`. |
| [`db_report.sql`](db_report.sql) | Project milestone, part 3: the database report script. |
| [`check_ready.sql`](check_ready.sql) | Exercise 10. |

---

<!-- toc:start -->
**Contents**

- [Exercise 1 ★: Where am I?](#exercise-1--where-am-i)
- [Exercise 2 ★: Pattern hunt](#exercise-2--pattern-hunt)
- [Exercise 3 ★: Two CSV exports and a trap](#exercise-3--two-csv-exports-and-a-trap)
- [Exercise 4 ★★: `search_path` detective](#exercise-4--search_path-detective)
- [Exercise 5 ★★: The widest catalogs](#exercise-5--the-widest-catalogs)
- [Exercise 6 ★★: Twelve tables, one query](#exercise-6--twelve-tables-one-query)
- [Exercise 7 ★★: Reverse-engineer `\du`](#exercise-7--reverse-engineer-du)
- [Exercise 8 ★★: Predict the transaction](#exercise-8--predict-the-transaction)
- [Exercise 9 ★★: Settings to a file, from the shell](#exercise-9--settings-to-a-file-from-the-shell)
- [Exercise 10 ★★★: An environment check with an exit code](#exercise-10--an-environment-check-with-an-exit-code)
- [Exercise 11 ★★★: Build your own `\d`](#exercise-11--build-your-own-d)
- [Exercise 12 ★★★: A census of the whole cluster](#exercise-12--a-census-of-the-whole-cluster)

<!-- toc:end -->

## Exercise 1 ★: Where am I?

```sql
SELECT current_database()                     AS db,
       current_user                           AS who,
       pg_backend_pid()                       AS pid,
       current_setting('server_version_num')  AS version_num,
       current_setting('search_path')         AS search_path,
       current_setting('is_superuser')        AS superuser
\gx
```

```text
-[ RECORD 1 ]---------------
db          | bazaar
who         | postgres
pid         | 574
version_num | 170011
search_path | bazaar, public
superuser   | on
```

**Reasoning.** `\gx` runs the buffer like `;` but forces expanded output for this one result. `\x` would change the setting for the rest of the session. `current_setting()` reads any configuration parameter as text, so one function covers three of the six fields. `is_superuser` is a read-only parameter the server reports for exactly this kind of question.

**Alternatives.** `SHOW search_path;` works too, but `SHOW` is a statement of its own and can't be combined into one row. In psql you could also `\echo :DBNAME :USER :SERVER_VERSION_NUM`, using psql's special variables, which are set from the connection with no query at all. Note that `search_path` is the *setting* (`bazaar, public`). `current_schemas(false)` would show the *effective* list after skipping missing schemas.

---

## Exercise 2 ★: Pattern hunt

```sql
\dfS *uuid*
\dvS pg_stat_user_*
\df+ pg_catalog.pg_size_pretty
```

1. `\dfS *uuid*` returns 18 rows. The generator is `gen_random_uuid()`: no arguments, returns `uuid`. (Most of the others are the type's plumbing: comparison operators' functions like `uuid_eq`, input/output `uuid_in`/`uuid_out`, hashing. PostgreSQL 17 also has `uuid_extract_timestamp` and `uuid_extract_version`.)
2. `\dvS pg_stat_user_*`:

   ```text
      Schema   |          Name          | Type |  Owner   
   ------------+------------------------+------+----------
    pg_catalog | pg_stat_user_functions | view | postgres
    pg_catalog | pg_stat_user_indexes   | view | postgres
    pg_catalog | pg_stat_user_tables    | view | postgres
   ```

3. `\df+` shows `Language = internal` and `Internal name = pg_size_pretty_numeric`. `\sf pg_size_pretty(numeric)` shows the same thing as a definition:

   ```text
   CREATE OR REPLACE FUNCTION pg_catalog.pg_size_pretty(numeric)
    RETURNS text
    LANGUAGE internal
    IMMUTABLE PARALLEL SAFE STRICT
   AS $function$pg_size_pretty_numeric$function$
   ```

**Reasoning.** `S` includes system objects. Without it, `\df` hides everything in `pg_catalog`. An unqualified pattern like `*uuid*` searches the schemas visible through `search_path`, and `pg_catalog` is always visible, so it works. `pg_catalog.pg_size*` is the explicit alternative and doesn't need `S`. "Internal" functions are written in C and compiled into the server. The "internal name" is the C function's name. `\sf` needs the argument types because `pg_size_pretty` is overloaded: `\sf pg_size_pretty` alone would be ambiguous.

---

## Exercise 3 ★: Two CSV exports and a trap

```sql
\pset null '∅'

-- 1. \g with one-off options
SELECT task, owner, due FROM launch_checklist WHERE NOT done ORDER BY id
\g (format=csv) /tmp/open_g.csv

-- 2. \copy
\copy (SELECT task, owner, due FROM launch_checklist WHERE NOT done ORDER BY id) TO '/tmp/open_copy.csv' WITH (FORMAT csv, HEADER)
```

```text
==> /tmp/open_g.csv <==
task,owner,due
Create bazaar database,Ben,2026-01-07
Write .psqlrc,Ben,∅
Design core schema,∅,2026-01-20

==> /tmp/open_copy.csv <==
task,owner,due
Create bazaar database,Ben,2026-01-07
Write .psqlrc,Ben,
Design core schema,,2026-01-20
```

The fix overrides the null display for this one query only:

```sql
SELECT task, owner, due FROM launch_checklist WHERE NOT done ORDER BY id
\g (format=csv null='') /tmp/open_g.csv
\! diff /tmp/open_g.csv /tmp/open_copy.csv && echo identical
```

```text
identical
```

**Reasoning.** `\g` output goes through psql's *display* machinery, so every `\pset` option applies, including `null`. Psql's CSV format prints a header by default, which is why method 1 needs no `HEADER` option. `\copy` uses the server's `COPY` protocol. The server produces the CSV text and psql only writes the bytes, so display settings don't apply, and CSV's convention of NULL as an unquoted empty field holds.

**Which to prefer?** For data exchange, `\copy`: it's faster for large results (no client-side formatting), and its output doesn't depend on anyone's `.psqlrc`. `\g (format=csv)` is handy when you're already in psql and want the result of the query you just ran. Remember that `\copy` must be on one line.

---

## Exercise 4 ★★: `search_path` detective

```text
(1) 2        -- staging.launch_checklist comes first
(2) 5        -- bazaar.launch_checklist comes first
(3) {staging}
(4) staging
(5) {postgres,staging}
(6) 0        -- yes: resolves to staging.where_did_i_go (empty)
(7) a NEW table in schema postgres
```

```sql
SELECT relnamespace::regnamespace, relname FROM pg_class WHERE relname = 'where_did_i_go';
```

```text
 relnamespace |    relname     
--------------+----------------
 staging      | where_did_i_go
 postgres     | where_did_i_go
(2 rows)
```

**Reasoning.**

- (1)/(2): first match wins, and the two schemas each contain a `launch_checklist`.
- (3): `nosuch` doesn't exist. `"$user"` expands to `postgres`, the current role, and there's no schema with that name yet. Both are skipped silently.
- (4): an unqualified `CREATE` goes to the first *existing* schema of the path, `staging`.
- (5): `search_path` is a list of *names*, re-resolved for every statement. As soon as schema `postgres` exists, `"$user"` starts to match, and it's now first.
- (6): name lookup for `where_did_i_go` checks `postgres` (not there), then `staging` (found), so the query works.
- (7): creation goes to the first existing schema, which is now `postgres`, so there's no "already exists" error. You get a second table that shadows the first. This is exactly how "the table I just created has no data" mysteries happen. A schema named after a user appears, and suddenly unqualified names resolve differently.

**Lesson.** The default `"$user", public` is convenient on a laptop, but surprising when schemas come and go. That's why Bazaar pins `search_path = bazaar, public` at the database level, and why production code often qualifies names.

---

## Exercise 5 ★★: The widest catalogs

```sql
SELECT c.relname,
       count(*) AS columns
FROM pg_class AS c
JOIN pg_attribute AS a
  ON a.attrelid = c.oid
WHERE c.relnamespace = 'pg_catalog'::regnamespace
  AND c.relkind = 'r'
  AND a.attnum > 0
  AND NOT a.attisdropped
GROUP BY c.relname
ORDER BY columns DESC, c.relname
LIMIT 5;
```

```text
   relname    | columns 
--------------+---------
 pg_class     |      33
 pg_type      |      32
 pg_statistic |      31
 pg_proc      |      30
 pg_attribute |      26
(5 rows)
```

**Reasoning.** `pg_class` has one row per relation, and `relkind = 'r'` keeps ordinary tables, excluding views (`v`), indexes and so on. `'pg_catalog'::regnamespace` turns the schema name into its OID, so no join to `pg_namespace` is needed. Each `pg_attribute` row is a column, and `attnum > 0` drops the six system columns (`ctid`, `xmin`, ...) every table has. `NOT attisdropped` matters for user tables: a dropped column stays in `pg_attribute` as a placeholder. The secondary sort on `relname` makes ties deterministic.

**Alternative.** `information_schema.columns` with `table_schema = 'pg_catalog'` gives the same counts (it hides system and dropped columns for you), but it also includes views unless you join `information_schema.tables` on `table_type = 'BASE TABLE'`, and it's slower. For catalog-about-catalog questions, `pg_catalog` is the natural tool.

---

## Exercise 6 ★★: Twelve tables, one query

```sql
CREATE TABLE launch_logbook (x int);

-- 1. create
SELECT format('CREATE TABLE launch_log_%s (LIKE launch_checklist INCLUDING ALL)',
              to_char(m, 'YYYY_MM'))
FROM generate_series(date '2026-01-01', date '2026-12-01', interval '1 month') AS m
\gexec
```

```text
CREATE TABLE
... (12 times)
```

```sql
\dt launch_log*
```

```text
               List of relations
 Schema |        Name        | Type  |  Owner   
--------+--------------------+-------+----------
 bazaar | launch_log_2026_01 | table | postgres
 ...
 bazaar | launch_log_2026_12 | table | postgres
 bazaar | launch_logbook     | table | postgres
(13 rows)
```

```sql
-- 2. drop exactly the twelve
SELECT format('DROP TABLE %I.%I', n.nspname, c.relname)
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE c.relkind = 'r'
  AND n.nspname = 'bazaar'
  AND c.relname ~ '^launch_log_\d{4}_\d{2}$'
ORDER BY c.relname
\gexec
```

Afterwards `\dt launch_log*` shows only `launch_logbook`.

**Bonus.** `relname LIKE 'launch_log_%'` returns **13**: in `LIKE`, `_` matches any single character, so `launch_logbook` (`launch_log` + `b` + `ook`) matches too. The escaped pattern `'launch\_log\_%'` returns 12. Had you dropped with the unescaped `LIKE`, you'd have dropped the logbook. That's the most common way catalog-driven scripts go wrong.

**Reasoning.** `generate_series` over dates yields `timestamp` values one month apart, and `to_char` formats them. `LIKE ... INCLUDING ALL` copies columns, defaults, constraints, identity and indexes. `%I` quotes identifiers only when necessary, so the generated SQL stays readable, and it's safe if a name ever needs quoting. Including the schema in the drop statement (`%I.%I`) makes the generated statements independent of `search_path`.

**Alternative.** A single `DROP TABLE a, b, c, ...` built with `string_agg(format('%I.%I', ...), ', ')` drops all twelve in one statement, and therefore atomically: either all go or none do. With one statement per row, a failure in the middle leaves some dropped and some not. Module 14 does this kind of per-month table creation for real, with partitioning.

---

## Exercise 7 ★★: Reverse-engineer `\du`

`psql -E` shows that `\du` is a simple query on `pg_roles`:

```text
SELECT r.rolname, r.rolsuper, r.rolinherit,
  r.rolcreaterole, r.rolcreatedb, r.rolcanlogin,
  r.rolconnlimit, r.rolvaliduntil
, r.rolreplication
, r.rolbypassrls
FROM pg_catalog.pg_roles r
WHERE r.rolname !~ '^pg_'
ORDER BY 1;
```

Extended with database ownership:

```sql
SELECT r.rolname,
       r.rolsuper    AS superuser,
       r.rolcanlogin AS can_login,
       count(d.oid)  AS databases_owned,
       string_agg(d.datname, ', ' ORDER BY d.datname) AS databases
FROM pg_roles AS r
LEFT JOIN pg_database AS d
       ON d.datdba = r.oid
WHERE r.rolname !~ '^pg_'
GROUP BY r.rolname, r.rolsuper, r.rolcanlogin
ORDER BY r.rolname;
```

```text
  rolname   | superuser | can_login | databases_owned |               databases                
------------+-----------+-----------+-----------------+----------------------------------------
 bazaar_dev | f         | t         |               1 | bazaar_sandbox
 postgres   | t         | t         |               4 | bazaar, postgres, template0, template1
(2 rows)
```

**Reasoning.** `pg_database.datdba` is the owner's OID. A `LEFT JOIN` keeps roles that own nothing (they'd get `0` and NULL). `count(d.oid)` counts only matched rows, while `count(*)` would wrongly say 1 for them. The `!~ '^pg_'` filter (regular expression "doesn't start with `pg_`") hides the predefined roles, just as `\du` does. Aggregates (`count`, `string_agg`) and `GROUP BY` are module 05 topics. Here they're used in their simplest form.

`SET ROLE bazaar_dev` made the database owned by `bazaar_dev` because it was created *while acting as* that role. Ownership follows `current_user`, not the login user.

---

## Exercise 8 ★★: Predict the transaction

| Run | Rows kept | Exit | Last line printed for `COMMIT` |
|---|---|---|---|
| A (none) | 0 | 0 | `ROLLBACK` |
| B `ON_ERROR_ROLLBACK=on` | 2 | 0 | `COMMIT` |
| C `ON_ERROR_ROLLBACK=interactive` | 0 | 0 | `ROLLBACK` |
| D `ON_ERROR_STOP=1` | 0 | 3 | (nothing: psql stopped before it) |

Actual output of run A:

```text
BEGIN
INSERT 0 1
psql:/tmp/tx.sql:3: ERROR:  null value in column "task" of relation "launch_checklist" violates not-null constraint
DETAIL:  Failing row contains (7, null, null, f, null).
psql:/tmp/tx.sql:4: ERROR:  current transaction is aborted, commands ignored until end of transaction block
ROLLBACK
```

Run B:

```text
BEGIN
INSERT 0 1
psql:/tmp/tx.sql:3: ERROR:  null value in column "task" of relation "launch_checklist" violates not-null constraint
DETAIL:  Failing row contains (9, null, null, f, null).
INSERT 0 1
COMMIT
```

**Reasoning.**

- **A:** the failed insert aborts the transaction. The third insert is refused. `COMMIT` on an aborted transaction can't commit, so the server performs a rollback and reports `ROLLBACK`. psql carries on and exits 0, because errors don't affect the exit status without `ON_ERROR_STOP`. A script that "succeeded" and did nothing.
- **B:** psql wraps each statement in a savepoint. The failing insert is rolled back to its savepoint, the transaction stays healthy, and the other two inserts commit. Convenient, and dangerous in scripts: the script ran to completion with a step missing.
- **C:** `interactive` only activates when psql reads from a terminal. `-f` isn't interactive, so C behaves exactly like A. This is the reason to choose `interactive`.
- **D:** psql stops at the error and exits with 3. The connection closes with the transaction still open, and the server rolls back any transaction whose connection goes away.

Notice the ids in the `DETAIL` lines (7, 9): failed inserts still consume identity values, and rolled-back transactions don't give them back. Identity and sequence values are never "gap-free". Module 02 returns to this.

**The production recipe** is D plus `-1`: one transaction, stop at the first error, non-zero exit status.

---

## Exercise 9 ★★: Settings to a file, from the shell

```bash
mkdir -p workspace/01
docker exec course-pg psql -X --csv -U postgres \
  -c "SELECT name, setting FROM pg_settings WHERE source = 'command line' ORDER BY name" \
  > workspace/01/cmdline_settings.csv
cat workspace/01/cmdline_settings.csv
```

```text
name,setting
log_min_duration_statement,1000
pg_stat_statements.track,all
shared_preload_libraries,"pg_stat_statements,auto_explain"
track_io_timing,on
```

With `-XAt -F,` instead:

```text
log_min_duration_statement,1000
pg_stat_statements.track,all
shared_preload_libraries,pg_stat_statements,auto_explain
track_io_timing,on
```

**Reasoning.** `--csv` follows the CSV rules: a value containing the separator is wrapped in double quotes, so a CSV reader sees exactly two fields. `-A -F,` just glues the values together with commas, with no quoting, so the `shared_preload_libraries` line reads as three fields. `-A -F` is fine for values you *know* can't contain the separator (numbers, identifiers). Use `--csv` for anything else, or a separator that can't occur, such as `-F $'\t'` for simple values or `-z` (zero byte).

`-X` makes sure no `.psqlrc` adds `Time:` lines or changes the null display. No `-it` for `docker exec`: you aren't typing into the container, only reading its output.

---

## Exercise 10 ★★★: An environment check with an exit code

Reference: [`check_ready.sql`](check_ready.sql).

```sql
\set ON_ERROR_STOP on
\set QUIET on

SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'bazaar') AS has_schema,
       EXISTS (SELECT 1
                 FROM pg_db_role_setting s
                 JOIN pg_database d ON d.oid = s.setdatabase
                WHERE d.datname = current_database()
                  AND s.setrole = 0
                  AND EXISTS (SELECT 1
                                FROM unnest(s.setconfig) AS cfg
                               WHERE cfg LIKE 'search\_path=bazaar%'))
           AS has_search_path
\gset

\set ok true
\if :has_schema
\else
    \warn 'missing: schema bazaar'
    \set ok false
\endif
\if :has_search_path
\else
    \warn 'missing: ALTER DATABASE ... SET search_path = bazaar, public'
    \set ok false
\endif

\if :ok
    \echo READY
\else
    DO $$ BEGIN RAISE EXCEPTION 'bazaar environment is not ready'; END $$;
\endif
```

```bash
docker exec -i course-pg psql -X -U postgres -d bazaar < check_ready.sql; echo "exit=$?"
```

```text
READY
exit=0
```

After `ALTER DATABASE bazaar RESET search_path;`:

```text
missing: ALTER DATABASE ... SET search_path = bazaar, public
ERROR:  bazaar environment is not ready
CONTEXT:  PL/pgSQL function inline_code_block line 1 at RAISE
exit=3
```

Against the `postgres` database:

```text
missing: schema bazaar
missing: ALTER DATABASE ... SET search_path = bazaar, public
ERROR:  bazaar environment is not ready
CONTEXT:  PL/pgSQL function inline_code_block line 1 at RAISE
exit=3
```

(Restore with `ALTER DATABASE bazaar SET search_path = bazaar, public;`.)

**Reasoning.**

- `pg_db_role_setting` is where `ALTER DATABASE ... SET` and `ALTER ROLE ... SET` store their values. `setrole = 0` means "for all roles", which is what a database-level setting is. `setconfig` is a text array of `name=value` strings, and `unnest()` turns it into rows we can test with `LIKE`. (The backslash before `_` makes it literal. Exercise 6's lesson.) Checking `current_setting('search_path')` instead would be fooled by a `SET` in the session, or by a role-level setting.
- `\if` can't combine conditions (`\if :a and :b` isn't supported), so the script accumulates an `ok` flag. The empty `\if ... \else` branch is the idiom for "if not".
- `\warn` writes to stderr, so a CI log shows the problems even if stdout is captured elsewhere.
- psql has no "exit with code N" command. `\q` always exits 0. A deliberate SQL error under `ON_ERROR_STOP` gives exit status 3 reliably. `DO` runs an anonymous PL/pgSQL block (module 09), and `RAISE EXCEPTION` produces an error with a readable message.

**Alternative.** `SELECT 1/0;` also produces exit status 3, but the message (`division by zero`) confuses whoever reads the CI log. A shell wrapper could also check the output (`grep -q READY`), but then the script's exit status lies when run on its own.

---

## Exercise 11 ★★★: Build your own `\d`

```sql
SELECT a.attname                               AS "Column",
       format_type(a.atttypid, a.atttypmod)    AS "Type",
       CASE WHEN a.attnotnull THEN 'not null' ELSE '' END AS "Nullable",
       CASE a.attidentity
           WHEN 'a' THEN 'generated always as identity'
           WHEN 'd' THEN 'generated by default as identity'
           ELSE pg_get_expr(d.adbin, d.adrelid)
       END                                     AS "Default"
FROM pg_attribute AS a
LEFT JOIN pg_attrdef AS d
       ON d.adrelid = a.attrelid
      AND d.adnum   = a.attnum
WHERE a.attrelid = 'bazaar.products'::regclass
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY a.attnum;
```

```text
   Column    |           Type           | Nullable |           Default            
-------------+--------------------------+----------+------------------------------
 id          | bigint                   | not null | generated always as identity
 seller_id   | bigint                   | not null | 
 category_id | integer                  | not null | 
 sku         | text                     | not null | 
 title       | text                     | not null | 
 description | text                     |          | 
 price       | numeric(12,2)            | not null | 
 status      | text                     | not null | 'draft'::text
 created_at  | timestamp with time zone | not null | now()
 updated_at  | timestamp with time zone | not null | now()
(10 rows)
```

**Reasoning.**

- `format_type(type_oid, typmod)` renders a type the way SQL writes it. The *typmod* is the type modifier (`(12,2)` for `numeric`, the length for `varchar(n)`). `atttypid::regtype` alone would print just `numeric`.
- Defaults are stored separately, in `pg_attrdef`, as a parsed expression tree. `pg_get_expr` turns it back into text, which is why the default shows as `'draft'::text` (with the explicit cast the parser added), not as you typed it.
- Identity columns don't have a `pg_attrdef` row. The identity is a property of the column (`attidentity`), backed by a sequence. Without the `CASE`, the `id` default would be empty.
- `\d` also prints a `Collation` column, filled only when a column's collation differs from its type's default. Add `CASE WHEN a.attcollation <> t.typcollation THEN ...` with a join to `pg_type` to complete it, which is exactly what psql's hidden query does.

**Alternative.** `information_schema.columns` gives `data_type = 'numeric'` plus separate `numeric_precision`/`numeric_scale` columns, and `column_default` as text. It works, but you'd have to reassemble the type string, and it uses standard names (`timestamp with time zone`, `character varying`) that don't always match `\d`.

---

## Exercise 12 ★★★: A census of the whole cluster

**Why no single SQL query?** A connection sees exactly one database's catalogs. `pg_class` in `bazaar` only describes `bazaar`'s relations, and cross-database references are rejected. Only cluster-wide catalogs like `pg_database` and `pg_roles` are visible from everywhere. So you need one connection per database.

```bash
Q="SELECT count(*)
   FROM pg_class c
   JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE c.relkind IN ('r', 'p')
     AND n.nspname NOT IN ('pg_catalog', 'information_schema')
     AND n.nspname NOT LIKE 'pg\_toast%'"

docker exec course-pg psql -XAt -U postgres -d postgres \
     -c "SELECT datname FROM pg_database WHERE datallowconn ORDER BY datname" |
while read -r db; do
    n=$(docker exec course-pg psql -XAt -U postgres -d "$db" -c "$Q" < /dev/null)
    printf '%-16s %s\n' "$db" "$n"
done
```

```text
bazaar           1
bazaar_sandbox   0
postgres         0
template1        0
```

**The real lesson.** Write the inner call as `docker exec -i ...` without `< /dev/null`, and the output is:

```text
bazaar           1
```

`docker exec -i` forwards its standard input into the container. Inside the loop, standard input is the pipe carrying the list of databases, so the first inner `docker exec` swallows the remaining names (psql with `-c` never reads them, but Docker has already consumed them), and `read` finds nothing more. The same trap exists with `ssh` and any other command that reads stdin. The fixes: drop `-i` when you don't feed the command any input, or redirect its stdin from `/dev/null`, as above. (A native `psql -c` doesn't read stdin, so the plain-psql version of this loop works either way.)

**Other details.** `WHERE datallowconn` skips `template0`, which refuses connections. Trying to connect would make psql exit with status 2. `-XAt` gives bare values with no headers, footers or `.psqlrc` effects. `printf` pads the names into columns.

**Alternative.** `pg_database_size(datname)` *can* be computed for every database from a single connection, because it reads file sizes rather than catalogs. So "how big is each database" is one query, but "what's inside each database" needs a connection to each. Monitoring tools face the same constraint, which is why they connect to every database they report on.

Clean up: `docker exec course-pg psql -X -U postgres -c 'DROP DATABASE bazaar_sandbox'`.
