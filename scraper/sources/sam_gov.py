"""SAM.gov (federal) opportunity source.

Uses the same public JSON endpoints the sam.gov web UI calls. No API key is
required. Two calls per run per NAICS code:

  1. search:  /api/prod/sgs/v1/search/?index=opp&naics=...   (list, paged)
  2. detail:  /api/prod/opps/v2/opportunities/{id}            (place of
     performance, set-aside, NAICS, point of contact, description)

Only real solicitations are kept (notice types: combined synopsis/solicitation,
solicitation, presolicitation, sources sought, special notice). Award notices
are skipped because nobody can bid on them.

Be polite: small page sizes, short sleeps, and a detail fetch only for notices
we have not stored yet.
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

SEARCH_URL = "https://sam.gov/api/prod/sgs/v1/search/"
DETAIL_URL = "https://sam.gov/api/prod/opps/v2/opportunities/{id}"
VIEW_URL = "https://sam.gov/opp/{id}/view"
UA = "Mozilla/5.0 (compatible; BidScout/1.0; +https://bidscout.pages.dev)"

# NAICS -> BidScout trade slug. Keep this list short and high-signal: small
# facility-services contractors are the target customer.
TRADES: dict[str, str] = {
    "238220": "hvac-plumbing",
    "238210": "electrical",
    "238160": "roofing",
    "238320": "painting",
    "238990": "site-work",        # paving, fencing, misc specialty
    "561730": "landscaping",
    "561720": "janitorial",
    "236220": "general-building",
}

# Notice types that can still be bid on.
NOTICE_TYPES = {
    "k": "Combined Synopsis/Solicitation",
    "o": "Solicitation",
    "p": "Presolicitation",
    "r": "Sources Sought",
    "s": "Special Notice",
}


@dataclass
class Bid:
    source: str
    source_id: str
    url: str
    title: str
    agency: str | None
    trade: str | None
    naics: str | None
    state: str | None
    county: str | None
    city: str | None
    notice_type: str | None
    set_aside: str | None
    posted_at: str | None
    due_at: str | None
    poc_email: str | None
    raw_text: str | None


def _get_json(url: str, retries: int = 3) -> dict:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/hal+json, */*"})
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001 - network flakiness is expected
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"<br\s*/?>|</p>|</li>|</div>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()[:20000] or None


def _org_name(result: dict) -> str | None:
    # Level 1 is the department (e.g. DEPT OF DEFENSE); the deepest level is the
    # contracting office. "Dept / Office" reads well in a digest.
    levels = sorted(result.get("organizationHierarchy") or [], key=lambda o: o.get("level", 0))
    if not levels:
        return None
    top = levels[0].get("name")
    leaf = levels[-1].get("name")
    if top and leaf and top != leaf:
        return f"{top} / {leaf}"
    return top or leaf


def search(naics: str, since: datetime, page_size: int = 25, max_pages: int = 8) -> Iterator[dict]:
    """Yield search results for one NAICS code modified since `since`."""
    for page in range(max_pages):
        params = {
            "index": "opp",
            "page": page,
            "size": page_size,
            "mode": "search",
            "is_active": "true",
            "naics": naics,
            "notice_type": ",".join(NOTICE_TYPES),
            "sort": "-modifiedDate",
        }
        data = _get_json(SEARCH_URL + "?" + urllib.parse.urlencode(params))
        results = (data.get("_embedded") or {}).get("results") or []
        if not results:
            return
        for r in results:
            modified = r.get("modifiedDate") or r.get("publishDate")
            if modified and datetime.fromisoformat(modified.replace("Z", "+00:00")) < since:
                return  # sorted by -modifiedDate, so everything after is older
            yield r
        if page + 1 >= data.get("page", {}).get("totalPages", 0):
            return
        time.sleep(0.5)


def detail(opp_id: str) -> dict:
    return _get_json(DETAIL_URL.format(id=opp_id))


def to_bid(result: dict, det: dict) -> Bid:
    d2 = det.get("data2") or det.get("data") or {}
    pop = d2.get("placeOfPerformance") or {}
    naics_codes = [c for n in (d2.get("naics") or []) for c in (n.get("code") or [])]
    primary_naics = next((c for n in (d2.get("naics") or []) if n.get("type") == "primary" for c in n.get("code") or []), None)
    naics = primary_naics or (naics_codes[0] if naics_codes else None)
    trade = TRADES.get(naics or "")
    if trade is None:
        for code in naics_codes:
            if code in TRADES:
                trade = TRADES[code]
                break
    pocs = d2.get("pointOfContact") or []
    poc_email = next((p.get("email") for p in pocs if p.get("email")), None)
    sol = d2.get("solicitation") or {}
    due = (sol.get("deadlines") or {}).get("response") or result.get("responseDateActual") or result.get("responseDate")
    desc_parts = [d.get("body") for d in (det.get("description") or []) if d.get("body")]
    notice_code = (result.get("type") or {}).get("code") or d2.get("type")
    state = (pop.get("state") or {}).get("code")
    return Bid(
        source="sam.gov",
        source_id=result["_id"],
        url=VIEW_URL.format(id=result["_id"]),
        title=(result.get("title") or d2.get("title") or "").strip()[:500],
        agency=_org_name(result),
        trade=trade,
        naics=naics,
        state=state.upper() if state else None,
        county=None,
        city=(pop.get("city") or {}).get("name"),
        notice_type=NOTICE_TYPES.get(notice_code or "", notice_code),
        set_aside=sol.get("setAside") or None,
        posted_at=result.get("publishDate") or det.get("postedDate"),
        due_at=due,
        poc_email=poc_email,
        raw_text=_strip_html("\n".join(desc_parts)),
    )


def fetch(since_days: int = 2, known_ids: set[str] | None = None, limit: int | None = None) -> Iterator[Bid]:
    """Yield new/updated bids across all tracked NAICS codes."""
    known_ids = known_ids or set()
    since = datetime.now(timezone.utc) - timedelta(days=since_days)
    seen: set[str] = set()
    count = 0
    for naics in TRADES:
        for r in search(naics, since):
            opp_id = r.get("_id")
            if not opp_id or opp_id in seen:
                continue
            seen.add(opp_id)
            if opp_id in known_ids:
                continue
            try:
                det = detail(opp_id)
            except RuntimeError as exc:
                print(f"  skip {opp_id}: {exc}")
                continue
            yield to_bid(r, det)
            count += 1
            if limit and count >= limit:
                return
            time.sleep(0.3)
