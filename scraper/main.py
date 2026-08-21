"""BidScout scraper entrypoint.

Pulls open federal bid notices from SAM.gov for the trades BidScout tracks and
upserts them into the `bids` table. Runs on a GitHub Actions cron (every 6h)
and can be run by hand:

    DATABASE_URL=... python scraper/main.py [--days 2] [--limit 50] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(__file__))

from neon_http import Neon  # noqa: E402
from sources import sam_gov  # noqa: E402

UPSERT = """
INSERT INTO bids (source, source_id, url, title, agency, trade, naics, state, county, city,
                  notice_type, set_aside, posted_at, due_at, poc_email, raw_text, updated_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16, now())
ON CONFLICT (url) DO UPDATE SET
  title = EXCLUDED.title,
  agency = COALESCE(EXCLUDED.agency, bids.agency),
  trade = COALESCE(EXCLUDED.trade, bids.trade),
  naics = COALESCE(EXCLUDED.naics, bids.naics),
  state = COALESCE(EXCLUDED.state, bids.state),
  city = COALESCE(EXCLUDED.city, bids.city),
  notice_type = EXCLUDED.notice_type,
  set_aside = COALESCE(EXCLUDED.set_aside, bids.set_aside),
  due_at = COALESCE(EXCLUDED.due_at, bids.due_at),
  poc_email = COALESCE(EXCLUDED.poc_email, bids.poc_email),
  raw_text = COALESCE(EXCLUDED.raw_text, bids.raw_text),
  updated_at = now()
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=int(os.environ.get("SCRAPE_DAYS", "3")))
    ap.add_argument("--limit", type=int, default=None, help="stop after N bids (for testing)")
    ap.add_argument("--dry-run", action="store_true", help="print bids, do not write to DB")
    args = ap.parse_args()

    database_url = os.environ.get("DATABASE_URL", "")
    db: Neon | None = None
    known: set[str] = set()
    if args.dry_run:
        print("dry run: not touching the database")
    else:
        if not database_url:
            print("error: DATABASE_URL is not set", file=sys.stderr)
            return 2
        db = Neon(database_url)
        # Notices already stored and unchanged in the last day are skipped so we
        # do not re-fetch thousands of detail pages on every run.
        rows = db.query(
            "SELECT source_id FROM bids WHERE source = 'sam.gov' AND updated_at > now() - interval '1 day'"
        )
        known = {r["source_id"] for r in rows}
        print(f"{len(known)} sam.gov notices refreshed within 24h, skipping those")

    inserted = 0
    for bid in sam_gov.fetch(since_days=args.days, known_ids=known, limit=args.limit):
        if args.dry_run or db is None:
            print(f"[{bid.state or '--'}] {bid.trade or bid.naics}: {bid.title} (due {bid.due_at}) {bid.url}")
            inserted += 1
            continue
        d = asdict(bid)
        db.execute(
            UPSERT,
            [
                d["source"], d["source_id"], d["url"], d["title"], d["agency"], d["trade"], d["naics"],
                d["state"], d["county"], d["city"], d["notice_type"], d["set_aside"], d["posted_at"],
                d["due_at"], d["poc_email"], d["raw_text"],
            ],
        )
        inserted += 1
        if inserted % 25 == 0:
            print(f"  {inserted} upserted...")

    print(f"done: {inserted} bids {'found' if args.dry_run else 'upserted'}")
    if db is not None:
        stats = db.query(
            "SELECT trade, count(*) AS n FROM bids WHERE due_at > now() GROUP BY trade ORDER BY n DESC"
        )
        print("open bids by trade:", ", ".join(f"{r['trade'] or '?'}={r['n']}" for r in stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
