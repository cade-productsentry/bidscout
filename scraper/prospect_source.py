"""Source prospect candidates from the USAspending API.

Finds small businesses that recently won small federal contracts in
BidScout's trade NAICS codes. Prints candidate companies (public
procurement data only). Email hunting and MX checks happen elsewhere;
results are stored in the private Neon prospects table, never in this
public repo.

Usage:
    python scraper/prospect_source.py [--naics 238160,561720] [--months 18] [--limit 60]
"""
import argparse
import json
import re
import time
import urllib.request
from datetime import date, timedelta

API = "https://api.usaspending.gov/api/v2/search/spending_by_award/"

# Hard server-side cap on the API's per-request "limit" field.
PAGE_MAX = 100

TRADE_NAICS = {
    "236220": "general-building",
    "238160": "roofing",
    "238210": "electrical",
    "238220": "hvac-plumbing",
    "238320": "painting",
    "238910": "site-work",
    "561720": "janitorial",
    "561730": "landscaping",
}


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def fetch_candidates(naics_codes, months, limit):
    start = (date.today() - timedelta(days=months * 30)).isoformat()
    end = date.today().isoformat()
    seen, out = set(), []
    for naics in naics_codes:
        trade = TRADE_NAICS.get(naics, naics)
        got, page = 0, 1
        # USAspending rejects limit > PAGE_MAX with HTTP 422, so a --limit
        # above it used to return zero rows for every NAICS with no clue why.
        # Page through instead.
        while got < limit:
            body = {
                "filters": {
                    "time_period": [{"start_date": start, "end_date": end}],
                    "award_type_codes": ["A", "B", "C", "D"],
                    "naics_codes": [naics],
                    "award_amounts": [
                        {"lower_bound": 25000, "upper_bound": 4000000}],
                    "recipient_type_names": ["small_business"],
                },
                "fields": ["Award ID", "Recipient Name", "Recipient Location",
                           "Award Amount", "Description", "Start Date"],
                "sort": "Start Date", "order": "desc",
                "limit": min(PAGE_MAX, limit - got), "page": page,
            }
            req = urllib.request.Request(
                API, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "User-Agent": "bidscout-research"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    res = json.load(r)
            except Exception as exc:  # noqa: BLE001 - log and continue
                print(f"# {naics} page {page} ERROR {exc}")
                break
            results = res.get("results", [])
            if not results:
                break
            for row in results:
                name = (row.get("Recipient Name") or "").title().strip()
                if not name or norm(name) in seen:
                    continue
                seen.add(norm(name))
                loc = row.get("Recipient Location") or {}
                out.append({
                    "company": name,
                    "trade": trade,
                    "state": loc.get("state_code"),
                    "city": (loc.get("city_name") or "").title(),
                    "amount": row.get("Award Amount"),
                    "desc": (row.get("Description") or "")[:100],
                })
            got += len(results)
            page += 1
            if not res.get("page_metadata", {}).get("hasNext", len(results) == PAGE_MAX):
                break
            time.sleep(1)
        time.sleep(1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--naics", default=",".join(TRADE_NAICS))
    ap.add_argument("--months", type=int, default=18)
    ap.add_argument("--limit", type=int, default=60)
    args = ap.parse_args()
    cands = fetch_candidates(args.naics.split(","), args.months, args.limit)
    print(json.dumps(cands, indent=1))
    print(f"# {len(cands)} candidates")


if __name__ == "__main__":
    main()
