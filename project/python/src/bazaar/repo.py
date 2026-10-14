"""The synchronous data-access layer: plain SQL over psycopg 3.

Conventions (the async twin, bazaar.aio.repo, follows them too):

* Every function takes a connection as its first argument and never commits.
  The caller owns the transaction: a web request, a script, or a test that
  rolls everything back. Functions that must be atomic on their own
  (place_order, import_products) open a `conn.transaction()` block, which is
  a real transaction on an idle autocommit connection and a SAVEPOINT inside
  an outer transaction, so they compose.
* Values are always passed as parameters. Identifiers and optional clauses
  are composed with psycopg.sql, never with f-strings.
* Database errors that carry business meaning are translated into the
  exceptions of bazaar.errors.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal

import psycopg
from psycopg import sql
from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from bazaar.errors import translate
from bazaar.models import (
    ImportResult,
    NewProduct,
    OrderPage,
    OrderSummary,
    Product,
    ProductHit,
    decode_cursor,
    encode_cursor,
)

# --- SQL ---------------------------------------------------------------------
# Kept as module constants so bazaar.aio.repo runs exactly the same statements.

GET_PRODUCT = """
    SELECT p.id, p.seller_id, p.category_id, p.sku, p.title, p.description,
           p.price, p.status, p.attributes, p.tags, p.created_at,
           i.quantity - i.reserved AS available
    FROM products p
    LEFT JOIN inventory i ON i.product_id = p.id
    WHERE p.id = %(id)s
"""

SEARCH_BASE = """
    SELECT p.id, p.title, p.price, ts_rank(p.search_vector, q)::float8 AS rank
    FROM products p, websearch_to_tsquery('english', %(q)s) AS q
    WHERE p.search_vector @@ q
      AND p.status = 'active'
"""

LIST_ORDERS_BASE = """
    SELECT o.id, o.seller_id, o.status, o.total, o.created_at,
           (SELECT count(*) FROM order_items oi WHERE oi.order_id = o.id)::int AS item_count
    FROM orders o
    WHERE o.user_id = %(user_id)s
"""

PLACE_ORDER = "SELECT place_order(%(user_id)s, %(items)s, %(address_id)s)"

IMPORT_STAGING = """
    CREATE TEMP TABLE import_products (
        seller_id   bigint,
        category_id int,
        sku         text,
        title       text,
        description text,
        price       numeric(12,2),
        status      text,
        attributes  jsonb,
        tags        text[],
        quantity    int
    ) ON COMMIT DROP
"""
IMPORT_COPY = (
    "COPY import_products (seller_id, category_id, sku, title, description, price,"
    " status, attributes, tags, quantity) FROM STDIN"
)
IMPORT_COPY_TYPES = [
    "int8", "int4", "text", "text", "text", "numeric", "text", "jsonb", "text[]", "int4",
]  # fmt: skip
IMPORT_UPSERT = """
    INSERT INTO products AS p
        (seller_id, category_id, sku, title, description, price, status, attributes, tags)
    SELECT seller_id, category_id, sku, title, description, price,
           status::product_status, attributes, tags
    FROM import_products
    ON CONFLICT (seller_id, sku) DO UPDATE
    SET category_id = EXCLUDED.category_id,
        title       = EXCLUDED.title,
        description = EXCLUDED.description,
        price       = EXCLUDED.price,
        status      = EXCLUDED.status,
        attributes  = EXCLUDED.attributes,
        tags        = EXCLUDED.tags
    RETURNING (xmax = 0) AS inserted
"""
IMPORT_INVENTORY = """
    INSERT INTO inventory (product_id, quantity)
    SELECT p.id, s.quantity
    FROM import_products s
    JOIN products p ON p.seller_id = s.seller_id AND p.sku = s.sku
    ON CONFLICT (product_id) DO UPDATE SET quantity = EXCLUDED.quantity
"""


# --- helpers shared with the async layer ---------------------------------------


def search_query(
    category_id: int | None, max_price: Decimal | None
) -> tuple[sql.Composed, dict[str, object]]:
    """Build the search statement with only the filters that were given.

    Composing the optional clauses (instead of `(%(cat)s IS NULL OR ...)`) keeps
    each variant a simple statement the planner can optimise on its own.
    """
    clauses: list[sql.Composable] = [sql.SQL(SEARCH_BASE)]  # type: ignore[arg-type]
    params: dict[str, object] = {}
    if category_id is not None:
        clauses.append(sql.SQL("AND p.category_id = %(category_id)s"))
        params["category_id"] = category_id
    if max_price is not None:
        clauses.append(sql.SQL("AND p.price <= %(max_price)s"))
        params["max_price"] = max_price
    clauses.append(sql.SQL("ORDER BY rank DESC, p.id LIMIT %(limit)s"))
    return sql.SQL("\n").join(clauses), params


def orders_query(after: str | None) -> tuple[sql.Composed, dict[str, object]]:
    parts: list[sql.Composable] = [sql.SQL(LIST_ORDERS_BASE)]  # type: ignore[arg-type]
    params: dict[str, object] = {}
    if after is not None:
        ts, last_id = decode_cursor(after)
        # Row comparison: "strictly older than the last row we returned".
        parts.append(sql.SQL("AND (o.created_at, o.id) < (%(after_ts)s, %(after_id)s)"))
        params |= {"after_ts": ts, "after_id": last_id}
    parts.append(sql.SQL("ORDER BY o.created_at DESC, o.id DESC LIMIT %(limit_plus_one)s"))
    return sql.SQL("\n").join(parts), params


def make_page(rows: list[OrderSummary], limit: int) -> OrderPage:
    has_more = len(rows) > limit
    rows = rows[:limit]
    cursor = encode_cursor(rows[-1].created_at, rows[-1].id) if has_more and rows else None
    return OrderPage(items=rows, next_cursor=cursor)


def order_items_param(items: Sequence[tuple[int, int]]) -> Jsonb:
    return Jsonb([{"product_id": pid, "quantity": qty} for pid, qty in items])


def import_row(p: NewProduct) -> tuple[object, ...]:
    return (
        p.seller_id, p.category_id, p.sku, p.title, p.description, p.price,
        str(p.status), p.attributes, p.tags, p.quantity,
    )  # fmt: skip


# --- the repository -------------------------------------------------------------


def get_product(conn: psycopg.Connection, product_id: int) -> Product | None:
    with conn.cursor(row_factory=class_row(Product)) as cur:
        cur.execute(GET_PRODUCT, {"id": product_id})
        return cur.fetchone()


def search_products(
    conn: psycopg.Connection,
    query: str,
    *,
    limit: int = 20,
    category_id: int | None = None,
    max_price: Decimal | None = None,
) -> list[ProductHit]:
    """Full-text search over active products (module 13's search_vector).

    `query` uses web-search syntax: `wireless headphones`, `"chef knife"`,
    `lamp -desk`, `mug or cup`.
    """
    stmt, params = search_query(category_id, max_price)
    with conn.cursor(row_factory=class_row(ProductHit)) as cur:
        cur.execute(stmt, {"q": query, "limit": limit, **params})
        return cur.fetchall()


def list_orders_for_user(
    conn: psycopg.Connection, user_id: int, *, limit: int = 20, after: str | None = None
) -> OrderPage:
    """A page of a user's orders, newest first, with keyset pagination.

    Pass `page.next_cursor` back as `after` for the next page. Unlike OFFSET,
    each page costs the same however deep you go, and rows inserted meanwhile
    never shift or duplicate entries across pages.
    """
    stmt, params = orders_query(after)
    with conn.cursor(row_factory=class_row(OrderSummary)) as cur:
        cur.execute(stmt, {"user_id": user_id, "limit_plus_one": limit + 1, **params})
        return make_page(cur.fetchall(), limit)


def place_order(
    conn: psycopg.Connection,
    user_id: int,
    items: Sequence[tuple[int, int]],
    shipping_address_id: int | None = None,
) -> list[int]:
    """Check out `items` [(product_id, quantity), ...]; returns one order id per seller.

    Raises InsufficientStock, ProductUnavailable, UserInactive, AddressMismatch
    or InvalidOrder. On error nothing is changed (the savepoint/transaction is
    rolled back) and the connection remains usable.
    """
    params = {
        "user_id": user_id,
        "items": order_items_param(items),
        "address_id": shipping_address_id,
    }
    try:
        with conn.transaction():
            rows = conn.execute(PLACE_ORDER, params).fetchall()
    except psycopg.Error as e:
        raise translate(e) from e
    return [r[0] for r in rows]


def import_products(conn: psycopg.Connection, products: Iterable[NewProduct]) -> ImportResult:
    """Bulk upsert products (and their stock) keyed by (seller_id, sku).

    Rows are streamed into a temporary staging table with COPY (the fastest
    way to get many rows into Postgres), then merged with one INSERT ... ON
    CONFLICT. Atomic: either every row is imported or none.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(IMPORT_STAGING)
        with cur.copy(IMPORT_COPY) as copy:
            copy.set_types(IMPORT_COPY_TYPES)
            for p in products:
                copy.write_row(import_row(p))
        cur.execute(IMPORT_UPSERT)
        flags = [r[0] for r in cur.fetchall()]
        cur.execute(IMPORT_INVENTORY)
        # ON COMMIT DROP only fires at the *outer* commit; drop explicitly so a
        # second import inside the same outer transaction works too.
        cur.execute("DROP TABLE import_products")
    inserted = sum(flags)
    return ImportResult(inserted=inserted, updated=len(flags) - inserted)
