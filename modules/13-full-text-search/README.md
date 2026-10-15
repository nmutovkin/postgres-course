# Module 13: Full-Text and Fuzzy Search

<!-- nav:start -->
> **Theory** · [Exercises](exercises.md) · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 12 Query Planning and Performance](../12-query-performance/README.md) · [Course home](../../README.md) · [14 Partitioning →](../14-partitioning/README.md)
<!-- nav:end -->

A shopper types `wireless headphones` into Bazaar's search box. Behind it there are 200,000 products. The shopper expects the best matches first, expects "headphone" to find "Headphones", expects a typo like `headphnes` to still work, and expects all of this in a few milliseconds. The first version of the search, `WHERE title ILIKE '%wireless headphones%'`, finds 64 products, misses hundreds of relevant ones, cannot say which result is best, and gets slower as the catalog grows. This module replaces it with real search, built entirely inside PostgreSQL: full-text search (documents, lexemes, ranking, highlighting) for relevance, and trigram matching (`pg_trgm`) for typos, substrings and autocomplete. At the end you build `search_products()`, the function the storefront calls, and an `autocomplete()` function for the search box.

**What you'll learn**

- Why `LIKE '%word%'` is not search: no linguistics, no ranking, and (without trigrams) no index.
- The full-text pipeline: a parser splits text into tokens, dictionaries turn tokens into lexemes, and a text search configuration wires the two together. You will inspect every step with `ts_debug`.
- Dictionaries: `simple`, Snowball stemmers, stop words, synonym and thesaurus dictionaries, `unaccent`, building your own configuration, and query rewriting with `ts_rewrite`.
- `tsvector` (lexemes, positions, weights A–D) and `tsquery` (`&`, `|`, `!`, `<->`, `<N>`, prefix `:*`, weight filters).
- `to_tsquery`, `plainto_tsquery`, `phraseto_tsquery` and `websearch_to_tsquery`, and which one is safe for user input.
- Ranking with `ts_rank` and `ts_rank_cd`: normalization flags, weights, and combining text relevance with business signals such as ratings and sales.
- Highlighting with `ts_headline`, and why it must only run on the rows you return.
- Storing the search document: a generated column, an expression index, or a trigger, with their trade-offs; GIN versus GiST for full-text search; GIN's `fastupdate` pending list.
- Multilingual search.
- `pg_trgm`: trigrams, `similarity()`, `word_similarity()`, the `%`, `<%` and `<->` operators, their thresholds, GIN and GiST trigram indexes, and speeding up `LIKE`, `ILIKE` and regular expressions. "Did you mean?" suggestions.
- `unaccent` and the immutable-wrapper trick that lets you index it.
- Three ways to build autocomplete, measured on 200,000 products.
- When PostgreSQL search is enough, and when a dedicated search engine (Elasticsearch, OpenSearch and others) is worth its cost.

**Before you start**

```bash
./project/bin/reset.sh 13     # large dataset (takes a few minutes)
./project/bin/psql.sh
```

The reset gives you the large dataset (200,000 products: 170,038 active, 13,968 draft, 15,994 archived). The search objects of this module come from [`project/db/1301_schema_search.sql`](../../project/db/1301_schema_search.sql). You build them yourself in section 9, but most sections use them, so apply the file now if you want to follow along in order:

```bash
docker exec -i course-pg psql -U postgres -d bazaar < project/db/1301_schema_search.sql
```

(`./project/bin/reset.sh 14` gives you the same state in one step.) It creates the `pg_trgm` and `unaccent` extensions, a stored generated column `products.search_vector`, a GIN index on it, and a trigram index on `products.title`. Turn on `\timing` in psql. Your timings will differ from the ones shown here, which were measured on a 4-vCPU container with the default configuration and a warm cache, but the ratios between them won't.

You need [Module 11](../11-indexes/) (GIN and GiST, expression indexes) and [Module 12](../12-query-performance/) (reading `EXPLAIN ANALYZE`). This module doesn't re-explain them.

---

<!-- toc:start -->
**Contents**

- [1. Why `LIKE` is not search](#1-why-like-is-not-search)
- [2. The pipeline: from document to lexemes](#2-the-pipeline-from-document-to-lexemes)
  - [2.1 The parser and its token types](#21-the-parser-and-its-token-types)
  - [2.2 Dictionaries](#22-dictionaries)
  - [2.3 Configurations](#23-configurations)
- [3. Building your own configuration](#3-building-your-own-configuration)
  - [3.1 Adding `unaccent`](#31-adding-unaccent)
  - [3.2 Adding a synonym dictionary](#32-adding-a-synonym-dictionary)
  - [3.3 Query-side synonyms with `ts_rewrite`](#33-query-side-synonyms-with-ts_rewrite)
- [4. `tsvector`: the document](#4-tsvector-the-document)
  - [4.1 Weights](#41-weights)
  - [4.2 What is in the whole catalog?](#42-what-is-in-the-whole-catalog)
- [5. `tsquery`: the query](#5-tsquery-the-query)
  - [5.1 Matching with `@@`](#51-matching-with-)
- [6. Turning user input into a `tsquery`](#6-turning-user-input-into-a-tsquery)
- [7. Ranking](#7-ranking)
  - [7.1 Combining text rank with business signals](#71-combining-text-rank-with-business-signals)
  - [7.2 What ranking costs](#72-what-ranking-costs)
- [8. Highlighting with `ts_headline`](#8-highlighting-with-ts_headline)
  - [8.1 Its cost: only run it on the rows you return](#81-its-cost-only-run-it-on-the-rows-you-return)
- [9. Storing the search document](#9-storing-the-search-document)
  - [9.1 Option 1: a stored generated column (Bazaar's choice)](#91-option-1-a-stored-generated-column-bazaars-choice)
  - [9.2 Option 2: an expression index](#92-option-2-an-expression-index)
  - [9.3 Option 3: a trigger-maintained column](#93-option-3-a-trigger-maintained-column)
  - [9.4 Indexes for `tsvector`: GIN and GiST](#94-indexes-for-tsvector-gin-and-gist)
  - [9.5 GIN's pending list (`fastupdate`)](#95-gins-pending-list-fastupdate)
- [10. Multilingual search](#10-multilingual-search)
- [11. Fuzzy matching with `pg_trgm`](#11-fuzzy-matching-with-pg_trgm)
  - [11.1 Trigrams and similarity](#111-trigrams-and-similarity)
  - [11.2 Operators and thresholds](#112-operators-and-thresholds)
  - [11.3 Trigram indexes: GIN and GiST](#113-trigram-indexes-gin-and-gist)
  - [11.4 Speeding up `LIKE`, `ILIKE` and regular expressions](#114-speeding-up-like-ilike-and-regular-expressions)
  - [11.5 "Did you mean?"](#115-did-you-mean)
- [12. `unaccent`, and the immutable wrapper](#12-unaccent-and-the-immutable-wrapper)
- [13. Autocomplete](#13-autocomplete)
  - [13.1 Prefix `tsquery`](#131-prefix-tsquery)
  - [13.2 Trigram `ILIKE`](#132-trigram-ilike)
  - [13.3 B-tree prefix on titles](#133-b-tree-prefix-on-titles)
  - [13.4 Which one?](#134-which-one)
- [14. When to reach for a dedicated search engine](#14-when-to-reach-for-a-dedicated-search-engine)
- [Summary](#summary)
- [What's next](#whats-next)

<!-- toc:end -->

## 1. Why `LIKE` is not search

Pattern matching with `LIKE` (and its case-insensitive twin `ILIKE`) compares characters. Search is about *words* and *meaning*. Three problems show up as soon as you try.

**No linguistics.** `ILIKE '%knife%'` does not find "Steak Knives". The plural is a different string. `ILIKE '%tent%'` finds every tent, and also every description that says "Perfect for content creators":

```sql
SELECT count(*) FILTER (WHERE title ILIKE '%tent%' OR description ILIKE '%tent%') AS like_tent,
       count(*) FILTER (WHERE search_vector @@ to_tsquery('english', 'tent'))   AS fts_tent,
       count(*) FILTER (WHERE title ILIKE '%knife%' OR description ILIKE '%knife%') AS like_knife,
       count(*) FILTER (WHERE title ILIKE '%knives%' OR description ILIKE '%knives%') AS like_knives
FROM products;
```

```text
 like_tent | fts_tent | like_knife | like_knives 
-----------+----------+------------+-------------
     10308 |     4043 |       2336 |         574
(1 row)
```

More than half of the `%tent%` hits are about "content" (6,265 descriptions contain it), and a shopper looking for a knife sees 2,336 products but not the 574 sets of knives.

**No ranking.** `LIKE` returns true or false. A product with "wireless headphones" in its title and one that mentions headphones once at the end of its description look exactly the same. You can sort by price or date, but not by *how well* a product matches.

**No index for `'%word%'`.** A B-tree can only help when the pattern starts with fixed characters (`LIKE 'Voltix%'`, see section 13). A leading `%` means "anywhere", so PostgreSQL reads every row. `description` has no special index, so:

```sql
EXPLAIN (ANALYZE, COSTS OFF)
SELECT count(*) FROM products WHERE description ILIKE '%skillet%';
```

```text
                                          QUERY PLAN                                           
-----------------------------------------------------------------------------------------------
 Finalize Aggregate (actual time=156.281..161.467 rows=1 loops=1)
   ->  Gather (actual time=156.129..161.458 rows=3 loops=1)
         Workers Planned: 2
         Workers Launched: 2
         ->  Partial Aggregate (actual time=152.422..152.424 rows=1 loops=3)
               ->  Parallel Seq Scan on products (actual time=0.624..152.334 rows=162 loops=3)
                     Filter: (description ~~* '%skillet%'::text)
                     Rows Removed by Filter: 66505
 Planning Time: 0.422 ms
 Execution Time: 161.514 ms
(10 rows)
```

160 ms using three CPU cores, for one search, and it grows linearly with the table. (The trigram extension of section 11 *can* index `'%skillet%'`, which fixes the third problem but not the first two.)

PostgreSQL's answer to all three is **full-text search** (FTS): turn every document into a sorted list of normalised words, turn the query into the same kind of words, and match the two with an index. Here is the whole idea in one statement:

```sql
SELECT to_tsvector('english', 'The quick brown foxes jumped over the lazy dogs');
```

```text
                      to_tsvector                      
-------------------------------------------------------
 'brown':3 'dog':9 'fox':4 'jump':5 'lazi':8 'quick':2
(1 row)
```

"The" and "over" are gone (they carry no meaning), "foxes" became `fox`, "jumped" became `jump`, and every word remembers its position. A query for `fox` or `jumping` now matches, because the query goes through the same normalisation. The rest of the module explains each step.

---

## 2. The pipeline: from document to lexemes

Full-text search processes text in two stages:

```text
 document (text)
      │
      ▼
 ┌──────────┐   tokens with a type:  asciiword "Headphones", uint "527",
 │  parser  │   blank ", ", url "bazaar.example/p/42", hword "USB-C", ...
 └──────────┘
      │  for each token type, the configuration names a list of dictionaries
      ▼
 ┌──────────────────────────────┐
 │ dictionaries (tried in order) │  "Headphones" -> {headphon}
 │ e.g. unaccent, synonyms, stem │  "the"        -> {}  (stop word: dropped)
 └──────────────────────────────┘
      │
      ▼
 lexemes with positions  ->  tsvector  'headphon':3 'voltix':1 'wireless':2
```

A **text search configuration** (for example `english`) is just the combination: which parser to use, and which dictionaries handle which token types.

### 2.1 The parser and its token types

PostgreSQL ships one parser, `default`. It recognises 23 token types:

```sql
SELECT tokid, alias, description FROM ts_token_type('default');
```

```text
 tokid |      alias      |               description                
-------+-----------------+------------------------------------------
     1 | asciiword       | Word, all ASCII
     2 | word            | Word, all letters
     3 | numword         | Word, letters and digits
     4 | email           | Email address
     5 | url             | URL
     6 | host            | Host
     7 | sfloat          | Scientific notation
     8 | version         | Version number
     9 | hword_numpart   | Hyphenated word part, letters and digits
    10 | hword_part      | Hyphenated word part, all letters
    11 | hword_asciipart | Hyphenated word part, all ASCII
    12 | blank           | Space symbols
    13 | tag             | XML tag
    14 | protocol        | Protocol head
    15 | numhword        | Hyphenated word, letters and digits
    16 | asciihword      | Hyphenated word, all ASCII
    17 | hword           | Hyphenated word, all letters
    18 | url_path        | URL path
    19 | file            | File or path name
    20 | float           | Decimal notation
    21 | int             | Signed integer
    22 | uint            | Unsigned integer
    23 | entity          | XML entity
(23 rows)
```

`ts_debug` runs the whole pipeline and shows each token, its type, the dictionaries consulted, and the resulting lexemes. It is the single most useful debugging tool in this module:

```sql
SELECT alias, token, dictionaries, lexemes
FROM ts_debug('english', 'Voltix Wireless Headphones 527, 2-year warranty! USB-C charger at https://bazaar.example/p/42 — 4.5 stars, café');
```

```text
      alias      |        token        |  dictionaries  |        lexemes        
-----------------+---------------------+----------------+-----------------------
 asciiword       | Voltix              | {english_stem} | {voltix}
 blank           |                     | {}             | 
 asciiword       | Wireless            | {english_stem} | {wireless}
 blank           |                     | {}             | 
 asciiword       | Headphones          | {english_stem} | {headphon}
 blank           |                     | {}             | 
 uint            | 527                 | {simple}       | {527}
 blank           | ,                   | {}             | 
 uint            | 2                   | {simple}       | {2}
 blank           | -                   | {}             | 
 asciiword       | year                | {english_stem} | {year}
 blank           |                     | {}             | 
 asciiword       | warranty            | {english_stem} | {warranti}
 blank           | !                   | {}             | 
 asciihword      | USB-C               | {english_stem} | {usb-c}
 hword_asciipart | USB                 | {english_stem} | {usb}
 blank           | -                   | {}             | 
 hword_asciipart | C                   | {english_stem} | {c}
 blank           |                     | {}             | 
 asciiword       | charger             | {english_stem} | {charger}
 blank           |                     | {}             | 
 asciiword       | at                  | {english_stem} | {}
 blank           |                     | {}             | 
 protocol        | https://            | {}             | 
 url             | bazaar.example/p/42 | {simple}       | {bazaar.example/p/42}
 host            | bazaar.example      | {simple}       | {bazaar.example}
 url_path        | /p/42               | {simple}       | {/p/42}
 blank           |  —                  | {}             | 
 float           | 4.5                 | {simple}       | {4.5}
 blank           |                     | {}             | 
 asciiword       | stars               | {english_stem} | {star}
 blank           | ,                   | {}             | 
 word            | café                | {english_stem} | {café}
(33 rows)
```

Things worth noticing:

- **Lexemes are not words.** `headphon` and `warranti` are *stems*, produced by an algorithm. Nobody ever displays a lexeme; it only has to be the same for "headphone", "headphones" and "headphone's".
- **Hyphenated words are emitted three times**: the whole compound (`usb-c`) and each part (`usb`, `c`). So a search for `usb` finds "USB-C", and so does a search for `usb-c`.
- **Blanks and punctuation are dropped** (no dictionary is configured for `blank`).
- **A URL becomes several tokens** (the URL, the host, the path), each kept verbatim by the `simple` dictionary.
- **`at` produced an empty lexeme list**: it is a *stop word*, and it is dropped.
- **`café` keeps its accent.** The `english` configuration doesn't strip accents, so "cafe" won't match "café". Section 12 fixes that.
- **`2-year` is not a hyphenated word** to this parser. It is a number, a blank and a word. Parsers have quirks; `ts_debug` shows them.

### 2.2 Dictionaries

A dictionary takes one token and returns one of three answers:

- an array of lexemes (usually one): **"I know this token, here is its normal form"**;
- an empty array: **"I know this token and it should be dropped"** (a stop word);
- `NULL`: **"I don't know this token, ask the next dictionary"**.

The configuration lists dictionaries per token type, and they are tried in order until one returns non-NULL. So specific dictionaries (synonyms, a product-name list) go first and a catch-all (a stemmer, or `simple`) goes last. `ts_lexize` calls one dictionary directly:

```sql
SELECT ts_lexize('english_stem', 'running'), ts_lexize('english_stem', 'the'),
       ts_lexize('english_stem', 'knives'),  ts_lexize('simple', 'Running'),
       ts_lexize('english_stem', 'universe'), ts_lexize('english_stem', 'university');
```

```text
 ts_lexize | ts_lexize | ts_lexize | ts_lexize | ts_lexize | ts_lexize 
-----------+-----------+-----------+-----------+-----------+-----------
 {run}     | {}        | {knive}   | {running} | {univers} | {univers}
(1 row)
```

The dictionary types you will meet:

| Template | What it does | Built-in examples |
|---|---|---|
| `simple` | Lower-cases the token. Optionally drops stop words from a list. Never returns NULL, so it ends a chain. | `simple` |
| `snowball` | An algorithmic **stemmer** (Martin Porter's Snowball family) that strips suffixes by rules, plus a stop-word list. Accepts every token, so it ends a chain too. | `english_stem`, `french_stem`, `german_stem` and 25 more |
| `synonym` | Replaces a whole word with another word, from a file. Returns NULL for unknown words. | none; you create them |
| `thesaurus` | Like synonym, but for *phrases* ("solid state drive" → `ssd`), and the result can be re-processed by another dictionary. | none |
| `ispell` | Uses an Ispell/Hunspell morphological dictionary (real word lists, not rules). Better normal forms, but you must install the files. | none |
| filtering (`unaccent`) | Transforms the token and passes the result *on* to the next dictionary, instead of ending the chain. | `unaccent` (from the extension) |

Stemmers are heuristics, and the last three results above show both of their failure modes. **Under-stemming**: `knives` becomes `knive` while `knife` stays `knife`, so the two never meet. **Over-stemming**: `universe` and `university` collapse into the same stem, so a search for one finds the other. For product search the trade is overwhelmingly worth it, but when a specific word pair matters to your business, a synonym dictionary in front of the stemmer fixes it (section 3.2).

> **Under the hood:** Stop-word lists and synonym files live in `$SHAREDIR/tsearch_data/` on the server (`/usr/share/postgresql/17/tsearch_data/` in the course container). `english.stop` contains 127 words. Dictionaries read their files when a session first uses them, so after editing a file you must start a new session (or run `ALTER TEXT SEARCH DICTIONARY ... (dummy option)` to force a reload).

### 2.3 Configurations

`\dF` lists 29 built-in configurations: one for each of the 28 Snowball languages, plus `simple`. `\dF+` shows the mapping from token types to dictionaries:

```sql
\dF+ english
```

```text
Text search configuration "pg_catalog.english"
Parser: "pg_catalog.default"
      Token      | Dictionaries 
-----------------+--------------
 asciihword      | english_stem
 asciiword       | english_stem
 email           | simple
 file            | simple
 float           | simple
 host            | simple
 hword           | english_stem
 hword_asciipart | english_stem
 hword_numpart   | simple
 hword_part      | english_stem
 int             | simple
 numhword        | simple
 numword         | simple
 sfloat          | simple
 uint            | simple
 url             | simple
 url_path        | simple
 version         | simple
 word            | english_stem
```

Token types that are not listed (`blank`, `tag`, `protocol`, `entity`) are ignored. Compare the two configurations you will use the most:

```sql
SELECT to_tsvector('simple', 'The quick brown foxes jumped over the lazy dogs');
```

```text
                                  to_tsvector                                  
-------------------------------------------------------------------------------
 'brown':3 'dogs':9 'foxes':4 'jumped':5 'lazy':8 'over':6 'quick':2 'the':1,7
(1 row)
```

`simple` lower-cases and nothing else: no stop words, no stemming. It is the right choice for text that is not natural language (SKUs, brand names, user names, tags) and for languages that have no stemmer.

Every FTS function takes the configuration as an optional first argument. **Always pass it explicitly.** Without it, the function uses the `default_text_search_config` setting (`pg_catalog.english` here), which any session can change, so the same call can produce different lexemes in different sessions. Section 9 shows why that also makes the one-argument form unusable in indexes.

---

## 3. Building your own configuration

Bazaar sells "Crème de Lys" skincare and "Lumière" lamps, and shoppers type `creme` and `lumiere`. It also sells steak knives, and shoppers type `knives` as often as `knife`. Both problems are fixed in the configuration, without touching any query.

### 3.1 Adding `unaccent`

`unaccent` is a *filtering* dictionary: it removes diacritics and passes the result to the next dictionary in the list.

```sql
SELECT unaccent('Crème brûlée Lumière Ærø Straße');
```

```text
             unaccent              
-----------------------------------
 Creme brulee Lumiere AEro Strasse
(1 row)
```

A configuration is created by copying an existing one and then changing mappings:

```sql
CREATE TEXT SEARCH CONFIGURATION bazaar_english (COPY = english);

ALTER TEXT SEARCH CONFIGURATION bazaar_english
    ALTER MAPPING FOR asciiword, word, hword, hword_part, asciihword, hword_asciipart
    WITH unaccent, english_stem;
```

Now `Crème` goes through `unaccent` (becoming `Creme`) and then through `english_stem` (becoming `creme`). Documents and queries both go through it, so `creme` and `crème` meet.

### 3.2 Adding a synonym dictionary

A synonym dictionary needs a file `<name>.syn` in `tsearch_data`, one "word replacement" pair per line. Create it in the course container:

```bash
printf 'knives knife\ntee shirt\nearphones headphon\n' | \
  docker exec -i course-pg sh -c 'cat > /usr/share/postgresql/17/tsearch_data/bazaar_synonyms.syn'
```

Then create the dictionary and put it between `unaccent` and the stemmer:

```sql
CREATE TEXT SEARCH DICTIONARY bazaar_synonyms (TEMPLATE = synonym, SYNONYMS = bazaar_synonyms);

SELECT ts_lexize('bazaar_synonyms', 'Knives'), ts_lexize('bazaar_synonyms', 'spoon');
```

```text
 ts_lexize | ts_lexize 
-----------+-----------
 {knife}   | 
(1 row)
```

`spoon` returns NULL ("not mine"), so the next dictionary handles it. Rebuild the mapping with all three dictionaries:

```sql
ALTER TEXT SEARCH CONFIGURATION bazaar_english
    ALTER MAPPING FOR asciiword, word, hword, hword_part, asciihword, hword_asciipart
    WITH unaccent, bazaar_synonyms, english_stem;

SELECT to_tsvector('english',        'Crème de Lys Steak Knives, Café tee') AS english,
       to_tsvector('bazaar_english', 'Crème de Lys Steak Knives, Café tee') AS bazaar_english;
```

```text
-[ RECORD 1 ]--+----------------------------------------------------------------
english        | 'café':6 'crème':1 'de':2 'knive':5 'lys':3 'steak':4 'tee':7
bazaar_english | 'cafe':6 'creme':1 'de':2 'knife':5 'lys':3 'shirt':7 'steak':4
```

`ts_debug` shows which dictionary answered for each token:

```sql
SELECT token, dictionaries, dictionary, lexemes FROM ts_debug('bazaar_english', 'Crème Knives');
```

```text
 token  |              dictionaries               |   dictionary    | lexemes 
--------+-----------------------------------------+-----------------+---------
 Crème  | {unaccent,bazaar_synonyms,english_stem} | unaccent        | {Creme}
        | {}                                      |                 | 
 Knives | {unaccent,bazaar_synonyms,english_stem} | bazaar_synonyms | {knife}
(3 rows)
```

(`ts_debug` reports the filtering dictionary's intermediate output for `Crème`; the final lexeme is `creme`, as the `to_tsvector` call above shows.)

Two details of synonym files trip people up:

- **The replacement is not stemmed.** The synonym dictionary ends the chain, so its output goes straight into the `tsvector`. That is why the file maps `earphones` to `headphon` (the stem) and not to `headphones`: a document containing "headphones" is indexed as `headphon`, and the synonym must produce the same lexeme.
- **One word maps to one word.** For phrases ("sci fi" → "science fiction") you need a *thesaurus* dictionary, which matches sequences of words and can pass its output on to a subdictionary for stemming. Its file format is `sample words : replacement words`.

> **In production:** Configurations and dictionaries are database objects, but their files are not. They are not in `pg_dump` output, they are not replicated to standbys, and managed services (Amazon RDS, Cloud SQL, Azure) usually don't let you write to `tsearch_data` at all. If you use custom dictionary files, deploy them with your server configuration and check them on every replica. Changing a configuration that is used by stored `tsvector`s also means re-computing them (section 9), because old rows keep the lexemes of the old configuration.

### 3.3 Query-side synonyms with `ts_rewrite`

Dictionaries change how *documents* are indexed, so every change means recomputing stored vectors, and they need files on the server. For "shoppers say X, the catalog says Y" there is a lighter tool that works on the *query* only: `ts_rewrite(query, 'SELECT target, substitute FROM table')` replaces every occurrence of each `target` sub-query with its `substitute`. The rules live in an ordinary table, which merchandisers can edit and which is dumped and replicated like any data:

```sql
CREATE TABLE search_aliases (
    target      tsquery PRIMARY KEY,
    substitute  tsquery NOT NULL
);

INSERT INTO search_aliases VALUES
    (websearch_to_tsquery('english', 'sci-fi'), websearch_to_tsquery('english', '"science fiction"')),
    (to_tsquery('english', 'novel'),            to_tsquery('english', 'novel | book'));

SELECT ts_rewrite(websearch_to_tsquery('english', 'sci-fi novel'),
                  'SELECT target, substitute FROM search_aliases');
```

```text
                  ts_rewrite                   
-----------------------------------------------
 ( 'novel' | 'book' ) & 'scienc' <-> 'fiction'
(1 row)
```

Bazaar's books never use the words "sci-fi" or "novel" (they say "science fiction" and "book"), so the original query finds nothing, and the rewritten one finds 3,424 products. Store the targets and substitutes already normalised (as `tsquery` values produced with the same configuration), as above. `ts_rewrite` matches lexemes, not the text the shopper typed. The table is read on every call, so keep it small, or cache the rules in the application.

The rest of the module uses the built-in `english` configuration, which is what `1301_schema_search.sql` uses. Clean up:

```sql
DROP TEXT SEARCH CONFIGURATION bazaar_english;
DROP TEXT SEARCH DICTIONARY bazaar_synonyms;
DROP TABLE search_aliases;
```

---

## 4. `tsvector`: the document

A `tsvector` is a sorted list of distinct lexemes, each with optional positions and weights. It is a real data type with its own storage format, not a string.

```sql
SELECT 'wireless headphones wireless'::tsvector;
SELECT to_tsvector('english', 'Wireless headphones. Truly wireless!');
```

```text
        tsvector         
-------------------------
 'headphones' 'wireless'
(1 row)

              to_tsvector              
---------------------------------------
 'headphon':2 'truli':3 'wireless':1,4
(1 row)
```

The cast (`::tsvector`) only parses the literal syntax: it doesn't normalise anything, which is why `headphones` survived unstemmed. Casting to `tsvector` is almost always a mistake outside of tests. `to_tsvector` is the function that runs the pipeline.

Each lexeme keeps the list of **positions** where it occurred (`wireless` at 1 and 4). Positions make phrase search and proximity ranking possible. They are capped at 16,383, and at most 256 positions are kept per lexeme, which matters only for very long documents.

### 4.1 Weights

Each position can carry a **weight**: `A`, `B`, `C` or `D` (D is the default and is not displayed). Weights record *where* a word occurred, so that ranking can value a title match above a description match. `setweight` labels a whole `tsvector`, and `||` concatenates two of them, shifting the positions of the right-hand one past the end of the left-hand one:

```sql
SELECT setweight(to_tsvector('english', 'Voltix Headphones'), 'A')
    || setweight(to_tsvector('english', 'Great headphones for travel'), 'B');
```

```text
                      ?column?                       
-----------------------------------------------------
 'great':3B 'headphon':2A,4B 'travel':6B 'voltix':1A
(1 row)
```

This is exactly how Bazaar's `search_vector` is built: title as A, description as B, tags as C. Here is product 44:

```sql
SELECT search_vector FROM products WHERE id = 44;
```

```text
                                                                                                                        search_vector                                                                                                                         
--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
 '850':7A 'aluminium':17B,25B 'bodi':26B 'call':30B 'compact':2A 'design':21B 'ear':5A,11B 'electron':31C 'featur':18B 'headphon':6A,12B,32C 'lightweight':20B 'made':15B 'over-ear':3A,9B 'perfect':27B 'premium':24B 'ultra':8B 'video':29B 'zentro':1A,14B
(1 row)
```

The title was "Zentro Compact Over-Ear Headphones 850". `over-ear` is at position 3, `over` (position 4) was dropped as a stop word, and `ear` is at 5. `headphon` occurs in all three parts, with all three weights.

Useful functions on a `tsvector`: `length(v)` (number of distinct lexemes), `strip(v)` (drop positions and weights; smaller, but no more phrase search or proximity ranking), `setweight(v, 'A', '{lexeme,...}')` (re-weight only some lexemes), `ts_delete(v, 'lexeme')`, `ts_filter(v, '{a,b}')` (keep only positions with the given weights), and `unnest(v)`, which turns it into rows of `(lexeme, positions, weights)`.

```sql
SELECT length(search_vector), strip(search_vector) FROM products WHERE id = 44;
```

```text
 length |                                                                                strip                                                                                 
--------+----------------------------------------------------------------------------------------------------------------------------------------------------------------------
     18 | '850' 'aluminium' 'bodi' 'call' 'compact' 'design' 'ear' 'electron' 'featur' 'headphon' 'lightweight' 'made' 'over-ear' 'perfect' 'premium' 'ultra' 'video' 'zentro'
(1 row)
```

### 4.2 What is in the whole catalog?

`ts_stat` runs a query that returns `tsvector`s and aggregates their lexemes: `ndoc` is the number of documents containing a lexeme, `nentry` the total number of occurrences. It is how you find stop-word candidates in your own domain:

```sql
SELECT word, ndoc FROM ts_stat('SELECT search_vector FROM products') ORDER BY ndoc DESC, word LIMIT 6;
```

```text
   word   |  ndoc  
----------+--------
 featur   | 154571
 made     | 154571
 perfect  | 154571
 electron |  52128
 home     |  44938
 book     |  37379
(6 rows)
```

"made", "perfect" and "features" appear in 77% of all products (every generated description says "made of ... Features ... Perfect for ..."). For Bazaar they are noise. A domain-specific stop-word file would remove them. The whole catalog has only 1,657 distinct lexemes, because the dataset is generated from small vocabularies. Real catalogs have tens or hundreds of thousands.

---

## 5. `tsquery`: the query

A `tsquery` is a tree of lexemes combined with operators:

| Syntax | Meaning |
|---|---|
| `a & b` | both |
| `a \| b` | either |
| `!a` | not |
| `a <-> b` | `b` immediately follows `a` (a phrase) |
| `a <N> b` | `b` is exactly N positions after `a` |
| `a:*` | prefix: any lexeme starting with `a` |
| `a:A`, `a:AB` | `a` with weight A (or A or B) only |
| `( )` | grouping |

`to_tsquery` parses this syntax and normalises each operand with the configuration:

```sql
SELECT to_tsquery('english', 'wireless & (headphones | earbuds) & !gaming');
SELECT to_tsquery('english', 'noise <-> cancelling');
SELECT to_tsquery('english', 'head:*');
SELECT to_tsquery('english', 'wireless:A');
```

```text
                    to_tsquery                    
--------------------------------------------------
 'wireless' & ( 'headphon' | 'earbud' ) & !'game'
(1 row)

     to_tsquery      
---------------------
 'nois' <-> 'cancel'
(1 row)

 to_tsquery 
------------
 'head':*
(1 row)

  to_tsquery  
--------------
 'wireless':A
(1 row)
```

### 5.1 Matching with `@@`

`tsvector @@ tsquery` is true when the document satisfies the query. Phrase operators look at positions:

```sql
SELECT to_tsvector('english', 'noise cancelling')     @@ to_tsquery('english', 'noise <-> cancelling') AS a,
       to_tsvector('english', 'cancelling noise')     @@ to_tsquery('english', 'noise <-> cancelling') AS b,
       to_tsvector('english', 'noise and cancelling') @@ to_tsquery('english', 'noise <-> cancelling') AS c,
       to_tsvector('english', 'noise and cancelling') @@ to_tsquery('english', 'noise <2> cancelling') AS d;
```

```text
 a | b | c | d 
---+---+---+---
 t | f | f | t
(1 row)
```

Case `c` deserves attention: "and" is a stop word, and it was removed, but its *position* was kept. So "cancelling" is two positions after "noise", and `<->` (exactly one) fails. `phraseto_tsquery` (next section) knows this and inserts `<2>` for you when the query itself contains a stop word.

Weight filters restrict a match to parts of the document. On Bazaar:

```sql
SELECT count(*) FROM products WHERE search_vector @@ to_tsquery('english', 'wireless');    -- anywhere
SELECT count(*) FROM products WHERE search_vector @@ to_tsquery('english', 'wireless:A');  -- in the title
```

```text
 count 
-------
  9569
(1 row)

 count 
-------
  5959
(1 row)
```

> **Pitfall:** `@@` also accepts plain text on either side (`text @@ text`, `text @@ tsquery`), in which case it silently calls `to_tsvector` and `plainto_tsquery` with the *default* configuration. It works in psql and then misbehaves in production, where `default_text_search_config` may differ, and it can never use your index. Always build both sides explicitly.

---

## 6. Turning user input into a `tsquery`

`to_tsquery` expects its own syntax. Feed it what a shopper types and it fails:

```sql
SELECT to_tsquery('english', 'wireless headphones');
SELECT to_tsquery('english', 'chef''s knife');
SELECT to_tsquery('english', 'rock & roll &');
```

```text
ERROR:  syntax error in tsquery: "wireless headphones"
ERROR:  syntax error in tsquery: "chef's knife"
ERROR:  no operand in tsquery: "rock & roll &"
```

Never pass raw user input to `to_tsquery`. There are three functions designed for input:

- **`plainto_tsquery`** normalises the text and ANDs all the lexemes. It ignores all punctuation, including quotes and minus signs.
- **`phraseto_tsquery`** does the same but joins the lexemes with `<->` (and `<N>` across stop words), so they must appear as a phrase.
- **`websearch_to_tsquery`** understands the syntax people know from web search engines: `"quoted phrase"`, `or`/`OR`, and `-word` for exclusion. Everything else is treated as plain words. **It never raises a syntax error**, whatever the input.

```sql
SELECT plainto_tsquery('english', 'wireless headphones'),
       plainto_tsquery('english', 'rock & roll | !'),
       plainto_tsquery('english', '"noise cancelling" -gaming');
SELECT phraseto_tsquery('english', 'noise cancelling headphones');
```

```text
     plainto_tsquery     | plainto_tsquery |      plainto_tsquery       
-------------------------+-----------------+----------------------------
 'wireless' & 'headphon' | 'rock' & 'roll' | 'nois' & 'cancel' & 'game'
(1 row)

          phraseto_tsquery          
------------------------------------
 'nois' <-> 'cancel' <-> 'headphon'
(1 row)
```

Note the last `plainto_tsquery`: the shopper said *not* gaming, and the query now *requires* gaming. `websearch_to_tsquery` gets it right:

```sql
SELECT q, websearch_to_tsquery('english', q)
FROM (VALUES
    ('wireless headphones'),
    ('"noise cancelling" headphones'),
    ('headphones -gaming'),
    ('earbuds or headphones'),
    ('laptop OR tablet -refurbished'),
    ('rock & roll | !'),
    ('"unterminated quote'),
    ('the'),
    ('C++ & (('),
    ('head*')
) AS v (q);
```

```text
               q               |        websearch_to_tsquery        
-------------------------------+------------------------------------
 wireless headphones           | 'wireless' & 'headphon'
 "noise cancelling" headphones | 'nois' <-> 'cancel' & 'headphon'
 headphones -gaming            | 'headphon' & !'game'
 earbuds or headphones         | 'earbud' | 'headphon'
 laptop OR tablet -refurbished | 'laptop' | 'tablet' & !'refurbish'
 rock & roll | !               | 'rock' & 'roll'
 "unterminated quote           | 'untermin' <-> 'quot'
 the                           | 
 C++ & ((                      | 'c'
 head*                         | 'head'
(10 rows)

NOTICE:  text-search query contains only stop words or doesn't contain lexemes, ignored
```

Things to know about `websearch_to_tsquery`:

- **`AND` binds tighter than `OR`**, as in SQL: `laptop OR tablet -refurbished` means `laptop | (tablet & !refurbish)`.
- **A query of only stop words becomes an empty `tsquery`**, with a NOTICE. An empty query matches nothing. Check for it with `numnode(q) = 0` and decide what to do (show nothing, show popular products, or fall back to another method).
- **There is no prefix syntax.** `head*` searches for `head`. For search-as-you-type you build prefix queries yourself (section 13).
- It is the right default for any search box. The function in this module's milestone uses it.

| Function | Input | Operators understood | Can raise an error |
|---|---|---|---|
| `to_tsquery` | query syntax | all | yes |
| `plainto_tsquery` | free text | none (all ANDed) | no |
| `phraseto_tsquery` | free text | none (all phrase-joined) | no |
| `websearch_to_tsquery` | free text | `"..."`, `or`, `-` | no |

> **Pitfall:** Building a query string for `to_tsquery` by concatenating user words with `' & '` is an injection bug waiting to happen: one word containing `|`, `!` or `(` changes the meaning or raises an error. If you need operators that `websearch_to_tsquery` doesn't offer, build the `tsquery` from *values*, not from text: normalise each word with `plainto_tsquery` and combine the results with the `&&`, `||`, `!!` and `<->` operators on `tsquery` (or `tsquery_phrase(a, b, distance)`).

---

## 7. Ranking

`@@` tells you *whether* a product matches. A shopper sees only the first page, so the order is what makes search good or bad. PostgreSQL has two ranking functions. Both return a `real`, larger is better, and both must be computed for every matching row before you can sort, which is the main performance consideration of this section.

- **`ts_rank(vector, query)`** looks at how often the query's lexemes occur in the document, weighted by the weights of the positions where they occur.
- **`ts_rank_cd(vector, query)`** ("cover density") looks at the *covers*: the shortest stretches of the document that contain all the query's terms. Short, dense covers rank high, so it rewards terms that appear close together. It needs positions (it returns 0 for a stripped vector).

Both take an optional **weights array** `{D, C, B, A}` (note the order), default `{0.1, 0.2, 0.4, 1.0}`: an A occurrence counts ten times as much as a D occurrence. And both take an optional **normalization** bitmask that adjusts for document length:

| Flag | Divides the rank by |
|---|---|
| 0 | nothing (the default) |
| 1 | 1 + log(document length) |
| 2 | the document length |
| 4 | the mean harmonic distance between extents (`ts_rank_cd` only) |
| 8 | the number of unique words in the document |
| 16 | 1 + log(number of unique words) |
| 32 | rank / (rank + 1), which maps any rank into the range 0–1 |

Flags combine with `|` (for example `2|32`). Five tiny documents show the differences:

```sql
WITH docs (doc, v) AS (VALUES
    ('title hit',  setweight(to_tsvector('english', 'Voltix Wireless Headphones'), 'A')),
    ('desc hit',   setweight(to_tsvector('english', 'Voltix Studio Monitor'), 'A')
                || setweight(to_tsvector('english', 'Pairs well with wireless headphones'), 'B')),
    ('tag hit',    setweight(to_tsvector('english', 'Voltix Studio Monitor'), 'A')
                || setweight(to_tsvector('english', 'wireless headphones'), 'C')),
    ('far apart',  setweight(to_tsvector('english', 'Wireless charging pad with a cable for headphones'), 'A')),
    ('long title', setweight(to_tsvector('english', 'Wireless Headphones with a very long marketing title that goes on and on about features nobody reads'), 'A'))
)
SELECT doc,
       ts_rank(v, q)        AS rank,
       ts_rank_cd(v, q)     AS rank_cd,
       ts_rank(v, q, 1)     AS rank_n1,
       ts_rank_cd(v, q, 32) AS rank_cd_n32
FROM docs, websearch_to_tsquery('english', 'wireless headphones') AS q
ORDER BY rank DESC;
```

```text
    doc     |    rank    |  rank_cd   |  rank_n1   | rank_cd_n32 
------------+------------+------------+------------+-------------
 title hit  |  0.9910322 |          1 |  0.4955161 |         0.5
 long title |  0.9910322 |          1 | 0.29833043 |         0.5
 far apart  |  0.7614762 | 0.14285715 |  0.2945792 |       0.125
 desc hit   | 0.39641288 |        0.4 | 0.13213763 |   0.2857143
 tag hit    | 0.19820644 |        0.2 | 0.07667672 |  0.16666667
(5 rows)
```

Reading the columns:

- **Weights work as designed**: title (A) beats description (B), which beats tags (C), in both functions.
- **`ts_rank` barely cares about proximity.** "Wireless charging pad with a cable for headphones" (a charging pad!) ranks 0.76, almost as high as a real headphone. `ts_rank_cd` gives it 0.14, below a description match. For product search, where the query words usually form a phrase in the good results, `ts_rank_cd` is usually the better choice.
- **Without normalization, length doesn't matter.** The long title ties with the short one. Flag 1 penalises it (0.30 versus 0.50). Whether you want that depends on the corpus: for articles, normalising by length stops long documents from winning just by repeating words; for product titles, length is mostly noise.
- **Flag 32 bounds the result** to the range 0–1, which helps when you combine it with other signals (next section).

On the real catalog:

```sql
SELECT id, title, ts_rank(search_vector, q) AS rank
FROM products, websearch_to_tsquery('english', 'wireless headphones') AS q
WHERE search_vector @@ q
ORDER BY rank DESC
LIMIT 10;
```

```text
   id   |               title                |    rank    
--------+------------------------------------+------------
 110124 | Orbitron Wireless Headphones 251   |  0.9991941
 127587 | Quanta Wireless Headphones 447     |  0.9991906
 134112 | Helix Wireless Headphones 339      | 0.99918926
  27305 | Orbitron Wireless Headphones 688   | 0.99645185
  72202 | Brightline Wireless Headphones 618 | 0.99644685
  85049 | Quanta Wireless Headphones 773     | 0.99644685
 151648 | Zentro Wireless Headphones 855     | 0.99644685
  58449 | Voltix Wireless Headphones 360     | 0.99644685
 176828 | Orbitron Wireless Headphones 292   | 0.99644685
 184768 | Lumio Wireless Headphones 451      | 0.99644685
(10 rows)
```

All ten are relevant, but the ranks are almost identical. Text relevance alone can't tell these products apart, and that is normal for a product catalog: many products match equally well. What separates them is everything the text doesn't know.

### 7.1 Combining text rank with business signals

Real product search blends text relevance with signals such as rating, sales, stock, margin or recency. Some rules make the blend behave:

1. **Normalise each signal to a comparable range (0–1)** before weighting it. A raw `ts_rank` near 1 plus a raw sales count of 700 is just "sort by sales".
2. **Dampen counts with a logarithm.** The 700th sale says much less than the 7th. `ln(1 + units) / ln(1 + cap)`, capped at 1, is a common shape.
3. **Don't trust small samples.** A product with one 5-star review should not outrank one with 200 reviews averaging 4.8. A **Bayesian average** adds a few virtual reviews at the global mean: `(C × m + sum(ratings)) / (C + n)`. With `C = 5` and Bazaar's global mean `m = 4.09`, one 5-star review gives 4.24, and 200 reviews at 4.8 give 4.78.
4. **Make relevance dominant**, so that a best-seller cannot beat a product that actually matches the query.

Bazaar's global mean rating, for the Bayesian average:

```sql
SELECT round(avg(rating), 3) AS mean_rating, count(*) FROM reviews;
```

```text
 mean_rating | count  
-------------+--------
       4.091 | 292355
(1 row)
```

The other practical problem is cost. Looking up ratings and sales for *every* matching product is expensive when a query matches 30,000 products. The standard solution is **two-phase ranking**: take the top few hundred candidates by text relevance (cheap: one `tsvector` per row, already in the heap), then compute the expensive signals only for them and re-rank:

```sql
WITH q AS (
    SELECT websearch_to_tsquery('english', 'wireless headphones') AS q
), candidates AS (
    SELECT p.id, p.title, ts_rank_cd(p.search_vector, q.q, 32) AS text_rank
    FROM products p, q
    WHERE p.search_vector @@ q.q
      AND p.status = 'active'
    ORDER BY text_rank DESC
    LIMIT 200
)
SELECT c.id, c.title, round(c.text_rank::numeric, 3) AS tr, r.avg_rating, r.n_reviews, s.units
FROM candidates c
LEFT JOIN LATERAL (
    SELECT round(avg(rating), 2) AS avg_rating, count(*) AS n_reviews
    FROM reviews WHERE product_id = c.id
) r ON true
LEFT JOIN LATERAL (
    SELECT coalesce(sum(quantity), 0) AS units
    FROM order_items WHERE product_id = c.id
) s ON true
ORDER BY s.units DESC
LIMIT 10;
```

```text
  id   |                  title                  |  tr   | avg_rating | n_reviews | units 
-------+-----------------------------------------+-------+------------+-----------+-------
 54188 | Helix Wireless Over-Ear Headphones 654  | 0.389 |       4.56 |         9 |    36
 16660 | Quanta Wireless Headphones 999          | 0.500 |       4.60 |         5 |    32
  6628 | Voltix Slim Headphones 715              | 0.407 |       4.20 |         5 |    30
 10666 | Kairo Max Over-Ear Headphones 222       | 0.278 |       4.40 |         5 |    27
  9142 | Nordvik Wireless Headphones 390         | 0.500 |       5.00 |         6 |    25
  5771 | Nordvik Wireless Headphones 684         | 0.500 |       3.50 |         2 |    24
 11584 | Kairo Ultra Headphones 230              | 0.407 |       4.00 |         3 |    24
  6992 | Sonora Wireless Over-Ear Headphones 507 | 0.200 |       4.25 |         8 |    24
 51401 | Sonora Pro Over-Ear Headphones 249      | 0.278 |       4.33 |         3 |    23
 11854 | Orbitron Lite Over-Ear Headphones 829   | 0.278 |       5.00 |         5 |    23
(10 rows)
```

This query took 15 ms. It sorts by sales only, to show the signals side by side. The milestone function combines them as `0.60 × relevance + 0.25 × rating + 0.15 × popularity`, each scaled to 0–1. The weights are a product decision, not a law: tune them by looking at real queries and real clicks.

> **In production:** The candidate cut-off is a trade-off. A product that ranks 201st on text but is a runaway best-seller never gets a chance. For large catalogs, precompute the business signals into a table or materialized view (`product_stats(product_id, units_sold_90d, bayes_rating, ...)`, refreshed periodically, see [Module 09](../09-views-functions-triggers/)), so that you can afford to score all matches, or a few thousand of them, cheaply.

### 7.2 What ranking costs

Ranking needs the `tsvector` of every matching row. A specific query is cheap. A broad one is not:

```sql
EXPLAIN (ANALYZE, COSTS OFF)
SELECT id, title, ts_rank(search_vector, q) AS rank
FROM products, websearch_to_tsquery('english', 'gift') AS q
WHERE search_vector @@ q
ORDER BY rank DESC
LIMIT 10;
```

```text
                                                   QUERY PLAN                                                    
-----------------------------------------------------------------------------------------------------------------
 Limit (actual time=78.436..78.440 rows=10 loops=1)
   ->  Sort (actual time=78.434..78.436 rows=10 loops=1)
         Sort Key: (ts_rank(products.search_vector, '''gift'''::tsquery)) DESC
         Sort Method: top-N heapsort  Memory: 25kB
         ->  Bitmap Heap Scan on products (actual time=10.146..70.446 rows=33715 loops=1)
               Recheck Cond: (search_vector @@ '''gift'''::tsquery)
               Heap Blocks: exact=15441
               ->  Bitmap Index Scan on products_search_vector_idx (actual time=6.899..6.900 rows=33715 loops=1)
                     Index Cond: (search_vector @@ '''gift'''::tsquery)
 Planning Time: 0.343 ms
 Execution Time: 78.473 ms
(11 rows)
```

The index found 33,715 products in 7 ms. The other 70 ms went into visiting 15,441 heap pages to read and rank every one of them. A GIN index can find matches but cannot return them in rank order, so `LIMIT 10` doesn't help: the sort needs all the rows first. (The `rum` extension, an index type that stores positions and can return rows in rank order, solves exactly this. It is not part of core PostgreSQL.) For the same reason, a broad query like `gift` is a good test case for any search function you write.

---

## 8. Highlighting with `ts_headline`

`ts_headline(config, document, query, options)` returns a fragment of the original text with the matching words marked:

```sql
SELECT ts_headline('english', description, websearch_to_tsquery('english', 'wireless headphones'))
FROM products WHERE id = 110124;
```

```text
                                                     ts_headline                                                     
---------------------------------------------------------------------------------------------------------------------
 <b>Wireless</b> <b>headphones</b> from Orbitron, made of glass. Features fast charging and a low-latency connection
(1 row)
```

The options string controls the output:

| Option | Default | Meaning |
|---|---|---|
| `StartSel`, `StopSel` | `<b>`, `</b>` | the markers around each match |
| `MaxWords`, `MinWords` | 35, 15 | the length of the fragment, in words |
| `ShortWord` | 3 | words this short at the edges of a fragment are dropped |
| `HighlightAll` | false | highlight the whole document instead of a fragment |
| `MaxFragments` | 0 | when > 0, return up to this many separate fragments |
| `FragmentDelimiter` | ` ... ` | the separator between fragments |

```sql
SELECT ts_headline('english', description, websearch_to_tsquery('english', 'noise cancelling'),
                   'MaxFragments=2, MaxWords=8, MinWords=3, FragmentDelimiter=" … "')
FROM products
WHERE search_vector @@ websearch_to_tsquery('english', 'noise cancelling')
  AND length(description) > 150
LIMIT 3;
```

```text
                               ts_headline                                
--------------------------------------------------------------------------
 recycled plastic. Features <b>noise</b> <b>cancelling</b>
 recycled plastic. Features <b>noise</b> <b>cancelling</b>
 assistant support and <b>noise</b> <b>cancelling</b>
(3 rows)
```

> **Pitfall:** `ts_headline` does **not** escape HTML. If a seller writes `<script>` into a description, the snippet contains it, next to your `<b>` tags. Either escape the text before highlighting and use markers that can't appear in escaped text, or highlight with neutral markers (`StartSel=[[, StopSel=]]`) and convert them to HTML after escaping in the application.

### 8.1 Its cost: only run it on the rows you return

`ts_headline` doesn't use the `tsvector`. It re-parses the *original document* with the full pipeline, every time, which makes it by far the most expensive function in this module. The rule: **compute the snippets for the 20 rows on the page, never for all the matches.**

PostgreSQL helps you in the simplest case (it has done so since version 9.6). With the headline in the select list and `ORDER BY rank LIMIT 20`, the planner evaluates the select list *after* the sort and limit (that is the `Result` node above the `Sort` below):

```sql
EXPLAIN (ANALYZE, COSTS OFF)
SELECT id, title, ts_headline('english', description, q) AS snippet
FROM products, websearch_to_tsquery('english', 'gift') AS q
WHERE search_vector @@ q
ORDER BY ts_rank_cd(search_vector, q) DESC
LIMIT 20;
```

```text
                                                      QUERY PLAN                                                       
-----------------------------------------------------------------------------------------------------------------------
 Limit (actual time=78.231..78.646 rows=20 loops=1)
   ->  Result (actual time=78.229..78.641 rows=20 loops=1)
         ->  Sort (actual time=78.148..78.150 rows=20 loops=1)
               Sort Key: (ts_rank_cd(products.search_vector, '''gift'''::tsquery)) DESC
               Sort Method: top-N heapsort  Memory: 30kB
               ->  Bitmap Heap Scan on products (actual time=7.674..70.623 rows=33715 loops=1)
...
 Execution Time: 78.681 ms
```

But it only takes a slightly different query shape to lose that. Put the snippet in a materialized CTE (or a view with `DISTINCT`, or a window function, or anything that has to produce all rows before the `LIMIT`) and every match gets a headline:

```sql
EXPLAIN (ANALYZE, COSTS OFF)
WITH hits AS MATERIALIZED (
    SELECT id, title,
           ts_headline('english', description, q) AS snippet,
           ts_rank_cd(search_vector, q)           AS rank
    FROM products, websearch_to_tsquery('english', 'gift') AS q
    WHERE search_vector @@ q
)
SELECT id, title, snippet FROM hits ORDER BY rank DESC LIMIT 20;
```

```text
                                                    QUERY PLAN                                                     
-------------------------------------------------------------------------------------------------------------------
 Limit (actual time=269.096..275.799 rows=20 loops=1)
   CTE hits
     ->  Gather (actual time=11.465..258.504 rows=33715 loops=1)
           Workers Planned: 2
           Workers Launched: 2
           ->  Parallel Bitmap Heap Scan on products (actual time=8.757..256.177 rows=11238 loops=3)
...
 Execution Time: 277.006 ms
```

Three and a half times slower *with three CPU cores* doing the work, for 20 visible snippets. Bazaar's descriptions are about 130 characters long. With real descriptions of a few kilobytes, the difference grows to seconds. Make the order of operations explicit, so that no refactoring can break it: select the top N in a subquery or CTE, and call `ts_headline` in the outer query.

```sql
SELECT id, title, ts_headline('english', description, q) AS snippet
FROM (
    SELECT id, title, description, q
    FROM products, websearch_to_tsquery('english', 'gift') AS q
    WHERE search_vector @@ q
    ORDER BY ts_rank_cd(search_vector, q) DESC
    LIMIT 20
) AS top;
```

---

## 9. Storing the search document

You can search `to_tsvector('english', title)` on the fly, but then every query re-parses every row:

```sql
EXPLAIN (ANALYZE, COSTS OFF)
SELECT count(*) FROM products
WHERE to_tsvector('english', title || ' ' || coalesce(description, ''))
      @@ websearch_to_tsquery('english', 'wireless headphones');
```

```text
                                                                              QUERY PLAN                                                                              
----------------------------------------------------------------------------------------------------------------------------------------------------------------------
 Finalize Aggregate (actual time=1871.507..1877.974 rows=1 loops=1)
   ->  Gather (actual time=1871.497..1877.966 rows=3 loops=1)
         Workers Planned: 2
         Workers Launched: 2
         ->  Partial Aggregate (actual time=1867.035..1867.037 rows=1 loops=3)
               ->  Parallel Seq Scan on products (actual time=39.033..1866.897 rows=91 loops=3)
                     Filter: (to_tsvector('english'::regconfig, ((title || ' '::text) || COALESCE(description, ''::text))) @@ '''wireless'' & ''headphon'''::tsquery)
                     Rows Removed by Filter: 66576
 Planning Time: 0.173 ms
 Execution Time: 1878.055 ms
(10 rows)
```

Almost two seconds with three cores. The parser is the expensive part, so it must run once per row *write*, not once per row per *search*. There are three ways to arrange that.

### 9.1 Option 1: a stored generated column (Bazaar's choice)

```sql
-- from project/db/1301_schema_search.sql
ALTER TABLE products
    ADD COLUMN search_vector tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A')
        || setweight(to_tsvector('english', coalesce(description, '')), 'B')
        || setweight(to_tsvector('english', tags_to_text(tags)), 'C')
    ) STORED;

CREATE INDEX products_search_vector_idx ON products USING gin (search_vector);
```

PostgreSQL computes the column on every `INSERT`, and on every `UPDATE` that assigns one of its input columns (`title`, `description` or `tags`, even to the same value); an `UPDATE` that only sets `price` keeps the stored value without re-parsing. It can never be stale, no application code is involved, and queries read the stored value. The details that make it work:

- **Generation expressions must be immutable.** `to_tsvector('english', ...)` with a constant configuration is immutable. `to_tsvector(text)`, the one-argument form, is only *stable*, because it depends on the `default_text_search_config` setting, so it is rejected.
- **`coalesce` matters.** `NULL || anything` is NULL. Without `coalesce`, the products whose description is NULL (9,881 of them) would get a NULL `search_vector` and could never be found, even by title.
- **`array_to_string` is not immutable**, because for some element types the result depends on settings. For `text[]` it is safe, so 1301 wraps it in a function declared `IMMUTABLE`, `tags_to_text()`. Declaring a function immutable is a *promise* that PostgreSQL can't check: if you break it, indexes and generated columns built on the function silently go wrong.
- **Cost:** 278 bytes per row on average (about 53 MB for the table), and the parsing work moves to writes. Adding the column rewrote the whole table under an `ACCESS EXCLUSIVE` lock (the whole 1301 file, indexes included, takes about 16 seconds for 200,000 rows). On a big live table, prefer adding a plain column, back-filling it in batches, and then using a trigger (option 3).

Ranking reads `search_vector` straight from the heap, which is why the `gift` query of section 7.2 ranked 33,715 rows in 70 ms.

### 9.2 Option 2: an expression index

```sql
CREATE INDEX ... ON products USING gin (to_tsvector('english', title || ' ' || coalesce(description, '')));
```

No extra column, a smaller table, and the index is maintained automatically. The catch: the `tsvector` exists only *inside the index*, and GIN can't hand it back. Two consequences, measured on a copy of the products table (`lab.docs`) with such an index (8.8 MB). To follow along, build the copy first (Module 11's `lab` schema doesn't survive a reset):

```sql
CREATE SCHEMA IF NOT EXISTS lab;
CREATE TABLE lab.docs AS SELECT id, title, description FROM products;
CREATE INDEX docs_fts_idx ON lab.docs
    USING gin (to_tsvector('english', title || ' ' || coalesce(description, '')));
```

- **Queries must repeat the exact expression**, or the index isn't used. Writing `title || ' ' || description` (without the `coalesce`) is a *different* expression:

  ```text
   Limit (actual time=0.357..8.362 rows=10 loops=1)
     ->  Seq Scan on docs (actual time=0.357..8.358 rows=10 loops=1)
           Filter: (to_tsvector('english'::regconfig, ((title || ' '::text) || description)) @@ '''gift'''::tsquery)
  ```

  Wrap the expression in an immutable SQL function (`product_document(p products)`) or a view, so that every query spells it the same way.

- **Ranking re-parses every match**, because `ts_rank` needs the vector:

  ```text
   Limit (actual time=149.684..149.687 rows=10 loops=1)
     ->  Sort (actual time=149.682..149.684 rows=10 loops=1)
           Sort Key: (ts_rank(to_tsvector('english'::regconfig, ((title || ' '::text) || COALESCE(description, ''::text))), '''gift'''::tsquery)) DESC
           Sort Method: top-N heapsort  Memory: 25kB
           ->  Bitmap Heap Scan on docs (actual time=0.837..148.025 rows=4285 loops=1)
                 Recheck Cond: (to_tsvector('english'::regconfig, ((title || ' '::text) || COALESCE(description, ''::text))) @@ '''gift'''::tsquery)
                 Heap Blocks: exact=2858
  ```

  150 ms to rank 4,285 rows, about 35 µs per row, against about 2 µs per row with a stored column. An expression index is fine for filter-only search (`WHERE ... @@ ...` with a simple sort), and wasteful for ranked search. Weighted documents also make the expression long and easy to mistype.

### 9.3 Option 3: a trigger-maintained column

Before generated columns existed (PostgreSQL 12), a `BEFORE INSERT OR UPDATE` trigger filled a regular `tsvector` column. PostgreSQL even ships a ready-made trigger function:

```sql
ALTER TABLE lab.docs ADD COLUMN tsv tsvector;

CREATE TRIGGER docs_tsv_update
    BEFORE INSERT OR UPDATE OF title, description ON lab.docs
    FOR EACH ROW
    EXECUTE FUNCTION tsvector_update_trigger(tsv, 'pg_catalog.english', title, description);

UPDATE lab.docs SET title = title;   -- back-fill tsv for the existing rows, through the trigger
```

(`tsvector_update_trigger_column` takes the configuration from a `regconfig` column instead.) The built-in one can't assign weights, so in practice you write your own PL/pgSQL trigger function. A trigger is the right choice when:

- the document includes data from **other tables** (the category name, the seller's shop name, the latest review titles), which a generated column can't reference. But then you must also update products when *those* tables change, and that is where trigger-maintained search documents usually rot;
- you want to **back-fill gradually** on a large live table instead of rewriting it in one locked step;
- you need logic that isn't immutable. (Skipping the work when irrelevant columns change is not a reason: `UPDATE OF title, description` does that for a trigger, and a generated column already does it on its own.)

| | Generated column | Expression index | Trigger column |
|---|---|---|---|
| Always in sync | yes | yes | only for the triggering table |
| Extra table storage | yes | no | yes |
| Ranking cost | read stored value | re-parse each match | read stored value |
| Can use other tables | no | no | yes, with extra triggers |
| Adding it to a big table | full rewrite under lock | `CREATE INDEX CONCURRENTLY` | add column, back-fill in batches |
| Query must repeat the expression | no | yes | no |

### 9.4 Indexes for `tsvector`: GIN and GiST

You met both index types in [Module 11](../11-indexes/). For full-text search:

**GIN** (Generalized Inverted Index) stores, for each lexeme, the list of rows that contain it, exactly like the index at the back of a book. `wireless & headphon` intersects two lists. It is precise for plain lexemes and fast at reading. It doesn't store positions or weights, so phrase queries (`<->`) and weight-restricted queries (`:A`) must be **rechecked** against the heap row (you see `Rows Removed by Index Recheck` in plans), and it can't help with ranking.

**GiST** stores a fixed-size *signature* of each document (a hashed bitmap of its lexemes), arranged in a tree. Signatures of different documents can collide, so every match is a candidate that must be rechecked. GiST is classically described as smaller and faster to update, but slower to search. On Bazaar's data, measured on a copy of `search_vector`:

| Index | Build time | Size | `wireless & headphon`, index scan |
|---|---|---|---|
| GIN | 1.3 s | 10 MB | 0.6 ms |
| GiST | 4.0 s | 24 MB | 9.1 ms |

GiST lost on every count here. Bazaar's documents are short and its vocabulary is tiny, which makes GIN's lists compact. GiST's fixed signature (124 bytes by default, adjustable with `gist_tsvector_ops(siglen = ...)`) is relatively large. Use GIN for full-text search unless measurements on *your* data say otherwise.

> **Pitfall:** With both indexes present, the planner picked the GiST index for that query, because its cost estimates for the two index types are rough. Never keep two index types "to let the planner choose". Measure, and keep the one that wins.

### 9.5 GIN's pending list (`fastupdate`)

Inserting one product adds about 18 lexemes, so a GIN index must update about 18 posting lists in different parts of the index: random I/O for every insert. To soften this, GIN indexes have **`fastupdate`** (on by default): new entries are appended to an unsorted **pending list**, and merged into the main structure in bulk when the list exceeds `gin_pending_list_limit` (4 MB by default), when (auto)vacuum or analyze runs on the table, or when you call `gin_clean_pending_list()`.

The `pgstattuple` extension shows the pending list. On the copy table, with a GIN index on `tsv`, after inserting 50,000 rows:

```sql
CREATE INDEX docs_tsv_idx ON lab.docs USING gin (tsv);
INSERT INTO lab.docs (id, title, description)
SELECT id, title, description FROM products LIMIT 50000;

CREATE EXTENSION IF NOT EXISTS pgstattuple;
SELECT * FROM pgstatginindex('lab.docs_tsv_idx');
```

```text
 version | pending_pages | pending_tuples 
---------+---------------+----------------
       2 |            45 |           1056
(1 row)
```

Searches must scan the whole pending list sequentially in addition to the index, which shows up as extra buffers in the index scan (49 buffers, against 4 after `SELECT gin_clean_pending_list('lab.docs_tsv_idx')`). And the insert that happens to overflow the list pays for merging all of it, which makes for an occasional slow insert.

The same 50,000-row insert took 2.0 s with `fastupdate` on, and 4.8 s after `ALTER INDEX lab.docs_tsv_idx SET (fastupdate = off)`. Keep it on for write-heavy tables. Turn it off, or lower `gin_pending_list_limit` for that index, if your latency requirements can't tolerate the occasional insert that flushes the list, or if searches must be consistently fast right after bulk writes.

> **In production:** For a big bulk load, it is usually fastest to drop the GIN index, load, and rebuild it with a generous `maintenance_work_mem` (1301 sets 512 MB). A GIN build sorts all entries once, which is far cheaper than inserting them one by one.

---

## 10. Multilingual search

Bazaar is English-only, but a marketplace rarely stays that way. The rule that drives all the designs: **documents and queries must be normalised with the same configuration.** A German stemmer turns "Kopfhörer" into `kopfhor`; an English one leaves `kopfhörer`. They never match.

**One language per row.** Store the language in a `regconfig` column and use it in the generated column. The two-argument `to_tsvector(regconfig, text)` is immutable even when the configuration comes from a column, because the result depends only on the arguments:

```sql
CREATE TABLE lab.reviews_i18n (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lang      regconfig NOT NULL,
    body      text NOT NULL,
    body_tsv  tsvector GENERATED ALWAYS AS (to_tsvector(lang, body)) STORED
);

INSERT INTO lab.reviews_i18n (lang, body) VALUES
    ('english', 'Great headphones, the batteries last for days'),
    ('french',  'Très bons écouteurs, les batteries durent des jours'),
    ('german',  'Tolle Kopfhörer, die Batterien halten tagelang'),
    ('simple',  'Super Kopfhörer!');

SELECT lang, body_tsv FROM lab.reviews_i18n;
```

```text
  lang   |                              body_tsv                               
---------+---------------------------------------------------------------------
 english | 'batteri':4 'day':7 'great':1 'headphon':2 'last':5
 french  | 'batter':5 'bon':2 'durent':6 'jour':8 'le':4 'tres':1 'écouteur':3
 german  | 'batteri':4 'halt':5 'kopfhor':2 'tagelang':6 'toll':1
 simple  | 'kopfhörer':2 'super':1
(4 rows)
```

At query time, you either know the shopper's language and use it, or you OR the query across the languages you support, so that each row can match the variant built with its own language:

```sql
SELECT id, lang
FROM lab.reviews_i18n
WHERE body_tsv @@ (websearch_to_tsquery('english', 'batterie')
                || websearch_to_tsquery('french',  'batterie')
                || websearch_to_tsquery('german',  'batterie'));
```

```text
 id |  lang   
----+---------
  1 | english
  2 | french
  3 | german
(3 rows)
```

The OR can produce false positives (an English stem matching an unrelated French word), and the row stored with `simple` ("Kopfhörer") is found only by a `simple` query. Other designs:

- **One column per language** (`search_en`, `search_fr`) when every document exists in several languages (translated product pages). Each has its own index, and the query picks the column.
- **`simple` as a universal fallback**: no stemming, but no wrong stemming either. It works for any language whose words are separated by spaces.
- **Languages without spaces between words** (Chinese, Japanese, Thai) need a different parser. Core PostgreSQL doesn't include one. Extensions such as `zhparser` or `pgroonga` do, or you use trigrams (next section), which don't care about word boundaries at all.

Detecting the language of a document belongs in the application (or the seller's settings), not in SQL.

---

## 11. Fuzzy matching with `pg_trgm`

Full-text search matches *words*. It does nothing for typos (`headphnes`), partial words (`headph`), or substrings (`%skillet%`). Those are the job of **trigrams**.

### 11.1 Trigrams and similarity

A trigram is three consecutive characters. `pg_trgm` lower-cases the text, splits it into words (on anything that isn't a letter or digit), pads each word with two spaces in front and one behind, and collects the distinct trigrams:

```sql
SELECT show_trgm('headphones'), show_trgm('headphnes');
```

```text
                      show_trgm                      |                    show_trgm                    
-----------------------------------------------------+-------------------------------------------------
 {"  h"," he",adp,dph,ead,"es ",hea,hon,nes,one,pho} | {"  h"," he",adp,dph,ead,"es ",hea,hne,nes,phn}
(1 row)
```

They share 8 trigrams out of 13 distinct ones in total. **`similarity(a, b)`** is exactly that ratio, shared / total: 8 / 13 = 0.615. A typo destroys only the two or three trigrams around it, so misspelled words stay similar. The padding gives word *starts* extra weight (`"  h"`, `" he"`), which matches how people type: the beginning is usually right.

Comparing a short query with a long title is a different problem. The title has many trigrams that the query doesn't have, so whole-string similarity is low even for a perfect match. **`word_similarity(query, text)`** instead finds the best-matching stretch of `text` and compares the query with that. **`strict_word_similarity`** does the same but snaps the stretch to whole words:

```sql
SELECT similarity('headphones', 'headphnes')                                 AS sim_words,
       similarity('headphnes', 'Voltix Wireless Headphones 527')             AS sim_title,
       word_similarity('headphnes', 'Voltix Wireless Headphones 527')        AS word_sim,
       strict_word_similarity('headphnes', 'Voltix Wireless Headphones 527') AS strict_word_sim;
```

```text
 sim_words  | sim_title  |  word_sim  | strict_word_sim 
------------+------------+------------+-----------------
 0.61538464 | 0.24242425 | 0.61538464 |      0.61538464
(1 row)
```

For "does this title contain something like what I typed", use `word_similarity`.

### 11.2 Operators and thresholds

Each similarity function has a boolean operator that compares the result with a threshold setting, and a distance operator (`1 − similarity`) for ordering:

| Function | Boolean operator | Threshold setting (default) | Distance operator |
|---|---|---|---|
| `similarity(a, b)` | `a % b` | `pg_trgm.similarity_threshold` (0.3) | `a <-> b` |
| `word_similarity(a, b)` | `a <% b` (or `b %> a`) | `pg_trgm.word_similarity_threshold` (0.6) | `a <<-> b` |
| `strict_word_similarity(a, b)` | `a <<% b` (or `b %>> a`) | `pg_trgm.strict_word_similarity_threshold` (0.5) | `a <<<-> b` |

**Only the operators can use an index.** `WHERE similarity(title, 'x') > 0.3` is a function call that the planner can't look inside, so it scans the table. `WHERE title % 'x'` is an indexable operator. Use the operator to filter, and the function to sort or display.

```sql
EXPLAIN (ANALYZE, COSTS OFF)
SELECT count(*) FROM products WHERE title % 'headphnes';
```

```text
                                               QUERY PLAN                                               
--------------------------------------------------------------------------------------------------------
 Aggregate (actual time=117.869..117.871 rows=1 loops=1)
   ->  Bitmap Heap Scan on products (actual time=13.931..117.830 rows=41 loops=1)
         Recheck Cond: (title % 'headphnes'::text)
         Rows Removed by Index Recheck: 12127
         Heap Blocks: exact=9013
         ->  Bitmap Index Scan on products_title_trgm_idx (actual time=7.238..7.239 rows=12168 loops=1)
               Index Cond: (title % 'headphnes'::text)
 Planning Time: 0.806 ms
 Execution Time: 117.983 ms
(9 rows)
```

```sql
EXPLAIN (ANALYZE, COSTS OFF)
SELECT count(*) FROM products WHERE 'headphnes' <% title;
```

```text
                                              QUERY PLAN                                               
-------------------------------------------------------------------------------------------------------
 Aggregate (actual time=19.987..19.988 rows=1 loops=1)
   ->  Bitmap Heap Scan on products (actual time=2.994..19.767 rows=1819 loops=1)
         Filter: ('headphnes'::text <% title)
         Heap Blocks: exact=1734
         ->  Bitmap Index Scan on products_title_trgm_idx (actual time=2.710..2.710 rows=1819 loops=1)
               Index Cond: (title %> 'headphnes'::text)
 Planning Time: 1.639 ms
 Execution Time: 20.052 ms
(8 rows)
```

The two plans tell the whole story. `%` compares the query with whole titles: only 41 titles pass the 0.3 threshold, the index produced 12,168 candidates that share *some* trigrams, and 12,127 of them were thrown away after reading them from the heap. `<%` finds all 1,819 products with "Headphones" in the title, and the index's candidates were exactly the matches. (Note how the planner rewrote `'headphnes' <% title` into the commuted form `title %> 'headphnes'` to match the index.)

The thresholds are ordinary settings. Change them per session, per transaction (`SET LOCAL`), or per function (`ALTER FUNCTION ... SET pg_trgm.word_similarity_threshold = 0.5`, which the milestone uses). Lower values find more, but also more garbage, and they make the index less selective. 0.3 for `similarity` is a reasonable start for single words, too low for long strings, and too high for very short ones.

### 11.3 Trigram indexes: GIN and GiST

`1301_schema_search.sql` creates `products_title_trgm_idx ON products USING gin (title gin_trgm_ops)` (20 MB). A trigram GIN index maps each trigram to the rows containing it, like a full-text GIN index maps lexemes. The GiST variant, `gist_trgm_ops`, stores trigram signatures. GiST is bigger here (32 MB) and slower for filtering, but it supports one thing GIN can't: **ordering by distance** (a K-nearest-neighbour index scan), which returns the closest matches first without computing the distance for every candidate:

```sql
CREATE INDEX products_title_trgm_gist ON products USING gist (title gist_trgm_ops);  -- 3.5 s, 32 MB

EXPLAIN (ANALYZE, COSTS OFF)
SELECT id, title, 'headphnes' <<-> title AS dist
FROM products
ORDER BY 'headphnes' <<-> title
LIMIT 5;
```

```text
                                                QUERY PLAN                                                 
-----------------------------------------------------------------------------------------------------------
 Limit (actual time=105.886..105.934 rows=5 loops=1)
   ->  Index Scan using products_title_trgm_gist on products (actual time=105.884..105.930 rows=5 loops=1)
         Order By: (title <->> 'headphnes'::text)
 Planning Time: 0.229 ms
 Execution Time: 107.902 ms
(5 rows)
```

KNN works, but at 106 ms it is five times slower than filtering with `<%` through the GIN index and sorting the 1,819 matches (20 ms). KNN GiST shines when there is no threshold you can choose in advance ("always show the 5 closest, however far"), and on short strings such as a vocabulary of words. Drop the GiST index again (`DROP INDEX products_title_trgm_gist;`): Bazaar keeps the GIN one.

### 11.4 Speeding up `LIKE`, `ILIKE` and regular expressions

The same trigram index accelerates **any** `LIKE`, `ILIKE`, `~` and `~*` pattern from which PostgreSQL can extract at least one trigram. It extracts the trigrams the pattern *requires*, uses the index to find the rows containing all of them, and rechecks the real pattern on those rows:

```sql
EXPLAIN (ANALYZE, COSTS OFF)
SELECT count(*) FROM products WHERE title ILIKE '%skillet%';
```

```text
                                              QUERY PLAN                                              
------------------------------------------------------------------------------------------------------
 Aggregate (actual time=4.993..4.994 rows=1 loops=1)
   ->  Bitmap Heap Scan on products (actual time=0.482..4.933 rows=518 loops=1)
         Recheck Cond: (title ~~* '%skillet%'::text)
         Heap Blocks: exact=507
         ->  Bitmap Index Scan on products_title_trgm_idx (actual time=0.412..0.412 rows=518 loops=1)
               Index Cond: (title ~~* '%skillet%'::text)
 Planning Time: 1.475 ms
 Execution Time: 5.116 ms
(8 rows)
```

5 ms on `title`, which has the index, against 160 ms on `description`, which doesn't (section 1). Regular expressions work too:

```sql
EXPLAIN (ANALYZE, COSTS OFF)
SELECT count(*) FROM products WHERE title ~* '\mcast[- ]iron\M';
```

```text
                                             QUERY PLAN                                             
----------------------------------------------------------------------------------------------------
 Aggregate (actual time=1.242..1.243 rows=1 loops=1)
   ->  Bitmap Heap Scan on products (actual time=1.239..1.240 rows=0 loops=1)
         Recheck Cond: (title ~* '\mcast[- ]iron\M'::text)
         ->  Bitmap Index Scan on products_title_trgm_idx (actual time=1.236..1.236 rows=0 loops=1)
               Index Cond: (title ~* '\mcast[- ]iron\M'::text)
 Planning Time: 0.473 ms
 Execution Time: 1.266 ms
(7 rows)
```

(No title says "cast iron". It is the material, so it is in the descriptions. The index proved that in 1 ms.)

When the pattern has no complete trigram, the index can't help and PostgreSQL reads every row:

```sql
EXPLAIN (ANALYZE, COSTS OFF)
SELECT count(*) FROM products WHERE title ILIKE '%ab%';
```

```text
                                          QUERY PLAN                                           
-----------------------------------------------------------------------------------------------
 Finalize Aggregate (actual time=97.106..102.695 rows=1 loops=1)
   ->  Gather (actual time=96.928..102.689 rows=3 loops=1)
         Workers Planned: 2
         Workers Launched: 2
         ->  Partial Aggregate (actual time=91.724..91.726 rows=1 loops=3)
               ->  Parallel Seq Scan on products (actual time=0.086..90.974 rows=5290 loops=3)
                     Filter: (title ~~* '%ab%'::text)
                     Rows Removed by Filter: 61377
 Planning Time: 0.247 ms
 Execution Time: 102.776 ms
(10 rows)
```

Very common trigrams (`%the%`, `%ing%`) are a similar problem: the index finds most of the table and the recheck reads it all. A search box that fires `ILIKE '%' || input || '%'` for every keystroke should require at least three characters.

> **Pitfall:** A pattern built from user input must have its `%`, `_` and `\` escaped (`replace(replace(replace(input, '\', '\\'), '%', '\%'), '_', '\_')`), or the shopper can type `%` and turn your query into "match everything". Escaping is about correctness, not SQL injection: the pattern is still a bound parameter.

### 11.5 "Did you mean?"

Trigram matching against whole titles is a blunt tool for typos. A better approach mirrors what search engines do: correct the **words**, then search with the corrected words. You need a vocabulary, which `ts_stat` gives you (with the `simple` configuration, so the words aren't stemmed):

```sql
CREATE TABLE lab.title_words AS
SELECT word, ndoc
FROM ts_stat($$SELECT to_tsvector('simple', title) FROM bazaar.products$$);   -- 1,413 words

CREATE INDEX ON lab.title_words USING gin (word gin_trgm_ops);

SELECT word, ndoc, similarity(word, 'headphnes') AS sim
FROM lab.title_words
WHERE word % 'headphnes'
ORDER BY sim DESC, ndoc DESC
LIMIT 3;
```

```text
    word    | ndoc |    sim     
------------+------+------------
 headphones | 1819 | 0.61538464
(1 row)
```

`lapptop` gives `laptop` (0.67), and `skilet` gives `skillet` (0.67), each in 2–5 ms. Sorting ties by `ndoc` prefers the common word, which is usually what the shopper meant. Accents are a problem, though:

```sql
SELECT word, ndoc, similarity(word, 'creme') AS sim
FROM lab.title_words WHERE word % 'creme' ORDER BY sim DESC, ndoc DESC LIMIT 3;
```

```text
 word  | ndoc |    sim     
-------+------+------------
 crème | 1435 | 0.33333334
 cream |  715 | 0.33333334
(2 rows)
```

"crème" and "cream" are equally similar to "creme", because `è` is a different character from `e`, so every trigram containing it differs. That is the next section's topic.

---

## 12. `unaccent`, and the immutable wrapper

The full-text configuration doesn't strip accents, and neither does `pg_trgm`. So shoppers who type `creme` find nothing:

```sql
SELECT count(*) FROM products WHERE search_vector @@ websearch_to_tsquery('english', 'creme');  -- 0
SELECT count(*) FROM products WHERE search_vector @@ websearch_to_tsquery('english', 'crème');  -- 1435
SELECT count(*) FROM products WHERE title ILIKE '%creme%';                                      -- 0
SELECT count(*) FROM products WHERE unaccent(title) ILIKE '%creme%';                            -- 1435, 145 ms
```

`unaccent()` fixes the matching, but the last query reads the whole table. The obvious index fails:

```sql
CREATE INDEX ON products USING gin (unaccent(title) gin_trgm_ops);
```

```text
ERROR:  functions in index expression must be marked IMMUTABLE
```

`unaccent(text)` is declared `STABLE`, not `IMMUTABLE`. The reason is that the one-argument form looks up a dictionary called `unaccent` through `search_path`, so its result could change if `search_path` changes or the dictionary's rules file is edited. An index stores computed values forever, so it only accepts functions that promise the same output for the same input forever.

The standard fix is a wrapper that removes the `search_path` dependency, by calling the two-argument form with a schema-qualified dictionary, and is declared `IMMUTABLE`:

```sql
CREATE FUNCTION immutable_unaccent(text)
RETURNS text
LANGUAGE sql
IMMUTABLE PARALLEL SAFE STRICT
RETURN public.unaccent('public.unaccent'::regdictionary, $1);

CREATE INDEX products_title_unaccent_trgm_idx
    ON products USING gin (immutable_unaccent(title) gin_trgm_ops);   -- 2.7 s, 20 MB

EXPLAIN (ANALYZE, COSTS OFF)
SELECT count(*) FROM products WHERE immutable_unaccent(title) ILIKE immutable_unaccent('%crème de%');
```

```text
                                                   QUERY PLAN                                                   
----------------------------------------------------------------------------------------------------------------
 Aggregate (actual time=6.395..6.397 rows=1 loops=1)
   ->  Bitmap Heap Scan on products (actual time=1.146..6.258 rows=1435 loops=1)
         Recheck Cond: (immutable_unaccent(title) ~~* '%creme de%'::text)
         Heap Blocks: exact=1380
         ->  Bitmap Index Scan on products_title_unaccent_trgm_idx (actual time=0.943..0.944 rows=1435 loops=1)
               Index Cond: (immutable_unaccent(title) ~~* '%creme de%'::text)
 Planning Time: 0.592 ms
 Execution Time: 6.427 ms
(8 rows)
```

Two details:

- **Apply the same function to the user's input**, so that "Crème", "creme" and "CREME" all meet. Because the function is immutable and the argument is a constant, the planner folded `immutable_unaccent('%crème de%')` into `'%creme de%'` at plan time.
- **The promise is now yours.** If someone edits `unaccent.rules` on the server, existing index entries keep the old transformation, and new ones get the new one. Rebuild the index (`REINDEX`) after changing the rules.

For full-text search, put `unaccent` into the configuration instead (section 3.1), and use that configuration in the generated column and in every query. Bazaar's 1301 file doesn't, so the milestone handles accents in its trigram fallback instead.

---

## 13. Autocomplete

Search-as-you-type sends a query on every keystroke, so it needs single-digit milliseconds, and the input is always a *partial* word: `voltix wi`. Three index-backed approaches, measured on the 200,000 products.

### 13.1 Prefix `tsquery`

The `:*` operator matches every lexeme starting with a prefix, and GIN supports it by scanning a range of its (sorted) lexeme tree. Complete words are normal lexemes, and the last, partial word gets `:*`:

```sql
EXPLAIN (ANALYZE, COSTS OFF)
SELECT id, title FROM products
WHERE search_vector @@ to_tsquery('english', 'voltix & wi:*')
ORDER BY title
LIMIT 10;
```

```text
                                                  QUERY PLAN                                                   
---------------------------------------------------------------------------------------------------------------
 Limit (actual time=5.376..5.379 rows=10 loops=1)
   ->  Sort (actual time=5.375..5.377 rows=10 loops=1)
         Sort Key: title
         Sort Method: top-N heapsort  Memory: 26kB
         ->  Bitmap Heap Scan on products (actual time=3.814..4.945 rows=880 loops=1)
               Recheck Cond: (search_vector @@ '''voltix'' & ''wi'':*'::tsquery)
               Heap Blocks: exact=867
               ->  Bitmap Index Scan on products_search_vector_idx (actual time=3.406..3.407 rows=880 loops=1)
                     Index Cond: (search_vector @@ '''voltix'' & ''wi'':*'::tsquery)
 Planning Time: 0.128 ms
 Execution Time: 5.397 ms
(11 rows)
```

5 ms, and it matches word starts anywhere in the document: the 880 matches include Voltix products whose description mentions "wireless" or a "wide-angle" lens. Restrict both terms to the title with `voltix:A & wi:*A` (587 matches), at the price of a recheck. It has two traps, both caused by the prefix being normalised like a word:

```sql
SELECT to_tsquery('english', 'he:*'),          to_tsquery('simple', 'he:*'),
       to_tsquery('english', 'headphones:*'),  to_tsquery('simple', 'headphones:*');
```

```text
 to_tsquery | to_tsquery |  to_tsquery  |   to_tsquery   
------------+------------+--------------+----------------
            | 'he':*     | 'headphon':* | 'headphones':*
(1 row)
```

- **A prefix that is a stop word vanishes.** A shopper who has typed `he` (on the way to "headphones") gets an empty query from the `english` configuration. `simple` keeps it.
- **A complete word longer than its stem matches nothing with `simple`**: the index contains `headphon`, and `headphones:*` is longer than that. `english` stems it first, so it works.

So the partial word needs both: `(english_prefix:* | simple_prefix:*)`, built from values as in section 6. And short prefixes match a lot: `w:*` restricted to titles matches 30,982 products, and sorting them by anything costs 50–100 ms.

### 13.2 Trigram `ILIKE`

The trigram index answers `title ILIKE '%voltix wi%'` (2.9 ms) or anchored `title ILIKE 'voltix wi%'` (13 ms here: the index can't use the anchoring, so it checks the trigrams and rechecks 587 candidates). Its strengths: it matches inside words (`%phones%`), it ignores stemming and stop words, and with `immutable_unaccent` it ignores accents too. Its weakness is short input: one or two characters contain no complete trigram, so the index is useless, and `ILIKE '%wi%'` reads the table (130 ms). Common trigrams are slow too: `'%hea%'` finds 12,508 titles and sorting them takes 55 ms.

### 13.3 B-tree prefix on titles

If suggestions should match the *start* of the title, a plain B-tree is unbeatable, because it can return rows already sorted and stop after 10. The index must compare bytes, not linguistic collation order, for `LIKE 'abc%'` to become a range scan. Either use the `text_pattern_ops` operator class (which supports `LIKE` but not `ORDER BY`) or, better, the `"C"` collation, which supports both:

```sql
CREATE INDEX products_title_prefix_idx ON products (lower(title) COLLATE "C");   -- 0.2 s

EXPLAIN (ANALYZE, COSTS OFF)
SELECT title FROM products
WHERE lower(title) COLLATE "C" LIKE 'v%'
ORDER BY lower(title) COLLATE "C"
LIMIT 10;
```

```text
                                               QUERY PLAN                                                
---------------------------------------------------------------------------------------------------------
 Limit (actual time=0.029..0.122 rows=10 loops=1)
   ->  Index Scan using products_title_prefix_idx on products (actual time=0.029..0.121 rows=10 loops=1)
         Index Cond: (((lower(title))::text >= 'v'::text) AND ((lower(title))::text < 'w'::text))
         Filter: ((lower(title))::text ~~ 'v%'::text)
 Planning Time: 0.071 ms
 Execution Time: 0.136 ms
(6 rows)
```

0.14 ms for a one-letter prefix that matches 8,877 titles, because the scan stops after 10 rows. With a `text_pattern_ops` index the same query had to fetch all 8,877 rows and sort them (55 ms), since that operator class has no ordering. But a title prefix only helps if shoppers type titles from the start. On Bazaar they type `headphones`, and no title starts with that. It is the right tool for names, SKUs, cities and tags; for product search, it is too narrow.

### 13.4 Which one?

| Approach | Matches | Short prefixes (1–2 chars) | Order of results | Typical cost here |
|---|---|---|---|---|
| prefix `tsquery` | word starts, stemmed | stop-word trap; many matches | must sort all matches | 5 ms; 50–100 ms for 1 letter |
| trigram `ILIKE` | any substring | no index | must sort all matches | 3–15 ms; 130 ms for 2 letters |
| B-tree `COLLATE "C"` | start of string only | fine | index order, stops early | < 1 ms |

All three show the same deeper issue: when the prefix is short, the interesting question is not *which* products match, but which *completions* are most useful, and that is a property of the vocabulary, not of 200,000 rows. That is why the milestone's `autocomplete()` completes the last word from a small materialized vocabulary (about 500 title words, so even a one-letter prefix is a tiny index range), and only turns to the products table, through the trigram index, once the shopper has typed enough to make that selective.

> **In production:** Debounce keystrokes in the client (send a request after 100–150 ms of no typing), require two or three characters, cancel in-flight requests when a new one starts, and cache the most popular prefixes. Most autocomplete load problems are solved in the client and the cache, not in the database.

---

## 14. When to reach for a dedicated search engine

PostgreSQL search is a good fit when:

- the corpus fits comfortably on one server (millions of documents is fine; Bazaar's 200,000 products are small);
- search must be **transactionally consistent** with the data: a product taken down by moderation must never appear, and a new listing should be searchable the moment it is committed. No sync pipeline, no lag, no second source of truth;
- filters are relational (category subtree, seller, stock, RLS policies from [Module 15](../15-security-rls/)) and are cheap to express in the same query;
- relevance needs are "good keyword search with some business boosting", which is what most shops, admin panels, help centres and internal tools need.

A dedicated engine (Elasticsearch, OpenSearch, Solr, or newer ones such as Meilisearch, Typesense and Vespa) earns its operational cost when you need:

- **Relevance engineering at scale**: BM25 with field-level tuning, learning-to-rank, per-query boosting rules, A/B testing of ranking, and analytics on what shoppers searched and clicked. `ts_rank` is a simple frequency-based score, and it has no corpus-wide statistics (it doesn't know that "voltix" is rare and "perfect" is everywhere, which is exactly what BM25's inverse document frequency captures).
- **Fast ranked retrieval over huge result sets**: an inverted index that stores scores can return the top 20 out of 10 million matches without visiting them all. GIN can't (section 7.2).
- **Faceted navigation** with counts on many dimensions at once ("Brand: Voltix (327), Price: under $50 (1,204)..."), computed over millions of hits on every query.
- **Rich language tooling**: compound-word splitting, language detection, phonetic matching, per-language analyzers, synonym graphs maintained by merchandisers, CJK tokenisers.
- **Horizontal scale for search load** separately from the transactional database.

The price: a second system to run, secure, monitor and upgrade, a sync pipeline (usually change data capture from PostgreSQL's logical replication, [Module 19](../19-replication/)) with lag and failure modes of its own, and eventual consistency between what the database says and what search shows.

Between the two sits a growing set of PostgreSQL extensions: `rum` (ranked GIN), `pg_search` (ParadeDB, BM25 inside PostgreSQL), `pgroonga` (multilingual full-text search), and `pgvector` for semantic (embedding) search, which is often combined with full-text search into "hybrid" ranking. They keep the transactional advantages, but each is an extension that your hosting must support.

A reasonable path: start in PostgreSQL, as this module does. Measure relevance with real queries and latency with `pg_stat_statements` ([Module 12](../12-query-performance/)). Move to an engine when a concrete requirement from the list above appears, not because "real search needs Elasticsearch".

---

## Summary

- `LIKE '%word%'` has no linguistics, no ranking, and no B-tree index. Full-text search normalises documents and queries into lexemes and matches those.
- The pipeline is parser → tokens → dictionaries → lexemes, wired together by a configuration. `ts_debug` shows every step. Always pass the configuration explicitly.
- Stemmers are heuristic (`knives` → `knive`); synonym, thesaurus and `unaccent` dictionaries in a custom configuration fix specific gaps. Dictionary files live on the server and aren't dumped or replicated. `ts_rewrite` with a rules table adds query-side synonyms without touching the index.
- A `tsvector` holds lexemes with positions and weights A–D; a `tsquery` combines lexemes with `& | ! <-> <N> :*` and weight filters.
- Use `websearch_to_tsquery` for user input: it understands quotes, `or` and `-`, and never raises an error. Check `numnode(q) = 0` for queries that are only stop words.
- `ts_rank` measures frequency, `ts_rank_cd` rewards proximity; weights and normalization flags tune both. Combine text rank with business signals after normalising them, and rank in two phases when the signals are expensive. Ranking always visits every match.
- `ts_headline` re-parses the document: run it only on the rows you return, and escape HTML yourself.
- Store the `tsvector` in a generated column (simple, always in sync, cheap ranking), use an expression index for filter-only search, or a trigger when the document spans tables. GIN beat GiST on every measure for Bazaar. `fastupdate` trades occasional slow inserts and slower searches for cheaper writes.
- Multilingual search: the same configuration for document and query; a `regconfig` column per row works in generated columns.
- `pg_trgm` handles typos, substrings and regular expressions. Filter with the operators (`%`, `<%`), which use the index, and sort with the functions. `word_similarity` suits short queries against long titles. Patterns with no complete trigram can't use the index.
- `unaccent` needs an immutable wrapper (two-argument form, schema-qualified dictionary) to be indexed.
- Autocomplete: prefix `tsquery`, trigrams and B-tree prefixes each have a niche. For short prefixes, complete from a vocabulary.
- PostgreSQL search is consistent, simple and good enough for most applications; a search engine is for relevance engineering, huge ranked result sets, facets and advanced language tooling.

## What's next

`events` is about to become Bazaar's biggest table: 5 million rows a year and growing. [Module 14](../14-partitioning/) splits it into monthly partitions, so that queries only read the months they ask for and a year of old data can be dropped in milliseconds.
