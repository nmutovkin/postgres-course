# Module 01: Project milestone — set up the Bazaar environment

<!-- nav:start -->
> [Theory](README.md) · [Exercises](exercises.md) · **Project** · [Solutions](solutions/README.md)  
> [← 00 Setup](../00-setup/README.md) · [Course home](../../README.md) · [02 Data Types and Tables →](../02-types-and-ddl/README.md)
<!-- nav:end -->

**Goal.** Until now, `reset.sh` has created the Bazaar database for you. In real life, somebody writes that setup, and it has to be repeatable: a new developer, a CI job and a fresh staging server must all end up with exactly the same structure. In this milestone you take over that job. You create the `bazaar` database and schema yourself, with a script that's safe to run twice. You configure psql for comfortable daily work. And you write a small report script the team can run against any environment to see what's there.

Keep all your files in `workspace/01/` (and your `.psqlrc` in your home directory).

<!-- toc:start -->
**Contents**

- [Requirements](#requirements)
  - [1. `workspace/01/setup_bazaar.sql`: database, schema, search path](#1-workspace01setup_bazaarsql-database-schema-search-path)
  - [2. `~/.psqlrc`: a comfortable psql](#2-psqlrc-a-comfortable-psql)
  - [3. `workspace/01/db_report.sql`: the database report](#3-workspace01db_reportsql-the-database-report)
- [Acceptance checks](#acceptance-checks)
- [Reference solution](#reference-solution)

<!-- toc:end -->

## Requirements

### 1. `workspace/01/setup_bazaar.sql`: database, schema, search path

A psql script, run while connected to the **`postgres`** database, that:

- creates the database **`bazaar`** if it doesn't exist yet (and doesn't fail if it does);
- connects to it and creates the schema **`bazaar`** if it doesn't exist;
- sets the **database-level** default `search_path` to `bazaar, public`;
- stops at the first error (`ON_ERROR_STOP`) and exits with status 0 on success;
- is **idempotent**: running it twice in a row succeeds both times and changes nothing the second time.

Start from a cluster without `bazaar`, so your script really creates it:

```bash
./project/bin/psql.sh postgres -c 'DROP DATABASE IF EXISTS bazaar'
```

Then run your script. With the container's psql, either feed it through stdin as shown here, or run `\i /course/workspace/01/setup_bazaar.sql` from an interactive session (`workspace/` is mounted read-only at `/course/workspace`):

```bash
docker exec -i course-pg psql -X -U postgres -d postgres < workspace/01/setup_bazaar.sql
```

or with a native psql: `psql -X -d postgres -f workspace/01/setup_bazaar.sql`.

Constraint: `CREATE DATABASE` has no `IF NOT EXISTS` and can't run inside a `DO` block or a transaction. Section 10.3 of the theory shows the technique to use.

### 2. `~/.psqlrc`: a comfortable psql

A `.psqlrc` for your **native** psql (or copied into the container with `docker cp ~/.psqlrc course-pg:/root/.psqlrc`) that at least:

- shows NULL as a visible symbol;
- switches to expanded output automatically for wide results;
- turns on timing;
- protects interactive transactions from typos (`ON_ERROR_ROLLBACK`), without affecting scripts;
- shows user, host, port and database in the prompt, along with the transaction-state markers;
- keeps a larger, de-duplicated history;
- loads silently, without "Null display is ..." lines at start-up.

Add anything else you find useful, such as saved queries in variables. Keep a copy at `workspace/01/psqlrc`, with your other workspace files (`workspace/` is git-ignored, so version it in your own repository if you like).

### 3. `workspace/01/db_report.sql`: the database report

A psql script, run while connected to any database, that prints:

- a header with the **database name** and the **current user**;
- the **server version** (human-readable) and the **database size** (human-readable, like `7590 kB`);
- the current `search_path`;
- a **version check**: `OK` when `server_version_num` is at least a minimum, given as an optional psql variable `min_version` (default `170000`, so `-v min_version=180000` makes the check fail), and a warning **on stderr** otherwise;
- a table with the **number of tables per schema** for every non-system schema, **including schemas with zero tables**;
- a closing line with the total number of user tables, or a friendly message if there are none yet.

It must use **`\gset`** to get values from SQL into psql variables, and **`\if`** for both the version check and the closing line. It must not depend on your `.psqlrc` (test it with `-X`).

## Acceptance checks

**Setup is correct and idempotent.**

```bash
./project/bin/psql.sh postgres -c 'DROP DATABASE IF EXISTS bazaar'
docker exec -i course-pg psql -X -U postgres -d postgres < workspace/01/setup_bazaar.sql; echo "exit=$?"
docker exec -i course-pg psql -X -U postgres -d postgres < workspace/01/setup_bazaar.sql; echo "exit=$?"
# both runs: exit=0 (the second may print NOTICEs like 'schema "bazaar" already exists, skipping')
```

In a **new** session (`./project/bin/psql.sh`):

```sql
\dn
```

```text
      List of schemas
  Name  |       Owner       
--------+-------------------
 bazaar | postgres
 public | pg_database_owner
(2 rows)
```

```sql
\drds
SHOW search_path;
```

```text
               List of settings
 Role | Database |          Settings          
------+----------+----------------------------
      | bazaar   | search_path=bazaar, public
(1 row)

  search_path   
----------------
 bazaar, public
(1 row)
```

If you did exercise 10, your `check_ready.sql` prints `READY` now.

**The report works on an empty database.**

```bash
docker exec -i course-pg psql -X -U postgres -d bazaar < workspace/01/db_report.sql
```

Expected (sizes and the minor version will differ):

```text
==============================================
 Database report: bazaar
==============================================
Connected as     : postgres
Server version   : 17.11 (Debian 17.11-1.pgdg13+2)
Database size    : 7590 kB
search_path      : bazaar, public
Version check    : OK (server_version_num >= 170000)

Tables per schema:
 schema | tables |  size   
--------+--------+---------
 bazaar |      0 | 0 bytes
 public |      0 | 0 bytes

No user tables yet. (The Bazaar core tables arrive in module 02.)
```

**The version check fails when it should, on stderr.**

```bash
docker exec -i course-pg psql -X -U postgres -d bazaar -v min_version=180000 < workspace/01/db_report.sql 2>/dev/null | grep -c 'Version check'
# 0   (the FAILED line went to stderr, so it was discarded)
```

**The report works on a populated database.** Load the reference state for module 02's tables, run the report, and expect `bazaar | 7` and `Total user tables: 7`:

```bash
./project/bin/reset.sh 03
docker exec -i course-pg psql -X -U postgres -d bazaar < workspace/01/db_report.sql
```

```text
Tables per schema:
 schema | tables |  size   
--------+--------+---------
 bazaar |      7 | 136 kB
 public |      0 | 0 bytes

Total user tables: 7
```

Then go back to the state module 02 starts from: `./project/bin/reset.sh 02`. That state is identical to what your setup script produces. `reset.sh` runs essentially the same three statements.

**Your `.psqlrc` is active, and scripts are unaffected.** In an interactive session:

```text
postgres@[local]:5432/bazaar=# SELECT NULL AS nothing;
 nothing 
─────────
 ∅
(1 row)

Time: 0.496 ms
postgres@[local]:5432/bazaar=# BEGIN;
BEGIN
Time: 0.052 ms
postgres@[local]:5432/bazaar=*# SELEC 1;
ERROR:  syntax error at or near "SELEC"
LINE 1: SELEC 1;
        ^
Time: 0.154 ms
postgres@[local]:5432/bazaar=*# ROLLBACK;
ROLLBACK
Time: 0.034 ms
```

The prompt must stay `*` (not `!`) after the typo. And `psql -X -Atc 'SELECT 1'` prints exactly `1`, with no `Time:` line.

## Reference solution

- [`solutions/setup_bazaar.sql`](solutions/setup_bazaar.sql): idempotent database creation with `\gset` and `\if`, schema, and `ALTER DATABASE ... SET search_path`.
- [`solutions/psqlrc`](solutions/psqlrc): the recommended `.psqlrc`, explained line by line in section 13.2 of the theory.
- [`solutions/db_report.sql`](solutions/db_report.sql): the report, using `\gset`, `:{?var}` defaults, `\if` and `\warn`.

This module has no file in `project/db/`. The reference database setup is built into [`project/bin/reset.sh`](../../project/bin/reset.sh), which runs the same `CREATE DATABASE`, `CREATE SCHEMA` and `ALTER DATABASE ... SET search_path` before applying any module's files.
