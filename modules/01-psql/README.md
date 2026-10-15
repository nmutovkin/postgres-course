# Module 01: psql and the Postgres Landscape

<!-- nav:start -->
> **Theory** · [Exercises](exercises.md) · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 00 Setup](../00-setup/README.md) · [Course home](../../README.md) · [02 Data Types and Tables →](../02-types-and-ddl/README.md)
<!-- nav:end -->

It's the first week of Bazaar. Before anyone designs a table, the team needs to agree on where things live. Which server? Which database? Which schema? Who owns what? Everyone also needs to be fluent in the one tool that is available on every PostgreSQL machine you'll ever log into: `psql`. In this module you learn the map (cluster, databases, schemas, roles) and the tool. By the end you'll set up the Bazaar database yourself, configure psql the way experienced people do, and write a psql script that reports on the database's health.

**What you'll learn**

- How a client talks to the PostgreSQL server, and why every connection gets its own server process.
- The hierarchy cluster → databases → schemas → objects, the special databases `postgres`, `template0` and `template1`, and how `search_path` decides which table a bare name refers to.
- Roles, at the level you need to read `\du` output and understand "permission denied".
- psql in depth: describing objects (`\d` and friends, with patterns), formatting output, editing and running files, `\g`/`\gx`/`\gset`/`\gexec`/`\watch`, variables and `\if`, error handling (`ON_ERROR_STOP`, `ON_ERROR_ROLLBACK`, `AUTOCOMMIT`), `\copy` versus `COPY`, `.psqlrc`.
- Scripting psql from the shell: `-c`, `-f`, `-X`, `-A`, `-t`, `--csv`, exit codes, pipelines.
- The system catalogs (`pg_class`, `pg_namespace`, `pg_attribute`) and `information_schema`, and how to see the queries psql runs behind `\d`.

**Before you start**

```bash
./project/bin/reset.sh 01
docker exec -i course-pg psql -X -U postgres -d bazaar < modules/01-psql/solutions/scratch_setup.sql
./project/bin/psql.sh
```

The `bazaar` database is empty at this point: the Bazaar tables arrive in module 02. So this module's examples use the system catalogs and a small scratch table, `launch_checklist`, which the second command creates. Section 6 shows and explains what that script contains. A few examples show Bazaar tables to illustrate `\d` on something realistic. Those come from `./project/bin/reset.sh 03`, and you'll build them yourself in module 02. If you run them, run `./project/bin/reset.sh 01` afterwards and re-run `scratch_setup.sql` (the second command above), because the reset removes `launch_checklist` too.

You should be comfortable connecting as described in [Module 00](../00-setup/). Examples use the container's psql via `./project/bin/psql.sh` unless they say otherwise.

---

<!-- toc:start -->
**Contents**

- [1. Client and server](#1-client-and-server)
  - [1.1 One process per connection](#11-one-process-per-connection)
- [2. Cluster, databases, schemas, objects](#2-cluster-databases-schemas-objects)
  - [2.1 A connection is to exactly one database](#21-a-connection-is-to-exactly-one-database)
  - [2.2 `postgres`, `template1` and `template0`](#22-postgres-template1-and-template0)
  - [2.3 Schemas](#23-schemas)
- [3. `search_path`: what does a bare name mean?](#3-search_path-what-does-a-bare-name-mean)
  - [3.1 Lookup in action](#31-lookup-in-action)
  - [3.2 Where do new objects go?](#32-where-do-new-objects-go)
  - [3.3 Setting it: session, role, database](#33-setting-it-session-role-database)
  - [3.4 Shadowing built-ins](#34-shadowing-built-ins)
- [4. Roles, briefly](#4-roles-briefly)
- [5. psql basics](#5-psql-basics)
  - [5.1 Starting psql](#51-starting-psql)
  - [5.2 Reading the prompt](#52-reading-the-prompt)
  - [5.3 Statements, buffers, cancelling](#53-statements-buffers-cancelling)
- [6. The scratch table for this module](#6-the-scratch-table-for-this-module)
- [7. Describing things: `\d` and friends](#7-describing-things-d-and-friends)
  - [7.1 Tables, views, functions](#71-tables-views-functions)
  - [7.2 Patterns](#72-patterns)
- [8. Formatting output](#8-formatting-output)
  - [8.1 Expanded display: `\x`](#81-expanded-display-x)
  - [8.2 Making NULL visible](#82-making-null-visible)
  - [8.3 Borders, lines, formats](#83-borders-lines-formats)
  - [8.4 `\timing`](#84-timing)
- [9. Working with files and the editor](#9-working-with-files-and-the-editor)
  - [9.1 `\e`: edit the query in your editor](#91-e-edit-the-query-in-your-editor)
  - [9.2 `\i` and `\ir`: run a file](#92-i-and-ir-run-a-file)
  - [9.3 `\o` and `\w`: write output and queries to files](#93-o-and-w-write-output-and-queries-to-files)
  - [9.4 `\!`: run a shell command](#94--run-a-shell-command)
- [10. Ways to run a query: `\g`, `\gx`, `\gset`, `\gexec`, `\watch`](#10-ways-to-run-a-query-g-gx-gset-gexec-watch)
  - [10.1 `\g` and `\gx`](#101-g-and-gx)
  - [10.2 `\gset`: store results in variables](#102-gset-store-results-in-variables)
  - [10.3 `\gexec`: run the statements a query generates](#103-gexec-run-the-statements-a-query-generates)
  - [10.4 `\watch`: re-run a query periodically](#104-watch-re-run-a-query-periodically)
  - [10.5 Two more: `\crosstabview` and `\bind`](#105-two-more-crosstabview-and-bind)
- [11. Variables and conditionals](#11-variables-and-conditionals)
  - [11.1 Setting and using variables](#111-setting-and-using-variables)
  - [11.2 Special variables](#112-special-variables)
  - [11.3 `\if`, `\elif`, `\else`, `\endif`](#113-if-elif-else-endif)
  - [11.4 Errors and transactions: `ON_ERROR_STOP`, `ON_ERROR_ROLLBACK`, `AUTOCOMMIT`](#114-errors-and-transactions-on_error_stop-on_error_rollback-autocommit)
- [12. `\copy` versus `COPY`](#12-copy-versus-copy)
- [13. History and `~/.psqlrc`](#13-history-and-psqlrc)
  - [13.1 History](#131-history)
  - [13.2 `~/.psqlrc`](#132-psqlrc)
- [14. Scripting psql from the shell](#14-scripting-psql-from-the-shell)
- [15. The system catalogs and `information_schema`](#15-the-system-catalogs-and-information_schema)
  - [15.1 The three you need first](#151-the-three-you-need-first)
  - [15.2 OIDs and the `reg*` casts](#152-oids-and-the-reg-casts)
  - [15.3 How `\d` is built: `psql -E`](#153-how-d-is-built-psql--e)
  - [15.4 `information_schema`](#154-information_schema)
- [16. Getting help](#16-getting-help)
- [Summary](#summary)
- [What's next](#whats-next)

<!-- toc:end -->

## 1. Client and server

PostgreSQL is a **client/server** system. The **server** is a long-running program that owns the data files on disk. It's the only thing that ever reads or writes them. A **client** is any program that wants data: psql, a Python service, a GUI, a backup tool. Clients never touch the files. They open a connection to the server, send SQL text, and get results back over a network protocol (the PostgreSQL *wire protocol*). The client and server can be on the same laptop or on opposite sides of the world. Nothing in the protocol cares.

That has consequences you'll meet over and over:

- A query runs **on the server**. A file path in SQL (`COPY ... TO '/tmp/x.csv'`) is a path on the *server's* filesystem, which in this course is inside the container. Section 12 deals with this.
- The server's configuration (memory, logging, ...) is separate from the client's configuration (how psql prints things). `SET` and `SHOW` talk to the server. psql's own `\set` and `\pset` don't leave your machine.
- Many clients can be connected at once. The server is responsible for making their work not collide. That's what transactions and locking are for (module 10).

### 1.1 One process per connection

When the server starts, a single process starts first. Historically it is called the **postmaster**. It listens on the port and the Unix socket. Each time a client connects, the postmaster **forks** a new process dedicated to that one connection, called a **backend**. The backend authenticates the client, then runs every query that client sends until the client disconnects, and then it exits. Alongside, a handful of **background processes** do housekeeping.

```text
                         ┌───────────────────────────────────────────────┐
  psql ───────────────┐  │  postmaster (pid 1 in the container)          │
                      ├──┼─▶ listens on :5432 and the Unix socket        │
  Python service ─────┤  │                                               │
                      │  │   ├─ backend  "postgres bazaar [local]"  ◀────┼── one per connection
  GUI ────────────────┘  │   ├─ backend  ...                             │
                         │   ├─ checkpointer          ┐                  │
                         │   ├─ background writer     │ housekeeping     │
                         │   ├─ walwriter             │ (module 16)      │
                         │   ├─ autovacuum launcher   │                  │
                         │   └─ logical replication launcher             │
                         └───────────────┬───────────────────────────────┘
                                         │ shared memory + data files
```

You can see this from the outside. Here's the container's process list while one psql session is running a query:

```bash
docker exec course-pg ps -ef --forest
```

```text
UID          PID    PPID  C STIME TTY          TIME CMD
root          90       0  4 07:17 ?        00:00:00 /usr/lib/postgresql/17/bin/psql -U postgres -d bazaar -c select pg_backend_pid(), pg_sleep(3)
postgres       1       0  0 07:15 ?        00:00:00 postgres -c shared_preload_libraries=pg_stat_statements,auto_explain -c pg_stat_statements.track=all -c track_io_timing=on -c log_min_duration_statement=1000
postgres      60       1  0 07:15 ?        00:00:00 postgres: checkpointer 
postgres      61       1  0 07:15 ?        00:00:00 postgres: background writer 
postgres      63       1  0 07:15 ?        00:00:00 postgres: walwriter 
postgres      64       1  0 07:15 ?        00:00:00 postgres: autovacuum launcher 
postgres      65       1  0 07:15 ?        00:00:00 postgres: logical replication launcher 
postgres      96       1  0 07:17 ?        00:00:00 postgres: postgres bazaar [local] SELECT
```

Process 96 is the backend serving that psql (process 90). Its title says who it serves: user `postgres`, database `bazaar`, over a local socket, currently running a `SELECT`. And from the inside, the view `pg_stat_activity` has one row per server process:

```sql
SELECT pid, usename, datname, backend_type, state, left(query, 40) AS query
FROM pg_stat_activity
ORDER BY pid;
```

```text
 pid | usename  | datname |         backend_type         | state  |                  query                   
-----+----------+---------+------------------------------+--------+------------------------------------------
  60 |          |         | checkpointer                 |        | 
  61 |          |         | background writer            |        | 
  63 |          |         | walwriter                    |        | 
  64 |          |         | autovacuum launcher          |        | 
  65 | postgres |         | logical replication launcher |        | 
  96 | postgres | bazaar  | client backend               | active | select pg_backend_pid(), pg_sleep(3)
 109 | postgres | bazaar  | client backend               | active | SELECT pid, usename, datname, backend_ty
(7 rows)
```

`pg_backend_pid()` returns the pid of *your* backend, which is handy for finding yourself in that list.

Why a process per connection, rather than threads? Isolation: a crash in one backend can't scribble over another's memory, and the design is simple and robust. The cost is that a connection is fairly expensive (a process, several MB of memory, a few milliseconds to start). PostgreSQL works well with tens or a few hundred connections, not tens of thousands. That's why production systems put a **connection pooler** in front (module 17) and why Python applications use connection pools (module 22).

> **Under the hood:** the backends share data through a region of **shared memory** (the buffer cache, lock tables and more), set up by the postmaster at start-up. That's why `shared_preload_libraries` must be set when the server starts: those libraries reserve shared memory before any backend exists. Module 16 opens all of this up.

---

## 2. Cluster, databases, schemas, objects

One running server manages one **cluster**. In PostgreSQL that word doesn't mean "several machines". It's the official name for *one server's data directory and everything in it*. A cluster contains several **databases**. Each database contains **schemas**, and each schema contains **objects**: tables, views, sequences, functions, types, and so on.

```text
cluster  (one server, one data directory: /var/lib/postgresql/data)
│
├── roles, tablespaces                 ← cluster-wide ("global") objects
│
├── database  postgres                 ← default maintenance database
├── database  template0                ← pristine template, no connections allowed
├── database  template1                ← template for every CREATE DATABASE
└── database  bazaar
    ├── schema  pg_catalog             ← the system catalogs (built in)
    ├── schema  information_schema     ← SQL-standard views over the catalogs
    ├── schema  public                 ← created in every database by default
    └── schema  bazaar                 ← where the Bazaar tables will live
        ├── table     users, products, orders, ...   (module 02)
        ├── view      ...
        └── function  ...
```

Every object has a fully qualified name: `database.schema.object`. In practice you rarely write the database part, because of the most important rule on this page.

### 2.1 A connection is to exactly one database

When you connect, you choose a database. That session can then only see objects **in that database**. There are no cross-database queries:

```sql
SELECT * FROM postgres.pg_catalog.pg_class LIMIT 1;
```

```text
ERROR:  cross-database references are not implemented: "postgres.pg_catalog.pg_class"
LINE 1: SELECT * FROM postgres.pg_catalog.pg_class LIMIT 1;
                      ^
```

To work in another database, you open another connection (`\c otherdb` in psql). This makes databases strong walls. They are the right tool for separating completely independent applications or tenants that must never be queried together. Within one application, you separate things with **schemas**, which you *can* join across freely. Bazaar uses one database, `bazaar`, and one schema, also called `bazaar`.

A few things live outside any database and are shared by the whole cluster. The most important are **roles** (users and groups, section 4) and the list of databases itself:

```sql
SELECT oid, datname, datistemplate, datallowconn
FROM pg_database
ORDER BY oid;
```

```text
  oid  |  datname  | datistemplate | datallowconn 
-------+-----------+---------------+--------------
     1 | template1 | t             | t
     4 | template0 | t             | f
     5 | postgres  | f             | t
 16384 | bazaar    | f             | t
(4 rows)
```

`oid` is the **object identifier**, the internal numeric id PostgreSQL gives to nearly everything. Ids below 16384 are reserved for objects created when the cluster was initialised. The first database you create gets 16384.

### 2.2 `postgres`, `template1` and `template0`

A fresh cluster has three databases:

- **`postgres`** is an ordinary, empty database that exists so that tools and people always have *somewhere* to connect. Use it as a maintenance database: connect to it to create or drop other databases. (You can't drop the database you're connected to.)
- **`template1`** is the template for new databases. `CREATE DATABASE x` makes `x` as a **copy of `template1`**. Anything you put in `template1`, such as a table, an extension or a function, appears in every database created afterwards.
- **`template0`** is a pristine copy of `template1` as it was at cluster creation. It refuses connections (`datallowconn = f`) so nobody can modify it by accident. You use it as the source when you need a guaranteed-clean database, for example when restoring a backup (module 18), or when you need a different encoding or locale than `template1` has.

Seeing is believing:

```sql
\c template1
CREATE TABLE welcome (msg text);
INSERT INTO welcome VALUES ('copied from template1');
\c postgres
CREATE DATABASE scratch;
\c scratch
TABLE welcome;
```

```text
          msg          
-----------------------
 copied from template1
(1 row)
```

Clean up (`\c postgres`, `DROP DATABASE scratch;`, `\c template1`, `DROP TABLE welcome;`) and don't leave things in `template1` unless you mean it. The protection on `template0` is easy to confirm:

```text
\connect: connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed: FATAL:  database "template0" is not currently accepting connections
```

You can use **any** database as a template: `CREATE DATABASE bazaar_copy TEMPLATE bazaar;` makes a fast copy of a whole database, which is handy for experiments. The catch: nobody else may be connected to the source while it's copied.

```text
ERROR:  source database "bazaar" is being accessed by other users
DETAIL:  There is 1 other session using the database.
```

`./project/bin/reset.sh` creates `bazaar` from the default `template1`. In the project milestone you'll do the same by hand.

### 2.3 Schemas

A **schema** is a namespace inside a database: a named container for objects, like a directory for files. Two schemas can each have a table called `orders`, and `sales.orders` and `archive.orders` are different tables. Unlike databases, schemas are not walls. One query can join tables from many schemas.

Why use schemas at all?

- **Organisation.** Group an application's objects together, apart from extensions' objects and ad-hoc scratch tables.
- **Permissions.** Access can be granted per schema (module 15).
- **Coexistence.** Several applications, or several versions of one, can share a database without name clashes.

`\dn` lists schemas:

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

Every database starts with a schema called **`public`**. Since PostgreSQL 15 it is owned by the special role `pg_database_owner` (meaning "whoever owns this database"), and ordinary users can no longer create objects in it unless granted permission. Before version 15, everyone could create tables in `public`, which was a long-standing security weakness. Many tutorials still assume the old behaviour.

`\dn` hides the system schemas. Add `S` to see them:

```sql
\dnS
```

```text
            List of schemas
        Name        |       Owner       
--------------------+-------------------
 bazaar             | postgres
 information_schema | postgres
 pg_catalog         | postgres
 pg_toast           | postgres
 public             | pg_database_owner
(5 rows)
```

- **`pg_catalog`** holds the **system catalogs**: tables describing every database object (section 15), plus all built-in functions, types and operators. `now()`, `count()` and `integer` all live here.
- **`information_schema`** is a set of SQL-standard views over the catalogs, portable across databases (section 15).
- **`pg_toast`** stores out-of-line copies of large values (module 16). You never query it directly.

Creating and dropping schemas:

```sql
CREATE SCHEMA staging;
CREATE SCHEMA IF NOT EXISTS staging;   -- no error if it exists
DROP SCHEMA staging;                   -- fails if it still contains objects
DROP SCHEMA staging CASCADE;           -- drops the schema and everything in it
```

> **Pitfall:** `DROP SCHEMA ... CASCADE` doesn't ask for confirmation. It prints a `NOTICE` listing what it dropped *after* doing it.

---

## 3. `search_path`: what does a bare name mean?

You'll almost never write `bazaar.bazaar.products`. You write `products`. PostgreSQL resolves an unqualified name by walking the **`search_path`**, a list of schemas, and using the first schema that contains an object of that name.

```sql
SHOW search_path;
```

```text
  search_path   
----------------
 bazaar, public
(1 row)
```

In the `bazaar` database it's `bazaar, public`, because `reset.sh` ran `ALTER DATABASE bazaar SET search_path = bazaar, public`. The built-in default is `"$user", public`. `"$user"` means "a schema with the same name as the current user, if it exists". It's skipped silently if it doesn't.

`current_schemas()` shows the *effective* path, after skipping schemas that don't exist:

```sql
SELECT current_schemas(true);
```

```text
      current_schemas       
----------------------------
 {pg_catalog,bazaar,public}
(1 row)
```

With `true`, it includes the implicit schemas. **`pg_catalog` is always searched**, and if you don't list it, it's searched *first*. That's why `now()` and `count()` work whatever your `search_path` is.

### 3.1 Lookup in action

Create a scratch table with a bare name:

```sql
CREATE TABLE scratch_note (id int, body text);
```

It lands in `bazaar`, the first schema in the path:

```sql
\dt
```

```text
            List of relations
 Schema |     Name     | Type  |  Owner   
--------+--------------+-------+----------
 bazaar | scratch_note | table | postgres
(1 row)
```

Change the path and the bare name stops resolving, although the table hasn't moved:

```sql
SET search_path = public;
SELECT * FROM scratch_note;
```

```text
ERROR:  relation "scratch_note" does not exist
LINE 1: SELECT * FROM scratch_note;
                      ^
```

A qualified name always works: `SELECT * FROM bazaar.scratch_note;`. Now create a second `scratch_note` in `public` (the path is still `public`, so that's where a bare `CREATE TABLE` goes), restore the path, and list both with a pattern (`*.scratch_note` means "in any schema", section 7):

```sql
CREATE TABLE scratch_note (id int);
SET search_path = bazaar, public;
\dt *.scratch_note
```

```text
            List of relations
 Schema |     Name     | Type  |  Owner   
--------+--------------+-------+----------
 bazaar | scratch_note | table | postgres
 public | scratch_note | table | postgres
(2 rows)
```

With `bazaar` first, `scratch_note` means `bazaar.scratch_note`. The `public` one is **shadowed**. It's there, but invisible to unqualified names. Clean up with `DROP TABLE public.scratch_note, bazaar.scratch_note;`.

### 3.2 Where do new objects go?

`CREATE` with an unqualified name puts the object in the **first schema of the path that exists**:

```sql
SET search_path = nosuch, bazaar;
SELECT current_schemas(false);
```

```text
 current_schemas 
-----------------
 {bazaar}
(1 row)
```

`nosuch` doesn't exist, so it's skipped, and a `CREATE TABLE` here would go to `bazaar`. If *no* schema in the path exists:

```sql
SET search_path = nosuch;
CREATE TABLE where_am_i (x int);
```

```text
ERROR:  no schema has been selected to create in
LINE 1: CREATE TABLE where_am_i (x int);
                     ^
```

### 3.3 Setting it: session, role, database

`search_path` is an ordinary configuration parameter, and like most parameters it can be set at several levels. The most specific one wins:

| Level | How | Lasts |
|---|---|---|
| Session | `SET search_path = ...;` | Until `RESET search_path` or disconnect |
| Role in a database | `ALTER ROLE r IN DATABASE d SET search_path = ...;` | New sessions of that role in that database |
| Role | `ALTER ROLE r SET search_path = ...;` | New sessions of that role |
| Database | `ALTER DATABASE d SET search_path = ...;` | New sessions in that database |
| Server | `postgresql.conf` | Everything else |

Role and database settings are stored in the catalog `pg_db_role_setting`, and psql shows them with `\drds`:

```sql
\drds
```

```text
               List of settings
 Role | Database |          Settings          
------+----------+----------------------------
      | bazaar   | search_path=bazaar, public
(1 row)
```

> **Pitfall:** `ALTER DATABASE ... SET` and `ALTER ROLE ... SET` apply only to **new** sessions. Your current session keeps its value until you reconnect (`\c`) or `SET` it yourself. "I changed it and nothing happened" is almost always this.

### 3.4 Shadowing built-ins

Because `pg_catalog` is searched first *unless you list it explicitly*, a user function can't accidentally replace a built-in one:

```sql
CREATE FUNCTION bazaar.now() RETURNS text LANGUAGE sql AS $$ SELECT 'gotcha' $$;
SET search_path = bazaar, public;
SELECT now();          -- still the real now(): pg_catalog is implicitly first
```

But if you put `pg_catalog` in the path yourself, its position is respected:

```sql
SET search_path = bazaar, pg_catalog;
SELECT now();
```

```text
  now   
--------
 gotcha
(1 row)
```

Drop that function (`DROP FUNCTION bazaar.now();`) and `RESET search_path;`. This isn't just trivia. It's the basis of a real class of security problems, where a malicious user creates a function in a schema that's searched before the one you meant. Module 15 shows how `SECURITY DEFINER` functions defend against it by pinning `search_path`.

> **In production:** application code should either qualify names fully, or rely on a `search_path` that's set at database or role level and never changed per query. Avoid `SET search_path` in the middle of application code, and never put a schema that untrusted users can write to before the schemas you rely on.

---

## 4. Roles, briefly

PostgreSQL has a single concept for users and groups: the **role**. A role that may log in (`LOGIN`) acts as a user. A role that other roles are members of acts as a group. Roles are cluster-wide: the same `alice` exists in every database of the cluster, but what she may *do* is granted per database and per object.

```sql
CREATE ROLE alice LOGIN PASSWORD 'alice';
CREATE ROLE analysts;              -- no LOGIN: a group
GRANT analysts TO alice;           -- alice is now a member
\du
```

```text
                             List of roles
 Role name |                         Attributes                         
-----------+------------------------------------------------------------
 alice     | 
 analysts  | Cannot login
 postgres  | Superuser, Create role, Create DB, Replication, Bypass RLS
```

`postgres` is a **superuser**: permission checks don't apply to it at all. That's convenient for learning and dangerous in production. Everything else must be granted. Every object has an **owner** (the role that created it), and the owner can do anything with it.

Watch what happens when `alice` connects (`\c - alice` means "same database, different user"):

```sql
\c - alice
SELECT * FROM launch_checklist;
```

```text
ERROR:  relation "launch_checklist" does not exist
LINE 1: SELECT * FROM launch_checklist;
                      ^
```

The table does exist, in schema `bazaar`. But `alice` has no `USAGE` privilege on that schema, so for her the schema contributes nothing to name lookup. It's as if it weren't in the `search_path`. Creating objects fails as well:

```sql
CREATE TABLE public.mine (x int);
```

```text
ERROR:  permission denied for schema public
LINE 1: CREATE TABLE public.mine (x int);
                     ^
```

That's the PostgreSQL 15+ behaviour of `public` from section 2.3. For this module, remember three things: a role is the unit of identity, superusers bypass all checks, and "does not exist" can mean "exists but you can't see it". Module 15 covers privileges, default privileges, row-level security and authentication properly. Clean up with `\c - postgres`, then `DROP ROLE alice; DROP ROLE analysts;`.

(`current_user` tells you which role you're acting as. There are also 15 built-in roles whose names start with `pg_`, such as `pg_read_all_data` and `pg_write_server_files`. They're predefined groups that grant specific powers, and `\du` hides them.)

---

## 5. psql basics

psql is a **REPL**: it reads what you type, sends SQL to the server, and prints the result. Everything starting with a backslash is a **meta-command**, handled by psql itself and never sent to the server as-is.

### 5.1 Starting psql

```bash
psql -h localhost -p 5432 -U postgres -d bazaar     # native, over TCP (module 00)
./project/bin/psql.sh                               # inside the container
```

The banner shows psql's version and, if it differs, the server's. Then you get the prompt.

### 5.2 Reading the prompt

The default prompt is `%/%R%x%# `: database name, then up to three status characters. They tell you things you'd otherwise have to guess:

```text
bazaar=# BEGIN;
BEGIN
bazaar=*# UPDATE launch_checklist SET done = true WHERE id = 3;
UPDATE 1
bazaar=*# SELECT 1/0;
ERROR:  division by zero
bazaar=!# SELECT done FROM launch_checklist WHERE id = 3;
ERROR:  current transaction is aborted, commands ignored until end of transaction block
bazaar=!# ROLLBACK;
ROLLBACK
bazaar=# SELECT task
bazaar-# FROM launch_checklist
bazaar-# WHERE id = 1;
      task       
-----------------
 Pick a database
(1 row)

bazaar=# SELECT 'unfinished
bazaar'# string';
```

| Marker | Meaning |
|---|---|
| `=` | Ready for a new statement. |
| `-` | Continuation: the statement isn't finished (no `;` yet). |
| `'` `"` `(` | You're inside an unclosed quote or parenthesis. A missing `'` is the most common reason psql "doesn't respond". |
| `*` | You're inside a transaction block (after `BEGIN`). |
| `!` | You're inside a **failed** transaction. Everything is ignored until `ROLLBACK`. |
| `#` / `>` | Superuser / ordinary role. |

The `*` and `!` markers are worth learning by heart. An `*` you forgot about means your changes aren't committed and you may be holding locks that block other people (module 10).

### 5.3 Statements, buffers, cancelling

psql collects what you type into a **query buffer** and sends it when it sees a `;` at the end of a line (outside quotes), or when you type `\g`. So you can spread a statement over many lines. Useful keys:

- **Ctrl-C** cancels the running query (psql asks the server to stop it), or clears the current input line.
- **Ctrl-D** or `\q` quits.
- `\r` resets (clears) the query buffer. `\p` prints it.
- **Tab** completes SQL keywords, table names, column names and meta-commands. Use it constantly.
- **Up/Down** walk through history (section 13).

`\c` (or `\connect`) switches connection: `\c postgres` changes database, `\c - alice` changes user, `\c bazaar postgres localhost 5432` changes everything. `\conninfo` tells you where you are:

```text
You are connected to database "bazaar" as user "postgres" via socket in "/var/run/postgresql" at port "5432".
```

---

## 6. The scratch table for this module

To have something to describe and query before module 02, "Before you start" ran `solutions/scratch_setup.sql`. Its core is a checklist for the Bazaar launch, plus a view and a function over it:

```sql
CREATE TABLE launch_checklist (
    id      int     GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task    text    NOT NULL,
    owner   text,
    done    boolean NOT NULL DEFAULT false,
    due     date
);

INSERT INTO launch_checklist (task, owner, done, due) VALUES
    ('Pick a database',        'Ana', true,  '2026-01-05'),
    ('Install PostgreSQL 17',  'Ana', true,  '2026-01-06'),
    ('Create bazaar database', 'Ben', false, '2026-01-07'),
    ('Write .psqlrc',          'Ben', false, NULL),
    ('Design core schema',     NULL,  false, '2026-01-20');

CREATE VIEW open_tasks AS
SELECT id, task, owner, due FROM launch_checklist WHERE NOT done;

CREATE FUNCTION tasks_left(p_owner text) RETURNS bigint
LANGUAGE sql STABLE
AS $$
    SELECT count(*) FROM launch_checklist WHERE owner = p_owner AND NOT done
$$;

COMMENT ON TABLE launch_checklist IS 'Things to do before Bazaar goes live.';
```

Re-run the script whenever you want the data back in this state. You don't need to understand every keyword yet. Module 02 covers types, `GENERATED ALWAYS AS IDENTITY`, `PRIMARY KEY` and `NOT NULL`. Module 09 covers views and functions. `NULL` means "no value". Two tasks have one.

---

## 7. Describing things: `\d` and friends

The meta-commands starting with `\d` ("describe") are how you explore a database. They all follow the same scheme: `\d` + a letter for the object type, an optional `+` for more detail, an optional `S` to include system objects, and an optional **pattern**.

| Command | Lists |
|---|---|
| `\l` | databases (cluster-wide) |
| `\dn` | schemas |
| `\dt` `\dv` `\dm` `\ds` `\di` | tables, views, materialized views, sequences, indexes |
| `\d` | all of the relations above at once |
| `\d NAME` | **one** object in detail: columns, indexes, constraints, references |
| `\df` | functions (`\dfa` aggregates, `\dfw` window functions, ...) |
| `\dT` | data types |
| `\du` (`\dg`) | roles |
| `\dx` | installed extensions |
| `\dp` | access privileges |
| `\drds` | per-role/per-database settings |
| `\dconfig` | server settings (non-default ones without a pattern) |
| `\sf NAME` / `\sv NAME` | show a function's / view's source |
| `\ef NAME` / `\ev NAME` | edit a function / view in your editor |

### 7.1 Tables, views, functions

```sql
\dt
```

```text
              List of relations
 Schema |       Name       | Type  |  Owner   
--------+------------------+-------+----------
 bazaar | launch_checklist | table | postgres
(1 row)
```

`+` adds size, persistence, access method and the comment:

```sql
\dt+
```

```text
                                                     List of relations
 Schema |       Name       | Type  |  Owner   | Persistence | Access method | Size  |              Description              
--------+------------------+-------+----------+-------------+---------------+-------+---------------------------------------
 bazaar | launch_checklist | table | postgres | permanent   | heap          | 16 kB | Things to do before Bazaar goes live.
(1 row)
```

`\d` with a name describes one object. This is the command you'll use most:

```sql
\d launch_checklist
```

```text
                    Table "bazaar.launch_checklist"
 Column |  Type   | Collation | Nullable |           Default            
--------+---------+-----------+----------+------------------------------
 id     | integer |           | not null | generated always as identity
 task   | text    |           | not null | 
 owner  | text    |           |          | 
 done   | boolean |           | not null | false
 due    | date    |           |          | 
Indexes:
    "launch_checklist_pkey" PRIMARY KEY, btree (id)
```

Notice that the primary key created an index (`launch_checklist_pkey`) automatically. Module 11 explains why. `\d+` adds storage details and per-column descriptions.

On a real Bazaar table, `\d` shows much more. This is `products` from module 02 (from `./project/bin/reset.sh 03`):

```text
                                   Table "bazaar.products"
   Column    |           Type           | Collation | Nullable |           Default            
-------------+--------------------------+-----------+----------+------------------------------
 id          | bigint                   |           | not null | generated always as identity
 seller_id   | bigint                   |           | not null | 
 category_id | integer                  |           | not null | 
 sku         | text                     |           | not null | 
 title       | text                     |           | not null | 
 description | text                     |           |          | 
 price       | numeric(12,2)            |           | not null | 
 status      | text                     |           | not null | 'draft'::text
 created_at  | timestamp with time zone |           | not null | now()
 updated_at  | timestamp with time zone |           | not null | now()
Indexes:
    "products_pkey" PRIMARY KEY, btree (id)
    "products_seller_id_sku_key" UNIQUE CONSTRAINT, btree (seller_id, sku)
Check constraints:
    "products_price_nonnegative" CHECK (price >= 0::numeric)
    "products_status_check" CHECK (status = ANY (ARRAY['draft'::text, 'active'::text, 'archived'::text]))
Foreign-key constraints:
    "products_category_id_fkey" FOREIGN KEY (category_id) REFERENCES categories(id)
    "products_seller_id_fkey" FOREIGN KEY (seller_id) REFERENCES sellers(id)
Referenced by:
    TABLE "inventory" CONSTRAINT "inventory_product_id_fkey" FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
    TABLE "order_items" CONSTRAINT "order_items_product_id_fkey" FOREIGN KEY (product_id) REFERENCES products(id)
```

That one screen tells you the columns and types, which ones are optional, the defaults, the unique rules, the valid values, which tables `products` points to, and which tables point at it. When you join to an unfamiliar table, `\d` it first.

Views and functions:

```sql
\dv
\df
\sf tasks_left
```

```text
           List of relations
 Schema |    Name    | Type |  Owner   
--------+------------+------+----------
 bazaar | open_tasks | view | postgres
(1 row)

                          List of functions
 Schema |    Name    | Result data type | Argument data types | Type 
--------+------------+------------------+---------------------+------
 bazaar | tasks_left | bigint           | p_owner text        | func
(1 row)

CREATE OR REPLACE FUNCTION bazaar.tasks_left(p_owner text)
 RETURNS bigint
 LANGUAGE sql
 STABLE
AS $function$
    SELECT count(*) FROM launch_checklist WHERE owner = p_owner AND NOT done
$function$
```

`\sf` prints the definition as a `CREATE OR REPLACE` statement you could run again. `\sf+` adds line numbers, which helps with error messages that say "line 2". `\ef tasks_left` opens that same text in your editor. When you save and quit, psql loads it into the query buffer, and a `;` (or `\g`) runs the `CREATE OR REPLACE`. It's the fastest way to tweak a function.

Extensions add features to a database (module 08 onwards uses several). A fresh database already has one:

```sql
\dx
```

```text
                 List of installed extensions
  Name   | Version |   Schema   |         Description          
---------+---------+------------+------------------------------
 plpgsql | 1.0     | pg_catalog | PL/pgSQL procedural language
(1 row)
```

`pg_stat_statements` is *preloaded* by the compose file but not yet *installed* in `bazaar`. Module 12 runs `CREATE EXTENSION` for it.

### 7.2 Patterns

Every `\d` command accepts a pattern. Patterns are psql's own mini-language, *not* SQL `LIKE`:

| Pattern | Matches |
|---|---|
| `products` | exactly `products` (case-insensitive unless quoted) |
| `prod*` | names starting with `prod` (`*` = any sequence) |
| `?` | any single character |
| `bazaar.*` | every object in schema `bazaar` |
| `*.products` | `products` in **any** schema, visible or not |
| `"MyTable"` | exact, case-sensitive |

Without a schema part, a pattern only matches objects that are **visible** through your `search_path`. With a schema part, the `search_path` doesn't matter. On the module 02 schema:

```sql
\dt bazaar.*
\dt *.*order*
```

```text
            List of relations
 Schema |    Name     | Type  |  Owner   
--------+-------------+-------+----------
 bazaar | order_items | table | postgres
 bazaar | orders      | table | postgres
(2 rows)
```

(the first command lists all seven core tables). Patterns work on system objects too, which makes psql a decent reference manual. "What size functions are there?":

```sql
\df pg_catalog.pg_size*
```

```text
                              List of functions
   Schema   |      Name      | Result data type | Argument data types | Type 
------------+----------------+------------------+---------------------+------
 pg_catalog | pg_size_bytes  | bigint           | text                | func
 pg_catalog | pg_size_pretty | text             | bigint              | func
 pg_catalog | pg_size_pretty | text             | numeric             | func
(3 rows)
```

Two rows for `pg_size_pretty`: functions can be **overloaded**, with the same name and different argument types. A function is identified by its name *and* argument types, so `\sf pg_size_pretty(numeric)` picks one.

> **Pitfall:** listing with a broad pattern (`\dt *`) is cheap, but `\d` on a broad pattern prints the full description of every table it matches, hundreds of them in a big database. Use `\dt pattern` to list first, then `\d name`.

> **Pitfall:** `\d` with a pattern that matches more than one kind of thing describes all of them. `\d bazaar.o*` on the module 02 schema describes the table `orders`, the sequence `orders_id_seq`, the index `orders_pkey` and the table `order_items`, along with its primary-key index.

---

## 8. Formatting output

How results look is purely a client-side matter. The server sends rows, and psql decides how to draw them. `\pset` controls this. A few settings have shortcuts.

### 8.1 Expanded display: `\x`

Wide rows wrap and become unreadable. **Expanded display** prints one line per column instead:

```sql
\x
SELECT * FROM launch_checklist WHERE id <= 2;
```

```text
Expanded display is on.
-[ RECORD 1 ]----------------
id    | 1
task  | Pick a database
owner | Ana
done  | t
due   | 2026-01-05
-[ RECORD 2 ]----------------
id    | 2
task  | Install PostgreSQL 17
owner | Ana
done  | t
due   | 2026-01-06
```

`\x` toggles it. **`\x auto`** is the setting you want permanently: normal tables when they fit the terminal, expanded when they don't. To expand a single query, end it with `\gx` instead of `;` (section 10).

### 8.2 Making NULL visible

By default, NULL prints as nothing, which looks exactly like an empty string:

```sql
\pset null '∅'
SELECT NULL AS a_null, '' AS empty_string;
```

```text
Null display is "∅".
 a_null | empty_string 
--------+--------------
 ∅      | 
(1 row)
```

Module 03 explains why the difference between NULL and `''` matters so much in SQL. A visible NULL marker saves real debugging time. Pick any symbol you like (`∅`, `¤`, `[null]`).

### 8.3 Borders, lines, formats

```sql
\pset border 2
\pset linestyle unicode
SELECT id, task FROM launch_checklist WHERE id <= 2;
```

```text
┌────┬───────────────────────┐
│ id │         task          │
├────┼───────────────────────┤
│  1 │ Pick a database       │
│  2 │ Install PostgreSQL 17 │
└────┴───────────────────────┘
(2 rows)
```

`border` is 0, 1 (default) or 2. `linestyle` is `ascii` (default) or `unicode`. The `format` option switches the whole output style:

| `\pset format` | Output |
|---|---|
| `aligned` | the default table |
| `unaligned` | values separated by `fieldsep` (default `\|`), no padding. Shortcut: `\a` |
| `wrapped` | like aligned, but wraps long values to the terminal width |
| `csv` | RFC 4180 CSV with proper quoting. Also `psql --csv` |
| `html`, `latex`, `asciidoc`, `troff-ms` | markup for documents |

```sql
\pset format csv
SELECT id, task, owner, due FROM launch_checklist;
```

```text
id,task,owner,due
1,Pick a database,Ana,2026-01-05
2,Install PostgreSQL 17,Ana,2026-01-06
3,Create bazaar database,Ben,2026-01-07
4,Write .psqlrc,Ben,∅
5,Design core schema,∅,2026-01-20
```

> **Pitfall:** look at the last two lines: the CSV contains `∅`, because the `null` display setting applies to every format. If you produce CSV for another program, reset `\pset null ''` first, or use `\copy` (section 12), which ignores display settings.

The HTML format is occasionally useful for pasting into a wiki:

```text
<table border="1">
  <tr>
    <th align="center">id</th>
    <th align="center">task</th>
  </tr>
  <tr valign="top">
    <td align="right">1</td>
    <td align="left">Pick a database</td>
  </tr>
  ...
```

More settings you'll use:

- `\t` (or `\pset tuples_only`) toggles printing **only the rows**, with no header and no `(N rows)` footer. It's great with `\a` for machine-readable output.
- `\pset footer off` hides just the `(N rows)` line.
- `\pset fieldsep '\t'` uses tab-separated values in unaligned mode.
- `\pset pager off` stops psql from piping long output through `less`. The pager is useful interactively, annoying in recordings. `\pset pager` on its own toggles it.
- `\pset` with no arguments shows every setting.

### 8.4 `\timing`

```sql
\timing on
SELECT count(*) FROM generate_series(1, 1000000);
```

```text
Timing is on.
  count  
---------
 1000000
(1 row)

Time: 245.310 ms
```

`\timing` measures from psql's side: it includes the network round trip and the time to receive the result, not only execution on the server. That's what your users experience, but it's not the server's own measurement. Module 12's `EXPLAIN ANALYZE` measures that.

---

## 9. Working with files and the editor

### 9.1 `\e`: edit the query in your editor

Long queries are miserable to edit on a prompt. **`\e`** opens the current query buffer, or the previous query if the buffer is empty, in your editor. When you save and quit, the text comes back into psql and runs if it ends with `;`. psql uses the `PSQL_EDITOR`, `EDITOR` or `VISUAL` environment variable. Set one of them in your shell profile: `export EDITOR=nano` (or `vim`, or `code --wait` for VS Code; `--wait` matters because psql must wait for the editor to close).

`\e file.sql` edits a file and then runs it. `\ef` and `\ev` edit functions and views (section 7.1).

### 9.2 `\i` and `\ir`: run a file

`\i path` reads a file and executes it as if you'd typed it, meta-commands included. `\ir path` does the same, but resolves a relative path **relative to the file that contains the `\ir`**, not to your current directory. Use `\ir` whenever one script includes another, so the scripts keep working wherever they're started from:

```text
/tmp/scripts/main.sql:          \ir lib/hello.sql
                                \i lib/hello.sql
/tmp/scripts/lib/hello.sql:     \echo hello from lib
```

Running `psql -f /tmp/scripts/main.sql` from `/`:

```text
hello from lib
psql:/tmp/scripts/main.sql:2: error: lib/hello.sql: No such file or directory
```

`\ir` found the file next to `main.sql`. `\i` looked in the current directory (`/`) and failed.

> **Pitfall:** with the course container, `\i` and `\ir` read files **inside the container**. Your `workspace/` is mounted there read-only at `/course/workspace`, so use `\i /course/workspace/01/script.sql`, or pipe the file through standard input: `./project/bin/psql.sh bazaar < workspace/01/script.sql`. A native psql reads your disk directly. The course's own files are at `/course/project` and `/course/modules` (read-only).

### 9.3 `\o` and `\w`: write output and queries to files

`\o file` sends all following query **results** to a file, and `\o` alone switches back to the screen. It's good for capturing a long report. `\o |command` pipes the results into a shell command instead. `\qecho text` writes a line to the `\o` destination, while `\echo` always goes to the screen. `\w file` writes the current query buffer (the query text, not results) to a file.

### 9.4 `\!`: run a shell command

`\! ls -l /tmp` runs a shell command from inside psql, without leaving the session. `\cd dir` changes psql's working directory, which affects relative paths in `\i`, `\o` and `\copy`.

---

## 10. Ways to run a query: `\g`, `\gx`, `\gset`, `\gexec`, `\watch`

A `;` sends the buffer and prints the result. The `\g` family sends the buffer and does something *else* with the result. Each one ends the statement: write it where you'd write the `;`.

### 10.1 `\g` and `\gx`

`\g` alone is the same as `;`. With an argument it redirects **this one** result:

```sql
SELECT id, task FROM launch_checklist WHERE done \g /tmp/done.txt
SELECT task FROM launch_checklist \g | sort
```

It also takes one-off `\pset` options in parentheses, without changing your settings:

```sql
SELECT id, task FROM launch_checklist WHERE done \g (format=csv tuples_only=on) /tmp/done.csv
```

```text
1,Pick a database
2,Install PostgreSQL 17
```

`\gx` is `\g` with expanded display forced on for this one query, perfect for "show me everything about this one row":

```sql
SELECT * FROM pg_stat_activity WHERE pid = pg_backend_pid() \gx
```

Related: `\gdesc` describes the columns a query *would* return, without running it:

```sql
SELECT id, task, due - date '2026-01-01' AS days_in, now() AS checked_at
FROM launch_checklist \gdesc
```

```text
   Column   |           Type           
------------+--------------------------
 id         | integer
 task       | text
 days_in    | integer
 checked_at | timestamp with time zone
(4 rows)
```

### 10.2 `\gset`: store results in variables

`\gset` runs the query and stores each column of the (single) result row in a **psql variable** named after the column (variables are explained in section 11):

```sql
SELECT count(*) AS open_count, max(due) AS last_due
FROM launch_checklist
WHERE NOT done \gset
\echo :open_count tasks open, last due :last_due
```

```text
3 tasks open, last due 2026-01-20
```

`\gset prefix_` prefixes every variable name, which avoids clashes. The query must return **exactly one row**:

```text
no rows returned for \gset
more than one row returned for \gset
```

> **Pitfall:** if a column is NULL, `\gset` **unsets** the variable rather than setting it to an empty string. A later `:var` is then left as literal text: `SELECT NULL AS z \gset` followed by `\echo [:z]` prints `[:z]`. Use `coalesce()` in the query if a value may be missing.

`\gset` is the bridge from SQL to psql logic. It's how a script decides what to do next based on what's in the database (section 11.3).

### 10.3 `\gexec`: run the statements a query generates

`\gexec` runs the query, then executes **each value of each row** as a SQL statement. Combined with the catalogs, it's a tiny code generator. Create three tables:

```sql
SELECT format('CREATE TABLE scratch_%s (id int)', g)
FROM generate_series(1, 3) AS g \gexec
```

```text
CREATE TABLE
CREATE TABLE
CREATE TABLE
```

And drop every table whose name starts with `scratch_` in schema `bazaar`:

```sql
SELECT format('DROP TABLE %I', relname)
FROM pg_class
WHERE relname LIKE 'scratch\_%'
  AND relkind = 'r'
  AND relnamespace = 'bazaar'::regnamespace
\gexec
```

```text
DROP TABLE
DROP TABLE
DROP TABLE
```

Details that matter:

- `format()` with **`%I`** quotes an *identifier* safely (double quotes when needed), and **`%L`** quotes a *literal* (single quotes, escaping embedded ones). Always build statements with these, never by gluing strings with `||`. A table named `Order Items` or a value like `O'Brien` would break a naive script.
- In `LIKE`, `_` matches any single character. `'scratch\_%'` escapes it so it matches a literal underscore. Without the backslash, `scratchy_notes` would match too.
- Each generated statement runs separately. Under the default autocommit, each one commits on its own (section 11.4).
- `CREATE DATABASE` can't run inside a `DO` block or a function, and has no `IF NOT EXISTS`. You need another way to run it only when the database is missing. That's what the project milestone asks for.

**Creating a database only if it doesn't exist.** The idea is to ask the catalog first and act on the answer. Using only tools from this module (`\gset` from 10.2, and `\if`, which section 11.3 covers in full):

```sql
SELECT count(*) = 0 AS need_db
FROM pg_database
WHERE datname = 'bazaar' \gset

\if :need_db
    CREATE DATABASE bazaar;
\endif
```

Line by line:

- `pg_database` is the catalog with one row per database (section 15). `WHERE datname = 'bazaar'` keeps only the row for `bazaar`, so `count(*)` is 1 if it exists and 0 if it doesn't.
- `count(*) = 0` turns that number into a boolean, `t` or `f`, and `AS need_db` names the column.
- `\gset` stores the column in the psql variable `need_db`.
- `\if :need_db` runs the lines up to `\endif` only when the variable is true.

The first run prints `CREATE DATABASE`. The second prints nothing and still succeeds, so the script is safe to run twice.

> **Under the hood:** you'll often see the same check written as a single query: `SELECT 'CREATE DATABASE bazaar' WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'bazaar') \gexec`. It returns the statement as one row when the database is missing and zero rows otherwise, so `\gexec` has either one statement or nothing to run. `EXISTS` and subqueries are covered in module 04.

### 10.4 `\watch`: re-run a query periodically

`\watch` re-executes the buffer every N seconds (default 2) until you press Ctrl-C. Options: `i=` interval, `c=` count (PostgreSQL 16+), `m=` stop when fewer than that many rows come back (17+).

```sql
SELECT now()::time(0) AS at, count(*) FILTER (WHERE NOT done) AS open
FROM launch_checklist \watch i=1 c=3
```

```text
Wed 14 Oct 2026 07:22:06 AM UTC (every 1s)

    at    | open 
----------+------
 07:22:06 |    3
(1 row)

Wed 14 Oct 2026 07:22:07 AM UTC (every 1s)

    at    | open 
----------+------
 07:22:07 |    3
(1 row)

Wed 14 Oct 2026 07:22:08 AM UTC (every 1s)
...
```

You'll use `\watch` to monitor long operations: an index build's progress (module 11), replication lag (module 19), a job queue draining (module 10).

### 10.5 Two more: `\crosstabview` and `\bind`

`\crosstabview` pivots a result: one column becomes the rows, another the column headers, a third the cells.

```sql
SELECT coalesce(owner, '(nobody)') AS owner,
       CASE WHEN done THEN 'done' ELSE 'open' END AS state,
       count(*)
FROM launch_checklist
GROUP BY 1, 2
ORDER BY 1 \crosstabview owner state count
```

```text
  owner   | done | open 
----------+------+------
 Ana      |    2 |     
 Ben      |      |    2
 (nobody) |      |    1
(3 rows)
```

`\bind` (PostgreSQL 16+) sends a query with **parameters** (`$1`, `$2`, ...) separately from its text, the way applications do (module 22):

```sql
SELECT task FROM launch_checklist WHERE owner = $1 ORDER BY id \bind 'Ana' \g
```

---

## 11. Variables and conditionals

psql has its own **variables**: named strings that live in the client. They aren't SQL variables, and the server never sees them. psql substitutes them into the text *before* sending it.

### 11.1 Setting and using variables

```sql
\set owner Ben
\echo :owner
```

```text
Ben
```

There are three ways to interpolate, and choosing the right one is the whole trick:

| Syntax | Becomes | Use for |
|---|---|---|
| `:var` | the raw text | numbers, keywords, SQL fragments |
| `:'var'` | a quoted SQL **literal**: `'Ben'` | values |
| `:"var"` | a quoted SQL **identifier**: `"launch_checklist"` | table and column names |

```sql
SELECT task FROM launch_checklist WHERE owner = :'owner';    -- WHERE owner = 'Ben'
\set tbl launch_checklist
SELECT count(*) FROM :"tbl";                                 -- FROM "launch_checklist"
\set limit 2
SELECT task FROM launch_checklist ORDER BY id LIMIT :limit;  -- LIMIT 2
```

Using the wrong form is a classic mistake:

```sql
SELECT task FROM launch_checklist WHERE owner = :owner;
```

```text
ERROR:  column "ben" does not exist
LINE 1: SELECT task FROM launch_checklist WHERE owner = Ben;
                                                        ^
```

Without quotes, `Ben` is an identifier, i.e. a column name (folded to lower case). `:'var'` also escapes properly: after `\set owner 'O''Brien'`, `:'owner'` becomes `'O''Brien'`, a correct literal. That's why you should use `:'var'` rather than writing `':var'` by hand. (Inside quotes, psql doesn't interpolate at all.)

Variables can be set from the command line with `-v name=value` (or `--set`), which is how shell scripts pass parameters to SQL scripts (section 14). `\unset name` removes one. `:{?name}` evaluates to `TRUE` or `FALSE` depending on whether `name` is set.

### 11.2 Special variables

`\set` without arguments lists all variables. Many are **special**: psql sets them, or reads them to control its behaviour.

| Variable | Meaning |
|---|---|
| `DBNAME`, `USER`, `HOST`, `PORT` | current connection |
| `SERVER_VERSION_NUM`, `VERSION_NUM` | server / psql version as numbers (`170011`) |
| `ERROR` | `true` if the last statement failed |
| `SQLSTATE` | error code of the last statement (`00000` = success) |
| `LAST_ERROR_MESSAGE`, `LAST_ERROR_SQLSTATE` | the most recent error, kept until the next one |
| `ROW_COUNT` | rows returned or affected by the last statement |
| `ON_ERROR_STOP`, `ON_ERROR_ROLLBACK`, `AUTOCOMMIT` | error and transaction behaviour (section 11.4) |
| `ECHO`, `ECHO_HIDDEN`, `QUIET`, `VERBOSITY` | what psql prints |
| `PROMPT1`, `PROMPT2`, `HISTFILE`, `HISTSIZE`, `HISTCONTROL`, `COMP_KEYWORD_CASE` | interactive comfort (section 13) |

```sql
UPDATE launch_checklist SET owner = owner WHERE owner = 'Ana';
\echo :ROW_COUNT
```

```text
UPDATE 2
2
```

`SQLSTATE` codes are standardised five-character codes: `42P01` is "undefined table", `23505` "unique violation", `40001` "serialization failure". Applications branch on these codes, never on message text, which can be translated. Module 10 retries on `40001`.

### 11.3 `\if`, `\elif`, `\else`, `\endif`

psql scripts can branch. The condition must be a value psql understands as a boolean: `true`/`false`, `on`/`off`, `yes`/`no`, `1`/`0` (any unambiguous prefix works, case-insensitive). The usual pattern is to compute the boolean in SQL, `\gset` it, and test the variable:

```sql
SELECT count(*) > 0 AS has_open FROM launch_checklist WHERE NOT done \gset
\if :has_open
    \echo 'There is still work to do.'
\else
    \echo 'Ready to launch!'
\endif
```

```text
There is still work to do.
```

```sql
SELECT to_regclass('bazaar.orders') IS NOT NULL AS has_orders \gset
\if :has_orders
    \echo orders table found
\elif :{?owner}
    \echo owner variable is set
\else
    \echo 'no orders table yet (it arrives in module 02)'
\endif
```

The `:{?owner}` test is how a script checks whether the caller passed a `-v owner=...`, so it can supply a default otherwise. `to_regclass()` returns NULL instead of raising an error when a table doesn't exist (section 15.4).

> **Pitfall:** `\if` doesn't evaluate expressions. `\if :count > 0` and `\if 7` both fail with `unrecognized value ... Boolean expected`, and the branch is treated as false. Do the comparison in SQL and `\gset` the boolean.

`\if` blocks also control meta-commands, so a script can skip whole sections, connect elsewhere, or stop. They're the backbone of robust setup scripts.

### 11.4 Errors and transactions: `ON_ERROR_STOP`, `ON_ERROR_ROLLBACK`, `AUTOCOMMIT`

**`AUTOCOMMIT`** (default `on`): each statement you send is its own transaction and commits immediately, unless you've started a transaction block with `BEGIN`. With `\set AUTOCOMMIT off`, psql silently issues a `BEGIN` before your first statement, and nothing is permanent until you `COMMIT`:

```text
bazaar=# \set AUTOCOMMIT off
bazaar=# UPDATE launch_checklist SET owner = 'Cai' WHERE id = 5;
UPDATE 1
bazaar=*# SELECT owner FROM launch_checklist WHERE id = 5;
 owner 
-------
 Cai
(1 row)

bazaar=*# ROLLBACK;
ROLLBACK
bazaar=# SELECT owner FROM launch_checklist WHERE id = 5;
 owner 
-------
 
(1 row)

bazaar=*# ROLLBACK;
```

Notice that the final `SELECT` started a new transaction too (`*`). Some people like `AUTOCOMMIT off` as a safety net when working on production data. The danger is forgetting an open transaction for hours, holding locks and blocking cleanup (module 16). If you use it, keep an eye on the `*`.

**What an error does inside a transaction.** In PostgreSQL, an error inside a transaction block **aborts the whole transaction**: every later statement is refused until `ROLLBACK` (section 5.2 showed the `!` prompt). That protects you from committing half of what you meant, but one typo while exploring at the prompt throws away all your work in the transaction.

**`ON_ERROR_ROLLBACK`** fixes that. When it's on, psql quietly sets a **savepoint** before every statement inside a transaction, and rolls back to it if the statement fails. Only the failed statement is undone:

```text
bazaar=# \set ON_ERROR_ROLLBACK interactive
bazaar=# BEGIN;
BEGIN
bazaar=*# UPDATE launch_checklist SET done = true WHERE id = 3;
UPDATE 1
bazaar=*# SELECT 1/0;
ERROR:  division by zero
bazaar=*# SELECT done FROM launch_checklist WHERE id = 3;
 done 
------
 t
(1 row)

bazaar=*# ROLLBACK;
```

The prompt stays `*`, not `!`. The values are `off` (default), `on`, and `interactive`, meaning only when you're typing, not in scripts run with `-f`. Use `interactive`: in a script, an error *should* abort the transaction, because the script's later steps assume the earlier ones succeeded.

**`ON_ERROR_STOP`** (default `off`) controls what happens to the *script* after an error. By default psql reports the error and **carries on with the next statement**. That's almost never what you want in a script. Take this file:

```sql
INSERT INTO launch_checklist (task) VALUES ('Step A');
INSERT INTO launch_checklist (task) VALUES (NULL);      -- violates NOT NULL
INSERT INTO launch_checklist (task) VALUES ('Step C');
```

| Run as | Rows inserted | Exit status |
|---|---|---|
| `psql -f bad.sql` | `Step A`, `Step C` | **0** |
| `psql -v ON_ERROR_STOP=1 -f bad.sql` | `Step A` | 3 |
| `psql -1 -v ON_ERROR_STOP=1 -f bad.sql` | none | 3 |

The first run reports an error but exits with 0 ("success"), so a deployment pipeline would happily continue. With `ON_ERROR_STOP`, psql stops at the first error and exits with status 3. Adding **`-1`** (`--single-transaction`) wraps the whole file in one transaction, so the script either fully succeeds or changes nothing.

> **In production:** run every SQL script with `-v ON_ERROR_STOP=1`, and usually with `-1`. Many incidents start with a migration that failed halfway and reported success.

**Seeing the details of an error.** `\errverbose` re-prints the most recent error with every field the server sent:

```sql
SELECT 1 FROM nosuchtable;
\errverbose
```

```text
ERROR:  relation "nosuchtable" does not exist
LINE 1: SELECT 1 FROM nosuchtable;
                      ^
ERROR:  42P01: relation "nosuchtable" does not exist
LINE 1: SELECT 1 FROM nosuchtable;
                      ^
LOCATION:  parserOpenTable, parse_relation.c:1449
```

The second message adds the SQLSTATE code and the location in PostgreSQL's source code. `\set VERBOSITY verbose` makes every error print that way. Errors from functions and triggers also carry `DETAIL`, `HINT` and `CONTEXT` lines, and `\errverbose` shows them all.

---

## 12. `\copy` versus `COPY`

`COPY` is the SQL command for bulk loading and unloading data (module 03 uses it to load the seed data). It's very fast, far faster than individual `INSERT`s. The question is always: **whose filesystem?**

- **`COPY table TO '/path'`** runs **on the server**. The server process opens `/path` on the **server's** filesystem. For you that's inside the container, as the operating-system user `postgres`.
- **`\copy table TO 'path'`** is a psql meta-command. psql runs `COPY ... TO STDOUT` on the server, receives the data over the connection, and writes it to `path` on **your** machine (wherever psql runs).

From a native psql on the host, connected to the course container:

```sql
COPY (SELECT 'hi' AS greeting) TO '/tmp/greeting.csv' WITH (FORMAT csv, HEADER);
\copy (SELECT 'hi' AS greeting) TO 'greeting.csv' WITH (FORMAT csv, HEADER)
```

```bash
ls -l /tmp/greeting.csv greeting.csv
docker exec course-pg ls -l /tmp/greeting.csv
```

```text
ls: cannot access '/tmp/greeting.csv': No such file or directory
-rw-r--r-- 1 root root 12 Oct 14 07:25 greeting.csv
-rw-r--r-- 1 postgres postgres 12 Oct 14 07:25 /tmp/greeting.csv
```

The server-side file was written inside the container, owned by `postgres`. Your laptop's `/tmp` doesn't have it. The `\copy` file landed in your current directory.

Server-side file access is also a privilege. Reading and writing arbitrary server files is a security risk, so it's restricted to superusers and members of `pg_read_server_files`/`pg_write_server_files`. An ordinary role gets this message, which names the solution itself:

```text
ERROR:  permission denied to COPY to a file
DETAIL:  Only roles with privileges of the "pg_write_server_files" role may COPY to a file.
HINT:  Anyone can COPY to stdout or from stdin. psql's \copy command also works for anyone.
```

The rule of thumb: **use `\copy` for files on your machine, and `COPY` for files that live on the database server** (like the course's mounted `/course/project` directory). Both accept the same options, and both accept a query instead of a table:

```sql
\copy (SELECT task, owner FROM launch_checklist WHERE NOT done ORDER BY id) TO 'open.csv' WITH (FORMAT csv, HEADER)
\copy launch_checklist (task, owner) FROM 'more_tasks.csv' WITH (FORMAT csv, HEADER)
```

> **Pitfall:** `\copy` is a meta-command, so it must fit on **one line** and doesn't end with `;`. A multi-line `\copy (SELECT ...)` is a syntax error. Put long queries in a view, or use `COPY (...) TO STDOUT` with `\g file`.

`\copy` writes NULL as an empty field in CSV, whatever `\pset null` says. That's the difference from `\pset format csv` noted in section 8.3.

---

## 13. History and `~/.psqlrc`

### 13.1 History

psql keeps a command history, by default in `~/.psql_history` (one file for all databases). Up/Down browse it, and **Ctrl-R** searches backwards, as in bash. `\s` prints the history, and `\s file` saves it to a file. Three variables tune it: `HISTSIZE` (number of entries, default 500), `HISTCONTROL` (`ignorespace`, `ignoredups` or `ignoreboth`), and `HISTFILE` (location). With `ignorespace`, a command starting with a space isn't recorded, which is useful when you type a password into an `ALTER ROLE`.

> **Pitfall:** the container's psql writes history *inside the container*, so `docker compose down` loses it. That's one more reason to use a native psql for daily work.

### 13.2 `~/.psqlrc`

At start-up, psql runs the commands in `~/.psqlrc`. For version-specific settings, a `~/.psqlrc-17` file (or one for an exact minor version, such as `~/.psqlrc-17.6`) is read *instead of* `~/.psqlrc` when it exists. It's where you make the interactive settings from this module permanent. Here's a well-commented starting point. It's also the reference solution for this module's project (`solutions/psqlrc`):

```sql
-- Don't print "Null display is ..." etc. while this file runs.
\set QUIET on

-- Make NULL visible: an empty cell could be NULL or an empty string ''.
\pset null '∅'

-- Switch to the vertical (expanded) layout only when a row is too wide.
\x auto

-- Print how long each statement took (client-measured, includes network).
\timing on

-- Box-drawing characters instead of ASCII dashes and pipes.
\pset linestyle unicode

-- Inside an explicit transaction, a typo no longer aborts the transaction.
-- "interactive" = only when typing at the prompt, never in scripts (-f).
\set ON_ERROR_ROLLBACK interactive

-- Tab completion upper-cases keywords (select<TAB> -> SELECT).
\set COMP_KEYWORD_CASE upper

-- History: big, de-duplicated, one file per database.
\set HISTSIZE 10000
\set HISTCONTROL ignoredups
\set HISTFILE ~/.psql_history- :DBNAME

-- Prompt: user@host:port/database, then the usual state markers.
\set PROMPT1 '%n@%M:%>/%/%R%x%# '
\set PROMPT2 '%n@%M:%>/%/%R%x%# '

-- Saved queries: type :activity or :settings at the prompt.
\set activity 'SELECT pid, usename, datname, state, wait_event_type, now() - query_start AS running_for, left(query, 60) AS query FROM pg_stat_activity WHERE backend_type = ''client backend'' ORDER BY query_start;'
\set settings 'SELECT name, setting, unit, source FROM pg_settings WHERE source NOT IN (''default'', ''override'') ORDER BY name;'

\set QUIET off
```

Notes on the less obvious lines:

- `\set HISTFILE ~/.psql_history- :DBNAME`: `\set` concatenates its arguments, so connected to `bazaar` this becomes `~/.psql_history-bazaar`. History is kept per database, so Up-arrow in `bazaar` doesn't offer the commands you ran in another project.
- Prompt escapes: `%n` user, `%M` host (`[local]` for a socket), `%>` port, `%/` database, then the `%R%x%#` markers from section 5.2. Seeing the host in the prompt is a real safety feature once you have a production server alongside your laptop. Some people go further and colour the prompt red on production hosts.
- `:activity` and `:settings` show a neat trick: a variable holding a whole query, including its `;`. Typing `:activity` at the prompt interpolates it and runs it. Embedded single quotes are doubled (`''`) inside the `\set` value.
- `ON_ERROR_STOP` is deliberately **not** set here. Scripts should set it themselves (with `-v ON_ERROR_STOP=1` or `\set ON_ERROR_STOP on` at the top), rather than depending on whoever runs them having the right `.psqlrc`.

With this file, a session looks like this:

```text
postgres@[local]:5432/bazaar=# SELECT NULL AS nothing, 1 AS one;
 nothing │ one 
─────────┼─────
 ∅       │   1
(1 row)

Time: 0.937 ms
postgres@[local]:5432/bazaar=# BEGIN;
BEGIN
Time: 0.238 ms
postgres@[local]:5432/bazaar=*# SELEC 1;
ERROR:  syntax error at or near "SELEC"
LINE 1: SELEC 1;
        ^
Time: 0.303 ms
postgres@[local]:5432/bazaar=*# SELECT 2;
 ?column? 
──────────
        2
(1 row)

Time: 0.192 ms
postgres@[local]:5432/bazaar=*# COMMIT;
COMMIT
```

Note the typo inside the transaction: thanks to `ON_ERROR_ROLLBACK`, the prompt stays `*` and the `COMMIT` really commits.

> **Pitfall:** `.psqlrc` also runs for `psql -c` and `psql -f`, and its output pollutes the results. With the file above, `psql -Atc 'SELECT 1'` prints `1` **and** `Time: 0.204 ms`, which breaks a shell script that captures the output. Scripts must use **`-X`** (`--no-psqlrc`) so they behave the same for everyone.

Where it lives: `~/.psqlrc` on your machine is read by your **native** psql. The container's psql (via `psql.sh`) runs as `root` inside the container and reads `/root/.psqlrc` there. You can copy yours in with `docker cp ~/.psqlrc course-pg:/root/.psqlrc`, but it's lost when the container is recreated. You can also point psql at a file with the `PSQLRC` environment variable.

---

## 14. Scripting psql from the shell

psql is as useful in shell scripts as at the prompt. The options that matter:

| Option | Effect |
|---|---|
| `-c 'SQL'` | run this command and exit (repeatable: `-c ... -c ...`) |
| `-f file` | run this file and exit (`-f -` reads stdin) |
| `-X` | don't read `.psqlrc` |
| `-A` | unaligned output (no padding) |
| `-t` | tuples only (no header, no footer) |
| `-F sep` | field separator for `-A` (default `\|`) |
| `--csv` | CSV output |
| `-v name=value` | set a psql variable |
| `-1` | run everything in one transaction (with `-f`) |
| `-q` | quiet: no `CREATE TABLE`/`INSERT 0 1` status lines |
| `-e` / `-E` | echo queries sent / echo the hidden queries behind `\d` commands (section 15.3) |
| `-l` | list databases and exit |

`-XAt` together is the standard incantation for "give me just the value":

```bash
open=$(docker exec course-pg psql -XAt -U postgres -d bazaar \
       -c "SELECT count(*) FROM launch_checklist WHERE NOT done")
echo "open tasks: $open"
```

```text
open tasks: 3
```

Multiple columns, read into shell variables, with a tab as separator:

```bash
docker exec course-pg psql -XAt -F $'\t' -U postgres -d bazaar \
    -c "SELECT owner, count(*) FROM launch_checklist WHERE owner IS NOT NULL GROUP BY owner ORDER BY owner" |
while IFS=$'\t' read -r owner n; do
    echo "$owner has $n task(s)"
done
```

```text
Ana has 2 task(s)
Ben has 2 task(s)
```

Passing parameters: `-v` sets a variable, and the script interpolates it safely with `:'var'`. A heredoc keeps everything in one place:

```bash
docker exec -i course-pg psql -X -U postgres -d bazaar -v ON_ERROR_STOP=1 -v owner=Ben <<'SQL'
SELECT task FROM launch_checklist WHERE owner = :'owner' ORDER BY id;
SQL
```

```text
          task          
------------------------
 Create bazaar database
 Write .psqlrc
(2 rows)
```

(Quote the heredoc marker, `<<'SQL'`, so the *shell* doesn't try to expand anything inside. psql does its own expansion.)

> **Pitfall:** variables are **not** interpolated in `-c` strings. `psql -v x=42 -c 'SELECT :x'` fails with `syntax error at or near ":"`. The `-c` text goes to the server untouched. Use stdin or `-f` when you need variables.

**Exit status**, which scripts should always check:

| Status | Meaning |
|---|---|
| 0 | success (but see `ON_ERROR_STOP`: without it, a script with errors still returns 0) |
| 1 | a fatal psql error, such as a failed `-c` command or an unreadable file |
| 2 | the connection failed or was lost |
| 3 | an error in a script while `ON_ERROR_STOP` was set |

```bash
psql -X -d nosuchdb -c 'SELECT 1'; echo "exit=$?"
```

```text
psql: error: connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed: FATAL:  database "nosuchdb" does not exist
exit=2
```

For data, prefer **`--csv`** to `-A -F,`. CSV quotes values that contain the separator, and `-A` doesn't:

```bash
docker exec course-pg psql -X --csv -U postgres -c \
  "SELECT name, setting FROM pg_settings WHERE name = 'shared_preload_libraries'"
```

```text
name,setting
shared_preload_libraries,"pg_stat_statements,auto_explain"
```

> **Pitfall:** `docker exec -i ... psql` inside a `while read` loop **eats the loop's input**. `-i` connects the container's stdin to the loop's stdin, and the first iteration swallows all the remaining lines. Add `< /dev/null` to the inner command (or drop `-i` when you don't feed it a file). Exercise 12 makes you hit this one.

---

## 15. The system catalogs and `information_schema`

Everything PostgreSQL knows about your objects (tables, columns, types, functions, constraints, roles, settings) is stored **in tables**, the **system catalogs** in schema `pg_catalog`. You can query them with ordinary SQL. Every `\d` command is just a catalog query that psql writes for you.

### 15.1 The three you need first

**`pg_namespace`**: one row per schema.

```sql
SELECT oid, nspname FROM pg_namespace ORDER BY oid;
```

```text
  oid  |      nspname       
-------+--------------------
    11 | pg_catalog
    99 | pg_toast
  2200 | public
 13283 | information_schema
 16385 | bazaar
(5 rows)
```

**`pg_class`**: one row per *relation*, meaning anything with columns or stored like a table: tables, indexes, sequences, views, materialized views. `relnamespace` points to `pg_namespace.oid`, and `relkind` says what kind of relation it is:

| `relkind` | Kind |
|---|---|
| `r` | ordinary table |
| `p` | partitioned table (module 14) |
| `i` / `I` | index / partitioned index |
| `S` | sequence |
| `v` / `m` | view / materialized view |
| `t` | TOAST table |
| `f` | foreign table (module 21) |

```sql
SELECT c.relname, c.relkind, n.nspname
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'bazaar'
ORDER BY c.relname;
```

```text
         relname         | relkind | nspname 
-------------------------+---------+---------
 launch_checklist        | r       | bazaar
 launch_checklist_id_seq | S       | bazaar
 launch_checklist_pkey   | i       | bazaar
 open_tasks              | v       | bazaar
(4 rows)
```

Our one `CREATE TABLE` produced three relations: the table, the sequence behind the identity column, and the primary-key index.

**`pg_attribute`**: one row per column of every relation. `attrelid` points to `pg_class.oid`, and `attnum` is the column's position:

```sql
SELECT attnum, attname, atttypid::regtype, attnotnull
FROM pg_attribute
WHERE attrelid = 'launch_checklist'::regclass
ORDER BY attnum;
```

```text
 attnum | attname  | atttypid | attnotnull 
--------+----------+----------+------------
     -6 | tableoid | oid      | t
     -5 | cmax     | cid      | t
     -4 | xmax     | xid      | t
     -3 | cmin     | cid      | t
     -2 | xmin     | xid      | t
     -1 | ctid     | tid      | t
      1 | id       | integer  | t
      2 | task     | text     | t
      3 | owner    | text     | f
      4 | done     | boolean  | t
      5 | due      | date     | f
(11 rows)
```

Surprise: six extra columns with negative numbers. These are **system columns** that every table row carries: `ctid` is the row's physical location, and `xmin`/`xmax` record which transactions created and deleted it. They're the heart of MVCC (modules 10 and 16). Real queries on `pg_attribute` filter `attnum > 0 AND NOT attisdropped`. A dropped column leaves a placeholder row behind until the table is rewritten.

Other catalogs you'll meet: `pg_type` (data types), `pg_proc` (functions), `pg_constraint`, `pg_index`, `pg_attrdef` (column defaults), `pg_roles`, `pg_database`, `pg_settings`, and the statistics views `pg_stat_*`. `\dS` lists them all. There are 142 tables and views in `pg_catalog` alone, so learn them as you need them.

### 15.2 OIDs and the `reg*` casts

Catalogs link to each other by OID. Joining on OIDs all the time is tedious, so PostgreSQL has **OID alias types** that convert between names and OIDs:

```sql
SELECT 'launch_checklist'::regclass,
       'launch_checklist'::regclass::oid,
       'bazaar'::regnamespace::oid,
       'int4'::regtype,
       'pg_size_pretty(bigint)'::regprocedure;
```

```text
     regclass     |  oid  |  oid  | regtype |      regprocedure      
------------------+-------+-------+---------+------------------------
 launch_checklist | 16414 | 16385 | integer | pg_size_pretty(bigint)
(1 row)
```

`'name'::regclass` resolves a relation name **using the search_path**, and errors if it doesn't exist. Casting an OID column to `regclass`/`regtype`/`regnamespace` *displays* it as a name. That's why `atttypid::regtype` above printed `integer` instead of `23`. In `WHERE attrelid = 'launch_checklist'::regclass`, the cast does the lookup for you, with no join to `pg_class` needed. (Your OIDs will differ. Never hard-code them.)

### 15.3 How `\d` is built: `psql -E`

Want to know which catalog query psql runs for a meta-command? Start psql with **`-E`** (or `\set ECHO_HIDDEN on` in a session), and it prints every hidden query before running it:

```bash
docker exec course-pg psql -X -E -U postgres -d bazaar -c '\dt'
```

```text
/******** QUERY *********/
SELECT n.nspname as "Schema",
  c.relname as "Name",
  CASE c.relkind WHEN 'r' THEN 'table' WHEN 'v' THEN 'view' WHEN 'm' THEN 'materialized view' WHEN 'i' THEN 'index' WHEN 'S' THEN 'sequence' WHEN 't' THEN 'TOAST table' WHEN 'f' THEN 'foreign table' WHEN 'p' THEN 'partitioned table' WHEN 'I' THEN 'partitioned index' END as "Type",
  pg_catalog.pg_get_userbyid(c.relowner) as "Owner"
FROM pg_catalog.pg_class c
     LEFT JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
     LEFT JOIN pg_catalog.pg_am am ON am.oid = c.relam
WHERE c.relkind IN ('r','p','')
      AND n.nspname <> 'pg_catalog'
      AND n.nspname !~ '^pg_toast'
      AND n.nspname <> 'information_schema'
  AND pg_catalog.pg_table_is_visible(c.oid)
ORDER BY 1,2;
/************************/

              List of relations
 Schema |       Name       | Type  |  Owner   
--------+------------------+-------+----------
 bazaar | launch_checklist | table | postgres
(1 row)
```

Read it and you learn exactly what `\dt` means: relations of kind `r` or `p`, excluding system schemas, and only those **visible** through the `search_path` (`pg_table_is_visible`). The query also explains the version problem from module 00: psql builds these queries for the catalog layout of the server version it knows about. `psql -E` is the best way to learn the catalogs. Find the meta-command that shows what you want, steal its query, and adapt it. Exercise 7 does exactly that.

Two helper functions appear constantly in these queries: `pg_get_userbyid(oid)` (role name) and the `pg_get_*def` family (`pg_get_constraintdef`, `pg_get_indexdef`, `pg_get_viewdef`, `pg_get_functiondef`), which turn catalog entries back into SQL text.

### 15.4 `information_schema`

`information_schema` is a set of **views** defined by the SQL standard. The same queries work, more or less, in MySQL, SQL Server and others:

```sql
SELECT column_name, data_type, is_nullable, column_default, is_identity
FROM information_schema.columns
WHERE table_schema = 'bazaar' AND table_name = 'launch_checklist'
ORDER BY ordinal_position;
```

```text
 column_name | data_type | is_nullable | column_default | is_identity 
-------------+-----------+-------------+----------------+-------------
 id          | integer   | NO          |                | YES
 task        | text      | NO          |                | NO
 owner       | text      | YES         |                | NO
 done        | boolean   | NO          | false          | NO
 due         | date      | YES         |                | NO
(5 rows)
```

When to use which:

| | `information_schema` | `pg_catalog` |
|---|---|---|
| Portable | yes | PostgreSQL only |
| Covers PostgreSQL-specific things (indexes, `relkind`, storage, extensions, ...) | no | yes |
| Shows objects you have **no privileges on** | no, it filters them out | yes |
| Speed on big schemas | slower (complex views) | fast |
| Values | `'YES'`/`'NO'` strings, standard type names | booleans, OIDs |

Use `information_schema` in code that must be portable, or when you want "what can I access?" semantics. Use `pg_catalog` for everything PostgreSQL-specific, for admin scripts, and when speed matters. Tools like psql and ORMs mostly use `pg_catalog`.

Finally, the "does it exist?" helpers. `to_regclass('name')`, `to_regtype`, `to_regproc` and friends work like the `::regclass` casts, but return NULL instead of raising an error when the object doesn't exist:

```sql
SELECT to_regclass('nosuch');      -- NULL, no error
SELECT 'nosuch'::regclass;         -- ERROR:  relation "nosuch" does not exist
```

That makes them ideal in `\gset`/`\if` scripts, as in section 11.3.

---

## 16. Getting help

You don't need to memorise anything in this module. You need to know where to look:

- **`\?`** lists every meta-command, grouped by topic. `\? variables` documents every special variable, and `\? options` every command-line option.
- **`\h`** lists every SQL command. `\h CREATE SCHEMA` shows its syntax:

```text
Command:     CREATE SCHEMA
Description: define a new schema
Syntax:
CREATE SCHEMA schema_name [ AUTHORIZATION role_specification ] [ schema_element [ ... ] ]
CREATE SCHEMA AUTHORIZATION role_specification [ schema_element [ ... ] ]
CREATE SCHEMA IF NOT EXISTS schema_name [ AUTHORIZATION role_specification ]
CREATE SCHEMA IF NOT EXISTS AUTHORIZATION role_specification

where role_specification can be:

    user_name
  | CURRENT_ROLE
  | CURRENT_USER
  | SESSION_USER

URL: https://www.postgresql.org/docs/17/sql-createschema.html
```

- **`psql -E`** shows how psql itself queries the catalogs (section 15.3).
- **Tab completion** knows the syntax: `ALTER TABLE launch_checklist <TAB><TAB>` lists what can come next.
- **`\df pattern`** with a description column (`\df+`) is a searchable reference for built-in functions.

> **PG 18:** psql 18 adds meta-commands for the protocol's pipeline mode (`\startpipeline`, `\syncpipeline`, `\endpipeline` and friends) and a few smaller conveniences. Nothing in this module changes. Everything here works the same with psql 18 against a PostgreSQL 17 server.

---

## Summary

- PostgreSQL is client/server. The postmaster forks one **backend process per connection**. Queries, and file paths in SQL, run on the server.
- A **cluster** (one server) holds **databases**, which hold **schemas**, which hold **objects**. A connection sees exactly one database, while schemas can be mixed freely in one query. Roles and the database list are cluster-wide.
- `CREATE DATABASE` copies **`template1`**. **`template0`** is the untouched original, and **`postgres`** is a maintenance database.
- **`search_path`** resolves unqualified names. The first matching schema wins, `pg_catalog` comes implicitly first, and new objects go to the first schema that exists. Set it per database or role with `ALTER ... SET`, which only affects new sessions.
- Roles are users and groups in one concept. Superusers bypass all checks, and "does not exist" can mean "no permission to see it".
- psql meta-commands: `\d` family with `+`, `S` and patterns (`bazaar.*`); `\x auto`, `\pset null`, `\timing`; `\e`, `\i`/`\ir`, `\o`; `\g`, `\gx`, `\gset`, `\gexec`, `\watch`.
- psql variables are client-side text. Interpolate with `:var`, `:'var'` (literal) or `:"var"` (identifier), and branch with `\if` on booleans computed in SQL.
- Scripts: always `-X` and `-v ON_ERROR_STOP=1`, usually `-1`. Check the exit status. At the prompt, `ON_ERROR_ROLLBACK interactive` saves your transaction from typos.
- `\copy` reads and writes files on the **client**, `COPY` on the **server**.
- `~/.psqlrc` makes interactive psql pleasant. It must never be relied on by scripts.
- The catalogs (`pg_class`, `pg_namespace`, `pg_attribute`, ...) describe everything. `psql -E` reveals the query behind any `\d` command. `information_schema` is the portable, privilege-filtered alternative.

## What's next

You know where things live and how to look at them. In [Module 02](../02-types-and-ddl/) you fill the `bazaar` schema with real tables, choosing data types and constraints for users, sellers, products and orders.
