"""Weekly digest generator.

Builds one plain-text digest per subscriber (or per trade/state combo) from the
`bids` table. Sending happens from the operations mailbox (Gmail) — this script
only produces the text so the heartbeat session can paste/send it.

    DATABASE_URL=... python scraper/digest.py                 # every subscriber
    DATABASE_URL=... python scraper/digest.py --trade hvac-plumbing --state TX
    DATABASE_URL=... python scraper/digest.py --json           # machine-readable

Rules: open bids only (due in the future), the subscriber's trade, their state
plus nationwide/unspecified-location notices, soonest deadline first, max 15.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from neon_http import Neon  # noqa: E402

SITE = "https://bidscout.pages.dev"

TRADE_LABEL = {
    "hvac-plumbing": "HVAC / Plumbing / Mechanical",
    "electrical": "Electrical",
    "roofing": "Roofing",
    "painting": "Painting",
    "site-work": "Paving / Fencing / Site work",
    "landscaping": "Landscaping / Grounds",
    "janitorial": "Janitorial / Custodial",
    "general-building": "General building construction",
}

QUERY = """
SELECT title, agency, state, city, set_aside, notice_type, due_at::text, url, left(raw_text, 400) AS raw_text
FROM bids
WHERE trade = $1
  AND due_at > now()
  AND (state = $2 OR state IS NULL)
ORDER BY (state IS NULL), due_at
LIMIT $3
"""


def _fmt_due(iso: str | None) -> str:
    if not iso:
        return "TBD"
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso[:10]
    days = (d - datetime.now(timezone.utc)).days
    return f"{d:%b %d} ({days}d)"


def _blurb(text: str | None, n: int = 160) -> str:
    if not text:
        return ""
    t = " ".join(text.split()).strip(" .*-")
    if len(t) < 20:
        return ""
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"


def build(db: Neon, trade: str, state: str, limit: int = 15) -> dict:
    rows = db.query(QUERY, [trade, state, limit])
    label = TRADE_LABEL.get(trade, trade)
    lines = [f"BidScout weekly digest — {label}, {state}", ""]
    local = [r for r in rows if r["state"]]
    national = [r for r in rows if not r["state"]]
    if not rows:
        lines.append(f"No open federal {label.lower()} solicitations in {state} this week.")
        lines.append(f"Browse other states: {SITE}/bids/")
    else:
        if local:
            lines.append(f"In {state} ({len(local)}):")
            for r in local:
                lines += _entry(r)
        if national:
            lines.append("")
            lines.append(f"Location not specified / nationwide ({len(national)}):")
            for r in national:
                lines += _entry(r)
    lines += [
        "",
        f"All open {label.lower()} bids in {state}: {SITE}/bids/{state.lower()}/{trade}/",
        "",
        "Want a one-page pursue/skip call on each of these, with the requirements checklist pulled out of the PDF? Reply \"triage\" and we'll set you up ($99/mo, cancel any time).",
        "",
        "— BidScout",
        "Reply \"stop\" to unsubscribe.",
    ]
    return {"trade": trade, "state": state, "count": len(rows), "subject": f"[BidScout] {len(rows)} open {label} bids — {state}, week of {datetime.now(timezone.utc):%b %d}", "body": "\n".join(lines)}


def _entry(r: dict) -> list[str]:
    where = ", ".join(x for x in [r.get("city"), r.get("state")] if x)
    meta = " · ".join(x for x in [f"Due {_fmt_due(r['due_at'])}", r.get("agency"), where, r.get("set_aside")] if x)
    out = [f"• {r['title']}", f"  {meta}"]
    b = _blurb(r.get("raw_text"))
    if b:
        out.append(f"  {b}")
    out.append(f"  {r['url']}")
    out.append("")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trade")
    ap.add_argument("--state")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    db = Neon(os.environ.get("DATABASE_URL", ""))

    targets: list[tuple[str | None, str, str]] = []
    if args.trade and args.state:
        targets.append((None, args.trade, args.state.upper()))
    else:
        subs = db.query("SELECT email, trade, state FROM subscribers WHERE tier IN ('free','triage','draft') AND trade IS NOT NULL AND state IS NOT NULL ORDER BY created_at")
        targets = [(s["email"], s["trade"], s["state"]) for s in subs]

    out = []
    for email, trade, state in targets:
        d = build(db, trade, state, args.limit)
        d["email"] = email
        out.append(d)
        if not args.json:
            print(f"=== {email or '(preview)'} ===\nSubject: {d['subject']}\n\n{d['body']}\n")
    if args.json:
        print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
