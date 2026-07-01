"""Reddit discourse via public RSS (NO API key). Rate-limited by IP, so requests are spaced.
RSS omits scores, so this captures discourse PRESENCE; top/day|week pre-filters to upvoted posts."""
import re
import time
from calendar import timegm
from datetime import datetime, timezone

import feedparser

from ..http_util import get
from ..config import REDDIT_RSS, REDDIT_MIN_INTERVAL_SEC
from ..schema import make_item

_TAG = re.compile(r"<[^>]+>")
_RESEARCH_TAG = re.compile(r"\[(r|p|d|research|project)\]", re.I)
_DROP = re.compile(r"\b(released?|open.?weights?|open.?sourc|weights|gguf|checkpoint|\d+\s?b\b|nvfp\d|quantiz)\b", re.I)


def _kind(sub, title):
    """research (r/ML or [R]/[P]) -> substance; LocalLLaMA model drops -> ledger + attention; else chatter."""
    if sub == "MachineLearning" or _RESEARCH_TAG.search(title):
        return "research"
    if sub == "LocalLLaMA" and _DROP.search(title):
        return "model_drop"
    return "chatter"


def _pub(e):
    pp = e.get("published_parsed") or e.get("updated_parsed")
    return datetime.fromtimestamp(timegm(pp), tz=timezone.utc) if pp else None


def _text(e):
    c = e["content"][0].get("value", "") if e.get("content") else ""
    return _TAG.sub("", c or e.get("summary", "")).strip()


def fetch(window_days, log=print):
    items, failed = [], 0
    for i, (label, url) in enumerate(REDDIT_RSS):
        if i:
            time.sleep(REDDIT_MIN_INTERVAL_SEC)  # space requests to dodge Reddit's RSS 429s
        try:
            feed = feedparser.parse(get(url, timeout=30).content)
        except Exception as e:  # noqa: BLE001 — per-feed fail-soft
            failed += 1
            log(f"  {label}: ERROR {e}")
            continue
        n = 0
        for e in feed.entries:  # the top/day|week param IS the window — keep all entries
            items.append(make_item(
                source=label, type="discussion", title=e.get("title", ""),
                url=e.get("link", ""), summary=_text(e)[:1000], author=e.get("author", ""),
                published=_pub(e),
                raw_signal={"axis": "in_field", "subreddit": label.split(":")[-1],
                            "reddit_kind": _kind(label.split(":")[-1], e.get("title", "") or "")},
            ))
            n += 1
        log(f"  {label}: {n}")
    if failed:
        log(f"  reddit: {failed} feed(s) rate-limited / failed (fail-soft)")
    return items
