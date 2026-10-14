# Module 06: Project milestone — category toolkit and seller leaderboard

**Goal.** Bazaar's storefront and its seller dashboard both need answers that plain joins can't give. The storefront needs the category tree: menus show a department's whole subtree, product pages show a breadcrumb, and the merchandising team wants revenue per category *including everything below it*, so "Electronics" means Electronics plus Computers plus Laptops and so on. The seller dashboard gets a monthly leaderboard showing where each shop ranks and whether it moved up or down since last month.

This milestone adds no tables. You write reusable, parameterised SQL scripts that the team can run with psql (and that the Python service will reuse in Part IV). Keep your files in `workspace/06/`.

Start from:

```bash
./project/bin/reset.sh 06
```

## Requirements

### 1. `workspace/06/category_tree.sql`: the category toolkit

One psql script with three queries. It takes two psql variables, `root_id` and `product_id`, and falls back to defaults (`1` and `3`) when they aren't given, so both of these work:

```bash
docker exec -i course-pg psql -X -U postgres -d bazaar < workspace/06/category_tree.sql
docker exec -i course-pg psql -X -U postgres -d bazaar -v root_id=14 -v product_id=25 < workspace/06/category_tree.sql
```

(Module 01 showed how to test whether a variable is set: `\if :{?root_id}`.)

**1a. Subtree.** Every category in the subtree rooted at `:root_id`, the root included, with:

- `id`;
- `category`: the name, indented by four spaces per level;
- `depth`: 0 for the root;
- `id_path`: an array of ids from the root of the subtree down to this category;
- `path`: the names from the subtree's root down to this category, joined with `' > '`.

Rows are in **depth-first order**, with siblings sorted **by name**: each category is followed immediately by its own subtree.

**1b. Breadcrumb.** One row for `:product_id`: `product_id`, `title`, and `breadcrumb`, the names from the root category down to the product's own category, joined with `' › '`.

**1c. Revenue roll-up.** One row per category (all 23), in depth-first order with siblings by name, with:

- `id`, `category` (indented by depth);
- `own_revenue`: revenue of products placed directly in this category;
- `total_revenue`: revenue of products in this category **or any of its descendants**.

Revenue is `sum(quantity * unit_price)` over order items of orders with status `paid`, `shipped` or `delivered`. Categories without sales show `0`.

**Robustness.** Queries 1a and 1b must **terminate** even if someone has corrupted the tree with a cycle (they may return odd results then, but they must not run forever). Use the `CYCLE` clause or an equivalent guard.

### 2. `workspace/06/leaderboard.sql`: the seller leaderboard

One query, for the months from January 2025 on, with one row per (month, seller that had revenue that month):

- `month` (first day of the month), `rank`, `shop_name`, `orders` (number of paid orders), `revenue` (sum of `orders.total`);
- `prev_rank`: the seller's rank in the **previous calendar month**, or NULL if the seller had no revenue then;
- `rank_change`: `'▲ n'` if the seller moved up n places, `'▼ n'` if down, `'='` if unchanged, `'entered'` if the seller wasn't ranked in the previous calendar month.

Rules: revenue counts orders with status `paid`, `shipped` or `delivered`, by the calendar month (UTC) of `created_at`. Ranks are **dense** (ties share a rank, and the next rank follows without a gap), highest revenue first. Order the output by month, then rank, then shop name.

Watch the trap from section 6.1 of the theory: "the previous row" is not "the previous calendar month" when a seller skipped a month.

## Acceptance checks

Run these against `./project/bin/reset.sh 06`.

**Subtree.** With `root_id=14`, query 1a returns exactly:

```text
 id |        category         | depth |  id_path   |               path                
----+-------------------------+-------+------------+-----------------------------------
 14 | Books                   |     0 | {14}       | Books
 15 |     Fiction             |     1 | {14,15}    | Books > Fiction
 17 |         Mystery         |     2 | {14,15,17} | Books > Fiction > Mystery
 16 |         Science Fiction |     2 | {14,15,16} | Books > Fiction > Science Fiction
 18 |     Non-Fiction         |     1 | {14,18}    | Books > Non-Fiction
 19 |         Cooking         |     2 | {14,18,19} | Books > Non-Fiction > Cooking
 20 |         History         |     2 | {14,18,20} | Books > Non-Fiction > History
(7 rows)
```

With the default `root_id=1`, it returns 7 rows, and Audio (with Headphones and Speakers) comes before Computers.

**Breadcrumbs.** `product_id=25` gives `Books › Fiction › Mystery`; `product_id=3` gives `Electronics › Computers › Laptops`; `product_id=62` (a product placed directly in a root) gives `Clothing`.

**Roll-up.** Query 1c returns 23 rows. Spot checks:

| Category | own_revenue | total_revenue |
|---|---|---|
| Electronics (1) | 0 | 41,598.82 |
| Computers (2) | 555.99 | 38,115.89 |
| Laptops (3) | 35,315.93 | 35,315.93 |
| Books (14) | 0 | 1,656.43 |
| Fiction (15) | 553.00 | 1,272.66 |
| History (20) | 0 | 0 |
| Kitchen (9) | 168.00 | 3,562.94 |

The four roots' `total_revenue` must add up to the marketplace's total revenue:

```sql
SELECT sum(total) FROM orders WHERE status IN ('paid', 'shipped', 'delivered');
```

```text
   sum    
----------
 49095.19
(1 row)
```

(Root totals equal seller revenues here only because every seller in the small dataset happens to sell in a single department.)

**Robustness.** This must finish within a second and print both results (the content doesn't matter):

```bash
( echo "BEGIN; UPDATE categories SET parent_id = 3 WHERE id = 1; SET LOCAL statement_timeout = '5s';"
  sed -n '1,/^-- 3\./p' workspace/06/category_tree.sql      # queries 1a and 1b only, if you mark query 1c with a "-- 3." comment
  echo "ROLLBACK;" ) | docker exec -i course-pg psql -X -U postgres -d bazaar -v root_id=2
```

If your file is organised differently, run your queries 1a and 1b by hand in a transaction after the `UPDATE`.

**Leaderboard.** 18 rows. The first and last months look like this:

```text
   month    | rank |    shop_name    | orders | revenue | prev_rank | rank_change 
------------+------+-----------------+--------+---------+-----------+-------------
 2025-01-01 |    1 | TechTrove       |      1 |  996.00 |         1 | =
 2025-01-01 |    2 | Bookworm Corner |      1 |   19.95 |         3 | ▲ 1
 ...
 2025-06-01 |    1 | TechTrove       |      8 | 9492.93 |         1 | =
 2025-06-01 |    2 | Casa Cucina     |      3 |  233.00 |         2 | =
 2025-06-01 |    3 | Threadbare Co.  |      3 |  228.00 |           | entered
 2025-06-01 |    4 | Bookworm Corner |      3 |  151.39 |         3 | ▼ 1
```

Two rows test the "previous calendar month" rule: in March 2025, Casa Cucina and Threadbare Co. are `entered` (neither sold in February, although both sold earlier), and in April 2025 Bookworm Corner is `entered` (no sales in March). A solution using `lag()` over each seller's selling months gets these wrong. January 2025 must compare with December 2024, so don't filter the months before ranking.

## Reference solution

This milestone has no file in `project/db/`, because it adds no schema. The reference queries are in this module's solutions:

- [`solutions/milestone_category_tree.sql`](solutions/milestone_category_tree.sql)
- [`solutions/milestone_leaderboard.sql`](solutions/milestone_leaderboard.sql)

The roll-up in the reference builds a **closure** CTE: every (ancestor, descendant) pair, including each category with itself. Rolling up then becomes an ordinary join and `GROUP BY`. Module 07 discusses storing such a closure table permanently, one of several ways to represent trees in a relational schema.
