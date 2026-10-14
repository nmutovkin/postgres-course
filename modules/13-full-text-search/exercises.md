# Module 13: Exercises

<!-- nav:start -->
> [Theory](README.md) · **Exercises** · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 12 Query Planning and Performance](../12-query-performance/README.md) · [Course home](../../README.md) · [14 Partitioning →](../14-partitioning/README.md)
<!-- nav:end -->

**Starting state.** Unless an exercise says otherwise:

```bash
./project/bin/reset.sh 14      # large dataset + 1301_schema_search.sql
./project/bin/psql.sh
```

Exercises 7, 9 and 10 also need this module's milestone, the reference version of which you can apply with:

```bash
docker exec -i course-pg psql -U postgres -d bazaar < modules/13-full-text-search/solutions/search_products.sql
```

"Active products" means `status = 'active'`. All counts were measured on PostgreSQL 17 with the reference data, and are deterministic.

Solutions: [solutions/README.md](solutions/README.md).

---

<!-- toc:start -->
**Contents**

- [Exercise 1 ★: The stop words in our descriptions](#exercise-1--the-stop-words-in-our-descriptions)
- [Exercise 2 ★: Title or elsewhere?](#exercise-2--title-or-elsewhere)
- [Exercise 3 ★: Say it in websearch syntax](#exercise-3--say-it-in-websearch-syntax)
- [Exercise 4 ★★: Phrases versus bags of words](#exercise-4--phrases-versus-bags-of-words)
- [Exercise 5 ★★: Where do the hits come from?](#exercise-5--where-do-the-hits-come-from)
- [Exercise 6 ★★: When the two ranking functions disagree](#exercise-6--when-the-two-ranking-functions-disagree)
- [Exercise 7 ★★: Synonyms the shoppers use](#exercise-7--synonyms-the-shoppers-use)
- [Exercise 8 ★★: Accent-insensitive full-text search](#exercise-8--accent-insensitive-full-text-search)
- [Exercise 9 ★★: The zero-results report](#exercise-9--the-zero-results-report)
- [Exercise 10 ★★★: Did you mean?](#exercise-10--did-you-mean)
- [Exercise 11 ★★★: Facets](#exercise-11--facets)
- [Exercise 12 ★★★: A prefix query that can't break](#exercise-12--a-prefix-query-that-cant-break)

<!-- toc:end -->

## Exercise 1 ★: The stop words in our descriptions

Which English stop words actually occur in Bazaar's product descriptions, and in how many products each? Produce one row per stop word with the number of products whose description contains it, most frequent first. Do it with the full-text functions, not with a hard-coded word list.

**Self-check:** 22 rows. The first two are `and` and `of`, each in 190,119 products (every product that has a description).

<details><summary>Hint</summary>

Parse the descriptions with a configuration that keeps stop words, collect the vocabulary with `ts_stat`, and ask the English stemmer about each word: `ts_lexize` returns an empty array for a stop word.

</details>

---

## Exercise 2 ★: Title or elsewhere?

For the word "wireless", count the active products that contain it (a) in the title, (b) somewhere, but *not* in the title, and (c) anywhere. One query, one row, three columns. Use the weights stored in `search_vector`, not `ILIKE`.

**Self-check:** 5,063 / 3,104 / 8,167, and (a) + (b) = (c).

<details><summary>Hint</summary>

`to_tsquery('english', 'wireless:A')` matches only positions with weight A. `count(*) FILTER (WHERE ...)` gives several counts in one pass.

</details>

---

## Exercise 3 ★: Say it in websearch syntax

A shopper wants a tent or a sleeping bag, but nothing ultralight. Write the string they should type into a search box that uses `websearch_to_tsquery` (no parentheses: the function ignores them). Then count the active products it matches.

**Self-check:** 5,698 products. If you got 6,351, look at the `tsquery` your string produces: what does the minus sign apply to?

<details><summary>Hint</summary>

In `websearch_to_tsquery`, `AND` (implicit, between words) binds tighter than `or`. Print the generated `tsquery` to see the tree. You can't group with parentheses, but you can repeat a term.

</details>

---

## Exercise 4 ★★: Phrases versus bags of words

For each of these queries: `wireless headphones`, `noise cancelling`, `cast iron`, `eau de parfum`, `usb-c charger`, `board game`, `face cream`, count the products (any status) that contain *all the words* and the products that contain them *as a phrase*. Then explain the two queries where the counts differ, by looking at real titles.

**Self-check:** they differ for exactly two queries: `wireless headphones` (643 versus 140) and `usb-c charger` (1,462 versus 893).

<details><summary>Hint</summary>

`plainto_tsquery` versus `phraseto_tsquery`. For the explanation, list a few titles that match the first but not the second.

</details>

---

## Exercise 5 ★★: Where do the hits come from?

A product matches `headphones` if the word is in its title (weight A), description (B) or tags (C). Break the 4,409 matches down by the combination of places the word occurs in: one row per combination, with the count. What kind of products match only through their tags?

**Self-check:** 3 rows: tags only 2,590; title, description and tags 1,728; title and tags 91.

<details><summary>Hint</summary>

Test each match three times, with `headphones:A`, `:B` and `:C`, and `GROUP BY` the three booleans.

</details>

---

## Exercise 6 ★★: When the two ranking functions disagree

For the query `wireless charger` over active products, compute the top 10 by `ts_rank` and the top 10 by `ts_rank_cd` (break ties by `id`). How many products are in both lists? Look at the `ts_rank` winners: why do they win, and why is that a problem for a marketplace?

**Self-check:** 1,187 products match, and only 1 product is in both top-10 lists.

<details><summary>Hint</summary>

Read the titles of the `ts_rank` top 10 carefully, and compare their `ts_rank_cd` values with the others'.

</details>

---

## Exercise 7 ★★: Synonyms the shoppers use

The search log (`events` with `event_type = 'search'`) shows that shoppers search for "sci-fi novel" about 32,000 times a year, and `search_products('sci-fi novel')` returns nothing: the catalog says "science fiction" and "book". Without changing the stored `search_vector`, make the milestone function understand these aliases:

- `sci-fi` → the phrase "science fiction"
- `novel` → `novel` or `book`
- `knives` → `knives` or `knife`
- `earphones` → `earphones`, `earbuds` or `headphones`

The aliases must be data that a merchandiser can edit, not code. Write a function `expand_query(q text) RETURNS tsquery` and use it in `search_products`.

**Self-check:** `expand_query('sci-fi novel')` returns `( 'novel' | 'book' ) & 'scienc' <-> 'fiction'`, and `search_products('sci-fi novel')` returns 20 rows, all with `matched_by = 'fts'`, all in the Science Fiction category, the first one being product 2, *The Burning Protocol*.

<details><summary>Hint</summary>

`ts_rewrite(query, 'SELECT target, substitute FROM table')`. Store both columns as `tsquery` values built with the `english` configuration. Think about the volatility category of `expand_query`.

</details>

---

## Exercise 8 ★★: Accent-insensitive full-text search

Today `creme`, `lumiere`, `saute pan` and `cafe` find nothing, while `crème` (1,435), `lumière` (1,586), `sauté pan` (527) and `café` (385) work. Create a configuration `bazaar_english` that ignores accents, rebuild `search_vector` and its index with it, and show that the accented and unaccented spellings now find the same products. Do it inside a transaction that you roll back, and time the expensive step.

**Self-check:** with the new configuration, `creme`, `crème` and `CRÈME` all become the query `'creme'` and match 1,435 products; `lumiere` matches 1,586, `saute pan` 527 and `cafe` 385.

<details><summary>Hint</summary>

Section 3.1. In PostgreSQL 17 you can't change a generated column's expression with `ALTER TABLE`: drop the column and add it again (which also drops its index). Every query must now use `bazaar_english` too.

</details>

---

## Exercise 9 ★★: The zero-results report

Every search a shopper makes is in `events` (`event_type = 'search'`, the query text in `payload->>'q'`). Produce a report with one row per distinct query: how often it was searched, how many active products full-text search finds for it, and the title and `matched_by` of the first result of `search_products()`. Sort it so that the most urgent problems come first.

**Self-check:** 18 rows. Exactly three queries have 0 full-text hits: `sci-fi novel` (31,843 searches, still no result at all), `lapptop` and `headphnes` (both rescued by the fuzzy fallback). (If you did exercise 7 first, `sci-fi novel` now returns results: re-apply `search_products.sql` to get the original function back.)

<details><summary>Hint</summary>

Aggregate the events first (5 million rows, but only 18 distinct queries), then call the function once per query with a `LATERAL` join.

</details>

---

## Exercise 10 ★★★: Did you mean?

Write `did_you_mean(q text) RETURNS text` that corrects each word of a query that doesn't occur anywhere in the searchable text of active products (titles, descriptions, tags), replacing it with the most similar known word. It returns the corrected query, or NULL when there is nothing to correct. It must be accent-insensitive, leave very short words alone, keep words it can't correct, and use an index for the similarity search.

**Self-check:**

| input | output |
|---|---|
| `wireles headphnes` | `wireless headphones` |
| `lapptop` | `laptop` |
| `cast iron skilet` | `cast iron skillet` |
| `voltx wireles charger` | `voltix wireless charger` |
| `usb-c chrger` | `usb-c charger` |
| `Crème de Lys` | NULL |
| `qwzx` | NULL |

<details><summary>Hint</summary>

Build the vocabulary once, as a materialized view over `ts_stat` with the `simple` configuration (no stemming: you want to suggest real words), and put a trigram index on it. A title-only vocabulary is not enough for `cast iron skilet`. Split the input with `regexp_split_to_table(...) WITH ORDINALITY` so you can reassemble the words in order.

</details>

---

## Exercise 11 ★★★: Facets

A search results page shows a sidebar: "Electronics (8,167): Computers (2,910), Phones & Accessories (2,339), ...". Write `search_facets(q text) RETURNS TABLE (root_name text, category_name text, products bigint)` that returns, for the active products matching `q`, one total row per root category (with `category_name` NULL) followed by one row per second-level category under it. Roots are ordered by their totals, and the categories within a root by their counts. It must stay fast for broad queries.

**Self-check:** `search_facets('wireless')` returns 5 rows: Electronics 8,167, then Computers 2,910, Phones & Accessories 2,339, Audio 1,771, Cameras 1,147. `search_facets('gift')` starts with Home & Garden 7,623 (Kitchen 3,029, Furniture 2,305, Garden 2,289), then Electronics 6,639. Each call takes well under 100 ms.

<details><summary>Hint</summary>

A recursive CTE maps every category to its root and its level-2 ancestor. Count the matches per `category_id` *first* (at most 69 groups), then join to the tree: that way the join handles 69 rows, not 33,000. `GROUPING SETS` produces the totals and the details in one pass.

</details>

---

## Exercise 12 ★★★: A prefix query that can't break

Section 13.1 showed the two traps of prefix queries: the `english` configuration drops a prefix that is a stop word (`he`), and the `simple` configuration doesn't stem (`headphones:*` misses `headphon`). Write `prefix_tsquery(input text) RETURNS tsquery` for search-as-you-type: every word except the last is a complete word; the last one is a prefix. It must handle both traps, ignore stop words among the complete words, and turn *any* input (quotes, `&`, `|`, `:*`, backslashes, empty strings) into a valid query without errors. The planner must be able to use the GIN index.

**Self-check:**

| input | `prefix_tsquery(input)` | products matched |
|---|---|---|
| `he` | `'he':*` | 23,220 |
| `headphones` | `'headphon':* \| 'headphones':*` | 4,409 |
| `voltix wi` | `'voltix' & 'wi':*` | 880 |
| `the wirel` | `'wirel':*` | 9,569 |
| `x')\|(y:*` | `'xy':*` | 0 |
| (empty) | empty query | 0 |

and `EXPLAIN` of `WHERE search_vector @@ prefix_tsquery('voltix wi')` shows a Bitmap Index Scan on `products_search_vector_idx`.

<details><summary>Hint</summary>

Sanitize first (keep letters, digits, spaces and hyphens), then build the query from *values*: `plainto_tsquery` for complete words, `to_tsquery(config, '''word'':*')` for the prefix in both configurations, combined with the `&&` and `||` operators on `tsquery`. For the index: what does the planner do with an `IMMUTABLE` function called on a constant?

</details>
