"""Jobs collectors. Same contract as Debrief's adapters (src/debrief/adapters/base.py): fetch()
talks to the network through the context, which records every response as a raw record; parse()
turns one raw record into JobItems and is pure, so replay gives the same items.

The site rule (Alex, 2026-11-23): any site that doesn't actively block us may be read. A block is an
answer of 401, 403, 429 (after the client's retries) or LinkedIn's 999, or a page the adapter knows is a
challenge or a login wall (get()'s `block`). After a block the context asks that host nothing more for
the rest of the run: every later request to it is recorded as not made (status 0), so a full snapshot
that lost a page never closes jobs, and the source's report names the host. A block is never worked
around.

`snapshot` says what a response means for jobs it doesn't list:
  "window"  a recent window or search: a missing job means nothing
  "flag"    the source marks closed jobs itself (Simplify's `active`)
  "full"    the complete current list for its scope (a watchlist board, a markdown list): a job
            missing from a complete snapshot (every request ok, no 304, no record's meta marked
            "partial") is closed. An adapter that stops reading a list early marks its records partial.
"""
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from ...http import Fetched
from ...store import raw

BLOCK_STATUSES = {401: "HTTP 401", 403: "HTTP 403", 429: "HTTP 429 after retries", 999: "HTTP 999"}
BLOCK_MARK = "a block"


def blocked_by_status(fetched):
    """Why this answer is a block, from its status alone, or None."""
    return BLOCK_STATUSES.get(fetched.status)


def host_of(url):
    return (urlsplit(url or "").hostname or "").lower()


@dataclass(frozen=True)
class Block:
    why: str
    source: str
    url: str


class Ctx:
    """What fetch() may use. Records and validators are proposals: collect keeps them once the
    source's items are stored."""

    def __init__(self, client, store, env, run_id, profile, contact, settings, log=print, early_sources=frozenset(),
                 run_date=None, blocks=None):
        self.client, self.store, self.env = client, store, env
        self.early_sources = early_sources          # the new-grad lists' source ids (collect.early_sources)
        self.run_id, self.run_date, self.profile, self.contact = run_id, run_date, profile, contact
        self.settings, self.log = settings, log
        self.records, self.validators, self.warnings = [], [], []
        self.blocks = {} if blocks is None else blocks
        self.skipped = Counter()
        self.not_made = set()

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

    def blocked(self, url):
        """The block on the URL's host this run, or None."""
        return self.blocks.get(host_of(url))

    def _send(self, source, url, secrets, each, meta, ask, block, keep=None):
        """One request under the site rule: none to a host that blocked us this run (a record of the request
        not made), and a block answer stops its host."""
        host, earlier = host_of(url), self.blocked(url)
        if earlier:
            self.skipped[host] += 1
            rec = self._record(source, Fetched(url=url, final_url=url, status=0,
                                               error=f"not requested: {host} blocked us this run ({earlier.why})"),
                               secrets, each, meta)
            self.not_made.add(rec["seq"])
            return rec, None
        fetched = ask()
        why = (block or blocked_by_status)(fetched)
        if why:
            self.blocks[host] = Block(why, source.id, url)
            fetched.error = f"{fetched.error or why} ({BLOCK_MARK}: {host} not asked again this run)"
            self.warn(f"{host} blocked us ({why}); not asked again this run")
        return self._record(source, keep(fetched) if keep else fetched, secrets, each, meta), (None if why else fetched)

    def get(self, source, url, *, each=None, meta=None, params=None, headers=None, conditional=True, keep=None, block=None):
        """keep: what of the response the raw store may keep (Fetched -> Fetched), for a page that holds
        what must never be stored. block: why an answer is a block (Fetched -> str or None), for an adapter
        that knows a site's challenge or login wall, or a 403 that is a page's own answer; the status rule
        (blocked_by_status) otherwise."""
        hdrs, auth_params, secrets = self._auth(source, headers)
        etag, last_modified = self.store.validators(url) if conditional else (None, None)
        rec, answered = self._send(
            source, url, secrets, each, meta, block=block, keep=keep,
            ask=lambda: self.client.get(url, params={**(params or {}), **auth_params} or None, headers=hdrs or None,
                                        etag=etag, last_modified=last_modified))
        if conditional and answered and answered.ok and (answered.headers.get("etag") or answered.headers.get("last-modified")):
            self.validators.append((url, answered.headers.get("etag"), answered.headers.get("last-modified")))
        return rec

    def post(self, source, url, json_body, *, each=None, meta=None, headers=None):
        hdrs, _, secrets = self._auth(source, headers)
        rec, _ = self._send(source, url, secrets, each, {**(meta or {}), "body": json_body}, block=None,
                            ask=lambda: self.client.post(url, json_body=json_body, headers=hdrs or None))
        return rec

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
    tolerate = ()             # HTTP statuses that are a normal answer, not a failure (describe: a job page withheld or gone)

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
        """[(job key "system:id" or listing (source id, item key), description, facts {employment,
        pay})] for postings this record describes (the describe step)."""
        return []


def _registry():
    from . import alerts, apis, bigtech, boards_watch, describe, finder, getro, hn, linkedin, lists, serpapi, yc
    out = {}
    for mod in (lists, apis, bigtech, linkedin, getro, hn, boards_watch, yc, finder, describe, alerts, serpapi):
        for a in mod.ADAPTERS:
            out[a.name] = a
    return out


REGISTRY = _registry()
