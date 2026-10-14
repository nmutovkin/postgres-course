# Module 07: Project milestone — Bazaar schema v2

**Goal.** Bazaar v1 can take orders, but it can't remember where to ship them, how they were paid, what customers thought of the products, what's waiting in a shopper's cart, or what a product cost last year. In this milestone you design and write **schema v2**: the migration that adds addresses, shipping addresses on orders, payments and refunds, reviews, carts and price history. This time, the requirements are given in business terms only. Choosing keys, types, constraints and delete behaviour is your job.

Keep your files in `workspace/07/`. Start from the v1 schema:

```bash
./project/bin/reset.sh 07
```

## Business requirements

These are the same requirements as in section 1.2 of the theory, with the details a developer would ask for.

1. **Addresses.** A user can store any number of postal addresses. An address has a recipient name, a first address line, an optional second line, a city, a postal code (which is *not* a number: `'SW1A 1AA'`, `'02116'`), a two-letter ISO country code and an optional label such as "Home" or "Work". Each address belongs to exactly one user. **At most one** of a user's addresses is their default; a user may have no default (or no addresses) at all.
2. **Shipping address on orders.** An order can record the address it ships to. Orders placed before addresses existed have none, so the column must allow NULL. An address that an order ships to must not be deletable.
3. **Payments and refunds.** An order can have any number of payments: each has an amount, a provider (`'stripe'`, `'paypal'`, `'adyen'`, ...), the provider's transaction reference (unknown until the provider confirms), a status (`pending`, `succeeded` or `failed`) and a creation time. A **refund is a payment with a negative amount**; an amount of zero is meaningless. Payments are financial records: an order that has payments can't be deleted.
4. **Reviews.** A user can review a product with a rating from 1 to 5 and an optional title and text. **A user reviews a given product at most once.** Reviews disappear with their product. Other things (helpful votes, seller replies) will later refer to individual reviews.
5. **Carts.** A user's cart contains products with a positive quantity and the time each was added. A product is in a user's cart at most once: adding it again increases the quantity, with `INSERT ... ON CONFLICT (user_id, product_id) DO UPDATE`.
6. **Price history.** For any product and any past moment, we must be able to find the price valid at that moment. A product has at most one price starting at a given moment, and prices are never negative.
7. **Lifecycle.** Deleting a user account (possible only for users without orders) also deletes their addresses and cart. Deleting a product (possible only for products never ordered) also deletes its reviews and price history, and removes it from carts.

### Names

So that the acceptance tests, the reference seed and later modules work with your schema, use exactly these table and column names (types, keys and constraints are up to you):

| Table | Columns |
|---|---|
| `addresses` | `id`, `user_id`, `label`, `recipient`, `line1`, `line2`, `city`, `postal_code`, `country_code`, `is_default`, `created_at` |
| `orders` (new column) | `shipping_address_id` |
| `payments` | `id`, `order_id`, `amount`, `provider`, `provider_ref`, `status`, `created_at` |
| `reviews` | `id`, `product_id`, `user_id`, `rating`, `title`, `body`, `created_at` |
| `cart_items` | `user_id`, `product_id`, `quantity`, `added_at` |
| `product_price_history` | `product_id`, `price`, `valid_from` |

Follow the course conventions from module 02 and theory section 12: `bigint GENERATED ALWAYS AS IDENTITY` surrogate keys where a surrogate is appropriate, `numeric(12,2)` for money, `timestamptz` for timestamps, defaults for `created_at`/`added_at`, named `CHECK` constraints, and a `COMMENT ON TABLE` for every new table.

## What to deliver

1. **`workspace/07/erd.md`**: a Mermaid ER diagram of the new tables and their relationships to the v1 tables, with cardinality and optionality on every line.
2. **`workspace/07/0701_schema_v2.sql`**: the migration. It must run without errors on `./project/bin/reset.sh 07`, in one go, with `ON_ERROR_STOP`:

   ```bash
   docker exec -i course-pg psql -X -U postgres -d bazaar -v ON_ERROR_STOP=1 < workspace/07/0701_schema_v2.sql
   ```

3. **`workspace/07/decisions.md`**: one or two sentences per non-obvious decision. At least: the primary key of each new table and why; how "at most one default address" is enforced; the `ON DELETE` behaviour of each new foreign key; why `order_items.unit_price` must stay even though you now have a price history.

## Acceptance checks

### 1. Behaviour: the constraint tests

[`solutions/acceptance_v2.sql`](solutions/acceptance_v2.sql) exercises every rule above: it inserts valid rows that must be accepted and invalid ones that must be rejected with the right class of error (unique violation, check violation, foreign-key violation, NOT NULL violation), and checks the delete behaviour. It doesn't depend on your constraint names, and it rolls everything back.

```bash
docker exec -i course-pg psql -X -U postgres -d bazaar < modules/07-data-modeling/solutions/acceptance_v2.sql
```

Every line must start with `PASS`. On the reference schema, the output is:

```text
PASS  addresses: first default address
PASS  addresses: two non-default addresses are fine
PASS  addresses: a second default for the same user is rejected  (rejected: 23505)
PASS  addresses: switching the default, step 1: unset the old default
PASS  addresses: switching the default, step 2: set the new default
PASS  addresses: recipient is required  (rejected: 23502)
PASS  addresses: unknown user is rejected  (rejected: 23503)
PASS  orders: shipping address may be NULL
PASS  orders: shipping address must exist  (rejected: 23503)
PASS  orders: shipping address can be set
PASS  addresses: an address used by an order cannot be deleted  (rejected: 23503)
PASS  payments: a payment
PASS  payments: a refund is a negative amount
PASS  payments: amount 0 is rejected  (rejected: 23514)
PASS  payments: unknown status is rejected  (rejected: 23514)
PASS  payments: unknown order is rejected  (rejected: 23503)
PASS  payments: an order with payments cannot be deleted  (rejected: 23503)
PASS  reviews: a review
PASS  reviews: a second review of the same product by the same user is rejected  (rejected: 23505)
PASS  reviews: the same user may review another product
PASS  reviews: rating 6 is rejected  (rejected: 23514)
PASS  reviews: rating 0 is rejected  (rejected: 23514)
PASS  reviews: title and body are optional
PASS  cart: add a product
PASS  cart: the same product twice is rejected  (rejected: 23505)
PASS  cart: "add again" works as an upsert on (user_id, product_id)
PASS  cart: quantity after the upsert is 3
PASS  cart: quantity 0 is rejected  (rejected: 23514)
PASS  price history: two prices
PASS  price history: two prices valid from the same moment are rejected  (rejected: 23505)
PASS  price history: negative price is rejected  (rejected: 23514)
PASS  price history: valid_from is required  (rejected: 23502)
PASS  users: deleting a user without orders or reviews removes their addresses and cart
PASS  users: no addresses or cart items left behind
PASS  products: deleting a never-ordered product removes its reviews and price history
```

(The script also prints a few housekeeping lines such as `BEGIN`, `CREATE FUNCTION` and `INSERT 0 1` for its fixtures.) If you chose an enum for the payment status, the "unknown status" test reports `22P02` instead of `23514`; that's accepted too. A missing table stops the script with an error: create all six.

### 2. Data: load the reference seed into your schema

If your names match the table above, the reference data loads into your schema unchanged:

```bash
docker exec -i course-pg psql -X -U postgres -d bazaar -v ON_ERROR_STOP=1 < project/db/0702_seed_modeling.sql
```

If it fails, the error tells you which of your constraints rejects real data, or which column differs. Fix your schema, not the seed. Then:

```sql
SELECT (SELECT count(*) FROM addresses)              AS addresses,
       (SELECT count(*) FROM payments)               AS payments,
       (SELECT count(*) FROM reviews)                AS reviews,
       (SELECT count(*) FROM cart_items)             AS cart_items,
       (SELECT count(*) FROM product_price_history)  AS price_history,
       (SELECT count(*) FROM orders WHERE shipping_address_id IS NOT NULL) AS orders_with_address;
```

```text
 addresses | payments | reviews | cart_items | price_history | orders_with_address 
-----------+----------+---------+------------+---------------+---------------------
        32 |      140 |      78 |         14 |            71 |                 119
(1 row)
```

And the questions v2 was built to answer:

- **Default address:** user 1's default address is in Portland (US); user 7 has an address but no default; user 24 has no address. Write the query with a `LEFT JOIN` on `addresses ... AND is_default`, so all three users appear.
- **Best-reviewed:** product 24 has the most reviews (4, average 4.25).
- **Carts:** 8 users have something in their cart; user 15's cart is worth 2,199.99 at current prices.
- **Price at order time:** for every order line, the price valid in `product_price_history` at the order's `created_at` equals `order_items.unit_price`: 235 lines, 0 mismatches (theory section 9.2 shows the query).
- **Ledger:** every refunded order has exactly one negative payment, and its net succeeded amount is 0.

Finally, run the course's sanity checks, which include v2 checks once `payments` exists:

```bash
docker exec -i course-pg psql -X -U postgres -d bazaar < project/db/tools/check.sql
```

All checks must show 0, except the "(info)" row about the one deliberate review by a non-buyer, which shows 1.

## Reference solution

- Schema: [`project/db/0701_schema_modeling.sql`](../../project/db/0701_schema_modeling.sql)
- Data: [`project/db/0702_seed_modeling.sql`](../../project/db/0702_seed_modeling.sql)

`./project/bin/reset.sh 08` gives you the reference v2 with its data, which is what every later module builds on. Compare your decisions with the comments at the top of `0701_schema_modeling.sql`. Where they differ, neither is necessarily wrong, but you should be able to say why you chose yours. The ERD in section 2.2 of the theory is the reference diagram.
