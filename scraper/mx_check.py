"""Check that the domains behind hunted email addresses can actually receive mail.

The hunting step only guarantees that an address is PRINTED on a page we
fetched. That is the rule that keeps us from inventing addresses, but it does
not make an address deliverable. On 2026-09-20 a prospect's contact page
printed info@<their-domain> while that domain carried no MX record at all,
only Squarespace web A records. Mail to it would have gone nowhere, and the
bounce would have counted against our sending reputation for no reason.

So: hunt for printed addresses, then run every domain through here before any
of them reaches the outreach batch. A domain with no MX gets filed with
outreach_channel = 'NO-MX' rather than 'EMAIL'. Do NOT respond to a NO-MX
result by guessing a different address at a different domain.

Resolution goes over DNS-over-HTTPS because raw DNS is blocked in the sandbox
this runs in. Note it must run in a MAIN session: subagents cannot reach
dns.google (they get PROVENANCE_REQUIRED with no one to answer the prompt).

Usage:
    python scraper/mx_check.py a@example.com b@other.com
    python scraper/mx_check.py --file addresses.txt
    DATABASE_URL=... python scraper/mx_check.py --source 'USAspending wave-7 2026-09-20'
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

DOH = "https://dns.google/resolve"
MX_TYPE = 15
TIMEOUT = 20


def mx_records(domain: str) -> list[str]:
    url = f"{DOH}?" + urllib.parse.urlencode({"name": domain, "type": "MX"})
    req = urllib.request.Request(url, headers={"Accept": "application/dns-json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        payload = json.loads(resp.read())
    return [a["data"] for a in payload.get("Answer", []) if a.get("type") == MX_TYPE]


def domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1].strip().lower()


def collect(args) -> list[str]:
    if args.source:
        sys.path.insert(0, os.path.dirname(__file__))
        from neon_http import Neon

        db = Neon(os.environ["DATABASE_URL"])
        rows = db.query(
            "select email from prospects where source = $1 and email is not null",
            [args.source],
        )
        return [r["email"] for r in rows]
    if args.file:
        with open(args.file) as fh:
            return [l.strip() for l in fh if l.strip()]
    return args.addresses


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("addresses", nargs="*", help="email addresses to check")
    ap.add_argument("--file", help="file with one address per line")
    ap.add_argument("--source", help="check every email on prospects with this source")
    args = ap.parse_args()

    addresses = collect(args)
    if not addresses:
        ap.error("nothing to check: pass addresses, --file, or --source")

    # One lookup per domain, not per address.
    cache: dict[str, list[str]] = {}
    bad = 0
    for addr in addresses:
        dom = domain_of(addr)
        if dom not in cache:
            try:
                cache[dom] = mx_records(dom)
            except Exception as exc:  # network/DoH failure is not a verdict
                print(f"{addr:48s} ERROR  {exc}")
                bad += 1
                continue
        mx = cache[dom]
        if mx:
            print(f"{addr:48s} MX-OK  {mx[0]}")
        else:
            print(f"{addr:48s} NO-MX  file as outreach_channel='NO-MX', do not send")
            bad += 1

    print(f"\n{len(addresses)} address(es), {len(cache)} domain(s), {bad} unusable")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
