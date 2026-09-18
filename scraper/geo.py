"""Infer a US state for a bid when SAM.gov's placeOfPerformance has no state code.

About a third of open notices arrive with an empty placeOfPerformance (or one
carrying only a ZIP). A bid with no state is invisible to state-filtered
digests, to /bids/{state}/{trade}/ pages, and to the in-state hook in outreach.
This module recovers a state, in decreasing order of confidence:

    1. ZIP code in placeOfPerformance -> state (deterministic 3-digit prefix table)
    2. "City, ST 12345" style address in the title/description (ST + 5-digit ZIP
       is unambiguous)
    3. Interior/FWS "ST-Site" title tag ("WY-JACKSON NFH-..."), gated on those
       agencies and a blocklist of codes that double as English prefixes.
    4. Exactly one distinct state spelled out in full in the TITLE alone (the
       description often adds the contracting office's state and would spoil it).
    5. Exactly one distinct state spelled out in full in the text ("in Kentucky",
       "Fort Bragg, North Carolina") — skipped when two or more different states
       are named, and "Washington" alone is never treated as WA/DC.

Each result carries a `method` so it can be audited; the scraper stores the
state but not the method. Anything below these bars stays NULL — a wrong state
is worse than none because the outreach copy promises "near you".
"""

from __future__ import annotations

import os
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
    r"(?!\s+(?:river|lake|city|valley|region|basin|delta|ave\b|avenue|st\b|street|road|rd\b|blvd|boulevard|hwy|highway|creek|bay|beach\b)\b)",
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


def _address(text: str) -> Inferred | None:
    # "City, ST 12345" — require the two-letter code to agree with the ZIP.
    for m in _ADDR_RE.finditer(text):
        st, z = m.group(2), m.group(3)
        if st in STATE_CODES and state_from_zip(z) == st:
            return Inferred(st, "address")
    return None


def _single_name(text: str, method: str = "name") -> Inferred | None:
    # Full state names: accept only if exactly one distinct state is named.
    # Ignore boilerplate about where the buying office lives ("DLA Disposition
    # Services Headquarters is in Battle Creek, Michigan") — that's not the
    # place of performance.
    found = {
        STATE_NAMES[m.group(1).lower()]
        for m in _NAME_RE.finditer(text)
        if "headquarter" not in text[max(0, m.start() - 80) : m.start()].lower()
    }
    if len(found) == 1:
        return Inferred(found.pop(), method)
    return None


def state_from_text(text: str | None) -> Inferred | None:
    if not text:
        return None
    return _address(text) or _single_name(text)


# Interior/FWS titles routinely lead with a state tag: "WY-JACKSON NFH-...",
# "S--WY-ALCOVA North Platte Area Office...", "S--R4-PR-Cabo Rojo NWR-...".
# Optional PSC letter prefix ("S--"), optional FWS region ("R4-"), then the
# two-letter code, hyphen, and an uppercase site name.
_TITLE_PREFIX_RE = re.compile(r"^\s*(?:[A-Z]--)?(?:R\d{1,2}-)?([A-Z]{2})-(?=[A-Z])")
# Codes whose "XX-" reading is more plausibly an English prefix than a state
# tag (IN-HOUSE, CO-OP, DE-ICING, LA-JOLLA, MS-DOS, AL-..., HI-TECH, ID-...).
# A wrong state is worse than none, so these never match by prefix.
_PREFIX_UNSAFE = {"IN", "CO", "DE", "LA", "MS", "AL", "HI", "ID"}
_PREFIX_AGENCIES = ("INTERIOR", "FISH AND WILDLIFE", "FWS")


def state_from_title_prefix(title: str | None, agency: str | None) -> Inferred | None:
    """The Interior/FWS "ST-Site" title convention, gated on those agencies."""
    if not title or not agency:
        return None
    a = agency.upper()
    if not any(tok in a for tok in _PREFIX_AGENCIES):
        return None
    m = _TITLE_PREFIX_RE.match(title)
    if m and m.group(1) in STATE_CODES and m.group(1) not in _PREFIX_UNSAFE:
        return Inferred(m.group(1), "title-prefix")
    return None


# --- place-of-performance city corroboration -------------------------------
# data/city_states.tsv: "CITY NAME<TAB>ST,ST,..." built from the 2023 Census
# Gazetteer (places + county subdivisions + counties, consolidated city-county
# names also indexed by their first token) plus the five NYC boroughs.
#
# IMPORTANT, learned the hard way 2026-09-18: this map CANNOT be used to refute
# a state in general. Run against the whole bids table it flags 64 rows and
# roughly 63 of them are correct: military installations named after a place in
# another state (Fort Bragg NC, Edwards CA, McClellan CA, Santa Rita GU) and
# unincorporated places the Gazetteer does not carry (Hines IL, Leeds MA,
# Chatsworth NJ). It is only sound in the prose-fallback branch of
# infer_state(), where SAM gave us a city, gave us no structured state, and the
# alternative is a state scraped out of free text.
_CITY_STATES: dict[str, frozenset[str]] | None = None


def _city_states() -> dict[str, frozenset[str]]:
    global _CITY_STATES
    if _CITY_STATES is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "city_states.tsv")
        m: dict[str, frozenset[str]] = {}
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    name, _, codes = line.rstrip("\n").partition("\t")
                    if name and codes:
                        m[name] = frozenset(codes.split(","))
        except OSError:
            m = {}
        _CITY_STATES = m
    return _CITY_STATES


def states_for_city(city: str | None) -> frozenset[str] | None:
    """States that have a place by this name, or None if the name is unknown."""
    if not city:
        return None
    key = re.sub(r"\s+", " ", city.strip()).upper()
    return _city_states().get(key)


def infer_state(pop: dict | None, *texts: str | None, agency: str | None = None) -> Inferred | None:
    """Best-effort state for a notice. `pop` is SAM's placeOfPerformance dict.

    `texts` should be (title, description); the title, when it names exactly one
    state, wins over the joined text, because descriptions often name the
    contracting office's state too ("ACC-NJ at Fort Dix, on behalf of ... in
    Western Pennsylvania" — the title says only Pennsylvania).
    """
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
    title = texts[0] if texts else None
    joined = "\n".join(t for t in texts if t)
    guess = (
        (_address(joined) if joined else None)          # ST+ZIP agreement, deterministic
        or state_from_title_prefix(title, agency)       # Interior/FWS "ST-Site" tag
        or (_single_name(title, "title-name") if title else None)  # one state in the title
        or (_single_name(joined) if joined else None)   # one state anywhere
    )
    # SAM told us the city but not the state. A state pulled out of prose is
    # unanchored, and prose in a multi-state office's notice routinely names the
    # wrong one: VA Network Contract Office 16 posted "TRIENNIAL ELECTRICAL
    # MAINTENANCE FY26" with city New Orleans and no state code, and the text
    # fallback read it as TX. It was one edit away from being featured to a
    # Texas roofer as "one in your state right now". So when a city is present,
    # the city arbitrates.
    city = (pop.get("city") or {}).get("name") if isinstance(pop.get("city"), dict) else pop.get("city")
    known = states_for_city(city if isinstance(city, str) else None)
    if known is not None:
        if len(known) == 1:
            # Unambiguous place name. Trust it over the prose either way.
            return Inferred(next(iter(known)), "city")
        if guess and guess.state in known:
            return Inferred(guess.state, guess.method + "+city")
        return None  # prose contradicts the city; a wrong state is worse than none
    return guess
