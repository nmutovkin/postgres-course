# Module 04: Solutions

Every solution was run on PostgreSQL 17 against `./project/bin/reset.sh 04` (the small dataset). Outputs show `NULL` as `∅`. Exercises 9 and 12 change data inside a transaction that ends with `ROLLBACK`, so the database stays as NOTES.md describes it.

Files in this directory:

| File | What |
|---|---|
| [`queries.sql`](queries.sql) | Project milestone: the saved business questions, with their expected answers. |

---

## Exercise 1 ★: Customers to win back

```sql
SELECT u.id,
       u.full_name,
       u.created_at::date AS signed_up,
       DATE '2025-06-30' - u.created_at::date AS days_since_signup
FROM users AS u
WHERE u.is_active
  AND NOT EXISTS (SELECT 1 FROM orders AS o WHERE o.user_id = u.id)
ORDER BY days_since_signup DESC;
```

```text
 id |   full_name    | signed_up  | days_since_signup 
----+----------------+------------+-------------------
  6 | Haruto Sato    | 2023-03-06 |               847
  7 | Priya Patel    | 2023-07-05 |               726
 15 | Ava Nguyen     | 2023-12-20 |               558
 16 | Lucas Martin   | 2024-01-10 |               537
 21 | Isabella Silva | 2024-04-24 |               432
 24 | Henry Wilson   | 2024-06-26 |               369
 29 | Nora Berg      | 2024-10-09 |               264
 30 | Samuel Okafor  | 2025-06-25 |                 5
(8 rows)
```

**Reasoning.** "Never ordered" is an anti-join, so `NOT EXISTS` states it directly. Subtracting two `date`s gives an `integer` number of days (module 03), and the fixed date `2025-06-30` is "today" in the small dataset, so the answer doesn't depend on when you run it.

**Alternatives.** `LEFT JOIN orders o ON o.user_id = u.id WHERE o.id IS NULL` returns the same eight rows. `WHERE u.id NOT IN (SELECT user_id FROM orders)` happens to work too, because `orders.user_id` is `NOT NULL`, but it's a habit worth not forming (section 9.4). Leo Jensen (28) also never ordered, but he's inactive, so he's excluded. Without the `is_active` filter you'd get the nine users of NOTES.md.

---

## Exercise 2 ★: The unsold catalog

```sql
SELECT p.id,
       p.title,
       p.status,
       s.shop_name,
       c.name AS category,
       inv.quantity - inv.reserved AS available
FROM products AS p
JOIN sellers    AS s   ON s.id = p.seller_id
JOIN categories AS c   ON c.id = p.category_id
JOIN inventory  AS inv ON inv.product_id = p.id
WHERE NOT EXISTS (SELECT 1 FROM order_items AS i WHERE i.product_id = p.id)
ORDER BY p.id;
```

```text
 id |            title             |  status  |    shop_name    |  category  | available 
----+------------------------------+----------+-----------------+------------+-----------
 14 | BassBoom Over-Ear Headphones | draft    | TechTrove       | Headphones |         0
 15 | Titan G15 Gaming Laptop      | active   | TechTrove       | Laptops    |         4
 28 | Midnight on the Orient Line  | draft    | Bookworm Corner | Mystery    |         0
 55 | Fleece-Lined Winter Hoodie   | draft    | Threadbare Co.  | Hoodies    |         0
 57 | Terracotta Planter 20cm      | active   | Maple & Pine    | Planters   |        30
 58 | Self-Watering Planter        | active   | Maple & Pine    | Planters   |        12
 59 | Hanging Macramé Planter      | draft    | Maple & Pine    | Planters   |         0
 60 | Cedar Raised Garden Bed      | active   | Maple & Pine    | Garden     |         3
 61 | Ceramic Herb Pot Trio        | archived | Maple & Pine    | Planters   |         0
(9 rows)
```

**Reasoning.** The three joins all follow many-to-one edges (each product has one seller, one category and one inventory row), so they can't change the row count: still the nine never-ordered products. The anti-join sits in `WHERE`, independent of the joins. The products that are active *and* have stock to sell are 15, 57, 58 and 60: one laptop nobody wants, and three items from Maple & Pine, which has never made a sale.

**Alternative.** `LEFT JOIN order_items i ON i.product_id = p.id ... WHERE i.order_id IS NULL`. It works, but here it would be the only outer join among inner joins, which makes the query harder to read. `NOT EXISTS` keeps the "what to show" (joins) separate from the "which rows" (filter).

---

## Exercise 3 ★: Orders at an old price

```sql
SELECT o.id AS order_id,
       o.created_at::date AS ordered,
       p.id AS product_id,
       p.title,
       i.unit_price,
       p.price AS current_price,
       i.unit_price - p.price AS difference
FROM orders AS o
JOIN order_items AS i ON i.order_id = o.id
JOIN products    AS p ON p.id = i.product_id
WHERE i.unit_price <> p.price
ORDER BY o.id, p.id;
```

```text
 order_id |  ordered   | product_id |           title           | unit_price | current_price | difference 
----------+------------+------------+---------------------------+------------+---------------+------------
        2 | 2024-01-13 |          1 | AeroBook 13 Laptop        |    1099.00 |        999.00 |     100.00
        3 | 2024-01-19 |          7 | ClearView 27" 4K Monitor  |     399.00 |        379.00 |      20.00
        6 | 2024-02-01 |         34 | Cast Iron Skillet 26cm    |      39.00 |         45.00 |      -6.00
        6 | 2024-02-01 |         37 | Dutch Oven 5.5L Enamel    |      99.00 |        119.00 |     -20.00
       10 | 2024-02-27 |         52 | Classic Zip Hoodie - Grey |      45.00 |         49.00 |      -4.00
       11 | 2024-03-01 |         19 | The Long Orbit            |      12.99 |         14.99 |      -2.00
       12 | 2024-03-03 |          7 | ClearView 27" 4K Monitor  |     399.00 |        379.00 |      20.00
       14 | 2024-03-18 |         29 | Salt, Fat, Flame          |      32.99 |         29.99 |       3.00
       18 | 2024-04-04 |         37 | Dutch Oven 5.5L Enamel    |      99.00 |        119.00 |     -20.00
       22 | 2024-04-26 |         34 | Cast Iron Skillet 26cm    |      39.00 |         45.00 |      -6.00
       22 | 2024-04-26 |         37 | Dutch Oven 5.5L Enamel    |      99.00 |        119.00 |     -20.00
       23 | 2024-04-26 |         29 | Salt, Fat, Flame          |      32.99 |         29.99 |       3.00
       26 | 2024-05-07 |         10 | QuietWave ANC Headphones  |     249.00 |        229.00 |      20.00
       28 | 2024-05-28 |         19 | The Long Orbit            |      12.99 |         14.99 |      -2.00
       28 | 2024-05-28 |         29 | Salt, Fat, Flame          |      32.99 |         29.99 |       3.00
       31 | 2024-06-09 |         19 | The Long Orbit            |      12.99 |         14.99 |      -2.00
       37 | 2024-07-11 |         37 | Dutch Oven 5.5L Enamel    |     109.00 |        119.00 |     -10.00
       39 | 2024-07-23 |         19 | The Long Orbit            |      12.99 |         14.99 |      -2.00
       40 | 2024-08-06 |          1 | AeroBook 13 Laptop        |    1099.00 |        999.00 |     100.00
       40 | 2024-08-06 |          7 | ClearView 27" 4K Monitor  |     399.00 |        379.00 |      20.00
       40 | 2024-08-06 |         10 | QuietWave ANC Headphones  |     249.00 |        229.00 |      20.00
       46 | 2024-09-17 |         52 | Classic Zip Hoodie - Grey |      45.00 |         49.00 |      -4.00
       53 | 2024-10-18 |         29 | Salt, Fat, Flame          |      32.99 |         29.99 |       3.00
       55 | 2024-10-28 |         52 | Classic Zip Hoodie - Grey |      45.00 |         49.00 |      -4.00
       68 | 2025-01-07 |         10 | QuietWave ANC Headphones  |     249.00 |        229.00 |      20.00
(25 rows)
```

Distinct orders in that result:

```sql
SELECT count(*) AS lines, count(DISTINCT i.order_id) AS orders
FROM order_items AS i
JOIN products AS p ON p.id = i.product_id
WHERE i.unit_price <> p.price;
```

```text
 lines | orders 
-------+--------
    25 |     20
(1 row)
```

**Reasoning.** `unit_price` is frozen at checkout and `products.price` is today's price, so a join on the product and a comparison of the two columns finds every line sold at a different price. 25 lines in 20 orders, matching NOTES.md. Order 40 alone has three such lines (laptop, monitor and headphones were all more expensive in August 2024). Product 37 (the Dutch Oven) shows two different old prices, 99.00 and 109.00, because it changed price twice.

`<>` is safe here because both columns are `NOT NULL`. If either could be `NULL`, `i.unit_price <> p.price` would be `NULL` for those rows and they'd be silently excluded. `i.unit_price IS DISTINCT FROM p.price` (module 03) treats `NULL` as a comparable value and is the robust choice for nullable columns.

---

## Exercise 4 ★★: Shops without a sale

```sql
SELECT s.id,
       s.shop_name,
       s.country_code,
       EXISTS (SELECT 1 FROM products AS p WHERE p.seller_id = s.id) AS has_products
FROM sellers AS s
WHERE NOT EXISTS (
        SELECT 1
        FROM orders AS o
        WHERE o.seller_id = s.id
          AND o.status IN ('paid', 'shipped', 'delivered')
      )
ORDER BY s.id;
```

```text
 id |   shop_name    | country_code | has_products 
----+----------------+--------------+--------------
  5 | Maple & Pine   | CA           | t
  6 | Sakura Gadgets | JP           | f
(2 rows)
```

**Reasoning.** A "sale" is an order with money in it: paid, shipped or delivered. The status filter belongs *inside* the `NOT EXISTS`, because the question is "is there an order of this seller that is a sale?". Putting it outside makes no sense, since `s` has no status column. The `EXISTS` in the `SELECT` list is a boolean expression like any other, evaluated per seller. It distinguishes the two cases from NOTES.md: Maple & Pine has products but no sales, Sakura Gadgets has nothing at all.

**A common wrong answer** is `sellers LEFT JOIN orders o ON o.seller_id = s.id WHERE o.status NOT IN ('paid', 'shipped', 'delivered') OR o.id IS NULL`. It returns every seller that has *at least one* non-sale order (a cancelled one, say), so TechTrove shows up. The anti-join must ask about *all* of a seller's orders at once, which is what `NOT EXISTS` does. If you want the outer-join form, put the status filter in `ON` and test `WHERE o.id IS NULL`.

---

## Exercise 5 ★★: Empty corners of the catalog

```sql
SELECT c.id, c.name
FROM categories AS c
WHERE NOT EXISTS (
        SELECT 1
        FROM products AS p
        JOIN categories AS pc       ON pc.id = p.category_id      -- the product's category
        LEFT JOIN categories AS pp  ON pp.id = pc.parent_id       -- its parent
        WHERE c.id IN (pc.id, pp.id, pp.parent_id)                -- c is the category, parent or grandparent
      )
ORDER BY c.id;
```

```text
 id |   name   
----+----------
  7 | Speakers
 20 | History
(2 rows)
```

Compare with "no product directly in the category":

```sql
SELECT c.id, c.name
FROM categories AS c
WHERE NOT EXISTS (SELECT 1 FROM products AS p WHERE p.category_id = c.id)
ORDER BY c.id;
```

```text
 id |     name      
----+---------------
  1 | Electronics
  5 | Audio
  7 | Speakers
  8 | Home & Garden
 14 | Books
 18 | Non-Fiction
 20 | History
(7 rows)
```

**Reasoning.** A product "belongs to" its own category and to every ancestor of it. With a tree of known depth (three levels), the ancestors are at most the parent and the grandparent, which two self joins reach. So for each product we compute its category, its parent and its grandparent, and a category is non-empty if it appears in any of those three positions for some product. `c.id IN (pc.id, pp.id, pp.parent_id)` is a literal list with possible `NULL`s, which is fine for `IN`: a `NULL` never matches, it doesn't spoil the other comparisons (it's `NOT IN` that breaks).

The parent join must be a `LEFT JOIN`: product 62 sits directly in the root Clothing, whose `parent_id` is `NULL`, and an inner join would drop product 62 from the subquery. In this dataset you wouldn't notice, because Clothing still counts as non-empty thanks to its T-shirts and hoodies. The bug would only show on a root whose *only* products sit directly in it, which is exactly the kind of bug that passes testing.

Only Speakers (7) and History (20) are truly empty. Roots like Electronics (1) and mid-level categories like Audio (5) have no products of their own, but their subtrees do.

**Alternative.** Walk the tree downwards instead: a category is empty if it has no products, its children have no products, and its grandchildren have no products, which is three `NOT EXISTS` subqueries. That's longer but some find it easier to read. For trees of unknown depth, module 06's recursive CTEs replace both.

---

## Exercise 6 ★★: Loyal to Europe

```sql
SELECT u.id, u.full_name
FROM users AS u
WHERE NOT EXISTS (
        SELECT 1
        FROM sellers AS s
        WHERE s.country_code IN ('GB', 'IT', 'DE')
          AND NOT EXISTS (
                SELECT 1
                FROM orders AS o
                WHERE o.user_id   = u.id
                  AND o.seller_id = s.id
                  AND o.status   <> 'cancelled'
              )
      )
ORDER BY u.id;
```

```text
 id |   full_name   
----+---------------
  8 | Marcus Lee
 12 | Liam O'Connor
 13 | Olivia Brown
 20 | Oliver Smith
 26 | Daniel Novák
(5 rows)
```

**Reasoning.** This is **relational division**: "users related to *all* items of a set". SQL has no "for all" operator, so it's expressed as a double negation: a user qualifies if there is **no** European seller from whom they have **not** bought. Read the query inside out: the inner `NOT EXISTS` is true for a seller the user never bought from; the outer `NOT EXISTS` requires that no such seller exists.

The `status <> 'cancelled'` filter matters. Mateo García (14) has orders from Casa Cucina and Threadbare Co., and his only Bookworm Corner order (64) was cancelled. Without the filter he'd qualify, and you'd get six users.

The double negation has a property worth knowing: if the set of European sellers were empty, *every* user would qualify ("bought from all zero sellers"), just like `ALL` over an empty set (section 11). If that matters, check the set separately.

**Alternative.** Counting: for each user, count the distinct European sellers they bought from and compare with the number of European sellers. That needs `GROUP BY` and `HAVING` (module 05):

```sql
SELECT o.user_id
FROM orders AS o
JOIN sellers AS s ON s.id = o.seller_id
WHERE s.country_code IN ('GB', 'IT', 'DE') AND o.status <> 'cancelled'
GROUP BY o.user_id
HAVING count(DISTINCT o.seller_id) = (SELECT count(*) FROM sellers WHERE country_code IN ('GB', 'IT', 'DE'))
ORDER BY o.user_id;
```

```text
 user_id 
---------
       8
      12
      13
      20
      26
(5 rows)
```

Both are common. The counting version is often faster on big tables. The double-negation version reads closer to the logic and doesn't need aggregates.

---

## Exercise 7 ★★: Each shop's biggest orders

```sql
SELECT s.id AS seller_id,
       s.shop_name,
       big.id AS order_id,
       big.total,
       big.status
FROM sellers AS s
LEFT JOIN LATERAL (
    SELECT o.id, o.total, o.status
    FROM orders AS o
    WHERE o.seller_id = s.id
      AND o.status <> 'cancelled'
    ORDER BY o.total DESC, o.id
    LIMIT 2
) AS big ON true
ORDER BY s.id, big.total DESC;
```

```text
 seller_id |    shop_name    | order_id |  total  |  status   
-----------+-----------------+----------+---------+-----------
         1 | TechTrove       |       40 | 8940.00 | delivered
         1 | TechTrove       |      114 | 4399.98 | paid
         2 | Bookworm Corner |       29 |  237.00 | delivered
         2 | Bookworm Corner |       93 |  119.48 | delivered
         3 | Casa Cucina     |       34 |  521.00 | delivered
         3 | Casa Cucina     |       30 |  412.99 | delivered
         4 | Threadbare Co.  |       89 |  401.00 | delivered
         4 | Threadbare Co.  |       85 |  376.00 | delivered
         5 | Maple & Pine    |        ∅ |       ∅ | ∅
         6 | Sakura Gadgets  |        ∅ |       ∅ | ∅
(10 rows)
```

**Reasoning.** "Top N per parent" is the textbook `LATERAL` case: the subquery is re-run per seller, sorted by total, and cut at two. `LEFT JOIN LATERAL ... ON true` keeps the two sellers whose subquery returns nothing. The tie-breaker `o.id` makes the result deterministic if two orders have the same total.

The `status <> 'cancelled'` filter matters for Bookworm Corner. Its cancelled order 53 (123.98) is larger than order 93 (119.48), so without the filter a cancelled order would take second place.

**Alternatives.** Window functions (`row_number() OVER (PARTITION BY seller_id ORDER BY total DESC)`, module 06) solve the same problem by ranking all orders and keeping ranks 1 and 2. The lateral version reads only two rows per seller when an index on `orders (seller_id, total)` exists, which matters with many orders per seller.

---

## Exercise 8 ★★: TechTrove's lapsed customers

```sql
SELECT user_id FROM orders
WHERE seller_id = 1 AND created_at <  '2025-01-01' AND status <> 'cancelled'
EXCEPT
SELECT user_id FROM orders
WHERE seller_id = 1 AND created_at >= '2025-01-01' AND status <> 'cancelled'
ORDER BY user_id;
```

```text
 user_id 
---------
       2
       3
      14
      17
      22
(5 rows)
```

Adding names needs a join, which you can wrap around the set operation as a derived table:

```sql
SELECT u.id, u.full_name, u.email
FROM (
    SELECT user_id FROM orders
    WHERE seller_id = 1 AND created_at <  '2025-01-01' AND status <> 'cancelled'
    EXCEPT
    SELECT user_id FROM orders
    WHERE seller_id = 1 AND created_at >= '2025-01-01' AND status <> 'cancelled'
) AS lapsed
JOIN users AS u ON u.id = lapsed.user_id
ORDER BY u.id;
```

```text
 id |    full_name    |           email            
----+-----------------+----------------------------
  2 | Benjamin Clarke | ben.clarke@example.co.uk
  3 | Giulia Rossi    | giulia.rossi@example.it
 14 | Mateo García    | mateo.garcia@example.es
 17 | Zoë Martin      | Zoe.Martin@Example.COM
 22 | James Anderson  | james.anderson@example.com
(5 rows)
```

**Reasoning.** `EXCEPT` is literally "in the first set but not in the second". It also removes duplicates (a user with five 2024 orders appears once), which is what a mailing list wants. Note that Sofía Hernández (9) isn't here: her only order was from Casa Cucina.

**Alternative.** `SELECT ... FROM users u WHERE EXISTS (2024 order) AND NOT EXISTS (2025 order)`, with both subqueries correlated on `u.id` and `seller_id = 1`. It's equivalent, and convenient when you need more user columns, since you start from `users` directly.

---

## Exercise 9 ★★: Retiring the T-shirt line

```sql
BEGIN;

UPDATE products AS p
SET status = 'archived', updated_at = now()
FROM categories AS c
WHERE c.id = p.category_id
  AND c.slug = 't-shirts'
  AND p.status = 'active'
  AND NOT EXISTS (
        SELECT 1
        FROM order_items AS i
        JOIN orders AS o ON o.id = i.order_id
        WHERE i.product_id = p.id
          AND o.status IN ('pending', 'paid')
      )
RETURNING p.id, p.title, c.name AS category;

ROLLBACK;
```

```text
BEGIN
 id |           title            | category 
----+----------------------------+----------
 50 | Linen Summer Shirt         | T-Shirts
 51 | Graphic Tee 'Ctrl+Alt+Del' | T-Shirts
(2 rows)

UPDATE 2
ROLLBACK
```

**Reasoning.** `UPDATE ... FROM categories` brings the category's slug into the decision, and `RETURNING` can show columns from the joined table. Each product joins to exactly one category, so the "at most one source row per target row" rule from section 14 holds. The `NOT EXISTS` protects products that are still in open orders: 47 and 48 are in order 111, 49 in orders 110 and 115, and 48 again in order 119. Only the linen shirt (50) and the graphic tee (51) can be archived.

**Alternative.** `WHERE p.category_id = (SELECT id FROM categories WHERE slug = 't-shirts')` with a scalar subquery instead of `FROM`. It's equally correct (the slug is unique, so the subquery returns one row) and portable to other databases. The `FROM` form becomes necessary when you need a value from the other table in `SET` or `RETURNING`.

---

## Exercise 10 ★★: Repeat purchases

```sql
SELECT o1.user_id,
       i1.product_id,
       o1.id AS first_order,
       o2.id AS later_order
FROM order_items AS i1
JOIN orders      AS o1 ON o1.id = i1.order_id
JOIN order_items AS i2 ON i2.product_id = i1.product_id
JOIN orders      AS o2 ON o2.id = i2.order_id
WHERE o2.user_id = o1.user_id
  AND o1.id < o2.id
  AND o1.status <> 'cancelled'
  AND o2.status <> 'cancelled'
ORDER BY o1.user_id, i1.product_id, o1.id, o2.id;
```

```text
 user_id | product_id | first_order | later_order 
---------+------------+-------------+-------------
       8 |         17 |          19 |          71
       8 |         19 |          39 |          86
       8 |         20 |          39 |          59
       8 |         22 |          51 |         109
       8 |         23 |           7 |          39
       8 |         24 |           9 |          86
       8 |         30 |          39 |          51
       8 |         31 |           9 |          59
       8 |         33 |          54 |          86
       8 |         43 |           4 |          63
       8 |         44 |           4 |          63
      10 |         17 |          76 |         116
...
(29 rows)
```

**Reasoning.** Two copies of "an order line with its order", joined on *same product* and *same user*. `o1.id < o2.id` does two jobs: it stops a line from pairing with itself, and it keeps only one ordering of each pair (otherwise every pair would appear twice, as (A, B) and (B, A)). A product bought three times gives three pairs: (1st, 2nd), (1st, 3rd), (2nd, 3rd). Olivia Brown (13) bought the AeroBook 15 Pro (product 2) in orders 27, 40 and 106.

A row count to check against (29 pairs for 27 user/product combinations: Olivia's three AeroBook purchases account for the two extra pairs):

```sql
SELECT count(*) AS pairs, count(DISTINCT (o1.user_id, i1.product_id)) AS user_product_combinations
FROM order_items AS i1
JOIN orders      AS o1 ON o1.id = i1.order_id
JOIN order_items AS i2 ON i2.product_id = i1.product_id
JOIN orders      AS o2 ON o2.id = i2.order_id
WHERE o2.user_id = o1.user_id
  AND o1.id < o2.id
  AND o1.status <> 'cancelled'
  AND o2.status <> 'cancelled';
```

```text
 pairs | user_product_combinations 
-------+---------------------------
    29 |                        27
(1 row)
```

**Alternative.** If you only need "which user bought which product more than once" (not the pairs), `GROUP BY user_id, product_id HAVING count(*) > 1` in module 05 is shorter. The self join is the tool when you need to put two specific rows side by side, for instance to compute the time between the purchases.

---

## Exercise 11 ★★★: Cookware-only orders

```sql
SELECT o.id, o.status, o.total
FROM orders AS o
WHERE EXISTS (SELECT 1 FROM order_items AS i WHERE i.order_id = o.id)
  AND NOT EXISTS (
        SELECT 1
        FROM order_items AS i
        JOIN products AS p ON p.id = i.product_id
        WHERE i.order_id = o.id
          AND p.category_id <> 10
      )
ORDER BY o.id;
```

```text
 id  |  status   | total  
-----+-----------+--------
  18 | refunded  | 232.99
  30 | delivered | 412.99
  47 | delivered |  34.99
  61 | delivered |  34.99
  79 | delivered |  54.00
  94 | delivered |  34.99
  98 | delivered |  88.99
 108 | delivered | 108.00
 120 | paid      |  90.00
(9 rows)
```

**Reasoning.** "Every line is cookware" is a universal statement, and like exercise 6 it becomes a negation: "there is no line that is *not* cookware". The `EXISTS` guards against the empty case. An order with no lines at all would satisfy "no non-cookware line" vacuously. Bazaar has no empty orders (the dataset guarantees at least one line per order), so the guard doesn't change today's answer, but an order whose lines were deleted would otherwise be reported as cookware-only.

Order 30, for instance, has the frying pan (36) and two Copper Sauté Pans (38), both in Cookware, whereas order 6 (skillets, saucepan, Dutch oven and a bread knife) has a Cutlery line and doesn't qualify.

If `products.category_id` were nullable, `p.category_id <> 10` would be `NULL` for a product without a category, the line wouldn't count as "not cookware", and an order containing it would pass. `p.category_id IS DISTINCT FROM 10` closes that gap. Here the column is `NOT NULL`.

**Alternative.** `= ALL`: `WHERE 10 = ALL (SELECT p.category_id FROM order_items i JOIN products p ON ... WHERE i.order_id = o.id)`. It's a nice direct translation of "all lines are cookware", with the same empty-set behavior (true for an order with no lines).

---

## Exercise 12 ★★★: Debug the June report

The original query has two bugs:

1. The conditions `o.status = 'delivered'` and `o.created_at >= '2025-06-01'` are in `WHERE`. For sellers without a matching order, the `LEFT JOIN` produces a padded row with `o.status` `NULL`, and `WHERE` throws it away (section 5.3).
2. `JOIN users u ON u.id = o.user_id` is an inner join through the outer-joined `orders`. Even after fixing bug 1, the padded rows have `o.user_id` `NULL`, and the inner join drops them (section 5.4).

The fix moves the order conditions into `ON` and makes the users join outer:

```sql
SELECT s.shop_name, o.id AS order_id, o.created_at::date AS ordered, u.full_name AS buyer
FROM sellers AS s
LEFT JOIN orders AS o
       ON o.seller_id = s.id
      AND o.status = 'delivered'
      AND o.created_at >= '2025-06-01'
LEFT JOIN users AS u ON u.id = o.user_id
ORDER BY s.id, o.id;
```

```text
    shop_name    | order_id |  ordered   |     buyer      
-----------------+----------+------------+----------------
 TechTrove       |       99 | 2025-06-01 | Marcus Lee
 TechTrove       |      102 | 2025-06-18 | Liam O'Connor
 TechTrove       |      103 | 2025-06-18 | Amelia Fischer
 TechTrove       |      104 | 2025-06-18 | Olivia Brown
 Bookworm Corner |      101 | 2025-06-12 | Daniel Novák
 Casa Cucina     |      108 | 2025-06-22 | Chloé Tremblay
 Threadbare Co.  |      100 | 2025-06-05 | Harper Johnson
 Maple & Pine    |        ∅ | ∅          | ∅
 Sakura Gadgets  |        ∅ | ∅          | ∅
(9 rows)
```

**How to find the bugs systematically** (section 15.3): count the rows of the original (7), then count what the grain says you need ("every seller at least once" means at least 6 rows, and 4 shops had June deliveries while 2 didn't, so 7 + 2 = 9). Then remove the `WHERE` clause: the padded rows still don't appear, which exposes the second bug. Fixing only one of the two bugs gives the original 7 rows again, which is why this kind of bug survives casual testing.

**Alternative.** Parenthesize the inner part: `sellers s LEFT JOIN (orders o JOIN users u ON u.id = o.user_id) ON o.seller_id = s.id AND ...`. It says "an order together with its buyer is the optional thing", which is arguably closer to the intent.

---

## Exercise 13 ★★★: Prices only ever went up

The tempting query:

```sql
SELECT p.id, p.title, p.price
FROM products AS p
WHERE p.price > ALL (SELECT i.unit_price FROM order_items AS i WHERE i.product_id = p.id)
ORDER BY p.id;
```

```text
 id |            title             |  price  
----+------------------------------+---------
 14 | BassBoom Over-Ear Headphones |   89.00
 15 | Titan G15 Gaming Laptop      | 1799.00
 28 | Midnight on the Orient Line  |   15.00
 52 | Classic Zip Hoodie - Grey    |   49.00
 55 | Fleece-Lined Winter Hoodie   |   69.00
 57 | Terracotta Planter 20cm      |   18.00
 58 | Self-Watering Planter        |   29.00
 59 | Hanging Macramé Planter      |   22.00
 60 | Cedar Raised Garden Bed      |  149.00
 61 | Ceramic Herb Pot Trio        |   35.00
(10 rows)
```

Ten rows, nine of which are the never-ordered products: for them the subquery is empty, and `> ALL` over an empty set is true (section 11). The fix requires at least one sale:

```sql
SELECT p.id, p.title, p.price,
       (SELECT max(i.unit_price) FROM order_items AS i WHERE i.product_id = p.id) AS highest_sold_at
FROM products AS p
WHERE p.price > ALL (SELECT i.unit_price FROM order_items AS i WHERE i.product_id = p.id)
  AND EXISTS (SELECT 1 FROM order_items AS i WHERE i.product_id = p.id)
ORDER BY p.id;
```

```text
 id |           title           | price | highest_sold_at 
----+---------------------------+-------+-----------------
 52 | Classic Zip Hoodie - Grey | 49.00 |           45.00
(1 row)
```

**Reasoning.** Only the grey zip hoodie (52) costs more today (49.00) than at every past sale (45.00). Products 19, 34 and 37 also went up in price, but each has been sold at the new price since, so `>` fails for those sales. Products whose price went down (1, 7, 10, 29) fail immediately.

**Alternative.** `p.price > (SELECT max(i.unit_price) ...)`. For a product with no sales `max()` is `NULL`, `price > NULL` is `NULL`, and the row is dropped. So the `max()` version gets the empty case "right" without an explicit `EXISTS`, but only by accident of `NULL` semantics. Writing the `EXISTS` makes the intent visible to the next reader.
