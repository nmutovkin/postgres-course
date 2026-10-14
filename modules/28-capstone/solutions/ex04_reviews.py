"""Exercise 4: reviews. Only buyers of a product may review it, once.

POST /products/{product_id}/reviews   X-User-Id; {"rating": 1-5, "title"?, "body"?}
GET  /products/{product_id}/reviews   newest first, keyset pages, plus the rating summary

The rule "only users with a delivered order containing the product" lives in
module 25's services.submit_review(), which is synchronous ORM code: run_sync
runs it on the AsyncSession's own Session (same connection, same transaction).
Errors map through the existing table in bazaar.api.errors:
ReviewNotAllowed -> 403, DuplicateReview -> 409, InvalidReview -> 422.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from bazaar.api.db import ReadSession, RwSession, UserId
from bazaar.api.errors import InvalidCursor
from bazaar.api.middleware import TaggedRoute
from bazaar.models import decode_cursor, encode_cursor
from bazaar.orm import services

router = APIRouter(route_class=TaggedRoute, tags=["reviews"])


class ReviewIn(BaseModel):
    rating: Annotated[int, Field(ge=1, le=5)]
    title: Annotated[str, Field(max_length=200)] | None = None
    body: Annotated[str, Field(max_length=5000)] | None = None


class ReviewOut(BaseModel):
    id: int
    user_id: int
    rating: int
    title: str | None
    body: str | None
    created_at: datetime


@router.post("/products/{product_id}/reviews", status_code=201, response_model=ReviewOut)
async def post_review(
    product_id: int, review: ReviewIn, user_id: UserId, session: RwSession
) -> ReviewOut:
    r = await session.run_sync(
        lambda s: services.submit_review(
            s, user_id, product_id, review.rating, review.title, review.body
        )
    )
    await session.flush()
    await session.refresh(r, ["created_at"])  # server default: fetch it
    return ReviewOut(
        id=r.id, user_id=r.user_id, rating=r.rating, title=r.title, body=r.body,
        created_at=r.created_at,
    )  # fmt: skip


_COLUMNS = (
    "SELECT id, user_id, rating, title, body, created_at FROM reviews WHERE product_id = :pid"
)
FIRST_PAGE = sa.text(f"{_COLUMNS} ORDER BY created_at DESC, id DESC LIMIT :limit")
NEXT_PAGE = sa.text(
    f"{_COLUMNS} AND (created_at, id) < (:ts, :last_id)"
    " ORDER BY created_at DESC, id DESC LIMIT :limit"
)


@router.get("/products/{product_id}/reviews")
async def list_reviews(
    product_id: int,
    session: ReadSession,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> dict:
    params: dict[str, object] = {"pid": product_id, "limit": limit + 1}
    stmt = FIRST_PAGE
    if cursor:
        try:
            params["ts"], params["last_id"] = decode_cursor(cursor)
        except Exception:  # noqa: BLE001
            raise InvalidCursor("malformed cursor") from None
        stmt = NEXT_PAGE
    rows = (await session.execute(stmt, params)).mappings().all()
    summary = (
        (
            await session.execute(
                sa.text(
                    "SELECT count(*) AS count, round(avg(rating), 2) AS average"
                    " FROM reviews WHERE product_id = :pid"
                ),
                {"pid": product_id},
            )
        )
        .mappings()
        .one()
    )
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "summary": dict(summary),
        "items": [ReviewOut(**r) for r in rows],
        "next_cursor": encode_cursor(rows[-1]["created_at"], rows[-1]["id"]) if more else None,
    }
