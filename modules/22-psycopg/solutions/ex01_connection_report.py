"""Exercise 1: a connection report."""

import os

import psycopg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")


def report(conn: psycopg.Connection) -> dict[str, object]:
    info = conn.info
    return {
        "server_version": f"{info.server_version // 10000}.{info.server_version % 10000}",
        "backend_pid": info.backend_pid,
        "user": info.user,
        "database": info.dbname,
        "application_name": info.parameter_status("application_name"),
        "TimeZone": info.parameter_status("TimeZone"),
        "search_path": conn.execute("SHOW search_path").fetchone()[0],
        "statement_timeout": conn.execute("SHOW statement_timeout").fetchone()[0],
        "ssl": conn.execute(
            "SELECT coalesce((SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()), false)"
        ).fetchone()[0],
    }


if __name__ == "__main__":
    with psycopg.connect(
        DSN, application_name="ex01-report", options="-c statement_timeout=5s", connect_timeout=5
    ) as conn:
        print("status at start:", conn.info.transaction_status.name)
        for k, v in report(conn).items():
            print(f"{k:18s} {v}")
        print("status after queries:", conn.info.transaction_status.name)
        conn.rollback()
        print("status after rollback:", conn.info.transaction_status.name)
