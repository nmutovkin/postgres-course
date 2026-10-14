# Module 03: Project milestone — the catalogue importer

**Goal.** Sakura Gadgets (seller 6) opened its shop in May 2025 but hasn't listed anything yet. Like most sellers, it keeps its catalogue in a spreadsheet and sends Bazaar a CSV export. Your job is the **catalogue importer**: a psql script that loads a seller's CSV into `products` and `inventory`. It must be safe to run again whenever the seller sends an updated file, and it must never let a bad line corrupt the catalogue. Instead, it reports every rejected line and the reason, so the seller can fix the spreadsheet.

This is how real data loads are built: **load into a staging table, validate with SQL, then upsert into the real tables, all in one transaction.** You'll write your own version in `workspace/03/load_catalog.sql`.

## The input

Two files are provided in [`data/`](data/), mounted in the container at `/course/modules/03-crud/data/`:

- [`sakura_catalog.csv`](data/sakura_catalog.csv): the first upload, 14 lines, with a range of problems;
- [`sakura_catalog_update.csv`](data/sakura_catalog_update.csv): the seller's corrected and updated file, 9 lines.

The format, with a header line:

| Column | Meaning | Rules |
|---|---|---|
| `sku` | the seller's product code | required; must not appear twice in the same file |
| `title` | product title | required |
| `description` | free text | optional: empty means `NULL` |
| `category_slug` | e.g. `headphones` | must be an existing `categories.slug` |
| `price` | e.g. `39.90` | a decimal with a **point**, fits `numeric(12,2)`, not negative |
| `quantity` | units in stock | a whole number, not negative. It's the **absolute** stock on hand, not a delta |
| `status` | `draft`, `active` or `archived` | optional: empty means `draft` |

Surrounding whitespace in any field should be ignored, and so should the case of `category_slug` and `status`.

## Requirements

Write `workspace/03/load_catalog.sql`, a psql script that:

1. **Takes parameters** as psql variables: `seller_id` (default 6) and `file` (default `/course/modules/03-crud/data/sakura_catalog.csv`). Use `\if :{?var}` to supply defaults (module 01). Note that `\copy` doesn't expand psql variables, so read the file with server-side `COPY ... FROM :'file'`, which works because you're connected as a superuser and the path is inside the container.
2. **Runs in one transaction** with `ON_ERROR_STOP`. If anything unexpected fails, the database is unchanged.
3. **Stages the file** in a temporary table whose input columns are all `text`, so that any line loads. Keep each line's position in the file, for the report.
4. **Validates every line** and records *all* of its problems, not just the first one: missing SKU or title, invalid or negative price, invalid or negative quantity, unknown status, unknown category, and an SKU that appears more than once in the file (reject every copy, since you can't know which one the seller meant). Don't let a bad value crash the script: test conversions with `pg_input_is_valid`.
5. **Upserts the valid lines into `products`** for the given seller, using the `(seller_id, sku)` unique constraint. Existing products get the new category, title, description, price and status, but **only if something actually changed**, and only then is `updated_at` set. Products of the seller that aren't in the file are left alone.
6. **Sets the stock** of every valid line in `inventory` (insert or update), again only touching rows whose quantity changed.
7. **Reports** the outcome of every line of the file: `insert`, `update` (product or stock changed), `unchanged`, or `rejected`, plus a list of the rejected lines with their reasons, plus summary counts.
8. Is **idempotent**: running it twice with the same file changes nothing the second time, and the report then shows every valid line as `unchanged`.

Tools from the theory you'll need: `COPY` (1.3), `UPDATE` with expressions (10.2), `UPDATE ... FROM` to look up category ids and product ids (10.2), `pg_input_is_valid` and casts (8.4), `CASE` (8.5), `coalesce`/`nullif` and `IS DISTINCT FROM` (4), `INSERT ... SELECT` (10.1) and `ON CONFLICT ... DO UPDATE ... WHERE` (11.2), and transactions (9).

> **Pitfall:** `WHERE pg_input_is_valid(price, 'numeric(12,2)') AND price::numeric(12,2) < 0` looks safe, but SQL doesn't promise to evaluate `AND` left to right. The planner may evaluate the cast first and crash on `'24,99'`. `CASE WHEN pg_input_is_valid(...) THEN price::numeric(12,2) < 0 ELSE ... END` guarantees the order.

## How to run it

```bash
mkdir -p workspace/03
./project/bin/reset.sh 04
docker exec -i course-pg psql -X -U postgres -d bazaar < workspace/03/load_catalog.sql
docker exec -i course-pg psql -X -U postgres -d bazaar \
    -v file=/course/modules/03-crud/data/sakura_catalog_update.csv < workspace/03/load_catalog.sql
```

## Acceptance checks

[`solutions/check_catalog.sql`](solutions/check_catalog.sql) prints seller 6's catalogue (SKU, title, whether the description is `NULL`, category, price, status, stock, and whether the product was changed after it was created), plus guard rails: the other sellers' 62 products are untouched, and every product has an inventory row.

```bash
docker exec -i course-pg psql -X -U postgres -d bazaar < modules/03-crud/solutions/check_catalog.sql
```

### 1. The first upload

After `./project/bin/reset.sh 04` and one run with the default file, your report must reject exactly these 8 lines, for these reasons (wording may differ):

```text
 line |    sku     |               title                |                     error                     
------+------------+------------------------------------+-----------------------------------------------
    2 | SG-AUD-001 | Sakura Mini Bluetooth Speaker      | duplicate sku in file
    6 | SG-CMP-002 | Bonsai USB-C Hub 7-in-1            | invalid price '24,99'
    7 | SG-CMP-003 | Kanji Mechanical Keyboard          | unknown category 'keyboards'
    9 | SG-MON-002 |                                    | missing title
   10 | SG-AUD-001 | Sakura Mini Bluetooth Speaker (v2) | duplicate sku in file
   11 | SG-ACC-001 | Washi Tape Cable Ties (10 pcs)     | invalid quantity '-5'
   12 | SG-ACC-002 | Daruma Phone Stand                 | invalid quantity 'ten'; invalid status 'live'
   15 | SG-MON-003 | Hokusai 49" Ultrawide Monitor      | invalid price '12345678901'
```

(Line numbers count the header as line 1. Line 12 has two problems, and both are reported. The price on line 15 doesn't fit `numeric(12,2)`.) The other 6 lines are inserted. `check_catalog.sql` must then show:

```text
    sku     |          title          | no_description |  category  | price  | status | quantity | product_changed 
------------+-------------------------+----------------+------------+--------+--------+----------+-----------------
 SG-AUD-002 | Hanami Wireless Earbuds | f              | headphones |  59.00 | active |       40 | f
 SG-AUD-003 | Koto Studio Headphones  | t              | headphones | 129.00 | draft  |        8 | f
 SG-AUD-004 | Taiko Party Speaker     | f              | speakers   | 149.00 | draft  |       12 | f
 SG-CMP-001 | Origami Laptop Stand    | f              | computers  |  34.50 | active |       60 | f
 SG-CMP-004 | Sumi-e Mouse Pad XL     | f              | computers  |  12.00 | active |      200 | f
 SG-MON-001 | Fuji 27" 4K Monitor     | f              | monitors   | 349.00 | active |        6 | f

 other_products | other_price_sum | products_without_stock 
----------------+-----------------+------------------------
             62 |        11463.34 |                      0
```

Points to check: the CSV-quoted title `Fuji 27" 4K Monitor` arrived intact; the empty description of SG-AUD-003 is `NULL`, not an empty string; SG-AUD-004's empty status became `draft`; the price `12` became 12.00; and SG-CMP-004's description has no leading or trailing spaces (`SELECT description FROM products WHERE sku = 'SG-CMP-004'`).

### 2. Idempotence

Run the script a second time with the same file. The summary must be:

```text
 lines | inserted | updated | unchanged | rejected 
-------+----------+---------+-----------+----------
    14 |        0 |       0 |         6 |        8
```

and `check_catalog.sql` must print exactly the same as before. In particular, `product_changed` stays `f` everywhere, because nothing was rewritten.

### 3. The corrected file

Run it with `-v file=/course/modules/03-crud/data/sakura_catalog_update.csv`. The seller fixed the duplicate, the price format and the category, lowered the earbuds' price and corrected the laptop stands' stock count. Expected outcomes per line:

```text
 line |    sku     |  outcome  
------+------------+-----------
    2 | SG-AUD-001 | insert
    3 | SG-AUD-002 | update
    4 | SG-AUD-003 | unchanged
    5 | SG-CMP-001 | update
    6 | SG-CMP-002 | insert
    7 | SG-CMP-003 | insert
    8 | SG-MON-001 | unchanged
    9 | SG-AUD-004 | unchanged
   10 | SG-CMP-004 | unchanged
```

and `check_catalog.sql`:

```text
    sku     |             title             | no_description |  category  | price  | status | quantity | product_changed 
------------+-------------------------------+----------------+------------+--------+--------+----------+-----------------
 SG-AUD-001 | Sakura Mini Bluetooth Speaker | f              | speakers   |  39.90 | active |       25 | f
 SG-AUD-002 | Hanami Wireless Earbuds       | f              | headphones |  54.00 | active |       40 | t
 SG-AUD-003 | Koto Studio Headphones        | t              | headphones | 129.00 | draft  |        8 | f
 SG-AUD-004 | Taiko Party Speaker           | f              | speakers   | 149.00 | draft  |       12 | f
 SG-CMP-001 | Origami Laptop Stand          | f              | computers  |  34.50 | active |       45 | f
 SG-CMP-002 | Bonsai USB-C Hub 7-in-1       | f              | computers  |  24.99 | active |      100 | f
 SG-CMP-003 | Kanji Mechanical Keyboard     | f              | computers  |  89.00 | active |       15 | f
 SG-CMP-004 | Sumi-e Mouse Pad XL           | f              | computers  |  12.00 | active |      200 | f
 SG-MON-001 | Fuji 27" 4K Monitor           | f              | monitors   | 349.00 | active |        6 | f

 other_products | other_price_sum | products_without_stock 
----------------+-----------------+------------------------
             62 |        11463.34 |                      0
```

Only the earbuds' product row changed (`product_changed` is `t`). The laptop stand's change was to its stock, so its product row is untouched. SG-CMP-004 is `unchanged` even though the new file says `12.00` instead of `12` and has no stray spaces: after normalisation, the values are equal.

### 4. All or nothing

Make the script fail halfway on purpose. For example, run it with `-v seller_id=99` (a seller that doesn't exist): the first `INSERT INTO products` violates `products_seller_id_fkey`. Check that psql exits with a non-zero status, and that nothing changed: `SELECT count(*) FROM products` is the same as before.

## Reference solution

[`solutions/load_catalog.sql`](solutions/load_catalog.sql), with comments explaining each step. Run it the same way:

```bash
docker exec -i course-pg psql -X -U postgres -d bazaar < modules/03-crud/solutions/load_catalog.sql
```

The canonical small dataset that later modules use comes from [`project/db/0301_seed_small.sql`](../../project/db/0301_seed_small.sql), not from this importer: `reset.sh 05` and later never contain Sakura's products. Your importer is a tool, and the seed is the reference data.

## Going further (optional)

- **Archive what's missing.** Add an option (`-v archive_missing=1`) that sets `status = 'archived'` for the seller's products that aren't in the file. Why archive instead of delete? (Exercise 11 and module 02's foreign keys.)
- **Reject file for the seller.** Write the rejected lines, with their reasons, to a CSV the seller can open: `COPY (SELECT ...) TO '/tmp/rejected.csv' WITH (FORMAT csv, HEADER)`, then `docker cp` it out of the container.
- **Stock below reservations.** What happens if a file sets a product's quantity below its `reserved` count? Try it with a TechTrove product (seller 1, product 3 has 2 reserved). Should the importer reject that line with a clear message instead of aborting the whole load? Implement it.
- **Thousand separators.** Accept `1,299.00` and `1.299,00` as prices. Is it even possible to tell `1,299` (one thousand two hundred ninety-nine) from `1,299` (one point two nine nine)? What would you ask the seller?
