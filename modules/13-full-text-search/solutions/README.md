# Module 13: Solutions

All solutions were run on PostgreSQL 17 against `./project/bin/reset.sh 14` (the large dataset plus `1301_schema_search.sql`). Exercises 7, 9 and 10 also use the milestone, applied with [`search_products.sql`](search_products.sql). Timings come from a 4-vCPU container with a warm cache; yours will differ, the counts won't.

Runnable files in this directory:

| File | What |
|---|---|
| [`search_products.sql`](search_products.sql) | The milestone: `immutable_unaccent`, the unaccented trigram index, `search_terms`, `search_products()`, `autocomplete()` |
| [`aliases.sql`](aliases.sql) | Exercise 7 |
| [`accent_insensitive.sql`](accent_insensitive.sql) | Exercise 8 (runs in a transaction that is rolled back) |
| [`did_you_mean.sql`](did_you_mean.sql) | Exercise 10 |
| [`search_facets.sql`](search_facets.sql) | Exercise 11 |
| [`prefix_tsquery.sql`](prefix_tsquery.sql) | Exercise 12 |

---

## Exercise 1 ★: The stop words in our descriptions

`search_vector` can't answer this: the `english` configuration has already thrown the stop words away. So parse the descriptions again with `simple`, which keeps every word, collect the vocabulary with `ts_stat`, and keep the words that the English stemmer turns into an empty array, which is how a dictionary says "stop word":

```sql
SELECT word, ndoc
FROM ts_stat($$SELECT to_tsvector('simple', description) FROM bazaar.products$$)
WHERE ts_lexize('english_stem', word) = '{}'
ORDER BY ndoc DESC, word;
```

```text
 word |  ndoc  
------+--------
 and  | 190119
 of   | 190119
 for  | 159709
 from | 154571
 a    | 131086
 by   |  35548
 the  |  19387
 in   |  16983
 an   |  16520
 into |   5096
 now  |   5096
 with |   4938
 to   |   3607
 all  |   3245
 up   |   2806
 no   |   2017
 out  |   1353
 over |    854
 down |    752
 t    |    714
 s    |    559
 can  |    557
(22 rows)
```

190,119 is the number of products with a non-NULL description (200,000 − 9,881). `t` and `s` come from contractions: the `default` parser splits "don't" and "seller's" at the apostrophe. The query takes about 3 seconds, because it parses every description again: `ts_stat` is an analysis tool, not something to run per request.

**Alternative:** read the stop-word file itself (`pg_read_file('/usr/share/postgresql/17/tsearch_data/english.stop')`, superuser only) and join. That ties the query to a file path and to one dictionary's file format; asking the dictionary is portable.

---

## Exercise 2 ★: Title or elsewhere?

Weight filters do the work; `FILTER` gives the three counts in one scan of the matching rows:

```sql
SELECT count(*) FILTER (WHERE search_vector @@ to_tsquery('english', 'wireless:A'))     AS in_title,
       count(*) FILTER (WHERE search_vector @@ to_tsquery('english', 'wireless')
                          AND NOT search_vector @@ to_tsquery('english', 'wireless:A')) AS elsewhere_only,
       count(*) FILTER (WHERE search_vector @@ to_tsquery('english', 'wireless'))       AS anywhere
FROM products
WHERE status = 'active';
```

```text
 in_title | elsewhere_only | anywhere 
----------+----------------+----------
     5063 |           3104 |     8167
(1 row)
```

"Elsewhere only" can also be written as a single `tsquery`: `wireless:BCD & !wireless:A` (3,104 products). Note that `wireless:BCD` alone is not the answer: it matches 4,776 products, because a product with "wireless" in its title *and* its description satisfies it too.

This version reads every active product (there is no `@@` condition in the `WHERE` clause, so no index can be used). Moving the common condition into the `WHERE` lets the GIN index find the 8,167 candidates first, with the same result:

```sql
SELECT count(*) FILTER (WHERE search_vector @@ to_tsquery('english', 'wireless:A'))     AS in_title,
       count(*) FILTER (WHERE NOT search_vector @@ to_tsquery('english', 'wireless:A')) AS elsewhere_only,
       count(*)                                                                         AS anywhere
FROM products
WHERE status = 'active'
  AND search_vector @@ to_tsquery('english', 'wireless');
```

`ILIKE` could answer (a) for the title, but not (b): it can't tell a word from a substring ("wirelessly"), and the tags are an array, not text.

---

## Exercise 3 ★: Say it in websearch syntax

The natural attempt doesn't do what it says:

```sql
SELECT websearch_to_tsquery('english', 'tent or "sleeping bag" -ultralight');
```

```text
            websearch_to_tsquery            
--------------------------------------------
 'tent' | 'sleep' <-> 'bag' & !'ultralight'
(1 row)
```

`&` binds tighter than `|`, so this is `tent | (sleeping bag & !ultralight)`: ultralight tents are still in (6,351 products). `websearch_to_tsquery` has no parentheses, so the exclusion must be repeated in each branch:

```sql
SELECT websearch_to_tsquery('english', 'tent -ultralight or "sleeping bag" -ultralight');

SELECT count(*)
FROM products
WHERE status = 'active'
  AND search_vector @@ websearch_to_tsquery('english', 'tent -ultralight or "sleeping bag" -ultralight');
```

```text
                    websearch_to_tsquery                    
------------------------------------------------------------
 'tent' & !'ultralight' | 'sleep' <-> 'bag' & !'ultralight'
(1 row)

 count 
-------
  5698
(1 row)
```

The same result in `to_tsquery` syntax, for your own code (never for user input): `to_tsquery('english', '(tent | sleeping <-> bag) & !ultralight')`. The lesson for a search UI: if exclusions matter to your users, offer them as a filter control, not only as query syntax.

---

## Exercise 4 ★★: Phrases versus bags of words

```sql
SELECT q,
       (SELECT count(*) FROM products WHERE search_vector @@ plainto_tsquery('english', q))  AS all_words,
       (SELECT count(*) FROM products WHERE search_vector @@ phraseto_tsquery('english', q)) AS phrase
FROM unnest(ARRAY['wireless headphones', 'noise cancelling', 'cast iron', 'eau de parfum',
                  'usb-c charger', 'board game', 'face cream']) AS q;
```

```text
          q          | all_words | phrase 
---------------------+-----------+--------
 wireless headphones |       643 |    140
 noise cancelling    |      8671 |   8671
 cast iron           |      6766 |   6766
 eau de parfum       |      1132 |   1132
 usb-c charger       |      1462 |    893
 board game          |      4215 |   4215
 face cream          |       715 |    715
(7 rows)
```

Five pairs of words only ever appear together in this catalog, so the phrase changes nothing. The two that differ:

```sql
SELECT id, title
FROM products
WHERE search_vector @@ plainto_tsquery('english', 'wireless headphones')
  AND NOT search_vector @@ phraseto_tsquery('english', 'wireless headphones')
ORDER BY id
LIMIT 4;
```

```text
  id  |                 title                 
------+---------------------------------------
  415 | Orbitron Wireless In-Ear Monitors 740
  541 | Orbitron Slim In-Ear Monitors 309
 1530 | Orbitron Wireless In-Ear Monitors 983
 2122 | Orbitron Mini Headset 388
(4 rows)
```

- **`wireless headphones`**: "Wireless" is in the title or the description, and `headphones` only in the tags, or "wireless" and "headphones" are separated by other words ("Wireless Over-Ear Headphones"). The phrase version loses relevant products like "Helix Wireless Over-Ear Headphones", and the bag-of-words version gains in-ear monitors and headsets. Neither is "right"; this is why `search_products` uses the bag of words for *matching* and `ts_rank_cd` (which rewards proximity) for *ordering*.
- **`usb-c charger`**: the phrase query is `'usb-c' <-> 'usb' <-> 'c' <-> 'charger'` (the hyphenated word produces three lexemes). The extra matches are chargers whose *description* says "Features USB-C charging": all the words are there, but not next to each other. "Charging" stems to `charg`, not `charger`, so even a "USB-C charging" phrase wouldn't count.

---

## Exercise 5 ★★: Where do the hits come from?

```sql
WITH m AS (
    SELECT search_vector AS v
    FROM products
    WHERE search_vector @@ to_tsquery('english', 'headphones')
)
SELECT v @@ to_tsquery('english', 'headphones:A') AS in_title,
       v @@ to_tsquery('english', 'headphones:B') AS in_description,
       v @@ to_tsquery('english', 'headphones:C') AS in_tags,
       count(*)
FROM m
GROUP BY 1, 2, 3
ORDER BY 4 DESC;
```

```text
 in_title | in_description | in_tags | count 
----------+----------------+---------+-------
 f        | f              | t       |  2590
 t        | t              | t       |  1728
 t        | f              | t       |    91
(3 rows)
```

Every one of the 2,590 tag-only matches is in category 6, Headphones: earbuds, headsets and in-ear monitors (for example product 100, "Arcadia Portable Earbuds 128", tags `{electronics,headphones}`). The category tag makes them findable by the generic word, which is good, and weight C keeps them below products that say "headphones" in the title, which is also good. That is the whole point of weights.

The 91 title-and-tags rows are the headphones without a description (NULL descriptions contribute nothing). `ts_filter(v, '{a}')` is another way to ask the same question: it keeps only the positions with the given weights, so `ts_filter(v, '{a}') @@ q` is equivalent to `v @@ q:A`.

---

## Exercise 6 ★★: When the two ranking functions disagree

```sql
WITH q AS (
    SELECT websearch_to_tsquery('english', 'wireless charger') AS q
), r AS (
    SELECT p.id, p.title,
           ts_rank(p.search_vector, q.q)    AS r,
           ts_rank_cd(p.search_vector, q.q) AS cd
    FROM products p, q
    WHERE p.status = 'active'
      AND p.search_vector @@ q.q
), top_r AS (
    SELECT id FROM r ORDER BY r DESC, id LIMIT 10
), top_cd AS (
    SELECT id FROM r ORDER BY cd DESC, id LIMIT 10
)
SELECT (SELECT count(*) FROM r)                              AS matches,
       (SELECT count(*) FROM top_r JOIN top_cd USING (id))   AS in_both;
```

```text
 matches | in_both 
---------+---------
    1187 |       1
(1 row)
```

The `ts_rank` winners:

```text
   id   |                 title                  |     r      |    cd     
--------+----------------------------------------+------------+-----------
  16448 | Voltix Wireless Wireless Charger 729   |  0.9999986 | 1.6857142
  36248 | Helix Wireless Wireless Charger 721    |  0.9999986 | 1.6857142
  37894 | Arcadia Wireless Wireless Charger 688  |  0.9999986 | 1.6857142
 120611 | Quanta Wireless Wireless Charger 502   |  0.9999986 | 1.6857142
 ...
```

The data generator combined the style "Wireless" with the product type "Wireless Charger", so these titles say "wireless" twice. `ts_rank` is frequency-based: the second occurrence pushes them to the top. `ts_rank_cd` scores the *covers* (the shortest spans containing all the query terms), and "Wireless Charger" is the same tight cover in every one of these titles, so it gives hundreds of products the same score (1.6857142), and the tie-break by `id` decides the top 10. Only product 16448 has both the top `cd` score and a low enough id.

On a marketplace, the frequency behaviour is an invitation to **keyword stuffing**: a seller who writes "Wireless Charger Wireless Fast Wireless" wins. Defences: prefer `ts_rank_cd` (repetition doesn't create a denser cover), normalize by document length (flag 1 or 2), cap or deduplicate title words at write time, and let business signals break ties, as `search_products` does.

---

## Exercise 7 ★★: Synonyms the shoppers use

The aliases go into a table of `tsquery` pairs, and a function applies them to the parsed query ([`aliases.sql`](aliases.sql)):

```sql
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

CREATE OR REPLACE FUNCTION expand_query(q text)
RETURNS tsquery
LANGUAGE sql
STABLE
RETURN ts_rewrite(websearch_to_tsquery('english', coalesce(q, '')),
                  'SELECT target, substitute FROM bazaar.search_aliases');
```

and `search_products` changes in exactly one line of its `DECLARE` section:

```sql
    v_tsq   tsquery := expand_query(q);          -- was: websearch_to_tsquery('english', ...)
```

```sql
SELECT expand_query('sci-fi novel'), expand_query('earphones -gaming');
SELECT product_id, title, category_name, score, matched_by
FROM search_products('sci-fi novel', max_results => 5);
```

```text
                 expand_query                  |                  expand_query                   
-----------------------------------------------+-------------------------------------------------
 ( 'novel' | 'book' ) & 'scienc' <-> 'fiction' | !'game' & ( 'headphon' | 'earbud' | 'earphon' )
(1 row)

 product_id |           title            |  category_name  | score  | matched_by 
------------+----------------------------+-----------------+--------+------------
          2 | The Burning Protocol       | Science Fiction | 0.9555 | fts
        112 | The Horizon of Brightwater | Science Fiction | 0.9372 | fts
        141 | The Machine of Brightwater | Science Fiction | 0.9371 | fts
        274 | The Endless Galaxy         | Science Fiction | 0.9295 | fts
        207 | The Hidden Machine         | Science Fiction | 0.9278 | fts
(5 rows)
```

Design points:

- **Both columns are normalised `tsquery` values**, built with the same configuration as the search. `ts_rewrite` matches lexemes (`'knive'`), not what the shopper typed (`knives`). Storing `'knives'` as text would never match.
- **The rewrite also works inside larger queries**: `earphones -gaming` keeps its exclusion.
- **`expand_query` is `STABLE`**: its result depends on a table, so it must not be `IMMUTABLE` (an immutable function may be evaluated once at plan time and cached in prepared statements, which would hide alias changes). `ts_rewrite` with a query string runs that `SELECT` on every call; with four rows that costs nothing.
- **The alternative**, a synonym or thesaurus dictionary, changes the document side: every stored `search_vector` must be recomputed after each change, and the dictionary files live outside the database. Query-side rewriting is the better tool for merchandiser-maintained aliases; dictionaries are better for linguistic rules that should apply to documents too (`knives` → `knife` for highlighting, for example).

To get the original function back for the other exercises, re-run `search_products.sql`.

---

## Exercise 8 ★★: Accent-insensitive full-text search

[`accent_insensitive.sql`](accent_insensitive.sql) runs everything in one transaction and rolls it back:

```sql
BEGIN;

CREATE TEXT SEARCH CONFIGURATION bazaar_english (COPY = english);
ALTER TEXT SEARCH CONFIGURATION bazaar_english
    ALTER MAPPING FOR asciiword, word, hword, hword_part, asciihword, hword_asciipart
    WITH unaccent, english_stem;

ALTER TABLE products DROP COLUMN search_vector;          -- also drops its index
ALTER TABLE products
    ADD COLUMN search_vector tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('bazaar_english', coalesce(title, '')), 'A')
        || setweight(to_tsvector('bazaar_english', coalesce(description, '')), 'B')
        || setweight(to_tsvector('bazaar_english', tags_to_text(tags)), 'C')
    ) STORED;                                             -- 16.5 s: rewrites all 200,000 rows
SET LOCAL maintenance_work_mem = '512MB';
CREATE INDEX products_search_vector_idx ON products USING gin (search_vector);   -- 1.1 s

SELECT q,
       websearch_to_tsquery('bazaar_english', q) AS tsq,
       (SELECT count(*) FROM products
        WHERE search_vector @@ websearch_to_tsquery('bazaar_english', q)) AS matches
FROM unnest(ARRAY['creme', 'crème', 'CRÈME', 'lumiere', 'saute pan', 'cafe']) AS q;

ROLLBACK;
```

```text
     q     |      tsq       | matches 
-----------+----------------+---------
 creme     | 'creme'        |    1435
 crème     | 'creme'        |    1435
 CRÈME     | 'creme'        |    1435
 lumiere   | 'lumier'       |    1586
 saute pan | 'saut' & 'pan' |     527
 cafe      | 'cafe'         |     385
(6 rows)
```

Points to notice:

- **Documents and queries must both use the new configuration.** `websearch_to_tsquery('english', 'creme')` against the new vectors would still work for `creme`, but `'crème'` would produce the lexeme `crème`, which no longer exists in any document: accented queries would now be the ones that fail.
- **The rewrite is the expensive step** (16.5 s, with an `ACCESS EXCLUSIVE` lock on `products` the whole time). On a live system, add a new plain column, fill it in batches, build the index `CONCURRENTLY`, switch the queries, and drop the old column.
- **`ROLLBACK` undoes even the DDL.** PostgreSQL's DDL is transactional, which makes this kind of experiment safe.
- The search function must be updated in the same deployment, and so must the trigram fallback's assumptions: with accent-insensitive FTS, `creme` no longer needs the fuzzy path.

---

## Exercise 9 ★★: The zero-results report

Aggregate the 5 million events down to 18 rows first, then do the expensive per-query work only 18 times:

```sql
WITH terms AS (
    SELECT payload ->> 'q' AS q, count(*) AS searches
    FROM events
    WHERE event_type = 'search'
    GROUP BY 1
)
SELECT t.q,
       t.searches,
       (SELECT count(*)
        FROM products p
        WHERE p.status = 'active'
          AND p.search_vector @@ websearch_to_tsquery('english', t.q)) AS fts_hits,
       top.title AS top_result,
       top.matched_by
FROM terms t
LEFT JOIN LATERAL (
    SELECT title, matched_by FROM search_products(t.q, max_results => 1)
) top ON true
ORDER BY fts_hits, t.searches DESC;
```

```text
          q          | searches | fts_hits |                top_result                | matched_by 
---------------------+----------+----------+------------------------------------------+------------
 sci-fi novel        |    31843 |        0 |                                          | 
 lapptop             |    31769 |        0 | Zentro Slim 2-in-1 Laptop 456            | fuzzy
 headphnes           |    31759 |        0 | Zentro Compact Over-Ear Headphones 850   | fuzzy
 cast iron skillet   |    32004 |       42 | Greenthumb Rustic Skillet - Beige        | fts
 kettle              |    32255 |      438 | Greenthumb Premium Kettle                | fts
 wireless headphones |    31864 |      549 | Orbitron Wireless Headphones 251         | fts
 face cream          |    32155 |      601 | Verdant Daily Face Cream                 | fts
 standing desk       |    31523 |      752 | Terraform Modern Standing Desk           | fts
 running shoes       |    31716 |      828 | Saltmarsh Classic Running Shoes - Silver | fts
 yoga mat            |    31678 |      840 | TrailPeak Performance Yoga Mat           | fts
 crème               |    31407 |     1201 | Crème de Lys Hydrating Eau de Parfum     | fts
 usb-c charger       |    31925 |     1214 | Zentro Max USB-C Charger 681             | fts
 mystery books       |    32039 |     2830 | The Burning Suspect                      | fts
 tent                |    32149 |     3480 | TrailPeak Pro Tent                       | fts
 board game          |    31337 |     3592 | Brickworks Ultimate Board Game           | fts
 laptop              |    32041 |     3645 | Zentro Slim 2-in-1 Laptop 456            | fts
 hoodie              |    31834 |     4097 | Indigo & Rye Recycled Zip Hoodie - Black | fts
 monitor             |    32075 |     4453 | Quanta Mini 4K Monitor 959               | fts
(18 rows)
```

It runs in about 2 seconds, almost all of it the `GROUP BY` over the events. The report's priorities: `sci-fi novel` is a dead end for 31,843 searches a year (exercise 7 fixes it); the two typos are rescued by the fallback, but each one costs a slower query, so an alias or a "did you mean" (exercise 10) would serve them better; `cast iron skillet` finds only 42 products, worth a look by a merchandiser. In production you would run this from `pg_stat_statements` or the application's search log every week, and order by searches × (results = 0).

---

## Exercise 10 ★★★: Did you mean?

[`did_you_mean.sql`](did_you_mean.sql). The vocabulary is a materialized view over everything searchable in active products, with the `simple` configuration (real words such as `headphones`, not stems such as `headphon`, because the suggestion is shown to the shopper), unaccented with the milestone's `immutable_unaccent`, and a trigram index:

```sql
CREATE MATERIALIZED VIEW search_vocabulary AS
SELECT word, ndoc
FROM ts_stat($$
    SELECT to_tsvector('simple', bazaar.immutable_unaccent(
               title || ' ' || coalesce(description, '') || ' ' || bazaar.tags_to_text(tags)))
    FROM bazaar.products
    WHERE status = 'active'
$$);                                                      -- 1,723 words

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
                   WHEN length(w.word) < 3
                     OR EXISTS (SELECT 1 FROM search_vocabulary v WHERE v.word = w.word)
                   THEN w.word
                   ELSE coalesce(
                       (SELECT v.word
                        FROM search_vocabulary v
                        WHERE v.word % w.word
                        ORDER BY similarity(v.word, w.word) DESC, v.ndoc DESC, v.word
                        LIMIT 1),
                       w.word)
               END AS fixed
        FROM words w
    )
    SELECT CASE WHEN bool_and(word = fixed) THEN NULL
                ELSE string_agg(fixed, ' ' ORDER BY n)
           END
    FROM fixed;
$$;
```

```sql
SELECT q, did_you_mean(q)
FROM unnest(ARRAY['wireles headphnes', 'lapptop', 'cast iron skilet', 'Crème de Lys',
                  'wireless headphones', 'voltx wireles charger', 'sci-fi novel', 'qwzx',
                  'usb-c chrger']) AS q;
```

```text
           q           |      did_you_mean       
-----------------------+-------------------------
 wireles headphnes     | wireless headphones
 lapptop               | laptop
 cast iron skilet      | cast iron skillet
 Crème de Lys          | 
 wireless headphones   | 
 voltx wireles charger | voltix wireless charger
 sci-fi novel          | 
 qwzx                  | 
 usb-c chrger          | usb-c charger
(9 rows)
```

All nine take 7 ms together. Design decisions:

- **Why not a title-only vocabulary** (like the milestone's `search_terms`)? Because "cast" and "iron" never appear in titles, only in descriptions. With title words only, the first version of this function "corrected" `cast iron skilet` to `casa ironcore skillet`.
- **Known words are never corrected**, even if a more frequent similar word exists. Only unknown words get a suggestion, which keeps the function from "fixing" correct but rare words.
- **Short words are left alone**: two letters have too few trigrams for similarity to mean anything.
- **Ties prefer frequent words** (`ndoc DESC`), which is usually what the shopper meant.
- **`sci-fi` stays**: no known word is similar enough (`%` uses the 0.3 threshold). That is correct: it is not a typo but a vocabulary gap, which exercise 7 solves.
- **Splitting on spaces only** keeps `usb-c` together, so it is recognised as a known word (the `simple` configuration indexes the hyphenated compound too).

Refresh the view when the catalog changes (`REFRESH MATERIALIZED VIEW CONCURRENTLY search_vocabulary`, which the unique index allows). In the storefront, call it only when `search_products` returns nothing or falls back to fuzzy, and show "Did you mean: *wireless headphones*?" as a link.

---

## Exercise 11 ★★★: Facets

[`search_facets.sql`](search_facets.sql):

```sql
CREATE OR REPLACE FUNCTION search_facets(q text)
RETURNS TABLE (root_name text, category_name text, products bigint)
LANGUAGE sql
STABLE
AS $$
    WITH RECURSIVE tree AS (
        SELECT c.id, c.id AS root_id, NULL::int AS level2_id
        FROM categories c
        WHERE c.parent_id IS NULL
        UNION ALL
        SELECT c.id, t.root_id, coalesce(t.level2_id, c.id)
        FROM categories c
        JOIN tree t ON c.parent_id = t.id
    ), hits AS (
        SELECT p.category_id, count(*) AS n
        FROM products p
        WHERE p.search_vector @@ websearch_to_tsquery('english', q)
          AND p.status = 'active'
        GROUP BY p.category_id
    ), facets AS (
        SELECT r.name AS root_name, l2.name AS category_name,
               sum(h.n)::bigint AS products, GROUPING(l2.name) AS is_total
        FROM hits h
        JOIN tree t ON t.id = h.category_id
        JOIN categories r ON r.id = t.root_id
        LEFT JOIN categories l2 ON l2.id = t.level2_id
        GROUP BY GROUPING SETS ((r.name), (r.name, l2.name))
    )
    SELECT root_name, category_name, products
    FROM facets
    WHERE NOT (is_total = 0 AND category_name IS NULL)
    ORDER BY max(products) FILTER (WHERE is_total = 1) OVER (PARTITION BY root_name) DESC,
             root_name, is_total DESC, products DESC, category_name
$$;
```

```sql
SELECT * FROM search_facets('wireless');            -- 36 ms
SELECT * FROM search_facets('gift') LIMIT 6;        -- 76 ms
```

```text
  root_name  |    category_name     | products 
-------------+----------------------+----------
 Electronics |                      |     8167
 Electronics | Computers            |     2910
 Electronics | Phones & Accessories |     2339
 Electronics | Audio                |     1771
 Electronics | Cameras              |     1147
(5 rows)

   root_name   | category_name | products 
---------------+---------------+----------
 Home & Garden |               |     7623
 Home & Garden | Kitchen       |     3029
 Home & Garden | Furniture     |     2305
 Home & Garden | Garden        |     2289
 Electronics   |               |     6639
 Electronics   | Computers     |     2167
(6 rows)
```

How it works:

- **The tree** carries each category's root and its level-2 ancestor down the recursion (`coalesce(t.level2_id, c.id)`: the first child below a root becomes the level-2 ancestor of everything under it).
- **Aggregating early** is the performance trick: `hits` turns up to 33,715 matching rows (for `gift`) into at most 69 rows before any join. Almost all the time is the bitmap heap scan that the search itself needs.
- **`GROUPING SETS ((root), (root, level2))`** computes the totals and the details in one pass; `GROUPING(l2.name)` tells the total rows (1) from the detail rows (0), which matters because a detail row can also have a NULL `category_name` (a product sitting directly in a root category; the large dataset has none, but the small one does). Those detail rows are filtered out, since the root total already counts them.
- **Ordering by the root's total** uses a window aggregate over the grouped rows, so roots with more hits come first, each followed by its own categories.

**Alternative:** run the search once and compute facets and the result page from the same CTE in the application's single query. With an exact count needed per facet, every matching row must be visited anyway; for very broad queries, estimate (for example from a sample of matches) or cache facet counts for popular queries.

---

## Exercise 12 ★★★: A prefix query that can't break

[`prefix_tsquery.sql`](prefix_tsquery.sql):

```sql
CREATE OR REPLACE FUNCTION prefix_tsquery(input text)
RETURNS tsquery
LANGUAGE plpgsql
IMMUTABLE PARALLEL SAFE
AS $$
DECLARE
    v_words  text[];
    v_word   text;
    v_part   tsquery;
    v_simple tsquery;
    v_result tsquery := ''::tsquery;
    i        int;
BEGIN
    v_words := array_remove(
        regexp_split_to_array(
            regexp_replace(lower(coalesce(input, '')), '[^[:alnum:]\s-]', '', 'g'),
            '\s+'),
        '');

    FOR i IN 1 .. coalesce(array_length(v_words, 1), 0) LOOP
        v_word := v_words[i];
        CONTINUE WHEN v_word ~ '^-*$';

        IF i < array_length(v_words, 1) THEN
            v_part := plainto_tsquery('english', v_word);
        ELSE
            v_part   := to_tsquery('english', '''' || v_word || ''':*');
            v_simple := to_tsquery('simple',  '''' || v_word || ''':*');
            IF v_part::text <> v_simple::text THEN
                v_part := v_part || v_simple;
            END IF;
        END IF;

        v_result := v_result && v_part;
    END LOOP;

    RETURN v_result;
END;
$$;
```

```sql
SELECT q, prefix_tsquery(q),
       (SELECT count(*) FROM products WHERE search_vector @@ prefix_tsquery(q)) AS matches
FROM unnest(ARRAY['he', 'headphones', 'voltix wi', 'the wirel', 'a&b|c', 'x'')|(y:*', '\', '',
                  'wireless headphones']) AS q;
```

```text
          q          |                 prefix_tsquery                 | matches 
---------------------+------------------------------------------------+---------
 he                  | 'he':*                                         |   23220
 headphones          | 'headphon':* | 'headphones':*                  |    4409
 voltix wi           | 'voltix' & 'wi':*                              |     880
 the wirel           | 'wirel':*                                      |    9569
 a&b|c               | 'abc':*                                        |       0
 x')|(y:*            | 'xy':*                                         |       0
 \                   |                                                |       0
                     |                                                |       0
 wireless headphones | 'wireless' & ( 'headphon':* | 'headphones':* ) |     643
(9 rows)
```

(Empty queries also print the NOTICE "text-search query contains only stop words or doesn't contain lexemes, ignored".)

```sql
EXPLAIN (ANALYZE, COSTS OFF)
SELECT count(*) FROM products WHERE search_vector @@ prefix_tsquery('voltix wi');
```

```text
                                               QUERY PLAN                                                
---------------------------------------------------------------------------------------------------------
 Aggregate (actual time=4.674..4.675 rows=1 loops=1)
   ->  Bitmap Heap Scan on products (actual time=3.855..4.622 rows=880 loops=1)
         Recheck Cond: (search_vector @@ '''voltix'' & ''wi'':*'::tsquery)
         Heap Blocks: exact=867
         ->  Bitmap Index Scan on products_search_vector_idx (actual time=3.758..3.759 rows=880 loops=1)
               Index Cond: (search_vector @@ '''voltix'' & ''wi'':*'::tsquery)
 Planning Time: 0.232 ms
 Execution Time: 4.770 ms
(8 rows)
```

How each requirement is met:

- **Safety by sanitising, then quoting.** After the `regexp_replace`, a word contains only letters, digits and hyphens, so wrapping it in quotes for `to_tsquery` can't produce an operator. (`quote_literal` is *not* the right tool here: it produces SQL string-literal syntax. `quote_literal('\')` is `E'\\'`, and `to_tsquery` reads that as the lexeme `e`.)
- **The stop-word trap**: the prefix is built with both configurations and ORed, so `he` survives through `simple`.
- **The stemming trap**: the `english` branch turns `headphones` into `headphon:*`, which matches the index; the `simple` branch adds the literal form, which is harmless. When both branches are identical, only one is kept.
- **Complete stop words disappear**: `plainto_tsquery('english', 'the')` is empty, and `&&` with an empty query returns the other operand.
- **The index is used because the function is `IMMUTABLE`**: called with a constant, the planner evaluates it once at plan time and the index condition contains the resulting `tsquery`. Is the promise honest? The result depends only on the input and on the `english` and `simple` configurations, which change only if you redefine them, the same assumption every full-text index already makes. Declared `STABLE`, the function would still be usable with an index (the planner evaluates stable functions once per scan), but `IMMUTABLE` also lets it be used in an expression index or a generated column.
- **Hyphens**: a word that is only hyphens is skipped; `usb-c ch` produces `'usb-c' & 'usb' & 'c' & 'ch':*`.
