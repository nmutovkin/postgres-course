# Module 15: Security and Row-Level Security

Bazaar is about to open its **seller portal**: two thousand shops will log in to manage their listings and ship their orders, and the analytics team wants a read-only connection for dashboards. So far every example in this course has connected as `postgres`, a superuser for whom permission checks don't exist. That has to stop. The application must not be able to drop tables. An analyst must not be able to export every customer's e-mail address. Seller 42 must never see seller 43's orders, even if someone forgets a `WHERE` clause in the portal's code. And none of this may depend on the application always behaving correctly. This module makes the database itself enforce these rules: roles and privileges for *what* each component may touch, row-level security for *which rows*, and authentication and TLS for *who* gets in at all.

**What you'll learn**

- How to think about database security: who connects, what each of them can do, and how far the damage goes when one of them is compromised.
- Roles in depth: attributes (`LOGIN`, `SUPERUSER`, `CREATEROLE`, `CREATEDB`, `REPLICATION`, `BYPASSRLS`), membership, `INHERIT`, `SET ROLE`, the `ADMIN`/`INHERIT`/`SET` grant options of PostgreSQL 16, and the predefined `pg_*` roles (including `pg_maintain`, new in 17).
- Object ownership, and why the application must never own its tables.
- The privilege system: database, schema, table, column, sequence and function privileges, the `PUBLIC` pseudo-role, the PostgreSQL 15 change to the `public` schema, and how to read ACLs (`\dp`, `aclexplode`, `has_*_privilege`).
- `ALTER DEFAULT PRIVILEGES`, and the subtlety that trips almost everyone (defaults belong to the *creating* role).
- A least-privilege role layout for Bazaar, as in the reference `1501_schema_security.sql`.
- `SECURITY DEFINER` functions as controlled privilege escalation, the `search_path` hijack, and how to write them safely.
- Views as a security boundary: `security_barrier` and `security_invoker`.
- Row-level security: `ENABLE` vs `FORCE`, `USING` vs `WITH CHECK`, permissive vs restrictive policies, multi-tenant RLS with `SET LOCAL app.seller_id`, its performance (leakproof functions, indexes) and its pitfalls (foreign keys, unique constraints, side channels).
- Authentication: `pg_hba.conf` line by line, `scram-sha-256` vs `md5`, client certificates; and TLS with `sslmode=verify-full`, in a hands-on lab.
- Briefly: audit logging with pgaudit, encryption at rest and pgcrypto, secrets handling, and per-role limits.

**Before you start**

This module needs very little data volume, and the small dataset is the one documented row by row in `project/db/NOTES.md`. So most examples run on the **schema of state 15 with the small dataset**, built by a helper script in this module's lab:

```bash
./modules/15-security-rls/lab/small15.sh     # = reset.sh 11 + the schema files of modules 11-14 (about 10 s)
./project/bin/psql.sh
```

`./project/bin/reset.sh 15` gives you the same schema with the large dataset. Use it for section 11 (performance), where volume matters; numbers there come from the large dataset. Everything else works on either, but the counts printed in this module come from the small one.

From section 6 on, the examples assume the reference roles and policies are in place:

```bash
./modules/15-security-rls/lab/small15.sh --with-1501     # state 16 on small data
```

Two things are different from earlier modules:

- **Roles are cluster-wide.** `reset.sh` drops and recreates the `bazaar` *database*, but roles live in the cluster and survive. The reference file is written to be re-run, and [`lab/README.md`](lab/README.md) shows how to remove the Bazaar roles completely.
- **You'll connect as different roles.** Inside the course container, local connections are trusted (section 13 explains why), so `\c - bazaar_seller` in psql switches user without a password. From your laptop, use the passwords in `project/db/NOTES.md`.

Sections 13 and 14 use a separate Docker Compose lab in [`lab/tls/`](lab/tls/).

---

## 1. The threat model

Security work starts with a list of who talks to the database and what could go wrong, not with `GRANT` statements. For Bazaar:

| Who connects | Why | What happens if it is compromised or buggy |
|---|---|---|
| **The web application** (`bazaar_app`) | Every checkout, page view and search | SQL injection or a stolen password gives the attacker whatever the app's role can do. If the app owns the tables, that includes `DROP TABLE` and disabling triggers. |
| **The seller portal** (`bazaar_seller`) | 2,000 shops managing their own products and orders | A bug that forgets `WHERE seller_id = ...` shows one shop another shop's orders: a data breach. |
| **Analysts and BI tools** (`bazaar_analyst`) | Dashboards, ad-hoc SQL | Read access is still access: an export of `users.email` is a reportable leak. |
| **Migrations and deploys** | Schema changes | They need DDL rights, but only during the deploy. |
| **Operators and on-call** | Incidents, maintenance | They need powerful rights, sometimes. Every superuser session is an unlimited blast radius. |
| **Anyone on the network** | | Without TLS, passwords (with weak methods) and all data travel in clear text. Without strict `pg_hba.conf`, anyone who can reach port 5432 can try to log in. |

The goal is **least privilege**: every component gets exactly what it needs and nothing more, so that a compromise of one component has a small **blast radius**. PostgreSQL gives you a series of checkpoints, and a request must pass all of them:

```text
 client ──► network (firewall, listen_addresses)
        ──► pg_hba.conf: is this user allowed to connect to this database from this address, and how must it prove its identity?   (section 13)
        ──► authentication: password (SCRAM), certificate, peer, ...                                                               (sections 13, 14)
        ──► CONNECT privilege on the database                                                                                      (section 4)
        ──► USAGE privilege on the schema                                                                                          (section 4)
        ──► privilege on the table / column / sequence / function                                                                  (section 4)
        ──► row-level security policies: which rows                                                                                (sections 9 to 12)
```

**Superusers skip every check after authentication.** That's why the first rule of PostgreSQL security is that nothing but a human administrator, in an emergency, connects as a superuser. Up to now, the whole course has broken that rule.

> **In production:** the database is the last line of defence, not the only one. RLS doesn't excuse missing authorization checks in the application, and TLS doesn't excuse exposing port 5432 to the internet. Layers matter because each one fails sometimes.

---

## 2. Roles

### 2.1 Users are roles that can log in

Module 01 introduced roles briefly. PostgreSQL has one concept for users and groups: a **role**. `CREATE USER x` is just `CREATE ROLE x LOGIN`. Roles are **cluster-wide**: they live in the shared catalog `pg_authid`, not in any database, so the same role exists in every database of the cluster. What it may do is granted per database and per object.

A role has **attributes**, which are properties of the role itself rather than privileges on objects:

| Attribute | Meaning |
|---|---|
| `LOGIN` | May be used to open a session. Without it the role is a group or a "privilege bundle". |
| `SUPERUSER` | Bypasses all permission checks, including RLS. Can do anything, including reading server files and running programs as the OS user `postgres`. |
| `CREATEDB` | May create databases. |
| `CREATEROLE` | May create roles, and manage the roles it has `ADMIN` on (section 2.3). |
| `REPLICATION` | May connect in replication mode and stream WAL (Module 19). A replication connection can read the entire cluster's data. |
| `BYPASSRLS` | Row-level security policies don't apply to this role. |
| `INHERIT` / `NOINHERIT` | Default for whether the role automatically uses the privileges of roles it's a member of (section 2.2). |
| `CONNECTION LIMIT n` | At most `n` concurrent sessions (`-1`, the default, means no limit). |
| `PASSWORD '...'`, `VALID UNTIL '...'` | The password (stored hashed) and its expiry. |

After `small15.sh --with-1501`, psql's `\du` lists Bazaar's roles:

```sql
\du
```

```text
                                List of roles
    Role name    |                         Attributes                         
-----------------+------------------------------------------------------------
 bazaar_analyst  | 
 bazaar_app      | 50 connections
 bazaar_owner    | Cannot login
 bazaar_readonly | Cannot login
 bazaar_seller   | 
 postgres        | Superuser, Create role, Create DB, Replication, Bypass RLS
```

An empty attribute list means "an ordinary role that can log in". The same information comes from `pg_roles` (a view over `pg_authid` that hides the password hash):

```sql
SELECT rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb,
       rolcanlogin, rolreplication, rolbypassrls, rolconnlimit
FROM pg_roles
WHERE rolname !~ '^pg_'
ORDER BY 1;
```

```text
     rolname     | rolsuper | rolinherit | rolcreaterole | rolcreatedb | rolcanlogin | rolreplication | rolbypassrls | rolconnlimit 
-----------------+----------+------------+---------------+-------------+-------------+----------------+--------------+--------------
 bazaar_analyst  | f        | t          | f             | f           | t           | f              | f            |           -1
 bazaar_app      | f        | t          | f             | f           | t           | f              | f            |           50
 bazaar_owner    | f        | t          | f             | f           | f           | f              | f            |           -1
 bazaar_readonly | f        | t          | f             | f           | f           | f              | f            |           -1
 bazaar_seller   | f        | t          | f             | f           | t           | f              | f            |           -1
 postgres        | t        | t          | t             | t           | t           | t              | t            |           -1
(6 rows)
```

### 2.2 Membership, INHERIT and SET ROLE

`GRANT role_a TO role_b` makes `role_b` a **member** of `role_a`. Membership is how you build groups: grant privileges to a `NOLOGIN` role once, then grant that role to the people and services that need it. Being a member can mean two different things, and since PostgreSQL 16 each grant controls them separately:

- **INHERIT**: the member automatically has the group's privileges, all the time, without doing anything.
- **SET**: the member may run `SET ROLE group`, which switches `current_user` to the group for the rest of the session (or transaction, with `SET LOCAL ROLE`). While switched, the session has the group's privileges *and only those* (plus anything granted to `PUBLIC`).

Two functions tell you who you are:

- `session_user` is the role that **authenticated**. It never changes during a session (except through `SET SESSION AUTHORIZATION`, which only superusers may use).
- `current_user` is the role whose privileges are checked **right now**. `SET ROLE` changes it, and so does entering a `SECURITY DEFINER` function (section 7).

Here is a support team, created with two members: `dana` with the default options and `erin` with `INHERIT FALSE`. (These demo roles aren't part of Bazaar; drop them when you're done, as shown at the end of this section.)

```sql
CREATE ROLE support_team NOLOGIN;
CREATE ROLE dana LOGIN PASSWORD 'dana';
CREATE ROLE erin LOGIN PASSWORD 'erin';
GRANT USAGE ON SCHEMA bazaar TO support_team;
GRANT SELECT ON bazaar.reviews TO support_team;
GRANT support_team TO dana;                         -- INHERIT TRUE, SET TRUE (the defaults)
GRANT support_team TO erin WITH INHERIT FALSE;      -- PG 16+ syntax
```

`dana` can read reviews immediately. `erin` can't, until she switches role:

```sql
\c - dana
SELECT count(*) FROM reviews;
\c - erin
SELECT count(*) FROM reviews;
SET ROLE support_team;
SELECT current_user, session_user, count(*) FROM reviews;
RESET ROLE;
```

```text
You are now connected to database "bazaar" as user "dana".
 count 
-------
    78
(1 row)

You are now connected to database "bazaar" as user "erin".
ERROR:  relation "reviews" does not exist
LINE 1: SELECT count(*) FROM reviews;
                             ^
SET
 current_user | session_user | count 
--------------+--------------+-------
 support_team | erin         |    78
(1 row)

RESET
```

Note the error message for `erin`: "does not exist", not "permission denied". Without `USAGE` on schema `bazaar` (which she would inherit from `support_team`), the schema is silently skipped during name lookup, exactly as Module 01 showed.

Why would you ever want `INHERIT FALSE`? For **powerful** roles. An on-call engineer who is a member of `bazaar_owner` with `INHERIT FALSE` has no extra power in their everyday session. They must `SET ROLE bazaar_owner` deliberately, which is visible in their history and in the logs, and they can't drop a table by accident while they think they're browsing. This is the PostgreSQL version of `sudo`.

Revoke both options, and membership remains, but is useless for privileges:

```sql
\c - postgres
REVOKE support_team FROM erin;
GRANT support_team TO erin WITH INHERIT FALSE, SET FALSE;
\c - erin
SET ROLE support_team;
```

```text
ERROR:  permission denied to set role "support_team"
```

Such a membership still counts in `pg_hba.conf`, where `+support_team` matches all members (section 13). psql's `\drg` ("describe role grants", PG 16+) shows the options:

```sql
\c - postgres
\drg
```

```text
                    List of role grants
   Role name    |    Member of    |   Options    | Grantor  
----------------+-----------------+--------------+----------
 bazaar_analyst | bazaar_readonly | INHERIT, SET | postgres
 dana           | support_team    | INHERIT, SET | postgres
 erin           | support_team    |              | postgres
(3 rows)
```

The role attribute `NOINHERIT` (`CREATE ROLE x NOINHERIT`) only sets the default for the `INHERIT` option of future grants to that role. Since PG 16, the option on each grant is what counts. Membership chains are followed transitively: if `a` is a member of `b`, and `b` of `c`, then `a` has `c`'s privileges as long as every link along the way has `INHERIT`.

> **Pitfall:** `pg_has_role('erin', 'support_team', 'MEMBER')` returns `true` for erin in both states above. To ask "does erin currently *have* the group's privileges", use `'USAGE'` (true only with inherit), and use `'SET'` for "may she `SET ROLE` to it".

### 2.3 Who may manage roles: CREATEROLE in PostgreSQL 16

Before PostgreSQL 16, `CREATEROLE` was **nearly superuser**: such a role could alter or drop any non-superuser role, grant itself membership in any role (including `pg_read_server_files` and other powerful predefined roles), and so reach almost anything. Many installations gave it to "admin" accounts without realising this.

PostgreSQL 16 tightened it:

- A `CREATEROLE` role may only alter, drop, rename or change the membership of roles on which it has the **`ADMIN` option**.
- When it creates a role, it automatically receives `ADMIN` on that role (and, if `createrole_self_grant` is set, `INHERIT`/`SET` too).
- It can't give attributes it doesn't have itself: `SUPERUSER`, `REPLICATION` and `BYPASSRLS` require having them.
- It can't grant membership in roles it doesn't have `ADMIN` on.

```sql
CREATE ROLE role_admin LOGIN CREATEROLE;
SET ROLE role_admin;
CREATE ROLE frank LOGIN PASSWORD 'frank';
ALTER ROLE frank CONNECTION LIMIT 5;
ALTER ROLE bazaar_app PASSWORD 'hacked';
CREATE ROLE root2 SUPERUSER;
CREATE ROLE sneaky BYPASSRLS;
GRANT bazaar_app TO frank;
RESET ROLE;
\drg frank|role_admin
```

```text
CREATE ROLE
ALTER ROLE
ERROR:  permission denied to alter role
DETAIL:  To change another role's password, the current user must have the CREATEROLE attribute and the ADMIN option on the role.
ERROR:  permission denied to create role
DETAIL:  Only roles with the SUPERUSER attribute may create roles with the SUPERUSER attribute.
ERROR:  permission denied to create role
DETAIL:  Only roles with the BYPASSRLS attribute may create roles with the BYPASSRLS attribute.
ERROR:  permission denied to grant role "bazaar_app"
DETAIL:  Only roles with the ADMIN option on role "bazaar_app" may grant this role.
RESET
             List of role grants
 Role name  | Member of | Options | Grantor  
------------+-----------+---------+----------
 role_admin | frank     | ADMIN   | postgres
(1 row)
```

`role_admin` can manage the roles it created, and nothing else. That makes delegated user management (an HR tool that creates analyst accounts, say) safe. In general, `GRANT role TO member WITH ADMIN OPTION` lets the member grant and revoke that role to others.

> **Pitfall:** on PostgreSQL 15 and earlier, treat `CREATEROLE` as superuser. Upgrades don't remove the extra rights retroactively from roles created under the old rules: after `pg_upgrade`, existing `CREATEROLE` roles are given `ADMIN` on the roles that existed, so review them.

Clean up the demo roles (they own nothing, so a plain `DROP ROLE` works; section 3 shows what to do when it doesn't):

```sql
DROP ROLE dana, erin, frank, role_admin;
REVOKE ALL ON bazaar.reviews FROM support_team;
REVOKE ALL ON SCHEMA bazaar FROM support_team;
DROP ROLE support_team;
```

### 2.4 Predefined roles

PostgreSQL ships with roles whose names start with `pg_`. They bundle powers that would otherwise require a superuser, so you can grant exactly one power to exactly one role. `\du` hides them; `\duS` shows them. In PostgreSQL 17:

| Role | Grants |
|---|---|
| `pg_read_all_data` | `SELECT` on all tables, views and sequences, and `USAGE` on all schemas, in every database, present and future. **RLS still applies.** |
| `pg_write_all_data` | `INSERT`, `UPDATE`, `DELETE` on everything, and `USAGE` on all schemas. RLS still applies. |
| `pg_monitor` | Member of `pg_read_all_settings`, `pg_read_all_stats` and `pg_stat_scan_tables`: everything a monitoring agent needs (Module 17), such as other sessions' queries in `pg_stat_activity`. |
| `pg_read_all_settings`, `pg_read_all_stats`, `pg_stat_scan_tables` | The parts of `pg_monitor`, one by one. |
| `pg_signal_backend` | `pg_cancel_backend()` and `pg_terminate_backend()` on other roles' sessions (not superusers'). |
| `pg_checkpoint` (PG 15) | Run `CHECKPOINT`. |
| `pg_maintain` (**PG 17**) | `VACUUM`, `ANALYZE`, `CLUSTER`, `REFRESH MATERIALIZED VIEW`, `REINDEX` and `LOCK TABLE` on every relation, as if it had the new `MAINTAIN` privilege everywhere. |
| `pg_use_reserved_connections` (PG 16) | May use the connection slots reserved by `reserved_connections` (Module 17). |
| `pg_create_subscription` (PG 16) | May create logical replication subscriptions (Module 19), given `CREATE` on the database. |
| `pg_read_server_files`, `pg_write_server_files`, `pg_execute_server_program` | `COPY ... FROM/TO` a file on the server, or `COPY ... PROGRAM`. These run as the OS user `postgres`, so they amount to **superuser**. |
| `pg_database_owner` | Not granted explicitly: its only member is always the owner of the current database. It owns the `public` schema (section 4.3). |

Two of these deserve a demonstration. First, `pg_read_all_data` doesn't bypass row-level security. On the reference state, `orders` has RLS enabled and no policy for a new role:

```sql
CREATE ROLE auditor LOGIN;
GRANT pg_read_all_data TO auditor;
\c - auditor
SELECT count(*) FROM users;
SELECT count(*) FROM orders;
INSERT INTO categories (name) VALUES ('x');
```

```text
 count 
-------
    30
(1 row)

 count 
-------
     0
(1 row)

ERROR:  permission denied for table categories
```

Thirty users, but zero orders, without any error. That's what RLS does when no policy matches (section 9). Second, the new `MAINTAIN` privilege in PostgreSQL 17 replaces a common `SECURITY DEFINER` workaround. Refreshing a materialized view used to require *owning* it:

```sql
\c - bazaar_app
REFRESH MATERIALIZED VIEW seller_sales_daily;
\c - postgres
GRANT MAINTAIN ON seller_sales_daily TO bazaar_app;
\c - bazaar_app
REFRESH MATERIALIZED VIEW CONCURRENTLY seller_sales_daily;
```

```text
ERROR:  permission denied for materialized view seller_sales_daily
GRANT
REFRESH MATERIALIZED VIEW
```

The reference file (written to work the same way on older versions) wraps the refresh in a `SECURITY DEFINER` function instead (section 7). Clean up with `\c - postgres`, `REVOKE MAINTAIN ON seller_sales_daily FROM bazaar_app;` and `DROP ROLE auditor;`.

> **PG 18:** adds `pg_signal_autovacuum_worker`, which lets a non-superuser cancel autovacuum workers.

---

## 3. Ownership

Every object has exactly one **owner**: the role that created it, unless changed with `ALTER ... OWNER TO`. The owner has rights that **can't be granted to anyone else**:

- `DROP` and `ALTER` the object: add columns, change types, rename, change storage parameters.
- `GRANT` and `REVOKE` privileges on it (owners hold every privilege `WITH GRANT OPTION` implicitly).
- Enable or disable triggers (`ALTER TABLE ... DISABLE TRIGGER`), including the audit trigger.
- Enable, disable or bypass **row-level security**: owners aren't subject to their table's policies unless the table has `FORCE ROW LEVEL SECURITY` (section 9.4).
- `CREATE INDEX`, `TRUNCATE`, `VACUUM`, `REFRESH MATERIALIZED VIEW` and so on (some of these can be granted separately: `TRUNCATE`, `MAINTAIN`).

Members of the owning role (with `INHERIT`) have these rights too. That is why ownership must not be given to the application's login role. If `bazaar_app` owned `orders`, then a single SQL injection in any endpoint could run `DROP TABLE orders`, `ALTER TABLE orders DISABLE TRIGGER orders_audit`, or `ALTER TABLE orders DISABLE ROW LEVEL SECURITY`. Privileges can't take these rights away from an owner.

So the reference design has a dedicated **owner role**, `bazaar_owner`, that nobody logs in as (`NOLOGIN`). It owns the schema and everything in it. Migrations run as an administrator who does `SET ROLE bazaar_owner` (section 5 explains why `SET ROLE` matters). The application gets only the DML privileges it needs, and the attempts above fail:

```sql
\c - bazaar_app
DROP TABLE orders;
TRUNCATE audit_log;
ALTER TABLE products DISABLE TRIGGER USER;
ALTER TABLE products DISABLE ROW LEVEL SECURITY;
CREATE TABLE bazaar.scratch (x int);
CREATE TABLE public.scratch (x int);
```

```text
ERROR:  must be owner of table orders
ERROR:  permission denied for table audit_log
ERROR:  must be owner of table products
ERROR:  must be owner of table products
ERROR:  permission denied for schema bazaar
LINE 1: CREATE TABLE bazaar.scratch (x int);
                     ^
ERROR:  permission denied for schema public
LINE 1: CREATE TABLE public.scratch (x int);
                     ^
```

### 3.1 Changing owners and dropping roles

`ALTER TABLE t OWNER TO r` requires being the current owner (or a member of it) and being a member of the new owner, with `CREATE` on the schema. A superuser can always do it. The reference `1501` loops over the catalogs and runs one `ALTER ... OWNER TO bazaar_owner` per table, view, materialized view, function, type and domain. Identity sequences and indexes follow their table automatically.

Dropping a role fails while anything still refers to it:

```sql
DROP ROLE bazaar_readonly;
```

```text
ERROR:  role "bazaar_readonly" cannot be dropped because some objects depend on it
DETAIL:  privileges for schema bazaar
privileges for table users
privileges for table sellers
...
privileges for default privileges on new relations belonging to role bazaar_owner in schema bazaar
target of policy products_all_access on table products
...
```

Two commands clean up a role's footprint, **in the current database only**:

- `REASSIGN OWNED BY old TO new` gives every object owned by `old` to `new`.
- `DROP OWNED BY old` drops every object `old` still owns, and **revokes every privilege granted to it**, including default privileges and policy memberships.

So the safe way to remove a person's role is `REASSIGN OWNED BY alice TO bazaar_owner; DROP OWNED BY alice;`, repeated in **every** database of the cluster, followed by `DROP ROLE alice`.

> **Pitfall:** because roles are cluster-wide and privileges are per database, `DROP DATABASE bazaar` removes every object and privilege inside it but leaves the roles. That is what happens on each `reset.sh`: the Bazaar roles survive, with their passwords and memberships.

---

## 4. Privileges

### 4.1 ACLs and how to read them

Every object that can be granted on stores an **access control list** (ACL): an array of `aclitem` values in a catalog column (`pg_class.relacl` for tables, `pg_namespace.nspacl` for schemas, `pg_proc.proacl` for functions, `pg_database.datacl` for databases). Each item reads

```text
grantee=privileges/grantor
```

with one letter per privilege. An empty grantee means `PUBLIC`. An asterisk after a letter means "with grant option".

| Letter | Privilege | Applies to |
|---|---|---|
| `r` | SELECT ("read") | tables, views, columns, sequences, large objects |
| `a` | INSERT ("append") | tables, columns |
| `w` | UPDATE ("write") | tables, columns, sequences (`setval`) |
| `d` | DELETE | tables |
| `D` | TRUNCATE | tables |
| `x` | REFERENCES | tables, columns |
| `t` | TRIGGER | tables |
| `m` | MAINTAIN (PG 17) | tables, materialized views |
| `X` | EXECUTE | functions, procedures |
| `U` | USAGE | schemas, sequences, types, domains, languages, foreign servers |
| `C` | CREATE | databases, schemas, tablespaces |
| `c` | CONNECT | databases |
| `T` | TEMPORARY | databases |
| `s`, `A` | SET, ALTER SYSTEM | configuration parameters (PG 15) |

A **NULL** ACL means "the defaults for this kind of object": the owner has everything, and `PUBLIC` gets whatever that kind of object gives `PUBLIC` by default (section 4.6). The first `GRANT` or `REVOKE` materialises the ACL, owner entry included.

psql's `\dp` (also `\z`) shows table ACLs, column ACLs and policies together:

```sql
\dp products
```

```text
                                                       Access privileges
 Schema |   Name   | Type  |         Access privileges          | Column privileges |                 Policies                 
--------+----------+-------+------------------------------------+-------------------+------------------------------------------
 bazaar | products | table | bazaar_owner=arwdDxtm/bazaar_owner+|                   | products_all_access:                    +
        |          |       | bazaar_app=arwd/bazaar_owner      +|                   |   (u): true                             +
        |          |       | bazaar_readonly=r/bazaar_owner    +|                   |   (c): true                             +
        |          |       | bazaar_seller=arw/bazaar_owner     |                   |   to: bazaar_app, bazaar_readonly       +
        |          |       |                                    |                   | products_seller:                        +
        |          |       |                                    |                   |   (u): (seller_id = current_seller_id())+
        |          |       |                                    |                   |   (c): (seller_id = current_seller_id())+
        |          |       |                                    |                   |   to: bazaar_seller
(1 row)
```

Read it as: the owner has all eight table privileges; the app may `SELECT`, `INSERT`, `UPDATE`, `DELETE`; the read-only role may `SELECT`; the seller portal may `SELECT`, `INSERT`, `UPDATE` (but not `DELETE`). All of them were granted by `bazaar_owner`: when a superuser grants on an object it doesn't own, the grant is recorded as if the owner had made it.

For queries, `aclexplode()` turns an ACL into rows:

```sql
SELECT a.grantee::regrole, a.privilege_type, a.is_grantable, a.grantor::regrole
FROM pg_class AS c, aclexplode(c.relacl) AS a
WHERE c.oid = 'bazaar.products'::regclass
ORDER BY 1, 2;
```

```text
     grantee     | privilege_type | is_grantable |   grantor    
-----------------+----------------+--------------+--------------
 bazaar_owner    | DELETE         | f            | bazaar_owner
 bazaar_owner    | INSERT         | f            | bazaar_owner
 bazaar_owner    | MAINTAIN       | f            | bazaar_owner
 bazaar_owner    | REFERENCES     | f            | bazaar_owner
 bazaar_owner    | SELECT         | f            | bazaar_owner
 bazaar_owner    | TRIGGER        | f            | bazaar_owner
 bazaar_owner    | TRUNCATE       | f            | bazaar_owner
 bazaar_owner    | UPDATE         | f            | bazaar_owner
 bazaar_app      | DELETE         | f            | bazaar_owner
 bazaar_app      | INSERT         | f            | bazaar_owner
 bazaar_app      | SELECT         | f            | bazaar_owner
 bazaar_app      | UPDATE         | f            | bazaar_owner
 bazaar_readonly | SELECT         | f            | bazaar_owner
 bazaar_seller   | INSERT         | f            | bazaar_owner
 bazaar_seller   | SELECT         | f            | bazaar_owner
 bazaar_seller   | UPDATE         | f            | bazaar_owner
(16 rows)
```

(A grantee OID of 0 means `PUBLIC`; `0::regrole` prints as `-`.)

An ACL only shows **direct** grants. To ask whether a role *effectively* has a privilege, through `PUBLIC` or inherited membership, use the `has_*_privilege` family: `has_database_privilege`, `has_schema_privilege`, `has_table_privilege`, `has_column_privilege`, `has_any_column_privilege`, `has_sequence_privilege`, `has_function_privilege`. Each takes an optional role (default: `current_user`), the object, and a privilege name. Exercise 2 builds a privilege matrix with them.

### 4.2 Database privileges: CONNECT, TEMPORARY, CREATE

| Privilege | Allows | Default for PUBLIC |
|---|---|---|
| `CONNECT` | Opening a session in this database (checked after `pg_hba.conf`). | **Granted** |
| `TEMPORARY` (`TEMP`) | Creating temporary tables (and functions, in `pg_temp`). | **Granted** |
| `CREATE` | Creating schemas (and installing trusted extensions). | Not granted |

```sql
\l bazaar
```

```text
                                                     List of databases
  Name  |  Owner   | Encoding | Locale Provider |  Collate   |   Ctype    | Locale | ICU Rules |     Access privileges     
--------+----------+----------+-----------------+------------+------------+--------+-----------+---------------------------
 bazaar | postgres | UTF8     | libc            | en_US.utf8 | en_US.utf8 |        |           | =Tc/postgres             +
        |          |          |                 |            |            |        |           | postgres=CTc/postgres    +
        |          |          |                 |            |            |        |           | bazaar_app=c/postgres    +
        |          |          |                 |            |            |        |           | bazaar_analyst=c/postgres+
        |          |          |                 |            |            |        |           | bazaar_seller=c/postgres
(1 row)
```

`=Tc/postgres` is `PUBLIC` with `TEMP` and `CONNECT`. On a cluster that hosts several applications' databases, a common hardening step is

```sql
REVOKE CONNECT, TEMPORARY ON DATABASE bazaar FROM PUBLIC;
GRANT CONNECT ON DATABASE bazaar TO bazaar_app, bazaar_analyst, bazaar_seller;
```

so that a role created for another application can't even open a session in `bazaar`. The reference grants `CONNECT` explicitly for exactly this reason, so the revoke can be added safely later.

### 4.3 Schema privileges, and the public schema since PostgreSQL 15

| Privilege | Allows |
|---|---|
| `USAGE` | Looking up objects in the schema. Without it, objects in the schema are invisible ("does not exist"), even with table privileges. |
| `CREATE` | Creating objects in the schema (and renaming objects in it, given ownership). |

A new schema such as `bazaar` grants **nothing** to `PUBLIC`. The `public` schema is special:

```sql
\dn+
```

```text
                                       List of schemas
  Name  |       Owner       |           Access privileges            |      Description       
--------+-------------------+----------------------------------------+------------------------
 bazaar | bazaar_owner      | bazaar_owner=UC/bazaar_owner          +| 
        |                   | bazaar_app=U/bazaar_owner             +| 
        |                   | bazaar_readonly=U/bazaar_owner        +| 
        |                   | bazaar_seller=U/bazaar_owner           | 
 public | pg_database_owner | pg_database_owner=UC/pg_database_owner+| standard public schema
        |                   | =U/pg_database_owner                   | 
(2 rows)
```

Since **PostgreSQL 15**, `public` is owned by `pg_database_owner` (whoever owns the database) and `PUBLIC` has only `USAGE` on it (`=U`). Before 15, `PUBLIC` also had `CREATE` there. Every role could create tables, functions and *operators* in a schema that was on everybody's `search_path`. That was the root of CVE-2018-1058: a malicious user could create a function `public.lower(text)`... no, wait: it could create a function with a **better-matching argument type** than a built-in, such as `public.lower(varchar)`, which would then be called instead of `pg_catalog.lower(text)` by other users' queries that passed a `varchar`, running the attacker's code with the victim's privileges. Section 7 shows the same trick against `SECURITY DEFINER` functions.

> **Pitfall:** `pg_upgrade` and `pg_dump`/`pg_restore` from an older version **keep** the old `public` schema ACL. A database created on PostgreSQL 13 and upgraded to 17 may still let everybody create objects in `public`. Check with `\dn+`, and fix with `REVOKE CREATE ON SCHEMA public FROM PUBLIC;`.

### 4.4 Table and column privileges

| Privilege | Allows | Notes |
|---|---|---|
| `SELECT` | Reading. | Also needed for any column used in `WHERE` or `RETURNING` of `UPDATE`/`DELETE`, and for `SELECT ... FOR UPDATE` together with `UPDATE`. |
| `INSERT` | Adding rows. | `ON CONFLICT DO UPDATE` also needs `UPDATE`. |
| `UPDATE` | Changing rows. | |
| `DELETE` | Deleting rows. | |
| `TRUNCATE` | Emptying the table. | Separate from `DELETE`: it bypasses `DELETE` triggers and RLS, so give it to nobody but the owner. |
| `REFERENCES` | Creating a foreign key that points to this table. | |
| `TRIGGER` | Creating triggers on the table. | |
| `MAINTAIN` (PG 17) | `VACUUM`, `ANALYZE`, `REINDEX`, `CLUSTER`, `REFRESH MATERIALIZED VIEW`, `LOCK TABLE`. | |

`GRANT ... ON ALL TABLES IN SCHEMA s` is shorthand for one grant per **existing** table, view, materialized view and foreign table in `s`. It doesn't affect tables created later (section 5).

For partitioned tables, privileges are checked on the table you name in the query. Querying `events` checks only `events`' ACL, not the partitions'. Querying a partition directly checks the partition's own ACL.

**Column privileges.** `SELECT`, `INSERT`, `UPDATE` and `REFERENCES` can be granted per column: `GRANT SELECT (id, full_name) ON users TO r`. A role with only column privileges can use exactly those columns; anything else is "permission denied for table", including `SELECT *`.

The analytics team should be able to count users but not export e-mail addresses. On the reference state, `bazaar_readonly` (and therefore `bazaar_analyst`) has `SELECT` on the whole `users` table. A tempting fix does nothing:

```sql
REVOKE SELECT (email) ON users FROM bazaar_readonly;
SELECT has_column_privilege('bazaar_analyst', 'users', 'email', 'SELECT');
```

```text
REVOKE
 has_column_privilege 
----------------------
 t
(1 row)
```

**A table-level privilege covers every column, and a column-level `REVOKE` only removes column-level grants.** There's no "all columns except" in PostgreSQL. You must revoke the table privilege and grant the allowed columns explicitly:

```sql
\set ON_ERROR_ROLLBACK on
BEGIN;
REVOKE SELECT ON users FROM bazaar_readonly;
GRANT SELECT (id, full_name, is_active, created_at) ON users TO bazaar_readonly;
SET LOCAL ROLE bazaar_analyst;
SELECT * FROM users LIMIT 1;
SELECT id, full_name FROM users WHERE id = 17;
SELECT count(*) FROM users;
SELECT count(email) FROM users;
SELECT count(*) FROM users WHERE email LIKE '%example.com';
SELECT has_table_privilege('users', 'SELECT') AS tbl,
       has_column_privilege('users', 'email', 'SELECT') AS email,
       has_any_column_privilege('users', 'SELECT') AS anycol;
ROLLBACK;
```

```text
ERROR:  permission denied for table users
 id | full_name  
----+------------
 17 | Zoë Martin
(1 row)

 count 
-------
    30
(1 row)

ERROR:  permission denied for table users
ERROR:  permission denied for table users
 tbl | email | anycol 
-----+-------+--------
 f   | f     | t
(1 row)
```

(`\set ON_ERROR_ROLLBACK on` makes psql wrap each statement in a savepoint, so the transaction survives the expected errors.) `count(*)` works because it reads no column. Using `email` anywhere, even in `WHERE`, needs `SELECT` on it: otherwise `WHERE email LIKE 'a%'` would let you guess addresses one letter at a time. Exercise 5 adds a view that exposes only the e-mail domain, and the project milestone makes this permanent.

> **In production:** column privileges are fail-closed for new columns: a column added later isn't granted to anyone until you say so. That is usually what you want for personal data, but it surprises BI tools that run `SELECT *`. Give analysts a view with the allowed columns as their documented interface.

### 4.5 Sequences and functions

**Sequences** have three privileges: `USAGE` (`nextval`, `currval`), `SELECT` (`currval`, and reading the sequence like a table: `SELECT last_value FROM s`), and `UPDATE` (`nextval`, `setval`). A role that inserts into a table whose key comes from a `serial` column's `DEFAULT nextval(...)` needs `USAGE` on that sequence.

**Identity columns** (`GENERATED ... AS IDENTITY`, used by all of Bazaar's tables) **don't** check privileges on their internal sequence: `INSERT` on the table is enough. So `bazaar_seller` doesn't need any sequence privilege to create a product. The reference grants `USAGE ON ALL SEQUENCES` to the seller role anyway, and Exercise 10 shows why that is a small information leak.

**Functions and procedures** have one privilege, `EXECUTE`, and **`PUBLIC` gets `EXECUTE` on every new function by default**. For an ordinary function (`SECURITY INVOKER`, the default) that is harmless: the function's body runs with the caller's privileges, so calling `place_order()` as `bazaar_readonly` fails at the first `INSERT`. For a `SECURITY DEFINER` function it is dangerous: such a function runs with its *owner's* privileges, so "anyone can execute" means "anyone can do what this function does". Always `REVOKE EXECUTE ... FROM PUBLIC` on those (section 7).

```sql
SELECT p.oid::regprocedure AS function, p.prosecdef AS definer, p.proacl
FROM pg_proc AS p
WHERE p.pronamespace = 'bazaar'::regnamespace
  AND p.proname IN ('current_seller_id', 'place_order', 'audit_trigger',
                    'create_events_partition', 'refresh_seller_sales_daily')
ORDER BY 1::text;
```

```text
           function           | definer |                                 proacl                                  
------------------------------+---------+-------------------------------------------------------------------------
 audit_trigger()              | t       | {=X/bazaar_owner,bazaar_owner=X/bazaar_owner,bazaar_app=X/bazaar_owner}
 create_events_partition(date) | t      | {bazaar_owner=X/bazaar_owner,bazaar_app=X/bazaar_owner}
 current_seller_id()          | f       | 
 place_order(bigint,jsonb,bigint) | f   | {=X/bazaar_owner,bazaar_owner=X/bazaar_owner,bazaar_app=X/bazaar_owner}
 refresh_seller_sales_daily() | t       | {bazaar_owner=X/bazaar_owner,bazaar_app=X/bazaar_owner}
(5 rows)
```

`current_seller_id()` has a NULL ACL: the defaults, `PUBLIC` may execute. `place_order` has `=X` (PUBLIC) plus explicit grants. The two definer functions that do real work (`create_events_partition`, `refresh_seller_sales_daily`) have had `PUBLIC` removed. `audit_trigger()` keeps `=X`, which is fine: a trigger function can't be called directly (`SELECT audit_trigger()` fails with "trigger functions can only be called as triggers"), and `EXECUTE` isn't checked when a trigger fires.

### 4.6 PUBLIC

`PUBLIC` is a pseudo-role that every role is implicitly a member of, including roles created in the future. You can grant to it and revoke from it, but you can't log in as it or remove anyone from it. What `PUBLIC` gets by default:

| Object | PUBLIC's default privileges |
|---|---|
| Database | `CONNECT`, `TEMPORARY` |
| Schema `public` | `USAGE` (and `CREATE` before PG 15) |
| Other schemas | nothing |
| Tables, views, sequences | nothing |
| Functions, procedures | `EXECUTE` |
| Types, domains, languages (`plpgsql`, `sql`) | `USAGE` |
| System catalogs (`pg_class`, `pg_proc`, `pg_stat_*`, ...) | `SELECT` (most of them) |

That last row matters: **every role can read the system catalogs**. Table names, column names, function source code, view definitions, row-count estimates and statistics counters are visible to all roles that can connect. Only a few columns are protected (`pg_authid.rolpassword`, the statistics in `pg_statistic`). Exercise 10 uses this.

### 4.7 GRANT and REVOKE mechanics

A few rules that explain surprising results:

- **Privileges are additive.** There is no "deny" entry. A role has a privilege if it was granted to the role, to any role it inherits from, or to `PUBLIC`. To take something away, you must revoke it where it was granted.
- **A grant is identified by (grantee, privilege, grantor).** If both `alice` (with grant option) and the owner granted `SELECT` to `bob`, revoking the owner's grant leaves `bob` with `alice`'s. `REVOKE ... CASCADE` also revokes privileges that others granted *based on* the revoked grant option.
- **`WITH GRANT OPTION`** lets the grantee pass the privilege on. Avoid it: it makes "who can read this table" depend on what other people did.
- **The owner can revoke its own privileges** (`REVOKE UPDATE ON t FROM bazaar_owner`), which can prevent accidents, but it can always grant them back. Ownership itself is the real power.

---

## 5. Default privileges

`GRANT ... ON ALL TABLES IN SCHEMA` is a one-time snapshot. Next month a migration creates a `coupons` table, and the application can't read it until someone remembers to grant it. **Default privileges** solve this: `ALTER DEFAULT PRIVILEGES` stores grants that are applied automatically **when an object is created**.

```sql
ALTER DEFAULT PRIVILEGES FOR ROLE bazaar_owner IN SCHEMA bazaar
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO bazaar_app;
```

reads: "whenever **`bazaar_owner`** creates a table in schema `bazaar`, grant these privileges to `bazaar_app`." You can set them for `TABLES` (which includes views and materialized views), `SEQUENCES`, `FUNCTIONS` (and `ROUTINES`), `TYPES` and `SCHEMAS`, and you can also `REVOKE` defaults, for example `ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC` to make new functions private by default. psql's `\ddp` lists them:

```sql
\ddp
```

```text
                     Default access privileges
    Owner     | Schema |   Type   |       Access privileges        
--------------+--------+----------+--------------------------------
 bazaar_owner | bazaar | function | bazaar_app=X/bazaar_owner
 bazaar_owner | bazaar | sequence | bazaar_app=rU/bazaar_owner
 bazaar_owner | bazaar | table    | bazaar_app=arwd/bazaar_owner  +
              |        |          | bazaar_readonly=r/bazaar_owner
(3 rows)
```

The **`FOR ROLE`** part is the subtlety that catches almost everyone. Default privileges belong to the role that **creates** the object, not to the schema. If you leave `FOR ROLE` out, they're defined for yourself, the role running the `ALTER DEFAULT PRIVILEGES`. So it matters *who* runs the migration:

```sql
CREATE TABLE bazaar.gift_cards (code text PRIMARY KEY, balance numeric(12,2) NOT NULL);  -- as postgres
SET ROLE bazaar_owner;
CREATE TABLE bazaar.coupons (code text PRIMARY KEY, percent_off int NOT NULL);           -- as bazaar_owner
RESET ROLE;
\dp gift_cards|coupons
```

```text
                                        Access privileges
 Schema |    Name    | Type  |         Access privileges          | Column privileges | Policies 
--------+------------+-------+------------------------------------+-------------------+----------
 bazaar | coupons    | table | bazaar_owner=arwdDxtm/bazaar_owner+|                   | 
        |            |       | bazaar_app=arwd/bazaar_owner      +|                   | 
        |            |       | bazaar_readonly=r/bazaar_owner     |                   | 
 bazaar | gift_cards | table |                                    |                   | 
(2 rows)
```

`gift_cards` was created by `postgres`: it is **owned by `postgres`** and has no grants, so the app gets "permission denied for table gift_cards" and only a superuser can fix it. `coupons` was created by `bazaar_owner`, so it is owned by the right role and got the default grants. This is why `NOTES.md` says to **run migrations as `SET ROLE bazaar_owner`**. Migration tools (Alembic in Module 26) can do this in their connection setup. Clean up with `DROP TABLE bazaar.gift_cards, bazaar.coupons;`.

> **Pitfall:** default privileges apply only at `CREATE` time. `ALTER TABLE gift_cards OWNER TO bazaar_owner` fixes the owner but adds no grants; you still have to `GRANT` explicitly. Exercise 7 writes a query that finds and fixes such objects.

---

## 6. Bazaar's least-privilege layout

The reference [`project/db/1501_schema_security.sql`](../../project/db/1501_schema_security.sql) puts the pieces together. Read it next to this section: it is commented line by line. The design:

```text
                       ┌──────────────────────────────┐
                       │ bazaar_owner (NOLOGIN)       │  owns schema bazaar and every object in it
                       │ migrations: SET ROLE to it   │  default privileges are defined FOR it
                       └──────────────────────────────┘
          grants DML        grants SELECT          grants a few tables, partly per column
               │                 │                       │
               ▼                 ▼                       ▼
     ┌────────────────┐  ┌───────────────────┐   ┌─────────────────────┐
     │ bazaar_app     │  │ bazaar_readonly   │   │ bazaar_seller       │
     │ LOGIN, 50 conns│  │ NOLOGIN (bundle)  │   │ LOGIN (seller portal)│
     │ RLS: sees all  │  │ RLS: sees all     │   │ RLS: own rows only  │
     └────────────────┘  └───────────────────┘   └─────────────────────┘
                                 ▲ member (INHERIT)
                          ┌───────────────────┐
                          │ bazaar_analyst    │
                          │ LOGIN (a person)  │
                          └───────────────────┘
```

| Role | Attributes | Gets | Must not |
|---|---|---|---|
| `bazaar_owner` | `NOLOGIN` | Ownership of everything | Be used by any running service |
| `bazaar_app` | `LOGIN`, `CONNECTION LIMIT 50` | `SELECT/INSERT/UPDATE/DELETE` on all tables, `USAGE, SELECT` on sequences, `EXECUTE` on functions, `EXECUTE` on two `SECURITY DEFINER` maintenance functions | Run DDL, truncate, disable triggers or RLS |
| `bazaar_readonly` | `NOLOGIN` | `SELECT` on all tables | Write anything |
| `bazaar_analyst` | `LOGIN` | Membership in `bazaar_readonly` | Have privileges of its own: replace the person, keep the bundle |
| `bazaar_seller` | `LOGIN` | `SELECT` on `categories`, `sellers`, `reviews`; `SELECT/INSERT/UPDATE` on `products`, `inventory` (no `DELETE`: archive instead); `SELECT` on `orders`, `order_items` and `UPDATE` only of `orders.status`, `shipped_at`, `delivered_at` | See or change any other seller's rows (RLS, sections 9 and 10) |

Things worth noticing in the file:

1. **Idempotent role creation.** `CREATE ROLE` has no `IF NOT EXISTS`, so the file wraps it in a `DO` block that checks `pg_roles`, then re-applies attributes and passwords with `ALTER ROLE`. Roles survive `reset.sh`, so the file must cope with them already existing.
2. **Ownership is transferred by catalog loops** (section 3.1), so nothing created by earlier modules as `postgres` is forgotten.
3. **Grants on existing objects plus default privileges for future ones**, both for `bazaar_owner`.
4. **Column-level `UPDATE`** for the seller portal on `orders`: a seller may mark an order shipped or delivered but can't change its `total` or move it to another customer. RLS decides *which* orders; column privileges decide *which columns*.
5. **Passwords in the file** (`'app'`, `'analyst'`, `'seller'`) are acceptable only because this is a course. Section 15 covers how real deployments handle secrets.

Two gaps are left in the reference on purpose, for you to find: the analyst can read `users.email` (section 4.4), and the app can rewrite the audit trail. Exercise 3 and the project milestone close them.

---

## 7. SECURITY DEFINER functions

### 7.1 Controlled privilege escalation

A function runs with its caller's privileges by default (`SECURITY INVOKER`). Declared **`SECURITY DEFINER`**, it runs with the privileges of its **owner**, like a setuid program. While it runs, `current_user` is the owner and `session_user` is still the caller. This is how you let a role do one specific privileged thing without granting the underlying privilege. Three examples from the reference:

- **`audit_trigger()`** writes to `audit_log`. If it were an invoker function, every role that updates `products` would need `INSERT` on `audit_log`, and could then also insert fake history directly. As a definer function owned by `bazaar_owner`, the trigger can write the log while the roles that fire it can't. (It records `session_user`, so the audit shows who actually connected.)
- **`create_events_partition(date)`** runs `CREATE TABLE ... PARTIT