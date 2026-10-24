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

    role: "anchor" can create or update a story; "mention" is a discussion or reference post,
    attached to stories by stories.attach; "seen" only records the key, so it's never new again;
    "signal" is a lookup about an existing story (story_id required) that only adds signals;
    "page" is a story's article page (story_id and page required). An anchor may carry its page
    too, when the adapter fetched it anyway (listing pages).
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
    # Snapshots, recorded every time the item is seen (not just the first time):
    signals: dict = field(default_factory=dict)      # about the artifact: upvotes, stars, likes ...
    engagement: dict = field(default_factory=dict)   # about a post: points, comments, likes, reposts
    # Mentions only:
    links: tuple = ()                 # URLs the post links to; attach and coverage resolve these
    mention_kind: str | None = None   # thread | post | comment | reference (default from the source's roles)
    parent_id: str | None = None      # a comment's thread, as a mention id
    story_hint: str | None = None     # the story the adapter already knows it belongs to
    rank: int | None = None           # position in the source's own ranked order (comments)
    replies: int | None = None        # descendants (comments)
    # Pages only: {"status": HTTP status, "text": main text, "links": [urls in the text]}
    page: dict | None = None


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
    # On a source's first run, record every anchor as seen (for snapshots like a leaderboard,
    # where "new" only means something relative to a previous run).
    first_run_seen = False
    # Each request is a separate page on someone else's site (article pages): one failing is a
    # warning, not a partial source; the source fails only if every request does.
    page_by_page = False
    # When collect runs it: None = by its roles (anchor, then discussion, then reference); or
    # "enrich" (lookups about stories, after the anchors) / "threads" (comment trees, after the
    # discussion sources they hang off).
    phase = None

    def fetch(self, ctx, source):
        raise NotImplementedError

    def parse(self, record, source, opts):
        raise NotImplementedError


class FetchContext:
    """Everything fetch() may use. Records, validators and the cursor are only proposals here:
    collect saves them once the source's items are stored."""

    def __init__(self, client, store, env, run_id, now, opts, log=print, run_date=None,
                 backfill_hours=None, settings=None, enabled=(), cadence="daily"):
        self.client, self.store, self.env = client, store, env
        self.run_id, self.now, self.opts, self.log = run_id, now, opts, log
        self.settings = settings or {"collect": opts}    # all of settings.yml (+ triage.yml)
        self.enabled = frozenset(enabled)                 # ids of the enabled sources
        self.cadence = cadence                            # "daily", or "6h" for the discussion poll
        self.run_date = run_date or now.date().isoformat()
        self.backfill_hours = backfill_hours     # bootstrap-bands: reach back this far, ignoring the cursor
        self.records, self.validators, self.warnings = [], [], []
        self.cursor = None

    @property
    def fetched_at(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @property
    def stored_until(self):
        """The last fetch this run has stored so far, or the run's start: what a selection made at
        fetch time (the unattached threads to prefetch) counts back from, so it sees the threads the
        run's discussion sources stored just before."""
        row = self.store.one("SELECT MAX(fetched_at) FROM fetches WHERE run_id=?", (self.run_id,))
        return row[0] if row and row[0] else self.now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

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

    def post(self, source, url, json_body, *, each=None, meta=None, headers=None):
        """A POST (GraphQL), recorded like get(). Never conditional; the request body isn't kept, so
        put what parse() needs to know about the request in meta."""
        hdrs = {**source.auth_headers(self.env), **(headers or {})}
        fetched = self.client.post(url, json_body=json_body, headers=hdrs or None)
        rec = raw.make_record(source.id, self.run_id, len(self.records), fetched, self.fetched_at,
                              each=each, meta=meta)
        self.records.append(rec)
        return rec

    def set_cursor(self, cursor):
        self.cursor = cursor

    def warn(self, message):
        self.warnings.append(message)
