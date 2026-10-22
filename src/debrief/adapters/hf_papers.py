"""Hugging Face Daily Papers. One request per day, from the day before the cursor (or the start of
the lookback window, whichever is earlier) to today, so a late run fills the gap, late additions
are caught, and upvote and comment counts are re-read while a paper is in the pool. The seen-set
drops repeats; every sighting records hf_upvotes and hf_comments as signals.

The API caps a day at `limit` papers (default 50, so we ask for 100). Hitting the cap is reported
as a warning, since the list may be truncated. Weekends legitimately return no papers.
"""
import json
from datetime import date, timedelta

from .. import sanitize, times
from ..store import raw
from .base import Adapter, Item


class HfDailyPapers(Adapter):
    name = "hf_daily_papers"
    windowed = False           # a paper's arXiv date can be weeks before HF lists it; listing is the news

    def fetch(self, ctx, source):
        o = {**ctx.opts["hf_daily_papers"], **source.options}
        today = ctx.now.date()
        last = ctx.store.cursor(source.id).get("last_date")
        start = date.fromisoformat(last) - timedelta(days=1) if last else today - timedelta(days=o["bootstrap_days"] - 1)
        # Re-read the last lookback_days days every run, so upvote and comment counts stay current.
        start = min(start, today - timedelta(days=o["lookback_days"] - 1))
        d, all_ok = start, True
        while d <= today:
            rec = ctx.get(source, f"{source.url}?date={d.isoformat()}&limit={o['limit']}",
                          each=d.isoformat(), meta={"date": d.isoformat()})
            if raw.ok(rec):
                n = len(json.loads(raw.body(rec)) or [])
                if n >= o["limit"]:
                    ctx.warn(f"{d}: {n} papers, the request limit; the list may be truncated")
            else:
                all_ok = False
            d += timedelta(days=1)
        if all_ok:
            ctx.set_cursor({"last_date": today.isoformat()})

    def parse(self, record, source, opts):
        if not raw.ok(record):
            return []
        data = json.loads(raw.body(record)) or []
        out = []
        for d in data if isinstance(data, list) else []:
            p = d.get("paper") or {}
            aid = (p.get("id") or "").strip()
            if not aid:
                continue
            authors = [a.get("name") for a in p.get("authors") or [] if a.get("name")]
            org = (p.get("organization") or d.get("organization") or {}).get("fullname")
            repo = p.get("githubRepo") or ""
            out.append(Item(
                item_key=aid, role="anchor", story_id=f"arxiv:{aid}", url=f"https://arxiv.org/abs/{aid}",
                title=sanitize.text(p.get("title") or d.get("title"), opts["title_max_chars"]),
                summary=sanitize.text(p.get("summary") or d.get("summary"), opts["summary_max_chars"]),
                published_at=times.iso(p.get("publishedAt")),
                authors=tuple(sanitize.text(a, 120) for a in authors[: opts["max_authors"]]),
                kind="paper", org=sanitize.text(org, 120) or source.org,
                artifact_urls=(f"https://huggingface.co/papers/{aid}",),
                code_urls=(repo,) if repo.startswith("https://github.com/") else (),
                signals={"hf_upvotes": p.get("upvotes") or 0, "hf_comments": d.get("numComments") or 0},
                extra={"hf_upvotes": p.get("upvotes"), "hf_comments": d.get("numComments"),
                       "n_authors": len(authors), "listed_on": record.get("meta", {}).get("date"),
                       "submitted_on_daily_at": p.get("submittedOnDailyAt"),
                       "ai_summary": sanitize.text(p.get("ai_summary"), opts["summary_max_chars"]),
                       "ai_keywords": [sanitize.text(k, 60) for k in (p.get("ai_keywords") or [])[:12]],
                       "project_page": p.get("projectPage")}))
        return out
