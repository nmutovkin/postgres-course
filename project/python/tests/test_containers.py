"""Module 27: a throwaway PostgreSQL server per test session, with testcontainers.

Opt-in (it needs Docker and starts a container, ~3 s with the image cached):

    BAZAAR_TEST_DOCKER=1 pytest -m docker

This is the strongest isolation there is: a brand-new cluster, so even
cluster-wide objects (roles) start from nothing. It proves the baseline
revision can build Bazaar where no role exists yet, which no test against
the course container can.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
import sqlalchemy as sa

from bazaar import migrate
from bazaar.catalog import diff, snapshot
from bazaar.config import get_settings

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.environ.get("BAZAAR_TEST_DOCKER") != "1", reason="set BAZAAR_TEST_DOCKER=1"
    ),
]


@pytest.fixture(scope="module")
def pg_container() -> Iterator[str]:
    from testcontainers.community.postgres import PostgresContainer

    # driver=None: a plain postgresql:// URL (we add the SQLAlchemy driver ourselves).
    # The superuser must be called postgres: the baseline dump names it as the
    # owner of one object (the extended statistics of module 12).
    with PostgresContainer(
        "postgres:17", username="postgres", password="postgres", dbname="bazaar", driver=None
    ) as pg:
        yield pg.get_connection_url()


def test_migrations_build_bazaar_in_a_new_cluster(pg_container: str) -> None:
    migrate.upgrade(pg_container)
    fresh = sa.create_engine(pg_container.replace("postgresql://", "postgresql+psycopg://"))
    reference = sa.create_engine(
        get_settings().dsn.replace("postgresql://", "postgresql+psycopg://")
    )
    try:
        with fresh.connect() as c:
            migrate.check_schema(c)
            roles = c.execute(
                sa.text(
                    "SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname LIKE 'bazaar%'"
                    " ORDER BY 1"
                )
            ).all()
            got = snapshot(c)
        with reference.connect() as c:
            want = snapshot(c)
    finally:
        fresh.dispose()
        reference.dispose()
    # The roles exist, without LOGIN: passwords are provisioning, not schema.
    assert [r[0] for r in roles] == [
        "bazaar_analyst",
        "bazaar_app",
        "bazaar_owner",
        "bazaar_readonly",
        "bazaar_seller",
    ]
    assert not any(r[1] for r in roles)
    assert diff(want, got, labels=("course container", "testcontainer")) == []
