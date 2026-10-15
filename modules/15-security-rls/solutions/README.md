# Module 15: Solutions

<!-- nav:start -->
> [Theory](../README.md) · [Exercises](../exercises.md) · [Project](../project.md) · **Solutions**  
> [← 14 Partitioning](../../14-partitioning/README.md) · [Course home](../../../README.md) · [16 Internals Deep Dive →](../../16-internals/README.md)
<!-- nav:end -->

All solutions were run on PostgreSQL 17 against the small dataset with the reference roles and RLS applied:

```bash
./modules/15-security-rls/lab/small15.sh --with-1501
```

(the same schema and roles as `./project/bin/reset.sh 16`, on the hand-readable small dataset). Exercise 10 uses the large dataset (`reset.sh 16`); exercises 11–12 use the TLS lab. Row counts and values below are real. Several solutions are also standalone, runnable scripts in this directory; each cleans up after itself so you can re-run them. To start completely clean, re-run `small15.sh --with-1501`.

---

<!-- toc:start -->
**Contents**

- [Exercise 1 ★: A role report from the catalogs](#exercise-1--a-role-report-from-the-catalogs)
- [Exercise 2 ★: What can each login role do?](#exercise-2--what-can-each-login-role-do)
- [Exercise 3 ★: The append-only audit log](#exercise-3--the-append-only-audit-log)
- [Exercise 4 ★★: A read-only reporting role, two ways](#exercise-4--a-read-only-reporting-role-two-ways)
- [Exercise 5 ★★: Hide users.email from the read-only bundle](#exercise-5--hide-usersemail-from-the-read-only-bundle)
- [Exercise 6 ★★: Sellers cannot modify archived products](#exercise-6--sellers-cannot-modify-archived-products)
- [Exercise 7 ★★: Find and fix mis-owned objects](#exercise-7--find-and-fix-mis-owned-objects)
- [Exercise 8 ★★★: Design a "support" role](#exercise-8--design-a-support-role)
- [Exercise 9 ★★: Reviews, per seller](#exercise-9--reviews-per-seller)
- [Exercise 10 ★★★: Measure RLS overhead (large data)](#exercise-10--measure-rls-overhead-large-data)
- [Exercise 11 ★★: sslmode in the lab](#exercise-11--sslmode-in-the-lab)
- [Exercise 12 ★★★: Harden pg_hba.conf and migrate off md5](#exercise-12--harden-pg_hbaconf-and-migrate-off-md5)
- [Exercise 13 ★★★: Exploit, then fix, a search_path hole](#exercise-13--exploit-then-fix-a-search_path-hole)
- [Exercise 14 ★★: Close the sequence side channel](#exercise-14--close-the-sequence-side-channel)
- [Exercise 15 ★★★: Prove the FK existence leak and seal it](#exercise-15--prove-the-fk-existence-leak-and-seal-it)

<!-- toc:end -->

## Exercise 1 ★: A role report from the catalogs

Script: [ex01_role_report.sql](ex01_role_report.sql).

Reasoning: everything about a role lives in `pg_roles` (one row per role, with boolean attribute columns and `rolconnlimit`) except *memberships*, which are in `pg_auth_members` — and since PG 16 that table carries the three per-grant options (`admin_option`, `inherit_option`, `set_option`). Build the attribute list by turning each true flag into a label and dropping the NULLs; correlate the memberships with a scalar subquery so the result stays one row per role.

```sql
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
       (SELECT string_agg(g.rolname || ' (' || concat_ws(',',
                   CASE WHEN m.admin_option   THEN 'admin'   END,
                   CASE WHEN m.inherit_option THEN 'inherit' END,
                   CASE WHEN m.set_option     THEN 'set'     END) || ')', ', ' ORDER BY g.rolname)
        FROM pg_auth_members AS m JOIN pg_roles AS g ON g.oid = m.roleid
        WHERE m.member = r.oid) AS member_of
FROM pg_roles AS r
WHERE r.rolname !~ '^pg_'
ORDER BY r.rolname;
```

```text
     rolname     |                         attributes                          | conn_limit |           member_of
-----------------+-------------------------------------------------------------+------------+-------------------------------
 bazaar_analyst  | {LOGIN}                                                     |            | bazaar_readonly (inherit,set)
 bazaar_app      | {LOGIN}                                                     |         50 |
 bazaar_owner    | {}                                                          |            |
 bazaar_readonly | {}                                                          |            |
 bazaar_seller   | {LOGIN}                                                     |            |
 postgres        | {SUPERUSER,LOGIN,CREATEROLE,CREATEDB,REPLICATION,BYPASSRLS} |            |
```

Six rows, matching the self-check. Note the empty attribute set for the two group/owner roles (no `LOGIN`), and that `bazaar_analyst`'s membership shows `inherit,set` (the defaults) but not `admin` — it cannot grant `bazaar_readonly` onward.

Alternative: psql's `\du` and `\drg` show most of this interactively, but a SQL query is what you put in a monitoring dashboard or a CI check that fails if someone grants `SUPERUSER`.

---

## Exercise 2 ★: What can each login role do?

Script: [ex02_privilege_matrix.sql](ex02_privilege_matrix.sql).

Reasoning: `has_table_privilege(role, oid, priv)` is the right primitive — it follows membership and `PUBLIC`, so it reports *effective* privileges, which is what you actually care about. Cross-join the relations with the three roles, compute a compact flag string per (relation, role) with a `LATERAL`, and pivot the three roles into columns with `max(CASE ...)`. The one subtlety is distinguishing a table-level `SELECT`/`UPDATE` from a column-only one: `has_any_column_privilege(..., 'UPDATE')` is true when *some* column is granted even though `has_table_privilege(..., 'UPDATE')` is false.

```text
        relname        | rls | app  | analyst |  seller
-----------------------+-----+------+---------+----------
 active_products       | f   | rawd | r       |
 audit_log             | f   | rawd | r       |
 inventory             | t   | rawd | r       | raw
 order_items           | t   | rawd | r       | r
 orders                | t   | rawd | r       | rw(cols)
 products              | t   | rawd | r       | raw
 reviews               | f   | rawd | r       | r
 sellers               | f   | rawd | r       | r
 users                 | f   | rawd | r       |
 ...
```

The self-check holds: RLS is on for `products`/`inventory`/`orders`/`order_items`; `bazaar_seller` has `rw(cols)` on `orders` (SELECT plus a *column-level* UPDATE on `status`/`shipped_at`/`delivered_at`, not a whole-table UPDATE), `raw` on `products`/`inventory` (no DELETE), and nothing on `users`/`payments`/etc. This matrix plus exercise 1 is a complete, catalog-driven audit of "who can do what" — exactly what a reviewer wants before a release.

> Note the matrix shows *privileges*, not *rows*: `bazaar_seller` shows `raw` on `products`, but RLS still limits it to its own rows at query time. Privileges and RLS are independent layers (README §1).

---

## Exercise 3 ★: The append-only audit log

Reasoning: the audit trigger `audit_trigger()` is `SECURITY DEFINER` owned by `bazaar_owner`, so it writes `audit_log` with the owner's rights no matter who the caller is. That means `bazaar_app` does **not** need any write privilege on `audit_log` for auditing to work — the broad `INSERT/UPDATE/DELETE` it got from the `ALL TABLES` grant is pure downside (it lets the app tamper with history). Remove the writes, keep `SELECT`:

```sql
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON bazaar.audit_log FROM bazaar_app;
```

Verify:

```sql
\c - bazaar_app
UPDATE products SET price = 989.00 WHERE id = 1;   -- UPDATE 1
SELECT operation, old_row->>'price', new_row->>'price', changed_by
FROM audit_log ORDER BY id DESC LIMIT 1;           -- UPDATE | 999.00 | 989.00 | bazaar_app
DELETE FROM audit_log;                             -- ERROR: permission denied for table audit_log
INSERT INTO audit_log (table_name, operation, row_pk) VALUES ('orders','UPDATE','{"id":1}');
                                                   -- ERROR: permission denied for table audit_log
```

The business change is still recorded (by the trigger, as the owner), but the app cannot delete, forge or alter the trail. This is the first half of the milestone's audit requirement. (The reference milestone bakes this into `milestone_extra.sql`.)

---

## Exercise 4 ★★: A read-only reporting role, two ways

Reasoning: `pg_read_all_data` is *dynamic* — it grants "SELECT on everything that exists, now and in the future", evaluated at access time. `bazaar_readonly` holds *concrete* grants plus default privileges keyed on `bazaar_owner`. The consequences differ in two ways the self-check pins down.

```sql
CREATE ROLE bi_builtin LOGIN; GRANT pg_read_all_data TO bi_builtin;
CREATE ROLE bi_custom  LOGIN; GRANT bazaar_readonly  TO bi_custom;
```

1. **Sensitive columns.** `bi_builtin` reads `users.email` (`pg_read_all_data` is all-or-nothing, no column granularity). On a fresh `--with-1501` state `bazaar_readonly` *also* still reads it — until you apply the column restriction from exercise 5 / the milestone, after which `bi_custom` cannot. This is exactly why Bazaar uses a custom bundle: you cannot hide `users.email` with `pg_read_all_data`.
2. **New tables.** Create one as the owner and one as `postgres`:

   ```sql
   SET ROLE bazaar_owner; CREATE TABLE bazaar.newt (x int); RESET ROLE;
   CREATE TABLE bazaar.newt_pg (x int);   -- created by postgres
   ```

   `bi_builtin` reads both immediately. `bi_custom` reads `newt` (the default privileges `FOR ROLE bazaar_owner` fired, because it was created *by* `bazaar_owner`) but **not** `newt_pg`:

   ```text
   bi_custom:  SELECT count(*) FROM newt     --> 0
               SELECT count(*) FROM newt_pg  --> ERROR: permission denied for table newt_pg
   ```

   That error is the §5.4 "run migrations as the owner" lesson made concrete.

Both read `payments` (140 rows). Verdict: `pg_read_all_data` is the quick, blunt choice; Bazaar's custom `bazaar_readonly` is the one that can hide a column, so it is the right fit for the analyst policy. Clean up: `DROP TABLE bazaar.newt, bazaar.newt_pg; DROP ROLE bi_builtin, bi_custom;`.

---

## Exercise 5 ★★: Hide users.email from the read-only bundle

Script: [ex05_hide_email.sql](ex05_hide_email.sql).

Reasoning: the trap is that a *column* `REVOKE` does nothing while a *table-level* `SELECT` grant exists (the table grant already covers every column). So you remove the table grant and grant back the columns you want. The masked view must read `email` to compute the domain, so it has to run as its owner (`bazaar_owner`) — an ordinary view, **not** `security_invoker`.

```sql
REVOKE SELECT ON bazaar.users FROM bazaar_readonly;
GRANT SELECT (id, full_name, is_active, created_at) ON bazaar.users TO bazaar_readonly;

SET ROLE bazaar_owner;
CREATE VIEW bazaar.users_masked AS
SELECT id, full_name, split_part(lower(email), '@', 2) AS email_domain, is_active, created_at
FROM bazaar.users;
GRANT SELECT ON bazaar.users_masked TO bazaar_readonly;
```

Verify as the analyst:

```text
SELECT email FROM users WHERE id = 17;          ERROR: permission denied for table users
SELECT * FROM users LIMIT 1;                     ERROR: permission denied for table users
SELECT id, full_name FROM users WHERE id = 17;   17 | Zoë Martin
SELECT count(*) FROM users;                       30
SELECT email_domain, count(*) FROM users_masked GROUP BY 1 ORDER BY 2 DESC;
   example.com   | 10      <- the most common domain
   example.co.uk | 3
   ...
```

`has_column_privilege('bazaar_analyst','bazaar.users','email','SELECT')` is `f`. The analyst gets everything useful (names, domains for cohorting) and nothing sensitive (addresses). Alternative: keep the table grant and instead revoke at the column level — but that is a no-op (the trap above), so the remove-then-grant-columns order is mandatory.

---

## Exercise 6 ★★: Sellers cannot modify archived products

Script: [ex06_archived_products.sql](ex06_archived_products.sql).

Reasoning: you want to *subtract* an additional condition from the seller's existing `UPDATE` ability without touching the permissive `products_seller` policy. That is exactly what a `RESTRICTIVE` policy does (it is `AND`ed in). The split between `USING` and `WITH CHECK` is the whole exercise: `USING (status <> 'archived')` controls *which existing rows* may be updated (not archived ones); `WITH CHECK (true)` allows the *result* row to be anything — which is what permits a seller to *archive* an active product.

```sql
CREATE POLICY products_seller_not_archived ON bazaar.products
    AS RESTRICTIVE FOR UPDATE TO bazaar_seller
    USING (status <> 'archived')
    WITH CHECK (true);
```

Verify (seller 1): updating archived product 5 affects 0 rows; archiving active product 2 succeeds; then updating product 2's price affects 0 rows (now archived). If you omit `WITH CHECK (true)`, the restrictive `USING` is reused as the check and archiving an active product *fails* ("new row violates row-level security policy"), because the post-image has `status = 'archived'` — the classic mistake this exercise is built to surface. Undo: `DROP POLICY products_seller_not_archived ON bazaar.products;`.

---

## Exercise 7 ★★: Find and fix mis-owned objects

Script: [ex07_fix_ownership.sql](ex07_fix_ownership.sql).

Reasoning: objects created while connected as `postgres` are owned by `postgres` and got no grants (default privileges are keyed on the *creating* role, §5.4). The report side is a `UNION ALL` over `pg_class` (relations) and `pg_proc` (routines) filtered by `relowner`/`proowner <> 'bazaar_owner'::regrole`. The fix side generates `ALTER ... OWNER TO` and `GRANT ...` statements with `format()` and runs them with `\gexec`; order `ALTER` before `GRANT` so the owner is correct when the grants are applied. Skip identity/serial sequences (they follow their table) by excluding `pg_depend` rows with `deptype IN ('a','i')`.

After seeding the mess (a `coupons` table and a `coupon_is_valid` function created as `postgres`), the report shows three mis-owned objects (the table, its identity sequence, the function). Before the fix, `bazaar_app` gets "permission denied" on both; after it, the inserts and the function call work and `coupons`'s ACL equals `cart_items`'s:

```text
 bazaar_app:  INSERT INTO coupons ... RETURNING id      --> 1
              SELECT coupon_is_valid('WELCOME10', '2025-06-30')  --> t
```

The report then returns zero rows. The real lesson is prevention: `SET ROLE bazaar_owner` at the top of every migration. This script is the clean-up you run when someone forgot. Clean up: `DROP FUNCTION bazaar.coupon_is_valid; DROP TABLE bazaar.coupons;`.

---

## Exercise 8 ★★★: Design a "support" role

Script: [ex08_support_role.sql](ex08_support_role.sql).

Reasoning: this combines every tool in the module. Reads are plain grants plus `FOR SELECT ... USING (true)` policies (needed because `orders`/`order_items` have RLS on, which would otherwise show support zero rows). Users are column-restricted to `id`/`full_name` so support can't read `email`. The *only* write path is a `SECURITY DEFINER` function that: validates a reason, locks the order `FOR UPDATE`, enforces an explicit transition table (support may cancel `pending`/`paid`, refund `delivered`; nothing else), performs the `UPDATE` as the owner, and records the reason in `audit_log` attributed to `session_user`. Direct `UPDATE orders` is never granted.

Two details that are easy to get wrong:

- Inside a `SECURITY DEFINER` function `current_user` is the owner, so `audit_log.changed_by` (which defaults to `CURRENT_USER`) would record `bazaar_owner`. Pass `session_user` explicitly to attribute the change to the human/role that called it.
- `PUBLIC` gets `EXECUTE` on the new function by default, **and** `bazaar_owner`'s default privileges (`1501`) grant `EXECUTE` to `bazaar_app` at creation time. Revoke *both*: `REVOKE EXECUTE ... FROM PUBLIC, bazaar_app;` then `GRANT ... TO bazaar_support;`.

Verify:

```text
bazaar_support:
  SELECT count(DISTINCT seller_id) FROM orders                       --> 4 (all sellers)
  SELECT email FROM users                                             --> ERROR: permission denied
  UPDATE orders SET status='cancelled' WHERE id=115                   --> ERROR: permission denied
  support_set_order_status(115,'cancelled','customer asked to cancel')--> cancelled
  support_set_order_status(116,'delivered','customer says it arrived')--> ERROR: support may not change order 116 from pending to delivered
  support_set_order_status(91,'refunded','x')                         --> ERROR: a reason of at least 5 characters is required
bazaar_app:
  support_set_order_status(...)                                       --> ERROR: permission denied for function
```

The audit row for the successful call has `changed_by = bazaar_support`. This is the template for "a role that may do one narrow mutation, safely". Clean up by dropping the policies, function and role, or re-run `small15.sh`.

---

## Exercise 9 ★★: Reviews, per seller

Script: [ex09_reviews_rls.sql](ex09_reviews_rls.sql).

Reasoning: identical shape to the reference `order_items` policy. Enable RLS (which locks everyone out), re-admit the app and read-only roles with a `USING (true)` policy, and give sellers a `FOR SELECT` policy whose `USING` reaches `reviews.product_id` into `products` — where the seller's own products policy already filters to their rows, so no explicit `seller_id` is needed.

```sql
ALTER TABLE reviews ENABLE ROW LEVEL SECURITY;
CREATE POLICY reviews_all_access ON reviews TO bazaar_app, bazaar_readonly USING (true) WITH CHECK (true);
CREATE POLICY reviews_seller_select ON reviews FOR SELECT TO bazaar_seller
    USING (EXISTS (SELECT 1 FROM products p WHERE p.id = reviews.product_id));
```

Verify: seller 1 sees 17 reviews (avg rating `3.53`); seller 5 (products but no sales) sees 0; no setting → 0; app and analyst see all 78. Undo: drop the two policies, then `ALTER TABLE reviews DISABLE ROW LEVEL SECURITY;`.

---

## Exercise 10 ★★★: Measure RLS overhead (large data)

Reasoning: an RLS `USING` predicate is `AND`ed into every query, so its cost is the cost of that predicate — which is dominated by whether it is *indexable*. On `reset.sh 16`:

- `orders` has an index on `(seller_id, created_at)`, so the policy predicate `seller_id = current_seller_id()` becomes an `Index Only Scan` — ~14 ms for seller 1 (69,805 orders), ~0.04 ms for seller 2000 (3 orders). Run as `bazaar_owner` (no RLS) the same count is a *parallel seq scan* (~116 ms), so here RLS makes the seller's own view faster, because it supplies a selective, indexed filter.
- `order_items` has *no* `seller_id`; its policy reaches `orders` via `EXISTS`, which the planner runs as a hashed subplan over a 1.27M-row seq scan — ~250–550 ms regardless of seller. The RLS predicate is not indexable on `order_items` itself.

The fix is to give `order_items` an indexable tenant key:

```sql
ALTER TABLE order_items ADD COLUMN seller_id bigint;
UPDATE order_items oi SET seller_id = o.seller_id FROM orders o WHERE o.id = oi.order_id;
CREATE INDEX order_items_seller_id_idx ON order_items (seller_id);
VACUUM ANALYZE order_items;
ALTER POLICY order_items_seller_select ON order_items USING (seller_id = current_seller_id());
```

Now `SELECT count(*) FROM order_items` as a seller is an `Index Only Scan`: ~22 ms for seller 1, ~0.05 ms for seller 2000. The general rule: in a multi-tenant RLS schema, carry the tenant key on every policy-protected table and index it — the storage is cheaper than the full scans RLS otherwise forces. (A real migration keeps the column in sync with a trigger or a generated value; the one-off backfill here is enough to measure. This is the `seller_id`-on-child-tables idea that [Module 21](../../21-sharding/)'s shard prep makes permanent.)

---

## Exercise 11 ★★: sslmode in the lab

Reasoning: `sslmode` controls two independent things — whether TLS is used, and whether the server is *verified*. The lab's `pg_hba.conf` rejects unencrypted connections, so `disable` always fails before auth. `require` encrypts but verifies nothing; `verify-ca` adds "chains to my CA" (so it needs `sslrootcert`); `verify-full` adds "the hostname matches the certificate".

| From | `disable` | `require` | `verify-ca` | `verify-full` |
|---|---|---|---|---|
| `db.bazaar.internal` (name in the cert) | FAIL (hba rejects) | OK | OK | OK |
| `db` (another name, same server) | FAIL | OK | OK | **FAIL** — "certificate for \"db.bazaar.internal\" ... does not match host name \"db\"" |

Without `sslrootcert=/lab/certs/ca.crt`, `verify-ca`/`verify-full` fail with "root certificate file ... does not exist". The teaching point is the one `verify-full` cell that fails where `verify-ca` passes: that is the hostname check, the piece that stops an attacker who holds *a* valid certificate (for a different name) from impersonating your server. `./tls-demo.sh` runs the whole matrix.

---

## Exercise 12 ★★★: Harden pg_hba.conf and migrate off md5

Script: [ex12/pg_hba.conf](ex12/pg_hba.conf).

Reasoning: `pg_hba.conf` is first-match-wins, so a `reject` for `postgres` over the network must come *before* any line that would otherwise match `postgres`. Group membership is matched with `+lab_users`; a network restriction uses `samenet`. The md5→scram migration order is the crux: flip the method to `scram-sha-256` *and* ensure every role has a SCRAM verifier. If you reload the hba change while `legacy` still has an md5-format password, it cannot log in:

```text
FATAL:  password authentication failed for user "legacy"
DETAIL:  User "legacy" does not have a valid SCRAM secret.
```

Re-hashing fixes it (`password_encryption` is already `scram-sha-256`, so simply re-setting the password stores a SCRAM verifier):

```sql
SELECT rolname FROM pg_authid WHERE rolpassword LIKE 'md5%';   -- find stragglers
ALTER ROLE legacy PASSWORD 'legacy_pw';                         -- now SCRAM
SELECT pg_reload_conf();
```

After the edit, `pg_hba_file_rules` shows no `md5` line and no `error`; `postgres` is rejected over TLS; the reporting role connects only from the server's network. The safe real-world sequence is always: **re-hash every password first, confirm `pg_authid` has no `md5%` rows, then change the `pg_hba.conf` method and reload.** Reset the lab's own `conf/pg_hba.conf` afterwards (`git checkout modules/15-security-rls/lab/tls/conf/pg_hba.conf`) so later `tls-demo.sh` runs match the lab README.

---

## Exercise 13 ★★★: Exploit, then fix, a search_path hole

Script: [ex13_search_path.sql](ex13_search_path.sql).

Reasoning: the vulnerable `shop_revenue()` is `SECURITY DEFINER` (runs as `bazaar_owner`), names `lower()` unqualified, and pins no `search_path`, so the name is resolved against the *caller's* path. The attacker (from their personal schema) defines `analyst_scratch.lower(text)` that performs a privileged `UPDATE` and then returns `pg_catalog.lower($1)` so the function still behaves normally. The non-obvious requirement: the function runs with the *owner's* privileges, so the owner must be able to resolve the trap — `GRANT USAGE ON SCHEMA analyst_scratch TO PUBLIC`, which the attacker can do because they own the schema. Then `SET search_path = analyst_scratch, pg_catalog, bazaar` and call the function.

```text
before fix:  SELECT bazaar.shop_revenue('TechTrove');  --> 41598.82 (normal)
             SELECT price FROM products WHERE id = 3;    --> 0.01     (changed by a role with no UPDATE!)
```

The fix pins the path so the caller cannot influence resolution:

```sql
ALTER FUNCTION bazaar.shop_revenue(text) SET search_path = pg_catalog, bazaar, pg_temp;
```

```text
after fix:   SELECT price FROM products WHERE id = 3;    --> 2199.99 (untouched)
```

An unqualified *operator* (`=`) is hijackable the same way when the planner resolves it from the caller's path; a pinned `search_path` blocks that too. Belt-and-braces: schema-qualify every name in the body. The script restores the price and drops the schema and function at the end.

> The `GRANT USAGE` requirement is the subtlety worth remembering: the search_path chooses *which* schema to look in, but the function's *owner* still needs privilege on that schema for the resolved object to be usable. The attack needs both — and a pinned `search_path` removes the first, which is enough.

---

## Exercise 14 ★★: Close the sequence side channel

Reasoning: RLS hides *rows*, not shared objects like sequences. A seller can call `nextval('orders_id_seq')` and learn the marketplace's global next order id — a volume leak across tenants. `1501` grants the seller `USAGE ON ALL SEQUENCES` (a leftover of a broader grant); revoke it:

```sql
REVOKE USAGE ON ALL SEQUENCES IN SCHEMA bazaar FROM bazaar_seller;
```

```text
bazaar_seller, app.seller_id = 2:
  before: SELECT nextval('orders_id_seq')  --> 121   (global high-water: a leak)
  after:  SELECT nextval('orders_id_seq')  --> ERROR: permission denied for sequence orders_id_seq
          INSERT INTO products (seller_id, ...) VALUES (2, ...)  --> INSERT 0 1   (still works!)
```

The interesting part is that identity inserts still work: an `INSERT` into a `GENERATED ... AS IDENTITY` column drives its backing sequence *internally*, with the table owner's rights, and does **not** require the inserting role to hold sequence `USAGE`. So revoking the grant closes the side channel with no cost to the seller's real work. Undo: `GRANT USAGE ON ALL SEQUENCES IN SCHEMA bazaar TO bazaar_seller;`. (Other volume side channels — `pg_class.reltuples`, `pg_stat_user_tables`, `EXPLAIN` estimates — are harder to close and are discussed in README §12.3.)

---

## Exercise 15 ★★★: Prove the FK existence leak and seal it

Script: [ex15_fk_leak.sql](ex15_fk_leak.sql).

Reasoning: foreign-key integrity checks are never subject to RLS — they must see all rows to do their job — so a child insert whose FK points at a *parent you cannot see* succeeds, while one pointing at a *non-existent* parent fails. That difference is an existence oracle. The naive `order_notes` policy (checking only `seller_id = current_seller_id()`) leaks:

```text
seller 2:  INSERT INTO order_notes (2, 40, ...)    --> INSERT 0 1   (order 40 is seller 1's, yet this reveals it exists)
           INSERT INTO order_notes (2, 4000, ...)  --> ERROR: FK violation (order 4000 doesn't exist)
```

The seal is to move an existence check *into* the policy's `WITH CHECK`, which *does* run under the seller's `orders` policy, so an invisible order looks identical to a non-existent one:

```sql
ALTER POLICY order_notes_seller ON order_notes
    WITH CHECK (seller_id = current_seller_id()
                AND EXISTS (SELECT 1 FROM orders WHERE id = order_id));
```

```text
seller 2:  INSERT INTO order_notes (2, 40, ...)    --> ERROR: new row violates row-level security policy
           INSERT INTO order_notes (2, 4000, ...)  --> ERROR: new row violates row-level security policy  (same error!)
           INSERT ... (2, <own order id>, ...)      --> succeeds
```

Both failure cases now return the identical RLS error, closing the oracle, while legitimate inserts for the seller's own orders still work. The general lesson: constraints and integrity checks see around RLS; when existence itself is sensitive, enforce it inside a policy predicate (which respects RLS), not only via an FK. Clean up: `DROP TABLE bazaar.order_notes;`.
