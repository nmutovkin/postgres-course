"""Exercise 5: the metadata change, applied in memory (the learner edits bazaar/sa/tables.py).

    reviews.helpful_votes  integer NOT NULL DEFAULT 0, CHECK (helpful_votes >= 0)
    index (product_id, helpful_votes DESC) for "most helpful reviews first"
"""

from __future__ import annotations

import sqlalchemy as sa

from bazaar.sa import tables


def patch_metadata() -> None:
    reviews = tables.reviews
    if "helpful_votes" in reviews.c:
        return
    reviews.append_column(
        sa.Column("helpful_votes", sa.Integer, nullable=False, server_default="0")
    )
    reviews.append_constraint(
        sa.CheckConstraint("helpful_votes >= 0", name="helpful_votes_nonnegative")
    )
    sa.Index(
        "reviews_product_id_helpful_votes_idx",
        reviews.c.product_id,
        reviews.c.helpful_votes.desc(),
    )
