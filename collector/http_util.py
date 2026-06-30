"""Shared HTTP with a polite UA, retries, and backoff (incl. 429-aware)."""
import time
import requests
from .config import USER_AGENT

_session = None


def session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    return _session


def get(url, *, params=None, timeout=30, retries=3, backoff=2.0):
    """GET with retries. Raises the last exception if all attempts fail (caller is fail-soft)."""
    last = None
    for attempt in range(retries):
        try:
            r = session().get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                last = RuntimeError("HTTP 429 rate limited")
                time.sleep(backoff * (attempt + 1) * 2)
                continue
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001 — deliberate: bubble up after retries
            last = e
            time.sleep(backoff * (attempt + 1))
    raise last
