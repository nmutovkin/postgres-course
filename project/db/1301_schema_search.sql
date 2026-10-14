-- =============================================================================
-- 1301_schema_search.sql  --  Module 13: Full-Text and Fuzzy Search
-- -----------------------------------------------------------------------------
-- 1. Extensions: pg_trgm (trigram similarity, fast LIKE/ILIKE '%...%') and
--    unaccent (strip diacritics: 'Crème' -> 'Creme').
-- 2. products.search_vector: a STORED generated tsvector combining
--      title        weight A (most important)
--      description  weight B
--      tags         weight C
--    Generated columns are recomputed by Postgres on every INSERT/UPDATE, so
--    they can never go stale (no trigger needed), and STORED means the work is
--    done once at write time, not at every search.
-- 3. A GIN index on search_vector for  search_vector @@ to_tsquery(...)
-- 4. A GIN trigram index on title for fuzzy matching / autocomplete:
--      WHERE title ILIKE '%headphon%'   or   WHERE title % 'hedphones'
-- =============================================================================

SET search_path = bazaar, public;
SET maintenance_work_mem = '512MB';

CREATE EXTENSION IF NOT EXISTS pg_trgm  WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA public;

-- A generated column may only call IMMUTABLE functions (same input => same
-- output forever). to_tsvector('english', text) qualifies because the
-- configuration is a constant; the one-argument to_tsvector(text) does not
-- (it depends on the default_text_search_config setting).
-- array_to_string() is only STABLE, because for arbitrary element types it
-- calls their output functions, which may depend on settings (think DateStyle
-- for date[]). For text[] the result depends on nothing but the input, so it
-- is safe to wrap it in a function we declare IMMUTABLE ourselves.
-- (Declaring a function IMMUTABLE is a promise; if you break it, indexes and
-- generated columns built on it silently become wrong.)
CREATE FUNCTION tags_to_text(tags text[])
RETURNS text
LANGUAGE sql
IMMUTABLE PARALLEL SAFE
RETURN array_to_string(tags, ' ');

-- Adding a STORED generated column rewrites the table (every row gets a value).
ALTER TABLE products
    ADD COLUMN search_vector tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A')
        || setweight(to_tsvector('english', coalesce(description, '')), 'B')
        || setweight(to_tsvector('english', tags_to_text(tags)), 'C')
    ) STORED;

-- Full-text search:
--   SELECT id, title, ts_rank(search_vector, q) AS rank
--   FROM products, websearch_to_tsquery('english', 'wireless headphones') AS q
--   WHERE search_vector @@ q
--   ORDER BY rank DESC LIMIT 20;
CREATE INDEX products_search_vector_idx ON products USING gin (search_vector);

-- Fuzzy search / typo tolerance / substring search on titles.
CREATE INDEX products_title_trgm_idx ON products USING gin (title gin_trgm_ops);

RESET maintenance_work_mem;

ANALYZE products;
