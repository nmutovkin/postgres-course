-- Exercise 7: query-side synonyms for search_products().
-- Needs the milestone (search_products.sql). Re-creates search_products() with
-- ONE changed line (v_tsq := expand_query(q)); everything else is identical.
SET search_path = bazaar, public;

CREATE TABLE IF NOT EXISTS search_aliases (
    target      tsquery PRIMARY KEY,
    substitute  tsquery NOT NULL
);

INSERT INTO search_aliases (target, substitute) VALUES
    (websearch_to_tsquery('english', 'sci-fi'),  websearch_to_tsquery('english', '"science fiction"')),
    (to_tsquery('english', 'novel'),             to_tsquery('english', 'novel | book')),
    (to_tsquery('english', 'knives'),            to_tsquery('english', 'knives | knife')),
    (to_tsquery('english', 'earphones'),         to_tsquery('english', 'earphones | earbuds | headphones'))
ON CONFLICT (target) DO UPDATE SET substitute = EXCLUDED.substitute;

-- STABLE, not IMMUTABLE: the result depends on the contents of a table.
CREATE OR REPLACE FUNCTION expand_query(q text)
RETURNS tsquery
LANGUAGE sql
STABLE
RETURN ts_rewrite(websearch_to_tsquery('english', coalesce(q, '')),
                  'SELECT target, substitute FROM bazaar.search_aliases');

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
    v_tsq   tsquery := expand_query(q);          -- was: websearch_to_tsquery('english', ...)
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

