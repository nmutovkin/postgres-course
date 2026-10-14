"""Plain data classes returned by the repository layers.

They are deliberately boring: no database handles, no lazy loading. Both the
sync (bazaar.repo) and async (bazaar.aio.repo) layers return these, and later
modules (SQLAlchemy, FastAPI) can convert them to and from their own types.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class ProductStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class OrderStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


@dataclass(slots=True)
class Product:
    id: int
    seller_id: int
    category_id: int
    sku: str
    title: str
    description: str | None
    price: Decimal
    status: ProductStatus
    attributes: dict[str, Any]
    tags: list[str]
    created_at: datetime
    available: int | None  # quantity - reserved; None if there is no inventory row


@dataclass(slots=True)
class ProductHit:
    id: int
    title: str
    price: Decimal
    rank: float


@dataclass(slots=True)
class OrderSummary:
    id: int
    seller_id: int
    status: OrderStatus
    total: Decimal
    created_at: datetime
    item_count: int


@dataclass(slots=True)
class OrderPage:
    items: list[OrderSummary]
    next_cursor: str | None  # pass back as `after=` to get the next page; None = last page


@dataclass(slots=True)
class NewProduct:
    """One row for import_products(). (seller_id, sku) identifies the product."""

    seller_id: int
    category_id: int
    sku: str
    title: str
    price: Decimal
    description: str | None = None
    status: ProductStatus = ProductStatus.DRAFT
    attributes: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    quantity: int = 0


@dataclass(slots=True)
class ImportResult:
    inserted: int
    updated: int


@dataclass(slots=True)
class Job:
    id: int
    queue: str
    kind: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int


# --- keyset cursors -----------------------------------------------------------
# A cursor is the (created_at, id) of the last row of a page, encoded so that a
# client treats it as an opaque token instead of something to construct.


def encode_cursor(created_at: datetime, id_: int) -> str:
    raw = json.dumps([created_at.isoformat(), id_]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(token: str) -> tuple[datetime, int]:
    padded = token + "=" * (-len(token) % 4)
    ts, id_ = json.loads(base64.urlsafe_b64decode(padded))
    return datetime.fromisoformat(ts), int(id_)
