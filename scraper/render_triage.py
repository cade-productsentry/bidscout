"""Triage skeleton renderer — semi-automates the Triage tier deliverable.

Pulls a bid row from Neon and renders the 1-page triage template with every
mechanical field pre-filled (title, buyer, location, due date/days-out,
set-aside, link, raw-text excerpt), leaving the analyst sections (verdict,
scores, gotchas, next actions) as clearly marked TODOs. Target: a finished
triage in under 30 minutes of session time.

    DATABASE_URL=... python scraper/render_triage.py --list --trade roofing --state OH
    DATABASE_URL=... python scraper/render_triage.py --id 123
    DATABASE_URL=... python scraper/render_triage.py --source-id N6247026R0055
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from neon_http import Neon  # noqa: E402

COLS = (
    "id, source, source_id, agency, title, trade, state, city, naics, "
    "notice_type, set_aside, posted_at::text, due_at::text, url, poc_email, "
    "left(raw_text, 1500) AS raw_text"
)

TEMPLATE = """\
## [VERDICT: TODO PURSUE / SKIP / WATCH] — <one-sentence reason>

**Bid:** {title} ({source_id})
**Buyer:** {agency}
**Where:** {where} | **Work window:** TODO <period of performance — check solicitation>
**Est. value:** TODO <range if given, else "not stated">
**Due:** {due} ({days_out}) | **Site visit:** TODO <required/encouraged/none>
**Set-aside:** {set_aside} — TODO <does client qualify?>
**Link:** {url}
**Gov POC:** {poc}

### What the job actually is

TODO 2-4 sentences from the solicitation. Raw excerpt below to start from:
> {excerpt}

### Why pursue / skip (scored)

- Fit to your trade & size: <1-5> — TODO
- Competition pressure: <1-5, 5 = least competition> — TODO (set-aside: {set_aside_short})
- Effort to bid: <1-5, 5 = easiest> — TODO (page count, bonding, wage determinations)
- Margin signal: <1-5> — TODO

**Total: <x>/20** (PURSUE >= 14 typical; 10-13 WATCH; < 10 SKIP)

### Deadlines & gotchas

- Questions due: TODO <date or "not stated">
- Amendments: TODO <count; must acknowledge? where>
- Bonding/insurance: TODO
- Registrations needed: SAM.gov active TODO; licenses TODO
- Wage rules: TODO <Davis-Bacon / SCA; determination #>

### If you pursue: next 3 actions

1. TODO <most urgent concrete step with date>
2. TODO
3. TODO

*Prepared by BidScout from the public solicitation ({notice_type}, posted {posted}).
Verify all dates and requirements against the posted documents before bidding.*
"""


def days_out(due_iso: str | None) -> str:
    if not due_iso:
        return "due date not stated"
    try:
        due = datetime.fromisoformat(due_iso.replace(" ", "T"))
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
    except ValueError:
        return "unparseable due date"
    delta = (due - datetime.now(timezone.utc)).days
    return f"{delta} days out" if delta >= 0 else f"CLOSED {-delta} days ago"


def render(row: dict) -> str:
    where = ", ".join(x for x in (row.get("city"), row.get("state")) if x) or "not stated"
    set_aside = row.get("set_aside") or "none stated"
    return TEMPLATE.format(
        title=row.get("title") or "?",
        source_id=row.get("source_id") or f'db#{row["id"]}',
        agency=row.get("agency") or "?",
        where=where,
        due=(row.get("due_at") or "not stated"),
        days_out=days_out(row.get("due_at")),
        set_aside=set_aside,
        set_aside_short=set_aside,
        url=row.get("url") or "?",
        poc=row.get("poc_email") or "not listed",
        excerpt=" ".join((row.get("raw_text") or "").split())[:1200] or "(no raw text captured)",
        notice_type=row.get("notice_type") or "notice",
        posted=(row.get("posted_at") or "?")[:10],
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--id", type=int, help="bids.id to render")
    ap.add_argument("--source-id", help="bids.source_id (e.g. SAM solicitation number)")
    ap.add_argument("--list", action="store_true", help="list open bids instead of rendering")
    ap.add_argument("--trade", help="filter for --list")
    ap.add_argument("--state", help="filter for --list")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    db = Neon(os.environ["DATABASE_URL"])

    if args.list:
        sql = f"SELECT id, title, agency, trade, state, due_at::text, set_aside FROM bids_current WHERE due_at > now()"
        params: list = []
        if args.trade:
            params.append(args.trade)
            sql += f" AND trade = ${len(params)}"
        if args.state:
            params.append(args.state.upper())
            sql += f" AND state = ${len(params)}"
        sql += f" ORDER BY due_at ASC LIMIT {int(args.limit)}"
        for r in db.query(sql, params):
            print(f'#{r["id"]:>6} | due {(r["due_at"] or "?")[:10]} | {r["trade"]:<16} | '
                  f'{r["state"] or "??"} | {(r["set_aside"] or "-")[:28]:<28} | {r["title"][:70]}')
        return 0

    if args.id:
        rows = db.query(f"SELECT {COLS} FROM bids WHERE id = $1", [args.id])
    elif args.source_id:
        rows = db.query(f"SELECT {COLS} FROM bids WHERE source_id = $1", [args.source_id])
    else:
        ap.error("provide --id, --source-id, or --list")
        return 2
    if not rows:
        print("No matching bid found.", file=sys.stderr)
        return 1
    print(render(rows[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
