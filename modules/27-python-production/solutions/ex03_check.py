"""Exercise 3: connect the way PgBouncer does (no startup options) and look.

./project/bin/psql.sh < modules/27-python-production/solutions/ex03_role_defaults.sql
cd project/python && python ../../modules/27-python-production/solutions/ex03_check.py
"""

from __future__ import annotations

import psycopg
from sqlalchemy.engine import make_url

from bazaar.config import get_settings

DSN = get_settings().dsn
SHOW = (
    "SELECT current_user, current_setting('statement_timeout'),"
    " current_setting('idle_in_transaction_session_timeout'), current_setting('application_name')"
)


def main() -> None:
    for user, password in (
        ("bazaar_app", "app"),
        ("bazaar_seller", "seller"),
        ("bazaar_analyst", "analyst"),
    ):
        dsn = (
            make_url(DSN)
            .set(username=user, password=password)
            .render_as_string(hide_password=False)
        )
        with psycopg.connect(dsn) as c:  # no options, no application_name: like PgBouncer
            print(c.execute(SHOW).fetchone())
    with psycopg.connect(
        make_url(DSN)
        .set(username="bazaar_app", password="app")
        .render_as_string(hide_password=False),
        application_name="bazaar-worker",
    ) as c:
        print("a client-sent application_name wins:", c.execute(SHOW).fetchone()[3])


if __name__ == "__main__":
    main()
