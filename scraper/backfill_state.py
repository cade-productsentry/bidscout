"""One-off / repeatable: fill in `state` for stored bids that have none.

    DATABASE_URL=... python scraper/backfill_state.py [--dry-run] [--limit N] [--no-fetch]

For every open bid with state IS NULL it re-reads the SAM.gov detail record (to
get a ZIP from placeOfPerformance) and runs geo.infer_state over the ZIP, the
title and the description. Only rows where inference succeeds are updated; the
rest stay NULL. Prints a per-method tally so the run can be audited.

--no-fetch skips SAM.gov and uses only the text already in the database (fast,
recovers the "address" and "name" methods but not "zip").
"""

from __future__ import annotations

import argparse
import collections
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from geo import infer_state, state_from_text  # noqa: E402
from neon_http import Neon  # noqa: E402
from sources import sam_gov  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-fetch", action="store_true", help="text-only, no SAM.gov calls")
    args = ap.parse_args()

    db = Neon(os.environ.get("DATABASE_URL", ""))
    rows = db.query(
        "select id, source_id, title, raw_text from bids "
        "where state is null and source = 'sam.gov' and (due_at is null or due_at >= now()) "
        "order by updated_at desc"
    )
    if args.limit:
        rows = rows[: args.limit]
    print(f"{len(rows)} open bids without a state")

    tally: collections.Counter[str] = collections.Counter()
    updated = 0
    for r in rows:
        pop = None
        if not args.no_fetch:
            try:
                det = sam_gov.detail(r["source_id"])
                pop = (det.get("data2") or det.get("data") or {}).get("placeOfPerformance")
            except RuntimeError as exc:
                print(f"  fetch failed {r['source_id']}: {exc}")
            time.sleep(0.25)
        inferred = infer_state(pop, r["title"], r["raw_text"]) if pop is not None else state_from_text(
            f"{r['title']}\n{r['raw_text'] or ''}"
        )
        if not inferred:
            tally["unresolved"] += 1
            continue
        tally[inferred.method] += 1
        print(f"  {inferred.state} [{inferred.method}] {r['title'][:80]}")
        if not args.dry_run:
            updated += db.execute(
                "update bids set state = $1, updated_at = now() where id = $2 and state is null",
                [inferred.state, r["id"]],
            )
    print(dict(tally))
    print(f"updated {updated} rows" if not args.dry_run else "dry run: nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
