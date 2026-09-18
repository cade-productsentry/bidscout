#!/usr/bin/env python3
"""Report bids whose placeOfPerformance city does not belong to their state.

Run:  DATABASE_URL=... python scraper/geo_audit.py [--open-only]

WHAT THIS IS FOR, AND WHAT IT IS NOT FOR
----------------------------------------
This is a review report. Do not wire it into a suppression path.

Measured 2026-09-18 against 1,840 rows carrying both a city and a state: it
flags 64, and on inspection roughly 63 of those are correct. The false
positives are systematic, not noise:

  * Military installations named after a place in another state.
    Fort Bragg NC (Fort Bragg is a city in CA), Edwards CA and McClellan CA
    (Air Force bases, not census places), Santa Rita GU.
  * Unincorporated places and hospital campuses the Census Gazetteer does not
    carry as places: Hines IL, Leeds MA, Chatsworth NJ, Upton NY.

So a city name cannot refute a state that SAM.gov stated outright. What it can
do is arbitrate in the one situation where we were otherwise guessing: SAM gave
us a city, gave us no state code and no ZIP, and the only remaining candidate
came out of free text. geo.infer_state() now resolves that case with the same
map (see the comment above states_for_city), which is where the real fix
lives. This script exists to catch the residue and to keep the measurement
honest if the data shifts.

A row that shows up here and is genuinely wrong is usually a multi-state
contracting office (VA Network Contract Office, a USACE district) whose notice
predates the inference fix.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from geo import states_for_city  # noqa: E402
from neon_http import Neon  # noqa: E402

SQL = """
select id, title, agency, state, state_method, city, due_at
  from bids
 where city is not null and state is not null
   {open_clause}
 order by due_at nulls last, id
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--open-only", action="store_true", help="only bids still accepting offers")
    args = ap.parse_args()

    db = Neon(os.environ.get("DATABASE_URL", ""))
    rows = db.query(SQL.format(open_clause="and due_at > now()" if args.open_only else ""))

    checked = unknown_city = 0
    flagged: list[tuple[dict, frozenset[str]]] = []
    for r in rows:
        checked += 1
        known = states_for_city(r["city"])
        if known is None:
            unknown_city += 1
            continue
        if r["state"] not in known:
            flagged.append((r, known))

    print(f"{checked} rows with a city and a state")
    print(f"{unknown_city} cities absent from the gazetteer (not judged)")
    print(f"{len(flagged)} flagged\n")
    for r, known in flagged:
        only = next(iter(known)) if len(known) == 1 else f"{len(known)} states"
        print(
            f"  id={r['id']:<6} state={r['state']} method={r['state_method'] or '-':<12}"
            f" city={r['city']!r} -> {only}"
        )
        print(f"         {(r['title'] or '')[:88]}")
        print(f"         {(r['agency'] or '')[:88]}")
    if flagged:
        print(
            "\nRead these before acting on them. A flag is a question, not a verdict:"
            "\nbases and unincorporated places flag legitimately (see this file's docstring)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
