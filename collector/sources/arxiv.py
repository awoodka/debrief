"""arXiv API — wide sweep over the six categories, paginated to a date cutoff."""
import time
from calendar import timegm
from datetime import datetime, timezone, timedelta

import feedparser

from ..http_util import get
from ..config import ARXIV_CATEGORIES, MAX_ARXIV_RESULTS, ARXIV_MIN_INTERVAL_SEC
from ..schema import make_item, arxiv_id_from

API = "http://export.arxiv.org/api/query"


def _pub_dt(entry):
    pp = entry.get("published_parsed")
    if pp:
        return datetime.fromtimestamp(timegm(pp), tz=timezone.utc)
    return None


def fetch(days, max_results=MAX_ARXIV_RESULTS, log=print):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query = " OR ".join(f"cat:{c}" for c in ARXIV_CATEGORIES)  # spaces -> requests encodes safely
    items, start, page = [], 0, 100
    while len(items) < max_results:
        params = {
            "search_query": query, "start": start, "max_results": page,
            "sortBy": "submittedDate", "sortOrder": "descending",
        }
        r = get(API, params=params, timeout=60)
        feed = feedparser.parse(r.text)
        if not feed.entries:
            break
        stopped = False
        for e in feed.entries:
            pub = _pub_dt(e)
            if pub and pub < cutoff:
                stopped = True
                break
            cats = [t.get("term") for t in e.get("tags", []) if t.get("term")]
            items.append(make_item(
                source="arxiv", type="paper",
                title=e.get("title", ""), url=e.get("link", ""),
                summary=e.get("summary", ""),
                author=", ".join(a.get("name", "") for a in e.get("authors", [])[:6]),
                published=pub, tags=cats, arxiv_id=arxiv_id_from(e.get("id", "")),
                raw_signal={"arxiv_categories": cats},
            ))
            if len(items) >= max_results:
                break
        log(f"  arxiv: start={start} (+{len(feed.entries)}) cumulative={len(items)}")
        if stopped or len(feed.entries) < page:
            break
        start += page
        time.sleep(ARXIV_MIN_INTERVAL_SEC)
    return items
