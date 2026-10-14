"""Exercise 8: add_review() with errors mapped through constraint names."""

import os

import psycopg
from psycopg import errors

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")


class ReviewError(Exception): ...
class DuplicateReview(ReviewError): ...
class InvalidRating(ReviewError): ...
class UnknownProduct(ReviewError): ...
class UnknownUser(ReviewError): ...
class NotABuyer(ReviewError): ...


CONSTRAINTS: dict[str, type[ReviewError]] = {
    "reviews_product_id_user_id_key": DuplicateReview,
    "reviews_rating_range": InvalidRating,
    "reviews_product_id_fkey": UnknownProduct,
    "reviews_user_id_fkey": UnknownUser,
}

ADD_REVIEW = """
    INSERT INTO reviews (product_id, user_id, rating, title, body)
    SELECT %(product_id)s, %(user_id)s, %(rating)s, %(title)s, %(body)s
    WHERE NOT %(verified)s
       OR EXISTS (SELECT 1 FROM orders o JOIN order_items oi ON oi.order_id = o.id
                  WHERE o.user_id = %(user_id)s AND oi.product_id = %(product_id)s
                    AND o.status = 'delivered')
    RETURNING id
"""


def add_review(conn: psycopg.Connection, user_id: int, product_id: int, rating: int,
               title: str | None = None, body: str | None = None, *,
               verified: bool = True) -> int:  # fmt: skip
    params = {"user_id": user_id, "product_id": product_id, "rating": rating,
              "title": title, "body": body, "verified": verified}  # fmt: skip
    try:
        with conn.transaction():
            row = conn.execute(ADD_REVIEW, params).fetchone()
    except errors.IntegrityError as e:  # unique, check and FK violations are all IntegrityErrors
        cls = CONSTRAINTS.get(e.diag.constraint_name or "")
        if cls is None:
            raise
        raise cls(e.diag.message_detail or e.diag.message_primary) from e
    if row is None:
        raise NotABuyer(f"user {user_id} has no delivered order containing product {product_id}")
    return row[0]


if __name__ == "__main__":
    with psycopg.connect(DSN) as conn:
        buyer, product = conn.execute(
            """SELECT o.user_id, oi.product_id FROM orders o
               JOIN order_items oi ON oi.order_id = o.id
               WHERE o.status = 'delivered'
                 AND NOT EXISTS (SELECT 1 FROM reviews r
                                 WHERE r.user_id = o.user_id AND r.product_id = oi.product_id)
               ORDER BY o.id LIMIT 1"""
        ).fetchone()
        print("buyer", buyer, "product", product)
        print("ok ->", add_review(conn, buyer, product, 5, "Great"))
        cases = [
            ("same review again", (buyer, product, 4), True),
            ("rating 6", (buyer, product, 6), True),
            ("not a buyer", (7, product, 3), True),
            ("unknown product", (buyer, 999_999_999, 5), False),
            ("unknown user", (999_999_999, product, 5), False),
        ]
        for label, args, verified in cases:
            try:
                add_review(conn, *args, verified=verified)
            except ReviewError as e:
                print(f"{label:18s} -> {type(e).__name__}: {e}")
        conn.rollback()
