"""Module 24, section 4: what Postgres types become in Python, through Core tables."""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import Range

from bazaar.sa.engine import create_engine
from bazaar.sa.tables import events, products, promotions, users

engine = create_engine()

with engine.connect() as conn:
    row = conn.execute(
        sa.select(
            products.c.id,
            products.c.price,
            products.c.status,
            products.c.attributes,
            products.c.tags,
            products.c.created_at,
        ).where(products.c.id == 20)
    ).one()
    for key, value in row._mapping.items():
        print(f"products.{key:<11} {type(value).__name__:<14} {value!r}")

    email = conn.execute(sa.select(users.c.email).where(users.c.id == 17)).scalar_one()
    print(f"users.email       {type(email).__name__:<14} {email!r}")

    promo = conn.execute(
        sa.select(promotions.c.active_during).where(promotions.c.product_id == 20)
    ).first()
    print("promotions.active_during", repr(promo[0]) if promo else None)

    # Writing: Python values in, the dialect adapts them.
    pid = conn.execute(
        sa.insert(products)
        .values(
            seller_id=7,
            category_id=3,
            sku="M24-TYPES",
            title="Types demo",
            price="19.90",  # a str is fine for numeric; Decimal is better
            status="active",  # or ProductStatus.ACTIVE
            attributes={"ram_gb": 16, "storage": {"type": "ssd", "gb": 512}},
            tags=["demo", "m24"],
        )
        .returning(products.c.id)
    ).scalar_one()
    week = Range(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 8, tzinfo=UTC))
    conn.execute(sa.insert(promotions).values(product_id=pid, discount_pct=15, active_during=week))
    contains = conn.execute(
        sa.select(promotions.c.active_during.contains(datetime(2026, 1, 3, tzinfo=UTC))).where(
            promotions.c.product_id == pid
        )
    ).scalar_one()
    print("range contains 2026-01-03:", contains)
    conn.execute(
        sa.insert(events).values(
            occurred_at=datetime(2026, 1, 2, tzinfo=UTC),
            session_id=uuid.UUID("00000000-0000-0000-0000-000000000024"),
            event_type="page_view",
        )
    )
    print("events.session_id", repr(conn.execute(sa.select(events.c.session_id).limit(1)).scalar()))
    conn.rollback()
