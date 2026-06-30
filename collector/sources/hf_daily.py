"""Hugging Face Daily Papers — title + arXiv id + upvotes + official repo.
upvotes = in-field signal. Looped over the last N days so more arXiv papers get enriched on merge."""
from datetime import datetime, timezone, timedelta

from ..http_util import get
from ..config import HF_DAILY_DAYS
from ..schema import make_item

API = "https://huggingface.co/api/daily_papers"


def fetch(days=HF_DAILY_DAYS, log=print):
    today = datetime.now(timezone.utc).date()
    attempts = [None] + [(today - timedelta(days=i)).isoformat() for i in range(1, days)]
    items, seen, failed = [], set(), 0
    for date in attempts:  # None = today's default list (always works); dates enrich prior days
        params = {} if date is None else {"date": date}
        try:
            data = get(API, params=params, timeout=30).json()
        except Exception:  # noqa: BLE001 — per-day fail-soft
            failed += 1
            continue
        for d in data if isinstance(data, list) else []:
            p = d.get("paper", d) or {}
            aid = p.get("id") or p.get("arxiv_id")
            if aid and aid in seen:
                continue
            if aid:
                seen.add(aid)
            kw = p.get("ai_keywords") or []
            items.append(make_item(
                source="hf_daily", type="paper",
                title=p.get("title") or d.get("title", ""),
                url=f"https://huggingface.co/papers/{aid}" if aid else "",
                summary=p.get("summary") or d.get("summary", ""),
                published=p.get("publishedAt") or d.get("publishedAt"),
                arxiv_id=aid, tags=kw if isinstance(kw, list) else [],
                raw_signal={
                    "hf_upvotes": p.get("upvotes", 0),
                    "hf_num_comments": d.get("numComments", 0),
                    "hf_github_repo": p.get("githubRepo", "") or "",
                    "axis": "in_field",
                },
            ))
    log(f"  hf_daily: {len(items)} papers over ~{days}d" + (f" ({failed} days failed)" if failed else ""))
    return items
