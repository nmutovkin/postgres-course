"""Request and response models (pydantic v2).

The API boundary is where input is validated and converted (module 23 warned
that asyncpg truncates 8.5 to 8 for a bigint): ids are positive ints,
quantities are bounded, money is Decimal (serialized as a string, never a
float), and responses are built from plain values, never from live ORM objects
(no lazy loading can happen while FastAPI serializes).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from bazaar.models import OrderStatus, ProductStatus

# Money travels as a JSON string ("19.99"): a float would turn 0.1 + 0.2 into
# 0.30000000000000004 in every client that parses it.
Money = Annotated[Decimal, PlainSerializer(lambda d: f"{d:.2f}", return_type=str)]
PositiveId = Annotated[int, Field(gt=0, le=2**63 - 1)]


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = Field(
        None, description="Pass as ?cursor= to get the next page; null on the last page"
    )


# --- catalog ----------------------------------------------------------------------------------


class ProductSummary(BaseModel):
    id: int
    title: str
    slug: str
    price: Money
    category_id: int
    seller_id: int
    available: int
    created_at: datetime


class CategoryRef(BaseModel):
    id: int
    name: str
    slug: str


class ProductDetail(BaseModel):
    id: int
    title: str
    slug: str
    description: str | None
    price: Money
    status: ProductStatus
    seller_id: int
    shop_name: str
    category_path: list[CategoryRef] = Field(description="Root first")
    attributes: dict[str, Any]
    tags: list[str]
    available: int
    rating_avg: Decimal | None
    rating_count: int


class SearchHit(BaseModel):
    product_id: int
    title: str
    price: Money
    category_name: str
    snippet: str
    score: Decimal
    matched_by: str = Field(description="'fts' (full-text) or 'fuzzy' (trigram fallback)")


class SearchResult(BaseModel):
    query: str
    items: list[SearchHit]


# --- cart, checkout, orders ------------------------------------------------------------------


class CartItemIn(BaseModel):
    product_id: PositiveId
    quantity: Annotated[int, Field(gt=0, le=100)] = 1


class CartLine(BaseModel):
    product_id: int
    title: str
    unit_price: Money
    quantity: int
    line_total: Money
    available: int
    purchasable: bool = Field(description="active and enough stock right now")


class Cart(BaseModel):
    items: list[CartLine]
    total: Money


class CheckoutIn(BaseModel):
    shipping_address_id: PositiveId | None = Field(
        None, description="Default: the user's default address"
    )


class OrderLine(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int
    quantity: int
    unit_price: Money


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    seller_id: int
    status: OrderStatus
    total: Money
    created_at: datetime
    shipping_address_id: int | None
    items: list[OrderLine]


class CheckoutOut(BaseModel):
    orders: list[OrderOut]
    replayed: bool = Field(description="true when the Idempotency-Key was seen before")


# --- seller portal ----------------------------------------------------------------------------


class SellerOrder(BaseModel):
    id: int
    user_id: int
    status: OrderStatus
    total: Money
    item_count: int
    created_at: datetime


class SellerProduct(BaseModel):
    id: int
    sku: str
    title: str
    description: str | None
    price: Money
    status: ProductStatus
    category_id: int
    tags: list[str]
    attributes: dict[str, Any]
    quantity: int | None
    reserved: int | None
    version: int = Field(description="Send it back in PATCH: optimistic locking (xmin)")


class ProductPatch(BaseModel):
    """Only the fields to change; `version` is the one the client last saw."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=0, description="the product's `version` from the last GET")
    title: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    description: Annotated[str, Field(max_length=5000)] | None = None
    price: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)] | None = None
    status: ProductStatus | None = None
    tags: list[Annotated[str, Field(min_length=1, max_length=50)]] | None = None
    attributes: dict[str, Any] | None = None


class DailyStats(BaseModel):
    day: date
    orders: int
    items_sold: int
    revenue: Money


class SellerStats(BaseModel):
    seller_id: int
    date_from: date
    date_to: date
    orders: int
    items_sold: int
    revenue: Money
    days: list[DailyStats]
