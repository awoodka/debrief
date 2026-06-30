#!/usr/bin/env python3
"""Check which credentials in .env are present and working. Prints STATUS ONLY — never secrets.
Run:  .venv/bin/python collector/check_keys.py
"""
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
UA = {"User-Agent": "debrief/0.1 (key check)"}


def line(name, status, detail=""):
    print(f"  {name:<17}{status:<8}{detail}")


def github():
    tok = os.environ.get("GITHUB_PAT")
    if not tok:
        return line("GitHub PAT", "– none", "(degrades GitHub impl signal to 60/hr)")
    try:
        r = requests.get("https://api.github.com/rate_limit",
                         headers={**UA, "Authorization": f"Bearer {tok}"}, timeout=20)
        if r.status_code == 200:
            res = r.json()["resources"]
            line("GitHub PAT", "OK", f"core {res['core']['limit']}/hr · search {res.get('search', {}).get('limit')}/min")
        else:
            line("GitHub PAT", "FAIL", f"HTTP {r.status_code} — token invalid?")
    except Exception as e:  # noqa: BLE001
        line("GitHub PAT", "FAIL", type(e).__name__)


def semscholar():
    key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    if not key:
        return line("Semantic Scholar", "– none", "(fine — we use it key-less)")
    try:
        r = requests.get("https://api.semanticscholar.org/graph/v1/paper/ARXIV:1706.03762",
                         params={"fields": "title,citationCount"}, headers={**UA, "x-api-key": key}, timeout=20)
        line("Semantic Scholar", "OK" if r.status_code == 200 else "FAIL",
             f"cites={r.json().get('citationCount')}" if r.status_code == 200 else f"HTTP {r.status_code}")
    except Exception as e:  # noqa: BLE001
        line("Semantic Scholar", "FAIL", type(e).__name__)


def bluesky():
    h, p = os.environ.get("BLUESKY_HANDLE"), os.environ.get("BLUESKY_APP_PASSWORD")
    if not (h and p):
        return line("Bluesky", "– none", "(optional discourse source)")
    try:
        r = requests.post("https://bsky.social/xrpc/com.atproto.server.createSession",
                          json={"identifier": h, "password": p}, headers=UA, timeout=20)
        j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code == 200 and j.get("accessJwt"):
            line("Bluesky", "OK", f"session for @{j.get('handle')}")
        else:
            line("Bluesky", "FAIL", (j.get("message") or f"HTTP {r.status_code}")[:45])
    except Exception as e:  # noqa: BLE001
        line("Bluesky", "FAIL", type(e).__name__)


def producthunt():
    tok = os.environ.get("PRODUCTHUNT_TOKEN")
    if not tok:
        return line("Product Hunt", "– none", "(optional launches source)")
    try:
        r = requests.post("https://api.producthunt.com/v2/api/graphql",
                          json={"query": "{ posts(first:1){edges{node{name}}} }"},
                          headers={**UA, "Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                          timeout=20)
        j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        edges = (((j.get("data") or {}).get("posts") or {}).get("edges")) or []
        if r.status_code == 200 and "data" in j:
            line("Product Hunt", "OK", f"e.g. {edges[0]['node']['name'][:26]}" if edges else "API reachable")
        else:
            line("Product Hunt", "FAIL", str(j.get("errors") or j.get("error") or f"HTTP {r.status_code}")[:45])
    except Exception as e:  # noqa: BLE001
        line("Product Hunt", "FAIL", type(e).__name__)


if __name__ == "__main__":
    print("\nCredential check (status only — no secret values printed):\n")
    github(); semscholar(); bluesky(); producthunt()
    print()
