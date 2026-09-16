"""BidScout scraper entrypoint.

Pulls open federal bid notices from SAM.gov for the trades BidScout tracks and
upserts them into the `bids` table. Runs on a GitHub Actions cron (every 6h)
and can be run by hand:

    DATABASE_URL=... python scraper/main.py [--days 2] [--limit 50] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import platform
import sys
import traceback
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


def _runner_name() -> str:
    """Where this run is executing, for the scraper_runs table."""
    if os.environ.get("GITHUB_ACTIONS"):
        return f"github-actions run {os.environ.get('GITHUB_RUN_ID', '?')} ({os.environ.get('GITHUB_EVENT_NAME', '?')})"
    return os.environ.get("BIDSCOUT_RUNNER", f"manual@{platform.node() or 'unknown'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=int(os.environ.get("SCRAPE_DAYS", "3")))
    ap.add_argument("--limit", type=int, default=None, help="stop after N bids (for testing)")
    ap.add_argument("--dry-run", action="store_true", help="print bids, do not write to DB")
    args = ap.parse_args()

    database_url = os.environ.get("DATABASE_URL", "")
    db: Neon | None = None
    known: set[str] = set()
    run_id: int | None = None
    if args.dry_run:
        print("dry run: not touching the database")
    else:
        if not database_url:
            print("error: DATABASE_URL is not set", file=sys.stderr)
            return 2
        db = Neon(database_url)
        # Every run leaves a row in scraper_runs so a failing cron is visible
        # from the database alone (the GitHub Actions UI is not always reachable
        # from where operations happen).
        try:
            run_id = db.query(
                "INSERT INTO scraper_runs (runner, status) VALUES ($1, 'running') RETURNING id",
                [_runner_name()],
            )[0]["id"]
        except Exception as exc:  # noqa: BLE001 - bookkeeping must never block a scrape
            print(f"warning: could not record run start: {exc}", file=sys.stderr)
        # Notices already stored and unchanged in the last day are skipped so we
        # do not re-fetch thousands of detail pages on every run.
        rows = db.query(
            "SELECT source_id FROM bids WHERE source = 'sam.gov' AND updated_at > now() - interval '1 day'"
        )
        known = {r["source_id"] for r in rows}
        print(f"{len(known)} sam.gov notices refreshed within 24h, skipping those")

    inserted = 0
    errors: list[str] = []
    try:
        for bid in sam_gov.fetch(since_days=args.days, known_ids=known, limit=args.limit, errors=errors):
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
    except Exception:  # noqa: BLE001 - record the failure, then re-raise so the job goes red
        errors.append(traceback.format_exc()[-1500:])
        _finish_run(db, run_id, "failed", inserted, len(known), errors)
        raise

    status = "ok" if not errors else ("partial" if inserted else "failed")
    print(f"done: {inserted} bids {'found' if args.dry_run else 'upserted'}; {len(errors)} source error(s)")
    for e in errors:
        print("  source error:", e.splitlines()[-1][:300])
    if db is not None:
        _finish_run(db, run_id, status, inserted, len(known), errors)
        stats = db.query(
            "SELECT trade, count(*) AS n FROM bids_current WHERE due_at > now() GROUP BY trade ORDER BY n DESC"
        )
        print("open bids by trade:", ", ".join(f"{r['trade'] or '?'}={r['n']}" for r in stats))
    return 0 if status != "failed" else 1


def _finish_run(db: Neon | None, run_id: int | None, status: str, upserted: int, skipped: int, errors: list[str]) -> None:
    if db is None or run_id is None:
        return
    try:
        db.execute(
            "UPDATE scraper_runs SET finished_at = now(), status = $2, upserted = $3, skipped = $4, error = $5 WHERE id = $1",
            [run_id, status, upserted, skipped, "\n---\n".join(errors)[:8000] or None],
        )
    except Exception as exc:  # noqa: BLE001
        print(f"warning: could not record run end: {exc}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
