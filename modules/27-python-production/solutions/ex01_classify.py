"""Exercise 1: trigger real errors on the reference database and classify them.

    cd project/python && python ../../modules/27-python-production/solutions/ex01_classify.py

Every case runs in its own short-lived connection; nothing is changed.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import psycopg
from sqlalchemy.engine import make_url

from bazaar.config import get_settings
from bazaar.resilience import DEFAULT_POLICY, classify

DSN = get_settings().dsn


def as_role(user: str, password: str) -> str:
    return make_url(DSN).set(username=user, password=password).render_as_string(
        hide_password=False
    )


def duplicate_email() -> None:
    with psycopg.connect(DSN) as c:
        c.execute("INSERT INTO users (email, full_name) SELECT email, 'x' FROM users WHERE id = 1")


def statement_timeout() -> None:
    with psycopg.connect(DSN) as c:
        c.execute("SET LOCAL statement_timeout = '50ms'")
        c.execute("SELECT count(*) FROM order_items a JOIN order_items b USING (product_id)")


def nowait() -> None:
    holder = psycopg.connect(DSN)
    holder.execute("SELECT 1 FROM orders WHERE id = 1 FOR UPDATE")
    try:
        with psycopg.connect(DSN) as c:
            c.execute("SELECT 1 FROM orders WHERE id = 1 FOR UPDATE NOWAIT")
    finally:
        holder.close()


def serialization_failure() -> None:
    """Two REPEATABLE READ transactions update the same row: the second one,
    once the first commits, cannot apply its update to a snapshot it didn't see."""
    a, b = psycopg.connect(DSN), psycopg.connect(DSN)
    errors: list[BaseException] = []

    def second() -> None:
        try:
            b.execute("UPDATE inventory SET quantity = quantity WHERE product_id = 2")
        except psycopg.Error as e:
            errors.append(e)

    try:
        for c in (a, b):
            c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            c.execute("SELECT quantity FROM inventory WHERE product_id = 2")
        a.execute("UPDATE inventory SET quantity = quantity WHERE product_id = 2")
        t = threading.Thread(target=second)
        t.start()  # blocks on a's row lock
        a.commit()  # ... and fails when a commits
        t.join()
        raise errors[0]
    finally:
        a.close()
        b.close()


def permission_denied() -> None:
    with psycopg.connect(as_role("bazaar_app", "app")) as c:
        c.execute("CREATE TABLE bazaar.not_allowed (id int)")


def business_rule() -> None:
    with psycopg.connect(DSN) as c:
        c.execute("""SELECT place_order(8, '[{"product_id": 999999999, "quantity": 1}]', NULL)""")


def wrong_password() -> None:
    psycopg.connect(as_role("bazaar_app", "wrong"))


def terminated() -> None:
    with psycopg.connect(DSN) as c, psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute("SELECT pg_terminate_backend(%s)", (c.info.backend_pid,))
        c.execute("SELECT 1")


CASES: list[tuple[str, Callable[[], None]]] = [
    ("duplicate email", duplicate_email),
    ("statement_timeout", statement_timeout),
    ("FOR UPDATE NOWAIT", nowait),
    ("concurrent update, REPEATABLE READ", serialization_failure),
    ("CREATE TABLE as bazaar_app", permission_denied),
    ("place_order(): unknown product", business_rule),
    ("wrong password", wrong_password),
    ("session terminated (failover)", terminated),
]

if __name__ == "__main__":
    print(f"{'situation':<36} {'exception':<26} {'sqlstate':<8} {'kind':<14} retried / if idempotent")
    for label, fn in CASES:
        try:
            fn()
            print(f"{label:<36} (no error)")
        except Exception as e:  # noqa: BLE001
            r1 = DEFAULT_POLICY.should_retry(e, idempotent=False)
            r2 = DEFAULT_POLICY.should_retry(e, idempotent=True)
            print(
                f"{label:<36} {type(e).__name__:<26} {str(getattr(e, 'sqlstate', None)):<8}"
                f" {classify(e)!s:<14} {r1} / {r2}"
            )
