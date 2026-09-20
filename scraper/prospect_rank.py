"""Rank raw USAspending candidates into a hunting shortlist.

prospect_source.py answers "who recently won small federal work in our trades".
This answers the next question: "which of them are worth spending hunting time
on". Hunting a company's website for a published email is the slow step in the
outreach pipeline (roughly one subagent-minute each), so the ordering here is
what decides whether that time produces mailable addresses or noise.

It used to live in an operator's scratch directory and get retyped each session.
That cost us: on 2026-09-19 a joint venture with "Jv," in the middle of its name
slipped through a filter that only looked for " JV" at the end, and had to be
dropped by hand. Keeping the rules in the repo means a fix stays fixed.

Usage:
    python scraper/prospect_source.py --limit 300 > cands.txt
    DATABASE_URL=... python scraper/prospect_rank.py --in cands.txt --out ranked.json

The input is prospect_source.py's stdout verbatim; the trailing "# N candidates"
comment is stripped here so the two can be piped together.

Ranking, in order:
  1. Drop anyone already in the prospects table, matched on a normalized name
     with legal suffixes stripped, so "Dot Construction, Inc" and "Dot
     Construction" collapse to one.
  2. Drop names that are not the ICP (joint ventures, holding companies,
     consultancies, staffing firms, bare domain names, and anything with no
     trade word in it at all).
  3. Score by whether the company's own (trade, state) has open bids in
     bids_current RIGHT NOW. A live in-state hook is what makes a first touch
     specific rather than generic, and it roughly doubled the reply-worthy
     hit rate when it was introduced on 2026-09-17.
  4. Break ties on national depth in that trade, then on award size.
  5. Cap at 2 per (trade, state) so a wave is not all one state.

Output is a JSON array ready to hand to the hunting step. No contact data is
produced or stored here; this file is safe for the public repo.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from neon_http import Neon  # noqa: E402

# Legal suffixes stripped before comparing two company names.
SUFFIXES = (
    "llc", "inc", "corp", "co", "ltd", "lp", "llp", "pc",
    "incorporated", "company", "corporation",
)

# Spelled-out legal forms, stripped from the end before the single-word pass.
# "Calvin L Wadsworth Construction Company Limited Liability Company" and
# "Calvin L Wadsworth Construction Company" have to collapse to one key, or the
# same firm gets hunted twice in successive waves.
SUFFIX_PHRASES = (
    "limited liability company",
    "limited liability co",
    "limited liability",
    "limited partnership",
    "general partnership",
)

# Joint ventures. The token shows up as "JV", "J.V.", "Jv," and mid-name as
# often as at the end, so match it as a word anywhere rather than as a suffix.
JV_RE = re.compile(r"\bj\.?\s?v\.?\b", re.I)

# Words that mark a company as outside the ICP: we want small trade contractors
# who swing hammers, not the firms that manage or finance them.
BAD_WORDS = (
    "consulting", "consultants", "holdings", "holding", "technologies",
    "technology", "capital", "ventures", "partners", "staffing", "logistics",
    "acquisition", "investments", "realty", "solutions group",
    "management services",
)

DOMAIN_RE = re.compile(r"\.(com|net|org)\b", re.I)

# A company with none of its trade's vocabulary in its name is usually either a
# diversified prime or a mis-coded NAICS. Cheap, and it removes most of the noise.
TRADE_WORDS = {
    "roofing": ("roof", "roofing", "sheet metal"),
    "painting": ("paint", "painting", "coating", "coatings", "finishes"),
    "janitorial": ("clean", "cleaning", "janitor", "janitorial", "maintenance",
                   "custodial", "sanitation"),
    "hvac-plumbing": ("hvac", "mechanical", "plumbing", "heating", "cooling",
                      "air", "climate", "refrigeration", "comfort"),
    "electrical": ("electric", "electrical", "power", "voltage", "wiring"),
    "landscaping": ("landscap", "lawn", "grounds", "tree", "turf", "irrigation",
                    "nursery"),
    "general-building": ("construction", "contractor", "contractors", "builders",
                         "building", "build", "constructors", "general"),
    "site-work": ("excavat", "grading", "paving", "site", "earthwork",
                  "concrete", "asphalt", "dirt", "utilities"),
}

PER_TRADE_STATE_CAP = 2


def norm(name: str) -> str:
    """Normalize a company name for dedupe: lowercase, alnum only, no suffixes.

    Periods are DELETED rather than turned into spaces, so "L.P." becomes the
    single token "lp" and gets stripped. Splitting it into "l" and "p" was why
    "Composite Cooling Solutions, L.P." failed to match the "Composite Cooling
    Solutions LP" row that had just been written.
    """
    s = name.lower().replace(".", "")
    s = " ".join(re.sub(r"[^a-z0-9 ]", " ", s).split())
    for phrase in SUFFIX_PHRASES:
        if s.endswith(" " + phrase):
            s = s[: -len(phrase) - 1].rstrip()
    parts = s.split()
    while parts and parts[-1] in SUFFIXES:
        parts.pop()
    return "".join(parts)


def icp_reason(company: str, trade: str) -> str | None:
    """Return None if the company is in the ICP, else why it was dropped."""
    low = company.lower()
    if JV_RE.search(company):
        return "joint venture"
    for w in BAD_WORDS:
        if w in low:
            return "non-ICP word"
    if DOMAIN_RE.search(low):
        return "bare domain name"
    if not any(w in low for w in TRADE_WORDS.get(trade, ())):
        return "no trade word"
    return None


def load_candidates(path: str) -> list[dict]:
    """Read prospect_source.py output, ignoring its trailing comment line."""
    with open(path) as fh:
        text = fh.read()
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    return json.loads(body)


def rank(cands: list[dict], db: Neon) -> tuple[list[dict], dict[str, int]]:
    existing = {norm(r["company"]) for r in db.query("select company from prospects")}
    hooks = {
        (r["trade"], r["state"]): int(r["n"])
        for r in db.query(
            "select trade, state, count(*) n from bids_current "
            "where due_at > now() and state is not null group by trade, state"
        )
    }
    national: dict[str, int] = defaultdict(int)
    for (trade, _state), n in hooks.items():
        national[trade] += n

    drops: dict[str, int] = defaultdict(int)
    kept, seen = [], set()
    for c in cands:
        key = norm(c["company"])
        if not key:
            drops["empty name"] += 1
            continue
        if key in existing:
            drops["already a prospect"] += 1
            continue
        if key in seen:
            drops["duplicate in batch"] += 1
            continue
        reason = icp_reason(c["company"], c["trade"])
        if reason:
            drops[reason] += 1
            continue
        seen.add(key)
        c["hook"] = hooks.get((c["trade"], c["state"]), 0)
        kept.append(c)

    kept.sort(key=lambda c: (-c["hook"], -national[c["trade"]], -c.get("amount", 0)))

    capped, per = [], defaultdict(int)
    for c in kept:
        ts = (c["trade"], c["state"])
        if per[ts] >= PER_TRADE_STATE_CAP:
            drops["over per-state cap"] += 1
            continue
        per[ts] += 1
        capped.append(c)
    return capped, dict(drops)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", required=True,
                    help="prospect_source.py output")
    ap.add_argument("--out", dest="outfile", default="ranked.json")
    ap.add_argument("--top", type=int, default=0,
                    help="if set, keep only the top N after ranking")
    args = ap.parse_args()

    cands = load_candidates(args.infile)
    db = Neon(os.environ["DATABASE_URL"])
    ranked, drops = rank(cands, db)
    if args.top:
        ranked = ranked[: args.top]

    with open(args.outfile, "w") as fh:
        json.dump(ranked, fh, indent=1)

    print(f"in={len(cands)} ranked={len(ranked)} -> {args.outfile}", file=sys.stderr)
    for reason, n in sorted(drops.items(), key=lambda kv: -kv[1]):
        print(f"  dropped {n:5d}  {reason}", file=sys.stderr)


if __name__ == "__main__":
    main()
