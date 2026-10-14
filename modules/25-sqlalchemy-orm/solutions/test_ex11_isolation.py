"""Exercise 11: test code that commits, without ever committing.

Run from project/python:
    pytest ../../modules/25-sqlalchemy-orm/solutions/test_ex11_isolation.py -v
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from bazaar.orm.models import Review, User
from bazaar.sa.engine import create_engine

# --- code under test: it manages its own transactions --------------------------------------


def register_user(session: Session, email: str, name: str) -> User:
    """Commits on success; on a duplicate e-mail, rolls back and returns the existing user."""
    user = User(email=email, full_name=name)
    session.add(user)
    try:
        session.commit()
    except sa.exc.IntegrityError:
        session.rollback()
        user = session.scalars(sa.select(User).where(sa.func.lower(User.email) == email.lower())).one()
    return user


def leave_review(session: Session, user_id: int, product_id: int, rating: int) -> Review:
    with session.begin_nested():  # a savepoint of its own
        review = Review(user_id=user_id, product_id=product_id, rating=rating)
        session.add(review)
    session.commit()
    return review


# --- fixtures -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine() -> Iterator[sa.Engine]:
    e = create_engine(pool_size=2)
    yield e
    e.dispose()


@pytest.fixture
def session(engine: sa.Engine) -> Iterator[Session]:
    """A Session joined to an outer transaction that is always rolled back.

    join_transaction_mode="create_savepoint": the Session never touches the outer
    transaction. Its commit() releases a SAVEPOINT, its rollback() rolls back
    to one, and a new SAVEPOINT starts for the next unit of work.
    """
    with engine.connect() as conn:
        outer = conn.begin()
        with Session(bind=conn, join_transaction_mode="create_savepoint") as s:
            yield s
        outer.rollback()


@pytest.fixture(scope="module")
def baseline(engine: sa.Engine) -> Iterator[tuple[int, int]]:
    """Row counts before the module's tests; checked again after all of them."""

    def counts() -> tuple[int, int]:
        with engine.connect() as c:
            return (
                c.scalar(sa.text("SELECT count(*) FROM users")),
                c.scalar(sa.text("SELECT count(*) FROM reviews")),
            )

    before = counts()
    yield before
    assert counts() == before, "a test leaked committed rows"


# --- tests ----------------------------------------------------------------------------------


def test_register_commits_and_is_visible(session: Session, baseline) -> None:
    u = register_user(session, "ex11@example.com", "Ex Eleven")
    assert u.id is not None
    # visible in the same (outer) transaction after the "commit"
    assert session.scalar(sa.select(sa.func.count()).where(User.email == "ex11@example.com")) == 1


def test_duplicate_rolls_back_only_the_savepoint(session: Session, baseline) -> None:
    first = register_user(session, "ex11-dup@example.com", "First")
    again = register_user(session, "EX11-DUP@example.com", "Second")  # IntegrityError inside
    assert again.id == first.id and again.full_name == "First"
    assert session.get(User, first.id) is first  # the session is still usable


def test_review_with_nested_savepoint(session: Session, baseline) -> None:
    u = register_user(session, "ex11-rev@example.com", "Reviewer")
    r = leave_review(session, u.id, 20, 4)
    assert session.get(Review, r.id).rating == 4


def test_nothing_from_other_tests_is_visible(session: Session, baseline) -> None:
    n = session.scalar(sa.select(sa.func.count()).where(User.email.like("ex11%")))
    assert n == 0
