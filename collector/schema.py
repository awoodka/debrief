"""One normalized item schema + dedupe/merge across sources."""
import re
from datetime import datetime, timezone

ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")


def iso(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt.strip() or None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def arxiv_id_from(text):
    m = ARXIV_ID_RE.search(text or "")
    return m.group(1) if m else None


def make_item(source, type, title, url, summary="", author="", published=None,
              tags=None, raw_signal=None, arxiv_id=None):
    """Build one normalized item. `raw_signal` holds source-specific fields; enrichment (step 3)
    adds in_field/mainstream/divergence later. Nothing is culled here."""
    return {
        "source": source,
        "sources": [source],
        "type": type,
        "title": (title or "").strip(),
        "url": (url or "").strip(),
        "summary": (summary or "").strip(),
        "author": (author or "").strip(),
        "published": iso(published),
        "tags": tags or [],
        "arxiv_id": arxiv_id,
        "raw_signal": raw_signal or {},
    }


def canonical_key(item):
    if item.get("arxiv_id"):
        return "arxiv:" + item["arxiv_id"]
    url = (item.get("url") or "").lower().split("?")[0].rstrip("/")
    url = re.sub(r"^https?://(www\.)?", "", url)
    if url:
        return "url:" + url
    return "title:" + re.sub(r"\W+", "", (item.get("title") or "").lower())[:80]


def _merge(a, b):
    """Fold b into a: union sources/tags, combine signals, keep the richer summary + any arxiv_id."""
    for s in b.get("sources", [b.get("source")]):
        if s and s not in a["sources"]:
            a["sources"].append(s)
    for k, v in b.get("raw_signal", {}).items():
        a["raw_signal"].setdefault(k, v)
    if len(b.get("summary", "")) > len(a.get("summary", "")):
        a["summary"] = b["summary"]
    a["tags"] = list(dict.fromkeys((a.get("tags") or []) + (b.get("tags") or [])))
    if not a.get("arxiv_id") and b.get("arxiv_id"):
        a["arxiv_id"] = b["arxiv_id"]
    if not a.get("author") and b.get("author"):
        a["author"] = b["author"]


def dedupe(items):
    """Dedupe by canonical key, merging signals from every source that surfaced the same item."""
    by_key = {}
    for it in items:
        k = canonical_key(it)
        if k in by_key:
            _merge(by_key[k], it)
        else:
            by_key[k] = it
    return list(by_key.values())
