"""Generic RSS/Atom fetch (labs, tech press, newsletters)."""
import re
from calendar import timegm
from datetime import datetime, timezone, timedelta

import feedparser

from ..http_util import get
from ..schema import make_item

_TAG = re.compile(r"<[^>]+>")


def _clean(html):
    return _TAG.sub("", html or "").strip()


def _pub(entry):
    pp = entry.get("published_parsed") or entry.get("updated_parsed")
    if pp:
        return datetime.fromtimestamp(timegm(pp), tz=timezone.utc)
    return None


def fetch_feed(label, url, type_, axis=None, window_days=None, log=print):
    r = get(url, timeout=30)
    feed = feedparser.parse(r.text)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)) if window_days else None
    items, skipped = [], 0
    for e in feed.entries:
        pub = _pub(e)
        if cutoff and pub and pub < cutoff:
            skipped += 1
            continue
        rs = {}
        if axis:
            rs["axis"] = axis
        items.append(make_item(
            source=f"rss:{label}", type=type_,
            title=e.get("title", ""), url=e.get("link", ""),
            summary=_clean(e.get("summary", "")), author=e.get("author", ""),
            published=pub, raw_signal=rs,
        ))
    log(f"  rss:{label}: {len(items)} in-window ({skipped} older skipped)")
    return items
