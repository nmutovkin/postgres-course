# Module 15 project: lock down Bazaar

<!-- nav:start -->
> [Theory](README.md) · [Exercises](exercises.md) · **Project** · [Solutions](solutions/README.md)  
> [← 14 Partitioning](../14-partitioning/README.md) · [Course home](../../README.md) · [16 Internals Deep Dive →](../16-internals/README.md)
<!-- nav:end -->

Bazaar has been running as `postgres`, a superuser, with one password shared by everyone. In this milestone you replace that with a real least-privilege security layout: a set of roles, each able to do exactly its job; row-level security so a seller sees only its own data; and the controlled-escalation and column-privilege details that make it safe. This is the single most security-critical milestone in the course, and it is the design you will connect a Python application to in Part IV.

**Starting state:** `./project/bin/reset.sh 15` (large data) or `./modules/15-security-rls/lab/small15.sh` (small data). Both give you the module-15 schema *without* any roles or RLS — you build those. The reference solution is [`project/db/1501_schema_security.sql`](../../project/db/1501_schema_security.sql); build your own first, then compare.

> **Roles are cluster-wide.** If you have run `small15.sh --with-1501` or `reset.sh 16`, the reference roles already exist. To start truly clean, drop them first (see [`lab/README.md`](lab/README.md)): `DROP DATABASE bazaar WITH (FORCE)` then `DROP ROLE bazaar_seller, bazaar_analyst, bazaar_app, bazaar_readonly, bazaar_owner;`, and rebuild with `reset.sh 15`.

<!-- toc:start -->
**Contents**

- [Goal, in business terms](#goal-in-business-terms)
- [Requirements](#requirements)
  - [1. Roles](#1-roles)
  - [2. Ownership](#2-ownership)
  - [3. Privileges on existing objects](#3-privileges-on-existing-objects)
  - [4. Default privileges](#4-default-privileges)
  - [5. Hide users.email](#5-hide-usersemail)
  - [6. Append-only audit log](#6-append-only-audit-log)
  - [7. SECURITY DEFINER hardening](#7-security-definer-hardening)
  - [8. Row-level security](#8-row-level-security)
- [Acceptance checks](#acceptance-checks)
- [Reference solution](#reference-solution)
- [What's next](#whats-next)

<!-- toc:end -->

## Goal, in business terms

- The web application runs with a credential that can do its queries and nothing structural: it cannot drop or truncate tables, change the schema, or tamper with the audit trail.
- Analysts and BI tools can read the business data for reporting, but **not** customer e-mail addresses.
- The seller portal uses one credential for all 2,000 sellers, yet each seller can see and edit **only its own** products, inventory and orders — enforced by the database, so a bug in the portal cannot leak across sellers.
- Every object is owned by a non-login role, and migrations run as that role so new objects inherit the right privileges automatically.

## Requirements

### 1. Roles

Create (idempotently — roles survive `reset.sh`, so guard every `CREATE ROLE`):

| Role | Attributes | Purpose |
|---|---|---|
| `bazaar_owner` | `NOLOGIN` | owns the schema and every object; migrations `SET ROLE` to it |
| `bazaar_app` | `LOGIN`, password, `CONNECTION LIMIT 50` | the application: full DML, `EXECUTE`, no DDL |
| `bazaar_readonly` | `NOLOGIN` | a privilege bundle: `SELECT` on everything (except `users.email`, req. 5) |
| `bazaar_analyst` | `LOGIN`, password, member of `bazaar_readonly` | a person; read access by membership |
| `bazaar_seller` | `LOGIN`, password | the seller portal; scoped by RLS |

Passwords are for the course only (`'app'`, `'analyst'`, `'seller'`). Grant `CONNECT` on the database to the three login roles.

### 2. Ownership

Transfer the `bazaar` schema and every table, view, materialized view, function, procedure, enum type and domain in it to `bazaar_owner`. (Identity sequences and indexes follow their tables.) A `DO` block that loops over `pg_class`, `pg_proc` and `pg_type` is the maintainable way.

### 3. Privileges on existing objects

- `USAGE` on schema `bazaar` to app, readonly, seller.
- `bazaar_app`: `SELECT, INSERT, UPDATE, DELETE` on all tables; `USAGE, SELECT` on all sequences; `EXECUTE` on all functions. No `TRUNCATE`, no DDL.
- `bazaar_readonly`: `SELECT` on all tables.
- `bazaar_seller` (least privilege, partly column-level): `SELECT` on `categories` and `sellers`; `SELECT, INSERT, UPDATE` (no `DELETE`) on `products` and `inventory`; `SELECT` on `orders`, `order_items`, `reviews`; and a **column-level** `UPDATE (status, shipped_at, delivered_at)` on `orders` (fulfilment only). `USAGE` on sequences it needs.

### 4. Default privileges

So that objects a future migration creates get the same grants automatically, set `ALTER DEFAULT PRIVILEGES FOR ROLE bazaar_owner IN SCHEMA bazaar` for tables (app DML, readonly SELECT), sequences (app USAGE/SELECT) and functions (app EXECUTE). Remember these apply to objects created **by** `bazaar_owner` — hence migrations must run as it.

### 5. Hide users.email

`bazaar_readonly` (so also `bazaar_analyst`) must read every column of `users` **except** `email`, and must still be able to `count(*)` and read names. (Remove the table-level `SELECT`, grant the harmless columns.) This is in [`solutions/milestone_extra.sql`](solutions/milestone_extra.sql).

### 6. Append-only audit log

`bazaar_app` may `SELECT` `audit_log` but must not `INSERT`, `UPDATE`, `DELETE` or `TRUNCATE` it directly — audit rows are written only by the `SECURITY DEFINER` `audit_trigger()`. (`solutions/milestone_extra.sql`.)

### 7. SECURITY DEFINER hardening

The functions that must do more than their caller may — `audit_trigger()`, `create_events_partition(date)`, and a new `refresh_seller_sales_daily()` wrapping `REFRESH MATERIALIZED VIEW CONCURRENTLY` — must be `SECURITY DEFINER`, owned by `bazaar_owner`, with a **pinned `search_path`** (`SET search_path = bazaar, pg_temp`), and with `EXECUTE` revoked from `PUBLIC` and granted only to the role that needs it (`bazaar_app`).

### 8. Row-level security

Define `current_seller_id()` reading `nullif(current_setting('app.seller_id', true), '')::bigint` (so an unset or blank setting yields NULL → the seller sees nothing). Enable RLS on `products`, `inventory`, `orders`, `order_items`. Then:

- an `USING (true) WITH CHECK (true)` policy `TO bazaar_app, bazaar_readonly` on each (otherwise enabling RLS locks them out);
- `products`: `FOR ALL TO bazaar_seller USING (seller_id = current_seller_id()) WITH CHECK (...)`;
- `inventory`: same, via an `EXISTS` subquery on `products` (no `seller_id` column);
- `orders`: a `FOR SELECT` and a `FOR UPDATE` seller policy on `seller_id = current_seller_id()`;
- `order_items`: a `FOR SELECT` seller policy via `EXISTS` on `orders`.

Do **not** use `FORCE ROW LEVEL SECURITY` (it would subject `bazaar_owner`, breaking the definer functions and migrations). The portal sets the tenant per request with `SET LOCAL app.seller_id = '<id>'` inside a transaction — never a session `SET` (README §10.3).

## Acceptance checks

Run the reference acceptance script, which connects as each role and prints `PASS`/`FAIL` for every requirement (it works on both the small and large datasets, and changes nothing):

```bash
docker exec -i course-pg psql -U postgres -d bazaar -X < modules/15-security-rls/examples/acceptance.sql
```

Expected (small data):

```text
--- bazaar_seller
PASS seller 1 sees exactly its 18 products
PASS seller 1 sees exactly its 38 orders
PASS seller 1 sees no order_items of other sellers
PASS seller 1 cannot update product 19 of another seller (0 rows)
PASS seller 1 cannot hand its products to another seller (new row violates row-level security policy ...)
PASS without app.seller_id the seller sees nothing
--- bazaar_analyst
PASS the analyst cannot read users.email (permission denied for table users)
PASS the analyst can still count users (30) and read their names
PASS the analyst reads all orders (120)
--- bazaar_app
PASS the app cannot DROP tables (must be owner of table orders)
PASS the app cannot rewrite audit_log (permission denied for table audit_log)
PASS the app sees all products (62)
--- ownership
PASS every table, view and function in bazaar is owned by bazaar_owner (0 exceptions)
```

(On large data the counts differ — seller 1 has ~18,500 products and ~70k orders — but every line still reads `PASS`.) The specific checks the brief calls out:

1. **As `bazaar_seller` with `app.seller_id = 1`:** you see only seller 1's products and orders; `UPDATE` of another seller's product affects 0 rows; moving a product to another seller errors; with no setting you see nothing.
2. **The analyst cannot read `users.email`** (but can read names and counts).
3. **The app cannot `DROP` tables** (nor truncate or tamper with `audit_log`).

You can also run requirements 5 and 6 on top of a plain `1501` with:

```bash
docker exec -i course-pg psql -U postgres -d bazaar -v ON_ERROR_STOP=1 < modules/15-security-rls/solutions/milestone_extra.sql
```

## Reference solution

- [`project/db/1501_schema_security.sql`](../../project/db/1501_schema_security.sql) — the complete roles, ownership, grants, default privileges, SECURITY DEFINER hardening and RLS (requirements 1–4, 7, 8). Its header NOTES document the role purposes, the course passwords, `current_seller_id()`, and the "run migrations as `SET ROLE bazaar_owner`" rule.
- [`solutions/milestone_extra.sql`](solutions/milestone_extra.sql) — the two requirements beyond `1501`: hiding `users.email` (5) and the append-only audit log (6).
- [`examples/acceptance.sql`](examples/acceptance.sql) — the acceptance checks.

## What's next

With the security layout in place, [Module 16](../16-internals/) and [Module 17](../17-operations/) run *on* this database (internals, then the ops toolkit with per-role timeouts and the PgBouncer lab). The RLS you built here is wired to a connection pool from Python in [Module 27](../27-python-production/) and powers the seller portal in the [capstone](../28-capstone/).
