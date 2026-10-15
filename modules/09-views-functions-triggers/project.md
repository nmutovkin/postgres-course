# Module 09: Project milestone — logic next to the data

<!-- nav:start -->
> [Theory](README.md) · [Exercises](exercises.md) · **Project** · [Solutions](solutions/README.md)  
> [← 08 Beyond Scalars](../08-beyond-scalars/README.md) · [Course home](../../README.md) · [10 Transactions and Concurrency →](../10-transactions-concurrency/README.md)
<!-- nav:end -->

Bazaar's bookkeeping has been the application's job, and the application keeps forgetting. In this milestone you move the mechanical rules into the database, so that every writer (the API, the admin scripts, a psql session during an incident) gets them for free. You also give the storefront and the seller dashboard a shared read model. You write one migration, `0901_schema_logic.sql`, that does the five things below.

**Starting state:** `./project/bin/reset.sh 09` (the v3 schema from module 08).

```bash
docker exec -i course-pg psql -X -U postgres -d bazaar -v ON_ERROR_STOP=1 < my_0901_schema_logic.sql
```

The theory walks through each piece. The point of the milestone is to assemble them into one migration that runs atomically, in the right order, and passes the checks. Try to write each part from the requirements before looking back at the theory.

<!-- toc:start -->
**Contents**

- [Requirements](#requirements)
- [Acceptance checks](#acceptance-checks)
- [Reference solution](#reference-solution)

<!-- toc:end -->

## Requirements

1. **`updated_at` that can't be forgotten.**
   - Add `orders.updated_at timestamptz NOT NULL`, backfilled with the latest of `created_at`, `paid_at`, `shipped_at` and `delivered_at` (NULLs ignored). New orders default to `now()`.
   - A trigger function `set_updated_at()`, attached as a `BEFORE UPDATE ... FOR EACH ROW` trigger to `products`, `inventory` and `orders` (named `<table>_set_updated_at`), that sets `updated_at` to the transaction time on every update, overriding any value the statement supplied.
   - Mind the order of operations: the backfill must not be overwritten by your own trigger.

2. **A generic audit log.**
   - A table `audit_log` (`id` identity primary key, `table_name`, `operation` restricted to `INSERT`/`UPDATE`/`DELETE`, `row_pk jsonb`, `old_row jsonb`, `new_row jsonb`, `changed_by text`, `changed_at timestamptz DEFAULT now()`).
   - **One** trigger function `audit_trigger()` that works for any table. It receives the primary-key column names as trigger arguments (`EXECUTE FUNCTION audit_trigger('id')`), stores whole rows as `jsonb`, records `session_user` as `changed_by`, and **skips updates that change nothing except `updated_at`**.
   - Attach it as `AFTER INSERT OR UPDATE OR DELETE ... FOR EACH ROW` to `products` (`products_audit`) and `orders` (`orders_audit`).

3. **`orders.total` maintained from `order_items`.**
   - `orders.total` must always equal `sum(quantity * unit_price)` of the order's items, after any `INSERT`, `UPDATE` (including moving an item to another order) or `DELETE` on `order_items`.
   - Use **statement-level** triggers with **transition tables**, so that a multi-row statement updates each affected order once. Apply **deltas** (not a recomputed sum), so concurrent transactions adding items to the same order don't lose each other's changes. Name the triggers `order_items_total_ins`, `_upd` and `_del`, and the function `order_items_maintain_total()`.

4. **`active_products` view.** What the storefront lists: every *active* product with its `id`, `seller_id`, `shop_name`, `category_id`, `category_name`, `sku`, `title`, `description`, `price`, `attributes`, `tags`, `available` (= quantity − reserved, **0 when the product has no inventory row**), `created_at` and `updated_at`.

5. **`seller_sales_daily` materialized view.** One row per (seller, day) with `orders_count`, `items_sold` and `revenue`, counting orders whose status is `paid`, `shipped` or `delivered`. The day is the order's `created_at` **in UTC**. Revenue must not be inflated by the order lines (aggregate items per order before joining). It must be refreshable with `REFRESH MATERIALIZED VIEW CONCURRENTLY`.

Add a `COMMENT` to the main objects, and make the whole migration a single transaction.

## Acceptance checks

```bash
docker exec -i course-pg psql -X -U postgres -d bazaar < modules/09-views-functions-triggers/solutions/project_checks.sql
```

Expected:

1. **8 triggers:** `products_audit`, `products_set_updated_at`, `inventory_set_updated_at`, `orders_audit`, `orders_set_updated_at` (row-level), and `order_items_total_del`/`_ins`/`_upd` (statement-level).
2. **0** orders whose `updated_at` differs from the latest lifecycle timestamp.
3. After an update, `products.updated_at` and `inventory.updated_at` equal `now()`: `t | t`.
4. A no-op update plus a real price change of product 10 leave exactly **1** audit row: `{"id": 10}`, 229.00 → 230.00, `changed_by = session_user`.
5. A new order with three lines has total **59.97**. After changing a quantity and deleting a line it's **62.97**, and after deleting all lines it's **0.00**. **0** orders are inconsistent.
6. `active_products` has **54** rows, product 3 has 0 available, and none of the draft or archived products (5, 14, 23, 28) appear.
7. `seller_sales_daily` gives sellers 1–4 the revenues 41,598.82, 1,656.43, 3,562.94 and 2,277.00 (31, 29, 23 and 18 orders), **96** rows in total, and `REFRESH ... CONCURRENTLY` succeeds.

Then run the project's sanity checks, which include the `orders.total` invariant:

```bash
docker exec -i course-pg psql -X -U postgres -d bazaar < project/db/tools/check.sql
```

Every check must report 0, except the deliberate "(info)" row.

Finally, a concurrency test with two psql sessions (module 10 explains *why* it works):

```sql
-- session A                                   -- session B
BEGIN;                                         BEGIN;
INSERT INTO order_items VALUES (120, 35, 1, 39.00);
                                               INSERT INTO order_items VALUES (120, 36, 1, 34.99);
                                               -- (waits for A: both update orders row 120)
COMMIT;
                                               COMMIT;
SELECT total, (SELECT sum(quantity * unit_price) FROM order_items WHERE order_id = 120) FROM orders WHERE id = 120;
-- both columns must be equal
```

## Reference solution

[`project/db/0901_schema_logic.sql`](../../project/db/0901_schema_logic.sql). Compare it with yours, in particular:

- the order *add column → backfill → create trigger* for `orders.updated_at`;
- `(v_old - 'updated_at') = (v_new - 'updated_at')` for the no-op check, and `jsonb_object_agg` over `unnest(TG_ARGV)` for the primary key;
- the three branches of `order_items_maintain_total()`, one per transition-table combination, and the `d.delta <> 0` filter that avoids pointless updates;
- `coalesce(i.quantity - i.reserved, 0)` with a `LEFT JOIN inventory`;
- the per-order item subquery and `AT TIME ZONE 'UTC'` in `seller_sales_daily`, plus its unique index `seller_sales_daily_seller_id_day_key`.

After this milestone, `./project/bin/reset.sh 10` gives exactly the reference state. Module 15 later makes `audit_trigger()` a `SECURITY DEFINER` function and adds `refresh_seller_sales_daily()` for roles that don't own the materialized view.
