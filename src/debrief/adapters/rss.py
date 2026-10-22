"""RSS and Atom feeds: lab blogs, newsletters, press. Conditional GET; the seen-set is the cursor."""
import hashlib

import feedparser

from .. import sanitize, times, urls
from ..store import raw
from .base import Adapter, Item


def entry_key(e):
    key = e.get("id") or e.get("link") or ""
    if not key:
        key = "title:" + hashlib.sha1((e.get("title") or "").encode()).hexdigest()[:16]
    return key


def entry_authors(e):
    names = [a.get("name") for a in e.get("authors") or [] if a.get("name")]
    if not names and e.get("author"):
        names = [e["author"]]
    return tuple(sanitize.text(n, 120) for n in names)


def entry_summary(e):
    content = (e.get("content") or [{}])[0].get("value") if e.get("content") else ""
    return e.get("summary") or content or ""


class Rss(Adapter):
    name = "rss"

    def fetch(self, ctx, source):
        for each, url in urls.expand(source.url, source.each):
            ctx.get(source, url, each=each)

    def parse(self, record, source, opts):
        if not raw.ok(record):
            return []
        feed = feedparser.parse(raw.body(record))
        role = "anchor" if "anchor" in source.roles else "mention"
        out = []
        for e in feed.entries:
            out.append(Item(
                item_key=entry_key(e), role=role, url=e.get("link") or "",
                title=sanitize.text(e.get("title"), opts["title_max_chars"]),
                summary=sanitize.text(entry_summary(e), opts["summary_max_chars"]),
                published_at=times.iso(e.get("published_parsed") or e.get("updated_parsed")),
                authors=entry_authors(e), kind=source.kind, org=source.org))
        return out
