"""Exercise 5: a CSV price importer with COPY and validation.

    python ex05_price_importer.py ../data/price_updates_s42.csv            # dry run (rolls back)
    python ex05_price_importer.py ../data/price_updates_s42.csv --commit   # really apply
"""

import argparse
import csv
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import psycopg

DSN = os.environ.get("BAZAAR_DSN", "postgresql://postgres:postgres@localhost:5432/bazaar")
PRICE_RE = re.compile(r"^\d{1,10}(\.\d{1,2})?$")  # numeric(12,2), non-negative, no exponent


@dataclass
class Report:
    accepted: int = 0
    changed: int = 0
    rejected: list[tuple[int, str]] = field(default_factory=list)  # (line number, reason)


def parse(path: Path, report: Report):
    """Yield (line_no, seller_id, sku, price) for rows that pass the checks done in Python."""
    seen: dict[tuple[int, str], int] = {}
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != ["seller_id", "sku", "new_price"]:
            raise ValueError(f"unexpected header {reader.fieldnames}")
        for line_no, rec in enumerate(reader, start=2):  # line 1 is the header
            price = (rec["new_price"] or "").strip()
            if not rec["seller_id"].isdigit():
                report.rejected.append((line_no, f"bad seller_id {rec['seller_id']!r}"))
                continue
            if not PRICE_RE.match(price):
                report.rejected.append((line_no, f"bad price {price!r}"))
                continue
            key = (int(rec["seller_id"]), rec["sku"].strip())
            if key in seen:
                report.rejected.append((line_no, f"duplicate of line {seen[key]}"))
                continue
            seen[key] = line_no
            yield line_no, key[0], key[1], Decimal(price)


def import_prices(conn: psycopg.Connection, path: Path) -> Report:
    report = Report()
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """CREATE TEMP TABLE price_staging (
                   line_no int, seller_id bigint, sku text, new_price numeric(12,2)
               ) ON COMMIT DROP"""
        )
        with cur.copy("COPY price_staging FROM STDIN") as copy:
            for row in parse(path, report):
                copy.write_row(row)

        # Rows that match no product (unknown sku, or sku of another seller).
        cur.execute(
            """SELECT s.line_no, s.seller_id, s.sku FROM price_staging s
               WHERE NOT EXISTS (SELECT 1 FROM products p
                                 WHERE p.seller_id = s.seller_id AND p.sku = s.sku)
               ORDER BY s.line_no"""
        )
        for line_no, seller_id, sku in cur.fetchall():
            report.rejected.append((line_no, f"unknown product {sku} for seller {seller_id}"))
        cur.execute(
            """SELECT count(*) FROM price_staging s
               JOIN products p ON p.seller_id = s.seller_id AND p.sku = s.sku"""
        )
        report.accepted = cur.fetchone()[0]

        # Apply only real changes, and record each one in the price history.
        cur.execute(
            """WITH changed AS (
                   UPDATE products p
                   SET price = s.new_price
                   FROM price_staging s
                   WHERE p.seller_id = s.seller_id AND p.sku = s.sku
                     AND p.price IS DISTINCT FROM s.new_price
                   RETURNING p.id, p.price
               )
               INSERT INTO product_price_history (product_id, price, valid_from)
               SELECT id, price, now() FROM changed"""
        )
        report.changed = cur.rowcount
        cur.execute("DROP TABLE price_staging")
    report.rejected.sort()
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", type=Path)
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    with psycopg.connect(DSN) as conn:
        conn.execute("SELECT 1")  # open the outer transaction: import_prices() is a savepoint
        r = import_prices(conn, args.csv)
        print(f"accepted {r.accepted}, changed {r.changed}, rejected {len(r.rejected)}")
        for line_no, reason in r.rejected:
            print(f"  line {line_no}: {reason}")
        if args.commit:
            conn.commit()
        else:
            conn.rollback()
            print("dry run: rolled back")
