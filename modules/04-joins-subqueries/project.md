# Module 04: Project milestone — the admin's saved questions

**Goal.** The Bazaar operations team asks the same questions every week: "what exactly was in this order?", "who stopped buying?", "what's about to run out?", "what happened in the last seven days?". Right now, each time somebody asks, an engineer writes a query from scratch, slightly differently, and sometimes wrongly. In this milestone you write those questions down once, as a file of saved, documented, tested queries that anyone on the team can run. Each query comes with the question it answers in plain words, and its **expected answer on the small dataset**, so whoever changes the query later can tell immediately whether it still works.

Put your work in `workspace/04/queries.sql`. Like module 01's report, it's a psql script: it should run top to bottom with `ON_ERROR_STOP`, print a header line before each query (`\echo`), and change no data.

## Requirements

### Format

- One section per question, each starting with a comment block that states the question and its **expected answer** on `reset.sh 04` data: the row count plus the key values that identify a correct result (ids, names or totals).
- Every query follows the course's SQL style: uppercase keywords, table aliases, every column qualified once more than one table is involved, one clause per line.
- Two parameters as psql variables with defaults, so the file runs as-is but can be pointed at another order or another "today":
  - `order_id` (default `102`), used by Q1;
  - `today` (default `2025-06-30`), used wherever the question says "this year", "last year" or "the last 7 days". **Never use `now()`**: the dataset is frozen at 2025-06-30.

  Module 01 (section 11) showed how to give a psql variable a default with `\if :{?name}`. Reference a variable in SQL as `:order_id`, or as `:'today'` when it must become a quoted literal.

### The questions

| # | Question | Techniques |
|---|---|---|
| Q1 | **Order sheet.** Every line of order `:order_id`: date, status, buyer name, shop name, product title, category name, quantity, price paid, today's price and line total. | 5-table inner join |
| Q2 | **Customer directory.** Every user: active flag, the shop they own (if any), and the id, date and status of their most recent order (if any). | `LEFT JOIN`, `LEFT JOIN LATERAL` |
| Q3 | **Stock alert.** Active products with nothing left to sell (`quantity - reserved = 0`), with the shop to contact and both stock numbers. | joins + filter |
| Q4 | **Dormant buyers.** Users who ordered during the calendar year *before* `:today`'s year but haven't ordered yet in `:today`'s year, with the date of their last order. | `EXISTS` + `NOT EXISTS` |
| Q5 | **Orders by inactive users.** Every order placed by a user whose account is now inactive. | join + filter |
| Q6 | **Breadcrumbs.** Every active product with its full category path, like `Electronics > Computers > Laptops`. Products in second-level and root categories must show shorter paths, not disappear. | self joins, `concat_ws` |
| Q7 | **Shop owners as customers.** Which shops' owners bought (non-cancelled) from which shops, one row per pair, with a boolean column saying whether it was their own shop. | `sellers` joined twice |
| Q8 | **Sticker-pack companions.** For every order containing the free sticker pack (product 62), the *other* products in that order. | `order_items` self join |
| Q9 | **Stale stock.** Active products with units available that nobody has ordered in `:today`'s year, with the date they were last ordered (or `NULL`). | anti-join, correlated subquery |
| Q10 | **Activity feed.** New orders (with shop and buyer), new users and new shops from the 7 days ending on `:today` (inclusive), in one list, newest first, with a `kind` column. | `UNION ALL` |

## Acceptance checks

Start from a fresh database and run your file:

```bash
./project/bin/reset.sh 04
docker exec -i course-pg psql -X -U postgres -d bazaar < workspace/04/queries.sql
```

(Or `\i /course/workspace/04/queries.sql` from inside `./project/bin/psql.sh`.) The script must finish without errors, and each query must match its documented expectation:

| # | Rows | Must contain |
|---|---|---|
| Q1 | 4 | buyer Liam O'Connor, shop TechTrove; line totals 2199.99 + 249.00 + 189.00 + 19.99 = 2657.98 |
| Q2 | 30 | 6 rows with a shop (users 1–6); 9 rows without an order (6, 7, 15, 16, 21, 24, 28, 29, 30); user 8's last order is 110 |
| Q3 | 3 | products 3 and 13 (fully reserved), 25 (quantity 0) |
| Q4 | 3 | users 2 (last ordered 2024-04-04), 9 (2024-02-01), 17 (2024-11-28) |
| Q5 | 4 | orders 6 (user 9), 28, 48, 77 (user 22) |
| Q6 | 54 | product 1 → `Electronics > Computers > Laptops`; 17 → `Electronics > Computers`; 33 → `Books > Fiction`; 62 → `Clothing` |
| Q7 | 9 | `own_shop` is false in every row |
| Q8 | 9 | orders 10, 46, 82, 111 and 115; order 111's companions are products 47, 48 and 54 |
| Q9 | 9 | products 15, 20, 21, 35, 44, 52, 57, 58, 60; `last_ordered` is `NULL` for 15, 57, 58 and 60 |
| Q10 | 13 | 12 orders (109–120) and one signup (user 30, Samuel Okafor) |

The parameters must work too:

```bash
docker exec -i course-pg psql -X -U postgres -d bazaar -v order_id=40 < workspace/04/queries.sql | head -8
# Q1 now shows order 40: 4 lines for Olivia Brown, AeroBook 15 Pro Laptop first (4497.00)

docker exec -i course-pg psql -X -U postgres -d bazaar -v today=2025-03-20 < workspace/04/queries.sql | sed -n '/Q10/,$p'
# Q10 now covers 2025-03-14 .. 2025-03-20: exactly 2 rows, orders 85 (2025-03-20 16:13) and 84 (2025-03-19).
# The orders of 2025-03-12 are outside the window, and so is 2025-03-21 00:00.
```

Finally, a property check that guards against fan-out in Q1. The line totals must add up to the order's stored total:

```sql
SELECT o.total = sum(i.quantity * i.unit_price) AS consistent
FROM orders AS o
JOIN order_items AS i ON i.order_id = o.id
WHERE o.id = 102
GROUP BY o.total;
--  consistent
-- ------------
--  t
```

## Reference solution

- [`solutions/queries.sql`](solutions/queries.sql): all ten questions with their documented expectations.

This module has no file in `project/db/`: the milestone adds queries, not schema. Modules 05 and 06 build on the same habit. Module 09 turns some of these questions into views, so the team can run them by name.
