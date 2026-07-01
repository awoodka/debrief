"""Hacker News via the free Algolia API — keyword AI stories + front page + Show HN + Launch HN.
HN points = mainstream-attention signal; Show/Launch HN = new projects & startups (builder discourse)."""
import re
from datetime import datetime, timezone, timedelta

from ..http_util import get
from ..config import HN_QUERIES, HN_POINTS_FLOOR
from ..schema import make_item

SEARCH = "https://hn.algolia.com/api/v1/search"
SEARCH_BY_DATE = "https://hn.algolia.com/api/v1/search_by_date"

AI_RE = re.compile(
    r"\b(ai|a\.i\.|llm|llms|gpt|claude|gemini|openai|anthropic|mistral|llama|qwen|deepseek|"
    r"machine learning|ml|neural|deep learning|model|models|agent|agents|agentic|diffusion|"
    r"transformer|rag|embedding|inference|fine.?tun|dataset|hugging.?face|pytorch|tensor|"
    r"cuda|chatbot|copilot|prompt)\b", re.I)


def _ts(epoch):
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat() if epoch else None


def _mk(h, *, axis="in_field", extra=None):  # HN is a builder/developer venue -> in-field
    url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
    rs = {"hn_points": h.get("points", 0), "hn_comments": h.get("num_comments", 0), "axis": axis}
    if extra:
        rs.update(extra)
    return make_item(source="hackernews", type="discussion", title=h.get("title", ""), url=url,
                     author=h.get("author", ""), published=_ts(h.get("created_at_i")), raw_signal=rs)


def fetch(window_days, log=print):
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=window_days)).timestamp())
    items, failed = [], 0

    def call(url, params, tag):
        nonlocal failed
        try:
            return get(url, params=params, timeout=30).json().get("hits", [])
        except Exception as e:  # noqa: BLE001
            failed += 1
            log(f"  hackernews[{tag}]: ERROR {e}")
            return []

    # 1) keyword AI stories above the points floor
    for q in HN_QUERIES:
        for h in call(SEARCH_BY_DATE, {"query": q, "tags": "story",
                      "numericFilters": f"points>={HN_POINTS_FLOOR},created_at_i>={cutoff}",
                      "hitsPerPage": 40}, q):
            items.append(_mk(h))
    # 2) current front page (AI-relevant only)
    for h in call(SEARCH, {"tags": "front_page", "hitsPerPage": 50}, "front_page"):
        if AI_RE.search(h.get("title", "") or ""):
            items.append(_mk(h, extra={"hn_front_page": True}))
    # 3) Show HN (new projects, AI-relevant; builder discourse)
    for h in call(SEARCH_BY_DATE, {"tags": "show_hn", "numericFilters": f"created_at_i>={cutoff}",
                  "hitsPerPage": 60}, "show_hn"):
        if AI_RE.search(h.get("title", "") or ""):
            items.append(_mk(h, axis="in_field", extra={"show_hn": True}))
    # 4) Launch HN (YC startups)
    for h in call(SEARCH_BY_DATE, {"query": "Launch HN", "tags": "story",
                  "numericFilters": f"created_at_i>={cutoff}", "hitsPerPage": 30}, "launch_hn"):
        if (h.get("title", "") or "").startswith("Launch HN"):
            items.append(_mk(h, axis="in_field", extra={"launch_hn": True}))

    log(f"  hackernews: {len(items)} (keyword + front-page + Show HN + Launch HN)"
        + (f" ({failed} calls failed)" if failed else ""))
    return items
