# Module 15: Security and Row-Level Security

Bazaar has grown up. Until now there has been one role, `postgres`, a superuser that can do anything, and every connection string in the company has carried its password. That is fine on a laptop and a disaster in production. Now there is a web application, a team of analysts who run SQL against the live database, a BI tool, a seller portal that lets 2,000 independent shops see and edit *their own* products and orders, and, eventually, auditors. Each of these should be able to do exactly what its job requires and nothing more. An analyst who copies the wrong `WHERE` clause should get an error, not a `TRUNCATE`. A compromised application credential should not be able to read every customer's e-mail or drop a table. A seller must never see another seller's orders, even if their code has a bug.

This module is about building that. We start with the threat model (who connects, what they can do, how much damage a mistake or a breach can cause), then work through PostgreSQL's two layers of access control: the **privilege system** (roles, ownership, `GRANT`/`REVOKE`, default privileges) decides *whether* you may touch an object at all, and **row-level security** (RLS) decides *which rows* of a table you may see and change. We then look at the controlled-escalation tool, `SECURITY DEFINER` functions, and the classic attack against them. Finally we drop below SQL to **authentication**: `pg_hba.conf`, SCRAM, and TLS, with a hands-on lab where you generate certificates and connect with full verification.

Everything is built on the Bazaar roles you have already met in passing. The reference layout — `bazaar_owner`, `bazaar_app`, `bazaar_readonly`, `bazaar_analyst`, `bazaar_seller` — lives in [`project/db/1501_schema_security.sql`](../../project/db/1501_schema_security.sql), and this module explains every line of it and then asks you to build your own.

**What you'll learn**

- A threat model for a database: the actors, their privileges, and the blast radius of a mistake or a breach.
- Roles in depth: users vs. groups, the attributes (`SUPERUSER`, `CREATEDB`, `CREATEROLE`, `REPLICATION`, `BYPASSRLS`), `INHERIT` vs. `NOINHERIT`, `SET ROLE`, membership, and what PostgreSQL 16 changed about role grants (`ADMIN`/`INHERIT`/`SET` options, and `CREATEROLE` no longer being a near-superuser).
- The predefined `pg_*` roles, and when to reach for them instead of a custom bundle.
- Object ownership, and why the application must not own its own tables.
- The privilege system end to end: database, schema, table, column, sequence and function privileges; the `PUBLIC` pseudo-role; the PostgreSQL 15 lock-down of the `public` schema; `ALTER DEFAULT PRIVILEGES` and its "for role" subtlety; and how to inspect all of it (`\dp`, `\ddp`, `has_table_privilege`, `aclexplode`).
- A least-privilege layout for Bazaar, matching the reference `1501`.
- `SECURITY DEFINER` functions as controlled privilege escalation, the `search_path` hijack attack, and the fix.
- Views as a security boundary: `security_barrier` and `security_invoker` (PG 15+).
- Row-level security: `ENABLE` vs. `FORCE`, `USING` vs. `WITH CHECK`, `PERMISSIVE` vs. `RESTRICTIVE`, per-command and per-role policies, how policies combine, multi-tenant RLS with `current_setting` and `SET LOCAL`, the owner bypass and `BYPASSRLS`, RLS performance, and the pitfalls (FK checks, unique-constraint leaks, side channels).
- Authentication: `pg_hba.conf` line by line, method ordering, `trust`/`peer`/`scram-sha-256`/`md5`/`cert`, `password_encryption` and migrating off md5, and SSL/TLS with client `sslmode` and why `verify-full` matters.
- An operational tour: `pgaudit`, encryption at rest and column-level encryption with `pgcrypto`, secrets handling, and per-role connection limits and `statement_timeout`.

**Before you start**

Most of this module needs no volume of data, and the small dataset is the one documented row by row in [`project/db/NOTES.md`](../../project/db/NOTES.md). A helper builds the module-15 schema on the small dataset in a few seconds:

```bash
./modules/15-security-rls/lab/small15.sh               # state at the start of module 15 (small data)
./modules/15-security-rls/lab/small15.sh --with-1501   # ... with the reference roles and RLS applied
./project/bin/psql.sh
```

If you prefer the real thing, `./project/bin/reset.sh 15` builds the same schema with the **large** dataset (100k users, 200k products, 500k orders; a few minutes and about 2 GB), and `./project/bin/reset.sh 16` is state 15 with `1501` already applied. The performance section (9) uses the large data; everything else works on either. The TLS lab (sections 14–15) is a separate Docker Compose project in [`lab/tls/`](lab/tls/).

> **Roles are cluster-wide.** `reset.sh` drops and recreates the `bazaar` *database*, but roles live in the cluster, not in a database, so they survive. That is why `1501` guards every `CREATE ROLE` and re-asserts attributes with `ALTER ROLE`: you can apply it any number of times. It also means that to start the roles truly from scratch you must drop them yourself — see [`lab/README.md`](lab/README.md).

You have met pieces of this before: roles and the `public` schema in [Module 01](../01-psql/#4-roles-briefly), the `SECURITY DEFINER` / `search_path` teaser in [Module 01 §3.4](../01-psql/), views in [Module 09](../09-views-functions-triggers/), SCRAM's cost in [Module 16](../16-internals/), and per-role settings and `pg_hba.conf` reloads in [Module 17](../17-operations/). This module makes all of it precise.

---

## 1. A threat model for a database

Security engineering starts with three questions, not with a feature list: **who connects, what can each of them do, and how much damage results when one of them is wrong?** The last question is the *blast radius*, and it is the one most teams skip.

Bazaar's actors:

| Actor | Connects as | Should be able to | Blast radius if compromised or buggy |
|---|---|---|---|
| The web/API application | one pooled login role | read and write all business tables through its queries and functions | everything the app role can do — so keep that set as small as the app's features allow |
| An analyst (a person) | a personal login role | read business data for reporting | read-only, and *not* the sensitive columns (e-mail, password hashes) |
| A BI tool | a service login role | read a defined set of tables/views | read-only; a leaked token reads only what was granted |
| The seller portal | one pooled login role, scoped per request | see and edit *only the current seller's* rows | one seller's data, never the whole marketplace |
| A DBA / migration job | a superuser or admin, `SET ROLE` to the owner | change the schema | total — so it is used rarely, from controlled places, with care |
| An attacker | whatever credential they stole, or SQL injection into the app | — | *exactly the privileges of whatever they got in as* |

Two principles fall out of the table.

**Least privilege.** Each actor gets the minimum it needs. This is not bureaucracy; it is blast-radius control. If the application role cannot `DROP TABLE`, then a SQL-injection bug that reaches `DROP TABLE orders` fails with "must be owner of table orders" instead of deleting the business. If the analyst role cannot read `users.email`, then a leaked analyst password does not leak the customer list. You will see shortly that the application role owning nothing is what makes the first of these true.

**Defence in depth.** No single control is trusted to be perfect. The seller portal relies on RLS *and* on the application setting `app.seller_id` correctly *and* on column privileges *and* on TLS so the credential is never sniffed. If the application has a bug and forgets to filter by seller, RLS still filters. If RLS had a hole, the column grants still hide what the seller never had. Each layer assumes the one above it might fail.

PostgreSQL gives you three independent layers, and this module covers all three:

```
                      ┌──────────────────────────────────────────┐
  a connection  ───▶  │ 1. Authentication  (pg_hba.conf, §14-15)  │  who are you? (and is the
                      │    which role, from where, how proven     │     channel encrypted?)
                      └──────────────────┬───────────────────────┘
                                         ▼
                      ┌──────────────────────────────────────────┐
                      │ 2. Privileges      (GRANT/REVOKE, §4-6)   │  may you touch this
                      │    database / schema / table / column /   │     object at all?
                      │    sequence / function                    │
                      └──────────────────┬───────────────────────┘
                                         ▼
                      ┌──────────────────────────────────────────┐
                      │ 3. Row-level security (policies, §10-12)  │  which rows of it?
                      └──────────────────────────────────────────┘
```

A query must pass all three. Authentication decides which role you are acting as; privileges decide whether that role may perform the operation on the object; RLS, if enabled on the table, decides which rows the operation sees. A superuser bypasses layers 2 and 3 entirely, which is exactly why production applications never connect as one.

---

## 2. Roles

PostgreSQL has a single concept for both "users" and "groups": the **role**. A role with the `LOGIN` attribute can start a session (it is what other systems call a user); a role without it is typically used as a group that other roles are *members* of. There is no separate `CREATE USER` concept — `CREATE USER` is just `CREATE ROLE ... LOGIN`. Roles are **cluster-wide**: the role `bazaar_app` exists once for the whole server and is the same in every database; what it may *do* is granted per database and per object.

### 2.1 Role attributes

A handful of attributes are properties of the role itself, set with `CREATE ROLE`/`ALTER ROLE`, and they are powerful enough to deserve individual attention:

| Attribute | Grants the power to | Notes |
|---|---|---|
| `SUPERUSER` | bypass **all** permission checks, including RLS | The keys to the kingdom. Only `postgres` and other break-glass accounts. Can read/modify every table, change any setting, run `COPY ... PROGRAM` (shell access). |
| `CREATEDB` | `CREATE DATABASE` | |
| `CREATEROLE` | create, alter and drop *other* roles | Before PG 16 this was almost superuser (see §2.5). Now constrained. |
| `REPLICATION` | start replication connections, `pg_basebackup` | Replicas and backup tools ([Module 19](../19-replication/)). Can read the entire cluster byte for byte, so treat it like superuser. |
| `BYPASSRLS` | ignore row-level security on every table | For roles that must see all rows despite RLS (e.g. a maintenance job). §10 and §12. |
| `LOGIN` | open a session | The user/group distinction. |
| `CONNECTION LIMIT n` | at most `n` concurrent sessions | `-1` = unlimited. §16. |
| `PASSWORD` / `VALID UNTIL` | a password, and its expiry | §13. |
| `INHERIT` / `NOINHERIT` | automatically use the privileges of roles you are a member of | §2.3. |

Bazaar's roles use almost none of these powers. Look at the reference setup:

```sql
SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls, rolcanlogin, rolconnlimit
FROM pg_roles WHERE rolname LIKE 'bazaar%' ORDER BY 1;
```

```text
     rolname     | rolsuper | rolcreaterole | rolcreatedb | rolreplication | rolbypassrls | rolcanlogin | rolconnlimit
-----------------+----------+---------------+-------------+----------------+--------------+-------------+--------------
 bazaar_analyst  | f        | f             | f           | f              | f            | t           |           -1
 bazaar_app      | f        | f             | f           | f              | f            | t           |           50
 bazaar_owner    | f        | f             | f           | f              | f            | f           |           -1
 bazaar_readonly | f        | f             | f           | f              | f            | f           |           -1
 bazaar_seller   | f        | f             | f           | f              | f            | t           |           -1
```

Not one of them is a superuser, can create roles or databases, or can bypass RLS. Three can log in (`bazaar_app`, `bazaar_analyst`, `bazaar_seller`); two are groups/owners that cannot (`bazaar_owner`, `bazaar_readonly`). `bazaar_app` is capped at 50 connections. This is what least privilege looks like in the catalog: every "can it?" column is `f`.

### 2.2 Users and groups: membership

A role becomes a *member* of another role with `GRANT role TO member`. Membership is how you bundle privileges: grant privileges to a group role once, then make people members of it. The reference does exactly this for read access:

```sql
GRANT bazaar_readonly TO bazaar_analyst;   -- the analyst is a member of the read-only group
```

`bazaar_readonly` is granted `SELECT` on every table (§5). Making `bazaar_analyst` a member gives the analyst that `SELECT` without repeating any grants. Add a second analyst next year and you grant them `bazaar_readonly` in one line.

### 2.3 INHERIT vs. NOINHERIT, and SET ROLE

Here is the subtle part. Being a member of a role is not the same as *using* that role's privileges. There are two ways a member can act with the group's privileges:

- **Inheritance** (`INHERIT`, the default): the member *automatically* has the group's privileges, all the time, without doing anything. `bazaar_analyst` can `SELECT` from `orders` the moment it connects, because it inherits `bazaar_readonly`.
- **`SET ROLE`**: the member explicitly *becomes* the group for the rest of the session (or until `RESET ROLE`). After `SET ROLE bazaar_readonly`, `current_user` is `bazaar_readonly` and any object you create is owned by it.

Whether inheritance happens is controlled per grant (PG 16+, see §2.4) and, historically, by the member's `INHERIT`/`NOINHERIT` attribute. A `NOINHERIT` member does *not* get the group's privileges automatically; it must `SET ROLE` to use them. This is useful for an administrative role that should act with its powerful group only deliberately.

Watch the difference. Grant a group two ways:

```sql
CREATE ROLE support_team NOLOGIN;
GRANT USAGE ON SCHEMA bazaar TO support_team;
GRANT SELECT ON bazaar.reviews TO support_team;

CREATE ROLE dana LOGIN PASSWORD 'dana';
CREATE ROLE erin LOGIN PASSWORD 'erin';
GRANT support_team TO dana;                    -- default: inherit YES, set YES
GRANT support_team TO erin WITH INHERIT FALSE; -- erin must SET ROLE to use it
```

Connect as `dana` — the privileges are simply there:

```sql
\c - dana
SELECT count(*) FROM reviews;   --> 78
```

Connect as `erin` — they are not, until she asks for them:

```sql
\c - erin
SELECT count(*) FROM reviews;
```

```text
ERROR:  permission denied for table reviews
```

```sql
SET ROLE support_team;
SELECT current_user, count(*) FROM reviews;   --> support_team | 78
```

`SET ROLE` can only switch to a role you are a member of (or any role, if you are a superuser). It is the mechanism a well-designed application uses: connect as one modestly-privileged login role, then `SET ROLE` to the specific role appropriate for the current request. We will use it for migrations (`SET ROLE bazaar_owner`) and discuss it again with connection pools in §11.

> **Under the hood:** `current_user` is the role whose privileges are being checked (it changes with `SET ROLE` and inside a `SECURITY DEFINER` function). `session_user` is the role you authenticated as and never changes. Bazaar's audit trigger records `session_user`, so even a change made after `SET ROLE` is attributed to the human who logged in.

### 2.4 PG 16: the three options on a role grant

Before PostgreSQL 16, `GRANT group TO member` had a single modifier, `WITH ADMIN OPTION` (may the member grant the group onward?), and whether the member inherited the group's privileges was decided solely by the member's `INHERIT`/`NOINHERIT` attribute. That was coarse: a role was either always-inheriting or never-inheriting, for *every* membership.

PostgreSQL 16 made it per-grant. Each membership now carries three independent options:

| Option | Default | Means |
|---|---|---|
| `ADMIN` | `FALSE` | the member may `GRANT` this role to others, and `REVOKE` it |
| `INHERIT` | the member's `rolinherit` attribute | the member automatically uses this role's privileges (no `SET ROLE` needed) |
| `SET` | `TRUE` | the member may `SET ROLE` to this role |

So you can now, for example, grant a monitoring role membership with `WITH INHERIT TRUE, SET FALSE`: it uses the privileges but can never *become* the role (handy when becoming the role would change object ownership). psql's `\drg` shows the options:

```sql
GRANT support_team TO erin WITH INHERIT FALSE, SET FALSE;  -- erin: member, but cannot use or become it
\drg erin
```

```text
                    List of role grants
 Role name |  Member of   | Options | Grantor
-----------+--------------+---------+----------
 erin      | support_team |         | postgres
```

With neither `INHERIT` nor `SET`, `erin` is a member in name only — useful when membership itself is the signal (for example, membership in `pg_use_reserved_connections`) rather than a grant of privileges. You can verify effective powers with `pg_has_role('erin', 'support_team', 'USAGE' | 'MEMBER' | 'SET')`.

### 2.5 PG 16: CREATEROLE is no longer a near-superuser

Historically, `CREATEROLE` was dangerous far beyond its name. A role with `CREATEROLE` could create *any* role, grant *any* role (including superuser-adjacent predefined roles) to anyone, and change other roles' passwords — effectively a path to superuser. "Just give the junior DBA `CREATEROLE`" was a quiet privilege escalation.

PostgreSQL 16 fixed this. Now a `CREATEROLE` role:

- can only grant a role to someone if it has `ADMIN` on that role;
- automatically gets `ADMIN` on roles it creates itself (so it can manage *its own* roles freely);
- cannot grant attributes it does not possess (it cannot hand out `SUPERUSER`, `REPLICATION` or `BYPASSRLS`);
- can only change the password of roles it has `ADMIN` on.

See it refuse the old tricks:

```sql
CREATE ROLE role_admin LOGIN CREATEROLE;
SET ROLE role_admin;

CREATE ROLE frank LOGIN PASSWORD 'frank';   -- OK: role_admin gets ADMIN on frank automatically
ALTER ROLE bazaar_app PASSWORD 'hijacked';  -- its own roles only
CREATE ROLE root2 SUPERUSER;                -- cannot hand out an attribute it lacks
GRANT bazaar_app TO frank;                  -- no ADMIN on bazaar_app
```

```text
ERROR:  permission denied to alter role
DETAIL:  To change another role's password, the current user must have the CREATEROLE attribute and the ADMIN option on the role.
ERROR:  permission denied to create role
DETAIL:  Only roles with the SUPERUSER attribute may create roles with the SUPERUSER attribute.
ERROR:  permission denied to grant role "bazaar_app"
DETAIL:  Only roles with the ADMIN option on role "bazaar_app" may grant this role.
```

`CREATEROLE` is now a reasonable thing to grant to a team lead who manages their own team's roles, which it never was before 16.

---

## 3. Predefined roles

PostgreSQL ships a set of roles whose names start with `pg_`. They are groups you make roles members of to grant specific, carefully-scoped powers that would otherwise require superuser. `\du` hides them; list them with a catalog query:

```sql
SELECT rolname FROM pg_roles WHERE rolname LIKE 'pg\_%' ORDER BY 1;
```

```text
          rolname
-----------------------------
 pg_checkpoint
 pg_create_subscription
 pg_database_owner
 pg_execute_server_program
 pg_maintain
 pg_monitor
 pg_read_all_data
 pg_read_all_settings
 pg_read_all_stats
 pg_read_server_files
 pg_signal_backend
 pg_stat_scan_tables
 pg_use_reserved_connections
 pg_write_all_data
 pg_write_server_files
```

The ones worth knowing:

| Predefined role | Grants | Use for |
|---|---|---|
| `pg_read_all_data` | `SELECT` on every table/view/sequence in every schema, plus `USAGE` on schemas | A read-only auditor without maintaining per-table grants. |
| `pg_write_all_data` | `INSERT`/`UPDATE`/`DELETE` on everything (not `SELECT`!) | Rare; bulk-load tooling. |
| `pg_monitor` | reads all the `pg_stat_*` views and monitoring functions (it is a member of `pg_read_all_settings`, `pg_read_all_stats`, `pg_stat_scan_tables`) | A monitoring agent (postgres_exporter, [Module 17](../17-operations/)) without superuser. |
| `pg_signal_backend` | `pg_cancel_backend`/`pg_terminate_backend` on non-superuser backends | An operator who may stop runaway queries ([Module 17](../17-operations/)). |
| `pg_read_server_files`, `pg_write_server_files`, `pg_execute_server_program` | server-side file and program access for `COPY` | Dangerous; essentially filesystem access as the postgres OS user. Almost never granted. |
| `pg_maintain` (**PG 17**) | run `VACUUM`, `ANALYZE`, `CLUSTER`, `REINDEX`, `REFRESH MATERIALIZED VIEW`, `LOCK TABLE` on *all* tables | A maintenance job that is not the table owner. New in 17; before it, maintenance meant ownership or superuser. |
| `pg_use_reserved_connections` | use the slots reserved by `reserved_connections` | Monitoring agents that must connect even when the server is full. |

`pg_read_all_data` is the quickest way to make a safe auditor. It reads everything but writes nothing:

```sql
CREATE ROLE auditor LOGIN PASSWORD '...';
GRANT pg_read_all_data TO auditor;

\c - auditor
SELECT count(*) FROM users;                 --> 30
SELECT email FROM users WHERE id = 17;      --> Zoe.Martin@Example.COM  (reads ALL columns)
INSERT INTO categories (name) VALUES ('x'); --> ERROR: permission denied for table categories
```

Note the trade-off: `pg_read_all_data` reads *every* column of *every* table, so it is not a fit for the analyst who must not see `users.email`. That is why Bazaar uses a custom `bazaar_readonly` bundle with column-level control instead (§5, §7). Predefined roles are blunt; custom roles are precise. Use predefined roles when "all data" or "all stats" is genuinely what you mean.

---

## 4. Ownership, and why the app owns nothing

Every object — table, view, sequence, function, schema, type — has exactly one **owner**: by default the role that created it. The owner can do anything to the object (all privileges, implicitly) and, uniquely, can `ALTER` it, `DROP` it, change its ownership, grant privileges on it to others, and (for a table) change its RLS policies. Ownership is not a privilege you can `GRANT`; it is transferred with `ALTER ... OWNER TO`.

This is the single most important structural decision in the whole module: **in Bazaar, one role owns everything, and it is not the role the application connects as.**

```sql
-- From 1501, after creating the roles:
ALTER SCHEMA bazaar OWNER TO bazaar_owner;
-- ... then a DO block hands every table, view, matview, function and type to bazaar_owner.
```

Why go to the trouble? Because owner privileges cannot be revoked — they come with ownership. If `bazaar_app` owned `orders`, then no `REVOKE` could stop `bazaar_app` from running `DROP TABLE orders`, `ALTER TABLE orders DISABLE ROW LEVEL SECURITY`, or `TRUNCATE orders`. A SQL-injection bug or a leaked app password would have that power, permanently, by construction. By making `bazaar_owner` the owner and never logging in as it, the application's destructive ceiling is only what we explicitly `GRANT` — and we grant it `SELECT, INSERT, UPDATE, DELETE` and nothing structural. See it refuse:

```sql
\c - bazaar_app
DROP TABLE orders;                               --> ERROR: must be owner of table orders
TRUNCATE audit_log;                              --> ERROR: permission denied for table audit_log
ALTER TABLE products DISABLE ROW LEVEL SECURITY; --> ERROR: must be owner of table products
CREATE TABLE bazaar.scratch (x int);             --> ERROR: permission denied for schema bazaar
```

The application can run every query its features need, and nothing else. That is the whole point.

`bazaar_owner` is `NOLOGIN`. Nobody connects as it. Migrations run as a superuser or admin that does `SET ROLE bazaar_owner` first — which matters for default privileges, as we will see in §5.4. The `1501` header says exactly this.

---

## 5. The privilege system

Privileges are grants of specific actions on specific objects, managed with `GRANT` and `REVOKE`. Every kind of object has its own set of privilege types. A query is allowed only if the acting role holds the needed privilege at *every* level it touches: to read `bazaar.orders` you need `CONNECT` on the database, `USAGE` on the schema `bazaar`, and `SELECT` on the table. Miss any one and you get "permission denied" — or, for a missing schema `USAGE`, the confusing "relation does not exist", because without schema access the object is invisible to name resolution.

### 5.1 Database privileges

| Privilege | Allows |
|---|---|
| `CONNECT` | opening a session to this database |
| `TEMP` (a.k.a. `TEMPORARY`) | creating temporary tables/objects |
| `CREATE` | creating new *schemas* in the database |

`CONNECT` and `TEMP` are granted to `PUBLIC` by default (so any role can connect). The reference grants `CONNECT` explicitly anyway:

```sql
GRANT CONNECT ON DATABASE bazaar TO bazaar_app, bazaar_analyst, bazaar_seller;
```

Why, if `PUBLIC` already has it? Because explicit is robust: a hardened deployment often runs `REVOKE CONNECT ON DATABASE bazaar FROM PUBLIC` so that only named roles can connect, and then these grants are what keep Bazaar working. Documenting intent in the grant is cheap.

### 5.2 Schema privileges

| Privilege | Allows |
|---|---|
| `USAGE` | "looking into" the schema: referencing objects in it by name |
| `CREATE` | creating new objects in the schema |

`USAGE` is the gatekeeper. Without it, you cannot name anything in the schema, even objects you have table-level grants on. The reference gives `USAGE` (never `CREATE`) to the working roles:

```sql
GRANT USAGE ON SCHEMA bazaar TO bazaar_app, bazaar_readonly, bazaar_seller;
```

Only `bazaar_owner` has `CREATE` on the schema (it owns it). The application cannot plant new tables in `bazaar`; migrations, running as the owner, can.

### 5.3 Table privileges

| Privilege | Allows |
|---|---|
| `SELECT` | read rows (and, for `UPDATE`/`DELETE ... WHERE`, read the columns in the condition) |
| `INSERT` | add rows |
| `UPDATE` | change rows (implies the right to read the row being updated only via a `SELECT` grant for the `WHERE`) |
| `DELETE` | remove rows |
| `TRUNCATE` | empty the table (a fast, non-logged bulk delete; separate from `DELETE`) |
| `REFERENCES` | create a foreign key referencing this table |
| `TRIGGER` | create a trigger on this table |
| `MAINTAIN` (**PG 17**) | `VACUUM`/`ANALYZE`/`CLUSTER`/`REINDEX`/`REFRESH`/`LOCK` this table |

`SELECT`, `INSERT`, `UPDATE`, `DELETE` are the four you use daily. `TRUNCATE` is deliberately separate from `DELETE`: Bazaar grants the app `DELETE` but never `TRUNCATE`, so the app can remove rows one by one (firing triggers, respecting foreign keys) but cannot wipe a table in one non-logged statement that also bypasses the `orders.total` trigger. The application grants:

```sql
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA bazaar TO bazaar_app;
GRANT SELECT ON ALL TABLES IN SCHEMA bazaar TO bazaar_readonly;
```

`ALL TABLES IN SCHEMA` is a convenience that expands, *at grant time*, to every table that exists right now. It does **not** cover tables created later — that is what default privileges (§5.4) are for.

### 5.4 Default privileges and the "for role" trap

New objects start with no privileges except the owner's. So the grants in §5.3 cover today's tables, but the moment a migration adds `order_notes` next month, `bazaar_app` has no access to it, and you are back to hand-granting. `ALTER DEFAULT PRIVILEGES` fixes this: it records grants to apply *automatically* to objects created in the future.

```sql
ALTER DEFAULT PRIVILEGES FOR ROLE bazaar_owner IN SCHEMA bazaar
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO bazaar_app;
ALTER DEFAULT PRIVILEGES FOR ROLE bazaar_owner IN SCHEMA bazaar
    GRANT SELECT ON TABLES TO bazaar_readonly;
-- (plus sequences -> USAGE,SELECT and functions -> EXECUTE for bazaar_app)
```

Inspect them with `\ddp`:

```text
                     Default access privileges
    Owner     | Schema |   Type   |       Access privileges
--------------+--------+----------+--------------------------------
 bazaar_owner | bazaar | table    | bazaar_app=arwd/bazaar_owner  +
              |        |          | bazaar_readonly=r/bazaar_owner
 ...
```

Now the **critical subtlety**, and it bites everyone once. `FOR ROLE bazaar_owner` does *not* mean "grant to `bazaar_owner`". It means **these defaults apply to objects created *by* `bazaar_owner`**. Default privileges are keyed on the *creating* role. If a migration creates a table while connected as `postgres` (not as `bazaar_owner`), these defaults do not fire, and the app silently has no access to the new table.

Prove it. Create one table as the owner and one as postgres:

```sql
-- as postgres:
CREATE TABLE bazaar.gift_cards (code text PRIMARY KEY, balance numeric(12,2));
SET ROLE bazaar_owner;
CREATE TABLE bazaar.coupons (code text PRIMARY KEY, percent_off int);
RESET ROLE;
```

```sql
\c - bazaar_app
SELECT * FROM coupons;     --> works: created by bazaar_owner, defaults applied
SELECT * FROM gift_cards;  --> ERROR: permission denied for table gift_cards
```

`coupons`, created *by* `bazaar_owner`, inherited the default grants; `gift_cards`, created by `postgres`, did not. This is precisely why the `1501` header and `NOTES.md` both insist: **run migrations as `SET ROLE bazaar_owner`.** Do it and new tables, sequences and functions are automatically accessible to the app; forget it and every migration leaves landmines. (A new table also lands with `bazaar_owner` as its owner only if created by `bazaar_owner`; a table created by `postgres` is owned by `postgres`, breaking the "owner owns everything" invariant too.)

### 5.5 Column-level privileges: hiding users.email

Privileges can be narrowed to specific columns. This is how Bazaar keeps analysts out of `users.email` while letting them read everything else about users. The mechanics have one trap: a *column* grant is only consulted when there is no *table-level* grant for that privilege. So you cannot "subtract" a column from a table grant; you remove the table grant and add back the columns you want.

```sql
REVOKE SELECT ON bazaar.users FROM bazaar_readonly;
GRANT SELECT (id, full_name, is_active, created_at) ON bazaar.users TO bazaar_readonly;
```

Now the analyst (a member of `bazaar_readonly`) can read names but not e-mail. Crucially, a `SELECT *` or any reference to `email` fails, but column-restricted queries work:

```sql
SET ROLE bazaar_analyst;
SELECT id, full_name FROM users WHERE id = 17;   --> 17 | Zoë Martin
SELECT * FROM users LIMIT 1;                      --> ERROR: permission denied for table users
SELECT count(*) FROM users;                       --> 30   (count(*) needs no specific column)
SELECT count(*) FROM users WHERE email LIKE '%';  --> ERROR: permission denied for table users
```

`SELECT *` fails because `*` expands to include `email`. `count(*)` succeeds because it reads no particular column. A `WHERE email ...` fails because the condition reads `email`. This is exactly the behaviour you want: the column is genuinely unreadable, in filters as well as in output.

> **Pitfall:** column privileges stop you from *naming* the column, but they do not stop inference. An analyst who may read `id` and filter on it, and who can also see a unique constraint on `lower(email)`, can still test whether a given e-mail exists by catching the constraint violation on an insert — if they have `INSERT`. Analysts have only `SELECT`, so this is closed, but the general lesson (unique constraints leak existence) returns in §12.

Inspect column privileges and test them programmatically:

```sql
SELECT has_column_privilege('bazaar_analyst', 'bazaar.users', 'email', 'SELECT') AS can_email,
       has_column_privilege('bazaar_analyst', 'bazaar.users', 'full_name', 'SELECT') AS can_name,
       has_any_column_privilege('bazaar_analyst', 'bazaar.users', 'SELECT') AS can_any;
```

```text
 can_email | can_name | can_any
-----------+----------+---------
 f         | t        | t
```

### 5.6 Sequence and function privileges

**Sequences** (behind `GENERATED ... AS IDENTITY` and `serial` columns) have three privileges: `USAGE` (use `nextval`/`currval`), `SELECT` (read the current value), `UPDATE` (`setval`). The app gets `USAGE, SELECT` so `nextval` works for identity inserts via the sequence. (In practice, an `INSERT` into an `IDENTITY` column drives the sequence internally and does not require the inserting role to hold sequence `USAGE` — a detail that surfaces in the exercises.)

**Functions and procedures** have one privilege: `EXECUTE`. And here is a default that surprises people: **`PUBLIC` is granted `EXECUTE` on every new function automatically.** Any role can call any function unless you revoke it. For most functions that is harmless. For a function that does something privileged — a `SECURITY DEFINER` function (§8) — it is not, so you revoke `PUBLIC` and grant narrowly:

```sql
REVOKE EXECUTE ON FUNCTION create_events_partition(date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION create_events_partition(date) TO bazaar_app;
```

Table privileges still apply *inside* an ordinary (`SECURITY INVOKER`, the default) function: being able to `EXECUTE` `place_order()` does not let you write tables you have no `INSERT` on, because the function runs as *you*. So `bazaar_readonly` can call `place_order()` but it fails the moment the function tries to insert. `EXECUTE` is about calling; the privileges inside are still checked against the caller — unless the function is `SECURITY DEFINER`.

### 5.7 The PUBLIC pseudo-role and the PG 15 public-schema change

`PUBLIC` is not a role you can log in as; it is "every role, including future ones". A grant to `PUBLIC` is a grant to everybody. It is the right tool for things that genuinely are public (a lookup table every tenant may read), and a frequent source of accidental over-exposure otherwise.

The most important `PUBLIC` story is the `public` **schema** (unfortunately named: the schema `public` and the pseudo-role `PUBLIC` are different things). Before PostgreSQL 15, every database's `public` schema granted `CREATE` to `PUBLIC`, so *any* role could create objects in it. Combined with `search_path` putting `public` first, this was the engine behind a whole class of attacks (CVE-2018-1058): a low-privilege user plants a function or table in `public` that shadows one a higher-privilege user expects, and gets their code run with the victim's rights.

PostgreSQL 15 removed the default `CREATE` grant to `PUBLIC` on the `public` schema, and made it owned by `pg_database_owner` (a special role meaning "whoever owns this database"). Now ordinary roles can read-reference `public` but not create in it:

```sql
\dn+ public
```

```text
  Name  |       Owner       |           Access privileges            |      Description
--------+-------------------+----------------------------------------+------------------------
 public | pg_database_owner | pg_database_owner=UC/pg_database_owner+| standard public schema
        |                   | =U/pg_database_owner                   |
```

The `=U/...` line is the grant to `PUBLIC`: `USAGE` only, no `CREATE`. Bazaar keeps all its objects in a dedicated `bazaar` schema, which grants *nothing* to `PUBLIC` by default, so this hardening is belt-and-braces for Bazaar — but it is why `bazaar_app` cannot create a table even in `public` (you saw the error in §4).

### 5.8 Reading privileges: \dp, aclexplode, has_*_privilege

You need three ways to answer "who can do what":

**`\dp` (and `\z`)** shows a table's ACL in psql's compact notation. The letters are the privilege types, the role before `=` is the grantee (blank = `PUBLIC`), and the role after `/` is the grantor:

```sql
\dp orders
```

```text
 Schema |  Name  | Type  |         Access privileges          |       Column privileges        |  Policies
--------+--------+-------+------------------------------------+--------------------------------+-----------
 bazaar | orders | table | bazaar_owner=arwdDxtm/bazaar_owner+| status:                       +| ...
        |        |       | bazaar_app=arwd/bazaar_owner      +|   bazaar_seller=w/bazaar_owner+|
        |        |       | bazaar_readonly=r/bazaar_owner    +| ...                            |
        |        |       | bazaar_seller=r/bazaar_owner       |                                |
```

The privilege letters: `r`=SELECT (**r**ead), `a`=INSERT (**a**ppend), `w`=UPDATE (**w**rite), `d`=DELETE, `D`=TRUNCATE, `x`=REFERENCES, `t`=TRIGGER, `m`=MAINTAIN; for other object types `U`=USAGE, `C`=CREATE, `c`=CONNECT, `T`=TEMP, `X`=EXECUTE. So `bazaar_app=arwd` is "INSERT, SELECT, UPDATE, DELETE"; `bazaar_owner=arwdDxtm` is everything. Note `orders` has *column* privileges too: `bazaar_seller` has `w` (UPDATE) only on `status`, `shipped_at`, `delivered_at`.

**`\ddp`** shows default privileges (§5.4). **`\dconfig`**, `\du`, `\drg`, `\dn+` round out the set.

**`aclexplode`** turns the raw ACL array into rows, which is how you query privileges in SQL (for audits, for example):

```sql
SELECT a.grantee::regrole AS grantee, a.privilege_type, a.grantor::regrole
FROM pg_class c, aclexplode(c.relacl) a
WHERE c.oid = 'bazaar.orders'::regclass
ORDER BY 1, 2;
```

```text
     grantee     | privilege_type |   grantor
-----------------+----------------+--------------
 bazaar_app      | DELETE         | bazaar_owner
 bazaar_app      | INSERT         | bazaar_owner
 ...
 bazaar_readonly | SELECT         | bazaar_owner
 bazaar_seller   | SELECT         | bazaar_owner
```

**`has_table_privilege`**, `has_column_privilege`, `has_schema_privilege`, `has_database_privilege`, `has_function_privilege`, `has_sequence_privilege` answer a yes/no question for any role, following membership and `PUBLIC`. They are the building blocks of the privilege-matrix query you will write in the exercises:

```sql
SELECT has_table_privilege('bazaar_seller', 'bazaar.orders', 'UPDATE') AS can_update,  --> t (it has column UPDATE)
       has_table_privilege('bazaar_seller', 'bazaar.orders', 'DELETE') AS can_delete;  --> f
```

---

## 6. The Bazaar least-privilege layout

Putting §2–§5 together, here is the reference design, which you will reproduce in the milestone. Each role has a job and exactly the privileges that job needs.

```
                         bazaar_owner  (NOLOGIN)
                         owns schema + every object; subject to no RLS
                         migrations run as: SET ROLE bazaar_owner
                               │ grants
      ┌────────────────┬───────┴────────┬──────────────────────┐
      ▼                ▼                ▼                      ▼
 bazaar_app       bazaar_readonly   bazaar_seller          (default privileges
 (LOGIN, 50)      (NOLOGIN group)   (LOGIN)                 keep this current for
 S/I/U/D all      SELECT all        catalog SELECT;         future objects)
 tables;          tables            S/I/U products+inv;
 EXECUTE funcs;                     S orders/items/reviews;
 USAGE seqs            │            UPDATE(status,shipped_at,
                       ▼            delivered_at) orders;
                 bazaar_analyst     scoped to its seller by RLS (§10)
                 (LOGIN, member
                  of readonly;
                  no users.email)
```

| Role | Login | Owns | Can do | Cannot |
|---|---|---|---|---|
| `bazaar_owner` | no | the schema and everything in it | anything (owner) | — (used only via `SET ROLE` for migrations) |
| `bazaar_app` | yes (≤50) | nothing | `SELECT/INSERT/UPDATE/DELETE` all tables, `EXECUTE` functions, use sequences | `DROP`, `TRUNCATE`, `ALTER`, DDL, write `audit_log` (milestone §13) |
| `bazaar_readonly` | no (group) | nothing | `SELECT` all tables (except `users.email`, milestone) | anything that writes |
| `bazaar_analyst` | yes | nothing | everything `bazaar_readonly` can, by membership | read `users.email`; write |
| `bazaar_seller` | yes | nothing | see/edit *only its own* products, inventory, orders (fulfilment columns), via RLS | see other sellers; `DELETE` products; touch non-fulfilment order columns |

The seller role is the most interesting, because privileges alone cannot express "only your own rows" — that is what RLS is for (§10). The seller's *table and column* privileges say what *kinds* of things it may do (read products, update only `status`/`shipped_at`/`delivered_at` on orders, no `DELETE`); RLS says *which rows*. Both apply, independently.

---

## 7. SECURITY DEFINER: controlled privilege escalation

Sometimes a role must do something it does not itself have the privilege for, but only in a controlled way. The seller portal must not be able to write `audit_log` directly (no faking history), yet changing a product *must* write an audit row. The app should be able to refresh a materialized view it does not own. A cron job should create next month's `events` partition without owning the parent.

A **`SECURITY DEFINER`** function is the tool. By default a function is `SECURITY INVOKER`: it runs with the *caller's* privileges. A `SECURITY DEFINER` function runs with the *owner's* privileges instead — like Unix setuid. The caller borrows the owner's rights for the duration of the call, but only to do exactly what the function's body does. Bazaar uses it in three places:

```sql
-- audit rows get written no matter who changes a product/order, but only the
-- owner may write audit_log directly:
ALTER FUNCTION audit_trigger() SECURITY DEFINER SET search_path = bazaar, pg_temp;

-- the app can prepare next month's partition (requires owning the parent):
ALTER FUNCTION create_events_partition(date) SECURITY DEFINER SET search_path = bazaar, pg_temp;

-- the app can refresh a matview it does not own:
CREATE FUNCTION refresh_seller_sales_daily() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = bazaar, pg_temp
AS $$ BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY seller_sales_daily; END $$;
ALTER FUNCTION refresh_seller_sales_daily() OWNER TO bazaar_owner;
REVOKE EXECUTE ON FUNCTION refresh_seller_sales_daily() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION refresh_seller_sales_daily() TO bazaar_app;
```

Notice the pattern around each one: `SET search_path = ...`, `OWNER TO bazaar_owner`, `REVOKE ... FROM PUBLIC`, `GRANT ... TO` the specific role. The first of these is not optional, and the next section explains the attack it blocks.

### 8. The search_path hijack, and the fix

> **This is the most important security pitfall in all of PostgreSQL. Read it twice.**

A `SECURITY DEFINER` function runs as its owner. If its body refers to an object by an *unqualified* name — `lower(email)`, `orders`, `=` — PostgreSQL resolves that name using the *caller's* `search_path`, which the caller controls. So a malicious caller can put a schema they control first on the path, define an object there that shadows the one the function meant, and have *their* code run with the *owner's* privileges. That is a full privilege escalation.

Let us actually do it. Here is a well-meaning `SECURITY DEFINER` function, owned by `bazaar_owner`, that checks whether an e-mail is taken (so the app can call it without having `SELECT` on `users.email`):

```sql
SET ROLE bazaar_owner;
CREATE FUNCTION bazaar.email_taken(p_email text)
RETURNS boolean LANGUAGE sql SECURITY DEFINER
AS $$ SELECT EXISTS (SELECT 1 FROM users WHERE lower(email) = lower(p_email)); $$;
RESET ROLE;
```

It looks innocent. But `lower(...)` and `users` are unqualified, and there is no pinned `search_path`. Now the attacker — here `bazaar_analyst`, who may create a schema of their own (common, and harmless-looking) — plants a trap:

```sql
\c - bazaar_analyst
CREATE SCHEMA evil;    -- or any schema the attacker can create in / write to
CREATE FUNCTION evil.lower(text) RETURNS text LANGUAGE plpgsql AS $$
BEGIN
    UPDATE bazaar.products SET price = 0.01 WHERE id = 3;   -- runs as bazaar_owner!
    RETURN pg_catalog.lower($1);
END $$;
GRANT USAGE ON SCHEMA evil TO PUBLIC;

SET search_path = evil, pg_catalog, bazaar;   -- put the trap first
SELECT bazaar.email_taken('zoe.martin@example.com');   --> t   (looks normal)
SELECT price FROM bazaar.products WHERE id = 3;          --> 0.01
```

The analyst has no `UPDATE` on `products`. But inside `email_taken`, the unqualified `lower()` resolved to `evil.lower()` (first on the caller's path), which ran `UPDATE bazaar.products ...` *with `bazaar_owner`'s privileges*. The product's price is now one cent. The audit log even blames `bazaar_analyst` (the `session_user`), which is true but no comfort.

The exact same attack works through a hijacked **operator** (operators are functions too): plant `evil.=(bigint,bigint)` and put `evil` first, and `WHERE seller_id = p_seller_id` inside a definer function runs your code. And through a shadowing **table** in `pg_temp` if the function reads an unqualified table name and `pg_temp` is early on the path.

**The fix** is to pin the function's `search_path` so the caller cannot influence name resolution:

```sql
ALTER FUNCTION bazaar.email_taken(text) SET search_path = pg_catalog, bazaar, pg_temp;
```

Re-run the attack and the trap is dead: `lower` resolves to `pg_catalog.lower`, `users` to `bazaar.users`, regardless of what the caller sets:

```sql
\c - bazaar_analyst
SET search_path = evil, pg_catalog, bazaar;
SELECT bazaar.email_taken('zoe.martin@example.com');   --> t
SELECT price FROM bazaar.products WHERE id = 3;          --> 2199.99  (untouched)
```

Every `SECURITY DEFINER` function in Bazaar carries `SET search_path = bazaar, pg_temp` (or `pg_catalog, bazaar, pg_temp`) for exactly this reason. The rules, in order of strength:

1. **Always `SET search_path`** on a `SECURITY DEFINER` function, to a fixed list you control.
2. Put `pg_catalog` first (or rely on it being implicitly first) so built-ins can't be shadowed, and put `pg_temp` **last** — if `pg_temp` is early, a caller can shadow a table with a temp table. (PG puts `pg_temp` implicitly first otherwise; naming it last is the safe habit.)
3. Even better, **schema-qualify every name** in the body (`pg_catalog.lower`, `bazaar.users`). Belt and braces.
4. Keep the function body minimal and its `EXECUTE` grant narrow (`REVOKE ... FROM PUBLIC`).

> **Pitfall:** `SET search_path = ''` (empty) is sometimes recommended, forcing *everything* to be schema-qualified. It is the most secure setting but it breaks any unqualified reference, including to your own schema, so Bazaar uses an explicit minimal list instead. Whatever you choose, never leave it unset on a `SECURITY DEFINER` function.

---

## 9. Views as a security boundary

A view runs, by default, with the privileges of its *owner*, not the caller (like a `SECURITY DEFINER` function). This makes views a natural way to expose a safe slice of a table: grant a role `SELECT` on the view, not on the underlying table, and it sees only what the view selects. The masked-users view in the milestone works this way — `bazaar_owner` owns it and can read `email`, so the view can compute an e-mail *domain*, but the analyst (who has `SELECT` on the view, not the table) never sees the address.

But there is a subtlety that is itself a security issue: a plain view is not a hard boundary against a *determined* caller, because the planner may push the caller's `WHERE` conditions down *below* the view's own filter. If the caller's condition calls a cheap, side-effecting function, it can observe rows the view was supposed to hide.

### 9.1 security_barrier

Consider a view that exposes only active users' names, and a seller who may query it but has no access to `users`:

```sql
CREATE VIEW bazaar.reviewer_names AS
SELECT u.id, u.full_name FROM bazaar.users u
WHERE u.is_active AND EXISTS (SELECT 1 FROM bazaar.reviews r WHERE r.user_id = u.id);
```

A curious seller defines a cheap leaky function and filters the view through it:

```sql
CREATE FUNCTION pg_temp.peek(text) RETURNS boolean LANGUAGE plpgsql COST 0.0000001
AS $$ BEGIN RAISE NOTICE 'saw: %', $1; RETURN true; END $$;

SELECT count(*) FROM reviewer_names WHERE pg_temp.peek(full_name);
```

Because `peek()` is marked as extremely cheap, the planner evaluates it *before* the view's `is_active`/`EXISTS` filter, so the `NOTICE`s leak the names of *inactive* users and users with no reviews too — rows the view exists to hide. `EXPLAIN` shows the leak: the `peek` filter sits on the raw `Seq Scan on users`.

Mark the view `security_barrier` and the planner may not push a non-leakproof qualifier below the view's own conditions:

```sql
ALTER VIEW bazaar.reviewer_names SET (security_barrier = true);
```

Now `peek()` runs only on rows that already passed the view's filter (`EXPLAIN` shows a `Subquery Scan` wrapping the view with `peek` *above* it). The trade-off is that a barrier can block some optimizations; a genuinely index-friendly condition on a leakproof operator (`id = 8`) still pushes through, because leakproof functions are safe to evaluate early (§12 covers leakproofness). Use `security_barrier` on any view whose whole purpose is to hide rows from the people who can query it.

### 9.2 security_invoker (PG 15+)

The owner-privilege behaviour of views has a consequence for RLS: because a view runs as its owner, a view over an RLS-protected table is filtered by the *owner's* policies, not the caller's. The storefront view `active_products` is owned by `bazaar_owner` (who is subject to no policy), so a seller querying it sees *all* sellers' active products — correct for a public storefront, wrong if you wanted the seller's own rows.

PostgreSQL 15 added `security_invoker` views, which run with the *caller's* privileges and RLS context:

```sql
SELECT count(DISTINCT seller_id) FROM active_products;   -- seller sees all 5 sellers' products

ALTER VIEW bazaar.active_products SET (security_invoker = true);
-- now, as bazaar_seller with app.seller_id = 2:
SELECT count(DISTINCT seller_id) FROM active_products;   -- sees 1 seller: their own
```

With `security_invoker = true`, the view's reads happen as the caller, so the caller needs `SELECT` on the underlying tables *and* the caller's RLS policies apply. This is the right choice for a view that should respect each caller's row-level access (a seller-facing report), and the wrong choice for a view that is deliberately a privilege boundary (the masked-users view, which must run as the owner to read `email`). Pick per view, consciously.

---

## 10. Row-level security

Everything so far decides whether a role may touch an object. Row-level security decides *which rows* of a table the role may see or change, with policies written in SQL. It is how Bazaar gives 2,000 sellers a shared `products` table while guaranteeing each sees only its own — enforced by the database, so a bug in the portal cannot leak across tenants.

### 10.1 Enabling RLS, and the fail-closed default

RLS is off until you turn it on per table:

```sql
ALTER TABLE products ENABLE ROW LEVEL SECURITY;
```

The instant you do, the table **fails closed** for everyone who is not the owner (and not a superuser or `BYPASSRLS` role): with RLS enabled and *no* policy, non-owners see zero rows and can change none. A policy then *adds back* access. This is the opposite of privileges (which are deny-by-default but, once granted, allow everything); RLS is "enabled = deny all rows, then policies permit some". The consequence shows up immediately: because the application and read-only roles must still see everything, they need explicit "allow all" policies, or RLS would lock them out too:

```sql
CREATE POLICY products_all_access ON products
    TO bazaar_app, bazaar_readonly USING (true) WITH CHECK (true);
```

(The alternative would be `ALTER ROLE bazaar_app BYPASSRLS`, but that is a blunt, cluster-wide attribute; an explicit `USING (true)` policy is clearer and scoped to the table.)

### 10.2 Anatomy of a policy

```sql
CREATE POLICY products_seller ON products
    FOR ALL                                      -- which commands
    TO bazaar_seller                             -- which roles
    USING (seller_id = current_seller_id())      -- which existing rows are visible/affectable
    WITH CHECK (seller_id = current_seller_id()); -- what new/changed rows are allowed to become
```

Four parts:

- **`FOR` command**: `ALL`, `SELECT`, `INSERT`, `UPDATE`, or `DELETE`. A policy applies only to its command(s). `FOR ALL` covers all four.
- **`TO` roles**: which roles the policy applies to. A policy with `TO bazaar_seller` is simply invisible to other roles — it neither grants nor denies them anything. (A role sees the union of the policies that name it, plus policies with `TO PUBLIC`.)
- **`USING`**: a boolean expression evaluated against each *existing* row. For `SELECT`/`UPDATE`/`DELETE`, rows where it is false (or NULL) are invisible — as if they weren't there. This is the *read/affect* filter.
- **`WITH CHECK`**: a boolean expression evaluated against each *new* row (from `INSERT`, or the post-image of an `UPDATE`). If it is false, the statement errors with "new row violates row-level security policy". This is the *write* filter. If omitted, `USING` is reused as `WITH CHECK`.

The split between `USING` and `WITH CHECK` is what lets you say "you can see these rows, but you can't create rows outside this set" — a seller can read and edit its own products (`USING`), and cannot insert a product for, or move a product to, another seller (`WITH CHECK`).

### 10.3 The multi-tenant pattern: current_setting + SET LOCAL

The portal connects as the single role `bazaar_seller` for all sellers (one pooled credential), and tells the database *which* seller this request is for using a custom GUC (session variable):

```sql
CREATE FUNCTION current_seller_id() RETURNS bigint
LANGUAGE sql STABLE PARALLEL SAFE
RETURN nullif(current_setting('app.seller_id', true), '')::bigint;
```

`current_setting('app.seller_id', true)` reads the setting; the `true` ("missing_ok") means "return NULL instead of erroring if it is unset". The `nullif(..., '')` turns the empty string (what you get after a `RESET`) into NULL too. So if the setting is absent or blank, `current_seller_id()` is NULL, every `seller_id = NULL` comparison is NULL (not true), and the seller sees **nothing**. Fail-closed by construction: forget to set it and you leak zero rows, never all of them.

The portal sets it **per transaction** with `SET LOCAL` (or `set_config(..., true)`):

```sql
BEGIN;
SET LOCAL app.seller_id = '2';     -- scoped to THIS transaction; auto-reset at COMMIT/ROLLBACK
SELECT count(*) FROM products;     --> 15  (only seller 2's)
SELECT count(*) FROM orders;       --> 36
COMMIT;
```

Observe the complete behaviour as `bazaar_seller`:

```sql
SELECT count(*) FROM products;     -- no setting: 0 rows. Fail closed.

BEGIN;
SET LOCAL app.seller_id = '2';
SELECT count(*) FROM products;                              --> 15
UPDATE products SET price = price + 1 WHERE id = 1;         --> UPDATE 0   (product 1 is seller 1's: invisible)
UPDATE products SET price = price + 1 WHERE id = 20;        --> UPDATE 1   (product 20 is seller 2's)
UPDATE products SET seller_id = 1 WHERE id = 20;            --> ERROR: new row violates row-level security policy
INSERT INTO products (seller_id, ...) VALUES (1, ...);      --> ERROR: new row violates row-level security policy
INSERT INTO products (seller_id, ...) VALUES (2, ...);      --> INSERT 0 1 (allowed: it's their own)
ROLLBACK;
```

The seller sees and edits only seller 2's rows (`USING`), cannot reassign a product to seller 1 or insert one for them (`WITH CHECK`), and an `UPDATE` touching an invisible row simply affects zero rows rather than erroring — the row isn't there, from the seller's point of view.

> **Pitfall — this is critical with connection pools.** Use `SET LOCAL`, not `SET`. A plain `SET app.seller_id = '2'` lasts for the whole *session*. Behind a transaction-mode pooler like PgBouncer ([Module 17 §11](../17-operations/#11-connection-pooling-with-pgbouncer)), the next request — *for a different seller* — may reuse the same backend and inherit the leftover setting, showing seller 2's data to seller 3. `SET LOCAL` is reset automatically at the end of the transaction, so it cannot leak across requests. Demonstrated:
>
> ```sql
> -- WRONG, on a pooled connection:
> SET app.seller_id = '1'; SELECT count(*) FROM orders;   --> 38  (seller 1)
> -- next request reuses the backend and forgets to set it:
> SELECT count(*) FROM orders;                             --> 38  STILL seller 1's data. Leak.
> ```
>
> This is the same session-state hazard Module 17 catalogues for pooling, and it returns in [Module 27](../27-python-production/) when the Python service wires RLS to PgBouncer. The rule is absolute: **tenant scoping goes in `SET LOCAL` inside the request's transaction, never in a session `SET`.**

### 10.4 Policies on a table without the tenant key

`inventory` has no `seller_id`; it links to `products`. Its policy reaches through:

```sql
CREATE POLICY inventory_seller ON inventory FOR ALL TO bazaar_seller
    USING (EXISTS (SELECT 1 FROM products p
                   WHERE p.id = inventory.product_id AND p.seller_id = current_seller_id()))
    WITH CHECK (...same...);
```

The subquery on `products` itself runs under the seller's policy on `products`, so it is doubly correct and the planner can use the products index. `order_items` reaches `orders` the same way. (§12 and the exercises explore the performance of such subquery policies, and when to denormalize the tenant key instead.)

### 10.5 How policies combine: PERMISSIVE and RESTRICTIVE

A table can have many policies. By default they are **permissive** and combined with **OR**: a row is visible if *any* applicable permissive policy's `USING` is true. That is why the app's `USING (true)` policy and the seller's `USING (seller_id = ...)` policy coexist — each role matches different policies via `TO`, and for a role that matched several, any one allowing the row is enough.

**Restrictive** policies (`AS RESTRICTIVE`) are combined with **AND**: a row must satisfy *every* applicable restrictive policy *and* at least one permissive policy. Restrictive policies can only ever *subtract* access; they are how you add an extra condition on top without widening anything. Example (from the exercises): sellers may edit their products, but not while a product is archived:

```sql
CREATE POLICY products_seller_not_archived ON products
    AS RESTRICTIVE FOR UPDATE TO bazaar_seller
    USING (status <> 'archived')   -- ANDed with the permissive products_seller policy
    WITH CHECK (true);             -- the new row may become anything (so archiving IS allowed)
```

The effective rule for a seller's `UPDATE` is now `(seller_id = current_seller_id())` **AND** `(status <> 'archived')`. An archived product is unaffectable; an active one can be edited and even archived (the `WITH CHECK (true)` permits the post-image). Omit the `WITH CHECK` and the restrictive `USING` is reused as the check, which would also forbid *archiving* an active product — a classic mistake. Always think about `USING` (which rows can I act on) and `WITH CHECK` (what may they become) separately for restrictive policies.

### 10.6 The owner bypass and FORCE ROW LEVEL SECURITY

The table owner is **not** subject to RLS by default. `bazaar_owner` sees all 62 products regardless of policies — which is deliberate: migrations and `SECURITY DEFINER` functions owned by `bazaar_owner` must see whole tables. If you *want* the owner filtered too (for a system with no "god" role), use `FORCE ROW LEVEL SECURITY`:

```sql
ALTER TABLE products FORCE ROW LEVEL SECURITY;
-- now even bazaar_owner sees 0 products, because no policy targets it
```

Bazaar deliberately does **not** use `FORCE`. The `1501` comment spells out the reason: with `FORCE`, `bazaar_owner` would be subject to the policies, no policy targets `bazaar_owner`, and so every `SECURITY DEFINER` function owned by it — and every migration run as it — would suddenly see empty tables. (A `BYPASSRLS` role is the other escape hatch: it ignores RLS everywhere, even with `FORCE`. Bazaar gives `BYPASSRLS` to no one.) The matrix:

| Acting role | RLS enabled, no `FORCE` | RLS enabled + `FORCE` |
|---|---|---|
| table owner | sees all rows | subject to policies |
| superuser / `BYPASSRLS` | sees all rows | sees all rows |
| other roles | subject to policies | subject to policies |

A session can also turn RLS off *for itself* with `SET row_security = off` — but this is not a bypass: if a query would then touch an RLS table the role is not the owner of, it **errors** ("query would be affected by row-level security policy") rather than silently showing everything. It exists so a tool can refuse to run rather than get filtered results unknowingly.

---

## 11. (Covered above) — tenant scoping and pooling

The pooling hazard in §10.3 is important enough that it is worth restating as its own rule, because it is where real multi-tenant RLS systems break: **the tenant setting lives in the transaction (`SET LOCAL`), the connection string, or a per-request `SET ROLE` to a tenant-specific role — never in session state that a pooled backend carries to the next client.** [Module 17 §11](../17-operations/#11-connection-pooling-with-pgbouncer) lists every kind of session state that leaks in transaction mode; RLS tenant GUCs are one more, and the most dangerous because the failure is silent and cross-tenant. [Module 27](../27-python-production/) builds the Python side.

---

## 12. RLS and performance, and the pitfalls

### 12.1 The policy predicate is added to every query

A policy's `USING` expression is `AND`ed into *every* query against the table, as an extra filter. You can see it in the plan — here `bazaar_seller` counting its orders on the large dataset:

```sql
SET app.seller_id = '1';
EXPLAIN (ANALYZE, BUFFERS) SELECT count(*) FROM orders;
```

```text
 Aggregate ...
   ->  Index Only Scan using orders_seller_id_created_at_idx on orders (rows=69805)
         Index Cond: (seller_id = (NULLIF(current_setting('app.seller_id'::text, true), ''::text))::bigint)
         Heap Fetches: 0
 Execution Time: 14.2 ms
```

Two lessons. First, **the policy predicate must be indexable**, or RLS turns every query into a full scan. `orders` has `(seller_id, created_at)`, so the `seller_id = ...` predicate becomes an index scan: 14 ms for a seller with 70k orders, 0.04 ms for a small seller. Without that index it would be a 500k-row seq scan on every seller query. **RLS raises the importance of indexing the tenant key.** The same `orders` query run as `bazaar_owner` (not subject to RLS) does a parallel seq scan + sort because there is no `seller_id` filter to exploit — the policy actually makes the seller's own view *faster*.

Second, the subquery policies (`inventory`, `order_items`) can be expensive. `SELECT count(*) FROM order_items` as a seller becomes a seq scan of 1.27M rows with a hashed subquery, ~500 ms for a big seller, because `order_items` has no `seller_id` of its own. The exercises measure this and explore two fixes: a `SECURITY DEFINER` function returning the seller's order-id array (fast for small sellers, planning-heavy for big ones), and **denormalizing `seller_id` onto `order_items`** with an index, which turns the policy into a plain indexable `seller_id = current_seller_id()` and the count into a 22 ms index-only scan. In a real multi-tenant schema, carrying the tenant key on every table is usually worth the storage precisely so RLS stays indexable.

### 12.2 Leakproof functions and the information leak

There is a second, subtler performance-and-security interaction. When a query has both a user condition and an RLS policy predicate, PostgreSQL will not let a *non-leakproof* user condition run before the policy filter — because a leaky operator (one that can reveal its arguments via an error message or side effect) could expose values from rows the policy should have hidden. A function is **leakproof** if it reveals nothing about its arguments except its return value (no data-dependent errors). `=` on integers is leakproof; `@@` (full-text match), `~~` (`LIKE`), `jsonb` containment and many others are **not**.

The consequence: a seller searching their products with full-text search can be *slower* than the owner doing the same search, because the fast GIN index on `search_vector` uses the non-leakproof `@@` operator, which the planner refuses to apply before the `seller_id` RLS filter. So the plan filters by `seller_id` first (via the `(seller_id, sku)` index) and applies `@@` as a post-filter, instead of using the GIN index:

```text
-- as bazaar_seller (seller 1), "wireless headphones":
 Bitmap Heap Scan on products  (rows=53)
   Recheck Cond: (seller_id = ...)              <- RLS predicate, indexable, applied first
   Filter: (search_vector @@ '...'::tsquery)    <- @@ not leakproof: cannot use the GIN index
   Rows Removed by Filter: 18447
 Execution Time: 100 ms
-- as bazaar_app (no RLS), same query: uses the GIN index, 3.6 ms
```

You *can* mark an operator `LEAKPROOF` (superuser only) if you are certain it leaks nothing — `ALTER FUNCTION pg_catalog.ts_match_vq(tsvector,tsquery) LEAKPROOF` restores the `BitmapAnd` of both indexes and drops the seller query to ~4 ms — but do it only after genuinely auditing that the function cannot leak via errors, and know you are changing a cluster-wide, security-relevant default. For most schemas the better answer is to make the RLS predicate itself highly selective and indexed (a small seller's search is already sub-millisecond) and accept the post-filter for large tenants.

### 12.3 Pitfalls: FK checks, unique leaks, side channels

RLS protects ordinary queries, but several mechanisms see *around* it. You must know them.

**Foreign-key checks bypass RLS.** The referential-integrity checks PostgreSQL runs on your behalf are *not* subject to policies — they must see all rows to do their job. This leaks existence: inserting a child row whose FK points at a parent you cannot see succeeds or fails based on whether the parent *exists*, not whether you can see it. If a seller could insert an `order_notes(order_id)` row, the FK error vs. success tells them whether *any* seller's order has that id:

```sql
-- as bazaar_seller, seller 2, with a table FK-referencing orders:
INSERT INTO order_notes (seller_id, order_id, note) VALUES (2, 40, 'probe');   --> INSERT 0 1
-- order 40 is seller 1's and invisible to seller 2, yet the FK check passed: now they know it exists
INSERT INTO order_notes (seller_id, order_id, note) VALUES (2, 4000, 'probe'); --> ERROR: FK violation
-- a non-existent id errors differently: an oracle for "does order N exist?"
```

The fix is to add the existence check *into the policy's* `WITH CHECK`, which *does* run under RLS, so it fails the same way whether the order is another seller's or absent:

```sql
CREATE POLICY order_notes_seller ON order_notes TO bazaar_seller
    USING (seller_id = current_seller_id())
    WITH CHECK (seller_id = current_seller_id()
                AND EXISTS (SELECT 1 FROM orders WHERE id = order_id));  -- EXISTS runs under RLS
```

**Unique constraints leak existence.** A unique index is enforced across *all* rows, visible or not. If `products.sku` were globally unique, a seller inserting a SKU that another seller already uses gets a duplicate-key error — revealing that *someone* has that SKU, even though the row is invisible to them. (Bazaar's SKU uniqueness is per-seller — `(seller_id, sku)` — which sidesteps this; a *globally* unique natural key in a multi-tenant table is a leak you must weigh.)

**Statistical and metadata side channels.** RLS hides *rows*, not *aggregates maintained outside the row set* nor *metadata*. A seller can learn things the policy "hid" through channels that don't go through the policy:

- `reltuples` in `pg_class`, `n_live_tup`/`n_tup_ins` in `pg_stat_user_tables`, and `pg_relation_size` reveal the *whole table's* size, not the tenant's slice.
- `EXPLAIN` (without `ANALYZE`) shows the planner's *total* row estimate for the table, not the policy-filtered count.
- `nextval()`/`currval()` on a shared sequence, and `max(id)` across visible rows, leak the global id high-water mark and thus the marketplace's overall activity.
- The optimizer statistics in `pg_stats` are visible to roles with the right grants and describe the whole column.

None of these let a seller read another seller's *data*, but they leak *volume* and *existence*. Mitigations: revoke what you can (Bazaar revokes sequence `USAGE` from the seller in an exercise, closing the `nextval` channel; identity inserts still work because they drive the sequence internally), keep sensitive aggregates behind `SECURITY DEFINER` functions that apply their own filter, and accept that RLS is a *row* boundary, not an *information-theoretic* one. For true isolation (not just row filtering), separate databases or schemas per tenant are the heavier alternative — discussed as a scaling option in [Module 21](../21-sharding/).

---

## 13. Authentication: who gets in, and how

Everything above assumes you are already connected *as* some role. Deciding which role a connection may be, from where, and how it must prove itself, happens *before* any SQL, in a file read top-to-bottom at connection time: **`pg_hba.conf`** (Host-Based Authentication).

### 13.1 pg_hba.conf, line by line

Each non-comment line is a rule with five fields:

```
# TYPE    DATABASE   USER         ADDRESS         METHOD [options]
local     all        all                          peer
host       bazaar     bazaar_app   10.0.0.0/8      scram-sha-256
hostssl    bazaar     +lab_users   0.0.0.0/0       scram-sha-256
hostssl    bazaar     alice        0.0.0.0/0       cert
```

| Field | Meaning |
|---|---|
| **TYPE** | How the client connects: `local` (Unix socket), `host` (TCP, SSL or not), `hostssl` (TCP, SSL **required**), `hostnossl` (TCP, SSL **forbidden**). |
| **DATABASE** | Which database(s): a name, `all`, `replication` (physical replication connections — these are *not* matched by `all`), or a comma list. |
| **USER** | Which role(s): a name, `all`, a comma list, or `+groupname` (any member of the group). |
| **ADDRESS** | For `host*` lines: a CIDR (`10.0.0.0/8`), `samehost`, `samenet`, `all`, or a hostname. Blank for `local`. |
| **METHOD** | How the client must authenticate: `trust`, `peer`, `scram-sha-256`, `md5`, `cert`, `reject`, and others. |

### 13.2 First match wins

The decisive rule: PostgreSQL uses the **first** line whose TYPE, DATABASE, USER and ADDRESS all match the connection attempt, and applies *that* line's method. It does **not** fall through on failure. If the matched line's method fails (wrong password, bad certificate), the connection is **refused** — later matching lines are never tried. And if *no* line matches, the connection is refused with "no pg_hba.conf entry for host ...".

This makes ordering a security control. A `reject` line early shuts a door no later line can reopen; a broad `all all ... trust` line early makes everything below it irrelevant. Put the most specific and most restrictive rules first. For example, to forbid the superuser over the network while allowing it locally, the `hostssl all postgres ... reject` line must come *before* any `hostssl all all ...` line.

Inspect the active rules — and catch typos — without reading the file, via `pg_hba_file_rules`:

```sql
SELECT line_number, type, database, user_name, address, auth_method, error
FROM pg_hba_file_rules;
```

```text
 line_number | type  |   database    | user_name | address |  auth_method  | error
-------------+-------+---------------+-----------+---------+---------------+-------
         117 | local | {all}         | {all}     |         | trust         |
         128 | host  | {all}         | {all}     | all     | scram-sha-256 |
```

An `error` value flags a malformed line (the default course container's own file is the trusting one above — fine for a laptop, never for production). `pg_hba.conf` is re-read on **reload** (`SELECT pg_reload_conf()` or `SIGHUP`), not restart; [Module 17](../17-operations/) covers that.

### 13.3 The methods

| Method | How it proves identity | Use when |
|---|---|---|
| `trust` | no check at all; the claimed role is accepted | Only a socket/loopback on a single-user machine. The course container uses it for `local`. **Never** on a network. |
| `peer` | the client's OS username (from the kernel, for `local` only) must match (or map to) the DB role | Local admin on the server host. The official image's entrypoint relies on `peer` for `postgres` over the socket. |
| `scram-sha-256` | SCRAM challenge-response; the password is never sent, only a proof | The default and correct choice for password auth. The server stores a non-reversible verifier. |
| `md5` | legacy challenge-response over an MD5 hash | **Deprecated.** Weaker; the stored hash is effectively password-equivalent and the scheme has known weaknesses. Only for old clients that can't do SCRAM. |
| `cert` | a client TLS certificate whose CN (or a mapped name) is the role; no password | `hostssl` only. Mutual TLS: the client proves identity with a key, not a secret the server stores. |
| `reject` | always fails | A deliberate door-slam, placed early. |
| `ldap`, `gss`, `radius`, ... | external directory / Kerberos / etc. | Enterprise SSO. Out of scope here. |

### 13.4 password_encryption, and migrating md5 → scram

How a password is *stored* is set by `password_encryption` (default `scram-sha-256` since PG 14). It takes effect when a password is *set*, so changing the parameter does nothing to existing passwords — each role is re-hashed only when you next `ALTER ROLE ... PASSWORD`. See the two formats:

```sql
SELECT rolname, left(rolpassword, 14) FROM pg_authid WHERE rolname IN ('bazaar_app', 'legacy');
```

```text
  rolname   |      left
------------+----------------
 bazaar_app | SCRAM-SHA-256$
 legacy     | md5114b0bf6035
```

Migrating a cluster off md5 is a two-step dance, and the order matters:

1. Set `password_encryption = scram-sha-256` (it already is by default) and **re-set every role's password** so a SCRAM verifier is stored: `ALTER ROLE x PASSWORD '<same or new>'`. Find the stragglers with `SELECT rolname FROM pg_authid WHERE rolpassword LIKE 'md5%'`.
2. *Only once every role has a SCRAM verifier*, change the `pg_hba.conf` method from `md5` to `scram-sha-256` and reload.

If you flip the `pg_hba.conf` line first, any role that still has an md5-format password can no longer log in — SCRAM authentication needs a SCRAM verifier, and the error is explicit:

```text
FATAL:  password authentication failed for user "legacy"
DETAIL:  User "legacy" does not have a valid SCRAM secret.
```

The TLS lab (§15, exercise 12) walks this through for real: a `legacy` role whose md5 line is replaced by a scram line, failing until its password is re-hashed.

---

## 14. SSL/TLS: encrypting the channel and authenticating the server

Authentication proves *who* connects. TLS protects the *connection*: it encrypts everything on the wire (so a password or a customer's data can't be sniffed) and, done fully, lets the *client* verify it is talking to the real server (so it can't be tricked into sending its credentials to an impostor).

### 14.1 Server side: ssl = on

The server enables TLS with `ssl = on` and points at a certificate, its private key, and (for client-cert auth) a CA:

```ini
ssl = on
ssl_cert_file = '.../server.crt'     # the server's certificate (its public identity)
ssl_key_file  = '.../server.key'     # the matching private key (secret; mode 600, owned by postgres)
ssl_ca_file   = '.../ca.crt'         # the CA that signs client certs, for method=cert
ssl_min_protocol_version = 'TLSv1.2'
```

PostgreSQL refuses to start if the key is readable by anyone but its owner — a deliberate guard. With `ssl = on`, `host` lines may be served with or without TLS, `hostssl` lines require it, `hostnossl` forbid it. A row in `pg_stat_ssl` tells you whether a given backend is encrypted and with what cipher.

### 14.2 Client side: sslmode, and why verify-full matters

The client's `sslmode` decides how hard it insists on encryption *and verification*. This is the setting that most often gives a false sense of security, because the common choices encrypt but do **not** authenticate the server:

| `sslmode` | Encrypts? | Checks the cert is signed by a CA you trust? | Checks the hostname matches the cert? | Protects against |
|---|---|---|---|---|
| `disable` | no | — | — | nothing |
| `allow` | if the server insists | no | no | nothing meaningful |
| `prefer` (libpq default) | if available | no | no | passive sniffing only, and only sometimes |
| `require` | yes | **no** | **no** | passive sniffing — **but not a man-in-the-middle** |
| `verify-ca` | yes | yes | no | MITM with an untrusted cert |
| `verify-full` | yes | yes | **yes** | MITM entirely |

The crucial gap is between `require` and `verify-full`. `require` encrypts, which feels secure, but it accepts *any* certificate — including a self-signed one an attacker presents after hijacking the DNS name or IP. The client happily sets up an encrypted channel *to the attacker* and sends its password. `verify-ca` checks the certificate chains to a CA you configured (`sslrootcert`), closing the "any cert" hole. `verify-full` additionally checks that the hostname you connected to matches the certificate's subject/SAN — closing the "attacker has *a* cert from your CA, but for a different host" hole.

> **In production, use `verify-full`.** Anything less either doesn't encrypt or doesn't authenticate the server, and "encrypted to the wrong server" is not security. The only reason to use `require` is a server whose certificate you genuinely cannot verify, which in your own infrastructure you always can.

PostgreSQL 16 added `require_auth` (e.g. `require_auth=scram-sha-256`) so the *client* can additionally insist the server use a specific auth method and refuse, for instance, to send a cleartext password — a useful belt-and-braces against a downgrade.

---

## 15. The TLS lab: do it for real

Reading about `verify-full` is not the same as watching it reject an impostor. The lab in [`lab/tls/`](lab/tls/) is a self-contained Docker Compose project: a PostgreSQL 17 server with TLS on, a private CA, a client certificate, and a strict `pg_hba.conf`. You generate the certificates, bring it up, and run a script that tries every `sslmode` and every authentication method and prints the outcome of each.

```bash
cd modules/15-security-rls/lab/tls
./make-certs.sh                 # a lab CA, a server cert (CN/SAN = db.bazaar.internal), a client cert for alice
docker compose up -d --wait
./tls-demo.sh
docker compose down -v          # and: rm -rf certs
```

`make-certs.sh` runs `openssl` inside the postgres image to build a three-certificate chain: a self-signed **CA**, a **server** certificate signed by it (with `db.bazaar.internal` in the Subject Alternative Name), and a **client** certificate for role `alice` (CN = `alice`). The server is reachable in the Compose network as both `db` and `db.bazaar.internal`; only the second name is in the certificate.

`tls-demo.sh` produces (abridged — the full output and explanation are in [`lab/README.md`](lab/README.md)):

```text
== 1. sslmode, connecting by the name in the certificate (db.bazaar.internal)
sslmode=disable       FAIL pg_hba.conf rejects connection ... no encryption
sslmode=require       OK  user=app ssl=true TLSv1.3 TLS_AES_256_GCM_SHA384
sslmode=verify-full   OK  user=app ssl=true TLSv1.3 ...

== 2. sslmode, connecting by another name for the same server (db)
sslmode=require       OK  user=app ...
sslmode=verify-full   FAIL server certificate for "db.bazaar.internal" ... does not match host name "db"

== 4. authentication methods (all over verify-full)
alice, client certificate (cert)   OK  user=alice ...
alice, no certificate              FAIL connection requires a valid client certificate
mallory (no matching hba line)     FAIL no pg_hba.conf entry for host ..., user "mallory"
legacy, md5 line, md5 hash         OK  user=legacy ...
app to database postgres           FAIL no pg_hba.conf entry ... database "postgres"
```

Three things to take away from running it yourself:

- **`verify-full` is the one that catches the hostname mismatch** (part 2): the *same* server, reached by a name not in its certificate, is accepted by `require` and `verify-ca` but rejected by `verify-full`. That rejection is the MITM protection, made concrete.
- **`cert` authentication needs no password** (part 4): `alice` logs in with her key and fails without it. The client's private key is her credential; the server stores nothing secret about her.
- **`pg_hba.conf` first-match and scoping are visible** (part 4): `mallory` has a valid password but no line admits her; `app` is fine for database `lab` but has no line for `postgres`. The method and the database/user scoping are enforced before any query.

Exercise 12 has you harden this `pg_hba.conf` further and perform the md5→scram migration against the running lab.

---

## 16. The operational perimeter (briefly)

A few more controls round out a production posture. Each is a pointer; the mechanics belong to the modules named.

**Audit logging with `pgaudit`.** PostgreSQL's built-in `log_statement = 'all'` logs everything or nothing and is unstructured. The `pgaudit` extension (not bundled; install the OS package) logs, in a parseable format, exactly the classes of statement you choose — e.g. every `WRITE` and `DDL`, or every statement by a particular role, or every statement that touches a specific table — so you can answer "who read the `payments` table last Tuesday?". It is `shared_preload_libraries`-loaded and configured per class (`pgaudit.log = 'write, ddl'`) or per role. Bazaar's application-level audit (the `audit_log` table and trigger from [Module 09](../09-views-functions-triggers/)) records business changes with the `session_user`; `pgaudit` complements it with statement-level logging at the server. Logging itself is [Module 17](../17-operations/).

**Encryption at rest** means the bytes on disk are encrypted, protecting against someone who steals the disk or a backup. PostgreSQL 17 has no built-in transparent data encryption; you get it from the *filesystem/volume* layer (LUKS, dm-crypt, a cloud provider's encrypted volumes) underneath the data directory, plus encrypting backups and WAL archives ([Module 18](../18-backup-recovery/)). It is transparent to SQL and defends a different threat (stolen storage) than privileges and RLS (a live, authenticated attacker).

**Column-level encryption with `pgcrypto`** encrypts specific sensitive values *inside* the database, so even a successful `SELECT` returns ciphertext. Two uses:

```sql
CREATE EXTENSION pgcrypto;
-- one-way, for passwords you only ever verify (never need back):
SELECT crypt('hunter2', gen_salt('bf', 8));          -- bcrypt hash; verify with crypt(input, stored) = stored
-- two-way, for data you must read back (e.g. a stored card token):
SELECT pgp_sym_decrypt(pgp_sym_encrypt('secret', :key), :key);
```

The hard part is never the cipher; it is **key management**. If the key sits in the same database (or in the app config next to the connection string), an attacker who reaches either has both ciphertext and key, and you have gained little. Real column encryption keeps keys in a separate KMS/HSM and often encrypts/decrypts in the *application*, so the database never sees plaintext or key. Use `pgcrypto` deliberately, knowing where the key lives and what threat you are actually defeating; "we encrypted the column" with the key one table over is theatre.

**Secrets handling.** No passwords in the repository — the course lab roles (`'app'`, `'analyst'`, `'seller'`) are the deliberate exception, labelled as such. Production credentials come from a secrets manager or the orchestration platform, injected as environment variables or files at deploy time, and rotated. `cert` authentication (§14) removes the shared secret entirely for the roles that can use it.

**Connection limits and timeouts per role** are cheap guardrails. Bazaar caps `bazaar_app` at `CONNECTION LIMIT 50` (a runaway app can't exhaust the server). Per-role `statement_timeout`, `idle_in_transaction_session_timeout` and (PG 17) `transaction_timeout` cap how long any one role's work may run — a few seconds for the app, minutes for the analyst — and are set with `ALTER ROLE ... IN DATABASE bazaar SET ...` so `reset.sh` cleans them up. The full treatment, including why these go per-role and never globally, is [Module 17 §10](../17-operations/#10-stopping-runaway-work).

---

## Summary

- **Threat-model first:** list the actors, give each the least privilege its job needs, and layer independent controls so no single failure is catastrophic. PostgreSQL's layers are authentication (who/from where), privileges (may you touch the object), and RLS (which rows).
- **Roles** are cluster-wide; `LOGIN` makes a user, membership makes a group. Attributes (`SUPERUSER`, `CREATEDB`, `CREATEROLE`, `REPLICATION`, `BYPASSRLS`) are powers to hand out sparingly. Membership can be used by **inheritance** or by **`SET ROLE`**; PG 16 made `ADMIN`/`INHERIT`/`SET` per-grant and declawed `CREATEROLE`.
- The **predefined `pg_*` roles** (`pg_read_all_data`, `pg_monitor`, `pg_signal_backend`, `pg_maintain` in PG 17, …) grant scoped powers without superuser; they are blunt, so Bazaar uses custom roles where precision (hiding a column) is needed.
- **Ownership** implies all privileges and cannot be revoked, so the application must **own nothing**: one `NOLOGIN` `bazaar_owner` owns everything, migrations run as `SET ROLE bazaar_owner`, and the app's ceiling is only what is granted.
- **Privileges** apply at every level a query touches (database `CONNECT`, schema `USAGE`, table/column/sequence/function). `PUBLIC` means everyone; functions are `EXECUTE`-able by `PUBLIC` by default (revoke for sensitive ones); PG 15 removed `CREATE` on `public` from `PUBLIC`. **Column privileges** hide `users.email` from analysts (remove the table grant, add column grants). `ALTER DEFAULT PRIVILEGES FOR ROLE r` applies to objects created **by** `r` — hence run migrations as the owner. Inspect with `\dp`, `\ddp`, `aclexplode`, `has_*_privilege`.
- **`SECURITY DEFINER`** functions run as their owner — controlled escalation — but an unqualified name in the body is resolved by the caller's `search_path`, enabling a **hijack**. Always `SET search_path`, schema-qualify, revoke `PUBLIC`.
- **Views** run as their owner (a boundary); mark hiding views `security_barrier` so leaky qualifiers can't peek below them, and use `security_invoker` (PG 15+) for views that should respect the caller's RLS.
- **RLS**: `ENABLE` fails closed (no policy = no rows); policies have `USING` (which rows) and `WITH CHECK` (what they may become), are `PERMISSIVE` (OR) or `RESTRICTIVE` (AND), per command and per role. Multi-tenant scoping uses `current_setting('app.seller_id', true)` set with **`SET LOCAL`** inside the transaction — never a session `SET`, which leaks across a transaction pool. The owner bypasses RLS unless `FORCE`; `BYPASSRLS` bypasses everywhere. The policy predicate joins every query, so **index the tenant key**; non-leakproof operators can't run before the policy filter; FK checks and unique constraints leak existence, and metadata/sequences are side channels.
- **Authentication** is `pg_hba.conf`, first match wins, fields TYPE/DATABASE/USER/ADDRESS/METHOD. Use `scram-sha-256`, not deprecated `md5`; migrate by re-hashing passwords *before* flipping the line. **TLS**: `ssl = on` server-side; client `sslmode = verify-full` is the only setting that both encrypts and authenticates the server against a man-in-the-middle.

## What's next

You can now lock Bazaar down. [Module 16](../16-internals/) you have already seen referenced throughout (SCRAM's cost, pages, MVCC); [Module 17](../17-operations/) turns the per-role timeouts and connection limits of §16 into a full operational toolkit and builds the PgBouncer lab whose pooling hazard we met in §10.3. The row-level security you built here reappears in [Module 27](../27-python-production/), wired to a connection pool from Python, and in the [capstone](../28-capstone/)'s seller portal. First, the milestone: build the whole role and RLS layout yourself, and pass the acceptance tests.
