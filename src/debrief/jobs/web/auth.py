"""Cloudflare Access for the jobs site (docs/jobs/PLAN.md, M4). Access sits in front of
jobs.alexwoodka.com and adds a signed token (Cf-Access-Jwt-Assertion) to every request it lets
through; Caddy refuses requests without the header. This module is the app's own check, so a
removed or misconfigured Access app, or a forged header, never exposes the site:

  - the token is RS256, signed by one of the team's published keys (fetched from
    https://<team>.cloudflareaccess.com/cdn-cgi/access/certs, kept an hour, fetched again at once
    for an unknown key id, but at most once a minute);
  - its audience is this Access app's AUD tag, its issuer the team domain, and it hasn't expired;
  - its email is one of the allowed addresses.

Anything else is refused.
"""
import json
import time

import jwt
import requests

KEEP_KEYS_S = 3600
REFETCH_GAP_S = 60


def fetch_json(url):
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


class AccessVerifier:
    def __init__(self, team, aud, emails, certs_url=None, fetch=fetch_json, clock=time.time):
        if not team or not aud:
            raise ValueError("the Access team and AUD tag are both required")
        self.iss = f"https://{team}.cloudflareaccess.com"
        self.certs_url = certs_url or f"{self.iss}/cdn-cgi/access/certs"
        self.aud = aud
        self.emails = {e.strip().lower() for e in emails if e and e.strip()}
        if not self.emails:
            raise ValueError("at least one allowed email is required")
        self.fetch, self.clock = fetch, clock
        self.keys, self.fetched_at, self.forced_at = {}, None, None

    @classmethod
    def from_env(cls, env):
        return cls(env.get("JOBS_WEB_ACCESS_TEAM", "").strip(), env.get("JOBS_WEB_ACCESS_AUD", "").strip(),
                   env.get("JOBS_WEB_ALLOWED_EMAILS", "").split(","), certs_url=env.get("JOBS_WEB_CERTS_URL") or None)

    def _load(self):
        data = self.fetch(self.certs_url)
        keys = {}
        for k in (data or {}).get("keys") or []:
            if k.get("kid") and k.get("kty") == "RSA":
                keys[k["kid"]] = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(k))
        self.keys, self.fetched_at = keys, self.clock()

    def _key(self, kid):
        now = self.clock()
        try:
            if self.fetched_at is None or now - self.fetched_at > KEEP_KEYS_S:
                self._load()
            if kid not in self.keys and (self.forced_at is None or now - self.forced_at > REFETCH_GAP_S):
                self.forced_at = now
                self._load()
        except Exception:  # noqa: BLE001 — keys we can't fetch verify nothing
            return None
        return self.keys.get(kid)

    def email_for(self, token):
        """The signed-in email if the token is valid and the email allowed, else None."""
        if not token:
            return None
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            return None
        if header.get("alg") != "RS256" or not header.get("kid"):
            return None
        key = self._key(header["kid"])
        if key is None:
            return None
        try:
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.aud, issuer=self.iss,
                                options={"require": ["exp", "aud", "iss"]})
        except jwt.PyJWTError:
            return None
        email = str(claims.get("email") or "").strip().lower()
        return email if email in self.emails else None
