"""Warm-outreach email builder.

Turns a prospect row (company + trade + home state) into a fully personalized
outreach email using live bid counts from Neon, so the operations session can
review and send rather than hand-assemble each one. Follows the rules in the
Drive doc "BidScout Warm Outreach Template v1 (2026-08-24)".

    DATABASE_URL=... python scraper/outreach_prep.py \
        --company "Solar Plexus LLC" --trade electrical --state MT \
        --first-name "" --agency "USDA" --scope "electrical work"

    DATABASE_URL=... python scraper/outreach_prep.py --batch prospects.json

NEVER commit a prospect/batch file to this repo — it is public. HubSpot is the
store of record for prospect contact data; generate the batch file at send time
into a scratch directory outside the working tree (see .gitignore).

Batch input is a JSON list of objects with keys: company, trade, state, email,
and optionally first_name, agency, scope. Prospects without an email, or marked
skip:true, are reported as unsendable instead of rendered.

Nothing here sends mail. Output is text for a human/operator review step.

COMPLIANCE (CAN-SPAM, 15 U.S.C. 7704): every commercial email must carry a valid
physical postal address, and the law makes no exception for business-to-business
mail. Penalties run to tens of thousands of dollars PER MESSAGE. This script
therefore fails closed: set BIDSCOUT_POSTAL_ADDRESS (a street address, a USPS
PO box, or a private mailbox registered with a commercial mail receiving agency)
or it will refuse to produce sendable drafts. Do not work around this check.
"""

from __future__ import annotations

import argparse
import json
import re
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from neon_http import Neon  # noqa: E402

SITE = "https://bidscout.pages.dev"

# Required in every commercial email — see COMPLIANCE note above.
POSTAL_ADDRESS = os.environ.get("BIDSCOUT_POSTAL_ADDRESS", "").strip()

# Deep links into /bids/{state}/{trade}/ are ON by default since 2026-08-27:
# those pages are now server-rendered from Neon (web/functions/) and verified
# to show real listings. They were empty 2026-08-22 → 08-27, hence the switch.
# Set BIDSCOUT_DEEP_LINKS=0 to fall back to linking only the sam.gov notice
# (do that if a spot check of the page for the prospect's state comes up empty).
DEEP_LINKS = os.environ.get("BIDSCOUT_DEEP_LINKS", "1") == "1"

TRADE_LABEL = {
    "hvac-plumbing": "HVAC / plumbing",
    "electrical": "electrical",
    "roofing": "roofing",
    "painting": "painting",
    "site-work": "site work",
    "landscaping": "grounds / landscaping",
    "janitorial": "janitorial",
    "general-building": "general construction",
}

# Minimum open bids in the home state before we lead with a state-specific hook.
STATE_HOOK_MIN = 1
# Minimum in the surrounding region before we fall back to a regional hook.
REGION_HOOK_MIN = 3

# Census divisions — used only to widen the geographic hook when a contractor's
# home state is quiet. Never claim a bid is "local" when it is not.
DIVISIONS = {
    "New England": ["CT", "ME", "MA", "NH", "RI", "VT"],
    "Mid-Atlantic": ["NJ", "NY", "PA"],
    "Midwest": ["IL", "IN", "MI", "OH", "WI"],
    "Plains": ["IA", "KS", "MN", "MO", "NE", "ND", "SD"],
    "South Atlantic": ["DE", "DC", "FL", "GA", "MD", "NC", "SC", "VA", "WV"],
    "Southeast": ["AL", "KY", "MS", "TN"],
    "South Central": ["AR", "LA", "OK", "TX"],
    "Mountain": ["AZ", "CO", "ID", "MT", "NV", "NM", "UT", "WY"],
    "Pacific": ["AK", "CA", "HI", "OR", "WA"],
}


def region_for(state: str | None) -> tuple[str | None, list[str]]:
    if not state:
        return None, []
    for name, members in DIVISIONS.items():
        if state in members:
            return name, members
    return None, []


def fetch_context(db: Neon, trade: str, state: str | None) -> dict:
    """Open-bid counts + soonest example: home state, surrounding region, national."""
    ctx: dict = {"trade": trade, "state": (state or "").upper() or None}
    st = ctx["state"]

    nat = db.query(
        "SELECT count(*) AS n FROM bids_current WHERE trade = $1 AND due_at > now()", [trade]
    )
    ctx["national_count"] = int(nat[0]["n"]) if nat else 0

    ctx["state_count"] = 0
    if st:
        r = db.query(
            "SELECT count(*) AS n FROM bids_current WHERE trade = $1 AND state = $2 AND due_at > now()",
            [trade, st],
        )
        ctx["state_count"] = int(r[0]["n"]) if r else 0

    region_name, members = region_for(st)
    ctx["region_name"] = region_name
    ctx["region_count"] = 0
    if members:
        placeholders = ", ".join(f"${i + 2}" for i in range(len(members)))
        r = db.query(
            f"SELECT count(*) AS n FROM bids_current WHERE trade = $1 AND due_at > now() "
            f"AND state IN ({placeholders})",
            [trade] + members,
        )
        ctx["region_count"] = int(r[0]["n"]) if r else 0

    # Example bid: home state first, then the surrounding region, then national.
    # Prefer something the prospect could actually still bid — a job closing
    # tomorrow makes the product look useless — and skip stub titles.
    ACTIONABLE = (" AND due_at > now() + interval '10 days'"
                  " AND length(title) > 25")
    # The in-state example is the one sentence in the whole email that makes a
    # specific geographic claim, so it should rest on a state SAM.gov stated
    # rather than one we read out of prose. state_method is written by
    # geo.infer_state (migration 002); 'name' and 'title-name' are the prose
    # guesses. NULL means the row predates the column, so it stays eligible -
    # every open notice is re-upserted at least daily and fills itself in.
    CORROBORATED = " AND (state_method IS NULL OR state_method NOT IN ('name', 'title-name'))"
    cols = ("SELECT title, agency, city, state, due_at::text, url, set_aside FROM bids_current "
            "WHERE trade = $1 AND due_at > now()")
    rows = []
    ctx["example_scope"] = "national"
    # Home state first and hardest: the volume sentence promises an in-state
    # bid, so showing an out-of-state one instead reads as a contradiction.
    # Only widen once the home state is genuinely exhausted.
    if ctx["state_count"]:
        for extra in (ACTIONABLE + CORROBORATED, CORROBORATED, ACTIONABLE, ""):
            rows = db.query(cols + extra + " AND state = $2 ORDER BY due_at ASC LIMIT 1",
                            [trade, st])
            if rows:
                ctx["example_scope"] = "state"
                break
    if not rows and members and ctx["region_count"]:
        placeholders = ", ".join(f"${i + 2}" for i in range(len(members)))
        for extra in (ACTIONABLE, ""):
            rows = db.query(
                cols + extra + f" AND state IN ({placeholders}) ORDER BY due_at ASC LIMIT 1",
                [trade] + members,
            )
            if rows:
                ctx["example_scope"] = "region"
                break
    if not rows:
        for extra in (ACTIONABLE, ""):
            rows = db.query(cols + extra + " ORDER BY due_at ASC LIMIT 1", [trade])
            if rows:
                ctx["example_scope"] = "national"
                break
    ctx["example"] = rows[0] if rows else None
    return ctx


def clean_title(raw: str | None) -> str:
    """Display form of a SAM.gov notice title.

    Strips the leading PSC classification code ("Z2DA--", "N--") that SAM.gov
    prepends on some notices: it is filing metadata, not part of the job name,
    and it makes a quoted title look like a typo to a contractor reading a cold
    email. Also collapses the runs of whitespace that show up in shouted titles.
    Display only; bids_current normalizes the same prefix for deduping.
    """
    t = re.sub(r"^[A-Z0-9]{1,6}--", "", (raw or "").strip())
    return " ".join(t.split())


def clean_agency(raw: str | None) -> str:
    """SAM stores agency as e.g. 'AGRICULTURE, DEPARTMENT OF / USDA-FS, CSA 8'.
    Render something a contractor would recognise instead of the raw string."""
    if not raw:
        return "a federal agency"
    head = raw.split("/")[0].strip().rstrip(".,")
    up = head.upper()

    KNOWN = {
        "DEPT OF DEFENSE": "Dept. of Defense",
        "DEFENSE, DEPARTMENT OF": "Dept. of Defense",
        "AGRICULTURE, DEPARTMENT OF": "USDA",
        "VETERANS AFFAIRS, DEPARTMENT OF": "the VA",
        "JUSTICE, DEPARTMENT OF": "Dept. of Justice",
        "INTERIOR, DEPARTMENT OF THE": "Dept. of the Interior",
        "HOMELAND SECURITY, DEPARTMENT OF": "DHS",
        "GENERAL SERVICES ADMINISTRATION": "GSA",
        "ENVIRONMENTAL PROTECTION AGENCY": "the EPA",
        "TRANSPORTATION, DEPARTMENT OF": "Dept. of Transportation",
        "HEALTH AND HUMAN SERVICES, DEPARTMENT OF": "HHS",
        "STATE, DEPARTMENT OF": "the State Dept.",
        "COMMERCE, DEPARTMENT OF": "Dept. of Commerce",
        "TREASURY, DEPARTMENT OF": "Treasury",
    }
    if up in KNOWN:
        return KNOWN[up]

    # Generic "<NAME>, DEPARTMENT OF [THE]" -> "Dept. of [the] <Name>"
    if ", DEPARTMENT OF" in up:
        name, _, tail = head.partition(",")
        article = " the" if tail.upper().strip().endswith("THE") else ""
        return f"Dept. of{article} {name.title()}"
    if up.startswith("DEPT OF "):
        return "Dept. of " + head[8:].title()

    ACRONYMS = {"VA", "GSA", "EPA", "DHS", "HHS", "NASA", "USDA", "DOD", "FAA", "NIH"}
    return " ".join(w if w.upper() in ACRONYMS else w.title() for w in head.split())


def days_out(due_iso: str | None) -> int | None:
    if not due_iso:
        return None
    try:
        due = datetime.fromisoformat(due_iso.replace(" ", "T"))
    except ValueError:
        return None
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return (due - datetime.now(timezone.utc)).days


def build_email(p: dict, ctx: dict) -> dict:
    trade_label = TRADE_LABEL.get(p["trade"], p["trade"])
    st = ctx["state"]
    company = p["company"]
    greeting = f"Hi {p['first_name']}," if p.get("first_name") else "Hi there,"

    # The CTA link must match the hook we just used. Linking the state page
    # behind a "near {st}" or nationwide hook sends the reader to a page that
    # shows zero bids, which reads as a lie even though every number is true.
    link = SITE
    if st and ctx["state_count"] >= STATE_HOOK_MIN:
        n = ctx["state_count"]
        if n == 1:
            subject = f"An open federal {trade_label} bid in {st}, thought of {company}"
            volume = (
                f"There's one open {trade_label} solicitation in {st} right now, "
                f"plus {ctx['national_count']} nationwide."
            )
        else:
            subject = f"{n} open federal {trade_label} bids in {st}, thought of {company}"
            volume = (
                f"Right now there are {n} open {trade_label} solicitations in {st}, "
                f"and {ctx['national_count']} nationwide."
            )
        link = f"{SITE}/bids/{st.lower()}/{p['trade']}/"
    elif ctx.get("region_name") and ctx["region_count"] >= REGION_HOOK_MIN:
        subject = (f"{ctx['region_count']} open federal {trade_label} bids near {st}, "
                   f"thought of {company}")
        volume = (
            f"{st} is quiet this week, but there are {ctx['region_count']} open {trade_label} "
            f"solicitations across the {ctx['region_name']} states and "
            f"{ctx['national_count']} nationwide, and federal work travels."
        )
        # No per-region page exists, and the state page is empty by definition
        # here, so send them to the trade page where the nearby ones are listed.
        link = f"{SITE}/bids/trade/{p['trade']}/"
    else:
        subject = f"{ctx['national_count']} open federal {trade_label} bids, thought of {company}"
        volume = (
            f"There are {ctx['national_count']} open federal {trade_label} solicitations "
            f"nationwide right now, and small shops win a real share of them."
        )
        link = f"{SITE}/bids/trade/{p['trade']}/"

    # Congratulations line — only if we actually know the award context.
    if p.get("agency"):
        agency = p["agency"].strip()
        if agency.lower().startswith("the "):
            agency = agency[4:]
        p = {**p, "agency": agency}
        scope = p.get("scope") or f"{trade_label} work"
        congrats = (
            f"Congrats on the {p['agency']} {scope} award. Public award records show {company} "
            f"is one of the smaller {trade_label} shops actually winning federal work."
        )
    else:
        congrats = (
            f"Public award records show {company} is one of the smaller {trade_label} shops "
            f"actually winning federal work."
        )

    footer = (
        f"You received this because {company} appears in public federal award records. "
        f"Reply \"stop\" and we'll remove you immediately.\n"
        f"BidScout · {POSTAL_ADDRESS}"
    )

    cta_line = f"See what's open in your area: {link}\n\n" if DEEP_LINKS else ""

    ex = ctx.get("example")
    if ex:
        d = days_out(ex.get("due_at"))
        due_txt = (ex.get("due_at") or "")[:10]
        when = f"due {due_txt}" + (
            f", {d} day{'' if d == 1 else 's'} out" if d is not None and d >= 0 else ""
        )
        ex_where = ", ".join(x for x in (ex.get("city"), ex.get("state")) if x)
        if ctx.get("example_scope") == "state" and ctx["state_count"] == 1:
            lead = "Here it is:"   # we just said there is exactly one - don't repeat
        else:
            lead = {
                "state": "One in your state right now:",
                "region": "One nearby right now:",
            }.get(ctx.get("example_scope"), "One that's open right now:")
        title = clean_title(ex["title"])
        ex_line = (
            f'{lead} "{title}" '
            f'({clean_agency(ex.get("agency"))}{", " + ex_where if ex_where else ""}, {when}).'
        )
        # Always show the official notice URL: it is the proof the example is real.
        if ex.get("url"):
            ex_line += f"\n{ex['url']}"
    else:
        ex_line = ""

    body = f"""{greeting}

{congrats}

BidScout watches SAM.gov for small {trade_label} contractors. Every Monday we send a short digest of new federal {trade_label} bids in your state, and our Triage plan adds a one-page "pursue or skip" call on each one so your estimating hours only go to work you can actually win.

{volume}
{ex_line}

{cta_line}Want the Monday digest? It's free. Just reply "yes" or sign up at {SITE}. And since you're clearly bidding already: your first month of Triage is on us, no card, no strings.

If federal bids aren't a fit, tell us and we won't email again.

- BidScout
{SITE}

{footer}
"""
    return {"to": p.get("email"), "subject": subject, "body": body.strip()}


def load_prospect(args) -> dict:
    return {
        "company": args.company,
        "trade": args.trade,
        "state": args.state,
        "email": args.email,
        "first_name": args.first_name,
        "agency": args.agency,
        "scope": args.scope,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", help="JSON file of prospects")
    ap.add_argument("--company")
    ap.add_argument("--trade")
    ap.add_argument("--state")
    ap.add_argument("--email")
    ap.add_argument("--first-name", dest="first_name", default="")
    ap.add_argument("--agency", default="")
    ap.add_argument("--scope", default="")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    if not POSTAL_ADDRESS:
        print(
            "REFUSING TO RENDER SENDABLE DRAFTS: BIDSCOUT_POSTAL_ADDRESS is not set.\n"
            "CAN-SPAM requires a valid physical postal address in every commercial\n"
            "email, including B2B, with penalties up to ~$53,000 per message. Get a\n"
            "mailing address on file (street address, USPS PO box, or CMRA private\n"
            "mailbox), set BIDSCOUT_POSTAL_ADDRESS, and re-run. Do not hand-edit the\n"
            "footer to bypass this.",
            file=sys.stderr,
        )
        return 2

    db = Neon(os.environ["DATABASE_URL"])

    if args.batch:
        prospects = json.load(open(args.batch))
    else:
        if not (args.company and args.trade):
            ap.error("--company and --trade required (or use --batch)")
        prospects = [load_prospect(args)]

    out, unsendable = [], []
    cache: dict = {}
    for p in prospects:
        if p.get("skip"):
            unsendable.append((p["company"], p.get("skip_reason", "marked skip")))
            continue
        if not p.get("email"):
            unsendable.append((p["company"], "no email on file — use web form / phone"))
            continue
        key = (p["trade"], (p.get("state") or "").upper())
        if key not in cache:
            cache[key] = fetch_context(db, p["trade"], p.get("state"))
        out.append(build_email(p, cache[key]))

    if args.json:
        print(json.dumps({"emails": out, "unsendable": unsendable}, indent=2))
        return 0

    for e in out:
        print("=" * 72)
        print(f"To: {e['to']}")
        print(f"Subject: {e['subject']}")
        print("-" * 72)
        print(e["body"])
        print()
    if unsendable:
        print("=" * 72)
        print(f"NOT SENDABLE BY EMAIL ({len(unsendable)}):")
        for name, why in unsendable:
            print(f"  - {name}: {why}")
    print(f"\n[{len(out)} email(s) ready. Review each before sending. "
          f"Daily cap ~50; weekly target ~20.]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
