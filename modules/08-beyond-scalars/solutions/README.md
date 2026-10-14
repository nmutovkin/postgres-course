# Module 08: Solutions

All solutions were run on PostgreSQL 17 against `./project/bin/reset.sh 09`, in the order below. The complete script is [`exercises.sql`](exercises.sql):

```bash
./project/bin/reset.sh 09
docker exec -i course-pg psql -X -U postgres -d bazaar < modules/08-beyond-scalars/solutions/exercises.sql
```

Exercises 3, 7 and 12 change the schema, so run `./project/bin/reset.sh 09` again when you're done.

---

## Exercise 1 ★: Laptops with at least 16 GB, three ways

**Containment.** `@>` tests equality only, so "≥ 16" has to be spelled out as a list of acceptable values. Look at the data first:

```sql
SELECT DISTINCT attributes -> 'ram_gb' FROM products WHERE attributes ? 'ram_gb';   -- 4, 8, 16, 32
```

Then test one containment per qualifying value. `@> ANY (array)` is a compact OR:

```sql
SELECT id, title
FROM products
WHERE category_id = 3
  AND status = 'active'
  AND attributes @> ANY (ARRAY['{"ram_gb": 16}', '{"ram_gb": 32}']::jsonb[])
ORDER BY id;
```

**jsonpath.** A filter compares numbers as numbers, and items of the wrong type simply don't match:

```sql
SELECT id, title
FROM products
WHERE category_id = 3
  AND status = 'active'
  AND attributes @? '$.ram_gb ? (@ >= 16)'
ORDER BY id;
```

**Cast.** `->>` returns text, and the cast makes the comparison numeric:

```sql
SELECT id, title
FROM products
WHERE category_id = 3
  AND status = 'active'
  AND (attributes ->> 'ram_gb')::int >= 16
ORDER BY id;
```

All three return:

```text
 id |          title
----+-------------------------
  1 | AeroBook 13 Laptop
  2 | AeroBook 15 Pro Laptop
  3 | Titan G17 Gaming Laptop
 15 | Titan G15 Gaming Laptop
(4 rows)
```

**Why containment is fragile.** It encodes *today's data* in the query. The day a seller lists a 64 GB laptop, or a 24 GB one, the query silently misses it. The jsonpath and cast versions express the actual rule.

**Why it's still worth knowing.** It's the form a GIN index serves best. With a `jsonb_path_ops` index, both `@> ANY (...)` and the equivalent `OR` of containments become bitmap index scans (try `EXPLAIN` with `SET enable_seqscan = off`). The jsonpath *range* filter can't use the index for the comparison (theory, section 10). On a large catalog with a known, small set of values (sizes, colours, memory tiers), containment can be the fastest option.

**Trade-offs of the cast.** It raises an error if any document has a non-numeric `ram_gb`, while jsonpath skips such rows. Which one you want depends on whether bad data should be loud or quiet. The cast is the version an expression index can serve.

---

## Exercise 2 ★: "You might also like"

`&&` (overlap) finds candidates. The shared tags are the intersection of two arrays, which you can compute by unnesting both and using `INTERSECT`, then wrapping the result in an `ARRAY(...)` constructor. Computing it once in a `LATERAL` subquery lets the `ORDER BY` reuse it:

```sql
SELECT p.id,
       p.title,
       s.shared_tags
FROM products p
JOIN products r ON r.id = 10
CROSS JOIN LATERAL (
    SELECT ARRAY(SELECT unnest(p.tags) INTERSECT SELECT unnest(r.tags) ORDER BY 1) AS shared_tags
) s
WHERE p.id <> r.id
  AND p.status = 'active'
  AND p.tags && r.tags
ORDER BY cardinality(s.shared_tags) DESC, p.id;
```

```text
 id |               title               |  shared_tags
----+-----------------------------------+----------------
 11 | QuietWave Buds                    | {anc,wireless}
  1 | AeroBook 13 Laptop                | {bestseller}
 12 | StudioPro Monitor Headphones      | {headphones}
 13 | KidSafe Volume-Limited Headphones | {headphones}
 25 | The Lighthouse Keeper's Secret    | {bestseller}
(5 rows)
```

The `p.tags && r.tags` condition is redundant (an empty intersection would sort last anyway). But it's the part a GIN index on `tags` can serve, so on a large table it narrows the candidates before the more expensive intersection runs.

**Alternative:** unnest product 10's tags once, join to `products` with `t.tag = ANY (p.tags)`, and `GROUP BY p.id` with `array_agg(t.tag)` and `count(*)`. That's equivalent, and arguably more "relational". The `intarray` extension has an `&` intersection operator, but only for `int[]`.

---

## Exercise 3 ★: A new order status

```sql
ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'on_hold' AFTER 'paid';
```

`IF NOT EXISTS` makes the script re-runnable. Run the statement on its own (autocommit), or commit it before using the new label: a label added in the current transaction can't be used until that transaction commits.

The report starts from the *labels*, not from the orders. Otherwise a status with no orders can't appear:

```sql
SELECT s.status,
       s.position,
       count(o.id) AS orders
FROM unnest(enum_range(NULL::order_status)) WITH ORDINALITY AS s(status, position)
LEFT JOIN orders o ON o.status = s.status
GROUP BY s.status, s.position
ORDER BY s.position;
```

```text
  status   | position | orders
-----------+----------+--------
 pending   |        1 |      5
 paid      |        2 |      6
 on_hold   |        3 |      0
 shipped   |        4 |      4
 delivered |        5 |     91
 cancelled |        6 |     10
 refunded  |        7 |      4
(7 rows)
```

`count(o.id)` rather than `count(*)` is what produces the 0: the outer join gives `on_hold` one row with all `orders` columns NULL, and `count(*)` would count it as 1.

`ORDER BY s.status` would give the same order, since enums sort by declaration, but the ordinality column makes the position explicit. Note that `on_hold` is third even though it was added last. Its `enumsortorder` is 2.5.

---

## Exercise 4 ★★: What's actually in `attributes`?

Two levels of aggregation: first count products per (category, key), then fold those counts into one row per category. Start from the per-category product counts, so that a category whose products have no keys (Clothing, with the empty `{}` of product 62) survives the `LEFT JOIN`:

```sql
WITH per_category AS (
    SELECT category_id, count(*) AS products
    FROM products
    GROUP BY category_id
), key_counts AS (
    SELECT p.category_id, k.key, count(*) AS n
    FROM products p
    CROSS JOIN LATERAL jsonb_object_keys(p.attributes) AS k(key)
    GROUP BY p.category_id, k.key
)
SELECT c.name AS category,
       pc.products,
       coalesce(jsonb_object_agg(kc.key, kc.n) FILTER (WHERE kc.key IS NOT NULL), '{}') AS key_counts,
       coalesce(array_agg(kc.key ORDER BY kc.key) FILTER (WHERE kc.n = pc.products), '{}') AS universal_keys
FROM per_category pc
JOIN categories c        ON c.id = pc.category_id
LEFT JOIN key_counts kc  ON kc.category_id = pc.category_id
GROUP BY c.id, c.name, pc.products
ORDER BY c.id;
```

```text
    category     | products |                                                      key_counts                                                       |            universal_keys
-----------------+----------+-----------------------------------------------------------------------------------------------------------------------+--------------------------------------
 Computers       |        2 | {"dpi": 1, "brand": 1, "ports": 1, "power_w": 1, "wireless": 1}                                                       | {}
 Laptops         |        7 | {"cpu": 6, "gpu": 2, "brand": 6, "colors": 1, "ram_gb": 6, "fits_in": 1, "storage": 4, "material": 1, "screen_in": 6} | {}
 Monitors        |        4 | {"brand": 4, "panel": 4, "ports": 2, "curved": 1, "size_in": 4, "refresh_hz": 3, "resolution": 4}                     | {brand,panel,resolution,size_in}
 Headphones      |        5 | {"anc": 2, "brand": 5, "colors": 2, "max_db": 1, "wireless": 5, "battery_h": 2, "impedance_ohm": 1}                   | {brand,wireless}
 Kitchen         |        1 | {"cups": 1, "material": 1, "induction": 1}                                                                            | {cups,induction,material}
 Cookware        |        6 | {"material": 6, "induction": 6, "capacity_l": 2, "diameter_cm": 1, "dishwasher_safe": 6}                              | {dishwasher_safe,induction,material}
 Cutlery         |        6 | {"blade_cm": 4, "material": 6}                                                                                        | {material}
 Garden          |        1 | {"material": 1, "dimensions_cm": 1}                                                                                   | {dimensions_cm,material}
 Planters        |        4 | {"pieces": 1, "handmade": 1, "material": 4, "diameter_cm": 2, "reservoir_ml": 1, "drainage_hole": 1}                  | {material}
 Fiction         |        1 | {"format": 1, "volumes": 1, "contents": 1, "language": 1}                                                             | {contents,format,language,volumes}
 Science Fiction |        5 | {"isbn": 5, "pages": 5, "author": 5, "language": 5}                                                                   | {author,isbn,language,pages}
 Mystery         |        5 | {"isbn": 4, "pages": 4, "author": 5, "language": 5}                                                                   | {author,language}
 Cooking         |        4 | {"isbn": 4, "pages": 4, "author": 4, "language": 4}                                                                   | {author,isbn,language,pages}
 Clothing        |        1 | {}                                                                                                                    | {}
 T-Shirts        |        5 | {"fit": 5, "size": 5, "color": 5, "material": 5}                                                                      | {color,fit,material,size}
 Hoodies         |        5 | {"fit": 5, "size": 5, "color": 5, "material": 5}                                                                      | {color,fit,material,size}
(16 rows)
```

Details:

- On the Clothing row, the `LEFT JOIN` produces one row with `kc.key` NULL. `jsonb_object_agg` would fail on a NULL key ("field name must not be null"), hence the `FILTER`. The aggregate over zero remaining rows is NULL, hence the `coalesce`.
- The keys in `key_counts` come out in jsonb's storage order (shorter keys first), whatever the aggregate's input order.
- **What it tells you:** the Laptops category has no universal key because product 16, a laptop *sleeve*, sits in it with sleeve properties. That's a catalog problem, not a JSON problem. Inventories like this are how you find drift (`ram_gb` versus `ramGB`) before your queries miss rows. Run one on any JSONB column you inherit.

**Alternative:** a single query that groups by category and uses `jsonb_object_agg` over a subquery per category (a correlated subquery in the `SELECT` list). That's shorter to write but scans `products` once per category.

---

## Exercise 5 ★★: Tags that travel together

Two lateral `unnest` calls over the same row produce every ordered pair of that product's tags. `a.tag < b.tag` keeps each unordered pair once and drops the pairs of a tag with itself:

```sql
SELECT a.tag AS tag_a,
       b.tag AS tag_b,
       count(*) AS products
FROM products p
CROSS JOIN LATERAL unnest(p.tags) AS a(tag)
CROSS JOIN LATERAL unnest(p.tags) AS b(tag)
WHERE a.tag < b.tag
GROUP BY a.tag, b.tag
HAVING count(*) >= 3
ORDER BY products DESC, tag_a, tag_b;
```

```text
  tag_a   |  tag_b  | products
----------+---------+----------
 book     | sci-fi  |        6
 book     | mystery |        5
 clothing | hoodie  |        5
 clothing | tee     |        5
 book     | cooking |        4
 cutlery  | knife   |        4
 garden   | planter |        4
(7 rows)
```

A product with *n* tags contributes *n(n−1)/2* pairs, so this is quadratic per product. That's harmless for tags, but worth knowing before you run it on arrays with hundreds of elements.

The counts assume no product repeats a tag. If tags could contain duplicates, count `DISTINCT p.id` instead of rows. Arrays don't enforce uniqueness; a junction table with a primary key would. To turn co-occurrence into a recommendation signal, divide by how often `tag_a` appears overall (the "confidence" of association-rule mining). Every pair here is a category tag plus a sub-genre, which tells you that the tags largely duplicate the category tree.

---

## Exercise 6 ★★: Monitor spec sheet with `JSON_TABLE`

```sql
SELECT p.id,
       jt.brand,
       jt.size_in,
       split_part(jt.resolution, 'x', 1)::int AS width_px,
       split_part(jt.resolution, 'x', 2)::int AS height_px,
       split_part(jt.resolution, 'x', 1)::int * split_part(jt.resolution, 'x', 2)::int AS pixels,
       jt.refresh_hz,
       jt.port_count,
       jt.has_usb_c
FROM products p,
     JSON_TABLE(p.attributes, '$'
         COLUMNS (
             brand       text          PATH '$.brand',
             size_in     numeric(4,1)  PATH '$.size_in',
             resolution  text          PATH '$.resolution',
             refresh_hz  int           PATH '$.refresh_hz'    DEFAULT 60 ON EMPTY,
             port_count  int           PATH '$.ports.size()'  DEFAULT 0 ON EMPTY,
             has_usb_c   boolean       EXISTS PATH '$.ports[*] ? (@ == "USB-C")'
         )) AS jt
WHERE p.category_id = 4
ORDER BY pixels DESC, p.id;
```

```text
 id |    brand    | size_in | width_px | height_px | pixels  | refresh_hz | port_count | has_usb_c
----+-------------+---------+----------+-----------+---------+------------+------------+-----------
  7 | ClearView   |    27.0 |     3840 |      2160 | 8294400 |         60 |          3 | t
  8 | UltraWide   |    34.0 |     3440 |      1440 | 4953600 |        144 |          0 | f
  6 | ClearView   |    24.0 |     1920 |      1080 | 2073600 |         75 |          0 | f
  9 | PortaScreen |    15.6 |     1920 |      1080 | 2073600 |         60 |          1 | t
(4 rows)
```

Points worth noticing:

- `DEFAULT 60 ON EMPTY` applies when the path finds nothing. Product 7 has no `refresh_hz` key at all. (`ON ERROR` would be for a value that exists but can't be converted.)
- `'$.ports.size()'`: in lax mode, a missing `ports` produces an empty sequence, `.size()` of nothing is nothing, and the `DEFAULT 0 ON EMPTY` kicks in.
- `EXISTS PATH` with a filter answers "is there an element equal to USB-C?" without unnesting the array.
- The resolution string is split *after* extraction. If you control the document format, store `{"width": 3840, "height": 2160}` instead. A string you have to parse is a small schema smell.

**Alternative:** `jsonb_to_record(attributes) AS r(brand text, size_in numeric, resolution text, refresh_hz int, ports jsonb)` plus `coalesce` and `jsonb_array_length(r.ports)`. It works on any PostgreSQL version, but you'd write the defaults and the USB-C test as separate expressions.

---

## Exercise 7 ★★: A `country_code` domain

```sql
BEGIN;
CREATE DOMAIN country_code AS char(2)
    CONSTRAINT country_code_format CHECK (VALUE ~ '^[A-Z]{2}$');
ALTER TABLE sellers DROP CONSTRAINT sellers_country_code_upper;
ALTER TABLE sellers   ALTER COLUMN country_code TYPE country_code;
ALTER TABLE addresses ALTER COLUMN country_code TYPE country_code;
COMMIT;

SELECT table_name, column_name, domain_name
FROM information_schema.columns
WHERE domain_name = 'country_code'
ORDER BY table_name;
```

```text
 table_name | column_name  | domain_name
------------+--------------+--------------
 addresses  | country_code | country_code
 sellers    | country_code | country_code
(2 rows)
```

```sql
UPDATE sellers SET country_code = 'it' WHERE id = 3;
UPDATE addresses SET country_code = 'D' WHERE id = 1;
```

```text
ERROR:  value for domain country_code violates check constraint "country_code_format"
ERROR:  value for domain country_code violates check constraint "country_code_format"
```

**Why `'D'` fails:** the base type is applied *before* the domain check, and `char(2)` pads a one-character value with a space to `'D '`. The regex `^[A-Z]{2}$` then sees a space. This is the behaviour you want, but it shows the order of operations: base type coercion (length, padding, `numeric` rounding) first, domain constraints second.

Converting both columns re-checked every existing row. If any address had held `'de'`, the `ALTER` would have failed and the transaction rolled back. The old `CHECK` on `sellers` would have kept working, but keeping it is redundant. Dropping it means one rule in one place.

**Alternatives:** a lookup table `countries(code char(2) PRIMARY KEY, name text)` with foreign keys is stricter, because `'XX'` passes the domain but isn't a country. Many schemas do both: a domain for the format, a foreign key for membership. `text` with a domain would avoid the padding surprise. `char(n)` is mostly a historical type, and `text` + `CHECK (length(VALUE) = 2)` is the more common modern choice.

---

## Exercise 8 ★★: Will this batch of promotions fit?

There are two kinds of conflict, so there are two joins: proposal × existing rows and proposal × proposal. `o.ref < n.ref` reports each pair of proposals once and excludes a proposal matching itself:

```sql
WITH proposed (ref, product_id, discount_pct, active_during) AS (
    VALUES ('a', 10, 10.00, tstzrange('2025-06-14 00:00+00', '2025-06-21 00:00+00')),
           ('b',  1, 15.00, tstzrange('2025-07-01 00:00+00', '2025-07-08 00:00+00')),
           ('c',  1,  5.00, tstzrange('2025-07-05 00:00+00', '2025-07-12 00:00+00')),
           ('d', 47, 10.00, tstzrange('2025-07-01 00:00+00', '2025-07-31 00:00+00')),
           ('e', 37, 20.00, tstzrange('2025-07-01 00:00+00', '2025-07-15 00:00+00')),
           ('f', 24, 10.00, tstzrange('2024-12-25 00:00+00', '2025-01-01 00:00+00'))
)
SELECT n.ref,
       n.product_id,
       'existing promotion ' || e.id      AS conflicts_with,
       n.active_during * e.active_during  AS overlap
FROM proposed n
JOIN promotions e
  ON e.product_id = n.product_id
 AND e.active_during && n.active_during
UNION ALL
SELECT n.ref,
       n.product_id,
       'proposal ' || o.ref,
       n.active_during * o.active_during
FROM proposed n
JOIN proposed o
  ON o.product_id = n.product_id
 AND o.ref < n.ref
 AND o.active_during && n.active_during
ORDER BY ref;
```

```text
 ref | product_id |    conflicts_with    |                       overlap
-----+------------+----------------------+-----------------------------------------------------
 a   |         10 | existing promotion 3 | ["2025-06-14 00:00:00+00","2025-06-15 00:00:00+00")
 c   |          1 | proposal b           | ["2025-07-05 00:00:00+00","2025-07-08 00:00:00+00")
 d   |         47 | existing promotion 6 | ["2025-07-01 00:00:00+00","2025-07-31 00:00:00+00")
(3 rows)
```

`e` starts exactly when promotion 9 ends (July 1), and `f` ends exactly when promotion 8 starts (January 1). Half-open ranges make these adjacent, not overlapping. `d` overlaps the open-ended promotion 6 *completely*, so its overlap is the whole proposal.

**Inserting what fits.** The exclusion constraint can do the filtering itself. `ON CONFLICT DO NOTHING` (without a conflict target) works with exclusion constraints:

```sql
BEGIN;
INSERT INTO promotions (product_id, discount_pct, active_during)
VALUES (10, 10.00, tstzrange('2025-06-14 00:00+00', '2025-06-21 00:00+00')),
       ( 1, 15.00, tstzrange('2025-07-01 00:00+00', '2025-07-08 00:00+00')),
       ( 1,  5.00, tstzrange('2025-07-05 00:00+00', '2025-07-12 00:00+00')),
       (47, 10.00, tstzrange('2025-07-01 00:00+00', '2025-07-31 00:00+00')),
       (37, 20.00, tstzrange('2025-07-01 00:00+00', '2025-07-15 00:00+00')),
       (24, 10.00, tstzrange('2024-12-25 00:00+00', '2025-01-01 00:00+00'))
ON CONFLICT DO NOTHING
RETURNING product_id, discount_pct, active_during;
ROLLBACK;
```

```text
 product_id | discount_pct |                    active_during
------------+--------------+-----------------------------------------------------
          1 |        15.00 | ["2025-07-01 00:00:00+00","2025-07-08 00:00:00+00")
         37 |        20.00 | ["2025-07-01 00:00:00+00","2025-07-15 00:00:00+00")
         24 |        10.00 | ["2024-12-25 00:00:00+00","2025-01-01 00:00:00+00")
(3 rows)
```

Within the batch, rows are inserted in order, so `b` wins over `c` simply because it came first. That's a policy decision made by accident. The report query exists so a human can decide instead. `ON CONFLICT DO UPDATE` would be rejected here: it needs a unique index to identify *the* conflicting row, and an exclusion constraint can conflict with several.

---

## Exercise 9 ★★: Promotion calendar for June 2025

Each day becomes a one-day range, so "on promotion at any time during that day" is a plain overlap test:

```sql
SELECT d::date                                                   AS day,
       count(pr.id)                                              AS products_on_promotion,
       coalesce(string_agg(pr.product_id::text, ',' ORDER BY pr.product_id), '') AS products
FROM generate_series(timestamptz '2025-06-01 00:00+00',
                     timestamptz '2025-06-30 00:00+00',
                     interval '1 day') AS d
LEFT JOIN promotions pr
       ON pr.active_during && tstzrange(d, d + interval '1 day')
GROUP BY d
ORDER BY d;
```

```text
    day     | products_on_promotion | products
------------+-----------------------+----------
 2025-06-01 |                     2 | 10,37
 ...
 2025-06-14 |                     2 | 10,37
 2025-06-15 |                     1 | 37
 ...
 2025-06-19 |                     1 | 37
 2025-06-20 |                     3 | 37,47,48
 ...
 2025-06-30 |                     3 | 37,47,48
(30 rows)
```

(Rows elided. The full output has one row per day: 14 × 2 + 5 × 1 + 11 × 3 = 66.)

Product 10's promotion ends at `2025-06-15 00:00`, exclusive, so it isn't on June 15. With `@> d` ("on promotion at midnight") instead of `&&`, a promotion starting at noon would be missed on its first day. "At any time during the day" needs the overlap.

`generate_series` over `timestamptz` steps in the session's time zone. In UTC that's exactly what you want. In a zone with daylight saving, `interval '1 day'` is a calendar day (23 or 25 hours on transition days), which is usually what a calendar means anyway.

---

## Exercise 10 ★★: Sale prices at a given moment

```sql
SELECT p.id,
       p.title,
       p.price,
       pr.discount_pct,
       round(p.price * (1 - coalesce(pr.discount_pct, 0) / 100), 2) AS effective_price
FROM products p
LEFT JOIN promotions pr
       ON pr.product_id = p.id
      AND pr.active_during @> timestamptz '2025-06-10 12:00+00'
WHERE p.status = 'active'
ORDER BY pr.discount_pct DESC NULLS LAST, p.id;
```

```text
 id |          title           |  price  | discount_pct | effective_price
----+--------------------------+---------+--------------+-----------------
 10 | QuietWave ANC Headphones |  229.00 |        20.00 |          183.20
 37 | Dutch Oven 5.5L Enamel   |  119.00 |        12.50 |          104.13
  1 | AeroBook 13 Laptop       |  999.00 |              |          999.00
  2 | AeroBook 15 Pro Laptop   | 1499.00 |              |         1499.00
  3 | Titan G17 Gaming Laptop  | 2199.99 |              |         2199.99
...  (54 rows)
```

and the totals:

```text
 products | total_effective | total_list
----------+-----------------+------------
       54 |        10168.67 |   10229.34
```

Why this join can't duplicate products: the exclusion constraint guarantees that at most one promotion per product contains any given instant. Without that guarantee, you'd need `DISTINCT ON` or a "best discount" subquery, and you'd have to decide what overlapping promotions *mean*. The constraint settles the business question once, so queries stay simple.

Rounding: 119.00 × 0.875 = 104.125, and `round(numeric, 2)` rounds half away from zero, giving 104.13. Money is `numeric`, so this is exact decimal arithmetic. With `float`, 104.125 might be stored as 104.12499999... and round down.

Module 09's first exercise wraps this calculation in a function, `effective_price(product_id, at)`.

---

## Exercise 11 ★★★: Did customers get their discount?

The price a customer should pay has two time-dependent parts: the list price valid at that moment, and the promotion active at that moment. The list price is "the latest history row at or before `created_at`", a top-1-per-row lookup that `LATERAL ... ORDER BY ... LIMIT 1` expresses directly:

```sql
WITH priced AS (
    SELECT o.id AS order_id,
           oi.product_id,
           oi.quantity,
           oi.unit_price,
           h.price AS list_price,
           pr.discount_pct,
           round(h.price * (1 - coalesce(pr.discount_pct, 0) / 100), 2) AS effective_price
    FROM orders o
    JOIN order_items oi ON oi.order_id = o.id
    CROSS JOIN LATERAL (
        SELECT price
        FROM product_price_history h
        WHERE h.product_id = oi.product_id
          AND h.valid_from <= o.created_at
        ORDER BY h.valid_from DESC
        LIMIT 1
    ) h
    LEFT JOIN promotions pr
           ON pr.product_id = oi.product_id
          AND pr.active_during @> o.created_at
)
SELECT order_id, product_id, quantity, list_price, discount_pct, effective_price, unit_price,
       quantity * (unit_price - effective_price) AS overcharged,
       sum(quantity * (unit_price - effective_price)) OVER () AS total_overcharged
FROM priced
WHERE unit_price <> effective_price
ORDER BY order_id, product_id;
```

```text
 order_id | product_id | quantity | list_price | discount_pct | effective_price | unit_price | overcharged | total_overcharged
----------+------------+----------+------------+--------------+-----------------+------------+-------------+-------------------
       97 |         37 |        1 |     119.00 |        12.50 |          104.13 |     119.00 |       14.87 |             37.67
      111 |         47 |        1 |      19.00 |        30.00 |           13.30 |      19.00 |        5.70 |             37.67
      111 |         48 |        1 |      19.00 |        30.00 |           13.30 |      19.00 |        5.70 |             37.67
      119 |         48 |        2 |      19.00 |        30.00 |           13.30 |      19.00 |       11.40 |             37.67
(4 rows)
```

The window function `sum(...) OVER ()` adds the grand total to every row without a second query. The spot checks:

```text
 product_id |           at           | list_price | discount_pct | effective_price
------------+------------------------+------------+--------------+-----------------
          1 | 2024-11-26 12:00:00+00 |     999.00 |        10.00 |          899.10
         34 | 2024-07-03 12:00:00+00 |      45.00 |        25.00 |           33.75
(2 rows)
```

Both spot checks are interesting because the list price changed *on the day the promotion started*: product 1 dropped to 999.00 on 2024-11-25, and product 34 rose to 45.00 on 2024-07-01. Using today's `products.price` instead of the history would give the right answer for product 1 only by coincidence.

**Why `CROSS JOIN LATERAL` is safe here:** every ordered product has a history row at or before its first sale (NOTES.md: the history matches `order_items.unit_price` exactly). If one could be missing, `LEFT JOIN LATERAL ... ON true` would keep the line, with a NULL list price.

**Alternatives:** store `product_price_history` with a `tstzrange` validity column (`[valid_from, next valid_from)`) and an exclusion constraint. The lookup then becomes `h.valid_during @> o.created_at`, with the same shape as the promotion join, and GiST-indexable. That's the "temporal table" design. The current design (only `valid_from`) is simpler to write to but needs the top-1 lookup to read.

---

## Exercise 12 ★★★: Taking a status back

There's no `DROP VALUE`, so: move the old type out of the way, create the new one under the old name, and rewrite the column. The default has to be dropped and recreated (it's typed `'pending'::order_status_old`), and the `CASE` maps the label that won't exist any more. Casting through `text` is required, because there's no cast between two different enum types:

```sql
UPDATE orders SET status = 'on_hold' WHERE id = 120;

BEGIN;
ALTER TYPE order_status RENAME TO order_status_old;
CREATE TYPE order_status AS ENUM ('pending', 'paid', 'shipped', 'delivered', 'cancelled', 'refunded');
ALTER TABLE orders ALTER COLUMN status DROP DEFAULT;
ALTER TABLE orders ALTER COLUMN status TYPE order_status
    USING (CASE status::text WHEN 'on_hold' THEN 'paid' ELSE status::text END)::order_status;
ALTER TABLE orders ALTER COLUMN status SET DEFAULT 'pending';
DROP TYPE order_status_old;
COMMIT;
```

```text
                     enum_range
-----------------------------------------------------
 {pending,paid,shipped,delivered,cancelled,refunded}

 id  | status
-----+--------
 120 | paid

     column_default
-------------------------
 'pending'::order_status

 old_type_left
---------------
             0
```

`DROP TYPE order_status_old` doubles as a safety net. If any other column still used the old type, it would fail ("cannot drop type ... because other objects depend on it") and roll everything back.

**With views or materialized views.** At the start of module 10, `seller_sales_daily` (module 09) depends on `orders.status`, and the `ALTER COLUMN ... TYPE` fails:

```text
ERROR:  cannot alter type of a column used by a view or rule
DETAIL:  rule _RETURN on materialized view seller_sales_daily depends on column "status"
```

You'd have to drop the dependent views in the same transaction and recreate them afterwards, along with their indexes, comments and grants. PL/pgSQL functions that mention the type aren't tracked as dependencies at all; they fail at their next call if they use the removed label. Removing an enum value is a real migration, which is why section 13.3 recommends enums only for value sets that rarely change. The table rewrite also takes an `ACCESS EXCLUSIVE` lock on `orders` for its duration.

**Alternative for big tables:** keep the label but stop using it: `UPDATE` the rows, then add `CHECK (status <> 'on_hold')` (with `NOT VALID` plus `VALIDATE` for zero downtime). The type is unchanged, so nothing is rewritten, at the cost of a dead label in `enum_range`.

---

## Exercise 13 ★★★: Gaps between promotions

Clip every promotion to the window with `*`, merge per product with `range_agg`, and subtract from the window, as a multirange, to get the gaps:

```sql
WITH win AS (
    SELECT tstzrange('2024-07-01 00:00+00', '2025-07-01 00:00+00') AS w
), promoted AS (
    SELECT pr.product_id,
           range_agg(pr.active_during * win.w) AS promoted
    FROM promotions pr, win
    WHERE pr.active_during && win.w
    GROUP BY pr.product_id
)
SELECT p.product_id,
       (SELECT sum(upper(r) - lower(r)) FROM unnest(p.promoted) AS r)                       AS promoted_time,
       (SELECT max(upper(g) - lower(g)) FROM unnest(win.w::tstzmultirange - p.promoted) AS g) AS longest_gap,
       win.w::tstzmultirange - p.promoted                                                     AS not_promoted
FROM promoted p, win
ORDER BY p.product_id;
```

```text
 product_id | promoted_time | longest_gap |                                                                         not_promoted
------------+---------------+-------------+---------------------------------------------------------------------------------------------------------------------------------------------------------------
          1 | 7 days        | 211 days    | {["2024-07-01 00:00:00+00","2024-11-25 00:00:00+00"),["2024-12-02 00:00:00+00","2025-07-01 00:00:00+00")}
          7 | 31 days       | 243 days    | {["2024-07-01 00:00:00+00","2025-03-01 00:00:00+00"),["2025-04-01 00:00:00+00","2025-07-01 00:00:00+00")}
         10 | 21 days       | 181 days    | {["2024-07-01 00:00:00+00","2024-11-25 00:00:00+00"),["2024-12-02 00:00:00+00","2025-06-01 00:00:00+00"),["2025-06-15 00:00:00+00","2025-07-01 00:00:00+00")}
         24 | 7 days        | 184 days    | {["2024-07-01 00:00:00+00","2025-01-01 00:00:00+00"),["2025-01-08 00:00:00+00","2025-07-01 00:00:00+00")}
         34 | 7 days        | 358 days    | {["2024-07-08 00:00:00+00","2025-07-01 00:00:00+00")}
         37 | 61 days       | 304 days    | {["2024-07-01 00:00:00+00","2025-05-01 00:00:00+00")}
         47 | 11 days       | 354 days    | {["2024-07-01 00:00:00+00","2025-06-20 00:00:00+00")}
         48 | 11 days       | 354 days    | {["2024-07-01 00:00:00+00","2025-06-20 00:00:00+00")}
(8 rows)
```

The clipping matters for two products. Promotion 9 of product 37 ends exactly at the window's end, and the open-ended promotions of 47 and 48 would otherwise extend to infinity and make `promoted_time` infinite. After clipping, they count 11 days (June 20 to July 1).

Part 2 is the same subtraction with *all* promotions merged into one multirange:

```sql
WITH win AS (
    SELECT tstzrange('2024-07-01 00:00+00', '2025-07-01 00:00+00') AS w
)
SELECT g                   AS no_promotion_at_all,
       upper(g) - lower(g) AS length
FROM win,
     unnest(win.w::tstzmultirange - (SELECT range_agg(active_during) FROM promotions)) AS g
ORDER BY g;
```

```text
                 no_promotion_at_all                 |  length
-----------------------------------------------------+----------
 ["2024-07-08 00:00:00+00","2024-11-25 00:00:00+00") | 140 days
 ["2024-12-02 00:00:00+00","2025-01-01 00:00:00+00") | 30 days
 ["2025-01-08 00:00:00+00","2025-03-01 00:00:00+00") | 52 days
 ["2025-04-01 00:00:00+00","2025-05-01 00:00:00+00") | 30 days
(4 rows)
```

Note what disappeared. Promotion 9 (May 1 to July 1) and promotions 6 and 7 (from June 20) cover the end of the window, and promotion 3 (June 1–15) lies *inside* promotion 9. `range_agg` merged them, so no gap appears there.

**Without multiranges** (PostgreSQL 13 and earlier), this is a classic "gaps and islands" problem (module 06): sort the ranges, use `lag(upper(...))` to find where a range starts after the previous one ended, and handle the window edges by hand. The multirange version is shorter and much harder to get wrong.
