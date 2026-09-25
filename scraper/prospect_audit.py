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


# Legal forms, longest first, stripped only from the END of a name and stripped
# repeatedly. Measured 2026-09-25 on Calvin L Wadsworth, which USAspending
# registers as "CALVIN L WADSWORTH CONSTRUCTION COMPANY LIMITED LIABILITY
# COMPANY": the old version replaced " company" ANYWHERE in the string, so the
# word in the middle of the trading name disappeared too and the normalized name
# matched nothing. Trailing-only and repeated handles the stacked forms.
LEGAL_FORMS = (
    "limited liability company", "limited liability", "incorporated", "corporation",
    "company", "corp", "inc", "llc", "lp", "ltd", "co",
)


def base_name(name: str) -> str:
    """The registered name without a parenthetical DBA.

    Hunting notes often land in the company field as "Apex Business Solutions
    LLC (Apex Construction)". USAspending registers the part before the bracket,
    and both the award search and the recipient lookup return nothing for the
    whole string, so every such prospect resolved to no UEI at all.
    """
    return " ".join(re.sub(r"\s*\([^)]*\)", " ", name or "").split())


def strip_legal(name: str) -> str:
    """The name with trailing legal forms removed but its punctuation intact.

    norm() is for COMPARING names and throws away the ampersands and periods a
    search still needs: "A & B Mechanical Inc" normalizes to "a b mechanical",
    which USAspending matches nothing at all, while "A & B Mechanical" returns
    the company's own award. So searches retry on this, comparisons use norm().
    """
    s = (name or "").strip()
    changed = True
    while changed:
        changed = False
        for form in LEGAL_FORMS:
            m = re.search(r"[,\s]+" + form.replace(" ", r"\s+") + r"\.?$", s, re.I)
            if m:
                s, changed = s[: m.start()].strip(" ,."), True
                break
    return s


def norm(s: str) -> str:
    s = " ".join(re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).split())
    changed = True
    while changed:
        changed = False
        for form in LEGAL_FORMS:
            if s.endswith(" " + form):
                s, changed = s[: -len(form) - 1].strip(), True
                break
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
    target = norm(base_name(name))
    # The keyword search is close to literal: "Sentinel Power Services LLC" returns
    # one UEI-less stub while "sentinel power services" returns both real firms. So
    # search on the normalized name, then on a shorter fragment if that finds nobody.
    queries = [target]
    for v in name_variants(name):  # keeps the "&", periods and hyphens norm() drops
        if v.lower() != target and norm(v) == target:
            queries.append(v)
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
    # If NOTHING funded carries the name, the only matches are registration stubs.
    # Returning one looks like a clean resolution and then guarantees an empty
    # award search, which reads as "no awards" rather than "wrong identifier".
    # Measured on Calvin L Wadsworth (139): a $0 UEI resolved, the by-UEI search
    # returned nothing, and the company's two real awards sit under a longer
    # registered name. Prefer the name search when every candidate is empty.
    return funded


def name_variants(name: str) -> list[str]:
    """Search spellings to try, most faithful first.

    Every entry here is a measured failure of the near-literal search, not a
    guess: the legal form ("A & B Mechanical Inc" -> nothing, "A & B Mechanical"
    -> the company) and the apostrophe, which USAspending drops from the stored
    name so that "Maguire-O'Hara Construction" returns zero awards while
    "Maguire-OHara Construction" returns 92. Duplicates are removed in order.
    """
    out = []
    for stem in (name.strip(), base_name(name)):
        shorter = strip_legal(stem)
        for v in (stem, shorter, shorter.replace("'", "").replace("’", "")):
            if v and v not in out:
                out.append(v)
    return out


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
    if uei:
        data = _post(API, body, retries)
        return None if data is None else data.get("results", [])

    # By name the search is near-literal, so try the measured spellings in turn
    # and keep the first that matches the company. Filtering on the normalized
    # recipient name is what keeps a broader spelling from pulling in strangers.
    target = norm(base_name(name))[:18]
    failed = False
    for variant in name_variants(name):
        body["filters"]["recipient_search_text"] = [variant]
        data = _post(API, body, retries)
        if data is None:
            failed = True
            continue
        matched = [a for a in data.get("results", []) if norm(a.get("Recipient Name")).startswith(target)]
        if matched:
            return matched
    return None if failed else []


def store_uei(db, prospect_id: int, uei: str | None, status: str) -> None:
    """Remember a resolution so the next audit does not pay for it again.

    Writing 'none' matters as much as writing a UEI: without it every later run
    re-resolves the names that are never going to resolve. uei_resolved_at is
    what makes a stale 'none' retryable (prospect_audit.py --backfill --refresh).
    """
    if db is None or not prospect_id:
        return
    try:
        db.execute(
            "update prospects set uei = $1, uei_status = $2, uei_resolved_at = now() where id = $3",
            [uei, status, prospect_id],
        )
    except Exception as exc:  # a CRM write must never take down a review report
        print(f"          (could not store uei for {prospect_id}: {exc})")


def resolve_for(p, db=None, refresh: bool = False):
    """Resolve one prospect's UEI, preferring a stored answer. -> (uei, status, candidates)"""
    if not refresh and p.get("uei"):
        return p["uei"], p.get("uei_status") or "resolved", []
    if not refresh and p.get("uei_status") == "none":
        return None, "none", []
    candidates = resolve_ueis(p["company"])
    if len(candidates) == 1:
        status, uei = "resolved", candidates[0][0]
    elif len(candidates) > 1:
        status, uei = "ambiguous", candidates[0][0]
    else:
        status, uei = "none", None
    store_uei(db, p.get("id") or 0, uei, status)
    return uei, status, candidates


def audit(rows, threshold: float, today: str, verbose: bool, by_name: bool = False,
          db=None, refresh: bool = False) -> int:
    flagged = 0
    for p in rows:
        company, trade, state = p["company"], p.get("trade"), p.get("state")

        uei, how, ambiguous = p.get("uei"), "uei:db", False
        if by_name:  # an explicit ask for the old fuzzy search beats a stored id
            uei, how = None, "name"
        elif uei and not refresh:
            ambiguous = p.get("uei_status") == "ambiguous"
            if ambiguous:
                print(f"{p['id']:>5} {company[:36]:36s} stored UEI {uei} is the largest of several; "
                      f"confirm it is the prospect before acting")
        else:
            how = "uei"
            uei, status, candidates = resolve_for(p, db=db, refresh=refresh)
            if status == "ambiguous":
                ambiguous = True
                print(f"{p['id']:>5} {company[:36]:36s} AMBIGUOUS-NAME, {len(candidates)} UEIs carry it:")
                for u, n, a in candidates:
                    print(f"          {u}  ${a:,.0f}  {n}")
                print(f"          auditing the largest ({uei}); confirm it is the prospect before acting")
            elif status == "none":
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


def backfill(rows, db, refresh: bool = False, limit: int = 0) -> int:
    """Resolve and store UEIs without auditing awards.

    Identity resolution is the slow, repeated half of an audit and it does not
    go stale the way an award mix does, so it is worth doing once for the whole
    table and keeping. Prints one line per row it actually resolved; rows that
    already carry an answer are counted and skipped.
    """
    done = Counter()
    for p in rows:
        if limit and done["resolved"] + done["ambiguous"] + done["none"] >= limit:
            break
        if not refresh and (p.get("uei") or p.get("uei_status")):
            done["already"] += 1
            continue
        uei, status, candidates = resolve_for(p, db=db, refresh=True)
        done[status] += 1
        extra = f"  ({len(candidates)} UEIs carry the name, kept the largest)" if status == "ambiguous" else ""
        print(f"{p['id']:>5} {p['company'][:40]:40s} {status:9s} {uei or '-'}{extra}")
    print(f"\nresolved {done['resolved']}, ambiguous {done['ambiguous']}, "
          f"none {done['none']}, already stored {done['already']}")
    if done["ambiguous"]:
        print("ambiguous rows keep the largest UEI and are marked as such: confirm before acting on one.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="audit every emailable prospect with this prospects.source")
    ap.add_argument("--ids", help="comma-separated prospect ids")
    ap.add_argument("--company", help="audit one company by name, no DB lookup")
    ap.add_argument("--uei", help="with --company, pin the audit to this exact UEI")
    ap.add_argument("--resolve", help="just list the UEIs carrying this company name, then exit")
    ap.add_argument("--by-name", action="store_true",
                    help="skip UEI resolution and use the old fuzzy name search")
    ap.add_argument("--backfill", action="store_true",
                    help="resolve and store UEIs for the selected prospects, no award audit")
    ap.add_argument("--all", action="store_true",
                    help="with --backfill, select every prospect rather than one source or id list")
    ap.add_argument("--refresh", action="store_true",
                    help="re-resolve even prospects that already carry a stored UEI or a stored 'none'")
    ap.add_argument("--limit", type=int, default=0, help="with --backfill, stop after this many rows")
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

    cols = "id,company,trade,state,uei,uei_status"
    db = None
    if args.company:
        rows = [{"id": 0, "company": args.company, "trade": None, "state": None, "uei": args.uei}]
    else:
        db = Neon(os.environ["DATABASE_URL"])
        if args.ids:
            ids = ",".join(str(int(i)) for i in args.ids.split(","))
            rows = db.query(f"select {cols} from prospects where id in ({ids}) order by id", [])
        elif args.source:
            rows = db.query(
                f"select {cols} from prospects "
                "where source = $1 and email is not null and outreach_channel = 'EMAIL' order by id",
                [args.source],
            )
        elif args.all and args.backfill:
            rows = db.query(f"select {cols} from prospects order by id", [])
        else:
            ap.error("give one of --source, --ids or --company (or --backfill --all)")
        if not rows:
            print("no prospects matched", file=sys.stderr)
            return 1

    if args.backfill:
        return backfill(rows, db, refresh=args.refresh, limit=args.limit)

    print(f"auditing {len(rows)} prospect(s), threshold {args.threshold:.0%}\n")
    flagged = audit(rows, args.threshold, today, args.verbose, by_name=args.by_name,
                    db=db, refresh=args.refresh)
    print(f"\n{flagged} flagged of {len(rows)}. Review by hand; this is not a filter.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
