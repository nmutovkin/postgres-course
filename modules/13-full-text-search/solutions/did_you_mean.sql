-- Exercise 10: did_you_mean(q) -- correct each unknown word of a search query.
-- Needs the milestone (search_products.sql) for immutable_unaccent().
SET search_path = bazaar, public;

-- The vocabulary of everything that is searchable (title, description, tags)
-- of active products: unstemmed ('simple'), unaccented, with document counts.
DROP MATERIALIZED VIEW IF EXISTS search_vocabulary;
CREATE MATERIALIZED VIEW search_vocabulary AS
SELECT word, ndoc
FROM ts_stat($$
    SELECT to_tsvector('simple', bazaar.immutable_unaccent(
               title || ' ' || coalesce(description, '') || ' ' || bazaar.tags_to_text(tags)))
    FROM bazaar.products
    WHERE status = 'active'
$$);

CREATE UNIQUE INDEX search_vocabulary_word_key ON search_vocabulary (word);
CREATE INDEX search_vocabulary_word_trgm_idx ON search_vocabulary USING gin (word gin_trgm_ops);

CREATE OR REPLACE FUNCTION did_you_mean(q text)
RETURNS text                    -- NULL when there is nothing to correct
LANGUAGE sql
STABLE
AS $$
    WITH words AS (
        SELECT w.word, w.n
        FROM regexp_split_to_table(lower(immutable_unaccent(btrim(q))), '\s+')
             WITH ORDINALITY AS w (word, n)
        WHERE w.word <> ''
    ), fixed AS (
        SELECT w.n,
               w.word,
               CASE
                   WHEN length(w.word) < 3                                   -- too short to judge
                     OR EXISTS (SELECT 1 FROM search_vocabulary v WHERE v.word = w.word)
                   THEN w.word
                   ELSE coalesce(
                       (SELECT v.word
                        FROM search_vocabulary v
                        WHERE v.word % w.word                                -- indexable
                        ORDER BY similarity(v.word, w.word) DESC, v.ndoc DESC, v.word
                        LIMIT 1),
                       w.word)                                               -- no candidate: keep it
               END AS fixed
        FROM words w
    )
    SELECT CASE WHEN bool_and(word = fixed) THEN NULL
                ELSE string_agg(fixed, ' ' ORDER BY n)
           END
    FROM fixed;
$$;
