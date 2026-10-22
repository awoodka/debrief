"""What every adapter is: fetch() talks to the network, parse() doesn't.

fetch(ctx, source) requests what the source needs, through ctx.get(), which records every
response as a raw record. It may read state (cursors, the seen-set) and propose a new cursor,
but writes nothing itself. parse(record, source, opts) turns one raw record into Items and must
be pure, so the same raw data always gives the same items. That's what makes replay work.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..store import raw


@dataclass
class Item:
    """One thing a source returned, normalized. The story builder decides what it becomes.

    role: "anchor" can create or update a story; "mention" is a discussion or reference post
    (M1 stores it unattached); "seen" only records the key, so it's never new again.
    """
    item_key: str
    role: str
    url: str = ""
    title: str = ""
    summary: str = ""
    published_at: str | None = None        # ISO 8601, UTC
    authors: tuple = ()
    kind: str | None = None
    org: str | None = None
    story_id: str | None = None            # when the adapter knows it; otherwise derived from url
    artifact_urls: tuple = ()
    code_urls: tuple = ()
    extra: dict = field(default_factory=dict)


class Adapter:
    name = ""
    # The age guard. Windowed sources return a rolling history (feeds, release lists): an anchor seen
    # for the first time whose publish date is older than collect.window_days is recorded as "seen"
    # rather than becoming a story, so a first run doesn't turn years of posts into today's news.
    #   True         always applies (feed and API dates are reliable)
    #   "bootstrap"  only on the source's first run (scraped dates are guesses; later, a link
    #                missing from the seen-set is itself the evidence that the post is new)
    #   False        never (the source only ever lists new things)
    windowed = True

    def fetch(self, ctx, source):
        raise NotImplementedError

    def parse(self, record, source, opts):
        raise NotImplementedError


class FetchContext:
    """Everything fetch() may use. Records, validators and the cursor are only proposals here:
    collect saves them once the source's items are stored."""

    def __init__(self, client, store, env, run_id, now, opts, log=print):
        self.client, self.store, self.env = client, store, env
        self.run_id, self.now, self.opts, self.log = run_id, now, opts, log
        self.records, self.validators, self.warnings = [], [], []
        self.cursor = None

    @property
    def fetched_at(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def get(self, source, url, *, each=None, meta=None, conditional=True, headers=None):
        hdrs = {**source.auth_headers(self.env), **(headers or {})}
        etag, last_modified = self.store.validators(url) if conditional else (None, None)
        fetched = self.client.get(url, headers=hdrs or None, etag=etag, last_modified=last_modified)
        rec = raw.make_record(source.id, self.run_id, len(self.records), fetched, self.fetched_at,
                              each=each, meta=meta)
        self.records.append(rec)
        if conditional and fetched.ok and (fetched.headers.get("etag") or fetched.headers.get("last-modified")):
            self.validators.append((url, fetched.headers.get("etag"), fetched.headers.get("last-modified")))
        return rec

    def set_cursor(self, cursor):
        self.cursor = cursor

    def warn(self, message):
        self.warnings.append(message)
