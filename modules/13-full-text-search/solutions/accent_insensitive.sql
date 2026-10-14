-- Exercise 8: accent-insensitive full-text search, tried out in a transaction
-- that is rolled back (nothing changes permanently).
SET search_path = bazaar, public;
\timing on

BEGIN;

CREATE TEXT SEARCH CONFIGURATION bazaar_english (COPY = english);
ALTER TEXT SEARCH CONFIGURATION bazaar_english
    ALTER MAPPING FOR asciiword, word, hword, hword_part, asciihword, hword_asciipart
    WITH unaccent, english_stem;

-- A generated expression cannot be altered in place (PG 17): drop and re-add.
-- Dropping the column also drops products_search_vector_idx.
ALTER TABLE products DROP COLUMN search_vector;
ALTER TABLE products
    ADD COLUMN search_vector tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('bazaar_english', coalesce(title, '')), 'A')
        || setweight(to_tsvector('bazaar_english', coalesce(description, '')), 'B')
        || setweight(to_tsvector('bazaar_english', tags_to_text(tags)), 'C')
    ) STORED;
SET LOCAL maintenance_work_mem = '512MB';
CREATE INDEX products_search_vector_idx ON products USING gin (search_vector);

SELECT q,
       websearch_to_tsquery('bazaar_english', q) AS tsq,
       (SELECT count(*) FROM products
        WHERE search_vector @@ websearch_to_tsquery('bazaar_english', q)) AS matches
FROM unnest(ARRAY['creme', 'crème', 'CRÈME', 'lumiere', 'saute pan', 'cafe']) AS q;

ROLLBACK;
