"""arXiv: the category RSS feeds, with OAI-PMH backfill for missed announcement days.

Each category feed holds only the latest announcement, so the cursor is the last announcement
date seen. If a run finds that one or more weekday announcements were missed since then, it
backfills them from OAI-PMH (ListRecords, arXivRaw format, sets from options.oai_sets) for exactly
that date range, following resumption tokens. In arXivRaw a paper is new when it has only a v1,
dated within a week of the record's datestamp; other records are replacements or metadata edits.

From the RSS: announce types new and cross become stories; replace and replace-cross are only
recorded. Every request shares arXiv's one-request-per-3-s budget (settings.http.buckets.arxiv).
"""
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import feedparser

from .. import canon, sanitize, times, urls
from ..store import raw
from .base import Adapter, Item

NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "ax": "http://arxiv.org/OAI/arXivRaw/"}
_ABSTRACT_PREFIX = re.compile(r"^arXiv:\S+\s+Announce Type:\s*\S+\s+Abstract:\s*", re.S)
NEW_TYPES = {"new", "cross"}


def _authors(text, cap):
    names = [n.strip() for n in re.split(r",\s*|\s+and\s+", text or "") if n.strip()]
    return tuple(sanitize.text(n, 120) for n in names[:cap]), len(names)


def _announce_date(feed):
    """Announcement day: the calendar date of the feed's pubDate (midnight US Eastern that day)."""
    try:
        return parsedate_to_datetime(feed.feed.get("published") or "").date()
    except (TypeError, ValueError):
        return None


def missed_weekdays(last, current):
    """Weekdays strictly between two announcement dates: the days whose announcements we missed."""
    d, out = last + timedelta(days=1), []
    while d < current:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


class ArxivRss(Adapter):
    name = "arxiv_rss"
    windowed = False           # the feed only ever holds the latest announcement

    def fetch(self, ctx, source):
        dates = []
        for each, url in urls.expand(source.url, source.each):
            rec = ctx.get(source, url, each=each, meta={"kind": "rss"})
            if raw.ok(rec):
                d = _announce_date(feedparser.parse(raw.body(rec)))
                if d:
                    dates.append(d)
        last = ctx.store.cursor(source.id).get("last_announce")
        current = max(dates) if dates else None
        if last and current:
            gap = missed_weekdays(date.fromisoformat(last), current)
            if gap:
                ctx.warn(f"missed announcements {gap[0]}..{gap[-1]}; backfilling from OAI-PMH")
                self._backfill(ctx, source, gap[0], gap[-1])
        if current:
            ctx.set_cursor({"last_announce": current.isoformat()})

    def _backfill(self, ctx, source, start, end):
        o = source.options
        base, max_pages = o["oai_base"], o.get("oai_max_pages", 40)
        for oai_set in o.get("oai_sets", ["cs", "stat"]):
            url = (f"{base}?verb=ListRecords&set={oai_set}&metadataPrefix=arXivRaw"
                   f"&from={start.isoformat()}&until={end.isoformat()}")
            for page in range(max_pages):
                rec = ctx.get(source, url, each=oai_set, meta={"kind": "oai", "set": oai_set, "page": page},
                              conditional=False)
                token = re.search(r"<resumptionToken[^>]*>([^<]+)</resumptionToken>", raw.body(rec).decode("utf-8", "replace")) \
                    if raw.ok(rec) else None
                if not token:
                    break
                url = f"{base}?verb=ListRecords&resumptionToken={quote(token.group(1).strip())}"
            else:
                ctx.warn(f"OAI set {oai_set}: stopped after {max_pages} pages")

    def parse(self, record, source, opts):
        if not raw.ok(record):
            return []
        if (record.get("meta") or {}).get("kind") == "oai":
            return self._parse_oai(record, source, opts)
        feed = feedparser.parse(raw.body(record))
        announced = times.iso(feed.feed.get("published_parsed"))
        out = []
        for e in feed.entries:
            aid = canon.arxiv_id(e.get("id") or e.get("link"))
            if not aid:
                continue
            atype = e.get("arxiv_announce_type", "")
            authors, n = _authors(e.get("author"), opts["max_authors"])
            out.append(Item(
                item_key=aid, role="anchor" if atype in NEW_TYPES else "seen",
                story_id=f"arxiv:{aid}", url=f"https://arxiv.org/abs/{aid}",
                title=sanitize.text(e.get("title"), opts["title_max_chars"]),
                summary=sanitize.text(_ABSTRACT_PREFIX.sub("", e.get("summary") or ""), opts["abstract_max_chars"]),
                published_at=announced or times.iso(e.get("published_parsed")),
                authors=authors, kind="paper",
                extra={"categories": [t.term for t in e.get("tags") or []], "announce_type": atype,
                       "n_authors": n}))
        return out

    def _parse_oai(self, record, source, opts):
        root = ET.fromstring(raw.body(record))
        wanted = set(source.each)
        out = []
        for rec in root.iter(f"{{{NS['oai']}}}record"):
            header = rec.find("oai:header", NS)
            if header is None or header.get("status") == "deleted":
                continue
            md = rec.find("oai:metadata/ax:arXivRaw", NS)
            if md is None:
                continue
            cats = (md.findtext("ax:categories", "", NS) or "").split()
            if not wanted & set(cats):
                continue
            aid = (md.findtext("ax:id", "", NS) or "").strip().lower()
            stamp = date.fromisoformat(header.findtext("oai:datestamp", "", NS).strip()[:10])
            versions = md.findall("ax:version", NS)
            try:
                v1 = parsedate_to_datetime(versions[0].findtext("ax:date", "", NS)).date()
            except (IndexError, TypeError, ValueError):
                v1 = None
            is_new = len(versions) == 1 and v1 is not None and v1 >= stamp - timedelta(days=7)
            authors, n = _authors(md.findtext("ax:authors", "", NS), opts["max_authors"])
            out.append(Item(
                item_key=aid, role="anchor" if is_new else "seen",
                story_id=f"arxiv:{aid}", url=f"https://arxiv.org/abs/{aid}",
                title=sanitize.text(md.findtext("ax:title", "", NS), opts["title_max_chars"]),
                summary=sanitize.text(md.findtext("ax:abstract", "", NS), opts["abstract_max_chars"]),
                published_at=times.iso(stamp), authors=authors, kind="paper",
                extra={"categories": cats, "via": "oai-backfill", "n_authors": n,
                       "n_versions": len(versions)}))
        return out
