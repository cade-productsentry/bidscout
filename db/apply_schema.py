"""Apply db/schema.sql to the database in DATABASE_URL and list the tables.

Usage:
    DATABASE_URL=postgres://... python db/apply_schema.py

Exists because psql is not always installed locally. Safe to re-run:
schema.sql is idempotent.
"""

import os
import pathlib
import sys

import psycopg


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("error: DATABASE_URL is not set", file=sys.stderr)
        return 1

    schema = pathlib.Path(__file__).with_name("schema.sql").read_text()

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(schema)
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                ORDER BY table_name
                """
            )
            tables = [row[0] for row in cur.fetchall()]
        conn.commit()

    print("tables in public schema:")
    for name in tables:
        print(f"  - {name}")

    missing = {"bids", "subscribers", "clients"} - set(tables)
    if missing:
        print(f"error: missing tables: {sorted(missing)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
