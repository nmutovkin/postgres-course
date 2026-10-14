# Module 01: Exercises

<!-- nav:start -->
> [Theory](README.md) · **Exercises** · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 00 Setup](../00-setup/README.md) · [Course home](../../README.md) · [02 Data Types and Tables →](../02-types-and-ddl/README.md)
<!-- nav:end -->

**Starting state.** Unless an exercise says otherwise, start from:

```bash
./project/bin/reset.sh 01
docker exec -i course-pg psql -X -U postgres -d bazaar < modules/01-psql/solutions/scratch_setup.sql
```

That gives you an empty `bazaar` schema plus the scratch objects from the theory: the table `launch_checklist` (5 rows), the view `open_tasks` and the function `tasks_left(text)`. If you've done this module's project milestone, your own setup works just as well. Re-run `scratch_setup.sql` whenever an exercise has changed the data.

Exercise 11 uses the Bazaar tables from module 02 (`./project/bin/reset.sh 03`). Do it last, or reset back to 01 afterwards.

Solutions: [solutions/README.md](solutions/README.md).

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

Write **one** query that returns, as a single row displayed vertically: the current database, the current user, the process id of your backend, the server version as a number, your effective `search_path` setting, and whether you're a superuser. Display it vertically **without** turning on `\x` for the rest of the session.

**Self-check:** one record with six fields. The version field starts with `17`, `search_path` is `bazaar, public`, and the superuser field is `on`. Running a normal `SELECT 1;` afterwards prints a normal table.

<details><summary>Hint</summary>

`current_setting('name')` reads any setting as text, including `server_version_num`, `search_path` and `is_superuser`. One meta-command ends a query and shows it expanded, just for that query.

</details>

---

## Exercise 2 ★: Pattern hunt

Using **only `\d`-family meta-commands with patterns** (no SQL), answer:

1. How many functions in `pg_catalog` have `uuid` anywhere in their name? Which one creates a random UUID?
2. Which views in `pg_catalog` start with `pg_stat_user_`?
3. In which language is `pg_size_pretty(numeric)` implemented, and what is its "internal name"?

**Self-check:** (1) 18 rows, and the generator takes no arguments and returns `uuid`. (2) 3 views. (3) The language isn't `sql` or `plpgsql`.

<details><summary>Hint</summary>

System objects need the `S` modifier (`\dfS`, `\dvS`) or an explicit `pg_catalog.` schema in the pattern. `*` matches any sequence of characters. For (3), either `\df+` or `\sf` with the full signature in parentheses.

</details>

---

## Exercise 3 ★: Two CSV exports and a trap

With `\pset null '∅'` set (as in a typical `.psqlrc`), export the open tasks (`task`, `owner`, `due`; ordered by `id`) to a CSV file with a header, in two ways:

1. with `\g` and one-off format options,
2. with `\copy`.

Compare the two files. Then fix the first method so that both files are identical, **without** changing your session's `\pset null` setting.

**Self-check:** each file has 4 lines (header plus 3 rows). Before the fix, exactly one file contains the character `∅`, twice. After the fix, `diff` on the two files prints nothing.

<details><summary>Hint</summary>

`\g (option=value ...) file` accepts *any* `\pset` option for that one query, not only `format`.

</details>

---

## Exercise 4 ★★: `search_path` detective

Run this setup:

```sql
CREATE SCHEMA staging;
CREATE TABLE staging.launch_checklist (task text);
INSERT INTO staging.launch_checklist VALUES ('staging row 1'), ('staging row 2');
```

**Predict first, then check**: what does each numbered query return, and where does each `CREATE TABLE` put its table?

```sql
SET search_path = staging, bazaar;
SELECT count(*) FROM launch_checklist;                 -- (1)

SET search_path = bazaar, staging;
SELECT count(*) FROM launch_checklist;                 -- (2)

SET search_path = nosuch, "$user", staging;
SELECT current_schemas(false);                         -- (3)
CREATE TABLE where_did_i_go (x int);                   -- (4) which schema?

CREATE SCHEMA postgres;
SELECT current_schemas(false);                         -- (5)
SELECT count(*) FROM where_did_i_go;                   -- (6) does this still work?
CREATE TABLE where_did_i_go (x int);                   -- (7) error, or a new table? where?
```

Clean up afterwards: `RESET search_path; DROP SCHEMA staging CASCADE; DROP SCHEMA postgres CASCADE;`

**Self-check:** (1) and (2) differ. After step (7), this query returns **two** rows:

```sql
SELECT relnamespace::regnamespace, relname FROM pg_class WHERE relname = 'where_did_i_go';
```

<details><summary>Hint</summary>

You're connected as the role `postgres`. What does `"$user"` expand to, and is there a schema with that name at step (3)? At step (5)? `search_path` is re-evaluated for each statement.

</details>

---

## Exercise 5 ★★: The widest catalogs

Which five **tables** (not views) in `pg_catalog` have the most columns? Count only real user-visible columns: no system columns, nothing dropped. Write the query against `pg_class`, `pg_namespace` (or a `reg*` cast) and `pg_attribute`.

**Self-check:** 5 rows. The first is `pg_class` with 33 columns. `pg_attribute` itself is fifth with 26.

<details><summary>Hint</summary>

`relkind = 'r'` means ordinary table. `relnamespace = 'pg_catalog'::regnamespace` saves a join. In `pg_attribute`, system columns have `attnum < 0`, and dropped columns have `attisdropped = true`. Break ties by name, so the output is deterministic.

</details>

---

## Exercise 6 ★★: Twelve tables, one query

Bazaar's analytics team wants one empty logging table per month of 2026, named `launch_log_2026_01` ... `launch_log_2026_12`, each with the same columns as `launch_checklist`. There's also an existing table that must **not** be touched. Create it first:

```sql
CREATE TABLE launch_logbook (x int);
```

1. Create the twelve tables with a **single** query plus `\gexec`.
2. Drop exactly those twelve again with a single query plus `\gexec`, making sure `launch_logbook` survives.

**Self-check:** after step 1, `\dt launch_log*` lists 13 tables. After step 2, it lists only `launch_logbook`. Bonus question: how many rows does `SELECT count(*) FROM pg_class WHERE relname LIKE 'launch_log_%'` return after step 1, and why isn't it 12?

<details><summary>Hint</summary>

`generate_series` works with dates and an `interval '1 month'` step, and `to_char(d, 'YYYY_MM')` formats them. `CREATE TABLE x (LIKE y)` copies column definitions. For the drop, match names precisely. In `LIKE`, `_` is a wildcard. A regular expression (`~ '^launch_log_\d{4}_\d{2}$'`) is stricter.

</details>

---

## Exercise 7 ★★: Reverse-engineer `\du`

Set up a developer role that owns a database:

```sql
CREATE ROLE bazaar_dev LOGIN CREATEDB PASSWORD 'dev';
SET ROLE bazaar_dev;
CREATE DATABASE bazaar_sandbox;
RESET ROLE;
```

Use `psql -E` (or `\set ECHO_HIDDEN on`) to see which catalog query `\du` runs. Then write your own version that lists every non-system role with: name, superuser yes/no, can log in yes/no, **how many databases it owns**, and their names as a comma-separated list.

Clean up: `DROP DATABASE bazaar_sandbox; DROP ROLE bazaar_dev;` (from the `bazaar` or `postgres` database).

**Self-check:** 2 rows. `bazaar_dev` owns 1 database (`bazaar_sandbox`). `postgres` owns 4: `bazaar, postgres, template0, template1`.

<details><summary>Hint</summary>

`\du` reads `pg_roles`. Database ownership is `pg_database.datdba`, an OID that matches `pg_roles.oid`. A role that owns nothing must still appear: which join keeps it? `string_agg(x, ', ' ORDER BY x)` builds the list.

</details>

---

## Exercise 8 ★★: Predict the transaction

Save this as a file:

```sql
BEGIN;
INSERT INTO launch_checklist (task) VALUES ('Order domain name');
INSERT INTO launch_checklist (task) VALUES (NULL);
INSERT INTO launch_checklist (task) VALUES ('Buy TLS certificate');
COMMIT;
```

It will be run four times with `psql -X -f`, re-running `scratch_setup.sql` between runs. The extra options are:

| Run | Extra options |
|---|---|
| A | (none) |
| B | `-v ON_ERROR_ROLLBACK=on` |
| C | `-v ON_ERROR_ROLLBACK=interactive` |
| D | `-v ON_ERROR_STOP=1` |

For each run, **predict** (a) how many of the two valid tasks end up in the table, (b) psql's exit status, and (c) what psql prints for the final `COMMIT` line, if anything. Then run them and check.

**Self-check:** across the four runs, exactly one run keeps any rows, and exactly one run exits with a non-zero status. They're not the same run.

<details><summary>Hint</summary>

After an error inside `BEGIN ... COMMIT`, the transaction is aborted, and PostgreSQL answers `COMMIT` on an aborted transaction with `ROLLBACK`. `ON_ERROR_ROLLBACK` works with savepoints, but `interactive` only applies when psql reads from a terminal. A file isn't one. With `ON_ERROR_STOP`, psql exits at the error, so what happens to a transaction when its connection closes?

</details>

---

## Exercise 9 ★★: Settings to a file, from the shell

Write **one shell command** (no interactive psql) that writes `workspace/01/cmdline_settings.csv`: a CSV file with a header, listing `name` and `setting` for every server setting whose source is the command line, sorted by name. Then try the same with `-A -F,` instead of `--csv`, and explain why that output is broken.

**Self-check:** the CSV file has 5 lines (header plus 4 settings). One line contains double quotes. In the `-A -F,` version, that same line appears to have three fields.

<details><summary>Hint</summary>

`pg_settings.source = 'command line'`. Remember `-X`. For the container, `docker exec course-pg psql ...` (no `-it` needed when you only read output). Which setting's value contains a comma?

</details>

---

## Exercise 10 ★★★: An environment check with an exit code

Write a psql script `workspace/01/check_ready.sql` for a CI pipeline. It checks that, in the database it's connected to:

1. a schema named `bazaar` exists, and
2. a **database-level** setting makes `search_path` start with `bazaar`.

If both hold, it prints `READY` and exits with status 0. Otherwise it prints (to stderr) one line per missing item, and exits with a **non-zero** status. The checks must look at stored configuration, not at the current session's `search_path` (a `SET` in the session must not fool it).

**Self-check:**

- on the starting state: prints `READY`, exit status 0;
- after `ALTER DATABASE bazaar RESET search_path;`: prints one `missing:` line, exit status 3 (restore the setting afterwards!);
- run against the `postgres` database: two `missing:` lines, exit status 3.

<details><summary>Hint</summary>

Database-level settings live in `pg_db_role_setting` (`setdatabase`, `setrole = 0` for "all roles", `setconfig` as a text array like `{"search_path=bazaar, public"}`). `\gset` the two booleans, combine them with `\if`, and use `\warn` for stderr. psql has no `\exit 3`, but with `ON_ERROR_STOP` any SQL error makes psql exit with 3. Can you raise one on purpose?

</details>

---

## Exercise 11 ★★★: Build your own `\d`

**State:** `./project/bin/reset.sh 03` (the module 02 core tables; you'll build them yourself in module 02).

Write a catalog query that reproduces the **column section** of `\d bazaar.products`: column name, type exactly as `\d` prints it (`numeric(12,2)`, `timestamp with time zone`), `not null` or empty, and the default, including `generated always as identity` for identity columns. Don't use `information_schema`.

**Self-check:** 10 rows that match `\d products` line by line, for example `price | numeric(12,2) | not null |` and `status | text | not null | 'draft'::text`. When you're done, `./project/bin/reset.sh 01` and re-run `scratch_setup.sql`.

<details><summary>Hint</summary>

Run `\d products` under `psql -E` and look at the second hidden query. You need `format_type(atttypid, atttypmod)` (the typmod carries `(12,2)`), `pg_attrdef` for defaults (match `adrelid` and `adnum`), `pg_get_expr(adbin, adrelid)` to turn a stored default back into text, and `pg_attribute.attidentity` (`'a'` = always, `'d'` = by default). Identity columns have no `pg_attrdef` row.

</details>

---

## Exercise 12 ★★★: A census of the whole cluster

Bazaar ops wants a list of **every database you can connect to** in the cluster, with the number of user tables (kinds `r` and `p`, excluding system schemas) in each. A single SQL query can't do this. Explain why, then write a short **bash** loop that does it with psql, using the course container. Recreate `bazaar_sandbox` from exercise 7 first, so there's one more database to find:

```bash
docker exec course-pg psql -X -U postgres -c 'CREATE DATABASE bazaar_sandbox'
```

**Self-check:** four lines, `bazaar 1`, `bazaar_sandbox 0`, `postgres 0`, `template1 0`. `template0` must not appear, and your loop must not error on it. If you see only **one** line, you've found this exercise's real lesson: work out why. Drop `bazaar_sandbox` when you're done.

<details><summary>Hint</summary>

First psql call: list the databases with `-XAt` and `WHERE datallowconn`. Then `while read -r db; do ...; done`, with one psql call per database. If the loop stops after the first database, look at the pitfall at the end of theory section 14: who is reading the loop's standard input?

</details>
