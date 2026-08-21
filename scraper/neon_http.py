"""Tiny Postgres client that talks to Neon over its HTTPS SQL endpoint.

Why not psycopg? Raw Postgres TCP is blocked in some of the environments this
code runs in (notably the sandbox used for day-to-day operations). Neon exposes
every database over plain HTTPS, which works everywhere, so we use that as the
only code path. GitHub Actions could use psycopg, but one path is simpler.

Usage:
    db = Neon(os.environ["DATABASE_URL"])
    rows = db.query("select * from bids where state = $1", ["TX"])
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any


class Neon:
    def __init__(self, database_url: str, timeout: int = 60) -> None:
        if not database_url:
            raise ValueError("DATABASE_URL is empty")
        parsed = urllib.parse.urlparse(database_url)
        if not parsed.hostname:
            raise ValueError("DATABASE_URL has no hostname")
        # The HTTP endpoint wants the connection string without query params.
        self.conn_str = database_url.split("?", 1)[0]
        self.endpoint = f"https://{parsed.hostname}/sql"
        self.timeout = timeout

    def query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        body = json.dumps({"query": sql, "params": params or []}).encode()
        req = urllib.request.Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Neon-Connection-String": self.conn_str,
                "Content-Type": "application/json",
                "User-Agent": "bidscout-scraper/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as exc:  # surface Neon's error message
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"Neon HTTP {exc.code}: {detail[:500]}") from exc
        if isinstance(payload, dict) and payload.get("message") and "rows" not in payload:
            raise RuntimeError(f"Neon error: {payload['message']}")
        return payload.get("rows", [])

    def execute(self, sql: str, params: list[Any] | None = None) -> int:
        """Run a statement and return rowCount."""
        body = json.dumps({"query": sql, "params": params or []}).encode()
        req = urllib.request.Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Neon-Connection-String": self.conn_str,
                "Content-Type": "application/json",
                "User-Agent": "bidscout-scraper/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"Neon HTTP {exc.code}: {detail[:500]}") from exc
        return int(payload.get("rowCount") or 0)
