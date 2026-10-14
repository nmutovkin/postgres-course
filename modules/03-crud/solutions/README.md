# Module 03: Solutions

<!-- nav:start -->
> [Theory](../README.md) · [Exercises](../exercises.md) · [Project](../project.md) · **Solutions**  
> [← 02 Data Types and Tables](../../02-types-and-ddl/README.md) · [Course home](../../../README.md) · [04 Joins and Subqueries →](../../04-joins-subqueries/README.md)
<!-- nav:end -->

Every solution was run on PostgreSQL 17 against `./project/bin/reset.sh 04` (the core schema plus the small seed), with the session time zone at UTC. Exercises that change data run inside a transaction that's rolled back, so they all start from the same data. Generated ids and `now()` values may differ on your machine if you've inserted other rows. Everything else should match.

Files in this directory:

| File | What |
|---|---|
| [`load_catalog.sql`](load_catalog.sql) | Project milestone: the reference catalogue loader (staging table, validation, upserts, report). |
| [`check_catalog.sql`](check_catalog.sql) | Project milestone: acceptance check that prints seller 6's catalogue. |

The data files are in [`../data/`](../data/).

---

<!-- toc:start -->
**Contents**

- [Exercise 1 ★: Cheap reads](#exercise-1--cheap-reads)
- [Exercise 2 ★: Where are our users?](#exercise-2--where-are-our-users)
- [Exercise 3 ★: Page 3 of the catalogue](#exercise-3--page-3-of-the-catalogue)
- [Exercise 4 ★★: The first quarter of 2025](#exercise-4--the-first-quarter-of-2025)
- [Exercise 5 ★★: How fast do we deliver?](#exercise-5--how-fast-do-we-deliver)
- [Exercise 6 ★★: A `NULL` audit](#exercise-6--a-null-audit)
- [Exercise 7 ★★: A price rise with nice endings](#exercise-7--a-price-rise-with-nice-endings)
- [Exercise 8 ★★: A stock delivery](#exercise-8--a-stock-delivery)
- [Exercise 9 ★★: URL slugs](#exercise-9--url-slugs)
- [Exercise 10 ★★★: Ship the paid orders](#exercise-10--ship-the-paid-orders)
- [Exercise 11 ★★★: Catalogue clean-up](#exercise-11--catalogue-clean-up)
- [Exercise 12 ★★★: Is every order's history consistent?](#exercise-12--is-every-orders-history-consistent)

<!-- toc:end -->

## Exercise 1 ★: Cheap reads

```sql
SELECT id, title, price
FROM products
WHERE seller_id = 2
  AND status = 'active'
  AND price < 15
ORDER BY price, title;
```

```text
 id |             title              | price 
----+--------------------------------+-------
 22 | Echoes of Andromeda            |  9.99
 26 | A Death in Dorset              | 10.00
 24 | Murder at Willow Lane          | 11.99
 27 | The Silent Witness             | 12.00
 20 | Children of the Red Dust       | 12.50
 25 | The Lighthouse Keeper's Secret | 13.49
 19 | The Long Orbit                 | 14.99
(7 rows)
```

**Reasoning.** Three conditions, all of which must hold, so `AND`. `status = 'active'` matters: product 28 (*Midnight on the Orient Line*, 15.00) is a draft, and product 23 (*The Clockwork Nebula*, 16.00) is archived. Neither is under 15 anyway, but a query that forgets the status filter is a classic way to show customers products they can't buy. The second sort key decides the order among equal prices. There are none here, but the requirement says so, and data changes.

---

## Exercise 2 ★: Where are our users?

```sql
SELECT id, full_name, lower(split_part(email, '@', 2)) AS domain
FROM users
WHERE lower(email) NOT LIKE '%.com'
ORDER BY domain, id;
```

```text
 id |    full_name     |    domain     
----+------------------+---------------
 25 | Amelia Fischer   | example.at
  5 | Chloé Tremblay   | example.ca
  2 | Benjamin Clarke  | example.co.uk
 13 | Olivia Brown     | example.co.uk
 20 | Oliver Smith     | example.co.uk
 26 | Daniel Novák     | example.cz
  4 | Lukas Becker     | example.de
 11 | Emma Schmidt     | example.de
 28 | Leo Jensen       | example.dk
  9 | Sofía Hernández  | example.es
 14 | Mateo García     | example.es
 16 | Lucas Martin     | example.fr
 23 | Charlotte Dubois | example.fr
 12 | Liam O'Connor    | example.ie
  3 | Giulia Rossi     | example.it
 19 | Mia Rossi        | example.it
  6 | Haruto Sato      | example.jp
 30 | Samuel Okafor    | example.ng
 29 | Nora Berg        | example.no
 21 | Isabella Silva   | example.pt
(20 rows)
```

**Reasoning.** User 17's address is `Zoe.Martin@Example.COM`. `LIKE` is case-sensitive, so a plain `email NOT LIKE '%.com'` lists her as non-`.com` and returns 21 rows. Lower-casing first (or using `NOT ILIKE '%.com'`) treats the address the way mail servers do: domain names are case-insensitive. `ORDER BY domain` uses the select-list alias, which is allowed because `ORDER BY` is evaluated after `SELECT`.

**Alternatives.** `WHERE split_part(lower(email), '@', 2) NOT LIKE '%.com'` tests only the domain part, which is stricter if a local part could end in `.com` before the `@`. A regular expression does the same in one go: `WHERE email !~* '\.com$'`. Module 08 makes emails case-insensitively unique.

---

## Exercise 3 ★: Page 3 of the catalogue

```sql
SELECT id, title, price
FROM products
WHERE status = 'active'
ORDER BY price, id
LIMIT 10 OFFSET 20;
```

```text
 id |               title               | price 
----+-----------------------------------+-------
 58 | Self-Watering Planter             | 29.00
 13 | KidSafe Volume-Limited Headphones | 29.99
 29 | Salt, Fat, Flame                  | 29.99
 45 | Knife Sharpening Stone 1000/6000  | 32.00
 54 | Kids' Rainbow Hoodie              | 34.00
 36 | Non-Stick Frying Pan 28cm         | 34.99
 43 | Bread Knife 23cm                  | 35.00
 35 | Stainless Steel Saucepan 2L       | 39.00
 50 | Linen Summer Shirt                | 39.00
 18 | Wireless Ergonomic Mouse          | 39.99
(10 rows)
```

**Reasoning.** Page `n` with page size `s` is `LIMIT s OFFSET (n - 1) * s`. The tie-breaker `id` makes the sort key unique. Without it, products with equal prices (13 and 29 at 29.99, 35 and 50 at 39.00) can come back in a different order on each request, and a product at a page boundary can show up on two pages or on none. Any unique column works as the last key; the primary key is the natural choice.

**Alternative.** `OFFSET 20 ROWS FETCH FIRST 10 ROWS ONLY` is the SQL-standard spelling. For deep pages, keyset pagination (module 12) replaces `OFFSET` with `WHERE (price, id) > (39.99, 18)`: "after the last row I showed".

---

## Exercise 4 ★★: The first quarter of 2025

```sql
SELECT id,
       created_at::date            AS day,
       to_char(created_at, 'Dy')   AS weekday,
       status,
       total
FROM orders
WHERE created_at >= '2025-01-01'
  AND created_at <  '2025-04-01'
  AND status <> 'cancelled'
ORDER BY created_at;
```

```text
 id |    day     | weekday |  status   | total  
----+------------+---------+-----------+--------
 68 | 2025-01-07 | Tue     | delivered | 996.00
 69 | 2025-01-09 | Thu     | refunded  | 379.00
 70 | 2025-01-27 | Mon     | delivered |  19.95
 71 | 2025-02-01 | Sat     | delivered | 129.00
 72 | 2025-02-03 | Mon     | delivered | 319.98
 74 | 2025-02-25 | Tue     | refunded  |  69.97
 75 | 2025-02-26 | Wed     | delivered |  38.98
 76 | 2025-02-27 | Thu     | delivered | 129.00
 77 | 2025-02-28 | Fri     | delivered |  33.98
 78 | 2025-03-02 | Sun     | delivered | 297.00
 79 | 2025-03-02 | Sun     | delivered |  54.00
 80 | 2025-03-06 | Thu     | delivered |  39.99
 83 | 2025-03-12 | Wed     | delivered |  32.00
 85 | 2025-03-20 | Thu     | delivered | 376.00
(14 rows)
```

Weekend orders, UTC and Berlin:

```sql
SELECT id, created_at
FROM orders
WHERE created_at >= '2025-01-01' AND created_at < '2025-04-01'
  AND status <> 'cancelled'
  AND extract(isodow FROM created_at) IN (6, 7)
ORDER BY id;
```

```text
 id |       created_at       
----+------------------------
 71 | 2025-02-01 04:48:13+00
 78 | 2025-03-02 04:34:58+00
 79 | 2025-03-02 23:02:45+00
(3 rows)
```

```sql
SELECT id,
       created_at AT TIME ZONE 'Europe/Berlin'                 AS berlin_time,
       to_char(created_at AT TIME ZONE 'Europe/Berlin', 'Dy')  AS berlin_day
FROM orders
WHERE created_at >= '2025-01-01' AND created_at < '2025-04-01'
  AND status <> 'cancelled'
  AND extract(isodow FROM created_at AT TIME ZONE 'Europe/Berlin') IN (6, 7)
ORDER BY id;
```

```text
 id |     berlin_time     | berlin_day 
----+---------------------+------------
 71 | 2025-02-01 05:48:13 | Sat
 78 | 2025-03-02 05:34:58 | Sun
(2 rows)
```

**Reasoning.** The half-open range `>= '2025-01-01' AND < '2025-04-01'` covers the quarter exactly, including orders late on March 31st. `status <> 'cancelled'` is safe here because `status` is `NOT NULL`. On a nullable column it would silently drop the `NULL` rows. Order 79 was placed at 23:02 UTC on Sunday, which was already 00:02 on **Monday** in Berlin (UTC+1 in winter). "Which day was it?" always depends on a time zone, and `extract` and `to_char` use the session's unless you convert first. The quarter boundaries themselves are also UTC here. A Berlin-based report would use `'2025-01-01 00:00 Europe/Berlin'` and so on.

**Alternative.** `extract(dow ...) IN (0, 6)` works too (Sunday is 0 in `dow`). `isodow` (Monday 1 to Sunday 7) avoids the split ends.

---

## Exercise 5 ★★: How fast do we deliver?

```sql
SELECT id,
       delivered_at - created_at                     AS delivery_time,
       extract(day FROM delivered_at - created_at)   AS full_days,
       CASE
           WHEN delivered_at - created_at <  interval '5 days' THEN 'fast'
           WHEN delivered_at - created_at <= interval '7 days' THEN 'normal'
           ELSE 'slow'
       END                                           AS speed
FROM orders
WHERE status = 'delivered'
  AND created_at >= '2025-01-01' AND created_at < '2026-01-01'
ORDER BY full_days DESC
FETCH FIRST 3 ROWS WITH TIES;
```

```text
 id  |  delivery_time  | full_days | speed 
-----+-----------------+-----------+-------
  94 | 9 days 13:32:00 |         9 | slow
  88 | 9 days 07:55:00 |         9 | slow
  93 | 8 days 04:27:00 |         8 | slow
  89 | 8 days 06:55:00 |         8 | slow
  96 | 8 days 12:03:00 |         8 | slow
 104 | 8 days 09:49:00 |         8 | slow
 103 | 8 days 08:42:00 |         8 | slow
(7 rows)
```

(The order of the rows *within* each `full_days` value may differ on your machine. Only the sort key is ordered.)

**Reasoning.**

- The `CASE` branches are tested in order, so the second branch only sees intervals of at least 5 days. "5 up to and including 7" needs only `<= interval '7 days'`. Comparing intervals with intervals keeps the units straight, with no conversion to hours.
- `extract(day FROM interval)` returns the *days field* of the interval. That's "full days" here, because a difference of two timestamps is normalised to days plus a time of day. It is **not** a total for intervals with months: `extract(day FROM interval '1 month 2 days')` is 2.
- `WITH TIES` compares on the `ORDER BY` key, `full_days`. The third row has 8 days, so all five 8-day orders are included. Adding `id` as a tie-breaker would change the question: the key `(full_days, id)` is unique, so there are no ties, and the result shrinks to 3 rows. With `WITH TIES`, the sort key *is* the definition of "tie", so don't add a tie-breaker.

---

## Exercise 6 ★★: A `NULL` audit

**1.** The naive query misses the products without a description:

```sql
SELECT count(*) FROM products WHERE description NOT ILIKE '%cotton%';                          -- 50
SELECT count(*) FROM products WHERE description NOT ILIKE '%cotton%' OR description IS NULL;   -- 58
```

`NULL NOT ILIKE '%cotton%'` is unknown, and `WHERE` drops unknown rows, so the 8 products without a description disappear. The colleague said they count as "doesn't mention cotton", so they have to be added back explicitly. `coalesce(description, '') NOT ILIKE '%cotton%'` gives the same 58 by turning `NULL` into a value that certainly doesn't mention cotton.

**2.** The same trap with `<>`:

```sql
SELECT count(*) FROM categories WHERE parent_id <> 2;                 -- 17
SELECT count(*) FROM categories WHERE parent_id IS DISTINCT FROM 2;   -- 21
```

The four root categories have `parent_id NULL`. They're certainly not children of Computers, but `NULL <> 2` is unknown. `IS DISTINCT FROM` treats `NULL` as a comparable value, so the answer is 23 − 2 = 21 (Laptops and Monitors are Computers' children).

**3.**

```sql
SELECT product_id, quantity, reserved,
       round(100.0 * reserved / nullif(quantity, 0), 1) AS pct_reserved
FROM inventory
ORDER BY pct_reserved DESC NULLS LAST, product_id
LIMIT 5;
```

```text
 product_id | quantity | reserved | pct_reserved 
------------+----------+----------+--------------
          3 |        2 |        2 |        100.0
         13 |        1 |        1 |        100.0
         17 |       14 |        2 |         14.3
         34 |       20 |        2 |         10.0
          4 |       40 |        3 |          7.5
(5 rows)
```

Two traps. Without `nullif`, the nine out-of-stock products cause `division by zero`. With `nullif` but without `NULLS LAST`, the descending sort puts their `NULL` shares **first** (`NULL` sorts as larger than any value), and the top five are five products with no stock at all:

```text
 product_id | quantity | reserved | pct_reserved 
------------+----------+----------+--------------
         23 |        0 |        0 |             
         25 |        0 |        0 |             
         14 |        0 |        0 |             
          5 |        0 |        0 |             
         28 |        0 |        0 |             
```

`100.0 *` (a `numeric` literal) makes the division exact. With `100 * reserved / quantity` it would be integer division, and 2 / 14 would show as 14, not 14.3. The `product_id` tie-breaker keeps products 3 and 13 in a stable order.

---

## Exercise 7 ★★: A price rise with nice endings

```sql
BEGIN;

UPDATE products
SET price      = ceil(price * 1.08) - 0.01,
    updated_at = '2025-07-01 00:00+00'
WHERE seller_id = 4
  AND category_id = 22
  AND status = 'active'
RETURNING id, title, price;
```

```text
 id |           title            | price 
----+----------------------------+-------
 47 | Organic Cotton Tee - White | 20.99
 48 | Organic Cotton Tee - Black | 20.99
 49 | Vintage Logo Tee           | 25.99
 50 | Linen Summer Shirt         | 42.99
 51 | Graphic Tee 'Ctrl+Alt+Del' | 23.99
(5 rows)

UPDATE 5
```

```sql
SELECT sum(price) FROM products WHERE seller_id = 4 AND category_id = 22;   -- 134.95
ROLLBACK;
```

**Reasoning.** `price * 1.08` has four decimals (19.00 × 1.08 = 20.5200). `ceil` rounds up to the next whole number (21), and subtracting 0.01 gives the `.99` ending just below it. The right-hand side of `SET` sees the *old* price, so the statement is safe even though it reads and writes the same column. `RETURNING` shows the new values, which is why you check them before deciding between `COMMIT` and `ROLLBACK`.

**The edge case.** If the raised price is already a whole number, `ceil` doesn't move it, and the formula goes *down* by a cent. A 25.00 shirt becomes 25.00 × 1.08 = 27.0000, and `ceil(27.0000) - 0.01 = 26.99`, less than an 8 % rise. Shifting by a cent before rounding fixes it:

```sql
SELECT x, ceil(x) - 0.01 AS naive, ceil(x + 0.01) - 0.01 AS fixed
FROM (VALUES (20.5200), (27.0000), (26.9900), (26.9950)) AS t(x);
```

```text
    x    | naive | fixed 
---------+-------+-------
 20.5200 | 20.99 | 20.99
 27.0000 | 26.99 | 27.99
 26.9900 | 26.99 | 26.99
 26.9950 | 26.99 | 27.99
(4 rows)
```

`ceil(x + 0.01) - 0.01` is the smallest `.99` price that's at least `x`. Rounding rules for money are business rules: write them down, test the edges, and keep the arithmetic in `numeric`.

---

## Exercise 8 ★★: A stock delivery

```sql
BEGIN;

INSERT INTO inventory (product_id, quantity, updated_at)
VALUES (5,  10,  '2025-07-01 08:00+00'),
       (14, 25,  '2025-07-01 08:00+00'),
       (25, 40,  '2025-07-01 08:00+00'),
       (62, 100, '2025-07-01 08:00+00')
ON CONFLICT (product_id) DO UPDATE
SET quantity   = inventory.quantity + EXCLUDED.quantity,
    updated_at = EXCLUDED.updated_at
RETURNING product_id, quantity, reserved;
```

```text
 product_id | quantity | reserved 
------------+----------+----------
          5 |       10 |        0
         14 |       25 |        0
         25 |       40 |        0
         62 |      600 |        2
(4 rows)

INSERT 0 4
```

`SELECT sum(quantity) FROM inventory` returns 1845 inside the transaction (1670 + 175). `ROLLBACK;`.

The "no row yet" case, in a fresh transaction:

```sql
BEGIN;
DELETE FROM inventory WHERE product_id = 14;
INSERT INTO inventory (product_id, quantity, updated_at)
VALUES (14, 25, '2025-07-01 08:00+00')
ON CONFLICT (product_id) DO UPDATE
SET quantity   = inventory.quantity + EXCLUDED.quantity,
    updated_at = EXCLUDED.updated_at
RETURNING product_id, quantity, reserved;
ROLLBACK;
```

```text
 product_id | quantity | reserved 
------------+----------+----------
         14 |       25 |        0
```

**Reasoning.** In `DO UPDATE`, `inventory.quantity` is the stored value and `EXCLUDED.quantity` is the value the `INSERT` proposed, so their sum is "add the delivery". When there's no conflict, the row is simply inserted with the delivered quantity, and `reserved` gets its default of 0. One statement covers both cases atomically, with no "check, then insert or update" race.

**Product 25 listed twice:**

```text
ERROR:  ON CONFLICT DO UPDATE command cannot affect row a second time
HINT:  Ensure that no rows proposed for insertion within the same command have duplicate constrained values.
```

PostgreSQL won't apply two updates to the same row in one statement. Combine the lines first: add them up by hand here, or with `GROUP BY` (module 05) when the delivery comes from a table.

**A product that doesn't exist** fails the foreign key: `insert or update on table "inventory" violates foreign key constraint "inventory_product_id_fkey"`, and the whole statement (all four lines) is rejected. That's the right outcome for stock: a delivery note with an unknown product needs a human, not a silent skip.

**Alternative.** Since every product already has an inventory row in the seed, a plain `UPDATE inventory SET quantity = quantity + 10 WHERE product_id = 5` per product would also work, but it would silently report `UPDATE 0` for a product without a row. The upsert states the intent ("ensure the row exists and add the units") in one statement.

---

## Exercise 9 ★★: URL slugs

```sql
SELECT id,
       title,
       trim(BOTH '-' FROM
            regexp_replace(
                lower(translate(title, 'àáâäãåçèéêëìíîïñòóôöõùúûüýÿ',
                                       'aaaaaaceeeeiiiinooooouuuuyy')),
                '[^a-z0-9]+', '-', 'g'))
       || '-' || id AS slug
FROM products
ORDER BY id;
```

Selected rows:

```text
 id |             title              |               slug                
----+--------------------------------+-----------------------------------
  6 | ClearView 24" 1080p Monitor    | clearview-24-1080p-monitor-6
 16 | Laptop Sleeve 13"              | laptop-sleeve-13-16
 25 | The Lighthouse Keeper's Secret | the-lighthouse-keeper-s-secret-25
 38 | Copper Sauté Pan               | copper-saute-pan-38
 51 | Graphic Tee 'Ctrl+Alt+Del'     | graphic-tee-ctrl-alt-del-51
 54 | Kids' Rainbow Hoodie           | kids-rainbow-hoodie-54
 59 | Hanging Macramé Planter        | hanging-macrame-planter-59
```

Wrapping the expression as a condition, `WHERE length(...) > 30`, counts **7** slugs longer than 30 characters. The longest is `portascreen-15-6-usb-c-portable-monitor-9`.

**Reasoning, step by step (inside out):**

1. `translate(title, from, to)` replaces each character of `from` with the character at the same position in `to`, so `é` becomes `e`. It must come first: once `é` has been replaced by `-`, the information is gone (`copper-saut-pan`).
2. `lower` comes before the regex, so the character class only needs `a-z`.
3. `regexp_replace(..., '[^a-z0-9]+', '-', 'g')` replaces each *run* of other characters (spaces, quotes, `+`, `"`, `/`) with a single dash. The `+` makes it a run, and `'g'` (global) replaces every run, not just the first.
4. `trim(BOTH '-' FROM ...)` removes dashes produced by a leading or trailing special character (`Laptop Sleeve 13"` would otherwise end in `-`).
5. Appending the id makes slugs unique even if two products had the same title, and lets the application look the product up by id.

**Alternatives.** The `unaccent` extension (module 13) handles every accented character instead of a hand-written list. Computing the slug in every query is wasteful. A generated column (module 02) could store it, but `translate` with a fixed list is immutable, while `unaccent` is not marked immutable, so that combination needs a wrapper. Module 13 deals with exactly this.

---

## Exercise 10 ★★★: Ship the paid orders

```sql
BEGIN;

UPDATE orders
SET status = 'shipped',
    shipped_at = '2025-07-01 09:00+00'
WHERE status = 'paid'
  AND created_at < '2025-06-28'
RETURNING id;
```

```text
 id  
-----
 110
 111
 112
 113
 114
(5 rows)

UPDATE 5
```

```sql
UPDATE inventory AS i
SET quantity   = i.quantity - oi.quantity,
    reserved   = i.reserved - oi.quantity,
    updated_at = '2025-07-01 09:00+00'
FROM order_items AS oi
WHERE oi.product_id = i.product_id
  AND oi.order_id IN (110, 111, 112, 113, 114)
RETURNING i.product_id, i.quantity, i.reserved;
```

```text
 product_id | quantity | reserved 
------------+----------+----------
         49 |       39 |        1
         47 |       79 |        0
         48 |       74 |        2
         54 |       14 |        0
         62 |      499 |        1
         13 |        0 |        0
          4 |       39 |        2
          3 |        0 |        0
(8 rows)

UPDATE 8
```

`SELECT sum(reserved) FROM inventory` returns 15 (it was 24). `ROLLBACK;`.

**Reasoning.**

- **Safe to run twice:** `WHERE status = 'paid'` is the precondition. A second run finds no paid orders before June 28th and reports `UPDATE 0`. Order 120 (paid on June 30th) is untouched.
- **Consuming stock** needs data from `order_items` (which product, how many) to update `inventory`. That's `UPDATE ... FROM`: for each inventory row, PostgreSQL finds the matching order lines, and `SET` can use their columns. Both counters go down by the line's quantity, so `available = quantity - reserved` doesn't change, which is correct: those units were already promised. The `inventory_reserved_within_quantity` check would stop any line that tried to consume more than was reserved.
- **The trap:** if a product appeared in two of the shipped orders, the `FROM` would produce two matching rows for one inventory row, and PostgreSQL would apply **only one** of them, silently, without an error. Here each product appears once (check with `SELECT order_id, product_id, quantity FROM order_items WHERE order_id BETWEEN 110 AND 114`), so the result is right. In general you'd first add up quantities per product (`GROUP BY`, module 05) and update from that. Module 04 discusses this behaviour of `UPDATE ... FROM` in detail.
- **One transaction** for both statements: an order marked shipped without its stock consumed (or the reverse) would leave the data inconsistent. Module 10 turns this into a proper function.

**Alternative.** Instead of copying the ids from `RETURNING`, the second statement can find the orders itself: `AND oi.order_id IN (SELECT id FROM orders WHERE status = 'shipped' AND shipped_at = '2025-07-01 09:00+00')`. That's more robust in a script that runs unattended. Module 06's data-modifying CTEs chain the two statements into one.

---

## Exercise 11 ★★★: Catalogue clean-up

```sql
BEGIN;

DELETE FROM products
WHERE (status = 'archived'
       OR (status = 'draft' AND created_at < timestamptz '2025-06-30' - interval '6 months'))
  AND id NOT IN (SELECT product_id FROM order_items)
RETURNING id, title, status, created_at;
```

```text
 id |          title          |  status  |       created_at       
----+-------------------------+----------+------------------------
 59 | Hanging Macramé Planter | draft    | 2023-06-05 12:00:00+00
 61 | Ceramic Herb Pot Trio   | archived | 2023-06-05 12:00:00+00
(2 rows)

DELETE 2
```

`SELECT count(*) FROM products` returns 60, and so does `SELECT count(*) FROM inventory`: the cascade removed the two stock rows. `ROLLBACK;`.

**Reasoning.**

- **Precedence.** Without the outer parentheses, `a OR b AND c` means `a OR (b AND c)`: *every* archived product qualifies regardless of orders. The `DELETE` then tries to remove product 5, which was ordered, and the whole statement fails:

  ```text
  ERROR:  update or delete on table "products" violates foreign key constraint "order_items_product_id_fkey" on table "order_items"
  DETAIL:  Key (id)=(5) is still referenced from table "order_items".
  ```

  The foreign key saved the day, but don't rely on that. The parentheses state the rule: (archived OR old draft) AND never ordered.
- **Six months.** `timestamptz '2025-06-30' - interval '6 months'` is 2024-12-30. The drafts are 14 (2025-06-02), 28 (2025-05-30), 55 (2025-06-10) and 59 (2023-06-05), so only 59 is old enough.
- **Why `NOT IN` is safe here.** The `NOT IN` trap needs a `NULL` in the list. `order_items.product_id` is `NOT NULL` (it's part of the primary key), so the subquery can never return a `NULL`. Module 04's `NOT EXISTS` works regardless, and is the habit to build.
- Products 5, 23 and 38 are archived but stay, because they're part of order history.

---

## Exercise 12 ★★★: Is every order's history consistent?

```sql
SELECT id, status, created_at, paid_at, shipped_at, delivered_at
FROM orders
WHERE NOT CASE status
              WHEN 'pending'   THEN paid_at IS NULL AND shipped_at IS NULL AND delivered_at IS NULL
              WHEN 'paid'      THEN paid_at IS NOT NULL AND shipped_at IS NULL AND delivered_at IS NULL
              WHEN 'shipped'   THEN paid_at IS NOT NULL AND shipped_at IS NOT NULL AND delivered_at IS NULL
              WHEN 'delivered' THEN paid_at IS NOT NULL AND shipped_at IS NOT NULL AND delivered_at IS NOT NULL
              WHEN 'cancelled' THEN shipped_at IS NULL AND delivered_at IS NULL
              WHEN 'refunded'  THEN paid_at IS NOT NULL AND (delivered_at IS NULL OR shipped_at IS NOT NULL)
              ELSE false
          END
   OR paid_at < created_at
   OR shipped_at < paid_at
   OR delivered_at < shipped_at
ORDER BY id;
```

On the clean data: `(0 rows)`. With the three deliberate errors:

```sql
BEGIN;
UPDATE orders SET delivered_at = NULL WHERE id = 1;
UPDATE orders SET shipped_at = paid_at - interval '1 hour' WHERE id = 2;
UPDATE orders SET status = 'paid' WHERE id = 115;
-- ... the query again ...
ROLLBACK;
```

```text
 id  |  status   |       created_at       |        paid_at         |       shipped_at       |      delivered_at      
-----+-----------+------------------------+------------------------+------------------------+------------------------
   1 | delivered | 2024-01-12 10:39:36+00 | 2024-01-12 13:37:36+00 | 2024-01-15 20:37:36+00 | 
   2 | delivered | 2024-01-13 05:07:14+00 | 2024-01-13 06:53:14+00 | 2024-01-13 05:53:14+00 | 2024-01-20 10:53:14+00
 115 | paid      | 2025-06-28 08:34:48+00 |                        |                        | 
(3 rows)
```

**Reasoning.**

- **The `CASE` returns a boolean.** Each branch is the rule for one status, expressed only with `IS NULL`/`IS NOT NULL`, which are never unknown. So the `CASE` is always true or false, and `NOT` gives the violations. `ELSE false` flags any status the table doesn't know, although the `CHECK` constraint makes that impossible.
- **The ordering rules** use plain `<`, which is unknown when either side is `NULL` (a pending order has no `paid_at`). An unknown `OR`-ed with false is unknown, and `WHERE` drops it, so missing timestamps don't cause false alarms. That's three-valued logic working *for* you. A missing timestamp that *should* be there is caught by the `CASE` instead.
- **Refunded orders** are the interesting rule. The data has two refunds that happened before shipping (orders 69 and 74, with `paid_at` only) and two after delivery. A first draft that treated `refunded` like `delivered` flags 69 and 74. That's a typical outcome of a consistency check: you learn a business rule that nobody wrote down.

**Alternatives.** Each rule could be a separate query with its own label, which is easier to read in a report and tells you *which* rule failed. That's a good fit for a monitoring job. Several of these rules could also become `CHECK` constraints on `orders`, since they only involve one row. The `CASE` above, written positively, would be a valid `CHECK` expression. Whether to enforce them in the schema is a design decision for module 07. Module 09 adds triggers that keep timestamps in step with status changes.
