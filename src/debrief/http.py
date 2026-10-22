"""Polite HTTP for every Debrief v2 fetch.

- Requests go out one at a time, and requests to the same rate bucket are spaced at least
  `interval_s` apart. Buckets come from settings.http.buckets and match a host and its
  subdomains, so every arXiv interface shares one budget, as arXiv's terms require.
- Every request carries a descriptive User-Agent with contact info.
- Conditional GET: pass the stored ETag/Last-Modified and a 304 comes back as `not_modified`.
- 429/5xx: honour Retry-After (capped), else back off exponentially, then give up.

HTTP errors never raise. Callers get a Fetched with `ok` False and the reason, so a failure is
reported, not swallowed.
"""
import email.utils
import json
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import requests

from .robots import Robots

RATE_HEADERS = ("retry-after", "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset",
                "ratelimit-limit", "ratelimit-remaining", "ratelimit-reset", "ratelimit-policy")


@dataclass
class Fetched:
    url: str
    final_url: str = ""
    status: int = 0                 # 0 = no HTTP response (network error)
    headers: dict = field(default_factory=dict)
    content: bytes = b""
    error: str = ""
    attempts: int = 0
    elapsed_s: float = 0.0

    @property
    def ok(self):
        return 200 <= self.status < 300

    @property
    def not_modified(self):
        return self.status == 304

    @property
    def text(self):
        return self.content.decode(self.encoding, errors="replace")

    @property
    def encoding(self):
        ctype = self.headers.get("content-type", "")
        for part in ctype.split(";")[1:]:
            k, _, v = part.strip().partition("=")
            if k.lower() == "charset" and v:
                return v.strip('"')
        return "utf-8"

    def json(self):
        return json.loads(self.content)

    def rate_headers(self):
        return {k: self.headers[k] for k in RATE_HEADERS if k in self.headers}


def _host(url):
    return (urlsplit(url).hostname or "").lower()


def _retry_after_s(value):
    if not value:
        return None
    if value.strip().isdigit():
        return float(value)
    try:
        return max(0.0, email.utils.parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


class Client:
    def __init__(self, user_agent, http_settings, sleep=time.sleep, clock=time.monotonic,
                 session=None):
        self.user_agent = user_agent
        self.s = http_settings
        self._sleep, self._clock = sleep, clock
        self._last = {}            # bucket -> clock() of the last request sent
        self._robots = {}          # scheme://host -> RobotFileParser, or None if unreachable
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    def bucket(self, url):
        """(bucket name, min interval). A bucket entry is a host, which covers its subdomains too,
        optionally followed by a path prefix ("huggingface.co/papers"). The first bucket in
        settings order that matches wins, so list the narrower ones first."""
        parts = urlsplit(url)
        host, path = _host(url), parts.path or "/"
        for name, b in (self.s.get("buckets") or {}).items():
            for entry in b["hosts"]:
                h, _, prefix = entry.partition("/")
                if (host == h or host.endswith("." + h)) and path.startswith("/" + prefix):
                    return name, float(b["interval_s"])
        return host, float(self.s["default_interval_s"])

    def _wait_turn(self, url):
        name, interval = self.bucket(url)
        last = self._last.get(name)
        if last is not None:
            wait = last + interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last[name] = self._clock()

    def request(self, method, url, *, params=None, json_body=None, headers=None, etag=None,
                last_modified=None, timeout=None):
        hdrs = dict(headers or {})
        if etag:
            hdrs["If-None-Match"] = etag
        if last_modified:
            hdrs["If-Modified-Since"] = last_modified
        timeout = timeout or self.s["timeout_s"]
        retries = int(self.s["max_retries"])
        out = Fetched(url=url)
        t0 = self._clock()
        for attempt in range(retries + 1):
            out.attempts = attempt + 1
            self._wait_turn(url)
            try:
                r = self.session.request(method, url, params=params, json=json_body, headers=hdrs,
                                         timeout=timeout, allow_redirects=True)
            except requests.RequestException as e:
                out.status, out.error = 0, f"{type(e).__name__}: {e}"[:300]
                if attempt < retries:
                    self._sleep(self.s["backoff_s"] * 2 ** attempt)
                    continue
                break
            out.final_url, out.status = r.url, r.status_code
            out.headers = {k.lower(): v for k, v in r.headers.items()}
            out.content, out.error = r.content, ""
            if r.status_code == 429 or r.status_code >= 500:
                out.error = f"HTTP {r.status_code}"
                if attempt < retries:
                    wait = _retry_after_s(out.headers.get("retry-after"))
                    if wait is None:
                        wait = self.s["backoff_s"] * 2 ** attempt
                    if wait > self.s["max_retry_after_s"]:
                        out.error += f" (Retry-After {wait:.0f}s exceeds cap; not retrying)"
                        break
                    self._sleep(wait)
                    continue
            elif r.status_code >= 400:
                out.error = f"HTTP {r.status_code}"
            break
        out.elapsed_s = round(self._clock() - t0, 2)
        return out

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def robots_allowed(self, url):
        """True/False per the host's robots.txt (RFC 9309) for our product token, or None if
        robots.txt was unreachable (5xx or network error; RFC 9309 says to treat that as
        disallow-all)."""
        parts = urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}"
        if root not in self._robots:
            r = self.get(root + "/robots.txt")
            if r.ok:
                self._robots[root] = Robots(r.text, self.product)
            elif 400 <= r.status < 500:
                self._robots[root] = Robots("", self.product)   # RFC 9309: a 4xx means no rules
            else:
                self._robots[root] = None
        rb = self._robots[root]
        return None if rb is None else rb.allowed(url)

    @property
    def product(self):
        """The User-Agent's product token ("debrief" from "debrief/2.0 (...)")."""
        return self.user_agent.split("/", 1)[0].split()[0]
