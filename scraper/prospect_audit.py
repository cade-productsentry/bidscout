"""Verify a prospect's assigned trade and home state against its full award history.

WHY THIS EXISTS (found 2026-09-21). prospect_source.py queries USAspending one
NAICS code at a time, so a company enters the pool tagged with whichever trade
matched the query that found it. For a single-trade shop that is correct. For a
diversified contractor it can be a small minority of the company's actual work,
and the first-touch email then makes a trade claim about the prospect that its
own award record contradicts. The in-state hook has the same failure mode: a
contractor headquartered in one state may perform nearly all of its work
elsewhere, which makes "bids in your state" technically true but useless.

This script pulls every award for a recipient (not just the sourcing NAICS),
and reports what share supports the assigned trade and the assigned state.

    DATABASE_URL=... python scraper/prospect_audit.py --source 'USAspending wave-7 2026-09-20'
    DATABASE_URL=... python scraper/prospect_audit.py --ids 110,146,152
    DATABASE_URL=... python scraper/prospect_audit.py --company 'Victory Contracting'

Flags, both tunable with --threshold (default 0.34):
    TRADE?  fewer than the threshold share of awards map to the assigned trade
    STATE?  fewer than the threshold share are performed in the assigned state

IT IS A REVIEW REPORT, NOT A FILTER. Do not wire it into a suppression path.
A flag is the start of a judgment call, not the end of one. Three worked
examples from the first run, all decided by hand:

  - Victory Contracting (110): 24 awards, 11 NAICS, 14 states, 1 TX award and it
    is electrical. No honest trade claim and no honest state hook exists, so it
    was filed OUT-OF-ICP and never mailed.
  - Ark Construction Management (146): 64 pct roofing vs 14 pct general-building,
    so TRADE? fires. KEPT as general-building anyway, because PA roofing had 0
    open bids and PA general-building had 3, and 14 federal general-building
    awards make the claim true. The flag was right and the fix would have been
    worse than the problem.
  - Construction Support Solutions (152): trade is 97 pct correct but only 13 pct
    of performance is in its home state. Noted for the REGION variant.

The API's recipient_search_text is fuzzy, so a company whose awards do not
name-match prints NO-AWARDS-MATCHED. That is an audit miss, not a finding about
the prospect: verify those by hand rather than treating them as clean.

UEI RESOLUTION (added 2026-09-24). recipient_search_text is fuzzy in the other
direction too: it silently merges same-named but unrelated companies, and a
merged award set looks exactly like a clean one. So before searching by name,
this script asks /api/v2/recipient/ which UEIs actually carry that name.

  - exactly one UEI matches -> search by UEI. The result is the company itself,
    with no name bleed, and the line is marked "uei".
  - several UEIs match -> print AMBIGUOUS-NAME with each UEI and its dollar
    total, audit the largest, and mark the line so nobody reads it as settled.
  - none match -> fall back to the old name search, marked "name".

Worked example, the case that prompted this (Sentinel Power Services, 164):
the name search returned 100 awards that no one could attribute. By UEI the
two firms separate cleanly - VZH2RXQG6QD5 (LLC, $2.9M) against UBMJNJQ7S465
(Inc, $23.5k) - and the 100 awards turned out to be genuinely the LLC's:
100/100 electrical, but spread over 30 states and territories with only 6 pct
in its home AZ. Trade right, state hook false, and the real finding was that a
nationwide FAA electrical prime is not the small-shop ICP at all. Filed
OUT-OF-ICP. Resolving the identity turned an unanswerable flag into a decision.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
from neon_http import Neon  # noqa: E402

API = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
RECIPIENT_API = "https://api.usaspending.gov/api/v2/recipient/"
START = "2021-01-01"

# Same mapping prospect_source.py sources against, inverted.
NAICS_TRADE = {
    "238160": "roofing",
    "238320": "painting",
    "561720": "janitorial",
    "238220": "hvac-plumbing",
    "238210": "electrical",
    "561730": "landscaping",
    "236220": "general-building",
    "238910": "site-work",
}


def norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", "", (s or "").lower())
    for suffix in (" incorporated", " inc", " llc", " lp", " ltd", " corp", " corporation", " company"):
        s = s.replace(suffix, "")
    return " ".join(s.split())


def _post(url: str, body: dict, retries: int = 3):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    for attempt in range(retries):
        try:
            return json.load(urllib.request.urlopen(req, timeout=90))
        except Exception:
            if attempt == retries - 1:
                return None
            time.sleep(4)
    return None


def resolve_ueis(name: str):
    """Which UEIs actually carry this company name, largest first.

    recipient_search_text merges same-named unrelated firms without saying so.
    This is the only cheap way to find out that it happened. Returns a list of
    (uei, registered_name, dollars); an empty list means the lookup found
    nothing usable and the caller should fall back to the name search.
    """
    target = norm(name)
    # The keyword search is close to literal: "Sentinel Power Services LLC" returns
    # one UEI-less stub while "sentinel power services" returns both real firms. So
    # search on the normalized name, then on a shorter fragment if that finds nobody.
    queries = [target]
    words = target.split()
    if len(words) > 2:
        queries.append(" ".join(words[:2]))

    seen: dict[str, tuple[str, float]] = {}
    for q in queries:
        data = _post(RECIPIENT_API, {"keyword": q, "order": "desc", "sort": "amount",
                                     "limit": 25, "page": 1, "award_type": "all"})
        for r in (data or {}).get("results", []):
            uei = r.get("uei")
            if not uei or norm(r.get("name")) != target:
                continue
            amount = float(r.get("amount") or 0)
            # the same UEI comes back once per recipient_level (P/C/R); keep the best
            if uei not in seen or amount > seen[uei][1]:
                seen[uei] = (r.get("name"), amount)
        if seen:
            break

    candidates = sorted(((u, n, a) for u, (n, a) in seen.items()), key=lambda x: -x[2])
    # Most multi-UEI names are ONE company registered twice, not two companies.
    # Two tells, both measured on the wave-8 set: a duplicate registration usually
    # carries $0, and when it does not, the two rows carry the IDENTICAL total
    # (Briston and Raad each showed the same dollar figure under two UEIs). Real
    # ambiguity looks like Sentinel Power Services: $2.9M against $23.5k. So drop
    # the empty registrations, and collapse exact-tie totals onto one entity.
    funded = [c for c in candidates if c[2] > 0]
    if len(funded) > 1 and len({c[2] for c in funded}) == 1:
        funded = funded[:1]
    return funded or candidates[:1]


def fetch_awards(name: str, today: str, retries: int = 3, uei: str | None = None):
    """Awards for a company. With a uei, the match is exact and nothing is filtered."""
    body = {
        "filters": {
            "recipient_search_text": [uei or name],
            "award_type_codes": ["A", "B", "C", "D"],
            "time_period": [{"start_date": START, "end_date": today}],
        },
        "fields": [
            "Recipient Name",
            "naics_code",
            "naics_description",
            "Place of Performance State Code",
        ],
        "limit": 100,
        "page": 1,
    }
    data = _post(API, body, retries)
    if data is None:
        return None
    results = data.get("results", [])
    if uei:
        return results
    target = norm(name)[:18]
    return [a for a in results if norm(a.get("Recipient Name")).startswith(target)]


def audit(rows, threshold: float, today: str, verbose: bool, by_name: bool = False) -> int:
    flagged = 0
    for p in rows:
        company, trade, state = p["company"], p.get("trade"), p.get("state")

        uei, how, ambiguous = p.get("uei"), "uei", False
        if uei:
            pass
        elif by_name:
            how = "name"
        else:
            candidates = resolve_ueis(company)
            if len(candidates) == 1:
                uei = candidates[0][0]
            elif len(candidates) > 1:
                uei, ambiguous = candidates[0][0], True
                print(f"{p['id']:>5} {company[:36]:36s} AMBIGUOUS-NAME, {len(candidates)} UEIs carry it:")
                for u, n, a in candidates:
                    print(f"          {u}  ${a:,.0f}  {n}")
                print(f"          auditing the largest ({uei}); confirm it is the prospect before acting")
            else:
                how = "name"

        awards = fetch_awards(company, today, uei=uei)
        if awards is None:
            print(f"{p['id']:>5} {company[:36]:36s} API-ERROR")
            continue
        if not awards:
            print(f"{p['id']:>5} {company[:36]:36s} NO-AWARDS-MATCHED (verify by hand)")
            continue
        n = len(awards)
        naics = Counter(a.get("naics_code") for a in awards)
        states = Counter(a.get("Place of Performance State Code") for a in awards)
        trade_share = sum(v for k, v in naics.items() if NAICS_TRADE.get(k) == trade) / n
        state_share = states.get(state, 0) / n
        flags = ""
        if trade_share < threshold:
            flags += " TRADE?"
        if state_share < threshold:
            flags += " STATE?"
        if ambiguous:
            flags += " AMBIGUOUS"
        if flags:
            flagged += 1
        top_code, top_n = naics.most_common(1)[0]
        top_trade = NAICS_TRADE.get(top_code, "not a BidScout trade")
        print(
            f"{p['id']:>5} {company[:36]:36s} n={n:>3} {str(trade)[:15]:15s} "
            f"trade={trade_share:4.0%}  {state} pop={state_share:4.0%}  "
            f"top={top_code} ({top_trade}) [{how}]{flags}"
        )
        if verbose and flags:
            for (code), cnt in naics.most_common(5):
                print(f"          {cnt:>3} {code} -> {NAICS_TRADE.get(code, 'not a BidScout trade')}")
            print(f"          states: {dict(states.most_common(5))}")
    return flagged


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="audit every emailable prospect with this prospects.source")
    ap.add_argument("--ids", help="comma-separated prospect ids")
    ap.add_argument("--company", help="audit one company by name, no DB lookup")
    ap.add_argument("--uei", help="with --company, pin the audit to this exact UEI")
    ap.add_argument("--resolve", help="just list the UEIs carrying this company name, then exit")
    ap.add_argument("--by-name", action="store_true",
                    help="skip UEI resolution and use the old fuzzy name search")
    ap.add_argument("--threshold", type=float, default=0.34)
    ap.add_argument("--verbose", action="store_true", help="print the NAICS and state mix for flagged rows")
    args = ap.parse_args()

    today = time.strftime("%Y-%m-%d")

    if args.resolve:
        candidates = resolve_ueis(args.resolve)
        if not candidates:
            print("no UEI carries that exact name; the name search is all there is")
            return 1
        for u, n, a in candidates:
            print(f"{u}  ${a:,.0f}  {n}")
        return 0

    if args.company:
        rows = [{"id": 0, "company": args.company, "trade": None, "state": None, "uei": args.uei}]
    else:
        db = Neon(os.environ["DATABASE_URL"])
        if args.ids:
            ids = ",".join(str(int(i)) for i in args.ids.split(","))
            sql = f"select id,company,trade,state from prospects where id in ({ids}) order by id"
            rows = db.query(sql, [])
        elif args.source:
            rows = db.query(
                "select id,company,trade,state from prospects "
                "where source = $1 and email is not null and outreach_channel = 'EMAIL' order by id",
                [args.source],
            )
        else:
            ap.error("give one of --source, --ids or --company")
        if not rows:
            print("no prospects matched", file=sys.stderr)
            return 1

    print(f"auditing {len(rows)} prospect(s), threshold {args.threshold:.0%}\n")
    flagged = audit(rows, args.threshold, today, args.verbose, by_name=args.by_name)
    print(f"\n{flagged} flagged of {len(rows)}. Review by hand; this is not a filter.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
