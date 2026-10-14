"""storefront search: search_products() from module 13, and its trigram index

Revision ID: 2801
Revises: 2701
Create Date: 2026-10-14

Module 13's milestone wrote search_products() as a learner exercise
(modules/13-full-text-search/solutions/search_products.sql); project/db never
got it, so the reference database has the search *column* (products.
search_vector, 1301) but not the search *function*. The capstone's
GET /search calls it, so it becomes part of the schema here, as a revision:

* immutable_unaccent(text): an IMMUTABLE wrapper around unaccent() with a
  schema-qualified dictionary, so it may appear in an index (module 13, section 12);
* products_title_unaccent_trgm_idx: GIN trigram index on the unaccented title,
  for the fuzzy fallback ("headphnes", "creme"); built CONCURRENTLY, because
  products is a hot table (about 20 s on the large dataset, writes keep flowing);
* search_products(q, category_id, max_results): websearch syntax, category
  subtree, relevance re-ranked with rating and sales, snippets, trigram fallback.
  Unchanged from module 13, except that it is created by bazaar_owner (env.py's
  SET ROLE) so that module 15's default privileges grant EXECUTE to bazaar_app.

Module 13's search_terms materialized view and autocomplete() are left out: the
service does not use them yet (an exercise of module 28 adds autocomplete).

Downgrade drops the three objects. The function bodies are kept verbatim in
this file: a revision must do the same thing in a year (never import them).
"""

from collections.abc import Sequence

from alembic import op

from bazaar.migrate import create_index_concurrently, drop_index_concurrently, execute_script

revision: str = "2801"
down_revision: str | Sequence[str] | None = "2701"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMMUTABLE_UNACCENT = """
CREATE OR REPLACE FUNCTION immutable_unaccent(text)
RETURNS text
LANGUAGE sql
IMMUTABLE PARALLEL SAFE STRICT
RETURN public.unaccent('public.unaccent'::regdictionary, $1);
"""

SEARCH_PRODUCTS = r"""
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
SET pg_trgm.word_similarity_threshold = 0.5
AS $$
#variable_conflict use_column
DECLARE
    v_tsq   tsquery := websearch_to_tsquery('english', coalesce(q, ''));
    v_plain text    := immutable_unaccent(btrim(coalesce(q, '')));
    v_cats  int[];
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
        IF v_cats IS NULL THEN
            RETURN;
        END IF;
    END IF;

    IF numnode(v_tsq) = 0 THEN
        RETURN;
    END IF;

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

    IF length(v_plain) < 3 THEN
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
    'Storefront search: websearch syntax, category subtree filter, '
    'rank + rating + sales, snippets, trigram fallback.';
"""


def upgrade() -> None:
    execute_script(IMMUTABLE_UNACCENT)
    # The function's SET clause names pg_trgm's setting. Until pg_trgm's library
    # is loaded in this session, the server only knows it as a "placeholder",
    # and a non-superuser (bazaar_owner) may not attach a placeholder to a
    # function: "permission denied to set parameter". Calling any pg_trgm
    # function loads the library and defines the real (user-settable) parameter.
    op.execute("SELECT public.similarity('a', 'b')")
    execute_script(SEARCH_PRODUCTS)
    # Last: it commits the work above (an autocommit block), and is re-runnable.
    create_index_concurrently(
        "products_title_unaccent_trgm_idx",
        "products",
        "immutable_unaccent(title) gin_trgm_ops",
        using="gin",
    )


def downgrade() -> None:
    drop_index_concurrently("products_title_unaccent_trgm_idx")
    op.execute("DROP FUNCTION IF EXISTS search_products(text, int, int)")
    op.execute("DROP FUNCTION IF EXISTS immutable_unaccent(text)")
