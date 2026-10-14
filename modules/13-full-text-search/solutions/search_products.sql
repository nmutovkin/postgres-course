-- =============================================================================
-- Module 13 milestone: Bazaar product search  (reference solution)
-- -----------------------------------------------------------------------------
-- Applies on top of ./project/bin/reset.sh 14 (the state that already has
-- project/db/1301_schema_search.sql: products.search_vector, its GIN index,
-- pg_trgm and unaccent).
--
--   docker exec -i course-pg psql -U postgres -d bazaar -v ON_ERROR_STOP=1 \
--       < modules/13-full-text-search/solutions/search_products.sql
--
-- Objects:
--   1. immutable_unaccent(text)            IMMUTABLE wrapper, usable in indexes
--   2. products_title_unaccent_trgm_idx    trigram index for the fuzzy fallback
--                                          and for multi-word autocomplete
--   3. search_terms                        materialized view: the vocabulary of
--                                          active product titles (autocomplete)
--   4. search_products(q, category_id, max_results)
--   5. autocomplete(prefix, max_results)
-- Idempotent: safe to run twice.
-- =============================================================================

SET search_path = bazaar, public;

-- -----------------------------------------------------------------------------
-- 1. unaccent() is only STABLE (its result depends on which "unaccent"
--    dictionary search_path finds), so it cannot appear in an index. The
--    two-argument form with a schema-qualified dictionary has no such
--    dependency, so we may promise IMMUTABLE for this wrapper.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION immutable_unaccent(text)
RETURNS text
LANGUAGE sql
IMMUTABLE PARALLEL SAFE STRICT
RETURN public.unaccent('public.unaccent'::regdictionary, $1);

-- -----------------------------------------------------------------------------
-- 2. Accent-insensitive trigram index on titles. pg_trgm lower-cases by
--    itself, so no lower() is needed.
-- -----------------------------------------------------------------------------
SET maintenance_work_mem = '512MB';
CREATE INDEX IF NOT EXISTS products_title_unaccent_trgm_idx
    ON products USING gin (immutable_unaccent(title) gin_trgm_ops);
RESET maintenance_work_mem;

-- -----------------------------------------------------------------------------
-- 3. The vocabulary for single-word autocomplete: every word of an active
--    product title (unaccented, not stemmed), with the number of products it
--    appears in. About 1,400 rows, so any prefix is a tiny index range scan.
--    COLLATE "C" makes the unique index usable for LIKE 'abc%' (byte order),
--    and the unique index allows REFRESH MATERIALIZED VIEW CONCURRENTLY.
-- -----------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS search_terms;
CREATE MATERIALIZED VIEW search_terms AS
SELECT word COLLATE "C" AS term,
       ndoc::bigint     AS products
FROM ts_stat($$
    SELECT to_tsvector('simple', bazaar.immutable_unaccent(title))
    FROM bazaar.products
    WHERE status = 'active'
$$)
WHERE length(word) >= 2
  AND word !~ '^[0-9]+$';

CREATE UNIQUE INDEX search_terms_term_key ON search_terms (term);

-- -----------------------------------------------------------------------------
-- 4. search_products
--    * websearch_to_tsquery: user input can never cause a syntax error;
--    * filters: active products, optionally a whole category subtree;
--    * two-phase ranking: the 200 best candidates by text relevance are
--      re-ranked with business signals (Bayesian-average rating, units sold);
--    * ts_headline only for the rows actually returned;
--    * if full-text search finds nothing, a trigram fallback on unaccented
--      titles tolerates typos ("headphnes") and missing accents ("creme").
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION search_products(
    q            text,
    category_id  int DEFAULT NULL,
    max_results  int DEFAULT 20
)
RETURNS TABLE (
    product_id     bigint,
    title          text,
    price          numeric(12,2),
    category_name  text,
    snippet        text,
    score          numeric,
    matched_by     text          -- 'fts' or 'fuzzy'
)
LANGUAGE plpgsql
STABLE
-- The fallback uses the <% operator, whose cut-off is this setting
-- (default 0.6). A SET clause applies it for the duration of each call only.
SET pg_trgm.word_similarity_threshold = 0.5
AS $$
#variable_conflict use_column
DECLARE
    v_tsq   tsquery := websearch_to_tsquery('english', coalesce(q, ''));
    v_plain text    := immutable_unaccent(btrim(coalesce(q, '')));
    v_cats  int[];                                   -- NULL = no category filter
BEGIN
    IF v_plain = '' OR max_results <= 0 THEN
        RETURN;
    END IF;

    IF search_products.category_id IS NOT NULL THEN
        WITH RECURSIVE subtree AS (
            SELECT c.id FROM categories c WHERE c.id = search_products.category_id
            UNION ALL
            SELECT c.id FROM categories c JOIN subtree s ON c.parent_id = s.id
        )
        SELECT array_agg(id) INTO v_cats FROM subtree;
        IF v_cats IS NULL THEN                       -- unknown category: no results
            RETURN;
        END IF;
    END IF;

    -- Only stop words or punctuation ("the", "???"): nothing to search for.
    -- (Fuzzy-matching "the" against every title would be slow and useless.)
    IF numnode(v_tsq) = 0 THEN
        RETURN;
    END IF;

    -- ---------------------------------------------------------------- FTS ---
    RETURN QUERY
    WITH candidates AS (
        SELECT p.id, p.title, p.description, p.price, p.category_id,
               ts_rank_cd(p.search_vector, v_tsq, 32) AS relevance
        FROM products p
        WHERE p.search_vector @@ v_tsq
          AND p.status = 'active'
          AND (v_cats IS NULL OR p.category_id = ANY (v_cats))
        ORDER BY relevance DESC, p.id
        LIMIT 200
    ), scored AS (
        SELECT c.*,
               0.60 * (c.relevance / max(c.relevance) OVER ())
             + 0.25 * ((r.bayes_rating - 1) / 4)
             + 0.15 * least(1, ln(1 + s.units) / ln(501))  AS score
        FROM candidates c
        CROSS JOIN LATERAL (
            -- Bayesian average: 5 virtual reviews at the global mean 4.09,
            -- so one 5-star review does not beat 200 reviews at 4.8.
            SELECT (5 * 4.09 + coalesce(sum(rv.rating), 0)) / (5 + count(*)) AS bayes_rating
            FROM reviews rv
            WHERE rv.product_id = c.id
        ) r
        CROSS JOIN LATERAL (
            SELECT coalesce(sum(oi.quantity), 0) AS units
            FROM order_items oi
            WHERE oi.product_id = c.id
        ) s
        ORDER BY score DESC, c.id
        LIMIT max_results
    )
    SELECT sc.id,
           sc.title,
           sc.price,
           cat.name,
           ts_headline('english', coalesce(sc.description, sc.title), v_tsq,
                       'StartSel=<mark>, StopSel=</mark>, MaxWords=18, MinWords=8'),
           round(sc.score::numeric, 4),
           'fts'
    FROM scored sc
    JOIN categories cat ON cat.id = sc.category_id
    ORDER BY sc.score DESC, sc.id;

    IF FOUND THEN
        RETURN;
    END IF;

    -- ------------------------------------------------------ fuzzy fallback ---
    IF length(v_plain) < 3 THEN                      -- too short to be fuzzy about
        RETURN;
    END IF;

    RETURN QUERY
    WITH candidates AS (
        SELECT p.id, p.title, p.description, p.price, p.category_id,
               word_similarity(v_plain, immutable_unaccent(p.title)) AS relevance
        FROM products p
        WHERE v_plain <% immutable_unaccent(p.title)
          AND p.status = 'active'
          AND (v_cats IS NULL OR p.category_id = ANY (v_cats))
        ORDER BY relevance DESC, p.id
        LIMIT 200
    ), scored AS (
        SELECT c.*,
               0.60 * (c.relevance / max(c.relevance) OVER ())
             + 0.25 * ((r.bayes_rating - 1) / 4)
             + 0.15 * least(1, ln(1 + s.units) / ln(501))  AS score
        FROM candidates c
        CROSS JOIN LATERAL (
            SELECT (5 * 4.09 + coalesce(sum(rv.rating), 0)) / (5 + count(*)) AS bayes_rating
            FROM reviews rv
            WHERE rv.product_id = c.id
        ) r
        CROSS JOIN LATERAL (
            SELECT coalesce(sum(oi.quantity), 0) AS units
            FROM order_items oi
            WHERE oi.product_id = c.id
        ) s
        ORDER BY score DESC, c.id
        LIMIT max_results
    )
    SELECT sc.id,
           sc.title,
           sc.price,
           cat.name,
           left(coalesce(sc.description, sc.title), 120),
           round(sc.score::numeric, 4),
           'fuzzy'
    FROM scored sc
    JOIN categories cat ON cat.id = sc.category_id
    ORDER BY sc.score DESC, sc.id;
END;
$$;

COMMENT ON FUNCTION search_products(text, int, int) IS
    'Storefront search: websearch syntax, category subtree filter, rank + rating + sales, snippets, trigram fallback.';

-- -----------------------------------------------------------------------------
-- 5. autocomplete
--    'hea'        -> the most common title words starting with "hea"
--                    (search_terms; instant for any prefix length)
--    'voltix wi'  -> completions of the last word as they actually follow the
--                    typed words in active titles ("voltix wireless"), counted;
--                    the typed words make the trigram index selective.
--    Accent- and case-insensitive. Fewer than 2 characters: no suggestions.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION autocomplete(prefix text, max_results int DEFAULT 8)
RETURNS TABLE (suggestion text, products bigint)
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_prefix text := lower(immutable_unaccent(regexp_replace(btrim(coalesce(prefix, '')), '\s+', ' ', 'g')));
    v_head   text;      -- the words already typed, e.g. 'voltix'
    v_last   text;      -- the word being typed,    e.g. 'wi'
    v_regex  text;
BEGIN
    IF length(v_prefix) < 2 THEN
        RETURN;
    END IF;

    v_last := substring(v_prefix FROM '[^ ]*$');
    v_head := rtrim(left(v_prefix, length(v_prefix) - length(v_last)));

    IF v_head = '' THEN
        -- Single word: complete it from the vocabulary.
        RETURN QUERY
        SELECT t.term::text, t.products
        FROM search_terms t
        WHERE t.term LIKE replace(replace(replace(v_last, '\', '\\'), '%', '\%'), '_', '\_') || '%'
        ORDER BY t.products DESC, t.term
        LIMIT max_results;
        RETURN;
    END IF;

    -- The phrase must contain at least one trigram's worth of letters or
    -- digits, otherwise the trigram index cannot help and we would scan
    -- every title (try 'c++ (').
    IF length(regexp_replace(v_prefix, '[^[:alnum:]]', '', 'g')) < 3 THEN
        RETURN;
    END IF;

    -- Several words: find titles containing the typed phrase at a word start,
    -- and capture the word that completes the last, partial one.
    -- Regex metacharacters in the input are escaped.
    v_regex := '\m' || regexp_replace(v_prefix, '([.^$*+?()\[\]{}|\\])', '\\\1', 'g') || '\w*';

    RETURN QUERY
    SELECT v_head || ' ' || lower(substring(m.hit FROM '[^ ]*$')) AS suggestion,
           count(*)
    FROM products p
    CROSS JOIN LATERAL (SELECT substring(immutable_unaccent(p.title) FROM '(?i)' || v_regex) AS hit) m
    WHERE immutable_unaccent(p.title) ~* v_regex
      AND p.status = 'active'
    GROUP BY 1
    ORDER BY 2 DESC, 1
    LIMIT max_results;
END;
$$;

COMMENT ON FUNCTION autocomplete(text, int) IS
    'Search-box suggestions: completes the last word (vocabulary for one word, phrase-prefix match on titles for several).';

ANALYZE products;
