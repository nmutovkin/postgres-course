# Module 13 project: Bazaar product search

<!-- nav:start -->
> [Theory](README.md) · [Exercises](exercises.md) · **Project** · [Solutions](solutions/README.md)  
> [← 12 Query Planning and Performance](../12-query-performance/README.md) · [Course home](../../README.md) · [14 Partitioning →](../14-partitioning/README.md)
<!-- nav:end -->

Bazaar's storefront search is `title ILIKE '%...%'`. It misses plurals, can't rank, ignores ratings and sales, shows nothing for a typo, and reads the whole products table on every keystroke. In this milestone you replace it with two database functions that the web application (and, in [Module 28](../28-capstone/), the FastAPI service) calls directly:

- `search_products(q, category_id, max_results)` for the results page;
- `autocomplete(prefix, max_results)` for the suggestions under the search box.

**Starting state:** `./project/bin/reset.sh 13`.

<!-- toc:start -->
**Contents**

- [Part 1: the search document](#part-1-the-search-document)
- [Part 2: `search_products`](#part-2-search_products)
- [Part 3: `autocomplete`](#part-3-autocomplete)
- [Acceptance checks](#acceptance-checks)
- [Reference solution](#reference-solution)

<!-- toc:end -->

## Part 1: the search document

Write your own version of [`project/db/1301_schema_search.sql`](../../project/db/1301_schema_search.sql):

1. Install `pg_trgm` and `unaccent` (in schema `public`).
2. Add `products.search_vector`, a **stored generated** `tsvector` with the `english` configuration: the title with weight A, the description with weight B, and the tags with weight C. Products with a NULL description must still be found by their title. (You will need an immutable way to turn `tags text[]` into text: see section 9.1 of the theory.)
3. A GIN index on `search_vector`, and a GIN trigram index on `title`.

## Part 2: `search_products`

```sql
search_products(q text, category_id int DEFAULT NULL, max_results int DEFAULT 20)
RETURNS TABLE (
    product_id     bigint,
    title          text,
    price          numeric(12,2),
    category_name  text,
    snippet        text,
    score          numeric,
    matched_by     text      -- 'fts' or 'fuzzy'
)
```

(The third parameter is called `max_results` rather than `limit`, because `LIMIT` is a reserved word.)

Requirements:

1. **Input**: `q` is whatever the shopper typed. It is parsed with `websearch_to_tsquery('english', q)`, so quotes, `or` and `-word` work and no input can raise an error. Empty input, NULL, and queries made only of stop words (`the`) return no rows.
2. **Filters**: only active products. When `category_id` is given, only products in that category **or any of its descendants** (Electronics, id 1, includes Laptops, Headphones, and so on). An unknown category returns no rows.
3. **Ranking**: a score between 0 and 1 that combines
   - text relevance (use `ts_rank_cd`, normalised so the best candidate gets 1),
   - the product's rating as a **Bayesian average** (so one 5-star review doesn't beat hundreds of 4.8s; Bazaar's global mean is 4.09),
   - popularity, from units sold, dampened with a logarithm.

   Text relevance must dominate. Choose the weights and justify them in a comment. Compute the expensive signals only for a bounded set of candidates (for example the best 200 by text relevance).
4. **Snippets**: `ts_headline` of the description (or the title if there is no description), with `<mark>`…`</mark>` around the matched words, computed **only for the rows returned**.
5. **Typo tolerance**: when full-text search finds nothing, fall back to trigram matching of the query against the **unaccented** titles (so `headphnes` and `creme` both work), with the same filters and the same kind of score, and `matched_by = 'fuzzy'`. The fallback must use an index (create the immutable `unaccent` wrapper and an index for it).
6. **Speed**: a typical query (`wireless headphones`) runs in a few milliseconds once the cache is warm; a broad query (`gift`, about 33,000 matches) stays well under 200 ms.

## Part 3: `autocomplete`

```sql
autocomplete(prefix text, max_results int DEFAULT 8)
RETURNS TABLE (suggestion text, products bigint)
```

- Suggests completions of what the shopper is typing, with the number of active products behind each suggestion, most products first.
- Case- and accent-insensitive: `CRÈME DE L` and `creme de l` give the same suggestions.
- With one word (`hea`), it completes the word. With several words (`voltix wi`), it completes the last word *as it actually follows the previous ones in titles* (`voltix wireless`).
- Fewer than 2 characters: no suggestions. No input can raise an error or cause a full scan of `products`.
- It must answer in a few milliseconds even for a two-letter prefix. (Section 13 of the theory explains why that rules out the obvious queries against `products` for very short prefixes.)

## Acceptance checks

Run these after applying your files. Expected results are from the reference solution; your scores and the exact order may differ slightly if you chose different weights, but every property marked "must" has to hold.

```sql
-- 1. A normal query: 20 full-text results with highlighted snippets.
SELECT count(*), min(matched_by), max(matched_by),
       count(*) FILTER (WHERE snippet LIKE '%<mark>%') AS highlighted
FROM search_products('wireless headphones');
--  must: 20 | fts | fts | 20

SELECT product_id, title FROM search_products('wireless headphones') LIMIT 1;
--  reference: 110124 | Orbitron Wireless Headphones 251

-- 2. Typos and missing accents fall back to fuzzy matching.
SELECT count(*), min(matched_by), max(matched_by), min(category_name), max(category_name)
FROM search_products('headphnes');
--  must: 20 | fuzzy | fuzzy | Headphones | Headphones

SELECT count(*), min(matched_by), bool_and(title LIKE 'Crème de Lys%') FROM search_products('creme');
--  must: 20 | fuzzy | t

-- 3. The category filter includes the whole subtree, and nothing outside it.
WITH RECURSIVE electronics AS (
    SELECT id FROM categories WHERE id = 1
    UNION ALL
    SELECT c.id FROM categories c JOIN electronics e ON c.parent_id = e.id
)
SELECT count(*) AS n,
       count(*) FILTER (WHERE p.category_id NOT IN (SELECT id FROM electronics)) AS outside
FROM search_products('wireless', 1, 50) r
JOIN products p ON p.id = r.product_id;
--  must: 50 | 0

-- 4. Exclusions work, and only active products come back.
SELECT count(*) AS n,
       count(*) FILTER (WHERE p.search_vector @@ to_tsquery('english', 'wireless')) AS with_wireless,
       count(*) FILTER (WHERE p.status <> 'active') AS inactive
FROM search_products('headphones -wireless', max_results => 100) r
JOIN products p ON p.id = r.product_id;
--  must: 100 | 0 | 0

-- 5. Edge cases: no rows, no errors.
SELECT count(*) FROM search_products('the');                       -- must: 0
SELECT count(*) FROM search_products('');                          -- must: 0
SELECT count(*) FROM search_products(NULL);                        -- must: 0
SELECT count(*) FROM search_products('wireless headphones', 14);   -- must: 0 (Books)
SELECT count(*) FROM search_products('wireless headphones', 9999); -- must: 0 (no such category)
SELECT count(*) FROM search_products('rock & roll | !((');         -- must not raise an error

-- 6. Autocomplete.
SELECT * FROM autocomplete('hea');
--  reference: 7 rows, hearth 3004, heavy 2565, heavy-duty 2565, heavyweight 2333,
--             headphones 1549, headset 752, heart 308
SELECT * FROM autocomplete('h');            -- must: 0 rows
SELECT * FROM autocomplete('voltix wi');    -- must: voltix wireless | 327
SELECT * FROM autocomplete('CRÈME DE L');   -- must: creme de lys | 1201
```

Then time the two functions with `\timing` (run each twice, and look at the second run). Reference timings on a 4-vCPU container: `search_products('wireless headphones')` 10 ms, `search_products('headphnes')` (fuzzy) about 65 ms, `search_products('gift')` about 120 ms, `autocomplete('hea')` 2 ms, `autocomplete('voltix wi')` 10 ms.

## Reference solution

- [`project/db/1301_schema_search.sql`](../../project/db/1301_schema_search.sql): Part 1 (the column, the GIN index on it, and the trigram index on titles).
- [`solutions/search_products.sql`](solutions/search_products.sql): Parts 2 and 3. It adds `immutable_unaccent()`, the index `products_title_unaccent_trgm_idx` on `immutable_unaccent(title)`, the materialized view `search_terms` (the vocabulary of active titles, for single-word autocomplete), `search_products()` and `autocomplete()`. Apply it on top of `reset.sh 14`. These objects are not part of `project/db`, so later modules' reset states don't contain them; the capstone in Module 28 re-creates its own search.

Design choices worth reading in the reference file:

- `search_products` is PL/pgSQL, because the fallback depends on whether the first query found anything (`IF FOUND`). The function-level `SET pg_trgm.word_similarity_threshold = 0.5` applies the fallback's threshold only during the call. `#variable_conflict use_column` resolves the clash between the output column `title` and the table column `title`.
- Scores: `0.60 × relevance + 0.25 × rating + 0.15 × popularity`, each in 0–1. Relevance is `ts_rank_cd(..., 32)` divided by the best candidate's, so the top text match always gets the full 0.60; rating is the Bayesian average with 5 virtual reviews at 4.09, mapped from 1–5 to 0–1; popularity is `ln(1 + units) / ln(501)`, capped at 1.
- The candidate pool is 200. For `gift`, ranking 33,715 matches is most of the 120 ms; the rating and sales lookups for 200 candidates take a few milliseconds through the `reviews` and `order_items` indexes.
- `autocomplete` uses the small `search_terms` vocabulary for one word (a prefix range scan on a `COLLATE "C"` index, instant for any prefix length), and a trigram-indexed regular expression on unaccented titles for several words. For several words, it refuses input with fewer than three letters or digits in total, so the regular expression always has a trigram the index can use. Refresh `search_terms` when the catalog changes: `REFRESH MATERIALIZED VIEW CONCURRENTLY search_terms;`.
