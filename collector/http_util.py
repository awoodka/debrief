"""Shared HTTP: polite UA, retries, backoff, and an on-disk GET cache (SQLite).

Caching is on by default for GET (TTL from config) so dev re-runs reuse responses instead of
re-hitting the APIs — which is what was tripping the arXiv/Reddit rate limits. Daily runs (>TTL apart)
fetch fresh. Pass ttl=0 to bypass the cache for a call.
"""
import hashlib
import json as _json
import sqlite3
import time
from pathlib import Path

import requests

from .config import USER_AGENT, DEFAULT_CACHE_TTL, CACHE_DB, CACHE_MAX_AGE_DAYS

_session = None
_cache_conn = None


def session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    return _session


def _cache():
    global _cache_conn
    if _cache_conn is None:
        Path(CACHE_DB).parent.mkdir(parents=True, exist_ok=True)
        _cache_conn = sqlite3.connect(CACHE_DB)
        _cache_conn.execute(
            "CREATE TABLE IF NOT EXISTS http_cache (key TEXT PRIMARY KEY, ts INTEGER, status INTEGER, text TEXT)")
        _cache_conn.execute("DELETE FROM http_cache WHERE ts < ?",
                            (int(time.time()) - CACHE_MAX_AGE_DAYS * 86400,))
        _cache_conn.commit()
    return _cache_conn


def _key(url, params):
    return hashlib.sha256((url + "?" + _json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()


class CachedResponse:
    """Minimal stand-in for requests.Response served from cache."""

    def __init__(self, status, text):
        self.status_code = status
        self.text = text
        self.content = text.encode("utf-8", "replace")
        self.from_cache = True

    def json(self):
        return _json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def get(url, *, params=None, timeout=30, retries=3, backoff=2.0, ttl=None, headers=None):
    """GET with retries + on-disk cache. Raises the last exception if all attempts fail (caller is fail-soft)."""
    ttl = DEFAULT_CACHE_TTL if ttl is None else ttl
    key = _key(url, params)
    if ttl > 0:
        row = _cache().execute("SELECT ts, status, text FROM http_cache WHERE key=?", (key,)).fetchone()
        if row and (time.time() - row[0]) < ttl:
            return CachedResponse(row[1], row[2])
    last = None
    for attempt in range(retries):
        try:
            r = session().get(url, params=params, timeout=timeout, headers=headers)
            r.from_cache = False
            if r.status_code == 429:
                last = RuntimeError("HTTP 429 rate limited")
                time.sleep(backoff * (attempt + 1) * 2)
                continue
            r.raise_for_status()
            if ttl > 0:
                c = _cache()
                c.execute("INSERT OR REPLACE INTO http_cache VALUES (?,?,?,?)",
                          (key, int(time.time()), r.status_code, r.text))
                c.commit()
            return r
        except Exception as e:  # noqa: BLE001 — bubble up after retries; callers are fail-soft
            last = e
            time.sleep(backoff * (attempt + 1))
    raise last
