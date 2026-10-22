"""RSS and Atom feeds: lab blogs, newsletters, press. Conditional GET; the seen-set is the cursor."""
import hashlib

import feedparser

from .. import extract, sanitize, times, urls
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


def entry_content(e):
    return (e.get("content") or [{}])[0].get("value") or "" if e.get("content") else ""


def entry_summary(e):
    return e.get("summary") or entry_content(e)


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
            html = entry_summary(e)
            # A post's links are how attach and coverage find the stories it's about. Newsletters
            # (Substack) put a one-line teaser in summary and the whole issue in content.
            links = ()
            if role == "mention":
                body = entry_content(e) + " " + (e.get("summary") or "")
                links = tuple(dict.fromkeys(u for u, _ in extract.links(body, e.get("link") or "")))[:500]
            out.append(Item(
                item_key=entry_key(e), role=role, url=e.get("link") or "",
                title=sanitize.text(e.get("title"), opts["title_max_chars"]),
                summary=sanitize.text(html, opts["summary_max_chars"]),
                published_at=times.iso(e.get("published_parsed") or e.get("updated_parsed")),
                authors=entry_authors(e), kind=source.kind, org=source.org, links=links))
        return out
