"""Errors as HTTP responses: application/problem+json (RFC 9457).

Every error the service returns has the same shape:

    HTTP/1.1 409 Conflict
    Content-Type: application/problem+json

    {"type": "https://bazaar.example/problems/insufficient-stock",
     "title": "Insufficient stock", "status": 409,
     "detail": "insufficient stock for product 4711",
     "product_id": 4711, "requested": 3, "available": 1,
     "request_id": "6f1c..."}

Three sources, three handlers:

* domain errors (bazaar.errors, raised by the services or translated from
  SQLSTATEs such as BZ001): a fixed table maps each class to a status;
* database errors that were not translated: bazaar.resilience.classify()
  decides; transient ones (timeouts, lost connections, pool exhausted, a
  serialization failure that outlived its retries) become 503 with
  Retry-After, everything else 500 with no internals in the body;
* request validation (pydantic): 422 with the list of problems.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from bazaar import errors as domain
from bazaar.resilience import ErrorKind, classify, sqlstate

log = logging.getLogger("bazaar.api")

PROBLEM = "application/problem+json"
TYPE_BASE = "https://bazaar.example/problems/"


class ApiError(Exception):
    """An error raised by the HTTP layer itself (not a domain rule)."""

    status = 400
    slug = "bad-request"
    title = "Bad request"

    def __init__(self, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.detail = detail
        self.extra = extra


class Unauthorized(ApiError):
    status, slug, title = 401, "unauthenticated", "Not authenticated"


class Forbidden(ApiError):
    status, slug, title = 403, "forbidden", "Forbidden"


class EmptyCart(ApiError):
    status, slug, title = 422, "empty-cart", "The cart is empty"


class InvalidCursor(ApiError):
    status, slug, title = 400, "invalid-cursor", "Invalid pagination cursor"


# domain exception -> (status, type slug, title)
DOMAIN_STATUS: dict[type[domain.BazaarError], tuple[int, str, str]] = {
    domain.InsufficientStock: (409, "insufficient-stock", "Insufficient stock"),
    domain.ProductUnavailable: (422, "product-unavailable", "Product not available"),
    domain.UserInactive: (403, "user-inactive", "User missing or inactive"),
    domain.AddressMismatch: (422, "address-mismatch", "Address does not belong to the user"),
    domain.InvalidOrder: (422, "invalid-order", "Invalid order"),
    domain.NotFound: (404, "not-found", "Not found"),
    domain.ConcurrentUpdate: (409, "concurrent-update", "Changed by someone else"),
    domain.IdempotencyConflict: (
        422,
        "idempotency-key-reused",
        "Idempotency-Key was used for a different request",
    ),
    domain.InvalidReview: (422, "invalid-review", "Invalid review"),
    domain.ReviewNotAllowed: (403, "review-not-allowed", "Only buyers may review"),
    domain.DuplicateReview: (409, "duplicate-review", "Already reviewed"),
}

TRANSIENT = {
    ErrorKind.TIMEOUT: ("timeout", "The database took too long"),
    ErrorKind.OVERLOADED: ("overloaded", "The service is overloaded"),
    ErrorKind.CONNECTION: ("database-unavailable", "The database is unavailable"),
    ErrorKind.READ_ONLY: ("database-unavailable", "The database is read-only (failover?)"),
    ErrorKind.SERIALIZATION: ("conflict-retry", "Too much contention, try again"),
    ErrorKind.LOCK_TIMEOUT: ("conflict-retry", "Too much contention, try again"),
}


def problem(
    request: Request,
    status: int,
    slug: str,
    title: str,
    detail: str | None = None,
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> JSONResponse:
    body: dict[str, Any] = {"type": TYPE_BASE + slug, "title": title, "status": status}
    if detail:
        body["detail"] = detail
    body.update({k: v for k, v in extra.items() if v is not None})
    rid = getattr(request.state, "request_id", None)
    if rid:
        body["request_id"] = rid
    return JSONResponse(body, status_code=status, media_type=PROBLEM, headers=headers)


def _domain_extra(exc: domain.BazaarError) -> dict[str, Any]:
    if isinstance(exc, domain.InsufficientStock):
        return {
            "product_id": exc.product_id,
            "requested": exc.requested,
            "available": exc.available,
        }
    return {}


async def _on_domain_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, domain.BazaarError)
    for cls in type(exc).__mro__:
        if cls in DOMAIN_STATUS:
            status, slug, title = DOMAIN_STATUS[cls]
            return problem(request, status, slug, title, str(exc), **_domain_extra(exc))
    return problem(request, 400, "domain-error", "Request rejected", str(exc))


async def _on_api_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return problem(request, exc.status, exc.slug, exc.title, exc.detail, **exc.extra)


async def _on_database_error(request: Request, exc: Exception) -> JSONResponse:
    """Errors nobody translated. The full error goes to the log, never to the client:
    messages can contain table names, values, even other users' data."""
    # A domain error wrapped by SQLAlchemy (raised outside the services' translation)
    orig = getattr(exc, "orig", None)
    if orig is not None:
        translated = domain.translate(orig)
        if isinstance(translated, domain.BazaarError):
            return await _on_domain_error(request, translated)
    kind = classify(exc)
    if kind in TRANSIENT:
        slug, title = TRANSIENT[kind]
        log.warning("transient database error: %s (sqlstate %s)", kind, sqlstate(exc))
        return problem(request, 503, slug, title, headers={"Retry-After": "1"})
    if isinstance(exc, sa.exc.IntegrityError):
        log.info("integrity error: %s", str(getattr(exc, "orig", exc)).splitlines()[0])
        return problem(
            request,
            409,
            "constraint-violation",
            "The request conflicts with the data",
            constraint=domain.constraint_name(exc),
        )
    log.error("unexpected database error", exc_info=exc)
    return problem(request, 500, "internal", "Internal error")


async def _on_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    issues = [
        {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")}
        for e in exc.errors()
    ]
    return problem(
        request, 422, "validation", "Invalid request", "see `errors`", errors=issues
    )


async def _on_http_exception(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    return problem(
        request,
        exc.status_code,
        "http-" + str(exc.status_code),
        str(exc.detail),
        headers=getattr(exc, "headers", None),
    )


def install(app: FastAPI) -> None:
    app.add_exception_handler(domain.BazaarError, _on_domain_error)
    app.add_exception_handler(ApiError, _on_api_error)
    app.add_exception_handler(sa.exc.DBAPIError, _on_database_error)
    app.add_exception_handler(sa.exc.TimeoutError, _on_database_error)  # pool exhausted
    app.add_exception_handler(RequestValidationError, _on_validation_error)
    app.add_exception_handler(StarletteHTTPException, _on_http_exception)
