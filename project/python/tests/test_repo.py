"""Module 22 milestone: the synchronous repository layer (bazaar.repo)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest

from bazaar import repo
from bazaar.db import seller_scope
from bazaar.errors import (
    AddressMismatch,
    InsufficientStock,
    InvalidOrder,
    ProductUnavailable,
    UserInactive,
)
from bazaar.models import NewProduct, OrderStatus, ProductStatus

from .factories import make_address, make_product, make_seller, make_user

# --- get_product ----------------------------------------------------------------


def test_get_product_types(conn: psycopg.Connection) -> None:
    seller = make_seller(conn)
    pid = make_product(conn, seller, price=Decimal("19.99"), stock=7)
    conn.execute(
        "UPDATE products SET attributes = %s, tags = %s WHERE id = %s",
        ('{"color": "red", "sizes": [1, 2]}', ["a", "b"], pid),
    )
    p = repo.get_product(conn, pid)
    assert p is not None
    assert p.price == Decimal("19.99") and isinstance(p.price, Decimal)
    assert p.status is ProductStatus.ACTIVE
    assert p.attributes == {"color": "red", "sizes": [1, 2]}
    assert p.tags == ["a", "b"]
    assert p.created_at.tzinfo is not None
    assert p.available == 7


def test_get_product_missing(conn: psycopg.Connection) -> None:
    assert repo.get_product(conn, -1) is None


# --- search_products --------------------------------------------------------------


def test_search_products_filters(conn: psycopg.Connection) -> None:
    seller = make_seller(conn)
    cheap = make_product(conn, seller, title="Zyxquorp Lamp", price=Decimal("5.00"))
    dear = make_product(conn, seller, title="Zyxquorp Lamp Deluxe", price=Decimal("500.00"))
    make_product(conn, seller, title="Zyxquorp Draft", status="draft")
    other_cat = make_product(conn, seller, title="Zyxquorp Book", category_id=14)

    hits = repo.search_products(conn, "zyxquorp")
    assert {h.id for h in hits} == {cheap, dear, other_cat}  # drafts are never found

    hits = repo.search_products(conn, "zyxquorp", max_price=Decimal("9.99"))
    assert [h.id for h in hits] == [cheap]

    hits = repo.search_products(conn, "zyxquorp", category_id=14)
    assert [h.id for h in hits] == [other_cat]

    hits = repo.search_products(conn, "zyxquorp -deluxe")
    assert dear not in {h.id for h in hits}


def test_search_is_injection_safe(conn: psycopg.Connection) -> None:
    # Whatever the user types is a value, never SQL.
    assert repo.search_products(conn, "'; DROP TABLE products; --") == []
    assert conn.execute("SELECT to_regclass('bazaar.products')").fetchone() is not None


# --- list_orders_for_user ---------------------------------------------------------


def test_keyset_pagination(conn: psycopg.Connection) -> None:
    user = make_user(conn)
    seller = make_seller(conn)
    base = datetime(2025, 1, 1, tzinfo=UTC)
    ids = []
    # 5 orders; two share the same created_at to exercise the id tie-breaker.
    for created in [base, base + timedelta(days=1), base + timedelta(days=1), base + timedelta(3)]:
        row = conn.execute(
            "INSERT INTO orders (user_id, seller_id, created_at) VALUES (%s, %s, %s) RETURNING id",
            (user, seller, created),
        ).fetchone()
        assert row
        ids.append(row[0])
    row = conn.execute(
        "INSERT INTO orders (user_id, seller_id, created_at) VALUES (%s, %s, %s) RETURNING id",
        (user, seller, base + timedelta(days=4)),
    ).fetchone()
    assert row
    ids.append(row[0])

    seen: list[int] = []
    pages = 0
    after = None
    while True:
        page = repo.list_orders_for_user(conn, user, limit=2, after=after)
        pages += 1
        seen += [o.id for o in page.items]
        if page.next_cursor is None:
            break
        after = page.next_cursor
    assert pages == 3
    expected = [ids[4], ids[3], ids[2], ids[1], ids[0]]  # newest first, ties by id desc
    assert seen == expected
    first = repo.list_orders_for_user(conn, user, limit=10)
    assert first.items[0].status is OrderStatus.PENDING and first.next_cursor is None


# --- place_order -------------------------------------------------------------------


def test_place_order_splits_per_seller(conn: psycopg.Connection) -> None:
    buyer = make_user(conn)
    s1, s2 = make_seller(conn), make_seller(conn)
    a = make_product(conn, s1, price=Decimal("10.00"))
    b = make_product(conn, s2, price=Decimal("2.50"))
    order_ids = repo.place_order(conn, buyer, [(a, 2), (b, 4), (a, 1)])
    assert len(order_ids) == 2 and order_ids == sorted(order_ids)
    totals = conn.execute(
        "SELECT seller_id, total FROM orders WHERE id = ANY(%s) ORDER BY seller_id", (order_ids,)
    ).fetchall()
    assert totals == [(s1, Decimal("30.00")), (s2, Decimal("10.00"))]
    assert repo.get_product(conn, a).available == 7  # type: ignore[union-attr]


def test_insufficient_stock_is_translated_and_harmless(conn: psycopg.Connection) -> None:
    buyer = make_user(conn)
    seller = make_seller(conn)
    plenty = make_product(conn, seller, stock=100)
    scarce = make_product(conn, seller, stock=3)
    with pytest.raises(InsufficientStock) as exc:
        repo.place_order(conn, buyer, [(plenty, 1), (scarce, 5)])
    assert exc.value.product_id == scarce
    assert (exc.value.requested, exc.value.available) == (5, 3)
    assert isinstance(exc.value.__cause__, psycopg.Error)
    assert exc.value.__cause__.sqlstate == "BZ001"
    # The savepoint was rolled back: nothing reserved, and the connection works.
    assert repo.get_product(conn, plenty).available == 100  # type: ignore[union-attr]
    assert repo.place_order(conn, buyer, [(scarce, 3)])


@pytest.mark.parametrize(
    ("case", "exc_type"),
    [
        ("draft", ProductUnavailable),
        ("missing", ProductUnavailable),
        ("inactive_user", UserInactive),
        ("foreign_address", AddressMismatch),
        ("bad_quantity", InvalidOrder),
    ],
)
def test_place_order_error_mapping(conn: psycopg.Connection, case: str, exc_type: type) -> None:
    buyer = make_user(conn)
    seller = make_seller(conn)
    good = make_product(conn, seller)
    items = [(good, 1)]
    address = None
    if case == "draft":
        items = [(make_product(conn, seller, status="draft"), 1)]
    elif case == "missing":
        items = [(-42, 1)]
    elif case == "inactive_user":
        buyer = make_user(conn, active=False)
    elif case == "foreign_address":
        address = make_address(conn, make_user(conn))
    elif case == "bad_quantity":
        items = [(good, 0)]
    with pytest.raises(exc_type):
        repo.place_order(conn, buyer, items, address)


# --- import_products -------------------------------------------------------------------


def test_import_products_upserts(conn: psycopg.Connection) -> None:
    seller = make_seller(conn)
    rows = [
        NewProduct(
            seller_id=seller,
            category_id=3,
            sku=f"IMP-{i:05d}",
            title=f"Imported laptop {i}",
            price=Decimal("999.00") + i,
            status=ProductStatus.ACTIVE,
            attributes={"ram_gb": 16, "note": "tab\there, quote ' and \\ backslash"},
            tags=["laptop", "import"],
            quantity=i % 7,
        )
        for i in range(1000)
    ]
    result = repo.import_products(conn, rows)
    assert (result.inserted, result.updated) == (1000, 0)

    # Re-import 10 of them with a new price plus 5 new ones.
    changed = rows[:10]
    for r in changed:
        r.price = Decimal("1.00")
    new = [
        NewProduct(seller_id=seller, category_id=3, sku=f"NEW-{i}", title="New", price=Decimal(1))
        for i in range(5)
    ]
    result = repo.import_products(conn, changed + new)
    assert (result.inserted, result.updated) == (5, 10)

    stats = conn.execute(
        """
        SELECT count(*), count(*) FILTER (WHERE p.price = 1), sum(i.quantity)
        FROM products p JOIN inventory i ON i.product_id = p.id
        WHERE p.seller_id = %s
        """,
        (seller,),
    ).fetchone()
    assert stats == (1005, 15, sum(i % 7 for i in range(1000)))
    note = conn.execute(
        "SELECT attributes->>'note' FROM products WHERE seller_id = %s AND sku = 'IMP-00001'",
        (seller,),
    ).fetchone()
    assert note == ("tab\there, quote ' and \\ backslash",)


def test_import_is_atomic(conn: psycopg.Connection) -> None:
    seller = make_seller(conn)
    good = NewProduct(seller_id=seller, category_id=3, sku="OK", title="ok", price=Decimal(1))
    bad = NewProduct(seller_id=seller, category_id=3, sku="BAD", title="bad", price=Decimal(-1))
    with pytest.raises(psycopg.errors.CheckViolation):
        repo.import_products(conn, [good, bad])
    n = conn.execute("SELECT count(*) FROM products WHERE seller_id = %s", (seller,)).fetchone()
    assert n == (0,)


# --- RLS helper ---------------------------------------------------------------------


def test_seller_scope_sees_only_own_products(conn: psycopg.Connection) -> None:
    mine, theirs = make_seller(conn), make_seller(conn)
    p1 = make_product(conn, mine)
    make_product(conn, theirs)
    conn.execute("SET LOCAL ROLE bazaar_seller")  # superusers bypass RLS; become the portal role
    with seller_scope(conn, mine):
        visible = conn.execute(
            "SELECT id FROM products WHERE seller_id = ANY(%s)", ([mine, theirs],)
        ).fetchall()
    assert visible == [(p1,)]


def test_enqueue_job(conn: psycopg.Connection) -> None:
    job_id = repo.enqueue_job(conn, "noop", {"x": 1}, queue="test-sync")
    row = conn.execute("SELECT kind, payload FROM jobs WHERE id = %s", (job_id,)).fetchone()
    assert row == ("noop", {"x": 1})
