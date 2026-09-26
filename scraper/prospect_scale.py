"""Measure how BIG a company is before anyone spends hunting time on it.

WHY THIS EXISTS. Three companies have been dropped from the pool on the same
judgment, and all three were caught by hand, late, at three different stages:

  Victory Contracting (110)     found after it reached a send batch (wave-6 hold)
  Sentinel Power Services (164) found after it was hunted and MX-verified
  PD Power Systems (143)        found on a re-audit, 18 days after being hunted

The call in each case was not "is the trade claim true" but "is this company the
one the claim is FOR". BidScout sells bid triage to shops with nobody watching
SAM.gov. A nationwide prime with a federal capture team already has that job
covered, and an equipment manufacturer is on the other side of the transaction
from our customer entirely. Neither is a prospect at any price.

Since 2026-09-25 every prospect carries a resolved UEI, so the award record that
settles this question can be read BEFORE a subagent is sent to hunt an address,
instead of after. That is what this module does.

IT REPORTS, IT DOES NOT FILTER. Same standing rule as geo_audit.py and
prospect_audit.py, and for the same reason: the measurements below separate the
known drops from the known keeps, but they do it with margins of a few points,
not orders of magnitude. A flag is the start of a judgment call.

Usage:
    # flag the top of a ranked shortlist before hunting it
    DATABASE_URL=... python scraper/prospect_rank.py --in cands.txt --out ranked.json \
        --scale-top 24

    # measure companies already in the table (calibration, spot checks)
    DATABASE_URL=... python scraper/prospect_scale.py --ids 110,143,164
    DATABASE_URL=... python scraper/prospect_scale.py --company 'Ranco Construction'
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
from neon_http import Neon  # noqa: E402
from prospect_audit import (  # noqa: E402
    NAICS_TRADE,
    fetch_awards,
    resolve_for,
    resolve_ueis,
)


def today_str() -> str:
    return time.strftime("%Y-%m-%d")

# NAICS sector prefixes that mean the company SELLS things rather than installs
# them. 31-33 is manufacturing, 42 is merchant wholesale. This is the fifth
# corollary from the OPS LOG, made checkable: PD Power Systems (143) reads like
# an electrical contractor until the award mix is read, and then it is generator
# and motor manufacturing with switchgear and instrument codes around it. No
# BidScout trade describes what it does, so there is no honest variant to send.
SUPPLIER_SECTORS = ("31", "32", "33", "42")

# Thresholds. Every one of these was measured against the six companies below,
# not chosen for roundness. See the calibration table in the docstring of
# scale_flags(); the margin on each is thin, which is why this reports.
WIDE_STATES = 8           # distinct place-of-performance states
DIFFUSE_TOP_STATE = 0.45  # largest single state's share of awards
SUPPLIER_SHARE = 0.30     # share of awards under manufacturing/wholesale NAICS
SUPPLIER_MIN = 8          # ...and this many of them, so a 2-of-4 shop is not a supplier
MANY_AWARDS = 20          # volume floor, so a handful of awards is never "nationwide"


def measure(company: str, trade: str | None, state: str | None,
            uei: str | None, today: str) -> dict | None:
    """Award-record shape for one company. None if nothing could be matched."""
    awards = fetch_awards(company, today, uei=uei)
    if not awards:
        return None

    states = Counter()
    sectors = Counter()
    trades = Counter()
    for a in awards:
        st = a.get("Place of Performance State Code")
        if st:
            states[st] += 1
        code = str(a.get("naics_code") or "")
        if len(code) >= 2:
            sectors[code[:2]] += 1
        mapped = NAICS_TRADE.get(code)
        if mapped:
            trades[mapped] += 1

    n = len(awards)
    placed = sum(states.values())
    top_state, top_state_n = (states.most_common(1) or [(None, 0)])[0]
    supplier_n = sum(v for k, v in sectors.items() if k in SUPPLIER_SECTORS)

    return {
        "awards": n,
        "states": len(states),
        "placed": placed,
        "top_state": top_state,
        "top_state_share": (top_state_n / placed) if placed else None,
        "supplier_share": supplier_n / n,
        "supplier_n": supplier_n,
        "assigned_trade_share": (trades.get(trade, 0) / n) if trade else None,
        "assigned_state_share": (states.get(state, 0) / placed) if (state and placed) else None,
        "top_trade": (trades.most_common(1) or [(None, 0)])[0][0],
        "state_counts": dict(states.most_common(6)),
    }


def measure_unhunted(company: str, trade: str | None, state: str | None,
                     today: str) -> dict | None:
    """measure() for a candidate that is not a prospect row yet.

    There is no stored UEI to reuse at ranking time, so resolve one first: the
    recipient lookup is by name and needs no database row. Worth the extra call,
    because the by-name award search is near-literal and was returning nothing
    for 4 of 15 wave-8 companies before UEI resolution was introduced. If the
    name resolves to no funded UEI, fall through to the name search, which is
    what fetch_awards does with uei=None.
    """
    candidates = resolve_ueis(company)
    uei = candidates[0][0] if candidates else None
    m = measure(company, trade, state, uei, today)
    if m is not None:
        m["uei"] = uei
        m["uei_ambiguous"] = len(candidates) > 1
    return m


def scale_flags(m: dict) -> list[str]:
    """Which flags this award record earns.

    CALIBRATION, measured 2026-09-26 by running measure() over every company the
    pool has already judged by hand. Real numbers from that run, not estimates:

      id  company                    awards states top-state supplier verdict
      164 Sentinel Power Services       100     29    AK 23%       0%  DROPPED
      110 Victory Contracting            25     15    WA 12%       8%  DROPPED
      143 PD Power Systems              100     12    VA 48%      84%  DROPPED
      146 Ark Construction Mgmt         100      8    PA 82%       1%  KEPT
      153 Ranco Construction             80      1    NJ 100%      0%  KEPT
      155 Maguire-O'Hara                 92      3    OK 98%       1%  KEPT
      170 Briston Construction           58      8    TX 57%       0%  KEPT
      172 D Square Construction         100      4    CA 72%       2%  KEPT

    THE LESSON IN THAT TABLE, and the reason an award COUNT alone is worthless as
    a signal: Ranco (80) and Sentinel (100) are indistinguishable by volume, and
    Victory (25) is smaller than four of the five keeps. What separates them is
    FOOTPRINT. Ranco won 80 awards in ONE state, which is a busy local contractor
    and the best hook we have ever had; Sentinel won 100 across 29 with no state
    above 23%. So NATIONWIDE? requires a wide spread AND no state carrying much
    of the work, and it is the top-state-share test that does the real work: all
    five keeps clear it on that condition alone, at 57% or better against a 45%
    line. MANY_AWARDS is only a thin-data floor, set at 20 rather than 40 because
    40 let Victory through and no keep depends on it.

    SUPPLIER? NEEDS A COUNT, NOT ONLY A SHARE, and the first production run is
    what proved it. On a 30-prospect sweep of the staged batches the only
    SUPPLIER? flag was Shorts Specialty (148), at 50% - which on inspection was
    2 awards out of 4: one metal-window manufacturing code and one construction
    machinery wholesale code, in a 4-award record belonging to a small Galion OH
    shop. PD Power, the company the flag exists for, carries 84 supplier awards
    out of 100. A share alone cannot tell those apart, so SUPPLIER_MIN requires
    the absolute count as well. This is the same thin-data trap the OPS LOG
    recorded for Shorts on 09-25 from the other direction, when 3 of its 4 awards
    turned out to carry no place-of-performance state at all.

    Where this is still blind, stated plainly: a diversified regional prime that
    keeps 50% of its work in one state passes, and so does a nationwide operator
    with fewer than 20 awards. The parent-mailbox tell and the hand read of the
    award mix remain the backstop, exactly as for the ANC name filter.
    """
    flags = []
    top = m["top_state_share"]
    if (m["awards"] >= MANY_AWARDS and m["states"] >= WIDE_STATES
            and top is not None and top < DIFFUSE_TOP_STATE):
        flags.append("NATIONWIDE?")
    if m["supplier_share"] >= SUPPLIER_SHARE and m["supplier_n"] >= SUPPLIER_MIN:
        flags.append("SUPPLIER?")
    return flags


def describe(m: dict) -> str:
    top = m["top_state_share"]
    bits = [f"{m['awards']} awards", f"{m['states']} states"]
    if m["top_state"]:
        bits.append(f"top {m['top_state']} {top:.0%}")
    if m["supplier_share"]:
        bits.append(f"supplier NAICS {m['supplier_share']:.0%}")
    if m["assigned_trade_share"] is not None:
        bits.append(f"assigned trade {m['assigned_trade_share']:.0%}")
    return ", ".join(bits)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", help="comma-separated prospect ids")
    ap.add_argument("--company", help="one company name, by name search")
    ap.add_argument("--source", help="all prospects from one sourcing run")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    today = today_str()
    db = Neon(os.environ["DATABASE_URL"])

    if args.company:
        rows = [{"id": 0, "company": args.company, "trade": None, "state": None}]
    elif args.ids:
        ids = [int(x) for x in args.ids.split(",")]
        rows = db.query(
            "select id, company, trade, state, uei, uei_status from prospects "
            "where id = any($1) order by id", [ids])
    elif args.source:
        rows = db.query(
            "select id, company, trade, state, uei, uei_status from prospects "
            "where source = $1 order by id", [args.source])
    else:
        ap.error("need --ids, --company or --source")

    out, flagged = [], 0
    for p in rows:
        uei = None
        if p.get("id"):
            uei, _how, _amb = resolve_for(p, db=db)
        m = measure(p["company"], p.get("trade"), p.get("state"), uei, today)
        if m is None:
            print(f"{p.get('id') or '':>5} {p['company'][:38]:38s} NO-AWARDS-MATCHED")
            continue
        fl = scale_flags(m)
        flagged += bool(fl)
        print(f"{p.get('id') or '':>5} {p['company'][:38]:38s} {describe(m)}"
              + (f"   {' '.join(fl)}" if fl else ""))
        out.append({"id": p.get("id"), "company": p["company"], "flags": fl, **m})

    if args.json:
        print(json.dumps(out, indent=1))
    print(f"\n{flagged} of {len(rows)} flagged for a human look", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
