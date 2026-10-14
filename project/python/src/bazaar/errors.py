"""Domain exceptions, and the translation from database errors to them.

The database reports business-rule violations with SQLSTATE codes (see
project/db/1001_schema_concurrency.sql):

    BZ001  insufficient stock          DETAIL: "requested N, available M"
    BZ002  product missing / not active
    BZ003  user missing / inactive
    BZ004  shipping address does not belong to the user
    22023  invalid_parameter_value     (malformed items)

Callers of the repository layer should never have to know about SQLSTATEs or
driver exception classes: they catch InsufficientStock, not
psycopg.errors.lookup("BZ001"). translate() works for both psycopg and asyncpg
exceptions, because both expose `.sqlstate`.
"""

from __future__ import annotations

import re


class BazaarError(Exception):
    """Base class of all domain errors."""


class InvalidOrder(BazaarError):
    """The order request itself is malformed (SQLSTATE 22023)."""


class InsufficientStock(BazaarError):
    """Not enough available stock for a product (SQLSTATE BZ001)."""

    def __init__(
        self,
        message: str,
        product_id: int | None = None,
        requested: int | None = None,
        available: int | None = None,
    ) -> None:
        super().__init__(message)
        self.product_id = product_id
        self.requested = requested
        self.available = available


class ProductUnavailable(BazaarError):
    """A product does not exist or is not active (SQLSTATE BZ002)."""


class UserInactive(BazaarError):
    """The buyer does not exist or is inactive (SQLSTATE BZ003)."""


class AddressMismatch(BazaarError):
    """The shipping address belongs to someone else (SQLSTATE BZ004)."""


_SIMPLE = {
    "22023": InvalidOrder,
    "BZ002": ProductUnavailable,
    "BZ003": UserInactive,
    "BZ004": AddressMismatch,
}

_PRODUCT_RE = re.compile(r"product (\d+)")
_DETAIL_RE = re.compile(r"requested (\d+), available (-?\d+)")


def _message_and_detail(exc: BaseException) -> tuple[str, str | None]:
    diag = getattr(exc, "diag", None)  # psycopg
    if diag is not None:
        return diag.message_primary or str(exc), diag.message_detail
    # asyncpg: PostgresMessage attributes
    return getattr(exc, "message", None) or str(exc), getattr(exc, "detail", None)


def translate(exc: BaseException) -> BaseException:
    """Return the domain exception for a database error, or `exc` itself.

    Usage:
        except psycopg.Error as e:
            raise translate(e) from e
    """
    sqlstate = getattr(exc, "sqlstate", None)
    message, detail = _message_and_detail(exc)
    if sqlstate == "BZ001":
        pid = _PRODUCT_RE.search(message)
        nums = _DETAIL_RE.search(detail or "")
        return InsufficientStock(
            message,
            product_id=int(pid.group(1)) if pid else None,
            requested=int(nums.group(1)) if nums else None,
            available=int(nums.group(2)) if nums else None,
        )
    cls = _SIMPLE.get(sqlstate or "")
    return cls(message) if cls else exc
