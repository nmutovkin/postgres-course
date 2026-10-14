# Module 08: Beyond Scalars

Bazaar's catalog has outgrown its columns. A laptop has RAM, a CPU and a screen size. A book has an author, a page count and an ISBN. A T-shirt comes in five sizes and a colour. Adding a column for every property of every kind of product would give `products` hundreds of mostly-NULL columns, and every new category would need a migration. Sellers also want free-form tags ("bestseller", "gaming"), marketing wants time-boxed promotions that must never overlap, and the status columns from module 02 are still `text` with a `CHECK`. PostgreSQL has a richer type system than "number, string, date" for exactly these problems. In this module you learn the types that hold *structure*, such as JSON documents, arrays, enums, domains, composite values and ranges. You also learn when each one is the right tool and when it quietly makes things worse.

**What you'll learn**

- `json` versus `jsonb`: how each is stored, what happens to key order, duplicate keys and whitespace, and why you almost always want `jsonb`.
- Building JSON from rows (`jsonb_build_object`, `to_jsonb`, `jsonb_agg`, `jsonb_object_agg`) and taking it apart again (`->`, `->>`, `#>`, `#>>`, subscripts, `jsonb_each`, `jsonb_array_elements`, `jsonb_to_recordset`).
- Containment and existence (`@>`, `<@`, `?`, `?|`, `?&`), and modifying documents (`||`, `-`, `#-`, `jsonb_set`, `jsonb_insert`, `jsonb_strip_nulls`).
- SQL/JSON path expressions (`jsonb_path_query`, `@?`, `@@`, filters, lax and strict mode) and the SQL-standard functions of PostgreSQL 17: `JSON_EXISTS`, `JSON_VALUE`, `JSON_QUERY` and `JSON_TABLE`.
- Validating JSON with `CHECK` constraints, a first look at indexing it, and an honest rule of thumb for when JSONB is right and when it is wrong.
- Arrays: literals, 1-based indexing, slices, `unnest ... WITH ORDINALITY`, `ANY`/`ALL`, the `&&`, `@>` and `<@` operators, and arrays versus junction tables.
- Enums (ordering, `ALTER TYPE ... ADD VALUE` and its limits), domains (`email_address`, `positive_money`), and composite types.
- Range types, multiranges, and **exclusion constraints** that make "no two promotions for the same product may overlap" a rule the database enforces.
- How the Bazaar v3 migration converts existing `text` columns to enums with `ALTER COLUMN ... TYPE ... USING`, and why the `CHECK` constraints and defaults had to go first.

**Before you start**

The examples in this module query the columns and tables that this module's own milestone adds: `products.attributes`, `products.tags`, `promotions`, and the enum status columns. To follow along, load the reference version of the milestone, which is the state at the start of module 09:

```bash
./project/bin/reset.sh 09
./project/bin/psql.sh
```

When you start the [project milestone](project.md), reset to `08` and build the same thing yourself. Examples that change data either run inside `BEGIN ... ROLLBACK` or say that you should reset afterwards. Section 18 walks through the reference migration, `project/db/0801_schema_types.sql`, step by step.

You need the SQL from modules 03–06 (joins, `LATERAL`, `GROUP BY`, `array_agg`, CTEs) and the modelling vocabulary of [module 07](../07-data-modeling/): normal forms, junction tables, and why we normally prefer one fact per column.

---

## 1. Why go beyond scalar columns?

Until now every column held one *scalar*, a single number, string, timestamp or boolean. That is the heart of the relational model, and of first normal form: each column holds one atomic value. It is a good default, because the database can then type-check, constrain, index and join every value individually.

But "atomic" depends on how you use a value. A timestamp is made of a year, a month, a day and a time, yet you never store those in four columns: you treat the timestamp as one value and use functions to look inside it. The types in this module push the same idea further:

| Type | Holds | Bazaar use |
|---|---|---|
| `jsonb` | A whole JSON document | `products.attributes`: properties that differ per category |
| `text[]` (arrays) | An ordered list of values of one type | `products.tags` |
| enum | One label from a fixed, ordered list | `orders.status`, `products.status`, `payments.status` |
| domain | A base type plus constraints, under a name | `email_address` |
| composite | A small record with named fields | Every table's row type; function results in module 09 |
| range / multirange | An interval (or a set of intervals) of numbers, dates or timestamps | `promotions.active_during` |

Each of them is a real PostgreSQL type. You can index it, constrain it, compare it and pass it to functions. The question for each one is not "is this allowed?" but "will I want to query, constrain or join on the parts inside it?" If the answer is yes for a part, that part probably deserves its own column or table. Section 11 turns this into a rule of thumb.

---

## 2. `json` versus `jsonb`

PostgreSQL has two JSON types, and the difference is how they store the document.

- **`json`** stores the **exact input text**. It checks that the text is valid JSON and keeps it byte for byte: whitespace, key order, duplicate keys. Every time you look inside it, PostgreSQL parses the text again.
- **`jsonb`** ("JSON binary") parses the input **once**, on the way in, and stores a binary tree: objects as sorted arrays of keys with offsets to their values, numbers as `numeric`, strings as text. Looking up a key is a binary search, not a re-parse.

The parsing has visible consequences:

```sql
SELECT '{"b": 1,   "a": [1, 2], "a": "dup", "price": 19.990}'::json  AS as_json,
       '{"b": 1,   "a": [1, 2], "a": "dup", "price": 19.990}'::jsonb AS as_jsonb;
```

```text
                       as_json                        |               as_jsonb
------------------------------------------------------+---------------------------------------
 {"b": 1,   "a": [1, 2], "a": "dup", "price": 19.990} | {"a": "dup", "b": 1, "price": 19.990}
(1 row)
```

`jsonb`:

- **dropped the whitespace** (output is re-generated from the binary form, in a canonical style);
- **reordered the keys.** Object keys are stored sorted, shorter keys first and then bytewise, which is what makes the binary search possible:

  ```sql
  SELECT '{"price": 1, "id": 2, "b": 3, "a": 4}'::jsonb;
  ```

  ```text
                   jsonb
  ---------------------------------------
   {"a": 4, "b": 3, "id": 2, "price": 1}
  (1 row)
  ```

- **kept only the last duplicate key** (`"a": "dup"`). The JSON specification leaves duplicates undefined; `jsonb` resolves them the way most JavaScript parsers do.
- **kept the number's precision.** Numbers become `numeric`, so `19.990` keeps its trailing zero and `12345678901234567890.123456789` survives intact. That is better than JavaScript, which would round both to a 64-bit float.

Because `jsonb` has a canonical form, it has an equality operator and a sort order. `json` has neither:

```sql
SELECT '{"a": 1, "b": 2}'::jsonb = '{"b": 2, "a": 1}'::jsonb AS jsonb_equal;   -- t
SELECT '{"n": 1.0}'::jsonb = '{"n": 1}'::jsonb;                                -- t (numeric equality)
SELECT '{"a": 1}'::json = '{"a": 1}'::json;
```

```text
ERROR:  operator does not exist: json = json
```

No equality means no `DISTINCT`, no `GROUP BY`, no `UNION` (without `ALL`) and no B-tree index on a `json` column. `jsonb` supports all of them, plus the containment operators and GIN indexes you'll meet below.

What about size? The binary format stores offsets and type headers, so small documents are usually a little *bigger* as `jsonb`:

```sql
SELECT pg_column_size('{"brand": "AeroTech", "ram_gb": 16, "cpu": "M-Core 8", "screen_in": 13.3, "storage": {"type": "ssd", "gb": 512}}'::json)  AS json_bytes,
       pg_column_size('{"brand": "AeroTech", "ram_gb": 16, "cpu": "M-Core 8", "screen_in": 13.3, "storage": {"type": "ssd", "gb": 512}}'::jsonb) AS jsonb_bytes;
```

```text
 json_bytes | jsonb_bytes
------------+-------------
        116 |         154
(1 row)
```

You pay a little space and a little time on input to make every read fast and to get operators and indexes. For data you query, that is the right trade almost every time.

**When is `json` the right choice?** Only when you must preserve the document *exactly as received*: key order matters to a consumer, duplicates matter, or you archive webhook payloads byte for byte (for example to verify a signature over the raw body). Even then, many teams store the raw body as `text` or `bytea` and a parsed `jsonb` copy next to it.

Both types reject invalid JSON, and `jsonb` is stricter than JavaScript in a few places worth knowing:

```sql
SELECT '{"bad": NaN}'::jsonb;    -- ERROR: Token "NaN" is invalid.
SELECT '[1, 2,]'::jsonb;         -- ERROR: Expected JSON value, but found "]".
SELECT '"\u0000"'::jsonb;        -- ERROR: unsupported Unicode escape sequence
```

JSON has no `NaN`, `Infinity` or trailing commas. And PostgreSQL text can't contain the NUL character, so `\u0000` is rejected by `jsonb` (`json` accepts it, because it never decodes the string). If you ingest JSON from other systems, these three are the classic load failures.

> **Pitfall: JSON `null` is not SQL `NULL`.** The JSON value `null` is a real value inside a document. A `jsonb` column containing `null` is NOT NULL as far as SQL is concerned:
>
> ```sql
> SELECT jsonb_typeof('null'::jsonb), 'null'::jsonb IS NULL AS is_sql_null;
> ```
>
> ```text
>  jsonb_typeof | is_sql_null
> --------------+-------------
>  null         | f
> ```
>
> Keep this in mind whenever a query gives "missing" results. A key that is absent, a key whose value is `null`, and a column that is SQL `NULL` are three different situations.

From here on, this module uses `jsonb` exclusively. Most functions have a `json` twin (`json_build_object`, `json_agg`, ...) that behaves the same way but returns `json`.

---

## 3. Building JSON from rows

APIs speak JSON, and sometimes it is convenient to let the database produce it directly. There are two families of constructors: per-row builders and aggregates.

**Per-row builders.** `jsonb_build_object(key1, value1, key2, value2, ...)` builds an object from alternating keys and values. Values are converted with their natural JSON type: numbers stay numbers, booleans stay booleans, SQL `NULL` becomes JSON `null`:

```sql
SELECT jsonb_build_object(
           'id', id, 'title', title, 'price', price,
           'on_sale', false, 'description', description)
FROM products
WHERE id = 9;
```

```text
                                                   jsonb_build_object
-------------------------------------------------------------------------------------------------------------------------
 {"id": 9, "price": 189.00, "title": "PortaScreen 15.6\" USB-C Portable Monitor", "on_sale": false, "description": null}
(1 row)
```

Note what you got for free. The `"` inside the title is escaped correctly (product 9's title contains one), the price kept its two decimals, and the NULL description became `null`. Building JSON by string concatenation in SQL or in your app gets at least one of these wrong eventually.

`jsonb_build_array(...)` is the array equivalent, and `to_jsonb(anything)` converts any SQL value. A whole row becomes an object whose keys are the column names:

```sql
SELECT to_jsonb(s) FROM sellers s WHERE id = 2;
```

```text
                                                         to_jsonb
--------------------------------------------------------------------------------------------------------------------------
 {"id": 2, "user_id": 2, "shop_name": "Bookworm Corner", "created_at": "2023-03-20T09:30:00+00:00", "country_code": "GB"}
(1 row)
```

```sql
SELECT to_jsonb(timestamptz '2025-06-30 12:00+00') AS ts,
       to_jsonb(19.90::numeric(12,2))             AS money,
       to_jsonb('O''Connor'::text)                AS txt,
       to_jsonb(ARRAY[1, 2, 3])                   AS arr,
       to_jsonb(true)                             AS bool;
```

```text
             ts              | money |    txt     |    arr    | bool
-----------------------------+-------+------------+-----------+------
 "2025-06-30T12:00:00+00:00" | 19.90 | "O'Connor" | [1, 2, 3] | true
(1 row)
```

JSON has no date type, so timestamps become ISO 8601 strings. That is the format JavaScript's `Date` and Python's `datetime.fromisoformat` read. `to_jsonb(row)` is the workhorse of the generic audit trigger you'll write in module 09: it serialises any row of any table without knowing its columns.

**Aggregates.** `jsonb_agg(expr)` collects one value per row into a JSON array, and `jsonb_object_agg(key, value)` collects key/value pairs into an object. They are `array_agg`'s JSON siblings (module 05), and like every aggregate they accept `ORDER BY` inside the call. A "shop page" document, in one query:

```sql
SELECT s.shop_name,
       jsonb_agg(jsonb_build_object('id', p.id, 'title', p.title, 'price', p.price)
                 ORDER BY p.price DESC) AS products
FROM sellers s
JOIN products p ON p.seller_id = s.id
WHERE s.id = 5
  AND p.status = 'active'
GROUP BY s.shop_name;
```

```text
  shop_name   |                                                                                            products
--------------+-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
 Maple & Pine | [{"id": 60, "price": 149.00, "title": "Cedar Raised Garden Bed"}, {"id": 58, "price": 29.00, "title": "Self-Watering Planter"}, {"id": 57, "price": 18.00, "title": "Terracotta Planter 20cm"}]
(1 row)
```

```sql
SELECT jsonb_object_agg(status, n)
FROM (SELECT status, count(*) AS n FROM orders GROUP BY status) AS t;
```

```text
                                     jsonb_object_agg
------------------------------------------------------------------------------------------
 {"paid": 6, "pending": 5, "shipped": 4, "refunded": 4, "cancelled": 10, "delivered": 91}
(1 row)
```

Two details trip people up:

- **An aggregate over zero rows returns SQL `NULL`, not `[]`.** If an API promises an array, write `coalesce(jsonb_agg(...), '[]')`.
- **`jsonb_strip_nulls(doc)`** removes every key whose value is JSON `null` (recursively in nested objects, not inside arrays). It's handy when optional fields should be absent rather than `null`:

  ```sql
  SELECT jsonb_build_object('a', NULL) AS sql_null_becomes,
         jsonb_strip_nulls(jsonb_build_object('a', NULL, 'b', 1)) AS stripped;
  ```

  ```text
   sql_null_becomes | stripped
  ------------------+----------
   {"a": null}      | {"b": 1}
  ```

`jsonb_pretty(doc)` indents a document for humans. It is useful in psql, together with `\x`:

```sql
SELECT jsonb_pretty(attributes) FROM products WHERE id = 3;
```

```text
      jsonb_pretty
-------------------------
 {                      +
     "cpu": "Ryzon 9",  +
     "gpu": "RTX-class",+
     "brand": "Titan",  +
     "ram_gb": 32,      +
     "storage": {       +
         "gb": 2048,    +
         "type": "ssd"  +
     },                 +
     "screen_in": 17.3  +
 }
(1 row)
```

> **In production:** generating the JSON response in SQL saves a round trip of rows into objects in your app and is often faster. But it also moves presentation logic into queries. A good compromise is to build JSON in SQL for nested, aggregated shapes (an order with its items) and let the application layer handle the rest. Part IV comes back to this.

> **Under the hood:** PostgreSQL 16 added the SQL-standard spellings `JSON_OBJECT('id': 1, ...)`, `JSON_ARRAY(...)`, `JSON_OBJECTAGG(k VALUE v)` and `JSON_ARRAYAGG(...)`. They do the same jobs. Two differences: `JSON_ARRAY` *skips* SQL NULLs by default (`ABSENT ON NULL`), and the result is `json` unless you add `RETURNING jsonb`. This course uses the `jsonb_*` functions, which you'll see in most existing code.

---

## 4. Reading JSON: the arrow operators, containment and existence

### 4.1 `->`, `->>`, `#>`, `#>>`

Four operators navigate into a document. The rule to memorise: **one arrow returns `jsonb`, two arrows return `text`.**

| Operator | Right operand | Returns | Example |
|---|---|---|---|
| `->` | key (text) or array index (int) | `jsonb` | `attributes -> 'storage'` |
| `->>` | key or index | `text` | `attributes ->> 'brand'` |
| `#>` | path (`text[]`) | `jsonb` | `attributes #> '{storage,type}'` |
| `#>>` | path | `text` | `attributes #>> '{storage,type}'` |

```sql
SELECT attributes -> 'ram_gb'          AS ram_json,
       attributes ->> 'ram_gb'         AS ram_text,
       attributes -> 'storage'         AS storage,
       attributes -> 'storage' -> 'gb' AS storage_gb,
       attributes #> '{storage,type}'  AS st_json,
       attributes #>> '{storage,type}' AS st_text,
       attributes -> 'missing'         AS missing
FROM products
WHERE id = 1;
```

```text
 ram_json | ram_text |          storage           | storage_gb | st_json | st_text | missing
----------+----------+----------------------------+------------+---------+---------+---------
 16       | 16       | {"gb": 512, "type": "ssd"} | 512        | "ssd"   | ssd     |
(1 row)
```

`ram_json` and `ram_text` *look* identical in psql, but one is `jsonb` and the other `text` (check with `pg_typeof`). For strings the difference is visible: `"ssd"` (a JSON string, with quotes) versus `ssd` (plain text). A missing key gives SQL `NULL`, not an error. So does stepping into something that isn't an object, such as `attributes -> 'brand' -> 'x'`.

Array elements are addressed by a zero-based integer, and negative numbers count from the end:

```sql
SELECT attributes -> 'ports' -> 0     AS first_port,
       attributes -> 'ports' ->> -1   AS last_port,
       attributes #>> '{ports,1}'     AS second
FROM products
WHERE id = 7;
```

```text
 first_port | last_port |   second
------------+-----------+-------------
 "HDMI"     | USB-C     | DisplayPort
(1 row)
```

> **Pitfall: JSON arrays count from 0, SQL arrays from 1.** `attributes -> 'ports' -> 0` is the first port. `tags[1]` (section 12) is the first tag. You will mix them up at least once.

### 4.2 Comparing values: the cast trap

Which laptops have at least 16 GB of RAM? `->` returns `jsonb`, and `jsonb` can't be compared with an integer:

```sql
SELECT id, title FROM products WHERE attributes -> 'ram_gb' > 16;
```

```text
ERROR:  operator does not exist: jsonb > integer
```

The tempting fix is `->>`, which gives text, compared with a text literal. It runs without complaint, and it's wrong:

```sql
SELECT id, title FROM products WHERE attributes ->> 'ram_gb' > '16';
```

```text
 id |              title
----+---------------------------------
  2 | AeroBook 15 Pro Laptop
  3 | Titan G17 Gaming Laptop
  4 | StudyMate 11 Chromebook
  5 | AeroBook 13 Laptop (2023 model)
(4 rows)
```

The 4 GB Chromebook and the 8 GB laptop are in, because as *strings* `'4' > '16'` and `'8' > '16'`. The correct version casts the text to a number:

```sql
SELECT id, title, attributes ->> 'ram_gb' AS ram_gb
FROM products
WHERE (attributes ->> 'ram_gb')::int >= 16
ORDER BY id;
```

```text
 id |          title          | ram_gb
----+-------------------------+--------
  1 | AeroBook 13 Laptop      | 16
  2 | AeroBook 15 Pro Laptop  | 32
  3 | Titan G17 Gaming Laptop | 32
 15 | Titan G15 Gaming Laptop | 16
(4 rows)
```

The cast is applied only to rows where the key exists, because for other rows `->>` returns NULL, and `NULL::int` is NULL. But if any product had `"ram_gb": "lots"`, the whole query would fail with `invalid input syntax for type integer`. That is one reason to validate documents (section 9).

You can also cast `jsonb` directly. Since PostgreSQL 11, a `jsonb` number casts to `int`, `numeric` or `float`, and a `jsonb` boolean casts to `boolean`. A JSON string cast this way raises an error (`cannot cast jsonb string to type integer`) rather than silently converting. Comparing two `jsonb` values (`attributes -> 'ram_gb' >= '16'`, where `'16'` is parsed as `jsonb`) also works for numbers. But jsonb's ordering across *types* is arbitrary (any number sorts above any string), so the cast to a SQL type is clearer.

### 4.3 Containment: `@>` and `<@`

`a @> b` ("a contains b") asks whether the document `b` is a *sub-document* of `a`: every key/value in `b` appears in `a`, recursively. `<@` is the same with the operands swapped. Containment is the most important JSONB operator, because a GIN index can answer it (section 10).

```sql
-- wireless headphones with active noise cancelling
SELECT id, title FROM products WHERE attributes @> '{"wireless": true, "anc": true}' ORDER BY id;
```

```text
 id |          title
----+--------------------------
 10 | QuietWave ANC Headphones
 11 | QuietWave Buds
(2 rows)
```

Containment works on nested objects (you only mention the keys you care about):

```sql
SELECT id, title FROM products WHERE attributes @> '{"storage": {"type": "ssd"}}' ORDER BY id;
```

```text
 id |          title
----+-------------------------
  1 | AeroBook 13 Laptop
  2 | AeroBook 15 Pro Laptop
  3 | Titan G17 Gaming Laptop
(3 rows)
```

and on arrays. An array contains another array if every element of the second appears somewhere in the first, in any order:

```sql
SELECT id, title FROM products WHERE attributes @> '{"ports": ["USB-C"]}' ORDER BY id;
```

```text
 id |                  title
----+------------------------------------------
  7 | ClearView 27" 4K Monitor
  9 | PortaScreen 15.6" USB-C Portable Monitor
 17 | USB-C Docking Station
(3 rows)
```

The structure has to match. An array does not contain a bare scalar, except at the top level, where a special rule allows it:

```sql
SELECT '{"size": ["S","M","L"]}'::jsonb @> '{"size": "M"}' AS scalar_vs_array,
       '["S","M"]'::jsonb @> '"M"'                         AS top_level_special,
       '[1,[2,3]]'::jsonb @> '[[3]]'                        AS nested;
```

```text
 scalar_vs_array | top_level_special | nested
-----------------+-------------------+--------
 f               | t                 | t
(1 row)
```

Containment only tests **equality**. There's no way to say "ram_gb ≥ 16" with `@>`. For ranges, use a cast (4.2) or a jsonpath filter (section 7).

### 4.4 Existence: `?`, `?|`, `?&`

`doc ? 'key'` is true if `key` is a **top-level** key of the object, or a top-level **string element** of an array. `?|` means "any of these keys", and `?&` means "all of these keys":

```sql
SELECT id, title FROM products WHERE attributes ? 'gpu' ORDER BY id;
```

```text
 id |          title
----+-------------------------
  3 | Titan G17 Gaming Laptop
 15 | Titan G15 Gaming Laptop
(2 rows)
```

```sql
-- products with a "black" colour option: ? on an array matches string elements
SELECT id FROM products WHERE attributes -> 'colors' ? 'black' ORDER BY id;     -- 10, 16
SELECT '{"a": {"b": 1}}'::jsonb ? 'b' AS nested_key;                            -- f: only top level
```

The existence operators check that a key is *present*, whatever its value. `{"gpu": null}` has the key `gpu`. Use `?&` to check that required keys are present, which section 9 does in a `CHECK` constraint.

> **Pitfall: `?` and database drivers.** Several client libraries (JDBC, and PHP's PDO) use `?` as a parameter placeholder, and choke on these operators. Workarounds are to escape them (`??` in JDBC), or call the underlying functions `jsonb_exists(doc, key)`, `jsonb_exists_any` and `jsonb_exists_all`. Note that the functions can't use a GIN index the way the operators can. Python's psycopg uses `%s`, so it is not affected.

### 4.5 Subscripting (PostgreSQL 14+)

Since PostgreSQL 14 you can also use array-style subscripts on `jsonb`. `attributes['storage']['gb']` is the same as `attributes -> 'storage' -> 'gb'`, and always returns `jsonb`:

```sql
SELECT attributes['storage']['gb'] AS gb,
       attributes['ports'][0]      AS p0
FROM products
WHERE id IN (1, 7)
ORDER BY id;
```

```text
 gb  |   p0
-----+--------
 512 |
     | "HDMI"
(2 rows)
```

Because the result is `jsonb`, comparing it to a string needs a JSON string literal, quotes included: `WHERE attributes['brand'] = '"Titan"'`. In `SELECT` lists, subscripts are mostly a matter of taste. Their real advantage is in `UPDATE`, which comes next.

---

## 5. Changing documents

A `jsonb` value is immutable, like every value in PostgreSQL. "Changing a key" means computing a new document and writing it back with `UPDATE`. The functions and operators below compute new documents. None of them modifies anything in place.

> **Under the hood:** because of MVCC (module 10, and in detail module 16), every `UPDATE` writes a complete new version of the row, including the whole `jsonb` document, however small the change. A 1 MB document updated once a second writes 1 MB per second, plus WAL. This is one reason to keep frequently-changing data (a stock counter, a "last seen" timestamp) out of big documents.

### 5.1 Merge and delete operators

`||` concatenates. For two objects it is a **shallow** merge: keys from the right overwrite keys from the left. For arrays it appends:

```sql
SELECT '{"a": 1, "b": 2}'::jsonb || '{"b": 3, "c": 4}' AS merged,
       '[1,2]'::jsonb || '[3]'                          AS arr,
       '[1,2]'::jsonb || '3'                            AS arr2,
       '{"a":1}'::jsonb || '[1]'                        AS weird;
```

```text
          merged          |    arr    |   arr2    |     weird
--------------------------+-----------+-----------+---------------
 {"a": 1, "b": 3, "c": 4} | [1, 2, 3] | [1, 2, 3] | [{"a": 1}, 1]
(1 row)
```

"Shallow" deserves a demonstration, because it's a classic bug in "patch" endpoints:

```sql
SELECT '{"a": {"x": 1, "y": 2}}'::jsonb || '{"a": {"z": 3}}' AS shallow;
```

```text
     shallow
-----------------
 {"a": {"z": 3}}
(1 row)
```

The nested object was *replaced*, not merged: `x` and `y` are gone. To change one nested key, use `jsonb_set` or a subscript.

`-` deletes. With a text operand it removes a key (or, from an array, every string element equal to it). With a `text[]` it removes several keys, and with an integer it removes an array element by position. `#-` deletes at a path:

```sql
SELECT '{"a":1,"b":2,"c":3}'::jsonb - 'b'            AS minus_key,
       '{"a":1,"b":2,"c":3}'::jsonb - '{a,c}'::text[] AS minus_keys,
       '["x","y","z"]'::jsonb - 1                     AS minus_idx,
       '["x","y","x"]'::jsonb - 'x'                   AS minus_str;
```

```text
    minus_key     | minus_keys | minus_idx  | minus_str
------------------+------------+------------+-----------
 {"a": 1, "c": 3} | {"b": 2}   | ["x", "z"] | ["y"]
(1 row)
```

```sql
SELECT attributes #- '{storage,gb}' FROM products WHERE id = 1;
```

```text
                                               ?column?
-------------------------------------------------------------------------------------------------------
 {"cpu": "M-Core 8", "brand": "AeroTech", "ram_gb": 16, "storage": {"type": "ssd"}, "screen_in": 13.3}
(1 row)
```

### 5.2 `jsonb_set`, `jsonb_set_lax` and `jsonb_insert`

`jsonb_set(doc, path, new_value [, create_missing => true])` replaces the value at `path`. If the *last* path element doesn't exist, it is created (unless `create_missing` is false). Intermediate objects are **not** created: if the parent is missing, the document comes back unchanged.

```sql
SELECT jsonb_set(attributes, '{storage,gb}', '1024') -> 'storage'          AS set_existing,
       jsonb_set(attributes, '{warranty_years}', '2') -> 'warranty_years'  AS set_new,
       jsonb_set(attributes, '{dimensions,w_cm}', '30') -> 'dimensions'    AS missing_parent,
       jsonb_set(attributes, '{warranty_years}', '2', false) ? 'warranty_years' AS create_missing_false
FROM products
WHERE id = 1;
```

```text
        set_existing         | set_new | missing_parent | create_missing_false
-----------------------------+---------+----------------+----------------------
 {"gb": 1024, "type": "ssd"} | 2       |                | f
(1 row)
```

The new value must be `jsonb`. `'1024'` works because an untyped literal is parsed as `jsonb`; for a SQL value use `to_jsonb(...)`. The big trap:

> **Pitfall: `jsonb_set` with a SQL NULL wipes the whole document.** `jsonb_set` is a STRICT function (module 09): if *any* argument is NULL, the result is NULL. `UPDATE products SET attributes = jsonb_set(attributes, '{ram_gb}', to_jsonb($1))` with `$1` = NULL sets the entire `attributes` to NULL. Bazaar's column is `NOT NULL`, so the update fails loudly. On a nullable column, the data is silently lost:
>
> ```sql
> SELECT jsonb_set(attributes, '{ram_gb}', to_jsonb(NULL::int)) IS NULL AS set_sql_null_kills_doc
> FROM products WHERE id = 1;   -- t
> ```
>
> PostgreSQL 13 added `jsonb_set_lax(doc, path, value, create_missing, null_value_treatment)`, where the last argument says what to do with a NULL value: `'use_json_null'` (the default), `'delete_key'`, `'return_target'` (leave the document alone) or `'raise_exception'`.

`jsonb_insert(doc, path, value [, insert_after => false])` is for arrays: it inserts *before* (or after) the element at `path` instead of replacing it. On an object key that already exists it raises an error, telling you to use `jsonb_set` instead:

```sql
SELECT jsonb_insert(attributes -> 'ports', '{0}', '"VGA"')        AS insert_before,
       jsonb_insert(attributes -> 'ports', '{0}', '"VGA"', true)  AS insert_after,
       jsonb_insert(attributes -> 'ports', '{-1}', '"VGA"', true) AS append
FROM products
WHERE id = 7;
```

```text
              insert_before              |              insert_after               |                 append
-----------------------------------------+-----------------------------------------+-----------------------------------------
 ["VGA", "HDMI", "DisplayPort", "USB-C"] | ["HDMI", "VGA", "DisplayPort", "USB-C"] | ["HDMI", "DisplayPort", "USB-C", "VGA"]
(1 row)
```

### 5.3 Subscripts in `UPDATE`

Subscript assignment is the most readable way to change one value, and unlike `jsonb_set` it **creates missing parent objects**:

```sql
BEGIN;

UPDATE products SET attributes['storage']['gb'] = '1024' WHERE id = 1
RETURNING attributes;

UPDATE products SET attributes['dimensions']['w_cm'] = '30' WHERE id = 1
RETURNING attributes -> 'dimensions';

UPDATE products SET attributes['ports'][5] = '"Thunderbolt"' WHERE id = 9
RETURNING attributes -> 'ports';

ROLLBACK;
```

```text
                                                    attributes
-------------------------------------------------------------------------------------------------------------------
 {"cpu": "M-Core 8", "brand": "AeroTech", "ram_gb": 16, "storage": {"gb": 1024, "type": "ssd"}, "screen_in": 13.3}

   ?column?
--------------
 {"w_cm": 30}

                     ?column?
--------------------------------------------------
 ["USB-C", null, null, null, null, "Thunderbolt"]
```

The last one shows the flip side. Assigning past the end of an array pads it with `null`s. The right-hand side must be `jsonb`: a JSON string needs its quotes (`'"Thunderbolt"'`), and a SQL value needs `to_jsonb(...)`.

---

## 6. From JSON back to rows

Reports and joins need rows, not documents. A family of **set-returning functions** turns a document into rows. You call them in `FROM`, usually with `LATERAL` (implicit for functions in `FROM`) so that they run once per row of the table to their left, a pattern you know from module 04.

| Function | Input | One row per | Columns |
|---|---|---|---|
| `jsonb_each(obj)` / `jsonb_each_text(obj)` | object | key | `key text`, `value jsonb` / `text` |
| `jsonb_object_keys(obj)` | object | key | the key |
| `jsonb_array_elements(arr)` / `_text(arr)` | array | element | `value jsonb` / `text` |
| `jsonb_to_record(obj)` | object | (one row) | the columns *you* declare |
| `jsonb_to_recordset(arr)` | array of objects | element | the columns you declare |
| `jsonb_populate_record(NULL::some_type, obj)` | object | (one row) | the columns of `some_type` |

```sql
SELECT key, value, jsonb_typeof(value)
FROM products, jsonb_each(attributes)
WHERE id = 1;
```

```text
    key    |           value            | jsonb_typeof
-----------+----------------------------+--------------
 cpu       | "M-Core 8"                 | string
 brand     | "AeroTech"                 | string
 ram_gb    | 16                         | number
 storage   | {"gb": 512, "type": "ssd"} | object
 screen_in | 13.3                       | number
(5 rows)
```

`jsonb_object_keys` is the tool for "which keys exist?" questions. Combined with `GROUP BY`, it tells you what your schemaless data actually looks like, which is one of the exercises:

```sql
SELECT jsonb_object_keys(attributes) FROM products WHERE id = 3;
```

```text
 jsonb_object_keys
-------------------
 cpu
 gpu
 brand
 ram_gb
 storage
 screen_in
(6 rows)
```

Arrays unpack with `jsonb_array_elements(_text)`. Add `WITH ORDINALITY` to number the elements (starting at 1), which is how you keep the original order:

```sql
SELECT p.id, port, ord
FROM products p
CROSS JOIN LATERAL jsonb_array_elements_text(p.attributes -> 'ports') WITH ORDINALITY AS t(port, ord)
WHERE p.id IN (7, 17)
ORDER BY p.id, ord;
```

```text
 id |    port     | ord
----+-------------+-----
  7 | HDMI        |   1
  7 | DisplayPort |   2
  7 | USB-C       |   3
 17 | HDMI        |   1
 17 | HDMI        |   2
 17 | RJ45        |   3
 17 | USB-A       |   4
 17 | USB-A       |   5
 17 | USB-C       |   6
(9 rows)
```

If the key is missing, the argument is NULL and the function returns zero rows, so with `CROSS JOIN LATERAL` the product disappears from the result. Use `LEFT JOIN LATERAL ... ON true` when you need to keep it. If the value isn't an array at all, you get an error (`cannot extract elements from an object`), not an empty result.

`jsonb_to_record` and `jsonb_to_recordset` go one step further: you declare the columns and their types, and PostgreSQL extracts the matching keys and casts them. Product 33, the sci-fi box set, has an array of objects:

```sql
SELECT b.*
FROM products p, jsonb_to_recordset(p.attributes -> 'contents') AS b(title text, author text)
WHERE p.id = 33;
```

```text
       title        |   author
--------------------+------------
 The Long Orbit     | Mara Quinn
 Starlight Protocol | Lena Vogt
(2 rows)
```

```sql
SELECT r.*
FROM products p, jsonb_to_record(p.attributes) AS r(author text, pages int, isbn text, language text)
WHERE p.category_id = 17
ORDER BY p.id;
```

```text
     author      | pages |     isbn      | language
-----------------+-------+---------------+----------
 Agatha Pembroke |   301 | 9780000000064 | en
 Rosalind Hart   |   334 | 9780000000071 | en
 Agatha Pembroke |   288 | 9780000000088 | en
 Jonas Weber     |   365 | 9780000000095 | de
 Agatha Pembroke |       |               | en
(5 rows)
```

Missing keys become NULL (product 28 has no pages or ISBN), and extra keys are ignored. A value that can't be cast to the declared type is an error, not a NULL: `jsonb_to_record('{"ram_gb": "lots"}') AS r(ram_gb int)` fails with `invalid input syntax for type integer`.

`jsonb_populate_record(NULL::table_type, doc)` uses an existing row type instead of a column list. This is handy for "insert this JSON payload into that table":

```sql
SELECT jsonb_populate_record(NULL::promotions,
       '{"product_id": 10, "discount_pct": 15, "active_during": "[2025-07-01,2025-07-08)"}');
```

```text
                         jsonb_populate_record
------------------------------------------------------------------------
 (,10,15.00,"[""2025-07-01 00:00:00+00"",""2025-07-08 00:00:00+00"")",)
(1 row)
```

The result is a composite value (section 15). `INSERT INTO promotions (product_id, discount_pct, active_during) SELECT product_id, discount_pct, active_during FROM jsonb_populate_record(NULL::promotions, $1)` turns an API payload into a row in one statement, with every value type-checked by PostgreSQL.

---

## 7. SQL/JSON path expressions

The arrow operators navigate one fixed path at a time. They can't say "any element of the array whose `author` is Mara Quinn" or "`ram_gb` greater than or equal to 16" in one expression. **SQL/JSON path** (jsonpath for short) is a small query language for JSON documents, defined by the SQL:2016 standard and stored in its own type, `jsonpath`. If you've used JSONPath in JavaScript or `jq`, the ideas will look familiar, though the syntax differs in details.

### 7.1 Syntax in five minutes

| Piece | Meaning | Example |
|---|---|---|
| `$` | The document root | `$` |
| `.key` / `."key with spaces"` | Object member | `$.storage.gb` |
| `.*` | All members of an object | `$.*` |
| `[n]`, `[n to m]`, `[last]` | Array elements (zero-based) | `$.ports[0]`, `$.ports[last]` |
| `[*]` | All array elements | `$.size[*]` |
| `.**` | All levels below (recursive descent) | `$.**.gb` |
| `? (predicate)` | Filter: keep items for which the predicate is true | `$.ram_gb ? (@ >= 16)` |
| `@` | The current item inside a filter | `? (@.author == "Mara Quinn")` |
| `$name` | A variable passed in from SQL | `? (@ >= $min)` |
| `.type()`, `.size()`, `.double()`, `.floor()`, `.keyvalue()`, `.datetime()` | Item methods | `$.ports.size()` |

Inside predicates you have `==`, `!=` (or `<>`), `<`, `<=`, `>`, `>=`, `&&`, `||`, `!`, `like_regex "pattern" flag "i"`, `starts with "prefix"`, `exists (path)` and `(...) is unknown`. Strings use **double quotes**. The whole path is a SQL string literal in single quotes.

A path returns a *sequence* of zero or more JSON items. The functions differ in what they do with that sequence:

| Function / operator | Returns |
|---|---|
| `jsonb_path_query(doc, path)` | A set: one row per item |
| `jsonb_path_query_array(doc, path)` | All items wrapped in one JSON array |
| `jsonb_path_query_first(doc, path)` | The first item (or NULL) |
| `jsonb_path_exists(doc, path)` / `doc @? path` | true if the path returns at least one item |
| `jsonb_path_match(doc, path)` / `doc @@ path` | The result of a path that is itself a *predicate* (true/false/null) |

```sql
SELECT jsonb_path_query(attributes, '$.storage.gb') AS gb,
       jsonb_path_query(attributes, '$.*')          AS everything
FROM products
WHERE id = 1;
```

```text
 gb  |         everything
-----+----------------------------
 512 | "M-Core 8"
     | "AeroTech"
     | 16
     | {"gb": 512, "type": "ssd"}
     | 13.3
(5 rows)
```

(Two set-returning functions in one `SELECT` list are zipped together, and the shorter one is padded with NULLs. Prefer one per query, in `FROM`.)

```sql
SELECT jsonb_path_query_array(attributes, '$.ports[last]')   AS last_p,
       jsonb_path_query_array(attributes, '$.ports[0 to 1]') AS first_two,
       jsonb_path_query_first(attributes, '$.ports.size()')  AS n
FROM products
WHERE id = 17;
```

```text
  last_p   |    first_two     | n
-----------+------------------+---
 ["USB-C"] | ["HDMI", "HDMI"] | 6
(1 row)
```

### 7.2 Filters: the reason jsonpath exists

A filter keeps the items for which a predicate holds. With `@?` ("does the path return anything?") it becomes a `WHERE` condition. Monitors with a refresh rate of at least 100 Hz:

```sql
SELECT id, title FROM products WHERE attributes @? '$.refresh_hz ? (@ >= 100)';
```

```text
 id |            title
----+------------------------------
  8 | UltraWide 34" Curved Monitor
(1 row)
```

There's no cast and no string-comparison trap. jsonpath compares JSON numbers as numbers. And items of the wrong type (a string where a number was expected) simply don't match, instead of raising an error. The `@@` operator takes a path that *is* a predicate, which reads more naturally for simple conditions:

```sql
SELECT count(*) FROM products WHERE attributes @@ '$.screen_in >= 15';   -- 3
```

Filters shine on arrays, where `@>` can only test equality. Box sets that contain a book by Mara Quinn:

```sql
SELECT id, title
FROM products
WHERE attributes @? '$.contents[*] ? (@.author == "Mara Quinn")';
```

```text
 id |            title
----+-----------------------------
 33 | The Complete Sci-Fi Box Set
(1 row)
```

The ports of each product that start with "USB", using `starts with`:

```sql
SELECT id, jsonb_path_query_array(attributes, '$.ports[*] ? (@ starts with "USB")')
FROM products
WHERE attributes ? 'ports'
ORDER BY id;
```

```text
 id |   jsonb_path_query_array
----+-----------------------------
  7 | ["USB-C"]
  9 | ["USB-C"]
 17 | ["USB-A", "USB-A", "USB-C"]
(3 rows)
```

A case-insensitive regular expression, and a filter on the root object combining two keys:

```sql
SELECT id, title FROM products WHERE attributes @? '$.brand ? (@ like_regex "^aero" flag "i")' ORDER BY id;   -- 1, 2, 5
SELECT id, title FROM products WHERE attributes @? '$ ? (@.wireless == true && @.battery_h > 10)';           -- 10
```

**Variables.** Never build a jsonpath by concatenating user input into the string. That is SQL injection's little cousin. Pass values as a JSON object in the third argument and refer to them as `$name`:

```sql
SELECT id, title
FROM products
WHERE jsonb_path_exists(attributes, '$.ram_gb ? (@ >= $min)', '{"min": 32}')
ORDER BY id;
```

```text
 id |          title
----+-------------------------
  2 | AeroBook 15 Pro Laptop
  3 | Titan G17 Gaming Laptop
(2 rows)
```

From an application you'd send `'{"min": 32}'` as a bound parameter (`jsonb_build_object('min', $1)` works too).

**Item methods** inspect or convert items. A few useful ones:

```sql
SELECT jsonb_path_query(attributes, '$.storage.keyvalue()') FROM products WHERE id = 3;
```

```text
              jsonb_path_query
--------------------------------------------
 {"id": 116, "key": "gb", "value": 2048}
 {"id": 116, "key": "type", "value": "ssd"}
(2 rows)
```

(`id` identifies the object the pair came from. It's an internal offset, so don't rely on its value.) `$.x.type()` returns `"number"`, `"string"` and so on, `$.x.size()` the length of an array, and `$.x.double()` / `.floor()` / `.ceiling()` / `.abs()` convert numbers. PostgreSQL 17 added `.bigint()`, `.boolean()`, `.decimal(p,s)`, `.integer()`, `.number()`, `.string()`, `.date()`, `.time()` and `.timestamp_tz()`. `.datetime()` parses a string into a date/time item, so you can compare timestamps stored as strings.

### 7.3 Lax and strict mode

Real documents are irregular: sometimes a key is missing, sometimes a value is a scalar where you expected an array. jsonpath has two modes for handling this, chosen by a prefix:

- **`lax`** (the default) is forgiving. A missing key produces an empty sequence instead of an error. When an array is accessed as if it were a single item (for example, by a filter or a key accessor), it is **automatically unwrapped**: the operation applies to each element.
- **`strict`** raises an error on missing keys and does no automatic unwrapping. You must write `[*]` explicitly.

```sql
SELECT jsonb_path_exists(attributes, 'lax $.size ? (@ == "M")')       AS lax_auto_unwrap,
       jsonb_path_exists(attributes, 'strict $.size ? (@ == "M")')    AS strict_no_unwrap,
       jsonb_path_exists(attributes, 'strict $.size[*] ? (@ == "M")') AS strict_explicit
FROM products
WHERE id = 47;
```

```text
 lax_auto_unwrap | strict_no_unwrap | strict_explicit
-----------------+------------------+-----------------
 t               | f                | t
(1 row)
```

In lax mode, `$.size ? (@ == "M")` compared each *element* of the size array with `"M"`. In strict mode, it compared the whole array with `"M"`, which isn't equal. Strict mode errors on a missing key:

```sql
SELECT jsonb_path_query(attributes, 'strict $.foo') FROM products WHERE id = 47;
```

```text
ERROR:  JSON object does not contain key "foo"
```

Every `jsonb_path_*` function takes a final `silent => true` argument that suppresses these structural errors (and type errors such as `$.a + 1` on a string) and returns an empty result instead. `@?` and `@@` are always silent: `'{"a": "x"}'::jsonb @@ '$.a > 1'` returns NULL rather than an error.

**Which mode should you use?** Use lax for querying irregular data, which is the normal case with product attributes. Use strict when the shape is guaranteed and you want deviations to be errors, for example inside validation functions. Lax unwrapping can surprise you with `.**` and with arrays of arrays, so write `[*]` explicitly when you mean "each element", even in lax mode. It documents your intent.

---

## 8. The SQL-standard JSON functions (PostgreSQL 16 and 17)

PostgreSQL 16 and 17 implemented the SQL:2023 JSON functions. They overlap with the `jsonb_*` functions you've seen, but they are standard, so they look the same in Oracle, Db2 and MySQL, and they give fine-grained control over errors and empty results.

| Function | Returns | Rough equivalent |
|---|---|---|
| `IS JSON [OBJECT \| ARRAY \| SCALAR] [WITH UNIQUE KEYS]` (16) | boolean: is this text valid JSON? | A cast inside an exception handler |
| `JSON_EXISTS(doc, path)` (17) | boolean | `doc @? path` |
| `JSON_VALUE(doc, path [RETURNING type])` (17) | a **scalar**, as SQL text or the requested type | `(doc #>> ...)::type` |
| `JSON_QUERY(doc, path [WITH WRAPPER])` (17) | a **JSON fragment** (object, array) | `jsonb_path_query_first` |
| `JSON_TABLE(doc, path COLUMNS (...))` (17) | a **table** | `jsonb_to_recordset` + `LATERAL` |

`IS JSON` lets you check text before you cast it, for example when loading JSON from a CSV file:

```sql
SELECT '{"a":1}' IS JSON OBJECT                 AS obj,
       '[1]' IS JSON SCALAR                     AS scalar,
       '{"a":1,"a":2}' IS JSON WITH UNIQUE KEYS AS uniq,
       'nope' IS JSON                           AS valid;
```

```text
 obj | scalar | uniq | valid
-----+--------+------+-------
 t   | f      | f    | f
(1 row)
```

### 8.1 `JSON_VALUE`, `JSON_QUERY`, `JSON_EXISTS`

```sql
SELECT id,
       JSON_VALUE(attributes, '$.ram_gb' RETURNING int) AS ram_gb,
       JSON_VALUE(attributes, '$.storage.type')         AS storage_type,
       JSON_QUERY(attributes, '$.storage')              AS storage,
       JSON_EXISTS(attributes, '$.gpu')                 AS has_gpu
FROM products
WHERE category_id = 3
ORDER BY id;
```

```text
 id | ram_gb | storage_type |           storage           | has_gpu
----+--------+--------------+-----------------------------+---------
  1 |     16 | ssd          | {"gb": 512, "type": "ssd"}  | f
  2 |     32 | ssd          | {"gb": 1024, "type": "ssd"} | f
  3 |     32 | ssd          | {"gb": 2048, "type": "ssd"} | t
  4 |      4 | emmc         | {"gb": 64, "type": "emmc"}  | f
  5 |      8 |              |                             | f
 15 |     16 |              |                             | t
 16 |        |              |                             | f
(7 rows)
```

`JSON_VALUE` is for scalars, and `RETURNING` casts for you. `JSON_QUERY` is for objects and arrays. Mixing them up doesn't fail by default; you just get NULL. Both accept two clauses that decide what happens in the awkward cases:

- `... ON EMPTY`: the path found nothing. Choices are `NULL` (default), `ERROR`, or `DEFAULT expr`.
- `... ON ERROR`: something went wrong (wrong type, more than one item, a failed cast). Same choices, `NULL` by default.

```sql
SELECT JSON_VALUE(attributes, '$.storage') FROM products WHERE id = 1;                  -- NULL (an object is not a scalar)
SELECT JSON_VALUE(attributes, '$.storage' ERROR ON ERROR) FROM products WHERE id = 1;
```

```text
ERROR:  JSON path expression in JSON_VALUE must return single scalar item
```

```sql
SELECT JSON_VALUE(attributes, '$.brand' RETURNING int DEFAULT -1 ON ERROR)   FROM products WHERE id = 1;  -- -1
SELECT JSON_VALUE(attributes, '$.warranty' RETURNING int DEFAULT 0 ON EMPTY) FROM products WHERE id = 1;  -- 0
```

The NULL-on-error default is convenient but dangerous in reports: a typo in a path, or a value of the wrong type, silently becomes NULL. Use `ERROR ON ERROR` while developing a query.

`JSON_QUERY` must return a single item. To collect several, ask for a wrapper. With `RETURNING text OMIT QUOTES`, a JSON string comes back without its quotes:

```sql
SELECT JSON_QUERY(attributes, '$.size[*]' WITH WRAPPER)              AS wrapped,
       JSON_QUERY(attributes, '$.size[0]')                           AS quoted,
       JSON_QUERY(attributes, '$.size[0]' RETURNING text OMIT QUOTES) AS unquoted
FROM products
WHERE id = 47;
```

```text
           wrapped           | quoted | unquoted
-----------------------------+--------+----------
 ["XS", "S", "M", "L", "XL"] | "XS"   | XS
(1 row)
```

Variables are passed with `PASSING value AS name`, which is often more convenient than building a JSON object: `JSON_EXISTS(attributes, '$.ram_gb ? (@ >= $min)' PASSING 16 AS min)`.

### 8.2 `JSON_TABLE`: a document as a table

`JSON_TABLE` is the most useful addition of PostgreSQL 17. You describe the rows (a path) and the columns (each with its own path and type), and it produces a relational table. It's used in `FROM`, with an implicit `LATERAL`, just like `jsonb_to_recordset`, but it handles nesting, defaults, existence checks and ordinality in one declaration. A laptop spec sheet:

```sql
SELECT p.id, jt.*
FROM products p,
     JSON_TABLE(p.attributes, '$'
         COLUMNS (
             brand     text          PATH '$.brand',
             ram_gb    int           PATH '$.ram_gb',
             screen_in numeric(4,1)  PATH '$.screen_in',
             disk_gb   int           PATH '$.storage.gb' DEFAULT 0 ON EMPTY,
             has_gpu   boolean       EXISTS PATH '$.gpu'
         )) AS jt
WHERE p.category_id = 3
ORDER BY p.id;
```

```text
 id |   brand   | ram_gb | screen_in | disk_gb | has_gpu
----+-----------+--------+-----------+---------+---------
  1 | AeroTech  |     16 |      13.3 |     512 | f
  2 | AeroTech  |     32 |      15.6 |    1024 | f
  3 | Titan     |     32 |      17.3 |    2048 | t
  4 | StudyMate |      4 |      11.6 |      64 | f
  5 | AeroTech  |      8 |      13.3 |       0 | f
 15 | Titan     |     16 |      15.6 |       0 | t
 16 |           |        |           |       0 | f
(7 rows)
```

The row path `'$'` produces one row per document. A row path such as `'$.contents[*]'` would produce one row per array element. `NESTED PATH` expands an inner array *within* each row, and `FOR ORDINALITY` numbers the rows. Clothing sizes, one row per size:

```sql
SELECT p.id, jt.*
FROM products p,
     JSON_TABLE(p.attributes, '$'
         COLUMNS (
             color text PATH '$.color',
             NESTED PATH '$.size[*]' COLUMNS (
                 size_no FOR ORDINALITY,
                 size    text PATH '$'
             )
         )) AS jt
WHERE p.id IN (47, 54)
ORDER BY p.id, jt.size_no;
```

```text
 id |   color    | size_no | size
----+------------+---------+-------
 47 | white      |       1 | XS
 47 | white      |       2 | S
 47 | white      |       3 | M
 47 | white      |       4 | L
 47 | white      |       5 | XL
 54 | multicolor |       1 | 4-6
 54 | multicolor |       2 | 7-9
 54 | multicolor |       3 | 10-12
(8 rows)
```

> **In production:** `JSON_TABLE` is ideal for *ingesting* JSON: a supplier feed, a webhook payload or an exported file can be turned into typed rows with one `INSERT ... SELECT ... FROM JSON_TABLE(...)`. That's often clearer than a Python loop, and much faster.

---

## 9. Validating JSONB with constraints

"Schemaless" doesn't have to mean "anything goes". The database can still enforce the rules that matter, with ordinary `CHECK` constraints built from the functions above. Validation is layered, from cheap and universal to specific:

**The shape of the column.** The reference migration adds this constraint (it's already in your database, so there's no need to run it):

```sql
ALTER TABLE products
    ADD CONSTRAINT products_attributes_is_object CHECK (jsonb_typeof(attributes) = 'object');
```

Without it, `attributes` could be an array, a string or `null`, and every query that does `attributes -> 'key'` would quietly return NULL for such rows. With it:

```sql
UPDATE products SET attributes = '[1,2]' WHERE id = 62;
```

```text
ERROR:  new row for relation "products" violates check constraint "products_attributes_is_object"
```

Combined with `NOT NULL DEFAULT '{}'`, this means every product has an object, possibly empty, so queries never need `coalesce`.

**Required keys.** `?&` checks that a set of keys is present. A rule for books (categories 16, 17 and 19) that requires an author and a language, an author that is a string, and a page count that is a number if present:

```sql
BEGIN;

ALTER TABLE products ADD CONSTRAINT products_book_attributes_check
    CHECK (category_id NOT IN (16, 17, 19)
           OR (attributes ?& ARRAY['author', 'language']
               AND jsonb_typeof(attributes -> 'author') = 'string'
               AND (NOT attributes ? 'pages' OR jsonb_typeof(attributes -> 'pages') = 'number')));

UPDATE products SET attributes = attributes - 'author' WHERE id = 19;

ROLLBACK;
```

```text
ERROR:  new row for relation "products" violates check constraint "products_book_attributes_check"
DETAIL:  Failing row contains (19, 2, 16, BW-SF-001, The Long Orbit, A generation ship, a mutiny, and a 300-year-old secret., 14.99, active, 2023-03-25 12:00:00+00, 2025-02-01 00:00:00+00, {"isbn": "9780000000019", "pages": 412, "language": "en"}, {book,sci-fi}).
```

**Typed values with a range.** "If `ram_gb` is present, it is a positive number." Here you must be careful about evaluation order. SQL does **not** promise to evaluate `AND`/`OR` operands left to right, so `jsonb_typeof(x) = 'number' AND (x ->> 'k')::numeric > 0` might attempt the cast first and fail with a cast error instead of a clean constraint violation. `CASE` *does* guarantee order:

```sql
BEGIN;

ALTER TABLE products ADD CONSTRAINT products_ram_gb_positive CHECK (
    CASE
        WHEN NOT attributes ? 'ram_gb'                        THEN true
        WHEN jsonb_typeof(attributes -> 'ram_gb') <> 'number' THEN false
        ELSE (attributes ->> 'ram_gb')::numeric > 0
    END
);

UPDATE products SET attributes['ram_gb'] = '"sixteen"' WHERE id = 1;   -- violates products_ram_gb_positive

ROLLBACK;
```

A few practical rules:

- **Validate what queries depend on**: keys that are filtered, sorted or joined on, and anything a cast in a query relies on. Leave purely descriptive keys free.
- **Category-specific rules hard-code category ids** in the constraint, which is brittle. When the rules grow, move them into a function (module 09) or into a table of required keys per category checked by a trigger (also module 09). A `CHECK` constraint can call a function, but it must only look at the row itself. It must not query other tables, because PostgreSQL won't re-check the row when the other table changes.
- **Adding a constraint scans the table** to check existing rows, while holding a lock that blocks writes. On a big table, use `ADD CONSTRAINT ... NOT VALID` (enforced for new writes only) and `VALIDATE CONSTRAINT` later, which takes a weaker lock. Module 26 covers this zero-downtime pattern.
- There's no built-in JSON Schema validator. Extensions such as `pg_jsonschema` add one if you need full JSON Schema.

---

## 10. Indexing JSONB: a preview

Module 11 covers indexes in depth. Here's what you need to know now, so that you design JSONB columns that *can* be indexed.

A B-tree index on `attributes` itself is useless: it can only answer `=` and `<` on whole documents. For JSONB there are two complementary approaches:

**1. A GIN index on the whole document.** GIN (Generalised Inverted Index) is the same structure as the index at the back of a book: for each *item* (a key, or a key/value pair), it lists the rows that contain it. PostgreSQL ships two flavours for `jsonb`:

| Operator class | Indexes | Supports | Size |
|---|---|---|---|
| `jsonb_ops` (default) | Every key and every value, separately | `@>`, `?`, `?|`, `?&`, `@?`, `@@` | Larger |
| `jsonb_path_ops` | A hash of each *path-to-value* (`storage.type = "ssd"`) | `@>`, `@?`, `@@` (not the key-existence operators) | Smaller, often faster for `@>` |

```sql
BEGIN;
CREATE INDEX products_attributes_gin     ON products USING gin (attributes);                 -- jsonb_ops
CREATE INDEX products_attributes_pathops ON products USING gin (attributes jsonb_path_ops);
CREATE INDEX products_ram_gb_idx         ON products (((attributes ->> 'ram_gb')::int));     -- see below
SET LOCAL enable_seqscan = off;
-- ... the EXPLAINs below ...
ROLLBACK;
```

With 62 products, the planner rightly prefers reading the whole table, so to *see* an index used on this small dataset you have to discourage sequential scans with `SET LOCAL enable_seqscan = off` (a debugging switch, never a production setting). With both GIN indexes present:

```text
EXPLAIN (COSTS OFF) SELECT id FROM products WHERE attributes @> '{"brand": "Titan"}';

 Bitmap Heap Scan on products
   Recheck Cond: (attributes @> '{"brand": "Titan"}'::jsonb)
   ->  Bitmap Index Scan on products_attributes_pathops
         Index Cond: (attributes @> '{"brand": "Titan"}'::jsonb)

EXPLAIN (COSTS OFF) SELECT id FROM products WHERE attributes ? 'gpu';

 Bitmap Heap Scan on products
   Recheck Cond: (attributes ? 'gpu'::text)
   ->  Bitmap Index Scan on products_attributes_gin
         Index Cond: (attributes ? 'gpu'::text)
```

The planner picked the smaller `jsonb_path_ops` index for containment and had to use `jsonb_ops` for `?`. With only `jsonb_path_ops`, the `?` query falls back to a sequential scan. Which operators *your queries* use decides which operator class you need. This is one reason to standardise on `@>` for attribute filters.

The important limit is that **GIN on jsonb only helps equality-style questions**. A jsonpath *range* filter is accepted by the planner, but the index can't look it up. Here's a 200,000-row scratch table with `{"ram_gb": 0..63, ...}` documents and a `jsonb_path_ops` index:

```text
-- @? '$.ram_gb ? (@ == 32)'
   ->  Bitmap Index Scan on t_a_idx (actual rows=3125 loops=1)
 Execution Time: 9.541 ms

-- @? '$.ram_gb ? (@ >= 62)'
         Rows Removed by Index Recheck: 193750
   ->  Bitmap Index Scan on t_a_idx (actual rows=200000 loops=1)
 Execution Time: 89.650 ms
```

For the range, the "index scan" returned all 200,000 rows and the recheck threw away 193,750 of them. That's slower than no index at all.

**2. An expression index on one extracted key.** If one key is filtered or sorted by range all the time, index exactly the expression your queries use:

```sql
CREATE INDEX products_ram_gb_idx ON products (((attributes ->> 'ram_gb')::int));   -- created in the transaction above
```

```text
EXPLAIN (COSTS OFF) SELECT id FROM products WHERE (attributes ->> 'ram_gb')::int >= 16;

 Bitmap Heap Scan on products
   Recheck Cond: (((attributes ->> 'ram_gb'::text))::integer >= 16)
   ->  Bitmap Index Scan on products_ram_gb_idx
         Index Cond: (((attributes ->> 'ram_gb'::text))::integer >= 16)
```

The query must repeat the indexed expression *exactly*. `attributes ->> 'brand' = 'Titan'` can't use an index on `attributes -> 'brand'`. A partial index (`WHERE category_id = 3`) keeps it small, because only laptops have RAM. And if a key is important enough to need its own B-tree index, ask whether it should be a real column (section 11).

> **Under the hood:** PostgreSQL keeps no statistics about what's *inside* a `jsonb` column. It knows how many rows the table has and how wide the column is, but not how many products have `"brand": "Titan"`. So it estimates containment selectivity with a fixed guess, and for large tables those guesses can lead to bad plans. An expression index *does* get statistics for its expression after `ANALYZE`, which is another reason to promote hot keys. Module 12 covers estimates.

---

## 11. When JSONB is right, and when it is wrong

JSONB is so convenient that it tempts you to use it for everything. Here is the honest version of the trade-off.

### 11.1 The problem it solves: EAV, revisited

The classic relational answer to "each kind of product has different properties" is **EAV** (entity-attribute-value). You may remember it from the anti-patterns in module 07:

```text
product_attributes(product_id, attribute_name, attribute_value text)
  (1, 'ram_gb', '16'), (1, 'cpu', 'M-Core 8'), (1, 'screen_in', '13.3'), ...
```

EAV is flexible, but it fights SQL at every step. All values are text, so `'4' > '16'` is back. You can't put a `NOT NULL` or `CHECK` on "ram_gb of a laptop". Each property you want to filter on costs another self-join, and reassembling one product means pivoting rows back into columns. JSONB keeps EAV's flexibility (any key, per row) without its costs. The document is stored with the row, values keep their JSON types, a whole product is one value, and GIN indexes answer multi-key filters in one lookup.

### 11.2 Where JSONB goes wrong

- **Data you join on.** A `seller_id` inside a document can't have a foreign key. Nothing stops it from pointing at a seller that doesn't exist, and nothing cascades when the seller is deleted. Relationships belong in columns.
- **Data with integrity rules.** Prices, quantities, statuses and anything with a uniqueness rule need types, `NOT NULL`, `CHECK` and `UNIQUE`. You *can* write those rules over JSONB (section 9), but they're clumsier and easy to forget.
- **Data that changes often.** Every change rewrites the whole document (section 5). A counter inside a document is an anti-pattern.
- **Data the planner needs to understand.** No statistics inside documents means poor row estimates for selective filters on big tables (section 10).
- **Schema drift.** "Schemaless" really means "the schema lives in your application code, in every version that ever wrote a document". After two years you'll have `ram_gb`, `ramGB` and `ram: "16 GB"` side by side, and every query has to handle all three. Somebody has to own the shape of the data: either the database (columns, constraints) or a single application layer with validation and migrations of old documents.
- **Big documents.** Values over about 2 kB are compressed and moved out of line ("TOAST", module 16). Reading one key then means fetching and decompressing the whole document.

### 11.3 A rule of thumb

> **Use a column** for anything you join on, constrain, aggregate across rows, or update independently, and for anything every row has.
> **Use JSONB** for properties that vary between rows, are mostly read and displayed together, and are filtered by equality if at all, plus payloads you store "as received".
> **Promote** a key to a real column (or a generated column) when it acquires any property of the first group.

Bazaar follows this rule. `price`, `status`, `seller_id` and `category_id` are columns, because they are joined, constrained and aggregated. `ram_gb`, `author` and `size` are attributes: they differ by category, are shown on the product page, and are filtered by the occasional "≥ 16 GB" query. If laptops became a big business and every search filtered on RAM, promoting `ram_gb` would be the next step:

```sql
-- a generated column: a real, typed, indexable column derived from the document (module 02)
ALTER TABLE products
    ADD COLUMN ram_gb int GENERATED ALWAYS AS ((attributes ->> 'ram_gb')::int) STORED;
```

The application keeps writing the document, and queries, indexes and statistics get a real column.

---

## 12. Arrays

Every PostgreSQL type has an array type: `int[]`, `text[]`, `timestamptz[]`, even `jsonb[]` and arrays of your own enums. You've already *produced* arrays with `array_agg` (module 05) and *consumed* them with `= ANY (...)` (module 04). Now we store them. Bazaar's `products.tags` is a `text[] NOT NULL DEFAULT '{}'`.

### 12.1 Writing arrays

There are two notations. The **literal** is a string in curly braces, and the **constructor** `ARRAY[...]` is an expression:

```sql
SELECT '{laptop,gaming}'::text[]                     AS literal,
       ARRAY['laptop', 'gaming']                     AS constructor,
       '{"usb-c","has space","quote\"d",NULL}'::text[] AS tricky,
       ARRAY[]::text[]                               AS empty;
```

```text
     literal     |   constructor   |               tricky                | empty
-----------------+-----------------+-------------------------------------+-------
 {laptop,gaming} | {laptop,gaming} | {usb-c,"has space","quote\"d",NULL} | {}
(1 row)
```

In the literal syntax, elements with spaces, commas, quotes or braces need double quotes, and an unquoted `NULL` is a NULL element. The constructor uses ordinary SQL expressions, so it's what you want in code. From an application you'd pass a Python list as one parameter, and the driver sends it as an array (module 22).

### 12.2 Indexing, slices and length

**SQL arrays are 1-based.** Subscripts out of range return NULL rather than raising an error. A slice `[from:to]` returns an array:

```sql
SELECT tags, tags[1] AS first, tags[2:3] AS slice, tags[0] AS zero, tags[99] AS out_of_range,
       cardinality(tags), array_length(tags, 1)
FROM products
WHERE id = 10;
```

```text
                 tags                 |   first    |     slice      | zero | out_of_range | cardinality | array_length
--------------------------------------+------------+----------------+------+--------------+-------------+--------------
 {headphones,wireless,anc,bestseller} | headphones | {wireless,anc} |      |              |           4 |            4
(1 row)
```

Prefer `cardinality(arr)`, the total number of elements. `array_length(arr, 1)` returns **NULL** for an empty array, not 0, which turns `WHERE array_length(tags, 1) = 0` into a condition that is never true.

The manipulation functions return new arrays:

```sql
SELECT array_position(tags, 'anc')                              AS pos,
       array_positions(ARRAY['HDMI', 'HDMI', 'USB-C'], 'HDMI')  AS all_pos,
       array_append(tags, 'sale')                               AS appended,
       array_remove(tags, 'anc')                                AS removed,
       'new'::text || tags                                      AS prepended
FROM products
WHERE id = 10;
```

```text
 pos | all_pos |                 appended                  |             removed              |                prepended
-----+---------+-------------------------------------------+----------------------------------+------------------------------------------
   3 | {1,2}   | {headphones,wireless,anc,bestseller,sale} | {headphones,wireless,bestseller} | {new,headphones,wireless,anc,bestseller}
(1 row)
```

The `::text` in the last column matters. `'new' || tags` fails with `malformed array literal: "new"`, because PostgreSQL guesses that an untyped literal next to an array is *another array*. `array_to_string(tags, ', ')` and `string_to_array('a,b', ',')` convert to and from delimited text.

To add a tag only if it isn't there yet, guard the update:

```sql
UPDATE products
SET tags = array_append(tags, 'sale')
WHERE id = 10
  AND NOT 'sale' = ANY (tags);
```

Arrays are ordered and may contain duplicates. PostgreSQL has no built-in "set" type, so uniqueness is your job.

### 12.3 Searching arrays: `ANY`, `ALL`, `@>`, `<@`, `&&`

```sql
SELECT id, title FROM products WHERE 'gaming' = ANY (tags) ORDER BY id;          -- has the tag
SELECT id, title FROM products WHERE tags @> ARRAY['wireless', 'anc'] ORDER BY id; -- has all of these
SELECT id, title FROM products WHERE tags && ARRAY['4k', 'usb-c'] ORDER BY id;     -- has any of these (overlap)
```

```text
 id |            title
----+------------------------------
  3 | Titan G17 Gaming Laptop
  8 | UltraWide 34" Curved Monitor
 15 | Titan G15 Gaming Laptop
(3 rows)

 id |          title
----+--------------------------
 10 | QuietWave ANC Headphones
 11 | QuietWave Buds
(2 rows)

 id |                  title
----+------------------------------------------
  7 | ClearView 27" 4K Monitor
  9 | PortaScreen 15.6" USB-C Portable Monitor
 17 | USB-C Docking Station
(3 rows)
```

`a <@ b` (a is contained in b) completes the set. Which one to use? `'x' = ANY (tags)` reads naturally, but **a GIN index on `tags` supports only `@>`, `<@`, `&&` and `=`**, not `ANY`. On a large table, write `tags @> ARRAY['gaming']` to make the index usable. Module 11 builds this index (`products_tags_idx`).

`ALL` is the "every element" counterpart. Mind the edge cases. Over an empty array, `ALL` is true and `ANY` is false (there are no counterexamples, and no witnesses). And NULL elements follow three-valued logic, just like `NOT IN` with a NULL in the list (module 03):

```sql
SELECT 20 > ALL (ARRAY[5, 10, 15]) AS gt_all, 20 > ALL ('{}'::int[]) AS gt_all_empty, 20 = ANY ('{}'::int[]) AS any_empty;
SELECT 'b' = ANY (ARRAY['a', NULL]) AS b_any_with_null, 'b' <> ALL (ARRAY['a', NULL]) AS b_not_in;
```

```text
 gt_all | gt_all_empty | any_empty
--------+--------------+-----------
 t      | t            | f

 b_any_with_null | b_not_in
-----------------+----------
                 |
```

> **In production:** `WHERE id = ANY ($1)` with one array parameter is the right way to pass "a list of ids" from an application. The SQL text is the same whatever the list length, so the statement can be prepared once and reused. `IN (...)` with a growing list of placeholders generates a different statement for every list length.

### 12.4 From arrays to rows and back

`unnest(arr)` turns an array into rows, and `WITH ORDINALITY` adds the position, exactly as with `jsonb_array_elements`:

```sql
SELECT p.id, t.tag, t.pos
FROM products p
CROSS JOIN LATERAL unnest(p.tags) WITH ORDINALITY AS t(tag, pos)
WHERE p.id IN (1, 10)
ORDER BY p.id, t.pos;
```

```text
 id |    tag     | pos
----+------------+-----
  1 | ultralight |   1
  1 | laptop     |   2
  1 | bestseller |   3
 10 | headphones |   1
 10 | wireless   |   2
 10 | anc        |   3
 10 | bestseller |   4
(7 rows)
```

`unnest` then `GROUP BY` answers "how is this array used across the table?":

```sql
SELECT tag, count(*) AS products
FROM products, unnest(tags) AS tag
GROUP BY tag
ORDER BY products DESC, tag
LIMIT 5;
```

```text
   tag    | products
----------+----------
 book     |       15
 clothing |       10
 laptop   |        7
 cookware |        6
 cutlery  |        6
(5 rows)
```

and `array_agg` goes the other way. Each seller's distinct tags:

```sql
SELECT seller_id, array_agg(DISTINCT tag ORDER BY tag) AS seller_tags
FROM products, unnest(tags) AS tag
WHERE seller_id = 5
GROUP BY seller_id;
```

```text
 seller_id |                  seller_tags
-----------+------------------------------------------------
         5 | {discontinued,garden,handmade,outdoor,planter}
(1 row)
```

`unnest` with several arrays zips them side by side (padding the shorter with NULLs): `SELECT * FROM unnest(ARRAY[1,2,3], ARRAY['a','b']) AS t(n, s)` gives `(1,a), (2,b), (3,NULL)`. This is a handy way to bulk-insert from parallel lists sent by an application.

### 12.5 Multi-dimensional arrays: mostly avoid them

PostgreSQL arrays can have several dimensions, but the implementation has surprises:

```sql
SELECT ARRAY[[1,2],[3,4]]              AS matrix,
       (ARRAY[[1,2],[3,4]])[2][1]      AS cell,
       (ARRAY[[1,2],[3,4]])[2]         AS row_attempt,
       array_dims(ARRAY[[1,2],[3,4]]);
```

```text
    matrix     | cell | row_attempt | array_dims
---------------+------+-------------+------------
 {{1,2},{3,4}} |    3 |             | [1:2][1:2]
(1 row)
```

- The **type doesn't record the number of dimensions**: `int[]` and `int[][]` are the same type, and a column declared `int[][]` happily accepts a 1-D array.
- Sub-arrays must all have the same length (`ARRAY[[1,2],[3]]` is an error). There are no "jagged" arrays.
- `arr[2]` on a 2-D array is NULL, not the second row. You need a slice: `arr[2:2][1:2]`.
- `unnest` flattens all dimensions into one list of elements.
- Arrays can even have custom lower bounds (`'[0:2]={a,b,c}'::text[]`), which breaks code that assumes index 1.

If you need a matrix, a JSON array of arrays (`[[1,2],[3]]`) or a table with `(row, col)` columns is usually clearer.

### 12.6 Arrays or a junction table?

Tags could also be modelled the module 07 way, with a `tags` table and a `product_tags(product_id, tag_id)` junction table. Neither is always right:

| | `text[]` column | Junction table |
|---|---|---|
| Read a product with its tags | One row, no join | Join + aggregate |
| "Products with tag X" | `tags @> '{x}'` + GIN index | Index on `product_tags(tag_id)` |
| Referential integrity (tag must exist) | None | Foreign key |
| Rename a tag everywhere | Update every product | Update one row |
| Per-link data (who tagged, when) | Impossible | Extra columns |
| Many elements (thousands) / frequent single-element changes | Poor: every change rewrites the array and the row | Good |
| Concurrency: two users add tags to the same product | They update the same row (one waits) | Independent inserts |

Use an **array** for a small, bounded list of simple values that belongs to the row and is read with it: tags, a handful of phone numbers, the `TG_ARGV` of a trigger. Use a **junction table** as soon as the elements are entities in their own right (they have attributes, they're referenced elsewhere, they're renamed), the list is unbounded, or you need a foreign key. In practice, **never store foreign keys in an array**: `ARRAY[3, 15]` as "related product ids" can't be checked, cascaded or efficiently joined in both directions.

---

## 13. Enums

An **enumerated type** is a type whose values are a fixed, ordered list of labels:

```sql
CREATE TYPE order_status AS ENUM ('pending', 'paid', 'shipped', 'delivered', 'cancelled', 'refunded');
```

After that, `order_status` is a type like any other, and Bazaar's `orders.status` column uses it. (This is the definition from the reference migration, so it already exists in your database. Section 18 shows how the column was converted.)

### 13.1 How enums behave

- **Only listed labels are accepted**, case-sensitively:

  ```sql
  SELECT 'Shipped'::order_status;
  ```

  ```text
  ERROR:  invalid input value for enum order_status: "Shipped"
  ```

- **Storage is 4 bytes**, whatever the label length. The label text lives once, in the catalog table `pg_enum`, and each row stores the label's OID. (`pg_column_size('delivered'::order_status)` is 4, against 13 for the text `'delivered'`.)
- **Ordering follows the declaration**, not the alphabet. That gives a natural lifecycle order in reports:

  ```sql
  SELECT status, count(*) FROM orders GROUP BY status ORDER BY status;
  ```

  ```text
    status   | count
  -----------+-------
   pending   |     5
   paid      |     6
   shipped   |     4
   delivered |    91
   cancelled |    10
   refunded  |     4
  (6 rows)
  ```

  `min()`, `max()`, `<` and `BETWEEN` all work with this order, and `enum_range(NULL::order_status)`, `enum_first(...)` and `enum_last(...)` list the labels. `enum_range('paid'::order_status, 'delivered'::order_status)` returns `{paid,shipped,delivered}`.

> **Pitfall: an enum's order is a list, not a state machine.** `status >= 'shipped'` *looks* like "has shipped", but `cancelled` and `refunded` are declared after `delivered`, so they match too:
>
> ```sql
> SELECT count(*) FROM orders WHERE status >= 'shipped';             -- 109
> SELECT count(*) FROM orders WHERE status IN ('shipped', 'delivered'); -- 95
> ```
>
> Use range comparisons only on enums whose order really is linear (`low < medium < high`), and list the states explicitly otherwise.

- **Enums are strictly typed.** `status = 'shipped'` works because the untyped literal is converted to `order_status`. But a value already typed as `text` isn't converted automatically, and text operators such as `LIKE` don't exist for enums:

  ```sql
  SELECT status FROM orders WHERE status = 'shipped'::text;
  ```

  ```text
  ERROR:  operator does not exist: order_status = text
  ```

  Cast explicitly when needed: `status::text LIKE 'p%'`, or `$1::order_status` for a parameter. Some ORMs and drivers send parameters as `text` or `varchar` and hit this error. Part IV comes back to mapping enums from Python.

### 13.2 Changing an enum: easy to add, hard to remove

**Adding** a label is a catalog change with no table rewrite:

```sql
ALTER TYPE order_status ADD VALUE 'on_hold' AFTER 'paid';
```

`BEFORE`/`AFTER` choose its position in the order. If you run this, run `./project/bin/reset.sh 09` afterwards: as you'll see in a moment, there's no simple way to take a label back out. To make this possible without renumbering, `pg_enum.enumsortorder` is a floating-point number, and the new label slots in between its neighbours:

```text
 enumsortorder | enumlabel
---------------+-----------
             1 | pending
             2 | paid
           2.5 | on_hold
             3 | shipped
...
```

The caveats:

- **A new value can't be used in the transaction that added it.** Since PostgreSQL 12, `ADD VALUE` may run inside a transaction block (before that it couldn't), but using the label before the commit fails:

  ```text
  BEGIN;
  ALTER TYPE order_status ADD VALUE 'on_hold' AFTER 'paid';
  SELECT 'on_hold'::order_status;
  ERROR:  unsafe use of new value "on_hold" of enum type order_status
  HINT:  New enum values must be committed before they can be used.
  ```

  Migration tools that wrap every migration in one transaction need the `ADD VALUE` in a migration of its own, before the one that uses it (module 26 deals with migrations in Alembic).
- **`ADD VALUE` fails if the label exists.** Use `ADD VALUE IF NOT EXISTS` for idempotent scripts.
- **`RENAME VALUE`** works (PostgreSQL 10+): `ALTER TYPE order_status RENAME VALUE 'on_hold' TO 'held'`. Every row using it shows the new name instantly, since rows store the OID, not the text.
- **There is no `DROP VALUE`.** (`ALTER TYPE ... DROP VALUE` fails with "dropping an enum value is not implemented".) A label might still be stored in some row, in an index page or in a table's history, so PostgreSQL won't remove it. To remove a value you must build a new type and convert every column that uses the old one. That rewrites those tables, and it's one of the exercises.

### 13.3 Enum, `CHECK` or lookup table?

Three ways to restrict a column to a list of values:

| | `text` + `CHECK (status IN (...))` | Enum | Lookup table + foreign key |
|---|---|---|---|
| Storage per row | Label length + 1 byte | 4 bytes | Size of the key (often `text` or `smallint`) |
| Sort order | Alphabetical | Declaration order | Whatever column you add (`sort_order`) |
| Add a value | Replace the CHECK (scans the table) | `ADD VALUE` (instant) | `INSERT` |
| Remove a value | Replace the CHECK | New type, rewrite tables | `DELETE` (FK refuses if used) |
| Extra data per value (label, colour, "is final?") | No | No | Yes, columns |
| Reusable across tables | Repeat the CHECK | One type, many columns | One table, many FKs |
| Values editable by non-developers | No | No | Yes |

The rule of thumb: an **enum** for a small, stable set of values that code branches on (statuses, which change only together with the code that handles them). A **lookup table** when the list is data that changes at runtime, or when values need attributes of their own (countries, currencies, product conditions). **`CHECK`** when you want maximum flexibility and the ordering doesn't matter. It's also the easiest to migrate away from. Bazaar uses enums for its three status columns. They change only with a code release, and the lifecycle order makes reports read naturally.

---

## 14. Domains

A **domain** is a base type with constraints, and optionally a default, under a new name. It lets you define a rule once and reuse it on every column that means the same thing:

```sql
CREATE DOMAIN email_address AS text
    CONSTRAINT email_address_format CHECK (VALUE ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$');
```

`VALUE` stands for the value being checked. (This domain is part of the reference migration, so it already exists.) `users.email` is now an `email_address`, and anything assigned to it is checked:

```sql
UPDATE users SET email = 'nobody@nowhere' WHERE id = 7;
```

```text
ERROR:  value for domain email_address violates check constraint "email_address_format"
```

The regular expression is deliberately loose (something `@` something `.` something). Fully validating e-mail syntax with a regex is famously hopeless, and the only real validation is sending a confirmation mail. The domain catches typos, swapped columns and empty strings, which is what database constraints are for.

A second example, money that must be positive. This one is new, so create it to follow along:

```sql
CREATE DOMAIN positive_money AS numeric(12,2)
    CONSTRAINT positive_money_check CHECK (VALUE > 0);
```

### 14.1 How domain constraints are enforced

Domain checks run **whenever a value is converted to the domain type**: on `INSERT` and `UPDATE` of a domain column, on an explicit cast, and when passing an argument to a function parameter of the domain type. The base type's rules apply first, so `19.999::positive_money` is rounded to `20.00` by `numeric(12,2)` and then checked:

```sql
SELECT 19.999::positive_money AS rounded;   -- 20.00
SELECT 0::positive_money;
```

```text
ERROR:  value for domain positive_money violates check constraint "positive_money_check"
```

The details matter:

- **Arithmetic returns the base type.** `amount - 30` on a `positive_money` column is plain `numeric`. The check happens only when the result is stored back:

  ```sql
  CREATE TEMP TABLE gift_cards (id int, amount positive_money NOT NULL);
  INSERT INTO gift_cards VALUES (1, 25);
  UPDATE gift_cards SET amount = amount - 30;
  ```

  ```text
  ERROR:  value for domain positive_money violates check constraint "positive_money_check"
  ```

- **NULL passes a domain `CHECK`**, as with any `CHECK`: `SELECT NULL::positive_money` is fine. Put `NOT NULL` on the *column*, as above. You can also declare a domain `NOT NULL`, but the documentation advises against it: an outer join or a scalar subquery that finds no row still produces a NULL "of the domain type", so the guarantee isn't watertight.
- **`ALTER DOMAIN ... ADD CONSTRAINT` checks every column that uses the domain**, across all tables, and fails if any stored value violates it. Add `NOT VALID` to skip the existing data (only new values are checked), then `ALTER DOMAIN ... VALIDATE CONSTRAINT` once you've fixed the old rows.
- **Converting a column to a domain with constraints rewrites the table** (section 18 shows this happening to `users`), even though the underlying type is the same.

### 14.2 Domain or `CHECK` on the column?

A domain wins when the *same concept* appears in several places: e-mail addresses in `users`, `sellers.contact_email` and `newsletter_signups`; country codes in `sellers` and `addresses`; prices in `products`, `order_items` and `product_price_history`. One definition, one name in `\d` output, and one place to change the rule. A plain column `CHECK` wins for one-off rules, and for rules that involve several columns (`reserved <= quantity`), which a domain can't express because it sees only one value. Domains also show up in function signatures (module 09), where they validate arguments for free.

> **Under the hood:** clients see a domain column as its base type. `pg_typeof` says `email_address`, but over the wire it's `text`, so drivers and ORMs need no special support.

---

## 15. Composite types

A **composite type** is a record: an ordered list of named, typed fields. You've been using them all along. **Every table defines a composite type of the same name**, with one field per column, and a table alias in a query refers to the *whole row* as a value of that type:

```sql
SELECT s FROM sellers s WHERE id = 3;
```

```text
                        s
-------------------------------------------------
 (3,3,"Casa Cucina",IT,"2023-04-12 14:00:00+00")
(1 row)
```

That parenthesised text is the composite literal format. `to_jsonb(s)` from section 3 and `jsonb_populate_record(NULL::promotions, ...)` from section 6 were working with these row values.

To access a field of a composite *value*, wrap it in parentheses: `(s).shop_name`. Without parentheses, `s.shop_name` would be read as "column `shop_name` of table `s`". That happens to work here, but for a composite *column* it doesn't:

```sql
SELECT (s).shop_name, (s).country_code FROM sellers s WHERE id = 3;   -- Casa Cucina | IT
```

`ROW(...)` (or just parentheses with a comma) builds an anonymous record. Records compare field by field, left to right, which is what powers the row-value comparisons used for keyset pagination in module 12:

```sql
SELECT ROW(1, 'x', NULL) AS anon, ROW(1, 2) < ROW(1, 3) AS row_cmp, (1, 'a') = (1, 'a') AS eq;
```

```text
  anon  | row_cmp | eq
--------+---------+----
 (1,x,) | t       | t
(1 row)
```

You can also declare a standalone composite type and use it as a column type:

```sql
CREATE TYPE money_amount AS (amount numeric(12,2), currency char(3));

CREATE TEMP TABLE quotes (id int, price money_amount);
INSERT INTO quotes VALUES (1, ROW(10, 'USD')), (2, '(12.50,EUR)');
UPDATE quotes SET price.amount = 11 WHERE id = 1;   -- assign one field: no parentheses here
SELECT id, (price).amount, (price).currency FROM quotes ORDER BY id;
```

```text
 id | amount | currency
----+--------+----------
  1 |  11.00 | USD
  2 |  12.50 | EUR
(2 rows)
```

`(price).*` expands all fields into columns. Composite *columns* are rare in practice. You can't put a foreign key or a `NOT NULL` on a field, and most drivers return them as a string you have to parse. Their real home is **functions**: a function that returns a composite type, or `SETOF` one, returns rows with named columns, and `RETURNS TABLE (...)` in module 09 is shorthand for exactly that.

> **Pitfall: `IS NULL` on a row means "all fields are NULL".** `ROW(NULL, 1) IS NULL` is false, and so is `ROW(NULL, 1) IS NOT NULL`. A row is `IS NOT NULL` only when *every* field is non-null. When you test "did the LEFT JOIN find a row?", test the primary key column (`s.id IS NULL`), not the row (`s IS NULL`).

---

## 16. Range types and multiranges

Bazaar's promotions run *from* a start time *until* an end time, sometimes open-ended. Two columns, `starts_at` and `ends_at`, work, but every query then has to spell out the interval logic: does the end include the last instant? Does NULL mean "forever"? Is `starts_at <= ends_at` enforced? Overlap tests become four-way comparisons that are easy to get subtly wrong. A **range type** stores the interval as one value, with its boundary semantics built in.

### 16.1 Built-in range types and bounds

| Range type | Element type | Discrete? |
|---|---|---|
| `int4range`, `int8range` | `int`, `bigint` | Yes |
| `numrange` | `numeric` | No |
| `daterange` | `date` | Yes |
| `tsrange`, `tstzrange` | `timestamp`, `timestamptz` | No |

Each bound is **inclusive** `[ ]` or **exclusive** `( )`. The constructor functions take an optional third argument with the bound types, defaulting to `'[)'`: include the lower bound, exclude the upper one. **Discrete** types are normalised to the `[)` form, so equal ranges always look the same:

```sql
SELECT int4range(1, 5)                                  AS default_bounds,
       int4range(1, 5, '[]')                            AS closed,
       numrange(1.5, 2.5, '(]')                         AS num,
       daterange('2025-06-01', '2025-06-30', '[]')      AS d;
```

```text
 default_bounds | closed |    num    |            d
----------------+--------+-----------+-------------------------
 [1,5)          | [1,6)  | (1.5,2.5] | [2025-06-01,2025-07-01)
(1 row)
```

**Why half-open `[)` is the right default.** Consecutive periods share a boundary without overlapping or leaving a gap: `[June 1, June 15)` and `[June 15, July 1)` cover every instant exactly once. With closed ranges you'd need an "end" of `June 14 23:59:59.999999`, which depends on the timestamp precision and breaks on time-zone conversions. Every promotion in Bazaar is `[start, end)`.

Omitting a bound makes it **infinite** (unbounded): `'[2025-06-20,)'` means "from June 20, no end", and `upper()` returns NULL for it. `isempty()` detects the **empty range** (`int4range(5,5)` is `empty`), and constructing a range with lower > upper is an error:

```sql
SELECT lower(int4range(1,5)), upper(int4range(1,5)), upper_inc(int4range(1,5)),
       isempty(int4range(5,5)), upper_inf('[2025-06-20,)'::tstzrange);
```

```text
 lower | upper | upper_inc | isempty | upper_inf
-------+-------+-----------+---------+-----------
     1 |     5 | f         | t       | t
(1 row)
```

The promotions:

```sql
SELECT id, product_id, discount_pct, active_during FROM promotions ORDER BY id;
```

```text
 id | product_id | discount_pct |                    active_during
----+------------+--------------+-----------------------------------------------------
  1 |          1 |        10.00 | ["2024-11-25 00:00:00+00","2024-12-02 00:00:00+00")
  2 |         10 |        15.00 | ["2024-11-25 00:00:00+00","2024-12-02 00:00:00+00")
  3 |         10 |        20.00 | ["2025-06-01 00:00:00+00","2025-06-15 00:00:00+00")
  4 |          7 |         5.00 | ["2025-03-01 00:00:00+00","2025-04-01 00:00:00+00")
  5 |         34 |        25.00 | ["2024-07-01 00:00:00+00","2024-07-08 00:00:00+00")
  6 |         47 |        30.00 | ["2025-06-20 00:00:00+00",)
  7 |         48 |        30.00 | ["2025-06-20 00:00:00+00",)
  8 |         24 |        50.00 | ["2025-01-01 00:00:00+00","2025-01-08 00:00:00+00")
  9 |         37 |        12.50 | ["2025-05-01 00:00:00+00","2025-07-01 00:00:00+00")
(9 rows)
```

> **Pitfall: `tstzrange` literals use your session's time zone.** `tstzrange('2024-11-01', '2024-12-01')` means midnight *in the `TimeZone` setting*. The course container runs in UTC. For an application serving several time zones, write explicit offsets (`'2024-11-01 00:00+00'`) or pass `timestamptz` parameters.

### 16.2 Range operators

| Operator | Meaning | Example (true) |
|---|---|---|
| `@>` | contains (element or range) | `int4range(1,5) @> 3` |
| `<@` | is contained by | `3 <@ int4range(1,5)` |
| `&&` | overlaps (shares at least one point) | `int4range(1,5) && int4range(4,8)` |
| `-\|-` | is adjacent (touch without overlapping) | `int4range(1,5) -\|- int4range(5,8)` |
| `<<`, `>>` | strictly left / right of | `int4range(1,5) << int4range(7,8)` |
| `&<`, `&>` | does not extend to the right / left of | `int4range(1,5) &< int4range(3,8)` |
| `*`, `+`, `-` | intersection, union, difference | `int4range(1,5) * int4range(3,8)` = `[3,5)` |

```sql
SELECT int4range(1,5) @> 5                AS contains_upper,
       int4range(1,5) && int4range(5,8)   AS overlaps_touching,
       int4range(1,5) -|- int4range(5,8)  AS adjacent;
```

```text
 contains_upper | overlaps_touching | adjacent
----------------+-------------------+----------
 f              | f                 | t
(1 row)
```

That is the half-open design at work: `[1,5)` and `[5,8)` touch but don't overlap. Applied to promotions, "which promotions are running at 12:00 UTC on 30 June 2025?" and "which ones touched November 2024?" become one operator each:

```sql
SELECT id, product_id, discount_pct
FROM promotions
WHERE active_during @> timestamptz '2025-06-30 12:00+00'
ORDER BY id;
```

```text
 id | product_id | discount_pct
----+------------+--------------
  6 |         47 |        30.00
  7 |         48 |        30.00
  9 |         37 |        12.50
(3 rows)
```

```sql
SELECT id, product_id
FROM promotions
WHERE active_during && tstzrange('2024-11-01 00:00+00', '2024-12-01 00:00+00')
ORDER BY id;   -- 1 and 2
```

The open-ended promotions 6 and 7 were found without any special handling of their missing end. Union and difference raise an error if the result would not be one contiguous range (`int4range(1,8) - int4range(3,5)` would leave two pieces). That's what multiranges are for.

### 16.3 Multiranges (PostgreSQL 14+)

A **multirange** is an ordered set of non-overlapping ranges. Every range type has one: `int4multirange`, `tstzmultirange`, `datemultirange` and so on. Overlapping or adjacent input ranges are merged automatically, and the difference that failed above now works:

```sql
SELECT '{[1,5), [3,9), [20,30)}'::int4multirange                        AS normalized,
       int4range(1,8)::int4multirange - int4multirange(int4range(3,5)) AS diff;
```

```text
   normalized    |     diff
-----------------+---------------
 {[1,9),[20,30)} | {[1,3),[5,8)}
(1 row)
```

The aggregate `range_agg(range)` collects ranges into a multirange (merging as it goes), `range_intersect_agg` intersects them, and `unnest(multirange)` turns one back into rows. All periods during which product 10 was on promotion:

```sql
SELECT range_agg(active_during) FROM promotions WHERE product_id = 10;
```

```text
                                                 range_agg
-----------------------------------------------------------------------------------------------------------
 {["2024-11-25 00:00:00+00","2024-12-02 00:00:00+00"),["2025-06-01 00:00:00+00","2025-06-15 00:00:00+00")}
(1 row)
```

Multiranges answer "when was X *not* happening?" questions: take the period of interest as a multirange and subtract the `range_agg` of the activity. The exercises use this to find the gaps between promotions.

### 16.4 Generating rows from a range

Reports often need one row per day of a range: a calendar, a daily chart. `generate_series` (module 05) over the bounds does it. Remember that the upper bound is exclusive:

```sql
SELECT d::date AS day
FROM promotions p,
     generate_series(lower(p.active_during), upper(p.active_during) - interval '1 day', interval '1 day') AS d
WHERE p.id = 1;
```

```text
    day
------------
 2024-11-25
 2024-11-26
 2024-11-27
 2024-11-28
 2024-11-29
 2024-11-30
 2024-12-01
(7 rows)
```

For an open-ended range, `upper()` is NULL and the series is empty, so substitute a horizon such as "today": `coalesce(upper(p.active_during), timestamptz '2025-06-30')`.

> **PG 18:** PostgreSQL 18 adds *temporal* constraints built on ranges: `PRIMARY KEY (product_id, active_during WITHOUT OVERLAPS)` and foreign keys with `PERIOD`. On PostgreSQL 17, the exclusion constraint in the next section is how you express "no overlaps".

---

## 17. Exclusion constraints

A `UNIQUE` constraint says: no two rows may have *equal* values in these columns. An **exclusion constraint** generalises this: no two rows may have values for which *all* of the given operators return true. With `=` everywhere, it's just `UNIQUE`. With `&&` on a range, it means "no overlaps". Bazaar's rule "a product never has two overlapping promotions" becomes (this is the table from the reference migration, already in your database):

```sql
CREATE TABLE promotions (
    id             bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id     bigint       NOT NULL REFERENCES products (id) ON DELETE CASCADE,
    discount_pct   numeric(5,2) NOT NULL,
    active_during  tstzrange    NOT NULL,
    created_at     timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT promotions_discount_pct_range CHECK (discount_pct > 0 AND discount_pct < 100),
    CONSTRAINT promotions_range_not_empty CHECK (NOT isempty(active_during)),
    CONSTRAINT promotions_no_overlap
        EXCLUDE USING gist (product_id WITH =, active_during WITH &&)
);
```

Read the constraint as: "reject a new row if some existing row has `product_id =` the new one's **and** `active_during &&` the new one's". Product 10 already has a promotion from June 1 to June 15, 2025:

```sql
INSERT INTO promotions (product_id, discount_pct, active_during)
VALUES (10, 10, '[2025-06-10, 2025-06-20)');
```

```text
ERROR:  conflicting key value violates exclusion constraint "promotions_no_overlap"
DETAIL:  Key (product_id, active_during)=(10, ["2025-06-10 00:00:00+00","2025-06-20 00:00:00+00")) conflicts with existing key (product_id, active_during)=(10, ["2025-06-01 00:00:00+00","2025-06-15 00:00:00+00")).
```

A promotion that *starts* at the instant the previous one ends is fine, because `[..., June 15)` and `[June 15, ...)` don't overlap. The open-ended promotion of product 47 blocks every later one until somebody sets its end:

```sql
BEGIN;
INSERT INTO promotions (product_id, discount_pct, active_during)
VALUES (10, 10, '[2025-06-15, 2025-06-22)');   -- OK: adjacent, not overlapping
ROLLBACK;

INSERT INTO promotions (product_id, discount_pct, active_during)
VALUES (47, 10, '[2025-07-01, 2025-07-08)');   -- ERROR: conflicts with ["2025-06-20 00:00:00+00",)
```

### 17.1 How it works, and why it needs `btree_gist`

An exclusion constraint is enforced with an **index**, exactly like a `UNIQUE` constraint. On every insert or update, PostgreSQL searches the index for rows that conflict. The index must support every operator in the constraint, and that rules out B-tree, which can't answer `&&`. GiST (Generalised Search Tree) indexes support the range operators. But out of the box, GiST doesn't support plain `=` on a `bigint`:

```sql
-- in a database without btree_gist
CREATE TEMP TABLE promo_test (
    product_id bigint, active_during tstzrange,
    EXCLUDE USING gist (product_id WITH =, active_during WITH &&));
```

```text
ERROR:  data type bigint has no default operator class for access method "gist"
HINT:  You must specify an operator class for the index or define a default operator class for the data type.
```

The `btree_gist` extension, which ships with PostgreSQL in the contrib package, adds GiST operator classes for the ordinary scalar types. The migration installs it first:

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA public;
```

Extensions are installed per database, by a sufficiently privileged role (module 15). `WITH SCHEMA public` keeps the extension's objects out of the `bazaar` schema.

Things to know about exclusion constraints:

- **Concurrency-safe.** Two transactions inserting overlapping promotions at the same time can't both succeed. The second waits for the first and then fails, exactly as with `UNIQUE`. A "check for overlaps with a `SELECT`, then insert" approach in application code has a race condition. Module 10 explains why.
- **`ON CONFLICT DO NOTHING`** works with exclusion constraints, but `ON CONFLICT DO UPDATE` doesn't (it needs a unique index to identify the one conflicting row).
- **Partial exclusion:** add `WHERE (...)` to apply the rule only to some rows, for example only to promotions that aren't cancelled.
- **Deferrable:** `DEFERRABLE INITIALLY DEFERRED` postpones the check to commit time, which lets you shift two adjacent periods within one transaction even if an intermediate state overlaps.
- **Empty ranges never overlap anything**, so an empty range would slip past the constraint. Hence `promotions_range_not_empty`.
- **Failed inserts consume identity values.** Sequences are not transactional. After the two failed inserts above, the next successful promotion will not get id 10. Gaps in ids are normal, so never rely on ids being contiguous.

Exclusion constraints are the standard tool for scheduling-style rules: a meeting room can't be double-booked, a seller can't have two "store closed" periods that overlap, an employee can't have two overlapping contracts.

---

## 18. Putting it together: the v3 migration

The reference migration `project/db/0801_schema_types.sql` turns schema v2 into v3. You'll write your own in the project milestone, so here are the decisions and the traps, in the order the file handles them. The whole migration runs in one transaction (`BEGIN ... COMMIT`), because DDL in PostgreSQL is transactional: if any step fails, nothing has changed.

### 18.1 Converting `text` + `CHECK` columns to enums

Since module 02, `orders.status` has been `text NOT NULL DEFAULT 'pending'` with `CHECK (status IN (...))`. Converting it to `order_status` is a single `ALTER COLUMN ... TYPE`, but running it naively hits three errors in turn. They're worth seeing, because the same pattern applies to any type change. From `./project/bin/reset.sh 08`, after `CREATE TYPE order_status ...`:

```sql
ALTER TABLE orders ALTER COLUMN status TYPE order_status;
```

```text
ERROR:  column "status" cannot be cast automatically to type order_status
HINT:  You might need to specify "USING status::order_status".
```

There's no *implicit* cast from `text` to an enum. `USING expr` tells PostgreSQL how to compute each new value from the old row: here, an explicit cast. `USING` can be any expression, such as `USING lower(status)::order_status` or a `CASE` that maps old codes to new labels.

```sql
ALTER TABLE orders ALTER COLUMN status TYPE order_status USING status::order_status;
```

```text
ERROR:  default for column "status" cannot be cast automatically to type order_status
```

The `USING` clause applies to the data, not to the column's **default** (`'pending'::text`), which PostgreSQL also has to convert. Drop the default first and set it again afterwards. With the default gone:

```text
ERROR:  operator does not exist: order_status = text
HINT:  No operator matches the given name and argument types. You might need to add explicit type casts.
```

This one comes from the **`CHECK` constraint**. It was stored as `status = ANY (ARRAY['pending'::text, ...])`, and after the type change it would compare an enum with text. The enum makes the `CHECK` redundant anyway, so drop it. The working sequence, as in the reference file:

```sql
CREATE TYPE order_status AS ENUM ('pending', 'paid', 'shipped', 'delivered', 'cancelled', 'refunded');

ALTER TABLE orders DROP CONSTRAINT orders_status_check;
ALTER TABLE orders ALTER COLUMN status DROP DEFAULT;
ALTER TABLE orders ALTER COLUMN status TYPE order_status USING status::order_status;
ALTER TABLE orders ALTER COLUMN status SET DEFAULT 'pending';
```

The same applies to `products.status` (default `'draft'`) and `payments.status` (no default, so only the `CHECK` is dropped). If any row held a value that isn't a label of the enum, the `USING` cast would fail and the whole migration would roll back. That's a feature: run a `SELECT DISTINCT status` first if you're unsure about your data.

**What it costs.** Changing a column's type to a different storage format **rewrites the whole table** and rebuilds its indexes, while holding an `ACCESS EXCLUSIVE` lock that blocks even reads. You can see the rewrite in the table's file number, `pg_class.relfilenode`, which changes:

```text
before:  orders 16475   users 16387   products 16435
after:   orders 16657   users 16667   products 16435
```

`orders` was rewritten (enum conversion). So was `users`, because converting `email` to the `email_address` domain rewrites it: the domain has a constraint. `products` wasn't rewritten, even though it gained two columns: `ADD COLUMN` with a constant default only records the default in the catalog. On Bazaar's small tables, all of this takes milliseconds. On a 500-million-row `orders` table it could take an hour of downtime. Module 26 shows the zero-downtime alternative: add a new column, backfill it in batches, switch over, drop the old one.

### 18.2 The domain and the case-insensitive e-mail index

```sql
CREATE DOMAIN email_address AS text
    CONSTRAINT email_address_format CHECK (VALUE ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$');
ALTER TABLE users ALTER COLUMN email TYPE email_address;

ALTER TABLE users DROP CONSTRAINT users_email_key;
CREATE UNIQUE INDEX users_email_lower_key ON users (lower(email));
```

The domain conversion re-checks every existing e-mail, so a single bad address in the data would abort the migration. The second change fixes a bug that has existed since module 02. User 17 is `Zoe.Martin@Example.COM`, and the old case-sensitive `UNIQUE` would happily accept `zoe.martin@example.com` as a *different* account. A unique **expression index** on `lower(email)` makes uniqueness case-insensitive. The consequences for every later module: look users up with `WHERE lower(email) = lower($1)` (so that the index can be used), and write upserts as `ON CONFLICT (lower(email))`. Module 11 explains expression indexes. (Alternatives are the `citext` extension, a case-insensitive text type, or a nondeterministic collation. The expression index is the simplest and the most common.)

### 18.3 Attributes, tags and promotions

```sql
ALTER TABLE products
    ADD COLUMN attributes jsonb  NOT NULL DEFAULT '{}',
    ADD COLUMN tags       text[] NOT NULL DEFAULT '{}',
    ADD CONSTRAINT products_attributes_is_object CHECK (jsonb_typeof(attributes) = 'object');
```

Both columns are `NOT NULL` with an "empty" default, so no query ever has to distinguish "no attributes" from "unknown attributes", and `attributes -> 'x'` never has a NULL document to deal with. Then come `btree_gist` and the `promotions` table from section 17.

The data lives in a separate file, `0802_seed_types.sql`, which fills `attributes` and `tags` with one `UPDATE` per product and inserts the nine promotions. Keeping schema and seed apart is a habit worth adopting: the schema migration must run in every environment, while seed data belongs to development and test databases only.

---

## Summary

- **`jsonb`** parses JSON once into a binary form: keys sorted, duplicates removed, numbers as `numeric`. It supports equality, containment and GIN indexes. Use `json` only to preserve input byte for byte.
- Build JSON with `jsonb_build_object`, `to_jsonb(row)`, `jsonb_agg` and `jsonb_object_agg` (wrap aggregates in `coalesce(..., '[]')`). Read it with `->`/`#>` (jsonb) and `->>`/`#>>` (text), and **cast before comparing** numbers.
- `@>` tests containment (equality only; GIN-indexable), and `?`, `?|`, `?&` test key existence. `||` merges shallowly, `-` and `#-` delete, `jsonb_set` replaces (and returns NULL if given a NULL), and subscript assignment creates missing parents.
- `jsonb_each`, `jsonb_array_elements ... WITH ORDINALITY`, `jsonb_to_recordset` and PostgreSQL 17's **`JSON_TABLE`** turn documents into rows.
- **jsonpath** adds filters (`? (@ >= 16)`), variables and item methods. Lax mode forgives missing keys and unwraps arrays; strict mode doesn't. `JSON_VALUE`/`JSON_QUERY` return NULL on error unless you say `ERROR ON ERROR`.
- Validate documents with `CHECK` constraints (`jsonb_typeof`, `?&`, `CASE` for safe casts). Index them with GIN for containment and with expression indexes for hot keys. Promote keys you join on, constrain or aggregate to real columns.
- **Arrays** are 1-based, return NULL out of range, and suit small lists that belong to the row. `@>`/`&&` are indexable and `ANY` isn't. Never store foreign keys in an array.
- **Enums** are 4 bytes and ordered by declaration. Adding a label is instant (but it can't be used in the same transaction); removing one needs a new type and a table rewrite. Order comparisons on non-linear lifecycles are a trap.
- **Domains** name a type plus constraints. They're checked whenever a value is converted to the domain, and NULL passes.
- **Composite types** are records. Every table has one, and `(value).field` reads a field. Their main use is function results.
- **Ranges** (`[)` by default) and **multiranges** model intervals, with `@>`, `&&`, `-|-`, `range_agg` and set operations. **Exclusion constraints** (`EXCLUDE USING gist (product_id WITH =, active_during WITH &&)`, with `btree_gist`) make "no overlaps" a race-free database rule.
- Changing a column's type needs `USING`, and the old default and `CHECK` must go first. It rewrites the table under an exclusive lock.

## What's next

You now have richer data in Bazaar's tables. In [module 09](../09-views-functions-triggers/) you put *logic* next to it: views and materialized views, SQL and PL/pgSQL functions (starting with the effective price of a product under its promotions), procedures, and triggers that keep `updated_at`, an audit log and `orders.total` correct automatically.
