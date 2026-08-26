"""Infer a US state for a bid when SAM.gov's placeOfPerformance has no state code.

About a third of open notices arrive with an empty placeOfPerformance (or one
carrying only a ZIP). A bid with no state is invisible to state-filtered
digests, to /bids/{state}/{trade}/ pages, and to the in-state hook in outreach.
This module recovers a state, in decreasing order of confidence:

    1. ZIP code in placeOfPerformance -> state (deterministic 3-digit prefix table)
    2. "City, ST 12345" style address in the title/description (ST + 5-digit ZIP
       is unambiguous)
    3. Exactly one distinct state spelled out in full in the text ("in Kentucky",
       "Fort Bragg, North Carolina") — skipped when two or more different states
       are named, and "Washington" alone is never treated as WA/DC.

Each result carries a `method` so it can be audited; the scraper stores the
state but not the method. Anything below these bars stays NULL — a wrong state
is worse than none because the outreach copy promises "near you".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 3-digit ZIP prefix ranges -> USPS state code. Military (AA/AE/AP) and
# territories other than PR/VI/GU are left out on purpose.
_ZIP3_RANGES: list[tuple[int, int, str]] = [
    (5, 5, "NY"), (6, 9, "PR"),
    (10, 27, "MA"), (28, 29, "RI"), (30, 38, "NH"), (39, 49, "ME"), (50, 59, "VT"),
    (60, 69, "CT"), (70, 89, "NJ"), (100, 149, "NY"), (150, 196, "PA"), (197, 199, "DE"),
    (200, 205, "DC"), (206, 219, "MD"), (220, 246, "VA"), (247, 268, "WV"),
    (270, 289, "NC"), (290, 299, "SC"), (300, 319, "GA"), (320, 339, "FL"), (341, 349, "FL"),
    (350, 369, "AL"), (370, 385, "TN"), (386, 397, "MS"), (398, 399, "GA"),
    (400, 427, "KY"), (430, 459, "OH"), (460, 479, "IN"), (480, 499, "MI"),
    (500, 528, "IA"), (530, 549, "WI"), (550, 567, "MN"), (569, 569, "DC"),
    (570, 577, "SD"), (580, 588, "ND"), (590, 599, "MT"),
    (600, 629, "IL"), (630, 658, "MO"), (660, 679, "KS"), (680, 693, "NE"),
    (700, 714, "LA"), (716, 729, "AR"), (730, 732, "OK"), (733, 733, "TX"), (734, 749, "OK"),
    (750, 799, "TX"), (800, 816, "CO"), (820, 831, "WY"), (832, 838, "ID"),
    (840, 847, "UT"), (850, 865, "AZ"), (870, 884, "NM"), (885, 885, "TX"),
    (889, 898, "NV"), (900, 961, "CA"), (967, 968, "HI"), (969, 969, "GU"),
    (970, 979, "OR"), (980, 994, "WA"), (995, 999, "AK"),
]

STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
    "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
    "VT", "VA", "WA", "WV", "WI", "WY", "PR", "GU", "VI",
}

STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY", "puerto rico": "PR", "guam": "GU",
    # "washington" is deliberately absent: it is a state, a city, and a surname.
}

# "Norfolk, VA 23511" / "Fort Bragg NC 28310" / "Anchorage, AK  99506-1234"
_ADDR_RE = re.compile(r"\b([A-Z][A-Za-z.' -]{1,40}?),?\s+([A-Z]{2})\s+(\d{5})(?:-\d{4})?\b")
# Full state names, longest first so "West Virginia" beats "Virginia". A name
# followed by River/Lake/City/Avenue/... is a place named after a state, not the
# state ("Mississippi River Pool 3" is in Minnesota; "Kansas City" may be MO).
_NAME_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(n) for n in STATE_NAMES), key=len, reverse=True)) + r")\b"
    r"(?!\s+(?:river|lake|city|valley|ave\b|avenue|st\b|street|road|rd\b|blvd|boulevard|hwy|highway|creek|bay|beach\b)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Inferred:
    state: str
    method: str  # "zip" | "address" | "name"


def state_from_zip(zip_code: str | None) -> str | None:
    digits = re.sub(r"\D", "", zip_code or "")
    if len(digits) < 3:
        return None
    if len(digits) == 4:  # a leading zero dropped somewhere upstream
        digits = "0" + digits
    prefix = int(digits[:3])
    for lo, hi, st in _ZIP3_RANGES:
        if lo <= prefix <= hi:
            return st
    return None


def state_from_text(text: str | None) -> Inferred | None:
    if not text:
        return None
    # 1) "City, ST 12345" — require the two-letter code to agree with the ZIP.
    for m in _ADDR_RE.finditer(text):
        st, z = m.group(2), m.group(3)
        if st in STATE_CODES and state_from_zip(z) == st:
            return Inferred(st, "address")
    # 2) Full state names: accept only if exactly one distinct state is named.
    #    Ignore boilerplate about where the buying office lives ("DLA Disposition
    #    Services Headquarters is in Battle Creek, Michigan") — that's not the
    #    place of performance.
    found = {
        STATE_NAMES[m.group(1).lower()]
        for m in _NAME_RE.finditer(text)
        if "headquarter" not in text[max(0, m.start() - 80) : m.start()].lower()
    }
    if len(found) == 1:
        return Inferred(found.pop(), "name")
    return None


def infer_state(pop: dict | None, *texts: str | None) -> Inferred | None:
    """Best-effort state for a notice. `pop` is SAM's placeOfPerformance dict."""
    pop = pop or {}
    country = (pop.get("country") or {}).get("code") if isinstance(pop.get("country"), dict) else None
    if country and country.upper() not in ("US", "USA"):
        # Overseas work (embassies, Okinawa, Bogotá...). SAM puts ISO 3166-2
        # codes like "JP-47" in state.code; never a US state, and the text
        # fallbacks would only pick up the stateside contracting office.
        return None
    code = (pop.get("state") or {}).get("code") if isinstance(pop.get("state"), dict) else None
    if code and code.upper() in STATE_CODES:
        return Inferred(code.upper(), "pop")
    if code and "-" in code:
        return None  # foreign subdivision code with no country block
    st = state_from_zip(pop.get("zip"))
    if st:
        return Inferred(st, "zip")
    return state_from_text("\n".join(t for t in texts if t))
