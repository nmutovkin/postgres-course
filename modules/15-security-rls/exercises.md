# Module 15: Exercises

**Starting state.** Unless an exercise says otherwise, use the small dataset with the reference roles and RLS applied:

```bash
./modules/15-security-rls/lab/small15.sh --with-1501
./project/bin/psql.sh
```

(This is the state `./project/bin/reset.sh 16` produces, but on the small, hand-readable dataset documented in [`project/db/NOTES.md`](../../project/db/NOTES.md). Exercises 10 and the stretch parts that measure performance say to use `reset.sh 16` for the **large** dataset instead.)

To connect as one of Bazaar's roles inside the container, local connections need no password:

```bash
docker exec -it course-pg psql -U bazaar_analyst -d bazaar
docker exec -it course-pg psql -U bazaar_seller  -d bazaar
```

Or, in a superuser session, `SET ROLE bazaar_seller;` / `RESET ROLE;` (remember to set `app.seller_id` for the seller). Several exercises change privileges or add policies; each says how to undo its change, so the next starts clean. A quick full reset is always `./modules/15-security-rls/lab/small15.sh --with-1501`.

Exercises 11 and 12 use the TLS lab in [`lab/tls/`](lab/tls/) (`./make-certs.sh` then `docker compose up -d --wait`).

Solutions: [solutions/README.md](solutions/README.md). Runnable scripts are in [solutions/](solutions/).

---

## Exercise 1 ★: A role report from the catalogs

Write **one** query against the catalogs that lists every non-predefined role (exclude names matching `pg_%`) with: its attributes (which of `SUPERUSER`, `LOGIN`, `CREATEROLE`, `CREATEDB`, `REPLICATION`, `BYPASSRLS`, `NOINHERIT` it has), its connection limit (blank if unlimited), and the roles it is a member of together with that grant's options (`admin`/`inherit`/`set`).

**Self-check:** exactly six rows. `postgres` shows all six positive attributes; `bazaar_app` shows `LOGIN` and connection limit `50`; `bazaar_analyst` shows member of `bazaar_readonly (inherit,set)`; `bazaar_owner` and `bazaar_readonly` have no `LOGIN`.

<details><summary>Hint</summary>

`pg_roles` has the boolean attribute columns (`rolsuper`, `rolcanlogin`, `rolcreaterole`, `rolcreatedb`, `rolreplication`, `rolbypassrls`, `rolinherit`, `rolconnlimit`). Membership and its options are in `pg_auth_members` (`admin_option`, `inherit_option`, `set_option`, PG 16+). Build the attribute list with `array_remove(ARRAY[CASE WHEN ... THEN '...' END, ...], NULL)`.

</details>

---

## Exercise 2 ★: What can each login role do?

Produce a privilege matrix: one row per base table, view and materialized view in schema `bazaar` (skip the `events` partitions — query through the parent), with a column for each of `bazaar_app`, `bazaar_analyst`, `bazaar_seller` summarising the effective table privileges (SELECT/INSERT/UPDATE/DELETE), and a column showing whether RLS is enabled on the table. Distinguish a full `SELECT`/`UPDATE` from one that exists only at column level.

**Self-check:** `products`, `inventory`, `orders`, `order_items` have RLS enabled; the others don't. `bazaar_app` has full `SELECT/INSERT/UPDATE/DELETE` everywhere; `bazaar_analyst` has `SELECT` everywhere; `bazaar_seller` has `SELECT` on `categories`/`sellers`/`reviews`, `SELECT/INSERT/UPDATE` on `products`/`inventory`, and on `orders` a `SELECT` plus a *column-level* `UPDATE` (not a full one). `bazaar_seller` shows nothing for `users`, `payments`, etc.

<details><summary>Hint</summary>

`has_table_privilege(role, tableoid, 'SELECT')` and friends test effective (membership-aware) table privileges; `has_any_column_privilege(role, tableoid, 'UPDATE')` is true when *some* column is granted even if the whole table isn't. `pg_class.relrowsecurity` is the RLS flag; filter `relkind IN ('r','p','v','m') AND NOT relispartition`.

</details>

---

## Exercise 3 ★: The append-only audit log

The application role can currently `DELETE FROM audit_log` and even `UPDATE` historical rows (it got full DML from the `ALL TABLES` grant). The audit trail must be append-only: writable *only* through the `SECURITY DEFINER` `audit_trigger()`, readable by the app, but never directly inserted, updated, deleted or truncated by `bazaar_app`. Fix it, then prove that (a) a normal product change still writes an audit row and (b) the app can no longer tamper with the log.

**Self-check:** after your change, as `bazaar_app`, `UPDATE products SET price = 989.00 WHERE id = 1` still adds one `audit_log` row (written by the trigger), but `DELETE FROM audit_log`, `INSERT INTO audit_log ...` and `UPDATE audit_log ...` all raise "permission denied for table audit_log".

Undo: `GRANT INSERT, UPDATE, DELETE, TRUNCATE ON bazaar.audit_log TO bazaar_app;` (or re-run `small15.sh --with-1501`).

<details><summary>Hint</summary>

The trigger is `SECURITY DEFINER` owned by `bazaar_owner`, so it writes as the owner regardless of the caller's table privileges. You only need to `REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON bazaar.audit_log FROM bazaar_app;` and keep `SELECT`.

</details>

---

## Exercise 4 ★★: A read-only reporting role, two ways

A new BI tool needs read-only access to all of Bazaar. Implement it **two** ways and compare: (a) a role `bi_builtin` made a member of the predefined `pg_read_all_data`; (b) a role `bi_custom` made a member of Bazaar's own `bazaar_readonly`. For each, check whether it can read `users.email`, `payments`, and a brand-new table created afterwards by `bazaar_owner`. Explain the difference and say which fits Bazaar's policy that analysts must not read e-mail.

**Self-check:** `bi_builtin` can read `users.email`; `bi_custom`'s ability depends on whether you've applied the column restriction (exercise 5 / the milestone). Both read `payments`. For a new table created by `bazaar_owner` after the grants: `bi_builtin` reads it immediately (the predefined role is dynamic); `bazaar_readonly` reads it only because of the default privileges in `1501` (so `bi_custom` does too) — confirm by checking a table created by `postgres` instead, which `bazaar_readonly` cannot read.

<details><summary>Hint</summary>

`GRANT pg_read_all_data TO bi_builtin;` vs. `GRANT bazaar_readonly TO bi_custom;`. `pg_read_all_data` is evaluated dynamically (always "all data"); `bazaar_readonly` holds concrete grants plus default privileges keyed on the creating role (§5.4). Clean up with `DROP ROLE bi_builtin, bi_custom;`.

</details>

---

## Exercise 5 ★★: Hide users.email from the read-only bundle

Implement the milestone's e-mail rule: `bazaar_readonly` (and therefore `bazaar_analyst`) may read every column of `users` **except** `email`, and may still `count(*)` and read names. Then add a view `users_masked` that exposes the e-mail *domain* (the part after `@`) but not the address, readable by the analyst.

**Self-check:** as `bazaar_analyst`, `SELECT email FROM users` and `SELECT * FROM users` both fail with "permission denied for table users"; `SELECT id, full_name FROM users WHERE id = 17` returns `Zoë Martin`; `SELECT count(*) FROM users` returns 30. `SELECT email_domain, count(*) FROM users_masked GROUP BY 1` shows `example.com` with the most (10). `has_column_privilege('bazaar_analyst','bazaar.users','email','SELECT')` is false.

<details><summary>Hint</summary>

A column `REVOKE` is a no-op while a table-level `SELECT` grant exists: `REVOKE SELECT ON bazaar.users FROM bazaar_readonly;` then `GRANT SELECT (id, full_name, is_active, created_at) ...`. The masked view must run as its owner to read `email`, so create it as `bazaar_owner` (ordinary, owner-privilege view — not `security_invoker`), and grant `SELECT` on the view to `bazaar_readonly`. Reference: [`solutions/ex05_hide_email.sql`](solutions/ex05_hide_email.sql).

</details>

---

## Exercise 6 ★★: Sellers cannot modify archived products

Add a policy so a seller may read all of their own products but **cannot update a product whose status is `archived`** — while still being allowed to *archive* an active one. Do not touch the existing `products_seller` policy.

**Self-check:** as `bazaar_seller` with `app.seller_id = 1`: product 5 (archived, seller 1) — `UPDATE products SET price = 1.00 WHERE id = 5` affects 0 rows and `UPDATE ... SET status = 'active' WHERE id = 5` affects 0 rows; product 2 (active, seller 1) — `UPDATE ... SET status = 'archived' WHERE id = 2` succeeds (1 row); after which `UPDATE ... SET price = 1.00 WHERE id = 2` affects 0 rows (now archived). Other sellers' products remain invisible.

<details><summary>Hint</summary>

A `RESTRICTIVE` `FOR UPDATE` policy is `AND`ed with the permissive one. `USING (status <> 'archived')` says which existing rows may be updated. You must add `WITH CHECK (true)` explicitly — otherwise the `USING` is reused as the check and would also forbid *archiving* an active product. Reference: [`solutions/ex06_archived_products.sql`](solutions/ex06_archived_products.sql). Undo: `DROP POLICY products_seller_not_archived ON bazaar.products;`.

</details>

---

## Exercise 7 ★★: Find and fix mis-owned objects

A sloppy migration created some objects while connected as `postgres` instead of `SET ROLE bazaar_owner`. Simulate it, then write a script that (a) **reports** every table, view, sequence and function in schema `bazaar` not owned by `bazaar_owner`, and (b) fixes each: transfers ownership to `bazaar_owner` and applies the grants the default privileges would have given (app gets DML/USAGE/EXECUTE, readonly gets SELECT).

Simulate first:

```sql
CREATE TABLE bazaar.coupons (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code text NOT NULL UNIQUE, percent_off int NOT NULL CHECK (percent_off BETWEEN 1 AND 90),
    valid_until timestamptz NOT NULL);
CREATE FUNCTION bazaar.coupon_is_valid(p_code text, p_at timestamptz) RETURNS boolean
    LANGUAGE sql STABLE
    RETURN EXISTS (SELECT 1 FROM bazaar.coupons WHERE code = p_code AND valid_until > p_at);
```

**Self-check:** before the fix, as `bazaar_app`, `INSERT INTO coupons ...` and `SELECT coupon_is_valid(...)` fail (permission denied) — the objects are owned by `postgres` and got no grants. After the fix they succeed, and your report returns zero rows. Confirm `coupons`'s ACL now matches an existing table like `cart_items`.

<details><summary>Hint</summary>

`pg_class.relowner` and `pg_proc.proowner`; compare against `'bazaar_owner'::regrole`. Skip identity/serial sequences (they follow their table) by excluding `pg_depend` rows with `deptype IN ('a','i')`. Build `ALTER ... OWNER TO` and `GRANT ...` statements with `format(...)` and run them with `\gexec`. Reference: [`solutions/ex07_fix_ownership.sql`](solutions/ex07_fix_ownership.sql). Clean up: `DROP FUNCTION bazaar.coupon_is_valid; DROP TABLE bazaar.coupons;`.

</details>

---

## Exercise 8 ★★★: Design a "support" role

Customer support needs to look up any order (across all sellers) and change an order's **status** — but only through safe transitions, and nothing else. Design it:

- a login role `bazaar_support` that may **read** `orders`, `order_items`, `payments`, `sellers`, and users' `id`/`full_name` only (never `email`);
- it may change an order's status **only** by calling a function, which enforces the allowed transitions (support may cancel a `pending` or `paid` order, and refund a `delivered` one — nothing else) and requires a reason;
- it may not `UPDATE orders` directly, and the change is audited with the support user's name.

Because `orders`/`order_items` have RLS enabled, remember support needs a policy or it sees zero rows.

**Self-check:** as `bazaar_support`: `SELECT count(DISTINCT seller_id) FROM orders` returns all sellers (4 on small data); `SELECT email FROM users` fails; `UPDATE orders SET status='cancelled' WHERE id=115` fails (permission denied); `support_set_order_status(115,'cancelled','customer asked to cancel')` succeeds; `support_set_order_status(116,'delivered',...)` raises "support may not change order 116 from pending to delivered"; a too-short reason is rejected. `bazaar_app` cannot execute the function (revoked). The audit row's `changed_by` is `bazaar_support`.

<details><summary>Hint</summary>

Read grants + a `FOR SELECT ... USING (true)` policy `TO bazaar_support` on the RLS tables. The mutator is `SECURITY DEFINER` owned by `bazaar_owner`, with `SET search_path`, `REVOKE EXECUTE ... FROM PUBLIC, bazaar_app` and `GRANT EXECUTE ... TO bazaar_support`. Inside a `SECURITY DEFINER` function `current_user` is the owner, so pass `session_user` explicitly as `changed_by`. Reference: [`solutions/ex08_support_role.sql`](solutions/ex08_support_role.sql). Clean up: `DROP ROLE` after `DROP FUNCTION`/`DROP POLICY` and `REVOKE`, or re-run `small15.sh`.

</details>

---

## Exercise 9 ★★★: Reviews, per seller

Extend RLS so a seller sees only the reviews of **their own products**. `reviews` currently has no RLS. Enable it, keep the app and read-only roles seeing everything, and add a seller policy. (A review links to a product via `reviews.product_id`.)

**Self-check:** as `bazaar_seller` with `app.seller_id = 1`, `SELECT count(*) FROM reviews` returns 17 and `round(avg(rating),2)` is `3.53`; with `app.seller_id = 5` (a seller with products but no sales) it returns 0; with no setting, 0. As `bazaar_app` and `bazaar_analyst`, all 78 reviews are visible.

<details><summary>Hint</summary>

`ALTER TABLE reviews ENABLE ROW LEVEL SECURITY;` then a `TO bazaar_app, bazaar_readonly USING (true) WITH CHECK (true)` policy and a `FOR SELECT TO bazaar_seller USING (EXISTS (SELECT 1 FROM products p WHERE p.id = reviews.product_id))` policy — the subquery runs under the seller's own products policy, so it needs no explicit `seller_id`. Reference: [`solutions/ex09_reviews_rls.sql`](solutions/ex09_reviews_rls.sql). Undo: `DROP POLICY`s then `ALTER TABLE reviews DISABLE ROW LEVEL SECURITY;`.

</details>

---

## Exercise 10 ★★★: Measure RLS overhead (large data)

Use the large dataset: `./project/bin/reset.sh 16`. For `bazaar_seller`, compare the plan and timing of `SELECT count(*) FROM orders` for a huge seller (`app.seller_id = 1`, ~70k orders) and a tiny one (`app.seller_id = 2000`, 3 orders), then do the same for `SELECT count(*) FROM order_items`. Explain why `orders` is fast and index-backed while `order_items` is not, and propose a schema change that would make the `order_items` seller count index-only. Measure your fix.

**Self-check:** `orders` uses `Index Only Scan using orders_seller_id_created_at_idx` with the policy predicate as the `Index Cond` (~14 ms for seller 1, ~0.04 ms for seller 2000). `order_items` does a `Seq Scan` of 1.27M rows with a hashed subquery (~250–550 ms) regardless of seller, because the policy reaches `orders` via a subquery and `order_items` has no indexable tenant key. After adding a denormalised `order_items.seller_id` with an index and rewriting the policy to `seller_id = current_seller_id()`, the count is an `Index Only Scan` (~22 ms for seller 1, ~0.05 ms for seller 2000).

<details><summary>Hint</summary>

`EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)`. The RLS predicate appears in the plan as `(seller_id = (NULLIF(current_setting('app.seller_id', true), ''))::bigint)`. The fix: `ALTER TABLE order_items ADD COLUMN seller_id bigint; UPDATE ... FROM orders; CREATE INDEX; ALTER POLICY order_items_seller_select ... USING (seller_id = current_seller_id());`. (A real migration would keep it in sync with a trigger or a generated value; here a one-off backfill is enough to measure.)

</details>

---

## Exercise 11 ★★: sslmode in the lab

Bring up the TLS lab (`cd modules/15-security-rls/lab/tls && ./make-certs.sh && docker compose up -d --wait`). From the `client` container, connect as `app` to the database `lab` with each of `sslmode=disable`, `require`, `verify-ca`, `verify-full` — once using the host name that is in the server certificate (`db.bazaar.internal`) and once using the other name for the same server (`db`). Record which combinations succeed and which fail, and explain the two failures.

**Self-check:** `disable` always fails (the lab's `pg_hba.conf` rejects unencrypted connections). To `db.bazaar.internal`, `require`/`verify-ca`/`verify-full` all succeed. To `db`, `require` and `verify-ca` succeed but `verify-full` fails with "server certificate for \"db.bazaar.internal\" ... does not match host name \"db\"". `verify-ca`/`verify-full` also fail without `sslrootcert=/lab/certs/ca.crt` ("root certificate file ... does not exist").

<details><summary>Hint</summary>

`docker compose exec client psql "host=db.bazaar.internal dbname=lab user=app password=app_pw sslmode=verify-full sslrootcert=/lab/certs/ca.crt"`. Swap `host=` and `sslmode=`. `./tls-demo.sh` runs the whole matrix for you; the point is to predict each line first. `verify-ca` checks the chain; only `verify-full` also checks the host name.

</details>

---

## Exercise 12 ★★★: Harden pg_hba.conf and migrate off md5

In the TLS lab, the role `legacy` has an md5-format password and the `pg_hba.conf` has a `md5` line for it. Edit `conf/pg_hba.conf` (on your machine; it is mounted read-only into the container) to (a) forbid the superuser over the network entirely, (b) require SCRAM for `legacy` like everyone else, removing the md5 line, and (c) restrict a new reporting role to connections from the server's own network only. Then perform the md5→scram migration so `legacy` can still log in. Verify with `pg_hba_file_rules` and connection attempts.

**Self-check:** after editing and `SELECT pg_reload_conf()`, `pg_hba_file_rules` shows no `md5` line and no `error`. Connecting as `postgres` over TLS to `lab` now fails ("pg_hba.conf rejects connection"). `legacy` *fails* to log in until you run `ALTER ROLE legacy PASSWORD 'legacy_pw'` (which re-hashes it as SCRAM, since `password_encryption` is `scram-sha-256`); the server log shows "User \"legacy\" does not have a valid SCRAM secret" before the fix. The reporting role connects from inside the Compose network (`samenet`) but a `disable`/external attempt is rejected.

<details><summary>Hint</summary>

Order matters — the `reject` line for `postgres` must come before any line that would match it. Use `+lab_users` for the group and `samenet` for the network restriction. `SELECT rolname FROM pg_authid WHERE rolpassword LIKE 'md5%'` finds roles still on md5. Reload, don't restart. Reference `pg_hba.conf`: [`solutions/ex12/pg_hba.conf`](solutions/ex12/pg_hba.conf). Reset the lab's own file afterwards (`git checkout` or re-copy) so later runs of `tls-demo.sh` match the README.

</details>

---

## Exercise 13 ★★★: Exploit, then fix, a search_path hole

Here is a `SECURITY DEFINER` function the team added so analysts can get a shop's revenue by name without reading `orders` directly. It has the hijack bug. Create it, exploit it as `bazaar_analyst` to run an `UPDATE` you have no privilege for, then fix it and show the exploit no longer works. Assume the analyst has a personal schema `analyst_scratch` they may write to.

```sql
SET ROLE bazaar_owner;
CREATE FUNCTION bazaar.shop_revenue(p_shop_name text) RETURNS numeric
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_total numeric;
BEGIN
    SELECT coalesce(sum(o.total),0) INTO v_total
    FROM orders o JOIN sellers s ON s.id = o.seller_id
    WHERE lower(s.shop_name) = lower(p_shop_name)      -- unqualified lower(): the bug
      AND o.status IN ('paid','shipped','delivered');
    RETURN v_total;
END $$;
RESET ROLE;
```

**Self-check:** before the fix, as `bazaar_analyst`, you can cause `products` row 3's price to change to `0.01` even though the analyst has no `UPDATE` on `products`, by planting a shadowing `lower()` in your schema, granting the owner `USAGE` on that schema, and putting it first on `search_path`. After adding `SET search_path = pg_catalog, bazaar, pg_temp` to the function, the same attack leaves product 3 at `2199.99`.

<details><summary>Hint</summary>

The body calls an unqualified function (`lower()`) and names tables unqualified. The attacker needs a schema they can write to: the analyst cannot `CREATE SCHEMA` itself, so assume a personal schema exists (created for them with `CREATE SCHEMA analyst_scratch AUTHORIZATION bazaar_analyst`). In it, define `analyst_scratch.lower(text)` that does the privileged `UPDATE` and then returns `pg_catalog.lower($1)`. **Crucially**, the function runs as its owner `bazaar_owner`, so the owner must be able to resolve your schema: `GRANT USAGE ON SCHEMA analyst_scratch TO PUBLIC`. Then `SET search_path = analyst_scratch, pg_catalog, bazaar` and call the function. Fix with `ALTER FUNCTION ... SET search_path = pg_catalog, bazaar, pg_temp`. Reference: [`solutions/ex13_search_path.sql`](solutions/ex13_search_path.sql) (it uses a `shop_revenue(text)` function). Restore product 3 and drop the schema when done.

</details>

---

## Exercise 14 ★★: Close the sequence side channel

A seller can learn the marketplace's global order volume through a side channel: `SELECT nextval('orders_id_seq')` and `SELECT currval(...)` leak the current high-water id even though the seller sees only their own orders. Confirm the leak, then close it without breaking the seller's ability to insert products (which use an identity sequence).

**Self-check:** before the fix, as `bazaar_seller` with `app.seller_id = 2`, `SELECT nextval('orders_id_seq')` returns the global next id (≈121 on small data). After `REVOKE USAGE ON ALL SEQUENCES IN SCHEMA bazaar FROM bazaar_seller;`, that call raises "permission denied for sequence orders_id_seq", yet `INSERT INTO products (seller_id, ...) VALUES (2, ...)` still succeeds (an identity column drives its sequence internally, needing no sequence `USAGE` from the inserting role).

<details><summary>Hint</summary>

`1501` grants the seller `USAGE ON ALL SEQUENCES` (a leftover from a broader grant). Revoke it. Verify that identity inserts still work — they do, which is the interesting part. Undo: `GRANT USAGE ON ALL SEQUENCES IN SCHEMA bazaar TO bazaar_seller;`.

</details>

---

## Exercise 15 ★★★: Prove the FK existence leak and seal it

Create a seller-owned `order_notes(seller_id, order_id, note)` table with a foreign key to `orders` and a seller RLS policy. Show that a seller can use the FK check to learn whether an order id they cannot see *exists* (a different error for "exists but not yours" vs. "doesn't exist"), then change the policy so both cases fail identically.

**Self-check:** with the naive policy (`USING`/`WITH CHECK` on `seller_id = current_seller_id()` only), as seller 2, `INSERT INTO order_notes (2, 40, ...)` **succeeds** even though order 40 is seller 1's and invisible — the FK check saw it. `INSERT ... (2, 4000, ...)` fails with an FK violation. That difference is the oracle. After moving an existence check *into* the policy's `WITH CHECK` (`AND EXISTS (SELECT 1 FROM orders WHERE id = order_id)`), inserting a note for order 40 fails with "new row violates row-level security policy" — the same failure as for a non-existent id — while a note for one of seller 2's own orders still succeeds.

<details><summary>Hint</summary>

The FK integrity check is never subject to RLS, so it reveals existence. The `EXISTS` subquery in `WITH CHECK` *does* run under the seller's `orders` policy, so an invisible order looks non-existent to it. Reference: [`solutions/ex15_fk_leak.sql`](solutions/ex15_fk_leak.sql). Clean up: `DROP TABLE bazaar.order_notes;`.

</details>
