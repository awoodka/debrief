"""Jobs collectors. Same contract as Debrief's adapters (src/debrief/adapters/base.py): fetch()
talks to the network through the context, which records every response as a raw record; parse()
turns one raw record into JobItems and is pure, so replay gives the same items. The context checks
robots.txt before every request: a disallowed URL is recorded as refused and never requested.

`snapshot` says what a response means for jobs it doesn't list:
  "window"  a recent window or search: a missing job means nothing
  "flag"    the source marks closed jobs itself (Simplify's `active`)
  "full"    the complete current list for its scope (a watchlist board, a markdown list): a job
            missing from a complete snapshot (every request ok, no 304, no record's meta marked
            "partial") is closed. An adapter that stops reading a list early marks its records partial.
"""
from datetime import datetime, timezone

from ...http import Fetched
from ...store import raw


class Ctx:
    """What fetch() may use. Records and validators are proposals: collect keeps them once the
    source's items are stored."""

    def __init__(self, client, store, env, run_id, profile, contact, settings, log=print, early_sources=frozenset()):
        self.client, self.store, self.env = client, store, env
        self.early_sources = early_sources          # the new-grad lists' source ids (collect.early_sources)
        self.run_id, self.profile, self.contact = run_id, profile, contact
        self.settings, self.log = settings, log
        self.records, self.validators, self.warnings = [], [], []
        self.robots_waived = 0              # requests made under the source's robots exception

    @property
    def fetched_at(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _auth(self, source, headers):
        h, params = source.auth_parts(self.env) if not source.missing_keys(self.env) else ({}, {})
        extra = {k: v.replace("{contact}", self.contact) for k, v in (source.headers or {}).items()}
        secrets = [v for v in list(h.values()) + list(params.values()) if v]
        return {**extra, **h, **(headers or {})}, params, secrets

    def _record(self, source, fetched, secrets, each, meta):
        for s in secrets:                       # an API key in the query must never reach the raw store
            if s and len(s) >= 4:
                fetched.final_url = fetched.final_url.replace(s, "<redacted>")
                fetched.error = (fetched.error or "").replace(s, "<redacted>")
        rec = raw.make_record(source.id, self.run_id, len(self.records), fetched, self.fetched_at,
                              each=each, meta=meta)
        self.records.append(rec)
        return rec

    def _refused(self, url, source=None):
        """A robots.txt refusal, recorded like a failed fetch (status 0): never requested. A
        source's robots exception (an official keyed API, on its own hosts) skips the check."""
        if source is not None and source.robots_waived(url, self.env):
            self.robots_waived += 1
            return None
        allowed = self.client.robots_allowed(url)
        if allowed is True:
            return None
        return Fetched(url=url, final_url=url, status=0,
                       error="robots.txt disallows this URL" if allowed is False else "robots.txt unreachable")

    def get(self, source, url, *, each=None, meta=None, params=None, headers=None, conditional=True):
        hdrs, auth_params, secrets = self._auth(source, headers)
        refused = self._refused(url, source)
        if refused:
            return self._record(source, refused, secrets, each, meta)
        etag, last_modified = self.store.validators(url) if conditional else (None, None)
        fetched = self.client.get(url, params={**(params or {}), **auth_params} or None, headers=hdrs or None,
                                  etag=etag, last_modified=last_modified)
        rec = self._record(source, fetched, secrets, each, meta)
        if conditional and fetched.ok and (fetched.headers.get("etag") or fetched.headers.get("last-modified")):
            self.validators.append((url, fetched.headers.get("etag"), fetched.headers.get("last-modified")))
        return rec

    def post(self, source, url, json_body, *, each=None, meta=None, headers=None):
        hdrs, _, secrets = self._auth(source, headers)
        fetched = self._refused(url, source) or self.client.post(url, json_body=json_body, headers=hdrs or None)
        return self._record(source, fetched, secrets, each, {**(meta or {}), "body": json_body})

    def record(self, source, fetched, *, each=None, meta=None, secrets=()):
        """Keep a response that didn't come through get() or post() (the IMAP inbox): the same raw
        record, with `secrets` redacted from its URL and error."""
        return self._record(source, fetched, list(secrets), each, meta)

    def warn(self, message):
        self.warnings.append(message)


class Adapter:
    name = ""
    snapshot = "window"
    weekly = False            # fetched at most once every 7 days (the board directory)
    quiet_ok = False          # nothing new is a normal answer (HN job posts: only unseen ids are requested)
    tolerate = ()             # HTTP statuses that are a normal answer, not a failure (describe: a job page refused or gone)

    def fetch(self, ctx, source):
        raise NotImplementedError

    def parse(self, record, source):
        """[JobItem] from one raw record."""
        raise NotImplementedError

    def directory(self, record, source):
        """[(board, company name)] this record tells us about, for the board directory."""
        return []

    def gone(self, record, source):
        """{job key "system:id": True if the job's own board says it's gone (closed), False if the
        board shows it open} from this record (the describe step)."""
        return {}

    def texts(self, record, source):
        """[(job key "system:id", description, facts {employment, pay})] for postings this record
        describes (the describe step)."""
        return []


def _registry():
    from . import alerts, apis, boards_watch, describe, finder, hn, lists, serpapi
    out = {}
    for mod in (lists, apis, hn, boards_watch, finder, describe, alerts, serpapi):
        for a in mod.ADAPTERS:
            out[a.name] = a
    return out


REGISTRY = _registry()
