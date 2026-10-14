-- =============================================================================
-- load_catalog.sql  --  Module 03 milestone (reference solution)
-- -----------------------------------------------------------------------------
-- Imports a seller's product catalogue from a CSV file into products and
-- inventory, through a staging table with validation:
--
--   * every input line is loaded as text into a temporary staging table;
--   * each line is validated, and every problem is recorded in staging.error;
--   * valid lines are upserted into products (key: seller_id + sku) and their
--     stock is set in inventory;
--   * a report lists the outcome of every line: insert, update, unchanged,
--     or rejected with the reasons.
--
-- The whole load is one transaction: if anything unexpected fails, nothing
-- changes. Running the same file twice changes nothing the second time.
--
-- CSV columns (with a header line):
--   sku, title, description, category_slug, price, quantity, status
--   * description may be empty (stored as NULL);
--   * category_slug must be an existing categories.slug;
--   * price is a decimal with a point, at most 2 decimals, >= 0;
--   * quantity is the absolute stock on hand, a whole number >= 0;
--   * status is draft, active or archived; empty means draft.
--
-- Usage (server-side COPY, so the file path is inside the container):
--   docker exec -i course-pg psql -X -U postgres -d bazaar \
--       -v seller_id=6 -v file=/course/modules/03-crud/data/sakura_catalog.csv \
--       < modules/03-crud/solutions/load_catalog.sql
-- Both variables are optional; the defaults load Sakura Gadgets' catalogue.
-- =============================================================================
\set ON_ERROR_STOP on
\set QUIET on
\pset footer off
\pset null ''

\if :{?seller_id}
\else
    \set seller_id 6
\endif
\if :{?file}
\else
    \set file /course/modules/03-crud/data/sakura_catalog.csv
\endif

SET search_path = bazaar, public;

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Staging: raw text columns accept any line; the typed columns are filled in
--    only for lines that pass validation. row_no numbers the data lines in file
--    order (COPY inserts rows in file order). ON COMMIT DROP cleans up.
-- -----------------------------------------------------------------------------
CREATE TEMP TABLE catalog_staging (
    row_no        int GENERATED ALWAYS AS IDENTITY,
    -- raw input
    sku           text,
    title         text,
    description   text,
    category_slug text,
    price         text,
    quantity      text,
    status        text,
    -- derived, filled in for valid lines
    category_id   int,
    price_num     numeric(12,2),
    quantity_int  int,
    status_clean  text,
    product_id    bigint,
    action        text,     -- insert / update / unchanged
    error         text      -- NULL = valid; otherwise '; '-separated reasons
) ON COMMIT DROP;

COPY catalog_staging (sku, title, description, category_slug, price, quantity, status)
FROM :'file'
WITH (FORMAT csv, HEADER);

-- -----------------------------------------------------------------------------
-- 2. Normalise: trim whitespace, turn empty strings into NULL.
-- -----------------------------------------------------------------------------
UPDATE catalog_staging
SET sku           = nullif(trim(sku), ''),
    title         = nullif(trim(title), ''),
    description   = nullif(trim(description), ''),
    category_slug = nullif(lower(trim(category_slug)), ''),
    price         = nullif(trim(price), ''),
    quantity      = nullif(trim(quantity), ''),
    status        = nullif(lower(trim(status)), '');

-- -----------------------------------------------------------------------------
-- 3. Validate. Each rule appends its message, so a line reports every problem.
--    concat_ws skips NULLs: concat_ws('; ', NULL, 'x') = 'x'.
-- -----------------------------------------------------------------------------
UPDATE catalog_staging SET error = concat_ws('; ', error, 'missing sku')
WHERE sku IS NULL;

UPDATE catalog_staging SET error = concat_ws('; ', error, 'missing title')
WHERE title IS NULL;

-- Price: must convert to numeric(12,2) and be >= 0. pg_input_is_valid tests the
-- conversion without raising an error. CASE guarantees the cast only runs for
-- valid input (AND does not promise an evaluation order).
UPDATE catalog_staging
SET error = concat_ws('; ', error, format('invalid price %L', price))
WHERE CASE
          WHEN price IS NULL THEN true
          WHEN NOT pg_input_is_valid(price, 'numeric(12,2)') THEN true
          ELSE price::numeric(12,2) < 0
      END;

UPDATE catalog_staging
SET error = concat_ws('; ', error, format('invalid quantity %L', quantity))
WHERE CASE
          WHEN quantity IS NULL THEN true
          WHEN NOT pg_input_is_valid(quantity, 'integer') THEN true
          ELSE quantity::int < 0
      END;

UPDATE catalog_staging
SET error = concat_ws('; ', error, format('invalid status %L', status))
WHERE status IS NOT NULL
  AND status NOT IN ('draft', 'active', 'archived');

-- Category: look up the id by slug (UPDATE ... FROM, see the theory, 10.2).
UPDATE catalog_staging AS s
SET category_id = c.id
FROM categories AS c
WHERE c.slug = s.category_slug;

UPDATE catalog_staging
SET error = concat_ws('; ', error, format('unknown category %L', category_slug))
WHERE category_id IS NULL;

-- Duplicate SKUs within the file: ambiguous, so reject every copy. Also
-- protects the upsert from "cannot affect row a second time".
UPDATE catalog_staging AS s
SET error = concat_ws('; ', s.error, 'duplicate sku in file')
FROM catalog_staging AS other
WHERE other.sku = s.sku
  AND other.row_no <> s.row_no;

-- Typed values for the valid lines.
UPDATE catalog_staging
SET price_num    = price::numeric(12,2),
    quantity_int = quantity::int,
    status_clean = coalesce(status, 'draft')
WHERE error IS NULL;

-- -----------------------------------------------------------------------------
-- 4. Classify valid lines against what is in the database now.
-- -----------------------------------------------------------------------------
UPDATE catalog_staging AS s
SET product_id = p.id,
    action = CASE
                 WHEN (p.category_id, p.title, p.description, p.price, p.status)
                      IS DISTINCT FROM
                      (s.category_id, s.title, s.description, s.price_num, s.status_clean)
                 THEN 'update'
                 ELSE 'unchanged'
             END
FROM products AS p
WHERE p.seller_id = :seller_id
  AND p.sku = s.sku
  AND s.error IS NULL;

-- A product whose only change is its stock level counts as an update too.
UPDATE catalog_staging AS s
SET action = 'update'
FROM inventory AS i
WHERE i.product_id = s.product_id
  AND s.action = 'unchanged'
  AND i.quantity IS DISTINCT FROM s.quantity_int;

UPDATE catalog_staging
SET action = 'insert'
WHERE error IS NULL
  AND action IS NULL;

-- -----------------------------------------------------------------------------
-- 5. Upsert products. The WHERE on DO UPDATE skips rows that did not change,
--    so updated_at keeps meaning "last real change" and a re-run is a no-op.
-- -----------------------------------------------------------------------------
INSERT INTO products (seller_id, category_id, sku, title, description, price, status)
SELECT :seller_id, category_id, sku, title, description, price_num, status_clean
FROM catalog_staging
WHERE error IS NULL
ORDER BY row_no
ON CONFLICT (seller_id, sku) DO UPDATE
SET category_id = EXCLUDED.category_id,
    title       = EXCLUDED.title,
    description = EXCLUDED.description,
    price       = EXCLUDED.price,
    status      = EXCLUDED.status,
    updated_at  = now()
WHERE (products.category_id, products.title, products.description, products.price, products.status)
      IS DISTINCT FROM
      (EXCLUDED.category_id, EXCLUDED.title, EXCLUDED.description, EXCLUDED.price, EXCLUDED.status);

-- Ids of the newly inserted products.
UPDATE catalog_staging AS s
SET product_id = p.id
FROM products AS p
WHERE p.seller_id = :seller_id
  AND p.sku = s.sku
  AND s.error IS NULL
  AND s.product_id IS NULL;

-- -----------------------------------------------------------------------------
-- 6. Upsert inventory: the file states the absolute stock on hand.
--    (If the new quantity were below `reserved`, the CHECK constraint
--    inventory_reserved_within_quantity would abort the whole load.)
-- -----------------------------------------------------------------------------
INSERT INTO inventory (product_id, quantity)
SELECT product_id, quantity_int
FROM catalog_staging
WHERE error IS NULL
ON CONFLICT (product_id) DO UPDATE
SET quantity   = EXCLUDED.quantity,
    updated_at = now()
WHERE inventory.quantity IS DISTINCT FROM EXCLUDED.quantity;

-- -----------------------------------------------------------------------------
-- 7. Report.
-- -----------------------------------------------------------------------------
\echo
\echo 'Catalogue load for seller' :seller_id 'from' :file
\echo

SELECT row_no + 1 AS line,     -- +1: line 1 of the file is the header
       sku,
       coalesce(action, 'rejected') AS outcome,
       product_id
FROM catalog_staging
ORDER BY row_no;

\echo 'Rejected lines:'
SELECT row_no + 1 AS line, sku, title, error
FROM catalog_staging
WHERE error IS NOT NULL
ORDER BY row_no;

SELECT (SELECT count(*) FROM catalog_staging)                              AS lines,
       (SELECT count(*) FROM catalog_staging WHERE action = 'insert')     AS inserted,
       (SELECT count(*) FROM catalog_staging WHERE action = 'update')     AS updated,
       (SELECT count(*) FROM catalog_staging WHERE action = 'unchanged')  AS unchanged,
       (SELECT count(*) FROM catalog_staging WHERE error IS NOT NULL)     AS rejected;

COMMIT;
